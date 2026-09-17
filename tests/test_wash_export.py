"""Выгрузка журнала моек в .xlsx: средние по фазам, сборка строк, книга, роут.

Книга пишется своим кодом (webapp/xlsx_writer.py), поэтому её формат проверяется
здесь же: распаковываем zip и читаем XML — ошибка в разметке иначе всплыла бы
только при открытии файла в Excel у пользователя.
"""
import re
import zipfile
from datetime import datetime
from io import BytesIO
from xml.etree import ElementTree

import pytest

import wash_report as core
import webapp.app as app
from webapp import wash_export
from webapp.xlsx_writer import Column, build_workbook

SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _column_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref).group(1)
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _sheet_rows(content: bytes, width: int | None = None) -> list[list[str]]:
    """Значения ячеек листа построчно: inline-строки — текстом, числа — как есть.

    Ячейки раскладываются по адресу (`r="C5"`), а не по порядку: пустые значения
    книга не пишет вовсе, и чтение подряд сдвигало бы колонки — ровно та ошибка,
    которой не сделает ни Excel, ни openpyxl.
    """
    with zipfile.ZipFile(BytesIO(content)) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml")
    root = ElementTree.fromstring(xml)
    rows = []
    for row in root.iter(f"{SHEET_NS}row"):
        cells: dict[int, str] = {}
        for cell in row.iter(f"{SHEET_NS}c"):
            inline = cell.find(f"{SHEET_NS}is/{SHEET_NS}t")
            value = cell.find(f"{SHEET_NS}v")
            cells[_column_index(cell.get("r"))] = (
                inline.text if inline is not None else (value.text if value is not None else "")
            )
        size = width if width is not None else (max(cells) + 1 if cells else 0)
        rows.append([cells.get(index, "") for index in range(size)])
    return rows


def _sample(process, concentration, temperature_supply, ts=0.0, flow=10.0):
    return core.Sample(
        ts=ts,
        concentration_return=concentration,
        temperature_return=60.0,
        temperature_supply=temperature_supply,
        pressure_supply=2.0,
        flow_supply=flow,
        process=process,
        program=3,
        object_id=4,
    )


def _cycle(flow_average=None):
    flow = core.StatsBundle()
    if flow_average is not None:
        flow.add(flow_average)
    return core.Cycle(
        source_db="/a/Canal_1.db",
        channel=1,
        object_id=4,
        object_name="Танк 1",
        program_id=3,
        program_name="Щёлочь + кислота",
        start_ts=1_700_000_000.0,
        end_ts=1_700_003_600.0,
        operations=["x"],
        sample_count=5,
        concentration_return=core.StatsBundle(),
        temperature_return=core.StatsBundle(),
        temperature_supply=core.StatsBundle(),
        pressure_supply=core.StatsBundle(),
        flow_supply=flow,
    )


# --- средние по рабочей полке фазы ---------------------------------------


def test_phase_averages_skip_fill_and_flush_edges():
    # Края фазы — заполнение контура (0.2%, 40 °C) и вытеснение (0.4%, 50 °C):
    # они не режим и в средние попадать не должны.
    samples = [
        _sample(core.ALKALI_PROCESS_ID, 0.2, 40.0, ts=1),
        _sample(core.ALKALI_PROCESS_ID, 2.0, 80.0, ts=2),
        _sample(core.ALKALI_PROCESS_ID, 2.2, 82.0, ts=3),
        _sample(core.ALKALI_PROCESS_ID, 0.4, 50.0, ts=4),
    ]
    concentration, temperature = core.phase_working_averages(
        samples, core.ALKALI_PROCESS_ID, norm=2.0, tolerance_percent=10.0
    )
    assert concentration == pytest.approx(2.1)
    assert temperature == pytest.approx(81.0)


def test_phase_averages_without_norm_use_peak_fraction():
    # Норматив не задан — полку отделяет доля пика, а не порог нормы.
    samples = [
        _sample(core.ACID_PROCESS_ID, 0.1, 30.0, ts=1),
        _sample(core.ACID_PROCESS_ID, 1.8, 70.0, ts=2),
        _sample(core.ACID_PROCESS_ID, 0.2, 35.0, ts=3),
    ]
    concentration, temperature = core.phase_working_averages(samples, core.ACID_PROCESS_ID)
    assert concentration == pytest.approx(1.8)
    assert temperature == pytest.approx(70.0)


