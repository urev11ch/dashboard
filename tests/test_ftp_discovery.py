"""Тесты обнаружения панелей в локальной сети (кнопка «Найти панель»).

E2E в этой сборке проверить нельзя, поэтому логику скана покрываем юнит-тестами:
определение приватной подсети, TCP-проба порта 21 с чтением приветствия FTP и
сортировка результатов (Weintek-подобные — первыми).
"""
import asyncio
import socket

import webapp.app as app


class _FakeUDPSocket:
    """Заглушка UDP-сокета для _local_ipv4_networks: connect не шлёт пакетов, а
    getsockname отдаёт заданный «свой» адрес (либо connect падает — нет маршрута)."""

    def __init__(self, local_ip):
        self._local_ip = local_ip

    def connect(self, _addr):
        if self._local_ip is None:
            raise OSError("нет маршрута")

    def getsockname(self):
        return (self._local_ip, 12345)

    def close(self):
        pass


def _patch_local_ip(monkeypatch, local_ip):
    monkeypatch.setattr(app.socket, "socket", lambda *a, **k: _FakeUDPSocket(local_ip))


def test_local_networks_private_ip_yields_slash24(monkeypatch):
    _patch_local_ip(monkeypatch, "192.168.1.50")
    own, networks = app._local_ipv4_networks()
    assert own == "192.168.1.50"
    assert [str(net) for net in networks] == ["192.168.1.0/24"]


def test_local_networks_public_ip_not_scanned(monkeypatch):
    _patch_local_ip(monkeypatch, "8.8.8.8")
    own, networks = app._local_ipv4_networks()
    assert own == "8.8.8.8"
    assert networks == []


def test_local_networks_loopback_not_scanned(monkeypatch):
    _patch_local_ip(monkeypatch, "127.0.0.1")
    _own, networks = app._local_ipv4_networks()
    assert networks == []


def test_local_networks_no_route(monkeypatch):
    _patch_local_ip(monkeypatch, None)
    own, networks = app._local_ipv4_networks()
    assert own == ""
    assert networks == []


