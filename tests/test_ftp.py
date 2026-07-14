"""Тесты FTP-конфигурации, хранения паролей и списка объектов."""
import ftplib

import pytest

import webapp.app as app


def test_apply_ftp_url_full():
    payload = app.apply_ftp_url_payload({"host": "ftp://uploadhis:111111@192.168.1.50/datalog"})
    assert payload["host"] == "192.168.1.50"
    assert payload["username"] == "uploadhis"
    assert payload["password"] == "111111"
    assert payload["path"] == "/datalog"


def test_apply_ftp_url_bare_credentials():
    payload = app.apply_ftp_url_payload({"host": "user:pw@10.0.0.7/dir", "path": ""})
    assert payload["host"] == "10.0.0.7"
    assert payload["username"] == "user"


def test_apply_ftp_url_plain_ip_untouched():
    payload = app.apply_ftp_url_payload({"host": "192.168.1.50", "username": "x"})
    assert payload["host"] == "192.168.1.50"
    assert payload["username"] == "x"


def test_normalize_ftp_defaults():
    cfg = app.normalize_ftp_connection_settings({"host": "127.0.0.1"})
    assert cfg["port"] == 21
    assert cfg["username"] == "anonymous"
    assert cfg["path"] == "/"
    assert cfg["passive"] is True


def test_normalize_ftp_rejects_empty_host():
    with pytest.raises(ValueError):
        app.normalize_ftp_connection_settings({"host": ""})


def test_normalize_ftp_rejects_bad_port():
    with pytest.raises(ValueError):
        app.normalize_ftp_connection_settings({"host": "h", "port": "70000"})


def test_secret_roundtrip():
    token = app.protect_secret("s3cret")
    assert token and token != "s3cret"  # не хранится в открытом виде
    assert app.unprotect_secret(token) == "s3cret"
    assert app.protect_secret("") == ""
    assert app.unprotect_secret("") == ""


def test_connection_id_is_stable_and_distinct():
    a = app.normalize_ftp_connection_settings({"host": "1.1.1.1", "username": "u", "path": "/d"})
    b = app.normalize_ftp_connection_settings({"host": "2.2.2.2", "username": "u", "path": "/d"})
    assert app.ftp_connection_id(a) == app.ftp_connection_id(a)
    assert app.ftp_connection_id(a) != app.ftp_connection_id(b)


def test_build_object_rows_lists_detected_objects():
    class _Ov:
        def __init__(self, ch, oid):
            self.channel = ch
            self.object_id = oid

    class _Analysis:
        overviews = [_Ov(1, 3), _Ov(1, 5), _Ov(1, 0)]  # 1:0 пропускается

    rows = app.build_object_rows({}, _Analysis())
    assert [(r["channel"], r["object_id"]) for r in rows] == [(1, 3), (1, 5)]

    rows2 = app.build_object_rows({(1, 3): "Танк 3"}, _Analysis())
    named = [r for r in rows2 if (r["channel"], r["object_id"]) == (1, 3)][0]
    assert named["object_name"] == "Танк 3" and named["is_json_name"] is True


# --- Инкрементальная загрузка по FTP -------------------------------------


def test_parse_ftp_timestamp():
    assert app._parse_ftp_timestamp("20240101120000") is not None
    # Более позднее время даёт больший epoch.
    assert app._parse_ftp_timestamp("20240101120000") < app._parse_ftp_timestamp("20240101120001")
    # Дробные секунды отбрасываются, но строка парсится.
    assert app._parse_ftp_timestamp("20240101120000.500") == app._parse_ftp_timestamp("20240101120000")
    # Мусор и слишком короткие строки — None.
    assert app._parse_ftp_timestamp("") is None
    assert app._parse_ftp_timestamp("notatime") is None
    assert app._parse_ftp_timestamp("2024") is None


def test_parse_mdtm_reply():
    expected = app._parse_ftp_timestamp("20240101120000")
    assert app._parse_mdtm_reply("213 20240101120000") == expected
    assert app._parse_mdtm_reply("20240101120000") == expected
    assert app._parse_mdtm_reply("550 Not Found") is None
    assert app._parse_mdtm_reply("") is None


