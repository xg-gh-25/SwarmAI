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

Public symbols:
- ``PIPELINE_PROFILES``  — profile name → ordered stage list (incl. ``direct``).
- ``normalize_profile`` — canonical comparison key for a raw profile value.
- ``is_relaxed_profile`` — True iff gates run at relaxed rigor (fail-closed).
- ``get_profile_stages`` — ordered stage list with full-profile fallback.
- ``PROFILE_GATES`` / ``profile_gates`` — the gates (gate0 / gate1 / gate2) each
  profile runs.
"""

from core.run_protocol.lifecycle import PROFILE_STAGES as _V1_STAGES

# The v1 lifecycle owns the stage order; this view only re-shapes it (one source).
PIPELINE_PROFILES: dict[str, list[str]] = {name: list(stages)
                                           for name, stages in _V1_STAGES.items()}


# Profiles whose gates run at RELAXED rigor (no adversarial-enforcement, no
# understanding/REPRO block required). EVERYTHING ELSE — full, bugfix, goal, the
# legacy "standard" alias, AND any unknown/variant value — is STRICT. This is the
# single source of truth for the relaxed/strict decision; see is_relaxed_profile.
_RELAXED_PROFILES: frozenset[str] = frozenset({"trivial", "research"})

# Profiles that can no longer be chosen, with what to do instead. Their stage lists
# stay readable (_LEGACY_PROFILE_STAGES) so an old run still shows its own stages.
# "docs" (removed 2026-10-20): it walked five stages for a text change while a direct
# run needs three, and its relaxed rigor followed the profile NAME, so a "docs"
# run that edited code could ship with no tests and no review.
REMOVED_PROFILES: dict[str, str] = {
    "docs": ("the docs profile was removed. Documents outside a code repo (SwarmWS "
             "Knowledge / DDD) need no run: edit them directly. *.md files and backend/context/ in "
             "a code repo need no run either (core/doc_paths.py); any other file: "
             "`\"$SWARM_BACKEND_BIN\" pipeline-v1 create --profile direct --requirement "
             "\"<the request>\"`."),
}
_LEGACY_PROFILE_STAGES: dict[str, list[str]] = {
    "docs": ["evaluate", "think", "plan", "deliver", "reflect"],
}

# Legacy / spelling aliases → canonical profile name. "standard" is the historical
# rank-4 alias of "full" (same stage set). Keep this the ONLY place aliases live.
_PROFILE_ALIASES: dict[str, str] = {"standard": "full"}

#: Stage-record statuses that mean the stage FINISHED. Real run.json files carry all
#: three spellings ("complete" and "done" are legacy). run_docs and
#: pipeline_stage_events read it; other readers still hard-code their own tuple
#: (pipeline_validator, run_worktree, ddd_cultivation, clean_foreign_run_stubs) and
#: should move onto it — a new "is this stage done" check must use this set.
FINISHED_STATUSES: frozenset[str] = frozenset({"completed", "complete", "done"})


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


# The gates each profile runs — what the chat's run checklist lists as pending lines
# (pipeline_stage_events.checklist), and what decides plan's next row (Gate 1).
#   gate0 — EVALUATE's skeptic: strict profiles that run EVALUATE (the M3 rigor tier).
#   gate1 — the plan skeptic before BUILD / the goal's first cycle: strict profiles
#           with PLAN and BUILD. artifact_cli.GATE1_PROFILES (the profiles
#           whose BUILD refuses a missing verdict) is a subset of these.
#   gate2 — the adversarial review inside DELIVER: every profile with DELIVER.
# test_pipeline_run_checklist pins all three rules against the stage lists,
# is_relaxed_profile and artifact_cli.GATE1_PROFILES.
PROFILE_GATES: dict[str, tuple[str, ...]] = {
    "full": ("gate0", "review"),
    "bugfix": ("gate0", "review"),
    "direct": ("review",),
    "trivial": ("review",),
    "research": (),
}


def profile_gates(profile: str | None) -> tuple[str, ...]:
    """The gates a profile runs. Normalizes first; an unknown profile gets full's
    gates (it is strict and runs full's stages — the same fail-closed fallback as
    is_relaxed_profile / get_profile_stages)."""
    return PROFILE_GATES.get(normalize_profile(profile), PROFILE_GATES["full"])


def get_profile_stages(profile: str | None) -> list[str]:
    """Get the ordered stage list for a pipeline profile.

    Normalizes first (so 'Full'/'FULL'/'standard' resolve to full's stages); a
    removed profile (an old run) keeps its own list; otherwise falls back to 'full'
    if the normalized profile is still unknown or None.
    """
    key = normalize_profile(profile)
    return PIPELINE_PROFILES.get(key) or _LEGACY_PROFILE_STAGES.get(key) or PIPELINE_PROFILES["full"]


def removed_profile_error(profile: str | None) -> "str | None":
    """The refusal text when *profile* can no longer be chosen, else None."""
    return REMOVED_PROFILES.get(normalize_profile(profile)) if profile else None
