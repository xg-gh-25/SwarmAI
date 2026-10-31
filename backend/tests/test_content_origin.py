"""The read-only content origin (core/content_origin.py; ops-registry design §5.5, §8 #5).

Questions answered:
- does the listener serve only GET/HEAD of a file under a minted directory (no API,
  no traversal, no symlink escape, no dot segment, no unknown token), with a CSP that
  keeps the page from fetching other sites?
- does it refuse to mint a URL for a directory too wide to hand to a page?
- does it fall back to a free port beside another program's wildcard listener, and
  hand out no URL when its server never started?
- do a report's relative links resolve to files next to it?
- does it leave the daemon's signal handlers alone and stop cleanly?
- does the raw route put the sandbox CSP on active documents only?
- does /workspace/content-url return the origin URL, or null when it is off?
"""
from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import httpx
import pytest

from core import content_origin


@pytest.fixture
def home_dir(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    (home / "work" / "reports" / "img").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


async def _raw_get(base: str, path: str) -> int:
    """The status of GET *path* sent byte for byte (an HTTP client would clean up
    ``..`` before sending it)."""
    host, port = base.rsplit("/", 1)[-1].split(":")
    r, w = await asyncio.open_connection(host, int(port))
    w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    await w.drain()
    line = await r.readline()
    w.close()
    return int(line.split()[1])


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_serves_the_file_and_its_directory_only(home_dir, tmp_path):
    rep = home_dir / "work" / "reports"
    (rep / "r.html").write_text("<script>fetch('./data.json')</script>")
    (rep / "data.json").write_text('{"a":1}')
    (rep / "img" / "a.png").write_bytes(b"\x89PNG")
    (home_dir / "work" / "secret.txt").write_text("s3cret")
    (rep / "link").symlink_to(home_dir / "work" / "secret.txt")
    (rep / ".env").write_text("KEY=1")
    (rep / ".git").mkdir()
    (rep / ".git" / "config").write_text("[core]")
    (rep / "envlink").symlink_to(rep / ".env")

    async def go():
        base = await content_origin.start(54001)
        try:
            assert base and base.startswith("http://127.0.0.1:")
            url = content_origin.url_for(rep / "r.html")
            assert url.startswith(base + "/f/") and url.endswith("/r.html")
            tok_url = url.rsplit("/", 1)[0]
            async with httpx.AsyncClient() as c:
                r = await c.get(url)
                assert r.status_code == 200 and "text/html" in r.headers["content-type"]
                assert "access-control-allow-origin" not in r.headers
                assert r.headers["x-content-type-options"] == "nosniff"
                assert r.headers["content-security-policy"] == "connect-src 'self'; form-action 'none'"
                assert (await c.get(tok_url + "/data.json")).json() == {"a": 1}
                assert (await c.get(tok_url + "/img/a.png")).status_code == 200
                assert (await c.head(url)).status_code == 200
                # nothing outside the directory, however it is spelled
                for bad in (tok_url + "/%2e%2e/secret.txt", tok_url + "/link",
                            tok_url + "/.env", tok_url + "/.git/config", tok_url + "/envlink",
                            base + "/f/nope/r.html",
                            base + "/api/health", base + "/", base + "/f/"):
                    assert (await c.get(bad)).status_code == 404, bad
                assert (await c.post(url)).status_code == 405
                assert (await c.request("OPTIONS", url)).status_code == 405
            tok_path = tok_url[len(base):]
            assert await _raw_get(base, tok_path + "/data.json") == 200
            assert await _raw_get(base, tok_path + "/../secret.txt") == 404
            assert await _raw_get(base, tok_path + "/img/../../secret.txt") == 404
        finally:
            await content_origin.stop()
        assert content_origin.base() is None
        assert content_origin.url_for(rep / "r.html") is None

    _run(go())


def test_one_token_per_directory_and_none_for_wide_ones(home_dir):
    rep = home_dir / "work" / "reports"
    ws = home_dir / "work" / "ws"
    ws.mkdir()
    for f in (rep / "a.html", rep / "b.html", home_dir / "top.html",
              home_dir / "work" / "w.html", ws / "root.html", rep / ".hidden.html"):
        f.write_text("x")
    (rep / "alias.html").symlink_to(rep / "a.html")

    async def go():
        await content_origin.start(54011)
        try:
            ua, ub = content_origin.url_for(rep / "a.html"), content_origin.url_for(rep / "b.html")
            assert ua.rsplit("/", 1)[0] == ub.rsplit("/", 1)[0]
            # a symlink opens its target's name in its target's directory
            assert content_origin.url_for(rep / "alias.html") == ua
            assert content_origin.url_for(home_dir / "top.html") is None          # home
            assert content_origin.url_for(home_dir / "work" / "w.html") is None   # just under home
            assert content_origin.url_for(ws / "root.html", refuse=(ws,)) is None  # named by caller
            assert content_origin.url_for(ws / "root.html") is not None
            assert content_origin.url_for(rep / ".hidden.html") is None
        finally:
            await content_origin.stop()

    _run(go())


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_falls_back_to_a_free_port_and_keeps_signals(home_dir, host):
    import socket
    held = socket.socket()
    held.bind((host, 0))
    held.listen(1)
    taken = held.getsockname()[1]
    before = signal.getsignal(signal.SIGTERM)

    async def go():
        base = await content_origin.start(taken - 1)
        try:
            assert base and not base.endswith(f":{taken}")
            await asyncio.sleep(0.2)          # the server's startup has run
            assert signal.getsignal(signal.SIGTERM) is before
        finally:
            await content_origin.stop()

    try:
        _run(go())
    finally:
        held.close()


def test_no_url_when_the_server_never_starts(home_dir, monkeypatch):
    import uvicorn

    async def broken(self, sockets=None):
        raise OSError("startup failed")

    monkeypatch.setattr(uvicorn.Server, "_serve", broken)
    (home_dir / "work" / "reports" / "r.html").write_text("x")

    async def go():
        assert await content_origin.start(54021) is None
        assert content_origin.base() is None
        assert content_origin.url_for(home_dir / "work" / "reports" / "r.html") is None
        await content_origin.stop()

    _run(go())


def test_raw_route_sandboxes_active_documents(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routers import workspace_api

    (tmp_path / "r.html").write_text("<p>x</p>")
    (tmp_path / "i.svg").write_text("<svg/>")
    (tmp_path / "f.rdf").write_text("<rdf/>")
    (tmp_path / "f.xsl").write_text("<x/>")
    (tmp_path / "u.unknownext").write_text("?")
    (tmp_path / "p.png").write_bytes(b"\x89PNG")
    (tmp_path / "d.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "v.mp4").write_bytes(b"\0")

    async def ws():
        return str(tmp_path)

    async def landed(p, _s):
        return p

    monkeypatch.setattr(workspace_api, "_get_workspace_path", ws)
    monkeypatch.setattr(workspace_api, "_landed_session_path", landed)
    app = FastAPI()
    app.include_router(workspace_api.router, prefix="/api")
    c = TestClient(app)
    for name in ("r.html", "i.svg", "f.rdf", "f.xsl", "u.unknownext"):
        r = c.get("/api/workspace/file/raw", params={"path": name})
        assert r.headers.get("content-security-policy") == "sandbox allow-scripts allow-popups", name
        assert r.headers.get("x-content-type-options") == "nosniff", name
    for name in ("p.png", "d.pdf", "v.mp4"):
        r = c.get("/api/workspace/file/raw", params={"path": name})
        assert r.status_code == 200 and "content-security-policy" not in r.headers, name

    # content-url: null while the origin is off, the origin's URL while it runs, and
    # null for a file in the workspace root (its directory is the whole workspace)
    (tmp_path / "Reports").mkdir()
    (tmp_path / "Reports" / "s.html").write_text("<p>s</p>")
    assert c.get("/api/workspace/content-url", params={"path": "Reports/s.html"}).json() == {"url": None}
    monkeypatch.setitem(content_origin._STATE, "base", "http://127.0.0.1:1")
    try:
        url = c.get("/api/workspace/content-url", params={"path": "Reports/s.html"}).json()["url"]
        assert url.startswith("http://127.0.0.1:1/f/") and url.endswith("/s.html")
        assert c.get("/api/workspace/content-url", params={"path": "r.html"}).json() == {"url": None}
        assert c.get("/api/workspace/content-url", params={"path": "missing.html"}).status_code == 404
    finally:
        content_origin._ROOTS.clear()
        content_origin._TOKEN_OF.clear()
