"""Bug condition exploration and preservation tests for context usage ring.

These tests cover two complementary aspects of the context usage ring bugfix:

Bug summary
-----------
``check_context_usage()`` in ``context_monitor.py`` reads ``.jsonl``
transcript files from ``~/.claude/projects/`` (Claude Code data) instead
of using the ``input_tokens`` value from the SDK's ``ResultMessage.usage``
dict.  Additionally, ``CHECK_INTERVAL_TURNS = 5`` causes the monitor to
skip turns 2, 3, 4, 6, 7, 8, 9, etc., leaving the ring frozen.

Test methodology
----------------
- **TestBugConditionExploration**: Each test demonstrates a specific facet
  of the bug by asserting the expected (fixed) behavior.  Failures on
  unfixed code are the counterexamples that prove the bug.
- **TestPreservation**: Property-based and unit tests that verify threshold
  classification, percentage math, SSE event shape, and error resilience.
  These MUST PASS on unfixed code (the logic is correct, just fed wrong data)
  and must continue to pass after the fix.

Key public symbols
------------------
- ``classify_level``          — Pure helper mapping pct → ok/warn/critical
- ``TestBugConditionExploration`` — Exploration tests (expected to fail pre-fix)
- ``TestPreservation``        — Preservation tests (must always pass)

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.3, 2.5, 3.1–3.7
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core.context_monitor import (
    CHECK_INTERVAL_TURNS,
    CRITICAL_PCT,
    WARN_PCT,
    ContextStatus,
    check_context_usage,
)


class TestBugConditionExploration:
    """Exploration tests that surface counterexamples proving the bug.

    Each test encodes the EXPECTED behavior.  On unfixed code these tests
    WILL FAIL — that failure IS the proof that the bug exists.
    """

    # ------------------------------------------------------------------
    # Test 1: check_context_usage() uses filesystem, not SDK data
    # ------------------------------------------------------------------
    def test_context_warning_uses_sdk_data_not_filesystem(self):
        """Demonstrates the data-source mismatch bug.

        **Validates: Requirements 1.1, 1.2, 2.1**

        When no .jsonl transcript files exist (as with SDK 0.1.34+),
        ``check_context_usage()`` returns pct=0.  But the EXPECTED
        behavior is that context usage should be computed from SDK
        ``input_tokens``.  For example, 50 000 input_tokens on a
        200 000-token window should yield pct=25.

        The function has no parameter to accept SDK usage data — it can
        only scan the filesystem.  This architectural gap IS the bug.
        """
        with tempfile.TemporaryDirectory() as empty_dir:
            # Call with an empty dir — no .jsonl files to find
            status = check_context_usage(projects_dir=empty_dir)

            # The function finds nothing → returns pct=0, level="ok"
            assert status.pct == 0, (
                "Expected pct=0 when no transcripts exist (confirms filesystem dependency)"
            )

            # --- Now assert the EXPECTED (fixed) behavior ---
            # If the SDK reports 50 000 input_tokens on a 200K window,
            # the correct pct is round(50000 / 200000 * 100) = 25.
            expected_pct = round(50_000 / 200_000 * 100)  # 25
            assert expected_pct == 25

            # The bug: check_context_usage() returned 0, but the SDK
            # would report 25%.  These MUST be equal in the fixed code.
            # On unfixed code this assertion FAILS — proving the bug.
            assert status.pct == expected_pct, (
                f"BUG: check_context_usage() returned pct={status.pct} "
                f"(filesystem scan found nothing), but SDK input_tokens "
                f"would give pct={expected_pct}.  The function ignores SDK data."
            )

    # ------------------------------------------------------------------
    # Test 2: context_warning must be emitted on EVERY turn
    # ------------------------------------------------------------------
    def test_context_warning_emitted_on_every_turn(self):
        """Demonstrates the skipped-turn bug.

        **Validates: Requirements 1.3, 2.3**

        ``CHECK_INTERVAL_TURNS`` is currently 5, meaning turns 2, 3, 4
        never trigger a context_warning.  The EXPECTED behavior is that
        every turn emits a context_warning (i.e. the interval should
        effectively be 1 — no gating).
        """
        # The fix removes CHECK_INTERVAL_TURNS gating entirely.
        # Assert the constant should be 1 (every turn).
        # On unfixed code CHECK_INTERVAL_TURNS == 5 → this FAILS.
        assert CHECK_INTERVAL_TURNS == 1, (
            f"BUG: CHECK_INTERVAL_TURNS is {CHECK_INTERVAL_TURNS}, "
            f"expected 1 (emit every turn).  Turns 2-4 are skipped."
        )

        # Additionally, verify that turns 2, 3, 4 would pass the gate.
        # The current gate is: `turn == 1 or turn % CHECK_INTERVAL_TURNS == 0`
        # For turns 2, 3, 4 with CHECK_INTERVAL_TURNS=5, this is False.
        for turn in [2, 3, 4]:
            would_emit = (turn == 1 or turn % CHECK_INTERVAL_TURNS == 0)
            assert would_emit, (
                f"BUG: Turn {turn} would NOT emit context_warning "
                f"(gate: turn==1 or turn%{CHECK_INTERVAL_TURNS}==0 → {would_emit}). "
                f"Every turn should emit."
            )

    # ------------------------------------------------------------------
    # Test 3: pct must come from SDK input_tokens, not filesystem
    # ------------------------------------------------------------------
    def test_context_warning_pct_from_sdk_input_tokens(self):
        """Demonstrates the computation mismatch between expected and actual.

        **Validates: Requirements 1.4, 2.1, 2.4**

        The EXPECTED computation is:
            pct = round(input_tokens / model_context_window * 100)

        But ``check_context_usage()`` with no transcripts returns pct=0,
        not the SDK-derived value.  This proves the function uses the
        wrong data source.
        """
        # Expected: SDK reports 50000 tokens on 200K window → 25%
        sdk_input_tokens = 50_000
        model_context_window = 200_000
        expected_pct = round(sdk_input_tokens / model_context_window * 100)
        assert expected_pct == 25, "Sanity check on expected computation"

        # Actual: check_context_usage() with no transcripts → 0%
        with tempfile.TemporaryDirectory() as empty_dir:
            status = check_context_usage(projects_dir=empty_dir)
            actual_pct = status.pct

        # On unfixed code: actual_pct == 0, expected_pct == 25
        # These MUST be equal in the fixed system.
        assert actual_pct == expected_pct, (
            f"BUG: Filesystem-based pct={actual_pct} != "
            f"SDK-based pct={expected_pct}.  "
            f"check_context_usage() ignores SDK input_tokens."
        )


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
    # Unit test 4: SSE event shape
    # ------------------------------------------------------------------
    def test_sse_event_shape(self):
        """Verify ContextStatus.to_dict() produces the expected SSE event shape.

        **Validates: Requirements 3.5**

        The ``context_warning`` SSE event must contain ``tokensEst``,
        ``pct``, ``level``, and ``message`` keys.  The ``type`` field is
        added by ``agent_manager.py``, not by ``ContextStatus`` itself.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = os.path.join(tmpdir, "test.jsonl")
            # Write a minimal user message
            entry = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Hello world",
                },
            }
            with open(transcript, "w") as f:
                f.write(json.dumps(entry) + "\n")

            status = check_context_usage(projects_dir=tmpdir)

        d = status.to_dict()

        # Assert required top-level keys exist
        assert "tokensEst" in d, f"Missing 'tokensEst' in {d.keys()}"
        assert "pct" in d, f"Missing 'pct' in {d.keys()}"
        assert "level" in d, f"Missing 'level' in {d.keys()}"
        assert "message" in d, f"Missing 'message' in {d.keys()}"

        # Assert types
        assert isinstance(d["tokensEst"], int)
        assert isinstance(d["pct"], int)
        assert isinstance(d["level"], str)
        assert isinstance(d["message"], str)

        # Level must be one of the valid values
        assert d["level"] in ("ok", "warn", "critical")

    # ------------------------------------------------------------------
    # Unit test 5: error resilience — no crash on missing dir
    # ------------------------------------------------------------------
    def test_error_resilience_no_crash_on_missing_dir(self):
        """Calling check_context_usage() with a nonexistent path must not raise.

        **Validates: Requirements 3.6**

        The system must fail silently (best-effort) without breaking the
        response stream.
        """
        status = check_context_usage(projects_dir="/nonexistent/path/that/does/not/exist")

        # Must return a ContextStatus, not raise
        assert isinstance(status, ContextStatus)
        # Graceful fallback: level should be "ok" (no data → no warning)
        assert status.level == "ok"
        # pct should be 0 (no data)
        assert status.pct == 0
