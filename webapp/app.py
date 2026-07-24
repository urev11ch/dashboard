from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import hashlib
import hmac
import ftplib
import logging
import os
import pickle
import posixpath
import re
import secrets
import shutil
import socket
import ssl
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote, urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from runtime_paths import resolve_cache_root, resolve_runtime_root
import wash_report as core
from webapp import __version__ as APP_VERSION
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
# Бюджет дискового кэша: одного TTL мало — автообновление FTP каждые 5 минут
# плодит новые записи (ключ зависит от mtime+size файлов), поэтому поверх TTL
# работает LRU-эвикция по объёму и количеству записей (см. prune_cache_root).
# «Время доступа» — mtime записи: попадание в кэш обновляет его (touch_cache_entry).
ARCHIVE_CACHE_MAX_BYTES = 2 * 1024**3
ARCHIVE_CACHE_MAX_ENTRIES = 256
ANALYSIS_CACHE_MAX_BYTES = 1024**3
ANALYSIS_CACHE_MAX_ENTRIES = 2048
# Сколько источников помним для удаления предыдущей версии их кэша.
CACHE_SOURCE_REGISTRY_LIMIT = 512
DB_ANALYSIS_CACHE_VERSION = 3
# v5: сэмплы вынесены из workspace-пикла в отдельные side-файлы по потокам
# (ws-samples-*), а в RAM подтягиваются лениво — см. make_sample_loader.
WORKSPACE_ANALYSIS_CACHE_VERSION = 5
CHART_PAYLOAD_DISK_CACHE_VERSION = 2
CHART_PAYLOAD_CACHE_LIMIT = 64
DB_ANALYSIS_MAX_WORKERS = 4
WORKSPACE_JOB_STREAM_KEEPALIVE_SECONDS = 10.0
# Сколько новый рабочий поток ждёт завершения предыдущего (см. run_workspace_job).
WORKSPACE_JOB_JOIN_TIMEOUT_SECONDS = 60.0
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
    "autostart": False,
    "archive_retention_enabled": False,
    "archive_retention_days": 365,
    "concentration_eval_enabled": False,
    "concentration_norms": {"alkali": None, "acid": None},
    "concentration_tolerance_percent": 10.0,
    # Требовать финальный шаг «Окончание мойки» (process 21): при True мойка без
    # него понижается до «Требует проверки». По умолчанию выключено — многие
    # станции не пишут этот шаг, и его отсутствие не должно считаться ошибкой.
    "require_completion_step": False,
}
ARCHIVE_RETENTION_MIN_DAYS = 1
ARCHIVE_RETENTION_MAX_DAYS = 730
# Нормативы концентрации рабочих растворов (%). Фазы задаёт ядро (wash_report).
CONCENTRATION_PHASE_KEYS = tuple(phase for phase, _pid, _label in core.CONCENTRATION_PHASES)
CONCENTRATION_MIN = 0.0
CONCENTRATION_MAX = 100.0
CONCENTRATION_TOLERANCE_MIN = 0.0
CONCENTRATION_TOLERANCE_MAX = 100.0
GITHUB_REPO = "urev11ch/dashboard"
UPDATE_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
# Автообновление: качаем только это вложение и только с этого префикса — URL
# приходит из ответа GitHub, но проверяем его отдельно (защита от подмены
# ссылки на чужой хост, если ответ окажется не тем, чего мы ждём).
UPDATE_ASSET_NAME = "OptiCIP-Dashboard-Setup.exe"
UPDATE_ASSET_URL_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
# Больше установщика (~22 МБ) быть не должно; ограничение отсекает бесконечный
# ответ, который иначе забил бы диск.
UPDATE_MAX_BYTES = 256 * 1024 * 1024
UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 300.0
# Как часто фоновый цикл просыпается, чтобы сверить, не пора ли обновлять FTP.
FTP_AUTO_REFRESH_POLL_SECONDS = 20.0
# Настраиваемые подписи результата мойки. Ядро (wash_report) считает результат
# в виде строк по умолчанию; здесь их можно переопределить в настройках.
RESULT_LABEL_CATEGORIES = ("completed", "check")
RESULT_LABEL_DEFAULTS: dict[str, str] = {
    "completed": "Завершено штатно",
    "check": "Требует проверки",
}
RESULT_LABEL_MAX_LEN = 120
# Все стандартные строки результата из ядра сводятся к двум категориям
# (варианты «были паузы» тоже попадают в «завершено»/«требует проверки»).
_RESULT_CATEGORY_BY_DEFAULT = {
    "Завершено штатно": "completed",
    "Завершено, были паузы": "completed",
    "Требует проверки": "check",
    "Требует проверки, были паузы": "check",
}
# CONCENTRATION_LOW_LABEL и CONCENTRATION_UNAVAILABLE_LABEL намеренно НЕ в
# маппинге: это самостоятельные подписи, которые apply_concentration_verdict
# показывает как есть (а категорию check выставляет явно). В маппинге пустой
# result_labels свёл бы их к «Требует проверки», и причина (концентрация ниже
# нормы либо отсутствие данных) потерялась бы.
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
# Формат id сохранённого FTP-подключения (см. ftp_connection_id): 12 hex-символов.
FTP_CONNECTION_ID_RE = re.compile(r"^[0-9a-f]{12}$")
# Папка удаляемого профиля: пока рабочий поток мог держать в ней файлы, она
# переименовывается в `<id>.deleted-<uuid>` и удаляется отложенно.
DELETED_PROFILE_DIR_RE = re.compile(r"\.deleted-[0-9a-f]{32}$")
# Сколько ждём завершения рабочего потока перед удалением папки профиля.
PROFILE_DELETE_JOIN_TIMEOUT_SECONDS = 30.0
# Штатная учётка выгрузки истории у Weintek (EasyBuilder Pro, Chapter 32):
# всегда `uploadhis`, пароль — [history upload password] панели, заводской 111111.
# Имя в приложении не редактируется — подключение = IP + PORT + PASS.
FTP_HISTORY_USERNAME = "uploadhis"
FTP_HISTORY_DEFAULT_PASSWORD = "111111"
DEFAULT_FTP_FORM_VALUES = {
    "host": "",
    "port": "21",
    "username": FTP_HISTORY_USERNAME,
    "password": FTP_HISTORY_DEFAULT_PASSWORD,
    "path": "/datalog",
}

# --- Обнаружение панелей в локальной сети (кнопка «Найти панель») ---------
# Скан только по кнопке, только по приватной локальной подсети, только порт 21.
FTP_DISCOVERY_PROBE_TIMEOUT = 0.4  # с на TCP-пробу порта 21
FTP_DISCOVERY_BANNER_TIMEOUT = 1.5  # с на чтение приветствия FTP (220)
FTP_DISCOVERY_CONCURRENCY = 128  # одновременных проб
FTP_DISCOVERY_MAX_HOSTS = 1024  # предохранитель на размер подсети (>/22 не сканируем)
# Признаки Weintek в приветствии FTP — мягкая эвристика по баннеру (панель может
# отдавать дженерик Pure-FTPd без этих слов). Опознаётся только для сортировки/
# пометки; в список попадают все FTP-хосты.
FTP_WEINTEK_HINTS = ("weintek", "cmt", "easybuilder", "ftpdmini", "hmi")
# Папки данных на панели (Data Sampling / алармы / рецепты).
FTP_WEINTEK_MARKER_DIRS = ("datalog", "eventlog", "recipe")
# Надёжное опознание панели — по её веб-интерфейсу EasyWeb: GET / отдаёт
# SPA-оболочку cMT с этими маркерами. Работает БЕЗ FTP-пароля и при TLS, поэтому
# это основной признак панели (баннер FTP — лишь мягкий запасной). Пробуем и
# HTTP :80, и HTTPS :443 (панели с «[TLS]» отдают веб только по https).
HTTP_DISCOVERY_PORT = 80
HTTPS_DISCOVERY_PORT = 443
# Порядок проб веб-интерфейса: (порт, использовать_TLS).
HTTP_EASYWEB_PORTS = ((HTTP_DISCOVERY_PORT, False), (HTTPS_DISCOVERY_PORT, True))
HTTP_EASYWEB_READ_LIMIT = 16384  # байт тела ответа достаточно (маркеры в <head>)
HTTP_EASYWEB_MARKERS = ("easywebconfig", "icon-weintek", "<title>cmt</title>")
# Самый надёжный признак: MAC-префикс (OUI) Weintek Labs. Берётся из ARP-таблицы
# ОС (её наполняют TCP-пробы скана), работает без пароля/web и при любом TLS,
# но только в пределах своей L2-подсети (ARP не ходит за маршрутизатор).
WEINTEK_MAC_PREFIXES = ("00:0c:26",)

PORTABLE_ENV_VAR = "OPTICIP_PORTABLE"
APP_DATA_SUBDIRS = ("datalog", "temp")


