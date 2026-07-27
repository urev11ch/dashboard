"""Дисковый и оперативный кэш анализа: распакованные .db, пикл-результаты по
базам и по источнику, ленивые side-файлы сэмплов, готовые payload графиков.

Ключи записей зависят от mtime+size исходников, а FTP-автообновление плодит новые
записи каждые несколько минут — поэтому поверх TTL работает бюджет с LRU-эвикцией
(prune_cache_root). Пиклы подписаны HMAC (defense-in-depth перед unpickle).

Выделено из webapp/app.py. Каталоги кэша и лимит реестра читаются динамически
через config (app_config.*), чтобы тесты подменяли их, патча app.config.<имя>;
in-memory реестры/кэши патчатся как app.cache.<имя>.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import pickle
import secrets
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import wash_report as core
from webapp import config as app_config
from webapp.config import (
    ANALYSIS_CACHE_MAX_BYTES,
    ANALYSIS_CACHE_MAX_ENTRIES,
    ANALYSIS_CACHE_TTL_SECONDS,
    ARCHIVE_CACHE_MAX_BYTES,
    ARCHIVE_CACHE_MAX_ENTRIES,
    ARCHIVE_CACHE_TTL_SECONDS,
    CHART_PAYLOAD_CACHE_LIMIT,
    CHART_PAYLOAD_DISK_CACHE_VERSION,
    DB_ANALYSIS_CACHE_VERSION,
    WEB_RUNTIME_OUTPUT_DIR,
    WORKSPACE_ANALYSIS_CACHE_VERSION,
)
from webapp.archives import extract_archive_dbs
from webapp.io_utils import atomic_write_bytes
from webapp.state import analysis_cache_lock, archive_cache_lock, chart_payload_cache_lock

# Последний ключ кэша по источнику (архив / .db / рабочая папка) — чтобы удалять
# предыдущую версию записи того же источника. Ограничены по размеру: раньше
# словарь рос монотонно и не чистился.
archive_cache_keys_by_source: OrderedDict[str, str] = OrderedDict()
db_cache_keys_by_source: OrderedDict[str, str] = OrderedDict()
workspace_cache_keys_by_source: OrderedDict[str, str] = OrderedDict()
chart_payload_cache: OrderedDict[tuple[int, str], dict[str, Any]] = OrderedDict()


def _dir_size_bytes(root_path: Path) -> int:
    """Суммарный размер файлов внутри каталога кэша (для LRU-бюджета)."""
    total = 0
    for dirpath, _dirs, files in os.walk(root_path):
        base = Path(dirpath)
        for name in files:
            try:
                total += (base / name).stat().st_size
            except OSError:
                continue
    return total


def path_cache_signature(path: Path) -> str:
    """Подпись файла (mtime+size) для ключа кэша. Исчезнувший под работающим
    анализом файл (например, его удалила очистка архивов) не должен ронять
    джоб — вместо исключения возвращаем маркер, ключ просто не совпадёт."""
    try:
        stat_result = path.stat()
    except OSError:
        return "missing"
    return f"{stat_result.st_mtime_ns}::{stat_result.st_size}"


def archive_cache_key(archive_path: Path) -> str:
    payload = f"{archive_path}::{path_cache_signature(archive_path)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def touch_cache_entry(path: Path) -> None:
    """Отмечает запись как использованную: mtime записи — это её «время
    последнего доступа» для LRU-эвикции (см. prune_cache_root)."""
    try:
        os.utime(path, None)
    except OSError:
        return


def remove_cache_entry(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return


def cache_entry_size_bytes(path: Path) -> int:
    try:
        if path.is_dir():
            return _dir_size_bytes(path)
        return path.stat().st_size
    except OSError:
        return 0


def is_protected_cache_entry(path: Path) -> bool:
    """Служебные записи, которые нельзя вытеснять: рабочая папка отчётов лежит
    внутри корня кэша, а незавершённые распаковки (`<key>.tmp-<uuid>`) пишет
    другой поток — он сам за собой уберёт."""
    if ".tmp-" in path.name:
        return True
    return path.name == WEB_RUNTIME_OUTPUT_DIR.name and path.parent == app_config.ANALYSIS_CACHE_ROOT


def cleanup_expired_cache_entries(cache_root: Path, ttl_seconds: int) -> None:
    cutoff = time.time() - ttl_seconds
    try:
        candidates = list(cache_root.iterdir())
    except OSError:
        return
    for candidate in candidates:
        if is_protected_cache_entry(candidate):
            continue
        try:
            if candidate.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        remove_cache_entry(candidate)


def prune_cache_root(
    cache_root: Path,
    *,
    ttl_seconds: int,
    max_bytes: int,
    max_entries: int,
) -> None:
    """TTL + бюджет кэша с LRU-эвикцией.

    Одного TTL недостаточно: ключи записей зависят от mtime+size исходников, а
    автообновление FTP идёт каждые несколько минут, поэтому за сутки набегают
    сотни новых db-*/workspace-*/chart-* — кэш рос без ограничений. Сначала
    выбрасываем протухшее по TTL, затем, пока не уложились в бюджет по объёму и
    количеству, удаляем давно не используемые записи (mtime обновляется при
    каждом попадании в кэш, см. touch_cache_entry)."""
    cleanup_expired_cache_entries(cache_root, ttl_seconds)

    try:
        candidates = list(cache_root.iterdir())
    except OSError:
        return

    entries: list[tuple[float, int, Path]] = []
    total_bytes = 0
    for candidate in candidates:
        if is_protected_cache_entry(candidate):
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        size = cache_entry_size_bytes(candidate)
        total_bytes += size
        entries.append((mtime, size, candidate))

    if total_bytes <= max_bytes and len(entries) <= max_entries:
        return

    entries.sort(key=lambda item: item[0])  # от самых давно использованных
    entry_count = len(entries)
    for _mtime, size, candidate in entries:
        if total_bytes <= max_bytes and entry_count <= max_entries:
            break
        remove_cache_entry(candidate)
        total_bytes -= size
        entry_count -= 1
        logging.debug("Кэш: вытеснена запись %s (%d байт)", candidate.name, size)


def prune_archive_cache() -> None:
    with archive_cache_lock:
        prune_cache_root(
            app_config.ARCHIVE_CACHE_ROOT,
            ttl_seconds=ARCHIVE_CACHE_TTL_SECONDS,
            max_bytes=ARCHIVE_CACHE_MAX_BYTES,
            max_entries=ARCHIVE_CACHE_MAX_ENTRIES,
        )


def prune_analysis_cache() -> None:
    with analysis_cache_lock:
        prune_cache_root(
            app_config.ANALYSIS_CACHE_ROOT,
            ttl_seconds=ANALYSIS_CACHE_TTL_SECONDS,
            max_bytes=ANALYSIS_CACHE_MAX_BYTES,
            max_entries=ANALYSIS_CACHE_MAX_ENTRIES,
        )


def remember_cache_key(
    registry: OrderedDict[str, str],
    source_key: str,
    cache_key: str,
) -> str | None:
    """Запоминает актуальный ключ кэша источника и возвращает предыдущий (если
    он был другим). Реестр ограничен по размеру: раньше он рос монотонно."""
    previous_key = registry.pop(source_key, None)
    registry[source_key] = cache_key
    while len(registry) > app_config.CACHE_SOURCE_REGISTRY_LIMIT:
        registry.popitem(last=False)
    if previous_key is None or previous_key == cache_key:
        return None
    return previous_key


def cleanup_stale_archive_cache(source_path: Path, cache_key: str) -> None:
    previous_key = remember_cache_key(archive_cache_keys_by_source, str(source_path), cache_key)
    if previous_key is None:
        return
    remove_cache_entry(app_config.ARCHIVE_CACHE_ROOT / previous_key)


def cleanup_stale_db_analysis_cache(db_path: Path, cache_key: str) -> None:
    """Удаляет пикл предыдущей версии этой же базы: при дозаписи `.db` меняется
    mtime+size, а значит и ключ, и старая запись иначе висела бы до TTL."""
    previous_key = remember_cache_key(db_cache_keys_by_source, str(db_path), cache_key)
    if previous_key is None:
        return
    remove_cache_entry(db_analysis_cache_path(previous_key))


def cleanup_stale_workspace_cache(source_key: str, cache_key: str) -> None:
    """Удаляет предыдущий сводный анализ источника вместе с его графиками
    (chart-<ключ анализа>-*.pkl) — они больше не будут востребованы."""
    previous_key = remember_cache_key(workspace_cache_keys_by_source, source_key, cache_key)
    if previous_key is None:
        return
    remove_cache_entry(workspace_analysis_cache_path(previous_key))
    # Графики и side-файлы сэмплов прошлого анализа — по префиксам с его ключом.
    chart_prefix = f"chart-{previous_key[:CHART_CACHE_KEY_PREFIX_LEN]}-"
    samples_prefix = f"ws-samples-{previous_key[:WS_SAMPLES_KEY_PREFIX_LEN]}-"
    try:
        stale = [
            path
            for path in app_config.ANALYSIS_CACHE_ROOT.iterdir()
            if path.name.startswith(chart_prefix) or path.name.startswith(samples_prefix)
        ]
    except OSError:
        return
    for path in stale:
        remove_cache_entry(path)


def extract_archive_dbs_cached(
    archive_path: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[Path]:
    cache_key = archive_cache_key(archive_path)
    cache_dir = app_config.ARCHIVE_CACHE_ROOT / cache_key

    with archive_cache_lock:
        cleanup_stale_archive_cache(archive_path, cache_key)
        if cache_dir.exists():
            touch_cache_entry(cache_dir)
            return sorted(path.resolve() for path in cache_dir.rglob("*.db") if path.is_file())

    # Временная папка уникальна на вызов: параллельная распаковка того же
    # архива в другом потоке не должна удалять или переименовывать чужой tmp.
    temp_dir = app_config.ARCHIVE_CACHE_ROOT / f"{cache_key}.tmp-{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        extract_archive_dbs(
            archive_path,
            temp_dir,
            cancel_check=cancel_check,
        )
        with archive_cache_lock:
            if cache_dir.exists():
                # Другой поток успел распаковать этот же архив — используем его
                # результат, свою копию выбрасываем.
                shutil.rmtree(temp_dir, ignore_errors=True)
                touch_cache_entry(cache_dir)
            else:
                try:
                    temp_dir.rename(cache_dir)
                except OSError:
                    if not cache_dir.exists():
                        raise
                    shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return sorted(path.resolve() for path in cache_dir.rglob("*.db") if path.is_file())


def db_analysis_cache_key(db_path: Path) -> str:
    payload = f"{db_path}::{path_cache_signature(db_path)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def db_analysis_cache_path(cache_key: str) -> Path:
    return app_config.ANALYSIS_CACHE_ROOT / f"db-{cache_key}.pkl"


def workspace_analysis_cache_key(db_files: list[Path], *, max_gap_seconds: float) -> str:
    payload_parts = [f"v{WORKSPACE_ANALYSIS_CACHE_VERSION}", f"gap:{max_gap_seconds:.6f}"]
    for db_path in sorted(db_files, key=lambda item: str(item).lower()):
        payload_parts.append(f"{db_path}::{db_analysis_cache_key(db_path)}")
    payload = "\n".join(payload_parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def workspace_analysis_cache_path(cache_key: str) -> Path:
    return app_config.ANALYSIS_CACHE_ROOT / f"workspace-{cache_key}.pkl"


_cache_hmac_key: bytes | None = None
_cache_hmac_key_lock = threading.Lock()
CACHE_HMAC_DIGEST_SIZE = 32  # sha256


def cache_hmac_key() -> bytes:
    """Секрет для подписи записей кэша, хранится в приватном 0700-каталоге кэша.
    HMAC — defense-in-depth поверх прав доступа: даже если файл кэша подменят,
    неверная подпись отсеет его ДО unpickle (pickle.load на чужих данных = RCE).
    Если ключ не прочитать/не сохранить — генерируем новый: старые записи просто
    не пройдут проверку и будут перечитаны (промах кэша, не сбой)."""
    global _cache_hmac_key
    if _cache_hmac_key is not None:
        return _cache_hmac_key
    with _cache_hmac_key_lock:
        if _cache_hmac_key is not None:
            return _cache_hmac_key
        key_path = app_config.ANALYSIS_CACHE_ROOT / "cache-hmac.key"
        try:
            existing = key_path.read_bytes()
            if len(existing) >= CACHE_HMAC_DIGEST_SIZE:
                _cache_hmac_key = existing
                return existing
        except FileNotFoundError:
            pass
        except OSError:
            logging.warning("Не удалось прочитать ключ подписи кэша, генерирую новый.")
        key = secrets.token_bytes(CACHE_HMAC_DIGEST_SIZE)
        try:
            atomic_write_bytes(key_path, key)
            os.chmod(key_path, 0o600)
        except OSError:
            logging.warning("Не удалось сохранить ключ подписи кэша — кэш станет одноразовым.")
        _cache_hmac_key = key
        return key


def load_pickle_cache(path: Path) -> Any | None:
    """Промах кэша не должен ронять джоб: битый пикл даёт что угодно
    (IndexError/KeyError/TypeError/ImportError после смены формата чанков), а не
    только PickleError — поэтому ловим Exception и выбрасываем запись.

    Перед unpickle проверяем HMAC-подпись (первые 32 байта файла): запись без
    валидной подписи (подмена, старый формат, чужой ключ) выбрасывается, не
    доходя до pickle.loads."""
    try:
        with path.open("rb") as handle:
            blob = handle.read()
    except FileNotFoundError:
        return None
    except Exception:
        logging.warning("Повреждённая запись кэша, удаляю: %s", path.name)
        remove_cache_entry(path)
        return None

    signature, data = blob[:CACHE_HMAC_DIGEST_SIZE], blob[CACHE_HMAC_DIGEST_SIZE:]
    expected = hmac.new(cache_hmac_key(), data, hashlib.sha256).digest()
    if len(blob) < CACHE_HMAC_DIGEST_SIZE or not hmac.compare_digest(signature, expected):
        logging.warning("Подпись записи кэша не совпала, удаляю: %s", path.name)
        remove_cache_entry(path)
        return None

    try:
        return pickle.loads(data)
    except Exception:
        logging.warning("Повреждённая запись кэша, удаляю: %s", path.name)
        remove_cache_entry(path)
        return None


def save_pickle_cache(path: Path, payload: Any) -> None:
    data = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    signature = hmac.new(cache_hmac_key(), data, hashlib.sha256).digest()
    atomic_write_bytes(path, signature + data)


def load_cached_db_analysis(db_path: Path) -> core.DbAnalysisChunk | None:
    cache_key = db_analysis_cache_key(db_path)
    cache_path = db_analysis_cache_path(cache_key)
    with analysis_cache_lock:
        payload = load_pickle_cache(cache_path)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != DB_ANALYSIS_CACHE_VERSION:
            return None
        if payload.get("cache_key") != cache_key:
            return None
        if payload.get("db_path") != str(db_path):
            return None

        chunk = payload.get("chunk")
        if not isinstance(chunk, core.DbAnalysisChunk):
            return None
        touch_cache_entry(cache_path)
        return chunk


def save_cached_db_analysis(db_path: Path, chunk: core.DbAnalysisChunk) -> None:
    cache_key = db_analysis_cache_key(db_path)
    cache_path = db_analysis_cache_path(cache_key)
    payload = {
        "version": DB_ANALYSIS_CACHE_VERSION,
        "cache_key": cache_key,
        "db_path": str(db_path),
        "chunk": chunk,
    }
    with analysis_cache_lock:
        save_pickle_cache(cache_path, payload)
        cleanup_stale_db_analysis_cache(db_path, cache_key)


# Сэмплы одного анализа лежат по одному side-файлу на поток (канал). Префикс
# включает ключ анализа — чтобы находить и чистить их вместе с workspace-пиклом.
WS_SAMPLES_KEY_PREFIX_LEN = 16
# Сколько потоков сэмплов держать загруженными в процессе (LRU).
#
# Лимит обязан покрывать число каналов источника. Сборка строк идёт по мойкам в
# порядке ВРЕМЕНИ (sorted_cycles), то есть каналы чередуются, и любой лимит меньше
# числа каналов вытесняется на каждом шаге: N моек → N полных чтений
# многомегабайтного пикла с диска. С прежним значением 4 источник на пяти-шести
# каналах ронял /api/workspace-data в минуты, причём каждые пять минут заново —
# FTP-автообновление меняет ревизию и инвалидирует кэш строк.
#
# 12 — компромисс: покрывает реальные источники с запасом, но не даёт кэшу расти
# бесконечно (потоки многомегабайтные). Источник с бо́льшим числом каналов снова
# начнёт вытеснять — такие редки, и дешевле перечитать, чем держать всё в RAM.
SAMPLE_STREAM_LRU_LIMIT = 12
_sample_stream_cache: "OrderedDict[tuple[str, str], list[core.Sample]]" = OrderedDict()
_sample_stream_cache_lock = threading.Lock()


def ws_samples_path(cache_key: str, stream_key: str) -> Path:
    prefix = cache_key[:WS_SAMPLES_KEY_PREFIX_LEN]
    stream_hash = hashlib.sha1(stream_key.encode("utf-8")).hexdigest()[:16]
    return app_config.ANALYSIS_CACHE_ROOT / f"ws-samples-{prefix}-{stream_hash}.pkl"


def make_sample_loader(cache_key: str) -> Callable[[str], list[core.Sample]]:
    """Ленивый загрузчик потока сэмплов с диска с LRU-кэшем в процессе. Позволяет
    держать в RAM метаданные анализа без всех сэмплов — они читаются по запросу
    графика/оценки концентрации (о размере кэша см. SAMPLE_STREAM_LRU_LIMIT).

    Отсутствие или порча файла — не пустой поток: загрузчик бросает
    SampleStreamUnavailable, см. комментарий у самого исключения."""
    def loader(stream_key: str) -> list[core.Sample]:
        if not stream_key:
            return []
        cache_id = (cache_key, stream_key)
        with _sample_stream_cache_lock:
            cached = _sample_stream_cache.get(cache_id)
            if cached is not None:
                _sample_stream_cache.move_to_end(cache_id)
                return cached

        path = ws_samples_path(cache_key, stream_key)
        # Чтение и unpickle — вне analysis_cache_lock: он глобальный, его же берут
        # фоновый анализ (save_cached_workspace_analysis) и чистка кэша, а распаковка
        # десятков мегабайт под ним заставляла их ждать друг друга. Под локом
        # оставляем только учёт обращения.
        payload = load_pickle_cache(path)
        if not isinstance(payload, list):
            # None — файла нет или подпись не сошлась; не-список — формат побился.
            # Отрицательный результат НЕ кэшируем: файл может появиться снова
            # (переанализ), а закэшированная пустота пережила бы его.
            raise core.SampleStreamUnavailable(
                f"поток сэмплов недоступен: {path.name}"
            )
        with analysis_cache_lock:
            touch_cache_entry(path)
        samples = payload

        with _sample_stream_cache_lock:
            _sample_stream_cache[cache_id] = samples
            _sample_stream_cache.move_to_end(cache_id)
            while len(_sample_stream_cache) > SAMPLE_STREAM_LRU_LIMIT:
                _sample_stream_cache.popitem(last=False)
        return samples

    return loader


def load_cached_workspace_analysis(cache_key: str) -> tuple[core.AnalysisResult, list[str]] | None:
    """Сводный анализ источника из кэша вместе со списком пропущенных (битых)
    баз — при попадании в кэш пользователь должен видеть то же предупреждение."""
    cache_path = workspace_analysis_cache_path(cache_key)
    with analysis_cache_lock:
        payload = load_pickle_cache(cache_path)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != WORKSPACE_ANALYSIS_CACHE_VERSION:
            return None
        if payload.get("cache_key") != cache_key:
            return None

        analysis = payload.get("analysis")
        if not isinstance(analysis, core.AnalysisResult):
            return None
        raw_skipped = payload.get("skipped_db_files")
        skipped = [str(name) for name in raw_skipped] if isinstance(raw_skipped, list) else []
        touch_cache_entry(cache_path)
        # Держим side-файлы сэмплов «тёплыми» рядом с их анализом, чтобы LRU не
        # вытеснил их раньше самого анализа.
        for stream_key in analysis.sample_stream_by_channel.values():
            touch_cache_entry(ws_samples_path(cache_key, stream_key))

    # Сэмплы — лениво с диска (в пикле их нет), метаданные остаются в RAM.
    analysis.sample_loader = make_sample_loader(cache_key)
    return analysis, skipped


def save_cached_workspace_analysis(
    cache_key: str,
    analysis: core.AnalysisResult,
    *,
    source_key: str = "",
    skipped_db_files: list[str] | None = None,
) -> None:
    with analysis_cache_lock:
        # Сэмплы пишем отдельными файлами по потокам, из workspace-пикла их
        # убираем — так метаданные малы, а RAM освобождается (см. ниже).
        for stream_key, samples in analysis.samples_by_db.items():
            save_pickle_cache(ws_samples_path(cache_key, stream_key), samples)

        analysis.samples_by_db = {}
        analysis.sample_loader = make_sample_loader(cache_key)

        payload = {
            "version": WORKSPACE_ANALYSIS_CACHE_VERSION,
            "cache_key": cache_key,
            "analysis": analysis,  # __getstate__ отбросит sample_loader, samples пусты
            "skipped_db_files": list(skipped_db_files or []),
        }
        save_pickle_cache(workspace_analysis_cache_path(cache_key), payload)
        if source_key:
            cleanup_stale_workspace_cache(source_key, cache_key)


# Ключ анализа в имени chart-файла — чтобы графики устаревшего анализа можно
# было найти и удалить по префиксу (см. cleanup_stale_workspace_cache).
CHART_CACHE_KEY_PREFIX_LEN = 16


def chart_payload_disk_cache_key(analysis_cache_key: str, key: str) -> str:
    payload = f"{analysis_cache_key}::{key}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def chart_payload_disk_cache_path(analysis_cache_key: str, key: str) -> Path:
    cache_key = chart_payload_disk_cache_key(analysis_cache_key, key)
    prefix = analysis_cache_key[:CHART_CACHE_KEY_PREFIX_LEN]
    return app_config.ANALYSIS_CACHE_ROOT / f"chart-{prefix}-{cache_key}.pkl"


def load_cached_chart_payload_disk(analysis_cache_key: str, key: str) -> dict[str, Any] | None:
    if not analysis_cache_key:
        return None

    cache_path = chart_payload_disk_cache_path(analysis_cache_key, key)
    with analysis_cache_lock:
        payload = load_pickle_cache(cache_path)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != CHART_PAYLOAD_DISK_CACHE_VERSION:
            return None
        if payload.get("analysis_cache_key") != analysis_cache_key:
            return None
        if payload.get("cycle_key") != key:
            return None

        chart_payload = payload.get("payload")
        if not isinstance(chart_payload, dict):
            return None
        touch_cache_entry(cache_path)
        return chart_payload


def save_cached_chart_payload_disk(analysis_cache_key: str, key: str, payload: dict[str, Any]) -> None:
    if not analysis_cache_key:
        return

    cache_path = chart_payload_disk_cache_path(analysis_cache_key, key)
    serialized_payload = {
        "version": CHART_PAYLOAD_DISK_CACHE_VERSION,
        "analysis_cache_key": analysis_cache_key,
        "cycle_key": key,
        "payload": payload,
    }
    with analysis_cache_lock:
        save_pickle_cache(cache_path, serialized_payload)


# ---- оперативный кэш готовых payload графиков (в памяти) ---------------------
def clear_chart_payload_cache() -> None:
    with chart_payload_cache_lock:
        chart_payload_cache.clear()


def clear_all_chart_caches() -> int:
    """Полностью очищает кэш графиков: и в памяти, и дисковые файлы chart-*.pkl.

    Возвращает число удалённых с диска файлов. Нужна, чтобы пользователь мог
    вручную сбросить графики (например, после смены оформления кривых) — иначе
    старые payload'ы висят в кэше и график рисуется в прежнем виде.
    """
    clear_chart_payload_cache()
    removed = 0
    with analysis_cache_lock:
        for cache_file in app_config.ANALYSIS_CACHE_ROOT.glob("chart-*.pkl"):
            try:
                cache_file.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def cleanup_stale_disk_caches() -> None:
    """Очистка дискового кэша (результаты анализа, готовые графики, распакованные
    базы): протухшее по TTL и всё, что не влезло в бюджет (LRU). Полное удаление
    общих корней недопустимо — их может использовать параллельно работающий
    второй экземпляр приложения."""
    clear_chart_payload_cache()
    prune_archive_cache()
    prune_analysis_cache()


def get_cached_chart_payload(analysis_revision: int, key: str) -> dict[str, Any] | None:
    cache_key = (analysis_revision, key)
    with chart_payload_cache_lock:
        payload = chart_payload_cache.get(cache_key)
        if payload is None:
            return None
        chart_payload_cache.move_to_end(cache_key)
        return payload


def set_cached_chart_payload(analysis_revision: int, key: str, payload: dict[str, Any]) -> None:
    cache_key = (analysis_revision, key)
    with chart_payload_cache_lock:
        chart_payload_cache[cache_key] = payload
        chart_payload_cache.move_to_end(cache_key)
        while len(chart_payload_cache) > CHART_PAYLOAD_CACHE_LIMIT:
            chart_payload_cache.popitem(last=False)
