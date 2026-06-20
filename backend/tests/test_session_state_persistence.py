"""Tests for session state persistence (Pipeline 1 of Session Stability Design).

Tests cover:
- AC1: Graceful restart → sessions resume via --resume (fast path)
- AC2: Crash restart (state file <60s old) → IDLE sessions recover fast
- AC3: State file >24hr → discarded
- AC4: Stale sdk_session_id fallback to cold resume
- AC5-6: Scoped heal immunity (turn_count<3)
- AC7: Only IDLE sessions persisted
- AC8: No regression in existing behavior
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Subsystem 2B: State Persistence ───


@pytest.fixture
def state_file(tmp_path):
    """Temporary state file path."""
    return tmp_path / "session_state.json"


@pytest.fixture
def mock_router(state_file):
    """Minimal mock router with session state persistence."""
    from core.session_router import SessionRouter

    router = MagicMock(spec=SessionRouter)
    router._units = {}
    router.SESSION_STATE_FILE = state_file
    return router


class TestStatePersistence:
    """AC1, AC2, AC7: Persist IDLE sessions, skip STREAMING/WAITING_INPUT."""

    def test_only_idle_sessions_persisted(self, state_file):
        """AC7: STREAMING and WAITING_INPUT sessions must NOT be in state file."""
        from core.session_unit import SessionState
        from core.session_state_persistence import persist_session_state

        # Create mock units in different states
        idle_unit = MagicMock()
        idle_unit.state = SessionState.IDLE
        idle_unit._sdk_session_id = "sdk-idle-123"
        idle_unit.session_id = "session-idle"
        idle_unit.last_used = time.time()
        idle_unit._health_sensor = MagicMock(turn_count=10)

        streaming_unit = MagicMock()
        streaming_unit.state = SessionState.STREAMING
        streaming_unit._sdk_session_id = "sdk-streaming-456"
        streaming_unit.session_id = "session-streaming"

        waiting_unit = MagicMock()
        waiting_unit.state = SessionState.WAITING_INPUT
        waiting_unit._sdk_session_id = "sdk-waiting-789"
        waiting_unit.session_id = "session-waiting"

        units = {
            "session-idle": idle_unit,
            "session-streaming": streaming_unit,
            "session-waiting": waiting_unit,
        }

        persist_session_state(units, state_file)

        state = json.loads(state_file.read_text())
        # Only IDLE session should be persisted
        assert "session-idle" in state
        assert "session-streaming" not in state
        assert "session-waiting" not in state
        assert state["session-idle"]["sdk_session_id"] == "sdk-idle-123"

    def test_state_file_includes_timestamp(self, state_file):
        """AC2: State file must have _persisted_at for staleness check."""
        from core.session_state_persistence import persist_session_state

        unit = MagicMock()
        unit.state = MagicMock(value="idle")
        unit.state.__eq__ = lambda self, other: str(self.value) == str(getattr(other, 'value', other))
        unit._sdk_session_id = "sdk-123"
        unit.session_id = "s1"
        unit.last_used = time.time()
        unit._health_sensor = MagicMock(turn_count=5)

        # Import the actual SessionState for comparison
        from core.session_unit import SessionState
        unit.state = SessionState.IDLE

        persist_session_state({"s1": unit}, state_file)

        state = json.loads(state_file.read_text())
        assert "_persisted_at" in state
        assert abs(state["_persisted_at"] - time.time()) < 5

    def test_stale_state_file_discarded(self, state_file):
        """AC3: State file older than 24hr must be discarded."""
        from core.session_state_persistence import restore_session_state

        # Write a state file from 25 hours ago
        old_state = {
            "_persisted_at": time.time() - 90000,  # 25 hours ago
            "session-old": {
                "sdk_session_id": "old-sdk-id",
                "turn_count": 20,
                "last_used": time.time() - 90000,
            },
        }
        state_file.write_text(json.dumps(old_state))

        units = {"session-old": MagicMock(_sdk_session_id=None)}
        restored = restore_session_state(units, state_file)

        # Should be discarded — no restoration
        assert restored == 0
        assert units["session-old"]._sdk_session_id is None
        # File should be cleaned up
        assert not state_file.exists()

    def test_recent_state_file_restores(self, state_file):
        """AC1/AC2: Fresh state file restores sdk_session_ids."""
        from core.session_state_persistence import restore_session_state

        state = {
            "_persisted_at": time.time() - 30,  # 30 seconds ago
            "session-1": {
                "sdk_session_id": "valid-sdk-id",
                "turn_count": 15,
                "last_used": time.time() - 60,
            },
        }
        state_file.write_text(json.dumps(state))

        unit = MagicMock(_sdk_session_id=None)
        units = {"session-1": unit}
        restored = restore_session_state(units, state_file, validate_db=False)

        assert restored == 1
        assert unit._sdk_session_id == "valid-sdk-id"
        # File consumed (deleted)
        assert not state_file.exists()

    def test_missing_unit_skipped(self, state_file):
        """State references a session not in _units → skip gracefully."""
        from core.session_state_persistence import restore_session_state

        state = {
            "_persisted_at": time.time() - 10,
            "session-gone": {
                "sdk_session_id": "orphan-sdk",
                "turn_count": 5,
                "last_used": time.time() - 100,
            },
        }
        state_file.write_text(json.dumps(state))

        units = {}  # No matching unit
        restored = restore_session_state(units, state_file, validate_db=False)

        assert restored == 0

    def test_atomic_write(self, state_file):
        """Persist uses tmp+rename for crash safety."""
        from core.session_unit import SessionState
        from core.session_state_persistence import persist_session_state

        unit = MagicMock()
        unit.state = SessionState.IDLE
        unit._sdk_session_id = "sdk-atomic"
        unit.session_id = "s1"
        unit.last_used = time.time()
        unit._health_sensor = MagicMock(turn_count=3)

        persist_session_state({"s1": unit}, state_file)

        # File exists and is valid JSON
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["s1"]["sdk_session_id"] == "sdk-atomic"
        # No .tmp file left behind
        assert not state_file.with_suffix(".tmp").exists()

    def test_corrupt_state_file_handled(self, state_file):
        """Corrupt JSON → discard gracefully, no crash."""
        from core.session_state_persistence import restore_session_state

        state_file.write_text("not valid json {{{")

        units = {"s1": MagicMock(_sdk_session_id=None)}
        restored = restore_session_state(units, state_file, validate_db=False)

        assert restored == 0
        assert not state_file.exists()


# ─── Subsystem 2C: Preserve sdk_session_id on shutdown ───


class TestDisconnectAllPreservesIdentity:
    """AC1: disconnect_all must NOT call clear_session_identity."""

    def test_disconnect_all_does_not_call_clear_identity(self):
        """After disconnect_all, sdk_session_id is preserved (for state file)."""
        # Verify the method doesn't CALL clear_session_identity.
        # Check for actual call invocations (`.clear_session_identity()`), not comments.
        import inspect
        import textwrap
        from core.session_router import SessionRouter

        source = inspect.getsource(SessionRouter.disconnect_all)
        # Strip comments (lines starting with #) to avoid false positives
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("#") and not line.strip().startswith("//")
        ]
        code_only = "\n".join(code_lines)
        # Check for actual method call pattern
        assert ".clear_session_identity()" not in code_only, (
            "disconnect_all must NOT call .clear_session_identity() (PE F1/2C)"
        )


# ─── Subsystem 3A: Scoped Heal Immunity ───


class TestScopedHealImmunity:
    """AC5/AC6: turn<3 immunity only for specific triggers."""

    def test_young_session_immune_to_latency(self):
        """AC5: turn_count=0, latency trigger → should NOT heal."""
        from core.session_healing import HealthSensor

        sensor = HealthSensor(max_turns=500)
        # Simulate 0 turns but bad latency
        sensor._turn_count = 1
        # Fill latency buffer to trigger
        sensor._turn_latencies.extend([100] * 10 + [500] * 5)
        sensor._rss_samples.extend([500] * 5)
        sensor._last_activity_time = time.time()

        should_heal, trigger = sensor.should_checkpoint(session_state="idle")
        # Even though latency is bad, young session is immune
        if trigger == "latency_degradation":
            assert not should_heal, "Young session (turn<3) must be immune to latency_degradation"

    def test_young_session_immune_to_turn_approaching(self):
        """AC5: turn_count=2, turn_approaching → should NOT heal."""
        from core.session_healing import HealthSensor

        sensor = HealthSensor(max_turns=500)
        sensor._turn_count = 2
        sensor._last_activity_time = time.time()
        # Artificially make turn count near max
        sensor._max_turns = 4  # turn_count=2, buffer=20, so 2 >= (4-20) is False
        # Need: turn_count >= (max_turns - TURN_APPROACH_BUFFER)
        # TURN_APPROACH_BUFFER = 20, so max_turns must be ≤ 22 for turn=2 to trigger
        sensor._max_turns = 22

        should_heal, trigger = sensor.should_checkpoint(session_state="idle")
        if trigger == "turn_approaching":
            assert not should_heal, "Young session (turn<3) must be immune to turn_approaching"

    def test_young_session_NOT_immune_to_hang(self):
        """AC6: turn_count=1, hang_detected → MUST still heal."""
        from core.session_healing import HealthSensor

        sensor = HealthSensor(max_turns=500)
        sensor._turn_count = 1
        # Simulate hang: last activity was 6 minutes ago
        sensor._last_activity_time = time.time() - 400  # > HANG_TIMEOUT_S (300)

        should_heal, trigger = sensor.should_checkpoint(session_state="idle")
        assert should_heal is True, "Young session must NOT be immune to hang_detected"
        assert trigger == "hang_detected"

    def test_young_session_NOT_immune_to_error_cascade(self):
        """AC6: turn_count=0, error_cascade → MUST still heal."""
        from core.session_healing import HealthSensor

        sensor = HealthSensor(max_turns=500)
        sensor._turn_count = 0
        sensor._consecutive_errors = 5  # > ERROR_CASCADE_THRESHOLD (3)
        sensor._last_activity_time = time.time()

        should_heal, trigger = sensor.should_checkpoint(session_state="idle")
        assert should_heal is True, "Young session must NOT be immune to error_cascade"
        assert trigger == "error_cascade"

    def test_mature_session_not_immune(self):
        """Sessions with turn_count >= 3 get no immunity."""
        from core.session_healing import HealthSensor

        sensor = HealthSensor(max_turns=500)
        sensor._turn_count = 5
        sensor._last_activity_time = time.time() - 400  # hang

        should_heal, trigger = sensor.should_checkpoint(session_state="idle")
        assert should_heal is True
        assert trigger == "hang_detected"


# ─── AC4: Stale sdk_session_id Fallback ───


class TestStaleSessionIdFallback:
    """AC4: If --resume fails with session-not-found, fall back to cold resume."""

    def test_session_not_found_clears_sdk_id(self):
        """Stale sdk_session_id must be cleared on CLI resume-specific errors."""
        from core.session_utils import is_session_not_found_error

        # CLI resume-specific error patterns (anchored)
        assert is_session_not_found_error("failed to load session abc123")
        assert is_session_not_found_error("Cannot resume: session file not found")
        assert is_session_not_found_error("unable to restore session data")
        assert is_session_not_found_error("ENOENT: /home/user/.claude/sessions/abc.json session")
        assert is_session_not_found_error("resume session abc123 does not exist")
        # Should NOT match generic errors containing "session"
        assert not is_session_not_found_error("Connection timeout")
        assert not is_session_not_found_error("Rate limit exceeded")
        assert not is_session_not_found_error("Redis session not found")  # MCP error
        assert not is_session_not_found_error("Session expired in user's app")  # tool error
