"""The egress proxy of a non-owner channel session (core/egress_proxy.py).

Questions answered:
1. does CONNECT to a host that resolves to any non-public address (loopback, private,
   link-local, IPv4 inside IPv6), or does not resolve, get 403 with no connection made?
2. does a public host get connected to the address the proxy checked (never a fresh
   lookup), with bytes tunnelled both ways?
3. are non-CONNECT requests, ports other than 443, malformed targets and connections
   beyond the cap refused?
5. tunnel behaviour: a one-way stream longer than the idle limit is not cut, a tunnel
   with no traffic either way is closed, a half-close is passed on, and the next
   checked address is tried when the first does not answer.
4. does build_options send a non-owner session's CLI through it, and only that session,
   and take WebFetch away when it cannot start, or when the CLI would sign in itself
   over the default chain (ADA / Midway can need the local network)?
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from core import egress_proxy as ep


def _answers(addrs):
    def fake(host, port, *a, **k):
        if addrs is OSError:
            raise OSError("no such host")
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, 1, 6, "", (a, port))
                for a in addrs]
    return fake


@pytest.mark.parametrize("addrs,ok", [
    (["93.184.215.14"], True),
    (["127.0.0.1"], False),
    (["93.184.215.14", "10.1.2.3"], False),     # one private answer is enough
    (["169.254.169.254"], False),
    (["::ffff:127.0.0.1"], False),
    (["fd00::1"], False),
    (OSError, False),
])
def test_public_addresses(monkeypatch, addrs, ok):
    monkeypatch.setattr(socket, "getaddrinfo", _answers(addrs))
    assert bool(ep.public_addresses("x.example", 443)) is ok


@pytest.mark.parametrize("host", ["localhost", "a.localhost", "printer.local", "db.internal", ""])
def test_local_names_never_resolve(monkeypatch, host):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("no DNS for local names"))
    assert ep.public_addresses(host, 443) == []


async def _talk(port, request: bytes) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(request)
    await w.drain()
    data = await asyncio.wait_for(r.read(4096), 5)
    w.close()
    return data


@pytest.fixture
def proxy(monkeypatch):
    """A fresh proxy, started inside the test's own event loop (``await proxy()``)."""
    monkeypatch.setattr(ep, "_server", None)
    monkeypatch.setattr(ep, "_port", None)

    async def start():
        monkeypatch.setattr(ep, "_start_lock", asyncio.Lock())
        return await ep.ensure_started()
    yield start
    if ep._server is not None:
        ep._server.close()


@pytest.mark.asyncio
async def test_a_rebinding_name_is_refused_and_nothing_connects(proxy, monkeypatch):
    proxy = await proxy()
    monkeypatch.setattr(socket, "getaddrinfo", _answers(["127.0.0.1"]))
    opened = []
    real_open = asyncio.open_connection

    async def spy(host, port, **kw):
        if host != "127.0.0.1" or port != proxy:
            opened.append((host, port))
        return await real_open(host, port, **kw)
    monkeypatch.setattr(asyncio, "open_connection", spy)
    out = await _talk(proxy, b"CONNECT rebind.example:443 HTTP/1.1\r\nHost: rebind.example:443\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 403")
    assert opened == []


@pytest.mark.asyncio
async def test_a_public_host_is_connected_by_the_checked_address(proxy, monkeypatch):
    proxy = await proxy()
    # an upstream echo server stands in for the public host; the proxy must dial the
    # address its own lookup returned, so the lookup "returns" a public address and the
    # dial is redirected to the echo server only if it targets exactly that address
    async def echo(r, w):
        w.write(await r.read(5))
        await w.drain()
        w.close()
    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    up_port = upstream.sockets[0].getsockname()[1]
    monkeypatch.setattr(socket, "getaddrinfo", _answers(["93.184.215.14"]))
    dialled = []
    real_open = asyncio.open_connection

    async def redirect(host, port, **kw):
        if host == "93.184.215.14":
            dialled.append((host, port))
            return await real_open("127.0.0.1", up_port)
        return await real_open(host, port, **kw)
    monkeypatch.setattr(asyncio, "open_connection", redirect)
    r, w = await real_open("127.0.0.1", proxy)
    w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\nhello")
    await w.drain()
    data = b""
    while b"hello" not in data:
        chunk = await asyncio.wait_for(r.read(4096), 5)
        if not chunk:
            break
        data += chunk
    w.close()
    upstream.close()
    assert data.startswith(b"HTTP/1.1 200") and data.endswith(b"hello")
    assert dialled == [("93.184.215.14", 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_line,status", [
    (b"GET http://example.com/ HTTP/1.1", b"403"),
    (b"CONNECT example.com HTTP/1.1", b"400"),
    (b"CONNECT example.com:25 HTTP/1.1", b"403"),
    (b"CONNECT example.com:99999 HTTP/1.1", b"400"),
    (b"CONNECT :443 HTTP/1.1", b"400"),
])
async def test_other_requests_are_refused(proxy, request_line, status):
    proxy = await proxy()
    out = await _talk(proxy, request_line + b"\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 " + status)


