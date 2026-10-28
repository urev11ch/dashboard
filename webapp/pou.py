"""ПОУ (пастеризационно-охладительная установка): описание архивов и тренды.

Журнала прогонов с оценкой пока нет — коды процесса ещё не известны. Сейчас
режим ПОУ показывает тренды всех тегов архива за выбранный период: какие теги
есть на панели, определяется по подписям `data_format`, а не по номерам.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any, Callable

import wash_report as core
from webapp.chart_payload import _downsample_points
from webapp.state import PouColumn, PouWorkspace

# Панели графика ПОУ: (ключ, подпись, единица). Тег попадает на панель по корню
# слова в подписи; нераспознанные — на «Прочее».
POU_PANELS = (
    ("temperature", "Температура", "C"),
    ("valve", "Клапан возврата", ""),
    ("state", "Процесс и шаг", ""),
    ("other", "Прочее", ""),
)
_PANEL_INDEX = {key: index for index, (key, _label, _unit) in enumerate(POU_PANELS)}
# Цвета — из палитры серий графика мойки (chart_payload.SERIES_CONFIG).
_SERIES_COLORS = ("#2563eb", "#dc2626", "#059669", "#7c3aed")


def pou_panel_for(label: str) -> str:
    text = core.normalize_column_comment(label)
    if "температур" in text or "temperature" in text:
        return "temperature"
    if "клапан" in text or "valve" in text:
        return "valve"
    if any(word in text for word in ("процесс", "шаг", "режим", "process", "step")):
        return "state"
    return "other"


def _data_columns(connection: sqlite3.Connection) -> list[tuple[int, str]]:
    columns = []
    for row in connection.execute("PRAGMA table_info(data)"):
        name = str(row[1]) if row and len(row) > 1 else ""
        match = core.DATA_FORMAT_COLUMN_RE.match(name)
        if match:
            columns.append((int(match.group(1)), name))
    columns.sort()
    return columns


def describe_pou_files(
    db_files: list[Path],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[PouWorkspace, list[str]]:
    """Теги и общий диапазон времени по базам ПОУ. Битые базы пропускаются —
    возвращаются вторым значением (имена), как у разбора моек."""
    workspace = PouWorkspace()
    labels_seen: dict[str, PouColumn] = {}
    skipped: list[str] = []
    for db_path in db_files:
        core.raise_if_cancelled(cancel_check)
        try:
            connection = core.connect_read_only(db_path)
            try:
                comments = core.read_data_format_comments(connection)
                columns = _data_columns(connection)
                row = connection.execute(
                    f'SELECT MIN("{core.TIMESTAMP_COLUMN}"), MAX("{core.TIMESTAMP_COLUMN}"), COUNT(*) FROM data'
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            skipped.append(db_path.name)
            continue
        workspace.db_files.append(db_path)
        for index, column in columns:
            label = str(comments.get(index) or column)
            # Один тег на подпись: у разных файлов одной панели номера столбцов
            # совпадают, но мерджим по подписи — она и есть смысл тега.
            labels_seen.setdefault(label, PouColumn(column=column, label=label))
        if row and row[0] is not None:
            start, end, count = float(row[0]), float(row[1]), int(row[2] or 0)
            workspace.start_ts = start if workspace.start_ts is None else min(workspace.start_ts, start)
            workspace.end_ts = end if workspace.end_ts is None else max(workspace.end_ts, end)
            workspace.row_count += count
    workspace.columns = list(labels_seen.values())
    return workspace, skipped


def build_pou_trend_payload(
    workspace: PouWorkspace, start_ts: float, end_ts: float
) -> dict[str, Any]:
    """Тренд всех тегов за [start_ts, end_ts] (секунды архива) в формате графика
    мойки (WashChart): панели, серии, прореженные точки."""
    if end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts
    used_panels = sorted({_PANEL_INDEX[pou_panel_for(col.label)] for col in workspace.columns})
    panel_remap = {original: position for position, original in enumerate(used_panels)}
    series_points: dict[str, list[list[float]]] = {col.label: [] for col in workspace.columns}

    for db_path in workspace.db_files:
        try:
            connection = core.connect_read_only(db_path)
        except sqlite3.Error:
            continue
        try:
            comments = core.read_data_format_comments(connection)
            columns = _data_columns(connection)
            if not columns:
                continue
            labels = [str(comments.get(index) or column) for index, column in columns]
            select = ", ".join(f'"{column}"' for _index, column in columns)
            rows = connection.execute(
                f'SELECT "{core.TIMESTAMP_COLUMN}", {select} FROM data '
                f'WHERE "{core.TIMESTAMP_COLUMN}" BETWEEN ? AND ? '
                f'ORDER BY "{core.TIMESTAMP_COLUMN}"',
                (start_ts, end_ts),
            )
            for row in rows:
                ts = row[0]
                if ts is None:
                    continue
                ts_ms = round(float(ts) * 1000)
                for label, value in zip(labels, row[1:]):
                    if value is None or label not in series_points:
                        continue
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(number):
                        series_points[label].append([ts_ms, number])
        except sqlite3.Error:
            continue
        finally:
            connection.close()

    series = []
    color_by_panel: dict[int, int] = {}
    for col in workspace.columns:
        points = sorted(series_points[col.label])
        panel = panel_remap[_PANEL_INDEX[pou_panel_for(col.label)]]
        color_index = color_by_panel.get(panel, 0)
        color_by_panel[panel] = color_index + 1
        series.append(
            {
                "id": f"pou_{col.column}",
                "label": col.label,
                "unit": POU_PANELS[used_panels[panel]][2],
                "color": _SERIES_COLORS[color_index % len(_SERIES_COLORS)],
                "panel": panel,
                "line_style": "solid",
                "points": _downsample_points(points),
            }
        )
    has_data = any(item["points"] for item in series)
    return {
        "has_data": has_data,
        "panels": [
            {"label": POU_PANELS[index][1], "unit": POU_PANELS[index][2]} for index in used_panels
        ],
        "series": series,
        "segments": [],
        "meta": {"start": round(start_ts * 1000), "end": round(end_ts * 1000)},
    }
