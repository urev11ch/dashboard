#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

OBJECT_NAMES_FILENAME = "wash_object_names.json"
REQUIRED_DATA_COLUMNS = {
    "time@timestamp",
    "data_format_0",
    "data_format_1",
    "data_format_2",
    "data_format_3",
    "data_format_4",
    "data_format_5",
    "data_format_6",
    "data_format_7",
}

DEFAULT_MAX_GAP_SECONDS = 15.0

logger = logging.getLogger(__name__)

PROCESS_NAMES = {
    0: "Нет операций",
    1: "Ожидание предополаскивания вторичной водой",
    2: "Предополаскивание вторичной водой",
    3: "Ожидание предополаскивания чистой водой",
    4: "Предополаскивание чистой водой",
    5: "Ожидание мойки щелочью",
    6: "Мойка щелочью",
    7: "Ожидание ополаскивания 1",
    8: "Ополаскивание 1",
    9: "Ожидание мойки кислотой",
    10: "Мойка кислотой",
    11: "Ожидание ополаскивания 2",
    12: "Ополаскивание 2",
    13: "Ожидание химической дезинфекции",
    14: "Химическая дезинфекция",
    15: "Ожидание ополаскивания 3",
    16: "Ополаскивание 3",
    17: "Ожидание термической дезинфекции",
    18: "Термическая дезинфекция",
    19: "Ожидание ополаскивания 4",
    20: "Ополаскивание 4",
    21: "Окончание мойки",
    22: "Ожидание подготовки резервуара щелочи",
    23: "Подготовка резервуара щелочи",
    24: "Ожидание подготовки резервуара кислоты",
    25: "Подготовка резервуара кислоты",
    26: "Ожидание подготовки резервуара вторичной воды",
    27: "Подготовка резервуара вторичной воды",
    28: "Окончание подготовки резервуаров станции",
    29: "Ожидание мойки резервуара щелочи",
    30: "Мойка резервуара щелочи",
    31: "Ожидание мойки резервуара кислоты",
    32: "Мойка резервуара кислоты",
    33: "Ожидание мойки резервуара вторичной воды",
    34: "Мойка резервуара вторичной воды",
    35: "Ожидание мойки резервуара чистой воды",
    36: "Мойка резервуара чистой воды",
    37: "Окончание мойки резервуаров станции",
    50: "Аварийная пауза",
    55: "Пауза",
}

PROGRAM_NAMES = {
    0: "Нет программы",
    1: "Ополаскивание вторичной водой",
    2: "Ополаскивание чистой водой",
    3: "Мойка щелочью и кислотой",
    4: "Мойка щелочью",
    5: "Мойка кислотой",
    6: "Химическая дезинфекция",
    7: "Термическая дезинфекция",
}

WASH_PROCESS_CODES = set(range(1, 22)) | {50, 55}
NON_SUBSTANTIVE_PROCESS_CODES = {21, 28, 37, 50, 55}
PAUSE_PROCESS_NAMES = {PROCESS_NAMES[50], PROCESS_NAMES[55]}
COMPLETED_PROCESS_NAME = PROCESS_NAMES[21]

# Фазы мойки, в которых оценивается концентрация рабочего раствора.
ALKALI_PROCESS_ID = 6   # «Мойка щелочью»
ACID_PROCESS_ID = 10    # «Мойка кислотой»
# Стандартная строка вердикта, когда концентрация раствора ниже норматива.
CONCENTRATION_LOW_LABEL = "Концентрация ниже нормы"
# Вердикт, когда сэмплы мойки прочитать не удалось и оценивать нечем.
CONCENTRATION_UNAVAILABLE_LABEL = "Нет данных для оценки"

# Порядок и подписи оцениваемых фаз (для payload и UI).
CONCENTRATION_PHASES = (
    ("alkali", ALKALI_PROCESS_ID, "Щёлочь"),
    ("acid", ACID_PROCESS_ID, "Кислота"),
)

class AnalysisCancelledError(RuntimeError):
    pass


class SampleStreamUnavailable(RuntimeError):
    """Поток сэмплов канала не удалось прочитать: side-файл вытеснен из дискового
    кэша по бюджету, побился или не имеет валидной HMAC-подписи.

    Отдельный тип нужен, чтобы отличать это от «поток пуст». Пустой поток —
    законный результат (в мойке нет оцениваемых фаз), а недоступный означает, что
    судить о мойке НЕ ПО ЧЕМУ. Раньше загрузчик в обоих случаях возвращал [], и
    оценка концентрации на пустых сэмплах давала kind=None → вердикт оставался
    базовым: мойка с концентрацией ниже нормы показывалась как «Завершено
    штатно». Для журнала моек это тихая потеря брака, поэтому теперь загрузчик
    бросает, а вызывающий обязан решить, что делать."""

@dataclass(slots=True)
class Sample:
    # Метрики опциональны: NULL в архиве (обрыв связи панели с контроллером)
    # хранится как None и не должен попадать в статистику и на график.
    ts: float
    concentration_return: float | None
    temperature_return: float | None
    temperature_supply: float | None
    pressure_supply: float | None
    flow_supply: float | None
    process: int
    program: int
    object_id: int

@dataclass(slots=True)
class StatsBundle:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def merge(self, other: "StatsBundle") -> None:
        if other.count == 0:
            return
        self.count += other.count
        self.total += other.total
        self.minimum = other.minimum if self.minimum is None else min(self.minimum, other.minimum)
        self.maximum = other.maximum if self.maximum is None else max(self.maximum, other.maximum)

    @property
    def average(self) -> float | None:
        if self.count == 0:
            return None
        return self.total / self.count

