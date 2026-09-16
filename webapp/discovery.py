"""Обнаружение панелей Weintek в локальной сети (кнопка «Найти панель»).

Скан только по кнопке, только по приватным локальным подсетям, только порт 21.
Опознание панели: MAC-OUI Weintek из ARP, веб-интерфейс EasyWeb (:80/:443) и
мягкая эвристика по баннеру FTP. Выделено из webapp/app.py.

Тесты патчат символы этого модуля как app.discovery.<имя> (например
app.discovery._probe_ftp_host, app.discovery.HTTP_EASYWEB_PORTS).
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import ssl
import subprocess
from typing import Any

from webapp.config import (
    FTP_DEFAULT_PORT,
    FTP_DISCOVERY_BANNER_TIMEOUT,
    FTP_DISCOVERY_CONCURRENCY,
    FTP_DISCOVERY_MAX_HOSTS,
    FTP_DISCOVERY_MAX_NETWORKS,
    FTP_DISCOVERY_PROBE_TIMEOUT,
    FTP_WEINTEK_HINTS,
    HTTP_EASYWEB_MARKERS,
    HTTP_EASYWEB_PORTS,
    HTTP_EASYWEB_READ_LIMIT,
    WEINTEK_MAC_PREFIXES,
)


def _route_ipv4() -> str:
    """Свой основной IPv4 — тот, что ОС выберет для маршрута по умолчанию. Адрес
    выбираем UDP-«подключением» к внешнему адресу: пакет не отправляется, ОС лишь
    выбирает исходящий интерфейс. "" — маршрута нет."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


_INET_RE = re.compile(r"\binet (?:addr:)?(\d{1,3}(?:\.\d{1,3}){3})")


def _interface_ipv4_from_os() -> list[str]:
    """Адреса интерфейсов из `ip -4 -o addr show` (фолбэк — `ifconfig -a`).
    Только POSIX: на Windows все адреса адаптеров отдаёт getaddrinfo по своему
    имени. Пусто, если команд нет."""
    for command in (["ip", "-4", "-o", "addr", "show"], ["ifconfig", "-a"]):
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.stdout:
            return _INET_RE.findall(proc.stdout)
    return []


def _interface_ipv4() -> list[str]:
    """IPv4 ВСЕХ локальных интерфейсов, а не только адрес маршрута по умолчанию:
    ПК инженера часто стоит сразу в двух заводских подсетях (напр. 192.168.18.x
    и 192.168.19.x), и панель может быть в любой из них — по одному адресу
    маршрута вторая подсеть не сканировалась вовсе."""
    addresses: list[str] = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except (OSError, UnicodeError):
        infos = []
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else None
        if isinstance(sockaddr, tuple) and sockaddr and isinstance(sockaddr[0], str):
            addresses.append(sockaddr[0])
    if os.name != "nt":
        addresses.extend(_interface_ipv4_from_os())
    return addresses


def _private_slash24(address: str) -> ipaddress.IPv4Network | None:
    """/24 вокруг адреса — типовая заводская подсеть. None, если сканировать
    нечего: не IPv4, публичный адрес (не «шумим» вне доверенной локальной сети),
    loopback или APIPA 169.254.x (DHCP не дал адреса)."""
    try:
        addr = ipaddress.ip_address(address)
    except ValueError:
        return None
    if not isinstance(addr, ipaddress.IPv4Address):
        return None
    if not addr.is_private or addr.is_loopback or addr.is_link_local:
        return None
    return ipaddress.ip_network(f"{address}/24", strict=False)


def _local_ipv4_networks() -> tuple[str, list[ipaddress.IPv4Network]]:
    """Свой основной IPv4 и приватные /24, по которым имеет смысл искать панель —
    по одной на каждый локальный интерфейс. Подсеть адреса маршрута по умолчанию
    идёт первой, дубликаты убираются, число подсетей ограничено
    FTP_DISCOVERY_MAX_NETWORKS (скан каждой — это 254 TCP-пробы)."""
    own_ip = _route_ipv4()
    networks: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()
    for address in [own_ip, *_interface_ipv4()]:
        network = _private_slash24(address)
        if network is None or str(network) in seen:
            continue
        seen.add(str(network))
        networks.append(network)
        if len(networks) >= FTP_DISCOVERY_MAX_NETWORKS:
            break
    return own_ip, networks