def portable_mode_enabled() -> bool:
    return str(os.environ.get(PORTABLE_ENV_VAR) or "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def resolve_app_data_root() -> Path:
    """Корень данных приложения.

    В собранной версии это постоянный пользовательский корень (%LOCALAPPDATA%,
    см. resolve_runtime_root): каталог рядом с .exe непригоден — при установке в
    Program Files он доступен на запись только администратору, и выбор корня «по
    факту записи» приводил к тому, что данные зависели от прав запуска и
    «пропадали» при переходе admin → обычный пользователь.

    Портативный режим (данные рядом с .exe) включается явно: OPTICIP_PORTABLE=1
    либо OPTICIP_RUNTIME_ROOT=<путь> (его учитывает resolve_runtime_root)."""
    if getattr(sys, "frozen", False):
        if portable_mode_enabled():
            return Path(sys.executable).resolve().parent
        return resolve_runtime_root()
    return PROJECT_ROOT


def legacy_app_data_root() -> Path | None:
    """Прежний корень данных собранной версии — каталог рядом с .exe."""
    if not getattr(sys, "frozen", False) or portable_mode_enabled():
        return None
    return Path(sys.executable).resolve().parent


def migrate_legacy_app_subdir(name: str, target: Path) -> None:
    """Переносит данные из прежнего каталога рядом с .exe в постоянный корень.
    Выполняется один раз: если целевая папка уже не пуста, ничего не трогаем."""
    legacy_root = legacy_app_data_root()
    if legacy_root is None:
        return
    legacy_dir = legacy_root / name
    try:
        if not legacy_dir.is_dir() or legacy_dir.resolve() == target.resolve():
            return
        if next(target.iterdir(), None) is not None:
            return
        entries = list(legacy_dir.iterdir())
    except OSError:
        return

    moved = 0
    for entry in entries:
        try:
            shutil.move(str(entry), str(target / entry.name))
            moved += 1
        except (OSError, shutil.Error):
            continue
    if moved:
        logging.info("Данные перенесены из `%s` в `%s`: %d элементов", legacy_dir, target, moved)


def resolve_app_subdir(name: str) -> Path:
    """Создаёт подпапку `name` в корне данных приложения."""
    candidate = resolve_app_data_root() / name
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        migrate_legacy_app_subdir(name, candidate)
        return candidate.resolve()
    except OSError:
        # Корень недоступен (например, проект распакован в read-only каталог) —
        # уходим в пользовательский runtime-корень.
        fallback = resolve_runtime_root() / name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()


# datalog — постоянное хранилище скачанных архивов (подпапки по месяцам).
# temp — служебные файлы приложения (имена объектов и т. п.).
DATALOG_ROOT = resolve_app_subdir("datalog")
TEMP_ROOT = resolve_app_subdir("temp")

ARCHIVE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
ANALYSIS_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
WEB_RUNTIME_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_auto_refresh_task: "asyncio.Task[None] | None" = None


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    # startup — подчищаем отложенно удаляемые профили FTP и поднимаем фоновый
    # цикл автообновления (папки уже созданы выше)
    global _auto_refresh_task
    purge_deleted_profile_dirs()
    _auto_refresh_task = asyncio.create_task(ftp_auto_refresh_loop())
    try:
        yield
    finally:
        # shutdown — останавливаем фоновую задачу. Дисковые кэши целиком не
        # удаляем: они общие для пользователя, и их может использовать второй
        # запущенный экземпляр приложения (десктоп + браузер). Ограничиваемся
        # возрастной очисткой устаревших записей.
        if _auto_refresh_task is not None:
            _auto_refresh_task.cancel()
            try:
                await _auto_refresh_task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - защита остановки
                logging.exception("Ошибка при остановке фонового автообновления")
            _auto_refresh_task = None
        cleanup_stale_disk_caches()


app = FastAPI(title="Отчеты по мойкам", lifespan=_app_lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Сервер не имеет аутентификации, а API даёт доступ к файловой системе
# (/workspace/open) и реестру FTP-подключений, поэтому:
#   1) главный барьер — фактический адрес клиента (request.client.host): пускаем
#      только loopback. Заголовку Host верить нельзя — он приходит от клиента, и
#      при запуске на 0.0.0.0 любой в сети мог подставить `Host: localhost`;
#   2) заголовки Host/Origin дополнительно защищают от DNS rebinding и CSRF
#      (form-POST/fetch с чужих страниц). Запросы без Origin пропускаем для
#      совместимости (pywebview, curl, собственные страницы).
# Осознанный удалённый доступ включается переменной окружения.
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOW_REMOTE_ENV_VAR = "OPTICIP_ALLOW_REMOTE"


def remote_access_allowed() -> bool:
    return str(os.environ.get(ALLOW_REMOTE_ENV_VAR) or "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def _is_loopback_address(value: str | None) -> bool:
    address = (value or "").strip().strip("[]")
    if not address:
        return False
    # IPv6 с zone-id (fe80::1%eth0) и IPv4-mapped адреса разбираются ipaddress.
    address = address.split("%", 1)[0]
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def client_is_local(request: Request) -> bool:
    client = request.client
    if client is None:
        # Нет TCP-пира (unix-сокет, внутренний транспорт) — удалённым быть не может.
        return True
    return _is_loopback_address(client.host)


def _is_local_hostname(hostname: str | None) -> bool:
    return (hostname or "").strip("[]").lower() in LOCAL_HOSTNAMES


def _header_host_is_local(host_header: str) -> bool:
    try:
        return _is_local_hostname(urlsplit(f"//{host_header}").hostname)
    except ValueError:
        return False


def _origin_is_local(origin_header: str) -> bool:
    try:
        return _is_local_hostname(urlsplit(origin_header).hostname)
    except ValueError:
        return False


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    if not client_is_local(request) and not remote_access_allowed():
        client_host = request.client.host if request.client else "?"
        logging.warning("Отклонён нелокальный запрос от %s к %s", client_host, request.url.path)
        return JSONResponse(
            {"detail": "Доступ разрешён только с локального компьютера."}, status_code=403
        )

    host_header = request.headers.get("host")
    if host_header and not _header_host_is_local(host_header) and not remote_access_allowed():
        return JSONResponse({"detail": "Недопустимый заголовок Host."}, status_code=403)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin_header = request.headers.get("origin")
        if origin_header and not _origin_is_local(origin_header) and not remote_access_allowed():
            return JSONResponse({"detail": "Недопустимый Origin запроса."}, status_code=403)
    return await call_next(request)


@dataclass
class ScanSummary:
    archive_count: int = 0
    ftp_source_count: int = 0
    # Файлы, которые не удалось скачать с FTP, и текст сбоя синхронизации:
    # показываем их пользователю, а не «глотаем» (см. materialize_ftp_sources).
    ftp_failed_files: list[str] = field(default_factory=list)
    ftp_error: str = ""
    # Базы, пропущенные при анализе (повреждены/не подходят по структуре).
    skipped_db_files: list[str] = field(default_factory=list)


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
class UpdateJob:
    """Скачивание установщика обновления. `path` заполняется только после
    успешной сверки sha256 — мост берёт оттуда файл на запуск, поэтому непустой
    path означает «проверено и можно исполнять»."""

    id: str
    version: str = ""
    status: str = "running"  # running | ready | error
    phase: str = "download"  # download | verify | ready
    downloaded: int = 0
    total: int = 0
    path: str = ""
    error: str | None = None
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
    update_job: UpdateJob | None = None
    last_sync_ts: float | None = None
    last_cleanup_ts: float | None = None
    # Панель, выбранная кнопкой «Подключиться» (зелёная строка + WebView/Графики/
    # Отключить в меню). Сессионное состояние, одна панель одновременно.
    connected_ftp_id: str = ""


@dataclass(frozen=True)
class AppStateSnapshot:
    analysis: core.AnalysisResult | None
    analysis_revision: int
    selected_root: Path | None
    pending_root: Path | None
    selected_display_root: str
    pending_display_root: str
    object_name_overrides: dict[tuple[int, int], str]
    error: str | None
    scan_summary: ScanSummary
    workspace_job_payload: dict[str, Any]
    connected_ftp_id: str


state = AppState()
state_lock = threading.Lock()
# Настройки читаются-меняются-пишутся (частичное обновление), поэтому у файла
# настроек свой лок — иначе параллельные POST /api/settings теряют изменения.
# RLock: save_app_settings вызывается и сам по себе, и изнутри секции.
app_settings_lock = threading.RLock()
archive_cache_lock = threading.Lock()
analysis_cache_lock = threading.Lock()
chart_payload_cache_lock = threading.Lock()
# Последний ключ кэша по источнику (архив / .db / рабочая папка) — чтобы удалять
# предыдущую версию записи того же источника. Ограничены по размеру: раньше
# словарь рос монотонно и не чистился.
archive_cache_keys_by_source: OrderedDict[str, str] = OrderedDict()
db_cache_keys_by_source: OrderedDict[str, str] = OrderedDict()
workspace_cache_keys_by_source: OrderedDict[str, str] = OrderedDict()
chart_payload_cache: OrderedDict[tuple[int, str], dict[str, Any]] = OrderedDict()


def format_source_label(value: str) -> str:
    return Path(value).name


def format_file_list(names: list[str], limit: int = 3) -> str:
    """Короткий перечень имён файлов для сообщения пользователю."""
    shown = ", ".join(f"`{name}`" for name in names[:limit])
    remainder = len(names) - limit
    return f"{shown} и ещё {remainder}" if remainder > 0 else shown


def copy_scan_summary(summary: ScanSummary) -> ScanSummary:
    return ScanSummary(
        archive_count=summary.archive_count,
        ftp_source_count=summary.ftp_source_count,
        ftp_failed_files=list(summary.ftp_failed_files),
        ftp_error=summary.ftp_error,
        skipped_db_files=list(summary.skipped_db_files),
    )


def capture_state_snapshot() -> AppStateSnapshot:
    return AppStateSnapshot(
        analysis=state.analysis,
        analysis_revision=state.analysis_revision,
        selected_root=state.selected_root,
        pending_root=state.pending_root,
        selected_display_root=state.selected_display_root,
        pending_display_root=state.pending_display_root,
        object_name_overrides=dict(state.object_name_overrides),
        error=state.error,
        scan_summary=copy_scan_summary(state.scan_summary),
        workspace_job_payload=serialize_job(state.workspace_job),
        connected_ftp_id=state.connected_ftp_id,
    )


def is_ignored_workspace_dir(path: Path, ignored_paths: set[Path]) -> bool:
    if path.name.lower() in IGNORED_WORKSPACE_DIR_NAMES:
        return True
    if DELETED_PROFILE_DIR_RE.search(path.name):
        return True

    try:
        return path.resolve() in ignored_paths
    except OSError:
        return False


def is_ftp_profile(path: Path | None) -> bool:
    """Это папка профиля FTP-подключения (datalog/<id>), а не обычная папка?"""
    if path is None:
        return False
    try:
        return path.resolve().parent == DATALOG_ROOT.resolve()
    except OSError:
        return False


# Имя `.tmp` без уникального суффикса ломает атомарную запись, если запущено два
# экземпляра приложения (общие temp/ и кэш): один перезапишет чужой временный
# файл. Поэтому у каждой записи свой суффикс.
def atomic_write_bytes(path: Path, data: bytes) -> None:
    temp_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temp_path.open("wb") as handle:
            handle.write(data)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def local_tz_offset_min() -> int:
    """Смещение зоны сервера в минутах (с учётом летнего времени). Клиент считает
    границы суток по нему: `start_day` формируется в зоне сервера, а не браузера."""
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


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
# Windows — DPAPI (CryptProtectData), пароль шифруется ключом пользователя ОС.
# Linux/macOS — системное хранилище секретов через keyring (Secret Service /
# Keychain). Если хранилища нет (headless/CI/контейнер) — фолбэк на обратимое
# base64-кодирование (не шифрование), чтобы приложение всё равно работало.
KEYRING_SERVICE = "OptiCIP Dashboard FTP"


def _keyring_backend() -> Any | None:
    """Модуль keyring, если он есть и не на Windows (там DPAPI). None — нет keyring."""
    if sys.platform == "win32":
        return None
    try:
        import keyring
    except Exception:
        return None
    return keyring


def _keyring_store(secret_id: str, value: str) -> bool:
    keyring = _keyring_backend()
    if keyring is None or not secret_id:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, secret_id, value)
        return True
    except Exception:
        # Нет backend'а (NoKeyringError) или сбой доступа — уходим в base64-фолбэк.
        logging.warning("Системное хранилище секретов недоступно, использую локальное кодирование.")
        return False


def _keyring_fetch(secret_id: str) -> str:
    keyring = _keyring_backend()
    if keyring is None or not secret_id:
        return ""
    try:
        return keyring.get_password(KEYRING_SERVICE, secret_id) or ""
    except Exception:
        logging.warning("Не удалось прочитать пароль из системного хранилища секретов.")
        return ""


def _keyring_delete(secret_id: str) -> None:
    keyring = _keyring_backend()
    if keyring is None or not secret_id:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, secret_id)
    except Exception:
        pass  # best-effort очистка; запись могла отсутствовать (был base64-фолбэк)


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


def protect_secret(value: str, secret_id: str = "") -> str:
    """Токен-обёртка над паролем. Схемы: `dpapi:` (Windows), `keyring:<id>`
    (секрет в системном хранилище, в токене только ссылка), `b64:` (фолбэк).
    secret_id нужен для keyring — обычно id FTP-подключения."""
    raw = (value or "").encode("utf-8")
    if not raw:
        return ""
    blob = _dpapi_crypt(raw, protect=True)
    if blob is not None:
        return "dpapi:" + base64.b64encode(blob).decode("ascii")
    # Не-Windows: системное хранилище секретов, если доступно и есть id.
    if _keyring_store(secret_id, value):
        return "keyring:" + secret_id
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
    if token.startswith("keyring:"):
        return _keyring_fetch(token[len("keyring:"):])
    if token.startswith("b64:"):
        try:
            return base64.b64decode(token[4:]).decode("utf-8")
        except Exception:
            return ""
    return token  # legacy plaintext


# ---- реестр сохранённых FTP-подключений (несколько панелей) ------------
# Читаем-модифицируем-пишем реестр под локом, чтобы параллельные запросы
# не теряли изменения друг друга (lost update).
ftp_sources_lock = threading.Lock()


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
        candidates = list(DATALOG_ROOT.iterdir())
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
    profile_dir = DATALOG_ROOT / conn_id
    try:
        if not profile_dir.exists() or profile_dir.resolve().parent != DATALOG_ROOT.resolve():
            return
    except OSError:
        return

    trash_dir = DATALOG_ROOT / f"{conn_id}.deleted-{uuid.uuid4().hex}"
    try:
        profile_dir.rename(trash_dir)
    except OSError:
        # Переименовать не вышло (Windows держит открытый файл) — удаляем на месте,
        # но всё равно после завершения рабочего потока.
        trash_dir = profile_dir

    worker = _workspace_job_thread

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
    value = directory_size_bytes(DATALOG_ROOT)
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

    atomic_write_json(
        path,
        {
            "version": OBJECT_NAME_OVERRIDES_VERSION,
            "objects": objects_payload,
        },
    )


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
    atomic_write_json(
        path,
        {
            "version": CHART_STYLE_SETTINGS_VERSION,
            "series": series_styles,
        },
    )


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
    atomic_write_json(target, {"version": FOLDER_SOURCE_SETTINGS_VERSION, "last_path": value})


# ---- общие настройки приложения (автообновление FTP и т. п.) ----------------
def app_settings_path() -> Path:
    return TEMP_ROOT / APP_SETTINGS_FILENAME


def _coerce_auto_refresh_minutes(value: Any, fallback: int) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(FTP_AUTO_REFRESH_MIN_MINUTES, min(FTP_AUTO_REFRESH_MAX_MINUTES, minutes))


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _coerce_concentration(value: Any) -> float | None:
    """Норматив концентрации (%): число в [0..100] или None (не задан).

    Пустая строка/None/нечисловое → None. Отрицательные и >100 клампятся в диапазон.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(CONCENTRATION_MIN, min(CONCENTRATION_MAX, number))


def _coerce_tolerance(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:  # NaN
        return fallback
    return max(CONCENTRATION_TOLERANCE_MIN, min(CONCENTRATION_TOLERANCE_MAX, number))


def normalize_app_settings(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}

    enabled = _coerce_bool(
        data.get("ftp_auto_refresh_enabled"),
        DEFAULT_APP_SETTINGS["ftp_auto_refresh_enabled"],
    )

    minutes = _coerce_auto_refresh_minutes(
        data.get("ftp_auto_refresh_minutes"),
        DEFAULT_APP_SETTINGS["ftp_auto_refresh_minutes"],
    )

    default_folder_path = data.get("default_folder_path", DEFAULT_APP_SETTINGS["default_folder_path"])
    if not isinstance(default_folder_path, str):
        default_folder_path = ""
    default_folder_path = default_folder_path.strip()

    raw_labels = data.get("result_labels")
    raw_labels = raw_labels if isinstance(raw_labels, dict) else {}
    result_labels: dict[str, str] = {}
    for category in RESULT_LABEL_CATEGORIES:
        value = raw_labels.get(category)
        value = value.strip() if isinstance(value, str) else ""
        # Пустая строка означает «использовать значение по умолчанию».
        result_labels[category] = value[:RESULT_LABEL_MAX_LEN]

    try:
        retention_days = int(data.get("archive_retention_days"))
    except (TypeError, ValueError):
        retention_days = DEFAULT_APP_SETTINGS["archive_retention_days"]
    retention_days = max(ARCHIVE_RETENTION_MIN_DAYS, min(ARCHIVE_RETENTION_MAX_DAYS, retention_days))

    raw_norms = data.get("concentration_norms")
    raw_norms = raw_norms if isinstance(raw_norms, dict) else {}
    concentration_norms = {
        phase: _coerce_concentration(raw_norms.get(phase)) for phase in CONCENTRATION_PHASE_KEYS
    }

    return {
        "ftp_auto_refresh_enabled": enabled,
        "ftp_auto_refresh_minutes": minutes,
        "default_folder_path": default_folder_path,
        "result_labels": result_labels,
        "autostart": _coerce_bool(data.get("autostart"), DEFAULT_APP_SETTINGS["autostart"]),
        "archive_retention_enabled": _coerce_bool(
            data.get("archive_retention_enabled"), DEFAULT_APP_SETTINGS["archive_retention_enabled"]
        ),
        "archive_retention_days": retention_days,
        "concentration_eval_enabled": _coerce_bool(
            data.get("concentration_eval_enabled"),
            DEFAULT_APP_SETTINGS["concentration_eval_enabled"],
        ),
        "concentration_norms": concentration_norms,
        "concentration_tolerance_percent": _coerce_tolerance(
            data.get("concentration_tolerance_percent"),
            DEFAULT_APP_SETTINGS["concentration_tolerance_percent"],
        ),
        "require_completion_step": _coerce_bool(
            data.get("require_completion_step"),
            DEFAULT_APP_SETTINGS["require_completion_step"],
        ),
    }


def resolve_cycle_default_status(
    analysis: core.AnalysisResult, cycle: core.Cycle, *, require_completion_step: bool
) -> str:
    """Базовый статус мойки с учётом тумблера «требовать шаг окончания».
    Применяется на чтении (как и оценка концентрации), поэтому смена настройки
    действует сразу, без переанализа. При включённом требовании берём готовый
    индекс ядра (посчитан с require_completion_step=True); при выключенном —
    пересчитываем из операций без требования финального шага."""
    if require_completion_step:
        return analysis.cycle_results_by_key.get(
            core.make_cycle_key(cycle),
            core.cycle_result_label_from_operations(
                cycle.operations, require_completion_step=True
            ),
        )
    return core.cycle_result_label_from_operations(
        cycle.operations, require_completion_step=False
    )


def resolve_result_label(default_label: str, result_labels: dict[str, str] | None) -> str:
    """Переводит стандартную подпись результата мойки в пользовательскую, если та
    задана в настройках. Незнакомые строки возвращаются как есть."""
    category = _RESULT_CATEGORY_BY_DEFAULT.get(default_label)
    if category is None:
        return default_label
    custom = (result_labels or {}).get(category) or ""
    return custom or RESULT_LABEL_DEFAULTS[category]


def resolve_result_kind(default_label: str) -> str:
    """Категория результата (`completed`/`check`) по стандартной строке ядра —
    для цветовой индикации на фронтенде независимо от текста подписи."""
    return _RESULT_CATEGORY_BY_DEFAULT.get(default_label, "")


def evaluate_cycle_concentration(
    analysis: core.AnalysisResult,
    cycle: core.Cycle,
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    """Оценка концентрации мойки по настройкам, либо None если оценивать нечего.

    None означает «функция выключена, нормативы не заданы или в мойке нет
    оцениваемых фаз» — в этом случае вердикт и payload остаются как раньше.

    kind="unavailable" — отдельный случай: сэмплы мойки прочитать не удалось.
    Возвращать здесь None нельзя, иначе мойка с концентрацией ниже нормы молча
    показалась бы завершённой штатно (см. SampleStreamUnavailable).
    """
    if not settings.get("concentration_eval_enabled"):
        return None
    try:
        samples = core.analysis_samples_for_cycle(analysis, cycle)
    except core.SampleStreamUnavailable:
        logging.warning(
            "Сэмплы мойки недоступны, концентрация не оценена: канал=%s, ключ=%s",
            cycle.channel,
            core.make_cycle_key(cycle),
        )
        return {"phases": [], "kind": "unavailable"}
    result = core.evaluate_concentration(
        samples,
        settings.get("concentration_norms") or {},
        settings.get("concentration_tolerance_percent") or 0.0,
    )
    return result if result["kind"] is not None else None


def apply_concentration_verdict(
    default_status: str,
    result_labels: dict[str, str] | None,
    concentration: dict[str, Any] | None,
) -> tuple[str, str]:
    """Итоговые (подпись, категория) результата с учётом оценки концентрации.

    Концентрация ниже нормы делает мойку «требующей проверки». Если базовый вердикт
    был «завершено», подпись меняется на «Концентрация ниже нормы» (чтобы причина
    была видна); если мойка и так требовала проверки — её текст не затираем.

    Недоступные сэмплы ("unavailable") дают ту же категорию «требует проверки»:
    оценка не выполнена, и выдавать это за успешную мойку нельзя — оператор должен
    увидеть, что вердикт не подтверждён данными, а не поверить в тишину.
    """
    result_kind = resolve_result_kind(default_status)
    effective_status = default_status
    kind = concentration.get("kind") if concentration is not None else None
    if kind in ("low", "unavailable"):
        result_kind = "check"
        if resolve_result_kind(default_status) == "completed":
            effective_status = (
                core.CONCENTRATION_LOW_LABEL
                if kind == "low"
                else core.CONCENTRATION_UNAVAILABLE_LABEL
            )
    return resolve_result_label(effective_status, result_labels), result_kind


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
    with app_settings_lock:
        atomic_write_json(path, {"version": APP_SETTINGS_VERSION, "settings": settings})
    return settings


def _deep_merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивное слияние: вложенные словари (result_labels, concentration_norms)
    сливаются по ключам, а не затираются целиком. Иначе частичный POST без второй
    фазы концентрации молча сбрасывал бы недостающие ключи в дефолт/None."""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_settings(existing, value)
        else:
            merged[key] = value
    return merged


def update_app_settings(source: dict[str, Any]) -> dict[str, Any]:
    """Частичное обновление настроек: переданные поля накладываются поверх
    сохранённых. Чтение и запись — под общим локом, иначе два параллельных
    запроса читают одно состояние и второй затирает изменения первого."""
    with app_settings_lock:
        merged = _deep_merge_settings(load_app_settings(), source)
        return save_app_settings(merged)


def apply_object_name_overrides(
    analysis: core.AnalysisResult | None,
    overrides: dict[tuple[int, int], str],
) -> None:
    if analysis is None:
        return

    collections = (
        analysis.segments,
        analysis.cycles,
        analysis.overviews,
    )
    for collection in collections:
        for item in collection:
            item.object_name = resolve_object_name(item.channel, item.object_id, overrides)

    # Список заменяем целиком (не сортируем in-place): другие потоки могут в это
    # время итерировать прежний список overviews вне state_lock.
    analysis.overviews = sorted(
        analysis.overviews, key=lambda item: (item.channel, item.object_name, item.start_ts)
    )


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
        for cache_file in ANALYSIS_CACHE_ROOT.glob("chart-*.pkl"):
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
    state.connected_ftp_id = ""  # «Отключить»: снимаем пометку подключения
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

        # Отсутствующие в событии поля не сбрасывают прежние значения — иначе
        # сообщение без current/total обнуляло бы прогресс-бар.
        phase = str(payload.get("phase") or job.phase)
        message = str(payload.get("message") or job.message)
        raw_current = payload.get("current")
        raw_total = payload.get("total")
        current = int(raw_current) if raw_current is not None else job.current
        total = int(raw_total) if raw_total is not None else job.total
        item = str(payload.get("item") or job.item)

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
    # `PurePosixPath` не разбивает по `\`, поэтому Windows-разделители и имена
    # с диском (`..\..\evil.db`, `C:x`) отклоняем сразу.
    if "\\" in name or ":" in name:
        return None
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
    resolved_root = target_root.resolve()

    def resolve_member_target(relative_path: Path) -> Path | None:
        # Финальная страховка от path traversal: записываем только внутрь
        # target_root, что бы ни осталось в имени после санитизации.
        target_path = (target_root / relative_path).resolve()
        if not target_path.is_relative_to(resolved_root):
            return None
        return target_path

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
                target_path = resolve_member_target(relative_path)
                if target_path is None:
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(member) as source, target_path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                extracted_paths.append(target_path)
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
                target_path = resolve_member_target(relative_path)
                if target_path is None:
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                extracted_member = handle.extractfile(member)
                if extracted_member is None:
                    continue
                with extracted_member as source, target_path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                extracted_paths.append(target_path)

    return extracted_paths


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
            return directory_size_bytes(path)
        return path.stat().st_size
    except OSError:
        return 0


def is_protected_cache_entry(path: Path) -> bool:
    """Служебные записи, которые нельзя вытеснять: рабочая папка отчётов лежит
    внутри корня кэша, а незавершённые распаковки (`<key>.tmp-<uuid>`) пишет
    другой поток — он сам за собой уберёт."""
    if ".tmp-" in path.name:
        return True
    return path.name == WEB_RUNTIME_OUTPUT_DIR.name and path.parent == ANALYSIS_CACHE_ROOT


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
            ARCHIVE_CACHE_ROOT,
            ttl_seconds=ARCHIVE_CACHE_TTL_SECONDS,
            max_bytes=ARCHIVE_CACHE_MAX_BYTES,
            max_entries=ARCHIVE_CACHE_MAX_ENTRIES,
        )


