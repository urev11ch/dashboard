"""Сохранённые папки с архивами — источники наравне с FTP-панелями.

Раньше папка была одним полем пути на главной (плюс запомненный последний путь).
Теперь это список: у каждой папки своё название и тип оборудования (CIP/ПОУ).
Удаление папки из списка трогает только реестр — файлы архивов не удаляются.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

from webapp import config as app_config
from webapp.equipment import EQUIPMENT_CIP, classify_db_file, normalize_equipment
from webapp.io_utils import atomic_write_json, read_json_object
from webapp.settings_store import load_last_folder_path

FOLDER_SOURCES_FILENAME = "wash_folder_sources.json"
FOLDER_SOURCES_VERSION = 1
# Сколько баз смотреть, угадывая тип новой папки: на большом архиве обходить всё
# дерево ради подсказки незачем.
GUESS_SAMPLE_LIMIT = 40

folder_sources_lock = threading.Lock()


def folder_sources_path() -> Path:
    return app_config.TEMP_ROOT / FOLDER_SOURCES_FILENAME


def folder_source_id(path: str | Path) -> str:
    """Стабильный id папки: регистр пути на Windows не различается."""
    normalized = os.path.normcase(str(Path(path).expanduser().resolve()))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _load_registry() -> dict[str, Any]:
    payload = read_json_object(folder_sources_path())
    folders = payload.get("folders")
    if not isinstance(folders, list):
        folders = []
    cleaned = [f for f in folders if isinstance(f, dict) and f.get("id") and f.get("path")]
    return {"version": FOLDER_SOURCES_VERSION, "folders": cleaned, "migrated": bool(payload.get("migrated"))}


def _save_registry(registry: dict[str, Any]) -> None:
    target = folder_sources_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, registry)


def _migrate_last_folder_locked(registry: dict[str, Any]) -> bool:
    """Однократно переносит «последний открытый путь» в список папок, чтобы после
    обновления у пользователя не пропала его папка. True — реестр изменён."""
    if registry["migrated"]:
        return False
    registry["migrated"] = True
    last_path = load_last_folder_path()
    if last_path and Path(last_path).expanduser().is_dir():
        entry = _make_entry(last_path, label="", equipment=None)
        if not any(f["id"] == entry["id"] for f in registry["folders"]):
            registry["folders"].append(entry)
    return True


def _make_entry(path: str | Path, label: str, equipment: str | None) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    return {
        "id": folder_source_id(resolved),
        "label": (label or "").strip() or resolved.name or str(resolved),
        "path": str(resolved),
        "equipment": normalize_equipment(equipment) if equipment else EQUIPMENT_CIP,
    }


def list_folder_sources() -> list[dict[str, Any]]:
    with folder_sources_lock:
        registry = _load_registry()
        if _migrate_last_folder_locked(registry):
            _save_registry(registry)
    rows = []
    for folder in registry["folders"]:
        rows.append(
            {
                "id": str(folder["id"]),
                "label": str(folder.get("label") or folder["path"]),
                "path": str(folder["path"]),
                "equipment": normalize_equipment(folder.get("equipment")),
                "exists": Path(folder["path"]).is_dir(),
            }
        )
    rows.sort(key=lambda row: row["label"].lower())
    return rows


def find_folder_source(source_id: str) -> dict[str, Any] | None:
    for row in list_folder_sources():
        if row["id"] == source_id:
            return row
    return None


def add_folder_source(
    path: str | Path,
    label: str = "",
    equipment: str | None = None,
    fallback_equipment: str | None = None,
) -> dict[str, Any]:
    """Добавляет папку (или обновляет запись той же папки). equipment=None —
    прежний тип записи, для новой — угаданный по содержимому, иначе
    fallback_equipment (режим, в котором её добавляют), иначе CIP."""
    entry = _make_entry(path, label, equipment)
    # Обход папки — до блокировки реестра: на большом архиве он заметно дольше.
    guessed = guess_folder_equipment(entry["path"]) if equipment is None else None
    with folder_sources_lock:
        registry = _load_registry()
        _migrate_last_folder_locked(registry)
        previous = next((f for f in registry["folders"] if f["id"] == entry["id"]), None)
        if equipment is None:
            if previous is not None:
                entry["equipment"] = normalize_equipment(previous.get("equipment"))
            else:
                entry["equipment"] = guessed or normalize_equipment(fallback_equipment)
        if previous is not None and not (label or "").strip():
            entry["label"] = previous.get("label") or entry["label"]
        registry["folders"] = [f for f in registry["folders"] if f["id"] != entry["id"]]
        registry["folders"].append(entry)
        _save_registry(registry)
    return entry


def _update_folder(source_id: str, **changes: Any) -> bool:
    with folder_sources_lock:
        registry = _load_registry()
        _migrate_last_folder_locked(registry)
        for folder in registry["folders"]:
            if folder["id"] == source_id:
                folder.update(changes)
                _save_registry(registry)
                return True
    return False


def rename_folder_source(source_id: str, label: str) -> bool:
    label = (label or "").strip()
    if not label:
        found = find_folder_source(source_id)
        label = Path(found["path"]).name if found else ""
    return _update_folder(source_id, label=label)


def set_folder_equipment(source_id: str, equipment: str) -> bool:
    return _update_folder(source_id, equipment=normalize_equipment(equipment))


def delete_folder_source(source_id: str) -> bool:
    """Убирает папку из списка. Файлы на диске НЕ трогаются."""
    with folder_sources_lock:
        registry = _load_registry()
        _migrate_last_folder_locked(registry)
        before = len(registry["folders"])
        registry["folders"] = [f for f in registry["folders"] if f["id"] != source_id]
        changed = len(registry["folders"]) != before
        _save_registry(registry)
    return changed


def guess_folder_equipment(path: str | Path) -> str | None:
    """Тип папки по первым найденным базам: какого типа больше. None — баз нет
    или ни одна не распознана (архивы .zip здесь не распаковываем)."""
    counts = {"cip": 0, "pou": 0}
    seen = 0
    for current_root, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for filename in sorted(filenames):
            if not filename.lower().endswith(".db"):
                continue
            kind = classify_db_file(Path(current_root) / filename)
            if kind in counts:
                counts[kind] += 1
            seen += 1
            if seen >= GUESS_SAMPLE_LIMIT:
                break
        if seen >= GUESS_SAMPLE_LIMIT:
            break
    if not counts["cip"] and not counts["pou"]:
        return None
    return "pou" if counts["pou"] > counts["cip"] else "cip"
