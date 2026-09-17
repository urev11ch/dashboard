"""Выгрузка журнала моек в .xlsx.

Строка файла — одна мойка: время, объект, рецепт и режимные значения фаз
щёлочи и кислоты. Температура и концентрация фазы усредняются по рабочей полке
(`core.phase_working_averages`), а не по всей фазе: заполнение контура в начале и
вытеснение раствора водой в конце занижают среднее и делают колонки
несравнимыми между мойками.

Расход берётся из статистики цикла (средний за мойку) — она уже посчитана
анализом, поэтому колонка не требует чтения сэмплов.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, Sequence

import wash_report as core

from .settings_store import resolve_object_name
from .xlsx_writer import Column, build_workbook

EXPORT_SHEET_NAME = "Мойки"
EXPORT_FILENAME_PREFIX = "Мойки"

EXPORT_COLUMNS: tuple[Column, ...] = (
    Column("Время начала", "datetime", 19.0),
    Column("Время окончания", "datetime", 19.0),
    Column("Объект мойки", "text", 28.0),
    Column("Рецепт мойки", "text", 30.0),
    Column("Температура щёлочи, °C", "number", 21.0),
    Column("Концентрация щёлочи, %", "number", 21.0),
    Column("Температура кислоты, °C", "number", 21.0),
    Column("Концентрация кислоты, %", "number", 21.0),
    Column("Расход, м³/ч", "number", 14.0),
)


def _local_datetime(timestamp: float) -> datetime | str:
    """Метка времени в зоне сервера. Битую метку (архивы это умеют) отдаём
    строкой «н/д»: пустая ячейка выглядела бы как «данных не выгрузили»."""
    try:
        return datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return core.format_ts(timestamp)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def build_export_row(
    analysis: core.AnalysisResult,
    cycle: core.Cycle,
    settings: dict[str, Any],
    overrides: dict[tuple[int, int], str],
) -> list[Any]:
    norms = settings.get("concentration_norms") or {}
    tolerance = settings.get("concentration_tolerance_percent") or 0.0

    try:
        samples = core.analysis_samples_for_cycle(analysis, cycle)
    except core.SampleStreamUnavailable:
        # Поток вытеснен из кэша или побился: режимные колонки останутся
        # пустыми, но время, объект, рецепт и расход в строке честные — они
        # известны из самого анализа.
        logging.warning(
            "Сэмплы мойки недоступны, режимные колонки выгрузки пусты: канал=%s, ключ=%s",
            cycle.channel,
            core.make_cycle_key(cycle),
        )
        samples = []

    phase_values: list[float | None] = []
    for phase_key, process_id, _ in core.CONCENTRATION_PHASES:
        concentration, temperature = core.phase_working_averages(
            samples,
            process_id,
            norm=norms.get(phase_key),
            tolerance_percent=tolerance,
        )
        phase_values.extend([_rounded(temperature), _rounded(concentration)])

    return [
        _local_datetime(cycle.start_ts),
        _local_datetime(cycle.end_ts),
        resolve_object_name(cycle.channel, cycle.object_id, overrides),
        cycle.program_name,
        *phase_values,
        _rounded(cycle.flow_supply.average),
    ]


def build_export_rows(
    analysis: core.AnalysisResult,
    keys: Sequence[str],
    settings: dict[str, Any],
    overrides: dict[tuple[int, int], str],
) -> tuple[list[list[Any]], int]:
    """Строки выгрузки в порядке `keys` (то есть в порядке списка на экране).

    Возвращает `(строки, число пропущенных ключей)`. Ключ пропускается, если
    такой мойки в текущем анализе нет: между открытием списка и нажатием кнопки
    источник мог обновиться, и ронять всю выгрузку из-за устаревшего ключа
    незачем — пользователь получит остальные мойки и предупреждение.
    """
    cycles_by_key = {core.make_cycle_key(cycle): cycle for cycle in analysis.cycles}
    rows: list[list[Any]] = []
    missing = 0
    for key in keys:
        cycle = cycles_by_key.get(key)
        if cycle is None:
            missing += 1
            continue
        rows.append(build_export_row(analysis, cycle, settings, overrides))
    return rows, missing


def build_export_workbook(rows: Iterable[Sequence[Any]]) -> bytes:
    return build_workbook(EXPORT_COLUMNS, rows, sheet_name=EXPORT_SHEET_NAME)


def export_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M")
    return f"{EXPORT_FILENAME_PREFIX}_{stamp}.xlsx"
