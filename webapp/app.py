from __future__ import annotations

import asyncio
import base64
import json
import hashlib
import ftplib
import logging
import os
import pickle
import posixpath
import re
import shutil
import sqlite3
import sys
import tarfile
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote, urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from runtime_paths import resolve_cache_root, resolve_runtime_root
import wash_report as core
from webapp.chart_payload import SERIES_CONFIG, build_cycle_chart_payload


def resolve_project_root() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root)
    return Path(__file__).resolve().parent.parent


def resolve_workspace_input_value(
    selected_root: Path | None,
    pending_root: Path | None,
) -> str:
    current_root = selected_root or pending_root
    if current_root is not None:
        return str(current_root)

    # После перезапуска активного источника ещё нет — подставляем последний
    # открытый путь к папке, если он сохранён.
    last_path = load_last_folder_path()
    if last_path:
        return last_path

    # Иначе — путь по умолчанию: заданный в настройках, а при его отсутствии —
    # локальная папка со скачанными архивами (datalog). Она определяется per-user
    # (%LOCALAPPDATA%\OptiCIP Dashboard\datalog на Windows), поэтому на каждом ПК
    # подставляется путь текущего пользователя.
    return resolve_default_folder_path()


def resolve_default_folder_path() -> str:
    """Путь по умолчанию для поля «Папка»: заданный пользователем в настройках,
    а при его отсутствии — встроенная папка datalog."""
    configured = load_app_settings().get("default_folder_path") or ""
    return configured or str(DATALOG_ROOT)


def resolve_workspace_path_placeholder() -> str:
    # Подсказка = путь по умолчанию (настройка или встроенная папка datalog).
    return resolve_default_folder_path()


