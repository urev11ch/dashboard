"""Штриховка полос фаз на графике: связь с цветом и с таблицей в wash-chart.js.

Полосы фаз различались только заливкой, а на ч/б печати (отчёт по мойке уходит
в папку в распечатанном виде) пастельные цвета сводятся к почти одинаковому
серому. Штриховка дублирует цвет линиями, поэтому она обязана следовать за той
же группой операций, что и цвет, и её идентификаторы должны знать обе стороны:
Python отдаёт id в payload, wash-chart.js по нему строит SVG-паттерн.
"""
import re
from pathlib import Path

import wash_report as core
from webapp import chart_payload

CHART_JS = Path(__file__).resolve().parents[1] / "webapp" / "static" / "wash-chart.js"


def _js_pattern_ids() -> set[str]:
    block = re.search(
        r"const SEGMENT_PATTERN_OPTIONS = \[(?P<body>.*?)\n  \];",
        CHART_JS.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert block, "SEGMENT_PATTERN_OPTIONS не найден в wash-chart.js"
    return set(re.findall(r"\{\s*id:\s*\"([^\"]+)\"", block.group("body")))


def _sample_process_id(group_index: int) -> int:
    return min(core.OPERATION_STYLE_GROUPS[group_index][0])


def test_each_color_group_has_its_own_pattern():
    # Цвет и штриховка обязаны нести одно и то же различие: если две группы
    # делят штриховку, на ч/б они сливаются ровно так же, как без неё.
    patterns = [pattern for _, _, pattern in core.OPERATION_STYLE_GROUPS]
    colors = [color for _, color, _ in core.OPERATION_STYLE_GROUPS]
    assert len(set(patterns)) == len(patterns)
    assert len(set(colors)) == len(colors)
    assert core.OPERATION_STYLE_DEFAULT[1] not in patterns


def test_group_ids_do_not_overlap():
    seen: set[int] = set()
    for process_ids, _, _ in core.OPERATION_STYLE_GROUPS:
        assert not (seen & process_ids)
        seen |= process_ids


def test_color_and_pattern_read_the_same_group():
    for index, (_, color, pattern) in enumerate(core.OPERATION_STYLE_GROUPS):
        process_id = _sample_process_id(index)
        assert core.operation_color(process_id) == color
        assert core.operation_pattern(process_id) == pattern


def test_unknown_process_stays_neutral():
    # Незнакомая операция не должна притворяться известной фазой.
    color, pattern = core.OPERATION_STYLE_DEFAULT
    assert core.operation_color(9999) == color
    assert core.operation_pattern(9999) == pattern
    assert pattern == "plain"


def test_every_pattern_id_is_known_to_chart_js():
    js_ids = _js_pattern_ids()
    assert "plain" in js_ids  # страховка от «зелёного» теста на сломанном регекспе
    used = {pattern for _, _, pattern in core.OPERATION_STYLE_GROUPS}
    used.add(core.OPERATION_STYLE_DEFAULT[1])
    assert used <= js_ids


def _segment(process_id: int) -> core.Segment:
    stats = core.StatsBundle()
    return core.Segment(
        source_db="/a/Canal_1.db",
        channel=1,
        object_id=4,
        object_name="o",
        program_id=3,
        program_name="p",
        process_id=process_id,
        process_name="Щелочная мойка (контур 1)",
        start_ts=1_700_000_000.0,
        end_ts=1_700_000_060.0,
        last_sample_ts=1_700_000_060.0,
        sample_count=2,
        concentration_return=stats,
        temperature_return=stats,
        temperature_supply=stats,
        pressure_supply=stats,
        flow_supply=stats,
    )


def test_payload_segment_carries_pattern(monkeypatch):
    sample = core.Sample(
        ts=1_700_000_000.0,
        concentration_return=1.5,
        temperature_return=60.0,
        temperature_supply=65.0,
        pressure_supply=2.0,
        flow_supply=10.0,
        process=5,
        program=3,
        object_id=4,
    )
    cycle = core.Cycle(
        source_db="/a/Canal_1.db",
        channel=1,
        object_id=4,
        object_name="o",
        program_id=3,
        program_name="p",
        start_ts=1_700_000_000.0,
        end_ts=1_700_000_060.0,
        operations=["x"],
        sample_count=1,
        concentration_return=core.StatsBundle(),
        temperature_return=core.StatsBundle(),
        temperature_supply=core.StatsBundle(),
        pressure_supply=core.StatsBundle(),
        flow_supply=core.StatsBundle(),
    )
    monkeypatch.setattr(core, "analysis_samples_for_cycle", lambda analysis, cycle: [sample])
    monkeypatch.setattr(
        core, "analysis_segments_for_cycle", lambda analysis, cycle: [_segment(5)]
    )

    payload = chart_payload.build_cycle_chart_payload(None, cycle)
    segment = payload["segments"][0]
    assert segment["color"] == core.operation_color(5)
    assert segment["pattern"] == core.operation_pattern(5)
    assert segment["pattern"] in _js_pattern_ids()
