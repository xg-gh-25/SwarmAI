"""Stage-boundary commands: ``artifact_cli.py stage-start`` / ``stage-complete``.

WHAT IS TESTED (pipeline-speed-systematic-fix §4-A, F2, G1, C4, C7)
- stage-complete validates EVERYTHING before writing; any failure prints one
  structured JSON line, exits 1 and leaves artifact files, manifest.json and
  run.json byte-identical. ``--check`` never writes.
- Every existing gate still blocks through stage-complete (missing required
  artifact field, invalid litmus, adversarial tier 'skipped' on a strict profile,
  stage-order violation) — the checks are validate_artifact_data + validate() run
  on the in-memory candidate, not a copy.
- Derived fields (F2): stage_doc_consumed, status, artifact_id,
  consumed_artifacts, token_cost (C5 measured / estimate / unavailable),
  files_changed from files_touched, findings counts, test layers/regressions from
  runner evidence (evidence wins; source recorded), push_ready upper bound.
- G1 exit contract: structure required at strict plan stage-complete (not on the
  legacy publish path); judging per kind test / probe / mutation / manual.
- direct profile (plan → build → deliver) end to end + its completion gate.
- Structured errors (A3): code / path / expected / got for several shapes, plus
  the additive ``structured_errors`` key on the legacy publish failure.
- stage-start: card, upstream, examples, stage_timing.started_at (C4 merge),
  review/deliver changed-file scope + expert dispatch + prompt FILES.
- Adversarial-review fixes: profile change may not drop a pending stage (HIGH-1);
  push_ready bound reads every report, freshness digests, passed>0, tests_pass
  (MED-2); replan after BUILD only adds criteria and never moves the manifest
  back (MED-3); legacy run-update caps push_ready (MED-4); direct without a
  contract is unmet (MED-5); write failure is one structured line with no stub
  re-point (MED-6); non-finite record numbers are rejected (LOW).

METHODOLOGY
- Command functions are driven in-process with a SimpleNamespace args object and
  a tmp_path workspace (SWARM_WORKSPACE via monkeypatch); stdout is parsed.
- "Writes nothing" is proven by snapshotting every file under the project's
  .artifacts/ tree (path → bytes) before and after the failing call.
- Evidence files (C2/C3) are hand-written fixtures in the documented shapes.
- The git scope test builds its fixture repo with plumbing (commit-tree), so no
  commit hooks run and no git config is touched.

KEY INVARIANTS
1. ok:false ⇒ zero bytes changed on disk.
2. ok:true ⇒ record completed + stage_doc_consumed + stage_timing.completed_at,
   artifact published through the publish code path, manifest advanced to the
   profile's NEXT stage (computed), next_action carries the continuation phrase.
3. push_ready stored ≤ the gate-computed bound; the model can only lower it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.artifact_cli as cli  # noqa: E402
import pipeline_validator as pv  # noqa: E402
from core.artifact_registry import ArtifactRegistry  # noqa: E402
from core.pipeline_profiles import PIPELINE_PROFILES, is_relaxed_profile  # noqa: E402

PROJECT = "TestProject"


# ── fixtures / helpers ─────────────────────────────────────────────────────

@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A tmp workspace with one project; returns (root, registry)."""
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    (tmp_path / "Projects" / PROJECT / ".artifacts").mkdir(parents=True)
    return tmp_path, ArtifactRegistry(tmp_path)


def _run_dir(root: Path, run_id: str) -> Path:
    return root / "Projects" / PROJECT / ".artifacts" / "runs" / run_id


def _make_run(root: Path, run_id: str, profile: str, stages=(), **extra) -> Path:
    d = _run_dir(root, run_id)
    d.mkdir(parents=True, exist_ok=True)
    recs = [s if isinstance(s, dict) else {"stage": s, "status": "completed"} for s in stages]
    state = {"id": run_id, "run_id": run_id, "project": PROJECT, "profile": profile,
             "status": "running", "requirement": "make X work", "stages": recs,
             "created_at": "2026-01-01T00:00:00+00:00", **extra}
    (d / "run.json").write_text(json.dumps(state), encoding="utf-8")
    return d


def _run_state(root: Path, run_id: str) -> dict:
    return json.loads((_run_dir(root, run_id) / "run.json").read_text(encoding="utf-8"))


