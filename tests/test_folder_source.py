"""Тесты запоминания последнего пути к папке и подстановки пути по умолчанию."""
import json
from pathlib import Path

import webapp.app as app


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(app.config, "TEMP_ROOT", tmp_path)
    assert app.load_last_folder_path() == ""


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(app.config, "TEMP_ROOT", tmp_path)
    app.save_last_folder_path("/data/archive")

    saved = app.folder_source_settings_path()
    assert saved.parent == tmp_path
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["version"] == app.FOLDER_SOURCE_SETTINGS_VERSION
    assert payload["last_path"] == "/data/archive"
    assert app.load_last_folder_path() == "/data/archive"


def test_save_blank_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(app.config, "TEMP_ROOT", tmp_path)
    app.save_last_folder_path("/data/archive")
    app.save_last_folder_path("   ")
    assert app.load_last_folder_path() == "/data/archive"


def test_load_ignores_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app.config, "TEMP_ROOT", tmp_path)
    app.folder_source_settings_path().write_text("{ not json", encoding="utf-8")
    assert app.load_last_folder_path() == ""
