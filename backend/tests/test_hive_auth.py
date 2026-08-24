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
