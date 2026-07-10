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
    samples_by_db = {
        "/a/2026-06-29/c.db": [_sample(1000, 3), _sample(1001, 3)],
        "/a/2026-06-30/c.db": [_sample(2000, 3)],  # same object, another file
    }
    channels_by_db = {"/a/2026-06-29/c.db": 1, "/a/2026-06-30/c.db": 1}
    overviews = core.build_object_overviews(samples_by_db, channels_by_db)
    keys = [(o.channel, o.object_id) for o in overviews]
    assert keys == [(1, 3)]  # one merged object, not two per-file entries


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


def test_deduplicate_wash_intervals():
    def _interval(source_db, start, samples):
        return core.WashInterval(
            source_db=source_db,
            channel=1,
            object_id=3,
            object_name="o",
            program_id=3,
            program_name="p",
            start_ts=float(start),
            end_ts=float(start) + 600.0,
            sample_count=samples,
            concentration_return=_stats(),
            temperature_return=_stats(),
            temperature_supply=_stats(),
            pressure_supply=_stats(),
            flow_supply=_stats(),
        )

    intervals = [
        _interval("/a/2026-06-29/c.db", 1000.0, 120),
        _interval("/a/2026-06-30/c.db", 1000.2, 150),  # тот же интервал, полнее
        _interval("/a/2026-06-30/c.db", 9000.0, 80),
    ]
    result = core.deduplicate_wash_intervals(intervals)
    assert len(result) == 2
    fullest = [item for item in result if int(item.start_ts) == 1000][0]
    assert fullest.sample_count == 150


def test_build_object_overviews_deduplicates_overlapping_archives():
    # одинаковые сэмплы из перекрывающихся архивов считаются один раз
    samples_by_db = {
        "/a/2026-06-29/c.db": [_sample(1000, 3), _sample(1001, 3)],
        "/a/2026-06-30/c.db": [_sample(1000, 3), _sample(1001, 3), _sample(2000, 3)],
    }
    channels_by_db = {"/a/2026-06-29/c.db": 1, "/a/2026-06-30/c.db": 1}
    overviews = core.build_object_overviews(samples_by_db, channels_by_db)
    assert len(overviews) == 1
    assert overviews[0].sample_count == 3
    assert overviews[0].temperature_return.count == 3
