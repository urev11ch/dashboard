#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

import uvicorn

from runtime_paths import resolve_log_root


HOST = "127.0.0.1"
APP_TITLE = "OptiCIP Dashboard"
# Минимальный размер окна. Ниже него уменьшённый режим не опускается — иначе на
# Full HD «половина экрана» (960×540) была бы тесна для интерфейса по высоте.
MIN_WINDOW_WIDTH = 960
MIN_WINDOW_HEIGHT = 600


def configure_runtime_environment() -> None:
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")
        enable_windows_dpi_awareness()


def enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        user32 = ctypes.windll.user32
        shcore = getattr(ctypes.windll, "shcore", None)

        try:
            per_monitor_v2 = ctypes.c_void_p(-4)
            if user32.SetProcessDpiAwarenessContext(per_monitor_v2):
                return
        except Exception:
            pass

        if shcore is not None:
            try:
                shcore.SetProcessDpiAwareness(2)
                return
            except Exception:
                pass

        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


configure_runtime_environment()

import webview


def resolve_log_path() -> Path:
    return resolve_log_root() / "desktop.log"


LOG_PATH = resolve_log_path()


def resolve_webview_storage_path() -> Path:
    path = resolve_log_root().parent / "webview-data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging() -> Path:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
        ],
        force=True,
    )
    logging.info("Starting %s", APP_TITLE)
    logging.info("Python executable: %s", sys.executable)
    logging.info("Frozen: %s", getattr(sys, "frozen", False))
    return LOG_PATH


def build_desktop_loading_html() -> str:
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OptiCIP Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: #f4f7fb;
      color: #183147;
    }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding-top: 36px;
      background:
        radial-gradient(circle at top, rgba(53, 122, 184, 0.18), transparent 38%),
        linear-gradient(180deg, #f9fbfd 0%, #edf3f8 100%);
    }
    .boot-titlebar {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      height: 36px;
      display: flex;
      align-items: center;
      justify-content: flex-end;
      padding-left: 14px;
      z-index: 10;
    }
    .boot-titlebar .boot-drag {
      flex: 1;
      align-self: stretch;
      font-size: 12px;
      display: flex;
      align-items: center;
      color: #5b7b96;
      letter-spacing: 0.04em;
    }
    .boot-titlebar button {
      width: 46px;
      height: 36px;
      border: 0;
      background: transparent;
      color: #46637c;
      font-size: 15px;
      cursor: pointer;
    }
    .boot-titlebar button:hover {
      background: rgba(200, 68, 55, 0.14);
      color: #b5271a;
    }
    .card {
      width: min(520px, calc(100vw - 48px));
      padding: 28px 30px;
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid rgba(24, 49, 71, 0.08);
      box-shadow: 0 26px 70px rgba(24, 49, 71, 0.12);
    }
    .eyebrow {
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: #5b7b96;
      margin-bottom: 10px;
    }
    h1 {
      margin: 0 0 10px;
      font-size: 28px;
      line-height: 1.15;
    }
    p {
      margin: 0;
      font-size: 16px;
      line-height: 1.5;
      color: #46637c;
    }
    .bar {
      margin-top: 18px;
      height: 10px;
      border-radius: 999px;
      background: rgba(53, 122, 184, 0.12);
      overflow: hidden;
      position: relative;
    }
    .bar::before {
      content: "";
      position: absolute;
      inset: 0;
      width: 34%;
      border-radius: inherit;
      background: linear-gradient(90deg, #357ab8 0%, #72a7d4 100%);
      animation: loading 1.2s ease-in-out infinite;
    }
    @keyframes loading {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(310%); }
    }
  </style>
</head>
<body>
  <div class="boot-titlebar">
    <div class="boot-drag pywebview-drag-region">OptiCIP Dashboard</div>
    <button type="button" title="Закрыть" onclick="window.pywebview&&window.pywebview.api&&window.pywebview.api.close_window()">&#10005;</button>
  </div>
  <main class="card">
    <div class="eyebrow">OptiCIP Dashboard</div>
    <h1>Запускаю интерфейс</h1>
    <p>Подготавливаю локальный web-интерфейс и подключаю desktop-окно.</p>
    <div class="bar" aria-hidden="true"></div>
  </main>
