"""Greenfield-only Working-Backwards lens — customer/value framing for NET-NEW features.

Plan B follow-up to the self-Socratic ambiguity scan (plan A, run_932c0991). Where
the Understanding Gate scans diagnosis-hedge and the Ambiguity Scan scans spec
ambiguity (both ALL strict profiles), this gate fires ONLY for greenfield work —
when `understanding.work_type == "greenfield"` AND the profile is strict. It is the
FIRST gate to enforce work_type, and the first to code-enforce pre_mortem (which
evaluate.md mandates doc-side but the validator never checked).

Scope was sharpened by the EVALUATE skeptic (run_b5b26ebe, WRONG-FRAME): the
original 5-question spec was ~80% redundant — "who has it" and "top-3 failure
reasons" already live in evaluate.md:140 (greenfield Understanding row) and the
Pre-mortem Gate (L493). So the NOVEL fields are the 4 ECONOMIC/value questions
neither gate captures:
  - target_customer   — the specific segment
  - current_workaround — how they solve it today
  - why_better        — faster / cheaper / better than the alternative
  - must_be_true      — adoption assumptions
plus a non-empty pre_mortem (REUSE, not duplicate — this is its first enforcement).

Intelligent-Default (self-answer each question first, human confirms at REVIEW as a
taste decision) is a STAGE-DOC behavior, not validator logic — the validator only
checks the block exists + fields are non-empty (structural, never content-truth).
These tests FORCE EXECUTION of each branch (STEERING #11).
"""

import pytest

from scripts.pipeline_validator import validate_artifact_data

WB = "Working-Backwards:"  # distinct tag — must not collide with sibling gates


def _wb_errors(errors):
    return [e for e in errors if WB in e]


# A clean understanding block (greenfield) so the Understanding Gate itself does
# not fire — we isolate the Working-Backwards behavior.
_GOOD_GREENFIELD_UNDERSTANDING = {
    "work_type": "greenfield",
    "claim": "No tool today lets a solo builder run a team-scale AI workspace; the problem is unsolved for that segment.",
    "evidence": "premortem: problem = solo builders lack team-scale orchestration; top-3 fail = adoption, trust, cost.",
    "evidence_kind": "premortem",
    "skeptic_verdict": "SUPPORTED",
    "alternative_considered": "buy an existing PM tool — loses because none orchestrate autonomous agents.",
}


def _good_wb():
    return {
        "target_customer": "solo technical founders who want team-scale output without hiring",
        "current_workaround": "they juggle ChatGPT tabs + manual copy-paste between tools, losing context each time",
        "why_better": "persistent memory + autonomous pipeline means 10x less context re-establishment than tab-juggling",
        "must_be_true": "users must trust an autonomous agent to act without per-step confirmation",
    }


def _eval(work_type, *, wb=None, pre_mortem=None, profile_understanding=True, **overrides):
    """Build an evaluate artifact with a given work_type."""
    und = dict(_GOOD_GREENFIELD_UNDERSTANDING)
    und["work_type"] = work_type
    data = {
        "recommendation": "GO",
        "scope": "standard",
        "acceptance_criteria": ["x"],
    }
    if profile_understanding:
        data["understanding"] = und
    if wb is not None:
        data["working_backwards"] = wb
    if pre_mortem is not None:
        data["pre_mortem"] = pre_mortem
    data.update(overrides)
    return data


_GOOD_PREMORTEM = ["adoption friction", "trust in autonomy", "token cost at scale"]


# ---------------------------------------------------------------------------
# Greenfield + strict REQUIRES the block
# ---------------------------------------------------------------------------
class TestGreenfieldStrictRequiresBlock:
    @pytest.mark.parametrize("profile", ["full", "goal", "standard", "complex", ""])
    def test_missing_wb_blocks_on_greenfield_strict(self, profile):
        data = _eval("greenfield", wb=None, pre_mortem=_GOOD_PREMORTEM)
        errors = validate_artifact_data("evaluate", data, profile=profile)
        assert _wb_errors(errors), f"{profile}: greenfield missing WB must block: {errors}"

    def test_full_valid_wb_passes(self):
        data = _eval("greenfield", wb=_good_wb(), pre_mortem=_GOOD_PREMORTEM)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors) == [], f"valid greenfield WB must pass: {errors}"

    def test_wb_must_be_dict(self):
        data = _eval("greenfield", wb="did working backwards", pre_mortem=_GOOD_PREMORTEM)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"non-dict WB must block: {errors}"


