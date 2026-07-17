"""Тесты ограничения дискового кэша: TTL, бюджет с LRU-эвикцией и удаление
предыдущей версии записи того же источника."""
import os
import sqlite3
import time

import pytest

import webapp.app as app
import wash_report as core


def _write_entry(root, name, size, age_seconds):
    path = root / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_prune_removes_expired_entries(tmp_path):
    old = _write_entry(tmp_path, "old.pkl", 10, age_seconds=10 * 86400)
    fresh = _write_entry(tmp_path, "fresh.pkl", 10, age_seconds=60)

    app.prune_cache_root(tmp_path, ttl_seconds=7 * 86400, max_bytes=10**9, max_entries=1000)

    assert not old.exists()
    assert fresh.exists()


def test_prune_evicts_least_recently_used_over_size_budget(tmp_path):
    # Автообновление FTP плодит новые записи (ключ зависит от mtime+size), поэтому
    # поверх TTL работает бюджет: лишнее вытесняется, начиная с давно не
    # использованных (mtime = время последнего попадания в кэш).
    oldest = _write_entry(tmp_path, "a.pkl", 100, age_seconds=300)
    middle = _write_entry(tmp_path, "b.pkl", 100, age_seconds=200)
    newest = _write_entry(tmp_path, "c.pkl", 100, age_seconds=100)

    app.prune_cache_root(tmp_path, ttl_seconds=10**9, max_bytes=250, max_entries=1000)

    assert not oldest.exists()
    assert middle.exists() and newest.exists()


def test_prune_evicts_over_entry_budget(tmp_path):
    for index in range(5):
        _write_entry(tmp_path, f"e{index}.pkl", 10, age_seconds=100 - index)

    app.prune_cache_root(tmp_path, ttl_seconds=10**9, max_bytes=10**9, max_entries=2)

    remaining = sorted(path.name for path in tmp_path.iterdir())
    assert remaining == ["e3.pkl", "e4.pkl"]  # остались два самых свежих


def test_prune_evicts_directories_by_size(tmp_path):
    stale_dir = tmp_path / "archive-old"
    stale_dir.mkdir()
    (stale_dir / "a.db").write_bytes(b"x" * 200)
    stamp = time.time() - 300
    os.utime(stale_dir, (stamp, stamp))
    fresh = _write_entry(tmp_path, "fresh.pkl", 50, age_seconds=10)

    app.prune_cache_root(tmp_path, ttl_seconds=10**9, max_bytes=100, max_entries=1000)

    assert not stale_dir.exists()
    assert fresh.exists()


def test_prune_keeps_pending_temp_entries(tmp_path):
    # `<key>.tmp-<uuid>` — незавершённая распаковка в другом потоке, не трогаем.
    pending = _write_entry(tmp_path, "abc.tmp-0123456789", 500, age_seconds=10 * 86400)

    app.prune_cache_root(tmp_path, ttl_seconds=10**9, max_bytes=1, max_entries=0)

    assert pending.exists()


def test_touch_marks_entry_as_recently_used(tmp_path):
    keep = _write_entry(tmp_path, "keep.pkl", 100, age_seconds=300)
    drop = _write_entry(tmp_path, "drop.pkl", 100, age_seconds=200)

    app.touch_cache_entry(keep)  # обращение к записи «омолаживает» её
    app.prune_cache_root(tmp_path, ttl_seconds=10**9, max_bytes=100, max_entries=1000)

    assert keep.exists()
    assert not drop.exists()


