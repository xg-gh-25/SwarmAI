"""Completion-gate cross_boundary_e2e enforcement (run_6b709df9).

WHAT IS TESTED: the profile-agnostic gate in artifact_cli.cmd_run_update
(`--status completed`) that BLOCKS completion when the EVALUATE artifact set
`cross_boundary.value == true` but NO stage artifact recorded a truthy
`cross_boundary_e2e.run`. This closes the run_889af826 miss: the goal profile
has no standalone `test` stage, so TEST's Layer-4 E2E was unreachable and a
cross-boundary goal run silently skipped mandatory E2E.

METHODOLOGY: build a temp workspace (run.json + manifest.json + artifact data
files that `_load_artifact_for_metrics` resolves), invoke the real
`cmd_run_update`, assert SystemExit (BLOCK) or clean completion.

KEY INVARIANTS:
- true-flag + NO e2e  → BLOCK  (teeth; mutation-proven RED-on-revert)
- true-flag + e2e     → OK     (evidence satisfies the gate)
- false-flag          → OK     (exempt, no ceremony tax)
- no evaluation artifact / cross_boundary absent → OK (fail-open, legacy runs)
"""
import argparse
import json
from datetime import datetime, timezone

import pytest


def _update_args(project, run_id):
    """A fully-defaulted Namespace for cmd_run_update (status=completed path)."""
    attrs = ("active_only actual_effort adversarial_count alternatives backend "
             "categories command context data ddd_checksums dismissed escalated "
             "evaluation_id event files_estimated files_touched fixed force_checkpoint "
             "frontend full indicators lessons limit modules outcome overlap partial "
             "probes producer profile project reason requirement resolved retries "
             "review_count rp_violations run_id scope stage stage_json state status "
             "summary taste_decision timestamp tokens_consumed topic type types "
             "user_override").split()
    ns = argparse.Namespace(**{a: None for a in attrs})
    ns.project = project
    ns.run_id = run_id
    ns.status = "completed"
    return ns


def _seed(workspace, project, run_id, *, profile, cross_boundary, e2e_in_stage,
          eval_has_artifact=True):
    """Write run.json + manifest.json + artifact data files.

    cross_boundary: the dict to put on the evaluation artifact (or None to omit).
    e2e_in_stage: if True, the goal_cycle/changeset artifact carries a truthy
                  cross_boundary_e2e; if False it does not.
    eval_has_artifact: if False, the evaluate stage has no artifact_id (fail-open).
    """
    art_dir = workspace / "Projects" / project / ".artifacts"
    run_dir = art_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"artifacts": []}

    def _art(aid, data):
        rel = f"{aid}.json"
        (art_dir / rel).write_text(json.dumps(data), encoding="utf-8")
        manifest["artifacts"].append({"id": aid, "file": rel, "type": "x"})

    stages = []
    # evaluate
    eval_data = {"recommendation": "GO"}
    if cross_boundary is not None:
        eval_data["cross_boundary"] = cross_boundary
    if eval_has_artifact:
        _art("art_eval", eval_data)
        stages.append({"stage": "evaluate", "status": "completed",
                       "artifact_id": "art_eval", "token_cost": 100})
    else:
        stages.append({"stage": "evaluate", "status": "completed", "token_cost": 100})

    # think / plan (artifactless-ish, just completed)
    stages.append({"stage": "think", "status": "completed", "token_cost": 100})
    stages.append({"stage": "plan", "status": "completed", "artifact_id": "art_plan",
                   "token_cost": 100})
    _art("art_plan", {"approach": "x"})

    # the build-equivalent stage that would carry the e2e evidence
    if profile == "goal":
        cyc = {"stage": "goal_cycle", "status": "completed", "artifact_id": "art_cyc",
               "token_cost": 100, "dod_met": 3,
               "adversarial_review": {"spawned": True, "findings": []}}
        cyc_data = {"dod_met": 3, "adversarial_review": {"spawned": True, "findings": []}}
        if e2e_in_stage:
            cyc_data["cross_boundary_e2e"] = {"run": True, "test_file": "t.py",
                                              "drives_real": "seam", "mutation": "reverted -> RED"}
        _art("art_cyc", cyc_data)
        stages.append(cyc)
    else:  # full/bugfix: a test stage carries it
        tst_data = {"passed": True, "layers": {"ac_driven": {"run": True, "pass": 3}}}
        if e2e_in_stage:
            tst_data["cross_boundary_e2e"] = {"run": True, "test_file": "t.py",
                                              "drives_real": "seam", "mutation": "reverted -> RED"}
        _art("art_test", tst_data)
        stages.append({"stage": "build", "status": "completed", "artifact_id": "art_build",
                       "token_cost": 100})
        _art("art_build", {"files_changed": ["f.py"]})
        stages.append({"stage": "review", "status": "completed", "artifact_id": "art_rev",
                       "token_cost": 100})
        _art("art_rev", {"findings": []})
        stages.append({"stage": "test", "status": "completed", "artifact_id": "art_test",
                       "token_cost": 100})

    # deliver + reflect (present so the missing_stages gate passes)
    stages.append({"stage": "deliver", "status": "completed", "artifact_id": "art_del",
                   "token_cost": 100,
                   "adversarial_review": {"spawned": True, "findings": [],
                                          "evidence": "Agent tool", "profile_tier": "full"},
                   "completion_audit": {"all_green": True},
                   "ac_verification": {"status": "ok"}})
    _art("art_del", {"title": "t", "quality": {"tests_pass": True, "regressions": 0},
                     "adversarial_review": {"spawned": True, "findings": [],
                                            "evidence": "Agent tool"}})
    stages.append({"stage": "reflect", "status": "completed", "token_cost": 100,
                   "lessons": ["[guideline] A sufficiently long and substantive reflect lesson."]})

    (art_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    run_data = {
        "id": run_id, "project": project, "requirement": f"req {run_id}",
        "profile": profile, "status": "running", "stages": stages,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_dir / "run.json").write_text(json.dumps(run_data, indent=2), encoding="utf-8")
    # a REPORT.md so the post-validator report gate is satisfied
    (run_dir / "REPORT.md").write_text("# Report\n" + "x" * 600, encoding="utf-8")
    return run_dir


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "Projects" / "P" / ".artifacts" / "runs").mkdir(parents=True)
    return tmp_path


