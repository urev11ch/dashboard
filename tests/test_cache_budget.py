"""Тесты ограничения дискового кэша: TTL, бюджет с LRU-эвикцией и удаление
предыдущей версии записи того же источника."""
import os
import time

import webapp.app as app


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


def test_path_cache_signature_tolerates_missing_file(tmp_path):
    # Файл может исчезнуть под работающим анализом (очистка архивов) — ключ
    # кэша не должен ронять джоб исключением.
    assert app.path_cache_signature(tmp_path / "gone.db") == "missing"
    assert app.db_analysis_cache_key(tmp_path / "gone.db")
    assert app.workspace_analysis_cache_key([tmp_path / "gone.db"], max_gap_seconds=15.0)