def test_phase_averages_fall_back_to_whole_phase_when_norm_never_reached():
    # Раствор не подан: полки нет. Строка выгрузки обязана показать фактические
    # (низкие) значения, а не остаться пустой.
    samples = [
        _sample(core.ALKALI_PROCESS_ID, 0.2, 40.0, ts=1),
        _sample(core.ALKALI_PROCESS_ID, 0.4, 44.0, ts=2),
    ]
    concentration, temperature = core.phase_working_averages(
        samples, core.ALKALI_PROCESS_ID, norm=9.0
    )
    assert concentration == pytest.approx(0.3)
    assert temperature == pytest.approx(42.0)


def test_phase_averages_ignore_missing_values_and_other_phases():
    samples = [
        _sample(core.ACID_PROCESS_ID, 1.0, 70.0, ts=1),
        _sample(core.ACID_PROCESS_ID, None, None, ts=2),  # NULL при обрыве связи
        _sample(core.ALKALI_PROCESS_ID, 5.0, 90.0, ts=3),  # чужая фаза
    ]
    assert core.phase_working_averages(samples, core.ACID_PROCESS_ID) == (
        pytest.approx(1.0),
        pytest.approx(70.0),
    )
    # Фазы в мойке нет — считать нечего, но и падать не на чем.
    assert core.phase_working_averages(samples, 999) == (None, None)


# --- сборка строк выгрузки ------------------------------------------------


def test_export_row_has_columns_in_declared_order(monkeypatch):
    cycle = _cycle(flow_average=12.345)
    samples = [
        _sample(core.ALKALI_PROCESS_ID, 2.0, 80.0, ts=1),
        _sample(core.ACID_PROCESS_ID, 1.0, 65.0, ts=2),
    ]
    monkeypatch.setattr(core, "analysis_samples_for_cycle", lambda analysis, cycle: samples)

    row = wash_export.build_export_row(None, cycle, {}, {(1, 4): "Танк 1"})

    assert len(row) == len(wash_export.EXPORT_COLUMNS)
    start, end, obj, program, alkali_t, alkali_c, acid_t, acid_c, flow = row
    assert isinstance(start, datetime) and isinstance(end, datetime)
    assert (obj, program) == ("Танк 1", "Щёлочь + кислота")
    assert (alkali_t, alkali_c) == (80.0, 2.0)
    assert (acid_t, acid_c) == (65.0, 1.0)
    assert flow == 12.35  # округление до сотых


def test_export_row_survives_unavailable_samples(monkeypatch):
    # Поток сэмплов вытеснен из кэша: режимные колонки пустые, но время, объект,
    # рецепт и расход известны из анализа — строку отдаём.
    def raise_unavailable(analysis, cycle):
        raise core.SampleStreamUnavailable("side-файл недоступен")

    monkeypatch.setattr(core, "analysis_samples_for_cycle", raise_unavailable)
    row = wash_export.build_export_row(None, _cycle(flow_average=8.0), {}, {})

    assert row[2:4] == ["Объект 4", "Щёлочь + кислота"]
    assert row[4:8] == [None, None, None, None]
    assert row[8] == 8.0


def test_export_rows_skip_keys_missing_from_analysis(monkeypatch):
    cycle = _cycle(flow_average=5.0)
    analysis = type("A", (), {"cycles": [cycle]})()
    monkeypatch.setattr(core, "analysis_samples_for_cycle", lambda analysis, cycle: [])

    rows, missing = wash_export.build_export_rows(
        analysis, [core.make_cycle_key(cycle), "чужой::ключ"], {}, {}
    )
    assert len(rows) == 1
    assert missing == 1


def test_export_rows_follow_key_order(monkeypatch):
    first = _cycle(flow_average=1.0)
    second = _cycle(flow_average=2.0)
    second.start_ts = first.start_ts + 7200
    second.object_name = "Танк 2"
    analysis = type("A", (), {"cycles": [first, second]})()
    monkeypatch.setattr(core, "analysis_samples_for_cycle", lambda analysis, cycle: [])

    keys = [core.make_cycle_key(second), core.make_cycle_key(first)]
    rows, _ = wash_export.build_export_rows(analysis, keys, {}, {})
    # Порядок задаёт клиент (сортировка списка на экране), а не порядок анализа.
    assert [row[8] for row in rows] == [2.0, 1.0]


