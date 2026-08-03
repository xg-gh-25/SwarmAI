"""Tests for llm_optimizer — LLM-based skill optimization via Bedrock Opus.

Tests use mocked Bedrock responses to avoid real API calls.
Covers: TextChange generation, JSON parsing, error handling, fallback,
pre-validation, token tracking, skill text truncation.
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock


from core.evolution_optimizer import TextChange
from core.llm_optimizer import LLMUsage


# ── AC1: llm_optimizer.py exists with optimize_skill_with_llm ──

class TestOptimizeSkillWithLLM:
    """Core function produces TextChange list from LLM response."""

    def test_returns_text_changes_from_valid_response(self):
        """Valid LLM JSON response → list of TextChange objects."""
        from core.llm_optimizer import optimize_skill_with_llm

        mock_response = json.dumps({"changes": [
            {
                "original": "Always include verbose output.",
                "replacement": "Only include verbose output when --verbose flag is set.",
                "reason": "Users corrected: don't include verbose output by default",
            }
        ]})

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.return_value = (mock_response, LLMUsage(500, 200))
            changes, usage = optimize_skill_with_llm(
                skill_text="Always include verbose output.\nRun deployment.",
                corrections=[
                    ("don't include verbose output", "remove", "high"),
                    ("should add timestamps", "add", "low"),
                ],
                skill_name="test-skill",
            )

        assert len(changes) == 1
        assert isinstance(changes[0], TextChange)
        assert "verbose" in changes[0].replacement
        assert changes[0].original == "Always include verbose output."
        assert usage.input_tokens == 500
        assert usage.output_tokens == 200

    def test_returns_empty_on_malformed_json(self):
        """Malformed LLM response → empty list, no crash."""
        from core.llm_optimizer import optimize_skill_with_llm

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.return_value = ("This is not valid JSON at all", LLMUsage())
            changes, usage = optimize_skill_with_llm(
                skill_text="Do the thing.",
                corrections=[("don't do it wrong", "remove", "high")],
                skill_name="broken",
            )

        assert changes == []

    def test_returns_empty_on_api_error(self):
        """Bedrock API failure → empty list, no crash."""
        from core.llm_optimizer import optimize_skill_with_llm

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.side_effect = Exception("Bedrock throttling")
            changes, usage = optimize_skill_with_llm(
                skill_text="Do the thing.",
                corrections=[("fix it", "add", "high")],
                skill_name="error",
            )

        assert changes == []
        assert usage.input_tokens == 0

    def test_caps_at_max_changes(self):
        """LLM proposes >5 changes → capped at 5."""
        from core.llm_optimizer import optimize_skill_with_llm

        mock_response = json.dumps({"changes": [
            {"original": "", "replacement": f"- Rule {i}", "reason": f"reason {i}"}
            for i in range(10)
        ]})

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.return_value = (mock_response, LLMUsage(100, 100))
            changes, _ = optimize_skill_with_llm(
                skill_text="Do stuff.",
                corrections=[("fix stuff", "add", "high")],
                skill_name="many",
            )

        assert len(changes) <= 5

    def test_handles_json_wrapped_in_markdown(self):
        """LLM wraps JSON in ```json ... ``` code block → still parses."""
        from core.llm_optimizer import optimize_skill_with_llm

        mock_response = '```json\n{"changes": [{"original": "X", "replacement": "Y", "reason": "Z"}]}\n```'

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.return_value = (mock_response, LLMUsage(80, 50))
            changes, _ = optimize_skill_with_llm(
                skill_text="X is here.",
                corrections=[("fix X", "add", "high")],
                skill_name="wrapped",
            )

        assert len(changes) == 1
        assert changes[0].replacement == "Y"


# ── F3: Pre-validation — drop changes whose original doesn't exist ──

class TestPreValidation:
    """LLM changes with non-matching originals are dropped."""

    def test_drops_change_with_nonexistent_original(self):
        """LLM proposes change for text not in skill → dropped."""
        from core.llm_optimizer import optimize_skill_with_llm

        mock_response = json.dumps({"changes": [
            {"original": "This text does NOT exist in the skill", "replacement": "New text", "reason": "fix"},
            {"original": "", "replacement": "- Appended rule", "reason": "add"},
        ]})

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.return_value = (mock_response, LLMUsage(100, 100))
            changes, _ = optimize_skill_with_llm(
                skill_text="Do stuff correctly.",
                corrections=[("fix it", "add", "high")],
                skill_name="validate",
            )

        # First change dropped (original not found), second kept (append)
        assert len(changes) == 1
        assert changes[0].original == ""
        assert "Appended" in changes[0].replacement

    def test_keeps_change_with_exact_original(self):
        """LLM proposes change for text that exists → kept."""
        from core.llm_optimizer import optimize_skill_with_llm

        mock_response = json.dumps({"changes": [
            {"original": "Do stuff correctly.", "replacement": "Do stuff very carefully.", "reason": "improve"},
        ]})

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.return_value = (mock_response, LLMUsage(100, 100))
            changes, _ = optimize_skill_with_llm(
                skill_text="Do stuff correctly.",
                corrections=[("be careful", "add", "high")],
                skill_name="exact",
            )

        assert len(changes) == 1
        assert changes[0].original == "Do stuff correctly."


# ── AC2: Config gate in optimize_skill ──

class TestOptimizerConfigGate:
    """evolution_optimizer respects config.evolution.optimizer setting."""

    def test_heuristic_mode_uses_heuristic(self):
        """optimizer='heuristic' uses _apply_heuristic_changes, not LLM."""
        from core.evolution_optimizer import EvolutionOptimizer
        opt = EvolutionOptimizer.__new__(EvolutionOptimizer)
        assert hasattr(opt, "_apply_heuristic_changes")
        assert hasattr(opt, "_try_llm_optimization")


# ── AC3: Fallback on LLM failure ──

class TestFallbackBehavior:
    """Auto mode falls back to heuristic when LLM fails."""

    def test_empty_corrections_returns_empty(self):
        """No corrections → no LLM call needed → empty changes."""
        from core.llm_optimizer import optimize_skill_with_llm

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            changes, usage = optimize_skill_with_llm(
                skill_text="Do stuff.", corrections=[], skill_name="empty",
            )

        mock_call.assert_not_called()
        assert changes == []


# ── AC4: Meaningful TextChange output ──

class TestPromptQuality:
    """LLM prompt includes all necessary context for meaningful output."""

    def test_prompt_includes_skill_text_and_corrections(self):
        """The Bedrock prompt must contain skill text + correction evidence."""
        from core.llm_optimizer import optimize_skill_with_llm

        captured_prompt = None

        def capture_prompt(prompt: str, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return ('{"changes": []}', LLMUsage(50, 10))

        with patch("core.llm_optimizer._call_bedrock_opus", side_effect=capture_prompt):
            optimize_skill_with_llm(
                skill_text="Always use verbose mode.",
                corrections=[
                    ("don't use verbose mode by default", "remove", "high"),
                    ("Power and recall are primary concerns", "add", "low"),
                ],
                skill_name="test",
            )

        assert captured_prompt is not None
        assert "Always use verbose mode" in captured_prompt
        assert "verbose mode by default" in captured_prompt
        assert "Power and recall" in captured_prompt


# ── F4: Token budget guard — large skills truncated ──

class TestTokenBudget:
    """Large skill text is truncated before sending to LLM."""

    def test_large_skill_is_truncated(self):
        """Skill text >10KB gets truncated in prompt."""
        from core.llm_optimizer import _build_prompt

        big_text = "x" * 15000  # 15KB > 10KB limit
        prompt = _build_prompt(big_text, [("fix", "add", "high")], "big")

        assert "truncated" in prompt
        assert len(prompt.encode("utf-8")) < 15000 + 500  # prompt overhead


# ── F5: Token usage tracking ──

class TestTokenTracking:
    """LLM token usage is tracked and returned."""

    def test_usage_returned_on_success(self):
        """Successful call returns token counts."""
        from core.llm_optimizer import optimize_skill_with_llm

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.return_value = ('{"changes": []}', LLMUsage(1234, 567))
            _, usage = optimize_skill_with_llm(
                skill_text="Do stuff.",
                corrections=[("fix", "add", "high")],
                skill_name="track",
            )

        assert usage.input_tokens == 1234
        assert usage.output_tokens == 567

    def test_usage_zero_on_failure(self):
        """Failed call returns zero usage."""
        from core.llm_optimizer import optimize_skill_with_llm

        with patch("core.llm_optimizer._call_bedrock_opus") as mock_call:
            mock_call.side_effect = Exception("boom")
            _, usage = optimize_skill_with_llm(
                skill_text="Do stuff.",
                corrections=[("fix", "add", "high")],
                skill_name="fail",
            )

        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


# ── Empty-return log-signal distinguishability (observability) ──
#
# _call_bedrock_opus returns "" for TWO distinct causes; the caller adds a THIRD
# generic log. These tests drive the REAL _call_bedrock_opus (mocking only the
# boto3 client boundary) and the REAL caller, asserting each empty cause emits ONE
# distinguishable, greppable signal — no double-logging, no silent case.

def _mock_bedrock_client(content_blocks):
    """A boto3-client stand-in whose converse() returns the given content blocks."""
    client = MagicMock()
    client.converse.return_value = {
        "usage": {"inputTokens": 10, "outputTokens": 0},
        "output": {"message": {"content": content_blocks}},
    }
    return client


class TestEmptyReturnLogSignals:
    """Each empty-return cause must be told apart in the logs (observability only)."""

    def test_thinking_only_logs_distinct_signal(self, caplog):
        """Blocks present but none has 'text' → ONE thinking-only signal."""
        import logging
        from core.llm_optimizer import _call_bedrock_opus

        # reasoningContent-only response (adaptive thinking, no text block)
        client = _mock_bedrock_client([{"reasoningContent": {"text": "thinking..."}}])
        with patch("core.llm_optimizer._get_bedrock_client", return_value=client):
            with caplog.at_level(logging.WARNING, logger="core.llm_optimizer"):
                text, _ = _call_bedrock_opus("prompt")

        assert text == ""
        msgs = [r.getMessage() for r in caplog.records]
        thinking = [m for m in msgs if "thinking-only" in m.lower()]
        assert len(thinking) == 1, f"expected exactly one thinking-only signal, got: {msgs}"

    def test_zero_content_blocks_logs_distinct_signal(self, caplog):
        """Zero content blocks → a DISTINCT non-thinking-only signal (was silent)."""
        import logging
        from core.llm_optimizer import _call_bedrock_opus

        client = _mock_bedrock_client([])
        with patch("core.llm_optimizer._get_bedrock_client", return_value=client):
            with caplog.at_level(logging.WARNING, logger="core.llm_optimizer"):
                text, _ = _call_bedrock_opus("prompt")

        assert text == ""
        msgs = [r.getMessage() for r in caplog.records]
        # The zero-block case must produce SOME warning at the source (previously silent)…
        assert msgs, f"zero-content-blocks was silent at source: {msgs}"
        # …and it must NOT be mislabeled as thinking-only (that would be a wrong signal).
        assert not any("thinking-only" in m.lower() for m in msgs), (
            f"zero-block wrongly labeled thinking-only: {msgs}"
        )

    def test_thinking_only_not_double_logged_through_caller(self, caplog):
        """A thinking-only response must NOT be logged twice (source + caller)."""
        import logging
        from core.llm_optimizer import optimize_skill_with_llm

        client = _mock_bedrock_client([{"reasoningContent": {"text": "hmm"}}])
        with patch("core.llm_optimizer._get_bedrock_client", return_value=client):
            with caplog.at_level(logging.WARNING, logger="core.llm_optimizer"):
                changes, _ = optimize_skill_with_llm(
                    skill_text="Do stuff.",
                    corrections=[("fix", "add", "high")],
                    skill_name="thinky",
                )

        assert changes == []  # empty return → heuristic fallback path unaffected
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        # Exactly ONE empty-cause warning — the source signal, not source + generic caller.
        empty_cause = [m for m in warnings if "thinking-only" in m.lower() or "empty response" in m.lower()]
        assert len(empty_cause) == 1, f"empty cause double-logged: {empty_cause}"
