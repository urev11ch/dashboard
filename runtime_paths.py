from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_DIRNAME = "OptiCIP Dashboard"
LINUX_RUNTIME_DIRNAME = "opticip-dashboard"
RUNTIME_ROOT_ENV_VAR = "OPTICIP_RUNTIME_ROOT"


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

    return Path(tempfile.gettempdir()) / LINUX_RUNTIME_DIRNAME


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


def resolve_cache_root(dirname: str) -> Path:
    if sys.platform == "win32":
        return resolve_runtime_root() / "cache" / dirname
    return Path(tempfile.gettempdir()) / dirname


def resolve_log_root() -> Path:
    if sys.platform == "win32":
        return resolve_runtime_root() / "logs"
    return Path(tempfile.gettempdir())


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
