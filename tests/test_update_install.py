"""Автообновление: выбор вложения и проверка скачанного установщика.

Фича качает .exe и запускает его с правами администратора, поэтому проверки
здесь — не формальность: ошибка означает исполнение чужого кода.
"""
import hashlib

import pytest

import webapp.app as app


def _asset(**overrides):
    body = {
        "name": app.UPDATE_ASSET_NAME,
        "browser_download_url": f"{app.UPDATE_ASSET_URL_PREFIX}v9.9.9/{app.UPDATE_ASSET_NAME}",
        "digest": "sha256:" + "a" * 64,
        "size": 1024,
    }
    body.update(overrides)
    return body


def _release(assets):
    return {"tag_name": "v9.9.9", "assets": assets}


def test_picks_installer_asset():
    picked = app._pick_installer_asset(_release([_asset()]))
    assert picked == {
        "url": f"{app.UPDATE_ASSET_URL_PREFIX}v9.9.9/{app.UPDATE_ASSET_NAME}",
        "size": 1024,
        "sha256": "a" * 64,
    }


def test_ignores_other_assets():
    other = _asset(name="OptiCIP-Dashboard.exe")
    assert app._pick_installer_asset(_release([other])) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/releases/download/v9.9.9/OptiCIP-Dashboard-Setup.exe",
        "http://github.com/urev11ch/dashboard/releases/download/v9.9.9/OptiCIP-Dashboard-Setup.exe",
        "https://github.com/someone-else/dashboard/releases/download/v9.9.9/OptiCIP-Dashboard-Setup.exe",
    ],
)
def test_rejects_foreign_download_url(url):
    # URL приходит из ответа GitHub, но доверять ему на слово нельзя: скачанное
    # запускается с админскими правами.
    assert app._pick_installer_asset(_release([_asset(browser_download_url=url)])) is None


@pytest.mark.parametrize(
    "digest",
    ["", None, "md5:" + "a" * 32, "sha256:" + "a" * 63, "sha256:", "deadbeef"],
)
def test_rejects_asset_without_valid_sha256(digest):
    # Без пригодной контрольной суммы проверить нечего — обновление недоступно.
    assert app._pick_installer_asset(_release([_asset(digest=digest)])) is None


@pytest.mark.parametrize("size", [0, -1, None, "1024"])
def test_rejects_bad_size(size):
    assert app._pick_installer_asset(_release([_asset(size=size)])) is None


def test_empty_release_payload():
    assert app._pick_installer_asset({}) is None
    assert app._release_tag({}) == ""


def test_release_tag_strips_v_prefix():
    assert app._release_tag({"tag_name": "v1.2.3"}) == "1.2.3"
    assert app._release_tag({"tag_name": "1.2.3"}) == "1.2.3"


def test_download_verifies_checksum(tmp_path, monkeypatch):
    """Подменённый файл не должен доехать до status=ready."""
    payload = b"x" * 2048
    real_sha = hashlib.sha256(payload).hexdigest()

    class FakeResponse:
        def __init__(self):
            self._data = payload
            self._pos = 0

        def read(self, size):
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(app.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(app, "_update_dir", lambda: tmp_path)

    job = app.UpdateJob(id="job-1", version="9.9.9", total=len(payload))
    with app.state_lock:
        app.state.update_job = job

    # Ожидаем ЧУЖУЮ сумму — как если бы файл подменили в пути.
    app.download_update_worker("job-1", {"url": "https://example.com/setup.exe", "size": len(payload), "sha256": "b" * 64}, "9.9.9")
    assert app.state.update_job.status == "error"
    assert "сумма" in (app.state.update_job.error or "").lower()
    assert app.state.update_job.path == ""
    # Частично скачанный файл не остаётся на диске.
    assert list(tmp_path.glob("*.part")) == []

    # А с настоящей суммой — доходит до ready.
    job2 = app.UpdateJob(id="job-2", version="9.9.9", total=len(payload))
    with app.state_lock:
        app.state.update_job = job2
    app.download_update_worker("job-2", {"url": "https://example.com/setup.exe", "size": len(payload), "sha256": real_sha}, "9.9.9")
    assert app.state.update_job.status == "ready"
    assert app.state.update_job.path


def test_download_removes_stale_installers(tmp_path, monkeypatch):
    """Установщики по ~22 МБ не должны копиться от версии к версии."""
    payload = b"y" * 512
    real_sha = hashlib.sha256(payload).hexdigest()

    class FakeResponse:
        def __init__(self):
            self._data = payload
            self._pos = 0

        def read(self, size):
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(app.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(app, "_update_dir", lambda: tmp_path)

    stale = tmp_path / "OptiCIP-Dashboard-Setup-1.0.0.exe"
    stale.write_bytes(b"old")

    with app.state_lock:
        app.state.update_job = app.UpdateJob(id="job-3", version="9.9.9", total=len(payload))
    app.download_update_worker(
        "job-3", {"url": "https://example.com/setup.exe", "size": len(payload), "sha256": real_sha}, "9.9.9"
    )

    assert app.state.update_job.status == "ready"
    assert not stale.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["OptiCIP-Dashboard-Setup-9.9.9.exe"]
