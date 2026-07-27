"""Константы и разрешение путей приложения OptiCIP Dashboard.

Выделено из webapp/app.py: здесь только то, что не зависит от состояния и
FastAPI-приложения — константы, лимиты кэшей, параметры обнаружения панелей и
разрешение корней данных (datalog/temp, кэши). Модуль — «лист» графа импортов:
его импортируют все остальные, он не импортирует ничего из webapp.app.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from runtime_paths import resolve_cache_root, resolve_runtime_root
import wash_report as core


def resolve_project_root() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root)
    return Path(__file__).resolve().parent.parent


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
# Суммарный потолок распаковки одного архива: страховка от zip/tar-бомбы,
# которая иначе забила бы диск за одну распаковку (LRU-чистка кэша срабатывает
# до/после задачи, но не во время). Легальные .db панели — десятки МБ, так что
# запас огромный.
ARCHIVE_EXTRACT_MAX_BYTES = 4 * 1024**3
ANALYSIS_CACHE_MAX_BYTES = 1024**3
ANALYSIS_CACHE_MAX_ENTRIES = 2048
# Сколько источников помним для удаления предыдущей версии их кэша.
CACHE_SOURCE_REGISTRY_LIMIT = 512
DB_ANALYSIS_CACHE_VERSION = 3
# Бампать при любом изменении формата workspace-пикла. История:
# v5 — сэмплы вынесены из пикла в side-файлы по потокам (ws-samples-*), в RAM
# подтягиваются лениво (см. make_sample_loader); v6 — +Segment.last_sample_ts
# (порог разрыва цикла меряется по сырому времени последнего сэмпла).
WORKSPACE_ANALYSIS_CACHE_VERSION = 6
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
# Потолок на тело JSON релиза (releases/latest). Реальный ответ — единицы КБ;
# 4 МБ с запасом на много вложений, но отсекает раздувание памяти враждебным/
# битым ответом (бинарь капается отдельно через UPDATE_MAX_BYTES).
RELEASE_JSON_MAX_BYTES = 4 * 1024 * 1024
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
