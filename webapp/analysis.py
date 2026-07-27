"""Оркестрация анализа источника: обнаружение .db (папка/архивы/FTP-зеркало),
инкрементальный разбор с кэшем, фоновый рабочий джоб и его прогресс, а также
периодическое автообновление активной FTP-панели.

Выделено из webapp/app.py. Кэш-каталоги читаются через config (config.<имя>).
Тесты патчат внутренние вызовы как app.analysis.<имя> (например
app.analysis.prune_analysis_cache, app.analysis.start_workspace_job), потому что
функции резолвят их в неймспейсе этого модуля.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import tarfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import wash_report as core
from webapp import config
from webapp import state as state_module
from webapp.config import (
    DB_ANALYSIS_MAX_WORKERS,
    DELETED_PROFILE_DIR_RE,
    FTP_AUTO_REFRESH_POLL_SECONDS,
    IGNORED_WORKSPACE_DIR_NAMES,
    SUPPORTED_ARCHIVE_SUFFIXES,
    WORKSPACE_JOB_JOIN_TIMEOUT_SECONDS,
)
from webapp.state import ScanSummary, WorkspaceJob, state, state_lock
from webapp.io_utils import format_file_list
from webapp.cache import (
    clear_chart_payload_cache,
    extract_archive_dbs_cached,
    load_cached_db_analysis,
    load_cached_workspace_analysis,
    prune_analysis_cache,
    prune_archive_cache,
    save_cached_db_analysis,
    save_cached_workspace_analysis,
    workspace_analysis_cache_key,
)
from webapp.ftp_client import is_ftp_profile, materialize_ftp_sources
from webapp.settings_store import (
    apply_object_name_overrides,
    load_app_settings,
    load_object_name_overrides,
)

def is_ignored_workspace_dir(path: Path, ignored_paths: set[Path]) -> bool:
    if path.name.lower() in IGNORED_WORKSPACE_DIR_NAMES:
        return True
    if DELETED_PROFILE_DIR_RE.search(path.name):
        return True

    try:
        return path.resolve() in ignored_paths
    except OSError:
        return False


def finish_workspace_job_cancelled(job_id: str, message: str) -> dict[str, Any] | None:
    with state_lock:
        job = state.workspace_job
        if job is None or job.id != job_id:
            return None

        job.status = "cancelled"
        job.phase = "cancelled"
        job.message = message
        job.finished_at = time.time()
        state.pending_root = None
        state.pending_display_root = ""

        if state.analysis is None:
            state.selected_root = None
            state.selected_display_root = ""
            state.scan_summary = ScanSummary()
        return serialize_job(job)


def finish_workspace_job_failed(job_id: str, message: str) -> dict[str, Any] | None:
    with state_lock:
        job = state.workspace_job
        if job is None or job.id != job_id:
            return None

        job.status = "failed"
        job.phase = "failed"
        job.error = message
        job.message = message
        job.finished_at = time.time()
        state.pending_root = None
        state.pending_display_root = ""
        state.error = message

        if state.analysis is None:
            state.selected_root = None
            state.selected_display_root = ""
            state.scan_summary = ScanSummary()
        return serialize_job(job)


def serialize_job(job: WorkspaceJob | None) -> dict[str, Any]:
    if job is None:
        return {
            "id": "",
            "active": False,
            "status": "idle",
            "phase": "idle",
            "message": "",
            "current": 0,
            "total": 0,
            "item": "",
            "target_root": "",
            "display_target": "",
            "error": "",
            "background": False,
        }

    return {
        "id": job.id,
        "active": job.status in {"running", "cancelling"},
        "status": job.status,
        "phase": job.phase,
        "message": job.message,
        "current": job.current,
        "total": job.total,
        "item": job.item,
        "target_root": str(job.target_root) if job.target_root is not None else "",
        "display_target": job.display_target or (str(job.target_root) if job.target_root is not None else ""),
        "error": job.error or "",
        "background": bool(job.background),
    }


def push_job_progress(job_id: str, payload: dict[str, object]) -> None:
    with state_lock:
        job = state.workspace_job
        if job is None or job.id != job_id:
            return

        # Отсутствующие в событии поля не сбрасывают прежние значения — иначе
        # сообщение без current/total обнуляло бы прогресс-бар.
        phase = str(payload.get("phase") or job.phase)
        message = str(payload.get("message") or job.message)
        raw_current = payload.get("current")
        raw_total = payload.get("total")
        current = int(raw_current) if raw_current is not None else job.current
        total = int(raw_total) if raw_total is not None else job.total
        item = str(payload.get("item") or job.item)

        job.phase = phase
        job.current = current
        job.total = total
        job.item = item

        if job.cancel_requested:
            job.status = "cancelling"
            job.message = "Отменяю открытие папки."
        else:
            job.status = "running"
            job.message = message


def job_cancel_requested(job_id: str) -> bool:
    with state_lock:
        job = state.workspace_job
        return job is None or job.id != job_id or job.cancel_requested



def resolve_db_analysis_workers(task_count: int) -> int:
    if task_count <= 1:
        return 1
    cpu_budget = os.cpu_count() or 1
    return max(1, min(DB_ANALYSIS_MAX_WORKERS, task_count, cpu_budget))


def discover_db_files(
    root_path: Path,
    *,
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[Path], ScanSummary]:
    direct_db_files: list[Path] = []
    archive_files: list[Path] = []
    scanned_files = 0

    prune_archive_cache()

    # Сначала докачиваем свежие архивы с FTP в datalog/ГГГГ-ММ/, чтобы обход
    # ниже увидел и их, и ранее скачанные за прошлые месяцы.
    ftp_result = materialize_ftp_sources(
        root_path,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )

    core.emit_progress(
        progress_callback,
        phase="scan",
        message="Сканирую папку и ищу базы данных.",
        item=str(root_path),
    )

    ignored_workspace_dirs = {
        config.ARCHIVE_CACHE_ROOT.resolve(),
        config.ANALYSIS_CACHE_ROOT.resolve(),
        config.WEB_RUNTIME_OUTPUT_DIR.resolve(),
    }

    for current_root, _dirnames, filenames in os.walk(root_path):
        current_root_path = Path(current_root)
        _dirnames[:] = [
            dirname
            for dirname in _dirnames
            if not is_ignored_workspace_dir(current_root_path / dirname, ignored_workspace_dirs)
        ]
        if cancel_check is not None and cancel_check():
            raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

        for filename in filenames:
            scanned_files += 1
            candidate = current_root_path / filename
            lower_name = filename.lower()

            # resolve() на каждый файл — лишний системный вызов на элемент дерева;
            # os.walk и так идёт от уже нормализованного корня.
            if lower_name.endswith(".db"):
                direct_db_files.append(candidate)
            elif any(lower_name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES):
                archive_files.append(candidate)

            if scanned_files == 1 or scanned_files % 200 == 0:
                core.emit_progress(
                    progress_callback,
                    phase="scan",
                    message="Сканирую содержимое папки.",
                    current=scanned_files,
                    item=filename,
                )

    extracted_db_files: list[Path] = []
    for index, archive_path in enumerate(sorted(archive_files), start=1):
        if cancel_check is not None and cancel_check():
            raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

        core.emit_progress(
            progress_callback,
            phase="extract",
            message=f"Распаковываю архив {index} из {len(archive_files)}.",
            current=index,
            total=len(archive_files),
            item=archive_path.name,
        )

        try:
            extracted_db_files.extend(
                extract_archive_dbs_cached(
                    archive_path,
                    cancel_check=cancel_check,
                )
            )
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError):
            continue

    unique_paths = {str(path): path for path in [*direct_db_files, *extracted_db_files]}
    db_files = sorted(unique_paths.values(), key=lambda item: str(item).lower())
    return db_files, ScanSummary(
        archive_count=len(archive_files),
        ftp_source_count=len(ftp_result.present_files),
        ftp_failed_files=list(ftp_result.failed_files),
        ftp_error=ftp_result.ftp_error_message,
    )


def analyze_db_files_incremental(
    db_files: list[Path],
    *,
    output_dir: Path,
    max_gap_seconds: float = 15.0,
    source_key: str = "",
    progress_callback: core.ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[core.AnalysisResult, list[str]]:
    """Возвращает сводный анализ и имена пропущенных баз (битые/неподходящие)."""
    prune_analysis_cache()

    workspace_cache_key = workspace_analysis_cache_key(db_files, max_gap_seconds=max_gap_seconds)
    cached = load_cached_workspace_analysis(workspace_cache_key)
    if cached is not None:
        cached_analysis, cached_skipped = cached
        core.emit_progress(
            progress_callback,
            phase="cache",
            message="Загружаю сохранённый анализ из кэша.",
            current=1,
            total=1,
            item=f"{len(cached_analysis.db_files)} баз данных",
        )
        cached_analysis.output_dir = output_dir
        cached_analysis.analysis_cache_key = workspace_cache_key
        return cached_analysis, cached_skipped

    chunks_by_db: dict[str, core.DbAnalysisChunk] = {}
    pending_jobs: list[tuple[int, Path, int]] = []
    skipped_db_files: list[str] = []
    total_files = len(db_files)
    cached_files = 0

    for index, db_path in enumerate(db_files, start=1):
        if cancel_check is not None and cancel_check():
            raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

        cached_chunk = load_cached_db_analysis(db_path)
        if cached_chunk is not None:
            cached_files += 1
            core.emit_progress(
                progress_callback,
                phase="cache",
                message=f"Загружаю файл {cached_files} из {total_files} из локального кэша.",
                current=cached_files,
                total=total_files,
                item=db_path.name,
            )
            chunks_by_db[str(db_path)] = cached_chunk
            continue

        core.emit_progress(
            progress_callback,
            phase="preflight",
            message=f"Проверяю файл {index} из {total_files}.",
            current=index,
            total=total_files,
            item=db_path.name,
        )
        # Битая или неподходящая база (нет таблицы `data`, повреждён файл,
        # исчез под работающим анализом) не должна валить весь джоб: файл
        # пропускаем, а пользователю потом показываем, сколько таких было.
        try:
            channel = core.preflight_db_file(db_path)
        except (SystemExit, sqlite3.Error, OSError, ValueError) as exc:
            logging.warning("Файл `%s` пропущен: %s", db_path.name, exc)
            skipped_db_files.append(db_path.name)
            continue
        pending_jobs.append((index, db_path, channel))

    if pending_jobs:
        worker_count = resolve_db_analysis_workers(len(pending_jobs))
        analyzed_files = 0
        core.emit_progress(
            progress_callback,
            phase="analyze",
            message=(
                f"Обрабатываю {len(pending_jobs)} файлов"
                f"{' параллельно' if worker_count > 1 else ''}."
            ),
            current=cached_files,
            total=total_files,
            item=f"Воркеров: {worker_count}",
        )

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="wash-db-analysis") as executor:
            future_to_job = {
                executor.submit(
                    core.analyze_single_db_file,
                    db_path,
                    max_gap_seconds=max_gap_seconds,
                    cancel_check=cancel_check,
                    channel=channel,
                ): (index, db_path)
                for index, db_path, channel in pending_jobs
            }
            try:
                for future in as_completed(future_to_job):
                    if cancel_check is not None and cancel_check():
                        raise core.AnalysisCancelledError("Открытие источника было отменено пользователем.")

                    index, db_path = future_to_job[future]
                    # Файл мог оказаться битым уже на разборе данных (или исчезнуть
                    # под работающим анализом) — пропускаем его, а не джоб целиком.
                    try:
                        chunk = future.result()
                    except (SystemExit, sqlite3.Error, OSError, ValueError) as exc:
                        logging.warning("Файл `%s` пропущен: %s", db_path.name, exc)
                        skipped_db_files.append(db_path.name)
                        continue
                    save_cached_db_analysis(db_path, chunk)
                    chunks_by_db[str(db_path)] = chunk
                    analyzed_files += 1
                    core.emit_progress(
                        progress_callback,
                        phase="analyze",
                        message=f"Обрабатываю файл {index} из {total_files}.",
                        current=cached_files + analyzed_files,
                        total=total_files,
                        item=db_path.name,
                    )
            except Exception:
                for future in future_to_job:
                    future.cancel()
                raise

    chunks = [
        chunks_by_db[db_key]
        for db_key in (str(path) for path in db_files)
        if db_key in chunks_by_db
    ]

    if not chunks:
        if skipped_db_files:
            raise SystemExit(
                "Ни одну базу данных не удалось прочитать: "
                f"{format_file_list(skipped_db_files)}. "
                "Проверьте, что файлы не повреждены и имеют вид `Canal_*.db`."
            )
        raise SystemExit("SQLite-файлы не найдены.")

    core.emit_progress(
        progress_callback,
        phase="merge",
        message="Собираю общий индекс моек.",
        current=len(chunks),
        total=len(chunks),
        item=f"{len(chunks)} баз данных",
    )
    analysis = core.build_analysis_result(
        [chunk.db_path for chunk in chunks],
        output_dir=output_dir,
        max_gap_seconds=max_gap_seconds,
        chunks=chunks,
        analysis_cache_key=workspace_cache_key,
    )
    save_cached_workspace_analysis(
        workspace_cache_key,
        analysis,
        source_key=source_key,
        skipped_db_files=skipped_db_files,
    )
    return analysis, skipped_db_files


def build_job_completion_message(scan_summary: ScanSummary) -> str:
    """Сообщение об успешном обновлении. Пропущенные базы и несостоявшиеся
    загрузки — не молчаливые: пользователь должен видеть, что часть данных не
    попала в отчёт."""
    notes: list[str] = []
    if scan_summary.skipped_db_files:
        notes.append(
            f"пропущено баз: {len(scan_summary.skipped_db_files)} "
            f"({format_file_list(scan_summary.skipped_db_files)})"
        )
    if scan_summary.ftp_failed_files:
        notes.append(
            f"не скачано файлов с FTP: {len(scan_summary.ftp_failed_files)} "
            f"({format_file_list(scan_summary.ftp_failed_files)})"
        )
    if scan_summary.ftp_error:
        notes.append(f"синхронизация с FTP не удалась ({scan_summary.ftp_error})")

    if not notes:
        return "Данные успешно обновлены."
    return "Данные обновлены, но " + "; ".join(notes) + "."


def run_workspace_job(
    job_id: str,
    target_root: Path,
    previous_thread: threading.Thread | None = None,
) -> None:
    progress_callback = lambda payload: push_job_progress(job_id, payload)
    cancel_check = lambda: job_cancel_requested(job_id)

    # Дожидаемся завершения предыдущего джоба (ему уже выставлен
    # cancel_requested), чтобы два потока не писали одни и те же файлы
    # зеркала. Ждём здесь, в рабочем потоке, а не под state_lock. Если
    # предыдущий поток так и не завершился, второй параллельно не запускаем:
    # иначе оба пишут в одно зеркало и портят скачанные базы.
    if previous_thread is not None and previous_thread.is_alive():
        previous_thread.join(timeout=WORKSPACE_JOB_JOIN_TIMEOUT_SECONDS)
        if previous_thread.is_alive():
            finish_workspace_job_failed(
                job_id,
                "Предыдущая обработка источника не завершилась вовремя. "
                "Повторите попытку через некоторое время.",
            )
            return

    try:
        db_files, scan_summary = discover_db_files(
            target_root,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        if not db_files:
            raise SystemExit(
                "В выбранном источнике не найдено ни одной базы `.db` ни в папке, ни в поддерживаемых архивах, ни на FTP."
            )

        analysis, skipped_db_files = analyze_db_files_incremental(
            db_files,
            output_dir=config.WEB_RUNTIME_OUTPUT_DIR,
            source_key=str(target_root),
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        scan_summary.skipped_db_files = skipped_db_files
        object_name_overrides = load_object_name_overrides(config.TEMP_ROOT)
        apply_object_name_overrides(analysis, object_name_overrides)

        with state_lock:
            job = state.workspace_job
            if job is None or job.id != job_id:
                return

            state.selected_root = target_root
            state.pending_root = None
            state.selected_display_root = job.display_target or str(target_root)
            state.pending_display_root = ""
            state.analysis = analysis
            state.analysis_revision += 1
            state.object_name_overrides = object_name_overrides
            state.scan_summary = scan_summary
            state.error = None
            state.last_sync_ts = time.time()
            clear_chart_payload_cache()

            job.target_root = target_root
            job.status = "completed"
            job.phase = "completed"
            job.message = build_job_completion_message(scan_summary)
            job.current = max(job.current, job.total)
            job.finished_at = time.time()
    except core.AnalysisCancelledError:
        finish_workspace_job_cancelled(job_id, "Обработка источника отменена.")
    except SystemExit as exc:
        message = str(exc) or "Не удалось открыть выбранный источник."
        finish_workspace_job_failed(job_id, message)
    except Exception as exc:  # pragma: no cover - safety net for background worker
        finish_workspace_job_failed(job_id, f"Не удалось открыть источник: {exc}")


def start_workspace_job(
    candidate: Path,
    *,
    display_target: str | None = None,
    background: bool = False,
) -> None:
    # Активный рабочий поток живёт в state.py (см. там комментарий) — читаем/пишем
    # как атрибут модуля, чтобы ftp_registry видел актуальное значение.
    previous_thread = state_module._workspace_job_thread
    if state.workspace_job is not None and state.workspace_job.status in {"running", "cancelling"}:
        state.workspace_job.cancel_requested = True
        state.workspace_job.status = "cancelling"
        state.workspace_job.message = "Отменяю предыдущую обработку источника."

    resolved_candidate = candidate.resolve()
    job = WorkspaceJob(
        id=uuid.uuid4().hex,
        target_root=resolved_candidate,
        display_target=display_target or str(resolved_candidate),
        background=background,
    )
    state.workspace_job = job
    state.pending_root = resolved_candidate
    state.pending_display_root = job.display_target
    if state.analysis is None:
        state.selected_root = None
        state.selected_display_root = ""
        state.object_name_overrides = {}
        state.scan_summary = ScanSummary()
        clear_chart_payload_cache()
    state.error = None

    thread = threading.Thread(
        target=run_workspace_job,
        args=(job.id, resolved_candidate, previous_thread),
        name="wash-workspace-loader",
        daemon=True,
    )
    state_module._workspace_job_thread = thread
    thread.start()


def trigger_ftp_auto_refresh() -> bool:
    """Запускает фоновое обновление активной FTP-панели, если сейчас нет другой
    обработки. Папочный (folder) источник и отсутствие анализа пропускаются."""
    with state_lock:
        job = state.workspace_job
        if job is not None and job.status in {"running", "cancelling"}:
            return False

        target_root = state.selected_root or state.pending_root
        if target_root is None or state.analysis is None:
            return False

        if not is_ftp_profile(target_root):
            return False

        display_target = (
            state.selected_display_root
            or state.pending_display_root
            or str(target_root.resolve())
        )
        start_workspace_job(target_root.resolve(), display_target=display_target, background=True)
        return True


async def ftp_auto_refresh_loop() -> None:
    """Фоновый цикл: пока приложение запущено, периодически (интервал из настроек)
    докачивает архивы с активной FTP-панели и обновляет данные без блокирующего
    оверлея. Интервал и включение читаются из настроек на каждом тике."""
    last_run = time.monotonic()
    while True:
        try:
            await asyncio.sleep(FTP_AUTO_REFRESH_POLL_SECONDS)

            # load_app_settings читает и парсит файл с диска, а trigger_ftp_auto_refresh
            # берёт state_lock (threading.Lock) и делает syscall'ы — оба уводим в
            # поток, иначе ожидание лока встаёт на event loop и подвешивает все
            # запросы и SSE (та же защита, что в workspace_job_status_stream).
            settings = await asyncio.to_thread(load_app_settings)
            if not settings["ftp_auto_refresh_enabled"]:
                # При выключенном автообновлении откладываем следующий запуск на
                # полный интервал после повторного включения.
                last_run = time.monotonic()
                continue

            interval_seconds = settings["ftp_auto_refresh_minutes"] * 60
            now = time.monotonic()
            if now - last_run < interval_seconds:
                continue

            last_run = now
            if await asyncio.to_thread(trigger_ftp_auto_refresh):
                logging.info("Фоновое автообновление FTP запущено.")
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - защита фонового цикла
            logging.exception("Сбой фонового автообновления FTP")