def parse_scan_subnet(value: str) -> ipaddress.IPv4Network:
    """Подсеть, заданную вручную («192.168.18.0/24» или «192.168.18.5» — без
    маски подразумевается /24), приводит к сети для скана. Нужна, когда панель
    за маршрутизатором и своего адреса в её подсети у ПК нет. ValueError —
    некорректный адрес, публичная сеть или слишком широкий диапазон."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Не указана подсеть.")
    try:
        network = ipaddress.ip_network(text if "/" in text else f"{text}/24", strict=False)
    except ValueError as exc:
        raise ValueError(f"Некорректная подсеть: {text}") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("Поддерживаются только адреса IPv4.")
    if not network.is_private or network.is_loopback or network.is_link_local:
        raise ValueError("Сканировать можно только приватную локальную сеть.")
    if network.num_addresses - 2 > FTP_DISCOVERY_MAX_HOSTS:
        raise ValueError(
            f"Слишком широкая подсеть: {network} — максимум "
            f"{FTP_DISCOVERY_MAX_HOSTS} адресов."
        )
    return network


async def _ftp_read_reply(reader: asyncio.StreamReader) -> tuple[str, str]:
    """Читает ответ FTP (возможно многострочный `NNN-...` до строки `NNN ...`).
    Возвращает (код, текст). Пустой код — соединение закрылось/таймаут."""
    line = await asyncio.wait_for(reader.readline(), timeout=FTP_DISCOVERY_BANNER_TIMEOUT)
    if not line:
        return "", ""
    text = line.decode("latin-1", "replace")
    code = text[:3]
    # Многострочный ответ: первая строка вида "220-...", конец — "220 ...".
    if len(text) >= 4 and text[3] == "-" and code.isdigit():
        while True:
            more = await asyncio.wait_for(
                reader.readline(), timeout=FTP_DISCOVERY_BANNER_TIMEOUT
            )
            if not more:
                break
            chunk = more.decode("latin-1", "replace")
            text += chunk
            if chunk[:3] == code and len(chunk) >= 4 and chunk[3] == " ":
                break
    return code, text.strip()


def _insecure_ssl_context() -> ssl.SSLContext:
    """TLS без проверки сертификата: у панелей самоподписанный серт. Опознание —
    только чтение публичной стартовой страницы, конфиденциальных данных нет."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _fetch_easyweb_title(host: str, port: int, use_tls: bool) -> str | None:
    """GET / на host:port (опц. TLS), поиск маркеров EasyWeb в теле. Возвращает
    `<title>` (обычно «cMT») либо None, если это не EasyWeb / порт недоступен."""
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, port, ssl=_insecure_ssl_context() if use_tls else None
            ),
            timeout=FTP_DISCOVERY_PROBE_TIMEOUT,
        )
        # HTTP/1.0 + identity: без gzip, иначе маркеры не найти в сжатом теле.
        request = (
            f"GET / HTTP/1.0\r\nHost: {host}\r\n"
            "User-Agent: OptiCIP-Dashboard\r\n"
            "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
        )
        writer.write(request.encode("latin-1", "replace"))
        await writer.drain()
        body = b""
        while len(body) < HTTP_EASYWEB_READ_LIMIT:
            chunk = await asyncio.wait_for(
                reader.read(4096), timeout=FTP_DISCOVERY_BANNER_TIMEOUT
            )
            if not chunk:
                break
            body += chunk
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        return None
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass

    text = body.decode("latin-1", "replace")
    if not any(marker in text.lower() for marker in HTTP_EASYWEB_MARKERS):
        return None
    # Первый <title> — это заголовок <head> (SVG-title в спрайте идут позже).
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


async def _probe_http_easyweb(host: str) -> tuple[str, str] | None:
    """Ищет веб-интерфейс EasyWeb на host по HTTP :80 и HTTPS :443 (панели с
    «[TLS]» отдают веб только по https). Порты пробуем ПАРАЛЛЕЛЬНО, чтобы для
    TLS-only панели не терять таймаут на закрытом :80. Возвращает (`<title>`,
    схема) — при обоих ответах предпочитаем :80/http; либо None — не панель."""
    results = await asyncio.gather(
        *(_fetch_easyweb_title(host, port, use_tls) for port, use_tls in HTTP_EASYWEB_PORTS)
    )
    for (port, use_tls), title in zip(HTTP_EASYWEB_PORTS, results):
        if title is not None:
            return title, ("https" if use_tls else "http")
    return None


