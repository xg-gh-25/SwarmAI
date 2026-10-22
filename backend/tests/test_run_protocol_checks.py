"""Pipeline v1 check-registry tests (v13 §A2.1-§A2.4, §D4).

Methodology: exhaustively enumerate the frozen registry. Invariants: every profile/check
pair applies xor has an explicit exemption; effects are independent; no land effect is
ever a stage-complete guard; direct/full/goal route shared checks at the right stage.
"""
from __future__ import annotations

import pytest

from core.run_protocol import checks, events

AUTH = events.EventAuthority(b"c" * 32)

EXPECTED = {
    "RQ-1", "RQ-2", "RQ-3", "RQ-4", "RQ-5", "RQ-6", "RQ-8", "RQ-8b",
    "DS-1", "DS-2a", "DS-2b", "DS-3", "DS-4a", "DS-4b", "DS-5", "DS-6",
    "DS-7", "DS-9a", "DS-9b", "BX-1", "RV-1a", "RV-1b", "RV-1c",
    "DC-1", "DC-2", "RV-2", "FX-1", "TS-1", "TS-2", "TS-3", "DV-1",
    "LG-1", "LG-2", "LG-3", "LG-4", "LG-5", "QX-1", "QX-2",
    "MD-1", "MD-2", "MD-3", "RF-1", "RF-2", "RF-3", "RF-4", "RF-5",
}


def ids(specs):
    return {spec.check_id for spec in specs}


def test_registry_is_the_complete_v13_table():
    assert set(checks.CHECKS) == EXPECTED
    assert len(checks.CHECKS) == 46
    assert checks.coverage_errors() == []


def test_every_profile_check_pair_applies_xor_has_an_explicit_exemption():
    for profile in checks.PROFILES:
        for check_id, spec in checks.CHECKS.items():
            applies = profile in spec.profiles
            exempt = (profile, check_id) in checks.EXEMPTIONS
            assert applies != exempt, (profile, check_id)
            if exempt:
                assert checks.EXEMPTIONS[(profile, check_id)].strip()


def test_effects_are_independent_not_one_block_advise_flag():
    assert checks.CHECKS["LG-2"].effects == {"land", "push"}
    assert checks.CHECKS["DS-1"].effects == {"push"}
    assert checks.CHECKS["RQ-3"].effects == set()
    assert checks.CHECKS["DV-1"].effects == {"stage"}


def test_land_effect_never_appears_in_any_stage_complete_guard():
    land_ids = {check_id for check_id, spec in checks.CHECKS.items()
                if "land" in spec.effects}
    assert land_ids == {"LG-1", "LG-2", "LG-3", "LG-4", "LG-5"}
    for profile, sequence in checks.PROFILE_STAGES.items():
        for stage in sequence:
            routed = checks.stage_checks(profile, stage)
            assert ids(routed).isdisjoint(land_ids), (profile, stage)
            assert all("stage" in spec.effects for spec in routed)


def test_land_and_push_queries_route_only_their_effect():
    assert ids(checks.land_checks("full")) == {"LG-1", "LG-2", "LG-3", "LG-4", "LG-5"}
    assert not checks.land_checks("research")
    assert {"LG-2", "LG-5", "RV-1a", "RV-2", "DS-1", "TS-2"} \
        <= ids(checks.push_checks("full"))
    assert all("push" in spec.effects for spec in checks.push_checks("direct"))


def test_requirements_run_at_evaluate_except_direct_plan():
    rq = {"RQ-1", "RQ-2", "RQ-8"}
    assert rq <= ids(checks.stage_checks("full", "evaluate"))
    assert rq.isdisjoint(ids(checks.stage_checks("full", "plan")))
    assert rq <= ids(checks.stage_checks("direct", "plan"))
    assert "RQ-5" not in ids(checks.stage_checks("direct", "plan"))


def test_trivial_uses_full_requirements_direct_is_the_only_rq5_rq6_exemption():
    assert {"RQ-5", "RQ-6"} <= ids(checks.stage_checks("trivial", "evaluate"))
    assert ("direct", "RQ-5") in checks.EXEMPTIONS
    assert ("trivial", "RQ-5") not in checks.EXEMPTIONS


