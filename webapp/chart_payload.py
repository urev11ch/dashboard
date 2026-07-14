from __future__ import annotations

import math
import textwrap
from datetime import datetime, timedelta
from typing import Any

import wash_report as core


MAX_CHART_POINTS = 900

# Палитра кривых — ISA-101 / PlantPAx. Красный, оранжевый, жёлтый и фиолетовый
# зарезервированы за приоритетами аварий и в кривых не применяются: иначе
# нормальный тренд неотличим от аварийного признака. Оттенки взяты из палитры
# сред (STYLE.md §1) — они намеренно уведены от алармовых.
SERIES_CONFIG = (
    {
        "id": "temperature_supply",
        "label": "Температура подачи",
        "unit": "C",
        "color": "#2f5c8a",
        "panel": 0,
    },
    {
        "id": "temperature_return",
        "label": "Температура возврата",
        "unit": "C",
        "color": "#3e7c8c",
        "panel": 0,
        "line_style": "dashed",
    },
    {
        "id": "concentration_return",
        "label": "Концентрация возврата",
        "unit": "%",
        "color": "#8a7a6a",
        "panel": 1,
    },
    {
        "id": "flow_supply",
        "label": "Расход подачи",
        "unit": "м3/ч",
        "color": "#6f9f82",
        "panel": 2,
    },
)

PANEL_CONFIG = (
    {"label": "Температура", "unit": "C"},
    {"label": "Концентрация", "unit": "%"},
    {"label": "Расход", "unit": "м3/ч"},
)


def _downsample_points(points: list[list[float]]) -> list[list[float]]:
    if len(points) <= MAX_CHART_POINTS:
        return points

    inner_points = points[1:-1]
    if not inner_points:
        return points

    # В каждый бакет попадает до 4 точек (first, min, max, last), поэтому
    # делим на 4 — иначе результат превышал бы MAX_CHART_POINTS и наивная
    # децимация ниже выбрасывала бы сохранённые экстремумы.
    bucket_count = max((MAX_CHART_POINTS - 2) // 4, 1)
    bucket_size = math.ceil(len(inner_points) / bucket_count)
    reduced: list[list[float]] = [points[0]]

    for start in range(0, len(inner_points), bucket_size):
        bucket = inner_points[start : start + bucket_size]
        if not bucket:
            continue

        min_point = min(bucket, key=lambda item: item[1])
        max_point = max(bucket, key=lambda item: item[1])
        if min_point[0] <= max_point[0]:
            bucket_points = [min_point, max_point]
        else:
            bucket_points = [max_point, min_point]

        first_point = bucket[0]
        last_point = bucket[-1]
        for point in [first_point, *bucket_points, last_point]:
            if point != reduced[-1]:
                reduced.append(point)

    if reduced[-1] != points[-1]:
        reduced.append(points[-1])

    # Дополнительная децимация не нужна: bucket_count бакетов дают не больше
    # 4 точек каждый, то есть максимум MAX_CHART_POINTS - 2 точки.
    return reduced


def _format_segment_label(segment: core.Segment, index: int) -> str:
    label = f"{index}. {core.operation_label(segment.process_name)}"
    return textwrap.fill(label, width=26, break_long_words=False)


def _tz_offset_minutes(timestamp: float) -> int:
    """Смещение локальной таймзоны сервера от UTC в минутах. Битая метка
    времени в архиве не должна ронять график: берём смещение «сейчас»."""
    try:
        offset = datetime.fromtimestamp(timestamp).astimezone().utcoffset()
    except (OverflowError, OSError, ValueError):
        offset = None
    if offset is None:
        offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return int(offset.total_seconds() // 60)


def build_cycle_chart_payload(
    analysis: core.AnalysisResult,
    cycle: core.Cycle,
) -> dict[str, Any]:
    cycle_samples = core.analysis_samples_for_cycle(analysis, cycle)
    cycle_segments = core.analysis_segments_for_cycle(analysis, cycle)

    if not cycle_samples:
        return {
            "has_data": False,
            "panels": list(PANEL_CONFIG),
            "series": [],
            "segments": [],
        }

    series_payload = []
    for config in SERIES_CONFIG:
        points = []
        for sample in cycle_samples:
            value = getattr(sample, config["id"])
            if value is None:
                # NULL в архиве (обрыв связи) — точку на кривую не кладём.
                continue
            # Значения не правим: концентрация клипается один раз, на разборе
            # строки архива (иначе статистика и кривая расходятся).
            points.append([round(sample.ts * 1000), value])
        points = _downsample_points(points)

        series_payload.append(
            {
                "id": config["id"],
                "label": config["label"],
                "unit": config["unit"],
                "color": config["color"],
                "panel": config["panel"],
                "line_style": config.get("line_style", "solid"),
                "points": points,
            }
        )

    segments_payload = [
        {
            "label": _format_segment_label(segment, index),
            "start": round(segment.start_ts * 1000),
            "end": round(segment.end_ts * 1000),
            "color": core.operation_color(segment.process_id),
        }
        for index, segment in enumerate(cycle_segments, start=1)
    ]

    return {
        "has_data": True,
        "panels": list(PANEL_CONFIG),
        "series": series_payload,
        "segments": segments_payload,
        "meta": {
            "start": round(cycle.start_ts * 1000),
            "end": round(cycle.end_ts * 1000),
            "point_count": len(cycle_samples),
            # Смещение локальной таймзоны сервера от UTC в минутах на момент
            # начала мойки: фронтенд форматирует время на графике так же,
            # как таблицы (format_ts в таймзоне сервера).
            "tz_offset_min": _tz_offset_minutes(cycle.start_ts),
        },
    }
