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
