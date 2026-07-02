"""Тесты общих настроек приложения, CSV-экспорта и триггера автообновления FTP."""
import asyncio
import json

import pytest

import webapp.app as app


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


# ---- настройки --------------------------------------------------------------
def test_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.load_app_settings() == {
        "ftp_auto_refresh_enabled": True,
        "ftp_auto_refresh_minutes": 5,
        "default_folder_path": "",
        "result_labels": {c: "" for c in app.RESULT_LABEL_CATEGORIES},
    }


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    saved = app.save_app_settings(
        {"ftp_auto_refresh_enabled": False, "ftp_auto_refresh_minutes": 12}
    )
    assert saved == {
        "ftp_auto_refresh_enabled": False,
        "ftp_auto_refresh_minutes": 12,
        "default_folder_path": "",
        "result_labels": {c: "" for c in app.RESULT_LABEL_CATEGORIES},
    }

    payload = json.loads(app.app_settings_path().read_text(encoding="utf-8"))
    assert payload["version"] == app.APP_SETTINGS_VERSION
    assert payload["settings"] == saved
    assert app.load_app_settings() == saved


def test_minutes_are_clamped(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.save_app_settings({"ftp_auto_refresh_minutes": 0})["ftp_auto_refresh_minutes"] == 1
    assert (
        app.save_app_settings({"ftp_auto_refresh_minutes": 99999})["ftp_auto_refresh_minutes"]
        == app.FTP_AUTO_REFRESH_MAX_MINUTES
    )


def test_invalid_minutes_fall_back(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.save_app_settings({"ftp_auto_refresh_minutes": "abc"})["ftp_auto_refresh_minutes"] == 5


def test_enabled_accepts_stringy_values(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.normalize_app_settings({"ftp_auto_refresh_enabled": "off"})["ftp_auto_refresh_enabled"] is False
    assert app.normalize_app_settings({"ftp_auto_refresh_enabled": "yes"})["ftp_auto_refresh_enabled"] is True


def test_load_ignores_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.app_settings_path().write_text("{ broken", encoding="utf-8")
    assert app.load_app_settings()["ftp_auto_refresh_minutes"] == 5


# ---- путь по умолчанию ------------------------------------------------------
def test_default_folder_path_defaults_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.load_app_settings()["default_folder_path"] == ""
    assert app.resolve_default_folder_path() == str(app.DATALOG_ROOT)


def test_default_folder_path_used_when_set(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.save_app_settings({"default_folder_path": "  /data/x  "})
    assert app.load_app_settings()["default_folder_path"] == "/data/x"
    assert app.resolve_default_folder_path() == "/data/x"
    # активная сессия и последний путь имеют приоритет над настройкой по умолчанию
    assert app.resolve_workspace_input_value(None, None) == "/data/x"


def test_settings_route_merges_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    asyncio.run(app.update_app_settings_route(_FakeRequest({"settings": {"default_folder_path": "/data/x"}})))
    asyncio.run(app.update_app_settings_route(_FakeRequest({"settings": {"ftp_auto_refresh_minutes": 9}})))
    result = app.load_app_settings()
    assert result["default_folder_path"] == "/data/x"
    assert result["ftp_auto_refresh_minutes"] == 9


# ---- подписи результата мойки -----------------------------------------------
def test_result_labels_default_empty_and_resolve_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    labels = app.load_app_settings()["result_labels"]
    assert labels == {c: "" for c in app.RESULT_LABEL_CATEGORIES}
    # пустая настройка -> стандартная подпись
    assert app.resolve_result_label("Завершено штатно", labels) == "Завершено штатно"


def test_result_labels_custom_override(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.save_app_settings({"result_labels": {"completed_clean": "OK", "check": "Проверить"}})
    labels = app.load_app_settings()["result_labels"]
    assert app.resolve_result_label("Завершено штатно", labels) == "OK"
    assert app.resolve_result_label("Требует проверки", labels) == "Проверить"
    # незаданная категория -> стандарт; неизвестная строка -> без изменений
    assert app.resolve_result_label("Завершено, были паузы", labels) == "Завершено, были паузы"
    assert app.resolve_result_label("прочее", labels) == "прочее"


def test_result_labels_length_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    long_value = "я" * 500
    saved = app.save_app_settings({"result_labels": {"check": long_value}})
    assert len(saved["result_labels"]["check"]) == app.RESULT_LABEL_MAX_LEN


# ---- дефолты оформления графика ---------------------------------------------
def test_chart_style_defaults():
    defaults = app.chart_style_defaults()
    ids = [item["id"] for item in defaults]
    assert ids == [
        "temperature_supply",
        "temperature_return",
        "concentration_return",
        "flow_supply",
    ]
    ret = next(item for item in defaults if item["id"] == "temperature_return")
    assert ret["lineStyle"] == "dashed"
    assert ret["color"] == "#dc2626"
    assert ret["label"]


# ---- триггер автообновления -------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_state():
    saved = app.state
    app.state = app.AppState()
    try:
        yield
    finally:
        app.state = saved


def test_trigger_skips_without_workspace():
    assert app.trigger_ftp_auto_refresh() is False


def test_trigger_skips_folder_source(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "start_workspace_job", lambda *a, **k: calls.append((a, k)))
    app.state.analysis = object()
    app.state.selected_root = tmp_path  # обычная папка, не FTP-профиль
    assert app.trigger_ftp_auto_refresh() is False
    assert calls == []


def test_trigger_skips_when_job_running(monkeypatch):
    monkeypatch.setattr(app, "start_workspace_job", lambda *a, **k: pytest.fail("не должно запускаться"))
    app.state.analysis = object()
    app.state.selected_root = app.DATALOG_ROOT / "panel1"
    app.state.workspace_job = app.WorkspaceJob(id="x", status="running")
    assert app.trigger_ftp_auto_refresh() is False


def test_trigger_starts_background_for_ftp_profile(monkeypatch):
    captured = {}

    def fake_start(candidate, *, display_target=None, background=False):
        captured["candidate"] = candidate
        captured["display_target"] = display_target
        captured["background"] = background

    monkeypatch.setattr(app, "start_workspace_job", fake_start)
    app.state.analysis = object()
    app.state.selected_root = app.DATALOG_ROOT / "panel1"
    app.state.selected_display_root = "FTP · Цех 1"

    assert app.trigger_ftp_auto_refresh() is True
    assert captured["background"] is True
    assert captured["display_target"] == "FTP · Цех 1"
    assert captured["candidate"] == (app.DATALOG_ROOT / "panel1").resolve()
