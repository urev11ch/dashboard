from __future__ import annotations

import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

APP_DIRNAME = "OptiCIP Dashboard"
LINUX_RUNTIME_DIRNAME = "opticip-dashboard"
RUNTIME_ROOT_ENV_VAR = "OPTICIP_RUNTIME_ROOT"

logger = logging.getLogger(__name__)


def _resolve_windows_runtime_root() -> Path:
    for env_var in ("LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(env_var)
        if value:
            return Path(value) / APP_DIRNAME
    return Path.home() / "AppData" / "Local" / APP_DIRNAME


def resolve_runtime_root() -> Path:
    override = str(os.environ.get(RUNTIME_ROOT_ENV_VAR) or "").strip()
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        return _resolve_windows_runtime_root()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRNAME

    xdg_state_home = str(os.environ.get("XDG_STATE_HOME") or "").strip()
    if xdg_state_home:
        return Path(xdg_state_home) / LINUX_RUNTIME_DIRNAME

    xdg_cache_home = str(os.environ.get("XDG_CACHE_HOME") or "").strip()
    if xdg_cache_home:
        return Path(xdg_cache_home) / LINUX_RUNTIME_DIRNAME

    # В общий /tmp не уходим: каталог с предсказуемым именем на многопользо-
    # вательской машине может подменить кто угодно, а приложение пишет туда
    # pickle и потом его же читает.
    return Path.home() / ".local" / "state" / LINUX_RUNTIME_DIRNAME


def _resolve_windows_default_workspace_candidates() -> list[Path]:
    candidates: list[Path] = [
        Path(r"C:\Program Files\EBpro\HMI_memory\datalog"),
    ]

    for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
        base_dir = str(os.environ.get(env_var) or "").strip()
        if not base_dir:
            continue
        candidates.append(Path(base_dir) / "EBpro" / "HMI_memory" / "datalog")

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return unique_candidates


def _resolve_posix_cache_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_DIRNAME

    xdg_cache_home = str(os.environ.get("XDG_CACHE_HOME") or "").strip()
    if xdg_cache_home:
        return Path(xdg_cache_home) / LINUX_RUNTIME_DIRNAME
    return Path.home() / ".cache" / LINUX_RUNTIME_DIRNAME


def ensure_private_directory(path: Path) -> Path:
    """Создаёт каталог только для текущего пользователя (0700) и проверяет, что
    это не подсунутый симлинк и что владелец — мы.

    В кэш приложение пишет pickle и потом его же читает: чужой каталог с
    предсказуемым именем (старое поведение — /tmp/<имя>) означал бы локальный
    RCE на многопользовательской машине."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)

    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise PermissionError(f"Каталог кэша {path} — символическая ссылка.")
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(f"Путь кэша {path} не является каталогом.")

    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and info.st_uid != geteuid():
        raise PermissionError(f"Каталог кэша {path} принадлежит другому пользователю.")

    # mkdir не меняет права уже существующего каталога, а umask может срезать
    # даже у нового: доводим до 0700 явно.
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def resolve_cache_root(dirname: str) -> Path:
    if sys.platform == "win32":
        return resolve_runtime_root() / "cache" / dirname

    candidate = _resolve_posix_cache_root() / dirname
    try:
        return ensure_private_directory(candidate)
    except OSError as error:
        # Каталог занят чужим владельцем или симлинком — не пишем туда и не
        # падаем: уходим в приватный временный каталог со случайным именем.
        logger.warning(
            "Каталог кэша %s недоступен (%s); использую приватный временный каталог.",
            candidate,
            error,
        )
        return Path(tempfile.mkdtemp(prefix=f"{LINUX_RUNTIME_DIRNAME}-{dirname}-"))


def _resolve_posix_state_root() -> Path:
    """Writable-каталог состояния приложения на не-Windows (логи, профиль
    WebView и т. п.) — вместо общего /tmp с предсказуемыми именами."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRNAME

    xdg_state_home = str(os.environ.get("XDG_STATE_HOME") or "").strip()
    if xdg_state_home:
        return Path(xdg_state_home) / LINUX_RUNTIME_DIRNAME
    return Path.home() / ".local" / "state" / LINUX_RUNTIME_DIRNAME


def resolve_log_root() -> Path:
    if sys.platform == "win32":
        return resolve_runtime_root() / "logs"
    return _resolve_posix_state_root() / "logs"


def resolve_default_workspace_root() -> Path:
    home = Path.home()

    if sys.platform == "win32":
        for candidate in _resolve_windows_default_workspace_candidates():
            if candidate.exists():
                return candidate

        for dirname in ("Documents", "Desktop"):
            candidate = home / dirname
            if candidate.exists():
                return candidate

    return home
