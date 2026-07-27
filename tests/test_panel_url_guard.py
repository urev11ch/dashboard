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
