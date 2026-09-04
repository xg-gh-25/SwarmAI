"""Tests for PromptBuilder 1M-context family detection (Bedrock auto-discovery).

Verifies the ``_is_1m_model`` family check that replaced the hardcoded
``_1M_MODELS`` set. The set required a human edit for every new Claude model,
so a newly-discovered model (e.g. ``claude-opus-5``) silently ran below its
full 1M context window because it was not in the set — the exact silent
capability-degradation class MEMORY warns about (COE 039c4f32).

Mutation-proof design: ``test_new_model_gets_1m_suffix`` goes RED if the family
check is reverted to the old hardcoded set (opus-5 was not in it).

# Feature: bedrock-model-autodiscovery
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.prompt_builder import PromptBuilder


def _make_bedrock_builder() -> PromptBuilder:
    """PromptBuilder with Bedrock enabled and an empty model map (passthrough)."""
    mock_config = MagicMock()
    mock_config.get = MagicMock(side_effect=lambda key, default=None: {
        "use_bedrock": True,
        "bedrock_model_map": {},
    }.get(key, default))
    return PromptBuilder(config=mock_config)


class TestIs1MModel:
    """The family check must recognize any Claude opus/sonnet gen>=4 as 1M-capable."""

    @pytest.mark.parametrize("base", [
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-opus-4-8",
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-opus-6",       # future model — must be forward-compatible
        "claude-sonnet-6",
    ])
    def test_family_members_are_1m(self, base):
        assert PromptBuilder._is_1m_model(base) is True

    @pytest.mark.parametrize("base", [
        "claude-3-sonnet-20240229",   # gen 3 — NOT 1M
        "claude-3-opus",
        "gpt-4o",                      # not Claude
        "llama-3",
        "",
        "claude-haiku-4",              # haiku is not opus/sonnet family
    ])
    def test_non_family_are_not_1m(self, base):
        assert PromptBuilder._is_1m_model(base) is False


class TestResolveModel1MSuffix:
    """resolve_model must append [1m] for a discovered model, driven by the family check."""

    def test_new_model_gets_1m_suffix(self):
        """A newly-discovered opus-5 (never in the old hardcoded set) must get [1m].

        Mutation guard: reverting to the hardcoded _1M_MODELS set makes this RED
        because 'claude-opus-5' was not a member.
        """
        builder = _make_bedrock_builder()
        resolved = builder.resolve_model({"model": "claude-opus-5"})
        assert resolved is not None
        assert resolved.endswith("[1m]"), f"expected [1m] suffix, got {resolved!r}"

    def test_existing_model_still_gets_1m_suffix(self):
        """Regression: the previously-hardcoded opus-4-8 must still get [1m]."""
        builder = _make_bedrock_builder()
        resolved = builder.resolve_model({"model": "claude-opus-4-8"})
        assert resolved is not None
        assert resolved.endswith("[1m]")

    def test_non_1m_model_no_suffix(self):
        """A gen-3 model must NOT get the [1m] suffix."""
        builder = _make_bedrock_builder()
        resolved = builder.resolve_model({"model": "claude-3-sonnet-20240229"})
        assert resolved is not None
        assert not resolved.endswith("[1m]")

    def test_date_suffixed_model_gets_1m_suffix(self):
        """A date-suffixed id (…-20250514) must classify on the intact name.

        Guard for the Gate-2 finding: rstrip(':0') would have eaten the trailing
        '0' of the date and mangled the base; the literal-suffix strip must not.
        """
        builder = _make_bedrock_builder()
        # ends in '0' — a char-class rstrip(":0") would corrupt the base name.
        resolved = builder.resolve_model({"model": "claude-sonnet-4-20250510"})
        assert resolved is not None
        assert resolved.endswith("[1m]")

    def test_versioned_profile_id_gets_1m_suffix(self):
        """A full inference-profile id (us.anthropic.…-v1:0) still classifies 1M."""
        builder = _make_bedrock_builder()
        resolved = builder.resolve_model({"model": "us.anthropic.claude-opus-4-6-v1:0"})
        assert resolved is not None
        assert resolved.endswith("[1m]")
