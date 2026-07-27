"""FTP-клиент: подключение к панели, инкрементальная загрузка архивов в зеркало
datalog/<id>/, помесячная раскладка, ретеншн и синхронизация активной панели.

Выделено из webapp/app.py. DATALOG_ROOT читаем через config (app_config.DATALOG_ROOT),
чтобы тесты подменяли каталог, патча app.config.DATALOG_ROOT; open_ftp_connection
патчится тестами как app.ftp_client.open_ftp_connection (его зовёт download_ftp_files
внутри этого же модуля).
"""
from __future__ import annotations

import ftplib
import hashlib
import logging
import os
import posixpath
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import wash_report as core
from webapp import config as app_config
from webapp import state as state_module
from webapp.config import (
    ARCHIVE_RETENTION_MAX_DAYS,
    ARCHIVE_RETENTION_MIN_DAYS,
    DELETED_PROFILE_DIR_RE,
    FTP_CONNECT_TIMEOUT_SECONDS,
    FTP_DOWNLOAD_MAX_DEPTH,
    FTP_MTIME_TOLERANCE_SECONDS,
    SUPPORTED_ARCHIVE_SUFFIXES,
)
from webapp.state import state, state_lock
from webapp.archives import safe_archive_member_path
from webapp.ftp_registry import (
    connection_to_config,
    find_ftp_connection,
    format_ftp_display_label,
)
from webapp.settings_store import load_app_settings

def is_ftp_profile(path: Path | None) -> bool:
    """Это папка профиля FTP-подключения (datalog/<id>), а не обычная папка?"""
    if path is None:
        return False
    try:
        return path.resolve().parent == app_config.DATALOG_ROOT.resolve()
    except OSError:
        return False



def open_ftp_connection(config: dict[str, Any]) -> ftplib.FTP:
    connection = ftplib.FTP()
    try:
        connection.connect(
            host=config["host"],
            port=int(config["port"]),
            timeout=FTP_CONNECT_TIMEOUT_SECONDS,
        )
        connection.login(user=config["username"], passwd=config["password"])
        connection.set_pasv(bool(config.get("passive", True)))
    except Exception as exc:
        try:
            connection.close()
        except Exception:
            pass
        raise ValueError(
            f"Не удалось подключиться к FTP `{config['host']}:{config['port']}`: {exc}"
        ) from exc
    return connection


def _parse_ftp_timestamp(value: str) -> float | None:
    """Разбирает время FTP формата `YYYYMMDDHHMMSS[.fff]` (UTC) в epoch-секунды."""
    if not value:
        return None
    digits = value.strip()
    if "." in digits:
        digits = digits.split(".", 1)[0]
    if len(digits) < 14 or not digits[:14].isdigit():
        return None
    try:
        parsed = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc).timestamp()


def _parse_mdtm_reply(reply: str) -> float | None:
    """Разбирает ответ команды MDTM вида `213 YYYYMMDDHHMMSS`."""
    if not reply:
        return None
    parts = reply.split()
    if not parts:
        return None
    candidate = parts[-1] if len(parts) > 1 and parts[0].isdigit() else parts[0]
    return _parse_ftp_timestamp(candidate)


def _ftp_remote_size(connection: ftplib.FTP, remote_path: str) -> int | None:
    try:
        return connection.size(remote_path)
    except (ftplib.error_perm, ftplib.error_temp, ftplib.error_proto, OSError):
        return None


def _ftp_remote_mtime(connection: ftplib.FTP, remote_path: str) -> float | None:
    try:
        reply = connection.sendcmd(f"MDTM {remote_path}")
    except (ftplib.error_perm, ftplib.error_temp, ftplib.error_proto, OSError):
        return None
    return _parse_mdtm_reply(reply)


