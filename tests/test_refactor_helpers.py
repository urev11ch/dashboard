"""Юнит-тесты общих хелперов, выделенных при разгрузке тех-долга:
parse_bool_flag (config), read_json_object (io_utils), raise_if_cancelled и
SOURCE_CANCELLED_MESSAGE (wash_report)."""
import logging

import pytest

import wash_report as core
from webapp import config
from webapp.io_utils import read_json_object


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("что-угодно", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
        ("", False),  # пустая строка при default=False
        (None, False),
    ],
)
def test_parse_bool_flag(value, expected):
    assert config.parse_bool_flag(value) is expected


def test_parse_bool_flag_default_for_empty_and_none():
    assert config.parse_bool_flag(None, default=True) is True
    assert config.parse_bool_flag("", default=True) is True
    assert config.parse_bool_flag("  ", default=True) is True
    # Непустая строка игнорирует default и идёт по набору.
    assert config.parse_bool_flag("off", default=True) is False


def test_read_json_object_missing_file_returns_empty(tmp_path):
    assert read_json_object(tmp_path / "нет.json") == {}


def test_read_json_object_valid(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text('{"a": 1, "b": "x"}', encoding="utf-8")
    assert read_json_object(path) == {"a": 1, "b": "x"}


def test_read_json_object_non_object_returns_empty(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert read_json_object(path) == {}


def test_read_json_object_corrupt_warns_only_when_asked(tmp_path, caplog):
    path = tmp_path / "broken.json"
    path.write_text("{не json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert read_json_object(path) == {}
    assert not caplog.records  # по умолчанию молча

    with caplog.at_level(logging.WARNING):
        assert read_json_object(path, warn_on_corrupt=True) == {}
    assert any("broken.json" in r.getMessage() for r in caplog.records)


def test_read_json_object_missing_is_silent_even_with_warn(tmp_path, caplog):
    # Отсутствие файла — нормальный первый запуск, не повод для предупреждения.
    with caplog.at_level(logging.WARNING):
        assert read_json_object(tmp_path / "absent.json", warn_on_corrupt=True) == {}
    assert not caplog.records


def test_raise_if_cancelled_raises_when_requested():
    with pytest.raises(core.AnalysisCancelledError) as exc:
        core.raise_if_cancelled(lambda: True)
    assert str(exc.value) == core.SOURCE_CANCELLED_MESSAGE


def test_raise_if_cancelled_noop_paths():
    # None-проверка и «отмены нет» не бросают.
    core.raise_if_cancelled(None)
    core.raise_if_cancelled(lambda: False)
