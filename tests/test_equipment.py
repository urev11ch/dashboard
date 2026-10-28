"""Тип оборудования (CIP / ПОУ): определение по подписям тегов, реестр папок,
сводка для карточек главного экрана."""
import sqlite3
import time
from pathlib import Path

import pytest

import webapp.app as app
from webapp import equipment, folder_sources, source_stats
from webapp.state import PouWorkspace

FIXTURES = Path(__file__).resolve().parent / "e2e" / "fixtures"
POU_LABELS = [
    "Процесс",
    "Шаг",
    "Состояние клапана возвратного",
    "Температура пастеризации - основная",
    "Температура пастеризации - контрольная",
    "Температура продукта на выходе",
]


def make_db(path: Path, labels: list[str], rows: int = 3) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE data_format (data_format_index INTEGER, comment TEXT, data_type TEXT, size INTEGER)")
    connection.executemany(
        "INSERT INTO data_format VALUES (?, ?, 'REAL32', 1)", list(enumerate(labels))
    )
    columns = ", ".join(f"data_format_{i} REAL" for i in range(len(labels)))
    connection.execute(f'CREATE TABLE data ("time@timestamp" REAL, {columns})')
    connection.executemany(
        f"INSERT INTO data VALUES ({', '.join('?' * (len(labels) + 1))})",
        [(1783900000.0 + i * 10, *([float(i)] * len(labels))) for i in range(rows)],
    )
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    root = tmp_path / "temp"
    root.mkdir()
    monkeypatch.setattr(app.config, "TEMP_ROOT", root)
    return root


# ---- определение типа ----------------------------------------------------
def test_pou_labels_classified_as_pou():
    # «Процесс» есть и у мойки: признаки ПОУ обязаны проверяться первыми.
    assert equipment.classify_comments(dict(enumerate(POU_LABELS))) == "pou"


def test_wash_fixture_classified_as_cip():
    assert equipment.classify_db_file(FIXTURES / "Canal_1_20260713.db") == "cip"


@pytest.mark.parametrize(
    "labels",
    [[], ["Процесс", "Температура"], ["Расход", "Давление"]],
)
def test_unrecognized_labels_are_none(labels):
    assert equipment.classify_comments(dict(enumerate(labels))) is None


def test_broken_file_is_none(tmp_path):
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database")
    assert equipment.classify_db_file(broken) is None


def test_normalize_equipment_defaults_to_cip():
    assert equipment.normalize_equipment("POU") == "pou"
    assert equipment.normalize_equipment("") == "cip"
    assert equipment.normalize_equipment("milk") == "cip"


def test_split_cip_mode_keeps_unknown_and_moves_pou(tmp_path):
    cip = FIXTURES / "Canal_1_20260713.db"
    pou = make_db(tmp_path / "pou.db", POU_LABELS)
    unknown = make_db(tmp_path / "unknown.db", ["Расход"])
    own, foreign, dropped, other = equipment.split_db_files_by_equipment([cip, pou, unknown], "cip")
    # Нераспознанное в CIP идёт в разбор мойки как раньше (позиционная раскладка).
    assert own == [cip, unknown]
    assert foreign == [pou] and other == "pou" and dropped == []


def test_split_pou_mode_drops_unknown(tmp_path):
    cip = FIXTURES / "Canal_1_20260713.db"
    pou = make_db(tmp_path / "pou.db", POU_LABELS)
    unknown = make_db(tmp_path / "unknown.db", ["Расход"])
    own, foreign, dropped, other = equipment.split_db_files_by_equipment([cip, pou, unknown], "pou")
    assert own == [pou]
    assert foreign == [cip] and other == "cip"
    assert dropped == [unknown]


# ---- сохранённые папки ---------------------------------------------------
def test_add_folder_guesses_type_and_keeps_files(temp_root, tmp_path):
    folder = tmp_path / "pou_archive"
    folder.mkdir()
    db = make_db(folder / "POU.db", POU_LABELS)
    entry = folder_sources.add_folder_source(folder)
    assert entry["equipment"] == "pou"
    assert entry["label"] == "pou_archive"
    assert [row["id"] for row in folder_sources.list_folder_sources()] == [entry["id"]]

    assert folder_sources.delete_folder_source(entry["id"])
    assert folder_sources.list_folder_sources() == []
    assert db.exists()  # убрать из списка ≠ удалить архивы