async def _reverse_dns_name(host: str) -> str:
    """Имя хоста по обратному DNS/mDNS. Панель Weintek обычно отзывается сетевым
    именем `cMT-XXXX` (суффикс MAC). Возвращает первую метку имени без домена,
    либо "" если имя не разрешилось / совпало с самим IP."""
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(socket.gethostbyaddr, host), timeout=2.0
        )
    except (OSError, asyncio.TimeoutError):
        return ""
    hostname = (info[0] if info else "") or ""
    label = hostname.split(".")[0].strip()
    return "" if label == host else label


def _is_weintek_mac(mac: str) -> bool:
    """True, если MAC начинается с OUI Weintek (00:0C:26)."""
    normalized = (mac or "").replace("-", ":").lower()
    return any(normalized.startswith(prefix) for prefix in WEINTEK_MAC_PREFIXES)


def _weintek_name_from_mac(mac: str) -> str:
    """Имя панели по умолчанию = `cMT-` + два последних октета MAC (заглавными).
    Напр. 00:0c:26:11:3c:6f → «cMT-3C6F». Пусто, если MAC не из 6 октетов."""
    octets = (mac or "").replace("-", ":").split(":")
    if len(octets) != 6 or not all(len(o) == 2 for o in octets):
        return ""
    return "cMT-" + (octets[4] + octets[5]).upper()


def _read_arp_table() -> dict[str, str]:
    """IP→MAC из ARP-таблицы ОС (её наполняют TCP-пробы скана). MAC — в нижнем
    регистре через двоеточие. Пусто, если таблицу не удалось прочитать."""
    if os.name == "nt":
        commands = (["arp", "-a"],)
    else:
        # ip neigh — основной; arp -a/-n — фолбэк (net-tools).
        commands = (["ip", "neigh", "show"], ["arp", "-a"], ["arp", "-n"])
    # На Windows подавляем мелькание консольного окна в GUI-сборке (pywebview).
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    output = ""
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.stdout:
            output = proc.stdout
            break
    if not output:
        return {}
    ip_re = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
    mac_re = re.compile(r"\b([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})\b")
    table: dict[str, str] = {}
    for line in output.splitlines():
        ip_match = ip_re.search(line)
        mac_match = mac_re.search(line)
        if ip_match and mac_match:
            table[ip_match.group(1)] = mac_match.group(1).replace("-", ":").lower()
    return table


async def _probe_ftp_host(host: str, semaphore: asyncio.Semaphore) -> dict[str, Any] | None:
    """Пробует host:21 (FTP нужен для выгрузки datalog) и читает приветствие.
    Опознаёт панель по её веб-интерфейсу EasyWeb на host:80 — это работает без
    FTP-пароля и при обязательном TLS. Баннер FTP — лишь мягкий запасной признак.
    Имя панели берём из обратного DNS (`cMT-XXXX`), иначе из `<title>` EasyWeb.
    Возвращает описание хоста либо None, если порт 21 закрыт/недоступен."""
    async with semaphore:
        writer: asyncio.StreamWriter | None = None
        banner = ""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, FTP_DEFAULT_PORT),
                timeout=FTP_DISCOVERY_PROBE_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError):
            return None
        try:
            _banner_code, banner = await _ftp_read_reply(reader)
        # LimitOverrunError/ValueError — слишком длинная строка приветствия рвёт
        # readline; иначе исключение всплывает через gather в discover_ftp_panels
        # и роут отдаёт 500 из-за одного «шумного» хоста в подсети.
        except (OSError, asyncio.TimeoutError, asyncio.LimitOverrunError, ValueError):
            banner = ""
        finally:
            try:
                writer.write(b"QUIT\r\n")
                await writer.drain()
            except OSError:
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

        # Опознание по веб-морде — основной признак (пароль не нужен).
        easyweb_result = await _probe_http_easyweb(host)
        easyweb = easyweb_result is not None
        name = ""
        web_scheme = ""  # http/https EasyWeb — для веб-просмотра /app/dashboard
        if easyweb:
            easyweb_title, web_scheme = easyweb_result
            # Имя: обратный DNS (cMT-XXXX) приоритетнее дженерик-title «cMT».
            name = (await _reverse_dns_name(host)) or (easyweb_title or "")

    lowered = banner.lower()
    banner_hint = any(hint in lowered for hint in FTP_WEINTEK_HINTS)
    return {
        "host": host,
        "port": FTP_DEFAULT_PORT,
        "banner": banner,
        "name": name,
        "web_scheme": web_scheme,
        "confirmed_weintek": easyweb,  # подтверждено веб-интерфейсом EasyWeb
        "likely_weintek": easyweb or banner_hint,
    }


