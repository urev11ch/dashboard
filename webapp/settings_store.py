"""Настройки приложения и их применение к результатам моек.

Содержит четыре группы, вынесенные из webapp/app.py:
  • имена объектов (overrides в JSON рядом с данными);
  • стили кривых графика (цвет + тип линии), общие для всех источников;
  • последний открытый путь к папке (режим «Папка и архивы»);
  • общие настройки приложения (автообновление FTP, нормативы концентрации,
    подписи результата) + применение этих настроек к вердикту мойки.

TEMP_ROOT берём динамически как app_config.TEMP_ROOT — тесты подменяют каталог,
патча app.config.TEMP_ROOT. Функции load/save реэкспортируются в app.py, поэтому
роуты и тесты обращаются к ним как app.<имя>.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import wash_report as core
from webapp import config as app_config
from webapp.config import (
    APP_SETTINGS_FILENAME,
    APP_SETTINGS_VERSION,
    ARCHIVE_RETENTION_MAX_DAYS,
    ARCHIVE_RETENTION_MIN_DAYS,
    CHART_COLOR_RE,
    CHART_LINE_STYLE_IDS,
    CHART_STYLE_SETTINGS_FILENAME,
    CHART_STYLE_SETTINGS_VERSION,
    CONCENTRATION_MAX,
    CONCENTRATION_MIN,
    CONCENTRATION_PHASE_KEYS,
    CONCENTRATION_TOLERANCE_MAX,
    CONCENTRATION_TOLERANCE_MIN,
    DEFAULT_APP_SETTINGS,
    FOLDER_SOURCE_SETTINGS_FILENAME,
    FOLDER_SOURCE_SETTINGS_VERSION,
    FTP_AUTO_REFRESH_MAX_MINUTES,
    FTP_AUTO_REFRESH_MIN_MINUTES,
    OBJECT_NAME_OVERRIDES_FILENAME,
    OBJECT_NAME_OVERRIDES_VERSION,
    RESULT_LABEL_CATEGORIES,
    RESULT_LABEL_DEFAULTS,
    RESULT_LABEL_MAX_LEN,
    _RESULT_CATEGORY_BY_DEFAULT,
)
from webapp.io_utils import atomic_write_json
from webapp.state import app_settings_lock


# ---- имена объектов ---------------------------------------------------------
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


# ---- стили кривых графика (цвет + тип линии), общие для всех источников -----
def chart_style_settings_path() -> Path:
    return app_config.TEMP_ROOT / CHART_STYLE_SETTINGS_FILENAME


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
    return app_config.TEMP_ROOT / FOLDER_SOURCE_SETTINGS_FILENAME


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
    return app_config.TEMP_ROOT / APP_SETTINGS_FILENAME


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
        "update_auto_enabled": _coerce_bool(
            data.get("update_auto_enabled"),
            DEFAULT_APP_SETTINGS["update_auto_enabled"],
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