def prune_analysis_cache() -> None:
    with analysis_cache_lock:
        prune_cache_root(
            ANALYSIS_CACHE_ROOT,
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
    while len(registry) > CACHE_SOURCE_REGISTRY_LIMIT:
        registry.popitem(last=False)
    if previous_key is None or previous_key == cache_key:
        return None
    return previous_key


def cleanup_stale_archive_cache(source_path: Path, cache_key: str) -> None:
    previous_key = remember_cache_key(archive_cache_keys_by_source, str(source_path), cache_key)
    if previous_key is None:
        return
    remove_cache_entry(ARCHIVE_CACHE_ROOT / previous_key)


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
            for path in ANALYSIS_CACHE_ROOT.iterdir()
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
    cache_dir = ARCHIVE_CACHE_ROOT / cache_key

    with archive_cache_lock:
        cleanup_stale_archive_cache(archive_path, cache_key)
        if cache_dir.exists():
            touch_cache_entry(cache_dir)
            return sorted(path.resolve() for path in cache_dir.rglob("*.db") if path.is_file())

    # Временная папка уникальна на вызов: параллельная распаковка того же
    # архива в другом потоке не должна удалять или переименовывать чужой tmp.
    temp_dir = ARCHIVE_CACHE_ROOT / f"{cache_key}.tmp-{uuid.uuid4().hex}"
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
    return ANALYSIS_CACHE_ROOT / f"db-{cache_key}.pkl"


def workspace_analysis_cache_key(db_files: list[Path], *, max_gap_seconds: float) -> str:
    payload_parts = [f"v{WORKSPACE_ANALYSIS_CACHE_VERSION}", f"gap:{max_gap_seconds:.6f}"]
    for db_path in sorted(db_files, key=lambda item: str(item).lower()):
        payload_parts.append(f"{db_path}::{db_analysis_cache_key(db_path)}")
    payload = "\n".join(payload_parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def workspace_analysis_cache_path(cache_key: str) -> Path:
    return ANALYSIS_CACHE_ROOT / f"workspace-{cache_key}.pkl"


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
        key_path = ANALYSIS_CACHE_ROOT / "cache-hmac.key"
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
    return ANALYSIS_CACHE_ROOT / f"ws-samples-{prefix}-{stream_hash}.pkl"


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
    return ANALYSIS_CACHE_ROOT / f"chart-{prefix}-{cache_key}.pkl"


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

    prune_archive_cache()

    # Сначала докачиваем свежие архивы с FTP в datalog/ГГГГ-ММ/, чтобы обход
    # ниже увидел и их, и ранее скачанные за прошлые месяцы.
    ftp_result = materialize_ftp_sources(
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
            candidate = current_root_path / filename
            lower_name = filename.lower()

            # resolve() на каждый файл — лишний системный вызов на элемент дерева;
            # os.walk и так идёт от уже нормализованного корня.
            if lower_name.endswith(".db"):
                direct_db_files.append(candidate)
            elif any(lower_name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES):
                archive_files.append(candidate)

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
        ftp_source_count=len(ftp_result.present_files),
        ftp_failed_files=list(ftp_result.failed_files),
        ftp_error=ftp_result.ftp_error_message,
    )


def analyze_db_files_incremental(
    db_files: list[Path],
    *,
    output_dir: Path,
    max_gap_seconds: float = 15.0,
    source_key: str = "",
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[core.AnalysisResult, list[str]]:
    """Возвращает сводный анализ и имена пропущенных баз (битые/неподходящие)."""
    prune_analysis_cache()

    workspace_cache_key = workspace_analysis_cache_key(db_files, max_gap_seconds=max_gap_seconds)
    cached = load_cached_workspace_analysis(workspace_cache_key)
    if cached is not None:
        cached_analysis, cached_skipped = cached
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
        return cached_analysis, cached_skipped

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
            chunks_by_db[str(db_path)] = cached_chunk
            continue

        core.emit_progress(
            progress_callback,
            phase="preflight",
            message=f"Проверяю файл {index} из {total_files}.",
            current=index,
            total=total_files,
            item=db_path.name,
        )
        # Битая или неподходящая база (нет таблицы `data`, повреждён файл,
        # исчез под работающим анализом) не должна валить весь джоб: файл
        # пропускаем, а пользователю потом показываем, сколько таких было.
        try:
            channel = core.preflight_db_file(db_path)
        except (SystemExit, sqlite3.Error, OSError, ValueError) as exc:
            logging.warning("Файл `%s` пропущен: %s", db_path.name, exc)
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
                    # Файл мог оказаться битым уже на разборе данных (или исчезнуть
                    # под работающим анализом) — пропускаем его, а не джоб целиком.
                    try:
                        chunk = future.result()
                    except (SystemExit, sqlite3.Error, OSError, ValueError) as exc:
                        logging.warning("Файл `%s` пропущен: %s", db_path.name, exc)
                        skipped_db_files.append(db_path.name)
                        continue
                    save_cached_db_analysis(db_path, chunk)
                    chunks_by_db[str(db_path)] = chunk
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
        for db_key in (str(path) for path in db_files)
        if db_key in chunks_by_db
    ]

    if not chunks:
        if skipped_db_files:
            raise SystemExit(
                "Ни одну базу данных не удалось прочитать: "
                f"{format_file_list(skipped_db_files)}. "
                "Проверьте, что файлы не повреждены и имеют вид `Canal_*.db`."
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
    save_cached_workspace_analysis(
        workspace_cache_key,
        analysis,
        source_key=source_key,
        skipped_db_files=skipped_db_files,
    )
    return analysis, skipped_db_files


def build_job_completion_message(scan_summary: ScanSummary) -> str:
    """Сообщение об успешном обновлении. Пропущенные базы и несостоявшиеся
    загрузки — не молчаливые: пользователь должен видеть, что часть данных не
    попала в отчёт."""
    notes: list[str] = []
    if scan_summary.skipped_db_files:
        notes.append(
            f"пропущено баз: {len(scan_summary.skipped_db_files)} "
            f"({format_file_list(scan_summary.skipped_db_files)})"
        )
    if scan_summary.ftp_failed_files:
        notes.append(
            f"не скачано файлов с FTP: {len(scan_summary.ftp_failed_files)} "
            f"({format_file_list(scan_summary.ftp_failed_files)})"
        )
    if scan_summary.ftp_error:
        notes.append(f"синхронизация с FTP не удалась ({scan_summary.ftp_error})")

    if not notes:
        return "Данные успешно обновлены."
    return "Данные обновлены, но " + "; ".join(notes) + "."


def run_workspace_job(
    job_id: str,
    target_root: Path,
    previous_thread: threading.Thread | None = None,
) -> None:
    progress_callback = lambda payload: push_job_progress(job_id, payload)
    cancel_check = lambda: job_cancel_requested(job_id)

    # Дожидаемся завершения предыдущего джоба (ему уже выставлен
    # cancel_requested), чтобы два потока не писали одни и те же файлы
    # зеркала. Ждём здесь, в рабочем потоке, а не под state_lock. Если
    # предыдущий поток так и не завершился, второй параллельно не запускаем:
    # иначе оба пишут в одно зеркало и портят скачанные базы.
    if previous_thread is not None and previous_thread.is_alive():
        previous_thread.join(timeout=WORKSPACE_JOB_JOIN_TIMEOUT_SECONDS)
        if previous_thread.is_alive():
            finish_workspace_job_failed(
                job_id,
                "Предыдущая обработка источника не завершилась вовремя. "
                "Повторите попытку через некоторое время.",
            )
            return

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

        analysis, skipped_db_files = analyze_db_files_incremental(
            db_files,
            output_dir=WEB_RUNTIME_OUTPUT_DIR,
            source_key=str(target_root),
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        scan_summary.skipped_db_files = skipped_db_files
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
            state.last_sync_ts = time.time()
            clear_chart_payload_cache()

            job.target_root = target_root
            job.status = "completed"
            job.phase = "completed"
            job.message = build_job_completion_message(scan_summary)
            job.current = max(job.current, job.total)
            job.finished_at = time.time()
    except core.AnalysisCancelledError:
        finish_workspace_job_cancelled(job_id, "Обработка источника отменена.")
    except SystemExit as exc:
        message = str(exc) or "Не удалось открыть выбранный источник."
        finish_workspace_job_failed(job_id, message)
    except Exception as exc:  # pragma: no cover - safety net for background worker
        finish_workspace_job_failed(job_id, f"Не удалось открыть источник: {exc}")


_workspace_job_thread: threading.Thread | None = None


def start_workspace_job(
    candidate: Path,
    *,
    display_target: str | None = None,
    background: bool = False,
) -> None:
    global _workspace_job_thread
    previous_thread = _workspace_job_thread
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
        args=(job.id, resolved_candidate, previous_thread),
        name="wash-workspace-loader",
        daemon=True,
    )
    _workspace_job_thread = thread
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

        if not is_ftp_profile(target_root):
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

            # load_app_settings читает и парсит файл с диска, а trigger_ftp_auto_refresh
            # берёт state_lock (threading.Lock) и делает syscall'ы — оба уводим в
            # поток, иначе ожидание лока встаёт на event loop и подвешивает все
            # запросы и SSE (та же защита, что в workspace_job_status_stream).
            settings = await asyncio.to_thread(load_app_settings)
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
            if await asyncio.to_thread(trigger_ftp_auto_refresh):
                logging.info("Фоновое автообновление FTP запущено.")
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - защита фонового цикла
            logging.exception("Сбой фонового автообновления FTP")


def parse_cycle_key(key: str) -> tuple[str, int, int, int, int, int]:
    # Режем справа: последние 5 полей — числа, а source_db (путь) сам может
    # содержать «::». split слева отдал бы часть пути в channel и врал бы 400.
    parts = key.rsplit("::", 5)
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


def build_wash_rows(
    analysis: core.AnalysisResult,
    settings: dict[str, Any],
    conc_verdicts: dict[str, dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Форматирует строки списка моек. Вердикты концентрации приходят готовыми
    (conc_verdicts), поэтому сэмплы с диска здесь НЕ читаются — сборка дешёвая и
    не зависит от концентрационных настроек (только от меток/тумблера/анализа)."""
    result_labels = settings["result_labels"]
    rows: list[dict[str, Any]] = []
    for cycle in analysis.sorted_cycles:
        cycle_key = core.make_cycle_key(cycle)
        date_time = core.format_ts(cycle.start_ts)
        default_status = resolve_cycle_default_status(
            analysis, cycle, require_completion_step=settings["require_completion_step"]
        )
        concentration = conc_verdicts.get(cycle_key)
        status, result_kind = apply_concentration_verdict(
            default_status, result_labels, concentration
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
                "result_kind": result_kind,
                "concentration_kind": concentration["kind"] if concentration else None,
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


# Кэш строк списка моек для /api/workspace-data: пересборка нужна только при
# смене анализа/оверрайдов имён (analysis_revision) или файла настроек (mtime).
# Собирается ВНЕ state_lock: сборка десятков тысяч словарей и чтение файла
# настроек под общим локом подвешивали и остальные запросы, и SSE.
_wash_rows_cache: dict[str, Any] = {"revision": None, "settings_mtime": None, "rows": []}
_wash_rows_cache_lock = threading.Lock()

# Кэш вердиктов концентрации по циклам. Отдельно от строк, потому что вычисление
# концентрации ТЯЖЁЛОЕ (читает с диска полные потоки сэмплов всех каналов), но
# зависит только от анализа и КОНЦЕНТРАЦИОННЫХ настроек (вкл/нормы/допуск) — не от
# меток результата и тумблера завершения. Так смена метки/тумблера пересобирает
# строки дёшево, не перечитывая сэмплы. Ключ: (analysis_revision, сигнатура
# концентрационных настроек).
_conc_verdicts_cache: dict[str, Any] = {"key": None, "verdicts": {}}
_conc_verdicts_cache_lock = threading.Lock()


def _concentration_settings_signature(settings: dict[str, Any]) -> tuple[Any, ...]:
    """Сигнатура настроек, влияющих на вердикт концентрации. Выключенная оценка —
    один общий ключ (вердикты не зависят от норм/допуска)."""
    if not settings.get("concentration_eval_enabled"):
        return ("off",)
    norms = settings.get("concentration_norms") or {}
    tol = settings.get("concentration_tolerance_percent") or 0.0
    return ("on", json.dumps(norms, sort_keys=True, ensure_ascii=False), float(tol))


def concentration_verdicts_cached(
    analysis: core.AnalysisResult,
    analysis_revision: int,
    settings: dict[str, Any],
) -> dict[str, dict[str, Any] | None]:
    """Вердикты концентрации по cycle_key с кэшем. Сэмплы читаются только при
    промахе (смена анализа или концентрационных настроек)."""
    key = (analysis_revision, _concentration_settings_signature(settings))
    with _conc_verdicts_cache_lock:
        if _conc_verdicts_cache["key"] == key:
            return _conc_verdicts_cache["verdicts"]

    # Промах — считаем вне лока (тяжёлое чтение сэмплов).
    verdicts: dict[str, dict[str, Any] | None] = {}
    if settings.get("concentration_eval_enabled"):
        for cycle in analysis.sorted_cycles:
            verdicts[core.make_cycle_key(cycle)] = evaluate_cycle_concentration(
                analysis, cycle, settings
            )
    with _conc_verdicts_cache_lock:
        _conc_verdicts_cache["key"] = key
        _conc_verdicts_cache["verdicts"] = verdicts
    return verdicts


def app_settings_mtime_ns() -> int | None:
    try:
        return app_settings_path().stat().st_mtime_ns
    except OSError:
        return None


def build_wash_rows_cached(
    analysis: core.AnalysisResult | None,
    analysis_revision: int,
) -> list[dict[str, Any]]:
    """Строки списка моек с кэшем; вызывать вне state_lock."""
    if analysis is None:
        return []

    settings_mtime = app_settings_mtime_ns()
    with _wash_rows_cache_lock:
        if (
            _wash_rows_cache["revision"] == analysis_revision
            and _wash_rows_cache["settings_mtime"] == settings_mtime
        ):
            return _wash_rows_cache["rows"]

    settings = load_app_settings()
    # Вердикты концентрации — из своего кэша (сэмплы читаются только при смене
    # анализа/концентрационных настроек, а не на каждое сохранение настроек).
    conc_verdicts = concentration_verdicts_cached(analysis, analysis_revision, settings)
    rows = build_wash_rows(analysis, settings, conc_verdicts)
    with _wash_rows_cache_lock:
        _wash_rows_cache["revision"] = analysis_revision
        _wash_rows_cache["settings_mtime"] = settings_mtime
        _wash_rows_cache["rows"] = rows
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
    settings = load_app_settings()
    default_status = resolve_cycle_default_status(
        analysis, cycle, require_completion_step=settings["require_completion_step"]
    )
    concentration = evaluate_cycle_concentration(analysis, cycle, settings)
    status, result_kind = apply_concentration_verdict(
        default_status, settings["result_labels"], concentration
    )

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
        "status": status,
        "result_kind": result_kind,
        "concentration_kind": concentration["kind"] if concentration else None,
        "concentration_eval": concentration["phases"] if concentration else None,
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
        "skipped_db_count": len(scan_summary.skipped_db_files),
        "ftp_failed_count": len(scan_summary.ftp_failed_files),
    }


def build_workspace_payload(snapshot: AppStateSnapshot) -> dict[str, Any]:
    analysis = snapshot.analysis
    selected_root = snapshot.selected_root
    pending_root = snapshot.pending_root
    current_root = selected_root or pending_root
    display_root = (
        snapshot.selected_display_root
        or snapshot.pending_display_root
        or (str(current_root) if current_root else "")
    )
    return {
        "has_analysis": analysis is not None,
        "selected_root": str(selected_root) if selected_root else "",
        "display_root": display_root,
        "summary": build_summary_payload(analysis, snapshot.scan_summary),
        "error": snapshot.error,
        "warnings": build_scan_warnings(snapshot.scan_summary),
        "job_status": snapshot.workspace_job_payload,
        "tz_offset_min": local_tz_offset_min(),
    }


def build_scan_warnings(scan_summary: ScanSummary) -> list[str]:
    """Непрерывающие проблемы прошедшей синхронизации (битые базы, недокачанные
    файлы, недоступный FTP) — их видно в интерфейсе, а не только в логе."""
    warnings: list[str] = []
    if scan_summary.skipped_db_files:
        warnings.append(
            f"Пропущены повреждённые или неподходящие базы ({len(scan_summary.skipped_db_files)}): "
            f"{format_file_list(scan_summary.skipped_db_files)}."
        )
    if scan_summary.ftp_failed_files:
        warnings.append(
            f"Не удалось скачать файлы с FTP ({len(scan_summary.ftp_failed_files)}): "
            f"{format_file_list(scan_summary.ftp_failed_files)}."
        )
    if scan_summary.ftp_error:
        warnings.append(f"Синхронизация с FTP не удалась: {scan_summary.ftp_error}")
    return warnings


def page_context(request: Request, snapshot: AppStateSnapshot) -> dict[str, Any]:
    analysis = snapshot.analysis
    selected_root = snapshot.selected_root
    pending_root = snapshot.pending_root
    workspace_payload = build_workspace_payload(snapshot)
    workspace_input_value = resolve_workspace_input_value(selected_root, pending_root)

    # Подключённая панель («Подключиться» → зелёная строка + WebView/Графики/
    # Отключить). Состояние сессионное (state.connected_ftp_id), не привязано к
    # загрузке графиков; одновременно одна панель. ?view=menu показывает меню даже
    # при загруженной области («Главное меню» без разрыва соединения).
    ftp_sources = list_ftp_sources_public()
    force_menu = request.query_params.get("view") == "menu"
    connected_id = snapshot.connected_ftp_id
    if connected_id and not any(src["id"] == connected_id for src in ftp_sources):
        connected_id = ""  # панель удалили — сбрасываем пометку
    # На экране меню (даже при загруженной области) wash-JS не должен стартовать —
    # его DOM отсутствует. Гейт `if (!hasWorkspace) return` смотрит на hasWorkspace.
    wash_visible = analysis is not None and not force_menu
    def asset_version(filename: str) -> int:
        try:
            return int((STATIC_DIR / filename).stat().st_mtime)
        except OSError:
            return 0

    asset_versions = {
        "style_css": asset_version("style.css"),
        "wash_chart_js": asset_version("wash-chart.js"),
        "app_js": asset_version("app.js"),
        # Иконка тоже версионируется: favicon и титлбар кэшируются браузером и
        # WebView2 намертво, и после смены иконки показывалась бы прежняя.
        "icon_svg": asset_version("washjournal-icon.svg"),
    }
    return {
        "request": request,
        "page_title": "OptiCIP Dashboard",
        "has_analysis": analysis is not None,
        "selected_root": str(selected_root) if selected_root else "",
        "display_root": workspace_payload["display_root"],
        "project_root": str(PROJECT_ROOT),
        "workspace_input_value": workspace_input_value,
        # Подсказка в поле «Папка» = путь по умолчанию (настройка или datalog).
        "workspace_path_placeholder": resolve_default_folder_path(),
        "workspace_default_path": resolve_default_folder_path(),
        "ftp_form_defaults": dict(DEFAULT_FTP_FORM_VALUES),
        "ftp_sources": ftp_sources,
        "force_menu": force_menu,
        "connected_id": connected_id,
        "app_version": APP_VERSION,
        "summary": workspace_payload["summary"],
        "error": workspace_payload["error"],
        "asset_versions": asset_versions,
        "job_status": workspace_payload["job_status"],
        "app_state": {
            "appVersion": APP_VERSION,
            # hasWorkspace = показан ли wash-экран (в меню он false, даже если
            # рабочая область загружена) — по нему wash-JS решает, стартовать ли.
            "hasWorkspace": wash_visible,
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
    # exists/is_dir/resolve — stat/realpath syscalls, а save_last_folder_path пишет
    # файл на диск. Держать их под глобальным state_lock (на нём ждут SSE и все
    # запросы) незачем: проверку и запись делаем вне лока, под ним — только смена
    # состояния и запуск джоба.
    if not candidate.exists() or not candidate.is_dir():
        with state_lock:
            state.error = f"Папка не найдена: {candidate}"
            if state.analysis is None:
                state.pending_root = None
                state.pending_display_root = ""
        return RedirectResponse(url="/", status_code=303)

    resolved = candidate.resolve()
    save_last_folder_path(str(resolved))
    with state_lock:
        start_workspace_job(resolved, display_target=str(resolved))
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/open-ftp")
def open_ftp_workspace(
    source_id: str = Form(""),
    host: str = Form(""),
    port: str = Form("21"),
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


@app.post("/workspace/ftp-source/add")
def add_ftp_source(
    host: str = Form(""),
    port: str = Form("21"),
    password: str = Form(""),
    path: str = Form("/datalog"),
    passive: str = Form(""),
    label: str = Form(""),
    web_scheme: str = Form(""),
) -> RedirectResponse:
    """Сохраняет панель в реестр БЕЗ открытия рабочей области (кнопка «Добавить
    панель»). Панель появляется в списке сохранённых; подключение — отдельным
    шагом (веб-просмотр / графики)."""
    try:
        config = normalize_ftp_connection_settings(
            {
                "host": host,
                "port": port,
                "password": password,
                "path": path,
                "passive": passive,
                "web_scheme": web_scheme,
            }
        )
    except ValueError as exc:
        with state_lock:
            state.error = str(exc)
        return RedirectResponse(url="/", status_code=303)
    upsert_ftp_connection(config, label=label)
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/ftp-source/connect")
def connect_ftp_source(source_id: str = Form(...)) -> RedirectResponse:
    """Помечает панель как подключённую (зелёная строка + WebView/Графики/
    Отключить в меню). Графики НЕ загружаются здесь — только по кнопке «Графики».
    Одновременно активна одна панель. Возвращаемся в меню."""
    saved_id = source_id.strip()
    if saved_id and find_ftp_connection(saved_id) is not None:
        with state_lock:
            state.connected_ftp_id = saved_id
    return RedirectResponse(url="/?view=menu", status_code=303)


@app.post("/workspace/ftp-source/rename")
def rename_ftp_source(
    source_id: str = Form(...), label: str = Form("")
) -> RedirectResponse:
    """Переименовывает сохранённую панель (правка названия в списке)."""
    saved_id = source_id.strip()
    if saved_id:
        rename_ftp_connection(saved_id, label)
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
            # Снимаем пометку подключения, даже если графики не были загружены —
            # иначе connected_ftp_id залипает и повторно добавленная панель с тем
            # же id (host|port|user|path) покажется «подключённой».
            if state.connected_ftp_id == saved_id:
                state.connected_ftp_id = ""
        delete_ftp_connection(saved_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/reset")
def reset_workspace_route() -> RedirectResponse:
    with state_lock:
        reset_workspace()
    return RedirectResponse(url="/", status_code=303)


def start_refresh_job_locked() -> dict[str, Any] | None:
    """Перезапуск обработки текущего источника; вызывать под state_lock.
    Возвращает описание джоба либо None, если источник ещё не выбран."""
    target_root = state.selected_root or state.pending_root
    if target_root is None:
        return None

    display_target = (
        state.selected_display_root or state.pending_display_root or str(target_root.resolve())
    )
    start_workspace_job(target_root.resolve(), display_target=display_target)
    return serialize_job(state.workspace_job)


@app.post("/workspace/refresh")
def refresh_workspace_route() -> RedirectResponse:
    with state_lock:
        if start_refresh_job_locked() is None:
            state.error = "Сначала выберите источник данных."
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/workspace/refresh")
def refresh_workspace_api() -> JSONResponse:
    with state_lock:
        job = start_refresh_job_locked()
    if job is None:
        raise HTTPException(status_code=400, detail="Сначала выберите источник данных.")
    return JSONResponse({"ok": True, "job": job})


@app.get("/api/workspace-job")
def workspace_job_status() -> JSONResponse:
    with state_lock:
        return JSONResponse(serialize_job(state.workspace_job))


@app.get("/api/workspace-data")
def workspace_data() -> JSONResponse:
    # Под state_lock — только снимок ссылок на данные. Тяжёлая сборка строк и
    # чтение файла настроек идут снаружи: держать общий лок на время сборки
    # десятков тысяч словарей нельзя (на нём же ждут SSE и все другие запросы).
    with state_lock:
        snapshot = capture_state_snapshot()

    payload = build_workspace_payload(snapshot)
    payload["wash_rows"] = build_wash_rows_cached(snapshot.analysis, snapshot.analysis_revision)
    payload["object_rows"] = build_object_rows(snapshot.object_name_overrides, snapshot.analysis)
    return JSONResponse(payload)


def snapshot_job_status() -> dict[str, Any]:
    with state_lock:
        return serialize_job(state.workspace_job)


@app.get("/api/workspace-job/stream")
async def workspace_job_status_stream() -> StreamingResponse:
    # Асинхронный опрос состояния задачи: не занимает поток из ограниченного
    # пула (раньше блокирующий sync-генератор мог его исчерпать). Блокирующий
    # state_lock берём в отдельном потоке (asyncio.to_thread) — если взять его
    # прямо в генераторе, ожидание лока встаёт на event loop и подвешивает весь
    # сервер, пока другой запрос держит лок. При обрыве соединения генератор
    # корректно отменяется.
    poll_interval = 0.5
    keepalive_ticks = max(1, int(WORKSPACE_JOB_STREAM_KEEPALIVE_SECONDS / poll_interval))

    async def event_stream() -> Any:
        last_payload: str | None = None
        idle_ticks = 0
        while True:
            snapshot = await asyncio.to_thread(snapshot_job_status)
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


# Эндпоинты с синхронной записью на диск объявлены обычными `def`: Starlette
# выполняет их в пуле потоков, не блокируя событийный цикл.
@app.post("/api/object-name")
def update_object_name(payload: dict[str, Any] = Body(...)) -> JSONResponse:
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
            state.analysis_revision += 1
        resolved_name = resolve_object_name(channel, object_id, overrides)
        # Ответ собираем под тем же локом: снаружи состояние мог успеть сменить
        # другой запрос (или завершившийся джоб), и клиент получил бы список
        # объектов, не соответствующий только что сохранённому переименованию.
        object_rows = build_object_rows(state.object_name_overrides, state.analysis)

    return JSONResponse(
        {
            "ok": True,
            "mode": mode,
            "channel": channel,
            "object_id": object_id,
            "object_name": resolved_name,
            "has_json_name": (channel, object_id) in overrides,
            "is_custom_name": resolved_name != fallback_object_name(object_id),
            "object_rows": object_rows,
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
            state.analysis_revision += 1

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
def update_chart_styles(payload: dict[str, Any] = Body(...)) -> JSONResponse:
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
def update_app_settings_route(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    source = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    if not isinstance(source, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    try:
        settings = update_app_settings(source)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось сохранить настройки: {exc}"
        ) from exc

    return JSONResponse({"ok": True, "settings": settings})


def _local_ipv4_networks() -> tuple[str, list[ipaddress.IPv4Network]]:
    """Свой основной IPv4 и приватные подсети, по которым имеет смысл искать
    панель. Адрес выбираем UDP-«подключением» к внешнему адресу — пакет не
    отправляется, ОС лишь выбирает исходящий интерфейс. Публичные и loopback-
    адреса не сканируем (чтобы не «шуметь» вне доверенной локальной сети)."""
    local_ip = ""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        local_ip = probe.getsockname()[0]
    except OSError:
        local_ip = ""
    finally:
        probe.close()

    networks: list[ipaddress.IPv4Network] = []
    if local_ip:
        try:
            addr = ipaddress.ip_address(local_ip)
        except ValueError:
            addr = None
        if isinstance(addr, ipaddress.IPv4Address) and addr.is_private and not addr.is_loopback:
            # /24 вокруг основного адреса — типовая заводская подсеть.
            networks.append(ipaddress.ip_network(f"{local_ip}/24", strict=False))
    return local_ip, networks


async def _ftp_read_reply(reader: asyncio.StreamReader) -> tuple[str, str]:
    """Читает ответ FTP (возможно многострочный `NNN-...` до строки `NNN ...`).
    Возвращает (код, текст). Пустой код — соединение закрылось/таймаут."""
    line = await asyncio.wait_for(reader.readline(), timeout=FTP_DISCOVERY_BANNER_TIMEOUT)
    if not line:
        return "", ""
    text = line.decode("latin-1", "replace")
    code = text[:3]
    # Многострочный ответ: первая строка вида "220-...", конец — "220 ...".
    if len(text) >= 4 and text[3] == "-" and code.isdigit():
        while True:
            more = await asyncio.wait_for(
                reader.readline(), timeout=FTP_DISCOVERY_BANNER_TIMEOUT
            )
            if not more:
                break
            chunk = more.decode("latin-1", "replace")
            text += chunk
            if chunk[:3] == code and len(chunk) >= 4 and chunk[3] == " ":
                break
    return code, text.strip()


def _insecure_ssl_context() -> ssl.SSLContext:
    """TLS без проверки сертификата: у панелей самоподписанный серт. Опознание —
    только чтение публичной стартовой страницы, конфиденциальных данных нет."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _fetch_easyweb_title(host: str, port: int, use_tls: bool) -> str | None:
    """GET / на host:port (опц. TLS), поиск маркеров EasyWeb в теле. Возвращает
    `<title>` (обычно «cMT») либо None, если это не EasyWeb / порт недоступен."""
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, port, ssl=_insecure_ssl_context() if use_tls else None
            ),
            timeout=FTP_DISCOVERY_PROBE_TIMEOUT,
        )
        # HTTP/1.0 + identity: без gzip, иначе маркеры не найти в сжатом теле.
        request = (
            f"GET / HTTP/1.0\r\nHost: {host}\r\n"
            "User-Agent: OptiCIP-Dashboard\r\n"
            "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
        )
        writer.write(request.encode("latin-1", "replace"))
        await writer.drain()
        body = b""
        while len(body) < HTTP_EASYWEB_READ_LIMIT:
            chunk = await asyncio.wait_for(
                reader.read(4096), timeout=FTP_DISCOVERY_BANNER_TIMEOUT
            )
            if not chunk:
                break
            body += chunk
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        return None
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass

    text = body.decode("latin-1", "replace")
    if not any(marker in text.lower() for marker in HTTP_EASYWEB_MARKERS):
        return None
    # Первый <title> — это заголовок <head> (SVG-title в спрайте идут позже).
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


async def _probe_http_easyweb(host: str) -> tuple[str, str] | None:
    """Ищет веб-интерфейс EasyWeb на host по HTTP :80 и HTTPS :443 (панели с
    «[TLS]» отдают веб только по https). Порты пробуем ПАРАЛЛЕЛЬНО, чтобы для
    TLS-only панели не терять таймаут на закрытом :80. Возвращает (`<title>`,
    схема) — при обоих ответах предпочитаем :80/http; либо None — не панель."""
    results = await asyncio.gather(
        *(_fetch_easyweb_title(host, port, use_tls) for port, use_tls in HTTP_EASYWEB_PORTS)
    )
    for (port, use_tls), title in zip(HTTP_EASYWEB_PORTS, results):
        if title is not None:
            return title, ("https" if use_tls else "http")
    return None


async def _reverse_dns_name(host: str) -> str:
    """Имя хоста по обратному DNS/mDNS. Панель Weintek обычно отзывается сетевым
    именем `cMT-XXXX` (суффикс MAC). Возвращает первую метку имени без домена,
    либо "" если имя не разрешилось / совпало с самим IP."""
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(socket.gethostbyaddr, host), timeout=2.0
        )
    except (OSError, asyncio.TimeoutError):
        return ""
    hostname = (info[0] if info else "") or ""
    label = hostname.split(".")[0].strip()
    return "" if label == host else label


def _is_weintek_mac(mac: str) -> bool:
    """True, если MAC начинается с OUI Weintek (00:0C:26)."""
    normalized = (mac or "").replace("-", ":").lower()
    return any(normalized.startswith(prefix) for prefix in WEINTEK_MAC_PREFIXES)


def _weintek_name_from_mac(mac: str) -> str:
    """Имя панели по умолчанию = `cMT-` + два последних октета MAC (заглавными).
    Напр. 00:0c:26:11:3c:6f → «cMT-3C6F». Пусто, если MAC не из 6 октетов."""
    octets = (mac or "").replace("-", ":").split(":")
    if len(octets) != 6 or not all(len(o) == 2 for o in octets):
        return ""
    return "cMT-" + (octets[4] + octets[5]).upper()


def _read_arp_table() -> dict[str, str]:
    """IP→MAC из ARP-таблицы ОС (её наполняют TCP-пробы скана). MAC — в нижнем
    регистре через двоеточие. Пусто, если таблицу не удалось прочитать."""
    if os.name == "nt":
        commands = (["arp", "-a"],)
    else:
        # ip neigh — основной; arp -a/-n — фолбэк (net-tools).
        commands = (["ip", "neigh", "show"], ["arp", "-a"], ["arp", "-n"])
    # На Windows подавляем мелькание консольного окна в GUI-сборке (pywebview).
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    output = ""
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.stdout:
            output = proc.stdout
            break
    if not output:
        return {}
    ip_re = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
    mac_re = re.compile(r"\b([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})\b")
    table: dict[str, str] = {}
    for line in output.splitlines():
        ip_match = ip_re.search(line)
        mac_match = mac_re.search(line)
        if ip_match and mac_match:
            table[ip_match.group(1)] = mac_match.group(1).replace("-", ":").lower()
    return table


async def _probe_ftp_host(host: str, semaphore: asyncio.Semaphore) -> dict[str, Any] | None:
    """Пробует host:21 (FTP нужен для выгрузки datalog) и читает приветствие.
    Опознаёт панель по её веб-интерфейсу EasyWeb на host:80 — это работает без
    FTP-пароля и при обязательном TLS. Баннер FTP — лишь мягкий запасной признак.
    Имя панели берём из обратного DNS (`cMT-XXXX`), иначе из `<title>` EasyWeb.
    Возвращает описание хоста либо None, если порт 21 закрыт/недоступен."""
    async with semaphore:
        writer: asyncio.StreamWriter | None = None
        banner = ""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, FTP_DEFAULT_PORT),
                timeout=FTP_DISCOVERY_PROBE_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError):
            return None
        try:
            _banner_code, banner = await _ftp_read_reply(reader)
        except (OSError, asyncio.TimeoutError):
            banner = ""
        finally:
            try:
                writer.write(b"QUIT\r\n")
                await writer.drain()
            except OSError:
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

        # Опознание по веб-морде — основной признак (пароль не нужен).
        easyweb_result = await _probe_http_easyweb(host)
        easyweb = easyweb_result is not None
        name = ""
        web_scheme = ""  # http/https EasyWeb — для веб-просмотра /app/dashboard
        if easyweb:
            easyweb_title, web_scheme = easyweb_result
            # Имя: обратный DNS (cMT-XXXX) приоритетнее дженерик-title «cMT».
            name = (await _reverse_dns_name(host)) or (easyweb_title or "")

    lowered = banner.lower()
    banner_hint = any(hint in lowered for hint in FTP_WEINTEK_HINTS)
    return {
        "host": host,
        "port": FTP_DEFAULT_PORT,
        "banner": banner,
        "name": name,
        "web_scheme": web_scheme,
        "confirmed_weintek": easyweb,  # подтверждено веб-интерфейсом EasyWeb
        "likely_weintek": easyweb or banner_hint,
    }


