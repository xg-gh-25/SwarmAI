"""Unit tests for disconnect_all() edge cases and idempotency.

This module verifies that ``AgentManager.disconnect_all()`` behaves
correctly under edge conditions, particularly:

- **Double disconnect_all**: Calling ``disconnect_all()`` twice in
  succession completes without error.  The second call finds
  ``_active_sessions`` empty, ``_clients`` empty, and the cleanup
  loop already cancelled, so it is effectively a no-op.

Testing methodology: async unit tests using ``pytest-asyncio`` with
``unittest.mock.AsyncMock`` and ``unittest.mock.MagicMock`` for mocking
hook executors, SDK client wrappers, and DB queries.

**Validates: Requirements 3.1**
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_hooks import (
    HookContext,
    BackgroundHookExecutor,
    SessionLifecycleHookManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_info(agent_id: str = "agent-dc-001") -> dict:
    """Build a minimal ``_active_sessions`` entry for testing."""
    return {
        "agent_id": agent_id,
        "created_at": time.time(),
        "last_used": time.time(),
        "wrapper": AsyncMock(),  # mock SDK wrapper with __aexit__
        "activity_extracted": False,
    }


def _make_hook_context(
    session_id: str = "sess-dc-001",
    agent_id: str = "agent-dc-001",
) -> HookContext:
    """Build a ``HookContext`` for assertions."""
    return HookContext(
        session_id=session_id,
        agent_id=agent_id,
        message_count=3,
        session_start_time="2025-01-01T00:00:00Z",
        session_title="Disconnect Test Session",
    )


# ---------------------------------------------------------------------------
# Task 8.7 — Double disconnect_all is a no-op (edge case)
# ---------------------------------------------------------------------------


class TestDoubleDisconnectAll:
    """Verify calling disconnect_all() twice completes without error.

    After the first ``disconnect_all()``:
    - ``_active_sessions`` is empty
    - ``_clients`` is empty
    - The cleanup loop is cancelled
    - ``drain()`` on empty ``_pending`` returns ``(0, 0)``

    The second call should find nothing to do and complete cleanly.

    **Validates: Requirements 3.1**
    """

    @pytest.mark.asyncio
    async def test_double_disconnect_all_no_error(self):
        """Two consecutive disconnect_all() calls complete without error."""
        from core.agent_manager import agent_manager

        session_id = "sess-double-dc-001"
        info = _make_session_info(agent_id="agent-double-dc-001")
        ctx = _make_hook_context(
            session_id=session_id,
            agent_id="agent-double-dc-001",
        )

        # Set up a mock hook executor that tracks drain calls
        hook_manager = SessionLifecycleHookManager(timeout_seconds=30.0)
        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()
        mock_executor.pending_count = 0
        mock_executor.drain = AsyncMock(return_value=(0, 0))

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        original_clients = agent_manager._clients.copy()
        original_cleanup_task = agent_manager._cleanup_task
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info
            agent_manager._clients = {}
            agent_manager._cleanup_task = None

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=ctx,
            ):
                # First disconnect_all — cleans up the session
                await agent_manager.disconnect_all()

            assert session_id not in agent_manager._active_sessions
            assert len(agent_manager._active_sessions) == 0

            # Second disconnect_all — should be a no-op
            await agent_manager.disconnect_all()

            # Still empty after second call
            assert len(agent_manager._active_sessions) == 0
            assert len(agent_manager._clients) == 0

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions
            agent_manager._clients = original_clients
            agent_manager._cleanup_task = original_cleanup_task

    @pytest.mark.asyncio
    async def test_second_disconnect_does_not_fire_hooks(self):
        """The second disconnect_all() does not fire any hook tasks.

        After the first call empties ``_active_sessions``, the loop
        in ``disconnect_all()`` has nothing to iterate, so ``fire()``
        should not be called again.

        **Validates: Requirements 3.1**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-double-dc-002"
        info = _make_session_info(agent_id="agent-double-dc-002")
        ctx = _make_hook_context(
            session_id=session_id,
            agent_id="agent-double-dc-002",
        )

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()
        mock_executor.pending_count = 0
        mock_executor.drain = AsyncMock(return_value=(0, 0))

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        original_clients = agent_manager._clients.copy()
        original_cleanup_task = agent_manager._cleanup_task
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info
            agent_manager._clients = {}
            agent_manager._cleanup_task = None

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=ctx,
            ):
                await agent_manager.disconnect_all()

            # fire() called once for the single session
            first_call_count = mock_executor.fire.call_count
            assert first_call_count == 1

            # Second disconnect — no sessions to process
            await agent_manager.disconnect_all()

            # fire() call count unchanged — no new fires
            assert mock_executor.fire.call_count == first_call_count

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions
            agent_manager._clients = original_clients
            agent_manager._cleanup_task = original_cleanup_task

    @pytest.mark.asyncio
    async def test_second_disconnect_drain_returns_zero(self):
        """The second disconnect_all() drain returns (0, 0).

        With no pending tasks after the first call, ``drain()``
        on the second call should return ``(0, 0)`` immediately.

        **Validates: Requirements 3.1, 3.5**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-double-dc-003"
        info = _make_session_info(agent_id="agent-double-dc-003")
        ctx = _make_hook_context(
            session_id=session_id,
            agent_id="agent-double-dc-003",
        )

        # Track drain return values across both calls
        drain_results = []

        async def _tracking_drain(timeout=10.0):
            result = (0, 0)
            drain_results.append(result)
            return result

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()
        mock_executor.pending_count = 0
        mock_executor.drain = AsyncMock(side_effect=_tracking_drain)

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        original_clients = agent_manager._clients.copy()
        original_cleanup_task = agent_manager._cleanup_task
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info
            agent_manager._clients = {}
            agent_manager._cleanup_task = None

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=ctx,
            ):
                await agent_manager.disconnect_all()

            await agent_manager.disconnect_all()

            # drain() called twice (once per disconnect_all)
            assert mock_executor.drain.call_count == 2
            # Both returned (0, 0)
            assert all(r == (0, 0) for r in drain_results)

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions
            agent_manager._clients = original_clients
            agent_manager._cleanup_task = original_cleanup_task
