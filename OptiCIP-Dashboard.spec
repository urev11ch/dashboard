# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the OptiCIP Dashboard desktop build (Windows).

Build:  pyinstaller --noconfirm OptiCIP-Dashboard.spec
Output: dist/OptiCIP-Dashboard.exe  (single-file, windowed)

Must be built ON Windows — PyInstaller does not cross-compile.
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

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

# Version resource shown in the .exe "Properties" tab.
version_file = "version_info.txt" if os.path.exists("version_info.txt") else None


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
    upx=True,
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
