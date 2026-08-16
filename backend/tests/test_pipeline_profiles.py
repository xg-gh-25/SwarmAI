"""Tests for pipeline profile definitions.

Ensures all profiles return correct stage sequences, fallback behavior works,
and the goal profile is properly defined. Achieves 100% coverage of
core/pipeline_profiles.py.
"""
import pytest

from core.pipeline_profiles import (
    PIPELINE_PROFILES,
    get_profile_stages,
    normalize_profile,
    is_relaxed_profile,
)


class TestPipelineProfiles:
    """Verify the PIPELINE_PROFILES dictionary structure."""

    def test_all_profiles_present(self):
        """All 6 profiles exist."""
        expected = {"full", "trivial", "research", "docs", "bugfix", "goal"}
        assert set(PIPELINE_PROFILES.keys()) == expected

    def test_no_empty_stage_lists(self):
        """Every profile has at least 1 stage."""
        for name, stages in PIPELINE_PROFILES.items():
            assert len(stages) > 0, f"Profile '{name}' has empty stage list"

    def test_all_stages_are_strings(self):
        """Every stage in every profile is a non-empty string."""
        for name, stages in PIPELINE_PROFILES.items():
            for stage in stages:
                assert isinstance(stage, str) and len(stage) > 0, (
                    f"Profile '{name}' has invalid stage: {stage!r}"
                )

    @pytest.mark.parametrize("profile,expected", [
        ("full", ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"]),
        ("trivial", ["evaluate", "think", "build", "review", "test", "deliver", "reflect"]),
        ("research", ["evaluate", "think", "reflect"]),
        ("docs", ["evaluate", "think", "plan", "deliver", "reflect"]),
        ("bugfix", ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"]),
        ("goal", ["evaluate", "think", "plan", "goal_cycle", "deliver", "reflect"]),
    ])
    def test_profile_stages_correct(self, profile, expected):
        """Each profile returns its documented stage sequence."""
        assert PIPELINE_PROFILES[profile] == expected

    def test_evaluate_in_all_profiles(self):
        """Every profile starts with evaluate."""
        for name, stages in PIPELINE_PROFILES.items():
            assert stages[0] == "evaluate", f"Profile '{name}' doesn't start with evaluate"


class TestGetProfileStages:
    """Verify get_profile_stages() function behavior."""

    def test_known_profile(self):
        """Known profile name returns its stages."""
        assert get_profile_stages("full") == PIPELINE_PROFILES["full"]
        assert get_profile_stages("goal") == ["evaluate", "think", "plan", "goal_cycle", "deliver", "reflect"]

    def test_none_defaults_to_full(self):
        """None input defaults to full profile."""
        assert get_profile_stages(None) == PIPELINE_PROFILES["full"]

    def test_unknown_falls_back_to_full(self):
        """Unknown profile name falls back to full."""
        assert get_profile_stages("nonexistent") == PIPELINE_PROFILES["full"]
        assert get_profile_stages("") == PIPELINE_PROFILES["full"]

    def test_return_is_list_not_reference(self):
        """Returned list is the actual profile list (not a copy — by design)."""
        result = get_profile_stages("goal")
        assert result is PIPELINE_PROFILES["goal"]


class TestNormalizeProfile:
    """C3 SSOT: profile normalization is the single source of truth for
    canonicalizing a profile value before ANY strict/relaxed gate decision.
    Root cause of C3: a profile variant like 'Full' got full's stage list
    (get_profile_stages fallback) but slipped past deliver's hardcoded
    `profile in ('full','bugfix','')` adversarial-enforcement literals."""

    @pytest.mark.parametrize("raw,expected", [
        ("full", "full"),
        ("Full", "full"),          # case variant — the C3 bypass
        ("FULL", "full"),
        ("  full  ", "full"),      # whitespace
        ("bugfix", "bugfix"),
        ("BugFix", "bugfix"),
        ("standard", "full"),      # legacy alias (rank-4 == full)
        ("Standard", "full"),
        ("trivial", "trivial"),
        ("docs", "docs"),
        ("research", "research"),
        ("goal", "goal"),
        (None, "full"),            # None → full (matches get_profile_stages)
        ("", "full"),              # empty → full (matches strict-set '' semantics)
        ("   ", "full"),
    ])
    def test_normalize_known_and_variants(self, raw, expected):
        assert normalize_profile(raw) == expected

    def test_normalize_unknown_lowercased_not_full(self):
        """An unknown profile normalizes to its lowercased form (NOT silently
        remapped to full) — so is_relaxed_profile can fail-closed to strict on it.
        (get_profile_stages still falls back to full's STAGE LIST separately.)"""
        assert normalize_profile("xyz") == "xyz"
        assert normalize_profile("Ful") == "ful"   # typo → not relaxed → strict

    def test_get_profile_stages_normalizes(self):
        """C3: get_profile_stages must honor normalization — 'Full'/'FULL'/'standard'
        all resolve to full's stage list (previously only exact 'full' did)."""
        full = PIPELINE_PROFILES["full"]
        assert get_profile_stages("Full") == full
        assert get_profile_stages("FULL") == full
        assert get_profile_stages("standard") == full
        assert get_profile_stages("  bugfix  ") == PIPELINE_PROFILES["bugfix"]


class TestIsRelaxedProfile:
    """C3 SSOT: the single relaxed/strict predicate. relaxed = {trivial,docs,research};
    EVERYTHING ELSE (incl. unknown/variant) is strict — fail-closed. This replaces
    the 6+ scattered `profile in ('full','bugfix','')` (fail-OPEN) literals whose
    inconsistency let 'Full' skip adversarial enforcement."""

    @pytest.mark.parametrize("profile", ["trivial", "docs", "research",
                                          "Trivial", "DOCS", "  research  "])
    def test_relaxed_profiles(self, profile):
        assert is_relaxed_profile(profile) is True

    @pytest.mark.parametrize("profile", ["full", "bugfix", "goal", "standard",
                                          "Full", "FULL", "BugFix", ""])
    def test_strict_profiles(self, profile):
        assert is_relaxed_profile(profile) is False

    def test_unknown_is_strict_fail_closed(self):
        """The C3 core fix: an unknown/variant profile is STRICT (fail-closed),
        never relaxed — so a typo can never silently skip adversarial enforcement."""
        assert is_relaxed_profile("xyz") is False
        assert is_relaxed_profile("Ful") is False
        assert is_relaxed_profile(None) is False
