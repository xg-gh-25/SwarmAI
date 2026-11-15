"""Tests for HiveAuthMiddleware — the hive app-layer defense-in-depth auth layer.

Methodology: mount HiveAuthMiddleware on a MINIMAL Starlette app with test routes
(a plain JSON route + a real StreamingResponse route), so the middleware is exercised
in isolation with full control over SWARMAI_MODE and the credential settings. This is
faster and more precise than loading the whole FastAPI app, and it lets us prove the
SSE-not-buffered property against a genuine StreamingResponse.

Every test maps to a PLAN acceptance criterion (AC1..AC9). The suite is mutation-proven:
removing the middleware (or its enforcement) turns the hive-mode 401 tests RED.
"""

from __future__ import annotations

import base64

import bcrypt
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.hive_auth import HiveAuthMiddleware

# A known user + bcrypt hash pair (hash of "s3cret", as `caddy hash-password` would emit).
_TEST_USER = "admin"
_TEST_PASS = "s3cret"
_TEST_HASH = bcrypt.hashpw(_TEST_PASS.encode(), bcrypt.gensalt()).decode()


def _basic_header(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _plain(request):  # noqa: ANN001
    return JSONResponse({"ok": True})


async def _stream(request):  # noqa: ANN001
    async def gen():
        for i in range(5):
            yield f"chunk{i}\n".encode()

    return StreamingResponse(gen(), media_type="text/event-stream")


def _build_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/api/jobs/run", _plain, methods=["POST"]),
            Route("/api/workspace/file", _plain, methods=["GET"]),
            Route("/api/autonomous-jobs", _plain, methods=["GET"]),
            Route("/api/chat/stream", _stream, methods=["GET"]),
            Route("/health", _plain, methods=["GET"]),
        ]
    )
    app.add_middleware(HiveAuthMiddleware)
    return app


@pytest.fixture
def hive_env(monkeypatch):
    """Simulate hive mode with a configured credential."""
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    # settings is a cached singleton; patch its attributes in place.
    from config import settings

    monkeypatch.setattr(settings, "hive_user", _TEST_USER, raising=False)
    monkeypatch.setattr(settings, "hive_pass_hash", _TEST_HASH, raising=False)
    return settings


@pytest.fixture
def desktop_env(monkeypatch):
    """Simulate desktop/daemon mode (no enforcement)."""
    monkeypatch.setenv("SWARMAI_MODE", "daemon")
    return None


# ── AC1 / AC2: hive + no credential → 401 on privileged routes ──────────────────
@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/jobs/run"),
        ("get", "/api/workspace/file"),
        ("get", "/api/autonomous-jobs"),
    ],
)
def test_hive_no_credential_returns_401(hive_env, method, path):
    client = TestClient(_build_app())
    resp = getattr(client, method)(path)
    assert resp.status_code == 401
    assert "www-authenticate" in {k.lower() for k in resp.headers}


def test_hive_invalid_credential_returns_401(hive_env):
    client = TestClient(_build_app())
    resp = client.post("/api/jobs/run", headers=_basic_header(_TEST_USER, "wrongpass"))
    assert resp.status_code == 401


def test_hive_wrong_user_returns_401(hive_env):
    client = TestClient(_build_app())
    resp = client.post("/api/jobs/run", headers=_basic_header("attacker", _TEST_PASS))
    assert resp.status_code == 401


def test_hive_malformed_authorization_returns_401(hive_env):
    client = TestClient(_build_app())
    # Not base64, not "Basic <b64>"
    resp = client.post("/api/jobs/run", headers={"Authorization": "Basic !!!notb64!!!"})
    assert resp.status_code == 401
    resp2 = client.post("/api/jobs/run", headers={"Authorization": "Bearer sometoken"})
    assert resp2.status_code == 401


# ── AC3: hive + valid credential → passes through (200) ─────────────────────────
def test_hive_valid_credential_passes(hive_env):
    client = TestClient(_build_app())
    resp = client.post("/api/jobs/run", headers=_basic_header(_TEST_USER, _TEST_PASS))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── AC4: desktop/daemon mode → privileged routes reachable with NO credential ───
