"""Tests for adaptive streaming timeout and circuit breaker.

Verifies:
- AC1: Timeout scales with context size (2M → ≥600s)
- AC2: Circuit breaker stops retry after 2 consecutive timeouts on high context
- AC3: CONTEXT_TOO_LARGE event yielded
- AC5: Normal sessions (<500K) keep 300s timeout
"""



class TestAdaptiveTimeout:
    """AC1 + AC5: Timeout formula scales correctly."""

    def _make_unit(self, context_tokens: int = 0):
        """Create a SessionUnit with mocked dependencies and set context."""
        from core.session_unit import SessionUnit

        unit = SessionUnit.__new__(SessionUnit)
        unit._last_known_context_tokens = context_tokens
        return unit

    def test_low_context_keeps_300s(self):
        """AC5: <500K tokens → 300s (floor)."""
        unit = self._make_unit(context_tokens=200_000)
        timeout = unit._compute_message_timeout()
        assert timeout == 300.0

    def test_medium_context_keeps_300s(self):
        """AC5: 500K tokens → 300s (still below formula output)."""
        unit = self._make_unit(context_tokens=500_000)
        timeout = unit._compute_message_timeout()
        # 500K / 3000 = 166.7, below floor of 300
        assert timeout == 300.0

    def test_high_context_scales_up(self):
        """AC1: 1.5M tokens → 500s."""
        unit = self._make_unit(context_tokens=1_500_000)
        timeout = unit._compute_message_timeout()
        assert timeout == 500.0

    def test_2m_context_over_600s(self):
        """AC1: 2M tokens → ≥600s (667s specifically)."""
        unit = self._make_unit(context_tokens=2_000_000)
        timeout = unit._compute_message_timeout()
        assert timeout >= 600.0
        assert abs(timeout - 666.7) < 1.0

    def test_very_large_capped_at_900s(self):
        """Timeout never exceeds 900s cap."""
        unit = self._make_unit(context_tokens=5_000_000)
        timeout = unit._compute_message_timeout()
        assert timeout == 900.0

    def test_zero_context_uses_floor(self):
        """No context tracked yet → use 300s floor."""
        unit = self._make_unit(context_tokens=0)
        timeout = unit._compute_message_timeout()
        assert timeout == 300.0


class TestInitTimeoutResumeFloor:
    """Part B (run_4b74b764): the first-message INIT timeout must floor at the
    adaptive message timeout for --resume sessions (heavy context replay), while
    a fresh spawn keeps the fast 180s. Guards against reverting to a fixed
    INIT_TIMEOUT = 180.0 that guillotines a healthy large-context resume.

    Drives the REAL SessionUnit._compute_init_timeout() — the single source the
    orchestrator calls. NO local re-derivation (Gate-2: a test that re-implements
    the formula passes even when prod is reverted = theater). Reverting
    _compute_init_timeout to a fixed 180.0 makes the resume tests below go RED.
    """

    def _make_unit(self, context_tokens=0, resume=False):
        from core.session_unit import SessionUnit
        unit = SessionUnit.__new__(SessionUnit)
        unit._last_known_context_tokens = context_tokens
        unit._sdk_session_id = "sdk-resume" if resume else None
        return unit

    def test_fresh_session_keeps_fast_180(self):
        """Fresh spawn (no _sdk_session_id) → 180s regardless of context."""
        unit = self._make_unit(context_tokens=2_000_000, resume=False)
        assert unit._compute_init_timeout() == 180.0

    def test_resume_heavy_context_floors_above_180(self):
        """--resume with 2.4M tokens → INIT floored at the adaptive timeout
        (the 180s fixed floor would have killed a healthy resume). RED if prod
        reverts to a fixed 180.0."""
        unit = self._make_unit(context_tokens=2_400_000, resume=True)
        init = unit._compute_init_timeout()
        assert init > 180.0
        # equals the resume-multiplied adaptive timeout, not the fixed floor
        assert init == unit._compute_message_timeout()

    def test_resume_small_context_still_at_least_180(self):
        """--resume with tiny context → adaptive timeout is 600s (resume-mult of
        the 300 floor), so INIT is ≥180; never below the fresh floor. RED if
        prod reverts to a fixed 180.0 (this would equal exactly 180, < 600)."""
        unit = self._make_unit(context_tokens=10_000, resume=True)
        init = unit._compute_init_timeout()
        assert init >= 180.0
        assert init == unit._compute_message_timeout()  # proves not the fixed floor


class TestCircuitBreaker:
    """AC2 + AC3: Circuit breaker stops retry and emits event."""

    def test_high_context_2x_timeout_breaks(self):
        """AC2: >1M + 2 consecutive timeouts → should break retry."""
        from core.session_unit import should_circuit_break_timeout

        # 2 consecutive timeouts, high context
        result = should_circuit_break_timeout(
            consecutive_timeouts=2,
            context_tokens=1_500_000,
        )
        assert result is True

    def test_low_context_2x_timeout_continues(self):
        """AC5: <1M + 2 timeouts → should NOT break (normal retry behavior)."""
        from core.session_unit import should_circuit_break_timeout

        result = should_circuit_break_timeout(
            consecutive_timeouts=2,
            context_tokens=800_000,
        )
        assert result is False

    def test_high_context_1x_timeout_continues(self):
        """Single timeout even on high context → let it retry once."""
        from core.session_unit import should_circuit_break_timeout

        result = should_circuit_break_timeout(
            consecutive_timeouts=1,
            context_tokens=1_000_000,
        )
        assert result is False

    def test_context_too_large_event_structure(self):
        """AC3: The error event has required fields."""
        from core.session_unit import build_context_too_large_event

        event = build_context_too_large_event(
            context_tokens=1_500_000,
            consecutive_timeouts=2,
        )
        assert event["type"] == "error"
        assert event["code"] == "CONTEXT_TOO_LARGE"
        assert "1500K" in event["message"] or "1,500K" in event["message"]
        assert event["recoverable"] is True
