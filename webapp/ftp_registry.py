"""Реестр сохранённых FTP-подключений (несколько панелей) и нормализация их
настроек.

Читаем-модифицируем-пишем реестр под локом, чтобы параллельные запросы не теряли
изменения друг друга (lost update). Пароли в реестре защищены (secrets_store),
каждой панели выделяется папка datalog/<id>. Выделено из webapp/app.py.

TEMP_ROOT/DATALOG_ROOT берём динамически через модуль config (app_config.*),
чтобы тесты могли их подменить, патча app.config.TEMP_ROOT/DATALOG_ROOT.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from webapp import config as app_config
from webapp import state as state_module
from webapp.config import (
    DELETED_PROFILE_DIR_RE,
    FTP_CONNECTION_ID_RE,
    FTP_DEFAULT_PORT,
    FTP_HISTORY_USERNAME,
    FTP_HOST_RE,
    FTP_SOURCE_CONFIG_VERSION,
    FTP_SOURCES_FILENAME,
    FTP_SOURCES_VERSION,
    PROFILE_DELETE_JOIN_TIMEOUT_SECONDS,
)
from webapp.io_utils import atomic_write_json
from webapp.secrets_store import _keyring_delete, protect_secret, unprotect_secret


def format_ftp_display_label(config: dict[str, Any]) -> str:
    host = str(config.get("host") or "").strip()
    port = int(config.get("port") or FTP_DEFAULT_PORT)
    path = str(config.get("path") or "/").strip() or "/"
    return f"FTP · {host}:{port}{path}"


ftp_sources_lock = threading.Lock()


def ftp_sources_path() -> Path:
    return app_config.TEMP_ROOT / FTP_SOURCES_FILENAME


def ftp_connection_id(config: dict[str, Any]) -> str:
    payload = f"{config['host']}|{config['port']}|{config['username']}|{config['path']}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def load_ftp_sources_registry() -> dict[str, Any]:
    try:
        payload = json.loads(ftp_sources_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    connections = payload.get("connections")
    if not isinstance(connections, list):
        connections = []
    cleaned = [c for c in connections if isinstance(c, dict) and c.get("id")]
    return {
        "version": FTP_SOURCES_VERSION,
        "active_id": payload.get("active_id"),
        "connections": cleaned,
    }


def save_ftp_sources_registry(registry: dict[str, Any]) -> None:
    path = ftp_sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, registry)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def upsert_ftp_connection(config: dict[str, Any], label: str = "") -> dict[str, Any]:
    conn_id = ftp_connection_id(config)
    entry = {
        "id": conn_id,
        "label": (label or "").strip() or format_ftp_display_label(config),
        "host": config["host"],
        "port": config["port"],
        "username": config["username"],
        "password_enc": protect_secret(config.get("password", ""), secret_id=conn_id),
        "path": config["path"],
        "passive": bool(config.get("passive", True)),
        "web_scheme": config.get("web_scheme", ""),
    }
    with ftp_sources_lock:
        registry = load_ftp_sources_registry()
        registry["connections"] = [c for c in registry["connections"] if c.get("id") != conn_id]
        registry["connections"].append(entry)
        registry["active_id"] = conn_id
        save_ftp_sources_registry(registry)
    return entry


def find_ftp_connection(conn_id: str) -> dict[str, Any] | None:
    if not conn_id:
        return None
    for conn in load_ftp_sources_registry()["connections"]:
        if conn.get("id") == conn_id:
            return conn
    return None


def connection_to_config(conn: dict[str, Any]) -> dict[str, Any]:
    return normalize_ftp_connection_settings(
        {
            "host": conn.get("host"),
            "port": conn.get("port"),
            "username": conn.get("username"),
            "password": unprotect_secret(conn.get("password_enc", "")),
            "path": conn.get("path"),
            "passive": conn.get("passive", True),
            "web_scheme": conn.get("web_scheme", ""),
        }
    )


def purge_deleted_profile_dirs() -> None:
    """Удаляет папки `<id>.deleted-<uuid>`, оставшиеся от отложенного удаления
    профилей (например, если приложение закрыли до завершения уборки)."""
    try:
        candidates = list(app_config.DATALOG_ROOT.iterdir())
    except OSError:
        return
    for candidate in candidates:
        if candidate.is_dir() and DELETED_PROFILE_DIR_RE.search(candidate.name):
            shutil.rmtree(candidate, ignore_errors=True)


def remove_ftp_profile_dir(conn_id: str) -> None:
    """Удаляет папку профиля FTP-подключения.

    В неё может прямо сейчас писать рабочий поток (синхронизация зеркала),
    поэтому папку сначала переименовываем в `<id>.deleted-<uuid>` (её больше не
    видит ни один сканер), а физическое удаление откладываем до завершения
    потока. Так rmtree не выдёргивает файлы из-под работающей загрузки."""
    datalog_root = app_config.DATALOG_ROOT
    profile_dir = datalog_root / conn_id
    try:
        if not profile_dir.exists() or profile_dir.resolve().parent != datalog_root.resolve():
            return
    except OSError:
        return

    trash_dir = datalog_root / f"{conn_id}.deleted-{uuid.uuid4().hex}"
    try:
        profile_dir.rename(trash_dir)
    except OSError:
        # Переименовать не вышло (Windows держит открытый файл) — удаляем на месте,
        # но всё равно после завершения рабочего потока.
        trash_dir = profile_dir

    # Активный рабочий поток анализа живёт в state.py — читаем как атрибут модуля.
    worker = state_module._workspace_job_thread

    def _purge() -> None:
        if worker is not None and worker.is_alive():
            worker.join(timeout=PROFILE_DELETE_JOIN_TIMEOUT_SECONDS)
        shutil.rmtree(trash_dir, ignore_errors=True)

    if worker is not None and worker.is_alive():
        threading.Thread(target=_purge, name="wash-profile-cleanup", daemon=True).start()
    else:
        shutil.rmtree(trash_dir, ignore_errors=True)


def rename_ftp_connection(conn_id: str, label: str) -> bool:
    """Меняет отображаемое имя сохранённой панели. Пустое имя — сбрасываем на
    автолейбл (host:port/path). True, если запись найдена и обновлена."""
    label = (label or "").strip()
    with ftp_sources_lock:
        registry = load_ftp_sources_registry()
        updated = False
        for conn in registry["connections"]:
            if conn.get("id") == conn_id:
                conn["label"] = label or conn.get("host") or "Панель"
                updated = True
                break
        if updated:
            save_ftp_sources_registry(registry)
    return updated


def delete_ftp_connection(conn_id: str) -> None:
    with ftp_sources_lock:
        registry = load_ftp_sources_registry()
        existed = any(c.get("id") == conn_id for c in registry["connections"])
        registry["connections"] = [c for c in registry["connections"] if c.get("id") != conn_id]
        if registry.get("active_id") == conn_id:
            registry["active_id"] = None
        save_ftp_sources_registry(registry)

    # Убираем пароль из системного хранилища (если он там был) — иначе утечка.
    if existed:
        _keyring_delete(conn_id)

    # Папку профиля удаляем только для подключения, которое реально было в
    # реестре, id которого соответствует формату (hex, см. ftp_connection_id) и
    # чья папка лежит непосредственно в datalog — чтобы подделанный id
    # (`../…`, абсолютный путь) не привёл к rmtree постороннего каталога.
    if not existed or not FTP_CONNECTION_ID_RE.fullmatch(conn_id):
        return
    remove_ftp_profile_dir(conn_id)


def list_ftp_sources_public() -> list[dict[str, Any]]:
    registry = load_ftp_sources_registry()
    rows: list[dict[str, Any]] = []
    for conn in registry["connections"]:
        rows.append(
            {
                "id": conn.get("id") or "",
                "label": conn.get("label") or "",
                "host": conn.get("host") or "",
                "port": conn.get("port") or FTP_DEFAULT_PORT,
                "path": conn.get("path") or "/",
                "username": conn.get("username") or "",
                "web_scheme": conn.get("web_scheme") or "",
                "active": conn.get("id") == registry.get("active_id"),
            }
        )
    rows.sort(key=lambda row: str(row["label"]).lower())
    return rows


def normalize_ftp_host(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError("В FTP-конфигурации не указан `host`.")
    if any(char.isspace() for char in value):
        raise ValueError(
            "Поле `host` не должно содержать пробелы. Укажите адрес вроде `127.0.0.1`, "
            "`localhost` или имя сервера."
        )
    if not FTP_HOST_RE.fullmatch(value):
        raise ValueError(
            "Поле `host` содержит недопустимые символы. Укажите адрес FTP-сервера, "
            "например `127.0.0.1` или `localhost`."
        )
    return value


def normalize_ftp_path(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return "/"
    if "\r" in value or "\n" in value:
        raise ValueError("Поле `path` не должно содержать переводы строк.")
    if not value.startswith("/"):
        value = "/" + value
    if len(value) > 1:
        value = value.rstrip("/") or "/"
    return value


def apply_ftp_url_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Если в поле `host` вставили целую ссылку `ftp://user:pass@host/path`,
    раскладываем её на отдельные поля (явно заданные поля имеют приоритет)."""
    raw_host = str(payload.get("host") or "").strip()
    if "://" not in raw_host and "@" not in raw_host:
        return payload

    candidate = raw_host if "://" in raw_host else f"ftp://{raw_host}"
    parts = urlsplit(candidate)
    if not parts.hostname:
        return payload

    merged = dict(payload)
    merged["host"] = parts.hostname
    if parts.port:
        merged["port"] = parts.port
    if parts.username:
        merged["username"] = unquote(parts.username)
    if parts.password is not None:
        merged["password"] = unquote(parts.password)
    if parts.path and parts.path != "/":
        merged["path"] = parts.path
    return merged


