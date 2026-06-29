@echo off
REM ============================================================
REM  Сборка OptiCIP Dashboard в один .exe (Windows).
REM  Требуется установленный Python 3.11/3.12 (64-bit).
REM  Результат: dist\OptiCIP-Dashboard.exe
REM ============================================================
setlocal

cd /d "%~dp0"

echo [1/4] Создаю виртуальное окружение .build-venv ...
py -3 -m venv .build-venv || python -m venv .build-venv || goto :error

echo [2/4] Устанавливаю зависимости ...
call ".build-venv\Scripts\activate.bat" || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requirements-windows.txt || goto :error

echo [3/4] Запускаю PyInstaller ...
pyinstaller --noconfirm OptiCIP-Dashboard.spec || goto :error

echo [4/4] Готово.
echo Собранный файл: %cd%\dist\OptiCIP-Dashboard.exe
goto :eof

:error
echo.
echo СБОРКА ПРЕРВАНА. Проверьте сообщения выше.
exit /b 1
