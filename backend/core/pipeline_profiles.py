"""Pipeline profile definitions — shared between CLI, routers, and skills.

Single source of truth for which stages each pipeline profile includes.
Imported by artifact_cli.py, routers/pipelines.py, and any future
component that needs to know pipeline stage sequences.
"""

PIPELINE_PROFILES: dict[str, list[str]] = {
    "full": ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"],
    "trivial": ["evaluate", "build", "review", "test", "deliver", "reflect"],
    "research": ["evaluate", "think", "reflect"],
    "docs": ["evaluate", "think", "plan", "deliver", "reflect"],
    "bugfix": ["evaluate", "plan", "build", "review", "test", "deliver", "reflect"],
}


def get_profile_stages(profile: str | None) -> list[str]:
    """Get the ordered stage list for a pipeline profile.

    Falls back to 'full' if the profile is unknown or None.
    """
    return PIPELINE_PROFILES.get(profile or "full", PIPELINE_PROFILES["full"])
