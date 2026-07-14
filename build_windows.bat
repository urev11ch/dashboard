@echo off
REM ============================================================
REM  Сборка OptiCIP Dashboard в один .exe (Windows).
REM  Требуется Python 3.12 (64-bit) — та же версия, что на CI.
REM  Результат: dist\OptiCIP-Dashboard.exe
REM  Если найден Inno Setup — ещё и installer_out\OptiCIP-Dashboard-Setup.exe
REM ============================================================
setlocal

cd /d "%~dp0"

REM Версия Python зафиксирована намеренно: на 3.13+ нет готовых колёс
REM pythonnet/pywebview — сборка либо падает, либо даёт нерабочий .exe.
set "PY=py -3.12"

echo [1/6] Проверяю Python 3.12 (64-bit) ...
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)" 2>nul
if errorlevel 1 goto :nopython

echo [2/6] Готовлю виртуальное окружение .build-venv ...
if exist ".build-venv\Scripts\python.exe" (
    ".build-venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" 2>nul
    REM Окружение от другой версии Python: пакеты в нём несовместимы — пересоздаём.
    if errorlevel 1 rmdir /s /q ".build-venv"
)
if not exist ".build-venv\Scripts\python.exe" (
    %PY% -m venv ".build-venv"
    if errorlevel 1 goto :error
)

echo [3/6] Устанавливаю зависимости ...
call ".build-venv\Scripts\activate.bat" || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requirements-windows.txt || goto :error

echo [4/6] Определяю версию приложения ...
REM Единый источник версии — webapp/__init__.py (__version__): его же читает
REM webapp.app и spec-файл, из него берётся /DAppVersion для установщика.
for /f "delims=" %%v in ('python -c "import webapp; print(webapp.__version__)"') do set "APP_VERSION=%%v"
if not defined APP_VERSION goto :error
echo     версия: %APP_VERSION%

echo [5/6] Запускаю PyInstaller (version_info.txt генерируется из __version__) ...
pyinstaller --noconfirm OptiCIP-Dashboard.spec || goto :error

echo [6/6] Собираю установщик (если установлен Inno Setup) ...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
    if not exist "MicrosoftEdgeWebview2Setup.exe" (
        echo     Скачиваю MicrosoftEdgeWebview2Setup.exe ...
        powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile 'MicrosoftEdgeWebview2Setup.exe'" || goto :error
    )
    "%ISCC%" /DAppVersion=%APP_VERSION% installer.iss || goto :error
    echo     Установщик: %cd%\installer_out\OptiCIP-Dashboard-Setup.exe
) else (
    echo     Inno Setup не найден — установщик пропущен, собран только .exe.
)

echo.
echo Готово. Собранный файл: %cd%\dist\OptiCIP-Dashboard.exe
goto :eof

:nopython
echo.
echo Не найден 64-bit Python 3.12 (команда "py -3.12").
echo Установите Python 3.12 x64 с https://www.python.org/downloads/
echo (на 3.13+ ломаются зависимости pythonnet/pywebview).
exit /b 1

:error
echo.
echo СБОРКА ПРЕРВАНА. Проверьте сообщения выше.
exit /b 1
