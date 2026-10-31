"""The egress proxy for a non-owner channel session: its CLI reaches only public addresses
(design 2026-10-31-swarmai-aws-account §5.3).

The session's PreToolUse gate checks a WebFetch URL before the call, but the CLI then
resolves the name again on its own; a name whose answer changes in between (DNS
rebinding) would reach this machine or its network. So the session's CLI is given
``HTTPS_PROXY`` = this proxy, which resolves the host itself, refuses unless every
address is public, and connects to the address it checked — the CLI never chooses the
address. Plain HTTP, anything but ``CONNECT host:443`` and more than ``_MAX_TUNNELS``
open tunnels are refused (WebFetch fetches over https; the model APIs are https).

Loopback names stay out of the proxy (``NO_PROXY``) so the session's own credential
export reaches the daemon; the gate denies a WebFetch to them before any connection.

One proxy per daemon, on 127.0.0.1 with a free port, started on first use. Every
connection that is refused or fails is logged once.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HEADER_LIMIT = 64 * 1024
_CONNECT_TIMEOUT_S = 15
_IDLE_TIMEOUT_S = 300   # neither direction moved for this long: the tunnel is closed
_POLL_S = 10            # how often an idle direction checks the shared clock
_MAX_TUNNELS = 64       # beyond this a new connection gets 503 (a local process, not a session)
_PORTS = frozenset({443})  # WebFetch and the model APIs are https

_server: Optional[asyncio.base_events.Server] = None
_port: Optional[int] = None
_start_lock = asyncio.Lock()
_slots = asyncio.Semaphore(_MAX_TUNNELS)
#: its own threads for DNS: slow lookups never take the daemon's shared executor
_RESOLVER = ThreadPoolExecutor(max_workers=4, thread_name_prefix="egress-dns")


def not_public(ip: Any) -> bool:
    """An address that is not public, including an IPv4 address carried inside IPv6
    (mapped, 6to4, the deprecated ::a.b.c.d form, NAT64 64:ff9b::/96)."""
    if not ip.is_global:
        return True
    if ip.version == 6:
        embedded = [ip.ipv4_mapped, ip.sixtofour]
        packed = int(ip)
        if packed >> 32 == 0 or ip in ipaddress.ip_network("64:ff9b::/96"):
            embedded.append(ipaddress.IPv4Address(packed & 0xFFFFFFFF))
        return any(e is not None and not e.is_global for e in embedded)
    return False


def public_addresses(host: str, port: int) -> list:
    """The addresses ``host`` resolves to, or ``[]`` when any of them is not public or
    it does not resolve (fail closed). Blocking."""
    host = host.strip("[]").rstrip(".").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return []
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    out = []
    for family, _t, _p, _c, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
        except ValueError:
            return []
        if not_public(ip):
            return []
        out.append((family, sockaddr))
    return out


async def _refuse(writer: asyncio.StreamWriter, status: str) -> None:
    try:
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


class _Tunnel:
    """Both directions of one tunnel: idle means neither direction moved for
    ``_IDLE_TIMEOUT_S`` (a long one-way stream, such as a model's reply, is not idle);
    an EOF from one side is passed on as a half-close."""

    def __init__(self) -> None:
        self.last = asyncio.get_running_loop().time()

    async def pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(65536), _POLL_S)
                except asyncio.TimeoutError:
                    if loop.time() - self.last >= _IDLE_TIMEOUT_S:
                        break
                    continue
                if not data:  # EOF: pass it on, keep the other direction open
                    if writer.can_write_eof():
                        writer.write_eof()
                    return
                self.last = loop.time()
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        writer.close()  # idle or broken: end this side for good


def _close(*writers: asyncio.StreamWriter) -> None:
    for w in writers:
        try:
            w.close()
        except Exception:  # noqa: BLE001 — already closed
            pass


async def _open_checked(addrs: list, port: int):
    """Connect to the first of the checked addresses that answers (never a new lookup)."""
    for family, sockaddr in addrs:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(sockaddr[0], port, family=family), _CONNECT_TIMEOUT_S)
        except (asyncio.TimeoutError, OSError):
            continue
    return None


async def _lookup(host: str, port: int) -> list:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_RESOLVER, public_addresses, host, port), _CONNECT_TIMEOUT_S)
    except asyncio.TimeoutError:
        return []


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    if _slots.locked():
        await _refuse(writer, "503 Service Unavailable")
        return
    async with _slots:
        await _serve(reader, writer)


async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _CONNECT_TIMEOUT_S)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            ConnectionError):
        writer.close()
        return
    request = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = request.split()
    if len(parts) != 3 or parts[0] != "CONNECT":
        logger.info("egress_proxy: refused a non-CONNECT request")
        await _refuse(writer, "403 Forbidden")
        return
    host, sep, port_text = parts[1].rpartition(":")
    try:
        port = int(port_text)
    except ValueError:
        port = 0
    if not sep or not host or not 0 < port < 65536:
        await _refuse(writer, "400 Bad Request")
        return
    if port not in _PORTS:
        logger.info("egress_proxy: refused port %d", port)
        await _refuse(writer, "403 Forbidden")
        return
    addrs = await _lookup(host, port)
    if not addrs:
        logger.info("egress_proxy: refused %s (not a public address)", host[:255])
        await _refuse(writer, "403 Forbidden")
        return
    upstream = await _open_checked(addrs, port)
    if upstream is None:
        await _refuse(writer, "502 Bad Gateway")
        return
    up_reader, up_writer = upstream
    try:
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        tunnel = _Tunnel()  # the idle rule is the only hang-guard: a live stream never ends early
        await asyncio.gather(tunnel.pipe(reader, up_writer), tunnel.pipe(up_reader, writer))
    except (ConnectionError, OSError):
        pass
    finally:
        _close(up_writer, writer)


async def ensure_started() -> int:
    """The proxy's port on 127.0.0.1 (started on first call)."""
    global _server, _port
    async with _start_lock:
        if _server is None:
            _server = await asyncio.start_server(_handle, "127.0.0.1", 0, limit=_HEADER_LIMIT)
            _port = _server.sockets[0].getsockname()[1]
            logger.info("egress_proxy: listening on 127.0.0.1:%d", _port)
        return _port  # type: ignore[return-value]


def session_env(port: int) -> dict:
    """The environment that sends a CLI's outbound traffic through the proxy."""
    url = f"http://127.0.0.1:{port}"
    bypass = "127.0.0.1,localhost,::1"
    return {"HTTPS_PROXY": url, "https_proxy": url, "HTTP_PROXY": url, "http_proxy": url,
            "NO_PROXY": bypass, "no_proxy": bypass}