def test_stale_workspace_cache_replaces_previous_version(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ANALYSIS_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(app, "workspace_cache_keys_by_source", app.OrderedDict())
    source = "/data/panel"

    old_key = "a" * 40
    new_key = "b" * 40
    old_analysis = app.workspace_analysis_cache_path(old_key)
    old_analysis.write_bytes(b"old")
    old_chart = app.chart_payload_disk_cache_path(old_key, "cycle-1")
    old_chart.write_bytes(b"chart")

    app.cleanup_stale_workspace_cache(source, old_key)  # первая регистрация
    assert old_analysis.exists() and old_chart.exists()

    new_analysis = app.workspace_analysis_cache_path(new_key)
    new_analysis.write_bytes(b"new")
    app.cleanup_stale_workspace_cache(source, new_key)

    # Прежняя версия анализа этого же источника и её графики удалены.
    assert not old_analysis.exists()
    assert not old_chart.exists()
    assert new_analysis.exists()


def test_stale_db_cache_replaces_previous_version(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ANALYSIS_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(app, "db_cache_keys_by_source", app.OrderedDict())
    db_path = tmp_path / "Canal_1.db"

    old_entry = app.db_analysis_cache_path("1" * 40)
    old_entry.write_bytes(b"old")
    app.cleanup_stale_db_analysis_cache(db_path, "1" * 40)

    new_entry = app.db_analysis_cache_path("2" * 40)
    new_entry.write_bytes(b"new")
    app.cleanup_stale_db_analysis_cache(db_path, "2" * 40)

    assert not old_entry.exists()
    assert new_entry.exists()


def test_cache_source_registry_is_bounded(monkeypatch):
    registry = app.OrderedDict()
    monkeypatch.setattr(app, "CACHE_SOURCE_REGISTRY_LIMIT", 3)

    for index in range(10):
        app.remember_cache_key(registry, f"/src/{index}", f"key-{index}")

    assert len(registry) == 3
    assert list(registry) == ["/src/7", "/src/8", "/src/9"]


def test_remember_cache_key_returns_previous_key():
    registry = app.OrderedDict()
    assert app.remember_cache_key(registry, "/a", "k1") is None
    assert app.remember_cache_key(registry, "/a", "k1") is None  # тот же ключ
    assert app.remember_cache_key(registry, "/a", "k2") == "k1"


def test_load_pickle_cache_survives_corrupt_entry(tmp_path):
    # Битый пикл — это промах кэша, а не падение джоба (после смены формата
    # чанков распаковка кидает что угодно: KeyError, TypeError, ImportError).
    broken = tmp_path / "db-broken.pkl"
    broken.write_bytes(b"not a pickle at all")

    assert app.load_pickle_cache(broken) is None
    assert not broken.exists()  # повреждённая запись удалена

    good = tmp_path / "db-good.pkl"
    app.save_pickle_cache(good, {"value": 1})
    assert app.load_pickle_cache(good) == {"value": 1}
    # Атомарная запись не оставляет временных файлов с общим именем.
    assert list(tmp_path.glob("*.tmp*")) == []


def test_save_pickle_cache_is_atomic_with_unique_temp(tmp_path, monkeypatch):
    target = tmp_path / "db-x.pkl"
    seen: list[str] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr(app.os, "replace", spy_replace)
    app.save_pickle_cache(target, {"a": 1})
    app.save_pickle_cache(target, {"a": 2})

    assert len(seen) == 2 and seen[0] != seen[1]  # имена .tmp уникальны
    # Файл кэша подписан HMAC (32 байта в начале) — читаем через публичный API.
    assert app.load_pickle_cache(target) == {"a": 2}


def test_load_pickle_cache_rejects_tampered_entry(tmp_path):
    # HMAC-подпись — defense-in-depth: подменённый файл кэша не должен доходить
    # до pickle.loads (unpickle чужих данных = выполнение кода).
    target = tmp_path / "db-tamper.pkl"
    app.save_pickle_cache(target, {"secret": 1})
    assert app.load_pickle_cache(target) == {"secret": 1}

    blob = target.read_bytes()
    # Портим полезную нагрузку, подпись оставляем прежней — проверка не пройдёт.
    target.write_bytes(blob + b"tampered")
    assert app.load_pickle_cache(target) is None
    assert not target.exists()  # невалидная запись удалена


def _make_wash_db(path):
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE data ([time@timestamp] REAL, data_format_0, data_format_1,"
        " data_format_2, data_format_3, data_format_4, data_format_5,"
        " data_format_6, data_format_7)"
    )
    rows = [(1000.0 + i, 1.0 + i, 60.0, 65.0, 2.0, 10.0, 6, 3, 4) for i in range(6)]
    connection.executemany("INSERT INTO data VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()


def test_workspace_samples_offloaded_and_lazy_loaded(tmp_path, monkeypatch):
    # Сэмплы выносятся из RAM в side-файлы и подтягиваются лениво, отдавая те же
    # точки, что и резидентный анализ.
    monkeypatch.setattr(app, "ANALYSIS_CACHE_ROOT", tmp_path)
    app._sample_stream_cache.clear()

    db_path = tmp_path / "Canal_1_20260101.db"
    _make_wash_db(db_path)
    chunk = core.analyze_single_db_file(db_path)
    cache_key = "k" * 40
    analysis = core.build_analysis_result(
        [db_path],
        output_dir=tmp_path,
        max_gap_seconds=core.DEFAULT_MAX_GAP_SECONDS,
        chunks=[chunk],
        analysis_cache_key=cache_key,
    )
    cycles = analysis.sorted_cycles
    assert cycles, "ожидали хотя бы одну мойку"

    def samples_of(a):
        return {
            core.make_cycle_key(c): [s.ts for s in core.analysis_samples_for_cycle(a, c)]
            for c in a.sorted_cycles
        }

    baseline = samples_of(analysis)
    assert sum(len(v) for v in baseline.values()) > 0

    app.save_cached_workspace_analysis(cache_key, analysis, source_key="src")
    # Сэмплы больше не в RAM, но лениво отдаются те же самые.
    assert analysis.samples_by_db == {}
    assert analysis.sample_loader is not None
    assert samples_of(analysis) == baseline

    # Свежая загрузка из кэша — тоже без резидентных сэмплов, доступ ленивый.
    loaded, _skipped = app.load_cached_workspace_analysis(cache_key)
    assert loaded.samples_by_db == {}
    assert samples_of(loaded) == baseline


def test_path_cache_signature_tolerates_missing_file(tmp_path):
    # Файл может исчезнуть под работающим анализом (очистка архивов) — ключ
    # кэша не должен ронять джоб исключением.
    assert app.path_cache_signature(tmp_path / "gone.db") == "missing"
    assert app.db_analysis_cache_key(tmp_path / "gone.db")
    assert app.workspace_analysis_cache_key([tmp_path / "gone.db"], max_gap_seconds=15.0)


def _cached_analysis_with_cycles(tmp_path, cache_key="u" * 40):
    """Анализ, выгруженный в кэш: сэмплы лежат в side-файлах, в RAM их нет."""
    db_path = tmp_path / "Canal_1_20260101.db"
    _make_wash_db(db_path)
    chunk = core.analyze_single_db_file(db_path)
    analysis = core.build_analysis_result(
        [db_path],
        output_dir=tmp_path,
        max_gap_seconds=core.DEFAULT_MAX_GAP_SECONDS,
        chunks=[chunk],
        analysis_cache_key=cache_key,
    )
    app.save_cached_workspace_analysis(cache_key, analysis, source_key="src")
    assert analysis.samples_by_db == {}
    return analysis


def test_missing_sample_stream_raises_instead_of_empty(tmp_path, monkeypatch):
    # Пропавший side-файл — это «судить не по чему», а не «поток пуст». Раньше
    # загрузчик молча отдавал [], и мойка с концентрацией ниже нормы уходила в
    # вердикт «Завершено штатно» — тихая потеря брака.
    monkeypatch.setattr(app, "ANALYSIS_CACHE_ROOT", tmp_path)
    app._sample_stream_cache.clear()
    analysis = _cached_analysis_with_cycles(tmp_path)
    cycle = analysis.sorted_cycles[0]

    removed = list(tmp_path.glob("ws-samples-*.pkl"))
    assert removed, "ожидали side-файлы сэмплов на диске"
    for path in removed:
        path.unlink()
    app._sample_stream_cache.clear()

    with pytest.raises(core.SampleStreamUnavailable):
        core.analysis_samples_for_cycle(analysis, cycle)


def test_missing_sample_stream_marks_wash_for_check(tmp_path, monkeypatch):
    # Сквозная проверка того же: недоступные сэмплы доходят до вердикта мойки,
    # а не растворяются в «оценивать нечего» (kind=None).
    monkeypatch.setattr(app, "ANALYSIS_CACHE_ROOT", tmp_path)
    app._sample_stream_cache.clear()
    analysis = _cached_analysis_with_cycles(tmp_path)
    cycle = analysis.sorted_cycles[0]
    for path in tmp_path.glob("ws-samples-*.pkl"):
        path.unlink()
    app._sample_stream_cache.clear()

    settings = {
        "concentration_eval_enabled": True,
        "concentration_norms": {"alkali": 2.0, "acid": None},
        "concentration_tolerance_percent": 10.0,
    }
    concentration = app.evaluate_cycle_concentration(analysis, cycle, settings)
    assert concentration is not None, "недоступные сэмплы не должны давать None"
    assert concentration["kind"] == "unavailable"

    label, kind = app.apply_concentration_verdict("Завершено штатно", None, concentration)
    assert kind == "check"
    assert label == core.CONCENTRATION_UNAVAILABLE_LABEL


def test_unavailable_sample_stream_is_not_cached(tmp_path, monkeypatch):
    # Отрицательный результат кэшировать нельзя: файл может вернуться (переанализ),
    # а закэшированная пустота пережила бы его и врала бы до перезапуска.
    monkeypatch.setattr(app, "ANALYSIS_CACHE_ROOT", tmp_path)
    app._sample_stream_cache.clear()
    analysis = _cached_analysis_with_cycles(tmp_path)
    cycle = analysis.sorted_cycles[0]

    saved = {path: path.read_bytes() for path in tmp_path.glob("ws-samples-*.pkl")}
    for path in saved:
        path.unlink()
    app._sample_stream_cache.clear()
    with pytest.raises(core.SampleStreamUnavailable):
        core.analysis_samples_for_cycle(analysis, cycle)

    for path, blob in saved.items():
        path.write_bytes(blob)
    # Кэш не должен помнить отказ — сэмплы обязаны прочитаться снова.
    assert core.analysis_samples_for_cycle(analysis, cycle)
