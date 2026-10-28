"""Сводка по источникам для карточек главного экрана.

Считать мойки каждого источника при каждом показе главной слишком дорого
(анализ — это скачивание и разбор архивов), поэтому сводка запоминается при
открытии источника и показывается до следующего. Источник, который ни разу не
открывали, честно показывается как «ещё не открывался».
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from webapp import config as app_config
from webapp.io_utils import atomic_write_json, local_tz_offset_min, read_json_object
from webapp.state import PouWorkspace

SOURCE_STATS_FILENAME = "wash_source_stats.json"
SOURCE_STATS_VERSION = 1
# Сколько дней моек хранить для счётчика «за неделю»: с запасом — источник
# могли не открывать несколько дней.
RECENT_DAYS = 45
DAY_SECONDS = 86400

_stats_lock = threading.Lock()
# (ключ источника, ревизия) последней записанной сводки: /api/workspace-data
# зовут часто, а писать файл нужно только при новых данных.
_last_recorded: dict[str, Any] = {}


def source_stats_path():
    return app_config.TEMP_ROOT / SOURCE_STATS_FILENAME


def load_source_stats() -> dict[str, dict[str, Any]]:
    payload = read_json_object(source_stats_path())
    sources = payload.get("sources")
    return {str(k): v for k, v in sources.items() if isinstance(v, dict)} if isinstance(sources, dict) else {}


def _write(key: str, entry: dict[str, Any]) -> None:
    with _stats_lock:
        sources = load_source_stats()
        sources[key] = entry
        target = source_stats_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(target, {"version": SOURCE_STATS_VERSION, "sources": sources})


def forget_source(key: str) -> None:
    with _stats_lock:
        sources = load_source_stats()
        if sources.pop(key, None) is not None:
            atomic_write_json(source_stats_path(), {"version": SOURCE_STATS_VERSION, "sources": sources})


def record_cip_stats(
    key: str, revision: int, rows: Iterable[dict[str, Any]], last_sync_ts: float | None
) -> None:
    """Сводка журнала моек: последние мойки (время начала по часам панели и
    флаг «требует проверки») для счётчиков «за неделю»."""
    if not key or _last_recorded.get(key) == revision:
        return
    starts = [(float(row["start_ts"]), row.get("result_kind") == "check") for row in rows if row.get("start_ts")]
    last_cycle_ts = max((ts for ts, _check in starts), default=None)
    recent = []
    if last_cycle_ts is not None:
        border = last_cycle_ts - RECENT_DAYS * DAY_SECONDS
        recent = sorted([[round(ts), 1 if check else 0] for ts, check in starts if ts >= border])
    _write(
        key,
        {
            "equipment": "cip",
            "opened_at": time.time(),
            "last_sync_ts": last_sync_ts,
            "cycle_count": len(starts),
            "last_cycle_ts": last_cycle_ts,
            "recent": recent,
        },
    )
    _last_recorded[key] = revision


def record_pou_stats(key: str, revision: int, workspace: PouWorkspace, last_sync_ts: float | None) -> None:
    if not key or _last_recorded.get(key) == revision:
        return
    _write(
        key,
        {
            "equipment": "pou",
            "opened_at": time.time(),
            "last_sync_ts": last_sync_ts,
            "db_count": len(workspace.db_files),
            "row_count": workspace.row_count,
            "last_data_ts": workspace.end_ts,
        },
    )
    _last_recorded[key] = revision


# ---- подписи для карточек ---------------------------------------------------
def panel_now_ts() -> float:
    """«Сейчас» в шкале меток архива: панель пишет свои часы как UTC."""
    return time.time() + local_tz_offset_min() * 60


def _panel_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def format_panel_moment(ts: float, now_ts: float | None = None) -> str:
    """Метка архива по-человечески: «сегодня 09:00», «вчера 22:40», «20.10.2026»."""
    now = _panel_dt(now_ts if now_ts is not None else panel_now_ts())
    moment = _panel_dt(ts)
    delta_days = (now.date() - moment.date()).days
    if delta_days == 0:
        return f"сегодня {moment:%H:%M}"
    if delta_days == 1:
        return f"вчера {moment:%H:%M}"
    return f"{moment:%d.%m.%Y}"


def format_ago(ts: float | None, now: float | None = None) -> str:
    """Реальное время (time.time()) относительно «сейчас»: «5 мин назад»."""
    if not ts:
        return ""
    seconds = max(0, (now if now is not None else time.time()) - float(ts))
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"
    if seconds < DAY_SECONDS:
        return f"{int(seconds // 3600)} ч назад"
    local = datetime.fromtimestamp(float(ts))
    return f"{local:%d.%m.%Y}"


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    count = abs(count) % 100
    if 10 < count < 20:
        return many
    count %= 10
    if count == 1:
        return one
    if 1 < count < 5:
        return few
    return many


def summarize_for_card(entry: dict[str, Any] | None, now_panel: float | None = None) -> dict[str, Any] | None:
    """Строки сводки карточки или None, если источник ещё не открывали."""
    if not entry:
        return None
    now_panel = now_panel if now_panel is not None else panel_now_ts()
    if entry.get("equipment") == "pou":
        last = entry.get("last_data_ts")
        db_count = int(entry.get("db_count") or 0)
        return {
            "headline": f"Данные по {format_panel_moment(last, now_panel)}" if last else "Данных нет",
            "detail": f"{db_count} {plural_ru(db_count, 'база', 'базы', 'баз')}",
            "verdict": "",
            "verdict_kind": "",
        }
    last = entry.get("last_cycle_ts")
    recent = entry.get("recent") or []
    week_border = now_panel - 7 * DAY_SECONDS
    week = [item for item in recent if isinstance(item, list) and len(item) == 2 and item[0] >= week_border]
    checks = sum(1 for _ts, check in week if check)
    count = len(week)
    if checks:
        verdict = f"{checks} {plural_ru(checks, 'требует', 'требуют', 'требуют')} проверки"
        verdict_kind = "warn"
    elif count:
        verdict, verdict_kind = "Все завершены штатно", "ok"
    else:
        verdict, verdict_kind = "", ""
    return {
        "headline": f"Последняя мойка {format_panel_moment(last, now_panel)}" if last else "Моек не найдено",
        "detail": f"{count} {plural_ru(count, 'мойка', 'мойки', 'моек')} за неделю",
        "verdict": verdict,
        "verdict_kind": verdict_kind,
    }
