"""Tests for POST /chat/refresh/{session_id} endpoint and SessionUnit.refresh_context().

Validates:
- Guard: STREAMING state → 409 (both router and unit level)
- Guard: WAITING_INPUT state → 409 (both router and unit level)
- Semantics: missing session → 404 (not 409)
- Success: IDLE session → 200 + subprocess killed

Testing methodology: unit test with mocked SessionRouter/SessionUnit.
"""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.session_unit import SessionState, SessionUnit
from core.session_router import SessionRouter


# ---------------------------------------------------------------------------
# SessionUnit.refresh_context() — guard tests
# ---------------------------------------------------------------------------

class TestRefreshContextUnitGuard:
    """refresh_context() rejects STREAMING and WAITING_INPUT at the unit level."""

    def _make_unit(self, state: SessionState) -> SessionUnit:
        """Create a minimal SessionUnit with mocked internals."""
        unit = object.__new__(SessionUnit)
        unit.state = state
        unit.session_id = "test-session-123"
        unit._sdk_session_id = "sdk-abc"
        unit._process = MagicMock()
        unit._crash_to_cold_async = AsyncMock()
        return unit

    @property
    def _state_property(self):
        """Helper — SessionUnit.state is a property, need to verify it reads _state."""
        pass

    @pytest.mark.asyncio
    async def test_rejects_streaming(self):
        """STREAMING → RuntimeError raised (defense in depth with router)."""
        unit = self._make_unit(SessionState.STREAMING)
        with pytest.raises(RuntimeError, match="Cannot refresh while streaming"):
            await unit.refresh_context()

    @pytest.mark.asyncio
    async def test_rejects_waiting_input(self):
        """WAITING_INPUT → RuntimeError raised (prevents killing pending question)."""
        unit = self._make_unit(SessionState.WAITING_INPUT)
        with pytest.raises(RuntimeError, match="Cannot refresh while waiting"):
            await unit.refresh_context()

    @pytest.mark.asyncio
    async def test_cold_is_noop(self):
        """COLD → returns without killing (no subprocess to kill)."""
        unit = self._make_unit(SessionState.COLD)
        # Should return without calling _crash_to_cold_async
        await unit.refresh_context()
        unit._crash_to_cold_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_calls_crash_to_cold(self):
        """IDLE → kills subprocess via _crash_to_cold_async(clear_identity=True).

        clear_identity=True drops _sdk_session_id, so the next send() is a TRUE
        cold resume (session_router is_cold_resume=True) → mechanism-B structured
        summary injection fires. This is the whole point of the button: shed the
        bloated transcript and restart on a summary, NOT --resume the full log.
        """
        unit = self._make_unit(SessionState.IDLE)
        await unit.refresh_context()
        unit._crash_to_cold_async.assert_called_once_with(clear_identity=True)

    @pytest.mark.asyncio
    async def test_idle_clears_sdk_session_id(self):
        """IDLE refresh must NULL _sdk_session_id (the is_cold_resume precondition).

        Drives the REAL _crash_to_cold_async (NOT mocked — only _force_kill +
        _transition are stubbed) so this test has TEETH: revert refresh_context to
        clear_identity=False and _cleanup_internal preserves _sdk_session_id →
        this assertion goes RED. Mechanism-B injection is gated on
        _sdk_session_id is None (session_router.py is_cold_resume), so this is the
        load-bearing effect, not just the call arg.
        """
        unit = object.__new__(SessionUnit)
        unit.state = SessionState.IDLE
        unit.session_id = "test-session-123"
        unit._sdk_session_id = "sdk-abc"
        # Real _crash_to_cold_async + real _full_cleanup run; stub only the
        # OS-touching internals and the transient-field reset (_cleanup_internal
        # touches ~24 subprocess attrs a bare unit lacks). Teeth are preserved:
        # _full_cleanup = _cleanup_internal() + `_sdk_session_id = None`, and the
        # fix's discriminator is WHICH cleanup runs — clear_identity=True →
        # _full_cleanup (nulls id); revert to False → _cleanup_internal (no-op
        # here) leaves the id set → this assertion goes RED.
        unit._lock = asyncio.Lock()
        unit._force_kill = AsyncMock()
        unit._transition = MagicMock()
        unit._cleanup_internal = MagicMock()
        await unit.refresh_context()
        assert unit._sdk_session_id is None


# ---------------------------------------------------------------------------
# SessionRouter.refresh_session() — integration guard tests
# ---------------------------------------------------------------------------

