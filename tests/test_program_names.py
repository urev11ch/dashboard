"""Пользовательские названия программ мойки.

В архиве панели названий программ нет — только номер, поэтому имена задаёт
пользователь. Список программ на станции один и тот же для всех объектов, так
что название одно на номер: ни канала, ни объекта в ключе нет.
"""
import json

import pytest

import wash_report as core
import webapp.app as app


def _cycle(channel=1, object_id=4, program_id=3, program_name=None):
    return core.Cycle(
        source_db="/a/Canal_1.db",
        channel=channel,
        object_id=object_id,
        object_name=f"Объект {object_id}",
        program_id=program_id,
        program_name=program_name or core.fallback_program_name(program_id),
        start_ts=1_700_000_000.0,
        end_ts=1_700_003_600.0,
        operations=["x"],
        sample_count=5,
        concentration_return=core.StatsBundle(),
        temperature_return=core.StatsBundle(),
        temperature_supply=core.StatsBundle(),
        pressure_supply=core.StatsBundle(),
        flow_supply=core.StatsBundle(),
    )


def _segment(channel=1, object_id=4, program_id=3):
    return core.Segment(
        source_db="/a/Canal_1.db",
        channel=channel,
        object_id=object_id,
        object_name=f"Объект {object_id}",
        program_id=program_id,
        program_name=core.fallback_program_name(program_id),
        process_id=core.ALKALI_PROCESS_ID,
        process_name="Мойка щелочью",
        start_ts=1_700_000_000.0,
        end_ts=1_700_000_600.0,
        last_sample_ts=1_700_000_590.0,
        sample_count=5,
        concentration_return=core.StatsBundle(),
        temperature_return=core.StatsBundle(),
        temperature_supply=core.StatsBundle(),
        pressure_supply=core.StatsBundle(),
        flow_supply=core.StatsBundle(),
    )


def _analysis(cycles=None, segments=None):
    analysis = type("A", (), {})()
    analysis.cycles = list(cycles or [])
    analysis.segments = list(segments or [])
    analysis.overviews = []
    analysis.channel_labels = {}
    return analysis


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(app.config, "TEMP_ROOT", tmp_path)
    app.state.program_name_overrides = {}
    app.state.analysis = None
    yield tmp_path
    app.state.program_name_overrides = {}
    app.state.analysis = None


# --- резолв -----------------------------------------------------------------
def test_resolve_prefers_user_name():
    assert core.resolve_program_name(3, {3: "Своё"}) == "Своё"


def test_resolve_falls_back_to_builtin_and_then_to_number():
    assert core.resolve_program_name(3, {}) == core.PROGRAM_NAMES[3]
    assert core.resolve_program_name(3) == core.PROGRAM_NAMES[3]
    # Номер вне семи штатных слотов не прячем за прочерком: видно, что слот пуст.
    assert core.resolve_program_name(42, {}) == "Программа 42"


def test_parse_time_name_is_builtin_only():
    # Имя на этапе разбора архива обязано оставаться встроенным: разобранный
    # чанк уезжает в дисковый кэш, и запекание туда пользовательского названия
    # делало бы кэш протухшим после каждого переименования.
    assert core.name_for_program(3) == core.PROGRAM_NAMES[3]


# --- нормализация и хранение -------------------------------------------------
def test_normalize_drops_bad_ids_and_names():
    raw = {
        "3": " Мойка   щелочью ",
        "bad": "x",
        "999": "вне диапазона",
        "4": "   ",
    }
    assert app.settings_store.normalize_program_name_overrides(raw) == {3: "Мойка щелочью"}


def test_normalize_non_dict_input():
    assert app.settings_store.normalize_program_name_overrides(None) == {}
    assert app.settings_store.normalize_program_name_overrides(["x"]) == {}


