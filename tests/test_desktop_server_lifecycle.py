"""Гонка «закрытие окна во время старта сервера».

start() крутится в webview-потоке и может долго висеть в wait_until_ready.
Если окно закрыли, stop() гасит стартующий uvicorn; раньше start() принимал
это за перехват порта и поднимал ВТОРОЙ сервер уже после запрошенного
завершения. Проверяем, что после stop() повторный запуск запрещён.
"""
import run_wash_desktop as desktop


def _server():
    # uvicorn.Config не валидирует приложение до run(), поэтому заглушки хватает.
    return desktop.DesktopServer(web_app=lambda *a, **k: None)


def test_start_does_not_relaunch_after_stop(monkeypatch):
    srv = _server()
    srv.stop()  # поток не запускался -> просто выставит _stop_requested
    assert srv._stop_requested is True

    started = []
    monkeypatch.setattr(srv.thread, "start", lambda: started.append(True))
    prepared = []
    monkeypatch.setattr(srv, "_prepare", lambda port: prepared.append(port))

    srv.start()

    assert started == []  # новый uvicorn не поднимаем
    assert prepared == []  # и новый поток/порт не готовим


def test_start_bails_out_when_stop_requested_mid_retry(monkeypatch):
    srv = _server()

    # Первая попытка "падает" как будто порт перехвачен, но к этому моменту уже
    # пришёл stop() — повторного запуска быть не должно.
    def fake_wait():
        srv.stop()
        raise RuntimeError("поток умер")

    monkeypatch.setattr(srv, "wait_until_ready", fake_wait)
    monkeypatch.setattr(srv.thread, "start", lambda: None)
    prepared = []
    monkeypatch.setattr(srv, "_prepare", lambda port: prepared.append(port))

    srv.start()  # не должен бросить и не должен перезапускать

    assert prepared == []


def test_stop_is_safe_before_start():
    srv = _server()
    # Не должно бросать RuntimeError на join() непущенного потока.
    srv.stop()
    assert srv.server.should_exit is True


# --- дочерние окна панелей не должны переживать главное окно ---------------
#
# Пока живо хоть одно окно webview, webview.start() не возвращается: процесс
# остаётся в памяти без видимых окон, держит мьютекс единственного экземпляра и
# открытый .exe. Установщик обновления в этом случае сообщает «программе
# установки не удалось закрыть все приложения».


class _FakeWindow:
    def __init__(self, fail=False):
        self.destroyed = False
        self.fail = fail

    def destroy(self):
        if self.fail:
            raise RuntimeError("окно уже закрыто")
        self.destroyed = True


def _bridge_with_panels(**kwargs):
    bridge = desktop.DesktopBridge()
    panels = {url: _FakeWindow(**kwargs) for url in ("http://panel-1/", "http://panel-2/")}
    bridge._panel_windows.update(panels)
    return bridge, panels


def test_close_window_destroys_panel_windows():
    bridge, panels = _bridge_with_panels()
    main = _FakeWindow()
    bridge._window = main

    assert bridge.close_window() == {"ok": True}

    assert main.destroyed is True
    assert all(window.destroyed for window in panels.values())
    assert bridge._panel_windows == {}


def test_main_window_closed_event_destroys_panels():
    # Крестик и Restart Manager закрывают окно мимо close_window — панели
    # закрываются по событию closed главного окна.
    bridge, panels = _bridge_with_panels()

    bridge._on_main_window_closed()

    assert all(window.destroyed for window in panels.values())
    assert bridge._panel_windows == {}


def test_panel_cleanup_survives_destroy_failure():
    # Окно могло закрыться само: исключение не должно оставлять запись в реестре
    # окон, иначе процесс «ждёт» уже несуществующее окно.
    bridge, panels = _bridge_with_panels(fail=True)

    bridge._on_main_window_closed()

    assert bridge._panel_windows == {}


def test_bind_window_subscribes_to_closed():
    class _Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class _Events:
        def __init__(self):
            self.maximized = _Event()
            self.restored = _Event()
            self.closed = _Event()

    class _Window:
        def __init__(self):
            self.events = _Events()

    bridge = desktop.DesktopBridge()
    window = _Window()
    bridge.bind_window(window)

    assert window.events.closed.handlers == [bridge._on_main_window_closed]


def test_background_shutdown_finds_loader_thread(monkeypatch):
    # Поток загрузчика хранится в webapp.state; раньше его искали в webapp.app,
    # получали None, и выход не ждал фоновую загрузку.
    import threading
    import types

    from webapp import state as state_module

    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    job = types.SimpleNamespace(status="running", cancel_requested=False)
    monkeypatch.setattr(state_module, "_workspace_job_thread", thread)
    monkeypatch.setattr(state_module.state, "workspace_job", job)
    try:
        assert desktop.request_background_shutdown() is thread
        assert job.cancel_requested is True
        assert job.status == "cancelling"
    finally:
        release.set()
        thread.join(1)
