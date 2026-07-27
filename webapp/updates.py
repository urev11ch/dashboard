"""Проверка и скачивание обновлений приложения (GitHub Releases).

Разбор/сравнение версий, выбор вложения-установщика с проверкой URL и sha256 и
фоновый воркер загрузки. Выделено из webapp/app.py. Роуты остаются в app.py и
обращаются сюда как updates.<функция>; тесты патчат символы как
app.updates.<имя> (например app.updates._fetch_latest_release, ._update_dir).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from runtime_paths import resolve_cache_root

from webapp.config import (
    GITHUB_REPO,
    UPDATE_ASSET_NAME,
    UPDATE_ASSET_URL_PREFIX,
    RELEASE_JSON_MAX_BYTES,
    UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
    UPDATE_MAX_BYTES,
)
from webapp.state import UpdateJob, state, state_lock

# Ведущий X[.Y[.Z[.W]]] с необязательным префиксом v. Суффиксы (-rc.2, «(hotfix)»)
# в сравнение версий не идут — см. _parse_version.
_VERSION_RE = re.compile(r"\s*v?(\d+(?:\.\d+){0,3})", re.IGNORECASE)


def _parse_version(value: str) -> tuple[int, ...]:
    """Числовой кортеж версии для сравнения.

    Раньше здесь был re.findall(r"\\d+"), который выгребал ВСЕ числа строки:
    «1.1.8-rc.2» превращалось в (1,1,8,2) и оказывалось новее релиза «1.1.8» —
    то есть pre-release обгонял релиз. Сегодня это недостижимо (releases/latest
    пропускает pre-release, а CI сверяет тег с __version__), но цена ошибки —
    рассылка rc всем клиентам, поэтому разбираем только ведущий X.Y.Z.
    """
    match = _VERSION_RE.match(str(value or ""))
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def _is_newer_version(latest: str, current: str) -> bool:
    try:
        a = _parse_version(latest)
        b = _parse_version(current)
        # Выравниваем длину нулями: иначе (1,2,0) > (1,2) и «1.2.0» ложно
        # считается новее равной «1.2», предлагая лишнее обновление.
        n = max(len(a), len(b))
        a += (0,) * (n - len(a))
        b += (0,) * (n - len(b))
        return a > b
    except Exception:
        return False


def _fetch_latest_release() -> dict[str, Any]:
    """Payload последнего релиза на GitHub или {} при недоступности/отсутствии
    релизов. Запрос анонимный — репозиторий публичный; приватный отдал бы 404."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "OptiCIP-Dashboard",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            # Читаем с потолком: тело от GitHub — TLS-проверенное, но не даём
            # враждебному/битому ответу раздуть память (бинарь капается отдельно
            # через UPDATE_MAX_BYTES; здесь у JSON своего лимита не было).
            raw = response.read(RELEASE_JSON_MAX_BYTES + 1)
            if len(raw) > RELEASE_JSON_MAX_BYTES:
                return {}
            payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _release_tag(payload: dict[str, Any]) -> str:
    tag = str(payload.get("tag_name") or "").strip()
    return tag[1:] if tag[:1].lower() == "v" else tag


