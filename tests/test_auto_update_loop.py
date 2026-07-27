"""Фоновое автообновление (десктоп): решение «ставить/не ставить» в _auto_update_cycle.

HTTP-эндпоинты и сам запуск установщика замокированы — проверяем только логику
гейтов: выключено, идёт обработка, нет обновления, успех.
"""
import threading

import run_wash_desktop as desktop


class _Bridge:
    def __init__(self, result):
        self._result = result
        self.installed = 0

    def install_update(self):
        self.installed += 1
        return self._result


def _cycle(monkeypatch, *, enabled=True, busy=False, check=None, job_seq=None,
           install_result=None):
    monkeypatch.setattr(desktop, "_auto_update_enabled", lambda: enabled)
    monkeypatch.setattr(desktop, "_workspace_busy", lambda: busy)
    posts = []
    monkeypatch.setattr(desktop, "_local_post", lambda base, path, timeout=30.0: posts.append(path) or {})
    jobs = list(job_seq or [])

    def fake_get(base, path, timeout=10.0):
        if path == "/api/update-check":
            return check or {}
        if path == "/api/update/job":
            return jobs.pop(0) if jobs else {"status": "ready"}
        return {}

    monkeypatch.setattr(desktop, "_local_get_json", fake_get)
    bridge = _Bridge(install_result)
    desktop._auto_update_cycle(bridge, "http://127.0.0.1:1", threading.Event())
    return bridge, posts


def test_skips_when_disabled(monkeypatch):
    bridge, posts = _cycle(monkeypatch, enabled=False,
                           check={"update_available": True, "installable": True})
    assert bridge.installed == 0 and posts == []


def test_skips_when_busy(monkeypatch):
    bridge, posts = _cycle(monkeypatch, busy=True,
                           check={"update_available": True, "installable": True})
    assert bridge.installed == 0 and posts == []


def test_skips_when_no_update(monkeypatch):
    bridge, posts = _cycle(monkeypatch,
                           check={"update_available": False, "installable": False})
    assert bridge.installed == 0 and posts == []


def test_skips_when_not_installable(monkeypatch):
    # Обновление есть, но не устанавливается (веб-режим / нет установщика).
    bridge, posts = _cycle(monkeypatch,
                           check={"update_available": True, "installable": False})
    assert bridge.installed == 0 and posts == []


def test_installs_when_ready(monkeypatch):
    bridge, posts = _cycle(
        monkeypatch,
        check={"update_available": True, "installable": True, "latest": "9.9.9"},
        job_seq=[{"status": "running"}, {"status": "ready"}],
        install_result={"ok": True},
    )
    assert posts == ["/api/update/download"]
    assert bridge.installed == 1


def test_does_not_install_on_download_error(monkeypatch):
    bridge, posts = _cycle(
        monkeypatch,
        check={"update_available": True, "installable": True, "latest": "9.9.9"},
        job_seq=[{"status": "error", "error": "сумма не совпала"}],
    )
    assert posts == ["/api/update/download"]
    assert bridge.installed == 0