# --- книга ----------------------------------------------------------------


def test_workbook_parts_and_values():
    columns = (Column("Время", "datetime", 19.0), Column("Объект"), Column("Расход", "number"))
    content = build_workbook(
        columns,
        [[datetime(2026, 9, 17, 8, 30, 0), 'Танк <1> & "2"', 12.5], [None, "Без чисел", None]],
        sheet_name="Мойки",
    )

    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = set(archive.namelist())
    assert {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml"} <= names

    rows = _sheet_rows(content)
    assert rows[0] == ["Время", "Объект", "Расход"]
    # Дата — числом (Excel-serial), спецсимволы имени сохранены как текст.
    assert float(rows[1][0]) == pytest.approx(46282.354166, abs=1e-5)
    assert rows[1][1] == 'Танк <1> & "2"'
    assert float(rows[1][2]) == pytest.approx(12.5)
    # Пустые значения ячейку не создают, но адресация держит колонку на месте.
    assert _sheet_rows(content, width=3)[2] == ["", "Без чисел", ""]


def test_workbook_is_deterministic():
    columns = (Column("A"),)
    rows = [["значение"]]
    assert build_workbook(columns, rows) == build_workbook(columns, rows)


def test_workbook_sheet_name_is_sanitized():
    content = build_workbook((Column("A"),), [["x"]], sheet_name="Мойки/2026: [итог] очень длинное имя листа")
    with zipfile.ZipFile(BytesIO(content)) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
    name = workbook.split('name="', 1)[1].split('"', 1)[0]
    assert len(name) <= 31
    assert not set(name) & set(r":\/?*[]")


def test_workbook_strips_control_characters():
    # Имя объекта приходит из чужого архива: управляющий символ сделал бы XML
    # невалидным, и Excel отказался бы открывать книгу целиком.
    content = build_workbook((Column("A"),), [["Танк\x01 1"]])
    assert _sheet_rows(content)[1] == ["Танк 1"]


# --- роут -----------------------------------------------------------------


def test_export_route_rejects_bad_payloads(monkeypatch):
    with pytest.raises(app.HTTPException) as empty:
        app.export_washes({"keys": []})
    assert empty.value.status_code == 400

    with pytest.raises(app.HTTPException) as wrong_type:
        app.export_washes({"keys": "ключ"})
    assert wrong_type.value.status_code == 400

    monkeypatch.setattr(app.config, "WASH_EXPORT_MAX_ROWS", 2)
    with pytest.raises(app.HTTPException) as too_many:
        app.export_washes({"keys": ["a", "b", "c"]})
    assert too_many.value.status_code == 400
    assert "2" in too_many.value.detail


def test_export_route_returns_workbook(monkeypatch):
    cycle = _cycle(flow_average=9.0)
    analysis = type("A", (), {"cycles": [cycle]})()
    key = core.make_cycle_key(cycle)

    monkeypatch.setattr(app, "require_analysis", lambda: analysis)
    monkeypatch.setattr(app, "load_app_settings", lambda: {})
    monkeypatch.setattr(core, "analysis_samples_for_cycle", lambda analysis, cycle: [])
    app.state.object_name_overrides = {(1, 4): "Танк 1"}

    response = app.export_washes({"keys": [key, "устаревший::ключ"]})

    assert response.status_code == 200
    assert response.media_type == app.XLSX_MEDIA_TYPE
    assert response.headers["x-export-rows"] == "1"
    assert response.headers["x-export-missing"] == "1"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]

    rows = _sheet_rows(response.body, width=len(wash_export.EXPORT_COLUMNS))
    assert rows[0] == [column.title for column in wash_export.EXPORT_COLUMNS]
    assert rows[1][2] == "Танк 1"
    # Фаз в мойке нет — режимные колонки пустые, но расход на своём месте.
    assert rows[1][4:8] == ["", "", "", ""]
    assert float(rows[1][8]) == pytest.approx(9.0)


def test_export_route_409_when_nothing_found(monkeypatch):
    analysis = type("A", (), {"cycles": []})()
    monkeypatch.setattr(app, "require_analysis", lambda: analysis)
    monkeypatch.setattr(app, "load_app_settings", lambda: {})

    with pytest.raises(app.HTTPException) as error:
        app.export_washes({"keys": ["устаревший::ключ"]})
    assert error.value.status_code == 409