@dataclass(slots=True)
class Segment:
    source_db: str
    channel: int
    object_id: int
    object_name: str
    program_id: int
    program_name: str
    process_id: int
    process_name: str
    start_ts: float
    end_ts: float
    sample_count: int
    concentration_return: StatsBundle
    temperature_return: StatsBundle
    temperature_supply: StatsBundle
    pressure_supply: StatsBundle
    flow_supply: StatsBundle

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)

@dataclass(slots=True)
class Cycle:
    source_db: str
    channel: int
    object_id: int
    object_name: str
    program_id: int
    program_name: str
    start_ts: float
    end_ts: float
    operations: list[str]
    sample_count: int
    concentration_return: StatsBundle
    temperature_return: StatsBundle
    temperature_supply: StatsBundle
    pressure_supply: StatsBundle
    flow_supply: StatsBundle

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)

@dataclass(slots=True)
class ObjectOverview:
    # Потребителям (список объектов, переименование) нужны только канал, номер
    # и имя объекта; метрики и счётчики по объекту никто не читает, а их сбор
    # требовал прохода по всем сэмплам архива.
    source_db: str
    channel: int
    object_id: int
    object_name: str
    start_ts: float
    end_ts: float

@dataclass(slots=True)
class AnalysisResult:
    db_files: list[Path]
    output_dir: Path
    max_gap_seconds: float
    segments: list[Segment]
    cycles: list[Cycle]
    overviews: list[ObjectOverview]
    # Сэмплы хранятся одним потоком на канал (мойки склеиваются через границу
    # суточных файлов), ключ потока — путь самого раннего архива канала.
    samples_by_db: dict[str, list[Sample]]
    channels_by_db: dict[str, int]
    sample_stream_by_channel: dict[int, str]
    sorted_cycles: list[Cycle]
    cycles_by_key: dict[str, Cycle]
    segments_by_cycle_key: dict[str, list[Segment]]
    sample_ranges_by_cycle_key: dict[str, tuple[int, int]]
    cycle_results_by_key: dict[str, str]
    analysis_cache_key: str = ""
    # Ленивый загрузчик потока сэмплов по ключу потока. Если задан, а samples_by_db
    # для потока пуст — сэмплы подтягиваются с диска по запросу (график/оценка
    # концентрации), а не держатся все в RAM. Не сериализуется (см. __getstate__).
    sample_loader: Callable[[str], list["Sample"]] | None = None

    def __getstate__(self) -> dict[str, Any]:
        # sample_loader — замыкание над дисковым кэшем, не пиклится.
        return {name: getattr(self, name) for name in self.__slots__ if name != "sample_loader"}

    def __setstate__(self, state: dict[str, Any]) -> None:
        for name in self.__slots__:
            setattr(self, name, state.get(name))

@dataclass(slots=True)
class DbAnalysisChunk:
    db_path: Path
    channel: int
    # Только точки внутри моек (с запасом на границах): держать весь архив в
    # памяти и в pickle-кэше незачем — статистика и график читают лишь их.
    samples: list[Sample]
    segments: list[Segment]
    cycles: list[Cycle]
    objects: list[ObjectOverview]

ProgressCallback = Callable[[dict[str, object]], None]

def make_cycle_key(cycle: Cycle) -> str:
    return "::".join(
        [
            cycle.source_db,
            str(cycle.channel),
            str(cycle.object_id),
            str(cycle.program_id),
            str(int(cycle.start_ts)),
            str(int(cycle.end_ts)),
        ]
    )

def emit_progress(
    progress_callback: ProgressCallback | None,
    *,
    phase: str,
    message: str,
    current: int | None = None,
    total: int | None = None,
    item: str = "",
) -> None:
    if progress_callback is None:
        return
    # Не заданные вызывающим current/total не попадают в событие: получатель
    # сохраняет прежние значения, и прогресс-бар не сбрасывается к нулю.
    payload: dict[str, object] = {
        "phase": phase,
        "message": message,
        "item": item,
    }
    if current is not None:
        payload["current"] = current
    if total is not None:
        payload["total"] = total
    progress_callback(payload)

def build_cycle_segment_index(
    cycles: Sequence[Cycle],
    segments: Sequence[Segment],
) -> dict[str, list[Segment]]:
    # Файл-источник в ключ маршрута не входит: мойка, разрезанная границей
    # суточного архива, состоит из сегментов разных файлов.
    route_to_cycles: dict[tuple[int, int, int], list[Cycle]] = defaultdict(list)
    route_to_segments: dict[tuple[int, int, int], list[Segment]] = defaultdict(list)

    for cycle in cycles:
        route_to_cycles[(cycle.channel, cycle.object_id, cycle.program_id)].append(cycle)
    for segment in segments:
        route_to_segments[(segment.channel, segment.object_id, segment.program_id)].append(segment)

    indexed: dict[str, list[Segment]] = {}
    for route, route_cycles in route_to_cycles.items():
        route_segments = route_to_segments.get(route, [])
        route_cycles = sorted(route_cycles, key=lambda item: item.start_ts)
        route_segments = sorted(route_segments, key=lambda item: item.start_ts)
        segment_cursor = 0

        for cycle in route_cycles:
            while segment_cursor < len(route_segments) and route_segments[segment_cursor].end_ts < cycle.start_ts:
                segment_cursor += 1

            probe = segment_cursor
            cycle_segments: list[Segment] = []
            while probe < len(route_segments):
                segment = route_segments[probe]
                if segment.start_ts > cycle.end_ts:
                    break
                if cycle.start_ts <= segment.start_ts and segment.end_ts <= cycle.end_ts:
                    cycle_segments.append(segment)
                probe += 1

            indexed[make_cycle_key(cycle)] = cycle_segments
            segment_cursor = max(segment_cursor, probe)

    return indexed

