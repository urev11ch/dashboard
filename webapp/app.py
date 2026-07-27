from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import hashlib
import hmac
import ftplib
import logging
import os
import pickle
import posixpath
import re
import secrets
import shutil
import socket
import ssl
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote, urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from runtime_paths import resolve_cache_root, resolve_runtime_root
import wash_report as core
from webapp import __version__ as APP_VERSION
from webapp import config
# Константы и разрешение путей вынесены в webapp/config.py; реэкспортируем их в
# неймспейс app.py, чтобы существующий код (и тесты, патчащие app.<КОНСТАНТА>)
# продолжали видеть их как app.<ИМЯ>.
from webapp.config import *  # noqa: F401,F403
from webapp.config import _RESULT_CATEGORY_BY_DEFAULT  # noqa: F401 (underscore не берётся *)
# Защищённое хранение паролей вынесено в webapp/secrets_store.py. Тесты, которым
# нужно подменить keyring, патчат app.secrets_store._keyring_store/_keyring_fetch.
from webapp import secrets_store
from webapp.secrets_store import protect_secret, unprotect_secret, _keyring_delete  # noqa: F401
# Обнаружение панелей в локальной сети вынесено в webapp/discovery.py. Тесты
# патчат его символы как app.discovery.<имя>.
from webapp import discovery
# Глобальное состояние и локи вынесены в webapp/state.py и разделяются между
# app.py и сервисными модулями (updates и т. д.) как один и тот же объект.
from webapp import state as state_module
from webapp.state import (  # noqa: F401
    AppState,
    AppStateSnapshot,
    ScanSummary,
    UpdateJob,
    WorkspaceJob,
    analysis_cache_lock,
    app_settings_lock,
    archive_cache_lock,
    chart_payload_cache_lock,
    state,
    state_lock,
)
# Проверка/скачивание обновлений вынесены в webapp/updates.py. Роуты обращаются
# сюда как updates.<функция>; тесты патчат символы как app.updates.<имя>.
from webapp import updates
# Распаковка архивов вынесена в webapp/archives.py; реэкспортируем функции —
# внутренние вызовы и прямые вызовы из тестов остаются как app.<имя>. Тесты,
# меняющие лимит распаковки, патчат app.archives.ARCHIVE_EXTRACT_MAX_BYTES.
from webapp import archives
from webapp.archives import extract_archive_dbs, is_supported_archive, safe_archive_member_path
# Мелкие утилиты ввода-вывода/форматирования вынесены в webapp/io_utils.py.
from webapp import io_utils
from webapp.io_utils import (  # noqa: F401
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    format_day_key,
    format_file_list,
    format_source_label,
    local_tz_offset_min,
)
# Реестр FTP-подключений вынесен в webapp/ftp_registry.py; реэкспортируем API —
# роуты/оркестрация и тесты обращаются как app.<имя>. TEMP_ROOT/DATALOG_ROOT
# реестр читает через config, поэтому тесты его каталогов патчат app.config.*.
from webapp import ftp_registry
from webapp.ftp_registry import (  # noqa: F401
    apply_ftp_url_payload,
    connection_to_config,
    create_ftp_workspace,
    delete_ftp_connection,
    find_ftp_connection,
    format_ftp_display_label,
    ftp_connection_id,
    ftp_sources_lock,
    ftp_sources_path,
    list_ftp_sources_public,
    load_ftp_sources_registry,
    normalize_ftp_connection_settings,
    normalize_ftp_host,
    normalize_ftp_path,
    purge_deleted_profile_dirs,
    remove_ftp_profile_dir,
    rename_ftp_connection,
    save_ftp_sources_registry,
    upsert_ftp_connection,
)
# Настройки приложения (имена объектов, стили графика, путь папки, общие
# настройки + применение к вердикту) вынесены в webapp/settings_store.py.
# TEMP_ROOT модуль читает через config, поэтому его тесты патчат app.config.TEMP_ROOT;
# concurrency-тест load_app_settings патчит app.settings_store.load_app_settings.
from webapp import settings_store
from webapp.settings_store import (  # noqa: F401
    app_settings_path,
    apply_concentration_verdict,
    apply_object_name_overrides,
    chart_style_settings_path,
    evaluate_cycle_concentration,
    fallback_object_name,
    folder_source_settings_path,
    load_app_settings,
    load_chart_style_settings,
    load_last_folder_path,
    load_object_name_overrides,
    normalize_app_settings,
    normalize_chart_style_series,
    object_name_override_key,
    object_name_overrides_path,
    parse_object_name_override_key,
    resolve_cycle_default_status,
    resolve_object_name,
    resolve_result_kind,
    resolve_result_label,
    save_app_settings,
    save_chart_style_settings,
    save_last_folder_path,
    save_object_name_overrides,
    update_app_settings,
)
# Дисковый и оперативный кэш анализа вынесены в webapp/cache.py. Каталоги кэша и
# лимит реестра кэш читает через config → тесты патчат app.config.<имя>; in-memory
# реестры/кэши патчатся как app.cache.<имя>. Функции реэкспортируются: оркестрация
# и роуты зовут их как app.<имя>, а тесты патчат load/save_cached_*/prune на app.
from webapp import cache
from webapp.cache import (  # noqa: F401
    archive_cache_key,
    cache_entry_size_bytes,
    cache_hmac_key,
    cleanup_expired_cache_entries,
    cleanup_stale_archive_cache,
    cleanup_stale_db_analysis_cache,
    cleanup_stale_disk_caches,
    cleanup_stale_workspace_cache,
    clear_all_chart_caches,
    clear_chart_payload_cache,
    chart_payload_disk_cache_key,
    chart_payload_disk_cache_path,
    db_analysis_cache_key,
    db_analysis_cache_path,
    extract_archive_dbs_cached,
    get_cached_chart_payload,
    is_protected_cache_entry,
    load_cached_chart_payload_disk,
    load_cached_db_analysis,
    load_cached_workspace_analysis,
    load_pickle_cache,
    make_sample_loader,
    path_cache_signature,
    prune_analysis_cache,
    prune_archive_cache,
    prune_cache_root,
    remember_cache_key,
    remove_cache_entry,
    save_cached_chart_payload_disk,
    save_cached_db_analysis,
    save_cached_workspace_analysis,
    save_pickle_cache,
    set_cached_chart_payload,
    touch_cache_entry,
    workspace_analysis_cache_key,
    workspace_analysis_cache_path,
    ws_samples_path,
)
# FTP-клиент (подключение, загрузка зеркала, ретеншн, синхронизация) вынесен в
# webapp/ftp_client.py. DATALOG_ROOT он читает через config → тесты патчат
# app.config.DATALOG_ROOT; open_ftp_connection патчится как
# app.ftp_client.open_ftp_connection (его зовёт download_ftp_files внутри модуля).
from webapp import ftp_client
from webapp.ftp_client import (  # noqa: F401
    FtpSyncResult,
    archive_month_folder,
    build_local_archive_index,
    cleanup_old_archives,
    datalog_has_archives,
    datalog_size_bytes_cached,
    directory_size_bytes,
    download_ftp_files,
    is_ftp_connection_lost,
    is_ftp_profile,
    iter_tree_files,
    materialize_ftp_sources,
    open_ftp_connection,
    _ftp_list_entries,
    _ftp_relative_target,
    _ftp_walk_files,
    _is_archive_or_db_name,
    _parse_ftp_timestamp,
    _parse_mdtm_reply,
    _should_skip_download,
)
# Оркестрация анализа/джобов вынесена в webapp/analysis.py. Внутренние вызовы
# резолвятся в неймспейсе модуля, поэтому тесты патчат их как app.analysis.<имя>
# (prune_analysis_cache, load/save_cached_*, start_workspace_job); функции
# реэкспортируются — роуты и лайфспан зовут их как app.<имя>.
from webapp import analysis
from webapp.analysis import (  # noqa: F401
    analyze_db_files_incremental,
    build_job_completion_message,
    discover_db_files,
    finish_workspace_job_cancelled,
    finish_workspace_job_failed,
    ftp_auto_refresh_loop,
    is_ignored_workspace_dir,
    job_cancel_requested,
    push_job_progress,
    resolve_db_analysis_workers,
    run_workspace_job,
    serialize_job,
    start_workspace_job,
    trigger_ftp_auto_refresh,
)
# Сборка данных для интерфейса (read-side билдеры + снимок состояния + контекст
# страницы) вынесена в webapp/views.py. Функции реэкспортируются — роуты зовут их
# как app.<имя>; тест концентрации патчит app.views.evaluate_cycle_concentration и
# app.views._conc_verdicts_cache.
from webapp import views
from webapp.views import (  # noqa: F401
    build_object_rows,
    build_scan_warnings,
    build_seed_object_name_overrides,
    build_summary_payload,
    build_wash_detail,
    build_wash_rows,
    build_wash_rows_cached,
    build_workspace_payload,
    capture_state_snapshot,
    chart_style_defaults,
    concentration_verdicts_cached,
    copy_scan_summary,
    find_cycle,
    page_context,
    parse_cycle_key,
    require_analysis,
    resolve_default_folder_path,
    resolve_workspace_input_value,
)
from webapp.chart_payload import SERIES_CONFIG, build_cycle_chart_payload