async def discover_ftp_panels() -> dict[str, Any]:
    """Сканирует локальную приватную подсеть по порту 21 и возвращает найденные
    FTP-хосты (Weintek-подобные — первыми). Действие ручное и локальное."""
    own_ip, networks = await asyncio.to_thread(_local_ipv4_networks)
    hosts: list[str] = []
    for network in networks:
        if network.num_addresses - 2 > FTP_DISCOVERY_MAX_HOSTS:
            # Слишком широкая подсеть — не рассылаем тысячи проб.
            continue
        for ip in network.hosts():
            host = str(ip)
            if host != own_ip:
                hosts.append(host)
    if not hosts:
        return {"scanned": 0, "network": "", "panels": []}

    semaphore = asyncio.Semaphore(FTP_DISCOVERY_CONCURRENCY)
    probed = await asyncio.gather(*(_probe_ftp_host(host, semaphore) for host in hosts))
    responded = [item for item in probed if item is not None]
    ftp_hosts = len(responded)  # откликнулось на порт 21

    # MAC из ARP (пробы скана уже наполнили таблицу) — самый надёжный признак
    # Weintek (OUI 00:0C:26): без пароля, без web, при любом TLS.
    arp = await asyncio.to_thread(_read_arp_table)
    seen = set()
    for item in responded:
        mac = arp.get(item["host"], "")
        item["mac"] = mac
        item["mac_weintek"] = _is_weintek_mac(mac)
        if item["mac_weintek"]:
            item["confirmed_weintek"] = True
            item["likely_weintek"] = True
            # Имя = cMT-<последние 2 октета MAC> (надёжно). Кастомное имя из
            # EasyWeb/DNS сохраняем, дженерик-«cMT»/пустое заменяем на MAC-имя.
            current = item.get("name") or ""
            if not current or current.lower() == "cmt":
                item["name"] = _weintek_name_from_mac(mac) or current
        seen.add(item["host"])

    # Панели, опознанные по MAC, но не ответившие на :21 (FTP выкл/медленный):
    # добавляем, чтобы не терять — подключение потом само попробует FTP.
    host_set = set(hosts)
    mac_only = [
        ip
        for ip, mac in arp.items()
        if ip in host_set and ip not in seen and ip != own_ip and _is_weintek_mac(mac)
    ]
    for ip in mac_only:
        mac = arp.get(ip, "")
        responded.append(
            {
                "host": ip,
                "port": FTP_DEFAULT_PORT,
                "banner": "",
                # Имя из MAC (cMT-XXXX) для 6-октетного Weintek-MAC всегда есть,
                # reverse-DNS тут не нужен (и не блокирует по хосту в цикле).
                "name": _weintek_name_from_mac(mac),
                "web_scheme": "",  # web не зондировали (на :21 не ответил)
                "mac": mac,
                "mac_weintek": True,
                "confirmed_weintek": True,
                "likely_weintek": True,
            }
        )

    # В список отдаём ТОЛЬКО опознанные панели Weintek (MAC / EasyWeb / баннер).
    # Прочие FTP-хосты скрываем, но их число возвращаем для пояснения в UI.
    panels = [item for item in responded if item.get("likely_weintek")]
    panels.sort(
        key=lambda item: (
            not item.get("confirmed_weintek"),  # подтверждённые — первыми
            tuple(int(part) for part in item["host"].split(".")),
        )
    )
    return {
        "scanned": len(hosts),
        "ftp_hosts": ftp_hosts,
        "network": str(networks[0]) if networks else "",
        "panels": panels,
    }