def test_goal_build_checks_run_in_goal_cycle_not_build():
    goal = ids(checks.stage_checks("goal", "goal_cycle"))
    assert {"DS-2b", "DS-9b", "BX-1"} <= goal
    full = ids(checks.stage_checks("full", "build"))
    assert {"DS-2b", "DS-9b", "BX-1"} <= full


def test_review_and_deliver_stage_guards_are_exact():
    assert ids(checks.stage_checks("full", "review")) == {"DS-9b"}
    assert ids(checks.stage_checks("full", "deliver")) == {
        "DV-1", "RF-1", "RF-2", "RF-3", "RF-4",
    }
    assert "RF-5" not in ids(checks.stage_checks("full", "reflect"))  # record only


@pytest.mark.parametrize("profile", sorted(checks.PROFILES))
def test_every_routed_stage_exists_in_the_profile(profile):
    for spec in checks.CHECKS.values():
        if profile in spec.profiles:
            assert set(checks._stages_for(spec, profile)) <= (
                set(checks.PROFILE_STAGES[profile]) | {"land", "run_land"}
            )


def test_named_exemptions_match_the_design_table():
    for profile in checks.PROFILES - {"bugfix"}:
        for check_id in ("RQ-4", "DS-4a", "DS-4b"):
            assert "not bugfix" in checks.EXEMPTIONS[(profile, check_id)]
    for check_id in ("DS-6",):
        assert "lite" in checks.EXEMPTIONS[("direct", check_id)]
        assert "lite" in checks.EXEMPTIONS[("trivial", check_id)]
    assert "Gate-0" in checks.EXEMPTIONS[("direct", "RV-1b")]
    assert "Gate-0" in checks.EXEMPTIONS[("trivial", "RV-1b")]
    research_exempt = {cid for profile, cid in checks.EXEMPTIONS if profile == "research"}
    assert {"DS-1", "BX-1", "RV-1a", "RV-2", "TS-1", "LG-1", "QX-1", "MD-1"} \
        <= research_exempt


def _context(requirements=None, design=None, **kwargs):
    requirements = requirements or {"requirements": [{"id": "R1", "acceptance": [{
        "id": "R1.1", "pattern": "event", "condition": "request arrives",
        "response": "return 200", "negative": False, "universal": False,
    }]}]}
    design = design or {
        "tasks": [{"id": "T1", "file": "backend/x.py", "symbol": "f", "change": "fix",
                   "requirements": ["R1.1"], "depends_on": [], "creates_test_files": []}],
        "exit_criteria": [{"id": "X1", "kind": "test", "ref": "tests/x.py::test_f",
                           "covers": ["R1.1"]}],
        "no_contract_change_reason": "private implementation",
    }
    return checks.make_context(profile="direct", requirement_variant="lite",
                               design_variant="lite", requirements=requirements,
                               design=design, authority=AUTH,
                               existing_files={"tests/x.py"}, **kwargs)


def test_check_evaluator_runs_schema_not_a_passed_boolean_claim():
    assert checks.evaluate("RQ-1", _context())[0] is True
    bad = _context(requirements={"requirements": []})
    ok, detail = checks.evaluate("RQ-1", bad)
    assert not ok and "non-empty" in detail
    with pytest.raises(checks.CheckError, match="validated CheckContext"):
        checks.evaluate("RQ-1", {"RQ-1": {"passed": True, "detail": "forged"}})
    with pytest.raises(checks.CheckError, match="unknown check"):
        checks.evaluate("NOPE", _context())


def test_ds9a_uses_the_shared_probe_validator():
    design = _context().design.copy()
    design["exit_criteria"] = [{"id": "X1", "kind": "probe",
        "argv": ["sh", "-c", "true"], "expect": {"rc": 0, "stdout": "empty"},
        "covers": ["R1.1"]}]
    ok, detail = checks.evaluate("DS-9a", _context(design=design))
    assert not ok and "only git grep" in detail


