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

    def test_default_model_is_the_registry_flagship(self):
        """Default model must be the registry's flagship, whatever that is.

        This used to assert a LITERAL model name, which made the test a second
        place to update on every model release — and it silently encoded a
        default that had already drifted two generations behind the live config.
        Asserting the derivation instead survives a flagship promotion and still
        catches a real regression (DEFAULT_CONFIG no longer deriving).
        """
        from model_registry import FLAGSHIP_MODEL
        assert DEFAULT_CONFIG["default_model"] == FLAGSHIP_MODEL

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
    """Tests for PromptBuilder 1M model detection.

    These assert BEHAVIOR (the family predicate + the resolved window), not the
    presence of a constant. They previously asserted membership in a
    ``_1M_MODELS`` set and a ``_MODEL_CONTEXT_WINDOWS`` dict; both were removed
    when the hardcoded tables were replaced by family derivation, so the tests
    failed with AttributeError — a red suite that verified nothing. Asserting
    behavior instead survives the implementation change AND actually checks the
    property that matters.
    """

    def test_opus_4_8_is_1m(self):
        """claude-opus-4-8 recognized as 1M context model."""
        assert PromptBuilder._is_1m_model("claude-opus-4-8") is True

    def test_opus_4_8_context_window(self):
        """claude-opus-4-8 has 1M context window."""
        assert PromptBuilder.get_model_context_window("claude-opus-4-8") == 1_000_000

    def test_opus_4_6_still_1m(self):
        """claude-opus-4-6 still recognized as 1M."""
        assert PromptBuilder._is_1m_model("claude-opus-4-6") is True
        assert PromptBuilder.get_model_context_window("claude-opus-4-6") == 1_000_000

    def test_sonnet_4_6_still_1m(self):
        """claude-sonnet-4-6 still recognized as 1M."""
        assert PromptBuilder._is_1m_model("claude-sonnet-4-6") is True
        assert PromptBuilder.get_model_context_window("claude-sonnet-4-6") == 1_000_000

    def test_newer_flagship_is_1m_without_a_code_edit(self):
        """A model NEWER than any the tables knew must still resolve to 1M.

        This is the property the old hardcoded set could not have: a newly
        promoted opus was absent from it and would have silently run below its
        real context window.
        """
        from model_registry import FLAGSHIP_MODEL
        assert PromptBuilder._is_1m_model(FLAGSHIP_MODEL) is True
        assert PromptBuilder.get_model_context_window(FLAGSHIP_MODEL) == 1_000_000

    def test_non_1m_model_does_not_claim_1m(self):
        """haiku is NOT 1M — and its window must agree with that."""
        assert PromptBuilder._is_1m_model("claude-haiku-3") is False
        assert PromptBuilder.get_model_context_window("claude-haiku-3") != 1_000_000


class TestSessionAwareThinking:
    """Tests for session-type-aware thinking config.

    Desktop sessions (channel_context=None) use global config as-is.
    Channel sessions (channel_context set) force adaptive thinking,
    because channels run unattended and should let the model skip
    thinking on simple questions (cost/latency). A globally *disabled*
    mode is a hard kill that channel override must NOT resurrect.
    """

    def test_desktop_enabled_returns_enabled(self):
        """AC1: desktop session + global enabled → enabled config."""
        pb = PromptBuilder({"thinking_mode": "enabled", "thinking_budget_tokens": 12000})
        cfg = pb._build_thinking_config(channel_context=None)
        assert cfg == {"type": "enabled", "budget_tokens": 12000}

    def test_channel_enabled_forces_adaptive(self):
        """AC2: channel session + global enabled → adaptive override."""
        pb = PromptBuilder({"thinking_mode": "enabled", "thinking_budget_tokens": 12000})
        cfg = pb._build_thinking_config(channel_context={"is_owner": True})
        assert cfg == {"type": "adaptive"}

    def test_channel_disabled_stays_disabled(self):
        """AC3: channel override never resurrects a globally disabled mode."""
        pb = PromptBuilder({"thinking_mode": "disabled"})
        cfg = pb._build_thinking_config(channel_context={"is_owner": True})
        assert cfg == {"type": "disabled"}

    def test_channel_adaptive_effort_not_none(self):
        """AC4: channel (forced adaptive) still yields a valid effort string."""
        pb = PromptBuilder({"thinking_mode": "enabled", "thinking_effort": "high"})
        effort = pb._build_effort(channel_context={"is_owner": True})
        assert effort == "high"

    def test_desktop_disabled_effort_none(self):
        """AC5: desktop disabled-mode short-circuit preserved — effort is None."""
        pb = PromptBuilder({"thinking_mode": "disabled", "thinking_effort": "max"})
        effort = pb._build_effort(channel_context=None)
        assert effort is None

    def test_channel_disabled_effort_none(self):
        """AC3+AC5: channel + globally disabled → effort still None (no resurrect)."""
        pb = PromptBuilder({"thinking_mode": "disabled", "thinking_effort": "max"})
        effort = pb._build_effort(channel_context={"is_owner": True})
        assert effort is None

    def test_desktop_default_adaptive_unchanged(self):
        """Regression: desktop with no thinking_mode set stays adaptive."""
        pb = PromptBuilder({"thinking_effort": "max"})
        assert pb._build_thinking_config(channel_context=None) == {"type": "adaptive"}

    def test_with_real_app_config_manager(self):
        """Production semantics: PromptBuilder uses an AppConfigManager (always
        truthy, no __bool__/__len__), not a plain dict. Verify channel override
        works against the real config object the rest of the tests stub with a
        dict — guards against the `not self._config` vs `is None` distinction."""
        from core.app_config_manager import AppConfigManager

        mgr = AppConfigManager()
        mgr._cache = dict(mgr.load())
        mgr._cache["thinking_mode"] = "enabled"
        mgr._cache["thinking_effort"] = "high"
        pb = PromptBuilder(mgr)
        # Desktop honors the global enabled mode.
        assert pb._build_thinking_config(channel_context=None)["type"] == "enabled"
        # Channel forces adaptive even with the real (always-truthy) manager.
        assert pb._build_thinking_config(channel_context={"is_owner": True}) == {"type": "adaptive"}
        assert pb._build_effort(channel_context={"is_owner": True}) == "high"


