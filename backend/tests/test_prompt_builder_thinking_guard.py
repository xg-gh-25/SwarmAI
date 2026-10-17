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
