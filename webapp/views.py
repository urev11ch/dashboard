"""Сборка данных для интерфейса (read-side): строки списка моек и их детализация,
сводки, предупреждения, снимок состояния и контекст страницы. Без побочных
эффектов над источником — только чтение анализа/настроек и форматирование.

Выделено из webapp/app.py. Кэши строк/вердиктов концентрации — модульные глобалы
(_wash_rows_cache, _conc_verdicts_cache); тест патчит их и evaluate_cycle_concentration
как app.views.<имя>.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request

import wash_report as core
from webapp import config
from webapp import __version__ as APP_VERSION
from webapp.config import DEFAULT_FTP_FORM_VALUES, PROJECT_ROOT, STATIC_DIR
from webapp.state import AppStateSnapshot, ScanSummary, state
from webapp.analysis import serialize_job
from webapp.io_utils import (
    format_day_key,
    format_file_list,
    format_source_label,
    local_tz_offset_min,
)
from webapp.settings_store import (
    app_settings_path,
    apply_concentration_verdict,
    evaluate_cycle_concentration,
    fallback_object_name,
    load_app_settings,
    load_last_folder_path,
    resolve_cycle_default_status,
    resolve_object_name,
)
from webapp.ftp_registry import list_ftp_sources_public
from webapp.chart_payload import SERIES_CONFIG

def resolve_workspace_input_value(
    selected_root: Path | None,
    pending_root: Path | None,
) -> str:
    current_root = selected_root or pending_root
    if current_root is not None:
        return str(current_root)

    # После перезапуска активного источника ещё нет — подставляем последний
    # открытый путь к папке, если он сохранён.
    last_path = load_last_folder_path()
    if last_path:
        return last_path

    # Иначе — путь по умолчанию: заданный в настройках, а при его отсутствии —
    # локальная папка со скачанными архивами (datalog). Она определяется per-user
    # (%LOCALAPPDATA%\OptiCIP Dashboard\datalog на Windows), поэтому на каждом ПК
    # подставляется путь текущего пользователя.
    return resolve_default_folder_path()


def resolve_default_folder_path() -> str:
    """Путь по умолчанию для поля «Папка»: заданный пользователем в настройках,
    а при его отсутствии — встроенная папка datalog."""
    configured = load_app_settings().get("default_folder_path") or ""
    return configured or str(config.DATALOG_ROOT)


def copy_scan_summary(summary: ScanSummary) -> ScanSummary:
    return ScanSummary(
        archive_count=summary.archive_count,
        ftp_source_count=summary.ftp_source_count,
        ftp_failed_files=list(summary.ftp_failed_files),
        ftp_error=summary.ftp_error,
        skipped_db_files=list(summary.skipped_db_files),
    )


def capture_state_snapshot() -> AppStateSnapshot:
    return AppStateSnapshot(
        analysis=state.analysis,
        analysis_revision=state.analysis_revision,
        selected_root=state.selected_root,
        pending_root=state.pending_root,
        selected_display_root=state.selected_display_root,
        pending_display_root=state.pending_display_root,
        object_name_overrides=dict(state.object_name_overrides),
        error=state.error,
        scan_summary=copy_scan_summary(state.scan_summary),
        workspace_job_payload=serialize_job(state.workspace_job),
        connected_ftp_id=state.connected_ftp_id,
    )


def parse_cycle_key(key: str) -> tuple[str, int, int, int, int, int]:
    # Режем справа: последние 5 полей — числа, а source_db (путь) сам может
    # содержать «::». split слева отдал бы часть пути в channel и врал бы 400.
    parts = key.rsplit("::", 5)
    if len(parts) != 6:
        raise HTTPException(status_code=400, detail="Некорректный ключ мойки.")
    source_db, channel, object_id, program_id, start_ts, end_ts = parts
    try:
        return source_db, int(channel), int(object_id), int(program_id), int(start_ts), int(end_ts)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректный ключ мойки.") from exc


def require_analysis() -> core.AnalysisResult:
    if state.analysis is None:
        raise HTTPException(status_code=400, detail=state.error or "Данные не загружены.")
    return state.analysis


def find_cycle(analysis: core.AnalysisResult, key: str) -> core.Cycle:
    cycle = analysis.cycles_by_key.get(key)
    if cycle is not None:
        return cycle

    parse_cycle_key(key)
    raise HTTPException(status_code=404, detail="Мойка не найдена.")


def build_wash_rows(
    analysis: core.AnalysisResult,
    settings: dict[str, Any],
    conc_verdicts: dict[str, dict[str, Any] | None],
    overrides: dict[tuple[int, int], str],
) -> list[dict[str, Any]]:
    """Форматирует строки списка моек. Вердикты концентрации приходят готовыми
    (conc_verdicts), поэтому сэмплы с диска здесь НЕ читаются — сборка дешёвая и
    не зависит от концентрационных настроек (только от меток/тумблера/анализа).

    Имя объекта берём из СНИМКА overrides (resolve_object_name), а не из
    cycle.object_name: последнее приложение переименовывает элементы анализа на
    месте под state_lock, а эта сборка идёт вне лока — из мутируемого поля можно
    было бы увидеть полу-применённое переименование в одном ответе. Значение
    идентично (cycle.object_name = resolve_object_name(...)), но снимок консистентен."""
    result_labels = settings["result_labels"]
    rows: list[dict[str, Any]] = []
    for cycle in analysis.sorted_cycles:
        cycle_key = core.make_cycle_key(cycle)
        date_time = core.format_ts(cycle.start_ts)
        default_status = resolve_cycle_default_status(
            analysis,
            cycle,
            require_completion_step=settings["require_completion_step"],
            cycle_key=cycle_key,
        )
        concentration = conc_verdicts.get(cycle_key)
        status, result_kind = apply_concentration_verdict(
            default_status, result_labels, concentration
        )
        source_name = format_source_label(cycle.source_db)
        object_name = resolve_object_name(cycle.channel, cycle.object_id, overrides)
        rows.append(
            {
                "key": cycle_key,
                "date_time": date_time,
                "start_ts": cycle.start_ts,
                "end_ts": cycle.end_ts,
                "start_day": format_day_key(cycle.start_ts),
                "object_id": cycle.object_id,
                "object": object_name,
                "program": cycle.program_name,
                "status": status,
                "result_kind": result_kind,
                "concentration_kind": concentration["kind"] if concentration else None,
                "channel": cycle.channel,
                "duration": core.format_duration(cycle.duration_seconds),
                "duration_seconds": cycle.duration_seconds,
                "source_name": source_name,
                "search_blob": " ".join(
                    [
                        object_name,
                        cycle.program_name,
                        date_time,
                        source_name,
                        status,
                        f"Канал {cycle.channel}",
                    ]
                ).lower(),
            }
        )
    return rows


# Кэш строк списка моек для /api/workspace-data: пересборка нужна только при
# смене анализа/оверрайдов имён (analysis_revision) или файла настроек (mtime).
# Собирается ВНЕ state_lock: сборка десятков тысяч словарей и чтение файла
# настроек под общим локом подвешивали и остальные запросы, и SSE.
_wash_rows_cache: dict[str, Any] = {"revision": None, "settings_mtime": None, "rows": []}
_wash_rows_cache_lock = threading.Lock()

# Кэш вердиктов концентрации по циклам. Отдельно от строк, потому что вычисление
# концентрации ТЯЖЁЛОЕ (читает с диска полные потоки сэмплов всех каналов), но
# зависит только от анализа и КОНЦЕНТРАЦИОННЫХ настроек (вкл/нормы/допуск) — не от
# меток результата и тумблера завершения. Так смена метки/тумблера пересобирает
# строки дёшево, не перечитывая сэмплы. Ключ: (analysis_revision, сигнатура
# концентрационных настроек).
_conc_verdicts_cache: dict[str, Any] = {"key": None, "verdicts": {}}
_conc_verdicts_cache_lock = threading.Lock()


def _concentration_settings_signature(settings: dict[str, Any]) -> tuple[Any, ...]:
    """Сигнатура настроек, влияющих на вердикт концентрации. Выключенная оценка —
    один общий ключ (вердикты не зависят от норм/допуска)."""
    if not settings.get("concentration_eval_enabled"):
        return ("off",)
    norms = settings.get("concentration_norms") or {}
    tol = settings.get("concentration_tolerance_percent") or 0.0
    return ("on", json.dumps(norms, sort_keys=True, ensure_ascii=False), float(tol))


def concentration_verdicts_cached(
    analysis: core.AnalysisResult,
    analysis_revision: int,
    settings: dict[str, Any],
) -> dict[str, dict[str, Any] | None]:
    """Вердикты концентрации по cycle_key с кэшем. Сэмплы читаются только при
    промахе (смена анализа или концентрационных настроек)."""
    key = (analysis_revision, _concentration_settings_signature(settings))
    with _conc_verdicts_cache_lock:
        if _conc_verdicts_cache["key"] == key:
            return _conc_verdicts_cache["verdicts"]

    # Промах — считаем вне лока (тяжёлое чтение сэмплов).
    verdicts: dict[str, dict[str, Any] | None] = {}
    if settings.get("concentration_eval_enabled"):
        for cycle in analysis.sorted_cycles:
            verdicts[core.make_cycle_key(cycle)] = evaluate_cycle_concentration(
                analysis, cycle, settings
            )
    with _conc_verdicts_cache_lock:
        _conc_verdicts_cache["key"] = key
        _conc_verdicts_cache["verdicts"] = verdicts
    return verdicts


def app_settings_mtime_ns() -> int | None:
    try:
        return app_settings_path().stat().st_mtime_ns
    except OSError:
        return None


def build_wash_rows_cached(
    analysis: core.AnalysisResult | None,
    analysis_revision: int,
    overrides: dict[tuple[int, int], str] | None = None,
) -> list[dict[str, Any]]:
    """Строки списка моек с кэшем; вызывать вне state_lock. overrides — из того же
    снимка, что и analysis_revision (они меняются вместе под локом), поэтому кэш
    по revision остаётся корректным."""
    if analysis is None:
        return []

    settings_mtime = app_settings_mtime_ns()
    with _wash_rows_cache_lock:
        if (
            _wash_rows_cache["revision"] == analysis_revision
            and _wash_rows_cache["settings_mtime"] == settings_mtime
        ):
            return _wash_rows_cache["rows"]

    settings = load_app_settings()
    # Вердикты концентрации — из своего кэша (сэмплы читаются только при смене
    # анализа/концентрационных настроек, а не на каждое сохранение настроек).
    conc_verdicts = concentration_verdicts_cached(analysis, analysis_revision, settings)
    rows = build_wash_rows(analysis, settings, conc_verdicts, overrides or {})
    with _wash_rows_cache_lock:
        _wash_rows_cache["revision"] = analysis_revision
        _wash_rows_cache["settings_mtime"] = settings_mtime
        _wash_rows_cache["rows"] = rows
    return rows


def build_object_rows(
    overrides: dict[tuple[int, int], str] | None = None,
    analysis: core.AnalysisResult | None = None,
) -> list[dict[str, Any]]:
    overrides = overrides or {}
    # Показываем не только уже переименованные объекты, но и все обнаруженные в
    # данных — чтобы их можно было назвать (и тем самым создать json), даже если
    # файла имён ещё нет.
    keys: set[tuple[int, int]] = set(overrides)
    if analysis is not None:
        for overview in analysis.overviews:
            if overview.object_id > 0:
                keys.add((overview.channel, overview.object_id))

    rows: list[dict[str, Any]] = []
    for channel, object_id in sorted(keys):
        if object_id <= 0:
            continue
        base_name = fallback_object_name(object_id)
        object_name = resolve_object_name(channel, object_id, overrides)
        rows.append(
            {
                "channel": channel,
                "object_id": object_id,
                "object_name": object_name,
                "base_object_name": base_name,
                "is_json_name": (channel, object_id) in overrides,
                "is_custom_name": object_name != base_name,
            }
        )

    return rows


def build_seed_object_name_overrides(
    analysis: core.AnalysisResult,
    overrides: dict[tuple[int, int], str] | None = None,
) -> dict[tuple[int, int], str]:
    seeded = dict(overrides or {})
    for overview in sorted(analysis.overviews, key=lambda item: (item.channel, item.object_id, item.start_ts)):
        key = (overview.channel, overview.object_id)
        if key in seeded:
            continue
        seeded[key] = str(overview.object_name or "").strip() or fallback_object_name(overview.object_id)
    return seeded


def build_wash_detail(analysis: core.AnalysisResult, key: str) -> dict[str, Any]:
    cycle = find_cycle(analysis, key)
    settings = load_app_settings()
    default_status = resolve_cycle_default_status(
        analysis, cycle, require_completion_step=settings["require_completion_step"]
    )
    concentration = evaluate_cycle_concentration(analysis, cycle, settings)
    status, result_kind = apply_concentration_verdict(
        default_status, settings["result_labels"], concentration
    )

    return {
        "key": key,
        "date_time": core.format_ts(cycle.start_ts),
        "start_time": core.format_ts(cycle.start_ts),
        "end_time": core.format_ts(cycle.end_ts),
        "start_ts": cycle.start_ts,
        "end_ts": cycle.end_ts,
        "object_id": cycle.object_id,
        "object_name": cycle.object_name,
        "program": cycle.program_name,
        "channel": cycle.channel,
        "status": status,
        "result_kind": result_kind,
        "concentration_kind": concentration["kind"] if concentration else None,
        "concentration_eval": concentration["phases"] if concentration else None,
        "duration": core.format_duration(cycle.duration_seconds),
        "chart_data_url": f"/api/wash-chart-data?key={quote(key, safe='')}",
    }


def build_summary_payload(
    analysis: core.AnalysisResult | None,
    scan_summary: ScanSummary,
) -> dict[str, int]:
    return {
        "db_count": len(analysis.db_files) if analysis else 0,
        "object_count": len(analysis.overviews) if analysis else 0,
        "cycle_count": len(analysis.cycles) if analysis else 0,
        "archive_count": scan_summary.archive_count,
        "ftp_source_count": scan_summary.ftp_source_count,
        "skipped_db_count": len(scan_summary.skipped_db_files),
        "ftp_failed_count": len(scan_summary.ftp_failed_files),
    }


def build_workspace_payload(snapshot: AppStateSnapshot) -> dict[str, Any]:
    analysis = snapshot.analysis
    selected_root = snapshot.selected_root
    pending_root = snapshot.pending_root
    current_root = selected_root or pending_root
    display_root = (
        snapshot.selected_display_root
        or snapshot.pending_display_root
        or (str(current_root) if current_root else "")
    )
    return {
        "has_analysis": analysis is not None,
        "selected_root": str(selected_root) if selected_root else "",
        "display_root": display_root,
        "summary": build_summary_payload(analysis, snapshot.scan_summary),
        "error": snapshot.error,
        "warnings": build_scan_warnings(snapshot.scan_summary),
        "job_status": snapshot.workspace_job_payload,
        "tz_offset_min": local_tz_offset_min(),
    }


def build_scan_warnings(scan_summary: ScanSummary) -> list[str]:
    """Непрерывающие проблемы прошедшей синхронизации (битые базы, недокачанные
    файлы, недоступный FTP) — их видно в интерфейсе, а не только в логе."""
    warnings: list[str] = []
    if scan_summary.skipped_db_files:
        warnings.append(
            f"Пропущены повреждённые или неподходящие базы ({len(scan_summary.skipped_db_files)}): "
            f"{format_file_list(scan_summary.skipped_db_files)}."
        )
    if scan_summary.ftp_failed_files:
        warnings.append(
            f"Не удалось скачать файлы с FTP ({len(scan_summary.ftp_failed_files)}): "
            f"{format_file_list(scan_summary.ftp_failed_files)}."
        )
    if scan_summary.ftp_error:
        warnings.append(f"Синхронизация с FTP не удалась: {scan_summary.ftp_error}")
    return warnings


def page_context(request: Request, snapshot: AppStateSnapshot) -> dict[str, Any]:
    analysis = snapshot.analysis
    selected_root = snapshot.selected_root
    pending_root = snapshot.pending_root
    workspace_payload = build_workspace_payload(snapshot)
    workspace_input_value = resolve_workspace_input_value(selected_root, pending_root)
    # Путь по умолчанию читает файл настроек (load_app_settings) — считаем один раз
    # и переиспользуем для подсказки и дефолта поля «Папка» (раньше два вызова →
    # двойное чтение+json.loads на каждую загрузку /).
    default_folder_path = resolve_default_folder_path()

    # Подключённая панель («Подключиться» → зелёная строка + WebView/Графики/
    # Отключить). Состояние сессионное (state.connected_ftp_id), не привязано к
    # загрузке графиков; одновременно одна панель. ?view=menu показывает меню даже
    # при загруженной области («Главное меню» без разрыва соединения).
    ftp_sources = list_ftp_sources_public()
    force_menu = request.query_params.get("view") == "menu"
    connected_id = snapshot.connected_ftp_id
    if connected_id and not any(src["id"] == connected_id for src in ftp_sources):
        connected_id = ""  # панель удалили — сбрасываем пометку
    # На экране меню (даже при загруженной области) wash-JS не должен стартовать —
    # его DOM отсутствует. Гейт `if (!hasWorkspace) return` смотрит на hasWorkspace.
    wash_visible = analysis is not None and not force_menu
    def asset_version(filename: str) -> int:
        try:
            return int((STATIC_DIR / filename).stat().st_mtime)
        except OSError:
            return 0

    asset_versions = {
        "style_css": asset_version("style.css"),
        "wash_chart_js": asset_version("wash-chart.js"),
        "app_js": asset_version("app.js"),
        # Иконка тоже версионируется: favicon и титлбар кэшируются браузером и
        # WebView2 намертво, и после смены иконки показывалась бы прежняя.
        "icon_svg": asset_version("washjournal-icon.svg"),
    }
    return {
        "request": request,
        "page_title": "OptiCIP Dashboard",
        "has_analysis": analysis is not None,
        "selected_root": str(selected_root) if selected_root else "",
        "display_root": workspace_payload["display_root"],
        "project_root": str(PROJECT_ROOT),
        "workspace_input_value": workspace_input_value,
        # Подсказка в поле «Папка» = путь по умолчанию (настройка или datalog).
        "workspace_path_placeholder": default_folder_path,
        "workspace_default_path": default_folder_path,
        "ftp_form_defaults": dict(DEFAULT_FTP_FORM_VALUES),
        "ftp_sources": ftp_sources,
        "force_menu": force_menu,
        "connected_id": connected_id,
        "app_version": APP_VERSION,
        "summary": workspace_payload["summary"],
        "error": workspace_payload["error"],
        "asset_versions": asset_versions,
        "job_status": workspace_payload["job_status"],
        "app_state": {
            "appVersion": APP_VERSION,
            # hasWorkspace = показан ли wash-экран (в меню он false, даже если
            # рабочая область загружена) — по нему wash-JS решает, стартовать ли.
            "hasWorkspace": wash_visible,
            "hasAnalysis": analysis is not None,
            "displayRoot": workspace_payload["display_root"],
            "summary": workspace_payload["summary"],
            "error": workspace_payload["error"],
            "jobStatus": workspace_payload["job_status"],
        },
    }


def chart_style_defaults() -> list[dict[str, str]]:
    """Стандартные оформления серий графика (id, подпись, цвет, тип линии) из
    SERIES_CONFIG — чтобы фронтенд мог показать их в панели настроек."""
    return [
        {
            "id": cfg["id"],
            "label": cfg["label"],
            "color": cfg["color"],
            "lineStyle": cfg.get("line_style", "solid"),
        }
        for cfg in SERIES_CONFIG
    ]