def build_cycle_sample_range_index(
    cycles: Sequence[Cycle],
    samples_by_db: dict[str, list[Sample]],
    sample_stream_by_channel: Mapping[int, str],
) -> dict[str, tuple[int, int]]:
    timestamps_by_stream = {
        stream_key: [sample.ts for sample in samples]
        for stream_key, samples in samples_by_db.items()
    }
    indexed: dict[str, tuple[int, int]] = {}
    for cycle in cycles:
        stream_key = sample_stream_by_channel.get(cycle.channel, "")
        timestamps = timestamps_by_stream.get(stream_key, [])
        start_index = bisect_left(timestamps, cycle.start_ts)
        end_index = bisect_right(timestamps, cycle.end_ts)
        indexed[make_cycle_key(cycle)] = (start_index, end_index)
    return indexed

def build_cycle_result_index(
    cycles: Sequence[Cycle],
    segments: Sequence[Segment],
    segments_by_cycle_key: Mapping[str, Sequence[Segment]] | None = None,
) -> dict[str, str]:
    segment_index = (
        dict(segments_by_cycle_key)
        if segments_by_cycle_key is not None
        else build_cycle_segment_index(cycles, segments)
    )
    results: dict[str, str] = {}
    for cycle in cycles:
        key = make_cycle_key(cycle)
        cycle_segments = segment_index.get(key, ())
        results[key] = (
            cycle_result_label(cycle_segments)
            if cycle_segments
            else cycle_result_label_from_operations(cycle.operations)
        )
    return results

def analysis_segments_for_cycle(analysis: AnalysisResult, cycle: Cycle) -> Sequence[Segment]:
    return analysis.segments_by_cycle_key.get(make_cycle_key(cycle), ())

def stream_samples(analysis: AnalysisResult, stream_key: str) -> list[Sample]:
    """Поток сэмплов канала: из RAM, если резидентен, иначе через ленивый
    загрузчик (с диска). Пустой список — если потока нет ни там, ни там.

    Загрузчик может бросить SampleStreamUnavailable (файл вытеснен/побился) —
    исключение намеренно пробрасывается: подменить его пустым списком значит
    выдать «данных нет» за «оценивать нечего»."""
    samples = analysis.samples_by_db.get(stream_key)
    if samples is None and analysis.sample_loader is not None and stream_key:
        samples = analysis.sample_loader(stream_key)
    return samples or []

def analysis_samples_for_cycle(analysis: AnalysisResult, cycle: Cycle) -> list[Sample]:
    cycle_key = make_cycle_key(cycle)
    stream_key = analysis.sample_stream_by_channel.get(cycle.channel, "")
    samples = stream_samples(analysis, stream_key)
    start_index, end_index = analysis.sample_ranges_by_cycle_key.get(cycle_key, (0, 0))
    if start_index >= end_index:
        return []

    return [
        sample
        for sample in samples[start_index:end_index]
        if is_wash_sample(sample)
        and sample.object_id == cycle.object_id
        and sample.program == cycle.program_id
    ]

def infer_channel(db_path: Path) -> int:
    match = re.search(r"Canal[_-]?(\d+)", db_path.name, flags=re.IGNORECASE)
    if not match:
        raise SystemExit(
            f"Не удалось определить номер канала по имени файла {db_path.name}. "
            "Ожидается шаблон вроде Canal_1_*.db."
        )
    return int(match.group(1))

def sqlite_read_only_uri(db_path: Path | str) -> str:
    """URI для открытия архива строго на чтение: обычный sqlite3.connect
    открывает файл на запись и создаёт его, если файла нет.

    Спецсимволы sqlite URI (`%`, `?`, `#`) в пути экранируем по документации
    https://www.sqlite.org/uri.html."""
    text = str(db_path)
    if os.name == "nt":
        # На Windows sqlite ожидает в URI прямые слэши.
        text = text.replace("\\", "/")
    text = text.replace("%", "%25").replace("?", "%3f").replace("#", "%23")
    return f"file:{text}?mode=ro"

def connect_read_only(db_path: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(sqlite_read_only_uri(db_path), uri=True)

def broken_db_exit(db_path: Path, error: Exception) -> SystemExit:
    """Битый/обрезанный архив (скачан не до конца, повреждён на флешке) не
    должен ронять разбор всего источника: вызывающий код ловит SystemExit и
    просто пропускает файл — как при отсутствующей таблице `data`."""
    return SystemExit(
        f"Файл {Path(db_path).name} повреждён или не является базой SQLite: {error}."
    )

def preflight_db_file(db_path: Path) -> int:
    channel = infer_channel(db_path)
    try:
        connection = connect_read_only(db_path)
    except sqlite3.DatabaseError as error:
        raise broken_db_exit(db_path, error) from error

    try:
        try:
            has_data_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'data' LIMIT 1"
            ).fetchone()
            if has_data_table is None:
                raise SystemExit(f"Файл {db_path.name} не содержит таблицу `data`.")

            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(data)")
                if row and len(row) > 1 and row[1]
            }
        except sqlite3.DatabaseError as error:
            raise broken_db_exit(db_path, error) from error

        missing_columns = sorted(REQUIRED_DATA_COLUMNS.difference(columns))
        if missing_columns:
            raise SystemExit(
                f"Файл {db_path.name} не содержит обязательные поля: {', '.join(missing_columns)}."
            )
    finally:
        connection.close()
    return channel

def fallback_object_name(object_id: int) -> str:
    return f"Объект {object_id}"

def object_name_override_key(channel: int, object_id: int) -> str:
    return f"{channel}:{object_id}"

def parse_object_name_override_key(raw_key: str) -> tuple[int, int] | None:
    parts = str(raw_key).split(":", 1)
    if len(parts) != 2:
        return None

    try:
        channel = int(parts[0])
        object_id = int(parts[1])
    except ValueError:
        return None

    if channel <= 0 or object_id < 0:
        return None
    return channel, object_id

