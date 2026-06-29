#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

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

OBJECT_NAME_OVERRIDES: dict[tuple[int, int], str] = {}

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
    1: "Ополаскивание ВторичнаяВода",
    2: "Ополаскивание ЧистаяВода",
    3: "Мойка ЩёлочьКислота",
    4: "МойкаЩёлочь",
    5: "МойкаКислота",
    6: "Дезинфекция Химическая",
    7: "Дезинфекция Термическая",
}

WASH_PROCESS_CODES = set(range(1, 22)) | {50, 55}
NON_SUBSTANTIVE_PROCESS_CODES = {21, 28, 37, 50, 55}
PAUSE_PROCESS_NAMES = {PROCESS_NAMES[50], PROCESS_NAMES[55]}
COMPLETED_PROCESS_NAME = PROCESS_NAMES[21]

class AnalysisCancelledError(RuntimeError):
    pass

@dataclass
class Sample:
    ts: float
    concentration_return: float
    temperature_return: float
    temperature_supply: float
    pressure_supply: float
    flow_supply: float
    process: int
    program: int
    object_id: int

@dataclass
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

@dataclass
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

@dataclass
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

@dataclass
class WashInterval:
    source_db: str
    channel: int
    object_id: int
    object_name: str
    program_id: int
    program_name: str
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

@dataclass
class ObjectOverview:
    source_db: str
    channel: int
    object_id: int
    object_name: str
    start_ts: float
    end_ts: float
    sample_count: int
    program_ids: list[int]
    process_ids: list[int]
    concentration_return: StatsBundle
    temperature_return: StatsBundle
    temperature_supply: StatsBundle
    pressure_supply: StatsBundle
    flow_supply: StatsBundle

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)

@dataclass
class AnalysisResult:
    db_files: list[Path]
    output_dir: Path
    max_gap_seconds: float
    segments: list[Segment]
    cycles: list[Cycle]
    wash_intervals: list[WashInterval]
    overviews: list[ObjectOverview]
    samples_by_db: dict[str, list[Sample]]
    channels_by_db: dict[str, int]
    sorted_cycles: list[Cycle]
    cycles_by_key: dict[str, Cycle]
    cycle_index_by_key: dict[str, int]
    segments_by_cycle_key: dict[str, list[Segment]]
    sample_ranges_by_cycle_key: dict[str, tuple[int, int]]
    cycle_results_by_key: dict[str, str]
    analysis_cache_key: str = ""

@dataclass
class DbAnalysisChunk:
    db_path: Path
    channel: int
    samples: list[Sample]
    segments: list[Segment]
    cycles: list[Cycle]
    wash_intervals: list[WashInterval]

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
    current: int = 0,
    total: int = 0,
    item: str = "",
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        {
            "phase": phase,
            "message": message,
            "current": current,
            "total": total,
            "item": item,
        }
    )

def build_cycle_segment_index(
    cycles: Sequence[Cycle],
    segments: Sequence[Segment],
) -> dict[str, list[Segment]]:
    route_to_cycles: dict[tuple[str, int, int, int], list[Cycle]] = defaultdict(list)
    route_to_segments: dict[tuple[str, int, int, int], list[Segment]] = defaultdict(list)

    for cycle in cycles:
        route_to_cycles[(cycle.source_db, cycle.channel, cycle.object_id, cycle.program_id)].append(cycle)
    for segment in segments:
        route_to_segments[(segment.source_db, segment.channel, segment.object_id, segment.program_id)].append(segment)

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
) -> dict[str, tuple[int, int]]:
    timestamps_by_db = {
        source_db: [sample.ts for sample in samples]
        for source_db, samples in samples_by_db.items()
    }
    indexed: dict[str, tuple[int, int]] = {}
    for cycle in cycles:
        timestamps = timestamps_by_db.get(cycle.source_db, [])
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

