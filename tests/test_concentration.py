"""Тесты оценки мойки по нормативам концентрации рабочих растворов.

Датчик концентрации стоит на возврате, поэтому в начале фазы (заполнение контура)
концентрация нарастает, а в конце — спадает. Оценка должна отделять эти переходные
края от рабочей «полки»: ловить и недостижение нормы, и провал на рабочем участке.
"""
import wash_report as core


def _sample(ts: float, process_id: int, conc: float | None):
    return core.Sample(
        ts=ts,
        concentration_return=conc,
        temperature_return=None,
        temperature_supply=None,
        pressure_supply=None,
        flow_supply=None,
        process=process_id,
        program=1,
        object_id=5,
    )


def _phase_series(process_id: int, values):
    """Сэмплы одной фазы с концентрацией values по возрастанию времени."""
    return [_sample(float(i), process_id, v) for i, v in enumerate(values)]


ALKALI = core.ALKALI_PROCESS_ID
ACID = core.ACID_PROCESS_ID


def test_transient_start_does_not_trigger_low():
    # Классический профиль: нарастание 0→2.0 (заполнение), полка 2.0, спад в конце.
    # Норма 2.0, допуск 10 % (порог 1.8). Раньше минимум (0.0 в начале) ложно давал
    # «ниже нормы»; теперь переходные края не учитываются → в норме.
    values = [0.0, 0.5, 1.2, 1.9, 2.0, 2.05, 2.0, 1.98, 1.0, 0.2]
    result = core.evaluate_concentration(_phase_series(ALKALI, values), {"alkali": 2.0}, 10.0)
    assert result["kind"] == "ok"
    phase = result["phases"][0]
    assert phase["status"] == "ok"
    assert phase["floor"] >= phase["threshold"]


def test_not_reached_is_low():
    # Раствор слабый: концентрация так и не поднялась до порога 1.8.
    values = [0.0, 0.4, 0.9, 1.2, 1.3, 1.2, 0.5]
    result = core.evaluate_concentration(_phase_series(ALKALI, values), {"alkali": 2.0}, 10.0)
    assert result["kind"] == "low"
    phase = result["phases"][0]
    assert phase["status"] == "low"
    assert phase["reason"] == "not_reached"
    assert phase["peak"] == 1.3


def test_dip_on_plateau_is_low():
    # Вышел на режим, но посередине полки провалился ниже порога (разбавление),
    # затем вернулся. Спад в конце — не в счёт, а вот провал внутри — да.
    values = [0.0, 1.5, 2.0, 2.0, 1.4, 2.0, 2.0, 0.3]
    result = core.evaluate_concentration(_phase_series(ALKALI, values), {"alkali": 2.0}, 0.0)
    assert result["kind"] == "low"
    phase = result["phases"][0]
    assert phase["status"] == "low"
    assert phase["reason"] == "dip"
    assert phase["floor"] == 1.4


def test_tolerance_widens_the_corridor():
    # Полка держится на 1.7. Норма 2.0: допуск 0 → порог 2.0 (не достигла),
    # допуск 20 → порог 1.6 (в норме).
    values = [0.0, 1.0, 1.7, 1.7, 1.7, 0.5]
    strict = core.evaluate_concentration(_phase_series(ALKALI, values), {"alkali": 2.0}, 0.0)
    lenient = core.evaluate_concentration(_phase_series(ALKALI, values), {"alkali": 2.0}, 20.0)
    assert strict["kind"] == "low"
    assert lenient["kind"] == "ok"


def test_separate_norms_per_phase():
    samples = _phase_series(ALKALI, [0.0, 2.0, 2.0, 0.1]) + _phase_series(ACID, [0.0, 1.5, 1.5, 0.1])
    result = core.evaluate_concentration(samples, {"alkali": 2.0, "acid": 1.5}, 0.0)
    assert result["kind"] == "ok"


def test_norm_not_set_phase_unknown():
    result = core.evaluate_concentration(
        _phase_series(ALKALI, [0.0, 2.0, 2.0]), {"alkali": None, "acid": None}, 0.0
    )
    assert result["kind"] is None
    assert all(p["status"] == "unknown" for p in result["phases"])


def test_missing_phase_is_unknown_and_ignored():
    # Программа только со щёлочью (ниже нормы): кислоту оценивать нечем.
    samples = _phase_series(ALKALI, [0.0, 1.0, 1.2, 1.0])
    result = core.evaluate_concentration(samples, {"alkali": 2.0, "acid": 2.0}, 0.0)
    assert result["kind"] == "low"
    acid = next(p for p in result["phases"] if p["phase"] == "acid")
    assert acid["status"] == "unknown"


def test_phase_without_data_is_unknown():
    samples = [_sample(0.0, ALKALI, None), _sample(1.0, ALKALI, None)]
    result = core.evaluate_concentration(samples, {"alkali": 2.0}, 0.0)
    assert result["kind"] is None
