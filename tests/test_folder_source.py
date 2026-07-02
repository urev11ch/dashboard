"""Тесты запоминания последнего пути к папке и подстановки пути по умолчанию."""
import json
from pathlib import Path

import webapp.app as app


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.load_last_folder_path() == ""


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.save_last_folder_path("/data/archive")

    saved = app.folder_source_settings_path()
    assert saved.parent == tmp_path
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["version"] == app.FOLDER_SOURCE_SETTINGS_VERSION
    assert payload["last_path"] == "/data/archive"
    assert app.load_last_folder_path() == "/data/archive"


def test_save_blank_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.save_last_folder_path("/data/archive")
    app.save_last_folder_path("   ")
    assert app.load_last_folder_path() == "/data/archive"


def test_load_ignores_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.folder_source_settings_path().write_text("{ not json", encoding="utf-8")
    assert app.load_last_folder_path() == ""


def test_resolve_prefers_active_session(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.save_last_folder_path("/data/archive")
    # активный выбранный/ожидающий источник имеет приоритет над сохранённым
    assert app.resolve_workspace_input_value(Path("/active"), None) == "/active"
    assert app.resolve_workspace_input_value(None, Path("/pending")) == "/pending"


def test_resolve_uses_last_path_without_session(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.save_last_folder_path("/data/archive")
    assert app.resolve_workspace_input_value(None, None) == "/data/archive"


def test_resolve_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    # ничего не сохранено -> путь по умолчанию (datalog)
    assert app.resolve_workspace_input_value(None, None) == str(app.DATALOG_ROOT)