def _pick_installer_asset(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Вложение-установщик из payload релиза: URL, размер и sha256 от GitHub.

    Ссылку берём ТОЛЬКО отсюда и никогда от клиента — иначе приложение
    скачивало бы и запускало произвольный URL с правами администратора.
    Без digest вложение не годится: проверить нечем, а запускать непроверенный
    .exe нельзя.

    ОСТАТОЧНЫЙ РИСК (доверие к источнику): sha256 берётся из поля digest того же
    ответа GitHub, что отдаёт и бинарник. Это защищает от искажения в сети (TLS +
    сверка суммы), но НЕ от компрометации репозитория/CI-токена: получив контроль
    над релизами, атакующий опубликует согласованные .exe и digest, и клиенты
    молча их установят. Для защиты от этого нужна независимая от GitHub проверка —
    Authenticode-подпись установщика (WinVerifyTrust/signtool) или встроенный
    публичный ключ и отдельная подпись артефакта. Здесь не реализовано намеренно:
    требует Windows-окружения для проверки и подписи в CI; отслеживается отдельно.
    """
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("name") != UPDATE_ASSET_NAME:
            continue
        url = str(asset.get("browser_download_url") or "")
        digest = str(asset.get("digest") or "")
        size = asset.get("size")
        if not url.startswith(UPDATE_ASSET_URL_PREFIX):
            logging.warning("Вложение релиза с неожиданным URL — пропускаю: %s", url)
            continue
        if not digest.startswith("sha256:") or len(digest) != len("sha256:") + 64:
            logging.warning("У вложения релиза нет корректного sha256 — обновление недоступно.")
            continue
        if not isinstance(size, int) or size <= 0:
            continue
        return {"url": url, "size": size, "sha256": digest.split(":", 1)[1].lower()}
    return None


def _update_dir() -> Path:
    """Приватный каталог под скачанный установщик (0700, как кэш): файл
    исполняется с правами администратора, поэтому лежать в общедоступном
    временном каталоге он не должен — иначе его можно подменить между
    проверкой sha256 и запуском."""
    target = resolve_cache_root("wash_journal_update")
    target.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(target, 0o700)
    return target


def _serialize_update_job(job: UpdateJob | None) -> dict[str, Any]:
    if job is None:
        return {"active": False, "status": "idle"}
    return {
        "active": job.status == "running",
        "status": job.status,
        "phase": job.phase,
        "version": job.version,
        "downloaded": job.downloaded,
        "total": job.total,
        "ready": job.status == "ready",
        "error": job.error,
    }


def _update_job_progress(job_id: str, **fields: Any) -> bool:
    """Обновляет поля задачи, если она всё ещё актуальна. False — задачу
    вытеснила новая, поток должен свернуться."""
    with state_lock:
        job = state.update_job
        if job is None or job.id != job_id:
            return False
        for key, value in fields.items():
            setattr(job, key, value)
        return True


def download_update_worker(job_id: str, asset: dict[str, Any], version: str) -> None:
    # Пролог (mkdir/chmod каталога и чистка старых файлов) обязан быть ВНУТРИ try:
    # он делает файловые операции и может кинуть OSError (нет места, права,
    # каталог занят). Раньше он стоял снаружи — поток умирал молча, задача
    # оставалась в статусе running навсегда, фронт крутил опрос вечно, а повторную
    # попытку запрещал guard в /api/update/download до перезапуска приложения.
    tmp_target: Path | None = None
    digest = hashlib.sha256()
    downloaded = 0
    try:
        target_dir = _update_dir()
        # Имя с версией: разные обновления не затирают друг друга, а старое не
        # выдаётся за новое, если версия сменилась между запусками.
        target = target_dir / f"OptiCIP-Dashboard-Setup-{version}.exe"
        # Времянка уникальна по job_id: вытесненная задача не должна удалить .part
        # той, что её сменила (обе видят один и тот же каталог и версию).
        tmp_target = target.with_suffix(f".{job_id}.part")

        # Установщики по ~22 МБ копились бы с каждым обновлением: перед новой
        # загрузкой сносим всё лишнее. Каталог наш и содержит только эти файлы.
        for stale in target_dir.iterdir():
            if stale != target and stale != tmp_target:
                try:
                    stale.unlink()
                except OSError:
                    logging.warning("Не удалось удалить старый файл обновления: %s", stale)

        request = urllib.request.Request(
            asset["url"], headers={"User-Agent": "OptiCIP-Dashboard"}
        )
        with urllib.request.urlopen(
            request, timeout=UPDATE_DOWNLOAD_TIMEOUT_SECONDS
        ) as response, open(tmp_target, "wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > UPDATE_MAX_BYTES:
                    raise ValueError("Ответ больше допустимого размера обновления.")
                digest.update(chunk)
                handle.write(chunk)
                if not _update_job_progress(job_id, downloaded=downloaded):
                    tmp_target.unlink(missing_ok=True)
                    return

        if not _update_job_progress(job_id, phase="verify"):
            tmp_target.unlink(missing_ok=True)
            return

        actual = digest.hexdigest()
        if not hmac.compare_digest(actual, asset["sha256"]):
            logging.error(
                "sha256 обновления не совпал: ожидали %s, получили %s", asset["sha256"], actual
            )
            raise ValueError("Контрольная сумма не совпала — файл повреждён или подменён.")
        if downloaded != asset["size"]:
            raise ValueError("Размер файла не совпал с заявленным в релизе.")
        logging.info("Обновление %s скачано и проверено: %s", version, target)

        # Переименование — последний шаг: файл под финальным именем существует
        # только целиком проверенным.
        tmp_target.replace(target)
        if os.name != "nt":
            os.chmod(target, 0o700)
        if not _update_job_progress(
            job_id,
            status="ready",
            phase="ready",
            path=str(target),
            sha256=actual,
            finished_at=time.time(),
        ):
            target.unlink(missing_ok=True)
    except Exception as error:  # noqa: BLE001 — пользователю нужен текст, а не трейс
        logging.exception("Не удалось скачать обновление")
        if tmp_target is not None:
            tmp_target.unlink(missing_ok=True)
        _update_job_progress(
            job_id,
            status="error",
            phase="error",
            error=str(error) or "Не удалось скачать обновление.",
            finished_at=time.time(),
        )
