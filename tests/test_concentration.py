"""Тесты оценки мойки по нормативам концентрации рабочих растворов."""
import wash_report as core


def _seg(process_id: int, conc_min: float | None):
    """Сегмент фазы с заданным минимумом концентрации возврата (или без данных)."""
    bundle = core.StatsBundle()
    if conc_min is not None:
        bundle.add(conc_min)
    return core.Segment(
        source_db="db",
        channel=1,
        object_id=5,
        object_name="Объект 5",
        program_id=1,
        program_name="prog",
        process_id=process_id,
        process_name=core.PROCESS_NAMES.get(process_id, "?"),
        start_ts=0.0,
        end_ts=10.0,
        sample_count=1,
        concentration_return=bundle,
        temperature_return=core.StatsBundle(),
        temperature_supply=core.StatsBundle(),
        pressure_supply=core.StatsBundle(),
        flow_supply=core.StatsBundle(),
    )


ALKALI = core.ALKALI_PROCESS_ID
ACID = core.ACID_PROCESS_ID


def test_phase_below_norm_is_low():
    segments = [_seg(ALKALI, 1.7), _seg(ACID, 2.1)]
    result = core.evaluate_concentration(segments, {"alkali": 2.0, "acid": 2.0}, 0.0)
    assert result["kind"] == "low"
    alkali = next(p for p in result["phases"] if p["phase"] == "alkali")
    assert alkali["status"] == "low"
    assert alkali["min"] == 1.7
    acid = next(p for p in result["phases"] if p["phase"] == "acid")
    assert acid["status"] == "ok"


def test_all_phases_in_norm_is_ok():
    segments = [_seg(ALKALI, 2.1), _seg(ACID, 2.2)]
    result = core.evaluate_concentration(segments, {"alkali": 2.0, "acid": 2.0}, 0.0)
    assert result["kind"] == "ok"


def test_tolerance_widens_the_corridor():
    # Минимум 1.7 при норме 2.0: с допуском 20 % порог 1.6 → в норме.
    segments = [_seg(ALKALI, 1.7)]
    strict = core.evaluate_concentration(segments, {"alkali": 2.0}, 0.0)
    lenient = core.evaluate_concentration(segments, {"alkali": 2.0}, 20.0)
    assert strict["kind"] == "low"
    assert lenient["kind"] == "ok"


def test_boundary_at_threshold_is_ok():
    # Ровно на пороге (норма·(1−допуск)) — это ещё норма, не «ниже».
    segments = [_seg(ALKALI, 1.6)]
    result = core.evaluate_concentration(segments, {"alkali": 2.0}, 20.0)
    assert result["kind"] == "ok"


def test_norm_not_set_phase_unknown():
    segments = [_seg(ALKALI, 1.0), _seg(ACID, 1.0)]
    result = core.evaluate_concentration(segments, {"alkali": None, "acid": None}, 0.0)
    assert result["kind"] is None
    assert all(p["status"] == "unknown" for p in result["phases"])


def test_missing_phase_is_unknown_and_ignored():
    # Программа только со щёлочью: кислоту оценивать нечем, но щёлочь ниже нормы.
    segments = [_seg(ALKALI, 1.5)]
    result = core.evaluate_concentration(segments, {"alkali": 2.0, "acid": 2.0}, 0.0)
    assert result["kind"] == "low"
    acid = next(p for p in result["phases"] if p["phase"] == "acid")
    assert acid["status"] == "unknown"


def test_phase_without_concentration_data_is_unknown():
    segments = [_seg(ALKALI, None)]
    result = core.evaluate_concentration(segments, {"alkali": 2.0}, 0.0)
    assert result["kind"] is None


def test_minimum_across_multiple_segments_of_same_phase():
    # Несколько сегментов одной фазы: берётся минимум по всем.
    segments = [_seg(ALKALI, 2.2), _seg(ALKALI, 1.5), _seg(ALKALI, 2.0)]
    result = core.evaluate_concentration(segments, {"alkali": 2.0}, 0.0)
    alkali = next(p for p in result["phases"] if p["phase"] == "alkali")
    assert alkali["min"] == 1.5
    assert result["kind"] == "low"