@app.post("/api/ftp/discover")
async def api_ftp_discover() -> JSONResponse:
    """Ищет панели (FTP-хосты) в локальной подсети. Только по нажатию кнопки —
    guard middleware уже ограничивает эндпоинт локальными запросами."""
    try:
        result = await discover_ftp_panels()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось выполнить поиск: {exc}"
        ) from exc
    return JSONResponse(result)


@app.post("/api/chart-cache/clear")
def clear_chart_cache_route() -> JSONResponse:
    try:
        removed = clear_all_chart_caches()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось очистить кэш графиков: {exc}"
        ) from exc
    return JSONResponse({"ok": True, "removed": removed})


@app.get("/api/diagnostics")
def get_diagnostics() -> JSONResponse:
    with state_lock:
        analysis = state.analysis
        selected_root = state.selected_root or state.pending_root
        display_root = (
            state.selected_display_root
            or state.pending_display_root
            or (str(selected_root) if selected_root else "")
        )
        scan = state.scan_summary
        last_sync = state.last_sync_ts
        last_cleanup = state.last_cleanup_ts
        error = state.error
        job_payload = serialize_job(state.workspace_job)
        summary = build_summary_payload(analysis, scan)

    source_kind = "none"
    if selected_root is not None:
        source_kind = "ftp" if is_ftp_profile(selected_root) else "folder"

    settings = load_app_settings()
    return JSONResponse(
        {
            "source_kind": source_kind,
            "display_root": display_root,
            "last_sync": core.format_ts(last_sync) if last_sync else "",
            "counts": {
                "cycles": summary["cycle_count"],
                "objects": summary["object_count"],
                "databases": summary["db_count"],
                "archives": scan.archive_count,
                "ftp_sources": scan.ftp_source_count,
            },
            "auto_refresh": {
                "enabled": settings["ftp_auto_refresh_enabled"],
                "minutes": settings["ftp_auto_refresh_minutes"],
            },
            "datalog": {
                "size_bytes": datalog_size_bytes_cached(),
                "last_cleanup": core.format_ts(last_cleanup) if last_cleanup else "",
                "retention_enabled": settings["archive_retention_enabled"],
                "retention_days": settings["archive_retention_days"],
            },
            "job": {
                "active": job_payload["active"],
                "status": job_payload["status"],
                "message": job_payload["message"],
            },
            "error": error or "",
        }
    )


