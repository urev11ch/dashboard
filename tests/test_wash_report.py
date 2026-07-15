"""Тесты основной логики анализа (wash_report)."""
import sqlite3

import wash_report as core


def _stats():
    return core.StatsBundle()


def _cycle(source_db, channel, object_id, program_id, start, end, samples):
    return core.Cycle(
        source_db=source_db,
        channel=channel,
        object_id=object_id,
        object_name="o",
        program_id=program_id,
        program_name="p",
        start_ts=float(start),
        end_ts=float(end),
        operations=["x"],
        sample_count=samples,
        concentration_return=_stats(),
        temperature_return=_stats(),
        temperature_supply=_stats(),
        pressure_supply=_stats(),
        flow_supply=_stats(),
    )


def _sample(ts, object_id, program=3, process=6):
    return core.Sample(
        ts=float(ts),
        concentration_return=1.0,
        temperature_return=2.0,
        temperature_supply=3.0,
        pressure_supply=4.0,
        flow_supply=5.0,
        process=process,
        program=program,
        object_id=object_id,
    )


def _chunk(db_path, channel, samples):
    from pathlib import Path

    return core.DbAnalysisChunk(
        db_path=Path(db_path),
        channel=channel,
        samples=list(samples),
        segments=[],
        cycles=[],
        objects=core.collect_object_overviews(samples, db_path, channel),
    )


def test_deduplicate_cycles_keeps_unique_and_fullest():
    cycles = [
        _cycle("/a/2026-06-29/c.db", 1, 3, 3, 1000.0, 1900.0, 120),
        _cycle("/a/2026-06-30/c.db", 1, 3, 3, 1000.2, 2000.0, 150),  # same wash, fuller
        _cycle("/a/2026-06-29/d.db", 2, 5, 4, 5000.0, 5600.0, 80),
        _cycle("/a/2026-06-30/d.db", 2, 5, 4, 5000.0, 5600.0, 80),  # duplicate
    ]
    result = core.deduplicate_cycles(cycles)
    assert len(result) == 2
    wash_a = [c for c in result if c.object_id == 3][0]
    assert wash_a.sample_count == 150  # most complete copy kept


def test_deduplicate_cycles_distinct_starts_not_merged():
    cycles = [
        _cycle("a", 1, 3, 3, 1000.0, 1500.0, 10),
        _cycle("a", 1, 3, 3, 9000.0, 9500.0, 10),  # different start -> separate
    ]
    assert len(core.deduplicate_cycles(cycles)) == 2


def test_build_object_overviews_groups_by_object_across_files():
    chunks = [
        _chunk("/a/2026-06-29/c.db", 1, [_sample(1000, 3), _sample(1001, 3)]),
        _chunk("/a/2026-06-30/c.db", 1, [_sample(2000, 3)]),  # тот же объект, другой файл
    ]
    overviews = core.build_object_overviews(chunks)
    keys = [(o.channel, o.object_id) for o in overviews]
    assert keys == [(1, 3)]  # одна объединённая запись, а не по одной на файл
    assert (overviews[0].start_ts, overviews[0].end_ts) == (1000.0, 2000.0)


def test_pluralize_russian():
    assert core.pluralize(1, "час", "часа", "часов") == "час"
    assert core.pluralize(2, "час", "часа", "часов") == "часа"
    assert core.pluralize(5, "час", "часа", "часов") == "часов"
    assert core.pluralize(11, "час", "часа", "часов") == "часов"
    assert core.pluralize(21, "час", "часа", "часов") == "час"


def test_format_duration():
    assert core.format_duration(0) == "0 секунд"
    assert core.format_duration(65) == "1 минута 5 секунд"
    assert core.format_duration(3661).startswith("1 час")