def _run_complete(ws, monkeypatch, project, run_id):
    """Invoke cmd_run_update(--status completed); return ('block', payload) on
    SystemExit or ('ok', None) on clean return."""
    import scripts.artifact_cli as cli
    import pipeline_validator
    monkeypatch.setattr(cli, "_get_workspace", lambda: ws)
    # neutralize the artifact-shape validator backstop so we isolate the cb gate
    monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])
    from core.artifact_registry import ArtifactRegistry
    reg = ArtifactRegistry(ws)
    args = _update_args(project, run_id)
    try:
        cli.cmd_run_update(args, reg)
        return ("ok", None)
    except SystemExit:
        return ("block", None)


def test_goal_cross_boundary_true_without_e2e_BLOCKS(ws, monkeypatch):
    _seed(ws, "P", "run_g1", profile="goal",
          cross_boundary={"value": True, "kinds": ["event-bus"], "seam": "s"},
          e2e_in_stage=False)
    verdict, _ = _run_complete(ws, monkeypatch, "P", "run_g1")
    assert verdict == "block", "goal + cross_boundary=true + no e2e must BLOCK completion"


def test_goal_cross_boundary_true_with_e2e_OK(ws, monkeypatch):
    _seed(ws, "P", "run_g2", profile="goal",
          cross_boundary={"value": True, "kinds": ["event-bus"], "seam": "s"},
          e2e_in_stage=True)
    verdict, _ = _run_complete(ws, monkeypatch, "P", "run_g2")
    assert verdict == "ok", "goal + cross_boundary=true + recorded e2e must complete"


def test_full_cross_boundary_true_without_e2e_BLOCKS(ws, monkeypatch):
    _seed(ws, "P", "run_f1", profile="bugfix",
          cross_boundary={"value": True, "kinds": ["data-migration"], "seam": "s"},
          e2e_in_stage=False)
    verdict, _ = _run_complete(ws, monkeypatch, "P", "run_f1")
    assert verdict == "block", "bugfix/full + cross_boundary=true + no e2e must BLOCK too"


def test_cross_boundary_false_is_exempt_OK(ws, monkeypatch):
    _seed(ws, "P", "run_c1", profile="goal",
          cross_boundary={"value": False, "ruled_out": "pure-logic"},
          e2e_in_stage=False)
    verdict, _ = _run_complete(ws, monkeypatch, "P", "run_c1")
    assert verdict == "ok", "cross_boundary=false must be exempt (no ceremony tax)"


def test_no_evaluation_artifact_fails_open_OK(ws, monkeypatch):
    _seed(ws, "P", "run_n1", profile="goal", cross_boundary=None,
          e2e_in_stage=False, eval_has_artifact=False)
    verdict, _ = _run_complete(ws, monkeypatch, "P", "run_n1")
    assert verdict == "ok", "no evaluation artifact → fail-open (legacy runs never false-block)"