PROJECT_ROOT = resolve_project_root()
TEMPLATES_DIR = PROJECT_ROOT / "webapp" / "templates"
STATIC_DIR = PROJECT_ROOT / "webapp" / "static"
SUPPORTED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
)
ARCHIVE_CACHE_ROOT = resolve_cache_root("wash_journal_archive_cache")
ANALYSIS_CACHE_ROOT = resolve_cache_root("wash_journal_analysis_cache")
WEB_RUNTIME_OUTPUT_DIR = ANALYSIS_CACHE_ROOT / "generated"
ARCHIVE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
ANALYSIS_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
DB_ANALYSIS_CACHE_VERSION = 2
WORKSPACE_ANALYSIS_CACHE_VERSION = 3
CHART_PAYLOAD_DISK_CACHE_VERSION = 1
CHART_PAYLOAD_CACHE_LIMIT = 64
DB_ANALYSIS_MAX_WORKERS = 4
WORKSPACE_JOB_STREAM_KEEPALIVE_SECONDS = 10.0
IGNORED_WORKSPACE_DIR_NAMES = frozenset(
    {
        ".git",
        ".idea",
        ".pyinstaller",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
APP_VERSION = "1.0.0"
OBJECT_NAME_OVERRIDES_FILENAME = core.OBJECT_NAMES_FILENAME
OBJECT_NAME_OVERRIDES_VERSION = 1
CHART_STYLE_SETTINGS_FILENAME = "wash_chart_styles.json"
CHART_STYLE_SETTINGS_VERSION = 1
FOLDER_SOURCE_SETTINGS_FILENAME = "wash_folder_source.json"
FOLDER_SOURCE_SETTINGS_VERSION = 1
APP_SETTINGS_FILENAME = "wash_app_settings.json"
APP_SETTINGS_VERSION = 1
FTP_AUTO_REFRESH_MIN_MINUTES = 1
FTP_AUTO_REFRESH_MAX_MINUTES = 1440
DEFAULT_APP_SETTINGS: dict[str, Any] = {
    "ftp_auto_refresh_enabled": True,
    "ftp_auto_refresh_minutes": 5,
    "default_folder_path": "",
}
# Как часто фоновый цикл просыпается, чтобы сверить, не пора ли обновлять FTP.
FTP_AUTO_REFRESH_POLL_SECONDS = 20.0
# Идентификаторы стилей линий должны совпадать с LINE_STYLE_OPTIONS в wash-chart.js.
CHART_LINE_STYLE_IDS = frozenset({"solid", "dashed", "dashdot", "dotted", "longdash"})
CHART_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FTP_SOURCE_CONFIG_VERSION = 1
FTP_SOURCES_FILENAME = "wash_ftp_sources.json"
FTP_SOURCES_VERSION = 1
FTP_CONNECT_TIMEOUT_SECONDS = 10
FTP_DEFAULT_PORT = 21
FTP_DOWNLOAD_MAX_DEPTH = 24
# Запас при сравнении времени модификации (сек): гасит секундные округления
# MDTM/MLSD и разницу файловых систем, чтобы не перекачивать неизменившиеся файлы.
FTP_MTIME_TOLERANCE_SECONDS = 2.0
FTP_HOST_RE = re.compile(r"^[A-Za-z0-9._:\-\[\]]+$")
DEFAULT_FTP_FORM_VALUES = {
    "host": "",
    "port": "21",
    "username": "uploadhis",
    "password": "",
    "path": "/datalog",
}

def resolve_app_data_root() -> Path:
    """Папка приложения: рядом с .exe в собранной версии, иначе корень проекта."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def resolve_app_subdir(name: str) -> Path:
    """Создаёт подпапку `name` в каталоге приложения (с запасным вариантом,
    если каталог приложения недоступен для записи — например, Program Files)."""
    candidate = resolve_app_data_root() / name
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return candidate.resolve()
    except OSError:
        fallback = resolve_runtime_root() / name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()


# datalog — постоянное хранилище скачанных архивов (подпапки по дате).
# temp — служебные файлы приложения (имена объектов и т. п.).
DATALOG_ROOT = resolve_app_subdir("datalog")
TEMP_ROOT = resolve_app_subdir("temp")

ARCHIVE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
ANALYSIS_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
WEB_RUNTIME_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_auto_refresh_task: "asyncio.Task[None] | None" = None


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    # startup — поднимаем фоновый цикл автообновления FTP (папки уже созданы выше)
    global _auto_refresh_task
    _auto_refresh_task = asyncio.create_task(ftp_auto_refresh_loop())
    try:
        yield
    finally:
        # shutdown — останавливаем фоновую задачу и чистим дисковый кэш, чтобы
        # следующий запуск строил всё заново
        if _auto_refresh_task is not None:
            _auto_refresh_task.cancel()
            try:
                await _auto_refresh_task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - защита остановки
                logging.exception("Ошибка при остановке фонового автообновления")
            _auto_refresh_task = None
        clear_disk_caches()


app = FastAPI(title="Отчеты по мойкам", lifespan=_app_lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@dataclass
class ScanSummary:
    archive_count: int = 0
    ftp_source_count: int = 0


@dataclass
class WorkspaceJob:
    id: str
    target_root: Path | None = None
    display_target: str = ""
    status: str = "running"
    phase: str = "queued"
    message: str = "Подготавливаю анализ источника."
    current: int = 0
    total: int = 0
    item: str = ""
    error: str | None = None
    cancel_requested: bool = False
    background: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


@dataclass
class AppState:
    selected_root: Path | None = None
    pending_root: Path | None = None
    selected_display_root: str = ""
    pending_display_root: str = ""
    analysis: core.AnalysisResult | None = None
    analysis_revision: int = 0
    object_name_overrides: dict[tuple[int, int], str] = field(default_factory=dict)
    error: str | None = None
    scan_summary: ScanSummary = field(default_factory=ScanSummary)
    workspace_job: WorkspaceJob | None = None


@dataclass(frozen=True)
class AppStateSnapshot:
    analysis: core.AnalysisResult | None
    selected_root: Path | None
    pending_root: Path | None
    selected_display_root: str
    pending_display_root: str
    object_name_overrides: dict[tuple[int, int], str]
    error: str | None
    scan_summary: ScanSummary
    workspace_job_payload: dict[str, Any]


state = AppState()
state_lock = threading.Lock()
archive_cache_lock = threading.Lock()
analysis_cache_lock = threading.Lock()
chart_payload_cache_lock = threading.Lock()
archive_cache_keys_by_source: dict[str, str] = {}
chart_payload_cache: OrderedDict[tuple[int, str], dict[str, Any]] = OrderedDict()


def format_source_label(value: str) -> str:
    return Path(value).name


def capture_state_snapshot() -> AppStateSnapshot:
    return AppStateSnapshot(
        analysis=state.analysis,
        selected_root=state.selected_root,
        pending_root=state.pending_root,
        selected_display_root=state.selected_display_root,
        pending_display_root=state.pending_display_root,
        object_name_overrides=dict(state.object_name_overrides),
        error=state.error,
        scan_summary=ScanSummary(
            archive_count=state.scan_summary.archive_count,
            ftp_source_count=state.scan_summary.ftp_source_count,
        ),
        workspace_job_payload=serialize_job(state.workspace_job),
    )


def is_ignored_workspace_dir(path: Path, ignored_paths: set[Path]) -> bool:
    if path.name.lower() in IGNORED_WORKSPACE_DIR_NAMES:
        return True

    try:
        return path.resolve() in ignored_paths
    except OSError:
        return False


def format_day_key(timestamp: float) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(timestamp))
    except (OverflowError, OSError, ValueError):
        return ""


def format_ftp_display_label(config: dict[str, Any]) -> str:
    host = str(config.get("host") or "").strip()
    port = int(config.get("port") or FTP_DEFAULT_PORT)
    path = str(config.get("path") or "/").strip() or "/"
    return f"FTP · {host}:{port}{path}"


# ---- защищённое хранение паролей ---------------------------------------
# На Windows используем DPAPI (CryptProtectData) — пароль шифруется ключом
# текущего пользователя ОС. На других платформах (dev) — обратимое кодирование
# (не шифрование), чтобы не хранить совсем уж в чистом виде.
def _dpapi_crypt(data: bytes, *, protect: bool) -> bytes | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = func(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect_secret(value: str) -> str:
    raw = (value or "").encode("utf-8")
    if not raw:
        return ""
    blob = _dpapi_crypt(raw, protect=True)
    if blob is not None:
        return "dpapi:" + base64.b64encode(blob).decode("ascii")
    return "b64:" + base64.b64encode(raw).decode("ascii")


def unprotect_secret(token: str) -> str:
    token = str(token or "")
    if not token:
        return ""
    if token.startswith("dpapi:"):
        try:
            blob = base64.b64decode(token[6:])
        except Exception:
            return ""
        raw = _dpapi_crypt(blob, protect=False)
        return raw.decode("utf-8") if raw is not None else ""
    if token.startswith("b64:"):
        try:
            return base64.b64decode(token[4:]).decode("utf-8")
        except Exception:
            return ""
    return token  # legacy plaintext


# ---- реестр сохранённых FTP-подключений (несколько панелей) ------------
def ftp_sources_path() -> Path:
    return TEMP_ROOT / FTP_SOURCES_FILENAME


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
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def upsert_ftp_connection(config: dict[str, Any], label: str = "") -> dict[str, Any]:
    registry = load_ftp_sources_registry()
    conn_id = ftp_connection_id(config)
    entry = {
        "id": conn_id,
        "label": (label or "").strip() or format_ftp_display_label(config),
        "host": config["host"],
        "port": config["port"],
        "username": config["username"],
        "password_enc": protect_secret(config.get("password", "")),
        "path": config["path"],
        "passive": bool(config.get("passive", True)),
    }
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
        }
    )


def delete_ftp_connection(conn_id: str) -> None:
    registry = load_ftp_sources_registry()
    registry["connections"] = [c for c in registry["connections"] if c.get("id") != conn_id]
    if registry.get("active_id") == conn_id:
        registry["active_id"] = None
    save_ftp_sources_registry(registry)
    profile_dir = DATALOG_ROOT / conn_id
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)


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

    username = str(payload.get("username") or payload.get("user") or "").strip() or "anonymous"
    password = str(payload.get("password") or "")
    path = normalize_ftp_path(payload.get("path") or payload.get("directory"))

    passive = payload.get("passive", True)
    if isinstance(passive, str):
        passive = passive.strip().lower() not in {"", "0", "false", "no", "off"}
    else:
        passive = bool(passive)

    return {
        "version": FTP_SOURCE_CONFIG_VERSION,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "path": path,
        "passive": passive,
    }


def create_ftp_workspace(config: dict[str, Any], label: str = "") -> tuple[Path, str]:
    # Подключение сохраняется в реестре (temp/wash_ftp_sources.json, пароль
    # зашифрован DPAPI), а каждой панели выделяется своя папка с архивами:
    #   datalog/<id>/<дата>/...
    # Это позволяет хранить несколько панелей и переключаться между ними, не
    # смешивая их данные.
    entry = upsert_ftp_connection(config, label=label)
    profile_dir = DATALOG_ROOT / entry["id"]
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir.resolve(), entry["label"]


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
            if name in {"", ".", ".."}:
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
    except (ftplib.error_perm, ftplib.error_proto, ftplib.error_temp, OSError):
        pass

    # MLSD не поддерживается — берём NLST и определяем тип по SIZE.
    try:
        names = connection.nlst(remote_dir)
    except (ftplib.error_perm, ftplib.error_temp, OSError):
        return []

    for raw_name in names:
        name = PurePosixPath(raw_name).name
        if name in {"", ".", ".."}:
            continue
        full = raw_name if raw_name.startswith("/") else posixpath.join(remote_dir, name)
        is_dir = False
        meta = {}
        try:
            size_value = connection.size(full)
        except (ftplib.error_perm, ftplib.error_temp):
            is_dir = True
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
        return Path(PurePosixPath(remote_file).name or "download.db")
    return safe_path


def _is_archive_or_db_name(name: str) -> bool:
    """Похоже ли имя на базу `.db` или поддерживаемый архив."""
    lower_name = name.lower()
    return lower_name.endswith(".db") or any(
        lower_name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES
    )


def build_local_archive_index(root_path: Path) -> dict[Any, dict[str, Any]]:
    """Индекс уже скачанных архивов/баз под `root_path` для пропуска повторных загрузок.

    Ключи двух видов: относительный путь (совпадает с `_ftp_relative_target`)
    для файлов в зеркале и кортеж `("name", имя, размер)` — чтобы распознавать
    копии, лежащие в старых подпапках-по-дате. Значение: `size`, `mtime`, `path`.
    """
    index: dict[Any, dict[str, Any]] = {}
    root_resolved = root_path.resolve()
    try:
        candidates = list(root_path.rglob("*"))
    except OSError:
        return index
    for candidate in candidates:
        try:
            if not candidate.is_file() or not _is_archive_or_db_name(candidate.name):
                continue
            stat_result = candidate.stat()
        except OSError:
            continue
        resolved = candidate.resolve()
        entry = {"size": stat_result.st_size, "mtime": stat_result.st_mtime, "path": resolved}
        try:
            rel_key = resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            rel_key = candidate.name
        index.setdefault(rel_key, entry)
        index.setdefault(("name", candidate.name, stat_result.st_size), entry)
    return index


def _should_skip_download(
    local_meta: dict[str, Any] | None,
    remote_size: int | None,
    remote_mtime: float | None,
) -> bool:
    """Можно ли не скачивать файл: локальная копия есть, размер совпал и она не старше панели."""
    if local_meta is None or remote_size is None:
        return False
    local_size = local_meta.get("size")
    if local_size is None or int(local_size) != int(remote_size):
        return False
    if remote_mtime is None:
        return True
    local_mtime = local_meta.get("mtime")
    if local_mtime is None:
        return True
    return local_mtime + FTP_MTIME_TOLERANCE_SECONDS >= remote_mtime


def download_ftp_files(
    config: dict[str, Any],
    target_dir: Path,
    *,
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[Path]:
    remote_root = config.get("path") or "/"
    core.emit_progress(
        progress_callback,
        phase="ftp",
        message="Подключаюсь к FTP-серверу.",
        current=0,
        total=0,
        item=format_ftp_display_label(config),
    )
    connection = open_ftp_connection(config)
    # Все файлы панели, которые сейчас представлены локально (скачанные + пропущенные).
    present_files: list[Path] = []
    downloaded_count = 0
    skipped_count = 0
    local_index = build_local_archive_index(target_dir)
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

            relative_target = _ftp_relative_target(remote_root, remote_file)
            target_path = target_dir / relative_target
            target_path.parent.mkdir(parents=True, exist_ok=True)

            remote_size = meta.get("size")
            if remote_size is None:
                remote_size = _ftp_remote_size(connection, remote_file)
            remote_mtime = meta.get("mtime")
            if remote_mtime is None:
                remote_mtime = _ftp_remote_mtime(connection, remote_file)

            rel_key = relative_target.as_posix()
            local_meta = local_index.get(rel_key)
            if local_meta is None and remote_size is not None:
                local_meta = local_index.get(("name", relative_target.name, remote_size))

            if _should_skip_download(local_meta, remote_size, remote_mtime):
                skipped_count += 1
                core.emit_progress(
                    progress_callback,
                    phase="ftp",
                    message=f"Файл {index} из {total} не изменился, пропускаю.",
                    current=index - 1,
                    total=total,
                    item=relative_target.name,
                )
                existing_path = local_meta.get("path") if local_meta else None
                present_files.append(Path(existing_path) if existing_path else target_path.resolve())
                continue

            core.emit_progress(
                progress_callback,
                phase="ftp",
                message=f"Скачиваю файл {index} из {total} с FTP.",
                current=index - 1,
                total=total,
                item=relative_target.name,
            )

            try:
                with target_path.open("wb") as handle:
                    connection.retrbinary(f"RETR {remote_file}", handle.write)
            except core.AnalysisCancelledError:
                raise
            except Exception as exc:
                raise SystemExit(
                    f"Не удалось скачать файл `{remote_file}` с FTP: {exc}"
                ) from exc

            # Сохраняем время панели, чтобы на следующих запусках сравнение по времени работало.
            if remote_mtime is not None:
                try:
                    os.utime(target_path, (remote_mtime, remote_mtime))
                except OSError:
                    pass

            resolved = target_path.resolve()
            downloaded_count += 1
            present_files.append(resolved)
            # Обновляем индекс, чтобы дубликаты в этом же прогоне тоже пропускались.
            try:
                stat_result = target_path.stat()
                fresh_entry = {
                    "size": stat_result.st_size,
                    "mtime": stat_result.st_mtime,
                    "path": resolved,
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

    core.emit_progress(
        progress_callback,
        phase="ftp",
        message=f"Файлы с FTP получены: скачано {downloaded_count}, пропущено {skipped_count} (без изменений).",
        current=len(present_files),
        total=len(present_files),
        item=f"{downloaded_count} новых из {len(present_files)}",
    )
    return present_files


def datalog_has_archives(root_path: Path) -> bool:
    """Есть ли в datalog уже скачанные базы `.db` или архивы (за любую дату)."""
    try:
        for candidate in root_path.rglob("*"):
            if not candidate.is_file():
                continue
            if _is_archive_or_db_name(candidate.name):
                return True
    except OSError:
        return False
    return False


def materialize_ftp_sources(
    root_path: Path,
    *,
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> int:
    """Синхронизирует архивы активной FTP-панели в зеркало `datalog/<id>/` и
    возвращает число файлов панели, представленных локально.

    Папка профиля определяется по `root_path` (это `datalog/<id>`); параметры
    подключения берутся из реестра по `id`. Загрузка инкрементальная: файлы,
    уже скачанные и не изменившиеся на панели (совпал размер и время не новее),
    повторно не качаются (см. `download_ftp_files`). Если FTP недоступен, но
    локальные архивы уже есть — работаем с ними. Для обычной папки (folder mode)
    функция ничего не делает."""
    try:
        is_ftp_profile = root_path.resolve().parent == DATALOG_ROOT
    except OSError:
        is_ftp_profile = False
    if not is_ftp_profile:
        return 0

    connection = find_ftp_connection(root_path.name)
    if connection is None:
        return 0
    config = connection_to_config(connection)

    # Стабильное зеркало панели: качаем прямо в профиль, без подпапок по дате,
    # чтобы проверка «файл уже есть» работала между запусками.
    download_dir = root_path
    download_dir.mkdir(parents=True, exist_ok=True)

    try:
        downloaded_files = download_ftp_files(
            config,
            download_dir,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except core.AnalysisCancelledError:
        raise
    except (ValueError, SystemExit, OSError) as exc:
        if datalog_has_archives(root_path):
            core.emit_progress(
                progress_callback,
                phase="ftp",
                message=f"FTP недоступен ({exc}); использую ранее скачанные архивы.",
                item=format_ftp_display_label(config),
            )
            return 0
        raise SystemExit(
            f"Не удалось скачать архивы с FTP, и локальных архивов в `datalog` нет: {exc}"
        ) from exc

    return len(downloaded_files)


def object_name_override_key(channel: int, object_id: int) -> str:
    return f"{channel}:{object_id}"


def parse_object_name_override_key(raw_key: str) -> tuple[int, int] | None:
    parts = str(raw_key).split(":", 1)
    if len(parts) != 2:
        return None
    try:
        channel = int(parts[0])
        object_id = int(parts[1])
    except ValueError:
        return None
    if channel <= 0 or object_id < 0:
        return None
    return channel, object_id


def object_name_overrides_path(root_path: Path) -> Path:
    return root_path / OBJECT_NAME_OVERRIDES_FILENAME


def fallback_object_name(object_id: int) -> str:
    return f"Объект {object_id}"


def load_object_name_overrides(root_path: Path | None) -> dict[tuple[int, int], str]:
    if root_path is None:
        return {}

    path = object_name_overrides_path(root_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    raw_objects = payload.get("objects")
    if not isinstance(raw_objects, dict):
        return {}

    overrides: dict[tuple[int, int], str] = {}
    for raw_key, raw_value in raw_objects.items():
        parsed_key = parse_object_name_override_key(str(raw_key))
        if parsed_key is None:
            continue

        value = str(raw_value or "").strip()
        if not value:
            continue
        overrides[parsed_key] = value

    return overrides


def save_object_name_overrides(root_path: Path, overrides: dict[tuple[int, int], str]) -> None:
    path = object_name_overrides_path(root_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    objects_payload = {
        object_name_override_key(channel, object_id): name
        for (channel, object_id), name in sorted(overrides.items(), key=lambda item: (item[0][0], item[0][1]))
        if name.strip()
    }
    if not objects_payload:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    payload = {
        "version": OBJECT_NAME_OVERRIDES_VERSION,
        "objects": objects_payload,
    }
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def resolve_object_name(channel: int, object_id: int, overrides: dict[tuple[int, int], str]) -> str:
    return overrides.get((channel, object_id)) or fallback_object_name(object_id)


# ---- стили кривых графика (цвет + тип линии), общие для всех источников -----
def chart_style_settings_path() -> Path:
    return TEMP_ROOT / CHART_STYLE_SETTINGS_FILENAME


def _normalize_chart_style_entry(raw_entry: Any) -> dict[str, str]:
    if not isinstance(raw_entry, dict):
        return {}
    entry: dict[str, str] = {}
    color = str(raw_entry.get("color") or "").strip()
    if CHART_COLOR_RE.fullmatch(color):
        entry["color"] = color.lower()
    line_style = str(raw_entry.get("lineStyle") or "").strip()
    if line_style in CHART_LINE_STYLE_IDS:
        entry["lineStyle"] = line_style
    return entry


def normalize_chart_style_series(raw_series: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw_series, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for raw_id, raw_entry in raw_series.items():
        series_id = str(raw_id).strip()
        if not series_id:
            continue
        entry = _normalize_chart_style_entry(raw_entry)
        if entry:
            normalized[series_id] = entry
    return normalized


def load_chart_style_settings() -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(chart_style_settings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return normalize_chart_style_series(payload.get("series"))


def save_chart_style_settings(series_styles: dict[str, dict[str, str]]) -> None:
    path = chart_style_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CHART_STYLE_SETTINGS_VERSION,
        "series": series_styles,
    }
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


# ---- последний открытый путь к папке (режим «Папка и архивы») ---------------
def folder_source_settings_path() -> Path:
    return TEMP_ROOT / FOLDER_SOURCE_SETTINGS_FILENAME


def load_last_folder_path() -> str:
    try:
        payload = json.loads(folder_source_settings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("last_path")
    return value.strip() if isinstance(value, str) else ""


def save_last_folder_path(path: str) -> None:
    value = str(path or "").strip()
    if not value:
        return
    target = folder_source_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": FOLDER_SOURCE_SETTINGS_VERSION, "last_path": value}
    temp_path = target.with_suffix(f"{target.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(target)


# ---- общие настройки приложения (автообновление FTP и т. п.) ----------------
def app_settings_path() -> Path:
    return TEMP_ROOT / APP_SETTINGS_FILENAME


def _coerce_auto_refresh_minutes(value: Any, fallback: int) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(FTP_AUTO_REFRESH_MIN_MINUTES, min(FTP_AUTO_REFRESH_MAX_MINUTES, minutes))


def normalize_app_settings(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}

    enabled = data.get("ftp_auto_refresh_enabled", DEFAULT_APP_SETTINGS["ftp_auto_refresh_enabled"])
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in {"", "0", "false", "no", "off"}
    else:
        enabled = bool(enabled)

    minutes = _coerce_auto_refresh_minutes(
        data.get("ftp_auto_refresh_minutes"),
        DEFAULT_APP_SETTINGS["ftp_auto_refresh_minutes"],
    )

    default_folder_path = data.get("default_folder_path", DEFAULT_APP_SETTINGS["default_folder_path"])
    if not isinstance(default_folder_path, str):
        default_folder_path = ""
    default_folder_path = default_folder_path.strip()

    return {
        "ftp_auto_refresh_enabled": enabled,
        "ftp_auto_refresh_minutes": minutes,
        "default_folder_path": default_folder_path,
    }


def load_app_settings() -> dict[str, Any]:
    try:
        payload = json.loads(app_settings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    # Поддерживаем как «плоский» объект, так и обёртку {"settings": {...}}.
    source = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    return normalize_app_settings(source)


def save_app_settings(raw: Any) -> dict[str, Any]:
    settings = normalize_app_settings(raw)
    path = app_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": APP_SETTINGS_VERSION, "settings": settings}
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    return settings


def apply_object_name_overrides(
    analysis: core.AnalysisResult | None,
    overrides: dict[tuple[int, int], str],
) -> None:
    if analysis is None:
        return

    collections = (
        analysis.segments,
        analysis.cycles,
        analysis.wash_intervals,
        analysis.overviews,
    )
    for collection in collections:
        for item in collection:
            item.object_name = resolve_object_name(item.channel, item.object_id, overrides)

    analysis.overviews.sort(key=lambda item: (item.channel, item.object_name, item.start_ts))


def clear_chart_payload_cache() -> None:
    with chart_payload_cache_lock:
        chart_payload_cache.clear()


def clear_disk_caches() -> None:
    """Полностью очищает дисковый кэш приложения: результаты анализа, готовые
    графики (`chart-*.pkl`) и распакованные из архивов базы. Вызывается при
    завершении работы, чтобы следующий запуск строил отчёты и графики заново и
    не отдавал устаревшие данные из кэша."""
    clear_chart_payload_cache()
    for cache_root, lock in (
        (ARCHIVE_CACHE_ROOT, archive_cache_lock),
        (ANALYSIS_CACHE_ROOT, analysis_cache_lock),
    ):
        with lock:
            try:
                shutil.rmtree(cache_root, ignore_errors=True)
                cache_root.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
    with archive_cache_lock:
        archive_cache_keys_by_source.clear()
    try:
        WEB_RUNTIME_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def reset_workspace() -> None:
    if state.workspace_job is not None:
        state.workspace_job.cancel_requested = True
    state.workspace_job = None
    state.pending_root = None
    state.selected_root = None
    state.pending_display_root = ""
    state.selected_display_root = ""
    state.analysis = None
    state.analysis_revision += 1
    state.object_name_overrides = {}
    state.error = None
    state.scan_summary = ScanSummary()
    clear_chart_payload_cache()


def finish_workspace_job_cancelled(job_id: str, message: str) -> dict[str, Any] | None:
    with state_lock:
        job = state.workspace_job
        if job is None or job.id != job_id:
            return None

        job.status = "cancelled"
        job.phase = "cancelled"
        job.message = message
        job.finished_at = time.time()
        state.pending_root = None
        state.pending_display_root = ""

        if state.analysis is None:
            state.selected_root = None
            state.selected_display_root = ""
            state.scan_summary = ScanSummary()
        return serialize_job(job)


def finish_workspace_job_failed(job_id: str, message: str) -> dict[str, Any] | None:
    with state_lock:
        job = state.workspace_job
        if job is None or job.id != job_id:
            return None

        job.status = "failed"
        job.phase = "failed"
        job.error = message
        job.message = message
        job.finished_at = time.time()
        state.pending_root = None
        state.pending_display_root = ""
        state.error = message

        if state.analysis is None:
            state.selected_root = None
            state.selected_display_root = ""
            state.scan_summary = ScanSummary()
        return serialize_job(job)


def serialize_job(job: WorkspaceJob | None) -> dict[str, Any]:
    if job is None:
        return {
            "id": "",
            "active": False,
            "status": "idle",
            "phase": "idle",
            "message": "",
            "current": 0,
            "total": 0,
            "item": "",
            "target_root": "",
            "display_target": "",
            "error": "",
            "background": False,
        }

    return {
        "id": job.id,
        "active": job.status in {"running", "cancelling"},
        "status": job.status,
        "phase": job.phase,
        "message": job.message,
        "current": job.current,
        "total": job.total,
        "item": job.item,
        "target_root": str(job.target_root) if job.target_root is not None else "",
        "display_target": job.display_target or (str(job.target_root) if job.target_root is not None else ""),
        "error": job.error or "",
        "background": bool(job.background),
    }


def push_job_progress(job_id: str, payload: dict[str, object]) -> None:
    with state_lock:
        job = state.workspace_job
        if job is None or job.id != job_id:
            return

        phase = str(payload.get("phase") or job.phase)
        message = str(payload.get("message") or job.message)
        current = int(payload.get("current") or 0)
        total = int(payload.get("total") or 0)
        item = str(payload.get("item") or "")

        job.phase = phase
        job.current = current
        job.total = total
        job.item = item

        if job.cancel_requested:
            job.status = "cancelling"
            job.message = "Отменяю открытие папки."
        else:
            job.status = "running"
            job.message = message


def job_cancel_requested(job_id: str) -> bool:
    with state_lock:
        job = state.workspace_job
        return job is None or job.id != job_id or job.cancel_requested


def is_supported_archive(path: Path) -> bool:
    if not path.is_file():
        return False
    lower_name = path.name.lower()
    return any(lower_name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES)


def safe_archive_member_path(name: str) -> Path | None:
    candidate = PurePosixPath(name)
    if candidate.is_absolute():
        return None

    parts = [part for part in candidate.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    return Path(*parts)


def extract_archive_dbs(
    archive_path: Path,
    target_root: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[Path]:
    extracted_paths: list[Path] = []

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as handle:
            for member in handle.infolist():
                if cancel_check is not None and cancel_check():
                    raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")
                if member.is_dir():
                    continue
                relative_path = safe_archive_member_path(member.filename)
                if relative_path is None or relative_path.suffix.lower() != ".db":
                    continue
                target_path = target_root / relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(member) as source, target_path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                extracted_paths.append(target_path.resolve())
        return extracted_paths

    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as handle:
            for member in handle.getmembers():
                if cancel_check is not None and cancel_check():
                    raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")
                if not member.isfile():
                    continue
                relative_path = safe_archive_member_path(member.name)
                if relative_path is None or relative_path.suffix.lower() != ".db":
                    continue
                target_path = target_root / relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                extracted_member = handle.extractfile(member)
                if extracted_member is None:
                    continue
                with extracted_member as source, target_path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                extracted_paths.append(target_path.resolve())

    return extracted_paths


def archive_cache_key(archive_path: Path) -> str:
    archive_stat = archive_path.stat()
    payload = f"{archive_path.resolve()}::{archive_stat.st_mtime_ns}::{archive_stat.st_size}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def touch_cache_entry(path: Path) -> None:
    try:
        os.utime(path, None)
    except FileNotFoundError:
        return


def cleanup_expired_cache_entries(cache_root: Path, ttl_seconds: int) -> None:
    cutoff = time.time() - ttl_seconds
    for candidate in cache_root.iterdir():
        try:
            if candidate.stat().st_mtime >= cutoff:
                continue
        except FileNotFoundError:
            continue

        try:
            if candidate.is_dir():
                shutil.rmtree(candidate, ignore_errors=True)
            else:
                candidate.unlink()
        except FileNotFoundError:
            continue


def cleanup_stale_archive_cache(source_path: Path, cache_key: str) -> None:
    source_key = str(source_path.resolve())
    previous_key = archive_cache_keys_by_source.get(source_key)
    archive_cache_keys_by_source[source_key] = cache_key
    if previous_key is None or previous_key == cache_key:
        return

    stale_dir = ARCHIVE_CACHE_ROOT / previous_key
    if stale_dir.exists():
        shutil.rmtree(stale_dir, ignore_errors=True)


def extract_archive_dbs_cached(
    archive_path: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[Path]:
    cache_key = archive_cache_key(archive_path)
    cache_dir = ARCHIVE_CACHE_ROOT / cache_key

    with archive_cache_lock:
        cleanup_stale_archive_cache(archive_path, cache_key)
        if cache_dir.exists():
            touch_cache_entry(cache_dir)
            return sorted(path.resolve() for path in cache_dir.rglob("*.db") if path.is_file())

    temp_dir = ARCHIVE_CACHE_ROOT / f"{cache_key}.tmp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        extracted_paths = extract_archive_dbs(
            archive_path,
            temp_dir,
            cancel_check=cancel_check,
        )
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        temp_dir.rename(cache_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return sorted(path.resolve() for path in cache_dir.rglob("*.db") if path.is_file())


def db_analysis_cache_key(db_path: Path) -> str:
    db_stat = db_path.stat()
    payload = f"{db_path.resolve()}::{db_stat.st_mtime_ns}::{db_stat.st_size}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def db_analysis_cache_path(cache_key: str) -> Path:
    return ANALYSIS_CACHE_ROOT / f"db-{cache_key}.pkl"


def workspace_analysis_cache_key(db_files: list[Path], *, max_gap_seconds: float) -> str:
    payload_parts = [f"v{WORKSPACE_ANALYSIS_CACHE_VERSION}", f"gap:{max_gap_seconds:.6f}"]
    for db_path in sorted(db_files, key=lambda item: str(item).lower()):
        payload_parts.append(f"{db_path.resolve()}::{db_analysis_cache_key(db_path)}")
    payload = "\n".join(payload_parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def workspace_analysis_cache_path(cache_key: str) -> Path:
    return ANALYSIS_CACHE_ROOT / f"workspace-{cache_key}.pkl"


def load_pickle_cache(path: Path) -> Any | None:
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
    except (FileNotFoundError, OSError, pickle.PickleError, AttributeError, EOFError, ValueError):
        return None

    touch_cache_entry(path)
    return payload


def save_pickle_cache(path: Path, payload: Any) -> None:
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with temp_path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    temp_path.replace(path)


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
    if payload.get("db_path") != str(db_path.resolve()):
        return None

    chunk = payload.get("chunk")
    if not isinstance(chunk, core.DbAnalysisChunk):
        return None
    return chunk


def save_cached_db_analysis(db_path: Path, chunk: core.DbAnalysisChunk) -> None:
    cache_key = db_analysis_cache_key(db_path)
    cache_path = db_analysis_cache_path(cache_key)
    payload = {
        "version": DB_ANALYSIS_CACHE_VERSION,
        "cache_key": cache_key,
        "db_path": str(db_path.resolve()),
        "chunk": chunk,
    }
    with analysis_cache_lock:
        save_pickle_cache(cache_path, payload)


def load_cached_workspace_analysis(cache_key: str) -> core.AnalysisResult | None:
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
    return analysis


def save_cached_workspace_analysis(cache_key: str, analysis: core.AnalysisResult) -> None:
    cache_path = workspace_analysis_cache_path(cache_key)
    payload = {
        "version": WORKSPACE_ANALYSIS_CACHE_VERSION,
        "cache_key": cache_key,
        "analysis": analysis,
    }
    with analysis_cache_lock:
        save_pickle_cache(cache_path, payload)


def chart_payload_disk_cache_key(analysis_cache_key: str, key: str) -> str:
    payload = f"{analysis_cache_key}::{key}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def chart_payload_disk_cache_path(analysis_cache_key: str, key: str) -> Path:
    cache_key = chart_payload_disk_cache_key(analysis_cache_key, key)
    return ANALYSIS_CACHE_ROOT / f"chart-{cache_key}.pkl"


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


def resolve_db_analysis_workers(task_count: int) -> int:
    if task_count <= 1:
        return 1
    cpu_budget = os.cpu_count() or 1
    return max(1, min(DB_ANALYSIS_MAX_WORKERS, task_count, cpu_budget))


def discover_db_files(
    root_path: Path,
    *,
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[Path], ScanSummary]:
    direct_db_files: list[Path] = []
    archive_files: list[Path] = []
    scanned_files = 0

    with archive_cache_lock:
        cleanup_expired_cache_entries(ARCHIVE_CACHE_ROOT, ARCHIVE_CACHE_TTL_SECONDS)

    # Сначала докачиваем свежие архивы с FTP в datalog/<дата>/, чтобы обход
    # ниже увидел и их, и ранее скачанные за прошлые даты.
    ftp_downloaded_count = materialize_ftp_sources(
        root_path,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )

    core.emit_progress(
        progress_callback,
        phase="scan",
        message="Сканирую папку и ищу базы данных.",
        item=str(root_path),
    )

    ignored_workspace_dirs = {
        ARCHIVE_CACHE_ROOT.resolve(),
        ANALYSIS_CACHE_ROOT.resolve(),
        WEB_RUNTIME_OUTPUT_DIR.resolve(),
    }

    for current_root, _dirnames, filenames in os.walk(root_path):
        current_root_path = Path(current_root)
        _dirnames[:] = [
            dirname
            for dirname in _dirnames
            if not is_ignored_workspace_dir(current_root_path / dirname, ignored_workspace_dirs)
        ]
        if cancel_check is not None and cancel_check():
            raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

        for filename in filenames:
            scanned_files += 1
            candidate = Path(current_root) / filename
            lower_name = filename.lower()

            if lower_name.endswith(".db"):
                direct_db_files.append(candidate.resolve())
            elif any(lower_name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES):
                archive_files.append(candidate.resolve())

            if scanned_files == 1 or scanned_files % 200 == 0:
                core.emit_progress(
                    progress_callback,
                    phase="scan",
                    message="Сканирую содержимое папки.",
                    current=scanned_files,
                    item=filename,
                )

    extracted_db_files: list[Path] = []
    for index, archive_path in enumerate(sorted(archive_files), start=1):
        if cancel_check is not None and cancel_check():
            raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

        core.emit_progress(
            progress_callback,
            phase="extract",
            message=f"Распаковываю архив {index} из {len(archive_files)}.",
            current=index,
            total=len(archive_files),
            item=archive_path.name,
        )

        try:
            extracted_db_files.extend(
                extract_archive_dbs_cached(
                    archive_path,
                    cancel_check=cancel_check,
                )
            )
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError):
            continue

    unique_paths = {str(path): path for path in [*direct_db_files, *extracted_db_files]}
    db_files = sorted(unique_paths.values(), key=lambda item: str(item).lower())
    return db_files, ScanSummary(
        archive_count=len(archive_files),
        ftp_source_count=ftp_downloaded_count,
    )


def analyze_db_files_incremental(
    db_files: list[Path],
    *,
    output_dir: Path,
    max_gap_seconds: float = 15.0,
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> core.AnalysisResult:
    with analysis_cache_lock:
        cleanup_expired_cache_entries(ANALYSIS_CACHE_ROOT, ANALYSIS_CACHE_TTL_SECONDS)

    workspace_cache_key = workspace_analysis_cache_key(db_files, max_gap_seconds=max_gap_seconds)
    cached_analysis = load_cached_workspace_analysis(workspace_cache_key)
    if cached_analysis is not None:
        core.emit_progress(
            progress_callback,
            phase="cache",
            message="Загружаю сохранённый анализ из кэша.",
            current=1,
            total=1,
            item=f"{len(cached_analysis.db_files)} баз данных",
        )
        cached_analysis.output_dir = output_dir
        cached_analysis.analysis_cache_key = workspace_cache_key
        return cached_analysis

    chunks_by_db: dict[str, core.DbAnalysisChunk] = {}
    pending_jobs: list[tuple[int, Path, int]] = []
    skipped_db_files: list[str] = []
    total_files = len(db_files)
    cached_files = 0

    for index, db_path in enumerate(db_files, start=1):
        if cancel_check is not None and cancel_check():
            raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

        cached_chunk = load_cached_db_analysis(db_path)
        if cached_chunk is not None:
            cached_files += 1
            core.emit_progress(
                progress_callback,
                phase="cache",
                message=f"Загружаю файл {cached_files} из {total_files} из локального кэша.",
                current=cached_files,
                total=total_files,
                item=db_path.name,
            )
            chunks_by_db[str(db_path.resolve())] = cached_chunk
            continue

        core.emit_progress(
            progress_callback,
            phase="preflight",
            message=f"Проверяю файл {index} из {total_files}.",
            current=index,
            total=total_files,
            item=db_path.name,
        )
        try:
            channel = core.preflight_db_file(db_path)
        except SystemExit:
            skipped_db_files.append(db_path.name)
            continue
        pending_jobs.append((index, db_path, channel))

    if pending_jobs:
        worker_count = resolve_db_analysis_workers(len(pending_jobs))
        analyzed_files = 0
        core.emit_progress(
            progress_callback,
            phase="analyze",
            message=(
                f"Обрабатываю {len(pending_jobs)} файлов"
                f"{' параллельно' if worker_count > 1 else ''}."
            ),
            current=cached_files,
            total=total_files,
            item=f"Воркеров: {worker_count}",
        )

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="wash-db-analysis") as executor:
            future_to_job = {
                executor.submit(
                    core.analyze_single_db_file,
                    db_path,
                    max_gap_seconds=max_gap_seconds,
                    cancel_check=cancel_check,
                    channel=channel,
                ): (index, db_path)
                for index, db_path, channel in pending_jobs
            }
            try:
                for future in as_completed(future_to_job):
                    if cancel_check is not None and cancel_check():
                        raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

                    index, db_path = future_to_job[future]
                    chunk = future.result()
                    save_cached_db_analysis(db_path, chunk)
                    chunks_by_db[str(db_path.resolve())] = chunk
                    analyzed_files += 1
                    core.emit_progress(
                        progress_callback,
                        phase="analyze",
                        message=f"Обрабатываю файл {index} из {total_files}.",
                        current=cached_files + analyzed_files,
                        total=total_files,
                        item=db_path.name,
                    )
            except Exception:
                for future in future_to_job:
                    future.cancel()
                raise

    chunks = [
        chunks_by_db[db_key]
        for db_key in (str(path.resolve()) for path in db_files)
        if db_key in chunks_by_db
    ]

    if not chunks:
        if skipped_db_files:
            raise SystemExit(
                "В выбранной папке не найдено ни одной подходящей базы данных "
                "с именем вида `Canal_*.db`."
            )
        raise SystemExit("SQLite-файлы не найдены.")

    core.emit_progress(
        progress_callback,
        phase="merge",
        message="Собираю общий индекс моек.",
        current=len(chunks),
        total=len(chunks),
        item=f"{len(chunks)} баз данных",
    )
    analysis = core.build_analysis_result(
        [chunk.db_path for chunk in chunks],
        output_dir=output_dir,
        max_gap_seconds=max_gap_seconds,
        chunks=chunks,
        analysis_cache_key=workspace_cache_key,
    )
    save_cached_workspace_analysis(workspace_cache_key, analysis)
    return analysis


def run_workspace_job(job_id: str, target_root: Path) -> None:
    progress_callback = lambda payload: push_job_progress(job_id, payload)
    cancel_check = lambda: job_cancel_requested(job_id)

    try:
        db_files, scan_summary = discover_db_files(
            target_root,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        if not db_files:
            raise SystemExit(
                "В выбранном источнике не найдено ни одной базы `.db` ни в папке, ни в поддерживаемых архивах, ни на FTP."
            )

        analysis = analyze_db_files_incremental(
            db_files,
            output_dir=WEB_RUNTIME_OUTPUT_DIR,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        object_name_overrides = load_object_name_overrides(TEMP_ROOT)
        apply_object_name_overrides(analysis, object_name_overrides)

        with state_lock:
            job = state.workspace_job
            if job is None or job.id != job_id:
                return

            state.selected_root = target_root
            state.pending_root = None
            state.selected_display_root = job.display_target or str(target_root)
            state.pending_display_root = ""
            state.analysis = analysis
            state.analysis_revision += 1
            state.object_name_overrides = object_name_overrides
            state.scan_summary = scan_summary
            state.error = None
            clear_chart_payload_cache()

            job.target_root = target_root
            job.status = "completed"
            job.phase = "completed"
            job.message = "Данные успешно обновлены."
            job.current = max(job.current, job.total)
            job.finished_at = time.time()
    except core.AnalysisCancelledError:
        finish_workspace_job_cancelled(job_id, "Обработка источника отменена.")
    except SystemExit as exc:
        message = str(exc) or "Не удалось открыть выбранный источник."
        finish_workspace_job_failed(job_id, message)
    except Exception as exc:  # pragma: no cover - safety net for background worker
        finish_workspace_job_failed(job_id, f"Не удалось открыть источник: {exc}")


def start_workspace_job(
    candidate: Path,
    *,
    display_target: str | None = None,
    background: bool = False,
) -> None:
    if state.workspace_job is not None and state.workspace_job.status in {"running", "cancelling"}:
        state.workspace_job.cancel_requested = True
        state.workspace_job.status = "cancelling"
        state.workspace_job.message = "Отменяю предыдущую обработку источника."

    resolved_candidate = candidate.resolve()
    job = WorkspaceJob(
        id=uuid.uuid4().hex,
        target_root=resolved_candidate,
        display_target=display_target or str(resolved_candidate),
        background=background,
    )
    state.workspace_job = job
    state.pending_root = resolved_candidate
    state.pending_display_root = job.display_target
    if state.analysis is None:
        state.selected_root = None
        state.selected_display_root = ""
        state.object_name_overrides = {}
        state.scan_summary = ScanSummary()
        clear_chart_payload_cache()
    state.error = None

    thread = threading.Thread(
        target=run_workspace_job,
        args=(job.id, resolved_candidate),
        name="wash-workspace-loader",
        daemon=True,
    )
    thread.start()


def trigger_ftp_auto_refresh() -> bool:
    """Запускает фоновое обновление активной FTP-панели, если сейчас нет другой
    обработки. Папочный (folder) источник и отсутствие анализа пропускаются."""
    with state_lock:
        job = state.workspace_job
        if job is not None and job.status in {"running", "cancelling"}:
            return False

        target_root = state.selected_root or state.pending_root
        if target_root is None or state.analysis is None:
            return False

        try:
            is_ftp_profile = target_root.resolve().parent == DATALOG_ROOT
        except OSError:
            is_ftp_profile = False
        if not is_ftp_profile:
            return False

        display_target = (
            state.selected_display_root
            or state.pending_display_root
            or str(target_root.resolve())
        )
        start_workspace_job(target_root.resolve(), display_target=display_target, background=True)
        return True


async def ftp_auto_refresh_loop() -> None:
    """Фоновый цикл: пока приложение запущено, периодически (интервал из настроек)
    докачивает архивы с активной FTP-панели и обновляет данные без блокирующего
    оверлея. Интервал и включение читаются из настроек на каждом тике."""
    last_run = time.monotonic()
    while True:
        try:
            await asyncio.sleep(FTP_AUTO_REFRESH_POLL_SECONDS)

            settings = load_app_settings()
            if not settings["ftp_auto_refresh_enabled"]:
                # При выключенном автообновлении откладываем следующий запуск на
                # полный интервал после повторного включения.
                last_run = time.monotonic()
                continue

            interval_seconds = settings["ftp_auto_refresh_minutes"] * 60
            now = time.monotonic()
            if now - last_run < interval_seconds:
                continue

            last_run = now
            if trigger_ftp_auto_refresh():
                logging.info("Фоновое автообновление FTP запущено.")
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - защита фонового цикла
            logging.exception("Сбой фонового автообновления FTP")


def parse_cycle_key(key: str) -> tuple[str, int, int, int, int, int]:
    parts = key.split("::", 5)
    if len(parts) != 6:
        raise HTTPException(status_code=400, detail="Некорректный ключ мойки.")
    source_db, channel, object_id, program_id, start_ts, end_ts = parts
    try:
        return source_db, int(channel), int(object_id), int(program_id), int(start_ts), int(end_ts)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректный ключ мойки.") from exc


def require_analysis() -> core.AnalysisResult:
    if state.analysis is None:
        raise HTTPException(status_code=400, detail=state.error or "Данные не загружены.")
    return state.analysis


def find_cycle(analysis: core.AnalysisResult, key: str) -> core.Cycle:
    cycle = analysis.cycles_by_key.get(key)
    if cycle is not None:
        return cycle

    parse_cycle_key(key)
    raise HTTPException(status_code=404, detail="Мойка не найдена.")


def build_wash_rows(analysis: core.AnalysisResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cycle in analysis.sorted_cycles:
        cycle_key = core.make_cycle_key(cycle)
        date_time = core.format_ts(cycle.start_ts)
        status = analysis.cycle_results_by_key.get(
            cycle_key,
            core.cycle_result_label_from_operations(cycle.operations),
        )
        source_name = format_source_label(cycle.source_db)
        rows.append(
            {
                "key": cycle_key,
                "date_time": date_time,
                "start_ts": cycle.start_ts,
                "end_ts": cycle.end_ts,
                "start_day": format_day_key(cycle.start_ts),
                "object_id": cycle.object_id,
                "object": cycle.object_name,
                "program": cycle.program_name,
                "status": status,
                "channel": cycle.channel,
                "duration": core.format_duration(cycle.duration_seconds),
                "duration_seconds": cycle.duration_seconds,
                "source_name": source_name,
                "search_blob": " ".join(
                    [
                        cycle.object_name,
                        cycle.program_name,
                        date_time,
                        source_name,
                        status,
                        f"Канал {cycle.channel}",
                    ]
                ).lower(),
            }
        )
    return rows


def build_object_rows(
    overrides: dict[tuple[int, int], str] | None = None,
    analysis: core.AnalysisResult | None = None,
) -> list[dict[str, Any]]:
    overrides = overrides or {}
    # Показываем не только уже переименованные объекты, но и все обнаруженные в
    # данных — чтобы их можно было назвать (и тем самым создать json), даже если
    # файла имён ещё нет.
    keys: set[tuple[int, int]] = set(overrides)
    if analysis is not None:
        for overview in analysis.overviews:
            if overview.object_id > 0:
                keys.add((overview.channel, overview.object_id))

    rows: list[dict[str, Any]] = []
    for channel, object_id in sorted(keys):
        if object_id <= 0:
            continue
        base_name = fallback_object_name(object_id)
        object_name = resolve_object_name(channel, object_id, overrides)
        rows.append(
            {
                "channel": channel,
                "object_id": object_id,
                "object_name": object_name,
                "base_object_name": base_name,
                "is_json_name": (channel, object_id) in overrides,
                "is_custom_name": object_name != base_name,
                "search_blob": " ".join(
                    [
                        f"Канал {channel}",
                        f"Объект {object_id}",
                        object_name,
                        base_name,
                    ]
                ).lower(),
            }
        )

    return rows


def build_seed_object_name_overrides(
    analysis: core.AnalysisResult,
    overrides: dict[tuple[int, int], str] | None = None,
) -> dict[tuple[int, int], str]:
    seeded = dict(overrides or {})
    for overview in sorted(analysis.overviews, key=lambda item: (item.channel, item.object_id, item.start_ts)):
        key = (overview.channel, overview.object_id)
        if key in seeded:
            continue
        seeded[key] = str(overview.object_name or "").strip() or fallback_object_name(overview.object_id)
    return seeded


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


def build_wash_detail(analysis: core.AnalysisResult, key: str) -> dict[str, Any]:
    cycle = find_cycle(analysis, key)

    return {
        "key": key,
        "date_time": core.format_ts(cycle.start_ts),
        "start_time": core.format_ts(cycle.start_ts),
        "end_time": core.format_ts(cycle.end_ts),
        "start_ts": cycle.start_ts,
        "end_ts": cycle.end_ts,
        "object_id": cycle.object_id,
        "object_name": cycle.object_name,
        "program": cycle.program_name,
        "channel": cycle.channel,
        "status": analysis.cycle_results_by_key.get(
            key,
            core.cycle_result_label_from_operations(cycle.operations),
        ),
        "duration": core.format_duration(cycle.duration_seconds),
        "chart_data_url": f"/api/wash-chart-data?key={quote(key, safe='')}",
    }


def build_summary_payload(
    analysis: core.AnalysisResult | None,
    scan_summary: ScanSummary,
) -> dict[str, int]:
    return {
        "db_count": len(analysis.db_files) if analysis else 0,
        "object_count": len(analysis.overviews) if analysis else 0,
        "cycle_count": len(analysis.cycles) if analysis else 0,
        "archive_count": scan_summary.archive_count,
        "ftp_source_count": scan_summary.ftp_source_count,
    }


def build_workspace_payload(
    snapshot: AppStateSnapshot,
    *,
    include_rows: bool,
) -> dict[str, Any]:
    analysis = snapshot.analysis
    selected_root = snapshot.selected_root
    pending_root = snapshot.pending_root
    current_root = selected_root or pending_root
    display_root = (
        snapshot.selected_display_root
        or snapshot.pending_display_root
        or (str(current_root) if current_root else "")
    )
    payload = {
        "has_analysis": analysis is not None,
        "selected_root": str(selected_root) if selected_root else "",
        "display_root": display_root,
        "summary": build_summary_payload(analysis, snapshot.scan_summary),
        "error": snapshot.error,
        "job_status": snapshot.workspace_job_payload,
    }
    if include_rows:
        payload["wash_rows"] = build_wash_rows(analysis) if analysis else []
        payload["object_rows"] = build_object_rows(snapshot.object_name_overrides, analysis)
    return payload


def page_context(request: Request, snapshot: AppStateSnapshot) -> dict[str, Any]:
    analysis = snapshot.analysis
    selected_root = snapshot.selected_root
    pending_root = snapshot.pending_root
    workspace_payload = build_workspace_payload(snapshot, include_rows=False)
    workspace_input_value = resolve_workspace_input_value(selected_root, pending_root)
    def asset_version(filename: str) -> int:
        try:
            return int((STATIC_DIR / filename).stat().st_mtime)
        except OSError:
            return 0

    asset_versions = {
        "style_css": asset_version("style.css"),
        "wash_chart_js": asset_version("wash-chart.js"),
        "app_js": asset_version("app.js"),
    }
    return {
        "request": request,
        "page_title": "OptiCIP Dashboard",
        "has_analysis": analysis is not None,
        "selected_root": str(selected_root) if selected_root else "",
        "display_root": workspace_payload["display_root"],
        "project_root": str(PROJECT_ROOT),
        "workspace_input_value": workspace_input_value,
        "workspace_path_placeholder": resolve_workspace_path_placeholder(),
        "workspace_default_path": resolve_default_folder_path(),
        "ftp_form_defaults": dict(DEFAULT_FTP_FORM_VALUES),
        "ftp_sources": list_ftp_sources_public(),
        "app_version": APP_VERSION,
        "summary": workspace_payload["summary"],
        "error": workspace_payload["error"],
        "asset_versions": asset_versions,
        "job_status": workspace_payload["job_status"],
        "app_state": {
            "hasWorkspace": analysis is not None,
            "hasAnalysis": analysis is not None,
            "displayRoot": workspace_payload["display_root"],
            "summary": workspace_payload["summary"],
            "error": workspace_payload["error"],
            "jobStatus": workspace_payload["job_status"],
        },
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    with state_lock:
        snapshot = capture_state_snapshot()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(request, snapshot),
    )


@app.post("/workspace/open")
def open_workspace(path: str = Form(...)) -> RedirectResponse:
    candidate = Path(path).expanduser()
    with state_lock:
        if not candidate.exists() or not candidate.is_dir():
            state.error = f"Папка не найдена: {candidate}"
            if state.analysis is None:
                state.pending_root = None
                state.pending_display_root = ""
            return RedirectResponse(url="/", status_code=303)

        resolved = candidate.resolve()
        save_last_folder_path(str(resolved))
        start_workspace_job(resolved, display_target=str(resolved))
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/open-ftp")
def open_ftp_workspace(
    source_id: str = Form(""),
    host: str = Form(""),
    port: str = Form("21"),
    username: str = Form(""),
    password: str = Form(""),
    path: str = Form("/datalog"),
    passive: str = Form(""),
    label: str = Form(""),
) -> RedirectResponse:
    try:
        saved_id = source_id.strip()
        if saved_id:
            connection = find_ftp_connection(saved_id)
            if connection is None:
                raise ValueError("Сохранённое подключение не найдено.")
            config = connection_to_config(connection)
            connection_label = connection.get("label") or ""
        else:
            config = normalize_ftp_connection_settings(
                {
                    "host": host,
                    "port": port,
                    "username": username,
                    "password": password,
                    "path": path,
                    "passive": passive,
                }
            )
            connection_label = label
    except ValueError as exc:
        with state_lock:
            state.error = str(exc)
        return RedirectResponse(url="/", status_code=303)

    workspace_dir, display_label = create_ftp_workspace(config, label=connection_label)
    with state_lock:
        start_workspace_job(workspace_dir, display_target=display_label)
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/ftp-source/delete")
def delete_ftp_source(source_id: str = Form(...)) -> RedirectResponse:
    saved_id = source_id.strip()
    if saved_id:
        with state_lock:
            current_root = state.selected_root or state.pending_root
            clears_active = current_root is not None and current_root.name == saved_id
            if clears_active:
                reset_workspace()
        delete_ftp_connection(saved_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/reset")
def reset_workspace_route() -> RedirectResponse:
    with state_lock:
        reset_workspace()
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/refresh")
def refresh_workspace_route() -> RedirectResponse:
    with state_lock:
        target_root = state.selected_root or state.pending_root
        if target_root is None:
            state.error = "Сначала выберите источник данных."
            return RedirectResponse(url="/", status_code=303)

        display_target = state.selected_display_root or state.pending_display_root or str(target_root.resolve())
        start_workspace_job(target_root.resolve(), display_target=display_target)
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/workspace/refresh")
def refresh_workspace_api() -> JSONResponse:
    with state_lock:
        target_root = state.selected_root or state.pending_root
        if target_root is None:
            raise HTTPException(status_code=400, detail="Сначала выберите источник данных.")

        display_target = state.selected_display_root or state.pending_display_root or str(target_root.resolve())
        start_workspace_job(target_root.resolve(), display_target=display_target)
        return JSONResponse({"ok": True, "job": serialize_job(state.workspace_job)})


@app.get("/api/workspace-job")
def workspace_job_status() -> JSONResponse:
    with state_lock:
        return JSONResponse(serialize_job(state.workspace_job))


@app.get("/api/workspace-data")
def workspace_data() -> JSONResponse:
    with state_lock:
        snapshot = capture_state_snapshot()
    return JSONResponse(build_workspace_payload(snapshot, include_rows=True))


@app.get("/api/workspace-job/stream")
async def workspace_job_status_stream() -> StreamingResponse:
    # Асинхронный опрос состояния задачи на событийном цикле: не занимает поток
    # из ограниченного пула (раньше блокирующий sync-генератор мог исчерпать
    # пул потоков и подвесить весь интерфейс). При обрыве соединения генератор
    # корректно отменяется.
    poll_interval = 0.5
    keepalive_ticks = max(1, int(WORKSPACE_JOB_STREAM_KEEPALIVE_SECONDS / poll_interval))

    async def event_stream() -> Any:
        last_payload: str | None = None
        idle_ticks = 0
        while True:
            with state_lock:
                snapshot = serialize_job(state.workspace_job)
            payload = json.dumps(snapshot, ensure_ascii=False)

            if payload != last_payload:
                last_payload = payload
                idle_ticks = 0
                yield f"data: {payload}\n\n"
                if not snapshot.get("active") and snapshot.get("status") in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    break
            else:
                idle_ticks += 1
                if idle_ticks >= keepalive_ticks:
                    idle_ticks = 0
                    yield ": keepalive\n\n"

            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/workspace-job/cancel")
def cancel_workspace_job() -> JSONResponse:
    with state_lock:
        if state.workspace_job is None or state.workspace_job.status not in {"running", "cancelling"}:
            return JSONResponse({"ok": False, "active": False})

        state.workspace_job.cancel_requested = True
        state.workspace_job.status = "cancelling"
        state.workspace_job.message = "Отменяю открытие папки."
    return JSONResponse({"ok": True, "active": True})


@app.post("/api/object-name")
async def update_object_name(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    try:
        channel = int(payload.get("channel"))
        object_id = int(payload.get("object_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Не удалось определить объект для переименования.") from exc

    if channel < 1 or channel > 5 or object_id < 1:
        raise HTTPException(status_code=400, detail="Укажите канал от 1 до 5 и object id от 1 и выше.")

    raw_name = str(payload.get("name") or "")
    normalized_name = " ".join(raw_name.split())
    mode = str(payload.get("mode") or "set").strip().lower()
    if mode not in {"create", "set", "reset"}:
        raise HTTPException(status_code=400, detail="Некорректный режим сохранения объекта.")

    if object_id > 30:
        raise HTTPException(status_code=400, detail="Object id должен быть в диапазоне от 1 до 30.")

    if mode != "reset":
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Название объекта не может быть пустым.")
        if len(normalized_name) > 120:
            raise HTTPException(status_code=400, detail="Название объекта не должно быть длиннее 120 символов.")

    with state_lock:
        if state.selected_root is None and state.pending_root is None:
            raise HTTPException(status_code=400, detail="Сначала выберите источник данных.")

        overrides = dict(state.object_name_overrides)
        if mode == "create" and (channel, object_id) in overrides:
            existing_name = overrides[(channel, object_id)]
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Для канала {channel} и object id {object_id} запись уже существует: "
                    f"«{existing_name}»."
                ),
            )

        if mode != "reset":
            overrides[(channel, object_id)] = normalized_name
        else:
            overrides.pop((channel, object_id), None)

        try:
            save_object_name_overrides(TEMP_ROOT, overrides)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Не удалось сохранить файл переименований: {exc}") from exc

        state.object_name_overrides = overrides
        if state.analysis is not None:
            apply_object_name_overrides(state.analysis, overrides)
        resolved_name = resolve_object_name(channel, object_id, overrides)

    return JSONResponse(
        {
            "ok": True,
            "mode": mode,
            "channel": channel,
            "object_id": object_id,
            "object_name": resolved_name,
            "has_json_name": (channel, object_id) in overrides,
            "is_custom_name": resolved_name != fallback_object_name(object_id),
            "object_rows": build_object_rows(state.object_name_overrides, state.analysis),
        }
    )


@app.post("/api/object-names-file/sync")
def sync_object_names_file() -> JSONResponse:
    with state_lock:
        analysis = require_analysis()

        existing_overrides = dict(state.object_name_overrides)
        path = object_name_overrides_path(TEMP_ROOT)
        file_existed = path.exists()
        next_overrides = build_seed_object_name_overrides(analysis, existing_overrides)
        added_entry_count = len(set(next_overrides.keys()) - set(existing_overrides.keys()))
        changed = next_overrides != existing_overrides or not file_existed

        if changed:
            try:
                save_object_name_overrides(TEMP_ROOT, next_overrides)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Не удалось сохранить файл переименований: {exc}") from exc

            state.object_name_overrides = next_overrides
            apply_object_name_overrides(analysis, next_overrides)

        return JSONResponse(
            {
                "ok": True,
                "changed": changed,
                "created": not file_existed,
                "file_path": str(path),
                "entry_count": len(next_overrides),
                "added_entry_count": added_entry_count,
                "object_rows": build_object_rows(state.object_name_overrides, state.analysis),
            }
        )


def chart_style_defaults() -> list[dict[str, str]]:
    """Стандартные оформления серий графика (id, подпись, цвет, тип линии) из
    SERIES_CONFIG — чтобы фронтенд мог показать их в панели настроек."""
    return [
        {
            "id": cfg["id"],
            "label": cfg["label"],
            "color": cfg["color"],
            "lineStyle": cfg.get("line_style", "solid"),
        }
        for cfg in SERIES_CONFIG
    ]


@app.get("/api/chart-styles")
def get_chart_styles() -> JSONResponse:
    return JSONResponse(
        {
            "series": load_chart_style_settings(),
            "defaults": chart_style_defaults(),
        }
    )


@app.post("/api/chart-styles")
async def update_chart_styles(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    series_styles = normalize_chart_style_series(payload.get("series"))
    try:
        save_chart_style_settings(series_styles)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось сохранить настройки графика: {exc}"
        ) from exc

    return JSONResponse({"ok": True, "series": series_styles})


@app.get("/api/settings")
def get_app_settings_route() -> JSONResponse:
    return JSONResponse({"settings": load_app_settings()})


@app.post("/api/settings")
async def update_app_settings_route(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    source = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    if not isinstance(source, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    # Частичное обновление: переданные поля накладываются поверх сохранённых,
    # чтобы можно было менять настройки по одной, не сбрасывая остальные.
    merged = {**load_app_settings(), **source}
    try:
        settings = save_app_settings(merged)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось сохранить настройки: {exc}"
        ) from exc

    return JSONResponse({"ok": True, "settings": settings})


@app.get("/api/wash-details")
def wash_details(key: str) -> JSONResponse:
    with state_lock:
        analysis = require_analysis()
        return JSONResponse(build_wash_detail(analysis, key))


@app.get("/api/wash-chart-data")
def wash_chart_data(key: str) -> JSONResponse:
    with state_lock:
        analysis = require_analysis()
        cycle = find_cycle(analysis, key)
        analysis_revision = state.analysis_revision
        analysis_cache_key = analysis.analysis_cache_key

    cached_payload = get_cached_chart_payload(analysis_revision, key)
    if cached_payload is not None:
        return JSONResponse(cached_payload)

    cached_payload = load_cached_chart_payload_disk(analysis_cache_key, key)
    if cached_payload is not None:
        set_cached_chart_payload(analysis_revision, key, cached_payload)
        return JSONResponse(cached_payload)

    payload = build_cycle_chart_payload(analysis, cycle)
    set_cached_chart_payload(analysis_revision, key, payload)
    save_cached_chart_payload_disk(analysis_cache_key, key, payload)
    return JSONResponse(payload)
