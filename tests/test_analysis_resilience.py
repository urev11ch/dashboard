"""Тесты устойчивости анализа: повреждённая база пропускается, а не валит джоб,
и пользователь узнаёт о пропущенных файлах."""
import sqlite3
from pathlib import Path

import pytest

import webapp.app as app


class _Chunk:
    def __init__(self, db_path):
        self.db_path = db_path


class _Analysis:
    def __init__(self, db_files):
        self.db_files = db_files
        self.output_dir = None
        self.analysis_cache_key = ""


@pytest.fixture
def isolated_analysis(tmp_path, monkeypatch):
    """Анализ без дискового кэша и без обращения к реальным SQLite-файлам."""
    monkeypatch.setattr(app.analysis, "prune_analysis_cache", lambda: None)
    monkeypatch.setattr(app.analysis, "load_cached_db_analysis", lambda path: None)
    monkeypatch.setattr(app.analysis, "save_cached_db_analysis", lambda path, chunk: None)
    monkeypatch.setattr(app.analysis, "load_cached_workspace_analysis", lambda key: None)
    monkeypatch.setattr(app.analysis, "save_cached_workspace_analysis", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        app.core, "analyze_single_db_file", lambda db_path, **kwargs: _Chunk(db_path)
    )
    monkeypatch.setattr(
        app.core, "build_analysis_result", lambda db_paths, **kwargs: _Analysis(db_paths)
    )
    return tmp_path


def _db(root, name):
    path = root / name
    path.write_bytes(b"sqlite-ish")
    return path


def test_corrupted_db_is_skipped_and_reported(isolated_analysis, monkeypatch):
    good = _db(isolated_analysis, "Canal_1.db")
    broken = _db(isolated_analysis, "Canal_2.db")

    def fake_preflight(db_path):
        # Ядро сообщает о битом файле через SystemExit.
        if db_path.name == "Canal_2.db":
            raise SystemExit("Файл Canal_2.db повреждён.")
        return 1

    monkeypatch.setattr(app.core, "preflight_db_file", fake_preflight)

    analysis, skipped = app.analyze_db_files_incremental(
        [good, broken], output_dir=isolated_analysis
    )

    assert skipped == ["Canal_2.db"]
    assert analysis.db_files == [good]  # исправная база проанализирована


def test_sqlite_error_in_preflight_does_not_fail_job(isolated_analysis, monkeypatch):
    good = _db(isolated_analysis, "Canal_1.db")
    broken = _db(isolated_analysis, "Canal_2.db")

    def fake_preflight(db_path):
        if db_path.name == "Canal_2.db":
            raise sqlite3.DatabaseError("file is not a database")
        return 1

    monkeypatch.setattr(app.core, "preflight_db_file", fake_preflight)

    analysis, skipped = app.analyze_db_files_incremental(
        [good, broken], output_dir=isolated_analysis
    )
    assert skipped == ["Canal_2.db"]
    assert analysis.db_files == [good]


def test_broken_db_during_analysis_is_skipped(isolated_analysis, monkeypatch):
    good = _db(isolated_analysis, "Canal_1.db")
    broken = _db(isolated_analysis, "Canal_2.db")

    monkeypatch.setattr(app.core, "preflight_db_file", lambda db_path: 1)

    def fake_analyze(db_path, **kwargs):
        if db_path.name == "Canal_2.db":
            raise sqlite3.DatabaseError("database disk image is malformed")
        return _Chunk(db_path)

    monkeypatch.setattr(app.core, "analyze_single_db_file", fake_analyze)

    analysis, skipped = app.analyze_db_files_incremental(
        [good, broken], output_dir=isolated_analysis
    )
    assert skipped == ["Canal_2.db"]
    assert analysis.db_files == [good]


def test_all_db_files_broken_fails_with_names(isolated_analysis, monkeypatch):
    broken = _db(isolated_analysis, "Canal_1.db")

    def fake_preflight(db_path):
        raise SystemExit("повреждён")

    monkeypatch.setattr(app.core, "preflight_db_file", fake_preflight)

    with pytest.raises(SystemExit) as excinfo:
        app.analyze_db_files_incremental([broken], output_dir=isolated_analysis)
    message = str(excinfo.value)
    assert "Canal_1.db" in message
    # Причина должна быть в самом сообщении: без неё оставалось идти в desktop.log.
    assert "повреждён" in message


def test_all_db_files_broken_message_groups_same_reason(isolated_analysis, monkeypatch):
    """Каталог с одинаковой структурой лога: причина одна на всех, показываем её
    один раз с числом файлов."""
    files = [_db(isolated_analysis, f"Canal_{index}.db") for index in range(1, 4)]

    def fake_preflight(db_path):
        raise SystemExit(f"Файл {Path(db_path).name} не содержит таблицу `data`.")

    monkeypatch.setattr(app.core, "preflight_db_file", fake_preflight)

    with pytest.raises(SystemExit) as excinfo:
        app.analyze_db_files_incremental(files, output_dir=isolated_analysis)
    message = str(excinfo.value)
    assert "не содержит таблицу `data` (таких файлов: 3)" in message
    # Имя файла из причины убрано — иначе одинаковые причины не схлопнулись бы.
    assert message.count("не содержит таблицу") == 1


def test_all_db_files_broken_message_limits_reasons(isolated_analysis, monkeypatch):
    """Разных причин много — показываем две, остальные считаем."""
    files = [_db(isolated_analysis, f"Canal_{index}.db") for index in range(1, 5)]

    def fake_preflight(db_path):
        raise SystemExit(f"причина {Path(db_path).name}")

    monkeypatch.setattr(app.core, "preflight_db_file", fake_preflight)

    with pytest.raises(SystemExit) as excinfo:
        app.analyze_db_files_incremental(files, output_dir=isolated_analysis)
    assert "и ещё 2 — см. desktop.log." in str(excinfo.value)


def test_describe_skip_reason_strips_file_name():
    error = SystemExit("Файл Canal_1.db не содержит таблицу `data`.")
    assert app.analysis.describe_skip_reason(Path("Canal_1.db"), error) == (
        "не содержит таблицу `data`"
    )
    # Причина без префикса «Файл <имя>» остаётся как есть.
    other = OSError("Отказано в доступе")
    assert app.analysis.describe_skip_reason(Path("Canal_1.db"), other) == "Отказано в доступе"
    # Пустой текст исключения — показываем хотя бы тип.
    assert app.analysis.describe_skip_reason(Path("Canal_1.db"), OSError()) == "OSError"


def test_format_skip_reasons_without_reasons():
    assert app.analysis.format_skip_reasons([]) == "Проверьте, что файлы не повреждены."


def test_completion_message_mentions_skipped_and_failed():
    summary = app.ScanSummary(
        skipped_db_files=["Canal_2.db"],
        ftp_failed_files=["Canal_9.db", "Canal_10.db"],
    )
    message = app.build_job_completion_message(summary)
    assert "пропущено баз: 1" in message
    assert "не скачано файлов с FTP: 2" in message

    warnings = app.build_scan_warnings(summary)
    assert len(warnings) == 2

    clean = app.build_job_completion_message(app.ScanSummary())
    assert clean == "Данные успешно обновлены."
    assert app.build_scan_warnings(app.ScanSummary()) == []


def test_format_file_list_truncates():
    assert app.format_file_list(["a", "b"]) == "`a`, `b`"
    assert app.format_file_list(["a", "b", "c", "d", "e"]) == "`a`, `b`, `c` и ещё 2"
