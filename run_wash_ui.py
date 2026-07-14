#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import sys

import uvicorn


# Приложение не имеет аутентификации, поэтому по умолчанию слушаем только
# loopback. Нелокальный интерфейс — осознанное решение: OPTICIP_ALLOW_REMOTE
# (ту же переменную проверяет local_request_guard в webapp/app.py).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOW_REMOTE_ENV_VAR = "OPTICIP_ALLOW_REMOTE"


def _is_loopback(host: str) -> bool:
    # strip("[]") — адрес IPv6 может быть записан как [::1].
    return host.strip("[]").lower() in LOOPBACK_HOSTS


def remote_access_allowed() -> bool:
    # Значения-«выключатели» те же, что в webapp/app.py: пустое, 0, false, no, off.
    return str(os.environ.get(ALLOW_REMOTE_ENV_VAR) or "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def resolve_host() -> str:
    host = (os.environ.get("HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if _is_loopback(host):
        return host

    if not remote_access_allowed():
        print(
            f"HOST={host} открыл бы доступ к приложению из сети, а аутентификации у него нет.\n"
            "Допустимы только локальные адреса: 127.0.0.1, localhost, ::1.\n"
            f"Если удалённый доступ действительно нужен (VPN, доверенная сеть), задайте "
            f"{ALLOW_REMOTE_ENV_VAR}=1.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    print(
        f"ВНИМАНИЕ: {ALLOW_REMOTE_ENV_VAR}=1 — приложение слушает {host} без аутентификации. "
        "Ограничьте доступ файрволом.",
        file=sys.stderr,
    )
    return host


def main() -> None:
    host = resolve_host()
    try:
        port = int(os.environ.get("PORT") or 8765)
    except ValueError:
        print("Переменная окружения PORT должна быть числом.", file=sys.stderr)
        raise SystemExit(2)

    # Проверяем порт заранее: вместо непонятного traceback/выхода uvicorn —
    # понятное сообщение. Семейство адресов берём из getaddrinfo, иначе для ::1
    # проверка падала бы на AF_INET и врала про «занятый порт».
    try:
        family, socktype, proto, _canonname, sockaddr = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )[0]
    except socket.gaierror:
        print(f"Не удалось разрешить адрес HOST={host}.", file=sys.stderr)
        raise SystemExit(2)

    try:
        with socket.socket(family, socktype, proto) as probe:
            probe.bind(sockaddr)
    except OSError:
        print(
            f"Порт {port} занят — закройте другой экземпляр приложения "
            "или укажите другой порт: PORT=<номер>.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    uvicorn.run("webapp.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
