"""Тесты путей рантайма: логи и кэш не должны указывать в общий /tmp."""
import stat
import tempfile
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


def test_cache_root_is_private_user_directory(monkeypatch, tmp_path):
    # Кэш (в него пишется pickle) не должен жить в общем /tmp с предсказуемым
    # именем — иначе на многопользовательской машине это локальный RCE.
    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    root = runtime_paths.resolve_cache_root("wash_journal_analysis_cache")

    assert root == (
        tmp_path / "cache" / runtime_paths.LINUX_RUNTIME_DIRNAME / "wash_journal_analysis_cache"
    )
    assert root.is_dir()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert root != Path(tempfile.gettempdir()) / "wash_journal_analysis_cache"


def test_cache_root_defaults_to_home_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    root = runtime_paths.resolve_cache_root("wash_journal_archive_cache")
    assert root == (
        tmp_path / ".cache" / runtime_paths.LINUX_RUNTIME_DIRNAME / "wash_journal_archive_cache"
    )
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_cache_root_refuses_symlinked_directory(monkeypatch, tmp_path):
    # Подменённый симлинком каталог кэша не используем: уходим в приватный
    # временный каталог со случайным именем.
    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    attacker_dir = tmp_path / "attacker"
    attacker_dir.mkdir()
    cache_parent = tmp_path / "cache" / runtime_paths.LINUX_RUNTIME_DIRNAME
    cache_parent.mkdir(parents=True)
    (cache_parent / "wash_journal_analysis_cache").symlink_to(attacker_dir)

    root = runtime_paths.resolve_cache_root("wash_journal_analysis_cache")

    assert root != cache_parent / "wash_journal_analysis_cache"
    assert root.resolve() != attacker_dir.resolve()
    assert root.is_dir()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_runtime_root_fallback_is_not_shared_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    monkeypatch.delenv(runtime_paths.RUNTIME_ROOT_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    root = runtime_paths.resolve_runtime_root()
    assert root == tmp_path / ".local" / "state" / runtime_paths.LINUX_RUNTIME_DIRNAME
    # Раньше фолбэк вёл в общий /tmp/<имя> с предсказуемым именем.
    assert root != Path(tempfile.gettempdir()) / runtime_paths.LINUX_RUNTIME_DIRNAME
