"""Распаковка .db из архивов панели с защитой от path traversal и zip/tar-бомбы.

Выделено из webapp/app.py. Зависит только от config и ядра (wash_report).
Тесты, меняющие лимит распаковки, патчат app.archives.ARCHIVE_EXTRACT_MAX_BYTES.
"""
from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

import wash_report as core
from webapp.config import ARCHIVE_EXTRACT_MAX_BYTES


def safe_archive_member_path(name: str) -> Path | None:
    # `PurePosixPath` не разбивает по `\`, поэтому Windows-разделители и имена
    # с диском (`..\..\evil.db`, `C:x`) отклоняем сразу.
    if "\\" in name or ":" in name:
        return None
    candidate = PurePosixPath(name)
    if candidate.is_absolute():
        return None

    parts = [part for part in candidate.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    return Path(*parts)


def extract_archive_dbs(
    archive_path: Path,
    target_root: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[Path]:
    extracted_paths: list[Path] = []
    resolved_root = target_root.resolve()
    budget = {"remaining": ARCHIVE_EXTRACT_MAX_BYTES}

    def resolve_member_target(relative_path: Path) -> Path | None:
        # Финальная страховка от path traversal: записываем только внутрь
        # target_root, что бы ни осталось в имени после санитизации.
        target_path = (target_root / relative_path).resolve()
        if not target_path.is_relative_to(resolved_root):
            return None
        return target_path

    def copy_capped(source, target_path: Path) -> None:
        """Потоковое копирование с общим лимитом на архив. Превышение = вероятная
        бомба: удаляем недописанный файл и валим распаковку (вызывающий её ловит
        ValueError и пропускает архив)."""
        written = 0
        try:
            with target_path.open("wb") as destination:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > budget["remaining"]:
                        raise ValueError(
                            "Архив распаковывается в слишком большой объём (возможна бомба)."
                        )
                    destination.write(chunk)
        except BaseException:
            target_path.unlink(missing_ok=True)
            raise
        budget["remaining"] -= written

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as handle:
            for member in handle.infolist():
                core.raise_if_cancelled(cancel_check)
                if member.is_dir():
                    continue
                relative_path = safe_archive_member_path(member.filename)
                if relative_path is None or relative_path.suffix.lower() != ".db":
                    continue
                target_path = resolve_member_target(relative_path)
                if target_path is None:
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(member) as source:
                    copy_capped(source, target_path)
                extracted_paths.append(target_path)
        return extracted_paths

    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as handle:
            for member in handle.getmembers():
                core.raise_if_cancelled(cancel_check)
                if not member.isfile():
                    continue
                relative_path = safe_archive_member_path(member.name)
                if relative_path is None or relative_path.suffix.lower() != ".db":
                    continue
                target_path = resolve_member_target(relative_path)
                if target_path is None:
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                extracted_member = handle.extractfile(member)
                if extracted_member is None:
                    continue
                with extracted_member as source:
                    copy_capped(source, target_path)
                extracted_paths.append(target_path)

    return extracted_paths
