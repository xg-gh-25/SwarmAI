"""Pipeline profile definitions — shared between CLI, routers, and skills.

Single source of truth for which stages each pipeline profile includes.
Imported by artifact_cli.py, routers/pipelines.py, and any future
component that needs to know pipeline stage sequences.

NOTE: The external architecture defines 9 stages (EVALUATE → THINK → PLAN →
BUILD → REVIEW → TEST → ADVERSARIAL → DELIVER → REFLECT). ADVERSARIAL is
architecturally stage 7, but in execution it is embedded inside the DELIVER
stage as a blocking gate (spawn fresh-context sub-agent before packaging).
This is why the profile lists below show 8 entries for "full" — ADVERSARIAL
is not a separate orchestration step, it's a mandatory sub-step of DELIVER.
All docs, READMEs, and external communications say "9 stages."
"""

PIPELINE_PROFILES: dict[str, list[str]] = {
    "full": ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"],
    "trivial": ["evaluate", "think", "build", "review", "test", "deliver", "reflect"],
    "research": ["evaluate", "think", "reflect"],
    "docs": ["evaluate", "think", "plan", "deliver", "reflect"],
    "bugfix": ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"],
    "goal": ["evaluate", "think", "plan", "goal_cycle", "deliver", "reflect"],
}


def get_profile_stages(profile: str | None) -> list[str]:
    """Get the ordered stage list for a pipeline profile.

    Falls back to 'full' if the profile is unknown or None.
    """
    return PIPELINE_PROFILES.get(profile or "full", PIPELINE_PROFILES["full"])