def test_readding_folder_keeps_type_and_label(temp_root, tmp_path):
    folder = tmp_path / "archive"
    folder.mkdir()
    entry = folder_sources.add_folder_source(folder, label="Цех 3", equipment="pou")
    again = folder_sources.add_folder_source(folder)
    assert again["id"] == entry["id"]
    assert again["equipment"] == "pou"
    assert again["label"] == "Цех 3"


def test_rename_and_set_equipment(temp_root, tmp_path):
    folder = tmp_path / "archive"
    folder.mkdir()
    entry = folder_sources.add_folder_source(folder)
    assert folder_sources.rename_folder_source(entry["id"], "Новое имя")
    assert folder_sources.set_folder_equipment(entry["id"], "pou")
    row = folder_sources.find_folder_source(entry["id"])
    assert row["label"] == "Новое имя" and row["equipment"] == "pou"


def test_last_folder_path_migrates_once(temp_root, tmp_path):
    folder = tmp_path / "legacy"
    folder.mkdir()
    app.save_last_folder_path(str(folder))
    rows = folder_sources.list_folder_sources()
    assert [row["path"] for row in rows] == [str(folder.resolve())]
    # Убранная пользователем папка не возвращается миграцией.
    folder_sources.delete_folder_source(rows[0]["id"])
    assert folder_sources.list_folder_sources() == []


def test_missing_folder_is_flagged(temp_root, tmp_path):
    folder = tmp_path / "gone"
    folder.mkdir()
    folder_sources.add_folder_source(folder)
    folder.rmdir()
    assert folder_sources.list_folder_sources()[0]["exists"] is False


# ---- FTP-панели: тип не теряется при повторном сохранении --------------------
def test_ftp_upsert_preserves_equipment(temp_root):
    config = app.normalize_ftp_connection_settings(
        {"host": "192.168.1.95", "port": "21", "password": "1", "path": "/datalog"}
    )
    entry = app.upsert_ftp_connection(config, label="ПОУ-1", equipment="pou")
    app.upsert_ftp_connection(config, label="ПОУ-1")
    rows = app.list_ftp_sources_public()
    assert rows[0]["id"] == entry["id"] and rows[0]["equipment"] == "pou"
    assert app.set_ftp_equipment(entry["id"], "cip")
    assert app.list_ftp_sources_public()[0]["equipment"] == "cip"


# ---- сводка карточек -----------------------------------------------------
NOW_PANEL = 1784019600.0 + 3600  # 14.07.2026 10:00 по часам панели


def test_card_summary_counts_week_and_checks(temp_root):
    rows = [
        {"start_ts": NOW_PANEL - 3600, "result_kind": "completed"},
        {"start_ts": NOW_PANEL - 2 * 86400, "result_kind": "check"},
        {"start_ts": NOW_PANEL - 10 * 86400, "result_kind": "check"},  # старше недели
    ]
    source_stats.record_cip_stats("folder:x", 1, rows, time.time())
    summary = source_stats.summarize_for_card(source_stats.load_source_stats()["folder:x"], NOW_PANEL)
    assert summary["headline"] == "Последняя мойка сегодня 09:00"
    assert summary["detail"] == "2 мойки за неделю"
    assert summary["verdict"] == "1 требует проверки" and summary["verdict_kind"] == "warn"


def test_card_summary_all_ok(temp_root):
    source_stats.record_cip_stats("ftp:a", 1, [{"start_ts": NOW_PANEL - 60, "result_kind": "completed"}], None)
    summary = source_stats.summarize_for_card(source_stats.load_source_stats()["ftp:a"], NOW_PANEL)
    assert summary["verdict"] == "Все завершены штатно" and summary["verdict_kind"] == "ok"


def test_card_summary_not_rewritten_for_same_revision(temp_root):
    source_stats.record_cip_stats("folder:y", 7, [{"start_ts": NOW_PANEL, "result_kind": "check"}], None)
    source_stats.record_cip_stats("folder:y", 7, [], None)  # та же ревизия — без записи
    assert source_stats.load_source_stats()["folder:y"]["cycle_count"] == 1