def test_cycle_result_label_from_operations():
    completed = [core.PROCESS_NAMES[6], core.PROCESS_NAMES[21]]
    assert core.cycle_result_label_from_operations(completed) == "Завершено штатно"
    with_pause = [core.PROCESS_NAMES[6], core.PROCESS_NAMES[55], core.PROCESS_NAMES[21]]
    assert core.cycle_result_label_from_operations(with_pause) == "Завершено, были паузы"
    unfinished = [core.PROCESS_NAMES[6]]
    assert core.cycle_result_label_from_operations(unfinished) == "Требует проверки"


def test_cycle_result_label_completion_step_optional():
    # require_completion_step=False: мойка без финального «Окончание мойки» (21)
    # больше не понижается до «Требует проверки».
    unfinished = [core.PROCESS_NAMES[6]]
    assert (
        core.cycle_result_label_from_operations(unfinished, require_completion_step=False)
        == "Завершено штатно"
    )
    # Паузы всё равно учитываются даже без требования финального шага.
    with_pause = [core.PROCESS_NAMES[6], core.PROCESS_NAMES[55]]
    assert (
        core.cycle_result_label_from_operations(with_pause, require_completion_step=False)
        == "Завершено, были паузы"
    )
    # По умолчанию (True) поведение прежнее.
    assert core.cycle_result_label_from_operations(unfinished) == "Требует проверки"


def test_format_ts_handles_bad_timestamp():
    # не должно падать на «битой» метке времени
    assert core.format_ts(10**30) == "н/д"


