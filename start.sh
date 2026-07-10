#!/usr/bin/env bash
# Запуск веб-приложения «Отчёты по мойкам» (OptiCIP Dashboard).
# Создаёт venv при первом запуске, ставит зависимости и поднимает сервер.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo ">> Создаю виртуальное окружение (.venv)…"
    python3 -m venv .venv
fi

# Маркер успешной установки зависимостей: без него (первый запуск или
# прерванный pip install) либо при изменившемся requirements.txt ставим заново.
DEPS_MARKER=".venv/.deps-ok"
DEPS_STAMP="$(sha256sum requirements.txt)"
if [ ! -f "$DEPS_MARKER" ] || [ "$(cat "$DEPS_MARKER")" != "$DEPS_STAMP" ]; then
    echo ">> Устанавливаю зависимости…"
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
    printf '%s' "$DEPS_STAMP" > "$DEPS_MARKER"
fi

export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8765}"
echo ">> Открой в браузере: http://${HOST}:${PORT}/"
# run_wash_ui.py читает HOST/PORT и понятно сообщает о занятом порте.
exec .venv/bin/python run_wash_ui.py
