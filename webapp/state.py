"""Глобальное состояние приложения и его блокировки.

Единый разделяемый экземпляр `state` и локи, которые импортируют и app.py, и
сервисные модули (updates, cache и т. д.). Так все видят один и тот же объект
состояния, а мутации из фонового потока видны в роутах. Выделено из webapp/app.py.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import wash_report as core


@dataclass
class ScanSummary:
    archive_count: int = 0
    ftp_source_count: int = 0
    # Файлы, которые не удалось скачать с FTP, и текст сбоя синхронизации:
    # показываем их пользователю, а не «глотаем» (см. materialize_ftp_sources).
    ftp_failed_files: list[str] = field(default_factory=list)
    ftp_error: str = ""
    # Базы, пропущенные при анализе (повреждены/не подходят по структуре).
    skipped_db_files: list[str] = field(default_factory=list)


@dataclass
class WorkspaceJob:
    id: str
    target_root: Path | None = None
    display_target: str = ""
    status: str = "running"
    phase: str = "queued"
    message: str = "Подготавливаю анализ источника."
    current: int = 0
    total: int = 0
    item: str = ""
    error: str | None = None
    cancel_requested: bool = False
    background: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


@dataclass
class UpdateJob:
    """Скачивание установщика обновления. `path` заполняется только после
    успешной сверки sha256 — мост берёт оттуда файл на запуск, поэтому непустой
    path означает «проверено и можно исполнять»."""

    id: str
    version: str = ""
    status: str = "running"  # running | ready | error
    phase: str = "download"  # download | verify | ready
    downloaded: int = 0
    total: int = 0
    path: str = ""
    # Ожидаемый sha256 проверенного файла. Мост (install_update) пересчитывает
    # хеш файла на диске непосредственно перед запуском и сверяет с этим полем —
    # закрываем окно TOCTOU между проверкой при скачивании и запуском .exe
    # (на Windows каталог кэша пишется правами пользователя, файл можно подменить).
    sha256: str = ""
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


@dataclass
class AppState:
    selected_root: Path | None = None
    pending_root: Path | None = None
    selected_display_root: str = ""
    pending_display_root: str = ""
    analysis: core.AnalysisResult | None = None
    analysis_revision: int = 0
    object_name_overrides: dict[tuple[int, int], str] = field(default_factory=dict)
    error: str | None = None
    scan_summary: ScanSummary = field(default_factory=ScanSummary)
    workspace_job: WorkspaceJob | None = None
    update_job: UpdateJob | None = None
    last_sync_ts: float | None = None
    last_cleanup_ts: float | None = None
    # Панель, выбранная кнопкой «Подключиться» (зелёная строка + WebView/Графики/
    # Отключить в меню). Сессионное состояние, одна панель одновременно.
    connected_ftp_id: str = ""


@dataclass(frozen=True)
class AppStateSnapshot:
    analysis: core.AnalysisResult | None
    analysis_revision: int
    selected_root: Path | None
    pending_root: Path | None
    selected_display_root: str
    pending_display_root: str
    object_name_overrides: dict[tuple[int, int], str]
    error: str | None
    scan_summary: ScanSummary
    workspace_job_payload: dict[str, Any]
    connected_ftp_id: str


state = AppState()
state_lock = threading.Lock()
# Активный рабочий поток анализа источника. Здесь (а не в app.py), чтобы удаление
# папки профиля (ftp_registry.remove_ftp_profile_dir) могло дождаться его
# завершения, не завися от app.py. Присваивается через state.<модуль>-атрибут
# (start_workspace_job), поэтому читатели должны брать его как атрибут модуля.
_workspace_job_thread: "threading.Thread | None" = None
# Настройки читаются-меняются-пишутся (частичное обновление), поэтому у файла
# настроек свой лок — иначе параллельные POST /api/settings теряют изменения.
# RLock: save_app_settings вызывается и сам по себе, и изнутри секции.
app_settings_lock = threading.RLock()
archive_cache_lock = threading.Lock()
analysis_cache_lock = threading.Lock()
chart_payload_cache_lock = threading.Lock()

# Эксклюзивный лок на запись зеркала КОНКРЕТНОГО профиля (datalog/<id>). Гарантирует
# одного писателя даже если старый рабочий поток «завис»: цепочка join имеет дыру
# (таймаут join → зависший поток выпадает, новый джоб той же панели начинает писать
# параллельно → битые .db). Лок закрывает это в точке опасности — самой записи.
# Здесь (в state) — чтобы им пользовались и ftp_client (запись), и ftp_registry
# (rmtree при удалении панели), не создавая циклический импорт.
_mirror_write_locks: dict[str, threading.Lock] = {}
_mirror_write_locks_guard = threading.Lock()


def mirror_write_lock(profile_key: str) -> threading.Lock:
    """Лок на запись зеркала профиля по ключу (обычно str(resolved datalog/<id>))."""
    with _mirror_write_locks_guard:
        lock = _mirror_write_locks.get(profile_key)
        if lock is None:
            lock = threading.Lock()
            _mirror_write_locks[profile_key] = lock
        return lock
