"""Тип оборудования источника и архива: CIP (мойка) или ПОУ (пастеризационно-
охладительная установка).

Тип архива определяется по подписям тегов в таблице `data_format`, а не по имени
файла: имена логов на панелях произвольные («Canal_1», «CIP», «Pasteur»…).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import wash_report as core

EQUIPMENT_CIP = "cip"
EQUIPMENT_POU = "pou"
EQUIPMENT_TYPES = (EQUIPMENT_CIP, EQUIPMENT_POU)
EQUIPMENT_LABELS = {EQUIPMENT_CIP: "CIP", EQUIPMENT_POU: "ПОУ"}

# Признаки ПОУ по корням слов подписей. Проверяются ДО признаков мойки: у ПОУ
# тоже есть тег «Процесс», и он совпал бы с полем мойки process.
POU_KEYWORD_GROUPS: tuple[tuple[str, ...], ...] = (
    ("пастериз",),
    ("клапан", "возврат"),
    ("pasteur",),
    ("divert",),
)


def normalize_equipment(value: object) -> str:
    """Тип оборудования из настроек/формы; всё незнакомое — CIP (так работали все
    источники до появления ПОУ)."""
    text = str(value or "").strip().lower()
    return text if text in EQUIPMENT_TYPES else EQUIPMENT_CIP


def equipment_label(value: object) -> str:
    return EQUIPMENT_LABELS[normalize_equipment(value)]


def classify_comments(comments: dict[int, str]) -> str | None:
    """Тип по подписям тегов; None — подписей нет или они не распознаны."""
    texts = [core.normalize_column_comment(comment) for comment in comments.values()]
    texts = [text for text in texts if text]
    if not texts:
        return None
    for text in texts:
        if any(all(keyword in text for keyword in group) for group in POU_KEYWORD_GROUPS):
            return EQUIPMENT_POU
    matched = {core.match_sample_field(text) for text in texts}
    if set(core.REQUIRED_SAMPLE_FIELDS).issubset(matched):
        return EQUIPMENT_CIP
    return None


# Классификация читает только маленькую таблицу data_format, но источник может
# содержать сотни баз, а задача обработки перезапускается при каждом обновлении.
# Кэш по (путь, размер, mtime): изменённый файл переклассифицируется.
_classify_cache: dict[tuple[str, int, int], str | None] = {}
_classify_cache_lock = threading.Lock()
_CLASSIFY_CACHE_LIMIT = 20000


def classify_db_file(db_path: Path) -> str | None:
    """CIP / ПОУ / None (нет подписей, не распознано или файл не читается)."""
    try:
        stat = os.stat(db_path)
    except OSError:
        return None
    key = (str(db_path), stat.st_size, stat.st_mtime_ns)
    with _classify_cache_lock:
        if key in _classify_cache:
            return _classify_cache[key]

    result: str | None
    try:
        connection = core.connect_read_only(db_path)
        try:
            result = classify_comments(core.read_data_format_comments(connection))
        finally:
            connection.close()
    except sqlite3.Error:
        result = None

    with _classify_cache_lock:
        if len(_classify_cache) >= _CLASSIFY_CACHE_LIMIT:
            _classify_cache.clear()
        _classify_cache[key] = result
    return result


def split_db_files_by_equipment(
    db_files: list[Path], equipment: str
) -> tuple[list[Path], list[Path], list[Path], str]:
    """Делит базы источника для режима `equipment`.

    Возвращает (свои, чужие, нераспознанные-отброшенные, тип чужих). В режиме CIP «свои» — всё, что не
    опознано как ПОУ: базы без подписей идут в разбор мойки как раньше
    (позиционная раскладка), неподходящие он сам пропустит. В режиме ПОУ «свои»
    — только опознанные ПОУ; опознанные CIP — чужие, нераспознанные отбрасываются
    (их показываем как пропущенные, а не как чужие)."""
    equipment = normalize_equipment(equipment)
    other = EQUIPMENT_POU if equipment == EQUIPMENT_CIP else EQUIPMENT_CIP
    own: list[Path] = []
    foreign: list[Path] = []
    dropped: list[Path] = []
    for db_path in db_files:
        kind = classify_db_file(db_path)
        if kind == other:
            foreign.append(db_path)
        elif equipment == EQUIPMENT_CIP or kind == EQUIPMENT_POU:
            own.append(db_path)
        else:
            dropped.append(db_path)
    return own, foreign, dropped, other