def _snapshot(root: Path) -> dict:
    base = root / "Projects" / PROJECT / ".artifacts"
    return {str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*") if p.is_file()}


def _call(fn, reg, capsys, **kw):
    """Run a cmd_* function; return (exit_code, last stdout JSON line)."""
    code = 0
    try:
        fn(SimpleNamespace(**kw), reg)
    except SystemExit as e:
        code = e.code
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, (json.loads(lines[-1]) if lines else None)


def _complete(reg, capsys, run_id, stage, artifact=None, record=None, check=False):
    payload = {"record": record or {}}
    if artifact is not None:
        payload["artifact"] = artifact
    return _call(cli.cmd_stage_complete, reg, capsys, project=PROJECT, run_id=run_id,
                 stage=stage, data=json.dumps(payload), check=check)


def _start(reg, capsys, run_id, stage):
    return _call(cli.cmd_stage_start, reg, capsys, project=PROJECT, run_id=run_id, stage=stage)


def _artifact_on_disk(root: Path, artifact_id: str) -> dict:
    manifest = json.loads((root / "Projects" / PROJECT / ".artifacts" / "manifest.json").read_text())
    entry = next(e for e in manifest["artifacts"] if e["id"] == artifact_id)
    return json.loads((root / "Projects" / PROJECT / ".artifacts" / entry["file"]).read_text())


def _write_evidence(run_dir: Path, rel: str, data: dict) -> None:
    p = run_dir / "evidence" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def _c2(mode, run_id, results, failures=(), layers=None, finished="2026-01-01T00:10:00+00:00"):
    counts = {"passed": 0, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for r in results:
        key = {"passed": "passed", "failed": "failed", "error": "errors",
               "timeout": "timeouts", "skipped": "skipped"}[r["outcome"]]
        counts[key] += 1
    return {"schema": 1, "mode": mode, "run_id": run_id, "started_at": finished,
            "finished_at": finished, "duration_s": 1.0, "cached": False,
            "selected_files": [], "skipped_for_budget": [], "counts": counts,
            "results": list(results), "failures": list(failures),
            "layers": layers or {"ac_driven": {"run": True, "pass": 1, "fail": 0},
                                 "dependency_scoped": {"run": True, "tests": 1, "pass": 1},
                                 "import_smoke": {"run": True, "modules": 1, "pass": 1}}}


def _tmpl(stage: str) -> dict:
    import copy
    return copy.deepcopy(pv.STAGE_TEMPLATES[stage])


def _plan_artifact(**over) -> dict:
    a = _tmpl("plan")
    a["acceptance_criteria"] = ["AC1: X works for a valid input"]
    a["exit_criteria"] = [
        {"id": "X1", "kind": "test", "ref": "backend/tests/test_x.py::test_a", "expect": "pass"},
        {"id": "X2", "kind": "manual", "expect": "done", "ref": "page renders"},
    ]
    a.update(over)
    return a


def _build_artifact() -> dict:
    a = _tmpl("build")
    a["ac_coverage"] = [{"ac": "AC1: X works for a valid input", "plan_ac_ref": "AC1",
                         "impl": "backend/a.py::x()", "test": "backend/tests/test_x.py::test_a",
                         "verified": True}]
    return a


DECISION = [{"description": "kept the change local", "classification": "taste"}]


# ── direct profile: plan → build → deliver end to end (items 1, 2, 7, 8) ────

class TestDirectProfileFlow:
    def test_profile_shape_is_strict(self):
        assert PIPELINE_PROFILES["direct"] == ["plan", "build", "deliver"]
        assert is_relaxed_profile("direct") is False

    def test_plan_build_deliver(self, ws, capsys):
        root, reg = ws
        rid = "run_direct1"
        rd = _make_run(root, rid, "direct",
                       files_touched=["backend/a.py", "backend/tests/test_x.py"],
                       stage_timing={"plan": {"observed_events": 3}})

        code, out = _start(reg, capsys, rid, "plan")
        assert code == 0 and out["ok"] is True
        assert out["card"].endswith("stages/plan.md")
        assert out["exit_criteria_default"], "plan stage-start must hand out a default contract"
        timing = _run_state(root, rid)["stage_timing"]["plan"]
        assert timing["started_at"] and timing["observed_events"] == 3  # C4 merge

        code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
        assert code == 0, out
        assert out["next_stage"] == "build"
        assert cli._CONTINUATION_PHRASE in out["next_action"]
        assert "stages/build.md" in out["next_action"] and out["next_card"].endswith("build.md")
        assert reg.get_pipeline_state(PROJECT) == "build"
        plan_rec = cli._stage_record_of(_run_state(root, rid), "plan")
        assert plan_rec["status"] == "completed" and plan_rec["stage_doc_consumed"] is True
        assert plan_rec["artifact_id"] == out["artifact_id"]
        st = _run_state(root, rid)["stage_timing"]["plan"]
        assert st["completed_at"] and st["observed_events"] == 3

        # Evidence for the exit contract: X1 test passed; X2 manual in the record.
        _write_evidence(rd, "tests/latest-tdd.json", _c2("tdd", rid, [
            {"nodeid": "tests/test_x.py::test_a", "outcome": "passed", "file": "tests/test_x.py"}]))
        _start(reg, capsys, rid, "build")
        code, out = _complete(reg, capsys, rid, "build", _build_artifact(),
                              {"decisions": DECISION, "exit_evidence": {"X2": "checked the page"}})
        assert code == 0, out
        assert out["exit_criteria"]["met"] is True
        assert cli._EXIT_MET_DIRECTIVE in out["next_action"]
        build_rec = cli._stage_record_of(_run_state(root, rid), "build")
        assert build_rec["consumed_artifacts"] == [{"type": "design_doc", "id": plan_rec["artifact_id"]}]
        built = _artifact_on_disk(root, build_rec["artifact_id"])
        assert built["files_changed"] == ["backend/a.py", "backend/tests/test_x.py"]  # from files_touched
        assert out["derived"]["files_changed"]["source"] == "run.json files_touched"

        deliver = _tmpl("deliver")
        deliver["adversarial_review"]["findings_total"] = 99  # model value — overridden
        deliver["adversarial_review"]["findings"] = [
            {"severity": "LOW", "confidence": 3, "resolved": True, "finding": "a.py x() line 3: nit. Fixed: renamed."},
            {"severity": "LOW", "confidence": 3, "resolved": False, "finding": "a.py x() line 9: note only."},
        ]
        code, out = _complete(reg, capsys, rid, "deliver", deliver,
                              {"exit_evidence": {"X2": "checked the page"}})
        assert code == 0, out
        assert out["next_stage"] is None and cli._CONTINUATION_PHRASE in out["next_action"]
        rec = cli._stage_record_of(_run_state(root, rid), "deliver")
        ar = _artifact_on_disk(root, rec["artifact_id"])["adversarial_review"]
        assert (ar["findings_total"], ar["findings_fixed"], ar["findings_remaining"]) == (2, 1, 1)
        assert rec["push_ready"] is True, out["derived"]["push_ready"]
        assert rec["token_cost_source"] in ("estimate", "unavailable", "measured")

    def test_completion_gate_deliver_not_skippable(self, ws):
        root, _ = ws
        rid = "run_direct2"
        _make_run(root, rid, "direct", stages=[
            "plan", "build",
            {"stage": "deliver", "status": "skipped", "skip_reason": "no time to run the deliver gates"}])
        env = {**os.environ, "SWARM_WORKSPACE": str(root), "SWARM_TODO_DB": str(root / "t.db")}
        r = subprocess.run([sys.executable, str(_BACKEND / "scripts" / "artifact_cli.py"),
                            "run-update", "--project", PROJECT, "--run-id", rid,
                            "--status", "completed"],
                           capture_output=True, text=True, env=env, cwd=str(_BACKEND), timeout=60)
        assert r.returncode != 0
        assert "cannot be skipped" in (r.stdout + r.stderr)


# ── every existing gate still blocks; a block writes NOTHING (item 1, X1) ──

def _case_missing_field(root):
    _make_run(root, "run_gate1", "direct")
    art = _plan_artifact()
    del art["acceptance_criteria"]
    return "run_gate1", "plan", art, {"decisions": DECISION}, "E_MISSING_FIELD", "acceptance_criteria"


def _case_invalid_litmus(root):
    _make_run(root, "run_gate2", "full", stages=["evaluate", "think", "plan", "build"])
    art = _tmpl("review")
    art["litmus_gate"]["verdict"] = "MAYBE"
    return "run_gate2", "review", art, {"decisions": DECISION}, "E_LITMUS", "litmus_gate.verdict"


def _case_tier_skipped(root):
    _make_run(root, "run_gate3", "direct", stages=["plan", "build"])
    art = _tmpl("deliver")
    art["adversarial_review"]["profile_tier"] = "skipped"
    return "run_gate3", "deliver", art, {}, "E_ADVERSARIAL_TIER", "adversarial_review.profile_tier"


def _case_stage_order(root):
    _make_run(root, "run_gate4", "direct")  # plan never completed
    return "run_gate4", "build", _build_artifact(), {"decisions": DECISION}, "E_STAGE_ORDER", "stage"


def _case_bad_decision(root):
    _make_run(root, "run_gate5", "direct")
    rec = {"decisions": [{"description": "x", "classification": "vibes"}]}
    return ("run_gate5", "plan", _plan_artifact(), rec, "E_RECORD_SCHEMA",
            "record.decisions[0].classification")


def _case_exit_contract_missing(root):
    _make_run(root, "run_gate6", "direct")
    art = _plan_artifact()
    del art["exit_criteria"]
    return "run_gate6", "plan", art, {"decisions": DECISION}, "E_EXIT_CRITERIA", "exit_criteria"


@pytest.mark.parametrize("case", [
    _case_missing_field, _case_invalid_litmus, _case_tier_skipped, _case_stage_order,
    _case_bad_decision, _case_exit_contract_missing,
], ids=lambda f: f.__name__)
def test_blocked_stage_complete_writes_nothing(ws, capsys, case):
    root, reg = ws
    rid, stage, art, rec, code_, path = case(root)
    before = _snapshot(root)
    state_before = reg.get_pipeline_state(PROJECT)
    code, out = _complete(reg, capsys, rid, stage, art, rec)
    assert code == 1
    assert out["ok"] is False and out["stage"] == stage and out["legacy_errors"]
    hits = [e for e in out["errors"] if e["code"] == code_]
    assert hits, out["errors"]
    assert any(e["path"] == path for e in hits), hits
    assert _snapshot(root) == before, "a blocked stage-complete must write nothing"
    assert reg.get_pipeline_state(PROJECT) == state_before


def test_check_mode_writes_nothing(ws, capsys):
    root, reg = ws
    _make_run(root, "run_check", "direct")
    before = _snapshot(root)
    code, out = _complete(reg, capsys, "run_check", "plan", _plan_artifact(),
                          {"decisions": DECISION}, check=True)
    assert code == 0 and out["ok"] is True and out["check"] is True
    assert out["next_stage"] == "build"
    assert _snapshot(root) == before


def test_missing_decisions_stays_a_warning(ws, capsys):
    root, reg = ws
    _make_run(root, "run_nodec", "direct")
    code, out = _complete(reg, capsys, "run_nodec", "plan", _plan_artifact(), {}, check=True)
    assert code == 0, out
    assert any("No decisions classified" in w for w in out["warnings"])


def test_structured_error_shapes():
    errs = pv.structure_errors("build", [
        "Missing required field 'tdd' for stage 'build'",
        "Missing 'tdd.smoke_tests' — required for depth validation",
        "'runtime_patterns.patterns' is empty — must contain at least one entry",
        "something nobody mapped",
    ], data={"tdd": {"green_pass": True}})
    assert [e["code"] for e in errs] == ["E_MISSING_FIELD", "E_DEPTH_MISSING",
                                         "E_EMPTY_PATTERNS", "E_VALIDATION"]
    assert errs[0]["path"] == "tdd" and errs[0]["expected"]["smoke_tests"] == 3
    assert errs[1]["path"] == "tdd.smoke_tests" and errs[1]["expected"] == 3
    assert "got" not in errs[1]
    lit = pv.structure_errors("review", ["Litmus gate invalid verdict: 'X'. Must be PASS."],
                              data={"litmus_gate": {"verdict": "X"}})[0]
    assert (lit["code"], lit["path"], lit["got"], lit["expected"]) == (
        "E_LITMUS", "litmus_gate.verdict", "X", "PASS")
    run_upd = pv.structure_errors(None, ["[deliver] adversarial_review.profile_tier='lite' but ..."])[0]
    assert run_upd["code"] == "E_ADVERSARIAL_TIER" and run_upd["expected"] == "full"


def test_publish_failure_carries_structured_errors(ws, capsys):
    _, reg = ws
    args = SimpleNamespace(project=PROJECT, type="delivery", data='{"title": "x"}',
                           producer="s_autonomous-pipeline", summary="s", topic="",
                           stage="deliver", run_id=None, quiet=True)
    with pytest.raises(SystemExit):
        cli.cmd_publish(args, reg)
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["validation_failed"] is True and err["errors"]
    assert {e["code"] for e in err["structured_errors"]} & {"E_MISSING_FIELD", "E_DEPTH_MISSING"}


# ── derived fields from evidence (item 2) ──────────────────────────────────

def test_test_layers_and_regressions_come_from_evidence(ws, capsys):
    root, reg = ws
    rid = "run_testev"
    rd = _make_run(root, rid, "trivial", stages=["evaluate", "think", "build", "review"])
    layers = {"ac_driven": {"run": True, "pass": 7, "fail": 0},
              "dependency_scoped": {"run": True, "tests": 4, "pass": 4},
              "import_smoke": {"run": True, "modules": 2, "pass": 2}}
    _write_evidence(rd, "tests/latest-regression.json", _c2(
        "regression", rid,
        [{"nodeid": "tests/test_y.py::test_b", "outcome": "failed", "file": "tests/test_y.py"}],
        failures=[{"nodeid": "tests/test_y.py::test_b", "classification": "regression",
                   "summary": "assert 1 == 2"}],
        layers=layers))
    art = _tmpl("test")
    art["regressions"] = 0                                 # model claims 0 — evidence says 1
    art["layers"]["ac_driven"]["pass"] = 1
    code, out = _complete(reg, capsys, rid, "test", art, {"decisions": DECISION})
    assert code == 0, out
    stored = _artifact_on_disk(root, out["artifact_id"])
    assert stored["regressions"] == 1
    assert stored["layers"]["ac_driven"]["pass"] == 7
    assert out["derived"]["test_layers"]["source"] == "evidence/tests/latest-regression.json"
    rec = cli._stage_record_of(_run_state(root, rid), "test")
    assert rec["derived_sources"]["test_layers"] == "evidence/tests/latest-regression.json"


def test_token_cost_measured_when_c5_available(ws, capsys, monkeypatch):
    root, reg = ws
    rid = "run_tokens"
    _make_run(root, rid, "direct", session_id="sess_1",
              stage_timing={"plan": {"started_at": "2026-01-01T00:00:00+00:00"}})
    seen = {}

    def _fake(session_id, start_iso, end_iso, db_path=None):
        seen.update(session_id=session_id, start=start_iso, end=end_iso)
        return {"cost_usd": 0.1, "input_tokens": 100, "output_tokens": 20,
                "cache_read_tokens": 9999, "cache_creation_tokens": 5, "rows": 2}

    mod = types.ModuleType("core.run_delivery_summary")
    mod.token_usage_for_window = _fake
    monkeypatch.setitem(sys.modules, "core.run_delivery_summary", mod)
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    rec = cli._stage_record_of(_run_state(root, rid), "plan")
    assert rec["token_cost"] == 125 and rec["token_cost_source"] == "measured"
    assert rec["token_usage"]["rows"] == 2
    assert seen["session_id"] == "sess_1" and seen["start"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.parametrize("model_value,expected", [(4321, (4321, "estimate")), (None, (0, "unavailable"))])
def test_token_cost_fallback(model_value, expected, monkeypatch):
    monkeypatch.setitem(sys.modules, "core.run_delivery_summary", types.ModuleType("x"))
    rec: dict = {}
    usage = cli._measure_stage_tokens("s", "2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00")
    assert usage is None
    cli._apply_token_cost(rec, model_value, usage)
    assert (rec["token_cost"], rec["token_cost_source"]) == expected


# ── push_ready upper bound (item 2) ────────────────────────────────────────

def _clean_deliver():
    return _tmpl("deliver")


@pytest.mark.parametrize("mutate,expect_ok", [
    (lambda a: None, True),
    (lambda a: a["adversarial_review"]["findings"].append(
        {"severity": "HIGH", "resolved": False, "finding": "x"}), False),
    (lambda a: a["adversarial_review"]["findings"].append(
        {"severity": "MEDIUM", "confidence": 8, "resolved": False, "finding": "x"}), False),
    (lambda a: a["adversarial_review"]["findings"].append(
        {"severity": "MEDIUM", "confidence": 3, "resolved": False, "finding": "x"}), True),
    (lambda a: a["completion_audit"].update(all_green=False), False),
    (lambda a: a["quality"].update(regressions=2), False),
    (lambda a: a["meta_review"].update(risks=[{"severity": "HIGH", "risk": "x"}]), False),
    (lambda a: a["quality"].update(tests_pass=False), False),
])
def test_push_ready_bound_gates(mutate, expect_ok):
    art = _clean_deliver()
    mutate(art)
    ok, reasons = pv.push_ready_upper_bound(art, "full")
    assert ok is expect_ok, reasons


def test_push_ready_bound_exit_criteria_strict_only():
    unmet = {"met": False, "unmet": ["X1"]}
    assert pv.push_ready_upper_bound(_clean_deliver(), "direct", exit_result=unmet)[0] is False
    assert pv.push_ready_upper_bound(_clean_deliver(), "trivial", exit_result=unmet)[0] is True
    failing_ev = _c2("regression", "r", [{"nodeid": "a::b", "outcome": "failed"}],
                     failures=[{"nodeid": "a::b", "classification": "new", "summary": "x"}])
    assert pv.push_ready_upper_bound(_clean_deliver(), "full", test_evidence=failing_ev)[0] is False


def test_deliver_push_ready_stored_false_when_bound_false(ws, capsys):
    root, reg = ws
    rid = "run_prbound"
    _make_run(root, rid, "direct", stages=["plan", "build"])
    art = _clean_deliver()
    art["quality"]["regressions"] = 0
    art["quality"]["tests_pass"] = True
    # bound false via meta_review HIGH (not a validator block) — model says True
    art["meta_review"]["risks"] = [{"severity": "HIGH", "risk": "untested path"}]
    code, out = _complete(reg, capsys, rid, "deliver", art, {"push_ready": True})
    assert code == 0, out
    rec = cli._stage_record_of(_run_state(root, rid), "deliver")
    assert rec["push_ready"] is False and "meta_review" in rec["push_ready_reason"]


def _direct_run_with_manual_contract(root, reg, capsys, rid, **extra):
    """A direct run whose PLAN (via stage-complete) declares one manual criterion,
    with build recorded completed — deliver's bound is then judgeable (MED-5)."""
    _make_run(root, rid, "direct", **extra)
    plan = _plan_artifact(exit_criteria=[{"id": "X2", "kind": "manual", "ref": "page renders",
                                          "expect": "done"}])
    code, out = _complete(reg, capsys, rid, "plan", plan, {"decisions": DECISION})
    assert code == 0, out
    st = _run_state(root, rid)
    st["stages"].append({"stage": "build", "status": "completed"})
    (_run_dir(root, rid) / "run.json").write_text(json.dumps(st), encoding="utf-8")


def test_model_may_only_lower_push_ready_with_reason(ws, capsys):
    root, reg = ws
    _direct_run_with_manual_contract(root, reg, capsys, "run_lower")
    ev = {"exit_evidence": {"X2": "opened the page"}}
    code, out = _complete(reg, capsys, "run_lower", "deliver", _clean_deliver(),
                          {**ev, "push_ready": False})
    assert code == 1 and any(e["path"] == "record.push_ready_reason" for e in out["errors"])
    code, out = _complete(reg, capsys, "run_lower", "deliver", _clean_deliver(),
                          {**ev, "push_ready": False, "push_ready_reason": "waiting on a design call"})
    assert code == 0, out
    rec = cli._stage_record_of(_run_state(root, "run_lower"), "deliver")
    assert rec["push_ready"] is False and out["derived"]["push_ready"]["upper_bound"] is True


# ── G1 exit contract: structure + judging per kind (item 7) ────────────────

class TestExitCriteria:
    def test_structure_required_only_when_strict(self):
        assert pv.validate_exit_criteria(None, required=False) == []
        assert pv.validate_exit_criteria(None, required=True)
        bad = pv.validate_exit_criteria([{"id": "X1", "kind": "fuzz", "ref": "<x>", "expect": ""}], True)
        assert any(".kind" in e for e in bad) and any(".expect" in e for e in bad)
        assert any("placeholder" in e for e in
                   pv.validate_exit_criteria([{"id": "X1", "kind": "test", "ref": "<t>", "expect": "pass"}], True))

    def test_legacy_publish_path_does_not_require_it(self):
        art = _plan_artifact()
        del art["exit_criteria"]
        assert pv.validate_artifact_data("plan", art, profile="full") == []

    def test_judging_each_kind(self, tmp_path):
        rd = tmp_path
        _write_evidence(rd, "tests/latest-tdd.json", _c2("tdd", "r", [
            {"nodeid": "backend/tests/test_x.py::test_a", "outcome": "passed", "file": "backend/tests/test_x.py"},
            {"nodeid": "backend/tests/test_x.py::test_b", "outcome": "failed", "file": "backend/tests/test_x.py"},
        ], finished="2026-01-01T00:01:00+00:00"))
        _write_evidence(rd, "tests/latest-verify.json", _c2("verify", "r", [
            {"nodeid": "tests/test_z.py::test_c", "outcome": "passed", "file": "tests/test_z.py"}],
            finished="2026-01-01T00:02:00+00:00"))
        _write_evidence(rd, "probes/bash-fuzz.json", {"schema": 1, "name": "bash-fuzz",
                                                     "rounds": [{}], "converged": True, "findings": []})
        _write_evidence(rd, "probes/path-fuzz.json", {"schema": 1, "name": "path-fuzz",
                                                     "rounds": [{}], "converged": False, "findings": []})
        _write_evidence(rd, "mutations/latest.json", {"schema": 1, "all_killed": True, "mutations": [
            {"id": "M1", "file": "a.py", "killed": True, "red_tests": ["t"]}]})
        crit = [
            {"id": "T1", "kind": "test", "ref": "tests/test_x.py::test_a", "expect": "pass"},
            {"id": "T2", "kind": "test", "ref": "backend/tests/test_x.py", "expect": "pass"},
            {"id": "T3", "kind": "test", "ref": "mode:verify", "expect": "pass"},
            {"id": "T4", "kind": "test", "ref": "tests/test_never.py", "expect": "pass"},
            {"id": "P1", "kind": "probe", "ref": "bash-fuzz", "expect": "no_new_findings"},
            {"id": "P2", "kind": "probe", "ref": "path-fuzz", "expect": "no_new_findings"},
            {"id": "P3", "kind": "probe", "ref": "../escape", "expect": "no_new_findings"},
            {"id": "M1", "kind": "mutation", "ref": "spec.json", "expect": "all_killed"},
            {"id": "H1", "kind": "manual", "expect": "done"},
            {"id": "H2", "kind": "manual", "expect": "done"},
        ]
        res = pv.judge_exit_criteria(crit, rd, {"exit_evidence": {"H1": "looked at it"}})
        assert res["met"] is False
        assert sorted(res["unmet"]) == sorted(["T2", "T4", "P2", "P3", "H2"]), res["detail"]

    def test_no_criteria_is_not_judged(self, tmp_path):
        assert pv.judge_exit_criteria(None, tmp_path)["met"] is None

    def test_default_templates_by_work_type(self):
        bug = pv.default_exit_criteria("bugfix")
        assert [c["kind"] for c in bug] == ["test", "test", "probe"]
        feat = pv.default_exit_criteria("greenfield", ["AC1: a", "AC2: b"])
        assert feat[-1]["ref"] == "mode:verify" and len(feat) == 3
        # defaults are templates: the structure check refuses them until filled in
        assert pv.validate_exit_criteria(bug, required=True)


# ── stage-record schema (item 5) ────────────────────────────────────────────

@pytest.mark.parametrize("stage,record,needle", [
    ("reflect", {"lessons": ["too short"]}, "lessons[0]"),
    ("reflect", {}, "lessons"),
    ("build", {"status": "skipped"}, "skip_reason"),
    ("build", {"exit_evidence": {"X1": 3}}, "exit_evidence"),
    ("build", {"status": "done"}, "status"),
    ("plan", {"decisions": [{"classification": "taste"}]}, "decisions[0].description"),
])
def test_record_schema_errors(stage, record, needle):
    errors, _ = pv.validate_stage_record(stage, record)
    assert any(needle in e for e in errors), errors


def test_record_templates_and_schema_cover_every_stage():
    for stage in pv.STAGE_RECORD_SCHEMAS:
        assert pv.validate_stage_record(stage, pv.STAGE_RECORD_TEMPLATES[stage]) == ([], [])
    assert pv.get_stage_schema("reflect")["record_template"]["lessons"]
    assert pv.get_stage_schema("review")["enums"]["litmus_gate.verdict"] == ["PASS", "BORDERLINE", "FAIL"]


# ── stage-start: scope, experts, prompt files, size (item 3) ───────────────

def _git(repo: Path, *args: str, env=None) -> str:
    r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                       timeout=30, env={**os.environ, **(env or {})})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _fixture_repo(tmp_path: Path) -> Path:
    """A repo whose HEAD holds core/a.py — built with plumbing (no commit hooks)."""
    repo = tmp_path / "repo"
    (repo / "core").mkdir(parents=True)
    (repo / "core" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "update-index", "--add", "core/a.py")
    tree = _git(repo, "write-tree")
    ident = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t"}
    sha = _git(repo, "commit-tree", tree, "-m", "init", env=ident)
    _git(repo, "update-ref", "HEAD", sha)
    return repo


def test_stage_start_deliver_scope_experts_and_prompts(ws, capsys):
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "core" / "a.py").write_text("x = 1\n" + "y = 2\n" * 60, encoding="utf-8")
    (repo / "routers").mkdir()
    (repo / "routers" / "r.py").write_text("z = 3\n" * 70, encoding="utf-8")
    rid = "run_scope"
    rd = _make_run(root, rid, "full",
                   stages=["evaluate", "think", "plan", "build", "review", "test"],
                   files_touched=[str(repo / "core" / "a.py"), str(repo / "routers" / "r.py")],
                   required_experts=["concurrency"])
    code, out = _start(reg, capsys, rid, "deliver")
    assert code == 0, out
    by_path = {Path(c["path"]).name: c for c in out["changed_files"]}
    assert by_path["a.py"]["status"] == "M" and by_path["a.py"]["added"] == 60
    assert by_path["r.py"]["status"] == "A" and by_path["r.py"]["added"] == 70
    assert out["diff_insertions"] == 130 and out["full_tier_override"] is True
    ids = {e["id"] for e in out["experts"]}
    assert {"correctness", "security", "api-contract", "integration", "concurrency"} <= ids
    assert "red-team" not in ids  # 130 lines ≤ 200
    for e in out["experts"]:
        if "prompt_path" in e:
            body = Path(e["prompt_path"]).read_text(encoding="utf-8")
            assert body.startswith(f"EXPERT-ID: {e['id']}")
            assert Path(e["prompt_path"]).parent == rd / "evidence" / "prompts"
    assert all("prompt_path" in e for e in out["experts"]), out["warnings"]
    assert "You are a specialist code reviewer" not in json.dumps(out), \
        "prompt bodies are returned as paths, never inline"
    assert len(json.dumps(out, ensure_ascii=False).encode()) <= 8 * 1024
    assert _run_state(root, rid)["stage_timing"]["deliver"]["started_at"]


@pytest.mark.parametrize("profile,work_type,ins,dels,required,expect", [
    ("full", None, 10, 5, [], set()),                               # < 50 lines → none
    ("full", None, 10, 5, ["security"], {"security"}),              # … unless required
    ("bugfix", None, 60, 0, [], {"correctness", "security"}),       # bugfix default
    ("bugfix", "refactor", 60, 0, [],
     {"correctness", "api-contract", "integration", "red-team"}),   # refactor rule
    ("full", None, 150, 100, [], {"correctness", "red-team"}),      # > 200 lines
])
def test_dispatch_rules(profile, work_type, ins, dels, required, expect):
    scope = {"changed_files": [{"path": "docs/x.md", "status": "M", "added": ins, "removed": dels}],
             "diff_insertions": ins, "diff_deletions": dels, "full_tier_override": ins > 100}
    got = {e["id"] for e in cli._dispatch_experts(profile=profile, work_type=work_type,
                                                    scope=scope, required=required)}
    assert got == expect


def test_stage_start_rejects_off_profile_stage(ws, capsys):
    root, reg = ws
    _make_run(root, "run_off", "direct")
    code, out = _start(reg, capsys, "run_off", "review")
    assert code == 1 and out["errors"][0]["code"] == "E_PROFILE"


# ── adversarial-review fixes: HIGH-1 … MED-6, LOW ──────────────────────────

def _run_update_args(run_id, **kw):
    ns = SimpleNamespace(project=PROJECT, run_id=run_id, status=None, stage_json=None,
                         taste_decision=None, profile=None, ddd_checksums=None,
                         files_touched=None, add_required_expert=None, force=False,
                         reason=None, force_checkpoint=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@pytest.mark.parametrize("current,stages,new,blocked", [
    ("bugfix", ["evaluate", "think", "plan", "build"], "direct", True),   # same rank (HIGH-1)
    ("full", ["evaluate"], "direct", True),
    ("trivial", ["evaluate", "think"], "full", False),                    # upgrade keeps all
    ("goal", ["evaluate", "think", "plan", "goal_cycle"], "full", False), # dropped stage done
])
def test_profile_change_may_not_drop_pending_stages(ws, capsys, current, stages, new, blocked):
    root, reg = ws
    rid = f"run_pc_{current}"
    _make_run(root, rid, current, stages=stages)
    code, out = _call(cli.cmd_run_update, reg, capsys,
                      **vars(_run_update_args(rid, profile=new)))
    if blocked:
        assert code == 1 and _run_state(root, rid)["profile"] == current
        if current == "bugfix":
            assert out["code"] == "E_PROFILE_CHANGE" and out["ok"] is False
            assert {"review", "test", "reflect"} <= set(out["missing_stages"])
    else:
        assert code == 0, out
        assert _run_state(root, rid)["profile"] == new


def test_push_ready_false_when_evidence_predates_a_later_edit(ws, capsys, monkeypatch):
    """MED-2: a green report whose digests no longer match files_touched caps it."""
    import hashlib
    root, reg = ws
    monkeypatch.setenv("SWARM_TEST_REPO_ROOT", str(root))
    src = root / "backend" / "a.py"
    src.parent.mkdir(parents=True)
    src.write_text("x = 1\n", encoding="utf-8")
    rid = "run_fresh"
    _direct_run_with_manual_contract(root, reg, capsys, rid, files_touched=["backend/a.py"])
    ev = _c2("verify", rid, [{"nodeid": "tests/test_x.py::test_a", "outcome": "passed",
                              "file": "tests/test_x.py"}])
    ev["tree_digest"] = {"backend/a.py": hashlib.sha256(src.read_bytes()).hexdigest()}
    _write_evidence(_run_dir(root, rid), "tests/latest-verify.json", ev)
    rec = {"exit_evidence": {"X2": "opened the page"}}
    code, out = _complete(reg, capsys, rid, "deliver", _clean_deliver(), rec, check=True)
    assert code == 0 and out["derived"]["push_ready"]["upper_bound"] is True, out
    src.write_text("x = 2  # edited after the test run\n", encoding="utf-8")
    code, out = _complete(reg, capsys, rid, "deliver", _clean_deliver(), rec, check=True)
    pr = out["derived"]["push_ready"]
    assert pr["upper_bound"] is False
    assert any("predates later edits" in r for r in pr["reasons"]), pr


def test_undigested_evidence_keeps_behavior_with_a_warning(ws, capsys):
    root, reg = ws
    rid = "run_nodigest"
    _direct_run_with_manual_contract(root, reg, capsys, rid, files_touched=["backend/a.py"])
    _write_evidence(_run_dir(root, rid), "tests/latest-verify.json", _c2("verify", rid, [
        {"nodeid": "tests/test_x.py::test_a", "outcome": "passed", "file": "tests/test_x.py"}]))
    code, out = _complete(reg, capsys, rid, "deliver", _clean_deliver(),
                          {"exit_evidence": {"X2": "opened the page"}}, check=True)
    assert code == 0 and out["derived"]["push_ready"]["upper_bound"] is True, out
    assert any("no tree_digest" in w for w in out["warnings"]), out["warnings"]


def test_bound_reads_all_reports_passed_count_and_tests_pass_flag():
    green = _c2("verify", "r", [{"nodeid": "a::b", "outcome": "passed"}],
                finished="2026-01-01T05:00:00+00:00")
    older_reg = _c2("regression", "r", [{"nodeid": "a::c", "outcome": "failed"}],
                    failures=[{"nodeid": "a::c", "classification": "regression", "summary": "x"}])
    ok, reasons = pv.push_ready_upper_bound(_clean_deliver(), "full", test_evidence=green,
                                            all_test_evidence=[green, older_reg])
    assert ok is False and any("regression-classified" in r for r in reasons)
    empty = _c2("verify", "r", [])
    assert pv.push_ready_upper_bound(_clean_deliver(), "full", test_evidence=empty)[0] is False
    art = _clean_deliver()
    art["quality"]["tests_pass"] = False
    assert pv.push_ready_upper_bound(art, "full", test_evidence=green)[0] is False


def test_mode_criterion_unmet_when_a_newer_report_has_a_regression(tmp_path):
    _write_evidence(tmp_path, "tests/latest-regression.json", _c2("regression", "r", [
        {"nodeid": "t.py::a", "outcome": "passed", "file": "t.py"}]))
    _write_evidence(tmp_path, "tests/latest-verify.json", _c2("verify", "r", [
        {"nodeid": "t.py::a", "outcome": "failed", "file": "t.py"}],
        failures=[{"nodeid": "t.py::a", "classification": "regression", "summary": "x"}],
        finished="2026-01-01T05:00:00+00:00"))
    res = pv.judge_exit_criteria([{"id": "X1", "kind": "test", "ref": "mode:regression",
                                   "expect": "pass"}], tmp_path)
    assert res["met"] is False and "regression" in res["detail"]["X1"]


def _plan_and_build(root, reg, capsys, rid):
    rd = _make_run(root, rid, "direct", files_touched=["backend/a.py"])
    assert _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})[0] == 0
    _write_evidence(rd, "tests/latest-tdd.json", _c2("tdd", rid, [
        {"nodeid": "tests/test_x.py::test_a", "outcome": "passed", "file": "tests/test_x.py"}]))
    code, out = _complete(reg, capsys, rid, "build", _build_artifact(),
                          {"decisions": DECISION, "exit_evidence": {"X2": "ok checked"}})
    assert code == 0, out
    assert reg.get_pipeline_state(PROJECT) == "deliver"
    return rd


