"""Self-Socratic ambiguity scan — interrogate the requirement/framing, not the user.

A micro-loop grafted onto the EVALUATE Requirement Clarification Check and the
THINK Design Risk Probe (design: plan art_ea9701a1, run_932c0991). After the
stage fills its own output (WHO/WHAT/WHY/WHEN in EVALUATE; risk-probe assumptions
+ recommendation in THINK), it re-scans THAT output for ambiguity/hedge wording
and forces ONE self-answer round: self-resolve via code/DDD, escalate only if
genuinely unknowable. The validator enforces the loop RAN — an `ambiguity_scan`
block with every hit carrying a non-empty resolution.

Philosophy (the conviction this run fixes into the system): Socratic method in an
AUTONOMOUS pipeline = interrogate the SPEC and your own FRAMING, not "ask the user
more". This is the Understanding Gate's "refute the claim" discipline shifted LEFT
and narrowed to the requirement-clarification layer — same family, different field:

  Understanding Gate ('Understanding gate' tag) → diagnosis-hedge in
      understanding.claim/evidence (observe-before-assert; INPUT epistemics).
  Ambiguity Scan     ('Ambiguity scan:' tag)   → spec-ambiguity in the stage's
      OWN filled output (clarification fields / probe assumptions; OUTPUT
      completeness). Distinct field, distinct tag — verified non-overlapping.

Strict profiles (full/bugfix/goal/standard/complex/"") REQUIRE the block; relaxed
(trivial/docs/research) are exempt when absent but still scanned if present
(cheap). These tests FORCE EXECUTION of each branch (STEERING #11).

NOT in scope (rejected by EVALUATE/THINK): interactive user-ask gate (grill
protocol, think.md:96 — almost always skipped); PR/FAQ Working-Backwards (plan B,
greenfield-only follow-up).
"""

import pytest

from scripts.pipeline_validator import validate_artifact_data

AS = "Ambiguity scan:"  # distinct marker — must NOT collide with other gates


def _as_errors(errors):
    return [e for e in errors if AS in e]


# A clean understanding block so the Understanding Gate never fires in these
# tests — we isolate the ambiguity-scan behavior. (Strict evaluate also needs UG.)
_GOOD_UNDERSTANDING = {
    "work_type": "existing-feature",
    "claim": (
        "EVALUATE Requirement Clarification (evaluate.md:11) and THINK Design Risk "
        "Probe (think.md:44) are one-shot today — neither re-scans its own output."
    ),
    "evidence": (
        "code-trace: evaluate.md:18-31 linear parse->flag->exit; think.md:88-93 "
        "routes on probe status with no self-rescan; grep risk_probe in validator = 0."
    ),
    "evidence_kind": "code-trace",
    "skeptic_verdict": "SUPPORTED",
    "alternative_considered": "redundant-with-Understanding-Gate loses: different field.",
}


def _clean_scan(**overrides):
    """A passing ambiguity_scan block (no hits, or all hits resolved)."""
    block = {
        "scanned_fields": ["who", "what", "why", "when"],
        "terms_checked": ["depends", "maybe", "standard", "可能"],
        "hits": [],
        "hit_count": 0,
        "all_resolved": True,
    }
    block.update(overrides)
    return block


def _eval(ambiguity_scan, profile_strict_extras=True, **overrides):
    """Build an evaluate artifact. Includes a clean understanding block so only the
    ambiguity gate is under test (for strict profiles)."""
    data = {
        "recommendation": "GO",
        "scope": "standard",
        "acceptance_criteria": ["x"],
    }
    if profile_strict_extras:
        data["understanding"] = dict(_GOOD_UNDERSTANDING)
    if ambiguity_scan is not None:
        data["ambiguity_scan"] = ambiguity_scan
    data.update(overrides)
    return data


