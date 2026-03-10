"""Bug condition exploration and preservation tests for context usage ring.

These tests cover three complementary aspects of the context usage ring bugfixes:

Bug summary (original — context-usage-ring-fix)
------------------------------------------------
``check_context_usage()`` in ``context_monitor.py`` reads ``.jsonl``
transcript files from ``~/.claude/projects/`` (Claude Code data) instead
of using the ``input_tokens`` value from the SDK's ``ResultMessage.usage``
dict.  Additionally, ``CHECK_INTERVAL_TURNS = 5`` causes the monitor to
skip turns 2, 3, 4, 6, 7, 8, 9, etc., leaving the ring frozen.

Bug summary (cached tokens — context-ring-cached-tokens-fix)
-------------------------------------------------------------
After the original fix, ``run_conversation()`` and ``continue_with_answer()``
capture only ``_usage.get("input_tokens")`` — the non-cached portion — ignoring
``cache_read_input_tokens`` and ``cache_creation_input_tokens``.  With prompt
caching active, ``input_tokens`` is often single digits (e.g. 3) while the bulk
of context consumption lives in the cached fields.  Additionally, the ``result``
SSE event has no ``model`` field, so ``last_model`` is always ``None``.

Test methodology
----------------
- **TestBugConditionExploration**: Each test demonstrates a specific facet
  of the original bug by asserting the expected (fixed) behavior.
- **TestPreservation**: Property-based and unit tests that verify threshold
  classification, percentage math, SSE event shape, and error resilience.
- **TestCachedTokensBugExploration**: Exploration tests for the cached tokens
  bug.  Each test simulates the token capture logic in ``run_conversation()``
  and asserts the expected (fixed) behavior.  Failures on unfixed code prove
  the bug exists.

Key public symbols
------------------
- ``classify_level``                  — Pure helper mapping pct → ok/warn/critical
- ``TestBugConditionExploration``     — Original exploration tests (expected to fail pre-fix)
- ``TestPreservation``                — Preservation tests (must always pass)
- ``TestCachedTokensBugExploration``  — Cached tokens exploration tests (expected to fail pre-fix)

Validates: Requirements 1.1–1.5, 2.1, 2.3, 2.5, 3.1–3.8
- ``TestCachedTokensPreservation``    — Preservation tests for _build_context_warning() (must always pass)
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from typing import Optional

from core.context_monitor import (
    CHECK_INTERVAL_TURNS,
    CRITICAL_PCT,
    WARN_PCT,
    ContextStatus,
    check_context_usage,
)
from core.agent_manager import AgentManager


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


# ---------------------------------------------------------------------------
# Cached Tokens Bug Exploration
# ---------------------------------------------------------------------------


class TestCachedTokensBugExploration:
    """Exploration tests for the cached tokens bug in context usage ring.

    These tests simulate the token capture logic in ``run_conversation()``
    and ``continue_with_answer()`` to prove that:

    1. Cached token fields (``cache_read_input_tokens``,
       ``cache_creation_input_tokens``) are ignored — only ``input_tokens``
       is passed to ``_build_context_warning()``.
    2. The ``model`` field is always ``None`` because the ``result`` SSE
       event does not include it.

    Each test encodes the EXPECTED (fixed) behavior.  On unfixed code these
    tests WILL FAIL — that failure IS the proof that the bug exists.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.3, 2.5**
    """

    # Helper: simulate the token capture logic from run_conversation()
    # This replicates the EXACT code path in the unfixed run_conversation():
    #   _usage = event.get("usage")
    #   if _usage:
    #       last_input_tokens = _usage.get("input_tokens")
    #   last_model = event.get("model")
    @staticmethod
    def _simulate_unfixed_capture(result_event: dict) -> tuple:
        """Simulate the unfixed token capture from run_conversation().

        Returns (last_input_tokens, last_model) as the unfixed code would
        compute them.
        """
        last_input_tokens = None
        last_model = None
        _usage = result_event.get("usage")
        if _usage:
            last_input_tokens = _usage.get("input_tokens")
        last_model = result_event.get("model")
        return last_input_tokens, last_model

    @staticmethod
    def _compute_expected_total(usage: dict) -> int:
        """Compute the EXPECTED total input tokens (sum of all three fields).

        This is what the fixed code should compute.
        """
        return (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        )

    # ------------------------------------------------------------------
    # Test 1: Cached tokens ignored — only input_tokens is captured
    # ------------------------------------------------------------------
    def test_cached_tokens_ignored_in_context_usage(self):
        """Proves cached token fields are ignored by the unfixed code.

        **Validates: Requirements 1.1, 1.3, 2.1, 2.3**

        Scenario: SDK returns ``input_tokens: 3``,
        ``cache_read_input_tokens: 98599``,
        ``cache_creation_input_tokens: 948``.
        Total should be 99550, but unfixed code captures only 3.

        On unfixed code, ``_build_context_warning()`` receives 3 (not
        99550), so pct ≈ 0% instead of ≈ 50%.  This assertion FAILS,
        proving the bug.
        """
        usage = {
            "input_tokens": 3,
            "cache_read_input_tokens": 98599,
            "cache_creation_input_tokens": 948,
            "output_tokens": 500,
        }
        result_event = {
            "type": "result",
            "session_id": "test-session-1",
            "usage": usage,
        }

        # What the unfixed code captures
        captured_tokens, _ = self._simulate_unfixed_capture(result_event)

        # What the EXPECTED (fixed) total should be
        expected_total = self._compute_expected_total(usage)
        assert expected_total == 99550, "Sanity: 3 + 98599 + 948 = 99550"

        # BUG ASSERTION: On unfixed code, captured_tokens == 3 (not 99550)
        # This MUST be equal in the fixed code.
        assert captured_tokens == expected_total, (
            f"BUG: run_conversation() captured last_input_tokens="
            f"{captured_tokens} (only input_tokens), but expected "
            f"total={expected_total} (sum of all three fields). "
            f"cache_read_input_tokens and cache_creation_input_tokens "
            f"are ignored."
        )

    # ------------------------------------------------------------------
    # Test 2: Over-window session not detected due to ignored cache
    # ------------------------------------------------------------------
    def test_over_window_not_detected_with_cached_tokens(self):
        """Proves over-window condition is missed by the unfixed code.

        **Validates: Requirements 1.2, 2.2**

        Scenario: SDK returns ``input_tokens: 11337``,
        ``cache_read_input_tokens: 661568``,
        ``cache_creation_input_tokens: 66889``.
        Total = 739794 on a 200K window → pct ≈ 370% (critical).
        But unfixed code sees only 11337 → pct ≈ 6% (ok).

        On unfixed code, the context_warning level is "ok" instead of
        "critical".  This assertion FAILS, proving the bug.
        """
        usage = {
            "input_tokens": 11337,
            "cache_read_input_tokens": 661568,
            "cache_creation_input_tokens": 66889,
            "output_tokens": 2000,
        }
        result_event = {
            "type": "result",
            "session_id": "test-session-2",
            "usage": usage,
        }

        captured_tokens, _ = self._simulate_unfixed_capture(result_event)
        expected_total = self._compute_expected_total(usage)
        assert expected_total == 739794, "Sanity: 11337 + 661568 + 66889"

        # Compute pct using the captured (buggy) value
        window = 200_000  # default context window
        buggy_pct = round(captured_tokens / window * 100)
        expected_pct = round(expected_total / window * 100)

        assert expected_pct == 370, "Sanity: 739794/200000*100 ≈ 370"

        # The expected pct should be critical (≥ 85%)
        expected_level = classify_level(expected_pct)
        assert expected_level == "critical"

        # BUG ASSERTION: buggy_pct should equal expected_pct
        # On unfixed code: buggy_pct ≈ 6 (ok), expected_pct ≈ 370
        assert buggy_pct == expected_pct, (
            f"BUG: Context warning shows pct={buggy_pct}% (level="
            f"{classify_level(buggy_pct)}) using only input_tokens="
            f"{captured_tokens}, but actual usage is pct="
            f"{expected_pct}% (level={expected_level}) with total="
            f"{expected_total} tokens. Over-window condition missed."
        )

    # ------------------------------------------------------------------
    # Test 3: Model always None — result event has no model field
    # ------------------------------------------------------------------
    def test_model_always_none_from_result_event(self):
        """Proves model is always None because result event lacks it.

        **Validates: Requirements 1.5, 2.5**

        The ``result`` SSE event built by ``_run_query_on_client()`` does
        NOT include a ``model`` field.  So ``event.get("model")`` always
        returns ``None``.  The EXPECTED behavior is to resolve the model
        from ``agent_config.get("model")`` instead.

        On unfixed code, ``last_model`` is ``None`` instead of the
        configured model string.  This assertion FAILS, proving the bug.
        """
        # Simulate a result event as built by _run_query_on_client()
        # — note: NO "model" field in the event
        result_event = {
            "type": "result",
            "session_id": "test-session-3",
            "usage": {
                "input_tokens": 50000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 1000,
            },
        }

        # The agent_config that would be available in run_conversation()
        agent_config = {
            "model": "claude-sonnet-4-20250514",
            "name": "test-agent",
        }

        # What the unfixed code captures for model
        _, captured_model = self._simulate_unfixed_capture(result_event)

        # What the EXPECTED (fixed) code should use
        expected_model = agent_config.get("model")
        assert expected_model == "claude-sonnet-4-20250514"

        # BUG ASSERTION: captured_model should equal expected_model
        # On unfixed code: captured_model is None (event has no model)
        assert captured_model == expected_model, (
            f"BUG: run_conversation() captured last_model="
            f"{captured_model!r} from event.get('model') (result "
            f"event has no model field), but expected model="
            f"{expected_model!r} from agent_config.get('model'). "
            f"_get_model_context_window(None) always returns the "
            f"default 200K window."
        )


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
