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


def _strip_ipv6_brackets(host: str) -> str:
    # Оператор может записать IPv6-литерал в скобках ([::1]), но getaddrinfo и
    # uvicorn ждут его БЕЗ скобок (::1) — иначе getaddrinfo бросает gaierror на
    # адресе, который сам код объявляет допустимым. Снимаем скобки один раз.
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _is_loopback(host: str) -> bool:
    return _strip_ipv6_brackets(host).lower() in LOOPBACK_HOSTS


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
    # Нормализуем [::1] → ::1 сразу: дальше host уходит в getaddrinfo и uvicorn,
    # которые скобочную форму не принимают.
    host = _strip_ipv6_brackets(host)
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
    # Диапазон проверяем сами: getaddrinfo молча берёт порт по модулю 65536, из-за
    # чего проба bind-ила бы ДРУГОЙ порт, а uvicorn.run падал бы сырым OverflowError.
    if not 0 <= port <= 65535:
        print("Переменная окружения PORT должна быть в диапазоне 0–65535.", file=sys.stderr)
        raise SystemExit(2)

    # Проверяем порт заранее: вместо непонятного traceback/выхода uvicorn —
    # понятное сообщение. Перебираем ВСЕ адреса из getaddrinfo: при host=localhost
    # первым по RFC 6724 может прийти ::1, и при отключённом IPv6 проба падала бы,
    # хотя uvicorn поднялся бы на 127.0.0.1. Порт свободен, если связался хоть один.
    # PORT=0 (эфемерный порт) не проверяем — проба и uvicorn взяли бы разные порты.
    if port != 0:
        try:
            addr_infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            print(f"Не удалось разрешить адрес HOST={host}.", file=sys.stderr)
            raise SystemExit(2)

        last_error: OSError | None = None
        bound_any = False
        for family, socktype, proto, _canonname, sockaddr in addr_infos:
            try:
                with socket.socket(family, socktype, proto) as probe:
                    # SO_REUSEADDR — иначе после перезапуска сокет в TIME_WAIT
                    # ложно выглядит занятым.
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(sockaddr)
                bound_any = True
                break
            except OSError as error:
                last_error = error

        if not bound_any:
            print(
                f"Порт {port} занят — закройте другой экземпляр приложения "
                "или укажите другой порт: PORT=<номер>."
                + (f" ({last_error})" if last_error else ""),
                file=sys.stderr,
            )
            raise SystemExit(1)

    uvicorn.run("webapp.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