def test_session_env_routes_everything_but_loopback():
    env = ep.session_env(4321)
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://127.0.0.1:4321"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:4321"
    assert set(env["NO_PROXY"].split(",")) == {"127.0.0.1", "localhost", "::1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tier,proxied", [("public", True), ("trusted", True), ("owner", False)])
async def test_build_options_sends_only_non_owner_sessions_through_it(tier, proxied, tmp_path,
                                                                     monkeypatch):
    from tests.test_knowledge_restricted_channel import _options

    from core import aws_account

    async def started():
        return 4321
    monkeypatch.setattr(ep, "ensure_started", started)
    monkeypatch.setattr(aws_account, "active", lambda: True)  # credentials via loopback
    ctx = {"channel_id": "c1", "channel_type": "slack",
           "sender_identity": {"permission_tier": tier, "external_id": "u1"}}
    opts = await _options(ctx, tmp_path, monkeypatch)
    env = opts.env or {}
    assert (env.get("HTTPS_PROXY") == "http://127.0.0.1:4321") is proxied
    assert "WebFetch" not in opts.disallowed_tools


@pytest.mark.asyncio
async def test_no_proxy_means_no_webfetch(tmp_path, monkeypatch):
    from tests.test_knowledge_restricted_channel import _options

    from core import aws_account

    async def broken():
        raise OSError("no port")
    monkeypatch.setattr(ep, "ensure_started", broken)
    monkeypatch.setattr(aws_account, "active", lambda: True)
    ctx = {"channel_id": "c1", "channel_type": "slack",
           "sender_identity": {"permission_tier": "public", "external_id": "u1"}}
    opts = await _options(ctx, tmp_path, monkeypatch)
    assert "WebFetch" in opts.disallowed_tools
    assert "HTTPS_PROXY" not in (opts.env or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("active,via,proxied", [
    (True, "aws", True),           # credentials from the daemon on loopback
    (False, "anthropic", True),    # an API key in the environment
    (False, "bedrock_key", True),
    (False, "aws", False),         # the CLI signs in itself (ADA, Midway): no proxy, no WebFetch
])
async def test_proxy_only_when_the_cli_signs_in_without_the_network(active, via, proxied,
                                                                   tmp_path, monkeypatch):
    from core import aws_account
    from tests.test_knowledge_restricted_channel import _options

    async def started():
        return 4321
    monkeypatch.setattr(ep, "ensure_started", started)
    monkeypatch.setattr(aws_account, "active", lambda: active)
    monkeypatch.setattr(aws_account, "get_chat_via", lambda config=None: via)
    ctx = {"channel_id": "c1", "channel_type": "slack",
           "sender_identity": {"permission_tier": "public", "external_id": "u1"}}
    opts = await _options(ctx, tmp_path, monkeypatch)
    assert ("HTTPS_PROXY" in (opts.env or {})) is proxied
    assert ("WebFetch" in opts.disallowed_tools) is (not proxied)


async def _tunnel_to(proxy_port, up_port, monkeypatch, addrs=("93.184.215.14",)):
    """A client connected through the proxy to a local stand-in for a public host."""
    monkeypatch.setattr(socket, "getaddrinfo", _answers(list(addrs)))
    real_open = asyncio.open_connection
    dialled = []

    async def redirect(host, port, **kw):
        if host != "127.0.0.1":
            dialled.append(host)
            if host == "93.184.215.99":
                raise OSError("no route")
            return await real_open("127.0.0.1", up_port)
        return await real_open(host, port, **kw)
    monkeypatch.setattr(asyncio, "open_connection", redirect)
    r, w = await real_open("127.0.0.1", proxy_port)
    w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
    await w.drain()
    assert (await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)).startswith(b"HTTP/1.1 200")
    return r, w, dialled


