"""Сторож для installer.iss: проверяет решения, которые иначе ломаются молча.

Компилирует скрипт только CI (ISCC есть лишь под Windows), поэтому здесь —
дешёвые проверки инвариантов, ради которых установщик и переделывали.
"""
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parent.parent / "installer.iss"


@pytest.fixture(scope="module")
def script() -> str:
    return INSTALLER.read_text(encoding="utf-8-sig")


def test_install_is_per_user(script):
    # Ради этого всё и затевалось: установка в Program Files требует прав
    # администратора, то есть запрос UAC при КАЖДОМ обновлении.
    assert "PrivilegesRequired=lowest" in script
    assert "PrivilegesRequired=admin" not in script


def test_install_dir_follows_privileges(script):
    # {autopf} под lowest разворачивается в {localappdata}\Programs; жёсткий
    # {commonpf} вернул бы установку в Program Files и UAC вместе с ней.
    assert "DefaultDirName={autopf}\\{#AppName}" in script
    assert "{commonpf}" not in script


def test_legacy_admin_install_is_removed(script):
    # Без этого у обновившихся остаётся вторая копия в Program Files: мёртвая
    # запись в «Программах и компонентах» и второй ярлык в «Пуске».
    assert "function PrepareToInstall" in script
    assert "LegacyUninstallKey" in script
    assert "ShellExec('runas'" in script


def test_legacy_removal_never_fails_the_install(script):
    # Отказ от UAC при удалении старой копии не должен ронять обновление:
    # PrepareToInstall обязан вернуть пустую строку (= «ошибок нет»).
    body = script.split("function PrepareToInstall", 1)[1]
    assert "Result := '';" in body
    # Никаких присваиваний Result непустым текстом — он стал бы текстом ошибки.
    assert "Result := 'Не" not in body


def test_relaunch_after_silent_update_is_kept(script):
    # Автообновление запускает установщик с /SILENT /RELAUNCH=1 и закрывает окно;
    # без этой строки приложение после обновления не вернулось бы.
    assert "Check: WantsRelaunch" in script
    assert "function WantsRelaunch" in script
