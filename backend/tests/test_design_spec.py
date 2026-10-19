"""Tests for ``scripts/design_spec.py`` — the three design gates of the pipeline.

1. PLAN ``design`` (``plan_errors``): user workflow, mermaid architecture + flow with a
   failure branch, state lifecycle, numbered invariants and failure modes; the light tier
   (bugfix / direct) and exempt profiles (trivial / docs / research).
2. BUILD ``break_tests`` (``build_errors``): every invariant and failure mode targeted.
3. Fix by class (``finding_errors``): every fixed blocking finding names an invariant
   (or adds the missed one in ``design_updates``), a root cause, a class fix and a break
   test; non-blocking and unresolved findings are not judged.

0. EVALUATE ``root_cause`` (``root_cause_errors``) incl. the bugfix-profile rule, THINK
   ``solution_class`` (``solution_errors``) and PLAN ``root_invariant``, plus the Gate-0
   prompt / sha carrying the root cause.

Plus ``stage_errors`` routing (plan / build / review / deliver / goal_cycle), the CUTOVER
grandfathering (errors become warnings for older runs, an unreadable timestamp is judged)
and the wiring through ``artifact_cli._validate_stage_candidate``.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import design_spec as ds  # noqa: E402

NEW = "2099-01-01T00:00:00Z"
OLD = "2020-01-01T00:00:00Z"


def _eval(**over):
    rc = copy.deepcopy(ds.ROOT_TEMPLATE)
    rc.update(over)
    return {"understanding": {"claim": "x", "root_cause": rc}}


def _think(**over):
    sc = copy.deepcopy(ds.SOLUTION_TEMPLATE)
    sc.update(over)
    return {"alternatives": [{"what": "patch the line"}, {"what": "fix the rule", "targets_root": True}],
            "solution_class": sc}


def _plan(**over):
    d = copy.deepcopy(ds.PLAN_TEMPLATE)
    d.update(over)
    return {"design": d}


def _good_finding(**over):
    f = {"severity": "HIGH", "confidence": 9, "resolved": True, "specialist": "correctness",
         **copy.deepcopy(ds.FINDING_TEMPLATE)}
    f.update(over)
    return f


# ── 1. PLAN ──────────────────────────────────────────────────────────────────

def test_template_passes_and_exempt_profiles_skip():
    assert ds.plan_errors(_plan(), "full") == []
    for p in ("trivial", "docs", "research"):
        assert ds.plan_errors({}, p) == []


def test_missing_design_blocks_full_and_unknown_profile():
    assert ds.plan_errors({}, "full")
    assert ds.plan_errors({}, "made-up-profile"), "unknown profile must fail closed"
    assert ds.plan_errors({"design": "text"}, "full")


def test_flow_without_failure_branch_is_rejected():
    errs = ds.plan_errors(_plan(flow="flowchart TD\n  A[start] --> B[do]\n  B --> C[done]"), "full")
    assert any("failure branch" in e for e in errs)


def test_prose_is_not_a_diagram():
    errs = ds.plan_errors(_plan(architecture="the CLI calls the store, then the hook"), "full")
    assert any("architecture" in e for e in errs)
    errs = ds.plan_errors(_plan(flow="flowchart TD\n  A --> B"), "full")   # one edge
    assert any("design.flow" in e for e in errs)


def test_state_needs_lifecycle_and_crossings():
    st = copy.deepcopy(ds.PLAN_TEMPLATE["states"][0])
    st.pop("removed_by")
    st["crosses"] = []
    errs = ds.plan_errors(_plan(states=[st]), "full")
    assert any("removed_by" in e for e in errs)
    assert any("crosses" in e for e in errs)


def test_empty_states_need_a_reason():
    assert any("no_state_reason" in e for e in ds.plan_errors(_plan(states=[]), "full"))
    assert ds.plan_errors(_plan(states=[], no_state_reason="pure function, no files, rows or flags"),
                          "full") == []


def test_invariant_and_failure_mode_ids_and_counts():
    errs = ds.plan_errors(_plan(invariants=[{"id": "X1", "statement": "something must hold always"}]), "full")
    assert any("invariants" in e for e in errs)
    one_fm = ds.PLAN_TEMPLATE["failure_modes"][:1]
    assert any("failure_modes" in e for e in ds.plan_errors(_plan(failure_modes=one_fm), "full"))
    # one bad entry next to a good one is still rejected (no hiding junk behind a good row)
    bad = one_fm + [{"id": "F2", "scenario": "x", "expected": "y"}]
    assert any("failure_modes" in e for e in ds.plan_errors(_plan(failure_modes=bad), "full"))


def test_light_tier_skips_workflow_and_architecture_but_not_the_rest():
    d = _plan(failure_modes=ds.PLAN_TEMPLATE["failure_modes"][:1])
    d["design"].pop("user_workflow")
    d["design"].pop("architecture")
    assert ds.plan_errors(d, "bugfix") == []
    d["design"].pop("invariants")
    assert ds.plan_errors(d, "bugfix")


# ── 2. BUILD ─────────────────────────────────────────────────────────────────

def test_build_requires_a_break_test_per_target():
    plan = _plan()
    assert ds.build_errors({}, plan, "full")
    only_i1 = {"break_tests": [{"targets": ["I1", "F1"], "test": "backend/tests/t.py::test_a",
                                "attack": "kill mid-way then call again"}]}
    errs = ds.build_errors(only_i1, plan, "full")
    assert errs and "F2" in errs[-1]
    full = {"break_tests": only_i1["break_tests"] + [
        {"targets": ["F2"], "test": "backend/tests/t.py::test_b", "attack": "two sessions at once"}]}
    assert ds.build_errors(full, plan, "full") == []


def test_build_break_test_shape():
    bt = {"break_tests": [{"targets": ["I1", "F1", "F2"], "test": "test_a", "attack": "x"}]}
    errs = ds.build_errors(bt, _plan(), "full")
    assert any("path::test_name" in e for e in errs)
    assert any("attack" in e for e in errs)


def test_build_skips_without_a_plan_design_or_for_exempt_profiles():
    assert ds.build_errors({}, None, "full") == []
    assert ds.build_errors({}, {}, "full") == []
    assert ds.build_errors({}, _plan(), "docs") == []


# ── 3. Fix by class ──────────────────────────────────────────────────────────

def test_fixed_blocking_finding_needs_the_class_fields():
    plan = _plan()
    assert ds.finding_errors([_good_finding()], plan, None, "full", "review") == []
    bad = _good_finding(invariant="", root_cause="typo", class_fix="", break_test="nope")
    errs = ds.finding_errors([bad], plan, None, "full", "review")
    assert len(errs) == 4


def test_missed_invariant_is_accepted_only_via_design_updates():
    plan = _plan()
    f = _good_finding(invariant="I9")
    assert ds.finding_errors([f], plan, None, "full", "review")
    upd = [{"id": "I9", "statement": "the marker never outlives its run",
            "why_missed": "the lifecycle after landing was not designed"}]
    assert ds.finding_errors([f], plan, upd, "full", "review") == []
    thin = [{"id": "I9", "statement": "the marker never outlives its run"}]   # no why_missed
    assert ds.finding_errors([f], plan, thin, "full", "review")


def test_only_resolved_blocking_findings_are_judged():
    plan = _plan()
    bare = {"severity": "HIGH", "confidence": 9, "resolved": True}
    assert ds.finding_errors([bare], plan, None, "full", "r")
    assert ds.finding_errors([dict(bare, resolved=False)], plan, None, "full", "r") == []
    assert ds.finding_errors([dict(bare, severity="LOW")], plan, None, "full", "r") == []
    assert ds.finding_errors([dict(bare, severity="MEDIUM", confidence=6)], plan, None, "full", "r") == []
    assert ds.finding_errors([dict(bare, severity="MEDIUM", confidence="8/10")], plan, None, "full", "r")
    assert ds.finding_errors([dict(bare, severity="MED", confidence="high")], plan, None, "full", "r"), \
        "an unreadable confidence on a MED is judged, not waved through"


# ── stage routing + cutover ──────────────────────────────────────────────────

def test_stage_routing():
    plan = _plan()
    assert ds.stage_errors("plan", {}, profile="full", plan=None, run_created_at=NEW)
    assert ds.stage_errors("build", {}, profile="full", plan=plan, run_created_at=NEW)
    bad = [{"severity": "HIGH", "confidence": 9, "resolved": True}]
    assert ds.stage_errors("review", {"findings": bad}, profile="full", plan=plan, run_created_at=NEW)
    assert ds.stage_errors("deliver", {"adversarial_review": {"findings": bad}}, profile="full",
                           plan=plan, run_created_at=NEW)
    gc = ds.stage_errors("goal_cycle", {"adversarial_review": {"findings": bad}}, profile="goal",
                         plan=plan, run_created_at=NEW)
    assert any(e.startswith("Break tests") for e in gc) and any(e.startswith("Fix by class") for e in gc)
    assert ds.stage_errors("test", {}, profile="full", plan=plan, run_created_at=NEW) == []


def test_design_updates_read_from_the_record_too():
    upd = [{"id": "I9", "statement": "the marker never outlives its run", "why_missed": "not designed"}]
    art = {"findings": [_good_finding(invariant="I9")]}
    assert ds.stage_errors("review", art, profile="full", plan=_plan(), run_created_at=NEW,
                           record={"design_updates": upd}) == []


def test_older_runs_get_warnings_and_unreadable_timestamps_are_judged():
    w: list[str] = []
    assert ds.stage_errors("plan", {}, profile="full", plan=None, run_created_at=OLD, warnings=w) == []
    assert w and w[0].startswith("(not enforced for this run)")
    assert ds.stage_errors("plan", {}, profile="full", plan=None, run_created_at="garbage")
    assert ds.stage_errors("plan", {}, profile="full", plan=None, run_created_at="2099-01-01T00:00:00")
    assert ds.stage_errors("plan", {}, profile="full", plan=None, run_created_at=""), \
        "a missing date is judged: deleting created_at must not switch the gate off"
    assert ds.stage_errors("plan", {}, profile="full", plan=None, run_created_at=None)


def test_not_a_bug_and_skipped_tier_are_not_judged():
    nab = {"severity": "HIGH", "confidence": 9, "resolved": True,
           "finding": "Not a bug: the caller already holds the lock (file.py:12)"}
    assert ds.finding_errors([nab], _plan(), None, "full", "r") == []
    bad = [{"severity": "HIGH", "confidence": 9, "resolved": True}]
    assert ds.stage_errors("deliver", {"adversarial_review": {"profile_tier": "skipped", "findings": bad}},
                           profile="direct", plan=_plan(), run_created_at=NEW) == []


def test_shared_blocking_rule_catches_decorated_severities():
    f = {"severity": "HIGH (conf 9)", "confidence": 9, "resolved": True}
    assert ds.finding_errors([f], _plan(), None, "full", "r")


def test_review_findings_list_required_when_counted():
    errs = ds.stage_errors("review", {"findings_count": 2}, profile="full", plan=_plan(), run_created_at=NEW)
    assert any("findings_count is 2" in e for e in errs)
    assert ds.stage_errors("review", {"findings_count": 0}, profile="full", plan=_plan(),
                           run_created_at=NEW) == []


@pytest.mark.parametrize("bad", [3, True, "x"])
def test_wrong_types_give_errors_not_crashes(bad):
    assert ds.plan_errors(_plan(invariants=bad), "full")
    assert ds.plan_errors(_plan(failure_modes=bad), "full")
    assert ds.build_errors({"break_tests": [{"targets": bad, "test": "a.py::t", "attack": "kill it mid-way"}]},
                           _plan(), "full")
    ds.finding_errors([_good_finding(invariant="I9")], _plan(), bad, "full", "r")


def test_published_templates_pass_their_own_design_gates():
    import pipeline_validator as pv
    T = pv.STAGE_TEMPLATES
    plan = {"design": T["plan"]["design"]}
    for stage in ("evaluate", "think", "plan", "build", "review", "deliver", "goal_cycle"):
        assert ds.stage_errors(stage, T[stage], profile="full" if stage != "goal_cycle" else "goal",
                               plan=plan, evaluate=T["evaluate"], run_created_at=NEW) == [], stage


# ── wiring through artifact_cli ──────────────────────────────────────────────

def test_validate_stage_candidate_runs_the_design_gate(monkeypatch):
    import artifact_cli
    monkeypatch.setattr(artifact_cli, "_stage_artifact", lambda *a, **k: None)
    errs, _ = artifact_cli._validate_stage_candidate(
        project="P", run_id="run_x", stage="plan", profile="full",
        artifact={"acceptance_criteria": ["a"]}, record_in={}, cand_rec={"status": "completed"},
        cand_run={"created_at": NEW, "stages": []})
    assert any(e.startswith("Design spec") for e in errs)
    errs, warns = artifact_cli._validate_stage_candidate(
        project="P", run_id="run_x", stage="plan", profile="full",
        artifact={"acceptance_criteria": ["a"]}, record_in={}, cand_rec={"status": "completed"},
        cand_run={"created_at": OLD, "stages": []})
    assert not any(e.startswith("Design spec") for e in errs)
    assert any("Design spec" in w for w in warns)


# ── 0. EVALUATE root cause / THINK solution class ────────────────────────────

def test_root_template_passes_and_missing_root_blocks():
    assert ds.root_cause_errors(_eval(), "full") == []
    assert ds.root_cause_errors({"understanding": {"claim": "x"}}, "full")
    assert ds.root_cause_errors({}, "docs") == []


def test_shallow_why_chain_is_rejected():
    two = ds.ROOT_TEMPLATE["why_chain"][:2]
    assert any("why_chain" in e for e in ds.root_cause_errors(_eval(why_chain=two), "full"))
    no_ev = [dict(w, evidence="") for w in ds.ROOT_TEMPLATE["why_chain"]]
    assert any("why_chain" in e for e in ds.root_cause_errors(_eval(why_chain=no_ev), "full"))


def test_recurrence_must_be_searched():
    errs = ds.root_cause_errors(_eval(recurrence={"searched": ["grep -rn x ."],
                                                  "prior_instances": [], "siblings": []}), "full")
    assert any("searched" in e for e in errs)
    assert any("recurrence is missing" in e for e in ds.root_cause_errors(_eval(recurrence=None), "full"))
    errs = ds.root_cause_errors(_eval(root_layer="somewhere", missed_invariant=""), "full")
    assert any("root_layer" in e for e in errs) and any("missed_invariant" in e for e in errs)


def test_bugfix_profile_only_for_an_isolated_code_line_root():
    clean = {"searched": ["git log -S'x'", "grep -rn x backend"], "prior_instances": [], "siblings": []}
    assert ds.root_cause_errors(_eval(root_layer="code-line", recurrence=clean), "bugfix") == []
    for over in ({"root_layer": "module-design", "recurrence": clean},          # design-level root
                 {"root_layer": "code-line"}):                                  # template has a prior + a sibling
        errs = ds.root_cause_errors(_eval(**over), "bugfix")
        assert any("whack-a-mole" in e and "--profile full" in e for e in errs), over
    assert ds.root_cause_errors(_eval(root_layer="code-line"), "full") == []


def test_solution_class_template_passes_and_binds_to_the_root():
    assert ds.solution_errors(_think(), _eval(), "full") == []
    assert ds.solution_errors({}, {"understanding": {}}, "full"), "no root upstream on a strict run → refused"
    assert ds.solution_errors({}, {"understanding": {}}, "research") == []
    assert any("solution_class is missing" in e
               for e in ds.solution_errors({"alternatives": _think()["alternatives"]}, _eval(), "full"))


def test_alternatives_must_include_a_root_fix():
    th = _think()
    th["alternatives"] = [{"what": "a"}, {"what": "b"}]
    assert any("targets_root" in e for e in ds.solution_errors(th, _eval(), "full"))
    th["alternatives"] = [{"what": "a", "targets_root": True}]
    assert any(">= 2" in e for e in ds.solution_errors(th, _eval(), "full"))


def test_systemic_fix_must_address_the_deepest_level_and_every_sibling():
    errs = ds.solution_errors(_think(addresses="W1"), _eval(), "full")
    assert any("not the root W3" in e for e in errs)
    assert ds.solution_errors(_think(addresses="W1", why_not_deepest="W3 is an external API we do not own"),
                              _eval(), "full") == []
    assert any("C1" in e for e in ds.solution_errors(_think(covers=[]), _eval(), "full"))
    assert ds.solution_errors(_think(covers=[{"id": "C1", "excluded": "dead code, deleted in this run"}]),
                              _eval(), "full") == []


def test_design_level_root_needs_a_structural_change_or_a_real_reason():
    errs = ds.solution_errors(_think(structural_change="none", structural_reason="not needed here"),
                              _eval(), "full")
    assert any("refactor / redesign it" in e for e in errs)
    long = "the rule already lives in one module; only its one caller skips it and is moved"
    assert ds.solution_errors(_think(structural_change="none", structural_reason=long), _eval(), "full") == []


def test_patch_only_as_declared_p0_with_follow_up():
    p = {"verdict": "patch"}
    assert any("P0" in e for e in ds.solution_errors(_think(**p), _eval(), "full"))
    ok = _think(verdict="patch", p0_reason="prod is losing user data every minute right now",
                follow_up="ToDo: root-fix run for W3")
    assert ds.solution_errors(ok, _eval(), "full") == []
    assert any("verdict" in e for e in ds.solution_errors(_think(verdict="maybe"), _eval(), "full"))


def test_plan_must_encode_the_missed_invariant():
    assert ds.stage_errors("plan", _plan(), profile="full", plan=None, evaluate=_eval(),
                           run_created_at=NEW) == []
    bad = _plan(root_invariant="I7")
    errs = ds.stage_errors("plan", bad, profile="full", plan=None, evaluate=_eval(), run_created_at=NEW)
    assert any("root_invariant" in e for e in errs)
    assert ds.stage_errors("plan", bad, profile="full", plan=None, evaluate={}, run_created_at=NEW) == []


def test_root_gates_have_their_own_cutover():
    between = "2026-10-19T09:00:00Z"   # after CUTOVER, before ROOT_CUTOVER
    w: list[str] = []
    assert ds.stage_errors("evaluate", {}, profile="full", plan=None, run_created_at=between, warnings=w) == []
    assert w and "Root cause" in w[0]
    assert ds.stage_errors("evaluate", {}, profile="full", plan=None, run_created_at=NEW)
    assert ds.stage_errors("think", {}, profile="full", plan=None, evaluate=_eval(), run_created_at=NEW)


def test_gate0_prompt_and_sha_carry_the_root_cause(tmp_path, monkeypatch):
    import artifact_cli
    und = {"work_type": "bugfix", "claim": "the CLI drops the flag", "evidence": "cli.py:10",
           "evidence_kind": "code-trace"}
    old = artifact_cli._gate0_understanding_sha(und)
    assert artifact_cli._gate0_understanding_sha(dict(und, root_cause=None)) == old, \
        "an absent root_cause keeps the pre-change sha (in-flight handouts stay bound)"
    with_rc = dict(und, root_cause=ds.ROOT_TEMPLATE)
    assert artifact_cli._gate0_understanding_sha(with_rc) != old
    path, err = artifact_cli._write_gate0_prompt(tmp_path, with_rc, [])
    assert path and not err
    text = Path(path).read_text(encoding="utf-8")
    assert "Root cause (why chain + recurrence search)" in text and "missed_invariant" in text
    assert "5 questions" in text and "is its last why_chain level the ROOT" in text


# ── review round 2: re-checked bugfix rule, missing upstream root, gate0 pre-check ──

def test_bugfix_switched_after_evaluate_is_caught_at_think_and_plan():
    errs = ds.solution_errors(_think(), _eval(), "bugfix")
    assert any("whack-a-mole" in e for e in errs)
    errs = ds.stage_errors("plan", _plan(), profile="bugfix", plan=None, evaluate=_eval(), run_created_at=NEW)
    assert any("whack-a-mole" in e for e in errs)


def test_strict_think_without_an_upstream_root_cause_is_refused():
    errs = ds.stage_errors("think", _think(), profile="full", plan=None, evaluate={"understanding": {}},
                           run_created_at=NEW)
    assert any("no understanding.root_cause" in e for e in errs)
    w: list[str] = []
    assert ds.stage_errors("think", _think(), profile="full", plan=None, evaluate=None,
                           run_created_at=OLD, warnings=w) == [] and w


def test_gate0_prompt_prechecks_the_root_cause_on_new_runs(ws, capsys):
    import json as _json
    import artifact_cli as cli
    from tests.test_stage_boundary_commands import PROJECT, _call, _make_run
    root, reg = ws
    _make_run(root, "run_rc_new", "bugfix", created_at=NEW)
    _make_run(root, "run_rc_old", "bugfix")                      # fixture default: before the cutover
    und = {"work_type": "bugfix", "claim": "the CLI drops the flag on retry",
           "evidence": "cli.py:10 returns before the flag is copied", "evidence_kind": "code-trace",
           "root_cause": ds.ROOT_TEMPLATE}                        # module-design root under bugfix
    data = _json.dumps({"understanding": und})
    code, out = _call(cli.cmd_gate0_prompt, reg, capsys, project=PROJECT, run_id="run_rc_new", data=data)
    assert code == 1 and "whack-a-mole" in out["error"]
    code, out = _call(cli.cmd_gate0_prompt, reg, capsys, project=PROJECT, run_id="run_rc_old", data=data)
    assert code == 0 and out["ok"], out


from tests.test_stage_boundary_commands import ws  # noqa: E402,F401  (fixture)