@app.post("/api/archives/cleanup")
def cleanup_archives_now() -> JSONResponse:
    settings = load_app_settings()
    days = settings["archive_retention_days"]
    with state_lock:
        target_root = state.selected_root or state.pending_root
        job = state.workspace_job
        job_active = job is not None and job.status in {"running", "cancelling"}

    # Удалять архивы под работающим анализом нельзя: файл исчезает прямо во время
    # чтения и джоб падает. Очистка и так выполняется в конце каждой синхронизации.
    if job_active:
        raise HTTPException(
            status_code=409,
            detail="Идёт обработка источника. Дождитесь её завершения и повторите очистку.",
        )

    if target_root is None:
        raise HTTPException(status_code=400, detail="Нет активного источника данных.")
    if not is_ftp_profile(target_root):
        raise HTTPException(
            status_code=400, detail="Очистка доступна только для FTP-источника (папка datalog)."
        )

    result = cleanup_old_archives(target_root, days)
    if result["removed"]:
        with state_lock:
            state.last_cleanup_ts = time.time()
    return JSONResponse({"ok": True, "days": days, **result})


# Ведущий X[.Y[.Z[.W]]] с необязательным префиксом v. Суффиксы (-rc.2, «(hotfix)»)
# в сравнение версий не идут — см. _parse_version.
_VERSION_RE = re.compile(r"\s*v?(\d+(?:\.\d+){0,3})", re.IGNORECASE)
# Строгая форма для тега, который подставляется в ИМЯ ФАЙЛА на диске: только цифры
# и точки, без пробелов, слэшей и суффиксов.
_SAFE_VERSION_RE = re.compile(r"\d+(?:\.\d+){0,3}")


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
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _release_tag(payload: dict[str, Any]) -> str:
    tag = str(payload.get("tag_name") or "").strip()
    return tag[1:] if tag[:1].lower() == "v" else tag


