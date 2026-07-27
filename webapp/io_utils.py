"""Мелкие утилиты ввода-вывода и форматирования без прикладной логики.

Атомарная запись файлов, форматирование меток времени и коротких списков имён.
Выделено из webapp/app.py; модуль-«лист» (только stdlib), его импортируют
и app.py, и сервисные модули.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json_object(path: Path, *, warn_on_corrupt: bool = False) -> dict[str, Any]:
    """Читает JSON-объект из файла. Возвращает dict; при отсутствии файла — {}
    (нормальный случай, первый запуск). При битом содержимом (не парсится или не
    объект) тоже {}, но с warn_on_corrupt=True пишет предупреждение в лог — иначе
    повреждённый файл настроек молча откатывался бы к дефолту без следа."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        if warn_on_corrupt:
            logging.warning("Не удалось прочитать %s — беру значения по умолчанию", path)
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        if warn_on_corrupt:
            logging.warning("Повреждён JSON в %s — сброс к значениям по умолчанию", path)
        return {}
    if not isinstance(payload, dict):
        if warn_on_corrupt:
            logging.warning("Ожидался JSON-объект в %s — сброс к значениям по умолчанию", path)
        return {}
    return payload


def format_source_label(value: str) -> str:
    return Path(value).name


def format_file_list(names: list[str], limit: int = 3) -> str:
    """Короткий перечень имён файлов для сообщения пользователю."""
    shown = ", ".join(f"`{name}`" for name in names[:limit])
    remainder = len(names) - limit
    return f"{shown} и ещё {remainder}" if remainder > 0 else shown


# Имя `.tmp` без уникального суффикса ломает атомарную запись, если запущено два
# экземпляра приложения (общие temp/ и кэш): один перезапишет чужой временный
# файл. Поэтому у каждой записи свой суффикс.
def atomic_write_bytes(path: Path, data: bytes) -> None:
    temp_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temp_path.open("wb") as handle:
            handle.write(data)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def local_tz_offset_min() -> int:
    """Смещение зоны сервера в минутах (с учётом летнего времени). Клиент считает
    границы суток по нему: `start_day` формируется в зоне сервера, а не браузера."""
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def format_day_key(timestamp: float) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(timestamp))
    except (OverflowError, OSError, ValueError):
        return ""