class TestThinkingDisplay:
    """Tests for thinking-display resolution.

    Opus 4.8 changed the thinking `display` default from "summarized" (4.6) to
    "omitted" — the model streams empty thinking blocks, so the desktop UI shows
    a "Thinking…" spinner with no reasoning content. The fix sets the CLI flag
    `--thinking-display summarized` via extra_args. It does NOT go in the thinking
    dict — the Python claude_agent_sdk silently drops any subfield other than
    `type`/`budget_tokens` (subprocess_cli.py L300-313), and ThinkingConfigAdaptive
    has no `display` field. extra_args is the correct transport.

    Policy: desktop gets `summarized`; channel sessions skip it (zero-streaming,
    thinking summary not rendered to Slack) — mirrors the _build_effort channel cap.
    Disabled thinking → no display (no thinking to show).
    """

    def test_desktop_default_returns_summarized(self):
        """AC1: desktop session, thinking active → summarized."""
        pb = PromptBuilder({"thinking_mode": "adaptive"})
        assert pb._build_thinking_display(channel_context=None) == "summarized"

    def test_desktop_enabled_returns_summarized(self):
        """AC1: desktop session, enabled mode → still summarized."""
        pb = PromptBuilder({"thinking_mode": "enabled", "thinking_budget_tokens": 12000})
        assert pb._build_thinking_display(channel_context=None) == "summarized"

    def test_desktop_disabled_returns_none(self):
        """AC2: thinking disabled → no display (no thinking to show)."""
        pb = PromptBuilder({"thinking_mode": "disabled"})
        assert pb._build_thinking_display(channel_context=None) is None

    def test_channel_returns_none(self):
        """AC3: channel session → no display (zero-streaming, not rendered)."""
        pb = PromptBuilder({"thinking_mode": "adaptive"})
        assert pb._build_thinking_display(channel_context={"is_owner": True}) is None

    def test_channel_disabled_returns_none(self):
        """AC3+AC2: channel + disabled → None (both reasons agree)."""
        pb = PromptBuilder({"thinking_mode": "disabled"})
        assert pb._build_thinking_display(channel_context={"is_owner": True}) is None

    def test_none_config_returns_summarized(self):
        """Defensive: None config falls through to summarized (matches _build_effort)."""
        pb = PromptBuilder({})
        pb._config = None
        assert pb._build_thinking_display(channel_context=None) == "summarized"

    @pytest.mark.asyncio
    async def test_build_options_emits_dash_free_key_desktop(self):
        """AC4 + Gate-1 PIT59 guard: build_options must put the flag in extra_args
        under the dash-free key 'thinking-display'. The SDK renders extra_args as
        f'--{key}', so a key of '--thinking-display' would emit '----thinking-display'
        and silently fail — the exact silent-drop class this fix exists to defeat.

        Exercises the real async build_options path (integration) and asserts the
        extra_args KEY is dash-free (the SDK adds the '--'). The wire-level rendering
        itself (cmd.extend([f'--{k}', v])) is verified separately by the BUILD-stage
        smoke test, not re-invoked here.
        """
        from core.app_config_manager import AppConfigManager

        mgr = AppConfigManager()
        mgr._cache = dict(mgr.load())
        mgr._cache["thinking_mode"] = "adaptive"
        pb = PromptBuilder(mgr)
        opts = await pb.build_options(
            agent_config={},
            enable_skills=False,
            enable_mcp=False,
            channel_context=None,
        )
        assert "thinking-display" in opts.extra_args
        assert "--thinking-display" not in opts.extra_args  # dashes are the SDK's job
        assert opts.extra_args["thinking-display"] == "summarized"

    @pytest.mark.asyncio
    async def test_build_options_omits_display_for_channel(self):
        """AC4: channel session → no thinking-display key in extra_args."""
        from core.app_config_manager import AppConfigManager

        mgr = AppConfigManager()
        mgr._cache = dict(mgr.load())
        mgr._cache["thinking_mode"] = "adaptive"
        pb = PromptBuilder(mgr)
        opts = await pb.build_options(
            agent_config={},
            enable_skills=False,
            enable_mcp=False,
            channel_context={"is_owner": True},
        )
        assert "thinking-display" not in opts.extra_args