def load_object_name_overrides_from_file(path: Path) -> dict[tuple[int, int], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        # Битый файл переименований молча терял бы ВСЕ пользовательские имена
        # объектов — оставляем след в логе, чтобы причину было видно.
        logger.warning(
            "Не удалось прочитать файл имён объектов %s: %s", path, error
        )
        return {}

    if not isinstance(payload, dict):
        return {}

    raw_objects = payload.get("objects")
    if not isinstance(raw_objects, dict):
        return {}

    overrides: dict[tuple[int, int], str] = {}
    for raw_key, raw_value in raw_objects.items():
        parsed_key = parse_object_name_override_key(str(raw_key))
        if parsed_key is None:
            continue

        value = str(raw_value or "").strip()
        if not value:
            continue
        overrides[parsed_key] = value

    return overrides

def find_nearest_object_names_file(db_path: Path) -> Path | None:
    for parent in [db_path.parent, *db_path.parents]:
        candidate = parent / OBJECT_NAMES_FILENAME
        if candidate.is_file():
            return candidate.resolve()
    return None

def load_object_name_overrides_for_db_files(
    db_files: Sequence[Path],
    *,
    object_names_file: Path | str | None = None,
) -> dict[tuple[int, int], str]:
    if object_names_file is not None:
        explicit_path = Path(object_names_file).expanduser().resolve()
        return load_object_name_overrides_from_file(explicit_path)

    merged_overrides: dict[tuple[int, int], str] = {}
    seen_files: set[Path] = set()
    for db_path in db_files:
        candidate = find_nearest_object_names_file(db_path.resolve())
        if candidate is None or candidate in seen_files:
            continue
        seen_files.add(candidate)
        merged_overrides.update(load_object_name_overrides_from_file(candidate))
    return merged_overrides

def name_for_program(program_id: int) -> str:
    return PROGRAM_NAMES.get(program_id, f"Программа {program_id}")

def name_for_process(process_id: int) -> str:
    return PROCESS_NAMES.get(process_id, f"Операция {process_id}")

def format_ts(timestamp: float) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "н/д"

def source_key(path: Path | str) -> str:
    return str(Path(path).expanduser().resolve())

def optional_metric(value: object) -> float | None:
    """NULL из архива не превращаем в 0.0 — иначе он попадает в min/avg."""
    if value is None:
        return None
    return float(value)

def concentration_metric(value: object) -> float | None:
    """Датчик концентрации на нуле шумит в минус. Клипаем один раз, на разборе
    строки: иначе график (клипал у себя) и статистика (брала сырое значение)
    показывали разное — кривая на нуле при «мин. −0.40 %»."""
    numeric = optional_metric(value)
    if numeric is None:
        return None
    return max(numeric, 0.0)

def filled_metric_count(sample: Sample) -> int:
    return sum(
        value is not None
        for value in (
            sample.concentration_return,
            sample.temperature_return,
            sample.temperature_supply,
            sample.pressure_supply,
            sample.flow_supply,
        )
    )

def is_wash_sample(sample: Sample) -> bool:
    return (
        sample.process in WASH_PROCESS_CODES
        and sample.program > 0
        and sample.object_id > 0
    )

def read_samples(
    db_path: Path,
    *,
    batch_size: int = 2000,
    cancel_check: Callable[[], bool] | None = None,
) -> list[Sample]:
    query = """
        SELECT
            [time@timestamp],
            data_format_0,
            data_format_1,
            data_format_2,
            data_format_3,
            data_format_4,
            data_format_5,
            data_format_6,
            data_format_7
        FROM data
        WHERE [time@timestamp] IS NOT NULL
        ORDER BY [time@timestamp]
    """
    try:
        connection = connect_read_only(db_path)
    except sqlite3.DatabaseError as error:
        raise broken_db_exit(db_path, error) from error

    try:
        samples: list[Sample] = []
        skipped_rows = 0
        try:
            cursor = connection.execute(query)
            while True:
                if cancel_check is not None and cancel_check():
                    raise AnalysisCancelledError("Обработка базы была отменена пользователем.")

                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break

                for row in rows:
                    # Битую строку (нечисловые значения) пропускаем, файл
                    # продолжаем анализировать дальше.
                    try:
                        sample = Sample(
                            ts=float(row[0]),
                            concentration_return=concentration_metric(row[1]),
                            temperature_return=optional_metric(row[2]),
                            temperature_supply=optional_metric(row[3]),
                            pressure_supply=optional_metric(row[4]),
                            flow_supply=optional_metric(row[5]),
                            process=int(float(row[6] or 0)),
                            program=int(float(row[7] or 0)),
                            object_id=int(float(row[8] or 0)),
                        )
                    except (ValueError, TypeError):
                        skipped_rows += 1
                        continue
                    samples.append(sample)
        except sqlite3.DatabaseError as error:
            # Повреждение страницы всплывает только на fetch — до этого места
            # файл выглядел исправным.
            raise broken_db_exit(db_path, error) from error
    finally:
        connection.close()
    if skipped_rows:
        logger.warning(
            "Файл %s: пропущено %d строк с нечисловыми значениями.",
            Path(db_path).name,
            skipped_rows,
        )
    return samples

def new_metrics() -> dict[str, StatsBundle]:
    return {
        "concentration_return": StatsBundle(),
        "temperature_return": StatsBundle(),
        "temperature_supply": StatsBundle(),
        "pressure_supply": StatsBundle(),
        "flow_supply": StatsBundle(),
    }

def add_sample_to_metrics(metrics: dict[str, StatsBundle], sample: Sample) -> None:
    # None (NULL в архиве) в статистику не добавляем.
    if sample.concentration_return is not None:
        metrics["concentration_return"].add(sample.concentration_return)
    if sample.temperature_return is not None:
        metrics["temperature_return"].add(sample.temperature_return)
    if sample.temperature_supply is not None:
        metrics["temperature_supply"].add(sample.temperature_supply)
    if sample.pressure_supply is not None:
        metrics["pressure_supply"].add(sample.pressure_supply)
    if sample.flow_supply is not None:
        metrics["flow_supply"].add(sample.flow_supply)

def median_sample_period(samples: Sequence[Sample], max_gap_seconds: float) -> float:
    """Медианный период логирования потока. Разрывы больше max_gap_seconds
    (простой панели) в расчёт не берём — иначе медиана уедет."""
    deltas = [
        second.ts - first.ts
        for first, second in zip(samples, samples[1:])
        if 0.0 < second.ts - first.ts <= max_gap_seconds
    ]
    if not deltas:
        return 0.0
    deltas.sort()
    middle = len(deltas) // 2
    # Настоящая медиана: на чётной выборке — среднее двух центральных, иначе
    # период систематически смещён вверх (брали верхний из пары).
    if len(deltas) % 2 == 0:
        return (deltas[middle - 1] + deltas[middle]) / 2.0
    return deltas[middle]

def build_segments(
    samples: Sequence[Sample],
    db_path: Path | str,
    channel: int,
    *,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
) -> list[Segment]:
    segments: list[Segment] = []
    source_db = source_key(db_path)
    # Если период определить не по чему (один сэмпл на весь поток), берём 1 с:
    # операция из одной точки всё равно длилась хотя бы период логирования.
    period = median_sample_period(samples, max_gap_seconds) or 1.0

    def flush(start_index: int, end_index: int) -> None:
        run = samples[start_index : end_index + 1]
        first = run[0]
        last = run[-1]

        # Конец операции — метка следующего сэмпла, а не последнего своего:
        # иначе длительность каждой операции занижена на период логирования,
        # операция из одного сэмпла длится 0 секунд, а на графике между
        # полосами операций появляются щели.
        next_index = end_index + 1
        if next_index < len(samples) and samples[next_index].ts - last.ts <= max_gap_seconds:
            end_ts = samples[next_index].ts
        else:
            end_ts = last.ts + period

        metrics = new_metrics()
        for sample in run:
            add_sample_to_metrics(metrics, sample)

        segments.append(
            Segment(
                source_db=source_db,
                channel=channel,
                object_id=first.object_id,
                object_name=fallback_object_name(first.object_id),
                program_id=first.program,
                program_name=name_for_program(first.program),
                process_id=first.process,
                process_name=name_for_process(first.process),
                start_ts=first.ts,
                end_ts=end_ts,
                sample_count=len(run),
                concentration_return=metrics["concentration_return"],
                temperature_return=metrics["temperature_return"],
                temperature_supply=metrics["temperature_supply"],
                pressure_supply=metrics["pressure_supply"],
                flow_supply=metrics["flow_supply"],
            )
        )

    run_start: int | None = None
    run_key: tuple[int, int, int] | None = None

    for index, sample in enumerate(samples):
        if not is_wash_sample(sample):
            if run_start is not None:
                flush(run_start, index - 1)
                run_start = None
                run_key = None
            continue

        sample_key = (sample.process, sample.program, sample.object_id)
        if run_start is None:
            run_start = index
            run_key = sample_key
        elif sample_key != run_key:
            flush(run_start, index - 1)
            run_start = index
            run_key = sample_key

    if run_start is not None:
        flush(run_start, len(samples) - 1)
    return segments

def build_cycles(segments: Sequence[Segment], max_gap_seconds: float) -> list[Cycle]:
    cycles: list[Cycle] = []

    current_segments: list[Segment] = []

    def flush() -> None:
        nonlocal current_segments
        if not current_segments:
            return
        if not any(segment.process_id not in NON_SUBSTANTIVE_PROCESS_CODES for segment in current_segments):
            current_segments = []
            return

        first = current_segments[0]
        last = current_segments[-1]
        concentration_return = StatsBundle()
        temperature_return = StatsBundle()
        temperature_supply = StatsBundle()
        pressure_supply = StatsBundle()
        flow_supply = StatsBundle()
        operations: list[str] = []
        sample_count = 0

        for segment in current_segments:
            concentration_return.merge(segment.concentration_return)
            temperature_return.merge(segment.temperature_return)
            temperature_supply.merge(segment.temperature_supply)
            pressure_supply.merge(segment.pressure_supply)
            flow_supply.merge(segment.flow_supply)
            operations.append(segment.process_name)
            sample_count += segment.sample_count

        cycles.append(
            Cycle(
                source_db=first.source_db,
                channel=first.channel,
                object_id=first.object_id,
                object_name=first.object_name,
                program_id=first.program_id,
                program_name=first.program_name,
                start_ts=first.start_ts,
                end_ts=last.end_ts,
                operations=operations,
                sample_count=sample_count,
                concentration_return=concentration_return,
                temperature_return=temperature_return,
                temperature_supply=temperature_supply,
                pressure_supply=pressure_supply,
                flow_supply=flow_supply,
            )
        )
        current_segments = []

    for segment in segments:
        if not current_segments:
            current_segments.append(segment)
            continue

        previous = current_segments[-1]
        # Файл-источник в маршрут не входит: мойка 23:50→00:20 лежит в двух
        # суточных архивах одного канала и должна остаться одним циклом.
        same_route = (
            segment.channel == previous.channel
            and segment.object_id == previous.object_id
            and segment.program_id == previous.program_id
        )
        close_enough = segment.start_ts - previous.end_ts <= max_gap_seconds

        if same_route and close_enough:
            current_segments.append(segment)
        else:
            flush()
            current_segments.append(segment)

    flush()
    return cycles

def collect_object_overviews(
    samples: Sequence[Sample],
    db_path: Path | str,
    channel: int,
) -> list[ObjectOverview]:
    """Список объектов, встреченных в архиве. Проход по сэмплам — один, без
    промежуточных множеств размером во весь архив: потребителям нужны только
    канал, номер и имя объекта (плюс время для сортировки)."""
    source_db = source_key(db_path)
    bounds: dict[int, list[float]] = {}
    for sample in samples:
        if sample.object_id <= 0:
            continue
        entry = bounds.get(sample.object_id)
        if entry is None:
            bounds[sample.object_id] = [sample.ts, sample.ts]
            continue
        if sample.ts < entry[0]:
            entry[0] = sample.ts
        if sample.ts > entry[1]:
            entry[1] = sample.ts

    return [
        ObjectOverview(
            source_db=source_db,
            channel=channel,
            object_id=object_id,
            object_name=fallback_object_name(object_id),
            start_ts=start_ts,
            end_ts=end_ts,
        )
        for object_id, (start_ts, end_ts) in sorted(bounds.items())
    ]

def build_object_overviews(chunks: Sequence[DbAnalysisChunk]) -> list[ObjectOverview]:
    # Объект уникален по (канал, объект); один и тот же объект из нескольких
    # архивов объединяем в одну запись, чтобы он не дублировался в списке.
    merged: dict[tuple[int, int], ObjectOverview] = {}
    for chunk in chunks:
        for overview in chunk.objects:
            key = (overview.channel, overview.object_id)
            existing = merged.get(key)
            if existing is None:
                merged[key] = ObjectOverview(
                    source_db=overview.source_db,
                    channel=overview.channel,
                    object_id=overview.object_id,
                    object_name=overview.object_name,
                    start_ts=overview.start_ts,
                    end_ts=overview.end_ts,
                )
                continue
            existing.start_ts = min(existing.start_ts, overview.start_ts)
            existing.end_ts = max(existing.end_ts, overview.end_ts)

    return sorted(
        merged.values(),
        key=lambda item: (item.channel, item.object_name, item.start_ts),
    )

def operation_color(process_id: int) -> str:
    if process_id in {1, 2, 3, 4, 7, 8, 11, 12, 15, 16, 19, 20}:
        return "#bfdbfe"
    if process_id in {5, 6, 22, 23, 29, 30}:
        return "#fdba74"
    if process_id in {9, 10, 24, 25, 31, 32}:
        return "#f9a8d4"
    if process_id in {13, 14}:
        return "#c4b5fd"
    if process_id in {17, 18}:
        return "#fca5a5"
    if process_id in {21, 28, 37}:
        return "#86efac"
    if process_id in {50, 55}:
        return "#d1d5db"
    return "#e5e7eb"

def operation_label(process_name: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", process_name).strip()

def format_duration(seconds: float) -> str:
    total_seconds = max(int(round(seconds)), 0)
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)

    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {pluralize(hours, 'час', 'часа', 'часов')}")
    if minutes:
        parts.append(f"{minutes} {pluralize(minutes, 'минута', 'минуты', 'минут')}")
    if remaining_seconds or not parts:
        parts.append(
            f"{remaining_seconds} {pluralize(remaining_seconds, 'секунда', 'секунды', 'секунд')}"
        )
    return " ".join(parts)

def pluralize(count: int, one: str, few: str, many: str) -> str:
    remainder_100 = count % 100
    remainder_10 = count % 10
    if 11 <= remainder_100 <= 14:
        return many
    if remainder_10 == 1:
        return one
    if 2 <= remainder_10 <= 4:
        return few
    return many

def cycle_result_label(
    cycle_segments: Sequence[Segment], *, require_completion_step: bool = True
) -> str:
    has_pause = any(segment.process_id in {50, 55} for segment in cycle_segments)
    # require_completion_step=False: не требуем финальный «Окончание мойки» (21) —
    # мойка без него не понижается до «Требует проверки». Тумблер в настройках.
    completed = (
        not require_completion_step
        or (bool(cycle_segments) and cycle_segments[-1].process_id == 21)
    )
    if completed and not has_pause:
        return "Завершено штатно"
    if completed and has_pause:
        return "Завершено, были паузы"
    if has_pause:
        return "Требует проверки, были паузы"
    return "Требует проверки"

def cycle_result_label_from_operations(
    operation_names: Sequence[str], *, require_completion_step: bool = True
) -> str:
    has_pause = any(name in PAUSE_PROCESS_NAMES for name in operation_names)
    completed = (
        not require_completion_step
        or (bool(operation_names) and operation_names[-1] == COMPLETED_PROCESS_NAME)
    )
    if completed and not has_pause:
        return "Завершено штатно"
    if completed and has_pause:
        return "Завершено, были паузы"
    if has_pause:
        return "Требует проверки, были паузы"
    return "Требует проверки"

def _phase_concentration_series(samples: Sequence[Sample], process_id: int) -> list[float]:
    """Ряд концентраций возврата за фазу мойки, в порядке времени.

    Кондуктометр стоит на возврате, поэтому в начале фазы (пока раствор идёт по
    контуру) концентрация ещё нарастает, а в конце — спадает при вытеснении.
    Ряд нужен целиком, чтобы отделить эти переходные края от рабочей «полки»."""
    return [
        sample.concentration_return
        for sample in samples
        if sample.process == process_id and sample.concentration_return is not None
    ]


def _evaluate_phase_concentration(
    series: Sequence[float], norm: float, threshold: float
) -> dict[str, Any]:
    """Оценка одной фазы по ряду концентраций относительно порога.

    Логика учитывает, что датчик на возврате: рабочий участок — это интервал от
    первого до последнего превышения порога (полка). Всё до него — заполнение
    контура, всё после — вытеснение; эти края в оценку не входят.

    - `not_reached`: концентрация ни разу не достигла порога — раствор слабый или
      не подан (ловим «недостижение»). Показываем пик — докуда дошла.
    - `dip`: раствор вышел на режим, но ПОСЕРЕДИНЕ полки провалился ниже порога
      (разбавление). Показываем минимум полки.
    - `ok`: вышел на режим и держался. Показываем минимум полки.
    """
    peak = max(series)
    reached = [index for index, value in enumerate(series) if value >= threshold]
    if not reached:
        return {"status": "low", "reason": "not_reached", "peak": peak, "floor": None}

    working = series[reached[0]: reached[-1] + 1]
    floor = min(working)
    if floor < threshold:
        return {"status": "low", "reason": "dip", "peak": peak, "floor": floor}
    return {"status": "ok", "reason": None, "peak": peak, "floor": floor}


def evaluate_concentration(
    samples: Sequence[Sample],
    norms: Mapping[str, float | None],
    tolerance_percent: float = 0.0,
) -> dict[str, Any]:
    """Оценивает концентрацию рабочих растворов мойки относительно нормативов.

    `samples` — сэмплы мойки (с полем `process`); из них по каждой фазе
    (щёлочь/кислота) берётся временной ряд концентрации возврата. `norms` —
    целевые концентрации по фазам (`{"alkali": %, "acid": %}`); None или
    отсутствие ключа = норматив не задан, фаза не оценивается. `tolerance_percent`
    задаёт порог `норма·(1 − допуск/100)`: раствор должен подняться до него и
    удержаться на рабочем участке.

    Возвращает `{"phases": [...], "kind": "low"|"ok"|None}`. `kind` = "low", если
    хотя бы одна оценённая фаза не достигла нормы или провалилась на рабочем
    участке; "ok" — если оценённые фазы в норме; None — если оценить нечего.
    """
    try:
        tolerance = float(tolerance_percent)
    except (TypeError, ValueError):
        tolerance = 0.0
    tolerance = max(0.0, min(100.0, tolerance))
    factor = 1.0 - tolerance / 100.0

    phases: list[dict[str, Any]] = []
    any_low = False
    any_evaluated = False
    for phase_key, process_id, label in CONCENTRATION_PHASES:
        norm = norms.get(phase_key)
        try:
            norm = float(norm) if norm is not None else None
        except (TypeError, ValueError):
            norm = None
        series = _phase_concentration_series(samples, process_id)

        if norm is None or norm <= 0 or not series:
            phases.append(
                {
                    "phase": phase_key,
                    "label": label,
                    "status": "unknown",
                    "reason": None,
                    "norm": norm,
                    "threshold": None,
                    "peak": max(series) if series else None,
                    "floor": None,
                }
            )
            continue

        any_evaluated = True
        threshold = norm * factor
        result = _evaluate_phase_concentration(series, norm, threshold)
        if result["status"] == "low":
            any_low = True
        phases.append(
            {
                "phase": phase_key,
                "label": label,
                "status": result["status"],
                "reason": result["reason"],
                "norm": norm,
                "threshold": threshold,
                "peak": result["peak"],
                "floor": result["floor"],
            }
        )

    if not any_evaluated:
        kind: str | None = None
    elif any_low:
        kind = "low"
    else:
        kind = "ok"
    return {"phases": phases, "kind": kind}

def prune_samples_to_cycles(
    samples: Sequence[Sample],
    cycles: Sequence[Cycle],
    *,
    margin_seconds: float,
) -> list[Sample]:
    """Оставляем только точки внутри моек (плюс запас на границах — он нужен,
    чтобы склеить мойку с соседним файлом и посчитать конец последней
    операции). Весь остальной архив никто не читает, а в памяти и в pickle-кэше
    он занимал сотни мегабайт."""
    if not cycles or not samples:
        return []

    windows: list[list[float]] = []
    for start, end in sorted(
        (cycle.start_ts - margin_seconds, cycle.end_ts + margin_seconds) for cycle in cycles
    ):
        if windows and start <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])

    timestamps = [sample.ts for sample in samples]
    kept: list[Sample] = []
    for start, end in windows:
        kept.extend(samples[bisect_left(timestamps, start) : bisect_right(timestamps, end)])
    return kept