def test_should_skip_download():
    ts = app._parse_ftp_timestamp("20240101120000")
    later = app._parse_ftp_timestamp("20240202120000")
    # Размер совпал, времени с панели нет — считаем неизменным.
    assert app._should_skip_download({"size": 10}, 10, None) is True
    # Размер совпал, локальная копия не старше панели — пропускаем.
    assert app._should_skip_download({"size": 10, "mtime": ts}, 10, ts) is True
    # Размер отличается — качаем.
    assert app._should_skip_download({"size": 10, "mtime": ts}, 20, ts) is False
    # На панели файл новее — качаем.
    assert app._should_skip_download({"size": 10, "mtime": ts}, 10, later) is False
    # Локальной копии нет — качаем.
    assert app._should_skip_download(None, 10, ts) is False
    # Размер неизвестен (нет SIZE/MLSD) — сравниваем только время модификации.
    assert app._should_skip_download({"size": 10, "mtime": ts}, None, ts) is True
    assert app._should_skip_download({"size": 10, "mtime": ts}, None, later) is False
    assert app._should_skip_download({"size": 10}, None, ts) is False
    assert app._should_skip_download({"size": 10, "mtime": ts}, None, None) is False


def test_build_local_archive_index(tmp_path):
    (tmp_path / "a.db").write_bytes(b"aaa")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.db").write_bytes(b"bbbb")
    (tmp_path / "note.txt").write_text("ignore me")

    index = app.build_local_archive_index(tmp_path)
    assert index["a.db"]["size"] == 3
    assert index["sub/b.db"]["size"] == 4
    # Ключ по имени+размеру для распознавания копий из старых папок.
    assert ("name", "a.db", 3) in index
    # Посторонние файлы не индексируются.
    assert "note.txt" not in index


class _FakeFTP:
    """Минимальный фейковый FTP: плоский каталог файлов с содержимым и mtime."""

    def __init__(self, files):
        # files: dict[имя] = {"data": bytes, "modify": "YYYYMMDDHHMMSS" | None}
        self.files = files
        self.retr_calls = []

    def voidcmd(self, cmd):
        return "200 OK"

    def mlsd(self, path):
        for name, info in self.files.items():
            facts = {"type": "file", "size": str(len(info["data"]))}
            if info.get("modify"):
                facts["modify"] = info["modify"]
            yield name, facts

    def size(self, path):
        return len(self.files[path.rsplit("/", 1)[-1]]["data"])

    def sendcmd(self, cmd):
        name = cmd.rsplit("/", 1)[-1]
        return "213 " + self.files[name]["modify"]

    def retrbinary(self, cmd, callback):
        name = cmd[len("RETR "):].rsplit("/", 1)[-1]
        self.retr_calls.append(name)
        callback(self.files[name]["data"])

    def quit(self):
        pass

    def close(self):
        pass


_FTP_CONFIG = {"host": "h", "port": 21, "username": "u", "password": "p", "path": "/datalog"}


def test_download_ftp_incremental(tmp_path, monkeypatch):
    files = {
        "a.db": {"data": b"aaa", "modify": "20240101120000"},
        "b.db": {"data": b"bbbb", "modify": "20240101120000"},
    }
    fake = _FakeFTP(files)
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    # Первый прогон — качаем всё.
    first = app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert sorted(fake.retr_calls) == ["a.db", "b.db"]
    assert len(first.present_files) == 2
    assert first.downloaded == 2 and first.failed_files == []

    # Ничего не менялось — не качаем ничего.
    fake.retr_calls.clear()
    second = app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == []
    assert len(second.present_files) == 2
    assert second.skipped == 2

    # Вырос размер b.db — качаем только его.
    files["b.db"]["data"] = b"bbbbbbbb"
    fake.retr_calls.clear()
    third = app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == ["b.db"]
    assert len(third.present_files) == 2


class _BrokenFTP(_FakeFTP):
    """FTP, обрывающий соединение посреди передачи файла."""

    def retrbinary(self, cmd, callback):
        callback(b"part")
        raise OSError("connection lost")


def test_download_interrupted_leaves_no_truncated_db(tmp_path, monkeypatch):
    files = {"a.db": {"data": b"full-data", "modify": "20240101120000"}}
    fake = _BrokenFTP(files)
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    with pytest.raises(SystemExit):
        app.download_ftp_files(_FTP_CONFIG, tmp_path)

    # Обрыв не оставляет ни усечённой базы, ни временного файла `.part`.
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_download_updates_existing_month_folder_without_duplicates(tmp_path, monkeypatch):
    files = {"a.db": {"data": b"aaa", "modify": "20240101120000"}}
    fake = _FakeFTP(files)
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert (tmp_path / "2024-01" / "a.db").exists()

    # Файл дописали в новом месяце — качаем поверх старой копии, а не в новую
    # месячную папку (иначе копилась бы копия на каждый месяц).
    files["a.db"] = {"data": b"aaa-grown", "modify": "20240215120000"}
    fake.retr_calls.clear()
    app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == ["a.db"]
    assert (tmp_path / "2024-01" / "a.db").read_bytes() == b"aaa-grown"
    assert not (tmp_path / "2024-02").exists()


