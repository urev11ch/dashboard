"""Тесты защиты локального API: доступ только с loopback, проверки Host/Origin."""
import asyncio

import pytest
from fastapi.responses import JSONResponse

import webapp.app as app


def _make_request(client, headers=None, method="GET"):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/api/workspace-data",
        "raw_path": b"/api/workspace-data",
        "query_string": b"",
        "root_path": "",
        "server": ("0.0.0.0", 8000),
        "client": client,
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in (headers or {}).items()
        ],
    }
    return app.Request(scope)


def _guard(request):
    async def call_next(_request):
        return JSONResponse({"ok": True})

    return asyncio.run(app.local_request_guard(request, call_next))


def test_loopback_client_is_allowed():
    response = _guard(_make_request(("127.0.0.1", 51234), {"host": "127.0.0.1:8000"}))
    assert response.status_code == 200

    response = _guard(_make_request(("::1", 51234), {"host": "localhost:8000"}))
    assert response.status_code == 200


def test_remote_client_is_rejected_even_with_local_host_header():
    # Заголовок Host задаёт клиент: при запуске на 0.0.0.0 любой в сети мог
    # прислать `Host: localhost` и получить полный доступ к API.
    response = _guard(_make_request(("192.168.1.77", 51234), {"host": "localhost:8000"}))
    assert response.status_code == 403

    response = _guard(_make_request(("10.0.0.5", 51234), {"host": "127.0.0.1:8000"}))
    assert response.status_code == 403


def test_remote_client_allowed_only_with_explicit_env(monkeypatch):
    request = _make_request(("192.168.1.77", 51234), {"host": "192.168.1.10:8000"})
    assert _guard(request).status_code == 403

    monkeypatch.setenv(app.ALLOW_REMOTE_ENV_VAR, "1")
    assert _guard(_make_request(("192.168.1.77", 51234), {"host": "192.168.1.10:8000"})).status_code == 200


def test_foreign_host_header_still_rejected_for_local_client():
    # Защита от DNS rebinding сохраняется.
    response = _guard(_make_request(("127.0.0.1", 51234), {"host": "evil.example.com"}))
    assert response.status_code == 403


def test_foreign_origin_rejected_on_write_requests():
    response = _guard(
        _make_request(
            ("127.0.0.1", 51234),
            {"host": "127.0.0.1:8000", "origin": "http://evil.example.com"},
            method="POST",
        )
    )
    assert response.status_code == 403

    # Без Origin (pywebview, curl) — пропускаем.
    response = _guard(
        _make_request(("127.0.0.1", 51234), {"host": "127.0.0.1:8000"}, method="POST")
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("127.0.0.1", True),
        ("127.5.5.5", True),
        ("::1", True),
        ("[::1]", True),
        ("192.168.1.10", False),
        ("0.0.0.0", False),
        ("localhost", False),  # имя, а не адрес: клиентский host всегда числовой
        ("", False),
    ],
)
def test_loopback_detection(value, expected):
    assert app._is_loopback_address(value) is expected


def test_missing_client_is_treated_as_local():
    # Нет TCP-пира (unix-сокет / внутренний транспорт) — удалённым быть не может.
    assert app.client_is_local(_make_request(None, {"host": "127.0.0.1:8000"})) is True