def _make_archive_db(path, rows):
    """Создаёт минимальный архив панели с таблицей data."""
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE data ([time@timestamp] REAL, data_format_0, data_format_1,"
        " data_format_2, data_format_3, data_format_4, data_format_5,"
        " data_format_6, data_format_7)"
    )
    connection.executemany(
        "INSERT INTO data VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    connection.commit()
    connection.close()


def test_read_samples_keeps_null_metrics_out_of_stats(tmp_path):
    # NULL (обрыв связи) не должен превращаться в 0.0 и портить min/avg
    db_path = tmp_path / "Canal_1_test.db"
    _make_archive_db(
        db_path,
        [
            (1000.0, 1.0, 60.0, 65.0, 2.0, 10.0, 6, 3, 4),
            (1001.0, None, None, None, None, None, 6, 3, 4),
        ],
    )
    samples = core.read_samples(db_path)
    assert len(samples) == 2
    assert samples[1].temperature_return is None

    metrics = core.new_metrics()
    for sample in samples:
        core.add_sample_to_metrics(metrics, sample)
    assert metrics["temperature_return"].count == 1
    assert metrics["temperature_return"].minimum == 60.0  # не 0.0


def test_read_samples_skips_broken_rows(tmp_path):
    # одна нечисловая строка не должна валить анализ всего файла
    db_path = tmp_path / "Canal_1_test.db"
    _make_archive_db(
        db_path,
        [
            (1000.0, 1.0, 60.0, 65.0, 2.0, 10.0, 6, 3, 4),
            (1001.0, "мусор", 60.0, 65.0, 2.0, 10.0, 6, 3, 4),
            (1002.0, 1.0, 60.0, 65.0, 2.0, 10.0, "мусор", 3, 4),
            (1003.0, 1.1, 61.0, 66.0, 2.1, 10.1, 6, 3, 4),
        ],
    )
    samples = core.read_samples(db_path)
    assert [sample.ts for sample in samples] == [1000.0, 1003.0]


def test_connect_read_only_does_not_create_missing_file(tmp_path):
    missing = tmp_path / "нет такого.db"
    try:
        connection = core.connect_read_only(missing)
    except sqlite3.OperationalError:
        pass
    else:
        connection.close()
    assert not missing.exists()


def test_connect_read_only_rejects_writes(tmp_path):
    db_path = tmp_path / "Canal_1 100%?#.db"  # спецсимволы sqlite URI в имени
    _make_archive_db(db_path, [])
    connection = core.connect_read_only(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM data").fetchone() == (0,)
        try:
            connection.execute("INSERT INTO data VALUES (1,1,1,1,1,1,1,1,1)")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("Запись в read-only соединение должна падать")
    finally:
        connection.close()


def test_deduplicate_cycles_merges_partial_overlap_from_other_archive():
    # Копии из перекрывающихся выгрузок обрезаны по-разному: одинакового
    # старта нет, но интервалы пересекаются — это одна и та же мойка.
    cycles = [
        _cycle("/a/2026-06-29/c.db", 1, 3, 3, 1000.0, 2000.0, 100),
        _cycle("/a/2026-06-30/c.db", 1, 3, 3, 1500.0, 2600.0, 250),
    ]
    result = core.deduplicate_cycles(cycles)
    assert len(result) == 1
    assert result[0].sample_count == 250  # оставлена самая полная копия


def test_deduplicate_cycles_survives_rounding_boundary():
    # Старый дедуп по int(round(start_ts)) рвался на 1000.4 / 1000.6.
    cycles = [
        _cycle("/a/c.db", 1, 3, 3, 1000.4, 2000.0, 100),
        _cycle("/a/d.db", 1, 3, 3, 1000.6, 2000.0, 120),
    ]
    assert len(core.deduplicate_cycles(cycles)) == 1


def test_deduplicate_cycles_keeps_different_objects():
    cycles = [
        _cycle("/a/c.db", 1, 3, 3, 1000.0, 2000.0, 100),
        _cycle("/a/c.db", 1, 5, 3, 1000.0, 2000.0, 100),  # другой объект — не дубликат
    ]
    assert len(core.deduplicate_cycles(cycles)) == 2


def test_merge_channel_samples_prefers_record_with_fewer_nulls():
    empty = core.Sample(
        ts=1000.0,
        concentration_return=None,
        temperature_return=None,
        temperature_supply=None,
        pressure_supply=None,
        flow_supply=None,
        process=6,
        program=3,
        object_id=4,
    )
    full = _sample(1000, 4)
    merged = core.merge_channel_samples([[empty], [full]])
    assert len(merged) == 1
    assert merged[0].temperature_return == 2.0  # NULL-копия не выигрывает


def test_merge_channel_samples_keeps_objects_with_same_timestamp():
    # Ключ дедупа включает объект: строки разных объектов с одной меткой
    # времени не должны молча теряться.
    merged = core.merge_channel_samples([[_sample(1000, 4), _sample(1000, 5)]])
    assert [sample.object_id for sample in merged] == [4, 5]


def test_segment_end_is_next_sample_timestamp():
    # Конец операции — метка следующего сэмпла: односэмпловая операция не
    # должна давать 0 секунд, а между полосами не должно быть щелей.
    samples = [
        _sample(1000, 4, process=2),
        _sample(1010, 4, process=6),
        _sample(1020, 4, process=6),
        _sample(1030, 4, process=21),
    ]
    segments = core.build_segments(samples, "/a/Canal_1.db", 1, max_gap_seconds=15.0)
    assert [segment.process_id for segment in segments] == [2, 6, 21]
    assert segments[0].end_ts == 1010.0  # начало следующей операции
    assert segments[0].duration_seconds == 10.0  # раньше было 0
    assert segments[1].end_ts == 1030.0
    # У последней операции следующего сэмпла нет — берём медианный период.
    assert segments[2].end_ts == 1040.0
    assert segments[2].duration_seconds == 10.0


def _make_archive_db_rows(base_ts, count, *, step=10.0, process=6, program=3,
                          object_id=4, temperature=60.0, concentration=1.0):
    return [
        (
            base_ts + index * step,
            concentration,
            temperature,
            temperature + 5.0,
            2.0,
            10.0,
            process,
            program,
            object_id,
        )
        for index in range(count)
    ]


def test_cycle_merged_across_daily_file_boundary(tmp_path):
    # Мойка 23:50 → 00:20 лежит в двух суточных файлах одного канала и должна
    # остаться одним циклом со статистикой по объединённому набору точек.
    base = 1_700_000_000.0
    first_db = tmp_path / "Canal_1_2026-06-29.db"
    second_db = tmp_path / "Canal_1_2026-06-30.db"
    _make_archive_db(first_db, _make_archive_db_rows(base, 10, temperature=60.0))
    _make_archive_db(
        second_db,
        _make_archive_db_rows(base + 100.0, 9, temperature=80.0)
        + [(base + 190.0, 1.0, 80.0, 85.0, 2.0, 10.0, 21, 3, 4)],
    )

    chunks = [core.analyze_single_db_file(path) for path in (first_db, second_db)]
    analysis = core.build_analysis_result(
        [first_db, second_db],
        output_dir=tmp_path,
        max_gap_seconds=15.0,
        chunks=chunks,
    )

    assert len(analysis.cycles) == 1
    cycle = analysis.cycles[0]
    assert cycle.start_ts == base
    assert cycle.end_ts >= base + 190.0
    assert cycle.sample_count == 20
    # min/avg/max считаются по точкам из обоих файлов.
    assert cycle.temperature_return.minimum == 60.0
    assert cycle.temperature_return.maximum == 80.0
    assert cycle.temperature_return.count == 20
    # Источник — файл, в котором мойка началась.
    assert cycle.source_db == core.source_key(first_db)
    # График получает точки обоих файлов.
    assert len(core.analysis_samples_for_cycle(analysis, cycle)) == 20


def test_analysis_keeps_only_cycle_samples(tmp_path):
    # Точки вне моек не должны оседать в памяти и в pickle-кэше.
    base = 1_700_000_000.0
    db_path = tmp_path / "Canal_1_2026-06-29.db"
    idle_before = _make_archive_db_rows(base, 20, process=0, program=0, object_id=0)
    wash = _make_archive_db_rows(base + 1000.0, 10)
    idle_after = _make_archive_db_rows(base + 2000.0, 20, process=0, program=0, object_id=0)
    _make_archive_db(db_path, idle_before + wash + idle_after)

    chunk = core.analyze_single_db_file(db_path)
    assert len(chunk.samples) < 20  # 10 точек мойки плюс запас на границах
    assert all(sample.ts >= base + 900.0 for sample in chunk.samples)
    # Объект при этом из списка не пропадает: он собран до отсева.
    assert [(item.channel, item.object_id) for item in chunk.objects] == [(1, 4)]


def test_read_samples_clips_negative_concentration(tmp_path):
    # Клипаем один раз, на разборе строки: иначе «мин. −0.40 %» в статистике
    # против кривой на нуле на графике.
    db_path = tmp_path / "Canal_1_test.db"
    _make_archive_db(db_path, [(1000.0, -0.4, 60.0, 65.0, 2.0, 10.0, 6, 3, 4)])
    samples = core.read_samples(db_path)
    assert samples[0].concentration_return == 0.0


def test_preflight_db_file_reports_broken_file(tmp_path):
    # Битый/обрезанный файл не должен ронять разбор источника: вызывающий код
    # ловит SystemExit и пропускает файл.
    db_path = tmp_path / "Canal_1_broken.db"
    db_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 512)
    try:
        core.preflight_db_file(db_path)
    except SystemExit as error:
        assert "повреждён" in str(error)
    else:
        raise AssertionError("Ожидался SystemExit на повреждённом файле")


def test_read_samples_reports_broken_pages(tmp_path):
    # Повреждение всплывает уже на выборке строк — это тоже SystemExit.
    db_path = tmp_path / "Canal_1_broken.db"
    _make_archive_db(db_path, _make_archive_db_rows(1000.0, 2000))
    raw = bytearray(db_path.read_bytes())
    raw[4096:12288] = b"\xff" * 8192  # затираем страницы данных
    db_path.write_bytes(bytes(raw))

    try:
        core.read_samples(db_path)
    except SystemExit as error:
        assert "повреждён" in str(error)
    else:
        raise AssertionError("Ожидался SystemExit на повреждённых страницах")
