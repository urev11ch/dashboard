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

# У приложения нет аутентификации: слушаем только loopback. Нелокальный адрес
# (например, 0.0.0.0) — осознанное решение через OPTICIP_ALLOW_REMOTE=1;
# ту же переменную проверяют run_wash_ui.py и webapp/app.py.
case "${HOST}" in
    127.0.0.1 | localhost | ::1 | "[::1]") ;;
    *)
        if [ "${OPTICIP_ALLOW_REMOTE:-}" != "1" ]; then
            echo "!! HOST=${HOST} открыл бы доступ к приложению из сети, а аутентификации у него нет." >&2
            echo "!! Допустимы только 127.0.0.1, localhost, ::1." >&2
            echo "!! Если удалённый доступ действительно нужен — OPTICIP_ALLOW_REMOTE=1." >&2
            exit 2
        fi
        echo "!! ВНИМАНИЕ: слушаю ${HOST} без аутентификации (OPTICIP_ALLOW_REMOTE=1)." >&2
        ;;
esac

echo ">> Открой в браузере: http://${HOST}:${PORT}/"
# run_wash_ui.py читает HOST/PORT и понятно сообщает о занятом порте.
exec .venv/bin/python run_wash_ui.py
