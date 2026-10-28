"""Главное меню: состояние зеркала панели и строки списка.

Состояние (сколько архивов, за какое число последний, сколько занимают) читается
прямо с диска — отдельного учёта нет, каталог зеркала и есть источник правды.
"""
import pytest

import webapp.app as app
from webapp import views
from webapp.ftp_client import archive_day_from_name, ftp_profile_stats
from webapp.io_utils import format_bytes, plural_ru


@pytest.fixture
def datalog_root(tmp_path, monkeypatch):
    monkeypatch.setattr(app.config, "DATALOG_ROOT", tmp_path)
    return tmp_path


def _archive(root, name, size=1024):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# --- дата архива ------------------------------------------------------------
def test_archive_day_reads_trailing_date():
    assert archive_day_from_name("Canal_1_20260713.db") == "2026-07-13"
    assert archive_day_from_name("20260713_Canal_1.db") == "2026-07-13"


def test_archive_day_rejects_impossible_and_glued_numbers():
    # 13-го месяца не бывает; девять цифр подряд — не дата, а счётчик.
    assert archive_day_from_name("Canal_2_20261302.db") == ""
    assert archive_day_from_name("a_123456789.db") == ""
    assert archive_day_from_name("Canal_1.db") == ""


def test_archive_day_takes_last_date_in_name():
    assert archive_day_from_name("20250101_Canal_1_20260713.db") == "2026-07-13"


# --- состояние зеркала -------------------------------------------------------
def test_stats_count_size_and_last_day(datalog_root):
    profile = datalog_root / "abc"
    _archive(profile / "2026-07", "Canal_1_20260713.db", size=2048)
    _archive(profile / "2026-07", "Canal_2_20260714.db", size=1024)

    stats = ftp_profile_stats("abc")

    assert stats["archive_count"] == 2
    assert stats["size_bytes"] == 3072
    assert stats["last_day"] == "2026-07-14"


def test_stats_count_only_db_files(datalog_root):
    profile = datalog_root / "abc"
    _archive(profile, "Canal_1_20260713.db")
    _archive(profile, "readme.txt")
    _archive(profile, "archive.zip")

    assert ftp_profile_stats("abc")["archive_count"] == 1


def test_stats_skip_deleted_profile_dirs(datalog_root):
    # Удалённый профиль переименовывается в `<id>.deleted-<uuid>` и физически
    # сносится позже: его файлы не должны попадать в счёт живого профиля.
    _archive(datalog_root / "abc", "Canal_1_20260713.db")
    _archive(datalog_root / "abc.deleted-deadbeef", "Canal_1_20260101.db")

    assert ftp_profile_stats("abc")["archive_count"] == 1


def test_stats_of_missing_profile_is_empty(datalog_root):
    assert ftp_profile_stats("нет-такого") == {
        "archive_count": 0,
        "size_bytes": 0,
        "last_day": "",
    }
    assert ftp_profile_stats("")["archive_count"] == 0


def test_stats_reject_path_escape(datalog_root):
    # id панели генерируется приложением, но подставлять его в путь без проверки
    # нельзя: каталог обязан лежать НЕПОСРЕДСТВЕННО в datalog.
    _archive(datalog_root.parent / "чужое", "Canal_1_20260713.db")
    assert ftp_profile_stats("../чужое")["archive_count"] == 0


# --- сводка зеркала для карточки ---------------------------------------------
def test_mirror_meta_shows_mirror_state(datalog_root):
    profile = datalog_root / "abc"
    _archive(profile, "Canal_1_20260713.db", size=43_000_000)

    assert views.ftp_mirror_meta("abc") == ["1 архив", "последний 13.07.2026", "41 МБ"]


def test_mirror_meta_says_mirror_is_empty(datalog_root):
    # Пустое зеркало — не ошибка, но молчать нельзя: иначе непонятно, почему у
    # панели нет данных.
    assert views.ftp_mirror_meta("abc") == ["архивов нет"]


# --- форматирование ----------------------------------------------------------
def test_format_bytes():
    assert format_bytes(0) == "0 Б"
    assert format_bytes(512) == "512 Б"
    assert format_bytes(43_000_000) == "41 МБ"
    # До 10 единиц оставляем десятую долю, дальше она только шумит.
    assert format_bytes(1_600_000_000) == "1.5 ГБ"


def test_plural_ru():
    cases = {1: "архив", 2: "архива", 5: "архивов", 11: "архивов", 21: "архив", 112: "архивов"}
    for count, expected in cases.items():
        assert plural_ru(count, "архив", "архива", "архивов") == expected, count