def test_replan_after_build_may_not_weaken_the_exit_contract(ws, capsys):
    """MED-3: dropping / changing a criterion after BUILD is refused, nothing written."""
    root, reg = ws
    rid = "run_replan"
    _plan_and_build(root, reg, capsys, rid)
    before = _snapshot(root)
    weak = _plan_artifact(exit_criteria=[{"id": "X1", "kind": "manual", "ref": "eyeballed",
                                          "expect": "done"}])
    code, out = _complete(reg, capsys, rid, "plan", weak, {"decisions": DECISION})
    assert code == 1 and any(e["code"] == "E_EXIT_CRITERIA" for e in out["errors"]), out
    assert _snapshot(root) == before
    added = _plan_artifact()
    added["exit_criteria"].append({"id": "X3", "kind": "manual", "ref": "log line", "expect": "done"})
    code, out = _complete(reg, capsys, rid, "plan", added, {"decisions": DECISION})
    assert code == 1 and any("reason" in e["path"] for e in out["errors"]), out


def test_replan_superset_is_allowed_and_manifest_never_moves_back(ws, capsys):
    root, reg = ws
    rid = "run_replan_ok"
    _plan_and_build(root, reg, capsys, rid)
    added = _plan_artifact()
    added["exit_criteria"].append({"id": "X3", "kind": "manual", "ref": "log line",
                                   "expect": "done", "reason": "found an edge in BUILD"})
    code, out = _complete(reg, capsys, rid, "plan", added, {"decisions": DECISION})
    assert code == 0, out
    assert out["advanced"] is False and reg.get_pipeline_state(PROJECT) == "deliver"
    assert any("never moves it backwards" in w for w in out["warnings"])


