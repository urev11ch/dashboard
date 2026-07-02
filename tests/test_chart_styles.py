"""Тесты серверного хранения стилей кривых графика (цвет + тип линии)."""
import json

import pytest

import webapp.app as app


def test_normalize_keeps_valid_entries():
    raw = {
        "temperature_return": {"color": "#DC2626", "lineStyle": "dashed"},
        "flow_supply": {"color": "#00ff00"},
    }
    result = app.normalize_chart_style_series(raw)
    assert result == {
        "temperature_return": {"color": "#dc2626", "lineStyle": "dashed"},
        "flow_supply": {"color": "#00ff00"},
    }


def test_normalize_drops_invalid_color_and_line_style():
    raw = {
        "a": {"color": "not-a-color", "lineStyle": "bogus"},
        "b": {"color": "#123456", "lineStyle": "bogus"},
        "": {"color": "#123456"},
        "c": "unexpected-type",
    }
    result = app.normalize_chart_style_series(raw)
    # entry `a` пустеет полностью и отбрасывается; `b` сохраняет только цвет.
    assert result == {"b": {"color": "#123456"}}


def test_normalize_non_dict_input():
    assert app.normalize_chart_style_series(None) == {}
    assert app.normalize_chart_style_series(["x"]) == {}


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    styles = {"temperature_return": {"color": "#dc2626", "lineStyle": "dashed"}}
    app.save_chart_style_settings(styles)

    saved_path = app.chart_style_settings_path()
    assert saved_path.parent == tmp_path
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    assert payload["version"] == app.CHART_STYLE_SETTINGS_VERSION
    assert payload["series"] == styles

    assert app.load_chart_style_settings() == styles


def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    assert app.load_chart_style_settings() == {}


def test_load_ignores_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    app.chart_style_settings_path().write_text("{ not json", encoding="utf-8")
    assert app.load_chart_style_settings() == {}


def test_get_endpoint_returns_saved_series(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path)
    styles = {"flow_supply": {"color": "#059669", "lineStyle": "dotted"}}
    app.save_chart_style_settings(styles)

    response = app.get_chart_styles()
    body = json.loads(bytes(response.body))
    assert body["series"] == styles
    # ответ также содержит стандартные оформления серий для панели настроек
    assert [item["id"] for item in body["defaults"]] == [
        "temperature_supply",
        "temperature_return",
        "concentration_return",
        "flow_supply",
    ]
