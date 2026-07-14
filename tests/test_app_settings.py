"""Тесты общих настроек приложения, CSV-экспорта и триггера автообновления FTP."""
import json

import pytest

import webapp.app as app


# ---- настройки --------------------------------------------------------------
def test_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.load_app_settings() == {
        "ftp_auto_refresh_enabled": True,
        "ftp_auto_refresh_minutes": 5,
        "default_folder_path": "",
        "result_labels": {c: "" for c in app.RESULT_LABEL_CATEGORIES},
        "check_updates": False,
        "autostart": False,
        "archive_retention_enabled": False,
        "archive_retention_days": 365,
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
        "check_updates": False,
        "autostart": False,
        "archive_retention_enabled": False,
        "archive_retention_days": 365,
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
    app.update_app_settings_route({"settings": {"default_folder_path": "/data/x"}})
    app.update_app_settings_route({"settings": {"ftp_auto_refresh_minutes": 9}})
    result = app.load_app_settings()
    assert result["default_folder_path"] == "/data/x"
    assert result["ftp_auto_refresh_minutes"] == 9


def test_concurrent_settings_updates_do_not_lose_changes(tmp_path, monkeypatch):
    """POST /api/settings — это read-modify-write: без общего лока параллельные
    запросы читают одно состояние и затирают изменения друг друга."""
    import threading
    import time as _time

    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.save_app_settings({})

    real_load = app.load_app_settings

    def slow_load():
        settings = real_load()
        _time.sleep(0.02)  # расширяем окно гонки между чтением и записью
        return settings

    monkeypatch.setattr(app, "load_app_settings", slow_load)

    updates = [
        {"ftp_auto_refresh_minutes": 7},
        {"default_folder_path": "/data/x"},
        {"archive_retention_days": 10},
        {"check_updates": True},
    ]
    threads = [
        threading.Thread(target=app.update_app_settings_route, args=({"settings": update},))
        for update in updates
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    monkeypatch.setattr(app, "load_app_settings", real_load)
    result = app.load_app_settings()
    # Ни одно из обновлений не потеряно.
    assert result["ftp_auto_refresh_minutes"] == 7
    assert result["default_folder_path"] == "/data/x"
    assert result["archive_retention_days"] == 10
    assert result["check_updates"] is True


# ---- подписи результата мойки -----------------------------------------------
def test_result_labels_default_empty_and_resolve_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    labels = app.load_app_settings()["result_labels"]
    assert labels == {c: "" for c in app.RESULT_LABEL_CATEGORIES}
    # пустая настройка -> стандартная подпись
    assert app.resolve_result_label("Завершено штатно", labels) == "Завершено штатно"


def test_result_labels_custom_override(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.save_app_settings({"result_labels": {"completed": "OK", "check": "Проверить"}})
    labels = app.load_app_settings()["result_labels"]
    assert app.resolve_result_label("Завершено штатно", labels) == "OK"
    assert app.resolve_result_label("Требует проверки", labels) == "Проверить"
    # варианты «были паузы» сводятся к тем же двум категориям
    assert app.resolve_result_label("Завершено, были паузы", labels) == "OK"
    assert app.resolve_result_label("Требует проверки, были паузы", labels) == "Проверить"
    # неизвестная строка -> без изменений
    assert app.resolve_result_label("прочее", labels) == "прочее"


def test_result_kind_mapping():
    assert app.resolve_result_kind("Завершено штатно") == "completed"
    assert app.resolve_result_kind("Завершено, были паузы") == "completed"
    assert app.resolve_result_kind("Требует проверки") == "check"
    assert app.resolve_result_kind("Требует проверки, были паузы") == "check"
    assert app.resolve_result_kind("прочее") == ""


def test_result_labels_length_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    long_value = "я" * 500
    saved = app.save_app_settings({"result_labels": {"check": long_value}})
    assert len(saved["result_labels"]["check"]) == app.RESULT_LABEL_MAX_LEN


# ---- сравнение версий (проверка обновлений) ---------------------------------
def test_is_newer_version():
    assert app._is_newer_version("1.0.1", "1.0.0") is True
    assert app._is_newer_version("v2.0", "1.9.9") is True
    assert app._is_newer_version("1.0.0", "1.0.0") is False
    assert app._is_newer_version("0.9", "1.0.0") is False
    assert app._is_newer_version("", "1.0.0") is False


# ---- хранение архивов (ретеншн) ---------------------------------------------
def test_archive_retention_days_clamped(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.save_app_settings({"archive_retention_days": 0})["archive_retention_days"] == 1
    assert app.save_app_settings({"archive_retention_days": 99999})["archive_retention_days"] == 730
    assert app.save_app_settings({"archive_retention_days": "abc"})["archive_retention_days"] == 365


def test_archive_month_folder():
    import time as _time

    ts = _time.mktime(_time.strptime("2026-05-15", "%Y-%m-%d"))
    assert app.archive_month_folder(ts) == "2026-05"
    assert app.archive_month_folder(None) == "unknown"


def test_cleanup_old_archives(tmp_path):
    import os
    import time as _time

    old = tmp_path / "2024-01" / "old.db"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x" * 10)
    fresh = tmp_path / "2026-07" / "fresh.db"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"y" * 20)
    service = tmp_path / "wash_object_names.json"  # не архив — не трогаем
    service.write_text("{}", encoding="utf-8")

    now = _time.time()
    os.utime(old, (now - 400 * 86400, now - 400 * 86400))
    os.utime(fresh, (now - 10 * 86400, now - 10 * 86400))

    result = app.cleanup_old_archives(tmp_path, 365)
    assert result == {"removed": 1, "freed_bytes": 10}
    assert not old.exists()
    assert fresh.exists()
    assert service.exists()
    # опустевшая папка месяца удалена
    assert not (tmp_path / "2024-01").exists()


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
