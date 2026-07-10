#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import sys

import uvicorn


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("PORT") or 8765)
    except ValueError:
        print("Переменная окружения PORT должна быть числом.", file=sys.stderr)
        raise SystemExit(2)

    # Проверяем порт заранее: вместо непонятного traceback/выхода uvicorn —
    # понятное сообщение.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
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