async def _read_all(r, limit=10):
    data = b""
    while True:
        chunk = await asyncio.wait_for(r.read(4096), limit)
        if not chunk:
            return data
        data += chunk


@pytest.mark.asyncio
async def test_a_long_one_way_stream_is_not_cut(proxy, monkeypatch):
    monkeypatch.setattr(ep, "_IDLE_TIMEOUT_S", 0.6)
    monkeypatch.setattr(ep, "_POLL_S", 0.1)

    async def stream(r, w):  # sends for 2s, longer than the idle limit, client stays silent
        for _ in range(10):
            w.write(b"0123456789")
            await w.drain()
            await asyncio.sleep(0.2)
        w.close()
    up = await asyncio.start_server(stream, "127.0.0.1", 0)
    r, w, _ = await _tunnel_to(await proxy(), up.sockets[0].getsockname()[1], monkeypatch)
    assert len(await _read_all(r)) == 100
    w.close()
    up.close()


@pytest.mark.asyncio
async def test_a_tunnel_with_no_traffic_is_closed(proxy, monkeypatch):
    monkeypatch.setattr(ep, "_IDLE_TIMEOUT_S", 0.4)
    monkeypatch.setattr(ep, "_POLL_S", 0.1)

    async def silent(r, w):
        await asyncio.sleep(5)
    up = await asyncio.start_server(silent, "127.0.0.1", 0)
    r, w, _ = await _tunnel_to(await proxy(), up.sockets[0].getsockname()[1], monkeypatch)
    assert await _read_all(r, limit=3) == b""  # closed by the proxy well before 3s
    w.close()
    up.close()


@pytest.mark.asyncio
async def test_a_half_close_is_passed_on(proxy, monkeypatch):
    async def answer_after_eof(r, w):
        got = await r.read()  # until the client's EOF
        w.write(b"reply:" + got)
        await w.drain()
        w.close()
    up = await asyncio.start_server(answer_after_eof, "127.0.0.1", 0)
    r, w, _ = await _tunnel_to(await proxy(), up.sockets[0].getsockname()[1], monkeypatch)
    w.write(b"hello")
    w.write_eof()
    assert await _read_all(r) == b"reply:hello"
    up.close()


@pytest.mark.asyncio
async def test_the_next_checked_address_is_tried(proxy, monkeypatch):
    async def echo(r, w):
        w.write(b"ok")
        await w.drain()
        w.close()
    up = await asyncio.start_server(echo, "127.0.0.1", 0)
    r, w, dialled = await _tunnel_to(await proxy(), up.sockets[0].getsockname()[1], monkeypatch,
                                     addrs=("93.184.215.99", "93.184.215.14"))
    assert await _read_all(r) == b"ok"
    assert dialled == ["93.184.215.99", "93.184.215.14"]
    up.close()


@pytest.mark.asyncio
async def test_beyond_the_cap_new_connections_get_503(proxy, monkeypatch):
    port = await proxy()
    monkeypatch.setattr(ep, "_slots", asyncio.Semaphore(0))
    out = await _talk(port, b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 503")


@pytest.mark.asyncio
async def test_no_export_means_no_proxy(tmp_path, monkeypatch):
    """active() but the export is withheld (cli_settings returned {}): the CLI signs in
    itself, so no proxy and no WebFetch."""
    from core import aws_account
    from core import prompt_builder as pb
    from tests.test_knowledge_restricted_channel import _options

    async def started():
        return 4321
    monkeypatch.setattr(ep, "ensure_started", started)
    monkeypatch.setattr(aws_account, "active", lambda: True)
    monkeypatch.setattr(aws_account, "get_chat_via", lambda config=None: "aws")
    monkeypatch.setattr(pb, "_aws_account_cli_settings", lambda config: {})
    ctx = {"channel_id": "c1", "channel_type": "slack",
           "sender_identity": {"permission_tier": "public", "external_id": "u1"}}
    opts = await _options(ctx, tmp_path, monkeypatch)
    assert "HTTPS_PROXY" not in (opts.env or {}) and "WebFetch" in opts.disallowed_tools
