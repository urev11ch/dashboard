"""Веб-просмотр панели открывается во встроенном WebView2, где отключена
проверка TLS-сертификата (--ignore-certificate-errors действует на весь движок).
Поэтому в это окно должны попадать только адреса локальной сети / сохранённых
панелей — иначе JS в окне увёл бы незащищённый движок на произвольный https."""
import run_wash_desktop as desktop


def test_allows_private_and_loopback(monkeypatch):
    monkeypatch.setattr(desktop, "_saved_panel_hosts", lambda: set())
    assert desktop.is_local_panel_url("https://192.168.1.50/")
    assert desktop.is_local_panel_url("http://10.0.0.1:8080/easyweb.html")
    assert desktop.is_local_panel_url("https://127.0.0.1/")
    assert desktop.is_local_panel_url("https://169.254.10.10/")  # link-local


def test_rejects_public_ip_and_bad_scheme(monkeypatch):
    monkeypatch.setattr(desktop, "_saved_panel_hosts", lambda: set())
    assert not desktop.is_local_panel_url("https://8.8.8.8/")
    assert not desktop.is_local_panel_url("https://evil.example.com/")
    assert not desktop.is_local_panel_url("ftp://192.168.1.50/")
    assert not desktop.is_local_panel_url("file:///etc/passwd")
    assert not desktop.is_local_panel_url("")


def test_saved_hostname_is_allowed(monkeypatch):
    # Кастомное DNS-имя сохранённой панели — доверяем реестру, не резолвим DNS.
    monkeypatch.setattr(desktop, "_saved_panel_hosts", lambda: {"cmt-panel.local"})
    assert desktop.is_local_panel_url("https://cmt-panel.local/")
    assert not desktop.is_local_panel_url("https://unknown.local/")


def test_saved_public_ip_is_allowed(monkeypatch):
    monkeypatch.setattr(desktop, "_saved_panel_hosts", lambda: {"8.8.8.8"})
    assert desktop.is_local_panel_url("https://8.8.8.8/")
    assert not desktop.is_local_panel_url("https://1.1.1.1/")


class _Handlers(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self


def _fake_panel_window():
    from types import SimpleNamespace

    core = SimpleNamespace(FrameNavigationStarting=_Handlers())
    control = SimpleNamespace(NavigationStarting=_Handlers(), CoreWebView2=core)
    return SimpleNamespace(native=SimpleNamespace(webview=control)), control, core


def _nav(uri):
    from types import SimpleNamespace

    return SimpleNamespace(Uri=uri, Cancel=False)


def test_panel_window_cancels_navigation_off_panel(monkeypatch):
    # Стартовый URL проверяется при открытии, но редирект/ссылка внутри окна
    # тоже должны пройти фильтр — иначе окно с отключённым TLS уйдёт наружу.
    monkeypatch.setattr(desktop, "_saved_panel_hosts", lambda: set())
    window, control, _core = _fake_panel_window()
    desktop._guard_panel_navigation(window)
    (handler,) = control.NavigationStarting

    inside = _nav("https://192.168.1.50/app/dashboard")
    handler(None, inside)
    assert inside.Cancel is False

    outside = _nav("https://evil.example.com/login")
    handler(None, outside)
    assert outside.Cancel is True


def test_panel_window_guards_frames_after_first_navigation(monkeypatch):
    # iframe не вызывает NavigationStarting страницы — нужен FrameNavigationStarting.
    monkeypatch.setattr(desktop, "_saved_panel_hosts", lambda: set())
    window, control, core = _fake_panel_window()
    desktop._guard_panel_navigation(window)
    (handler,) = control.NavigationStarting
    handler(None, _nav("https://192.168.1.50/"))
    handler(None, _nav("https://192.168.1.50/other"))
    (frame_handler,) = core.FrameNavigationStarting  # подписка ровно одна

    for allowed in ("https://192.168.1.50/frame", "about:srcdoc", "data:text/html,x",
                    "blob:https://192.168.1.50/0f1e"):
        args = _nav(allowed)
        frame_handler(None, args)
        assert args.Cancel is False, allowed
    for denied in ("https://attacker.example/", "blob:https://attacker.example/1", ""):
        args = _nav(denied)
        frame_handler(None, args)
        assert args.Cancel is True, denied


def test_open_panel_window_guards_before_showing(monkeypatch):
    # create_window в работающем приложении создаёт окно синхронно: фильтр должен
    # встать до показа, а не через событие, которое к тому моменту уже прошло.
    import sys
    from types import SimpleNamespace

    monkeypatch.setattr(desktop, "_saved_panel_hosts", lambda: set())
    calls = []
    window, control, _core = _fake_panel_window()
    window.show = lambda: calls.append(("show", len(control.NavigationStarting)))
    window.events = SimpleNamespace(closed=_Handlers())

    def create_window(title, url, **kwargs):
        calls.append(("create", kwargs.get("hidden")))
        return window

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create_window))
    bridge = desktop.DesktopBridge()
    assert bridge.open_panel_window({"url": "https://192.168.1.50/"}) == {"ok": True}
    assert calls == [("create", True), ("show", 1)]


def test_panel_window_fallback_leaves_foreign_page(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(desktop, "_saved_panel_hosts", lambda: set())
    loaded = _Handlers()
    shown_html = []
    window = SimpleNamespace(
        native=None,  # нет WebView2-контрола
        events=SimpleNamespace(loaded=loaded),
        get_current_url=lambda: "https://evil.example.com/",
        load_html=shown_html.append,
    )
    desktop._guard_panel_navigation(window)
    (on_loaded,) = loaded
    on_loaded(window)
    assert len(shown_html) == 1

    window.get_current_url = lambda: None  # своя заглушка — не зацикливаемся
    on_loaded(window)
    assert len(shown_html) == 1