def test_delete_ftp_connection_guards_profile_dir(tmp_path, monkeypatch):
    datalog = tmp_path / "datalog"
    datalog.mkdir()
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path / "temp")
    monkeypatch.setattr(app, "DATALOG_ROOT", datalog)

    cfg = app.normalize_ftp_connection_settings({"host": "1.1.1.1", "path": "/d"})
    entry = app.upsert_ftp_connection(cfg)
    profile = datalog / entry["id"]
    profile.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("x", encoding="utf-8")

    # id не из реестра (в т.ч. с `../`) не приводит к удалению чужой папки.
    app.delete_ftp_connection("../victim")
    assert victim.exists() and (victim / "keep.txt").exists()

    # Даже если вредоносный id попал в реестр, формат id и родитель папки
    # проверяются перед rmtree.
    registry = app.load_ftp_sources_registry()
    registry["connections"].append({"id": "../victim"})
    app.save_ftp_sources_registry(registry)
    app.delete_ftp_connection("../victim")
    assert victim.exists() and (victim / "keep.txt").exists()

    # Легитимное подключение удаляется вместе со своей папкой профиля.
    app.delete_ftp_connection(entry["id"])
    assert app.find_ftp_connection(entry["id"]) is None
    assert not profile.exists()


# --- Устойчивость синхронизации к сбоям на отдельных файлах ------------------


class _PartlyFailingFTP(_FakeFTP):
    """FTP, отдающий ошибку уровня файла на заданных именах."""

    def __init__(self, files, failing, error):
        super().__init__(files)
        self.failing = set(failing)
        self.error = error

    def retrbinary(self, cmd, callback):
        name = cmd[len("RETR "):].rsplit("/", 1)[-1]
        if name in self.failing:
            self.retr_calls.append(name)
            raise self.error
        super().retrbinary(cmd, callback)


def test_download_continues_after_single_file_error(tmp_path, monkeypatch):
    # Ошибка на одном файле (нет прав / файл занят) не должна обрывать всю
    # синхронизацию: раньше первый же сбой поднимал SystemExit и следующие
    # файлы не скачивались никогда.
    files = {
        "a.db": {"data": b"aaa", "modify": "20240101120000"},
        "b.db": {"data": b"bbbb", "modify": "20240101120000"},
        "c.db": {"data": b"ccccc", "modify": "20240101120000"},
    }
    fake = _PartlyFailingFTP(files, {"b.db"}, ftplib.error_perm("550 Permission denied"))
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    result = app.download_ftp_files(_FTP_CONFIG, tmp_path)

    # Попробовали все три файла, скачались два, сбойный помечен как неудавшийся.
    assert sorted(fake.retr_calls) == ["a.db", "b.db", "c.db"]
    assert result.downloaded == 2
    assert result.failed_files == ["b.db"]
    assert {path.name for path in result.present_files} == {"a.db", "c.db"}
    assert (tmp_path / "2024-01" / "c.db").read_bytes() == b"ccccc"
    # Незавершённый файл не оставлен ни в каком виде.
    assert not (tmp_path / "2024-01" / "b.db").exists()
    assert list(tmp_path.rglob("*.part*")) == []


def test_download_retries_failed_file_on_next_sync(tmp_path, monkeypatch):
    files = {"a.db": {"data": b"aaa", "modify": "20240101120000"}}
    failing = _PartlyFailingFTP(files, {"a.db"}, ftplib.error_temp("450 File busy"))
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: failing)
    assert app.download_ftp_files(_FTP_CONFIG, tmp_path).failed_files == ["a.db"]

    # На следующем проходе файл качается заново (локальной копии нет).
    healthy = _FakeFTP(files)
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: healthy)
    result = app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert healthy.retr_calls == ["a.db"]
    assert result.failed_files == [] and result.downloaded == 1