def test_never_opened_source_has_no_summary():
    assert source_stats.summarize_for_card(None) is None


def test_pou_card_summary(temp_root):
    workspace = PouWorkspace(db_files=[Path("a.db"), Path("b.db")], end_ts=NOW_PANEL - 86400, row_count=10)
    source_stats.record_pou_stats("folder:p", 1, workspace, None)
    summary = source_stats.summarize_for_card(source_stats.load_source_stats()["folder:p"], NOW_PANEL)
    assert summary["headline"].startswith("Данные по вчера")
    assert summary["detail"] == "2 базы"


@pytest.mark.parametrize(
    "seconds, expected",
    [(10, "только что"), (300, "5 мин назад"), (7200, "2 ч назад")],
)
def test_format_ago(seconds, expected):
    now = 1_800_000_000.0
    assert source_stats.format_ago(now - seconds, now) == expected


# ---- задача обработки: режим ПОУ и несовпадение типа -------------------------
from webapp import analysis
from webapp.state import ScanSummary, WorkspaceJob


def run_job(monkeypatch, root: Path, db_files: list[Path], equipment_mode: str) -> WorkspaceJob:
    """Синхронный прогон run_workspace_job без поиска по диску и без кэшей."""
    monkeypatch.setattr(analysis, "discover_db_files", lambda *a, **k: (list(db_files), ScanSummary()))
    job = WorkspaceJob(id="t", target_root=root, equipment=equipment_mode, source_key="folder:t")
    with app.state_lock:
        app.reset_workspace()
        app.state.workspace_job = job
        app.state.workspace_equipment = equipment_mode
    analysis.run_workspace_job("t", root)
    return job


def test_pou_mode_builds_pou_workspace(monkeypatch, tmp_path):
    pou = make_db(tmp_path / "pou.db", POU_LABELS, rows=5)
    cip = FIXTURES / "Canal_1_20260713.db"
    job = run_job(monkeypatch, tmp_path, [pou, cip], "pou")
    assert job.status == "completed"
    workspace = app.state.pou_workspace
    assert workspace is not None and app.state.analysis is None
    assert [col.label for col in workspace.columns] == POU_LABELS
    assert workspace.row_count == 5
    # Архив мойки в источнике ПОУ — «другое оборудование», а не «пропущен».
    assert app.state.scan_summary.foreign_db_files == [cip.name]
    assert app.state.scan_summary.foreign_equipment == "cip"
    assert app.state.scan_summary.skipped_db_files == []


def test_only_other_equipment_is_mismatch(monkeypatch, tmp_path):
    pou = make_db(tmp_path / "pou.db", POU_LABELS)
    job = run_job(monkeypatch, tmp_path, [pou], "cip")
    assert job.status == "failed"
    assert "найдены архивы ПОУ" in job.message
    mismatch = app.state.equipment_mismatch
    assert mismatch["equipment"] == "pou" and mismatch["count"] == 1
    assert mismatch["source_key"] == "folder:t"


def test_pou_trend_payload_groups_panels(tmp_path):
    from webapp.pou import build_pou_trend_payload, describe_pou_files

    workspace, skipped = describe_pou_files([make_db(tmp_path / "pou.db", POU_LABELS, rows=20)])
    assert skipped == []
    payload = build_pou_trend_payload(workspace, workspace.start_ts, workspace.end_ts)
    assert payload["has_data"] is True
    assert [panel["label"] for panel in payload["panels"]] == ["Температура", "Клапан возврата", "Процесс и шаг"]
    by_label = {series["label"]: series for series in payload["series"]}
    assert by_label["Температура продукта на выходе"]["panel"] == 0
    assert by_label["Состояние клапана возвратного"]["panel"] == 1
    assert by_label["Шаг"]["panel"] == 2
    assert all(len(series["points"]) == 20 for series in payload["series"])
    # Период вне данных — пустой тренд, а не ошибка.
    assert build_pou_trend_payload(workspace, 0, 1)["has_data"] is False