_auto_refresh_task: "asyncio.Task[None] | None" = None


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    # startup — подчищаем отложенно удаляемые профили FTP и поднимаем фоновый
    # цикл автообновления (папки уже созданы выше)
    global _auto_refresh_task
    purge_deleted_profile_dirs()
    _auto_refresh_task = asyncio.create_task(ftp_auto_refresh_loop())
    try:
        yield
    finally:
        # shutdown — останавливаем фоновую задачу. Дисковые кэши целиком не
        # удаляем: они общие для пользователя, и их может использовать второй
        # запущенный экземпляр приложения (десктоп + браузер). Ограничиваемся
        # возрастной очисткой устаревших записей.
        if _auto_refresh_task is not None:
            _auto_refresh_task.cancel()
            try:
                await _auto_refresh_task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - защита остановки
                logging.exception("Ошибка при остановке фонового автообновления")
            _auto_refresh_task = None
        cleanup_stale_disk_caches()


app = FastAPI(title="Отчеты по мойкам", lifespan=_app_lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Сервер не имеет аутентификации, а API даёт доступ к файловой системе
# (/workspace/open) и реестру FTP-подключений, поэтому:
#   1) главный барьер — фактический адрес клиента (request.client.host): пускаем
#      только loopback. Заголовку Host верить нельзя — он приходит от клиента, и
#      при запуске на 0.0.0.0 любой в сети мог подставить `Host: localhost`;
#   2) заголовки Host/Origin дополнительно защищают от DNS rebinding и CSRF
#      (form-POST/fetch с чужих страниц). Запросы без Origin пропускаем для
#      совместимости (pywebview, curl, собственные страницы).
# Осознанный удалённый доступ включается переменной окружения.
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOW_REMOTE_ENV_VAR = "OPTICIP_ALLOW_REMOTE"


def remote_access_allowed() -> bool:
    return str(os.environ.get(ALLOW_REMOTE_ENV_VAR) or "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def _is_loopback_address(value: str | None) -> bool:
    address = (value or "").strip().strip("[]")
    if not address:
        return False
    # IPv6 с zone-id (fe80::1%eth0) и IPv4-mapped адреса разбираются ipaddress.
    address = address.split("%", 1)[0]
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def client_is_local(request: Request) -> bool:
    client = request.client
    if client is None:
        # Нет TCP-пира (unix-сокет, внутренний транспорт) — удалённым быть не может.
        return True
    return _is_loopback_address(client.host)


def _is_local_hostname(hostname: str | None) -> bool:
    return (hostname or "").strip("[]").lower() in LOCAL_HOSTNAMES


def _header_host_is_local(host_header: str) -> bool:
    try:
        return _is_local_hostname(urlsplit(f"//{host_header}").hostname)
    except ValueError:
        return False


def _origin_is_local(origin_header: str) -> bool:
    try:
        return _is_local_hostname(urlsplit(origin_header).hostname)
    except ValueError:
        return False


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    if not client_is_local(request) and not remote_access_allowed():
        client_host = request.client.host if request.client else "?"
        logging.warning("Отклонён нелокальный запрос от %s к %s", client_host, request.url.path)
        return JSONResponse(
            {"detail": "Доступ разрешён только с локального компьютера."}, status_code=403
        )

    host_header = request.headers.get("host")
    if host_header and not _header_host_is_local(host_header) and not remote_access_allowed():
        return JSONResponse({"detail": "Недопустимый заголовок Host."}, status_code=403)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin_header = request.headers.get("origin")
        if origin_header and not _origin_is_local(origin_header) and not remote_access_allowed():
            return JSONResponse({"detail": "Недопустимый Origin запроса."}, status_code=403)
    return await call_next(request)









def reset_workspace() -> None:
    if state.workspace_job is not None:
        state.workspace_job.cancel_requested = True
    state.workspace_job = None
    state.pending_root = None
    state.selected_root = None
    state.pending_display_root = ""
    state.selected_display_root = ""
    state.analysis = None
    state.analysis_revision += 1
    state.object_name_overrides = {}
    state.error = None
    state.scan_summary = ScanSummary()
    state.connected_ftp_id = ""  # «Отключить»: снимаем пометку подключения
    clear_chart_payload_cache()





@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    with state_lock:
        snapshot = capture_state_snapshot()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(request, snapshot),
    )


@app.post("/workspace/open")
def open_workspace(path: str = Form(...)) -> RedirectResponse:
    candidate = Path(path).expanduser()
    # exists/is_dir/resolve — stat/realpath syscalls, а save_last_folder_path пишет
    # файл на диск. Держать их под глобальным state_lock (на нём ждут SSE и все
    # запросы) незачем: проверку и запись делаем вне лока, под ним — только смена
    # состояния и запуск джоба.
    if not candidate.exists() or not candidate.is_dir():
        with state_lock:
            state.error = f"Папка не найдена: {candidate}"
            if state.analysis is None:
                state.pending_root = None
                state.pending_display_root = ""
        return RedirectResponse(url="/", status_code=303)

    resolved = candidate.resolve()
    save_last_folder_path(str(resolved))
    with state_lock:
        start_workspace_job(resolved, display_target=str(resolved))
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/open-ftp")
def open_ftp_workspace(
    source_id: str = Form(""),
    host: str = Form(""),
    port: str = Form("21"),
    password: str = Form(""),
    path: str = Form("/datalog"),
    passive: str = Form(""),
    label: str = Form(""),
) -> RedirectResponse:
    try:
        saved_id = source_id.strip()
        if saved_id:
            connection = find_ftp_connection(saved_id)
            if connection is None:
                raise ValueError("Сохранённое подключение не найдено.")
            ftp_config = connection_to_config(connection)
            connection_label = connection.get("label") or ""
        else:
            ftp_config = normalize_ftp_connection_settings(
                {
                    "host": host,
                    "port": port,
                    "password": password,
                    "path": path,
                    "passive": passive,
                }
            )
            connection_label = label
        # create_ftp_workspace делает mkdir + запись реестра — тоже под этим
        # обработчиком: сбой прав/диска иначе даёт неперехваченный 500 вместо
        # аккуратного state.error + 303, как у остальных ошибок источника.
        workspace_dir, display_label = create_ftp_workspace(ftp_config, label=connection_label)
    except (ValueError, OSError) as exc:
        with state_lock:
            state.error = str(exc)
        return RedirectResponse(url="/", status_code=303)

    with state_lock:
        start_workspace_job(workspace_dir, display_target=display_label)
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/ftp-source/add")
def add_ftp_source(
    host: str = Form(""),
    port: str = Form("21"),
    password: str = Form(""),
    path: str = Form("/datalog"),
    passive: str = Form(""),
    label: str = Form(""),
    web_scheme: str = Form(""),
) -> RedirectResponse:
    """Сохраняет панель в реестр БЕЗ открытия рабочей области (кнопка «Добавить
    панель»). Панель появляется в списке сохранённых; подключение — отдельным
    шагом (веб-просмотр / графики)."""
    try:
        ftp_config = normalize_ftp_connection_settings(
            {
                "host": host,
                "port": port,
                "password": password,
                "path": path,
                "passive": passive,
                "web_scheme": web_scheme,
            }
        )
    except ValueError as exc:
        with state_lock:
            state.error = str(exc)
        return RedirectResponse(url="/", status_code=303)
    upsert_ftp_connection(ftp_config, label=label)
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/ftp-source/connect")
def connect_ftp_source(source_id: str = Form(...)) -> RedirectResponse:
    """Помечает панель как подключённую (зелёная строка + WebView/Графики/
    Отключить в меню). Графики НЕ загружаются здесь — только по кнопке «Графики».
    Одновременно активна одна панель. Возвращаемся в меню."""
    saved_id = source_id.strip()
    if saved_id and find_ftp_connection(saved_id) is not None:
        with state_lock:
            state.connected_ftp_id = saved_id
    return RedirectResponse(url="/?view=menu", status_code=303)


@app.post("/workspace/ftp-source/rename")
def rename_ftp_source(
    source_id: str = Form(...), label: str = Form("")
) -> RedirectResponse:
    """Переименовывает сохранённую панель (правка названия в списке)."""
    saved_id = source_id.strip()
    if saved_id:
        rename_ftp_connection(saved_id, label)
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/ftp-source/delete")
def delete_ftp_source(source_id: str = Form(...)) -> RedirectResponse:
    saved_id = source_id.strip()
    if saved_id:
        with state_lock:
            current_root = state.selected_root or state.pending_root
            clears_active = current_root is not None and current_root.name == saved_id
            if clears_active:
                reset_workspace()
            # Снимаем пометку подключения, даже если графики не были загружены —
            # иначе connected_ftp_id залипает и повторно добавленная панель с тем
            # же id (host|port|user|path) покажется «подключённой».
            if state.connected_ftp_id == saved_id:
                state.connected_ftp_id = ""
        delete_ftp_connection(saved_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/workspace/reset")
def reset_workspace_route() -> RedirectResponse:
    with state_lock:
        reset_workspace()
    return RedirectResponse(url="/", status_code=303)


def start_refresh_job_locked() -> dict[str, Any] | None:
    """Перезапуск обработки текущего источника; вызывать под state_lock.
    Возвращает описание джоба либо None, если источник ещё не выбран."""
    target_root = state.selected_root or state.pending_root
    if target_root is None:
        return None

    display_target = (
        state.selected_display_root or state.pending_display_root or str(target_root.resolve())
    )
    start_workspace_job(target_root.resolve(), display_target=display_target)
    return serialize_job(state.workspace_job)


@app.post("/workspace/refresh")
def refresh_workspace_route() -> RedirectResponse:
    with state_lock:
        if start_refresh_job_locked() is None:
            state.error = "Сначала выберите источник данных."
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/workspace/refresh")
def refresh_workspace_api() -> JSONResponse:
    with state_lock:
        job = start_refresh_job_locked()
    if job is None:
        raise HTTPException(status_code=400, detail="Сначала выберите источник данных.")
    return JSONResponse({"ok": True, "job": job})


@app.get("/api/workspace-job")
def workspace_job_status() -> JSONResponse:
    with state_lock:
        return JSONResponse(serialize_job(state.workspace_job))


@app.get("/api/workspace-data")
def workspace_data() -> JSONResponse:
    # Под state_lock — только снимок ссылок на данные. Тяжёлая сборка строк и
    # чтение файла настроек идут снаружи: держать общий лок на время сборки
    # десятков тысяч словарей нельзя (на нём же ждут SSE и все другие запросы).
    with state_lock:
        snapshot = capture_state_snapshot()

    payload = build_workspace_payload(snapshot)
    payload["wash_rows"] = build_wash_rows_cached(
        snapshot.analysis, snapshot.analysis_revision, snapshot.object_name_overrides
    )
    payload["object_rows"] = build_object_rows(snapshot.object_name_overrides, snapshot.analysis)
    return JSONResponse(payload)


def snapshot_job_status() -> dict[str, Any]:
    with state_lock:
        return serialize_job(state.workspace_job)


@app.get("/api/workspace-job/stream")
async def workspace_job_status_stream() -> StreamingResponse:
    # Асинхронный опрос состояния задачи: не занимает поток из ограниченного
    # пула (раньше блокирующий sync-генератор мог его исчерпать). Блокирующий
    # state_lock берём в отдельном потоке (asyncio.to_thread) — если взять его
    # прямо в генераторе, ожидание лока встаёт на event loop и подвешивает весь
    # сервер, пока другой запрос держит лок. При обрыве соединения генератор
    # корректно отменяется.
    poll_interval = 0.5
    keepalive_ticks = max(1, int(WORKSPACE_JOB_STREAM_KEEPALIVE_SECONDS / poll_interval))

    async def event_stream() -> Any:
        last_payload: str | None = None
        idle_ticks = 0
        while True:
            snapshot = await asyncio.to_thread(snapshot_job_status)
            payload = json.dumps(snapshot, ensure_ascii=False)

            if payload != last_payload:
                last_payload = payload
                idle_ticks = 0
                yield f"data: {payload}\n\n"
                if not snapshot.get("active") and snapshot.get("status") in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    break
            else:
                idle_ticks += 1
                if idle_ticks >= keepalive_ticks:
                    idle_ticks = 0
                    yield ": keepalive\n\n"

            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/workspace-job/cancel")
def cancel_workspace_job() -> JSONResponse:
    with state_lock:
        if state.workspace_job is None or state.workspace_job.status not in {"running", "cancelling"}:
            return JSONResponse({"ok": False, "active": False})

        state.workspace_job.cancel_requested = True
        state.workspace_job.status = "cancelling"
        state.workspace_job.message = "Отменяю открытие папки."
    return JSONResponse({"ok": True, "active": True})


# Эндпоинты с синхронной записью на диск объявлены обычными `def`: Starlette
# выполняет их в пуле потоков, не блокируя событийный цикл.
@app.post("/api/object-name")
def update_object_name(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    try:
        channel = int(payload.get("channel"))
        object_id = int(payload.get("object_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Не удалось определить объект для переименования.") from exc

    if channel < 1 or channel > 5 or object_id < 1:
        raise HTTPException(status_code=400, detail="Укажите канал от 1 до 5 и object id от 1 и выше.")

    raw_name = str(payload.get("name") or "")
    normalized_name = " ".join(raw_name.split())
    mode = str(payload.get("mode") or "set").strip().lower()
    if mode not in {"create", "set", "reset"}:
        raise HTTPException(status_code=400, detail="Некорректный режим сохранения объекта.")

    if object_id > 30:
        raise HTTPException(status_code=400, detail="Object id должен быть в диапазоне от 1 до 30.")

    if mode != "reset":
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Название объекта не может быть пустым.")
        if len(normalized_name) > 120:
            raise HTTPException(status_code=400, detail="Название объекта не должно быть длиннее 120 символов.")

    with state_lock:
        if state.selected_root is None and state.pending_root is None:
            raise HTTPException(status_code=400, detail="Сначала выберите источник данных.")

        overrides = dict(state.object_name_overrides)
        if mode == "create" and (channel, object_id) in overrides:
            existing_name = overrides[(channel, object_id)]
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Для канала {channel} и object id {object_id} запись уже существует: "
                    f"«{existing_name}»."
                ),
            )

        if mode != "reset":
            overrides[(channel, object_id)] = normalized_name
        else:
            overrides.pop((channel, object_id), None)

        try:
            save_object_name_overrides(config.TEMP_ROOT, overrides)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Не удалось сохранить файл переименований: {exc}") from exc

        state.object_name_overrides = overrides
        if state.analysis is not None:
            apply_object_name_overrides(state.analysis, overrides)
            state.analysis_revision += 1
        resolved_name = resolve_object_name(channel, object_id, overrides)
        # Ответ собираем под тем же локом: снаружи состояние мог успеть сменить
        # другой запрос (или завершившийся джоб), и клиент получил бы список
        # объектов, не соответствующий только что сохранённому переименованию.
        object_rows = build_object_rows(state.object_name_overrides, state.analysis)

    return JSONResponse(
        {
            "ok": True,
            "mode": mode,
            "channel": channel,
            "object_id": object_id,
            "object_name": resolved_name,
            "has_json_name": (channel, object_id) in overrides,
            "is_custom_name": resolved_name != fallback_object_name(object_id),
            "object_rows": object_rows,
        }
    )


@app.post("/api/object-names-file/sync")
def sync_object_names_file() -> JSONResponse:
    with state_lock:
        analysis = require_analysis()

        existing_overrides = dict(state.object_name_overrides)
        path = object_name_overrides_path(config.TEMP_ROOT)
        file_existed = path.exists()
        next_overrides = build_seed_object_name_overrides(analysis, existing_overrides)
        added_entry_count = len(set(next_overrides.keys()) - set(existing_overrides.keys()))
        changed = next_overrides != existing_overrides or not file_existed

        if changed:
            try:
                save_object_name_overrides(config.TEMP_ROOT, next_overrides)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Не удалось сохранить файл переименований: {exc}") from exc

            state.object_name_overrides = next_overrides
            apply_object_name_overrides(analysis, next_overrides)
            state.analysis_revision += 1

        return JSONResponse(
            {
                "ok": True,
                "changed": changed,
                "created": not file_existed,
                "file_path": str(path),
                "entry_count": len(next_overrides),
                "added_entry_count": added_entry_count,
                "object_rows": build_object_rows(state.object_name_overrides, state.analysis),
            }
        )




@app.get("/api/chart-styles")
def get_chart_styles() -> JSONResponse:
    return JSONResponse(
        {
            "series": load_chart_style_settings(),
            "defaults": chart_style_defaults(),
        }
    )


@app.post("/api/chart-styles")
def update_chart_styles(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    series_styles = normalize_chart_style_series(payload.get("series"))
    try:
        save_chart_style_settings(series_styles)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось сохранить настройки графика: {exc}"
        ) from exc

    return JSONResponse({"ok": True, "series": series_styles})


@app.get("/api/settings")
def get_app_settings_route() -> JSONResponse:
    return JSONResponse({"settings": load_app_settings()})


@app.post("/api/settings")
def update_app_settings_route(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    source = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    if not isinstance(source, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")

    try:
        settings = update_app_settings(source)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось сохранить настройки: {exc}"
        ) from exc

    return JSONResponse({"ok": True, "settings": settings})


@app.post("/api/ftp/discover")
async def api_ftp_discover() -> JSONResponse:
    """Ищет панели (FTP-хосты) в локальной подсети. Только по нажатию кнопки —
    guard middleware уже ограничивает эндпоинт локальными запросами."""
    try:
        result = await discovery.discover_ftp_panels()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось выполнить поиск: {exc}"
        ) from exc
    return JSONResponse(result)


@app.post("/api/chart-cache/clear")
def clear_chart_cache_route() -> JSONResponse:
    try:
        removed = clear_all_chart_caches()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось очистить кэш графиков: {exc}"
        ) from exc
    return JSONResponse({"ok": True, "removed": removed})


@app.get("/api/diagnostics")
def get_diagnostics() -> JSONResponse:
    with state_lock:
        analysis = state.analysis
        selected_root = state.selected_root or state.pending_root
        display_root = (
            state.selected_display_root
            or state.pending_display_root
            or (str(selected_root) if selected_root else "")
        )
        scan = state.scan_summary
        last_sync = state.last_sync_ts
        last_cleanup = state.last_cleanup_ts
        error = state.error
        job_payload = serialize_job(state.workspace_job)
        summary = build_summary_payload(analysis, scan)

    source_kind = "none"
    if selected_root is not None:
        source_kind = "ftp" if is_ftp_profile(selected_root) else "folder"

    settings = load_app_settings()
    return JSONResponse(
        {
            "source_kind": source_kind,
            "display_root": display_root,
            "last_sync": core.format_ts(last_sync) if last_sync else "",
            "counts": {
                "cycles": summary["cycle_count"],
                "objects": summary["object_count"],
                "databases": summary["db_count"],
                "archives": scan.archive_count,
                "ftp_sources": scan.ftp_source_count,
            },
            "auto_refresh": {
                "enabled": settings["ftp_auto_refresh_enabled"],
                "minutes": settings["ftp_auto_refresh_minutes"],
            },
            "datalog": {
                "size_bytes": datalog_size_bytes_cached(),
                "last_cleanup": core.format_ts(last_cleanup) if last_cleanup else "",
                "retention_enabled": settings["archive_retention_enabled"],
                "retention_days": settings["archive_retention_days"],
            },
            "job": {
                "active": job_payload["active"],
                "status": job_payload["status"],
                "message": job_payload["message"],
            },
            "error": error or "",
        }
    )


@app.post("/api/archives/cleanup")
def cleanup_archives_now() -> JSONResponse:
    settings = load_app_settings()
    days = settings["archive_retention_days"]
    with state_lock:
        target_root = state.selected_root or state.pending_root
        job = state.workspace_job
        job_active = job is not None and job.status in {"running", "cancelling"}

    # Удалять архивы под работающим анализом нельзя: файл исчезает прямо во время
    # чтения и джоб падает. Очистка и так выполняется в конце каждой синхронизации.
    if job_active:
        raise HTTPException(
            status_code=409,
            detail="Идёт обработка источника. Дождитесь её завершения и повторите очистку.",
        )

    if target_root is None:
        raise HTTPException(status_code=400, detail="Нет активного источника данных.")
    if not is_ftp_profile(target_root):
        raise HTTPException(
            status_code=400, detail="Очистка доступна только для FTP-источника (папка datalog)."
        )

    result = cleanup_old_archives(target_root, days)
    if result["removed"]:
        with state_lock:
            state.last_cleanup_ts = time.time()
    return JSONResponse({"ok": True, "days": days, **result})


# Строгая форма для тега, который подставляется в ИМЯ ФАЙЛА на диске: только цифры
# и точки, без пробелов, слэшей и суффиксов.
_SAFE_VERSION_RE = re.compile(r"\d+(?:\.\d+){0,3}")


@app.post("/api/update/download")
def update_download() -> JSONResponse:
    # Слот задачи резервируем ПОД ТЕМ ЖЕ локом, что и проверку «уже качается?».
    # Раньше между ними отпускался лок на updates._fetch_latest_release() (секунды сети),
    # и два POST (двойной клик) проходили проверку оба: стартовали два воркера на
    # один и тот же .part, каждый удалял времянку другого, и пользователь получал
    # «Не удалось скачать обновление» на ровном месте.
    with state_lock:
        active = state.update_job
        if active is not None and active.status == "running":
            return JSONResponse({"ok": True, "job": updates._serialize_update_job(active)})
        # Готовый к установке результат — единственное, что имеет смысл вернуть
        # вместо перекачки; годность проверяем ниже, когда узнаем свежий тег.
        previous = active if active is not None and active.status == "ready" else None
        job = UpdateJob(id=uuid.uuid4().hex)
        state.update_job = job

    def release_slot() -> None:
        """Снять резерв, если его никто не вытеснил. Без этого любой выход по
        ошибке оставил бы задачу в running навсегда: проверка выше запретила бы
        повтор до перезапуска приложения."""
        with state_lock:
            if state.update_job is job:
                state.update_job = previous

    try:
        payload = updates._fetch_latest_release()
        latest = updates._release_tag(payload)
        if not latest:
            raise HTTPException(status_code=502, detail="Не удалось получить сведения о релизе.")
        if not updates._is_newer_version(latest, APP_VERSION):
            raise HTTPException(status_code=400, detail="Установлена последняя версия.")
        # Тег попадёт в имя файла на диске, поэтому проверяем его форму так же
        # строго, как URL вложения: «1.2 (hotfix)» или тег со слэшем иначе уронит
        # open() под невнятным FileNotFoundError вместо честной ошибки.
        if not _SAFE_VERSION_RE.fullmatch(latest):
            raise HTTPException(
                status_code=502, detail=f"Непригодный номер версии в релизе: {latest}"
            )
        asset = updates._pick_installer_asset(payload)
        if asset is None:
            raise HTTPException(
                status_code=502, detail="В релизе нет установщика с контрольной суммой."
            )
    except Exception:
        release_slot()
        raise

    # Тот же релиз уже скачан и проверен (например, после промаха по UAC) —
    # 22 МБ по сети ради имеющегося файла ни к чему.
    if previous is not None and previous.version == latest and previous.path:
        if Path(previous.path).is_file():
            with state_lock:
                if state.update_job is job:
                    state.update_job = previous
            return JSONResponse({"ok": True, "job": updates._serialize_update_job(previous)})

    with state_lock:
        if state.update_job is not job:
            # Задачу вытеснила другая — не мешаем ей.
            return JSONResponse({"ok": True, "job": updates._serialize_update_job(state.update_job)})
        job.version = latest
        job.total = asset["size"]
    logging.info(
        "Начинаю скачивание обновления %s → %s (%s Б) с %s",
        APP_VERSION,
        latest,
        asset["size"],
        asset["url"],
    )
    threading.Thread(
        target=updates.download_update_worker,
        args=(job.id, asset, latest),
        name="update-download",
        daemon=True,
    ).start()
    return JSONResponse({"ok": True, "job": updates._serialize_update_job(job)})


@app.get("/api/update/job")
def update_job_status() -> JSONResponse:
    with state_lock:
        return JSONResponse(updates._serialize_update_job(state.update_job))


@app.get("/api/update-check")
def update_check() -> JSONResponse:
    """Разовая сверка версии с последним релизом — вызывается кнопкой в
    настройках. Автоматически при старте не дёргается: за спиной у пользователя
    в сеть не ходим."""
    payload = updates._fetch_latest_release()
    latest = updates._release_tag(payload)
    available = bool(latest) and updates._is_newer_version(latest, APP_VERSION)
    # Установить «в один клик» можно только собранную Windows-версию: ставит
    # .exe-установщик, а закрыть окно и перезапуститься умеет лишь десктоп-мост.
    installable = available and os.name == "nt" and updates._pick_installer_asset(payload) is not None
    return JSONResponse(
        {
            "current": APP_VERSION,
            # Пустой latest — «не выяснили» (нет сети/релизов), а не «актуально».
            "latest": latest,
            "update_available": available,
            "url": UPDATE_RELEASES_URL if available else "",
            "installable": installable,
        }
    )


@app.get("/api/wash-details")
def wash_details(key: str) -> JSONResponse:
    # Снимок анализа берём под локом, тяжёлую сборку (чтение настроек с диска,
    # оценка концентрации) делаем снаружи — на state_lock ждут SSE и все запросы.
    with state_lock:
        analysis = require_analysis()
    return JSONResponse(build_wash_detail(analysis, key))


@app.get("/api/wash-chart-data")
def wash_chart_data(key: str) -> JSONResponse:
    with state_lock:
        analysis = require_analysis()
        cycle = find_cycle(analysis, key)
        analysis_revision = state.analysis_revision
        analysis_cache_key = analysis.analysis_cache_key

    cached_payload = get_cached_chart_payload(analysis_revision, key)
    if cached_payload is not None:
        return JSONResponse(cached_payload)

    cached_payload = load_cached_chart_payload_disk(analysis_cache_key, key)
    if cached_payload is not None:
        set_cached_chart_payload(analysis_revision, key, cached_payload)
        return JSONResponse(cached_payload)

    payload = build_cycle_chart_payload(analysis, cycle)
    set_cached_chart_payload(analysis_revision, key, payload)
    save_cached_chart_payload_disk(analysis_cache_key, key, payload)
    return JSONResponse(payload)