def _ftp_list_entries(
    connection: ftplib.FTP, remote_dir: str
) -> list[tuple[str, bool, dict[str, Any]]]:
    entries: list[tuple[str, bool, dict[str, Any]]] = []
    try:
        for name, facts in connection.mlsd(remote_dir):
            # CR/LF в имени = инъекция FTP-команд (имя попадает в RETR/MDTM).
            # Панель таких имён не отдаёт; битый/вредоносный ответ отсекаем.
            if name in {"", ".", ".."} or "\r" in name or "\n" in name:
                continue
            entry_type = str(facts.get("type") or "").lower()
            if entry_type in {"dir", "cdir", "pdir"}:
                entries.append((name, True, {}))
            elif entry_type == "file":
                meta: dict[str, Any] = {}
                size_raw = facts.get("size")
                if size_raw is not None:
                    try:
                        meta["size"] = int(size_raw)
                    except (TypeError, ValueError):
                        pass
                modify_raw = facts.get("modify")
                if modify_raw:
                    mtime = _parse_ftp_timestamp(str(modify_raw))
                    if mtime is not None:
                        meta["mtime"] = mtime
                entries.append((name, False, meta))
        return entries
    except (ftplib.error_perm, ftplib.error_proto, ftplib.error_temp, OSError, UnicodeDecodeError):
        # MLSD мог отдать часть записей и упасть посреди потока (в т.ч.
        # UnicodeDecodeError на имени в чужой кодировке): сбрасываем частичный
        # результат, иначе NLST-фолбэк допишет к нему дубли.
        entries = []

    # MLSD не поддерживается — берём NLST и определяем тип по SIZE.
    try:
        names = connection.nlst(remote_dir)
    except (ftplib.error_perm, ftplib.error_temp, OSError, UnicodeDecodeError):
        return []

    for raw_name in names:
        name = PurePosixPath(raw_name).name
        if name in {"", ".", ".."} or "\r" in raw_name or "\n" in raw_name:
            continue  # CR/LF = инъекция FTP-команд (см. выше)
        full = raw_name if raw_name.startswith("/") else posixpath.join(remote_dir, name)
        meta: dict[str, Any] = {}
        # Имя, похожее на данные (.db/архив), — это ФАЙЛ, даже если SIZE запрещён
        # правами: иначе его приняли бы за каталог, рекурсия в него упала бы, и
        # файл молча пропал бы из зеркала.
        looks_like_data = _is_archive_or_db_name(name)
        try:
            size_value = connection.size(full)
            is_dir = False
        except (ftplib.error_perm, ftplib.error_temp):
            # SIZE запрещён/неприменим: каталог — только если имя не выглядит данными.
            is_dir = not looks_like_data
            size_value = None
        except OSError:
            is_dir = False
            size_value = None
        if not is_dir and size_value is not None:
            meta["size"] = size_value
        entries.append((name, is_dir, meta))
    return entries


def _ftp_walk_files(
    connection: ftplib.FTP,
    remote_dir: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
    depth: int = 0,
) -> list[tuple[str, dict[str, Any]]]:
    if depth > FTP_DOWNLOAD_MAX_DEPTH:
        return []

    discovered: list[tuple[str, dict[str, Any]]] = []
    for name, is_dir, meta in _ftp_list_entries(connection, remote_dir):
        if cancel_check is not None and cancel_check():
            raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")
        full = posixpath.join(remote_dir, name)
        if is_dir:
            discovered.extend(
                _ftp_walk_files(connection, full, cancel_check=cancel_check, depth=depth + 1)
            )
        else:
            discovered.append((full, meta))
    return discovered


def _ftp_relative_target(remote_root: str, remote_file: str) -> Path:
    root = remote_root.rstrip("/")
    relative = remote_file
    if root and remote_file.startswith(root + "/"):
        relative = remote_file[len(root) + 1 :]
    relative = relative.lstrip("/")
    safe_path = safe_archive_member_path(relative)
    if safe_path is None:
        # Запасной вариант: базовое имя + короткий хеш полного пути. Без хеша два
        # разных удалённых файла с одинаковым именем (из разных папок) затирали бы
        # друг друга в зеркале. Хеш вставляем перед расширением, чтобы имя всё ещё
        # распознавалось как .db/архив.
        base = PurePosixPath(remote_file.replace("\\", "/")).name.replace(":", "_")
        digest = hashlib.sha1(remote_file.encode("utf-8")).hexdigest()[:8]
        if base:
            dot = base.rfind(".")
            if dot > 0:
                fallback = f"{base[:dot]}-{digest}{base[dot:]}"
            else:
                fallback = f"{base}-{digest}"
        else:
            fallback = f"download-{digest}.db"
        return Path(fallback)
    return safe_path


def _is_archive_or_db_name(name: str) -> bool:
    """Похоже ли имя на базу `.db` или поддерживаемый архив."""
    lower_name = name.lower()
    return lower_name.endswith(".db") or any(
        lower_name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES
    )


