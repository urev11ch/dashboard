"""Определение потока по имени файла архива.

Имя `Canal_N` — всего лишь название объекта Data Sampling в конкретном проекте
панели; на другой панели лог называется как угодно. Проверяем, что произвольные
имена работают, а прежние `Canal_*` сохраняют свои номера каналов (от них зависят
ключи моек и ключи переименованных объектов в уже работающих установках).
"""
from pathlib import Path

import pytest

import wash_report as core


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Canal_1_20260713.db", "Canal_1"),
        ("Canal_1_20260714.db", "Canal_1"),
        # Реальная выгрузка EasyBuilder: дата В СЕРЕДИНЕ, имя лога по обе стороны.
        ("Canal_1_20260414_Canal_1.db", "Canal_1"),
        ("CIP_20260713.db", "CIP"),
        ("Мойка ЦЕХ2-2026-07-13.db", "Мойка ЦЕХ2"),
        ("wash.log.202607.db", "wash.log"),
        ("Log.db", "Log"),
        ("20260713.db", ""),
    ],
)
def test_date_is_stripped_from_name(name, expected):
    assert core.stream_base_name(Path(name).stem) == expected


def test_real_panel_exports_of_one_log_are_one_stream():
    # Два файла одной панели за разные дни — один поток, иначе мойка через
    # полночь не склеится, а журнал раздвоится.
    april = core.resolve_stream(Path("Canal_1_20260414_Canal_1.db"))
    august = core.resolve_stream(Path("Canal_1_20260825_Canal_1.db"))
    assert april == august == (1, "Канал 1")


def test_date_in_name_is_not_taken_for_a_channel_number():
    # `Canal20260414.db` — без разделителя: дата не должна стать номером канала.
    channel, label = core.resolve_stream(Path("Canal20260414.db"))
    assert label == "Canal"
    assert channel > core.STREAM_CHANNEL_ID_BASE


def test_legacy_canal_names_keep_their_channel_numbers():
    assert core.resolve_stream(Path("Canal_1_20260713.db")) == (1, "Канал 1")
    assert core.resolve_stream(Path("canal-4.db")) == (4, "Канал 4")


def test_arbitrary_name_is_accepted_and_labelled_by_log_name():
    channel, label = core.resolve_stream(Path("/data/CIP_20260713.db"))
    assert label == "CIP"
    assert channel > core.STREAM_CHANNEL_ID_BASE


def test_same_log_across_days_is_one_stream():
    first = core.resolve_stream(Path("/data/CIP_20260713.db"))
    second = core.resolve_stream(Path("/data/2026-08/CIP_20260801.db"))
    assert first == second


def test_different_logs_are_different_streams():
    first = core.resolve_stream(Path("/data/CIP_20260713.db"))
    second = core.resolve_stream(Path("/data/Pasteur_20260713.db"))
    assert first[0] != second[0]


def test_channel_id_is_stable_across_runs():
    # Номер входит в ключ мойки и в ключ переименования объекта — он обязан быть
    # воспроизводимым, а не зависеть от порядка обнаружения файлов.
    assert core.stream_channel_id("CIP") == core.stream_channel_id("CIP")
    assert core.stream_channel_id("CIP") != core.stream_channel_id("cip")


def test_date_only_name_falls_back_to_folder():
    assert core.resolve_stream(Path("/data/Линия 3/20260713.db"))[1] == "Линия 3"
    # Папка-месяц зеркала FTP именем потока быть не может.
    assert core.resolve_stream(Path("/data/2026-07/20260713.db"))[1] == core.UNNAMED_STREAM_LABEL


def test_generated_channel_never_collides_with_panel_channels():
    for name in ("CIP", "Pasteur", "Мойка", "Журнал"):
        assert core.stream_channel_id(name) > 5


def _make_archive_db(path: Path, rows):
    import sqlite3

    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE data ([time@timestamp] REAL, data_format_0, data_format_1,"
        " data_format_2, data_format_3, data_format_4, data_format_5,"
        " data_format_6, data_format_7)"
    )
    connection.executemany("INSERT INTO data VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()


def test_preflight_accepts_file_named_by_the_panel_log(tmp_path):
    # Регрессия: раньше любое имя без `Canal` отбрасывалось ещё до открытия базы,
    # и выбранная папка давала пустой журнал.
    db_path = tmp_path / "Мойка ЦЕХ2_20260713.db"
    _make_archive_db(db_path, [(1000.0, 1.0, 60.0, 65.0, 2.0, 10.0, 6, 3, 4)])
    assert core.preflight_db_file(db_path) == core.stream_channel_id("Мойка ЦЕХ2")


def test_preflight_still_rejects_a_file_without_data_table(tmp_path):
    db_path = tmp_path / "CIP_20260713.db"
    db_path.write_bytes(b"not a sqlite database")
    with pytest.raises(SystemExit):
        core.preflight_db_file(db_path)
