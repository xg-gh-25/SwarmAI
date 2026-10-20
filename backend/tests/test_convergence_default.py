"""Deterministic convergence step 3b: a run on the deterministic Gate-2 flow can finish
DELIVER on it, because the doors that judge DELIVER follow the flow
(Knowledge/Designs/2026-10-19-pipeline-deterministic-convergence.md §2.5, §7 step 3).

What is tested:
- the flow stays opt-in: run-create records convergence=deterministic only for
  --convergence deterministic (the default is not switched until runner-written proofs
  and the shared-run case exist);
- pipeline_validator.model_verdicts_decide is the one predicate for the mode, and
  artifact_cli._deterministic asks it;
- under deterministic, the publish door (validate_artifact_data) and the completion
  door (_check_depth) report none of the six model-verdict errors (unresolved
  finding x2, Red Team x2, meta_review, convergence), while every non-model check
  still runs; the same artifact under the legacy flow reports all of them;
- artifact_cli._red_team_required_now never fires for a deterministic run;
- stage-complete's derived fields replace a deterministic run's adversarial_review
  findings with the gate2-next ledger, keeping the CLI's own spawned/evidence, and
  carry each finding's reported severity for display;
- the DELIVER card sends a deterministic run to the gate2-next loop.
Methodology: pure functions on in-memory artifacts; run-create through its command
function in a temp workspace; the ledger view is monkeypatched.
Invariants: I1 every model-verdict check asks the one predicate; I2 a deterministic
artifact's findings are the ledger's; I3 a run without the key is validated as before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pipeline_validator as pv  # noqa: E402
import scripts.artifact_cli as cli  # noqa: E402
from core.artifact_registry import ArtifactRegistry  # noqa: E402

_SKILL = _BACKEND / "skills" / "s_autonomous-pipeline" / "stages"
_AFTER_CUTOVER = "2026-10-20T00:00:00+00:00"

# One error string per model-verdict check; a missed site keeps its string.
_MODEL_VERDICT_ERRORS = {
    "publish: unresolved finding": "unresolved blocking finding",
    "publish: Red Team": "Red Team required",
    "publish: meta_review": "meta_review missing",
    "publish: convergence": "convergence missing",
}
_DEPTH_ERRORS = {
    "completion: unresolved finding": "Depth: adversarial_review has",
    "completion: Red Team": "Depth: Red Team required",
}


def _deliver_artifact() -> dict:
    """A DELIVER artifact with one open specialist HIGH, no meta_review and no
    convergence block; every non-model field present."""
    return {
        "title": "t", "quality": {"push_ready": False},
        "completion_audit": {"all_green": True},
        "ac_verification": {"status": "pass"},
        "adversarial_review": {
            "spawned": True, "profile_tier": "full", "evidence": "ledger",
            "findings": [{"severity": "HIGH", "confidence": 9, "specialist": "correctness",
                          "description": "off by one"}]},
    }


# ── AC1: run-create default ──────────────────────────────────────────────────
@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)
    monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    return tmp_path


def _create(capsys, ws, **kw) -> dict:
    args = SimpleNamespace(project="Proj", requirement="REQ", profile=None, intent=None, **kw)
    cli.cmd_run_create(args, ArtifactRegistry(workspace_root=str(ws)))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return json.loads(Path(out["file"]).read_text(encoding="utf-8"))


def test_run_create_default_stays_legacy(ws, capsys):
    """Gate-2 R1 (integration-af71f7, correctness-d96ccc): a default deterministic run on
    a shared (non-isolated) checkout gets no snapshot, so no proof, so it can never be
    push-ready; the default stays legacy until that case exists."""
    st = _create(capsys, ws)
    assert st.get("convergence") != "deterministic"
    assert not cli._deterministic(st)


def test_run_create_deterministic_on_request(ws, capsys):
    st = _create(capsys, ws, convergence="deterministic")
    assert st["convergence"] == "deterministic"
    assert cli._deterministic(st)


# ── one predicate (I1, I3) ───────────────────────────────────────────────────
@pytest.mark.parametrize("mode,decides", [
    (None, True), ("legacy", True), ("deterministic", False), ("Deterministic", True), ("", True)])
def test_model_verdicts_decide_only_off_for_deterministic(mode, decides):
    assert pv.model_verdicts_decide(mode) is decides
    assert cli._deterministic({"convergence": mode}) is (not decides)


def test_deterministic_delegates_to_the_validator_predicate(monkeypatch):
    monkeypatch.setattr(pv, "model_verdicts_decide", lambda mode: False)
    assert cli._deterministic({"convergence": "legacy"})


# ── AC2: publish door ────────────────────────────────────────────────────────
def _publish_errors(convergence):
    return pv.validate_artifact_data("deliver", _deliver_artifact(), profile="full",
                                     run_created_at=_AFTER_CUTOVER, warnings=[],
                                     convergence=convergence)


def test_legacy_publish_reports_every_model_verdict_error():
    errs = " | ".join(_publish_errors(None))
    for name, s in _MODEL_VERDICT_ERRORS.items():
        assert s in errs, name


def test_deterministic_publish_reports_no_model_verdict_error():
    errs = _publish_errors("deterministic")
    joined = " | ".join(errs)
    for name, s in _MODEL_VERDICT_ERRORS.items():
        assert s not in joined, name
    assert errs == [], errs  # the artifact is otherwise complete


def test_deterministic_publish_keeps_the_non_model_checks():
    art = _deliver_artifact()
    art["adversarial_review"]["profile_tier"] = "skipped"
    art["completion_audit"] = {"all_green": False, "gaps": ["AC2"]}
    joined = " | ".join(pv.validate_artifact_data(
        "deliver", art, profile="full", run_created_at=_AFTER_CUTOVER, warnings=[],
        convergence="deterministic"))
    assert "profile_tier" in joined
    assert "completion_audit.all_green=false" in joined


# ── AC4: completion door ─────────────────────────────────────────────────────
def _depth_errors(convergence):
    return pv._check_depth("deliver", _deliver_artifact(), "full",
                           run_created_at=_AFTER_CUTOVER, warnings=[],
                           convergence=convergence)


def test_legacy_completion_reports_both_model_verdict_errors():
    errs = " | ".join(_depth_errors(None))
    for name, s in _DEPTH_ERRORS.items():
        assert s in errs, name


def test_deterministic_completion_reports_neither():
    joined = " | ".join(_depth_errors("deterministic"))
    for name, s in _DEPTH_ERRORS.items():
        assert s not in joined, name


def test_validate_passes_the_runs_mode_to_check_depth(monkeypatch, tmp_path):
    seen = {}

    def spy(stage, data, profile, run_id="", **kw):
        seen["convergence"] = kw.get("convergence")
        return []
    monkeypatch.setattr(pv, "_check_depth", spy)
    run = {"id": "run_x", "profile": "full", "convergence": "deterministic",
           "created_at": _AFTER_CUTOVER,
           "stages": [{"stage": "deliver", "status": "completed", "artifact_id": "art_1"}]}
    monkeypatch.setattr(pv, "_load_run", lambda project, run_id: run, raising=False)
    monkeypatch.setattr(pv, "_load_artifact_data", lambda p, r, a: _deliver_artifact())
    try:
        pv.validate(project="P", run_id="run_x", stage="deliver")
    except Exception:  # noqa: BLE001 — only the argument reaching _check_depth matters
        pass
    assert seen.get("convergence") == "deterministic"


# ── AC5: Red Team trigger ────────────────────────────────────────────────────
def test_red_team_trigger_off_for_deterministic():
    art = _deliver_artifact()
    legacy = {"created_at": _AFTER_CUTOVER, "profile": "full"}
    assert cli._red_team_required_now(legacy, art)
    assert not cli._red_team_required_now({**legacy, "convergence": "deterministic"}, art)
    st = {**legacy, "convergence": "deterministic"}
    assert not cli._require_red_team_expert(st, art)
    assert "required_experts" not in st


# ── AC3: findings come from the ledger ───────────────────────────────────────
def test_deterministic_findings_are_the_ledger(monkeypatch, tmp_path):
    view = {"ok": True, "spawns": 3, "findings": [
        {"id": "F1", "round": 1, "expert": "correctness", "state": "proven",
         "test": "backend/tests/test_a.py::t1", "file": "a.py", "claim": "off by one"},
        {"id": "F2", "round": 1, "expert": "security", "state": "open",
         "test": None, "file": "b.py", "claim": "token leak"}],
        "push_ready_reasons": ["gate2: F2 open: token leak"]}
    monkeypatch.setattr(cli, "_gate2_next_view", lambda rid, rd, rs: view)
    art = _deliver_artifact()
    art["adversarial_review"]["findings"] = [{"severity": "LOW", "description": "model text",
                                              "resolved": True}]
    rs = {"id": "run_x", "project": "P", "convergence": "deterministic", "profile": "full",
          "created_at": _AFTER_CUTOVER, "stages": [],
          "dispatch_plan": {"deliver": ["correctness", "security", "red-team"]}}
    derived = cli._project_ledger_review("run_x", tmp_path, rs, art)
    ar = art["adversarial_review"]
    assert [f["id"] for f in ar["findings"]] == ["F1", "F2"]
    f1, f2 = ar["findings"]
    assert f1["resolved"] is True and f1["specialist"] == "correctness"
    assert f1["break_test"] == "backend/tests/test_a.py::t1"
    assert "proven" in f1["description"] and "test_a.py::t1" in f1["description"]
    assert f2["resolved"] is False and f2["ledger_state"] == "open"
    assert ar["spawned"] is True and ar["profile_tier"] == "full"
    assert ar["red_team_dispatched"] is True
    assert "2 finding(s), 1 proven" in ar["evidence"]
    assert derived["source"].startswith("gate2-next ledger")
    # the projected artifact passes the deterministic publish door
    assert pv.validate_artifact_data("deliver", art, profile="full",
                                     run_created_at=_AFTER_CUTOVER, warnings=[],
                                     convergence="deterministic") == []


def test_unreadable_ledger_projects_no_findings(monkeypatch, tmp_path):
    def boom(rid, rd, rs):
        raise OSError("marker store unreadable")
    monkeypatch.setattr(cli, "_gate2_next_view", boom)
    art = _deliver_artifact()
    rs = {"id": "run_x", "convergence": "deterministic", "dispatch_plan": {}}
    cli._project_ledger_review("run_x", tmp_path, rs, art)
    ar = art["adversarial_review"]
    assert ar["findings"] == [] and ar["spawned"] is False
    assert "OSError" in ar["evidence"]


def test_legacy_run_keeps_the_model_findings(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_gate2_next_view",
                        lambda *a: pytest.fail("legacy run must not read the ledger"))
    art = _deliver_artifact()
    before = json.dumps(art, sort_keys=True)
    assert cli._project_ledger_review("run_x", tmp_path, {"id": "run_x"}, art) is None
    assert json.dumps(art, sort_keys=True) == before


# ── AC6: the card ────────────────────────────────────────────────────────────
def test_deliver_card_sends_deterministic_runs_to_the_loop():
    card = (_SKILL / "deliver.md").read_text(encoding="utf-8")
    assert "ref/deliver/gate2-deterministic.md" in card
    ref = (_SKILL / "ref" / "deliver" / "gate2-deterministic.md").read_text(encoding="utf-8")
    for s in ("gate2-next", "spawn", "fix", "prove", "done", "--test", "--declare",
              "--fixed", "gate2-fixspec", "gate2-recheck", "meta-review", "push_ready"):
        assert s in ref, s


# ── the other DELIVER doors (REVIEW fixes) ───────────────────────────────────
def test_publish_deliver_projects_the_ledger_for_a_deterministic_run(tmp_path, monkeypatch):
    run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_pub"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(
        {"id": "run_pub", "profile": "full", "convergence": "deterministic",
         "created_at": _AFTER_CUTOVER, "stages": []}))
    seen = {}

    def spy_project(rid, rd, rs, data):
        seen["project"] = (rid, Path(rd), rs.get("convergence"))
        data["adversarial_review"] = {"findings": [{"id": "F1"}]}
        return {"value": {}, "source": "spy"}

    def spy_validate(stage, data, **kw):
        seen["validated"] = (data.get("adversarial_review"), kw.get("convergence"))
        return ["stop here"]
    monkeypatch.setattr(cli, "_project_ledger_review", spy_project)
    monkeypatch.setattr(pv, "validate_artifact_data", spy_validate)
    args = SimpleNamespace(project="P", type="delivery", data=json.dumps(_deliver_artifact()),
                           producer="s_autonomous-pipeline", summary="s", topic="",
                           stage="deliver", run_id="run_pub", quiet=True)
    with pytest.raises(SystemExit):
        cli.cmd_publish(args, ArtifactRegistry(workspace_root=str(tmp_path)))
    assert seen["project"] == ("run_pub", run_dir, "deterministic")
    assert seen["validated"] == ({"findings": [{"id": "F1"}]}, "deterministic")


def test_deterministic_push_ready_skips_the_legacy_convergence_verdict(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_gate2_outcome",
                        lambda *a: pytest.fail("legacy verdict read in a deterministic run"))
    monkeypatch.setattr(cli, "_gate2_next_view", lambda rid, rd, rs: {
        "ok": True, "spawns": 1, "findings": [], "push_ready_reasons": []})
    monkeypatch.setattr(cli, "_deliver_push_ready_bound", lambda **kw: (True, [], []))
    monkeypatch.setattr(cli, "_measure_stage_tokens", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_plan_exit_criteria", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_stage_budget", lambda *a, **k: {})
    rs = {"id": "run_x", "project": "P", "convergence": "deterministic", "profile": "full",
          "created_at": _AFTER_CUTOVER, "stages": [], "stage_timing": {},
          "dispatch_plan": {"deliver": ["correctness"]}}
    art = _deliver_artifact()
    rec = {}
    derived, _ = cli._derive_stage_fields(project="P", run_id="run_x", run_dir=tmp_path,
                                          run_state=rs, stage="deliver", profile="full",
                                          artifact=art, record_in={}, cand_rec=rec)
    assert "gate2_outcome" not in rec
    assert derived["adversarial_review"]["source"].startswith("gate2-next ledger")
    assert art["adversarial_review"]["findings"] == []


def test_deterministic_findings_carry_reported_severity(monkeypatch, tmp_path):
    """Gate-2 R1 (integration-512930): the projection reports each finding's severity as
    the expert gave it, so the HIGH counts in METRICS and REPORT are not zero."""
    from core.gate2_findings import entry_id
    high = {"severity": "HIGH", "confidence": 9, "path": "a.py", "summary": "off by one"}
    fid = entry_id("correctness", high)
    monkeypatch.setattr(cli, "_gate2_next_view", lambda rid, rd, rs: {
        "ok": True, "spawns": 1, "push_ready_reasons": [], "findings": [
            {"id": fid, "round": 1, "expert": "correctness", "state": "open", "test": None,
             "file": "a.py", "claim": "off by one"},
            {"id": fid + "-R2", "round": 2, "expert": "correctness", "state": "open",
             "test": None, "file": "a.py", "claim": "off by one"}]})
    monkeypatch.setattr(cli, "_gate2_v2_inputs", lambda rid, rd, rs: (
        ["correctness"], [{"expert_id": "correctness", "reported": [high]}], [], {}, 0))
    art = _deliver_artifact()
    rs = {"id": "run_x", "convergence": "deterministic", "dispatch_plan": {"deliver": ["correctness"]}}
    cli._project_ledger_review("run_x", tmp_path, rs, art)
    f1, f2 = art["adversarial_review"]["findings"]
    assert f1.get("severity") == "HIGH" and f1.get("confidence") == 9
    assert f2.get("severity") == "HIGH"  # a later round's id keeps the reported severity


# ── the Gate-2 roster is frozen once a fix exists (this run's own DELIVER re-entry) ──
def _ledger(monkeypatch, tmp_path, run_id, handouts: dict):
    import config
    data = tmp_path / "appdata"
    audit = data / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    files = {f"/x/runs/{run_id}/evidence/prompts/{name}": [["sha", at]]
             for name, at in handouts.items()}
    (audit / f"handout_{run_id}.json").write_text(json.dumps({"files": files}))


def test_reentry_does_not_add_an_expert_to_round_one(monkeypatch, tmp_path):
    """A DELIVER re-entry re-computes the dispatch from the grown diff and adds the Red
    Team; before the freeze it held R1 open, and the replay ignored every later fix and
    answer as out of order (a dead end)."""
    _ledger(monkeypatch, tmp_path, "run_r", {
        "correctness.md": "2026-10-20T02:38:07+00:00", "security.md": "2026-10-20T02:38:07+00:00",
        "correctness-delta.md": "2026-10-20T02:52:07+00:00",
        "red-team.md": "2026-10-20T02:52:07+00:00", "meta-review.md": "2026-10-20T02:38:07+00:00",
        "review-code-quality.md": "2026-10-20T02:31:00+00:00"})
    fix_ts = datetime_ts("2026-10-20T02:43:24+00:00")
    rs = {"dispatch_plan": {"deliver": ["correctness", "security", "red-team", "meta-review"]}}
    events = [{"kind": "fixed", "pass": 1, "ts": fix_ts}]
    assert cli._gate2_round_roster("run_r", rs, events) == ["correctness", "security"]
    # before any fix the current dispatch is the roster
    assert cli._gate2_round_roster("run_r", rs, []) == ["correctness", "security", "red-team"]


def test_an_expert_that_left_the_dispatch_keeps_its_round_one_findings(monkeypatch, tmp_path):
    _ledger(monkeypatch, tmp_path, "run_s", {
        "correctness.md": "2026-10-20T02:38:07+00:00", "security.md": "2026-10-20T02:38:07+00:00"})
    rs = {"dispatch_plan": {"deliver": ["correctness"]}}
    events = [{"kind": "fixed", "pass": 1, "ts": datetime_ts("2026-10-20T02:43:24+00:00")}]
    assert cli._gate2_round_roster("run_s", rs, events) == ["correctness", "security"]


def datetime_ts(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()