# ---------------------------------------------------------------------------
# Economic fields — each must be non-empty (the NOVEL, non-redundant slice)
# ---------------------------------------------------------------------------
class TestEconomicFieldsEnforced:
    @pytest.mark.parametrize("field", ["target_customer", "current_workaround", "why_better", "must_be_true"])
    def test_missing_economic_field_blocks(self, field):
        wb = _good_wb()
        del wb[field]
        data = _eval("greenfield", wb=wb, pre_mortem=_GOOD_PREMORTEM)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"missing {field} must block: {errors}"

    @pytest.mark.parametrize("field", ["target_customer", "current_workaround", "why_better", "must_be_true"])
    def test_empty_economic_field_blocks(self, field):
        wb = _good_wb()
        wb[field] = "  "
        data = _eval("greenfield", wb=wb, pre_mortem=_GOOD_PREMORTEM)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"empty {field} must block: {errors}"

    def test_too_short_economic_field_blocks(self):
        wb = _good_wb()
        wb["why_better"] = "faster"  # < floor
        data = _eval("greenfield", wb=wb, pre_mortem=_GOOD_PREMORTEM)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"sub-floor field must block: {errors}"

    def test_bare_true_economic_field_blocks(self):
        wb = _good_wb()
        wb["target_customer"] = True
        data = _eval("greenfield", wb=wb, pre_mortem=_GOOD_PREMORTEM)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"bare True field must block: {errors}"


# ---------------------------------------------------------------------------
# pre_mortem REUSE — required + first enforcement for greenfield
# ---------------------------------------------------------------------------
class TestPreMortemReuseEnforced:
    def test_missing_pre_mortem_blocks_greenfield(self):
        data = _eval("greenfield", wb=_good_wb(), pre_mortem=None)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"greenfield without pre_mortem must block: {errors}"

    def test_empty_pre_mortem_blocks_greenfield(self):
        data = _eval("greenfield", wb=_good_wb(), pre_mortem=[])
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"greenfield empty pre_mortem must block: {errors}"

    def test_non_list_pre_mortem_blocks(self):
        data = _eval("greenfield", wb=_good_wb(), pre_mortem="three reasons")
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"non-list pre_mortem must block: {errors}"


# ---------------------------------------------------------------------------
# work_type gating — ONLY greenfield triggers (the key plan-A-vs-B difference)
# ---------------------------------------------------------------------------
class TestWorkTypeGating:
    @pytest.mark.parametrize("wt", ["existing-feature", "bugfix", "refactor", "research", "docs"])
    def test_non_greenfield_never_requires_wb(self, wt):
        # No working_backwards, no pre_mortem — must NOT trigger the WB gate.
        data = _eval(wt, wb=None, pre_mortem=None)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors) == [], f"{wt}: WB gate must not fire: {errors}"

    def test_missing_work_type_does_not_fire(self):
        # Fail-open: no understanding block at all → no work_type → no WB requirement
        # (a quality lens, not a safety gate). Bugfix scope avoids the strict
        # understanding-gate presence requirement clouding this.
        data = {"recommendation": "GO", "scope": "bugfix", "acceptance_criteria": ["x"]}
        errors = validate_artifact_data("evaluate", data, profile="bugfix")
        assert _wb_errors(errors) == [], f"missing work_type must not fire WB gate: {errors}"


# ---------------------------------------------------------------------------
# Relaxed profiles — greenfield in a relaxed profile is exempt
# ---------------------------------------------------------------------------
class TestRelaxedProfilesExempt:
    @pytest.mark.parametrize("profile", ["trivial", "docs", "research"])
    def test_greenfield_relaxed_not_required(self, profile):
        data = _eval("greenfield", wb=None, pre_mortem=None, profile_understanding=False)
        errors = validate_artifact_data("evaluate", data, profile=profile)
        assert _wb_errors(errors) == [], f"{profile}: greenfield relaxed must not require WB: {errors}"


# ---------------------------------------------------------------------------
# No tag collision with sibling gates
# ---------------------------------------------------------------------------
class TestNoTagCollision:
    def test_wb_tag_distinct(self):
        # greenfield + missing WB → WB gate fires; its tag must be distinct from
        # the other gate tags so sibling tests that filter by tag stay isolated.
        data = _eval("greenfield", wb=None, pre_mortem=_GOOD_PREMORTEM)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors), f"WB gate should fire: {errors}"
        for e in _wb_errors(errors):
            assert "Understanding gate" not in e
            assert "Ambiguity scan" not in e
            assert "REPRO gate" not in e


# ---------------------------------------------------------------------------
# Dogfood — this run is existing-feature, so its own EVALUATE is exempt
# ---------------------------------------------------------------------------
class TestSelfDogfood:
    def test_run_b5b26ebe_existing_feature_exempt(self):
        """This very feature is an existing-feature extension of EVALUATE, NOT
        greenfield — so its own evaluate artifact must NOT be required to carry a
        working_backwards block. Proves the work_type gate is correct."""
        data = _eval("existing-feature", wb=None, pre_mortem=None)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _wb_errors(errors) == [], f"existing-feature run must be WB-exempt: {errors}"