def test_download_aborts_on_connection_loss(tmp_path, monkeypatch):
    # Потеря соединения (сокет / 421) — это конец цикла, а не пропуск файла.
    files = {
        "a.db": {"data": b"aaa", "modify": "20240101120000"},
        "b.db": {"data": b"bbbb", "modify": "20240101120000"},
    }
    fake = _PartlyFailingFTP(files, {"a.db"}, OSError("connection reset"))
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    with pytest.raises(SystemExit):
        app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == ["a.db"]  # до b.db дело не дошло


def test_connection_lost_classification():
    assert app.is_ftp_connection_lost(OSError("socket")) is True
    assert app.is_ftp_connection_lost(EOFError()) is True
    assert app.is_ftp_connection_lost(ftplib.error_temp("421 closing control connection")) is True
    # Ошибки уровня файла — синхронизация продолжается.
    assert app.is_ftp_connection_lost(ftplib.error_perm("550 no such file")) is False
    assert app.is_ftp_connection_lost(ftplib.error_temp("450 file busy")) is False


def test_part_file_name_is_unique(tmp_path, monkeypatch):
    # Имя .part должно быть уникальным: два потока с детерминированным именем
    # писали в один файл и подсовывали битую базу.
    seen: list[str] = []

    class _CapturingFTP(_FakeFTP):
        def retrbinary(self, cmd, callback):
            seen.extend(p.name for p in tmp_path.rglob("*.part*"))
            super().retrbinary(cmd, callback)

    files = {"a.db": {"data": b"aaa", "modify": "20240101120000"}}
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: _CapturingFTP(files))
    app.download_ftp_files(_FTP_CONFIG, tmp_path)

    assert len(seen) == 1
    assert seen[0].startswith("a.db.part-") and len(seen[0]) > len("a.db.part-")
    # Временный файл не выглядит как база/архив — сканеры его не подхватят.
    assert not app._is_archive_or_db_name(seen[0])


def test_materialize_reports_ftp_failure_instead_of_hiding_it(tmp_path, monkeypatch):
    # FTP недоступен, но локальные архивы есть: работаем с ними и при этом
    # сообщаем о сбое (раньше он маскировался сообщением «использую скачанное»).
    datalog = tmp_path / "datalog"
    datalog.mkdir()
    monkeypatch.setattr(app, "TEMP_ROOT", tmp_path / "temp")
    monkeypatch.setattr(app, "DATALOG_ROOT", datalog)

    cfg = app.normalize_ftp_connection_settings({"host": "1.1.1.1", "path": "/d"})
    entry = app.upsert_ftp_connection(cfg)
    profile = datalog / entry["id"]
    (profile / "2024-01").mkdir(parents=True)
    (profile / "2024-01" / "old.db").write_bytes(b"cached")

    def _boom(config):
        raise ValueError("FTP недоступен")

    monkeypatch.setattr(app, "open_ftp_connection", _boom)
    result = app.materialize_ftp_sources(profile)
    assert "FTP недоступен" in result.ftp_error_message

    summary = app.ScanSummary(ftp_error=result.ftp_error_message)
    assert any("FTP" in warning for warning in app.build_scan_warnings(summary))


def test_legacy_day_folders_are_reused(tmp_path, monkeypatch):
    # Файл из папки прежнего формата (ГГГГ-ММ-ДД) обновляется на месте, а не
    # скачивается заново в ГГГГ-ММ/, оставляя старую копию навсегда.
    legacy = tmp_path / "2024-01-05"
    legacy.mkdir()
    (legacy / "a.db").write_bytes(b"aaa")

    files = {"a.db": {"data": b"aaa-grown", "modify": "20240215120000"}}
    fake = _FakeFTP(files)
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == ["a.db"]
    assert (legacy / "a.db").read_bytes() == b"aaa-grown"
    assert not (tmp_path / "2024-02").exists()


def test_download_ftp_redownload_on_newer_mtime(tmp_path, monkeypatch):
    files = {"a.db": {"data": b"same", "modify": "20240101120000"}}
    fake = _FakeFTP(files)
    monkeypatch.setattr(app, "open_ftp_connection", lambda config: fake)

    app.download_ftp_files(_FTP_CONFIG, tmp_path)
    fake.retr_calls.clear()

    # Размер тот же, но время на панели новее — качаем заново.
    files["a.db"]["modify"] = "20240202120000"
    app.download_ftp_files(_FTP_CONFIG, tmp_path)
    assert fake.retr_calls == ["a.db"]