def _reserve_closed_port():
    """Порт, на котором заведомо никто не слушает (для проверки «HTTP закрыт»)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _ftp_banner_handler(banner: bytes):
    async def handle(_reader, writer):
        writer.write(banner)
        await writer.drain()
        try:
            await asyncio.sleep(0.05)  # даём пробе прочитать баннер, затем закрываемся
        finally:
            writer.close()

    return handle


def _http_handler(body: bytes):
    async def handle(reader, writer):
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=1)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
            pass
        writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n" + body)
        await writer.drain()
        writer.close()

    return handle


# Веб-оболочка EasyWeb (как отдаёт панель на :80): содержит маркеры easywebConfig
# и <title>cMT</title>, по которым панель опознаётся без FTP-пароля.
_EASYWEB_HTML = (
    b"<html><head><title>cMT</title>"
    b"<script>window.easywebConfig = {webPanel:'false'};</script>"
    b"</head><body></body></html>"
)


def test_probe_easyweb_identifies_panel(monkeypatch):
    # Дженерик-баннер Pure-FTPd [TLS], но веб-интерфейс EasyWeb на :80 → панель.
    # Имя берётся из обратного DNS (cMT-3C6F).
    async def fake_dns(_host):
        return "cMT-3C6F"

    async def scenario():
        ftp = await asyncio.start_server(
            _ftp_banner_handler(b"220 ---------- WELCOME TO PURE-FTPD [TLS] ----------\r\n"),
            "127.0.0.1", 0,
        )
        http = await asyncio.start_server(_http_handler(_EASYWEB_HTML), "127.0.0.1", 0)
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", ftp.sockets[0].getsockname()[1])
        monkeypatch.setattr(app, "HTTP_EASYWEB_PORTS", ((http.sockets[0].getsockname()[1], False),))
        monkeypatch.setattr(app, "_reverse_dns_name", fake_dns)
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            ftp.close()
            await ftp.wait_closed()
            http.close()
            await http.wait_closed()
        return result

    result = asyncio.run(scenario())
    assert result is not None
    assert result["confirmed_weintek"] is True  # опознано веб-мордой, не паролем
    assert result["likely_weintek"] is True
    assert result["name"] == "cMT-3C6F"  # имя из обратного DNS


def test_probe_name_falls_back_to_easyweb_title(monkeypatch):
    # Обратный DNS не разрешился → имя берём из <title> EasyWeb («cMT»).
    async def no_dns(_host):
        return ""

    async def scenario():
        ftp = await asyncio.start_server(
            _ftp_banner_handler(b"220 pure-ftpd\r\n"), "127.0.0.1", 0
        )
        http = await asyncio.start_server(_http_handler(_EASYWEB_HTML), "127.0.0.1", 0)
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", ftp.sockets[0].getsockname()[1])
        monkeypatch.setattr(app, "HTTP_EASYWEB_PORTS", ((http.sockets[0].getsockname()[1], False),))
        monkeypatch.setattr(app, "_reverse_dns_name", no_dns)
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            ftp.close()
            await ftp.wait_closed()
            http.close()
            await http.wait_closed()
        return result

    result = asyncio.run(scenario())
    assert result is not None
    assert result["confirmed_weintek"] is True
    assert result["name"] == "cMT"  # из <title>cMT</title>


def test_probe_non_easyweb_http_not_identified(monkeypatch):
    # Порт 80 открыт, но это не EasyWeb (обычный роутер) — не панель.
    plain = b"<html><head><title>Router</title></head><body>hi</body></html>"

    async def scenario():
        ftp = await asyncio.start_server(
            _ftp_banner_handler(b"220 ProFTPD Server ready\r\n"), "127.0.0.1", 0
        )
        http = await asyncio.start_server(_http_handler(plain), "127.0.0.1", 0)
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", ftp.sockets[0].getsockname()[1])
        monkeypatch.setattr(app, "HTTP_EASYWEB_PORTS", ((http.sockets[0].getsockname()[1], False),))
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            ftp.close()
            await ftp.wait_closed()
            http.close()
            await http.wait_closed()
        return result

    result = asyncio.run(scenario())
    assert result is not None
    assert result["confirmed_weintek"] is False
    assert result["likely_weintek"] is False


def test_probe_banner_hint_marks_likely_when_http_closed(monkeypatch):
    # HTTP :80 закрыт, но слово Weintek в баннере → мягкая метка likely.
    async def scenario():
        ftp = await asyncio.start_server(
            _ftp_banner_handler(b"220 Weintek cMT FTP Server ready\r\n"), "127.0.0.1", 0
        )
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", ftp.sockets[0].getsockname()[1])
        monkeypatch.setattr(app, "HTTP_EASYWEB_PORTS", ((_reserve_closed_port(), False),))
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            ftp.close()
            await ftp.wait_closed()
        return result

    result = asyncio.run(scenario())
    assert result is not None
    assert result["banner"].startswith("220")
    assert result["confirmed_weintek"] is False  # веб-морда не подтвердила
    assert result["likely_weintek"] is True  # но баннер намекает


def test_probe_generic_banner_http_closed_not_weintek(monkeypatch):
    async def scenario():
        ftp = await asyncio.start_server(
            _ftp_banner_handler(b"220 ProFTPD Server ready\r\n"), "127.0.0.1", 0
        )
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", ftp.sockets[0].getsockname()[1])
        monkeypatch.setattr(app, "HTTP_EASYWEB_PORTS", ((_reserve_closed_port(), False),))
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            ftp.close()
            await ftp.wait_closed()
        return result

    result = asyncio.run(scenario())
    assert result is not None
    assert result["likely_weintek"] is False


def test_probe_http_easyweb_falls_back_to_https(monkeypatch):
    # Веб EasyWeb только по https (:443): :80 не отвечает → пробуем :443.
    calls = []

    async def fake_fetch(host, port, use_tls):
        calls.append((port, use_tls))
        return "cMT" if use_tls else None  # :80 — нет, :443 — есть

    monkeypatch.setattr(app, "_fetch_easyweb_title", fake_fetch)
    monkeypatch.setattr(app, "HTTP_EASYWEB_PORTS", ((80, False), (443, True)))

    result = asyncio.run(app._probe_http_easyweb("10.0.0.9"))
    assert result == ("cMT", "https")  # title + схема
    assert calls == [(80, False), (443, True)]  # порядок: сначала :80, затем :443


def test_probe_closed_port_returns_none(monkeypatch):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()  # порт освобождён и никто не слушает → connection refused
    monkeypatch.setattr(app, "FTP_DEFAULT_PORT", free_port)

    async def scenario():
        return await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))

    assert asyncio.run(scenario()) is None


def test_discover_returns_only_weintek_panels_and_excludes_own(monkeypatch):
    monkeypatch.setattr(
        app,
        "_local_ipv4_networks",
        lambda: ("192.168.1.50", [app.ipaddress.ip_network("192.168.1.0/24")]),
    )
    canned = {
        # Обычный FTP-хост — не панель: в список не попадает, но считается.
        "192.168.1.10": {
            "host": "192.168.1.10", "port": 21, "banner": "220 generic",
            "likely_weintek": False, "confirmed_weintek": False,
        },
        # Опознан по баннеру.
        "192.168.1.20": {
            "host": "192.168.1.20", "port": 21, "banner": "220 Weintek",
            "likely_weintek": True, "confirmed_weintek": False,
        },
        # Подтверждён входом — должен идти первым.
        "192.168.1.30": {
            "host": "192.168.1.30", "port": 21, "banner": "220 pure-ftpd",
            "likely_weintek": True, "confirmed_weintek": True,
        },
    }
    seen = []

    async def fake_probe(host, _sem):
        seen.append(host)
        return canned.get(host)

    monkeypatch.setattr(app, "_probe_ftp_host", fake_probe)
    monkeypatch.setattr(app, "_read_arp_table", lambda: {})  # MAC не влияет

    result = asyncio.run(app.discover_ftp_panels())
    assert result["network"] == "192.168.1.0/24"
    # /24 = 254 адреса-хоста, минус свой (192.168.1.50) = 253 проверенных.
    assert result["scanned"] == 253
    assert result["ftp_hosts"] == 3  # откликнулись все три, включая не-панель
    assert "192.168.1.50" not in seen  # свой адрес не сканируем
    hosts = [panel["host"] for panel in result["panels"]]
    # Только панели Weintek; подтверждённая входом — первой; обычный FTP скрыт.
    assert hosts == ["192.168.1.30", "192.168.1.20"]


def test_discover_identifies_panel_by_weintek_mac(monkeypatch):
    # Хост ответил на :21, но по web/баннеру НЕ опознан. ARP показывает MAC
    # Weintek (00:0C:26) → панель попадает в список (подтверждена по MAC).
    monkeypatch.setattr(
        app,
        "_local_ipv4_networks",
        lambda: ("192.168.1.50", [app.ipaddress.ip_network("192.168.1.0/24")]),
    )
    canned = {
        "192.168.1.77": {
            "host": "192.168.1.77", "port": 21, "banner": "220 pure-ftpd [tls]",
            "name": "", "likely_weintek": False, "confirmed_weintek": False,
        },
    }

    async def fake_probe(host, _sem):
        return canned.get(host)

    monkeypatch.setattr(app, "_probe_ftp_host", fake_probe)
    monkeypatch.setattr(
        app, "_read_arp_table", lambda: {"192.168.1.77": "00:0c:26:11:3c:6f"}
    )

    result = asyncio.run(app.discover_ftp_panels())
    panels = result["panels"]
    assert [p["host"] for p in panels] == ["192.168.1.77"]
    assert panels[0]["confirmed_weintek"] is True
    assert panels[0]["mac_weintek"] is True
    assert panels[0]["name"] == "cMT-3C6F"  # имя из последних октетов MAC (3C:6F)


def test_discover_adds_mac_panel_without_ftp_response(monkeypatch):
    # Панель по MAC есть в ARP, но на :21 не ответила (FTP выкл/медленный) —
    # всё равно добавляем в список.
    monkeypatch.setattr(
        app,
        "_local_ipv4_networks",
        lambda: ("192.168.1.50", [app.ipaddress.ip_network("192.168.1.0/24")]),
    )

    async def fake_probe(_host, _sem):
        return None  # никто не ответил на :21

    monkeypatch.setattr(app, "_probe_ftp_host", fake_probe)
    monkeypatch.setattr(
        app,
        "_read_arp_table",
        lambda: {"192.168.1.88": "00-0C-26-11-3C-6F", "192.168.1.9": "aa:bb:cc:dd:ee:ff"},
    )

    result = asyncio.run(app.discover_ftp_panels())
    hosts = [p["host"] for p in result["panels"]]
    assert hosts == ["192.168.1.88"]  # только Weintek-MAC; чужой MAC не добавлен
    assert result["ftp_hosts"] == 0
    assert result["panels"][0]["name"] == "cMT-3C6F"  # из MAC …:3C:6F


def test_is_weintek_mac_matches_oui():
    assert app._is_weintek_mac("00:0c:26:11:22:33") is True
    assert app._is_weintek_mac("00-0C-26-AA-BB-CC") is True  # дефисы, верхний регистр
    assert app._is_weintek_mac("aa:bb:cc:dd:ee:ff") is False
    assert app._is_weintek_mac("") is False


def test_weintek_name_from_mac():
    assert app._weintek_name_from_mac("00:0c:26:11:3c:6f") == "cMT-3C6F"
    assert app._weintek_name_from_mac("00-0C-26-AA-BB-CC") == "cMT-BBCC"  # дефисы
    assert app._weintek_name_from_mac("00:0c:26") == ""  # неполный MAC
    assert app._weintek_name_from_mac("") == ""


def test_read_arp_table_parses_output(monkeypatch):
    sample = (
        "192.168.1.88 dev eth0 lladdr 00:0c:26:11:22:33 REACHABLE\n"
        "192.168.1.9 dev eth0 lladdr aa:bb:cc:dd:ee:ff STALE\n"
        "192.168.1.5 dev eth0  FAILED\n"  # без MAC — пропускается
    )

    class _Proc:
        stdout = sample

    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: _Proc())
    table = app._read_arp_table()
    assert table["192.168.1.88"] == "00:0c:26:11:22:33"
    assert table["192.168.1.9"] == "aa:bb:cc:dd:ee:ff"
    assert "192.168.1.5" not in table


def test_discover_no_network_returns_empty(monkeypatch):
    monkeypatch.setattr(app, "_local_ipv4_networks", lambda: ("", []))
    result = asyncio.run(app.discover_ftp_panels())
    assert result == {"scanned": 0, "network": "", "panels": []}
