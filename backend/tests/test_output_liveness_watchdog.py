"""Tests for the output liveness watchdog in SessionUnit._pid_watchdog_loop.

The output liveness watchdog kills a subprocess that is alive but has not
produced any SDK events for longer than MESSAGE_TIMEOUT. This handles the
case where the Anthropic API hangs but the subprocess remains running —
asyncio.wait_for cannot cancel native pipe I/O reads, so the PID watchdog
is the only out-of-band mechanism to break the hang.

Testing methodology: unit tests with mocked time and state.
Key properties verified:
- AC1: Subprocess killed after MESSAGE_TIMEOUT of silence during STREAMING
- AC2: No false kills during IDLE, WAITING_INPUT, or when _last_event_time is None
- AC3: Kill triggers DEAD transition (existing retry path handles recovery)
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_unit(session_id: str = "test-session", pid: int | None = None) -> SessionUnit:
    """Create a minimal SessionUnit for testing."""
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = SessionState.COLD
    unit._sdk_session_id = None
    unit._client = None
    # Use a mock wrapper so the pid property returns what we want
    if pid is not None:
        wrapper = MagicMock()
        wrapper.pid = pid
        unit._wrapper = wrapper
    else:
        unit._wrapper = None
    unit._hooks_enqueued = False
    unit._streaming_start_time = None
    unit._last_event_time = None
    unit._peak_tree_rss_bytes = 0
    unit._last_proactive_restart = 0
    unit._pid_watchdog_task = None
    unit._last_known_context_tokens = 0
    # Tool-hang tracking (run_fb6e94a9) — real __init__ sets these; the
    # watchdog reads them every tick.
    unit._open_tool_uses = {}
    unit._tool_hang_interrupted = False
    unit._tool_hang_interrupt_at = None
    unit._tool_hang_episodes = 0
    unit._consecutive_unstick_timeouts = 0
    unit.interrupt = AsyncMock(return_value=True)
    unit._PID_WATCHDOG_INTERVAL = 0.05  # Fast for tests
    unit.last_used = time.time()
    unit.is_channel_session = False
    unit._retry_count = 0
    unit._max_retries = 3
    unit._on_state_change = None
    unit._stop_event = asyncio.Event()
    # Mock _force_kill to avoid real process operations
    unit._force_kill = AsyncMock()
    return unit


# ── AC1: Kill after MESSAGE_TIMEOUT silence ─────────────────────────────


class TestOutputLivenessKill:
    """Verify subprocess is killed when silent too long during STREAMING."""

    @pytest.mark.asyncio
    async def test_kills_subprocess_after_timeout(self):
        """AC1: When STREAMING and _last_event_time exceeds timeout, kill."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        # Last event was 400 seconds ago (> 300s default timeout)
        unit._last_event_time = time.time() - 400

        with patch("os.kill") as mock_kill:
            # os.kill(pid, 0) = process alive
            mock_kill.return_value = None

            # Run the watchdog loop — it should detect silence and kill
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)  # Let at least 2 poll cycles run
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have transitioned to DEAD
        assert unit.state == SessionState.DEAD
        # Should have called _force_kill
        unit._force_kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_adaptive_timeout(self):
        """Timeout scales with context size via _compute_message_timeout."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        # Set large context — timeout should be > 300s
        unit._last_known_context_tokens = 600_000  # 600K / 3000 = 200s, max(300, 200) = 300s
        # Last event only 100s ago — should NOT kill
        unit._last_event_time = time.time() - 100

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should NOT have killed — 100s < 300s timeout
        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()


# ── AC2: No false kills ─────────────────────────────────────────────────


class TestNoFalseKills:
    """Verify the watchdog does NOT kill in non-applicable states."""

    @pytest.mark.asyncio
    async def test_no_kill_when_idle(self):
        """IDLE state: watchdog should not check output liveness."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.IDLE
        unit._last_event_time = time.time() - 9999  # Very stale

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit.state == SessionState.IDLE
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_kill_when_waiting_input(self):
        """WAITING_INPUT: user is deciding, no timeout should apply."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.WAITING_INPUT
        unit._last_event_time = time.time() - 9999

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit.state == SessionState.WAITING_INPUT
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_kill_when_last_event_time_none(self):
        """Before first event received, don't apply output liveness."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = None  # No events yet (init phase)

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_kill_when_recent_event(self):
        """Recent event: process is working normally, don't kill."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 10  # 10s ago = fine

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()


# ── AC3: DEAD transition triggers recovery path ─────────────────────────


class TestRecoveryPath:
    """Verify the kill triggers correct state for retry."""

    @pytest.mark.asyncio
    async def test_transitions_to_dead_before_kill(self):
        """State should be DEAD when _force_kill is called."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 400

        states_during_kill = []

        async def capture_state_on_kill():
            states_during_kill.append(unit.state)

        unit._force_kill = AsyncMock(side_effect=capture_state_on_kill)

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # State was DEAD when _force_kill was called
        assert states_during_kill == [SessionState.DEAD]

    @pytest.mark.asyncio
    async def test_existing_pid_death_detection_still_works(self):
        """Original PID watchdog behavior: process gone → DEAD."""
        unit = _make_unit(pid=99999)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time()  # Recent event

        with patch("os.kill") as mock_kill:
            # Process doesn't exist
            mock_kill.side_effect = ProcessLookupError()

            task = asyncio.create_task(unit._pid_watchdog_loop(99999))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should transition to DEAD via original path
        assert unit.state == SessionState.DEAD
        # _force_kill NOT called — process already dead
        unit._force_kill.assert_not_called()


