"""Тесты FTP-конфигурации, хранения паролей и списка объектов."""
import pytest

import webapp.app as app


def test_apply_ftp_url_full():
    payload = app.apply_ftp_url_payload({"host": "ftp://uploadhis:111111@192.168.1.50/datalog"})
    assert payload["host"] == "192.168.1.50"
    assert payload["username"] == "uploadhis"
    assert payload["password"] == "111111"
    assert payload["path"] == "/datalog"


def test_apply_ftp_url_bare_credentials():
    payload = app.apply_ftp_url_payload({"host": "user:pw@10.0.0.7/dir", "path": ""})
    assert payload["host"] == "10.0.0.7"
    assert payload["username"] == "user"


def test_apply_ftp_url_plain_ip_untouched():
    payload = app.apply_ftp_url_payload({"host": "192.168.1.50", "username": "x"})
    assert payload["host"] == "192.168.1.50"
    assert payload["username"] == "x"


def test_normalize_ftp_defaults():
    cfg = app.normalize_ftp_connection_settings({"host": "127.0.0.1"})
    assert cfg["port"] == 21
    assert cfg["username"] == "anonymous"
    assert cfg["path"] == "/"
    assert cfg["passive"] is True


def test_normalize_ftp_rejects_empty_host():
    with pytest.raises(ValueError):
        app.normalize_ftp_connection_settings({"host": ""})


def test_normalize_ftp_rejects_bad_port():
    with pytest.raises(ValueError):
        app.normalize_ftp_connection_settings({"host": "h", "port": "70000"})


def test_secret_roundtrip():
    token = app.protect_secret("s3cret")
    assert token and token != "s3cret"  # не хранится в открытом виде
    assert app.unprotect_secret(token) == "s3cret"
    assert app.protect_secret("") == ""
    assert app.unprotect_secret("") == ""


def test_connection_id_is_stable_and_distinct():
    a = app.normalize_ftp_connection_settings({"host": "1.1.1.1", "username": "u", "path": "/d"})
    b = app.normalize_ftp_connection_settings({"host": "2.2.2.2", "username": "u", "path": "/d"})
    assert app.ftp_connection_id(a) == app.ftp_connection_id(a)
    assert app.ftp_connection_id(a) != app.ftp_connection_id(b)


def test_build_object_rows_lists_detected_objects():
    class _Ov:
        def __init__(self, ch, oid):
            self.channel = ch
            self.object_id = oid

    class _Analysis:
        overviews = [_Ov(1, 3), _Ov(1, 5), _Ov(1, 0)]  # 1:0 пропускается

    rows = app.build_object_rows({}, _Analysis())
    assert [(r["channel"], r["object_id"]) for r in rows] == [(1, 3), (1, 5)]

    rows2 = app.build_object_rows({(1, 3): "Танк 3"}, _Analysis())
    named = [r for r in rows2 if (r["channel"], r["object_id"]) == (1, 3)][0]
    assert named["object_name"] == "Танк 3" and named["is_json_name"] is True


# --- Инкрементальная загрузка по FTP -------------------------------------


def test_parse_ftp_timestamp():
    assert app._parse_ftp_timestamp("20240101120000") is not None
    # Более позднее время даёт больший epoch.
    assert app._parse_ftp_timestamp("20240101120000") < app._parse_ftp_timestamp("20240101120001")
    # Дробные секунды отбрасываются, но строка парсится.
    assert app._parse_ftp_timestamp("20240101120000.500") == app._parse_ftp_timestamp("20240101120000")
    # Мусор и слишком короткие строки — None.
    assert app._parse_ftp_timestamp("") is None
    assert app._parse_ftp_timestamp("notatime") is None
    assert app._parse_ftp_timestamp("2024") is None


def test_parse_mdtm_reply():
    expected = app._parse_ftp_timestamp("20240101120000")
    assert app._parse_mdtm_reply("213 20240101120000") == expected
    assert app._parse_mdtm_reply("20240101120000") == expected
    assert app._parse_mdtm_reply("550 Not Found") is None
    assert app._parse_mdtm_reply("") is None