def _think(ambiguity_scan, **overrides):
    """Build a think artifact (research). THINK has no understanding block."""
    data = {"key_findings": ["finding one is substantive"]}
    if ambiguity_scan is not None:
        data["ambiguity_scan"] = ambiguity_scan
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Presence — strict profiles REQUIRE the ambiguity_scan block (both stages)
# ---------------------------------------------------------------------------
class TestRequiresScanOnStrict:
    @pytest.mark.parametrize("profile", ["full", "bugfix", "goal", "standard", "complex", ""])
    def test_evaluate_missing_scan_blocks_on_strict(self, profile):
        data = _eval(ambiguity_scan=None)
        errors = validate_artifact_data("evaluate", data, profile=profile)
        assert _as_errors(errors), f"{profile}: missing evaluate scan must block: {errors}"

    @pytest.mark.parametrize("profile", ["full", "bugfix", "goal", "standard", "complex", ""])
    def test_think_missing_scan_blocks_on_strict(self, profile):
        data = _think(ambiguity_scan=None)
        errors = validate_artifact_data("think", data, profile=profile)
        assert _as_errors(errors), f"{profile}: missing think scan must block: {errors}"

    def test_evaluate_clean_scan_passes(self):
        data = _eval(ambiguity_scan=_clean_scan())
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors) == [], f"clean evaluate scan must pass: {errors}"

    def test_think_clean_scan_passes(self):
        data = _think(ambiguity_scan=_clean_scan(scanned_fields=["assumptions", "recommendation"]))
        errors = validate_artifact_data("think", data, profile="full")
        assert _as_errors(errors) == [], f"clean think scan must pass: {errors}"

    def test_scan_must_be_dict(self):
        data = _eval(ambiguity_scan="ran it")
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors), f"non-dict scan must block: {errors}"


# ---------------------------------------------------------------------------
# Resolution — a hit with no resolution BLOCKS (proves the loop RAN, AC5)
# ---------------------------------------------------------------------------
class TestHitResolutionEnforced:
    def test_hit_without_resolution_blocks(self):
        scan = _clean_scan(
            hits=[{"term": "depends", "where": "what", "resolution": "", "kind": "self-answer"}],
            hit_count=1,
            all_resolved=False,
        )
        data = _eval(ambiguity_scan=scan)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors), f"hit with empty resolution must block: {errors}"

    def test_hit_missing_resolution_key_blocks(self):
        scan = _clean_scan(
            hits=[{"term": "maybe", "where": "why"}],  # no resolution key at all
            hit_count=1,
        )
        data = _eval(ambiguity_scan=scan)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors), f"hit missing resolution key must block: {errors}"

    def test_hit_too_short_resolution_blocks(self):
        scan = _clean_scan(
            hits=[{"term": "standard", "where": "what", "resolution": "ok", "kind": "self-answer"}],
            hit_count=1,
        )
        data = _eval(ambiguity_scan=scan)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors), f"sub-floor resolution must block: {errors}"

    def test_hit_with_self_answer_resolution_passes(self):
        scan = _clean_scan(
            hits=[{
                "term": "standard",
                "where": "approach",
                "resolution": "self-answered: read pipeline_validator.py:465 — named the exact reuse target, not a vague 'standard pattern'.",
                "kind": "self-answer",
            }],
            hit_count=1,
        )
        data = _eval(ambiguity_scan=scan)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors) == [], f"resolved hit must pass: {errors}"

    def test_hit_with_escalation_resolution_passes(self):
        scan = _clean_scan(
            hits=[{
                "term": "depends",
                "where": "when",
                "resolution": "escalation: trigger timing is genuinely user-intent, cannot derive from code/DDD — flagged to user.",
                "kind": "escalation",
            }],
            hit_count=1,
        )
        data = _think(ambiguity_scan=scan)
        errors = validate_artifact_data("think", data, profile="full")
        assert _as_errors(errors) == [], f"escalated hit must pass: {errors}"

    def test_bare_true_resolution_blocks(self):
        scan = _clean_scan(
            hits=[{"term": "typical", "where": "what", "resolution": True, "kind": "self-answer"}],
            hit_count=1,
        )
        data = _eval(ambiguity_scan=scan)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors), f"bare True resolution carries no info: {errors}"