@pytest.mark.parametrize("path", ["/api/jobs/run", "/api/workspace/file", "/api/autonomous-jobs"])
def test_desktop_mode_passthrough_no_credential(desktop_env, path):
    client = TestClient(_build_app())
    method = "post" if path == "/api/jobs/run" else "get"
    resp = getattr(client, method)(path)
    assert resp.status_code == 200  # localhost trust — never locked out


# ── AC5: /health public in ALL modes ────────────────────────────────────────────
def test_health_public_in_hive(hive_env):
    client = TestClient(_build_app())
    resp = client.get("/health")
    assert resp.status_code == 200


def test_health_public_in_desktop(desktop_env):
    client = TestClient(_build_app())
    resp = client.get("/health")
    assert resp.status_code == 200


# ── AC6: SSE streaming preserved (NOT buffered) even under the middleware ────────
def test_sse_streaming_not_buffered_authenticated(hive_env):
    """The core Gate-1 guard: a StreamingResponse must stream incrementally through
    the pure-ASGI middleware. TestClient.stream exposes the raw chunks; we assert the
    body arrives as multiple lines (a buffered burst would still yield the bytes, so
    the real proof is that the middleware did not wrap/replace the StreamingResponse —
    verified by the response media_type surviving + iter_lines yielding each chunk)."""
    client = TestClient(_build_app())
    with client.stream("GET", "/api/chat/stream", headers=_basic_header(_TEST_USER, _TEST_PASS)) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in resp.iter_lines() if ln]
    assert lines == [f"chunk{i}" for i in range(5)]


def test_sse_blocked_without_credential_in_hive(hive_env):
    """SSE is still gated in hive (carries the same credential); no cred → 401,
    and the 401 must NOT be a streaming response."""
    client = TestClient(_build_app())
    resp = client.get("/api/chat/stream")
    assert resp.status_code == 401


# ── AC7: fail-closed on verification error ───────────────────────────────────────
def test_fail_closed_when_verify_raises(hive_env, monkeypatch):
    import middleware.hive_auth as mod

    def _boom(*a, **k):
        raise RuntimeError("bcrypt exploded")

    monkeypatch.setattr(mod, "verify_password", _boom)
    client = TestClient(_build_app())
    resp = client.post("/api/jobs/run", headers=_basic_header(_TEST_USER, _TEST_PASS))
    assert resp.status_code == 401  # exception → deny, never allow


# ── AC8: hive + unset hash → deny ALL non-public (fail-closed, never fail-open) ──
def test_unset_hash_denies_all(monkeypatch):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    from config import settings

    monkeypatch.setattr(settings, "hive_user", "", raising=False)
    monkeypatch.setattr(settings, "hive_pass_hash", "", raising=False)
    client = TestClient(_build_app())
    # Even WITH a plausible header, an unset expected hash denies.
    resp = client.post("/api/jobs/run", headers=_basic_header("admin", "anything"))
    assert resp.status_code == 401
    # And /health still public.
    assert client.get("/health").status_code == 200


# ── AC9: OPTIONS preflight passes through (CORS not broken) ──────────────────────
def test_options_preflight_passthrough_in_hive(hive_env):
    """OPTIONS carries no credential by design; it must reach the app/CORS layer,
    not be 401'd. (Our minimal app has no OPTIONS handler, so Starlette returns 405 —
    the point is it is NOT 401, i.e. auth let it through.)"""
    client = TestClient(_build_app())
    resp = client.options("/api/jobs/run")
    assert resp.status_code != 401


# ── bcrypt once per credential, off the event loop (success-only bounded cache) ──
# These use a cost-4 hash so a real bcrypt check stays fast; production keeps cost 14.
import asyncio  # noqa: E402
import threading  # noqa: E402

import httpx  # noqa: E402

import middleware.hive_auth as hive_auth_mod  # noqa: E402
from middleware.hive_auth import _VerifiedCache  # noqa: E402

_FAST_HASH = bcrypt.hashpw(_TEST_PASS.encode(), bcrypt.gensalt(rounds=4)).decode()
_AUTH = _basic_header(_TEST_USER, _TEST_PASS)


