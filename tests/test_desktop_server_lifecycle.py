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