def analyze_single_db_file(
    db_path: Path,
    *,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    cancel_check: Callable[[], bool] | None = None,
    channel: int | None = None,
) -> DbAnalysisChunk:
    resolved_db_path = Path(db_path).expanduser().resolve()
    resolved_channel = channel if channel is not None else preflight_db_file(resolved_db_path)
    samples = read_samples(resolved_db_path, cancel_check=cancel_check)
    segments = build_segments(
        samples,
        resolved_db_path,
        resolved_channel,
        max_gap_seconds=max_gap_seconds,
    )
    cycles = build_cycles(segments, max_gap_seconds)
    # Список объектов собираем до отсева сэмплов: объект, который в этот день
    # ни разу не мыли, всё равно должен попасть в список для переименования.
    objects = collect_object_overviews(samples, resolved_db_path, resolved_channel)
    return DbAnalysisChunk(
        db_path=resolved_db_path,
        channel=resolved_channel,
        samples=prune_samples_to_cycles(samples, cycles, margin_seconds=max_gap_seconds),
        segments=segments,
        cycles=cycles,
        objects=objects,
    )

def merge_channel_samples(streams: Sequence[Sequence[Sample]]) -> list[Sample]:
    """Сэмплы одного канала из разных архивов сливаются в один поток: суточные
    файлы режут мойку по полуночи, а перекрывающиеся выгрузки повторяют одни и
    те же строки.

    Дубликат ищем по (метка времени, объект, процесс, программа) — без этих полей
    в ключе разные строки с одной меткой (другой объект/процесс/программа) молча
    терялись. Настоящие повторы перекрывающихся выгрузок совпадают по всем четырём
    полям и по-прежнему схлопываются. Из копий берём ту, где меньше NULL-метрик
    (обрыв связи панели пишет строку с пустыми полями)."""
    best: dict[tuple[float, int, int, int], Sample] = {}
    for stream in streams:
        for sample in stream:
            key = (sample.ts, sample.object_id, sample.process, sample.program)
            existing = best.get(key)
            if existing is None or filled_metric_count(sample) > filled_metric_count(existing):
                best[key] = sample
    return sorted(best.values(), key=lambda sample: (sample.ts, sample.object_id))