def test_legacy_run_update_caps_push_ready_to_the_bound(ws, capsys):
    """MED-4: run-update --stage-json may lower push_ready, never raise it."""
    root, reg = ws
    rid = "run_legacy_pr"
    _make_run(root, rid, "bugfix", stages=["evaluate", "think", "plan", "build", "review", "test"])
    sj = json.dumps({"stage": "deliver", "status": "completed", "stage_doc_consumed": True,
                     "push_ready": True})
    code, _ = _call(cli.cmd_run_update, reg, capsys, **vars(_run_update_args(rid, stage_json=sj)))
    assert code == 0
    rec = cli._stage_record_of(_run_state(root, rid), "deliver")
    assert rec["push_ready"] is False and rec["push_ready_capped"] is True
    assert rec["push_ready_reason"].startswith("capped by run-update")
    # a bound that holds leaves the claimed True alone
    rid2 = "run_legacy_ok"
    _make_run(root, rid2, "bugfix", stages=["evaluate", "think", "plan", "build", "review", "test"])
    aid = reg.publish(project=PROJECT, artifact_type="delivery", data=_clean_deliver(),
                      producer="s_autonomous-pipeline", summary="d", run_id=rid2)
    sj2 = json.dumps({"stage": "deliver", "status": "completed", "stage_doc_consumed": True,
                      "push_ready": True, "artifact_id": aid})
    code, _ = _call(cli.cmd_run_update, reg, capsys, **vars(_run_update_args(rid2, stage_json=sj2)))
    assert code == 0
    assert cli._stage_record_of(_run_state(root, rid2), "deliver")["push_ready"] is True


