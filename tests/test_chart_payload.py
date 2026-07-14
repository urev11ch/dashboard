"""Тесты формирования данных графика мойки (webapp/chart_payload)."""
import math

import wash_report as core
from webapp import chart_payload
from webapp.chart_payload import MAX_CHART_POINTS, _downsample_points


def test_downsample_keeps_extremes_within_limit():
    # Пик и провал не должны выбрасываться наивной децимацией
    points = [[float(i), math.sin(i / 50.0) * 10.0] for i in range(10000)]
    points[3333][1] = 999.0  # одиночный пик
    points[7777][1] = -999.0  # одиночный провал

    reduced = _downsample_points(points)

    assert len(reduced) <= MAX_CHART_POINTS
    values = [point[1] for point in reduced]
    assert 999.0 in values
    assert -999.0 in values
    # первая и последняя точки сохраняются
    assert reduced[0] == points[0]
    assert reduced[-1] == points[-1]


def test_downsample_short_series_untouched():
    points = [[float(i), float(i)] for i in range(10)]
    assert _downsample_points(points) == points


def test_tz_offset_survives_broken_timestamp():
    # Битая метка времени в архиве (1e30) не должна давать 500 на графике.
    offset = chart_payload._tz_offset_minutes(1e30)
    assert isinstance(offset, int)
    assert offset == chart_payload._tz_offset_minutes(1_700_000_000.0)


def test_payload_meta_with_broken_cycle_start(monkeypatch):
    sample = core.Sample(
        ts=1_700_000_000.0,
        concentration_return=-0.4,  # клипается на разборе архива, не здесь
        temperature_return=60.0,
        temperature_supply=65.0,
        pressure_supply=2.0,
        flow_supply=10.0,
        process=6,
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
        start_ts=1e30,  # битая метка
        end_ts=1e30,
        operations=["x"],
        sample_count=1,
        concentration_return=core.StatsBundle(),
        temperature_return=core.StatsBundle(),
        temperature_supply=core.StatsBundle(),
        pressure_supply=core.StatsBundle(),
        flow_supply=core.StatsBundle(),
    )
    monkeypatch.setattr(core, "analysis_samples_for_cycle", lambda analysis, cycle: [sample])
    monkeypatch.setattr(core, "analysis_segments_for_cycle", lambda analysis, cycle: [])

    payload = chart_payload.build_cycle_chart_payload(None, cycle)
    assert payload["has_data"] is True
    assert isinstance(payload["meta"]["tz_offset_min"], int)

    # Значения метрик график не правит: концентрация уже сведена к общему виду
    # на разборе строки архива.
    concentration = [
        series for series in payload["series"] if series["id"] == "concentration_return"
    ][0]
    assert concentration["points"] == [[round(sample.ts * 1000), -0.4]]