def _build_mw() -> HiveAuthMiddleware:
    """The middleware around an inner app, returned as-is so a test can see its cache."""
    inner = Starlette(
        routes=[
            Route("/api/jobs/run", _plain, methods=["POST"]),
            Route("/health", _plain, methods=["GET"]),
        ]
    )
    return HiveAuthMiddleware(inner)


@pytest.fixture
def fast_hive_env(monkeypatch):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    from config import settings

    monkeypatch.setattr(settings, "hive_user", _TEST_USER, raising=False)
    monkeypatch.setattr(settings, "hive_pass_hash", _FAST_HASH, raising=False)
    return settings


@pytest.fixture
def bcrypt_calls(monkeypatch):
    """Count real verify_password calls, recording the thread each one ran on."""
    calls: list[str] = []
    real = hive_auth_mod.verify_password

    def _counting(password, hashed):
        calls.append(threading.current_thread().name)
        return real(password, hashed)

    monkeypatch.setattr(hive_auth_mod, "verify_password", _counting)
    return calls


def test_repeated_valid_credential_runs_bcrypt_once(fast_hive_env, bcrypt_calls):
    client = TestClient(_build_mw())
    for _ in range(5):
        assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200
    assert len(bcrypt_calls) == 1


async def test_concurrent_same_credential_runs_bcrypt_once(fast_hive_env, monkeypatch):
    calls: list[int] = []

    def _slow_ok(password, hashed):
        calls.append(1)
        threading.Event().wait(0.2)  # every request arrives while this check runs
        return True

    monkeypatch.setattr(hive_auth_mod, "verify_password", _slow_ok)
    transport = httpx.ASGITransport(app=_build_mw())
    async with httpx.AsyncClient(transport=transport, base_url="http://hive") as client:
        resps = await asyncio.gather(
            *(client.post("/api/jobs/run", headers=_AUTH) for _ in range(5))
        )
    assert [r.status_code for r in resps] == [200] * 5
    assert len(calls) == 1


def test_cached_success_expires_after_lifetime(fast_hive_env, bcrypt_calls):
    now = [1000.0]
    mw = _build_mw()
    mw._cache = _VerifiedCache(clock=lambda: now[0])
    client = TestClient(mw)
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200
    now[0] += 599.0
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200
    assert len(bcrypt_calls) == 1
    now[0] += 2.0  # 601 s after the check: past the 600 s lifetime
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200
    assert len(bcrypt_calls) == 2


def test_cache_is_bounded_and_evicts_oldest():
    assert hive_auth_mod._CACHE_TTL_S == 600.0
    assert hive_auth_mod._CACHE_MAX_ENTRIES == 32
    cache = _VerifiedCache()
    cache.bind(_FAST_HASH)
    keys = [cache.key(_FAST_HASH, _TEST_USER, f"pw{i}") for i in range(33)]
    for key in keys:
        cache.add(key)
    assert len(cache) == 32
    assert not cache.hit(keys[0])  # the oldest was evicted
    assert all(cache.hit(k) for k in keys[1:])


def test_wrong_password_refused_every_time_and_never_cached(fast_hive_env, bcrypt_calls):
    mw = _build_mw()
    client = TestClient(mw)
    bad = _basic_header(_TEST_USER, "wrongpass")
    for _ in range(3):
        assert client.post("/api/jobs/run", headers=bad).status_code == 401
    assert len(bcrypt_calls) == 3
    assert len(mw._cache) == 0
    # The right one still gets through after the failures.
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200


def test_changed_hash_refuses_old_password(fast_hive_env, bcrypt_calls):
    client = TestClient(_build_mw())
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200
    fast_hive_env.hive_pass_hash = bcrypt.hashpw(b"n3w-pass", bcrypt.gensalt(rounds=4)).decode()
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 401
    assert client.post(
        "/api/jobs/run", headers=_basic_header(_TEST_USER, "n3w-pass")
    ).status_code == 200