# ---------------------------------------------------------------------------
# Relaxed profiles — exempt when absent; still scan hits when present (AC7)
# ---------------------------------------------------------------------------
class TestRelaxedProfiles:
    @pytest.mark.parametrize("profile", ["trivial", "docs", "research"])
    def test_missing_scan_not_blocked_on_relaxed(self, profile):
        data = _eval(ambiguity_scan=None, profile_strict_extras=False)
        errors = validate_artifact_data("evaluate", data, profile=profile)
        assert _as_errors(errors) == [], f"{profile}: missing scan must NOT block: {errors}"

    @pytest.mark.parametrize("profile", ["trivial", "docs", "research"])
    def test_think_missing_scan_not_blocked_on_relaxed(self, profile):
        data = _think(ambiguity_scan=None)
        errors = validate_artifact_data("think", data, profile=profile)
        assert _as_errors(errors) == [], f"{profile}: missing think scan must NOT block: {errors}"

    def test_relaxed_still_catches_unresolved_hit(self):
        """If a relaxed profile DOES supply a scan, an unresolved hit is still caught."""
        scan = _clean_scan(
            hits=[{"term": "maybe", "where": "what", "resolution": "", "kind": "self-answer"}],
            hit_count=1,
        )
        data = _eval(ambiguity_scan=scan, profile_strict_extras=False)
        errors = validate_artifact_data("evaluate", data, profile="docs")
        assert _as_errors(errors), f"relaxed profile must still catch a bad hit: {errors}"


# ---------------------------------------------------------------------------
# Self-report consistency — summary fields must agree with hits (adversarial LOW)
# ---------------------------------------------------------------------------
class TestSelfReportConsistency:
    def test_hit_count_mismatch_blocks(self):
        scan = _clean_scan(
            hits=[{"term": "standard", "where": "w",
                   "resolution": "self-answer: pinned exact meaning via code read.",
                   "kind": "self-answer"}],
            hit_count=0,  # lies: says 0 but there's 1 hit
            all_resolved=True,
        )
        errors = validate_artifact_data("evaluate", _eval(ambiguity_scan=scan), profile="full")
        assert _as_errors(errors), f"hit_count disagreeing with len(hits) must block: {errors}"

    def test_all_resolved_true_with_unresolved_hit_blocks(self):
        scan = _clean_scan(
            hits=[{"term": "maybe", "where": "w", "resolution": "", "kind": "self-answer"}],
            hit_count=1,
            all_resolved=True,  # lies: says resolved but resolution is empty
        )
        errors = validate_artifact_data("evaluate", _eval(ambiguity_scan=scan), profile="full")
        # Two errors expected: the unresolved-hit error AND the all_resolved lie.
        assert _as_errors(errors), f"all_resolved=true with unresolved hit must block: {errors}"
        assert any("all_resolved" in e for e in _as_errors(errors)), f"consistency error expected: {errors}"

    def test_consistent_summary_passes(self):
        scan = _clean_scan(
            hits=[{"term": "standard", "where": "w",
                   "resolution": "self-answer: read retry_manager.py:40 — exponential backoff, not generic.",
                   "kind": "self-answer"}],
            hit_count=1,
            all_resolved=True,
        )
        errors = validate_artifact_data("evaluate", _eval(ambiguity_scan=scan), profile="full")
        assert _as_errors(errors) == [], f"consistent summary must pass: {errors}"


# ---------------------------------------------------------------------------
# Scope — the gate only fires on evaluate + think, never other stages
# ---------------------------------------------------------------------------
class TestScopeIsolation:
    @pytest.mark.parametrize("stage", ["plan", "build", "review", "test", "deliver"])
    def test_other_stages_never_fire_ambiguity_gate(self, stage):
        # These stages have their own schemas; an absent ambiguity_scan must not
        # trigger THIS gate regardless of profile.
        errors = validate_artifact_data(stage, {"_irrelevant": True}, profile="full")
        assert _as_errors(errors) == [], f"{stage}: ambiguity gate must not fire: {errors}"


