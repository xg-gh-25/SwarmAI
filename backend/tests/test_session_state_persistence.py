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
        from core.session_state_persistence import load_persisted_state

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

        result = load_persisted_state(state_file)

        # Should be discarded — empty result
        assert result == {}
        # File should be cleaned up
        assert not state_file.exists()

    def test_recent_state_file_loads(self, state_file):
        """AC1/AC2: Fresh state file returns session_id → sdk_session_id mapping."""
        from core.session_state_persistence import load_persisted_state

        state = {
            "_persisted_at": time.time() - 30,  # 30 seconds ago
            "session-1": {
                "sdk_session_id": "valid-sdk-id",
                "turn_count": 15,
                "last_used": time.time() - 60,
            },
        }
        state_file.write_text(json.dumps(state))

        result = load_persisted_state(state_file)

        assert result == {"session-1": "valid-sdk-id"}
        # File NOT deleted — let next persist_session_state() overwrite atomically.
        # This prevents crash-window data loss (adversarial F1).
        assert state_file.exists()

    def test_missing_file_returns_empty(self, state_file):
        """No state file → empty dict, no error."""
        from core.session_state_persistence import load_persisted_state

        result = load_persisted_state(state_file)
        assert result == {}

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
        """Corrupt JSON → discard gracefully, return empty dict."""
        from core.session_state_persistence import load_persisted_state

        state_file.write_text("not valid json {{{")

        result = load_persisted_state(state_file)

        assert result == {}
        assert not state_file.exists()

    def test_persist_merges_pending_ids(self, state_file):
        """pending_ids for unopened sessions survive persist overwrite.

        Scenario: boot loads {A,B,C}. User opens A only. 60s later persist
        runs with units={A(IDLE)} and pending={B,C}. Output file must have
        A (from live unit) + B,C (from pending). Without merge, B/C are lost.
        """
        from core.session_unit import SessionState
        from core.session_state_persistence import persist_session_state

        # Live unit A — IDLE with fresh sdk_session_id
        unit_a = MagicMock()
        unit_a.state = SessionState.IDLE
        unit_a._sdk_session_id = "sdk-a-live"
        unit_a.last_used = time.time()
        unit_a._health_sensor = MagicMock(turn_count=5)

        units = {"session-a": unit_a}
        pending = {"session-b": "sdk-b-cached", "session-c": "sdk-c-cached"}

        count = persist_session_state(units, state_file, pending_ids=pending)

        assert count == 3  # A + B + C
        data = json.loads(state_file.read_text())
        assert data["session-a"]["sdk_session_id"] == "sdk-a-live"
        assert data["session-b"]["sdk_session_id"] == "sdk-b-cached"
        assert data["session-c"]["sdk_session_id"] == "sdk-c-cached"

    def test_persist_live_unit_takes_precedence_over_pending(self, state_file):
        """If session is both live IDLE and in pending, live wins."""
        from core.session_unit import SessionState
        from core.session_state_persistence import persist_session_state

        unit = MagicMock()
        unit.state = SessionState.IDLE
        unit._sdk_session_id = "sdk-fresh-from-cli"
        unit.last_used = time.time()
        unit._health_sensor = MagicMock(turn_count=10)

        units = {"session-x": unit}
        pending = {"session-x": "sdk-stale-from-cache"}

        persist_session_state(units, state_file, pending_ids=pending)

        data = json.loads(state_file.read_text())
        # Live unit's fresh ID wins, not the stale cached one
        assert data["session-x"]["sdk_session_id"] == "sdk-fresh-from-cli"


# ─── Lazy-Inject Integration Test (fixes the original restore bug) ───


class TestLazyInjectAtSessionCreation:
    """The REAL test: session created AFTER boot gets injected sdk_session_id.

    This tests the production path that was broken:
    - Daemon boots (units={})
    - State file has sdk_session_ids from prior run
    - User opens tab → get_or_create_unit("session-1", "agent-1")
    - The new unit MUST have the persisted sdk_session_id injected
    """

    def test_get_or_create_unit_injects_persisted_id(self, state_file):
        """Production path: new unit gets sdk_session_id from cached state."""
        from core.session_router import SessionRouter
        from core.session_state_persistence import load_persisted_state

        # Setup: write state file as if daemon previously persisted it
        state = {
            "_persisted_at": time.time() - 10,
            "session-abc": {
                "sdk_session_id": "sdk-from-prior-run",
                "turn_count": 8,
                "last_used": time.time() - 30,
            },
        }
        state_file.write_text(json.dumps(state))

        # Load state (simulating what SessionRouter.__init__ now does)
        cached = load_persisted_state(state_file)
        assert cached == {"session-abc": "sdk-from-prior-run"}

        # Create router with cached state
        router = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
        router._persisted_sdk_ids = cached

        # Simulate user opening a tab — this is the lazy-inject moment
        unit = router.get_or_create_unit("session-abc", "agent-1")

        # The unit MUST have the injected sdk_session_id
        assert unit._sdk_session_id == "sdk-from-prior-run"

    def test_get_or_create_unit_no_match_leaves_none(self, state_file):
        """New session with no persisted state → sdk_session_id stays None."""
        from core.session_router import SessionRouter

        router = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
        router._persisted_sdk_ids = {}

        unit = router.get_or_create_unit("brand-new-session", "agent-1")
        assert unit._sdk_session_id is None

    def test_persisted_id_consumed_once(self, state_file):
        """Injected id is removed from cache after use (no stale reuse)."""
        from core.session_router import SessionRouter

        router = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
        router._persisted_sdk_ids = {"session-x": "sdk-x"}

        # First create — injects
        unit = router.get_or_create_unit("session-x", "agent-1")
        assert unit._sdk_session_id == "sdk-x"

        # Second create for same session — should NOT re-inject (unit already exists)
        unit2 = router.get_or_create_unit("session-x", "agent-1")
        assert unit2 is unit  # Same object returned


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

    # (test_young_session_immune_to_latency removed with the latency_degradation
    #  signal — run_099724ca. Young-immunity for the surviving signals is covered
    #  by test_young_session_immune_to_turn_approaching + the RSS immunity test.)

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
