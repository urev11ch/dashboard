"""Тесты путей рантайма: каталог логов не должен указывать в общий /tmp."""
from pathlib import Path

import runtime_paths


def test_log_root_on_windows_is_under_runtime_root(monkeypatch):
    monkeypatch.setattr(runtime_paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    assert runtime_paths.resolve_log_root() == (
        Path(r"C:\Users\test\AppData\Local") / runtime_paths.APP_DIRNAME / "logs"
    )


def test_log_root_on_linux_uses_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert runtime_paths.resolve_log_root() == (
        tmp_path / "state" / runtime_paths.LINUX_RUNTIME_DIRNAME / "logs"
    )


def test_log_root_on_linux_defaults_to_local_state(monkeypatch):
    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert runtime_paths.resolve_log_root() == (
        Path.home() / ".local" / "state" / runtime_paths.LINUX_RUNTIME_DIRNAME / "logs"
    )


def test_log_root_is_not_shared_tmp(monkeypatch):
    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    log_root = runtime_paths.resolve_log_root()
    assert log_root != Path("/tmp")
    assert log_root.parent != Path("/")
