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


# Profiles whose gates run at RELAXED rigor (no adversarial-enforcement, no
# understanding/REPRO block required). EVERYTHING ELSE — full, bugfix, goal, the
# legacy "standard" alias, AND any unknown/variant value — is STRICT. This is the
# single source of truth for the relaxed/strict decision; see is_relaxed_profile.
_RELAXED_PROFILES: frozenset[str] = frozenset({"trivial", "docs", "research"})

# Legacy / spelling aliases → canonical profile name. "standard" is the historical
# rank-4 alias of "full" (same stage set). Keep this the ONLY place aliases live.
_PROFILE_ALIASES: dict[str, str] = {"standard": "full"}


def normalize_profile(profile: str | None) -> str:
    """Canonicalize a raw profile value to its comparison key — the C3 SSOT.

    Every strict/relaxed gate decision MUST compare against this, never a raw
    field value. Root cause of C3: a variant like ``"Full"`` received full's
    stage list (get_profile_stages fallback) yet slipped past deliver's hardcoded
    ``profile in ("full","bugfix","")`` adversarial-enforcement literals, silently
    skipping the gate. Normalizing at every source removes that asymmetry.

    Rules (all lower + strip first):
      - ``None`` / empty / whitespace-only → ``"full"`` (matches the historical
        ``profile or "full"`` fallback AND the ``""``-in-strict-set semantics).
      - a known alias (``"standard"``) → its canonical name (``"full"``).
      - an unknown value → its lowercased/stripped form UNCHANGED (NOT remapped to
        full) so is_relaxed_profile can fail-closed to STRICT on it. (The stage-list
        fallback to full lives separately in get_profile_stages.)
    """
    if profile is None:
        return "full"
    key = profile.strip().lower()
    if not key:
        return "full"
    return _PROFILE_ALIASES.get(key, key)


def is_relaxed_profile(profile: str | None) -> bool:
    """True iff this profile runs gates at RELAXED rigor. FAIL-CLOSED: anything not
    explicitly in _RELAXED_PROFILES (including unknown/variant/typo profiles) is
    STRICT. This is the C3 fix — replaces the scattered, fail-OPEN
    ``profile in ("full","bugfix","")`` literals whose inconsistency let a variant
    skip adversarial enforcement. Normalizes first, so ``"DOCS"`` == ``"docs"``."""
    return normalize_profile(profile) in _RELAXED_PROFILES


def get_profile_stages(profile: str | None) -> list[str]:
    """Get the ordered stage list for a pipeline profile.

    Normalizes first (so 'Full'/'FULL'/'standard' resolve to full's stages), then
    falls back to 'full' if the normalized profile is still unknown or None.
    """
    return PIPELINE_PROFILES.get(normalize_profile(profile), PIPELINE_PROFILES["full"])