# ---------------------------------------------------------------------------
# Non-collision — the ambiguity tag must be distinct from sibling-gate tags
# ---------------------------------------------------------------------------
class TestNoTagCollision:
    def test_ambiguity_tag_distinct_from_understanding_and_repro(self):
        # A strict evaluate with a clean scan but NO understanding block: the
        # Understanding Gate fires (its own tag), the ambiguity gate does NOT.
        data = {
            "recommendation": "GO",
            "scope": "standard",
            "acceptance_criteria": ["x"],
            "ambiguity_scan": _clean_scan(),
            # no understanding block on purpose
        }
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors) == [], f"ambiguity gate must not fire on clean scan: {errors}"
        # Understanding gate SHOULD fire here (proves the tags don't collide and
        # the two gates are independent).
        assert [e for e in errors if "Understanding gate" in e], (
            f"understanding gate should fire when its block is absent: {errors}"
        )


# ---------------------------------------------------------------------------
# Self-dogfood — this very run's EVALUATE + THINK ambiguity_scan blocks pass
# ---------------------------------------------------------------------------
class TestSelfDogfood:
    def test_run_932c0991_evaluate_scan_passes(self):
        scan = {
            "scanned_fields": ["requirement", "acceptance_criteria"],
            "terms_checked": ["depends", "maybe", "not sure", "mix of", "somewhere between",
                              "standard", "typical", "看情况", "可能", "大概", "差不多",
                              "视情况", "标准做法", "一般"],
            "hits": [{
                "term": "standard",
                "where": "marker enforcement underspecified",
                "resolution": "self-answered in EVALUATE/PLAN: field=ambiguity_scan, both stages, BLOCK on strict.",
                "kind": "self-answer",
            }],
            "hit_count": 1,
            "all_resolved": True,
        }
        data = _eval(ambiguity_scan=scan)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _as_errors(errors) == [], f"self-dogfood evaluate scan must pass: {errors}"

    def test_run_932c0991_evaluate_scan_floor_boundary(self):
        """Pin the resolution floor exactly: 11 chars blocks, 12 passes (adversarial
        noted no boundary test existed)."""
        below = "x" * (11)   # 11 < 12 → block
        at = "y" * 12         # 12 == floor → pass
        scan_below = _clean_scan(
            hits=[{"term": "standard", "where": "w", "resolution": below, "kind": "self-answer"}],
            hit_count=1,
        )
        scan_at = _clean_scan(
            hits=[{"term": "standard", "where": "w", "resolution": at, "kind": "self-answer"}],
            hit_count=1,
        )
        assert _as_errors(validate_artifact_data("evaluate", _eval(ambiguity_scan=scan_below), profile="full")), "11-char resolution must block"
        assert _as_errors(validate_artifact_data("evaluate", _eval(ambiguity_scan=scan_at), profile="full")) == [], "12-char resolution must pass"

    def test_run_932c0991_think_scan_passes(self):
        scan = {
            "scanned_fields": ["recommendation", "risk_probe.verification"],
            "terms_checked": ["depends", "maybe", "standard", "typical", "可能"],
            "hits": [{
                "term": "standard",
                "where": "approach 'mirroring' risk of vague pattern",
                "resolution": "self-answer: named exact reuse targets _check_understanding_gate L465, _RELAXED L381, floor L455.",
                "kind": "self-answer",
            }],
            "hit_count": 1,
            "all_resolved": True,
        }
        data = _think(ambiguity_scan=scan)
        errors = validate_artifact_data("think", data, profile="full")
        assert _as_errors(errors) == [], f"self-dogfood think scan must pass: {errors}"
