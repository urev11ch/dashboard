"""Тесты защиты от path traversal при распаковке архивов и в именах с FTP."""
import zipfile
from pathlib import Path

import webapp.app as app


def test_safe_member_accepts_normal_names():
    assert app.safe_archive_member_path("a.db") == Path("a.db")
    assert app.safe_archive_member_path("sub/dir/ok.db") == Path("sub", "dir", "ok.db")
    assert app.safe_archive_member_path("./sub/ok.db") == Path("sub", "ok.db")


def test_safe_member_rejects_traversal_and_windows_names():
    # POSIX-обход и абсолютные пути.
    assert app.safe_archive_member_path("../evil.db") is None
    assert app.safe_archive_member_path("a/../../evil.db") is None
    assert app.safe_archive_member_path("/abs/evil.db") is None
    assert app.safe_archive_member_path("") is None
    # Windows-разделители и диски: PurePosixPath не разбивает по `\`,
    # поэтому такие имена отклоняются целиком.
    assert app.safe_archive_member_path("..\\..\\evil.db") is None
    assert app.safe_archive_member_path("dir\\evil.db") is None
    assert app.safe_archive_member_path("C:x.db") is None
    assert app.safe_archive_member_path("C:\\evil.db") is None


def test_extract_zip_skips_traversal_members(tmp_path):
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("good.db", b"good")
        handle.writestr("..\\..\\evil.db", b"evil")
        handle.writestr("../evil2.db", b"evil")

    target = tmp_path / "out"
    target.mkdir()
    extracted = app.extract_archive_dbs(archive, target)

    assert [path.name for path in extracted] == ["good.db"]
    assert (target / "good.db").read_bytes() == b"good"
    # За пределы целевой папки ничего не записано.
    assert list(tmp_path.rglob("evil*.db")) == []


def test_ftp_relative_target_sanitizes_windows_names():
    # Обычное имя раскладывается относительно корня.
    assert app._ftp_relative_target("/datalog", "/datalog/sub/a.db") == Path("sub", "a.db")
    # Имя с Windows-обходом сводится к безопасному базовому имени.
    target = app._ftp_relative_target("/datalog", "/datalog/..\\..\\evil.db")
    assert target == Path("evil.db")
    # Двоеточие (drive-relative на Windows) заменяется.
    target = app._ftp_relative_target("/datalog", "/datalog/C:x.db")
    assert ":" not in str(target) and len(target.parts) == 1
