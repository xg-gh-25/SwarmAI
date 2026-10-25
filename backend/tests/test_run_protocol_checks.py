"""Pipeline v1 check-registry tests (v13 §A2.1-§A2.4, §D4).

Methodology: exhaustively enumerate the frozen registry. Invariants: every profile/check
pair applies xor has an explicit exemption; effects are independent; no land effect is
ever a stage-complete guard; direct/full route shared checks at the right stage.
"""
from __future__ import annotations

import pytest

from core.run_protocol import checks, events

AUTH = events.EventAuthority()

EXPECTED = {
    "RQ-1", "RQ-2", "RQ-4", "RQ-5", "RQ-6", "RQ-8",
    "DS-1", "DS-2a", "DS-3", "DS-4a", "DS-5", "DS-6",
    "DS-7", "DS-9a", "BX-3", "RV-1a", "RV-1b", "RV-1c",
    "DC-1", "DC-2", "RV-2", "RV-4", "FX-1", "TS-1", "TS-3", "TS-4", "DV-1",
    "LG-1", "LG-3", "LG-4",
    "MD-1", "MD-3", "RF-1", "RF-2", "RF-3", "RF-4", "RF-5",
}


def ids(specs):
    return {spec.check_id for spec in specs}


def test_registry_is_the_complete_v13_table():
    assert set(checks.CHECKS) == EXPECTED
    # v13's 46 + BX-2, minus the runner's checks, plus BX-3 (S6-1), plus RV-4 (S6-2),
    # minus the word-list checks RQ-3 / RQ-8b (S6-4c: Gate-0 judges wording),
    # plus TS-4 (every DESIGN_SPEC test reference names a file of the tested code)
    assert len(checks.CHECKS) == 37
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
    assert checks.CHECKS["LG-1"].effects == {"land"}
    assert checks.CHECKS["TS-1"].effects == {"stage", "push"}
    assert checks.CHECKS["DS-1"].effects == {"push"}
    assert checks.CHECKS["DS-3"].effects == set()
    assert checks.CHECKS["DV-1"].effects == {"stage"}


def test_land_effect_never_appears_in_any_stage_complete_guard():
    land_ids = {check_id for check_id, spec in checks.CHECKS.items()
                if "land" in spec.effects}
    assert land_ids == {"LG-1", "LG-3", "LG-4"}
    for profile, sequence in checks.PROFILE_STAGES.items():
        for stage in sequence:
            routed = checks.stage_checks(profile, stage)
            assert ids(routed).isdisjoint(land_ids), (profile, stage)
            assert all("stage" in spec.effects for spec in routed)


def test_land_and_push_queries_route_only_their_effect():
    assert ids(checks.land_checks("full")) == {"LG-1", "LG-3", "LG-4"}
    assert not checks.land_checks("research")
    assert {"TS-1", "RV-1a", "RV-2", "DS-1"} <= ids(checks.push_checks("full"))
    assert all("push" in spec.effects for spec in checks.push_checks("direct"))


def test_requirements_run_at_evaluate_and_direct_has_none():
    rq = {"RQ-1", "RQ-2", "RQ-8"}
    assert rq <= ids(checks.stage_checks("full", "evaluate"))
    assert rq.isdisjoint(ids(checks.stage_checks("full", "plan")))
    for stage in checks.PROFILE_STAGES["direct"]:
        assert not any(cid.startswith(("RQ-", "DS-")) for cid in
                       ids(checks.stage_checks("direct", stage)))


def test_trivial_uses_full_requirements_direct_is_the_only_rq5_rq6_exemption():
    assert {"RQ-5", "RQ-6"} <= ids(checks.stage_checks("trivial", "evaluate"))
    assert ("direct", "RQ-5") in checks.EXEMPTIONS
    assert ("trivial", "RQ-5") not in checks.EXEMPTIONS


def test_build_checks_and_goal_is_no_profile():
    assert "goal" not in checks.PROFILES     # removed (two-ends §6)
    assert ids(checks.stage_checks("full", "build")) == {"BX-3"}
    assert ids(checks.stage_checks("direct", "build")) == {"BX-3"}


def test_review_and_deliver_stage_guards_are_exact():
    # S6-2: REVIEW completes only on the code a review round saw
    assert ids(checks.stage_checks("full", "review")) == {"RV-4"}
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
        for check_id in ("RQ-4", "DS-4a"):
            assert "not bugfix" in checks.EXEMPTIONS[(profile, check_id)]
    for check_id in ("DS-6",):
        assert "no REQUIREMENTS / DESIGN_SPEC" in checks.EXEMPTIONS[("direct", check_id)]
        assert "lite" in checks.EXEMPTIONS[("trivial", check_id)]
    assert "Gate-0" in checks.EXEMPTIONS[("direct", "RV-1b")]
    assert "Gate-0" in checks.EXEMPTIONS[("trivial", "RV-1b")]
    research_exempt = {cid for profile, cid in checks.EXEMPTIONS if profile == "research"}
    assert {"DS-1", "BX-3", "RV-1a", "RV-2", "TS-1", "LG-1", "MD-1"} <= research_exempt


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


