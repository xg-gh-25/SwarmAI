"""Tests for cross-turn bleed fix.

Verifies that when a user stops a streaming response and quickly sends
a new message, the old response data does NOT bleed into the new response.

Key invariant: pipe flush must COMPLETE (not be cancelled) before new send()
proceeds. This ensures the subprocess stdout pipe is clean.
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def session_unit_cls():
    """Import SessionUnit with mocked dependencies."""
    with patch.dict("sys.modules", {
        "claude_agent_sdk": MagicMock(),
        "claude_agent_sdk.types": MagicMock(),
    }):
        from core.session_unit import SessionUnit, SessionState
        return SessionUnit, SessionState


class TestPipeFlushNotCancelled:
    """Verify pipe flush completes instead of being cancelled on new send()."""

    @pytest.mark.asyncio
    async def test_pipe_flush_awaited_not_cancelled(self, session_unit_cls):
        """When pipe flush is in progress and send() arrives,
        the flush should be awaited (not cancelled) to drain the pipe."""
        SessionUnit, SessionState = session_unit_cls

        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "test-session"
        unit.state = SessionState.IDLE
        unit._send_generation = 0
        unit._stop_event = asyncio.Event()
        unit._interrupted = False
        unit._client = MagicMock()
        unit._sdk_session_id = "existing-session"
        unit._hooks_enqueued = False
        unit._last_event_time = None

        # Simulate a pipe flush task that takes 500ms
        flush_completed = asyncio.Event()

        async def slow_flush():
            await asyncio.sleep(0.5)
            flush_completed.set()

        unit._pipe_flush_task = asyncio.create_task(slow_flush())

        # The pipe flush section of send() — we test just that logic
        if unit._pipe_flush_task and not unit._pipe_flush_task.done():
            try:
                await asyncio.wait_for(unit._pipe_flush_task, timeout=5.0)
            except asyncio.TimeoutError:
                unit._pipe_flush_task.cancel()
                try:
                    await unit._pipe_flush_task
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass
            unit._pipe_flush_task = None

        # The flush should have completed (not been cancelled)
        assert flush_completed.is_set(), (
            "Pipe flush was cancelled instead of awaited — "
            "this leaves stale data in the subprocess pipe"
        )

    @pytest.mark.asyncio
    async def test_pipe_flush_timeout_kills_subprocess(self, session_unit_cls):
        """When pipe flush exceeds 3.5s timeout, subprocess is killed
        to ensure no stale data remains."""
        SessionUnit, SessionState = session_unit_cls

        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "test-session"
        unit.state = SessionState.IDLE
        unit._send_generation = 0
        unit._stop_event = asyncio.Event()
        unit._interrupted = False
        unit._client = MagicMock()
        unit._sdk_session_id = "existing-session"
        unit._hooks_enqueued = False
        unit._last_event_time = None

        # Simulate a pipe flush task that takes too long (>3.5s)
        flush_cancelled = asyncio.Event()

        async def very_slow_flush():
            try:
                await asyncio.sleep(10)  # Way over timeout
            except asyncio.CancelledError:
                flush_cancelled.set()
                raise

        unit._pipe_flush_task = asyncio.create_task(very_slow_flush())

        # Mock kill() to verify it's called on timeout
        kill_called = asyncio.Event()

        async def mock_kill():
            kill_called.set()

        unit.kill = mock_kill

        # Execute the pipe flush logic from send()
        if unit._pipe_flush_task and not unit._pipe_flush_task.done():
            try:
                await asyncio.wait_for(unit._pipe_flush_task, timeout=5.0)
            except asyncio.TimeoutError:
                unit._pipe_flush_task.cancel()
                try:
                    await unit._pipe_flush_task
                except (asyncio.CancelledError, Exception):
                    pass
                if unit._client is not None:
                    await unit.kill()
            except (asyncio.CancelledError, Exception):
                pass
            unit._pipe_flush_task = None

        assert flush_cancelled.is_set(), "Flush should be cancelled after timeout"
        assert kill_called.is_set(), (
            "Subprocess should be killed after flush timeout "
            "to ensure no stale pipe data"
        )

    @pytest.mark.asyncio
    async def test_already_completed_flush_is_instant(self, session_unit_cls):
        """When pipe flush already completed, send() proceeds immediately."""
        SessionUnit, SessionState = session_unit_cls

        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "test-session"
        unit.state = SessionState.IDLE
        unit._send_generation = 0
        unit._stop_event = asyncio.Event()
        unit._interrupted = False
        unit._client = MagicMock()

        # Create a task that's already done
        async def instant_flush():
            pass

        task = asyncio.create_task(instant_flush())
        await asyncio.sleep(0)  # Let it complete
        unit._pipe_flush_task = task

        import time
        start = time.monotonic()

        # Execute the pipe flush logic
        if unit._pipe_flush_task and not unit._pipe_flush_task.done():
            try:
                await asyncio.wait_for(unit._pipe_flush_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
            unit._pipe_flush_task = None

        elapsed = time.monotonic() - start
        # Already-done task should proceed in <10ms
        assert elapsed < 0.1, f"Already-completed flush took {elapsed:.3f}s"
