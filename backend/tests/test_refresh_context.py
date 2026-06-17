"""Tests for POST /chat/refresh/{session_id} endpoint and SessionUnit.refresh_context().

Validates:
- Guard: STREAMING state → 409 (both router and unit level)
- Guard: WAITING_INPUT state → 409 (both router and unit level)
- Semantics: missing session → 404 (not 409)
- Success: IDLE session → 200 + subprocess killed

Testing methodology: unit test with mocked SessionRouter/SessionUnit.
"""
from __future__ import annotations

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
        """IDLE → kills subprocess via _crash_to_cold_async(clear_identity=False)."""
        unit = self._make_unit(SessionState.IDLE)
        await unit.refresh_context()
        unit._crash_to_cold_async.assert_called_once_with(clear_identity=False)


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