def build_source_db_resolver(
    chunks: Sequence[DbAnalysisChunk],
    fallback: str,
) -> Callable[[float], str]:
    """Файл-источник мойки по её началу: после склейки канала мойка может
    приходить из двух архивов, показываем тот, в котором она началась."""
    ranges = sorted(
        (chunk.samples[0].ts, source_key(chunk.db_path))
        for chunk in chunks
        if chunk.samples
    )
    starts = [start for start, _ in ranges]

    def resolve(timestamp: float) -> str:
        index = bisect_right(starts, timestamp) - 1
        if index < 0:
            return fallback
        return ranges[index][1]

    return resolve

def cycle_completeness(cycle: Cycle) -> tuple[int, float]:
    return (cycle.sample_count, cycle.duration_seconds)

def deduplicate_cycles(cycles: Sequence[Cycle]) -> list[Cycle]:
    """Убирает повторяющиеся мойки, которые встречаются сразу в нескольких
    архивах (перекрытие периодов выгрузки).

    Дубликатом считаем мойки одного канала/объекта/программы, интервалы
    которых пересекаются: сравнение времени старта с точностью до секунды
    рвалось на округлении (1000.4 и 1000.6 давали разные ключи) и не ловило
    копии, обрезанные по-разному. Из группы оставляем самую полную запись — по
    числу точек, при равенстве по длительности."""
    by_route: dict[tuple[int, int, int], list[Cycle]] = defaultdict(list)
    for cycle in cycles:
        by_route[(cycle.channel, cycle.object_id, cycle.program_id)].append(cycle)

    unique: list[Cycle] = []
    for route_cycles in by_route.values():
        best: Cycle | None = None
        group_end = 0.0
        for cycle in sorted(route_cycles, key=lambda item: item.start_ts):
            if best is None or cycle.start_ts > group_end:
                if best is not None:
                    unique.append(best)
                best = cycle
                group_end = cycle.end_ts
                continue

            group_end = max(group_end, cycle.end_ts)
            if cycle_completeness(cycle) > cycle_completeness(best):
                best = cycle
        if best is not None:
            unique.append(best)

    return unique

