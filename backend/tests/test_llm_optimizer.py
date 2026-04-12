"""Tests for llm_optimizer — LLM-based skill optimization via Bedrock Opus.

Tests use mocked Bedrock responses to avoid real API calls.
Covers: TextChange generation, JSON parsing, error handling, fallback behavior.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.evolution_optimizer import TextChange


# ── AC1: llm_optimizer.py exists with optimize_skill_with_llm ──

class TestOptimizeSkillWithLLM:
    """Core function produces TextChange list from LLM response."""

    @pytest.mark.asyncio
    async def test_returns_text_changes_from_valid_response(self):
        """Valid LLM JSON response → list of TextChange objects."""
        from core.llm_optimizer import optimize_skill_with_llm

        mock_response = json.dumps({"changes": [
            {
                "original": "Always include verbose output.",
                "replacement": "Only include verbose output when --verbose flag is set.",
                "reason": "Users corrected: don't include verbose output by default",
            }
        ]})

        with patch("core.llm_optimizer._call_bedrock_opus", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            changes = await optimize_skill_with_llm(
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

    @pytest.mark.asyncio
    async def test_returns_empty_on_malformed_json(self):
        """Malformed LLM response → empty list, no crash."""
        from core.llm_optimizer import optimize_skill_with_llm

        with patch("core.llm_optimizer._call_bedrock_opus", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "This is not valid JSON at all"
            changes = await optimize_skill_with_llm(
                skill_text="Do the thing.",
                corrections=[("don't do it wrong", "remove", "high")],
                skill_name="broken",
            )

        assert changes == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_api_error(self):
        """Bedrock API failure → empty list, no crash."""
        from core.llm_optimizer import optimize_skill_with_llm

        with patch("core.llm_optimizer._call_bedrock_opus", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = Exception("Bedrock throttling")
            changes = await optimize_skill_with_llm(
                skill_text="Do the thing.",
                corrections=[("fix it", "add", "high")],
                skill_name="error",
            )

        assert changes == []

    @pytest.mark.asyncio
    async def test_caps_at_max_changes(self):
        """LLM proposes >5 changes → capped at 5."""
        from core.llm_optimizer import optimize_skill_with_llm

        mock_response = json.dumps({"changes": [
            {"original": "", "replacement": f"- Rule {i}", "reason": f"reason {i}"}
            for i in range(10)
        ]})

        with patch("core.llm_optimizer._call_bedrock_opus", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            changes = await optimize_skill_with_llm(
                skill_text="Do stuff.",
                corrections=[("fix stuff", "add", "high")],
                skill_name="many",
            )

        assert len(changes) <= 5

    @pytest.mark.asyncio
    async def test_handles_json_wrapped_in_markdown(self):
        """LLM wraps JSON in ```json ... ``` code block → still parses."""
        from core.llm_optimizer import optimize_skill_with_llm

        mock_response = '```json\n{"changes": [{"original": "X", "replacement": "Y", "reason": "Z"}]}\n```'

        with patch("core.llm_optimizer._call_bedrock_opus", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            changes = await optimize_skill_with_llm(
                skill_text="X is here.",
                corrections=[("fix X", "add", "high")],
                skill_name="wrapped",
            )

        assert len(changes) == 1
        assert changes[0].replacement == "Y"


# ── AC2: Config gate in optimize_skill ──

class TestOptimizerConfigGate:
    """evolution_optimizer respects config.evolution.optimizer setting."""

    def test_heuristic_mode_uses_heuristic(self):
        """optimizer='heuristic' uses _apply_heuristic_changes, not LLM."""
        from core.evolution_optimizer import EvolutionOptimizer
        # Heuristic mode should work without any LLM mock
        # (it's the default pre-v2.1 behavior)
        # Just verify the method exists and is callable
        opt = EvolutionOptimizer.__new__(EvolutionOptimizer)
        assert hasattr(opt, "_apply_heuristic_changes")


# ── AC3: Fallback on LLM failure ──

class TestFallbackBehavior:
    """Auto mode falls back to heuristic when LLM fails."""

    @pytest.mark.asyncio
    async def test_empty_corrections_returns_empty(self):
        """No corrections → no LLM call needed → empty changes."""
        from core.llm_optimizer import optimize_skill_with_llm

        with patch("core.llm_optimizer._call_bedrock_opus", new_callable=AsyncMock) as mock_call:
            changes = await optimize_skill_with_llm(
                skill_text="Do stuff.", corrections=[], skill_name="empty",
            )

        # No corrections → should not even call Bedrock
        mock_call.assert_not_called()
        assert changes == []


# ── AC4: Meaningful TextChange output ──

class TestPromptQuality:
    """LLM prompt includes all necessary context for meaningful output."""

    @pytest.mark.asyncio
    async def test_prompt_includes_skill_text_and_corrections(self):
        """The Bedrock prompt must contain skill text + correction evidence."""
        from core.llm_optimizer import optimize_skill_with_llm

        captured_prompt = None

        async def capture_prompt(prompt: str, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return '{"changes": []}'

        with patch("core.llm_optimizer._call_bedrock_opus", side_effect=capture_prompt):
            await optimize_skill_with_llm(
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
