"""Unit tests for the restructured ``disconnect_all()`` in ``AgentManager``.

This module verifies the four-phase shutdown sequence introduced by the
graceful-shutdown-fix spec.  Each test targets a specific sub-task:

- ``test_zero_sessions_fast_return``       — 13.1: empty sessions → no drain
- ``test_drain_called_with_timeout_8``     — 13.2: drain(timeout=8.0)
- ``test_da_cancellation_warning_log``     — 13.3: slow DA → warning log
- ``test_phase0_logging``                  — 13.4: session/extracted counts
- ``test_skip_if_extracted``               — 13.5: extracted sessions excluded

Testing methodology: async unit tests using ``pytest-asyncio`` with
``unittest.mock`` for mocking hook executors, SDK client wrappers,
and DB queries.  Tests operate on the ``agent_manager`` singleton,
saving and restoring original state in try/finally blocks.

**Validates: Req 2.4, 3.6, 6.2, 7.1, 8.2**
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_hooks import (
    BackgroundHookExecutor,
    HookContext,
    SessionLifecycleHookManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_info(
    agent_id: str = "agent-gs-001",
    activity_extracted: bool = False,
) -> dict:
    """Build a minimal ``_active_sessions`` entry for testing."""
    return {
        "agent_id": agent_id,
        "created_at": time.time(),
        "last_used": time.time(),
        "wrapper": AsyncMock(),
        "activity_extracted": activity_extracted,
    }


def _make_hook_context(
    session_id: str = "sess-gs-001",
    agent_id: str = "agent-gs-001",
) -> HookContext:
    """Build a ``HookContext`` for assertions."""
    return HookContext(
        session_id=session_id,
        agent_id=agent_id,
        message_count=3,
        session_start_time="2025-01-01T00:00:00Z",
        session_title="Graceful Shutdown Test",
    )


def _setup_agent_manager(
    session_map: dict[str, dict] | None = None,
    da_hook=None,
):
    """Inject mock sessions and executor into the agent_manager singleton.

    Returns ``(mock_executor, originals)`` where *originals* is a dict
    of original state to restore in teardown.
    """
    from core.agent_manager import agent_manager

    mock_executor = MagicMock(spec=BackgroundHookExecutor)
    mock_executor.fire = MagicMock()
    mock_executor.pending_count = 0
    mock_executor.drain = AsyncMock(return_value=(0, 0))
    mock_executor.hooks = [da_hook] if da_hook else []

    originals = {
        "executor": agent_manager._hook_executor,
        "sessions": agent_manager._active_sessions.copy(),
        "clients": agent_manager._clients.copy(),
        "cleanup_task": agent_manager._cleanup_task,
    }

    agent_manager._hook_executor = mock_executor
    agent_manager._active_sessions.clear()
    if session_map:
        agent_manager._active_sessions.update(session_map)
    agent_manager._clients = {}
    agent_manager._cleanup_task = None

    return mock_executor, originals


def _restore_agent_manager(originals: dict):
    """Restore agent_manager singleton to its original state."""
    from core.agent_manager import agent_manager

    agent_manager._hook_executor = originals["executor"]
    agent_manager._active_sessions = originals["sessions"]
    agent_manager._clients = originals["clients"]
    agent_manager._cleanup_task = originals["cleanup_task"]


# ---------------------------------------------------------------------------
# 13.1 — Zero sessions: returns immediately without calling drain()
# ---------------------------------------------------------------------------


class TestZeroSessionsFastReturn:
    """Verify disconnect_all() with empty _active_sessions returns
    immediately without calling drain().

    **Validates: Req 6.2**
    """

    @pytest.mark.asyncio
    async def test_zero_sessions_no_drain(self):
        """Empty _active_sessions → fast return, drain() never called."""
        from core.agent_manager import agent_manager

        mock_executor, originals = _setup_agent_manager(session_map=None)
        try:
            await agent_manager.disconnect_all()

            mock_executor.drain.assert_not_called()
        finally:
            _restore_agent_manager(originals)

    @pytest.mark.asyncio
    async def test_zero_sessions_clears_clients(self):
        """Even with zero sessions, transient clients are cleared."""
        from core.agent_manager import agent_manager

        mock_executor, originals = _setup_agent_manager(session_map=None)
        # Add a transient client to verify it gets cleared
        agent_manager._clients["transient-1"] = AsyncMock()
        try:
            await agent_manager.disconnect_all()

            assert len(agent_manager._clients) == 0
        finally:
            _restore_agent_manager(originals)


# ---------------------------------------------------------------------------
# 13.2 — Drain timeout value: assert drain() called with timeout=8.0
# ---------------------------------------------------------------------------


class TestDrainTimeoutValue:
    """Verify drain() is called with the correct 8.0s timeout.

    **Validates: Req 2.4**
    """

    @pytest.mark.asyncio
    async def test_drain_called_with_timeout_8(self):
        """drain() receives timeout=8.0 when sessions are present."""
        from core.agent_manager import agent_manager

        sid = "sess-drain-timeout-001"
        info = _make_session_info()
        ctx = _make_hook_context(session_id=sid)

        da_hook = MagicMock()
        da_hook.name = "daily_activity_extraction"
        da_hook.execute = AsyncMock(return_value=None)

        mock_executor, originals = _setup_agent_manager(
            session_map={sid: info},
            da_hook=da_hook,
        )
        try:
            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=ctx,
            ):
                await agent_manager.disconnect_all()

            mock_executor.drain.assert_called_once()
            call_kwargs = mock_executor.drain.call_args
            # drain() should be called with timeout=8.0
            assert call_kwargs == ((), {"timeout": 8.0}) or \
                   call_kwargs[1].get("timeout") == 8.0 or \
                   (call_kwargs[0] and call_kwargs[0][0] == 8.0)
        finally:
            _restore_agent_manager(originals)


# ---------------------------------------------------------------------------
# 13.3 — DA cancellation logging: mock slow DA (>5s), assert warning log
# ---------------------------------------------------------------------------


class TestDACancellationLogging:
    """Verify that a slow DailyActivity extraction (>5s per-session timeout)
    produces a warning log.

    **Validates: Req 3.6**
    """

    @pytest.mark.asyncio
    async def test_slow_da_logs_warning(self, caplog):
        """DA extraction exceeding 5s per-session timeout logs a warning."""
        from core.agent_manager import agent_manager

        sid = "sess-slow-da-001"
        info = _make_session_info(activity_extracted=False)
        ctx = _make_hook_context(session_id=sid)

        # Create a DA hook whose execute sleeps longer than the 5s timeout
        async def _slow_execute(context):
            await asyncio.sleep(60)  # Will be cancelled by wait_for

        da_hook = MagicMock()
        da_hook.name = "daily_activity_extraction"
        da_hook.execute = _slow_execute

        mock_executor, originals = _setup_agent_manager(
            session_map={sid: info},
            da_hook=da_hook,
        )
        try:
            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=ctx,
            ), caplog.at_level(logging.WARNING):
                await agent_manager.disconnect_all()

            # The per-session 5s timeout should trigger a warning
            warning_msgs = [
                r.message for r in caplog.records
                if r.levelno >= logging.WARNING and sid in r.message
            ]
            assert len(warning_msgs) > 0, (
                f"Expected warning log for slow DA on {sid}, "
                f"got: {[r.message for r in caplog.records]}"
            )
            # Verify the warning mentions the session ID
            assert any(sid in msg for msg in warning_msgs)
        finally:
            _restore_agent_manager(originals)


# ---------------------------------------------------------------------------
# 13.4 — Phase 0 logging: assert log contains session count and
#         activity_extracted counts
# ---------------------------------------------------------------------------


class TestPhase0Logging:
    """Verify Phase 0 logs session count and activity_extracted counts.

    **Validates: Req 7.1**
    """

    @pytest.mark.asyncio
    async def test_phase0_logs_session_and_extracted_counts(self, caplog):
        """Phase 0 log line includes total sessions and extracted count."""
        from core.agent_manager import agent_manager

        sessions = {
            "sess-p0-001": _make_session_info(activity_extracted=False),
            "sess-p0-002": _make_session_info(activity_extracted=True),
            "sess-p0-003": _make_session_info(activity_extracted=True),
        }

        da_hook = MagicMock()
        da_hook.name = "daily_activity_extraction"
        da_hook.execute = AsyncMock(return_value=None)

        mock_executor, originals = _setup_agent_manager(
            session_map=sessions,
            da_hook=da_hook,
        )
        try:
            # Build contexts for each session
            async def _build_ctx(sid, info):
                return _make_hook_context(session_id=sid)

            with patch.object(
                agent_manager,
                "_build_hook_context",
                side_effect=_build_ctx,
            ), caplog.at_level(logging.INFO):
                await agent_manager.disconnect_all()

            # Find the Phase 0 log line
            phase0_msgs = [
                r.message for r in caplog.records
                if "Phase 0" in r.message
            ]
            assert len(phase0_msgs) >= 1, (
                f"Expected Phase 0 log, got: {[r.message for r in caplog.records]}"
            )
            phase0_msg = phase0_msgs[0]
            # Should contain "3 sessions" and "2 with activity_extracted"
            assert "3" in phase0_msg, f"Expected 3 sessions in: {phase0_msg}"
            assert "2" in phase0_msg, f"Expected 2 extracted in: {phase0_msg}"
        finally:
            _restore_agent_manager(originals)


# ---------------------------------------------------------------------------
# 13.5 — Skip-if-extracted: sessions with flag excluded from DA batch,
#         skip_hooks passed to fire()
# ---------------------------------------------------------------------------


class TestSkipIfExtracted:
    """Verify that sessions with ``activity_extracted=True`` are excluded
    from the DA extraction batch and that ``fire()`` receives
    ``skip_hooks=["daily_activity_extraction"]``.

    **Validates: Req 8.2**
    """

    @pytest.mark.asyncio
    async def test_extracted_session_excluded_from_da_batch(self):
        """Sessions with activity_extracted=True skip DA extraction."""
        from core.agent_manager import agent_manager

        # Track which sessions DA hook.execute is called for
        da_executed_sessions: list[str] = []

        async def _tracking_execute(context):
            da_executed_sessions.append(context.session_id)

        da_hook = MagicMock()
        da_hook.name = "daily_activity_extraction"
        da_hook.execute = _tracking_execute

        sessions = {
            "sess-skip-001": _make_session_info(activity_extracted=True),
            "sess-skip-002": _make_session_info(activity_extracted=False),
            "sess-skip-003": _make_session_info(activity_extracted=True),
        }

        mock_executor, originals = _setup_agent_manager(
            session_map=sessions,
            da_hook=da_hook,
        )
        try:
            async def _build_ctx(sid, info):
                return _make_hook_context(session_id=sid)

            with patch.object(
                agent_manager,
                "_build_hook_context",
                side_effect=_build_ctx,
            ):
                await agent_manager.disconnect_all()

            # Only sess-skip-002 should have DA executed
            assert "sess-skip-002" in da_executed_sessions
            assert "sess-skip-001" not in da_executed_sessions
            assert "sess-skip-003" not in da_executed_sessions
        finally:
            _restore_agent_manager(originals)

    @pytest.mark.asyncio
    async def test_fire_receives_skip_hooks_for_da(self):
        """fire() is called with skip_hooks=["daily_activity_extraction"]
        for all sessions (DA is handled inline, not via executor)."""
        from core.agent_manager import agent_manager

        da_hook = MagicMock()
        da_hook.name = "daily_activity_extraction"
        da_hook.execute = AsyncMock(return_value=None)

        sessions = {
            "sess-fire-001": _make_session_info(activity_extracted=False),
            "sess-fire-002": _make_session_info(activity_extracted=True),
        }

        mock_executor, originals = _setup_agent_manager(
            session_map=sessions,
            da_hook=da_hook,
        )
        try:
            async def _build_ctx(sid, info):
                return _make_hook_context(session_id=sid)

            with patch.object(
                agent_manager,
                "_build_hook_context",
                side_effect=_build_ctx,
            ):
                await agent_manager.disconnect_all()

            # Every fire() call should have skip_hooks=["daily_activity_extraction"]
            assert mock_executor.fire.call_count == 2
            for call in mock_executor.fire.call_args_list:
                _, kwargs = call
                assert kwargs.get("skip_hooks") == ["daily_activity_extraction"], (
                    f"Expected skip_hooks=['daily_activity_extraction'], "
                    f"got: {kwargs}"
                )
        finally:
            _restore_agent_manager(originals)