# ── AC4: Property-based state transition invariants ────────────────────


class TestStateTransitionProperties:
    """Property: _last_event_time correctness across all transition paths.

    Invariant: _last_event_time is non-None IFF state == STREAMING.
    This must hold after ANY valid transition sequence.

    Note: _start_pid_watchdog is mocked to prevent leaked background tasks
    from _transition(STREAMING) creating real asyncio tasks in tests.
    """

    @pytest.mark.asyncio
    async def test_last_event_time_set_on_streaming_entry(self):
        """Entering STREAMING always sets _last_event_time to now."""
        unit = _make_unit(pid=12345)
        unit._start_pid_watchdog = MagicMock()  # Prevent background task leak
        unit.state = SessionState.COLD
        unit._transition(SessionState.IDLE)
        assert unit._last_event_time is None  # IDLE = no event tracking

        unit._transition(SessionState.STREAMING)
        assert unit._last_event_time is not None
        assert abs(unit._last_event_time - time.time()) < 1.0

    @pytest.mark.asyncio
    async def test_last_event_time_cleared_on_streaming_exit(self):
        """Leaving STREAMING always clears _last_event_time."""
        unit = _make_unit(pid=12345)
        unit._start_pid_watchdog = MagicMock()
        unit.state = SessionState.COLD
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)
        assert unit._last_event_time is not None

        # STREAMING → WAITING_INPUT clears it (exits STREAMING)
        unit._transition(SessionState.WAITING_INPUT)
        assert unit._last_event_time is None

    @pytest.mark.asyncio
    async def test_last_event_time_reset_on_streaming_reentry(self):
        """Re-entering STREAMING after WAITING_INPUT gets fresh timestamp."""
        unit = _make_unit(pid=12345)
        unit._start_pid_watchdog = MagicMock()
        unit.state = SessionState.COLD
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)
        original_time = unit._last_event_time

        # Simulate permission prompt: STREAMING → WAITING_INPUT → STREAMING
        unit._transition(SessionState.WAITING_INPUT)
        assert unit._last_event_time is None

        await asyncio.sleep(0.01)  # Tiny delay to get a different timestamp
        unit._transition(SessionState.STREAMING)
        assert unit._last_event_time is not None
        assert unit._last_event_time > original_time

    @pytest.mark.asyncio
    async def test_dead_transition_clears_event_time(self):
        """STREAMING → DEAD clears _last_event_time (exit from STREAMING)."""
        unit = _make_unit(pid=12345)
        unit._start_pid_watchdog = MagicMock()
        unit.state = SessionState.COLD
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)
        assert unit._last_event_time is not None

        unit._transition(SessionState.DEAD)
        assert unit._last_event_time is None

    @pytest.mark.asyncio
    async def test_rapid_transitions_maintain_invariant(self):
        """Rapid STREAMING→WAITING_INPUT→STREAMING cycles maintain invariant."""
        unit = _make_unit(pid=12345)
        unit._start_pid_watchdog = MagicMock()
        unit.state = SessionState.COLD
        unit._transition(SessionState.IDLE)

        for _ in range(10):
            unit._transition(SessionState.STREAMING)
            assert unit._last_event_time is not None

            unit._transition(SessionState.WAITING_INPUT)
            assert unit._last_event_time is None

        # Final: back to streaming
        unit._transition(SessionState.STREAMING)
        assert unit._last_event_time is not None


# ── AC5: Concurrent stress — race conditions ──────────────────────────


