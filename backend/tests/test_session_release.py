"""Tests for SessionRouter.release_session (R6b — tab-close session release).

R6b lets the frontend free a backend SessionUnit's concurrency slot when a
chat tab is closed, instead of waiting for the 12h idle TTL. The release MUST:

- Free the slot (alive_count drops) for IDLE units — the orphan being fixed.
- NEVER delete DB messages (history survives — user can reopen).
- NEVER kill the WRONG session-instance when a slot/sessionId is reused
  (generation stale-guard — Gate-1 CRITICAL 2a/2c).
- Route active states (STREAMING / WAITING_INPUT) through the generation-safe
  interrupt() primitive, never a raw kill that races
  _recover_streaming_on_disconnect (Gate-1 CRITICAL 5a).
- Preserve multi-tab isolation: release(A) must never touch unit B (Gate-1 3).

Test strategy: construct a SessionRouter with mock deps, populate _units with
SessionUnit instances walked to the target state via _transition(), and assert
release_session's effect on alive_count / kill / interrupt without spawning real
subprocesses.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.session_unit import SessionState, SessionUnit
from core.session_router import SessionRouter


def _make_router() -> SessionRouter:
    return SessionRouter(prompt_builder=MagicMock(), config=MagicMock())


def _add_unit(router: SessionRouter, session_id: str, state: SessionState) -> SessionUnit:
    """Add a SessionUnit in the given state, with kill()/interrupt() mocked.

    kill() and interrupt() are mocked so the test exercises release_session's
    DECISION logic (which primitive, on which unit) without real subprocess I/O.
    is_alive derives from state, so we walk the state machine via _transition.
    """
    _PATHS_FROM_COLD: dict[SessionState, list[SessionState]] = {
        SessionState.COLD: [],
        SessionState.IDLE: [SessionState.IDLE],
        SessionState.STREAMING: [SessionState.IDLE, SessionState.STREAMING],
        SessionState.WAITING_INPUT: [
            SessionState.IDLE, SessionState.STREAMING, SessionState.WAITING_INPUT,
        ],
        SessionState.DEAD: [SessionState.DEAD],
    }
    unit = SessionUnit(session_id=session_id, agent_id="default")
    for hop in _PATHS_FROM_COLD[state]:
        unit._transition(hop)
    if state in (SessionState.IDLE, SessionState.STREAMING, SessionState.WAITING_INPUT):
        unit._wrapper = MagicMock()
        unit._wrapper.pid = 12000 + len(router._units)
        unit._client = MagicMock()

    # Mock the two release primitives. kill() transitions to COLD (slot freed);
    # interrupt() returns survived=True and leaves the unit IDLE (warm).
    async def _fake_kill():
        if unit.state not in (SessionState.COLD, SessionState.DEAD):
            unit._transition(SessionState.DEAD)
            unit._transition(SessionState.COLD)
    async def _fake_interrupt(timeout: float = 5.0, autonomous: bool = False):
        if unit.state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            unit._transition(SessionState.IDLE)
        return unit.is_alive
    unit.kill = AsyncMock(side_effect=_fake_kill)
    unit.interrupt = AsyncMock(side_effect=_fake_interrupt)

    router._units[session_id] = unit
    return unit


# ── AC1: idle release frees the slot ──────────────────────────────────────

@pytest.mark.asyncio
async def test_release_idle_unit_frees_slot():
    """AC1: releasing an IDLE unit kills it → alive_count drops by 1."""
    router = _make_router()
    a = _add_unit(router, "sess-A", SessionState.IDLE)
    _add_unit(router, "sess-B", SessionState.IDLE)
    assert router.alive_count == 2

    result = await router.release_session("sess-A")

    assert result["status"] == "released"
    assert a.kill.await_count == 1
    assert router.alive_count == 1


@pytest.mark.asyncio
async def test_release_unknown_session_is_not_found():
    """Releasing a session_id with no unit → not_found, no crash."""
    router = _make_router()
    result = await router.release_session("ghost")
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_release_already_cold_unit_is_noop_released():
    """Releasing a COLD (already-dead) unit is safe — no kill needed."""
    router = _make_router()
    cold = _add_unit(router, "sess-C", SessionState.COLD)
    result = await router.release_session("sess-C")
    # COLD unit: nothing to kill, but state is still cleaned. Not an error.
    assert result["status"] in ("released", "not_found")
    assert cold.kill.await_count == 0


# ── Security HIGH: chat-tab release must NOT reap a channel session ────────

@pytest.mark.asyncio
async def test_release_channel_session_is_skipped():
    """A chat-tab close fires release with a session_id from the URL. If that id
    belongs to a Slack/channel session, release must NOT kill it — channel agents
    persist for the daemon's life (mirrors _check_ttl's channel exemption). Without
    this guard, closing a chat tab could tear down a live channel agent."""
    router = _make_router()
    unit = _add_unit(router, "chan-1", SessionState.IDLE)
    unit.is_channel_session = True

    result = await router.release_session("chan-1")

    assert result["status"] == "skipped_channel"
    assert unit.kill.await_count == 0
    assert unit.interrupt.await_count == 0
    assert unit.is_alive


@pytest.mark.asyncio
async def test_release_channel_session_skipped_even_with_force():
    """Even a forced (confirmed-close) release must not reap a channel session —
    a chat UI has no authority to kill a channel agent regardless of force."""
    router = _make_router()
    unit = _add_unit(router, "chan-2", SessionState.STREAMING)
    unit.is_channel_session = True

    result = await router.release_session("chan-2", force=True)

    assert result["status"] == "skipped_channel"
    assert unit.kill.await_count == 0
    assert unit.interrupt.await_count == 0
    assert unit.is_alive


# ── AC3: isolation — release(A) never touches B ───────────────────────────

@pytest.mark.asyncio
async def test_release_does_not_touch_other_units():
    """AC3: releasing A leaves B alive, present, and untouched."""
    router = _make_router()
    _add_unit(router, "sess-A", SessionState.IDLE)
    b = _add_unit(router, "sess-B", SessionState.IDLE)

    await router.release_session("sess-A")

    assert b.is_alive
    assert b.kill.await_count == 0
    assert b.interrupt.await_count == 0
    assert "sess-B" in router._units


# ── AC6 / Gate-1 2a,2c,5a: active states route through interrupt(), not kill ─

@pytest.mark.asyncio
async def test_release_streaming_unit_without_force_does_not_kill():
    """Gate-1 2a/5a: a STREAMING unit (e.g. freshly-reused slot) is NOT killed
    by an unforced release — it would race _recover_streaming_on_disconnect and
    could destroy a new session-instance."""
    router = _make_router()
    s = _add_unit(router, "sess-S", SessionState.STREAMING)

    result = await router.release_session("sess-S")  # no force

    assert result["status"] == "skipped_active"
    assert s.kill.await_count == 0
    assert s.is_alive  # still STREAMING — untouched


@pytest.mark.asyncio
async def test_release_streaming_unit_with_force_frees_the_slot():
    """Gate-2 HIGH (adversarial): a confirmed (force=True) release of a STREAMING
    unit MUST actually free the slot. interrupt() alone leaves the subprocess
    alive in IDLE — so release must kill after interrupt settles. Asserting only
    interrupt.await_count (as the original test did) masks the orphan-slot bug:
    the slot stays held, defeating the entire purpose of R6b on the streaming
    path. We assert alive_count DROPS, not just that interrupt was called."""
    router = _make_router()
    _add_unit(router, "sess-S", SessionState.STREAMING)
    _add_unit(router, "sess-keep", SessionState.IDLE)
    assert router.alive_count == 2

    result = await router.release_session("sess-S", force=True)

    assert result["status"] == "released"
    # The slot MUST be freed — interrupt-then-still-alive is the bug.
    assert router.alive_count == 1, "force-release of a streaming tab must free the slot"
    assert not router.get_unit("sess-S").is_alive


@pytest.mark.asyncio
async def test_release_waiting_input_without_force_does_not_kill():
    """WAITING_INPUT is a hidden active state — must be protected like STREAMING."""
    router = _make_router()
    w = _add_unit(router, "sess-W", SessionState.WAITING_INPUT)

    result = await router.release_session("sess-W")

    assert result["status"] == "skipped_active"
    assert w.kill.await_count == 0
    assert w.is_alive


# ── AC1/AC4: POST /api/chat/release/{session_id} endpoint ─────────────────
# Drive the endpoint over REAL HTTP via a minimal FastAPI app mounting ONLY the
# release route. We do NOT use TestClient(main.app): booting the full app lifespan
# runs the skill-projection copytree (intermittently fails on s_docx/s_pollinate
# node_modules in this env). A minimal app gives us FastAPI's real body parsing —
# which the meta-review flagged as the untested gap — without the lifespan hang.

def _release_client(monkeypatch):
    """Minimal FastAPI app with just POST /chat/release/{id} + a mocked router.
    Returns (TestClient, fake_router) so tests can assert force/delegation over
    a real HTTP request (real body deserialization, real content-type handling)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.chat as chat_mod

    fake_router = MagicMock()
    fake_router.release_session = AsyncMock(
        return_value={"status": "released", "alive_count": 0}
    )
    monkeypatch.setattr(chat_mod, "_get_router", lambda: fake_router)

    app = FastAPI()
    app.post("/chat/release/{session_id}")(chat_mod.release_session)
    return TestClient(app), fake_router