def test_ds9a_refuses_a_probe_exit_now_that_the_pipeline_runs_nothing():
    """S6-1: no probe / mutation executor exists, so those exit kinds are unknown."""
    design = _context().design.copy()
    for kind in ("probe", "mutation"):
        design["exit_criteria"] = [{"id": "X1", "kind": kind, "covers": ["R1.1"]}]
        ok, detail = checks.evaluate("DS-9a", _context(design=design))
        assert not ok and "unknown exit kind" in detail, kind


def test_unknown_profile_stage_and_effect_are_rejected():
    with pytest.raises(checks.CheckError, match="profile"):
        checks.stage_checks("nope", "plan")
    with pytest.raises(checks.CheckError, match="not in profile"):
        checks.stage_checks("direct", "evaluate")
    with pytest.raises(checks.CheckError, match="effect"):
        checks._for("direct", "record")


def test_missing_risk_and_mode_provenance_fails_closed():
    ctx = _context()
    for check_id in ("MD-1", "MD-3", "BX-3", "TS-1", "LG-1", "LG-4"):
        assert checks.evaluate(check_id, ctx)[0] is False, check_id


def _signed_event(kind, payload, *, stage=None, round=None, attempt=None, ordinal=None):
    return events.make_event(
        writer=AUTH.writer(events.EVENT_SPECS[kind].producer), run_id="run_x",
        kind=kind, at="2026-10-22T00:00:00Z", payload=payload,
        stage=stage, round=round, attempt=attempt, ordinal=ordinal,
    )[0]


def test_gate0_handout_without_parsed_answer_does_not_pass_vacuously():
    handout = _signed_event("handout", {"expert_id": "gate0-skeptic"},
                            stage="evaluate", round=1, attempt=1)
    ctx = _context(events=[handout])
    ok, detail = checks.evaluate("RV-1b", ctx)
    assert not ok and "lacks parsed answer" in detail


def _report(repo, stage, tree, n_fail=0, ordinal=1, prev=None):
    return events.make_event(
        writer=AUTH.writer("cli"), run_id="run_x", kind="validation_report",
        at="2026-10-22T00:00:00Z", ordinal=ordinal,
        prev_hash=events.event_digest(prev) if prev else None,
        payload={"stage": stage, "repo": repo, "code_tree": tree, "n_pass": 1,
                 "n_fail": n_fail, "n_not_run": 0, "report_json": "{}"})[0]


def test_report_checks_bind_every_repo_to_its_current_tree():
    """BX-3 / TS-1 / LG-1 (S6-1): one report per expected repo, on the current tree."""
    trees = {"api": "1" * 40, "web": "2" * 40}
    patches = {"api": "a" * 40, "web": "b" * 40}
    run = {"code_trees": trees, "changed_paths": ["x.py"], "patch_ids": patches,
           "reviewed_patches": [patches]}
    first = _report("api", "test", trees["api"])
    both = [first, _report("web", "test", trees["web"], ordinal=2, prev=first)]
    ctx = _context(events=both, expected_repos={"api", "web"}, run=run)
    assert checks.evaluate("TS-1", ctx)[0] and checks.evaluate("LG-1", ctx)[0]
    one = _context(events=both[:1], expected_repos={"api", "web"}, run=run)
    ok, detail = checks.evaluate("TS-1", one)
    assert not ok and "web: no test validation report" in detail
    stale = _context(events=[_report("api", "test", "9" * 40)], expected_repos={"api"},
                     run={"code_trees": {"api": trees["api"]}})
    ok, detail = checks.evaluate("LG-1", stale)
    assert not ok and "test again" in detail
    red = _context(events=[_report("api", "test", trees["api"], n_fail=1)],
                   expected_repos={"api"}, run={"code_trees": {"api": trees["api"]}})
    ok, detail = checks.evaluate("TS-1", red)
    assert not ok and "failed command" in detail
    # S6-2: LG-1 also needs the patch REVIEW completed; another patch is unreviewed
    unreviewed = _context(events=both, expected_repos={"api", "web"},
                          run={**run, "reviewed_patches": [{**patches, "web": "c" * 40}]})
    assert checks.evaluate("TS-1", unreviewed)[0]
    ok, detail = checks.evaluate("LG-1", unreviewed)
    assert not ok and "never reviewed" in detail
    build = _context(events=[_report("api", "build", trees["api"])], expected_repos={"api"},
                     run={"code_trees": {"api": trees["api"]}, "changed_paths": ["x.py"]})
    assert checks.evaluate("BX-3", build)[0]
    assert not checks.evaluate("TS-1", build)[0]          # a BUILD report is not TEST's


def test_bx3_refuses_a_build_that_changed_nothing():
    tree = "1" * 40
    ctx = _context(events=[_report("api", "build", tree)], expected_repos={"api"},
                   run={"code_trees": {"api": tree}, "changed_paths": []})
    ok, detail = checks.evaluate("BX-3", ctx)
    assert not ok and "no change" in detail


def test_lg4_needs_main_still_at_the_runs_base():
    ctx = _context(expected_repos={"api"},
                   run={"landing_bases": {"api": "b"}, "run_bases": {"api": "b"}})
    assert checks.evaluate("LG-4", ctx)[0]
    moved = _context(expected_repos={"api"},
                     run={"landing_bases": {"api": "c"}, "run_bases": {"api": "b"}})
    assert not checks.evaluate("LG-4", moved)[0]