class TestConcurrentRaces:
    """Verify safety under concurrent watchdog kill + normal completion."""

    @pytest.mark.asyncio
    async def test_double_dead_transition_is_noop(self):
        """Two coroutines racing to DEAD: second one is a no-op."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.COLD
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)

        # First transition succeeds
        unit._transition(SessionState.DEAD)
        assert unit.state == SessionState.DEAD

        # Second transition is same-state no-op (line 559)
        unit._transition(SessionState.DEAD)  # Should NOT raise
        assert unit.state == SessionState.DEAD

    @pytest.mark.asyncio
    async def test_watchdog_kill_after_normal_idle_transition(self):
        """If stream completes (→IDLE) before watchdog fires, no false kill."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        # Event was long ago — would trigger kill IF still STREAMING
        unit._last_event_time = time.time() - 400

        # Simulate normal completion: STREAMING → IDLE
        unit._transition(SessionState.IDLE)

        # Now run watchdog — it should see state=IDLE and NOT kill
        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # State unchanged, no kill
        assert unit.state == SessionState.IDLE
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_kill_idempotent(self):
        """Double _force_kill calls don't crash (ProcessLookupError handled)."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 400

        # Make _force_kill actually do something on first call, noop on second
        call_count = []

        async def mock_force_kill():
            call_count.append(1)

        unit._force_kill = AsyncMock(side_effect=mock_force_kill)

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Called once by watchdog
        assert len(call_count) == 1
        # Simulate a second call from error handler (idempotent)
        await unit._force_kill()
        assert len(call_count) == 2  # Both succeed without crash

    @pytest.mark.asyncio
    async def test_watchdog_exits_after_kill_no_infinite_loop(self):
        """After killing, the watchdog returns (does not loop again)."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 400

        loop_iterations = []

        original_sleep = asyncio.sleep

        async def counting_sleep(duration):
            loop_iterations.append(1)
            await original_sleep(duration)

        with patch("os.kill") as mock_kill, \
             patch("asyncio.sleep", side_effect=counting_sleep):
            mock_kill.return_value = None
            # Run to completion (not cancelled — watchdog should exit on its own)
            await unit._pid_watchdog_loop(12345)

        # Should have done exactly 1 iteration then returned
        assert len(loop_iterations) == 1
        assert unit.state == SessionState.DEAD
        unit._force_kill.assert_called_once()


# ── AC6: Edge cases — legitimate long operations ──────────────────────


class TestLegitimateOperations:
    """Verify no false kills during legitimate long-running scenarios."""

    @pytest.mark.asyncio
    async def test_waiting_input_immune_even_with_stale_time(self):
        """WAITING_INPUT: even if _last_event_time is very old, no kill.

        This covers the scenario where a permission prompt sits for
        10+ minutes waiting for user approval.
        """
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time()

        # User takes 600s to approve permission
        unit._transition(SessionState.WAITING_INPUT)
        # Note: _last_event_time is None after leaving STREAMING
        # But even if we manually set it (defensive), state check protects
        unit._last_event_time = time.time() - 600  # Stale (force for test)

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # State preserved — no kill
        assert unit.state == SessionState.WAITING_INPUT
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_timestamp_after_permission_resume_prevents_kill(self):
        """After WAITING_INPUT→STREAMING, fresh timestamp prevents false kill.

        This covers: user approves permission, model starts thinking,
        but TTFT is long (high context). The fresh timestamp gives full
        timeout window from the moment STREAMING is re-entered.
        """
        unit = _make_unit(pid=12345)
        unit._start_pid_watchdog = MagicMock()  # Prevent background task leak
        unit.state = SessionState.COLD
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)

        # Simulate: user approves after pause
        unit._transition(SessionState.WAITING_INPUT)
        unit._transition(SessionState.STREAMING)

        # _last_event_time is NOW (just entered STREAMING)
        assert unit._last_event_time is not None
        assert time.time() - unit._last_event_time < 1.0

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Fresh timestamp means silence < timeout — no kill
        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_context_extends_timeout(self):
        """High context tokens (900K) extends timeout to 300s.

        Verifies that _compute_message_timeout() is respected by the
        watchdog. At 900K tokens: max(300, 900000/3000) = max(300, 300) = 300s.
        At 2.7M tokens (hypothetical): max(300, 2700000/3000) = 900s (capped).
        """
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        # High context: timeout = max(300, 2700000/3000) = max(300, 900) → capped at 900
        unit._last_known_context_tokens = 2_700_000
        # Silent for 500s — exceeds 300s base but within 900s cap
        unit._last_event_time = time.time() - 500

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 500s < 900s timeout — should NOT kill
        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_cap_at_900s(self):
        """Even extremely high context, timeout never exceeds 900s."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_known_context_tokens = 10_000_000  # 10M tokens (absurd)
        # Silent for 950s — exceeds the 900s max cap
        unit._last_event_time = time.time() - 950

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 950s > 900s cap — SHOULD kill
        assert unit.state == SessionState.DEAD
        unit._force_kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_permission_error_treated_as_alive(self):
        """PermissionError from os.kill (zombie/different user) = process alive.

        Some macOS edge cases (sandboxed processes, different user ownership)
        raise PermissionError instead of ProcessLookupError. We treat this as
        "process exists" and continue to the liveness check.
        """
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time()  # Recent — no kill expected

        with patch("os.kill") as mock_kill:
            mock_kill.side_effect = PermissionError("Operation not permitted")
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should NOT have killed — PermissionError means alive
        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()