def test_release_endpoint_no_body_returns_200_unforced(monkeypatch):
    """AC1: idle close sends NO body → 200, unforced delegation."""
    client, fake_router = _release_client(monkeypatch)
    resp = client.post("/chat/release/sess-X")  # no body, like axios post(url, undefined)
    assert resp.status_code == 200
    assert resp.json()["status"] == "released"
    _, kwargs = fake_router.release_session.call_args
    assert kwargs.get("force") is False


def test_release_endpoint_force_true_delegates_forced(monkeypatch):
    """AC2: confirmed streaming close sends {force:true} → forced delegation."""
    client, fake_router = _release_client(monkeypatch)
    resp = client.post("/chat/release/sess-X", json={"force": True})
    assert resp.status_code == 200
    _, kwargs = fake_router.release_session.call_args
    assert kwargs.get("force") is True


def test_release_endpoint_force_is_strict_boolean(monkeypatch):
    """Adversarial LOW: force enabled ONLY by an explicit JSON `true`. A STRING
    {"force":"false"} (or any non-bool truthy) must NOT enable the destructive
    interrupt-active branch — verified over real HTTP."""
    client, fake_router = _release_client(monkeypatch)
    for bad in ({"force": "false"}, {"force": "true"}, {"force": 1}, {"force": "1"}, {}):
        fake_router.release_session.reset_mock()
        resp = client.post("/chat/release/sess-X", json=bad)
        assert resp.status_code == 200
        _, kwargs = fake_router.release_session.call_args
        assert kwargs.get("force") is False, f"non-bool {bad!r} must not force"