def build_analysis_result(
    db_files: Sequence[str | Path],
    *,
    output_dir: str | Path,
    max_gap_seconds: float,
    chunks: Sequence[DbAnalysisChunk],
    analysis_cache_key: str = "",
) -> AnalysisResult:
    resolved_output_dir = Path(output_dir).expanduser().resolve()
    resolved_db_files = [Path(path).expanduser().resolve() for path in db_files]

    chunks_by_channel: dict[int, list[DbAnalysisChunk]] = defaultdict(list)
    for chunk in chunks:
        chunks_by_channel[chunk.channel].append(chunk)

    all_segments: list[Segment] = []
    all_cycles: list[Cycle] = []
    samples_by_db: dict[str, list[Sample]] = {}
    channels_by_db: dict[str, int] = {}
    sample_stream_by_channel: dict[int, str] = {}

    for channel, channel_chunks in sorted(chunks_by_channel.items()):
        channel_chunks = sorted(channel_chunks, key=lambda chunk: source_key(chunk.db_path))
        # Сегменты и циклы пересобираем по объединённому потоку канала, а не
        # склеиваем готовые куски файлов: только так статистика мойки,
        # разрезанной границей суток, считается по общему набору точек.
        stream_key = source_key(channel_chunks[0].db_path)
        merged_samples = merge_channel_samples([chunk.samples for chunk in channel_chunks])
        samples_by_db[stream_key] = merged_samples
        channels_by_db[stream_key] = channel
        sample_stream_by_channel[channel] = stream_key

        resolve_source_db = build_source_db_resolver(channel_chunks, stream_key)
        channel_segments = build_segments(
            merged_samples,
            stream_key,
            channel,
            max_gap_seconds=max_gap_seconds,
        )
        for segment in channel_segments:
            segment.source_db = resolve_source_db(segment.start_ts)

        all_segments.extend(channel_segments)
        all_cycles.extend(build_cycles(channel_segments, max_gap_seconds))

    all_cycles = deduplicate_cycles(all_cycles)

    all_overviews = build_object_overviews(chunks)
    sorted_cycles = sorted(all_cycles, key=lambda item: item.start_ts, reverse=True)
    cycles_by_key = {make_cycle_key(cycle): cycle for cycle in sorted_cycles}
    segments_by_cycle_key = build_cycle_segment_index(all_cycles, all_segments)
    sample_ranges_by_cycle_key = build_cycle_sample_range_index(
        all_cycles,
        samples_by_db,
        sample_stream_by_channel,
    )
    cycle_results_by_key = build_cycle_result_index(
        all_cycles,
        all_segments,
        segments_by_cycle_key=segments_by_cycle_key,
    )

    return AnalysisResult(
        db_files=resolved_db_files,
        output_dir=resolved_output_dir,
        max_gap_seconds=max_gap_seconds,
        segments=all_segments,
        cycles=all_cycles,
        overviews=all_overviews,
        samples_by_db=samples_by_db,
        channels_by_db=channels_by_db,
        sample_stream_by_channel=sample_stream_by_channel,
        sorted_cycles=sorted_cycles,
        cycles_by_key=cycles_by_key,
        segments_by_cycle_key=segments_by_cycle_key,
        sample_ranges_by_cycle_key=sample_ranges_by_cycle_key,
        cycle_results_by_key=cycle_results_by_key,
        analysis_cache_key=analysis_cache_key,
    )
