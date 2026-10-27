"""Reject requests to the desktop daemon whose Host is not this machine (pure ASGI).

WHY THIS EXISTS
---------------
Off a Hive, the daemon listens on 127.0.0.1 and has no login: it trusts that whoever
reaches it is on this machine. CORS does not protect that trust against DNS
rebinding. A web page on ``evil.example`` re-points its own name at 127.0.0.1, and
its requests to ``http://evil.example:<port>/api/...`` are then SAME-origin for the
browser, so CORS never applies. The page can read every route, including the Hive
credential routes in ``routers/hive.py``, and call the routes that act for the person.

The browser cannot hide the name it resolved: such a request carries
``Host: evil.example:<port>``. Every legitimate caller on this machine (the Tauri
webview at ``http://localhost:<port>``, the Rust shell and the CLI scripts at
``http://127.0.0.1:<port>``) sends a loopback name. So a request that arrived on a
loopback connection with any other Host is refused with 421 Misdirected Request.

DESIGN NOTES
------------
* **Pure ASGI, not ``BaseHTTPMiddleware``**, for the same reason as
  ``middleware/hive_auth.py``: the base class buffers ``StreamingResponse`` and would
  break the SSE routes.
* **Every connection off a Hive is checked.** Allowed: a loopback name, or the
  address the connection arrived on (``scope["server"]``, from the socket, so a caller
  cannot change it). A daemon started with ``--host 0.0.0.0`` is then still reachable
  by its LAN address, while a rebinding page that points its own name at that address
  still sends its own name and is refused.
* **Off on a Hive.** There Caddy forwards to 127.0.0.1 with the CloudFront name as
  Host, and nothing on the instance knows that name. Caddy and ``HiveAuthMiddleware``
  already require the Hive credential on every request.
* **The port must match too.** ``localhost:<other port>`` is another local service,
  not this daemon's client. A Host with no port means port 80.
"""

from __future__ import annotations

import ipaddress
import logging
import os

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _norm_ip(host: str) -> str:
    """An address as Host would name it: an IPv4-mapped IPv6 address (a dual-stack
    socket's view of an IPv4 client) as its IPv4 form, IPv6 in brackets."""
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host.lower()
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return f"[{ip}]" if ip.version == 6 else str(ip)


def host_allowed(host_header: str | None, server: tuple | None) -> bool:
    """True when ``Host`` names this machine (a loopback name, or the address the
    connection arrived on) on the port the request arrived on."""
    if not host_header or not server:
        return False
    server_host, server_port = _norm_ip(str(server[0])), int(server[1])
    value = host_header.strip().lower()
    name, sep, port = value.rpartition(":")
    if not sep or not port.isdigit():
        name, port = value, "80"  # no port (or "[::1]"): the default one
    if name not in _LOOPBACK_NAMES and name != server_host:
        return False
    return port.isdigit() and int(port) == server_port


class LoopbackHostMiddleware:
    """Refuse a request whose Host is not this machine (421), off a Hive."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or os.environ.get("SWARMAI_MODE") == "hive":
            await self.app(scope, receive, send)
            return
        server = scope.get("server")
        if not server or server[1] is None:  # a unix socket: nothing to rebind
            await self.app(scope, receive, send)
            return
        host = None
        for key, value in scope.get("headers") or []:
            if key == b"host":
                host = value.decode("latin-1")
                break
        if host_allowed(host, tuple(server)):
            await self.app(scope, receive, send)
            return
        logger.warning("loopback_host.refused host=%r path=%s", (host or "")[:100],
                       scope.get("path", ""))
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 421,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                    "body": b'{"detail":"This address is not this machine."}'})