class TestRefreshSessionRouter:
    """Router-level tests for refresh_session() return values."""

    def _make_router_with_unit(self, state: SessionState) -> SessionRouter:
        """Create a minimal router with a mocked unit in given state."""
        router = object.__new__(SessionRouter)
        unit = MagicMock()
        unit.state = state
        unit.refresh_context = AsyncMock()
        router._units = {"sess-1": unit}
        router.get_unit = MagicMock(return_value=unit)
        return router

    def _make_router_no_unit(self) -> SessionRouter:
        """Create a router that returns None for get_unit."""
        router = object.__new__(SessionRouter)
        router._units = {}
        router.get_unit = MagicMock(return_value=None)
        return router

    @pytest.mark.asyncio
    async def test_not_found_returns_failure(self):
        """Missing session → success=False with 'not found' message."""
        router = self._make_router_no_unit()
        result = await router.refresh_session("nonexistent")
        assert result["success"] is False
        assert "not found" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_streaming_returns_failure(self):
        """STREAMING → success=False with 'active' message."""
        router = self._make_router_with_unit(SessionState.STREAMING)
        result = await router.refresh_session("sess-1")
        assert result["success"] is False
        assert "active" in result["message"].lower() or "stop" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_waiting_input_returns_failure(self):
        """WAITING_INPUT → success=False with guidance message."""
        router = self._make_router_with_unit(SessionState.WAITING_INPUT)
        result = await router.refresh_session("sess-1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_idle_returns_success(self):
        """IDLE session → success=True."""
        router = self._make_router_with_unit(SessionState.IDLE)
        result = await router.refresh_session("sess-1")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# HTTP endpoint — status code semantics
# ---------------------------------------------------------------------------

class TestRefreshEndpointHTTPSemantics:
    """Verify correct HTTP status codes via the FastAPI test client pattern."""

    @pytest.mark.asyncio
    async def test_not_found_is_404(self):
        """Session not found → HTTP 404 (not 409)."""
        from routers.chat import refresh_session
        from fastapi import HTTPException

        with patch("routers.chat._get_router") as mock_get_router:
            mock_router = AsyncMock()
            mock_router.refresh_session.return_value = {
                "success": False,
                "message": "Session xyz not found",
            }
            mock_get_router.return_value = mock_router

            with pytest.raises(HTTPException) as exc_info:
                await refresh_session("xyz")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_busy_is_409(self):
        """Session busy (streaming) → HTTP 409."""
        from routers.chat import refresh_session
        from fastapi import HTTPException

        with patch("routers.chat._get_router") as mock_get_router:
            mock_router = AsyncMock()
            mock_router.refresh_session.return_value = {
                "success": False,
                "message": "Cannot refresh while the AI is active.",
            }
            mock_get_router.return_value = mock_router

            with pytest.raises(HTTPException) as exc_info:
                await refresh_session("sess-1")
            assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_success_is_200(self):
        """Successful refresh → HTTP 200 with status=refreshed."""
        from routers.chat import refresh_session

        with patch("routers.chat._get_router") as mock_get_router:
            mock_router = AsyncMock()
            mock_router.refresh_session.return_value = {
                "success": True,
                "message": "Context refreshed.",
            }
            mock_get_router.return_value = mock_router

            result = await refresh_session("sess-1")
            assert result["status"] == "refreshed"


# ---------------------------------------------------------------------------
# dead→streaming race guard (run_c9fa2382) — send() must NOT crash the chat
# stream when a concurrent refresh/release/kill flips the session out of IDLE
# during its streaming-slot / _client_io wait.
# ---------------------------------------------------------------------------

class TestSendStateFlipRaceGuard:
    """send() re-checks state after its blocking waits and before
    _transition(STREAMING); a concurrent flip to DEAD/COLD → clean SESSION_BUSY
    abort, never a raw 'Invalid state transition …→streaming' RuntimeError."""

    @pytest.mark.asyncio
    async def test_flip_to_dead_during_slot_wait_aborts_cleanly(self, monkeypatch):
        """The exact observed race: unit is IDLE and reaches send()'s waits, a
        concurrent refresh drives it to DEAD during _await_streaming_slot, and
        send() must abort with SESSION_BUSY + _abort — NOT raise RuntimeError."""
        unit = SessionUnit(session_id="sess-race", agent_id="a")
        unit.state = SessionState.IDLE
        unit._client = object()  # warm, non-None → no spawn needed
        unit._last_turn_clean = True  # skip the poison-guard recycle

        # Simulate a concurrent refresh_context()/kill() landing DURING the
        # streaming-slot wait: flip IDLE → DEAD (the real refresh drives
        # IDLE→DEAD→COLD; we assert the guard catches ANY non-IDLE state).
        async def _flip_to_dead_during_wait():
            unit.state = SessionState.DEAD

        monkeypatch.setattr(unit, "_await_streaming_slot", _flip_to_dead_during_wait)

        events = []
        # MUST NOT raise RuntimeError("Invalid state transition dead→streaming").
        async for ev in unit.send("hi", MagicMock()):
            events.append(ev)

        # send() aborted cleanly: a SESSION_BUSY error event then {_abort}.
        assert any(
            e.get("type") == "error" and e.get("code") == "SESSION_BUSY"
            for e in events
        ), f"expected a SESSION_BUSY error event, got {events}"
        assert events[-1].get("_abort") is True, f"expected trailing _abort, got {events}"
        # And it never transitioned to STREAMING.
        assert unit.state != SessionState.STREAMING

    @pytest.mark.asyncio
    async def test_no_flip_reaches_streaming(self, monkeypatch):
        """Regression guard: with NO concurrent flip, send() still transitions
        IDLE → STREAMING (the guard must not false-positive on the happy path)."""
        unit = SessionUnit(session_id="sess-happy", agent_id="a")
        unit.state = SessionState.IDLE
        unit._client = object()
        unit._last_turn_clean = True

        async def _noop_slot():
            return

        monkeypatch.setattr(unit, "_await_streaming_slot", _noop_slot)

        # Stop send() right AFTER the STREAMING transition (before real streaming
        # body) so we assert the transition happened without driving the SDK.
        reached = {"streaming": False}
        real_transition = unit._transition

        def _spy_transition(new_state):
            real_transition(new_state)
            if new_state == SessionState.STREAMING:
                reached["streaming"] = True
                raise RuntimeError("stop-after-streaming-transition")

        monkeypatch.setattr(unit, "_transition", _spy_transition)

        with pytest.raises(RuntimeError, match="stop-after-streaming-transition"):
            async for _ in unit.send("hi", MagicMock()):
                pass
        assert reached["streaming"] is True, "happy path must reach STREAMING transition"


class TestCompactKilledDuringDrainDenoise:
    """compact() drain failure caused by an intentional concurrent kill (state
    DEAD/COLD at except-time) logs at INFO, not ERROR; a genuine failure
    (state still IDLE) stays ERROR."""

    def _make_idle_unit_with_failing_client(self, *, flip_to_on_drain=None):
        """Build an IDLE unit whose subprocess drain raises exit -9. If
        flip_to_on_drain is a SessionState, the unit's state flips to it AT the
        moment the drain fails — simulating a concurrent refresh/kill killing
        the subprocess mid-drain (state is IDLE at compact-entry, as it must be
        to pass compact()'s IDLE guard, then flips during the drain)."""
        unit = SessionUnit(session_id="sess-compact", agent_id="a")
        unit.state = SessionState.IDLE
        client = MagicMock()
        client.query = AsyncMock()

        async def _boom_receive(*a, **k):
            if flip_to_on_drain is not None:
                unit.state = flip_to_on_drain
            raise RuntimeError("Command failed with exit code -9 (exit code: -9)")
            yield  # pragma: no cover — async generator

        client.receive_response = _boom_receive
        unit._client = client
        unit._sdk_session_id = "sdk-x"
        return unit

    @pytest.mark.asyncio
    async def test_killed_during_drain_logs_info_not_error(self, caplog):
        """Concurrent kill flips state to DEAD DURING the drain → NOT an ERROR."""
        import logging
        # IDLE at compact-entry (passes the guard), flips to DEAD when the
        # drain fails — the real concurrent-kill-mid-drain sequence.
        unit = self._make_idle_unit_with_failing_client(flip_to_on_drain=SessionState.DEAD)

        with caplog.at_level(logging.INFO, logger="core.session_unit"):
            result = await unit.compact()

        assert result["success"] is False
        compact_errors = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and "ompact" in r.getMessage()
        ]
        assert not compact_errors, f"killed-during-drain must not log ERROR, got {compact_errors}"
        assert any(
            r.levelno == logging.INFO and "killed during drain" in r.getMessage()
            for r in caplog.records
        ), "expected an INFO 'killed during drain' line"

    @pytest.mark.asyncio
    async def test_genuine_failure_still_logs_error(self, caplog):
        """A genuine drain failure (state still IDLE, subprocess alive) → ERROR."""
        import logging
        unit = self._make_idle_unit_with_failing_client()
        # State stays IDLE (no concurrent kill) → genuine failure.

        with caplog.at_level(logging.INFO, logger="core.session_unit"):
            result = await unit.compact()

        assert result["success"] is False
        assert any(
            r.levelno == logging.ERROR and "Compact failed" in r.getMessage()
            for r in caplog.records
        ), "a genuine compact failure (state IDLE) must still log ERROR"