def iter_tree_files(root_path: Path) -> Any:
    """Один обход дерева: отдаёт (путь, относительный posix-путь, stat). Служит
    общей основой для индекса зеркала, ретеншна и подсчёта объёма — раньше каждая
    из этих операций делала свой полный rglob по datalog."""
    for current_root, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [name for name in dirnames if not DELETED_PROFILE_DIR_RE.search(name)]
        current_path = Path(current_root)
        try:
            relative_root = current_path.relative_to(root_path)
        except ValueError:
            relative_root = Path()
        for filename in filenames:
            candidate = current_path / filename
            try:
                stat_result = candidate.stat()
            except OSError:
                continue
            yield candidate, (relative_root / filename).as_posix(), stat_result


# Осиротевшие `.part-<uuid>` старше этого возраста считаем брошенными (краш/питание
# во время докачки). Порог с большим запасом над временем скачивания одной базы:
# активная докачка обновляет mtime, поэтому свежую времянку не заденем.
STALE_PART_FILE_AGE_SECONDS = 30 * 60


def sweep_stale_part_files(root_path: Path) -> int:
    """Удаляет брошенные `.part-<uuid>` в зеркале. Их не видит ретеншн (не .db/не
    архив), и они вечно держат месячные папки «непустыми» (rmdir не срабатывает).
    Возвращает число удалённых."""
    cutoff = time.time() - STALE_PART_FILE_AGE_SECONDS
    removed = 0
    for candidate, _rel, stat_result in iter_tree_files(root_path):
        if ".part-" not in candidate.name or stat_result.st_mtime >= cutoff:
            continue
        try:
            candidate.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logging.info("Убрано брошенных .part-времянок: %d (%s)", removed, root_path)
    return removed


def build_local_archive_index(root_path: Path) -> dict[Any, dict[str, Any]]:
    """Индекс уже скачанных архивов/баз под `root_path` для пропуска повторных загрузок.

    Ключи двух видов: относительный путь (совпадает с `_ftp_relative_target`)
    для файлов в зеркале и кортеж `("name", имя, размер)` — чтобы распознавать
    копии, лежащие в старых подпапках-по-дате. Значение: `size`, `mtime`, `path`.

    Путь файла не резолвим: `resolve()` на каждый файл — это отдельный системный
    вызов на элемент, а зеркало и так строится относительно `root_path`.
    """
    index: dict[Any, dict[str, Any]] = {}
    for candidate, rel_key, stat_result in iter_tree_files(root_path):
        if not _is_archive_or_db_name(candidate.name):
            continue
        entry = {"size": stat_result.st_size, "mtime": stat_result.st_mtime, "path": candidate}
        index.setdefault(rel_key, entry)
        index.setdefault(("name", candidate.name, stat_result.st_size), entry)
    return index


def _should_skip_download(
    local_meta: dict[str, Any] | None,
    remote_size: int | None,
    remote_mtime: float | None,
) -> bool:
    """Можно ли не скачивать файл: локальная копия есть, размер совпал и она не старше панели."""
    if local_meta is None:
        return False
    if remote_size is None:
        # Панель не сообщает размер (нет SIZE/MLSD) — сравниваем только время
        # модификации, иначе всё перекачивалось бы при каждой синхронизации.
        local_mtime = local_meta.get("mtime")
        if remote_mtime is None or local_mtime is None:
            return False
        return local_mtime + FTP_MTIME_TOLERANCE_SECONDS >= remote_mtime
    local_size = local_meta.get("size")
    if local_size is None or int(local_size) != int(remote_size):
        return False
    if remote_mtime is None:
        return True
    local_mtime = local_meta.get("mtime")
    if local_mtime is None:
        return True
    return local_mtime + FTP_MTIME_TOLERANCE_SECONDS >= remote_mtime


def archive_month_folder(mtime: float | None) -> str:
    """Имя папки месяца (ГГГГ-ММ) по времени файла; 'unknown' если времени нет."""
    if mtime is None:
        return "unknown"
    try:
        return time.strftime("%Y-%m", time.localtime(mtime))
    except (OverflowError, OSError, ValueError):
        return "unknown"


