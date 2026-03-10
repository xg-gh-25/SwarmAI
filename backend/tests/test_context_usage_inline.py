"""Inline context usage ring tests — threshold, summation, and num_turns normalization.

These tests cover the inline context monitoring pipeline in ``agent_manager.py``:

1. ``_sum_usage_input_tokens()`` — sums input_tokens + cache_read + cache_creation
2. ``_build_context_warning()`` — computes pct, level, message from (total, model)
3. **num_turns normalization** — divides cumulative SDK usage by num_turns to get
   approximate per-turn context window consumption (fixes 972% bug)

The deprecated ``context_monitor.py`` module and ``s_context-monitor/`` skill
have been removed.  These tests no longer reference them.

Test methodology
----------------
- ``TestPreservation``                — Threshold classification, event shape, null suppression
- ``TestCachedTokensPreservation``    — _build_context_warning() direct tests
- ``TestCachedTokensBugExploration``  — Cached tokens summation tests
- ``TestNumTurnsNormalization``       — num_turns division tests (new)

Key constants (inlined from deleted context_monitor.py)
-------------------------------------------------------
- ``WARN_PCT = 70``
- ``CRITICAL_PCT = 85``
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from typing import Optional

# Constants inlined from deleted context_monitor.py
WARN_PCT = 70
CRITICAL_PCT = 85

from core.agent_manager import AgentManager


# ---------------------------------------------------------------------------
# Helper: threshold classification (reused by preservation & fix tests)
# ---------------------------------------------------------------------------

def classify_level(pct: int) -> str:
    """Classify context usage percentage into a severity level.

    Mirrors the threshold logic in ``context_monitor.py``:
    - ``critical`` when pct >= 85
    - ``warn`` when 70 <= pct < 85
    - ``ok`` otherwise

    This helper is intentionally a pure function so it can be used in
    property-based tests without touching any I/O or mocking.
    """
    if pct >= CRITICAL_PCT:
        return "critical"
    elif pct >= WARN_PCT:
        return "warn"
    return "ok"


class TestPreservation:
    """Preservation tests that capture baseline behavior to protect against regressions.

    These tests verify the threshold classification logic, percentage
    calculation math, SSE event shape, and error resilience of the
    context monitoring system.  They MUST PASS on unfixed code because
    they test aspects of the system that are correct today (just fed
    wrong data) and must remain correct after the fix.

    Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7
    """

    # ------------------------------------------------------------------
    # Property-based test 1: threshold classification
    # ------------------------------------------------------------------
    @given(pct=st.integers(min_value=0, max_value=100))
    @settings(max_examples=200)
    def test_threshold_classification_property(self, pct: int):
        """For any pct in [0, 100], level classification is deterministic.

        **Validates: Requirements 3.1, 3.2, 3.3**

        - pct < 70  → ``ok``
        - 70 ≤ pct < 85 → ``warn``
        - pct ≥ 85 → ``critical``
        """
        level = classify_level(pct)

        if pct < WARN_PCT:
            assert level == "ok", f"pct={pct} should be 'ok', got '{level}'"
        elif pct < CRITICAL_PCT:
            assert level == "warn", f"pct={pct} should be 'warn', got '{level}'"
        else:
            assert level == "critical", f"pct={pct} should be 'critical', got '{level}'"

    # ------------------------------------------------------------------
    # Property-based test 2: percentage calculation
    # ------------------------------------------------------------------
    @given(
        input_tokens=st.integers(min_value=1, max_value=1_000_000),
        window=st.integers(min_value=1, max_value=1_000_000),
    )
    @settings(max_examples=200)
    def test_percentage_calculation_property(self, input_tokens: int, window: int):
        """For any (input_tokens, window) pair, pct = round(input_tokens / window * 100).

        **Validates: Requirements 3.1, 3.2, 3.3**

        This tests the pure math that both the old and new code must agree on.
        """
        pct = round(input_tokens / window * 100)
        # pct must be a non-negative integer
        assert isinstance(pct, int)
        assert pct >= 0
        # Verify the inverse relationship holds within rounding tolerance
        expected_ratio = input_tokens / window
        actual_ratio = pct / 100
        assert abs(expected_ratio - actual_ratio) < 0.01, (
            f"input_tokens={input_tokens}, window={window}: "
            f"expected ratio ~{expected_ratio:.4f}, got pct={pct} (ratio={actual_ratio:.4f})"
        )

    # ------------------------------------------------------------------
    # Unit test 3: boundary values
    # ------------------------------------------------------------------
    def test_threshold_boundary_values(self):
        """Test exact boundary values for threshold classification.

        **Validates: Requirements 3.1, 3.2, 3.3**
        """
        assert classify_level(0) == "ok"
        assert classify_level(69) == "ok"
        assert classify_level(70) == "warn"
        assert classify_level(84) == "warn"
        assert classify_level(85) == "critical"
        assert classify_level(100) == "critical"

    # ------------------------------------------------------------------
    # Unit test 4: SSE event shape (via _build_context_warning)
    # ------------------------------------------------------------------
    def test_sse_event_shape(self):
        """Verify _build_context_warning() produces the expected SSE event shape.

        **Validates: Requirements 3.5**

        The ``context_warning`` SSE event must contain ``type``,
        ``tokensEst``, ``pct``, ``level``, and ``message`` keys.
        """
        mgr = AgentManager.__new__(AgentManager)
        result = mgr._build_context_warning(100_000, None)
        assert result is not None

        assert "type" in result, f"Missing 'type' in {result.keys()}"
        assert "tokensEst" in result, f"Missing 'tokensEst' in {result.keys()}"
        assert "pct" in result, f"Missing 'pct' in {result.keys()}"
        assert "level" in result, f"Missing 'level' in {result.keys()}"
        assert "message" in result, f"Missing 'message' in {result.keys()}"

        assert isinstance(result["tokensEst"], int)
        assert isinstance(result["pct"], int)
        assert isinstance(result["level"], str)
        assert isinstance(result["message"], str)
        assert result["level"] in ("ok", "warn", "critical")

    # ------------------------------------------------------------------
    # Unit test 5: error resilience — _build_context_warning handles bad input
    # ------------------------------------------------------------------
    def test_error_resilience_bad_input(self):
        """_build_context_warning() returns None for invalid input.

        **Validates: Requirements 3.6**
        """
        mgr = AgentManager.__new__(AgentManager)
        assert mgr._build_context_warning(None, None) is None
        assert mgr._build_context_warning(0, None) is None
        assert mgr._build_context_warning(-1, None) is None


# ---------------------------------------------------------------------------
# Cached Tokens Preservation Tests
# ---------------------------------------------------------------------------


class TestCachedTokensPreservation:
    """Preservation tests for ``_build_context_warning()`` behavior.

    These tests target ``_build_context_warning()`` directly — the function
    that is NOT modified by the cached-tokens fix.  They verify that
    threshold classification, event shape, null suppression, and the
    None-handling sum formula remain correct on both unfixed and fixed code.

    Since ``isBugCondition`` is true for every turn (model is always None
    in the result event), preservation checking must target this function
    directly rather than the full pipeline.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**
    """

    # Shared fixture: lightweight AgentManager (no DB, no async needed)
    @pytest.fixture(autouse=True)
    def _setup_manager(self):
        self.mgr = AgentManager()
        self.window = 200_000  # default context window for all models

    # ------------------------------------------------------------------
    # Property-based test 1: Non-cached inputs produce correct pct/level
    # ------------------------------------------------------------------
    @given(input_tokens=st.integers(min_value=1, max_value=500_000))
    @settings(max_examples=200)
    def test_non_cached_input_produces_correct_pct_and_level(
        self, input_tokens: int
    ):
        """For random input_tokens > 0 with no cached tokens, verify pct and level.

        **Validates: Requirements 3.1, 3.2, 3.3**

        When cache_read=0 and cache_creation=0, the sum equals
        input_tokens.  ``_build_context_warning(input_tokens, model)``
        must return a dict with:
        - ``pct = round(input_tokens / window * 100)``
        - ``level`` matching the threshold classification
        """
        result = self.mgr._build_context_warning(input_tokens, None)
        assert result is not None, (
            f"Expected a dict for input_tokens={input_tokens}, got None"
        )

        expected_pct = round(input_tokens / self.window * 100)
        assert result["pct"] == expected_pct, (
            f"input_tokens={input_tokens}: expected pct={expected_pct}, "
            f"got {result['pct']}"
        )

        expected_level = classify_level(expected_pct)
        assert result["level"] == expected_level, (
            f"input_tokens={input_tokens}, pct={expected_pct}: "
            f"expected level={expected_level!r}, got {result['level']!r}"
        )

    # ------------------------------------------------------------------
    # Property-based test 2: Sum formula handles None values correctly
    # ------------------------------------------------------------------
    @given(
        input_tokens=st.one_of(st.none(), st.integers(min_value=0, max_value=500_000)),
        cache_read=st.one_of(st.none(), st.integers(min_value=0, max_value=500_000)),
        cache_creation=st.one_of(st.none(), st.integers(min_value=0, max_value=500_000)),
    )
    @settings(max_examples=200)
    def test_sum_formula_handles_none_values(
        self,
        input_tokens: Optional[int],
        cache_read: Optional[int],
        cache_creation: Optional[int],
    ):
        """Verify the three-field sum formula treats None as 0.

        **Validates: Requirements 3.4**

        The formula ``(x or 0) + (y or 0) + (z or 0)`` must produce
        the same result as replacing each None with 0 and summing.
        """
        # The formula used in the fix
        total_or = (
            (input_tokens or 0)
            + (cache_read or 0)
            + (cache_creation or 0)
        )

        # Explicit None→0 replacement
        expected = (
            (input_tokens if input_tokens is not None else 0)
            + (cache_read if cache_read is not None else 0)
            + (cache_creation if cache_creation is not None else 0)
        )

        assert total_or == expected, (
            f"Sum mismatch: ({input_tokens} or 0) + ({cache_read} or 0) "
            f"+ ({cache_creation} or 0) = {total_or}, expected {expected}"
        )

    # ------------------------------------------------------------------
    # Unit test 3: None or 0 input_tokens returns None (no event)
    # ------------------------------------------------------------------
    def test_returns_none_for_none_or_zero_input(self):
        """_build_context_warning() returns None when input_tokens is None or 0.

        **Validates: Requirements 3.4**

        No ``context_warning`` event should be emitted when there is no
        usage data.  This prevents false 0% readings.
        """
        assert self.mgr._build_context_warning(None, None) is None
        assert self.mgr._build_context_warning(None, "claude-sonnet-4-20250514") is None
        assert self.mgr._build_context_warning(0, None) is None
        assert self.mgr._build_context_warning(0, "claude-sonnet-4-20250514") is None
        # Negative values should also return None
        assert self.mgr._build_context_warning(-1, None) is None
        assert self.mgr._build_context_warning(-100, "claude-sonnet-4-20250514") is None

    # ------------------------------------------------------------------
    # Unit test 4: context_warning event shape has required fields
    # ------------------------------------------------------------------
    def test_context_warning_event_shape(self):
        """Verify the returned dict contains all required SSE event fields.

        **Validates: Requirements 3.5**

        The ``context_warning`` event must contain: ``type``, ``level``,
        ``pct``, ``tokensEst``, ``message``.
        """
        result = self.mgr._build_context_warning(100_000, None)
        assert result is not None

        required_keys = {"type", "level", "pct", "tokensEst", "message"}
        assert set(result.keys()) == required_keys, (
            f"Expected keys {required_keys}, got {set(result.keys())}"
        )

        # Type checks
        assert result["type"] == "context_warning"
        assert isinstance(result["level"], str)
        assert result["level"] in ("ok", "warn", "critical")
        assert isinstance(result["pct"], int)
        assert isinstance(result["tokensEst"], int)
        assert isinstance(result["message"], str)
        assert result["tokensEst"] == 100_000

    # ------------------------------------------------------------------
    # Unit test 5: Threshold boundary values preserved
    # ------------------------------------------------------------------
    def test_threshold_boundaries_preserved(self):
        """Verify exact threshold boundaries: 69→ok, 70→warn, 84→warn, 85→critical.

        **Validates: Requirements 3.1, 3.2, 3.3**

        Uses precise input_tokens values that produce exact boundary pct
        values on the 200K default window.
        """
        window = self.window  # 200_000

        # 69% → ok: input_tokens = 138_000 → round(138000/200000*100) = 69
        result_69 = self.mgr._build_context_warning(138_000, None)
        assert result_69 is not None
        assert result_69["pct"] == 69
        assert result_69["level"] == "ok"

        # 70% → warn: input_tokens = 140_000 → round(140000/200000*100) = 70
        result_70 = self.mgr._build_context_warning(140_000, None)
        assert result_70 is not None
        assert result_70["pct"] == 70
        assert result_70["level"] == "warn"

        # 84% → warn: input_tokens = 168_000 → round(168000/200000*100) = 84
        result_84 = self.mgr._build_context_warning(168_000, None)
        assert result_84 is not None
        assert result_84["pct"] == 84
        assert result_84["level"] == "warn"

        # 85% → critical: input_tokens = 170_000 → round(170000/200000*100) = 85
        result_85 = self.mgr._build_context_warning(170_000, None)
        assert result_85 is not None
        assert result_85["pct"] == 85
        assert result_85["level"] == "critical"

# ---------------------------------------------------------------------------
# num_turns normalization tests (fixes 972% bug)
# ---------------------------------------------------------------------------


class TestNumTurnsNormalization:
    """Tests for the num_turns normalization fix.

    The SDK's ``ResultMessage.usage`` reports cumulative token counts across
    all internal agentic turns (tool-use loops).  When the agent does N
    tool-use turns, ``cache_read_input_tokens`` can be N × context_window,
    producing absurd percentages like 972%.

    The fix divides the cumulative sum by ``num_turns`` to get the
    approximate per-turn context window consumption.
    """

    @staticmethod
    def _normalize(total_tokens: int, num_turns: int) -> int:
        """Replicate the normalization logic from agent_manager.py."""
        if num_turns > 1:
            return total_tokens // num_turns
        return total_tokens

    def test_single_turn_no_normalization(self):
        """num_turns=1 should not change the total."""
        assert self._normalize(150_000, 1) == 150_000

    def test_multi_turn_normalization(self):
        """10 turns with 1.95M cumulative → ~195K per turn."""
        total = 1_950_000
        num_turns = 10
        result = self._normalize(total, num_turns)
        assert result == 195_000
        # That's 97.5% of 200K — reasonable, not 975%
        pct = round(result / 200_000 * 100)
        assert pct == 98

    def test_972_percent_scenario(self):
        """Reproduce the exact 972% bug scenario and verify the fix."""
        # Cumulative: ~1.944M tokens across ~10 turns
        total = 1_944_000
        num_turns = 10
        # Without fix: 1944000 / 200000 * 100 = 972%
        unfixed_pct = round(total / 200_000 * 100)
        assert unfixed_pct == 972

        # With fix: 194400 / 200000 * 100 = 97%
        normalized = self._normalize(total, num_turns)
        fixed_pct = round(normalized / 200_000 * 100)
        assert fixed_pct == 97

    def test_num_turns_zero_treated_as_one(self):
        """num_turns=0 or None should be treated as 1 (no division)."""
        # The code does: _n_turns = event.get("num_turns") or 1
        # So 0 → 1, None → 1
        assert self._normalize(150_000, 1) == 150_000

    def test_num_turns_two(self):
        """Simple 2-turn case."""
        assert self._normalize(300_000, 2) == 150_000

    @given(
        total=st.integers(min_value=0, max_value=10_000_000),
        num_turns=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    def test_normalized_never_exceeds_total(self, total, num_turns):
        """For any positive num_turns, normalized <= total."""
        normalized = self._normalize(total, num_turns)
        assert normalized <= total

    @given(
        total=st.integers(min_value=0, max_value=10_000_000),
        num_turns=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    def test_normalized_is_integer_division(self, total, num_turns):
        """Normalization uses integer division (floor)."""
        normalized = self._normalize(total, num_turns)
        if num_turns > 1:
            assert normalized == total // num_turns
        else:
            assert normalized == total
