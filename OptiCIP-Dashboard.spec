# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the OptiCIP Dashboard desktop build (Windows).

Build:  pyinstaller --noconfirm OptiCIP-Dashboard.spec
Output: dist/OptiCIP-Dashboard.exe  (single-file, windowed)

Must be built ON Windows — PyInstaller does not cross-compile.
"""
import os
import re

from PyInstaller.utils.hooks import collect_all, collect_submodules


# --- version resource: single source of truth is webapp/__init__.py ------
def read_app_version() -> str:
    """Версия приложения из webapp/__init__.py (__version__) — читаем регуляркой,
    чтобы не импортировать пакет во время сборки."""
    source = open(os.path.join("webapp", "__init__.py"), encoding="utf-8").read()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        raise SystemExit("Не найден __version__ в webapp/__init__.py — версия сборки неизвестна.")
    return match.group(1)


def write_version_info(path: str, version: str) -> str:
    """Пересобирает version_info.txt (ресурс версии в свойствах .exe) из единой
    версии: раньше он расходился с APP_VERSION и с версией установщика, а по ней
    сравнивается тег GitHub-релиза при проверке обновлений."""
    parts = [int(chunk) for chunk in re.findall(r"\d+", version)][:4]
    parts += [0] * (4 - len(parts))
    quad = ", ".join(str(part) for part in parts)
    dotted = ".".join(str(part) for part in parts)
    content = f"""# ВНИМАНИЕ: файл генерируется при сборке из webapp/__init__.py (__version__).
# Правьте версию только там — здесь изменения будут перезаписаны.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}),
    prodvers=({quad}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'OptiCIP'),
          StringStruct('FileDescription', 'OptiCIP Dashboard — отчёты по мойкам CIP'),
          StringStruct('FileVersion', '{dotted}'),
          StringStruct('InternalName', 'OptiCIP-Dashboard'),
          StringStruct('OriginalFilename', 'OptiCIP-Dashboard.exe'),
          StringStruct('ProductName', 'OptiCIP Dashboard'),
          StringStruct('ProductVersion', '{version}'),
          StringStruct('LegalCopyright', '(c) OptiCIP'),
        ],
      )
    ]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
  ],
)
"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


APP_VERSION = read_app_version()

# --- bundled data: Jinja templates, static assets ------------------------
datas = [
    ("webapp/templates", "webapp/templates"),
    ("webapp/static", "webapp/static"),
]
binaries = []

# uvicorn loads its protocol/loop implementations dynamically.
hiddenimports = collect_submodules("uvicorn")
# pythonnet bridge used by the WebView2/winforms backend and PDF export.
hiddenimports += ["clr", "pythonnet"]

# pywebview ships platform backends + data that are imported dynamically.
for package in ("webview",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Optional app icon (drop a .ico at webapp/static/icon.ico to use it).
icon_path = os.path.join("webapp", "static", "icon.ico")
app_icon = icon_path if os.path.exists(icon_path) else None

# Version resource shown in the .exe "Properties" tab (regenerated on every build).
version_file = write_version_info("version_info.txt", APP_VERSION)


a = Analysis(
    ["run_wash_desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OptiCIP-Dashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX выключен намеренно: он ломает нативные DLL (pythonnet/WebView2) и резко
    # увеличивает ложные срабатывания антивирусов на неподписанном onefile.
    # Плюс на CI UPX не установлен — с upx=True локальная и CI-сборка расходились.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
    version=version_file,
)