def normalize_ftp_connection_settings(raw_payload: Any) -> dict[str, Any]:
    payload = raw_payload
    if isinstance(payload, dict) and isinstance(payload.get("ftp"), dict):
        payload = payload["ftp"]
    if not isinstance(payload, dict):
        raise ValueError("FTP-конфигурация должна быть JSON-объектом.")

    payload = apply_ftp_url_payload(payload)
    host = normalize_ftp_host(payload.get("host"))

    try:
        port = int(payload.get("port") or FTP_DEFAULT_PORT)
    except (TypeError, ValueError) as exc:
        raise ValueError("Порт FTP должен быть числом.") from exc
    if port <= 0 or port > 65535:
        raise ValueError("Порт FTP должен быть в диапазоне 1..65535.")

    # Имя пользователя не редактируется: у Weintek выгрузка истории всегда идёт
    # под `uploadhis` (см. FTP_HISTORY_USERNAME). Значение из формы/URL игнорируем.
    username = FTP_HISTORY_USERNAME
    password = str(payload.get("password") or "")
    path = normalize_ftp_path(payload.get("path") or payload.get("directory"))

    passive = payload.get("passive", True)
    if isinstance(passive, str):
        passive = passive.strip().lower() not in {"", "0", "false", "no", "off"}
    else:
        passive = bool(passive)

    # Схема веб-интерфейса EasyWeb (для веб-просмотра /app/dashboard). Из
    # обнаружения приходит http/https; иначе пусто (фронтенд подставит http).
    web_scheme = str(payload.get("web_scheme") or "").strip().lower()
    if web_scheme not in {"http", "https"}:
        web_scheme = ""

    return {
        "version": FTP_SOURCE_CONFIG_VERSION,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "path": path,
        "passive": passive,
        "web_scheme": web_scheme,
    }


def create_ftp_workspace(config: dict[str, Any], label: str = "") -> tuple[Path, str]:
    # Подключение сохраняется в реестре (temp/wash_ftp_sources.json, пароль
    # зашифрован DPAPI), а каждой панели выделяется своя папка с архивами:
    #   datalog/<id>/<дата>/...
    # Это позволяет хранить несколько панелей и переключаться между ними, не
    # смешивая их данные.
    entry = upsert_ftp_connection(config, label=label)
    profile_dir = app_config.DATALOG_ROOT / entry["id"]
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir.resolve(), entry["label"]
