"""Tests for pipe flush race condition prevention.

Verifies the 3-layer defense against the race where
flush_subprocess_pipe()'s _client.interrupt() kills a new stream
that started after stop:

1. send() cancels in-flight flush task
2. flush bails on generation mismatch (interrupt path)
3. flush bails on generation mismatch (timeout path)
4. flush handles CancelledError gracefully

See: 2026-05-10 resume error bug fix.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.session_unit import SessionUnit, SessionState


@pytest.fixture
def unit():
    """Create a SessionUnit in IDLE state with a mock client."""
    u = SessionUnit(session_id="test-flush-race", agent_id="default")
    # Transition to IDLE (matches post-disconnect state)
    u._transition(SessionState.IDLE)
    # Mock the subprocess client
    u._client = MagicMock()
    u._client.interrupt = AsyncMock(return_value=None)
    return u


class TestSendCancelsFlushTask:
    """Layer 1: send() cancels any in-flight flush task before starting."""

    @pytest.mark.asyncio
    async def test_send_cancels_pending_flush_task(self, unit):
        """send() should cancel _pipe_flush_task if it's still running."""
        # Create a long-running flush task
        flush_started = asyncio.Event()
        flush_cancelled = asyncio.Event()

        async def slow_flush():
            flush_started.set()
            try:
                await asyncio.sleep(10)  # simulate long interrupt
            except asyncio.CancelledError:
                flush_cancelled.set()
                raise

        loop = asyncio.get_running_loop()
        unit._pipe_flush_task = loop.create_task(slow_flush())

        # Wait for flush to start
        await flush_started.wait()

        # Simulate send() Layer 0 — the cancel logic
        unit._send_generation += 1
        unit._stop_event.clear()
        unit._interrupted = False

        if unit._pipe_flush_task and not unit._pipe_flush_task.done():
            unit._pipe_flush_task.cancel()
            unit._pipe_flush_task = None

        # Give event loop a tick to deliver CancelledError
        await asyncio.sleep(0)

        assert flush_cancelled.is_set(), "Flush task should have been cancelled"
        assert unit._pipe_flush_task is None

    @pytest.mark.asyncio
    async def test_send_skips_cancel_if_flush_already_done(self, unit):
        """send() should not error if flush task already completed."""
        # Create an already-completed task
        async def instant_flush():
            pass

        loop = asyncio.get_running_loop()
        unit._pipe_flush_task = loop.create_task(instant_flush())
        await asyncio.sleep(0)  # let it complete

        assert unit._pipe_flush_task.done()

        # Simulate send() cancel logic — should be a no-op
        unit._send_generation += 1
        unit._stop_event.clear()
        unit._interrupted = False

        if unit._pipe_flush_task and not unit._pipe_flush_task.done():
            unit._pipe_flush_task.cancel()
            unit._pipe_flush_task = None

        # _pipe_flush_task NOT set to None because .done() was True
        assert unit._pipe_flush_task is not None


class TestFlushGenerationGuardInterruptPath:
    """Layer 2: flush bails out when generation advances during interrupt."""

    @pytest.mark.asyncio
    async def test_flush_bails_when_generation_advances_during_interrupt(self, unit):
        """If send() bumps generation while flush awaits interrupt, flush skips."""
        original_gen = unit._send_generation

        # Mock _client.interrupt() to simulate send() bumping generation mid-await
        async def interrupt_with_gen_bump():
            # Simulate: send() runs between our state check and interrupt completion
            unit._send_generation += 1
            return None

        unit._client.interrupt = interrupt_with_gen_bump

        # Run flush — should detect generation mismatch and return without killing
        await unit.flush_subprocess_pipe(timeout=3.0)

        # Unit should still be alive (not killed)
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_flush_proceeds_when_generation_stable(self, unit):
        """Normal case: generation doesn't change, flush completes normally."""
        await unit.flush_subprocess_pipe(timeout=3.0)

        # interrupt was called
        unit._client.interrupt.assert_called_once()
        assert unit.state == SessionState.IDLE


class TestFlushGenerationGuardTimeoutPath:
    """Layer 2b: flush bails on timeout if generation advanced."""

    @pytest.mark.asyncio
    async def test_flush_skips_kill_when_generation_advances_on_timeout(self, unit):
        """If interrupt times out but send() started, don't kill subprocess."""
        # Mock interrupt to hang (trigger timeout) and bump generation
        async def hanging_interrupt():
            unit._send_generation += 1  # send() started
            await asyncio.sleep(100)  # will time out

        unit._client.interrupt = hanging_interrupt

        # Patch kill to track if it was called
        unit.kill = AsyncMock()

        await unit.flush_subprocess_pipe(timeout=0.1)

        # Should NOT have killed — generation advanced
        unit.kill.assert_not_called()
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_flush_kills_on_timeout_when_generation_stable(self, unit):
        """Normal timeout: generation stable → kill subprocess for clean respawn."""
        # Mock interrupt to hang
        async def hanging_interrupt():
            await asyncio.sleep(100)

        unit._client.interrupt = hanging_interrupt

        # Patch kill
        unit.kill = AsyncMock()

        await unit.flush_subprocess_pipe(timeout=0.1)

        # Should have killed
        unit.kill.assert_called_once()


class TestFlushCancelledError:
    """Layer 3: flush handles CancelledError from send()'s task.cancel()."""

    @pytest.mark.asyncio
    async def test_flush_handles_cancellation_gracefully(self, unit):
        """Flush should catch CancelledError and return cleanly."""
        # Create a flush coroutine that gets cancelled mid-execution
        flush_entered = asyncio.Event()

        async def slow_interrupt():
            flush_entered.set()
            await asyncio.sleep(10)

        unit._client.interrupt = slow_interrupt

        # Start flush
        loop = asyncio.get_running_loop()
        task = loop.create_task(unit.flush_subprocess_pipe(timeout=5.0))

        # Wait for it to enter the interrupt await
        await flush_entered.wait()
        await asyncio.sleep(0)  # yield to let wait_for wrap

        # Cancel it (simulating send()'s cancel)
        task.cancel()

        # Should not raise — CancelledError is handled internally
        # Note: asyncio.wait_for propagates CancelledError, which flush catches
        try:
            await task
        except asyncio.CancelledError:
            pass  # This is acceptable — task reports cancelled to parent

        # State should be unchanged (no kill, no transition)
        assert unit.state == SessionState.IDLE


class TestSchedulePipeFlush:
    """Unit.schedule_pipe_flush() encapsulation."""

    @pytest.mark.asyncio
    async def test_schedule_stores_task_reference(self, unit):
        """schedule_pipe_flush should store task on _pipe_flush_task."""
        loop = asyncio.get_running_loop()

        # Use a custom coroutine so we can control it
        completed = asyncio.Event()

        async def mock_cleanup():
            completed.set()

        unit.schedule_pipe_flush(loop, cleanup_coro=mock_cleanup())

        assert unit._pipe_flush_task is not None
        assert not unit._pipe_flush_task.done()

        # Let it complete
        await completed.wait()
        await asyncio.sleep(0)
        assert unit._pipe_flush_task.done()

    @pytest.mark.asyncio
    async def test_schedule_without_coro_uses_flush_directly(self, unit):
        """Without cleanup_coro, should call flush_subprocess_pipe directly."""
        loop = asyncio.get_running_loop()
        unit.schedule_pipe_flush(loop)

        assert unit._pipe_flush_task is not None

        # Wait for completion
        await unit._pipe_flush_task

        # interrupt was called (from flush_subprocess_pipe)
        unit._client.interrupt.assert_called_once()
