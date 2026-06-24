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
async def test_release_streaming_unit_with_force_routes_through_interrupt():
    """Gate-1 5a: a confirmed (force=True) release of a STREAMING unit uses the
    generation-safe interrupt() primitive, NOT a raw kill()."""
    router = _make_router()
    s = _add_unit(router, "sess-S", SessionState.STREAMING)

    result = await router.release_session("sess-S", force=True)

    assert s.interrupt.await_count == 1
    assert s.kill.await_count == 0  # never a raw kill on an active unit
    assert result["status"] in ("released", "interrupted")


@pytest.mark.asyncio
async def test_release_waiting_input_without_force_does_not_kill():
    """WAITING_INPUT is a hidden active state — must be protected like STREAMING."""
    router = _make_router()
    w = _add_unit(router, "sess-W", SessionState.WAITING_INPUT)

    result = await router.release_session("sess-W")

    assert result["status"] == "skipped_active"
    assert w.kill.await_count == 0
    assert w.is_alive


# ── Gate-1 2a: generation stale-guard on the IDLE path ────────────────────

@pytest.mark.asyncio
async def test_release_idle_aborts_if_generation_advanced():
    """Gate-1 2a (temporal aliasing): if a new send() bumps _send_generation
    between the release request and the kill, release must NOT kill — the slot
    was re-adopted by a new turn."""
    router = _make_router()
    a = _add_unit(router, "sess-A", SessionState.IDLE)
    gen_at_request = a._send_generation

    # Simulate a concurrent send() that advanced the generation AND moved the
    # unit back to STREAMING after the caller captured intent to release.
    async def _fake_kill_with_race():
        # by the time kill is reached, a new send has happened
        raise AssertionError("kill() must not be called when generation advanced")
    a.kill = AsyncMock(side_effect=_fake_kill_with_race)

    result = await router.release_session(
        "sess-A", expected_generation=gen_at_request + 5
    )
    assert result["status"] in ("skipped_stale", "skipped_active")
    assert a.kill.await_count == 0