def test_failed_verification_error_is_not_cached(fast_hive_env, monkeypatch):
    calls: list[int] = []
    real = hive_auth_mod.verify_password

    def _flaky(password, hashed):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("bcrypt exploded")
        return real(password, hashed)

    monkeypatch.setattr(hive_auth_mod, "verify_password", _flaky)
    mw = _build_mw()
    client = TestClient(mw)
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 401
    assert len(mw._cache) == 0
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200
    assert len(calls) == 2


async def test_concurrent_request_completes_while_first_check_runs(fast_hive_env, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def _blocking(password, hashed):
        entered.set()
        release.wait(5)
        return True

    monkeypatch.setattr(hive_auth_mod, "verify_password", _blocking)
    transport = httpx.ASGITransport(app=_build_mw())
    async with httpx.AsyncClient(transport=transport, base_url="http://hive") as client:
        first = asyncio.create_task(client.post("/api/jobs/run", headers=_AUTH))
        try:
            for _ in range(300):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            health = await asyncio.wait_for(client.get("/health"), 2)
            assert health.status_code == 200
            assert not first.done()  # the first check is still running
        finally:
            release.set()
        assert (await asyncio.wait_for(first, 5)).status_code == 200


def test_bcrypt_runs_on_dedicated_pool(fast_hive_env, bcrypt_calls):
    client = TestClient(_build_mw())
    assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200
    assert len(bcrypt_calls) == 1
    # core.executors names its threads swarm-<pool>; the default executor's are asyncio_N.
    assert bcrypt_calls[0].startswith("swarm-hive_auth")


async def test_pending_checks_are_capped_and_excess_refused_without_bcrypt(fast_hive_env, monkeypatch):
    """A flood of distinct uncached credentials is refused past the cap (503) with no
    bcrypt run and no in-flight entry, while the checks already admitted finish normally."""
    cap = hive_auth_mod._MAX_PENDING_CHECKS
    assert cap == 8
    release = threading.Event()
    calls: list[str] = []

    def _blocking(password, hashed):
        calls.append(password)
        if password == _TEST_PASS:
            return True  # the owner's credential, cached before the flood
        release.wait(5)
        return False

    monkeypatch.setattr(hive_auth_mod, "verify_password", _blocking)
    mw = _build_mw()
    transport = httpx.ASGITransport(app=mw)
    async with httpx.AsyncClient(transport=transport, base_url="http://hive") as client:
        assert (await client.post("/api/jobs/run", headers=_AUTH)).status_code == 200
        pending = [
            asyncio.create_task(
                client.post("/api/jobs/run", headers=_basic_header(_TEST_USER, f"bad{i}"))
            )
            for i in range(cap)
        ]
        try:
            for _ in range(300):
                if len(mw._inflight) == cap:
                    break
                await asyncio.sleep(0.01)
            assert len(mw._inflight) == cap
            extra = await asyncio.wait_for(
                client.post("/api/jobs/run", headers=_basic_header(_TEST_USER, "one-more")), 2
            )
            # Busy, not a wrong password: no challenge, so a browser keeps its credential.
            assert extra.status_code == 503
            assert extra.headers["retry-after"] == "5"
            assert "www-authenticate" not in extra.headers
            assert "one-more" not in calls
            assert len(mw._inflight) == cap
            # A credential cached before the flood keeps working, with no new bcrypt.
            owner = await asyncio.wait_for(client.post("/api/jobs/run", headers=_AUTH), 2)
            assert owner.status_code == 200
            assert calls.count(_TEST_PASS) == 1
        finally:
            release.set()
        assert [r.status_code for r in await asyncio.gather(*pending)] == [401] * cap
    assert mw._inflight == {}


def test_checks_left_by_a_closed_loop_do_not_hold_the_cap(fast_hive_env, bcrypt_calls):
    """In-flight entries of another loop never get their done callback; a full cap of
    them must not refuse every new credential."""
    mw = _build_mw()
    other = asyncio.new_event_loop()
    try:
        for i in range(hive_auth_mod._MAX_PENDING_CHECKS):
            mw._inflight[f"stale{i}".encode()] = other.create_future()
        client = TestClient(mw)
        assert client.post("/api/jobs/run", headers=_AUTH).status_code == 200
        assert len(bcrypt_calls) == 1
        assert mw._inflight == {}
    finally:
        other.close()
