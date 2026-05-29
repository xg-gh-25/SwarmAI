"""Tests for model configuration and Bedrock model ID resolution.

Verifies that:
- All supported models have correct Bedrock inference profile mappings
- Default model is claude-opus-4-8
- 1M context window detection works for all supported models
- Unknown models pass through unchanged (custom ARN support)
"""

import pytest

from config import ANTHROPIC_TO_BEDROCK_MODEL_MAP, get_bedrock_model_id
from core.app_config_manager import DEFAULT_CONFIG
from core.prompt_builder import PromptBuilder


class TestBedrockModelMapping:
    """Tests for ANTHROPIC_TO_BEDROCK_MODEL_MAP and get_bedrock_model_id."""

    def test_opus_4_8_in_model_map(self):
        """claude-opus-4-8 has a Bedrock inference profile mapping."""
        assert "claude-opus-4-8" in ANTHROPIC_TO_BEDROCK_MODEL_MAP

    def test_opus_4_8_resolves_to_correct_profile(self):
        """claude-opus-4-8 resolves to us.anthropic.claude-opus-4-8."""
        result = get_bedrock_model_id("claude-opus-4-8")
        assert result == "us.anthropic.claude-opus-4-8"

    def test_opus_4_6_still_mapped(self):
        """claude-opus-4-6 still resolves correctly (backward compat)."""
        result = get_bedrock_model_id("claude-opus-4-6")
        assert result == "us.anthropic.claude-opus-4-6-v1"

    def test_sonnet_4_6_still_mapped(self):
        """claude-sonnet-4-6 still resolves correctly."""
        result = get_bedrock_model_id("claude-sonnet-4-6")
        assert result == "us.anthropic.claude-sonnet-4-6"

    def test_unknown_model_passes_through(self):
        """Unknown model IDs pass through unchanged (custom ARN support)."""
        custom_arn = "arn:aws:bedrock:us-east-1:123456:custom-model/my-model"
        assert get_bedrock_model_id(custom_arn) == custom_arn

    def test_config_map_override_takes_precedence(self):
        """Config map override takes precedence over hardcoded map."""
        override = {"claude-opus-4-8": "custom.profile.id"}
        result = get_bedrock_model_id("claude-opus-4-8", config_map=override)
        assert result == "custom.profile.id"


class TestDefaultConfig:
    """Tests for DEFAULT_CONFIG model settings."""

    def test_default_model_is_opus_4_8(self):
        """Default model should be claude-opus-4-8."""
        assert DEFAULT_CONFIG["default_model"] == "claude-opus-4-8"

    def test_opus_4_6_in_available_models(self):
        """claude-opus-4-6 remains available as fallback."""
        assert "claude-opus-4-6" in DEFAULT_CONFIG["available_models"]

    def test_opus_4_8_in_available_models(self):
        """claude-opus-4-8 is in available_models."""
        assert "claude-opus-4-8" in DEFAULT_CONFIG["available_models"]

    def test_sonnet_4_6_in_available_models(self):
        """claude-sonnet-4-6 still available."""
        assert "claude-sonnet-4-6" in DEFAULT_CONFIG["available_models"]

    def test_bedrock_model_map_has_opus_4_8(self):
        """bedrock_model_map in config includes opus-4-8."""
        assert "claude-opus-4-8" in DEFAULT_CONFIG["bedrock_model_map"]
        assert DEFAULT_CONFIG["bedrock_model_map"]["claude-opus-4-8"] == "us.anthropic.claude-opus-4-8"


class TestEffortControl:
    """Tests for effort level configuration."""

    def test_valid_effort_levels_includes_xhigh(self):
        """xhigh is a valid effort level (CLI supports it)."""
        assert "xhigh" in PromptBuilder._VALID_EFFORT_LEVELS

    def test_all_cli_effort_levels_valid(self):
        """All CLI-supported effort levels are in valid set."""
        expected = {"low", "medium", "high", "xhigh", "max"}
        assert expected == PromptBuilder._VALID_EFFORT_LEVELS

    def test_llm_optimizer_effort_constant(self):
        """llm_optimizer defines BEDROCK_EFFORT constant for direct API calls."""
        from core.llm_optimizer import BEDROCK_EFFORT
        assert BEDROCK_EFFORT == "low"

    def test_skill_fitness_uses_low_effort(self):
        """skill_fitness LLMJudge uses low effort for cost efficiency."""
        from core.skill_fitness import LLMJudge
        assert hasattr(LLMJudge, "EFFORT")
        assert LLMJudge.EFFORT == "low"


class TestPromptBuilderModelResolution:
    """Tests for PromptBuilder 1M model detection."""

    def test_opus_4_8_in_1m_models(self):
        """claude-opus-4-8 recognized as 1M context model."""
        assert "claude-opus-4-8" in PromptBuilder._1M_MODELS

    def test_opus_4_8_context_window(self):
        """claude-opus-4-8 has 1M context window."""
        assert PromptBuilder._MODEL_CONTEXT_WINDOWS.get("claude-opus-4-8") == 1_000_000

    def test_opus_4_6_still_in_1m_models(self):
        """claude-opus-4-6 still recognized as 1M."""
        assert "claude-opus-4-6" in PromptBuilder._1M_MODELS

    def test_sonnet_4_6_still_in_1m_models(self):
        """claude-sonnet-4-6 still recognized as 1M."""
        assert "claude-sonnet-4-6" in PromptBuilder._1M_MODELS