def analysis_samples_for_cycle(analysis: AnalysisResult, cycle: Cycle) -> list[Sample]:
    cycle_key = make_cycle_key(cycle)
    samples = analysis.samples_by_db.get(cycle.source_db, [])
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

def preflight_db_file(db_path: Path) -> int:
    channel = infer_channel(db_path)
    connection = sqlite3.connect(str(db_path))
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
    except (OSError, json.JSONDecodeError):
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

def configure_object_name_overrides(
    db_files: Sequence[Path],
    *,
    object_names_file: Path | str | None = None,
) -> dict[tuple[int, int], str]:
    global OBJECT_NAME_OVERRIDES
    OBJECT_NAME_OVERRIDES = load_object_name_overrides_for_db_files(
        db_files,
        object_names_file=object_names_file,
    )
    return dict(OBJECT_NAME_OVERRIDES)

def name_for_object(channel: int, object_id: int) -> str:
    return OBJECT_NAME_OVERRIDES.get((channel, object_id)) or fallback_object_name(object_id)

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
    connection = sqlite3.connect(str(db_path))
    try:
        cursor = connection.execute(query)
        samples: list[Sample] = []
        while True:
            if cancel_check is not None and cancel_check():
                raise AnalysisCancelledError("Обработка базы была отменена пользователем.")

            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            for row in rows:
                samples.append(
                    Sample(
                        ts=float(row[0]),
                        concentration_return=float(row[1] or 0.0),
                        temperature_return=float(row[2] or 0.0),
                        temperature_supply=float(row[3] or 0.0),
                        pressure_supply=float(row[4] or 0.0),
                        flow_supply=float(row[5] or 0.0),
                        process=int(row[6] or 0),
                        program=int(row[7] or 0),
                        object_id=int(row[8] or 0),
                    )
                )
    finally:
        connection.close()
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
    metrics["concentration_return"].add(sample.concentration_return)
    metrics["temperature_return"].add(sample.temperature_return)
    metrics["temperature_supply"].add(sample.temperature_supply)
    metrics["pressure_supply"].add(sample.pressure_supply)
    metrics["flow_supply"].add(sample.flow_supply)