def test_direct_deliver_without_exit_contract_is_unmet(ws, capsys):
    """MED-5: met is None at a direct DELIVER caps the bound."""
    root, reg = ws
    _make_run(root, "run_nocontract", "direct", stages=["plan", "build"])
    code, out = _complete(reg, capsys, "run_nocontract", "deliver", _clean_deliver(), {}, check=True)
    assert code == 0, out
    pr = out["derived"]["push_ready"]
    assert pr["upper_bound"] is False and any("no exit contract" in r for r in pr["reasons"])


def test_write_failure_is_structured_and_leaves_the_old_record(ws, capsys, monkeypatch):
    """MED-6: a run.json write failure → one write_failed line; the completed plan
    record is NOT re-pointed at the new artifact (no auto-record stub)."""
    root, reg = ws
    rid = "run_wfail"
    _make_run(root, rid, "direct")
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0
    old = cli._stage_record_of(_run_state(root, rid), "plan")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(cli, "_write_run_atomic", boom)
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 1 and out["ok"] is False and out["code"] == "write_failed"
    assert out["partial"]["artifact_id"] and "idempotent" in out["hint"]
    assert cli._stage_record_of(_run_state(root, rid), "plan") == old


@pytest.mark.parametrize("record,path", [
    ({"decisions": DECISION, "token_cost": float("inf")}, "record.token_cost"),
    ({"decisions": DECISION, "extra": {"n": float("nan")}}, "record.extra.n"),
])
def test_non_finite_record_numbers_rejected(ws, capsys, record, path):
    root, reg = ws
    _make_run(root, "run_inf", "direct")
    before = _snapshot(root)
    code, out = _complete(reg, capsys, "run_inf", "plan", _plan_artifact(), record)
    assert code == 1 and any(e["path"] == path for e in out["errors"]), out["errors"]
    assert _snapshot(root) == before
