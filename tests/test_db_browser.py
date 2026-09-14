"""Просмотр содержимого `.db`: белый список файлов, имена таблиц, пагинация."""
import sqlite3
from pathlib import Path

import pytest

from webapp import db_browser


def _make_db(path: Path, rows=((1, "a"), (2, "b"), (3, "c"))) -> Path:
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE data (id INTEGER, name TEXT)")
    connection.executemany("INSERT INTO data VALUES (?, ?)", rows)
    connection.commit()
    connection.close()
    return path


def test_lists_tables_with_row_counts_and_columns(tmp_path):
    db_path = _make_db(tmp_path / "Canal_1.db")
    tables = db_browser.list_tables(db_path)
    assert [table["name"] for table in tables] == ["data"]
    assert tables[0]["row_count"] == 3
    assert [column["name"] for column in tables[0]["columns"]] == ["id", "name"]


def test_reads_rows_by_pages(tmp_path):
    db_path = _make_db(tmp_path / "Canal_1.db")
    page = db_browser.read_rows(db_path, "data", offset=1, limit=1)
    assert page["columns"] == ["id", "name"]
    assert page["rows"] == [[2, "b"]]
    assert page["total"] == 3


def test_page_size_is_capped(tmp_path):
    db_path = _make_db(tmp_path / "Canal_1.db")
    page = db_browser.read_rows(db_path, "data", offset=-5, limit=10**6)
    assert page["limit"] == db_browser.ROWS_PAGE_MAX
    assert page["offset"] == 0


def test_unknown_table_is_rejected(tmp_path):
    db_path = _make_db(tmp_path / "Canal_1.db")
    with pytest.raises(ValueError):
        db_browser.read_rows(db_path, "secrets")


def test_table_name_cannot_inject_sql(tmp_path):
    # Имя таблицы нельзя передать параметром, поэтому оно обязано сверяться со
    # списком таблиц базы — иначе строка вроде `data"; DROP ...` попала бы в SQL.
    db_path = _make_db(tmp_path / "Canal_1.db")
    with pytest.raises(ValueError):
        db_browser.read_rows(db_path, 'data" UNION SELECT 1,2 --')
    assert db_browser.quote_identifier('da"ta') == '"da""ta"'


def test_only_files_from_the_analysis_can_be_opened(tmp_path):
    allowed = _make_db(tmp_path / "Canal_1.db")
    outsider = _make_db(tmp_path / "secret.db")

    assert db_browser.resolve_browsable_db(str(allowed), [allowed]) == allowed.resolve()
    for attempt in (str(outsider), "", "/etc/passwd", str(tmp_path / ".." / "Canal_1.db")):
        with pytest.raises(ValueError):
            db_browser.resolve_browsable_db(attempt, [allowed])


def test_reading_does_not_create_or_modify_files(tmp_path):
    db_path = _make_db(tmp_path / "Canal_1.db")
    before = db_path.stat().st_mtime_ns
    db_browser.read_rows(db_path, "data")
    assert db_path.stat().st_mtime_ns == before
    # Отсутствующий файл не должен создаваться соединением (mode=ro).
    missing = tmp_path / "Canal_9.db"
    with pytest.raises(sqlite3.DatabaseError):
        db_browser.list_tables(missing)
    assert not missing.exists()


def test_blob_and_long_text_cells_are_shortened():
    assert db_browser.format_cell(b"\x00\x01\x02") == "<BLOB, 3 Б>"
    long_text = "x" * (db_browser.CELL_TEXT_MAX + 50)
    shortened = db_browser.format_cell(long_text)
    assert len(shortened) == db_browser.CELL_TEXT_MAX + 1
    assert shortened.endswith("…")
    assert db_browser.format_cell(None) is None
    assert db_browser.format_cell(3.5) == 3.5
