from __future__ import annotations

import math
import textwrap
from typing import Any

import wash_report as core


MAX_CHART_POINTS = 900

SERIES_CONFIG = (
    {
        "id": "temperature_supply",
        "label": "Температура подачи",
        "unit": "C",
        "color": "#2563eb",
        "panel": 0,
    },
    {
        "id": "temperature_return",
        "label": "Температура возврата",
        "unit": "C",
        "color": "#dc2626",
        "panel": 0,
        "line_style": "dashed",
    },
    {
        "id": "concentration_return",
        "label": "Концентрация возврата",
        "unit": "%",
        "color": "#7c3aed",
        "panel": 1,
    },
    {
        "id": "flow_supply",
        "label": "Расход подачи",
        "unit": "м3/ч",
        "color": "#059669",
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

    bucket_count = max((MAX_CHART_POINTS - 2) // 2, 1)
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

    if len(reduced) <= MAX_CHART_POINTS:
        return reduced

    step = math.ceil(len(reduced) / MAX_CHART_POINTS)
    compact = reduced[::step]
    if compact[-1] != reduced[-1]:
        compact.append(reduced[-1])
    return compact


def _format_segment_label(segment: core.Segment, index: int) -> str:
    label = f"{index}. {core.operation_label(segment.process_name)}"
    return textwrap.fill(label, width=26, break_long_words=False)


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
            if config["id"] == "concentration_return":
                value = max(value, 0.0)
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
        },
    }