</body>
</html>"""


def load_desktop_window_url(window: webview.Window, target_url: str) -> None:
    logging.info("Waiting for desktop window before loading %s", target_url)
    if not window.events.shown.wait(20):
        logging.error("Desktop window was not shown in time; cannot load %s", target_url)
        return

    time.sleep(0.35)

    try:
        window.load_url(target_url)
        logging.info("Desktop window navigation requested: %s", target_url)
    except Exception:
        logging.exception("Desktop window navigation failed")


def show_fatal_error(message: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def preflight_windows_runtime() -> None:
    if sys.platform != "win32":
        return

    from pythonnet import get_runtime_info, load

    load("netfx")

    import clr

    clr.AddReference("System.Windows.Forms")
    runtime_info = get_runtime_info()
    logging.info("pythonnet runtime: %s", runtime_info)


def load_web_app():
    from webapp.app import app as web_app

    return web_app


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as handle:
        handle.bind((HOST, 0))
        handle.listen(1)
        return int(handle.getsockname()[1])


class DesktopServer:
    def __init__(self, web_app, host: str = HOST) -> None:
        self.host = host
        self.port = find_free_port()
        self.config = uvicorn.Config(
            web_app,
            host=self.host,
            port=self.port,
            reload=False,
            log_level="warning",
        )
        self.server = uvicorn.Server(self.config)
        self.server.install_signal_handlers = lambda: None
        self.thread_error: Exception | None = None
        self.thread = threading.Thread(target=self._run_server, name="wash-ui-server", daemon=True)

    def _run_server(self) -> None:
        try:
            self.server.run()
        except Exception as exc:  # pragma: no cover - uvicorn failure is environment-specific
            self.thread_error = exc
            logging.exception("Local UI server thread crashed")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        self.thread.start()
        self.wait_until_ready()

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

    def wait_until_ready(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            if self.thread_error is not None:
                raise RuntimeError("Поток локального UI-сервера завершился с ошибкой.") from self.thread_error
            if not self.thread.is_alive():
                raise RuntimeError("Поток локального UI-сервера завершился до готовности приложения.")
            try:
                with urllib.request.urlopen(self.url, timeout=0.5) as response:
                    if response.status < 500:
                        return
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                time.sleep(0.1)
        raise RuntimeError(f"Не удалось запустить локальный UI по адресу {self.url}") from last_error


class DesktopBridge:
    def __init__(self) -> None:
        self._window: webview.Window | None = None
        # Окно создаётся развёрнутым (maximized=True), поэтому стартовое состояние
        # — «развёрнуто». Используется кастомной кнопкой «развернуть/восстановить».
        self._maximized = True

    def bind_window(self, window: webview.Window) -> None:
        self._window = window

    # ---- управление кастомным окном (frameless titlebar) -------------------
    def minimize_window(self, *_args) -> dict[str, bool]:
        if self._window is None:
            return {"ok": False}
        try:
            self._window.minimize()
        except Exception:
            logging.exception("Не удалось свернуть окно")
            return {"ok": False}
        return {"ok": True}

    def toggle_maximize(self, *_args) -> dict[str, bool]:
        """Переключает окно между двумя режимами: полностью развёрнуто ↔
        уменьшенное окно (~½ размеров экрана по пропорциям, по центру)."""
        if self._window is None:
            return {"ok": False}
        try:
            if self._maximized:
                self._apply_windowed_geometry()
                self._maximized = False
            else:
                maximize = getattr(self._window, "maximize", None)
                if callable(maximize):
                    maximize()
                    self._maximized = True
                else:  # запасной путь для старых версий pywebview
                    logging.warning("pywebview.Window.maximize недоступен")
        except Exception:
            logging.exception("Не удалось переключить размер окна")
            return {"ok": False, "maximized": self._maximized}
        return {"ok": True, "maximized": self._maximized}

    def _primary_screen_size(self) -> tuple[int, int, int, int]:
        """Возвращает (x, y, width, height) основного экрана; при недоступности —
        запасные значения для Full HD."""
        try:
            screens = webview.screens or []
            screen = screens[0] if screens else None
        except Exception:
            screen = None
        if screen is None:
            return 0, 0, 1920, 1080
        return (
            int(getattr(screen, "x", 0) or 0),
            int(getattr(screen, "y", 0) or 0),
            int(getattr(screen, "width", 1920) or 1920),
            int(getattr(screen, "height", 1080) or 1080),
        )

    def _apply_windowed_geometry(self) -> None:
        """Уменьшенный режим: половина размеров экрана по пропорциям, по центру.
        Ширина/высота не опускаются ниже минимально пригодных для интерфейса."""
        win = self._window
        assert win is not None
        screen_x, screen_y, screen_w, screen_h = self._primary_screen_size()
        width = max(screen_w // 2, MIN_WINDOW_WIDTH)
        height = max(screen_h // 2, MIN_WINDOW_HEIGHT)

        # На Windows центрируем через нативное окно WinForms (CenterToScreen) —
        # это надёжно с учётом DPI и центрирует по текущему монитору.
        if sys.platform == "win32" and self._center_windowed_native(width, height):
            return

        # Кроссплатформенный запасной путь через API pywebview.
        win.restore()
        win.resize(width, height)
        win.move(screen_x + (screen_w - width) // 2, screen_y + (screen_h - height) // 2)

    def _resolve_native_form(self):
        """Нативная форма WinForms (edgechromium backend) или None."""
        win = self._window
        if win is None:
            return None
        try:
            browser_view_class = getattr(win.gui, "BrowserView", None)
            if browser_view_class is None:
                return None
            return browser_view_class.instances.get(win.uid)
        except Exception:
            logging.exception("Не удалось получить нативное окно WinForms")
            return None

    def _center_windowed_native(self, width: int, height: int) -> bool:
        """Задаёт размер уменьшённого окна и центрирует его через WinForms.
        Возвращает True при успехе, иначе False (тогда сработает запасной путь)."""
        form = self._resolve_native_form()
        if form is None:
            return False

        try:
            import clr

            clr.AddReference("System.Windows.Forms")
            clr.AddReference("System.Drawing")

            from System import Action
            from System.Drawing import Size
            from System.Windows.Forms import FormWindowState
        except Exception:
            logging.exception("Не удалось загрузить сборки WinForms для центрирования")
            return False

        result = {"ok": False}

        def apply() -> None:
            try:
                form.WindowState = FormWindowState.Normal
                form.Size = Size(int(width), int(height))
                form.CenterToScreen()
                result["ok"] = True
            except Exception:
                logging.exception("Нативное центрирование окна не удалось")

        try:
            # Invoke маршалит вызов в UI-поток окна и ждёт завершения.
            form.Invoke(Action(apply))
        except Exception:
            logging.exception("Не удалось выполнить Invoke для центрирования окна")
            return False

        return bool(result["ok"])

    def close_window(self, *_args) -> dict[str, bool]:
        if self._window is None:
            return {"ok": False}
        try:
            self._window.destroy()
        except Exception:
            logging.exception("Не удалось закрыть окно")
            return {"ok": False}
        return {"ok": True}

    def choose_folder(self, payload: dict | None = None) -> dict[str, str | bool]:
        if self._window is None:
            raise RuntimeError("Окно приложения не инициализировано.")

        payload = payload or {}
        initial_path = str(payload.get("initial_path") or "").strip()
        selected_path = self._choose_directory(initial_path)
        if selected_path is None:
            return {"ok": False, "cancelled": True}

        return {"ok": True, "cancelled": False, "path": str(selected_path)}

    def save_graph_pdf(self, payload: dict | None = None) -> dict[str, str | bool]:
        if self._window is None:
            raise RuntimeError("Окно приложения не инициализировано.")
        if not self._supports_native_pdf_export():
            return {"ok": False, "cancelled": False, "unsupported": True}

        payload = payload or {}
        default_name = self._normalize_filename(payload.get("file_name") or "wash_graph.pdf")
        target_path = self._choose_target_path(default_name)
        if target_path is None:
            return {"ok": False, "cancelled": True}

        self._save_current_view_pdf(target_path)
        return {"ok": True, "cancelled": False, "path": str(target_path)}

    def _choose_target_path(self, default_name: str) -> Path | None:
        assert self._window is not None
        result = self._window.create_file_dialog(
            self._dialog_type("SAVE", webview.SAVE_DIALOG),
            save_filename=default_name,
            file_types=("PDF (*.pdf)",),
        )
        if not result:
            return None

        selected = result[0] if isinstance(result, (list, tuple)) else result
        path = Path(selected)
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        return path

    def _choose_directory(self, initial_path: str = "") -> Path | None:
        assert self._window is not None

        directory = ""
        if initial_path:
            candidate = Path(initial_path).expanduser()
            if candidate.exists():
                directory = str(candidate if candidate.is_dir() else candidate.parent)

        result = self._window.create_file_dialog(
            self._dialog_type("FOLDER", webview.FOLDER_DIALOG),
            directory=directory,
            allow_multiple=False,
        )
        if not result:
            return None

        selected = result[0] if isinstance(result, (list, tuple)) else result
        return Path(selected).expanduser()

    def _save_current_view_pdf(self, target_path: Path) -> None:
        if sys.platform == "darwin":
            pdf_bytes = self._capture_current_view_pdf_macos()
            target_path.write_bytes(pdf_bytes)
            return

        if sys.platform == "win32":
            self._capture_current_view_pdf_windows(target_path)
            return

        raise RuntimeError("Текущая платформа не поддерживает нативное сохранение PDF.")

    def _capture_current_view_pdf_macos(self) -> bytes:
        assert self._window is not None

        from PyObjCTools import AppHelper
        import WebKit

        browser_view_class = getattr(self._window.gui, "BrowserView", None)
        if browser_view_class is None:
            raise RuntimeError("Текущий GUI backend не поддерживает прямое сохранение PDF.")

        browser_view = browser_view_class.instances.get(self._window.uid)
        if browser_view is None or browser_view.webview is None:
            raise RuntimeError("Не удалось получить текущее окно WebView.")

        result: dict[str, bytes | str] = {}
        finished = threading.Event()

        def complete_with_data(pdf_data) -> None:
            try:
                result["data"] = bytes(pdf_data)
            except Exception as exc:  # pragma: no cover - platform-specific bridge failure
                result["error"] = str(exc)
            finally:
                finished.set()

        def complete_with_error(error: object) -> None:
            result["error"] = str(error)
            finished.set()

        def capture() -> None:
            try:
                webview_host = browser_view.webview
                if hasattr(WebKit, "WKPDFConfiguration") and hasattr(
                    webview_host, "createPDFWithConfiguration_completionHandler_"
                ):
                    config = WebKit.WKPDFConfiguration.alloc().init()
                    config.setRect_(webview_host.bounds())

                    def handler(pdf_data, error) -> None:
                        if error is not None:
                            complete_with_error(error)
                        else:
                            complete_with_data(pdf_data)

                    webview_host.createPDFWithConfiguration_completionHandler_(config, handler)
                    return

                complete_with_data(webview_host.dataWithPDFInsideRect_(webview_host.bounds()))
            except Exception as exc:  # pragma: no cover - platform-specific bridge failure
                complete_with_error(exc)

        AppHelper.callAfter(capture)

        if not finished.wait(15):
            raise RuntimeError("Не удалось сохранить PDF: истекло время ожидания.")
        if "error" in result:
            raise RuntimeError(str(result["error"]))

        pdf_bytes = result.get("data")
        if not isinstance(pdf_bytes, bytes):
            raise RuntimeError("Не удалось получить данные PDF.")
        return pdf_bytes

    def _capture_current_view_pdf_windows(self, target_path: Path) -> None:
        assert self._window is not None

        from System import Action

        browser_view_class = getattr(self._window.gui, "BrowserView", None)
        if browser_view_class is None:
            raise RuntimeError("Текущий GUI backend не поддерживает прямое сохранение PDF.")

        browser_view = browser_view_class.instances.get(self._window.uid)
        if browser_view is None or browser_view.webview is None:
            raise RuntimeError("Не удалось получить текущее окно WebView.")

        result: dict[str, bool | str] = {}
        finished = threading.Event()

        def capture() -> None:
            waiting_for_pdf = False
            try:
                webview_host = browser_view.webview
                core_webview = getattr(webview_host, "CoreWebView2", None)
                if core_webview is None:
                    raise RuntimeError("WebView2 ещё не инициализирован.")

                print_settings = core_webview.Environment.CreatePrintSettings()
                print_settings.ShouldPrintBackgrounds = True
                print_settings.ShouldPrintHeaderAndFooter = False
                print_settings.HeaderTitle = ""
                print_settings.FooterUri = ""
                self._configure_pdf_print_settings(print_settings)

                task = core_webview.PrintToPdfAsync(str(target_path), print_settings)

                def complete() -> None:
                    try:
                        if task.IsCanceled:
                            raise RuntimeError("WebView2 PDF export was cancelled.")
                        if task.IsFaulted:
                            raise RuntimeError(str(task.Exception))
                        if not task.Result:
                            raise RuntimeError("WebView2 PDF export failed.")

                        result["ok"] = True
                    except Exception as exc:  # pragma: no cover - Windows-specific bridge failure
                        result["error"] = str(exc)
                    finally:
                        finished.set()

                task.GetAwaiter().OnCompleted(Action(complete))
                waiting_for_pdf = True
                return
            except Exception as exc:  # pragma: no cover - Windows-specific bridge failure
                result["error"] = str(exc)
            finally:
                if not waiting_for_pdf:
                    finished.set()

        browser_view.Invoke(Action(capture))

        if not finished.wait(30):
            raise RuntimeError("Не удалось сохранить PDF: истекло время ожидания.")
        if "error" in result:
            raise RuntimeError(str(result["error"]))

    @staticmethod
    def _configure_pdf_print_settings(print_settings: object) -> None:
        try:
            from Microsoft.Web.WebView2.Core import CoreWebView2PrintOrientation

            print_settings.Orientation = CoreWebView2PrintOrientation.Landscape
        except Exception:
            pass

        for name, value in (
            ("PageWidth", 11.69),
            ("PageHeight", 8.27),
            ("MarginTop", 0.31),
            ("MarginBottom", 0.31),
            ("MarginLeft", 0.31),
            ("MarginRight", 0.31),
            ("ScaleFactor", 1.0),
        ):
            try:
                setattr(print_settings, name, value)
            except Exception:
                pass

    @staticmethod
    def _supports_native_pdf_export() -> bool:
        return sys.platform in {"darwin", "win32"}

    @staticmethod
    def _normalize_filename(value: str) -> str:
        normalized = str(value).strip() or "wash_graph"
        normalized = "".join("_" if char in '\\/:*?\"<>|' else char for char in normalized)
        normalized = "_".join(normalized.split())
        if not normalized.lower().endswith(".pdf"):
            normalized = f"{normalized}.pdf"
        return normalized

    @staticmethod
    def _dialog_type(kind: str, fallback: int) -> int:
        dialog_enum = getattr(webview, "FileDialog", None)
        if dialog_enum is not None and hasattr(dialog_enum, kind):
            return getattr(dialog_enum, kind)
        return fallback


def main() -> int:
    configure_logging()

    try:
        preflight_windows_runtime()
    except Exception as exc:  # pragma: no cover - Windows-specific runtime failure
        logging.exception("Windows runtime preflight failed")
        show_fatal_error(
            "Приложение не запустилось.\n\n"
            f"{exc}\n\n"
            f"Лог: {LOG_PATH}"
        )
        return 1

    try:
        web_app = load_web_app()
        logging.info("ASGI app imported successfully")
    except Exception as exc:
        logging.exception("ASGI app import failed")
        show_fatal_error(
            "Не удалось загрузить web-интерфейс приложения.\n\n"
            f"{exc}\n\n"
            f"Лог: {LOG_PATH}"
        )
        return 1

    server = DesktopServer(web_app)
    try:
        server.start()
    except Exception as exc:  # pragma: no cover - startup failures are environment-specific
        logging.exception("Local UI server failed to start")
        show_fatal_error(
            "Не удалось запустить локальный UI.\n\n"
            f"{exc}\n\n"
            f"Лог: {LOG_PATH}"
        )
        return 1

    bridge = DesktopBridge()
    window = None

    try:
        window = webview.create_window(
            APP_TITLE,
            html=build_desktop_loading_html(),
            js_api=bridge,
            width=1680,
            height=1040,
            min_size=(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT),
            maximized=True,
            text_select=True,
            # Кастомное окно: убираем стандартную рамку Windows, свою «шапку»
            # (перетаскивание, свернуть/развернуть/закрыть) рисуем в веб-интерфейсе.
            # easy_drag=False — перетаскивание только за область pywebview-drag-region,
            # чтобы не мешать взаимодействию с контентом.
            frameless=True,
            easy_drag=False,
        )
        if window is not None:
            bridge.bind_window(window)

        webview.start(
            load_desktop_window_url,
            args=(window, server.url),
            gui="edgechromium",
            private_mode=False,
            storage_path=str(resolve_webview_storage_path()),
        )
    except Exception as exc:  # pragma: no cover - GUI startup failure is platform-specific
        logging.exception("Desktop GUI failed to start")
        show_fatal_error(
            "Приложение не запустилось.\n\n"
            f"{exc}\n\n"
            f"Лог: {LOG_PATH}"
        )
        return 1
    finally:
        server.stop()
        if window is not None:
            try:
                window.destroy()
            except Exception:
                logging.exception("Window destroy failed during shutdown")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - final crash guard
        try:
            configure_logging()
            logging.error("Unhandled fatal error\n%s", traceback.format_exc())
        except Exception:
            pass
        show_fatal_error(
            "Приложение завершилось с критической ошибкой.\n\n"
            f"{exc}\n\n"
            f"Лог: {LOG_PATH}"
        )
        raise