def _fetch_latest_release_tag() -> str:
    """Тег последнего релиза на GitHub (без префикса v) или '' при недоступности/
    отсутствии релизов."""
    return _release_tag(_fetch_latest_release())


def _pick_installer_asset(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Вложение-установщик из payload релиза: URL, размер и sha256 от GitHub.

    Ссылку берём ТОЛЬКО отсюда и никогда от клиента — иначе приложение
    скачивало бы и запускало произвольный URL с правами администратора.
    Без digest вложение не годится: проверить нечем, а запускать непроверенный
    .exe нельзя.
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
            job_id, status="ready", phase="ready", path=str(target), finished_at=time.time()
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


@app.post("/api/update/download")
def update_download() -> JSONResponse:
    # Слот задачи резервируем ПОД ТЕМ ЖЕ локом, что и проверку «уже качается?».
    # Раньше между ними отпускался лок на _fetch_latest_release() (секунды сети),
    # и два POST (двойной клик) проходили проверку оба: стартовали два воркера на
    # один и тот же .part, каждый удалял времянку другого, и пользователь получал
    # «Не удалось скачать обновление» на ровном месте.
    with state_lock:
        active = state.update_job
        if active is not None and active.status == "running":
            return JSONResponse({"ok": True, "job": _serialize_update_job(active)})
        # Готовый к установке результат — единственное, что имеет смысл вернуть
        # вместо перекачки; годность проверяем ниже, когда узнаем свежий тег.
        previous = active if active is not None and active.status == "ready" else None
        job = UpdateJob(id=uuid.uuid4().hex)
        state.update_job = job

    def release_slot() -> None:
        """Снять резерв, если его никто не вытеснил. Без этого любой выход по
        ошибке оставил бы задачу в running навсегда: проверка выше запретила бы
        повтор до перезапуска приложения."""
        with state_lock:
            if state.update_job is job:
                state.update_job = previous

    try:
        payload = _fetch_latest_release()
        latest = _release_tag(payload)
        if not latest:
            raise HTTPException(status_code=502, detail="Не удалось получить сведения о релизе.")
        if not _is_newer_version(latest, APP_VERSION):
            raise HTTPException(status_code=400, detail="Установлена последняя версия.")
        # Тег попадёт в имя файла на диске, поэтому проверяем его форму так же
        # строго, как URL вложения: «1.2 (hotfix)» или тег со слэшем иначе уронит
        # open() под невнятным FileNotFoundError вместо честной ошибки.
        if not _SAFE_VERSION_RE.fullmatch(latest):
            raise HTTPException(
                status_code=502, detail=f"Непригодный номер версии в релизе: {latest}"
            )
        asset = _pick_installer_asset(payload)
        if asset is None:
            raise HTTPException(
                status_code=502, detail="В релизе нет установщика с контрольной суммой."
            )
    except Exception:
        release_slot()
        raise

    # Тот же релиз уже скачан и проверен (например, после промаха по UAC) —
    # 22 МБ по сети ради имеющегося файла ни к чему.
    if previous is not None and previous.version == latest and previous.path:
        if Path(previous.path).is_file():
            with state_lock:
                if state.update_job is job:
                    state.update_job = previous
            return JSONResponse({"ok": True, "job": _serialize_update_job(previous)})

    with state_lock:
        if state.update_job is not job:
            # Задачу вытеснила другая — не мешаем ей.
            return JSONResponse({"ok": True, "job": _serialize_update_job(state.update_job)})
        job.version = latest
        job.total = asset["size"]
    logging.info(
        "Начинаю скачивание обновления %s → %s (%s Б) с %s",
        APP_VERSION,
        latest,
        asset["size"],
        asset["url"],
    )
    threading.Thread(
        target=download_update_worker,
        args=(job.id, asset, latest),
        name="update-download",
        daemon=True,
    ).start()
    return JSONResponse({"ok": True, "job": _serialize_update_job(job)})


@app.get("/api/update/job")
def update_job_status() -> JSONResponse:
    with state_lock:
        return JSONResponse(_serialize_update_job(state.update_job))


@app.get("/api/update-check")
def update_check() -> JSONResponse:
    """Разовая сверка версии с последним релизом — вызывается кнопкой в
    настройках. Автоматически при старте не дёргается: за спиной у пользователя
    в сеть не ходим."""
    payload = _fetch_latest_release()
    latest = _release_tag(payload)
    available = bool(latest) and _is_newer_version(latest, APP_VERSION)
    # Установить «в один клик» можно только собранную Windows-версию: ставит
    # .exe-установщик, а закрыть окно и перезапуститься умеет лишь десктоп-мост.
    installable = available and os.name == "nt" and _pick_installer_asset(payload) is not None
    return JSONResponse(
        {
            "current": APP_VERSION,
            # Пустой latest — «не выяснили» (нет сети/релизов), а не «актуально».
            "latest": latest,
            "update_available": available,
            "url": UPDATE_RELEASES_URL if available else "",
            "installable": installable,
        }
    )


@app.get("/api/wash-details")
def wash_details(key: str) -> JSONResponse:
    # Снимок анализа берём под локом, тяжёлую сборку (чтение настроек с диска,
    # оценка концентрации) делаем снаружи — на state_lock ждут SSE и все запросы.
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