def test_unknown_profile_stage_and_effect_are_rejected():
    with pytest.raises(checks.CheckError, match="profile"):
        checks.stage_checks("nope", "plan")
    with pytest.raises(checks.CheckError, match="not in profile"):
        checks.stage_checks("direct", "evaluate")
    with pytest.raises(checks.CheckError, match="effect"):
        checks._for("direct", "record")


def test_final_checks_require_exact_nonempty_repos_and_validation_manifest():
    final = {"repos": {
        "api": {"code_tree": "1" * 40, "base": "a", "tests": {"verdict": "green"},
                "validations": [{"id": "lint", "verdict": "pass"}]},
        "web": {"code_tree": "2" * 40, "base": "b", "tests": {"verdict": "green"},
                "validations": []},
    }, "risk_expansion": {"status": "within_cap"},
       "provenance": {"final_kind": "regression"}}
    ctx = _context(final=final, expected_repos={"api", "web"},
                   validation_manifest={"api": {"lint"}, "web": {"typecheck"}},
                   run={"code_trees": {"api": "1" * 40, "web": "2" * 40},
                        "landing_bases": {"api": "a", "web": "b"},
                        "mode": "isolated", "changed_paths": [],
                        "validation_rule_paths": [], "landing": "landed"})
    assert checks.evaluate("TS-1", ctx)[0]
    assert checks.evaluate("LG-1", ctx)[0]
    assert not checks.evaluate("LG-5", ctx)[0]  # web has no required typecheck
    empty = _context(final={"repos": {}}, expected_repos=set(), run={"code_trees": {}})
    assert not checks.evaluate("LG-1", empty)[0]


def test_missing_risk_and_mode_provenance_fails_closed():
    ctx = _context()
    for check_id in ("QX-1", "QX-2", "MD-1", "MD-2", "MD-3"):
        assert checks.evaluate(check_id, ctx)[0] is False, check_id


def _signed_event(kind, payload, *, stage=None, round=None, attempt=None):
    return events.make_event(
        writer=AUTH.writer(events.EVENT_SPECS[kind].producer), run_id="run_x",
        kind=kind, at="2026-10-22T00:00:00Z", payload=payload,
        stage=stage, round=round, attempt=attempt,
    )[0]


def test_gate0_handout_without_parsed_answer_does_not_pass_vacuously():
    handout = _signed_event("handout", {"expert_id": "gate0-skeptic"},
                            stage="evaluate", round=1, attempt=1)
    ctx = _context(events=[handout])
    ok, detail = checks.evaluate("RV-1b", ctx)
    assert not ok and "lacks parsed answer" in detail


def test_ds4b_requires_unchanged_regression_test_to_collect():
    req = {"bugfix": {"unchanged": [{"id": "U1"}]}, "requirements": []}
    design = {"exit_criteria": [{"id": "X1", "kind": "test",
                                 "ref": "tests/u.py::test_u", "covers": ["U1"]}]}
    ctx = checks.make_context(profile="bugfix", requirement_variant="full",
                              design_variant="full", requirements=req, design=design,
                              authority=AUTH, collected_tests=set())
    assert not checks.evaluate("DS-4b", ctx)[0]


def test_ds9b_includes_probe_exit_result():
    design = dict(_context().design)
    design["exit_criteria"] = [{"id": "X1", "kind": "probe",
        "argv": ["git", "grep", "-e", "x", "--", "backend"],
        "expect": {"rc": 1, "stdout": "empty"}, "covers": ["R1.1"]}]
    assert not checks.evaluate("DS-9b", _context(design=design))[0]
    assert checks.evaluate("DS-9b", _context(design=design,
                                             test_results={"X1": "pass"}))[0]


def test_lg4_rejects_malformed_final_repo_rows_instead_of_vacuous_pass():
    ctx = _context(final={"repos": {"api": "malformed"}},
                   expected_repos={"api"},
                   run={"landing_bases": {"api": "base"}})
    assert checks.evaluate("LG-4", ctx)[0] is False
