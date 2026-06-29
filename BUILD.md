# Сборка Windows-приложения (OptiCIP Dashboard)

Десктоп-режим (`run_wash_desktop.py`) — это локальный FastAPI-сервер в окне
WebView2 (pywebview + pythonnet). Готовый `.exe` собирается через PyInstaller.

> ⚠️ PyInstaller **не умеет кросс-компиляцию**: Windows-сборку нужно делать
> на Windows (либо на Windows-раннере GitHub Actions).

## Вариант 1. Автоматически через GitHub Actions (рекомендуется)

В репозитории есть workflow `.github/workflows/windows-build.yml`, который на
`windows-latest` прогоняет тесты, собирает `.exe` (PyInstaller) и **установщик**
(Inno Setup) с автоустановкой среды WebView2.

1. Откройте вкладку **Actions** репозитория → workflow **«Windows build»**.
2. Нажмите **Run workflow** (или просто запушьте коммит в `main` — сборка
   запустится сама).
3. После завершения скачайте артефакт **OptiCIP-Dashboard-windows**:
   - `OptiCIP-Dashboard-Setup.exe` — установщик (ставит приложение, ярлыки и,
     при необходимости, WebView2 Runtime);
   - `OptiCIP-Dashboard.exe` — портативный вариант.

### Подпись кода (необязательно)

Чтобы убрать предупреждение SmartScreen «Неизвестный издатель», добавьте в
**Settings → Secrets and variables → Actions** секреты:

- `WINDOWS_PFX_BASE64` — ваш сертификат `.pfx` в Base64
  (`certutil -encode cert.pfx cert.txt` или `base64 cert.pfx`);
- `WINDOWS_PFX_PASSWORD` — пароль к нему.

Тогда CI автоматически подпишет и `.exe`, и установщик. Без секретов сборка
проходит, файлы остаются неподписанными.

## Вариант 2. Локально на Windows

Требуется Python 3.11 или 3.12 (64-bit).

```bat
build_windows.bat
```

Скрипт создаёт изолированное окружение `.build-venv`, ставит зависимости и
запускает PyInstaller. Результат — `dist\OptiCIP-Dashboard.exe`.

Вручную то же самое:

```bat
python -m venv .build-venv
.build-venv\Scripts\activate
pip install -r requirements-windows.txt
pyinstaller --noconfirm OptiCIP-Dashboard.spec
```

## Запуск на целевой машине

- Нужен **Microsoft Edge WebView2 Runtime** (предустановлен в Windows 11 и в
  актуальной Windows 10; иначе ставится бесплатно с сайта Microsoft).
- `.exe` самодостаточный, установка не требуется — просто запустите файл.
- Кэш и логи приложение хранит в `%LOCALAPPDATA%\OptiCIP Dashboard`.

## Иконка (необязательно)

Положите файл `webapp/static/icon.ico` — он автоматически попадёт в сборку
(см. `OptiCIP-Dashboard.spec`).