def test_release_endpoint_malformed_body_never_422(monkeypatch):
    """Meta-review MED: a fire-and-forget endpoint must ALWAYS return 200. A
    malformed/empty body with application/json content-type must degrade to an
    unforced release, never a 422 (which would silently leave the slot held)."""
    client, fake_router = _release_client(monkeypatch)
    # malformed JSON
    r1 = client.post("/chat/release/sess-X",
                     headers={"Content-Type": "application/json"}, content=b"{bad json")
    assert r1.status_code == 200
    # empty body WITH json content-type
    r2 = client.post("/chat/release/sess-X",
                     headers={"Content-Type": "application/json"}, content=b"")
    assert r2.status_code == 200
    # both degrade to unforced
    _, kwargs = fake_router.release_session.call_args
    assert kwargs.get("force") is False


def test_release_endpoint_does_not_delete_messages(monkeypatch):
    """AC4: the release endpoint must NOT call db.messages.delete_by_session
    (history survives so the user can reopen the closed session)."""
    from database import db
    client, fake_router = _release_client(monkeypatch)
    delete_spy = AsyncMock()
    monkeypatch.setattr(db.messages, "delete_by_session", delete_spy)

    resp = client.post("/chat/release/sess-X")
    assert resp.status_code == 200
    delete_spy.assert_not_awaited()