def test_save_load_roundtrip(temp_root):
    overrides = {1: "Ополаскивание", 3: "Щёлочь + кислота"}
    app.save_program_name_overrides(temp_root, overrides)

    path = app.program_name_overrides_path(temp_root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == app.config.PROGRAM_NAME_OVERRIDES_VERSION
    # Номера программ в файле — строки: JSON другого ключа не умеет.
    assert payload["programs"] == {"1": "Ополаскивание", "3": "Щёлочь + кислота"}
    assert app.load_program_name_overrides(temp_root) == overrides


def test_save_empty_removes_file(temp_root):
    app.save_program_name_overrides(temp_root, {1: "x"})
    path = app.program_name_overrides_path(temp_root)
    assert path.exists()

    app.save_program_name_overrides(temp_root, {})
    assert not path.exists()


def test_load_ignores_corrupt_file(temp_root):
    app.program_name_overrides_path(temp_root).write_text("{ not json", encoding="utf-8")
    assert app.load_program_name_overrides(temp_root) == {}


def test_load_missing_root_returns_empty():
    assert app.load_program_name_overrides(None) == {}


# --- наложение на готовый анализ ---------------------------------------------
def test_apply_renames_cycles_and_segments_in_place():
    cycle = _cycle(program_id=3)
    segment = _segment(program_id=3)
    other = _cycle(channel=2, object_id=6, program_id=3)
    analysis = _analysis(cycles=[cycle, other], segments=[segment])
    # Те же объекты, что и в analysis.cycles: правка должна быть видна и здесь.
    analysis.sorted_cycles = [cycle, other]

    app.apply_program_name_overrides(analysis, {3: "Щёлочь + кислота"})

    assert cycle.program_name == "Щёлочь + кислота"
    assert segment.program_name == "Щёлочь + кислота"
    assert analysis.sorted_cycles[0].program_name == "Щёлочь + кислота"
    # Название общее для всех объектов — второй объект получает его же.
    assert other.program_name == "Щёлочь + кислота"


def test_apply_resets_name_when_override_removed():
    cycle = _cycle(program_id=3, program_name="Старое")
    analysis = _analysis(cycles=[cycle])

    app.apply_program_name_overrides(analysis, {})

    assert cycle.program_name == core.PROGRAM_NAMES[3]


def test_apply_on_missing_analysis_is_noop():
    app.apply_program_name_overrides(None, {3: "x"})


# --- строки редактора --------------------------------------------------------
def test_rows_always_cover_seven_panel_programs():
    rows = app.build_program_rows({}, None)
    assert [row["program_id"] for row in rows] == [1, 2, 3, 4, 5, 6, 7]
    assert all(row["is_custom_name"] is False for row in rows)


def test_rows_add_unknown_program_seen_in_data():
    analysis = _analysis(cycles=[_cycle(program_id=9)])
    rows = app.build_program_rows({}, analysis)

    row = next(row for row in rows if row["program_id"] == 9)
    assert row["program_name"] == "Программа 9"


def test_rows_mark_custom_name_and_keep_builtin():
    rows = app.build_program_rows({3: "Своё"}, None)

    row = next(row for row in rows if row["program_id"] == 3)
    assert row["own_name"] == "Своё"
    assert row["program_name"] == "Своё"
    assert row["is_custom_name"] is True
    # Встроенное остаётся рядом: оно идёт в placeholder и вернётся при сбросе.
    assert row["base_program_name"] == core.PROGRAM_NAMES[3]


def test_rows_ignore_program_zero():
    # Программа 0 — «Нет программы», в редакторе ей не место.
    analysis = _analysis(cycles=[_cycle(program_id=0)])
    rows = app.build_program_rows({}, analysis)

    assert [row["program_id"] for row in rows] == [1, 2, 3, 4, 5, 6, 7]


# --- роуты -------------------------------------------------------------------
def test_route_sets_name_and_persists(temp_root):
    cycle = _cycle(program_id=3)
    app.state.analysis = _analysis(cycles=[cycle])
    revision_before = app.state.analysis_revision

    response = app.update_program_name({"program_id": 3, "name": "  Щёлочь +   кислота "})

    # Пробелы схлопнуты, название применено к уже разобранному анализу.
    assert app.state.program_name_overrides == {3: "Щёлочь + кислота"}
    assert cycle.program_name == "Щёлочь + кислота"
    # Строки журнала кэшируются по ревизии — без её сдвига список остался бы старым.
    assert app.state.analysis_revision == revision_before + 1
    assert app.load_program_name_overrides(temp_root) == {3: "Щёлочь + кислота"}

    row = next(r for r in json.loads(response.body)["program_rows"] if r["program_id"] == 3)
    assert row["is_custom_name"] is True


def test_route_reset_drops_entry_and_file(temp_root):
    app.update_program_name({"program_id": 3, "name": "Своё"})
    app.update_program_name({"program_id": 4, "name": "Второе"})

    app.update_program_name({"program_id": 3, "mode": "reset"})
    assert app.state.program_name_overrides == {4: "Второе"}

    # Последняя запись убирает и сам файл — пустой файл не держим.
    app.update_program_name({"program_id": 4, "mode": "reset"})
    assert app.state.program_name_overrides == {}
    assert not app.program_name_overrides_path(temp_root).exists()


def test_route_rejects_bad_input(temp_root):
    cases = [
        {"program_id": "x", "name": "x"},
        {"program_id": 999, "name": "x"},
        {"program_id": 3, "name": "   "},
        {"program_id": 3, "name": "x" * 200},
        {"program_id": 3, "name": "x", "mode": "bogus"},
    ]
    for payload in cases:
        with pytest.raises(app.HTTPException) as error:
            app.update_program_name(payload)
        assert error.value.status_code == 400, payload


def test_get_route_returns_rows(temp_root):
    app.state.program_name_overrides = {3: "Своё"}
    payload = json.loads(app.get_program_names().body)

    row = next(row for row in payload["program_rows"] if row["program_id"] == 3)
    assert row["own_name"] == "Своё"


def test_sync_route_materializes_visible_names(temp_root):
    app.state.analysis = _analysis(cycles=[_cycle(program_id=3)])

    payload = json.loads(app.sync_program_names_file().body)

    assert payload["created"] is True
    assert payload["changed"] is True
    assert payload["added_entry_count"] == 7
    # В файл легло ровно то, что показывалось: семь встроенных названий панели.
    assert app.load_program_name_overrides(temp_root) == {
        program_id: core.PROGRAM_NAMES[program_id] for program_id in range(1, 8)
    }


def test_sync_route_keeps_existing_names(temp_root):
    app.update_program_name({"program_id": 3, "name": "Своё"})

    payload = json.loads(app.sync_program_names_file().body)

    assert payload["added_entry_count"] == 6
    assert app.load_program_name_overrides(temp_root)[3] == "Своё"


def test_sync_route_second_call_changes_nothing(temp_root):
    app.sync_program_names_file()
    payload = json.loads(app.sync_program_names_file().body)

    assert payload["changed"] is False
    assert payload["added_entry_count"] == 0