def cleanup_old_archives(root_path: Path, retention_days: int) -> dict[str, int]:
    """Удаляет распознанные архивы/`.db` старше `retention_days` (по mtime файла)
    под `root_path` и убирает опустевшие подпапки. Возвращает статистику
    {'removed', 'freed_bytes'}. Служебные файлы (wash_*.json, кэш) не трогает.

    Удаление файлов и уборка пустых папок делаются одним обходом снизу вверх
    (topdown=False), а не двумя полными проходами по дереву."""
    removed = 0
    freed = 0
    days = max(ARCHIVE_RETENTION_MIN_DAYS, min(ARCHIVE_RETENTION_MAX_DAYS, int(retention_days)))
    cutoff = time.time() - days * 86400

    for current_root, dirnames, filenames in os.walk(root_path, topdown=False):
        current_path = Path(current_root)
        for filename in filenames:
            candidate = current_path / filename
            if not _is_archive_or_db_name(filename):
                continue
            try:
                stat_result = candidate.stat()
                if stat_result.st_mtime >= cutoff:
                    continue
                size = stat_result.st_size
                candidate.unlink()
            except OSError:
                continue
            removed += 1
            freed += size

        for dirname in dirnames:
            directory = current_path / dirname
            try:
                directory.rmdir()  # сработает только для опустевшей папки
            except OSError:
                continue

    if removed:
        logging.info(
            "Автоочистка архивов: удалено %d файлов, освобождено %d байт (%s)",
            removed,
            freed,
            root_path,
        )
    return {"removed": removed, "freed_bytes": freed}


def directory_size_bytes(root_path: Path) -> int:
    return sum(stat_result.st_size for _path, _rel, stat_result in iter_tree_files(root_path))


# Размер datalog для /api/diagnostics: полный обход дерева на каждое открытие
# диагностики слишком дорог, поэтому значение кэшируется на короткий TTL.
DATALOG_SIZE_CACHE_TTL_SECONDS = 60.0
_datalog_size_cache_lock = threading.Lock()
_datalog_size_cache: dict[str, float] = {"ts": 0.0, "value": 0}


def datalog_size_bytes_cached() -> int:
    now = time.monotonic()
    with _datalog_size_cache_lock:
        if _datalog_size_cache["ts"] and now - _datalog_size_cache["ts"] < DATALOG_SIZE_CACHE_TTL_SECONDS:
            return int(_datalog_size_cache["value"])
    value = directory_size_bytes(app_config.DATALOG_ROOT)
    with _datalog_size_cache_lock:
        _datalog_size_cache["ts"] = now
        _datalog_size_cache["value"] = value
    return value


@dataclass
class FtpSyncResult:
    """Итог синхронизации с панелью: что есть локально, что скачали, что не смогли."""

    present_files: list[Path] = field(default_factory=list)
    downloaded: int = 0
    skipped: int = 0
    failed_files: list[str] = field(default_factory=list)
    # Синхронизация целиком не удалась (FTP недоступен), но локальное зеркало есть.
    ftp_error_message: str = ""


def is_ftp_connection_lost(exc: BaseException) -> bool:
    """Сбой уровня сессии (соединение потеряно) — в отличие от ошибки на
    конкретном файле (нет прав, файл занят), после которой качать дальше можно."""
    if isinstance(exc, ftplib.error_temp):
        # 421 — сервер закрывает управляющее соединение; прочие 4xx — по файлу.
        return str(exc).strip().startswith("421")
    if isinstance(exc, ftplib.error_perm):
        return False
    return isinstance(exc, (OSError, EOFError, ftplib.error_proto, ftplib.error_reply))


