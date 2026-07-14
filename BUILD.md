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

Требуется **Python 3.12 (64-bit)** — ровно та же версия, что на CI. На 3.13+ нет
готовых колёс `pythonnet`/`pywebview`: сборка падает или даёт нерабочий `.exe`.
`build_windows.bat` проверяет версию и останавливается, если она не та.

```bat
build_windows.bat
```

Скрипт создаёт изолированное окружение `.build-venv` (пересоздаёт, если оно от
другой версии Python), ставит зависимости, запускает PyInstaller и — если на
машине есть Inno Setup — собирает установщик. Результат:
`dist\OptiCIP-Dashboard.exe`, `installer_out\OptiCIP-Dashboard-Setup.exe`.

Вручную то же самое:

```bat
py -3.12 -m venv .build-venv
.build-venv\Scripts\activate
pip install -r requirements-windows.txt
pyinstaller --noconfirm OptiCIP-Dashboard.spec
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.0 installer.iss
```

Версии зависимостей в `requirements-windows.txt` **закреплены** (`==`) — иначе
сборка одного и того же коммита невоспроизводима. Обновлять их следует осознанно,
отдельным коммитом, с проверкой сборки на Windows.

UPX в `OptiCIP-Dashboard.spec` намеренно выключен (`upx=False`): он портит
нативные DLL WebView2/pythonnet и увеличивает число ложных срабатываний
антивирусов на неподписанном onefile.

## Версия приложения

Единственный источник версии — `webapp/__init__.py` (`__version__`). Из неё:

- `webapp.app` берёт `APP_VERSION` (по ней сравнивается тег GitHub-релиза при
  проверке обновлений);
- spec-файл генерирует `version_info.txt` (свойства `.exe`) при каждой сборке —
  править его руками бессмысленно;
- `build_windows.bat` и CI передают её в Inno Setup: `ISCC.exe /DAppVersion=…`.

Чтобы выпустить новую версию, достаточно изменить `__version__` и поставить тег
релиза с тем же номером.

## Запуск на целевой машине

- Нужен **Microsoft Edge WebView2 Runtime** (предустановлен в Windows 11 и в
  актуальной Windows 10; иначе его ставит установщик).
- `.exe` самодостаточный, установка не требуется — просто запустите файл.
- Данные, кэш и логи приложение хранит в `%LOCALAPPDATA%\OptiCIP Dashboard`
  (постоянный пользовательский корень; при установке в `Program Files` каталог
  рядом с `.exe` доступен на запись только администратору).
- «Портативный» режим (данные рядом с `.exe`) включается **явно**:
  `OPTICIP_PORTABLE=1` либо `OPTICIP_RUNTIME_ROOT=<путь>`.
- Установщик запускает приложение после установки **от имени пользователя**
  (`runasoriginaluser`): иначе данные, ключ DPAPI (пароль FTP) и автозапуск
  достались бы администратору, а не оператору.
- При деинсталляции запись автозапуска в `HKCU\...\Run` удаляет само приложение
  (`OptiCIP-Dashboard.exe --remove-autostart`, [UninstallRun] с
  `runasoriginaluser`).
- Обновление поверх работающего приложения: Inno Setup проверяет мьютекс
  `Local\OptiCIP-Dashboard-SingleInstance` (`AppMutex`) и просит закрыть окно.

## Web-режим (без десктоп-окна)

`run_wash_ui.py` / `start.sh` поднимают тот же интерфейс в браузере. У приложения
нет аутентификации, поэтому слушается только loopback (`127.0.0.1`, `localhost`,
`::1`). Нелокальный `HOST` (например, `0.0.0.0`) отклоняется; если доступ по сети
действительно нужен и защищён (VPN/файрвол), задайте `OPTICIP_ALLOW_REMOTE=1` —
эту же переменную проверяет серверная защита (`local_request_guard`).

## Иконка (необязательно)

Положите файл `webapp/static/icon.ico` — он автоматически попадёт в сборку
(см. `OptiCIP-Dashboard.spec`).