def build_segments(samples: Sequence[Sample], db_path: Path, channel: int) -> list[Segment]:
    segments: list[Segment] = []
    source_db = source_key(db_path)

    current_samples: list[Sample] = []
    current_metrics = new_metrics()
    current_key: tuple[int, int, int] | None = None

    def flush() -> None:
        nonlocal current_samples, current_metrics, current_key
        if not current_samples:
            return

        first = current_samples[0]
        last = current_samples[-1]
        segments.append(
            Segment(
                source_db=source_db,
                channel=channel,
                object_id=first.object_id,
                object_name=name_for_object(channel, first.object_id),
                program_id=first.program,
                program_name=name_for_program(first.program),
                process_id=first.process,
                process_name=name_for_process(first.process),
                start_ts=first.ts,
                end_ts=last.ts,
                sample_count=len(current_samples),
                concentration_return=current_metrics["concentration_return"],
                temperature_return=current_metrics["temperature_return"],
                temperature_supply=current_metrics["temperature_supply"],
                pressure_supply=current_metrics["pressure_supply"],
                flow_supply=current_metrics["flow_supply"],
            )
        )
        current_samples = []
        current_metrics = new_metrics()
        current_key = None

    for sample in samples:
        if not is_wash_sample(sample):
            flush()
            continue

        sample_key = (sample.process, sample.program, sample.object_id)
        if current_key is None or sample_key != current_key:
            flush()
            current_key = sample_key

        current_samples.append(sample)
        add_sample_to_metrics(current_metrics, sample)

    flush()
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
        same_route = (
            segment.source_db == previous.source_db
            and segment.channel == previous.channel
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

def build_wash_intervals(samples: Sequence[Sample], db_path: Path, channel: int, max_gap_seconds: float) -> list[WashInterval]:
    intervals: list[WashInterval] = []
    source_db = source_key(db_path)

    current_samples: list[Sample] = []
    current_metrics = new_metrics()
    current_key: tuple[int, int] | None = None
    current_has_operation = False
    current_has_end = False

    def flush() -> None:
        nonlocal current_samples, current_metrics, current_key, current_has_operation, current_has_end
        if not current_samples:
            return

        if current_has_operation:
            first = current_samples[0]
            last = current_samples[-1]
            intervals.append(
                WashInterval(
                    source_db=source_db,
                    channel=channel,
                    object_id=first.object_id,
                    object_name=name_for_object(channel, first.object_id),
                    program_id=first.program,
                    program_name=name_for_program(first.program),
                    start_ts=first.ts,
                    end_ts=last.ts,
                    sample_count=len(current_samples),
                    concentration_return=current_metrics["concentration_return"],
                    temperature_return=current_metrics["temperature_return"],
                    temperature_supply=current_metrics["temperature_supply"],
                    pressure_supply=current_metrics["pressure_supply"],
                    flow_supply=current_metrics["flow_supply"],
                )
            )
        current_samples = []
        current_metrics = new_metrics()
        current_key = None
        current_has_operation = False
        current_has_end = False

    for sample in samples:
        if sample.object_id <= 0 or sample.program <= 0:
            flush()
            continue

        sample_key = (sample.object_id, sample.program)
        if current_samples:
            previous = current_samples[-1]
            same_key = sample_key == current_key
            close_enough = sample.ts - previous.ts <= max_gap_seconds
            if not same_key or not close_enough or (current_has_end and sample.process != 21):
                flush()

        if current_key is None or sample_key != current_key:
            flush()
            current_key = sample_key

        current_samples.append(sample)
        add_sample_to_metrics(current_metrics, sample)
        if sample.process in WASH_PROCESS_CODES:
            current_has_operation = True
        if sample.process == 21:
            current_has_end = True

    flush()
    return intervals

def build_object_overviews(
    samples_by_db: dict[str, list[Sample]],
    channels_by_db: dict[str, int],
) -> list[ObjectOverview]:
    grouped: dict[tuple[str, int, int], dict[str, object]] = {}

    for source_db, samples in samples_by_db.items():
        channel = channels_by_db[source_db]
        for sample in samples:
            if sample.object_id <= 0:
                continue

            key = (source_db, channel, sample.object_id)
            entry = grouped.setdefault(
                key,
                {
                    "start_ts": sample.ts,
                    "end_ts": sample.ts,
                    "sample_count": 0,
                    "program_ids": set(),
                    "process_ids": set(),
                    "metrics": new_metrics(),
                },
            )
            entry["start_ts"] = min(entry["start_ts"], sample.ts)
            entry["end_ts"] = max(entry["end_ts"], sample.ts)
            entry["sample_count"] += 1
            entry["program_ids"].add(sample.program)
            entry["process_ids"].add(sample.process)
            add_sample_to_metrics(entry["metrics"], sample)

    overviews: list[ObjectOverview] = []
    for (source_db, channel, object_id), entry in grouped.items():
        metrics = entry["metrics"]
        overviews.append(
            ObjectOverview(
                source_db=source_db,
                channel=channel,
                object_id=object_id,
                object_name=name_for_object(channel, object_id),
                start_ts=entry["start_ts"],
                end_ts=entry["end_ts"],
                sample_count=entry["sample_count"],
                program_ids=sorted(entry["program_ids"]),
                process_ids=sorted(entry["process_ids"]),
                concentration_return=metrics["concentration_return"],
                temperature_return=metrics["temperature_return"],
                temperature_supply=metrics["temperature_supply"],
                pressure_supply=metrics["pressure_supply"],
                flow_supply=metrics["flow_supply"],
            )
        )

    return sorted(overviews, key=lambda item: (item.channel, item.object_name, item.start_ts))

def slugify(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", text.strip())
    return cleaned.strip("_").lower() or "plot"

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

def format_metric(value: float | None, unit: str = "", digits: int = 2) -> str:
    if value is None:
        return "н/д"
    if unit:
        return f"{value:.{digits}f} {unit}"
    return f"{value:.{digits}f}"

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

def cycle_result_label(cycle_segments: Sequence[Segment]) -> str:
    has_pause = any(segment.process_id in {50, 55} for segment in cycle_segments)
    completed = bool(cycle_segments) and cycle_segments[-1].process_id == 21
    if completed and not has_pause:
        return "Завершено штатно"
    if completed and has_pause:
        return "Завершено, были паузы"
    if has_pause:
        return "Требует проверки, были паузы"
    return "Требует проверки"

def cycle_result_label_from_operations(operation_names: Sequence[str]) -> str:
    has_pause = any(name in PAUSE_PROCESS_NAMES for name in operation_names)
    completed = bool(operation_names) and operation_names[-1] == COMPLETED_PROCESS_NAME
    if completed and not has_pause:
        return "Завершено штатно"
    if completed and has_pause:
        return "Завершено, были паузы"
    if has_pause:
        return "Требует проверки, были паузы"
    return "Требует проверки"

def analyze_single_db_file(
    db_path: Path,
    *,
    max_gap_seconds: float = 15.0,
    cancel_check: Callable[[], bool] | None = None,
    channel: int | None = None,
) -> DbAnalysisChunk:
    resolved_db_path = Path(db_path).expanduser().resolve()
    resolved_channel = channel if channel is not None else preflight_db_file(resolved_db_path)
    samples = read_samples(resolved_db_path, cancel_check=cancel_check)
    segments = build_segments(samples, resolved_db_path, resolved_channel)
    cycles = build_cycles(segments, max_gap_seconds)
    wash_intervals = build_wash_intervals(samples, resolved_db_path, resolved_channel, max_gap_seconds)
    return DbAnalysisChunk(
        db_path=resolved_db_path,
        channel=resolved_channel,
        samples=samples,
        segments=segments,
        cycles=cycles,
        wash_intervals=wash_intervals,
    )

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
    all_segments: list[Segment] = []
    all_cycles: list[Cycle] = []
    all_wash_intervals: list[WashInterval] = []
    samples_by_db: dict[str, list[Sample]] = {}
    channels_by_db: dict[str, int] = {}

    for chunk in chunks:
        db_key = source_key(chunk.db_path)
        channels_by_db[db_key] = chunk.channel
        samples_by_db[db_key] = chunk.samples
        all_segments.extend(chunk.segments)
        all_cycles.extend(chunk.cycles)
        all_wash_intervals.extend(chunk.wash_intervals)

    all_overviews = build_object_overviews(samples_by_db, channels_by_db)
    sorted_cycles = sorted(all_cycles, key=lambda item: item.start_ts, reverse=True)
    cycles_by_key = {make_cycle_key(cycle): cycle for cycle in sorted_cycles}
    cycle_index_by_key = {
        key: index
        for index, key in enumerate(cycles_by_key.keys(), start=1)
    }
    segments_by_cycle_key = build_cycle_segment_index(all_cycles, all_segments)
    sample_ranges_by_cycle_key = build_cycle_sample_range_index(all_cycles, samples_by_db)
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
        wash_intervals=all_wash_intervals,
        overviews=all_overviews,
        samples_by_db=samples_by_db,
        channels_by_db=channels_by_db,
        sorted_cycles=sorted_cycles,
        cycles_by_key=cycles_by_key,
        cycle_index_by_key=cycle_index_by_key,
        segments_by_cycle_key=segments_by_cycle_key,
        sample_ranges_by_cycle_key=sample_ranges_by_cycle_key,
        cycle_results_by_key=cycle_results_by_key,
        analysis_cache_key=analysis_cache_key,
    )