def download_ftp_files(
    config: dict[str, Any],
    target_dir: Path,
    *,
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> FtpSyncResult:
    remote_root = config.get("path") or "/"
    core.emit_progress(
        progress_callback,
        phase="ftp",
        message="Подключаюсь к FTP-серверу.",
        current=0,
        total=0,
        item=format_ftp_display_label(config),
    )
    # Убираем брошенные при прошлом крахе .part-времянки до обхода зеркала.
    sweep_stale_part_files(target_dir)
    connection = open_ftp_connection(config)
    # Все файлы панели, которые сейчас представлены локально (скачанные + пропущенные).
    result = FtpSyncResult()
    present_files = result.present_files
    local_index = build_local_archive_index(target_dir)

    # Где файл с таким базовым именем уже лежит в зеркале (в какой месячной
    # папке). Активно дописываемый файл (например, `Canal_*.db`) в новом месяце
    # должен качаться поверх старой копии, а не плодить дубликаты по месяцам.
    # Формат `ГГГГ-ММ-ДД` — раскладка прежних версий: без него файл скачивался
    # заново в `ГГГГ-ММ/`, а старая копия оставалась в зеркале навсегда.
    month_dir_re = re.compile(r"^(?:\d{4}-\d{2}(?:-\d{2})?|unknown)$")
    existing_month_locations: dict[str, Path] = {}
    for index_key in local_index:
        if not isinstance(index_key, str):
            continue
        key_parts = PurePosixPath(index_key).parts
        if len(key_parts) >= 2 and month_dir_re.fullmatch(key_parts[0]):
            existing_month_locations.setdefault(
                PurePosixPath(*key_parts[1:]).as_posix(), Path(index_key)
            )

    # При включённом ретеншне не качаем файлы старше срока хранения — иначе
    # удалённые очисткой архивы возвращались бы при каждой синхронизации.
    settings = load_app_settings()
    retention_cutoff = (
        time.time() - settings["archive_retention_days"] * 86400
        if settings["archive_retention_enabled"]
        else None
    )
    try:
        try:
            connection.voidcmd("TYPE I")
        except (ftplib.error_perm, ftplib.error_temp, OSError):
            pass

        remote_files = [
            (remote_file, meta)
            for remote_file, meta in _ftp_walk_files(
                connection, remote_root, cancel_check=cancel_check
            )
            if _is_archive_or_db_name(remote_file)
        ]

        total = len(remote_files)
        for index, (remote_file, meta) in enumerate(remote_files, start=1):
            if cancel_check is not None and cancel_check():
                raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

            remote_size = meta.get("size")
            if remote_size is None:
                remote_size = _ftp_remote_size(connection, remote_file)
            remote_mtime = meta.get("mtime")
            if remote_mtime is None:
                remote_mtime = _ftp_remote_mtime(connection, remote_file)

            # Файлы старше срока хранения не скачиваем (см. retention_cutoff выше).
            if retention_cutoff is not None and remote_mtime is not None and remote_mtime < retention_cutoff:
                continue

            # Помесячная раскладка: datalog/<id>/ГГГГ-ММ/<файл> по времени файла.
            # Если файл уже лежит в другой месячной папке — обновляем его там,
            # не создавая вторую копию в папке нового месяца.
            base_target = _ftp_relative_target(remote_root, remote_file)
            relative_target = existing_month_locations.get(base_target.as_posix())
            if relative_target is None:
                relative_target = Path(archive_month_folder(remote_mtime)) / base_target
                existing_month_locations[base_target.as_posix()] = relative_target
            target_path = target_dir / relative_target
            target_path.parent.mkdir(parents=True, exist_ok=True)

            rel_key = relative_target.as_posix()
            local_meta = local_index.get(rel_key)
            if local_meta is None and remote_size is not None:
                local_meta = local_index.get(("name", relative_target.name, remote_size))

            if _should_skip_download(local_meta, remote_size, remote_mtime):
                result.skipped += 1
                core.emit_progress(
                    progress_callback,
                    phase="ftp",
                    message=f"Файл {index} из {total} не изменился, пропускаю.",
                    current=index - 1,
                    total=total,
                    item=relative_target.name,
                )
                existing_path = local_meta.get("path") if local_meta else None
                present_files.append(Path(existing_path) if existing_path else target_path)
                continue

            core.emit_progress(
                progress_callback,
                phase="ftp",
                message=f"Скачиваю файл {index} из {total} с FTP.",
                current=index - 1,
                total=total,
                item=relative_target.name,
            )

            # Качаем во временный файл `.part-<uuid>` (сканеры архивов его не
            # видят — см. _is_archive_or_db_name) и подменяем целевой только
            # после успешной загрузки: обрыв связи не оставит усечённую базу.
            # Суффикс уникален: два потока, качающих один файл, иначе писали бы
            # в общий `.part` и получалась битая база.
            part_path = target_path.with_name(f"{target_path.name}.part-{uuid.uuid4().hex}")
            try:
                with part_path.open("wb") as handle:

                    def _write_chunk(chunk: bytes) -> None:
                        # Проверка отмены прямо в потоке данных, чтобы отмена
                        # прерывала и передачу большого файла.
                        if cancel_check is not None and cancel_check():
                            raise core.AnalysisCancelledError(
                                "Открытие источника было отменено пользователем."
                            )
                        handle.write(chunk)

                    connection.retrbinary(f"RETR {remote_file}", _write_chunk)
                os.replace(part_path, target_path)
            except core.AnalysisCancelledError:
                part_path.unlink(missing_ok=True)
                raise
            except Exception as exc:
                part_path.unlink(missing_ok=True)
                if is_ftp_connection_lost(exc):
                    raise SystemExit(
                        f"Соединение с FTP потеряно при скачивании `{remote_file}`: {exc}"
                    ) from exc
                # Сбой на конкретном файле не должен обрывать всю синхронизацию:
                # иначе остальные файлы не скачались бы никогда. Помечаем файл
                # как неудавшийся (он перекачается на следующем проходе) и идём дальше.
                logging.warning("Не удалось скачать файл `%s` с FTP: %s", remote_file, exc)
                result.failed_files.append(relative_target.name)
                core.emit_progress(
                    progress_callback,
                    phase="ftp",
                    message=f"Файл {index} из {total} не скачан ({exc}); продолжаю.",
                    current=index,
                    total=total,
                    item=relative_target.name,
                )
                continue

            # Сохраняем время панели, чтобы на следующих запусках сравнение по времени работало.
            if remote_mtime is not None:
                try:
                    os.utime(target_path, (remote_mtime, remote_mtime))
                except OSError:
                    pass

            result.downloaded += 1
            present_files.append(target_path)
            # Обновляем индекс, чтобы дубликаты в этом же прогоне тоже пропускались.
            try:
                stat_result = target_path.stat()
                fresh_entry = {
                    "size": stat_result.st_size,
                    "mtime": stat_result.st_mtime,
                    "path": target_path,
                }
                local_index[rel_key] = fresh_entry
                local_index[("name", relative_target.name, stat_result.st_size)] = fresh_entry
            except OSError:
                pass
    finally:
        try:
            connection.quit()
        except Exception:
            try:
                connection.close()
            except Exception:
                pass

    failed_note = f", не удалось {len(result.failed_files)}" if result.failed_files else ""
    core.emit_progress(
        progress_callback,
        phase="ftp",
        message=(
            f"Файлы с FTP получены: скачано {result.downloaded}, "
            f"пропущено {result.skipped} (без изменений){failed_note}."
        ),
        current=len(present_files),
        total=len(present_files),
        item=f"{result.downloaded} новых из {len(present_files)}",
    )
    return result


def datalog_has_archives(root_path: Path) -> bool:
    """Есть ли в datalog уже скачанные базы `.db` или архивы (за любую дату)."""
    for candidate, _rel, _stat in iter_tree_files(root_path):
        if _is_archive_or_db_name(candidate.name):
            return True
    return False


def materialize_ftp_sources(
    root_path: Path,
    *,
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> FtpSyncResult:
    """Синхронизирует архивы активной FTP-панели в зеркало `datalog/<id>/`.

    Папка профиля определяется по `root_path` (это `datalog/<id>`); параметры
    подключения берутся из реестра по `id`. Загрузка инкрементальная: файлы,
    уже скачанные и не изменившиеся на панели (совпал размер и время не новее),
    повторно не качаются (см. `download_ftp_files`). Если FTP недоступен, но
    локальные архивы уже есть — работаем с ними, но сам сбой не «глотаем»: он
    возвращается в `ftp_error` и показывается пользователю. Для обычной папки
    (folder mode) функция ничего не делает."""
    if not is_ftp_profile(root_path):
        return FtpSyncResult()

    connection = find_ftp_connection(root_path.name)
    if connection is None:
        return FtpSyncResult()
    config = connection_to_config(connection)

    # Зеркало панели: качаем в профиль с помесячной раскладкой (ГГГГ-ММ по
    # времени файла, см. download_ftp_files); уже скачанные файлы находятся по
    # индексу зеркала, поэтому проверка «файл уже есть» работает между запусками.
    download_dir = root_path
    download_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = download_ftp_files(
            config,
            download_dir,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except core.AnalysisCancelledError:
        raise
    except (ValueError, SystemExit, OSError) as exc:
        message = str(exc) or "FTP недоступен."
        logging.warning("Синхронизация с FTP не удалась: %s", message)
        if datalog_has_archives(root_path):
            core.emit_progress(
                progress_callback,
                phase="ftp",
                message=f"FTP недоступен ({message}); использую ранее скачанные архивы.",
                item=format_ftp_display_label(config),
            )
            return FtpSyncResult(ftp_error_message=message)
        raise SystemExit(
            f"Не удалось скачать архивы с FTP, и локальных архивов в `datalog` нет: {message}"
        ) from exc

    # Автоочистка архивов старше срока хранения (только для FTP-зеркала).
    # «Последнюю очистку» отмечаем только если реально что-то удалили — иначе
    # поле показывало бы время каждого подключения, хотя ничего не чистилось.
    settings = load_app_settings()
    if settings["archive_retention_enabled"]:
        cleanup_result = cleanup_old_archives(root_path, settings["archive_retention_days"])
        if cleanup_result["removed"]:
            with state_lock:
                state.last_cleanup_ts = time.time()

    return result
