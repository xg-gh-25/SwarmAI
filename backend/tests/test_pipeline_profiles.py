"""Pipeline profile tables (design docs/design/2026-10-26-pipeline-two-ends.md §4).

What is tested:
- core.pipeline_profiles.PIPELINE_PROFILES is only a view of lifecycle.PROFILE_STAGES;
- lifecycle.PROFILE_CAPS names exactly the profiles of PROFILE_STAGES, and each row
  matches the stage list (lands_code <-> DELIVER lands; a design <-> PLAN; Gate-0 only
  with EVALUATE);
- removed profiles (goal, docs) are refused with what to do instead;
- the helpers that once lived in pipeline_profiles (gates, rigor, aliases) are gone.
"""
import pytest

import core.pipeline_profiles as pp
from core.run_protocol import lifecycle

STAGES = {
    "full": ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"],
    "bugfix": ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"],
    "trivial": ["evaluate", "plan", "build", "review", "test", "deliver", "reflect"],
    "direct": ["build", "review", "test", "deliver", "reflect"],
    "research": ["evaluate", "think", "deliver", "reflect"],
}


def test_profiles_and_their_stage_lists():
    assert pp.PIPELINE_PROFILES == STAGES
    assert {k: list(v) for k, v in lifecycle.PROFILE_STAGES.items()} == STAGES


@pytest.mark.parametrize("profile", sorted(STAGES))
def test_capabilities_agree_with_the_stage_list(profile):
    caps = lifecycle.caps(profile)
    stages = lifecycle.PROFILE_STAGES[profile]
    assert caps.lands_code is ("build" in stages)
    assert (caps.design is not None) is ("plan" in stages)
    assert caps.gate0 == "never" or "evaluate" in stages
    assert (caps.requirements == "words") is ("evaluate" not in stages)
    assert caps.triad is (profile == "bugfix")


@pytest.mark.parametrize("name,hint", [("goal", "one full run"), ("docs", "edit them directly")])
def test_a_removed_profile_is_refused_with_what_to_do(name, hint):
    with pytest.raises(lifecycle.LifecycleError, match=hint):
        lifecycle.caps(name)


def test_the_old_helpers_are_gone():
    for name in ("PROFILE_GATES", "profile_gates", "is_relaxed_profile", "normalize_profile",
                 "get_profile_stages", "REMOVED_PROFILES", "FINISHED_STATUSES",
                 "DEPTH_TIERS", "normalize_depth"):
        assert not hasattr(pp, name), name
