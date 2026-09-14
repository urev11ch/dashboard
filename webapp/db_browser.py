"""Просмотр содержимого `.db` панели: список таблиц и постраничная выдача строк.

Зачем отдельный модуль: анализ читает из архива только то, что нужно журналу
моек, а увидеть сырые данные (что именно записала панель, есть ли вообще строки,
какие значения у тегов) до сих пор было нечем — приходилось открывать файл
внешним SQLite-клиентом.

Безопасность. Эндпоинты локальные, но путь к базе приходит из браузера, поэтому:
  * читаем ТОЛЬКО файлы из текущего анализа (белый список `state.analysis`) —
    иначе `?path=/etc/passwd`-подобный запрос превратил бы приложение в читалку
    произвольных sqlite-файлов на машине;
  * соединение строго `mode=ro` (`core.connect_read_only`) — просмотр не должен
    ни создавать файл, ни писать в архив панели;
  * имя таблицы подставляется в SQL только после сверки со списком таблиц самой
    базы, идентификатор экранируется — параметром таблицу не передать.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import wash_report as core

# Потолок строк за один запрос: страница интерфейса, а не выгрузка целиком.
ROWS_PAGE_MAX = 500
ROWS_PAGE_DEFAULT = 100
# Длинные тексты/BLOB в ячейке режем — таблица на 10k строк иначе раздувает ответ.
CELL_TEXT_MAX = 300


def quote_identifier(name: str) -> str:
    """Идентификатор SQLite в двойных кавычках (внутренние кавычки удваиваются)."""
    return '"' + str(name).replace('"', '""') + '"'


def resolve_browsable_db(raw_path: str, allowed: list[Path]) -> Path:
    """Путь из запроса → файл из белого списка. Сравниваем разрешённые пути."""
    if not raw_path:
        raise ValueError("Не указан файл базы данных.")

    try:
        candidate = Path(raw_path).expanduser().resolve()
    except OSError as error:
        raise ValueError("Некорректный путь к базе данных.") from error

    for path in allowed:
        try:
            if Path(path).expanduser().resolve() == candidate:
                return candidate
        except OSError:
            continue
    raise ValueError("Эта база не входит в текущий анализ.")


def list_tables(db_path: Path) -> list[dict[str, Any]]:
    """Таблицы базы: имя, число строк, колонки. Служебные `sqlite_*` не показываем."""
    connection = core.connect_read_only(db_path)
    try:
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
            if row and row[0] is not None and not str(row[0]).startswith("sqlite_")
        ]

        tables: list[dict[str, Any]] = []
        for name in names:
            columns = [
                {"name": str(row[1]), "type": str(row[2] or "")}
                for row in connection.execute(f"PRAGMA table_info({quote_identifier(name)})")
                if row and len(row) > 2
            ]
            try:
                total = int(
                    connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(name)}").fetchone()[0]
                )
            except sqlite3.DatabaseError:
                # Повреждённая таблица не должна прятать остальные.
                total = -1
            tables.append({"name": name, "row_count": total, "columns": columns})
        return tables
    finally:
        connection.close()


def format_cell(value: Any) -> Any:
    """Значение ячейки для JSON: BLOB — как размер, длинный текст — с обрезкой."""
    if value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<BLOB, {len(bytes(value))} Б>"

    text = str(value)
    if len(text) > CELL_TEXT_MAX:
        return text[:CELL_TEXT_MAX] + "…"
    return text


def column_comments(connection: sqlite3.Connection) -> dict[str, str]:
    """`data_format_<i>` → название тега панели.

    Панель хранит подписи столбцов в отдельной таблице `data_format`
    («Концентрация возврата», «Температура подачи»…), поэтому сырой заголовок
    `data_format_3` сам по себе ничего не говорит."""
    try:
        rows = connection.execute("SELECT data_format_index, comment FROM data_format").fetchall()
    except sqlite3.DatabaseError:
        return {}

    labels: dict[str, str] = {}
    for row in rows:
        if not row or row[0] is None:
            continue
        comment = str(row[1] or "").strip()
        if comment:
            labels[f"data_format_{int(row[0])}"] = comment
    return labels


def read_rows(
    db_path: Path,
    table: str,
    *,
    offset: int = 0,
    limit: int = ROWS_PAGE_DEFAULT,
) -> dict[str, Any]:
    """Страница строк таблицы в естественном порядке хранения."""
    safe_offset = max(0, int(offset))
    safe_limit = max(1, min(ROWS_PAGE_MAX, int(limit)))

    connection = core.connect_read_only(db_path)
    try:
        known = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if row and row[0] is not None
        }
        if table not in known:
            raise ValueError("В этой базе нет такой таблицы.")

        quoted = quote_identifier(table)
        total = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
        cursor = connection.execute(f"SELECT * FROM {quoted} LIMIT ? OFFSET ?", (safe_limit, safe_offset))
        columns = [str(description[0]) for description in (cursor.description or [])]
        rows = [[format_cell(value) for value in row] for row in cursor.fetchall()]
        labels = column_comments(connection) if "data_format" in known else {}
        return {
            "table": table,
            "columns": columns,
            "column_labels": {name: labels[name] for name in columns if name in labels},
            "rows": rows,
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
        }
    finally:
        connection.close()
