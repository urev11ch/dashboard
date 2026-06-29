#!/usr/bin/env bash
# Запуск веб-приложения «Отчёты по мойкам» (OptiCIP Dashboard).
# Создаёт venv при первом запуске, ставит зависимости и поднимает сервер.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo ">> Создаю виртуальное окружение (.venv)…"
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
echo ">> Открой в браузере: http://${HOST}:${PORT}/"
exec .venv/bin/python -m uvicorn webapp.app:app --host "$HOST" --port "$PORT"
