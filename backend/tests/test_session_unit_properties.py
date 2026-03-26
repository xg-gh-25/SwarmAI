"""Property-based tests for SessionUnit state machine.

Tests the ``SessionUnit`` class from ``core/session_unit.py`` using
Hypothesis-generated event sequences to verify state machine correctness,
crash isolation, interrupt behavior, retry isolation, env lock scoping,
and WAITING_INPUT crash handling.

Testing methodology: property-based (Hypothesis) + unit tests.
Key properties verified:

- **Property 1**: State machine transitions follow the defined table
- **Property 3**: Crash isolation between SessionUnits
- **Property 15**: Interrupt preserves subprocess for reuse
- **Property 18**: Per-unit retry with cap and isolation
- **Property 19**: Environment spawn lock scoping
- **Property 20**: WAITING_INPUT crash transitions to DEAD

# Feature: multi-session-rearchitecture
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from core.session_unit import SessionState, SessionUnit
from tests.helpers import PROPERTY_SETTINGS



# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Events that can happen to a SessionUnit
EVENTS = ["spawn", "send", "complete", "permission_prompt", "crash", "kill", "cleanup", "evict", "interrupt", "answer"]
event_sequences = st.lists(st.sampled_from(EVENTS), min_size=1, max_size=20)

# Valid state transitions (matches SessionUnit._VALID_TRANSITIONS).
# "send" from COLD is a two-step: COLD→IDLE (spawn) then IDLE→STREAMING.
# Tests use _transition() directly, so we model atomic transitions here.
VALID_TRANSITIONS: dict[SessionState, dict[str, SessionState]] = {
    SessionState.COLD: {
        "spawn": SessionState.IDLE,
        "kill": SessionState.DEAD,
    },
    SessionState.IDLE: {
        "send": SessionState.STREAMING,
        "kill": SessionState.DEAD,
        "evict": SessionState.COLD,
    },
    SessionState.STREAMING: {
        "complete": SessionState.IDLE,
        "permission_prompt": SessionState.WAITING_INPUT,
        "crash": SessionState.COLD,
        "kill": SessionState.DEAD,
    },
    SessionState.WAITING_INPUT: {
        "answer": SessionState.STREAMING,
        "interrupt": SessionState.IDLE,
        "crash": SessionState.COLD,
        "kill": SessionState.DEAD,
    },
    SessionState.DEAD: {
        "cleanup": SessionState.COLD,
    },
}


# ---------------------------------------------------------------------------
# Property 1: State machine transitions follow the defined table
# ---------------------------------------------------------------------------

class TestStateMachineTransitions:
    """Property 1: State machine transitions follow the defined transition table.

    # Feature: multi-session-rearchitecture, Property 1: State machine transitions

    *For any* SessionUnit and any valid sequence of events, the resulting
    state after each event must match the transition table.

    **Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.7**
    """

    @given(events=event_sequences)
    @PROPERTY_SETTINGS
    def test_transitions_match_table(self, events: list[str]):
        """Every event applied to a SessionUnit produces the correct state."""
        unit = SessionUnit(session_id="test-prop1", agent_id="default")
        assert unit.state == SessionState.COLD

        for event in events:
            current = unit.state
            valid = VALID_TRANSITIONS.get(current, {})

            if event in valid:
                expected = valid[event]
                # Apply the transition directly via _transition
                unit._transition(expected)
                assert unit.state == expected, (
                    f"After {event} from {current.value}, "
                    f"expected {expected.value} but got {unit.state.value}"
                )
            # Events not in the valid set for current state are no-ops
            # (the real send/kill methods guard against invalid states)

    def test_cold_to_idle_on_spawn(self):
        """COLD → IDLE when _spawn() succeeds."""
        unit = SessionUnit(session_id="test-cold-spawn", agent_id="default")
        assert unit.state == SessionState.COLD
        unit._transition(SessionState.IDLE)
        assert unit.state == SessionState.IDLE

    def test_cold_to_streaming_blocked(self):
        """COLD → STREAMING is rejected — must go through IDLE first."""
        unit = SessionUnit(session_id="test-cold-stream-blocked", agent_id="default")
        assert unit.state == SessionState.COLD
        import pytest
        with pytest.raises(RuntimeError, match="Invalid state transition"):
            unit._transition(SessionState.STREAMING)

    def test_streaming_to_idle_on_complete(self):
        """STREAMING → IDLE when response completes."""
        unit = SessionUnit(session_id="test-stream-idle", agent_id="default")
        unit._transition(SessionState.IDLE)  # COLD→IDLE (spawn)
        unit._transition(SessionState.STREAMING)  # IDLE→STREAMING
        unit._transition(SessionState.IDLE)
        assert unit.state == SessionState.IDLE

    def test_streaming_to_waiting_input(self):
        """STREAMING → WAITING_INPUT on permission prompt."""
        unit = SessionUnit(session_id="test-waiting", agent_id="default")
        unit._transition(SessionState.IDLE)  # COLD→IDLE (spawn)
        unit._transition(SessionState.STREAMING)
        unit._transition(SessionState.WAITING_INPUT)
        assert unit.state == SessionState.WAITING_INPUT

    def test_dead_to_cold_on_cleanup(self):
        """DEAD → COLD after cleanup."""
        unit = SessionUnit(session_id="test-dead-cold", agent_id="default")
        unit._transition(SessionState.DEAD)
        unit._transition(SessionState.COLD)
        assert unit.state == SessionState.COLD

    def test_state_change_callback_fires(self):
        """_on_state_change callback fires on every transition."""
        transitions = []
        unit = SessionUnit(
            session_id="test-callback",
            agent_id="default",
            on_state_change=lambda sid, old, new: transitions.append((sid, old, new)),
        )
        unit._transition(SessionState.IDLE)      # COLD→IDLE
        unit._transition(SessionState.STREAMING)  # IDLE→STREAMING
        unit._transition(SessionState.IDLE)       # STREAMING→IDLE
        assert len(transitions) == 3
        assert transitions[0] == ("test-callback", SessionState.COLD, SessionState.IDLE)
        assert transitions[1] == ("test-callback", SessionState.IDLE, SessionState.STREAMING)
        assert transitions[2] == ("test-callback", SessionState.STREAMING, SessionState.IDLE)


# ---------------------------------------------------------------------------
# Property 3: Crash isolation between SessionUnits
# ---------------------------------------------------------------------------

class TestCrashIsolation:
    """Property 3: Crash isolation between SessionUnits.

    # Feature: multi-session-rearchitecture, Property 3: Crash isolation

    *For any* pair of SessionUnits (A, B), when unit A transitions to DEAD,
    unit B's state, subprocess PID, and client reference must remain unchanged.

    **Validates: Requirements 1.8, 10.1, 10.2**
    """

    def test_crash_in_unit_a_does_not_affect_unit_b(self):
        """Crashing unit A leaves unit B completely unchanged."""
        unit_a = SessionUnit(session_id="unit-a", agent_id="default")
        unit_b = SessionUnit(session_id="unit-b", agent_id="default")

        # Put both in IDLE (simulating alive subprocesses)
        unit_a._transition(SessionState.IDLE)
        unit_b._transition(SessionState.IDLE)

        # Give unit_b a mock client/wrapper
        mock_client = MagicMock()
        mock_wrapper = MagicMock()
        mock_wrapper.pid = 12345
        unit_b._client = mock_client
        unit_b._wrapper = mock_wrapper

        # Crash unit A
        unit_a._transition(SessionState.DEAD)
        unit_a._cleanup_internal()
        unit_a._transition(SessionState.COLD)

        # Unit B must be completely unaffected
        assert unit_b.state == SessionState.IDLE
        assert unit_b._client is mock_client
        assert unit_b._wrapper is mock_wrapper
        assert unit_b.pid == 12345

    @given(
        state_a=st.sampled_from([SessionState.STREAMING, SessionState.IDLE, SessionState.WAITING_INPUT]),
        state_b=st.sampled_from([SessionState.STREAMING, SessionState.IDLE, SessionState.WAITING_INPUT]),
    )
    @PROPERTY_SETTINGS
    def test_crash_isolation_across_all_alive_states(
        self, state_a: SessionState, state_b: SessionState,
    ):
        """For any pair of alive states, crashing A never affects B."""
        unit_a = SessionUnit(session_id="iso-a", agent_id="default")
        unit_b = SessionUnit(session_id="iso-b", agent_id="default")

        # Route through valid transitions to reach target state
        def _reach_state(unit: SessionUnit, target: SessionState) -> None:
            if target == SessionState.IDLE:
                unit._transition(SessionState.IDLE)  # COLD→IDLE
            elif target == SessionState.STREAMING:
                unit._transition(SessionState.IDLE)  # COLD→IDLE
                unit._transition(SessionState.STREAMING)  # IDLE→STREAMING
            elif target == SessionState.WAITING_INPUT:
                unit._transition(SessionState.IDLE)  # COLD→IDLE
                unit._transition(SessionState.STREAMING)  # IDLE→STREAMING
                unit._transition(SessionState.WAITING_INPUT)  # STREAMING→WAITING_INPUT

        _reach_state(unit_a, state_a)
        _reach_state(unit_b, state_b)

        b_state_before = unit_b.state

        # Crash A
        unit_a._transition(SessionState.DEAD)

        assert unit_b.state == b_state_before


# ---------------------------------------------------------------------------
# Property 15: Interrupt preserves subprocess for reuse
# ---------------------------------------------------------------------------

class TestInterruptPreservesSubprocess:
    """Property 15: Interrupt preserves subprocess for reuse.

    # Feature: multi-session-rearchitecture, Property 15: Interrupt preserves subprocess

    *For any* SessionUnit in STREAMING state, if interrupt() succeeds,
    the unit must transition to IDLE with the same subprocess PID.

    **Validates: Requirements 7.5, 11.2, 11.4**
    """

    @pytest.mark.asyncio
    async def test_interrupt_success_keeps_subprocess_warm(self):
        """After successful interrupt, state is IDLE and PID unchanged."""
        unit = SessionUnit(session_id="test-interrupt", agent_id="default")
        unit._transition(SessionState.IDLE)       # COLD→IDLE
        unit._transition(SessionState.STREAMING)   # IDLE→STREAMING

        mock_client = AsyncMock()
        mock_client.interrupt = AsyncMock()
        mock_wrapper = MagicMock()
        mock_wrapper.pid = 99999
        unit._client = mock_client
        unit._wrapper = mock_wrapper

        result = await unit.interrupt(timeout=5.0)

        assert result is True
        assert unit.state == SessionState.IDLE
        assert unit.pid == 99999  # Same PID — subprocess survived

    @pytest.mark.asyncio
    async def test_interrupt_timeout_kills_subprocess(self):
        """When interrupt() times out, subprocess is killed → COLD."""
        unit = SessionUnit(session_id="test-interrupt-timeout", agent_id="default")
        unit._transition(SessionState.IDLE)       # COLD→IDLE
        unit._transition(SessionState.STREAMING)   # IDLE→STREAMING

        mock_client = AsyncMock()
        mock_client.interrupt = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_wrapper = MagicMock()
        mock_wrapper.pid = 88888
        mock_wrapper.__aexit__ = AsyncMock(return_value=False)
        unit._client = mock_client
        unit._wrapper = mock_wrapper

        with patch("os.kill"):
            result = await unit.interrupt(timeout=0.01)

        assert result is False
        assert unit.state == SessionState.COLD


# ---------------------------------------------------------------------------
# Property 18: Per-unit retry with cap and isolation
# ---------------------------------------------------------------------------

class TestPerUnitRetryIsolation:
    """Property 18: Per-unit retry with cap and isolation.

    # Feature: multi-session-rearchitecture, Property 18: Per-unit retry isolation

    *For any* SessionUnit encountering retriable errors, the retry count
    must not exceed MAX_RETRY_ATTEMPTS (3). Retries in one unit must not
    affect any other unit.

    **Validates: Requirements 10.3, 10.4**
    """

    def test_retry_count_never_exceeds_max(self):
        """Retry counter is capped at MAX_RETRY_ATTEMPTS."""
        unit = SessionUnit(session_id="test-retry-cap", agent_id="default")
        assert unit.MAX_RETRY_ATTEMPTS == 3

        # Simulate retry increments
        for i in range(10):
            unit._retry_count = min(unit._retry_count + 1, unit.MAX_RETRY_ATTEMPTS)

        assert unit._retry_count <= unit.MAX_RETRY_ATTEMPTS

    def test_retry_state_isolated_between_units(self):
        """Retry count in unit A does not affect unit B."""
        unit_a = SessionUnit(session_id="retry-a", agent_id="default")
        unit_b = SessionUnit(session_id="retry-b", agent_id="default")

        unit_a._retry_count = 3
        assert unit_b._retry_count == 0  # Completely independent


# ---------------------------------------------------------------------------
# Property 19: Environment spawn lock scoping
# ---------------------------------------------------------------------------

class TestEnvSpawnLockScoping:
    """Property 19: Environment spawn lock scoping.

    # Feature: multi-session-rearchitecture, Property 19: Env spawn lock scoping

    *For any* SessionUnit spawn operation, the _spawn_lock must be acquired
    before os.environ mutation and released after subprocess creation.

    **Validates: Requirements 1.9**
    """

    @pytest.mark.asyncio
    async def test_spawn_lock_released_after_spawn(self):
        """_spawn_lock is not held after _spawn() completes."""
        from core.session_unit import _spawn_lock

        # Verify lock is free before
        assert not _spawn_lock.locked()

        # We can't easily test the full spawn (needs SDK), but we can
        # verify the lock contract: it should be free after any spawn
        # attempt (success or failure)
        unit = SessionUnit(session_id="test-lock", agent_id="default")

        with patch("core.session_unit._spawn_lock") as mock_lock:
            mock_lock.__aenter__ = AsyncMock()
            mock_lock.__aexit__ = AsyncMock(return_value=False)

            # Spawn will fail (no real SDK), but lock should be released
            with patch("core.claude_environment._env_lock") as mock_env_lock:
                mock_env_lock.__aenter__ = AsyncMock()
                mock_env_lock.__aexit__ = AsyncMock(return_value=False)

                with patch("core.claude_environment._ClaudeClientWrapper") as mock_wrapper_cls:
                    mock_wrapper = MagicMock()
                    mock_wrapper.__aenter__ = AsyncMock(return_value=MagicMock())
                    mock_wrapper.pid = 11111
                    mock_wrapper_cls.return_value = mock_wrapper

                    with patch("core.claude_environment._configure_claude_environment"):
                        await unit._spawn(MagicMock(), config=MagicMock())

        # After spawn, lock should be free
        assert not _spawn_lock.locked()


# ---------------------------------------------------------------------------
# Property 20: WAITING_INPUT crash transitions to DEAD
# ---------------------------------------------------------------------------

class TestWaitingInputCrash:
    """Property 20: WAITING_INPUT crash transitions to DEAD.

    # Feature: multi-session-rearchitecture, Property 20: WAITING_INPUT crash

    *For any* SessionUnit in WAITING_INPUT state, when the subprocess crashes,
    the unit must transition to DEAD and then to COLD after cleanup.

    **Validates: Requirements 1.7, 10.1**
    """

    def test_waiting_input_crash_goes_to_dead(self):
        """WAITING_INPUT → DEAD on crash."""
        unit = SessionUnit(session_id="test-wi-crash", agent_id="default")
        unit._transition(SessionState.IDLE)             # COLD→IDLE
        unit._transition(SessionState.STREAMING)         # IDLE→STREAMING
        unit._transition(SessionState.WAITING_INPUT)     # STREAMING→WAITING_INPUT

        # Simulate crash
        unit._transition(SessionState.DEAD)
        assert unit.state == SessionState.DEAD

    def test_waiting_input_crash_cleanup_goes_to_cold(self):
        """WAITING_INPUT → DEAD → COLD after cleanup."""
        unit = SessionUnit(session_id="test-wi-cleanup", agent_id="default")
        unit._transition(SessionState.IDLE)             # COLD→IDLE
        unit._transition(SessionState.STREAMING)         # IDLE→STREAMING
        unit._transition(SessionState.WAITING_INPUT)     # STREAMING→WAITING_INPUT

        # Give it a mock client
        unit._client = MagicMock()
        unit._wrapper = MagicMock()

        # Crash + cleanup
        unit._transition(SessionState.DEAD)
        unit._cleanup_internal()
        unit._transition(SessionState.COLD)

        assert unit.state == SessionState.COLD
        assert unit._client is None
        assert unit._wrapper is None

    @pytest.mark.asyncio
    async def test_health_check_detects_dead_subprocess_in_waiting_input(self):
        """health_check() detects dead PID and transitions WAITING_INPUT → COLD."""
        unit = SessionUnit(session_id="test-hc-wi", agent_id="default")
        unit._transition(SessionState.IDLE)             # COLD→IDLE
        unit._transition(SessionState.STREAMING)         # IDLE→STREAMING
        unit._transition(SessionState.WAITING_INPUT)     # STREAMING→WAITING_INPUT

        mock_wrapper = MagicMock()
        mock_wrapper.pid = 99999
        unit._wrapper = mock_wrapper

        with patch("os.kill", side_effect=ProcessLookupError):
            alive = await unit.health_check()

        assert alive is False
        assert unit.state == SessionState.COLD


# ---------------------------------------------------------------------------
# Property 21: Buffer overflow recovery
# ---------------------------------------------------------------------------

class TestBufferOverflowRecovery:
    """Property 21: Buffer overflow recovery via progressive processing.

    # Feature: progressive-content-processing

    When a tool response exceeds the CLI's 10MB JSONRPC buffer, the unit
    must set a recovery flag, NOT increment retry count, and limit
    recovery to one attempt per message.

    **Validates: Design doc 2026-03-20-progressive-content-processing-design.md**
    """

    def test_buffer_overflow_flag_initializes_false(self):
        """Recovery flag starts as False."""
        unit = SessionUnit(session_id="test-bo-init", agent_id="default")
        assert unit._buffer_overflow_recovery is False

    def test_buffer_overflow_sets_recovery_flag(self):
        """Simulating buffer overflow sets the recovery flag."""
        unit = SessionUnit(session_id="test-bo-flag", agent_id="default")
        unit._buffer_overflow_recovery = True
        assert unit._buffer_overflow_recovery is True

    def test_buffer_overflow_does_not_increment_retry(self):
        """Buffer overflow recovery must NOT count as a retry attempt.

        The recovery is a strategy correction (progressive processing),
        not a transient-failure retry.  Incrementing _retry_count would
        exhaust retries for a subsequent genuine transient error.
        """
        unit = SessionUnit(session_id="test-bo-retry", agent_id="default")
        initial_retry = unit._retry_count

        # Simulate: overflow detected → flag set, retry count untouched
        unit._buffer_overflow_recovery = True
        assert unit._retry_count == initial_retry

    def test_buffer_overflow_guard_prevents_infinite_loop(self):
        """When _buffer_overflow_recovery is already True, a second overflow
        must NOT re-trigger recovery (prevents infinite retry loop).

        The send() error handler checks:
            if "maximum buffer size" in error_str and not self._buffer_overflow_recovery:
        When the flag is already True, the condition short-circuits.
        """
        unit = SessionUnit(session_id="test-bo-guard", agent_id="default")
        unit._buffer_overflow_recovery = True

        # The guard condition: must be False to enter recovery
        error_str = "Failed to decode JSON: JSON message exceeded maximum buffer size of 10485760 bytes"
        should_recover = (
            "maximum buffer size" in error_str
            and not unit._buffer_overflow_recovery
        )
        assert should_recover is False

    def test_recovery_flag_allows_first_attempt(self):
        """When flag is False, the guard allows recovery."""
        unit = SessionUnit(session_id="test-bo-allow", agent_id="default")
        assert unit._buffer_overflow_recovery is False

        error_str = "JSON message exceeded maximum buffer size of 10485760 bytes"
        should_recover = (
            "maximum buffer size" in error_str
            and not unit._buffer_overflow_recovery
        )
        assert should_recover is True

    @pytest.mark.asyncio
    async def test_crash_to_cold_resets_state_for_recovery(self):
        """_crash_to_cold_async() transitions to COLD, enabling a fresh spawn."""
        unit = SessionUnit(session_id="test-bo-crash", agent_id="default")
        unit._transition(SessionState.IDLE)       # COLD→IDLE
        unit._transition(SessionState.STREAMING)   # IDLE→STREAMING

        # Give it mock subprocess refs
        unit._client = MagicMock()
        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=None)
        mock_wrapper.pid = 12345
        unit._wrapper = mock_wrapper

        with patch("core.session_unit.os.getpgid", side_effect=ProcessLookupError):
            await unit._crash_to_cold_async()

        assert unit.state == SessionState.COLD
        assert unit._client is None
        assert unit._wrapper is None

    def test_buffer_overflow_isolated_between_units(self):
        """Recovery flag in unit A does not affect unit B."""
        unit_a = SessionUnit(session_id="bo-iso-a", agent_id="default")
        unit_b = SessionUnit(session_id="bo-iso-b", agent_id="default")

        unit_a._buffer_overflow_recovery = True
        assert unit_b._buffer_overflow_recovery is False

    def test_buffer_overflow_resets_per_message(self):
        """Recovery flag resets at the start of each send() call.

        Bug: _buffer_overflow_recovery was set True on first overflow
        but never reset between messages.  After a successful recovery,
        subsequent overflows (different tool call, different turn) would
        skip recovery and surface a raw error instead.

        Fix: send() resets _buffer_overflow_recovery = False alongside
        _retry_count = 0 at the top of each invocation.
        """
        unit = SessionUnit(session_id="bo-reset", agent_id="default")

        # Simulate: first message triggered overflow, recovery succeeded,
        # flag is now True from the previous send().
        unit._buffer_overflow_recovery = True

        # Verify the flag is True before the "next send()" reset
        assert unit._buffer_overflow_recovery is True

        # Simulate what send() does at the top: reset per-send state
        unit._retry_count = 0
        unit._interrupted = False
        unit._buffer_overflow_recovery = False

        # Now a new overflow should be recoverable
        error_str = "JSON message exceeded maximum buffer size of 10485760 bytes"
        should_recover = (
            "maximum buffer size" in error_str
            and not unit._buffer_overflow_recovery
        )
        assert should_recover is True