def test_should_skip_download():
    ts = app._parse_ftp_timestamp("20240101120000")
    later = app._parse_ftp_timestamp("20240202120000")
    # Размер совпал, времени с панели нет — считаем неизменным.
    assert app._should_skip_download({"size": 10}, 10, None) is True
    # Размер совпал, локальная копия не старше панели — пропускаем.
    assert app._should_skip_download({"size": 10, "mtime": ts}, 10, ts) is True
    # Размер отличается — качаем.
    assert app._should_skip_download({"size": 10, "mtime": ts}, 20, ts) is False
    # На панели файл новее — качаем.
    assert app._should_skip_download({"size": 10, "mtime": ts}, 10, later) is False
    # Локальной копии нет — качаем.
    assert app._should_skip_download(None, 10, ts) is False
    # Размер неизвестен с панели — не рискуем, качаем.
    assert app._should_skip_download({"size": 10, "mtime": ts}, None, ts) is False


def test_build_local_archive_index(tmp_path):
    (tmp_path / "a.db").write_bytes(b"aaa")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.db").write_bytes(b"bbbb")
    (tmp_path / "note.txt").write_text("ignore me")

    index = app.build_local_archive_index(tmp_path)
    assert index["a.db"]["size"] == 3
    assert index["sub/b.db"]["size"] == 4
    # Ключ по имени+размеру для распознавания копий из старых папок.
    assert ("name", "a.db", 3) in index
    # Посторонние файлы не индексируются.
    assert "note.txt" not in index


class _FakeFTP:
    """Минимальный фейковый FTP: плоский каталог файлов с содержимым и mtime."""

    def __init__(self, files):
        # files: dict[имя] = {"data": bytes, "modify": "YYYYMMDDHHMMSS" | None}
        self.files = files
        self.retr_calls = []

    def voidcmd(self, cmd):
        return "200 OK"

    def mlsd(self, path):
        for name, info in self.files.items():
            facts = {"type": "file", "size": str(len(info["data"]))}
            if info.get("modify"):
                facts["modify"] = info["modify"]
            yield name, facts

    def size(self, path):
        return len(self.files[path.rsplit("/", 1)[-1]]["data"])

    def sendcmd(self, cmd):
        name = cmd.rsplit("/", 1)[-1]
        return "213 " + self.files[name]["modify"]

    def retrbinary(self, cmd, callback):
        name = cmd[len("RETR "):].rsplit("/", 1)[-1]
        self.retr_calls.append(name)
        callback(self.files[name]["data"])

    def quit(self):
        pass

    def close(self):
        pass


_FTP_CONFIG = {"host": "h", "port": 21, "username": "u", "password": "p", "path": "/datalog"}


def test_download_ftp_incremental(tmp_path, monkeypatch):
    files = {
        "a.db": {"data": b"aaa", "modify": "20240101120000"},
        "b.db": {"data": b"bbbb", "modify": "20240101120000"},
    }
    fake = _FakeFTP(files)
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    # Первый прогон — качаем всё.
    first = app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert sorted(fake.retr_calls) == ["a.db", "b.db"]
    assert len(first) == 2

    # Ничего не менялось — не качаем ничего.
    fake.retr_calls.clear()
    second = app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == []
    assert len(second) == 2

    # Вырос размер b.db — качаем только его.
    files["b.db"]["data"] = b"bbbbbbbb"
    fake.retr_calls.clear()
    third = app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == ["b.db"]
    assert len(third) == 2


def test_download_ftp_redownload_on_newer_mtime(tmp_path, monkeypatch):
    files = {"a.db": {"data": b"same", "modify": "20240101120000"}}
    fake = _FakeFTP(files)
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    app.download_ftp_files(_FTP_CONFIG, tmp_path)
    fake.retr_calls.clear()

    # Размер тот же, но время на панели новее — качаем заново.
    files["a.db"]["modify"] = "20240202120000"
    app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == ["a.db"]
