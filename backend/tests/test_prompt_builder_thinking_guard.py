"""Tests for the model-aware thinking-mode guard in PromptBuilder.

Opus 5+ rejects ``thinking.type`` "disabled"/"enabled" with a 400 on the wire.
A user can set ``thinking_mode: disabled`` in config.json, then switch the model
to opus-5-5 — the persisted config would then send a mode the model 400s on.

The guard (``_guard_thinking_for_model``) runs at the build_options call site,
where the resolved model is already in scope, and down-grades a disabled/enabled
thinking_config to adaptive ONLY for a model that rejects it. It must NOT touch
a model that genuinely supports disabled (opus-4-8, sonnet), and must NOT touch
an already-adaptive config.

Mutation-proof: removing the guard body (returning thinking_config unchanged)
turns test_opus_5_5_disabled_downgraded_to_adaptive RED — on the real
model_registry derivation, not a mock.

# Feature: opus-5-5-registry
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.prompt_builder import PromptBuilder


def _builder() -> PromptBuilder:
    return PromptBuilder(config=MagicMock())


class TestThinkingGuard:
    """model-aware down-grade of disabled/enabled → adaptive for opus>=5."""

    def test_opus_5_5_disabled_downgraded_to_adaptive(self):
        """opus-5-5 + disabled → adaptive (the core guard behavior).

        Teeth: deleting the guard body (pass-through) makes this RED.
        """
        b = _builder()
        out = b._guard_thinking_for_model({"type": "disabled"}, "claude-opus-5-5")
        assert out == {"type": "adaptive"}

    def test_opus_5_5_enabled_budget_downgraded_to_adaptive(self):
        """opus-5-5 + enabled(+budget) → adaptive (also a 400 on the wire)."""
        b = _builder()
        out = b._guard_thinking_for_model(
            {"type": "enabled", "budget_tokens": 10000}, "claude-opus-5-5"
        )
        assert out == {"type": "adaptive"}

    def test_bedrock_prefixed_model_also_guarded(self):
        b = _builder()
        out = b._guard_thinking_for_model(
            {"type": "disabled"}, "us.anthropic.claude-opus-5-5"
        )
        assert out == {"type": "adaptive"}

    def test_opus_4_8_disabled_is_NOT_touched(self):
        """opus-4-8 genuinely supports disabled — no false-trip."""
        b = _builder()
        cfg = {"type": "disabled"}
        out = b._guard_thinking_for_model(cfg, "claude-opus-4-8")
        assert out == {"type": "disabled"}

    def test_sonnet_disabled_is_NOT_touched(self):
        b = _builder()
        out = b._guard_thinking_for_model({"type": "disabled"}, "claude-sonnet-4-6")
        assert out == {"type": "disabled"}

    def test_adaptive_config_unchanged_for_opus_5_5(self):
        """An already-adaptive config is passed through untouched."""
        b = _builder()
        cfg = {"type": "adaptive"}
        out = b._guard_thinking_for_model(cfg, "claude-opus-5-5")
        assert out == {"type": "adaptive"}

    def test_none_thinking_config_is_safe(self):
        """A None thinking_config (disabled-via-None path) must not crash."""
        b = _builder()
        assert b._guard_thinking_for_model(None, "claude-opus-5-5") is None

    def test_none_model_is_safe(self):
        """No resolved model yet → do not guess, leave config untouched."""
        b = _builder()
        cfg = {"type": "disabled"}
        assert b._guard_thinking_for_model(cfg, None) == {"type": "disabled"}


class TestEffectiveThinkingMode:
    """ONE rule decides the thinking mode a model actually runs with; thinking
    config, effort and display all follow it (a downgraded session must be a
    normal adaptive session, not one with effort/display silently dropped)."""

    def test_registry_supported_and_effective_modes(self):
        from model_registry import effective_thinking_mode, supported_thinking_modes

        assert supported_thinking_modes("claude-opus-5-5") == ["adaptive"]
        assert supported_thinking_modes("us.anthropic.claude-opus-5-5[1m]") == ["adaptive"]
        assert supported_thinking_modes("claude-opus-4-8") == ["adaptive", "enabled", "disabled"]
        assert effective_thinking_mode("disabled", "claude-opus-5-5") == "adaptive"
        assert effective_thinking_mode("enabled", "claude-opus-5-5") == "adaptive"
        assert effective_thinking_mode("disabled", "claude-opus-4-8") == "disabled"
        assert effective_thinking_mode("disabled", None) == "disabled", "unresolved model: do not guess"
        assert effective_thinking_mode(None, "claude-opus-5-5") == "adaptive"

    def test_downgraded_session_keeps_effort_and_display(self):
        pb = PromptBuilder({"thinking_mode": "disabled", "thinking_effort": "max"})
        effective = "adaptive"  # what opus-5-5 runs with
        assert pb._build_effort(channel_context=None, effective_mode=effective) == "max"
        assert pb._build_thinking_display(channel_context=None, effective_mode=effective) == "summarized"

    def test_model_that_accepts_disabled_is_unchanged(self):
        pb = PromptBuilder({"thinking_mode": "disabled", "thinking_effort": "max"})
        assert pb._build_effort(channel_context=None, effective_mode="disabled") is None
        assert pb._build_thinking_display(channel_context=None, effective_mode="disabled") is None

    def test_guard_agrees_with_registry_rule(self):
        from model_registry import effective_thinking_mode

        b = _builder()
        for model in ("claude-opus-5-5", "claude-opus-4-8", "claude-sonnet-4-6"):
            for mode in ("disabled", "enabled"):
                cfg = {"type": mode, **({"budget_tokens": 1000} if mode == "enabled" else {})}
                assert b._guard_thinking_for_model(cfg, model)["type"] == effective_thinking_mode(mode, model)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model,thinking,effort,display", [
        ("claude-opus-5-5", {"type": "adaptive"}, "max", "summarized"),
        ("claude-opus-4-8", {"type": "disabled"}, None, None),
    ])
    async def test_build_options_runs_the_effective_mode(self, model, thinking, effort, display):
        """Drive the real build_options: thinking, effort and --thinking-display
        must all follow the mode the model actually runs with."""
        cfg = MagicMock()
        values = {"thinking_mode": "disabled", "thinking_effort": "max"}
        cfg.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
        cfg.get_model = MagicMock(return_value=model)
        pb = PromptBuilder(cfg)
        options = await pb.build_options(
            agent_config={"model": model},
            enable_skills=False, enable_mcp=False, resume_session_id=None,
            session_context={"sdk_session_id": "sid"}, channel_context=None,
            editor_context=None, terminal_context=None,
            cached_system_prompt="CACHED",
        )
        assert options.thinking == thinking
        assert options.effort == effort
        assert options.extra_args.get("thinking-display") == display


@pytest.mark.asyncio
async def test_arn_mapped_model_is_judged_by_its_name():
    """A bedrock_model_map value can be a custom ARN with no family version; the
    thinking rule must judge the model by its NAME, or a stored 'disabled'
    would be sent to opus-5-5 and 400."""
    values = {
        "thinking_mode": "disabled", "thinking_effort": "high", "use_bedrock": True,
        "default_model": "claude-opus-5-5",
        "bedrock_model_map": {
            "claude-opus-5-5": "arn:aws:bedrock:us-east-1:123:application-inference-profile/abc",
        },
    }
    cfg = MagicMock()
    cfg.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    pb = PromptBuilder(cfg)
    options = await pb.build_options(
        agent_config={}, enable_skills=False, enable_mcp=False, resume_session_id=None,
        session_context={"sdk_session_id": "sid"}, channel_context=None,
        editor_context=None, terminal_context=None, cached_system_prompt="CACHED",
    )
    assert options.thinking == {"type": "adaptive"}
    assert options.effort == "high"
