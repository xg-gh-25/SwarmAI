"""LoopbackHostMiddleware: a loopback connection whose Host is not this machine is refused.

Question each test answers: can a DNS-rebinding page (same origin, foreign Host) reach the
daemon's routes, and does every legitimate local caller still get through?
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.loopback_host import LoopbackHostMiddleware, host_allowed

PORT = 18321


async def _plain(request):  # noqa: ANN001
    return JSONResponse({"ok": True})


async def _stream(request):  # noqa: ANN001
    async def gen():
        for i in range(3):
            yield f"data: {i}\n\n".encode()
    return StreamingResponse(gen(), media_type="text/event-stream")


def _client(base: str = f"http://127.0.0.1:{PORT}") -> TestClient:
    app = Starlette(routes=[
        Route("/api/hive/instances/x/credentials", _plain),
        Route("/api/swarm-out/asks/h/s/r/answer", _plain, methods=["POST"]),
        Route("/api/chat/stream", _stream),
    ])
    app.add_middleware(LoopbackHostMiddleware)
    return TestClient(app, base_url=base)


@pytest.fixture(autouse=True)
def _desktop_mode(monkeypatch):
    monkeypatch.delenv("SWARMAI_MODE", raising=False)


@pytest.mark.parametrize("path,method", [
    ("/api/hive/instances/x/credentials", "get"),
    ("/api/swarm-out/asks/h/s/r/answer", "post"),
])
def test_rebinding_host_is_refused(path, method):
    r = getattr(_client(), method)(path, headers={"Host": f"evil.example:{PORT}"})
    assert r.status_code == 421


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"LOCALHOST:{PORT}"])
def test_local_names_pass(host):
    r = _client().get("/api/hive/instances/x/credentials", headers={"Host": host})
    assert r.status_code == 200


@pytest.mark.parametrize("host", ["localhost:1420", "localhost", "127.0.0.1", f"127.0.0.1.evil.example:{PORT}"])
def test_wrong_port_missing_port_or_lookalike_is_refused(host):
    r = _client().get("/api/hive/instances/x/credentials", headers={"Host": host})
    assert r.status_code == 421


def test_hive_mode_is_not_checked(monkeypatch):
    # Caddy forwards to 127.0.0.1 with the CloudFront name as Host.
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    r = _client().get("/api/hive/instances/x/credentials", headers={"Host": "d123.cloudfront.net"})
    assert r.status_code == 200


def test_a_lan_bound_daemon_accepts_its_own_address_and_refuses_a_foreign_name():
    # --host 0.0.0.0: a LAN client names the address it connected to; a rebinding page
    # that points its own name at that address still sends its own name.
    c = _client(f"http://192.168.1.20:{PORT}")
    assert c.get("/api/hive/instances/x/credentials").status_code == 200
    r = c.get("/api/hive/instances/x/credentials", headers={"Host": f"evil.example:{PORT}"})
    assert r.status_code == 421


def _raw(scope_extra: dict, headers: list) -> list:
    import asyncio

    async def app(scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.accept"})
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "GET", "path": "/x", "server": ("127.0.0.1", PORT),
             "headers": headers, **scope_extra}
    asyncio.run(LoopbackHostMiddleware(app)(scope, receive, send))
    return sent


def test_no_host_header_is_refused():
    assert _raw({}, [])[0]["status"] == 421


def test_websocket_with_foreign_host_is_closed():
    sent = _raw({"type": "websocket"}, [(b"host", f"evil.example:{PORT}".encode())])
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_websocket_with_local_host_is_accepted():
    sent = _raw({"type": "websocket"}, [(b"host", f"localhost:{PORT}".encode())])
    assert sent == [{"type": "websocket.accept"}]


@pytest.mark.parametrize("server", [("::1", PORT), ("::ffff:127.0.0.1", PORT)])
def test_ipv6_and_mapped_loopback_servers_are_checked(server):
    assert _raw({"server": server}, [(b"host", f"evil.example:{PORT}".encode())])[0]["status"] == 421
    assert _raw({"server": server}, [(b"host", f"localhost:{PORT}".encode())])[0]["status"] == 200


def test_mapped_address_matches_its_ipv4_host():
    ok = _raw({"server": ("::ffff:10.0.0.5", PORT)}, [(b"host", f"10.0.0.5:{PORT}".encode())])
    assert ok[0]["status"] == 200


def test_sse_streams_through_unbuffered():
    """Each chunk reaches the server as its own body message (no buffering layer)."""
    import asyncio

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for i in range(3):
            await send({"type": "http.response.body", "body": f"{i}".encode(), "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "GET", "path": "/api/chat/stream",
             "server": ("127.0.0.1", PORT), "headers": [(b"host", f"localhost:{PORT}".encode())]}
    asyncio.run(LoopbackHostMiddleware(app)(scope, receive, send))
    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert [m["body"] for m in bodies] == [b"0", b"1", b"2", b""]


def test_host_allowed_unit():
    srv = ("127.0.0.1", PORT)
    assert host_allowed(f"[::1]:{PORT}", srv)
    assert host_allowed("testserver", ("testserver", 80))
    assert not host_allowed(None, srv)
    assert not host_allowed(f"evil:{PORT}", srv)
    assert not host_allowed("localhost", srv)  # port 80 is not this daemon


def test_real_app_registers_it_outermost():
    from main import app
    assert app.user_middleware[0].cls is LoopbackHostMiddleware
