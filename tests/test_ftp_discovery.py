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


def test_probe_open_port_reads_banner_and_flags_weintek(monkeypatch):
    async def scenario():
        async def handle(_reader, writer):
            writer.write(b"220 Weintek cMT FTP Server ready\r\n")
            await writer.drain()
            try:
                await asyncio.sleep(0.2)
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", port)
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            server.close()
            await server.wait_closed()
        return result, port

    result, port = asyncio.run(scenario())
    assert result is not None
    assert result["host"] == "127.0.0.1"
    assert result["port"] == port
    assert result["banner"].startswith("220")
    assert result["likely_weintek"] is True


def test_probe_open_port_generic_banner_not_weintek(monkeypatch):
    async def scenario():
        async def handle(_reader, writer):
            writer.write(b"220 ProFTPD Server ready\r\n")
            await writer.drain()
            try:
                await asyncio.sleep(0.2)
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", port)
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            server.close()
            await server.wait_closed()
        return result

    result = asyncio.run(scenario())
    assert result is not None
    assert result["likely_weintek"] is False


def test_probe_confirms_weintek_on_uploadhis_login(monkeypatch):
    # Панель на дефолтном пароле: USER uploadhis → 331, PASS 111111 → 230.
    # Успешный вход однозначно опознаёт панель даже при дженерик-баннере.
    async def scenario():
        async def handle(reader, writer):
            writer.write(b"220 ---------- WELCOME TO PURE-FTPD ----------\r\n")
            await writer.drain()
            got_user = False
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    cmd = line.decode("latin-1").strip()
                    if cmd.upper() == "USER UPLOADHIS":
                        got_user = True
                        writer.write(b"331 Password required\r\n")
                    elif cmd.upper() == "PASS 111111" and got_user:
                        writer.write(b"230 Login successful\r\n")
                    elif cmd.upper() == "QUIT":
                        writer.write(b"221 Bye\r\n")
                        await writer.drain()
                        break
                    else:
                        writer.write(b"530 Denied\r\n")
                    await writer.drain()
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", port)
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            server.close()
            await server.wait_closed()
        return result

    result = asyncio.run(scenario())
    assert result is not None
    assert result["confirmed_weintek"] is True  # опознано входом, не баннером
    assert result["likely_weintek"] is True


def test_probe_wrong_password_not_confirmed(monkeypatch):
    # Пароль сменён с заводского: PASS 111111 → 530. Хост остаётся неопознанным.
    async def scenario():
        async def handle(reader, writer):
            writer.write(b"220 ---------- WELCOME TO PURE-FTPD ----------\r\n")
            await writer.drain()
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    cmd = line.decode("latin-1").strip().upper()
                    if cmd == "USER UPLOADHIS":
                        writer.write(b"331 Password required\r\n")
                    elif cmd.startswith("PASS"):
                        writer.write(b"530 Login incorrect\r\n")
                    elif cmd == "QUIT":
                        writer.write(b"221 Bye\r\n")
                        await writer.drain()
                        break
                    else:
                        writer.write(b"530 Denied\r\n")
                    await writer.drain()
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(app, "FTP_DEFAULT_PORT", port)
        try:
            result = await app._probe_ftp_host("127.0.0.1", asyncio.Semaphore(4))
        finally:
            server.close()
            await server.wait_closed()
        return result

    result = asyncio.run(scenario())
    assert result is not None
    assert result["confirmed_weintek"] is False
    assert result["likely_weintek"] is False  # баннер без слова Weintek


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

    result = asyncio.run(app.discover_ftp_panels())
    assert result["network"] == "192.168.1.0/24"
    # /24 = 254 адреса-хоста, минус свой (192.168.1.50) = 253 проверенных.
    assert result["scanned"] == 253
    assert result["ftp_hosts"] == 3  # откликнулись все три, включая не-панель
    assert "192.168.1.50" not in seen  # свой адрес не сканируем
    hosts = [panel["host"] for panel in result["panels"]]
    # Только панели Weintek; подтверждённая входом — первой; обычный FTP скрыт.
    assert hosts == ["192.168.1.30", "192.168.1.20"]


def test_discover_no_network_returns_empty(monkeypatch):
    monkeypatch.setattr(app, "_local_ipv4_networks", lambda: ("", []))
    result = asyncio.run(app.discover_ftp_panels())
    assert result == {"scanned": 0, "network": "", "panels": []}