async def discover_ftp_panels(subnet: str = "") -> dict[str, Any]:
    """Сканирует приватные локальные подсети (по одной на каждый интерфейс) по
    порту 21 и возвращает найденные FTP-хосты (Weintek-подобные — первыми).
    `subnet` (напр. «192.168.18.0/24») сканирует ТОЛЬКО указанную сеть — для
    панели за маршрутизатором, где своего адреса в её подсети нет; ValueError,
    если подсеть указана неверно. Действие ручное и локальное."""
    own_ip, networks = await asyncio.to_thread(_local_ipv4_networks)
    if subnet:
        networks = [parse_scan_subnet(subnet)]
    hosts: list[str] = []
    host_set: set[str] = set()
    for network in networks:
        if network.num_addresses - 2 > FTP_DISCOVERY_MAX_HOSTS:
            # Слишком широкая подсеть — не рассылаем тысячи проб.
            continue
        for ip in network.hosts():
            host = str(ip)
            # Подсети могут пересекаться (напр. ручная совпала со своей) —
            # один и тот же адрес пробуем один раз.
            if host != own_ip and host not in host_set:
                host_set.add(host)
                hosts.append(host)
    if not hosts:
        return {"scanned": 0, "network": "", "networks": [], "panels": []}

    semaphore = asyncio.Semaphore(FTP_DISCOVERY_CONCURRENCY)
    probed = await asyncio.gather(*(_probe_ftp_host(host, semaphore) for host in hosts))
    responded = [item for item in probed if item is not None]
    ftp_hosts = len(responded)  # откликнулось на порт 21

    # MAC из ARP (пробы скана уже наполнили таблицу) — самый надёжный признак
    # Weintek (OUI 00:0C:26): без пароля, без web, при любом TLS.
    arp = await asyncio.to_thread(_read_arp_table)
    seen = set()
    for item in responded:
        mac = arp.get(item["host"], "")
        item["mac"] = mac
        item["mac_weintek"] = _is_weintek_mac(mac)
        if item["mac_weintek"]:
            item["confirmed_weintek"] = True
            item["likely_weintek"] = True
            # Имя = cMT-<последние 2 октета MAC> (надёжно). Кастомное имя из
            # EasyWeb/DNS сохраняем, дженерик-«cMT»/пустое заменяем на MAC-имя.
            current = item.get("name") or ""
            if not current or current.lower() == "cmt":
                item["name"] = _weintek_name_from_mac(mac) or current
        seen.add(item["host"])

    # Панели, опознанные по MAC, но не ответившие на :21 (FTP выкл/медленный):
    # добавляем, чтобы не терять — подключение потом само попробует FTP.
    mac_only = [
        ip
        for ip, mac in arp.items()
        if ip in host_set and ip not in seen and ip != own_ip and _is_weintek_mac(mac)
    ]
    for ip in mac_only:
        mac = arp.get(ip, "")
        responded.append(
            {
                "host": ip,
                "port": FTP_DEFAULT_PORT,
                "banner": "",
                # Имя из MAC (cMT-XXXX) для 6-октетного Weintek-MAC всегда есть,
                # reverse-DNS тут не нужен (и не блокирует по хосту в цикле).
                "name": _weintek_name_from_mac(mac),
                "web_scheme": "",  # web не зондировали (на :21 не ответил)
                "mac": mac,
                "mac_weintek": True,
                "confirmed_weintek": True,
                "likely_weintek": True,
            }
        )

    # В список отдаём ТОЛЬКО опознанные панели Weintek (MAC / EasyWeb / баннер).
    # Прочие FTP-хосты скрываем, но их число возвращаем для пояснения в UI.
    panels = [item for item in responded if item.get("likely_weintek")]
    panels.sort(
        key=lambda item: (
            not item.get("confirmed_weintek"),  # подтверждённые — первыми
            tuple(int(part) for part in item["host"].split(".")),
        )
    )
    return {
        "scanned": len(hosts),
        "ftp_hosts": ftp_hosts,
        # "network" — первая (основная) подсеть, оставлена для совместимости;
        # "networks" — все просканированные.
        "network": str(networks[0]) if networks else "",
        "networks": [str(network) for network in networks],
        "panels": panels,
    }
