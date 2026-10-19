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
- DELIVER stage-complete generates a missing REPORT.md in-process (auto flag),
  then advances (docs → reflect) or leaves COMPLETE needing no run-report call
  (direct); a hand-written report is kept; a failed generation does not advance.
- goal_cycle is an advanceable state (PIPELINE_STATES derived from the profiles):
  a goal PLAN stage-complete moves the manifest to goal_cycle.
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
    """A tmp workspace with one project; returns (root, registry). The CLI session id
    the test process inherits is cleared, so stage commands never record — or measure
    tokens from — the transcript of the session running the tests."""
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
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
    """Every file under .artifacts; run.json without ``stage_complete_failures`` (the
    refused-attempt counter is the one write a refused stage-complete makes)."""
    base = root / "Projects" / PROJECT / ".artifacts"
    out = {}
    for p in base.rglob("*"):
        if not p.is_file() or p.name == ".lock":  # the run lock file the counter took
            continue
        data = p.read_bytes()
        if p.name == "run.json":
            d = json.loads(data)
            d.pop("stage_complete_failures", None)
            data = json.dumps(d, sort_keys=True).encode()
        out[str(p.relative_to(base))] = data
    return out


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

        # the ONE full-scope run, at DELIVER after the last change: push_ready needs it
        _write_evidence(rd, "tests/latest-regression.json", _c2("regression", rid, [
            {"nodeid": "tests/test_x.py::test_a", "outcome": "passed", "file": "tests/test_x.py"}]))
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
        # DELIVER is direct's last stage: stage-complete itself generates REPORT.md
        # (run-report code path, auto flag) so COMPLETE needs no extra run-report call.
        report = rd / "REPORT.md"
        assert out["report_generated"] is True and report.is_file()
        assert report.stat().st_size >= 500  # the run-update completion gate's floor
        assert _run_state(root, rid)["report_autogenerated"] is True
        assert "run-report" not in out["next_action"]

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


def _specialist_high(sev="HIGH", who="security", resolved=True):
    return {"severity": sev, "confidence": 9, "resolved": resolved, "specialist": who,
            "finding": "backend/a.py run() line 7: shell=True on input. Fixed: list argv."}


def _case_red_team_missing(root):
    # Gate-2 HIGH half: a specialist HIGH (even RESOLVED) without the Red Team.
    _make_run(root, "run_gate7", "direct", stages=["plan", "build"], created_at=_RT_POST)
    art = _tmpl("deliver")
    art["adversarial_review"]["findings"] = [_specialist_high()]
    return ("run_gate7", "deliver", art, {}, "E_RED_TEAM_REQUIRED",
            "adversarial_review.red_team_dispatched")


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


def _assert_degraded(root, rid, stage, needle):
    """A gate error completed the stage DEGRADED on the first try (SOUL P5: one landing
    decision): the record names the error, a degraded DELIVER is not push-ready."""
    rec = next(r for r in _run_state(root, rid)["stages"] if r.get("stage") == stage)
    errs = (rec.get("degraded") or {}).get("errors") or []
    assert any(needle in e for e in errs), rec
    if stage == "deliver":
        assert rec.get("push_ready") is False and "open gate error" in rec["push_ready_reason"]


@pytest.mark.parametrize("case", [
    _case_missing_field, _case_invalid_litmus, _case_tier_skipped,
    _case_exit_contract_missing, _case_red_team_missing,
], ids=lambda f: f.__name__)
def test_gate_error_completes_degraded_first_try(ws, capsys, case):
    """--check still reports the error with its code; the real call completes the stage
    degraded on the FIRST refusal (no 3-strike wait) and names the error on the record."""
    root, reg = ws
    rid, stage, art, rec, code_, path = case(root)
    code, out = _complete(reg, capsys, rid, stage, art, rec, check=True)
    assert code == 1 and any(e["code"] == code_ and e["path"] == path for e in out["errors"]), out
    code, out = _complete(reg, capsys, rid, stage, art, rec)
    assert code in (0, None) and out["ok"] is True, out
    assert any(f"{stage} degraded:" in w for w in out["warnings"]), out["warnings"]
    rec_ = next(r for r in _run_state(root, rid)["stages"] if r.get("stage") == stage)
    assert rec_.get("degraded", {}).get("errors"), rec_


@pytest.mark.parametrize("case", [_case_stage_order, _case_bad_decision],
                         ids=lambda f: f.__name__)
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


def _green_ev():
    return _c2("regression", "r", [{"nodeid": "a::b", "outcome": "passed"}])


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
    ok, reasons = pv.push_ready_upper_bound(art, "full", test_evidence=_green_ev())
    assert ok is expect_ok, reasons


def test_push_ready_needs_runner_evidence_not_an_artifact_claim():
    """quality.tests_pass / test.passed are model-written: a code profile ships only on a
    runner report (quality audit r2)."""
    art = _clean_deliver()
    art["quality"]["tests_pass"] = True
    ok, reasons = pv.push_ready_upper_bound(art, "full")
    assert ok is False and any("no runner evidence" in r for r in reasons)
    assert pv.push_ready_upper_bound(art, "docs")[0] is True  # no code, no runner


def test_push_ready_bound_exit_criteria_strict_only():
    unmet = {"met": False, "unmet": ["X1"]}
    assert pv.push_ready_upper_bound(_clean_deliver(), "direct", exit_result=unmet,
                                     test_evidence=_green_ev())[0] is False
    assert pv.push_ready_upper_bound(_clean_deliver(), "trivial", exit_result=unmet,
                                     test_evidence=_green_ev())[0] is True
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


# ── Gate-2 Red Team trigger, HIGH half (validator-enforced) ────────────────
_RT = "red_team_dispatched"
_RT_POST = "2026-10-19T00:00:00+00:00"   # after RED_TEAM_CUTOVER → enforced
_RT_PRE = "2026-10-13T01:38:37+00:00"    # before it → grandfathered (warning only)


def _rt_markers(root, monkeypatch, run_id, expert_ids, *, readable=True):
    """Point the marker store at <root>/data and write one SubagentStop marker per
    expert id for run_id. readable=False → the store dir does not exist."""
    data = root / "data"
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    if not readable:
        return
    audit = data / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True, exist_ok=True)
    for i, eid in enumerate(expert_ids):
        (audit / f"session_s_adv_{i}.marker").write_text(json.dumps(
            {"ts": i, "event": "SubagentStop", "run_id": run_id, "expert_id": eid}))


@pytest.mark.parametrize("profile,findings,extra,blocked", [
    ("direct", [_specialist_high()], {_RT: True}, False),           # red team ran
    ("direct", [_specialist_high()], {}, True),                     # ran nowhere
    ("direct", [_specialist_high()], {_RT: False}, True),
    ("direct", [_specialist_high()], {_RT: 1}, False),               # a count
    ("direct", [_specialist_high("CRITICAL", "correctness")], {}, True),
    ("direct", [_specialist_high("high", "Correctness")], {}, True),  # case-normalized
    ("direct", [{**_specialist_high(), "specialist": ""}], {}, True),  # unattributed
    ("direct", [{"severity": "MEDIUM", "confidence": 8, "resolved": True,
                 "specialist": "correctness", "finding": "a.py x() line 2: y. Fixed: z."}],
     {}, False),                                                      # MED only
    ("direct", [_specialist_high(who="red-team")], {}, False),       # own HIGH
    ("direct", [_specialist_high(who="Red Team")], {}, False),
    ("direct", [_specialist_high(), _specialist_high("LOW", "red-team")], {}, False),
    ("direct", [_specialist_high(who="correctness + red-team")], {}, False),
    ("trivial", [_specialist_high()], {}, False),                    # relaxed profile
])
def test_red_team_required_after_specialist_high(ws, capsys, monkeypatch, profile, findings,
                                                 extra, blocked):
    root, reg = ws
    rid = "run_rt"
    stages = PIPELINE_PROFILES[profile]
    prior = stages[:stages.index("deliver")]
    _make_run(root, rid, profile, stages=prior, created_at=_RT_POST)
    _rt_markers(root, monkeypatch, rid, ["red-team"])   # the Red Team really ran
    art = _clean_deliver()
    art["adversarial_review"]["findings"] = findings
    art["adversarial_review"].pop(_RT, None)
    art["adversarial_review"].update(extra)
    code, out = _complete(reg, capsys, rid, "deliver", art, {})
    hits = [e for e in out.get("errors", []) if e["code"] == "E_RED_TEAM_REQUIRED"]
    if blocked:
        assert code in (0, None), out   # a gate error degrades the stage, never stops it
        _assert_degraded(root, rid, "deliver", "Red Team required")
    else:
        assert not hits, hits
        assert code == 0, out


def _rt_publish(reg, capsys, art, run_id):
    args = SimpleNamespace(project=PROJECT, type="delivery", data=json.dumps(art),
                           producer="s_autonomous-pipeline", summary="s", topic="",
                           stage="deliver", run_id=run_id, quiet=True)
    code = 0
    try:
        cli.cmd_publish(args, reg)
    except SystemExit as e:
        code = e.code
    cap = capsys.readouterr()
    return code, [json.loads(ln) for ln in cap.err.splitlines() if ln.strip().startswith("{")]


def test_red_team_required_on_legacy_publish(ws, capsys):
    root, reg = ws
    art = _clean_deliver()
    art["adversarial_review"]["findings"] = [_specialist_high()]
    # Enforced run → the publish is refused with the structured code.
    _make_run(root, "run_pub_new", "direct", stages=["plan", "build"], created_at=_RT_POST)
    code, err = _rt_publish(reg, capsys, art, "run_pub_new")
    assert code == 1 and err[-1]["validation_failed"] is True
    assert {"code": "E_RED_TEAM_REQUIRED", "path": "adversarial_review.red_team_dispatched"} \
        in err[-1]["structured_errors"], err
    # A run created before the cutover → warning only, the publish goes through.
    _make_run(root, "run_pub_old", "direct", stages=["plan", "build"], created_at=_RT_PRE)
    code, err = _rt_publish(reg, capsys, art, "run_pub_old")
    assert code == 0 and not any(e.get("validation_failed") for e in err)
    assert any("Red Team required" in e.get("warning", "") for e in err), err
    assert "required_experts" not in _run_state(root, "run_pub_old")
    # No run at all (legacy publish) → warning only; it then fails closed on the
    # missing --run-id (exit 3), never on the Red Team rule.
    code, err = _rt_publish(reg, capsys, art, None)
    assert code == 3 and not any(e.get("validation_failed") for e in err)
    assert any("Red Team required" in e.get("warning", "") for e in err), err


def _rt_claimed_deliver():
    art = _clean_deliver()
    art["adversarial_review"]["findings"] = [_specialist_high()]
    art["adversarial_review"][_RT] = True          # the claim the marker must back
    return art


def test_red_team_claim_without_marker_is_refused(ws, capsys, monkeypatch):
    root, reg = ws
    _make_run(root, "run_rt_nomark", "direct", stages=["plan", "build"], created_at=_RT_POST)
    _rt_markers(root, monkeypatch, "run_rt_nomark", ["correctness", "security"])
    _rt_markers(root, monkeypatch, "run_sibling", ["red-team"])  # another run's marker
    code, out = _complete(reg, capsys, "run_rt_nomark", "deliver", _rt_claimed_deliver(), {},
                          check=True)
    hits = [e for e in out["errors"] if e["code"] == "E_RED_TEAM_REQUIRED"]
    assert code == 1 and hits and "EXPERT-ID marker" in hits[0]["message"], out
    code, out = _complete(reg, capsys, "run_rt_nomark", "deliver", _rt_claimed_deliver(), {})
    assert code in (0, None), out
    _assert_degraded(root, "run_rt_nomark", "deliver", "EXPERT-ID marker")


def test_red_team_marker_passes_and_is_declared_for_run_commit(ws, capsys, monkeypatch):
    root, reg = ws
    _make_run(root, "run_rt_ok", "direct", stages=["plan", "build"], created_at=_RT_POST,
              required_experts=["correctness"])
    _rt_markers(root, monkeypatch, "run_rt_ok", ["correctness", "red-team"])
    code, out = _complete(reg, capsys, "run_rt_ok", "deliver", _rt_claimed_deliver(), {})
    assert code == 0, out
    assert _run_state(root, "run_rt_ok")["required_experts"] == ["correctness", "red-team"]


def test_red_team_marker_store_unreadable_fails_open(ws, capsys, monkeypatch):
    root, reg = ws
    _make_run(root, "run_rt_open", "direct", stages=["plan", "build"], created_at=_RT_POST)
    _rt_markers(root, monkeypatch, "run_rt_open", [], readable=False)
    code, out = _complete(reg, capsys, "run_rt_open", "deliver", _rt_claimed_deliver(), {})
    assert code == 0, out
    assert any("Red Team marker NOT verified" in w for w in out["warnings"]), out
    assert "red-team" in _run_state(root, "run_rt_open")["required_experts"]


def test_red_team_grandfathered_run_warns_and_completes(ws, capsys, monkeypatch):
    root, reg = ws
    _make_run(root, "run_rt_old", "direct", stages=["plan", "build"], created_at=_RT_PRE)
    _rt_markers(root, monkeypatch, "run_rt_old", [])
    art = _clean_deliver()
    art["adversarial_review"]["findings"] = [_specialist_high()]   # no claim, no marker
    art["adversarial_review"].pop(_RT, None)
    code, out = _complete(reg, capsys, "run_rt_old", "deliver", art, {})
    assert code == 0, out
    assert any("Red Team required" in w and "grandfathered" in w for w in out["warnings"])
    assert "required_experts" not in _run_state(root, "run_rt_old")


def test_red_team_legacy_run_update_declares_red_team(ws, capsys, monkeypatch):
    root, reg = ws
    _make_run(root, "run_rt_leg", "direct", stages=["plan", "build"], created_at=_RT_POST)
    aid = reg.publish(project=PROJECT, artifact_type="delivery", data=_rt_claimed_deliver(),
                      producer="s_autonomous-pipeline", summary="s", run_id="run_rt_leg")
    rec = {"stage": "deliver", "status": "completed", "artifact_id": aid,
           "stage_doc_consumed": True}
    cli.cmd_run_update(_run_update_args("run_rt_leg", stage_json=json.dumps(rec)), reg)
    assert _run_state(root, "run_rt_leg")["required_experts"] == ["red-team"]
    cli.cmd_run_update(_run_update_args("run_rt_leg", stage_json=json.dumps(rec)), reg)
    assert _run_state(root, "run_rt_leg")["required_experts"] == ["red-team"]  # idempotent


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
    _write_evidence(_run_dir(root, "run_lower"), "tests/latest-regression.json",
                    _c2("regression", "run_lower", [{"nodeid": "a::b", "outcome": "passed"}]))
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
        # no `mode:verify`: the one full-scope run at DELIVER is required by the
        # push_ready bound for every code change, not per contract (XG 2026-10-20)
        assert len(feat) == 2 and not any(c["ref"].startswith("mode:") for c in feat)
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
    assert out["diff_insertions"] == 130
    assert "full_tier_override" not in out  # risk, not a line count, picks the tier
    ids = {e["id"] for e in out["experts"]}
    assert {"correctness", "security", "api-contract", "integration", "concurrency"} <= ids
    assert "red-team" not in ids  # 130 lines ≤ 200
    for e in out["experts"]:
        if "prompt_path" in e:
            body = Path(e["prompt_path"]).read_text(encoding="utf-8")
            assert body.startswith(f"EXPERT-ID: {e['id']}")
            # The output format names the real producer (deliver's Red Team gate reads it).
            assert f'"specialist":"{e["id"]}"' in body and '"specialist":"<name>"' not in body
            assert Path(e["prompt_path"]).parent == rd / "evidence" / "prompts"
    assert all("prompt_path" in e for e in out["experts"]), out["warnings"]
    assert "You are a specialist code reviewer" not in json.dumps(out), \
        "prompt bodies are returned as paths, never inline"
    assert len(json.dumps(out, ensure_ascii=False).encode()) <= 8 * 1024
    assert _run_state(root, rid)["stage_timing"]["deliver"]["started_at"]


@pytest.mark.parametrize("profile,work_type,ins,dels,required,expect", [
    ("full", None, 10, 5, [], set()),                               # < 50 lines → none
    ("full", None, 10, 5, ["security"], set()),                     # ≤ 20 lines: small, even if required
    ("full", None, 25, 5, ["security"], {"security"}),              # 21-49 lines: only the required
    ("bugfix", None, 60, 0, [], {"correctness", "security"}),       # bugfix default
    ("bugfix", "refactor", 60, 0, [],
     {"correctness", "api-contract", "integration", "red-team"}),   # refactor rule
    ("full", None, 150, 100, [], {"correctness", "security", "red-team"}),  # > 200 lines; strict → security
    ("direct", None, 150, 0, [], {"correctness", "security"}),     # no line-count force to full
    ("trivial", None, 150, 0, [], set()),                          # relaxed: no >100 override
])
def test_dispatch_rules(profile, work_type, ins, dels, required, expect):
    scope = {"changed_files": [{"path": "backend/x.py", "status": "M", "added": ins, "removed": dels}],
             "diff_insertions": ins, "diff_deletions": dels}
    got = {e["id"] for e in cli._dispatch_experts(profile=profile, work_type=work_type,
                                                    scope=scope, required=required)}
    assert got == expect


@pytest.mark.parametrize("required,expect", [([], set()), (["security"], {"security"})])
def test_docs_only_change_dispatches_no_expert(required, expect):
    # 400 lines of prose: no code runs, so no Gate-2 expert (unless run.json requires one)
    scope = {"changed_files": [{"path": "backend/context/SOUL.md", "status": "M", "added": 400,
                                "removed": 0}, {"path": "docs/a.md", "status": "A", "added": 50,
                                                "removed": 0}],
             "diff_insertions": 450, "diff_deletions": 0}
    assert cli._is_docs_only(scope)
    got = {e["id"] for e in cli._dispatch_experts(profile="direct", work_type=None,
                                                    scope=scope, required=required)}
    assert got == expect
    scope["changed_files"].append({"path": "backend/x.py", "status": "M", "added": 1, "removed": 0})
    assert not cli._is_docs_only(scope)
    assert not cli._is_docs_only({"changed_files": []})


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
    """MED-3: dropping / changing a criterion after BUILD is reported (--check) and, on a
    real call, recorded as a degraded PLAN (push_ready false), never a silent pass."""
    root, reg = ws
    rid = "run_replan"
    _plan_and_build(root, reg, capsys, rid)
    weak = _plan_artifact(exit_criteria=[{"id": "X1", "kind": "manual", "ref": "eyeballed",
                                          "expect": "done"}])
    code, out = _complete(reg, capsys, rid, "plan", weak, {"decisions": DECISION}, check=True)
    assert code == 1 and any(e["code"] == "E_EXIT_CRITERIA" for e in out["errors"]), out
    added = _plan_artifact()
    added["exit_criteria"].append({"id": "X3", "kind": "manual", "ref": "log line", "expect": "done"})
    code, out = _complete(reg, capsys, rid, "plan", added, {"decisions": DECISION}, check=True)
    assert code == 1 and any("reason" in e["path"] for e in out["errors"]), out
    code, out = _complete(reg, capsys, rid, "plan", weak, {"decisions": DECISION})
    assert code in (0, None), out
    _assert_degraded(root, rid, "plan", "replan")


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
    _write_evidence(_run_dir(root, rid2), "tests/latest-regression.json",
                    _c2("regression", rid2, [{"nodeid": "a::b", "outcome": "passed"}]))
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


# ── DELIVER generates REPORT.md itself; goal_cycle is an advanceable state ──

def test_pipeline_states_cover_every_profile_stage():
    from core.artifact_registry import PIPELINE_STATES
    for name, stages in PIPELINE_PROFILES.items():
        assert set(stages) <= set(PIPELINE_STATES), name
    assert PIPELINE_STATES[:8] == tuple(PIPELINE_PROFILES["full"])  # full's order kept


def test_goal_plan_advances_manifest_to_goal_cycle(ws, capsys):
    root, reg = ws
    rid = "run_goal_adv"
    _make_run(root, rid, "goal", stages=["evaluate", "think"])
    reg.advance_pipeline(PROJECT, "plan")
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    assert out["next_stage"] == "goal_cycle" and out["advanced"] is True, out
    assert reg.get_pipeline_state(PROJECT) == "goal_cycle"
    code, out = _start(reg, capsys, rid, "goal_cycle")
    assert code == 0 and out["ok"] is True and out["card"].endswith("goal_cycle.md")


def test_deliver_generates_report_then_advances_to_reflect(ws, capsys):
    """docs profile (… → deliver → reflect): no REPORT.md before DELIVER's
    stage-complete; it is generated in-process and the manifest advances."""
    root, reg = ws
    rid = "run_docs_rep"
    rd = _make_run(root, rid, "docs", stages=["evaluate", "think", "plan"])
    reg.advance_pipeline(PROJECT, "deliver")
    assert not (rd / "REPORT.md").exists()
    code, out = _complete(reg, capsys, rid, "deliver", _clean_deliver(), {})
    assert code == 0, out
    assert out["report_generated"] is True and (rd / "REPORT.md").is_file()
    assert out["next_stage"] == "reflect" and out["advanced"] is True, out
    assert reg.get_pipeline_state(PROJECT) == "reflect"
    assert "REPORT.md gate" not in out["next_action"]


def test_deliver_keeps_a_hand_written_report(ws, capsys):
    root, reg = ws
    rid = "run_docs_hand"
    rd = _make_run(root, rid, "docs", stages=["evaluate", "think", "plan"])
    (rd / "REPORT.md").write_text("hand-written report", encoding="utf-8")
    code, out = _complete(reg, capsys, rid, "deliver", _clean_deliver(), {})
    assert code == 0, out
    assert out["report_generated"] is False and out["advanced"] is True
    assert (rd / "REPORT.md").read_text(encoding="utf-8") == "hand-written report"


def test_deliver_report_failure_does_not_advance(ws, capsys, monkeypatch):
    """The gate still holds: no REPORT.md → advanced:false with the manual fix."""
    root, reg = ws
    rid = "run_docs_fail"
    rd = _make_run(root, rid, "docs", stages=["evaluate", "think", "plan"])
    reg.advance_pipeline(PROJECT, "deliver")

    def boom(*a, **k):
        raise RuntimeError("render crashed")

    monkeypatch.setattr(cli, "cmd_run_report", boom)
    code, out = _complete(reg, capsys, rid, "deliver", _clean_deliver(), {})
    assert code == 0, out
    assert out["advanced"] is False and out["report_generated"] is False
    assert not (rd / "REPORT.md").exists()
    assert reg.get_pipeline_state(PROJECT) == "deliver"
    assert "REPORT.md gate" in out["next_action"]


# ── dispatch scope norms: source-only size, one security owner, review list ──

def _scope(files):
    """files = [(path, status, added, removed)] → a scope dict shaped like
    _changed_files_scope's output (is_test + source counts derived the same way)."""
    changed = [{"path": p, "status": s, "added": a, "removed": r,
                "is_test": cli._is_test_path(p)} for p, s, a, r in files]
    src = [c for c in changed if not c["is_test"]]
    ins = sum(c["added"] for c in changed)
    s_ins = sum(c["added"] for c in src)
    return {"changed_files": changed, "diff_insertions": ins,
            "diff_deletions": sum(c["removed"] for c in changed),
            "source_insertions": s_ins, "source_deletions": sum(c["removed"] for c in src)}


@pytest.mark.parametrize("path,is_test", [
    ("backend/tests/test_x.py", True), ("backend/core/test_helpers_impl.py", True),
    ("pkg/thing_test.py", True), ("desktop/src/a.test.ts", True), ("desktop/src/B.spec.tsx", True),
    ("desktop/src/__tests__/c.tsx", True), ("backend/core/attest.py", False),
    ("backend/core/contest_rules.py", False), ("backend/scripts/artifact_cli.py", False),
])
def test_is_test_path(path, is_test):
    assert cli._is_test_path(path) is is_test


def test_size_escalation_counts_source_lines_only():
    # 60 source + 300 test lines on a bugfix: the old count (360) forced full tier + red-team.
    scope = _scope([("backend/core/gate.py", "M", 60, 0),
                    ("backend/tests/test_security_gate.py", "A", 300, 0)])
    got = {e["id"] for e in cli._dispatch_experts(profile="bugfix", work_type="bugfix",
                                                  scope=scope, required=[])}
    assert got == {"correctness", "security"}
    # a test path must not fire a file trigger either (test_security_gate.py matches 'security')
    scope2 = _scope([("backend/core/x.py", "M", 120, 0),
                     ("backend/tests/test_routers_auth.py", "A", 50, 0)])
    got2 = {e["id"] for e in cli._dispatch_experts(profile="full", work_type=None,
                                                   scope=scope2, required=[])}
    assert "api-contract" not in got2 and "performance" not in got2


def test_large_source_change_still_gets_full_tier_and_red_team():
    scope = _scope([("backend/core/gate.py", "M", 260, 10)])
    got = {e["id"] for e in cli._dispatch_experts(profile="bugfix", work_type="bugfix",
                                                  scope=scope, required=[])}
    assert {"correctness", "security", "red-team"} <= got


def test_strict_profile_gate2_always_carries_security():
    # core/x.py matches no security file trigger; before, Gate-2 had no security here
    scope = _scope([("backend/core/x.py", "M", 120, 0)])
    gate2 = {e["id"] for e in cli._dispatch_experts(profile="full", work_type=None,
                                                    scope=scope, required=[])}
    assert "security" in gate2
    review = {e["id"] for e in cli._review_dispatch(profile="full", scope=scope, gate2_ids=gate2)}
    assert "security" not in review  # one owner per run


def test_small_auth_fix_keeps_security_at_review():
    # 20 lines: Gate-2 dispatches nothing, REVIEW fan-out fires on the auth path → REVIEW owns it
    scope = _scope([("backend/routers/auth.py", "M", 20, 0)])
    gate2 = {e["id"] for e in cli._dispatch_experts(profile="bugfix", work_type="bugfix",
                                                    scope=scope, required=[])}
    assert gate2 == set()
    review = {e["id"] for e in cli._review_dispatch(profile="bugfix", scope=scope, gate2_ids=gate2)}
    assert {"spec-compliance", "code-quality", "security"} <= review


def test_review_dispatch_single_pass_when_fanout_does_not_apply():
    scope = _scope([("backend/core/util.py", "M", 30, 0)])
    review = cli._review_dispatch(profile="full", scope=scope, gate2_ids=set())
    assert [e["id"] for e in review] == ["spec-compliance"]


def test_review_dispatch_ux_only_for_frontend_source():
    fe = _scope([("desktop/src/pages/X.tsx", "M", 120, 0)])
    assert "ux-test" in {e["id"] for e in cli._review_dispatch(profile="full", scope=fe, gate2_ids={"security"})}
    fe_test_only = _scope([("backend/core/a.py", "M", 120, 0), ("desktop/src/X.test.tsx", "M", 40, 0)])
    assert "ux-test" not in {e["id"] for e in cli._review_dispatch(
        profile="full", scope=fe_test_only, gate2_ids={"security"})}


def test_stage_start_review_returns_review_list_not_gate2(ws, capsys):
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "core" / "a.py").write_text("x = 1\n" + "y = 2\n" * 120, encoding="utf-8")
    rid = "run_rev"
    rd = _make_run(root, rid, "full", stages=["evaluate", "think", "plan", "build"],
                   files_touched=[str(repo / "core" / "a.py")])
    code, out = _start(reg, capsys, rid, "review")
    assert code == 0, out
    assert "experts" not in out
    ids = [e["id"] for e in out["review_experts"]]
    assert ids[:2] == ["spec-compliance", "code-quality"] and "security" not in ids
    assert out["spawn_mode"] == "foreground"
    # REVIEW writes no Gate-2 prompt files, only its own reviewer inputs + one
    # review-<id>.md prompt per REVIEW sub-agent
    assert {p.name for p in (rd / "evidence" / "prompts").iterdir()} \
        <= {"review-knowledge.md", "review-change.diff"} | {f"review-{i}.md" for i in ids}
    assert all(e["prompt_path"].endswith(f"review-{e['id']}.md") for e in out["review_experts"])
    assert _run_state(root, rid)["dispatch_plan"]["review"] == ids


def test_stage_start_deliver_records_plan_and_foreground(ws, capsys):
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "core" / "a.py").write_text("x = 1\n" + "y = 2\n" * 120, encoding="utf-8")
    rid = "run_del"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan", "build", "review", "test"],
              files_touched=[str(repo / "core" / "a.py")])
    code, out = _start(reg, capsys, rid, "deliver")
    assert code == 0, out
    ids = [e["id"] for e in out["experts"]]
    assert "security" in ids and out["spawn_mode"] == "foreground"
    assert _run_state(root, rid)["dispatch_plan"]["deliver"] == ids + ["meta-review"]


def test_deliver_adds_security_when_no_stage_owned_it(ws, capsys):
    # REVIEW ran when Gate-2 would carry security (so REVIEW skipped it); fixes then shrank
    # the change under 50 lines → Gate-2 alone would dispatch nothing. Nobody reviewed security.
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "routers").mkdir()
    (repo / "routers" / "auth.py").write_text("z = 3\n" * 20, encoding="utf-8")
    rid = "run_drift"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan", "build", "review", "test"],
              files_touched=[str(repo / "routers" / "auth.py")],
              dispatch_plan={"review": ["spec-compliance", "code-quality"]})
    code, out = _start(reg, capsys, rid, "deliver")
    assert code == 0, out
    assert "security" in {e["id"] for e in out["experts"]}


def test_path_rules_match_repo_relative_not_absolute(tmp_path):
    """Path rules read the repo-relative path. Absolute paths carry the machine's
    directory names: a home folder called Desktop matched the frontend rule
    ``desktop/`` (REVIEW dispatched ux-test for a markdown expert file), and a
    checkout under a ``tests/`` folder would mark every file as a test."""
    import subprocess
    repo = tmp_path / "tests" / "Desktop" / "repo"
    (repo / "backend" / "core").mkdir(parents=True)
    src = repo / "backend" / "core" / "auth_store.py"
    src.write_text("x = 1\n")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
    subprocess.run(git + ["add", "."], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=repo, check=True)
    src.write_text("x = 1\n" + "".join(f"y{i} = {i}\n" for i in range(20)))
    doc = repo / "backend" / "experts.md"
    doc.write_text("note\n")

    scope = cli._changed_files_scope([str(src), str(doc)])
    by_rel = {c["rel"]: c for c in scope["changed_files"]}
    assert set(by_rel) == {"backend/core/auth_store.py", "backend/experts.md"}
    assert not any(c["is_test"] for c in scope["changed_files"])
    assert scope["source_insertions"] == scope["diff_insertions"] == 21

    ids = [e["id"] for e in cli._review_dispatch(profile="full", scope=scope, gate2_ids=set())]
    assert "ux-test" not in ids
    assert "code-quality" in ids  # auth_store.py is a sensitive path → fan-out applies


def test_pytest_named_source_outside_tests_dir_is_not_a_test(tmp_path):
    """``*_test.py`` / ``test_*.py`` outside a tests dir count as tests only if they
    define one: the test RUNNER backend/scripts/pipeline_test.py has none, and
    misreading it as a test would let its lines skip the size thresholds."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pipeline_test.py").write_text("def run():\n    pass\n")
    (tmp_path / "scripts" / "eval_gate_test.py").write_text("def test_gate():\n    assert 1\n")
    (tmp_path / "scripts" / "test_helpers_impl.py").write_text("class TestLike:\n    pass\n")
    root = str(tmp_path)
    assert cli._is_test_path("scripts/pipeline_test.py", root=root) is False
    assert cli._is_test_path("scripts/eval_gate_test.py", root=root) is True
    assert cli._is_test_path("scripts/test_helpers_impl.py", root=root) is True
    assert cli._is_test_path("scripts/gone_test.py", root=root) is True  # unreadable → by name
    assert cli._is_test_path("tests/simple_test.py", root=root) is True  # tests dir wins


def test_root_level_models_dir_still_fires_api_contract():
    """Triggers now match the repo-relative path, so a leading-slash pattern would
    miss a repo-root ``models/`` folder that the absolute path used to hit."""
    scope = _scope([("models/user.py", "M", 60, 0)])
    ids = {e["id"] for e in cli._dispatch_experts(profile="full", work_type="existing-feature",
                                                   scope=scope, required=None)}
    assert "api-contract" in ids


def test_stage_start_keeps_a_concurrent_run_json_write(ws, capsys, monkeypatch):
    """stage-start's git calls take seconds; a key another writer puts into run.json
    meanwhile must survive the dispatch_plan write, and the earlier stage's plan too."""
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "routers").mkdir()
    (repo / "routers" / "auth.py").write_text("z = 3\n" * 20, encoding="utf-8")
    rid = "run_race"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan", "build", "review", "test"],
              files_touched=[str(repo / "routers" / "auth.py")],
              dispatch_plan={"review": ["spec-compliance", "code-quality"]})
    real_scope = cli._changed_files_scope
    run_file = _run_dir(root, rid) / "run.json"

    def scope_with_writer(files):
        st = json.loads(run_file.read_text(encoding="utf-8"))
        st["written_meanwhile"] = True
        run_file.write_text(json.dumps(st), encoding="utf-8")
        return real_scope(files)

    monkeypatch.setattr(cli, "_changed_files_scope", scope_with_writer)
    code, out = _start(reg, capsys, rid, "deliver")
    assert code == 0, out
    st = json.loads(run_file.read_text(encoding="utf-8"))
    assert st.get("written_meanwhile") is True
    assert st["dispatch_plan"]["review"] == ["spec-compliance", "code-quality"]
    assert "security" in st["dispatch_plan"]["deliver"]


# ── H1 / H5: review rounds counted from completion markers, enforced at stage-complete ──

def test_completed_spawns_counts_per_expert_from_markers(tmp_path, monkeypatch):
    import config
    audit = tmp_path / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    monkeypatch.setattr(config, "get_app_data_dir", lambda: tmp_path)
    rows = [("s1_100", "run_a", 100.0, "security"), ("s1_101", "run_a", 101.0, "security"),
            ("s1_102", "run_a", 102.0, "gate1-skeptic"), ("s1_103", "run_a", 103.0, None),
            ("s1_104", "run_b", 104.0, "security"),          # other run
            ("s1_50", "run_a", 50.0, "security")]            # before the window
    for name, rid, ts, eid in rows:
        (audit / f"session_{name}.marker").write_text(json.dumps(
            {"ts": ts, "event": "SubagentStop", "run_id": rid, "expert_id": eid}))
    (audit / "session_s1_adv_101.marker").write_text(json.dumps(   # adversarial twin
        {"ts": 101.0, "run_id": "run_a", "expert_id": "security"}))
    (audit / "session_s1_105.marker").write_text("[]")  # malformed: skipped, not fatal
    (audit / "session_s1_106.marker").write_text(json.dumps(    # old daemon: no expert_id key
        {"ts": 106.0, "event": "SubagentStop", "run_id": "run_a"}))
    got = cli._completed_spawns("run_a", 90.0, 200.0)
    assert got == {"by_expert": {"gate1-skeptic": 1, "security": 2}, "unattributed": 1, "legacy": 1}
    monkeypatch.setattr(config, "get_app_data_dir", lambda: tmp_path / "missing")
    assert cli._completed_spawns("run_a", 90.0, 200.0) is None  # unreadable → None


def _budget_run(root, rid):
    _make_run(root, rid, "direct",
              stage_timing={"plan": {"started_at": "2026-01-01T00:00:00+00:00"}})


def test_stage_complete_refuses_a_fourth_round_without_a_reason(ws, capsys, monkeypatch):
    root, reg = ws
    _budget_run(root, "run_budget")
    monkeypatch.setattr(cli, "_completed_spawns", lambda rid, a, b: {
        "by_expert": {"gate1-skeptic": 4, "security": 3}, "unattributed": 1})
    code, out = _complete(reg, capsys, "run_budget", "plan", _plan_artifact(), {"decisions": DECISION},
                          check=True)
    assert code != 0
    msgs = json.dumps(out)
    assert "E_OVER_BUDGET" in msgs and "gate1-skeptic" in msgs and "'security'" not in msgs
    code, out = _complete(reg, capsys, "run_budget", "plan", _plan_artifact(), {"decisions": DECISION})
    assert code in (0, None), out   # an exhausted budget is a push_ready reason, not a stop
    _assert_degraded(root, "run_budget", "plan", "E_OVER_BUDGET")


def test_stage_complete_refuses_a_fourth_round_even_with_a_reason(ws, capsys, monkeypatch):
    """AC6 (再一再二不再三): over_budget_reasons no longer buys an extra round."""
    root, reg = ws
    _budget_run(root, "run_budget2")
    monkeypatch.setattr(cli, "_completed_spawns", lambda rid, a, b: {
        "by_expert": {"gate1-skeptic": 4}, "unattributed": 1})
    art = _plan_artifact()
    art["over_budget_reasons"] = {"gate1-skeptic": "second BLOCK on a different constraint"}
    code, out = _complete(reg, capsys, "run_budget2", "plan", art, {"decisions": DECISION},
                          check=True)
    assert code != 0
    msgs = json.dumps(out, ensure_ascii=False)
    assert "E_OVER_BUDGET" in msgs and "root-cause" in msgs
    b = cli._stage_budget("run_budget2", _run_dir(root, "run_budget2"),
                          _run_state(root, "run_budget2"), "plan", art)
    assert b["allowance_per_expert"] == 3 and b["over"] == {"gate1-skeptic": 4}
    assert b["missing"] == ["gate1-skeptic"] and b["reasons"] == {}


def test_stage_budget_without_stage_start_is_reported_not_enforced(tmp_path):
    b = cli._stage_budget("run_x", tmp_path, {}, "plan", {})
    assert b["missing"] == [] and b["spawns"] is None and b["window"].startswith("unknown")


def test_stage_budget_clamps_cycles_and_does_not_count_overlays(tmp_path, monkeypatch):
    import time as _t
    monkeypatch.setattr(cli, "_completed_spawns", lambda rid, a, b: {"by_expert": {}, "unattributed": 0})
    tdir = tmp_path / "evidence" / "tests"
    tdir.mkdir(parents=True)
    (tdir / "regression-1.json").write_text(json.dumps({"mode": "regression"}))
    (tdir / "recheck-1.json").write_text(json.dumps({"mode": "recheck"}))
    (tdir / "regression-2.json").write_text(json.dumps(
        {"mode": "regression", "rechecked_from": {"auto": True}}))      # an overlay write
    (tdir / "latest-regression.json").write_text("{}")
    first = "2026-01-01T00:00:00+00:00"
    state = {"stage_timing": {"goal_cycle": {"first_started_at": first,
                                             "started_at": "2099-01-01T00:00:00+00:00"}}}
    b = cli._stage_budget("r", tmp_path, state, "goal_cycle", {"cycles_run": 1e6})
    assert b["allowance_per_expert"] == 3 * 50
    assert b["test_runs"] == {"recheck": 1, "regression": 1} and b["auto_rechecks"] == 1
    # the window starts at the FIRST start: a later re-entry does not reset the allowance
    assert b["stage_seconds"] >= _t.time() - cli._iso_epoch(first) - 5
    b2 = cli._stage_budget("r", tmp_path, state, "goal_cycle", {"cycles_run": "inf"})
    assert b2["allowance_per_expert"] == 3


def test_deliver_does_not_add_security_for_a_run_without_a_recorded_review_plan(ws, capsys):
    """A run that predates dispatch_plan: whether REVIEW ran security is unknown, so the
    drift guard must not add a possibly duplicate security round."""
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "routers").mkdir()
    (repo / "routers" / "auth.py").write_text("z = 3\n" * 20, encoding="utf-8")
    rid = "run_noplan"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan", "build", "review", "test"],
              files_touched=[str(repo / "routers" / "auth.py")])
    code, out = _start(reg, capsys, rid, "deliver")
    assert code == 0, out
    assert "security" not in {e["id"] for e in out["experts"]}


def test_stage_complete_names_an_old_daemon_instead_of_blaming_prompts(ws, capsys, monkeypatch):
    root, reg = ws
    _budget_run(root, "run_legacy")
    monkeypatch.setattr(cli, "_completed_spawns", lambda rid, a, b: {
        "by_expert": {}, "unattributed": 0, "legacy": 3})
    code, out = _complete(reg, capsys, "run_legacy", "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    assert any("predates per-expert counting" in w for w in out.get("warnings") or []), out



# ── gate speed: evidence handed to the gate, not re-read by it ──

def test_diff_pack_has_context_new_files_and_names_what_does_not_fit(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / "r"
    (repo / "pkg").mkdir(parents=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    (repo / "pkg" / "a.py").write_text("".join(f"x{i} = {i}\n" for i in range(40)))
    subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
    subprocess.run(git + ["add", "."], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-qm", "i"], cwd=repo, check=True)
    (repo / "pkg" / "a.py").write_text("".join(f"x{i} = {i}\n" for i in range(40)).replace("x20 = 20", "x20 = 99"))
    (repo / "pkg" / "new.py").write_text("def f():\n    return 1\n")
    pack = cli._diff_pack([str(repo / "pkg" / "a.py"), str(repo / "pkg" / "new.py")])
    assert "+x20 = 99" in pack and " x10 = 10" in pack          # the hunk with context
    assert "+++ b/pkg/new.py (new file)" in pack and "+def f():" in pack
    monkeypatch.setattr(cli, "DIFF_PACK_MAX_LINES", 5)
    small = cli._diff_pack([str(repo / "pkg" / "a.py"), str(repo / "pkg" / "new.py")])
    assert small.splitlines()[0].startswith("# NOT INCLUDED") and "pkg/a.py" in small.splitlines()[0]


def test_gate1_prompt_carries_its_six_inputs(ws, capsys):
    root, reg = ws
    rid = "run_g1"
    rd = _make_run(root, rid, "full", stages=["evaluate", "think", "plan"])
    path, warns = cli._write_gate1_prompt(PROJECT, rd, _run_state(root, rid))
    assert path and not warns, warns
    text = open(path).read()
    assert text.splitlines()[0] == "EXPERT-ID: gate1-skeptic"
    for h in ("### 1. Plan", "### 2. Rejected alternatives", "### 3. TECH.md Blocking Constraints",
              "### 4. IMPROVEMENT.md What Failed", "### 5. EVALUATE verdict", "### 6. Work type"):
        assert h in text, h
    assert "Work type of this task: <work_type>" not in text   # first occurrence substituted


def test_stage_start_build_returns_the_assembled_gate1_prompt(ws, capsys):
    root, reg = ws
    rid = "run_g1b"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan"])
    code, out = _start(reg, capsys, rid, "build")
    assert code == 0, out
    assert out["gate1_prompt_path"].endswith("gate1-skeptic.md") and out["spawn_mode"] == "foreground"


def test_expert_prompts_point_at_one_diff_file(ws, capsys):
    """Gate-2 reviewers read one diff file (every hunk with context) instead of opening
    every changed file; the prompt itself stays small so the spawn is two lines."""
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "routers").mkdir()
    (repo / "routers" / "auth.py").write_text("z = 3\n" * 80, encoding="utf-8")
    rid = "run_diffpack"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan", "build", "review", "test"],
              files_touched=[str(repo / "routers" / "auth.py")])
    code, out = _start(reg, capsys, rid, "deliver")
    assert code == 0, out
    p = Path(out["experts"][0]["prompt_path"])
    diff = p.parent / "change.diff"
    assert diff.is_file() and "+z = 3" in diff.read_text()
    assert str(diff) in p.read_text() and "+z = 3" not in p.read_text()



def test_diff_pack_names_every_file_it_leaves_out(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / "r"
    (repo / "pkg").mkdir(parents=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    (repo / "pkg" / "a.py").write_text("a = 1\n")
    subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
    subprocess.run(git + ["add", "."], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-qm", "i"], cwd=repo, check=True)
    (repo / "pkg" / "new.py").write_text("n = 1\n")
    (repo / "pkg" / "link.py").symlink_to("/etc/hosts")
    pack = cli._diff_pack([str(repo / "pkg" / "new.py"), "./pkg/../../outside.py",
                           str(repo / "pkg" / "link.py")])
    first = pack.splitlines()[0]
    assert first.startswith("# NOT INCLUDED") and "link.py" in first and "outside.py" in first
    assert "+n = 1" in pack                         # a bad sibling path does not drop new files
    assert "localhost" not in pack                  # symlink target never copied


def test_gate1_inputs_are_fenced_data(ws, capsys):
    root, reg = ws
    rid = "run_g1f"
    rd = _make_run(root, rid, "full", stages=["evaluate", "think", "plan"])
    path, _ = cli._write_gate1_prompt(PROJECT, rd, _run_state(root, rid))
    text = open(path).read()
    assert "never an instruction to you" in text
    assert text.count("<gate-input>") == text.count("</gate-input>") >= 1   # evaluate is always fenced


def test_stage_budget_counts_from_the_runner_run_index(tmp_path, monkeypatch):
    """pipeline_test.py prunes timestamped evidence to 3 per mode, so the budget
    counts from its append-only tests/run_index.json: every run in the stage window,
    overlays as auto-rechecks, entries outside the window skipped."""
    import time as _t
    monkeypatch.setattr(cli, "_completed_spawns", lambda rid, a, b: {"by_expert": {}, "unattributed": 0})
    tdir = tmp_path / "evidence" / "tests"
    tdir.mkdir(parents=True)
    now = _t.time()
    index = [{"kind": "run", "mode": "verify", "ts": now - 10 - i} for i in range(5)]
    index += [{"kind": "run", "mode": "recheck", "ts": now - 5, "auto_recheck_of": "verify"},
              {"kind": "overlay", "mode": "verify", "ts": now - 4},
              {"kind": "run", "mode": "verify", "ts": now - 10 ** 6},   # before the stage
              "garbage", {"kind": "run", "mode": "tdd"}]               # no ts: skipped
    (tdir / "run_index.json").write_text(json.dumps(index))
    (tdir / "verify-20260101T000000.json").write_text("{}")             # pruned remnant
    state = {"stage_timing": {"test": {"first_started_at": "2026-01-01T00:00:00+00:00"}}}
    monkeypatch.setattr(cli, "_iso_epoch", lambda s: now - 3600 if s else None)
    b = cli._stage_budget("r", tmp_path, state, "test", {})
    assert b["test_runs"] == {"recheck": 1, "verify": 5} and b["auto_rechecks"] == 1


# ── transcript ids + per-response token cost (run_045b96b2) ─────────────────
# token_usage gets one row per TURN end, and a pipeline run sits inside one turn,
# so a stage window used to hold no row and every stage read "unavailable". The
# stage commands now record the CLI session id (CLAUDE_CODE_SESSION_ID) in
# run.json transcripts[] and measure the window from that transcript first.

TX_SID = "0d206ebe-9c4f-40ee-875a-491716e326b8"
TX_SID2 = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def cli_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", TX_SID)
    return tmp_path / "claude" / "projects" / "-ws"


def _no_db_usage(monkeypatch):
    mod = types.ModuleType("core.run_delivery_summary")
    mod.token_usage_for_window = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "core.run_delivery_summary", mod)


def test_stage_commands_record_the_transcript_id(ws, capsys, cli_cfg, monkeypatch):
    root, reg = ws
    rid = "run_txid"
    _make_run(root, rid, "direct")
    assert _start(reg, capsys, rid, "plan")[0] == 0
    assert _start(reg, capsys, rid, "plan")[0] == 0           # re-entry: no duplicate
    assert _run_state(root, rid)["transcripts"] == [TX_SID]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", TX_SID2)     # resumed in a new CLI session
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    assert _run_state(root, rid)["transcripts"] == [TX_SID, TX_SID2]
    assert _run_state(root, rid).get("session_id") is None   # never the app-session field


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "*", ""])
def test_malformed_session_id_is_not_recorded(ws, capsys, cli_cfg, monkeypatch, bad):
    root, reg = ws
    rid = "run_txbad"
    _make_run(root, rid, "direct")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", bad)
    assert _start(reg, capsys, rid, "plan")[0] == 0
    assert not _run_state(root, rid).get("transcripts")


def test_token_cost_measured_from_the_transcript(ws, capsys, cli_cfg, monkeypatch):
    root, reg = ws
    rid = "run_txcost"
    _make_run(root, rid, "direct", session_id="sess_1",
              stage_timing={"plan": {"started_at": "2026-01-01T00:00:00+00:00"}})
    cli_cfg.mkdir(parents=True)
    usage = {"input_tokens": 3, "output_tokens": 40, "cache_read_input_tokens": 5000,
             "cache_creation_input_tokens": 700}
    (cli_cfg / f"{TX_SID}.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"type": "assistant", "timestamp": "2025-12-31T23:59:00Z", "message": {"id": "m0", "usage": usage}},
        {"type": "assistant", "timestamp": "2026-01-01T00:05:00Z", "message": {"id": "m1", "usage": usage}},
        {"type": "assistant", "timestamp": "2026-01-01T00:05:01Z", "message": {"id": "m1", "usage": usage}},
    ]) + "\n", encoding="utf-8")
    _no_db_usage(monkeypatch)                                 # no turn row in the window
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    rec = cli._stage_record_of(_run_state(root, rid), "plan")
    assert rec["token_cost_source"] == "measured" and rec["token_cost"] == 3 + 40 + 700
    assert rec["token_usage"]["source"] == "transcript" and rec["token_usage"]["responses"] == 1


def test_token_cost_falls_back_without_a_transcript(ws, capsys, cli_cfg, monkeypatch):
    root, reg = ws
    rid = "run_txnone"
    _make_run(root, rid, "direct", session_id="sess_1",
              stage_timing={"plan": {"started_at": "2026-01-01T00:00:00+00:00"}})
    _no_db_usage(monkeypatch)                                 # transcript file absent too
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    assert cli._stage_record_of(_run_state(root, rid), "plan")["token_cost_source"] == "unavailable"


def test_report_tokens_label_for_transcript_usage():
    tx = {"source": "transcript", "tokens": 1234567, "cache_read_tokens": 9_000_000,
          "cost_usd": None}
    assert cli._report_tokens_label(tx) == "1,234,567 (measured per response, cache read 9,000,000)"
    settled = {**tx, "cost_usd": 12.5, "cost_excludes_current_turn": True}
    label = cli._report_tokens_label(settled)
    assert label.startswith("1,234,567 (measured per response, cache read 9,000,000; ")
    assert "$12.50 settled" in label and "excludes the turn in flight" in label
    assert "估算" not in label


def test_stage_start_reentry_from_a_new_session_keeps_concurrent_writes(ws, capsys, cli_cfg, monkeypatch):
    """A re-entry whose only change would be a new transcript id writes NOTHING, so a
    field another writer (e.g. a cancel) saves meanwhile survives; the id is recorded
    by stage-complete's own write instead."""
    root, reg = ws
    rid = "run_txreentry"
    _make_run(root, rid, "direct")
    assert _start(reg, capsys, rid, "plan")[0] == 0
    real_record = cli._record_transcript_id
    calls = []

    def _concurrent_write(state):          # 1st call: run.json already loaded, not yet written
        if not calls:
            st = _run_state(root, rid)
            st["status"] = "cancelled"
            (_run_dir(root, rid) / "run.json").write_text(json.dumps(st), encoding="utf-8")
        calls.append(1)
        return real_record(state)

    monkeypatch.setattr(cli, "_record_transcript_id", _concurrent_write)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", TX_SID2)
    assert _start(reg, capsys, rid, "plan")[0] == 0
    st = _run_state(root, rid)
    assert st["status"] == "cancelled"             # the concurrent cancel is not undone
    assert st["transcripts"] == [TX_SID]           # not written on re-entry
    # (stage-complete recording the new id: test_stage_commands_record_the_transcript_id)


# ── exit_evidence lists (run_a87bfe3f) ─────────────────────────────────────────
# stages/test.md describes fixes/unfixed per item, so agents write lists; the record
# schema wants strings. 4+ runs were rejected on exactly this shape. The parse step
# joins a list of strings into one string; an empty or all-blank list becomes '' so
# a manual exit criterion it names stays unmet (never a placeholder that looks like
# evidence).

class TestExitEvidenceLists:
    def test_list_values_are_joined(self):
        _, rec = cli._parse_stage_data(json.dumps({"record": {"exit_evidence": {
            "wtf": "0", "fixes": [], "unfixed": ["a.py::t: pre-existing", "b.py::u: flaky"]}}}))
        ev = rec["exit_evidence"]
        assert ev["fixes"] == ""
        assert ev["unfixed"] == "a.py::t: pre-existing; b.py::u: flaky"
        assert pv.validate_stage_record("test", rec)[0] == []

    def test_blank_items_are_dropped_before_joining(self):
        _, rec = cli._parse_stage_data(json.dumps({"record": {"exit_evidence": {"X6": ["", "  "]}}}))
        assert rec["exit_evidence"]["X6"] == ""

    def test_empty_list_leaves_a_manual_criterion_unmet(self, tmp_path):
        _, rec = cli._parse_stage_data(json.dumps({"record": {"exit_evidence": {"X6": []}}}))
        out = pv.judge_exit_criteria([{"id": "X6", "kind": "manual", "expect": "done"}], tmp_path, rec)
        assert out["met"] is False and out["unmet"] == ["X6"]

    def test_non_string_items_are_still_rejected(self):
        _, rec = cli._parse_stage_data(json.dumps({"record": {"exit_evidence": {"fixes": [1]}}}))
        errs, _ = pv.validate_stage_record("test", rec)
        assert any("exit_evidence" in e for e in errs)

    def test_test_template_shows_string_evidence(self):
        tmpl = pv.STAGE_RECORD_TEMPLATES["test"]["exit_evidence"]
        assert tmpl and all(isinstance(v, str) for v in tmpl.values())
        assert {"wtf", "fixes", "unfixed"} <= set(tmpl)


def test_untested_for_budget_flag_comes_from_the_newest_scope_report(tmp_path):
    """DELIVER's attention flag for what the runner did not test (transitive importers,
    import smoke past its cap, any skipped file) comes from the runner's evidence (the
    newest of regression / verify), never from the model."""
    t = tmp_path / "evidence" / "tests"
    t.mkdir(parents=True)

    def ev(mode, at, skipped, trans=()):
        (t / f"latest-{mode}.json").write_text(json.dumps(
            {"schema": 1, "run_id": "run_u", "mode": mode, "finished_at": at,
             "skipped_for_budget": skipped, "transitive_unrun": list(trans)}))

    assert cli._untested_for_budget_flag(tmp_path, "run_u") is None
    ev("verify", "2026-01-01T00:01:00Z", ["backend/tests/test_v.py"])
    ev("regression", "2026-01-01T00:02:00Z", ["import::core.x"], ["backend/tests/test_t.py"])
    flag = cli._untested_for_budget_flag(tmp_path, "run_u")
    assert flag.startswith(cli._RUNNER_FLAG_PREFIX + "Not tested in this run (regression)")
    assert "test_t.py" in flag and "core.x" in flag and "test_v.py" not in flag
    ev("regression", "2026-01-01T00:02:00Z", [])
    assert cli._untested_for_budget_flag(tmp_path, "run_u") is None


# ── one landing decision: a gate error degrades the stage on the FIRST try ──

def test_first_refusal_completes_the_stage_degraded(ws, capsys):
    root, reg = ws
    _make_run(root, "run_deg", "direct", stages=["plan", "build"])
    art = _tmpl("deliver")
    art["adversarial_review"]["profile_tier"] = "skipped"
    code, out = _complete(reg, capsys, "run_deg", "deliver", art, {})
    assert code in (0, None), out
    st = _run_state(root, "run_deg")
    rec = next(s for s in st["stages"] if s["stage"] == "deliver")
    assert rec["degraded"]["errors"] and "attempts" not in rec["degraded"]
    assert rec["push_ready"] is False and "open gate error" in rec["push_ready_reason"]
    assert "stage_complete_failures" not in st
    assert any("[degraded] deliver" in f for f in
               _artifact_on_disk(root, rec["artifact_id"]).get("attention_flags", []))


def test_input_and_order_errors_never_degrade(ws, capsys):
    root, reg = ws
    _make_run(root, "run_ord", "direct")  # plan never completed
    for _ in range(4):
        code, out = _complete(reg, capsys, "run_ord", "build", _build_artifact(),
                              {"decisions": DECISION})
        assert code == 1
    assert not any(s.get("stage") == "build" for s in _run_state(root, "run_ord")["stages"])


def test_a_model_cannot_write_degraded_itself(ws, capsys):
    assert "degraded" in cli._DERIVED_RECORD_KEYS


def test_an_unverified_gate_caps_push_ready():
    st = {"stages": [{"stage": "build", "gate1_unverified_reason": "gate_spawn_blocked twice"}]}
    assert cli._unverified_gates(st) == ["build:gate1"]
    assert cli._unverified_gates({"stages": [{"stage": "build",
                                              "gate1_unverified_reason": "short"}]}) == []
    assert cli._unverified_gates({"stages": []}, {"gate2_findings_unverified_reason":
                                                  "no audit hook here"}) == ["deliver:gate2_findings"]


def test_a_goal_final_gate_needs_a_completed_sub_agent(monkeypatch):
    monkeypatch.setattr(cli, "_spawns_in_windows",
                        lambda rid, w: {"by_expert": {}, "unattributed": 0})
    run = {"stage_timing": {"goal_cycle": {"started_at": "2026-01-01T00:00:00+00:00"}}}
    w: list = []
    errs = cli._final_gate_spawn_errors("r", run, {"adversarial_review": {"findings": []}}, {}, w)
    assert errs and errs[0].startswith("E_FINAL_GATE_UNBACKED")
    assert cli._final_gate_spawn_errors("r", run, {"dod_met": True}, {}, w) == []  # no claim
    monkeypatch.setattr(cli, "_spawns_in_windows",
                        lambda rid, w: {"by_expert": {"final-gate": 1}})
    assert cli._final_gate_spawn_errors("r", run, {"adversarial_review": True}, {}, w) == []
    monkeypatch.setattr(cli, "_spawns_in_windows", lambda rid, w: None)  # store unreadable
    assert cli._final_gate_spawn_errors("r", run, {"adversarial_review": True}, {}, w) == []


def test_a_verification_gap_is_latched_and_survives_a_legacy_rewrite(ws, capsys):
    """A gap recorded once (here: a gate passed on a recorded reason) caps push_ready
    through every path, including a legacy run-update that re-records the stage."""
    root, reg = ws
    rid = "run_latch"
    _make_run(root, rid, "bugfix", stages=["evaluate", "think", "plan", "build", "review", "test"],
              verification_gaps=[{"stage": "build", "why": "gate1 spawn NOT verified"}])
    aid = reg.publish(project=PROJECT, artifact_type="delivery", data=_clean_deliver(),
                      producer="s_autonomous-pipeline", summary="d", run_id=rid)
    _write_evidence(_run_dir(root, rid), "tests/latest-regression.json",
                    _c2("regression", rid, [{"nodeid": "a::b", "outcome": "passed"}]))
    sj = json.dumps({"stage": "deliver", "status": "completed", "stage_doc_consumed": True,
                     "push_ready": True, "artifact_id": aid})
    code, _ = _call(cli.cmd_run_update, reg, capsys, **vars(_run_update_args(rid, stage_json=sj)))
    assert code == 0
    rec = cli._stage_record_of(_run_state(root, rid), "deliver")
    assert rec["push_ready"] is False and "unverified" in rec["push_ready_reason"]


def test_gaps_come_from_the_gates_own_warnings():
    gaps = cli._verification_gaps_of("build", [
        "gate1 spawn NOT verified: the sub-agent marker store is unreadable",
        "refused stage-complete 1/3 for build", "some other note"], {})
    assert [g["why"][:5] for g in gaps] == ["gate1"]
    # a degraded stage is not latched (the bound reads its current record)
    assert cli._verification_gaps_of("test", [], {"degraded": {"errors": ["x"]}}) == []


def test_every_round_of_experts_are_checked_together(monkeypatch):
    """REVIEW / DELIVER experts run in parallel in EVERY round: round 1, and each
    gate2-recheck wave whatever its size. Spawns >30 s apart refuse."""
    t = 1_700_000_000.0  # in the past: the stage window ends now
    iso = __import__("datetime").datetime.fromtimestamp(t, __import__("datetime").timezone.utc).isoformat()
    run = {"dispatch_plan": {"deliver": ["correctness", "security", "red-team", "meta-review"],
                             "review": ["spec-compliance", "code-quality"]},
           "stage_timing": {"deliver": {"started_at": iso}, "review": {"started_at": iso}},
           "gate2_waves": [{"at": t + 300, "experts": ["correctness", "security"]},
                           {"at": t + 600, "experts": ["correctness"]}]}
    starts = {"correctness": [t + 10, t + 310, t + 610], "security": [t + 12, t + 312],
              "red-team": [t + 11], "meta-review": [t + 900],
              "spec-compliance": [t + 10], "code-quality": [t + 200]}
    monkeypatch.setattr(cli, "_spawn_starts", lambda rid, w: starts)
    w: list = []
    assert cli._parallel_dispatch_errors("r", run, "deliver", w) == []
    # recorded as a warning, never refused (the reviews already ran)
    assert cli._parallel_dispatch_errors("r", run, "review", w) == []
    assert any(x.startswith("serial dispatch") and "code-quality" in x for x in w)
    w.clear()
    starts["security"] = [t + 12, t + 400]  # round 2 re-checks spawned one after another
    assert cli._parallel_dispatch_errors("r", run, "deliver", w) == []
    assert any("re-check wave 1" in x and "security" in x for x in w)
    w.clear()
    monkeypatch.setattr(cli, "_spawn_starts", lambda rid, w: None)
    assert cli._parallel_dispatch_errors("r", run, "deliver", w) == []
    assert w and not any("NOT verified" in x for x in w)  # does not cap push_ready


# ── one landing decision (SOUL P5): tests decide landing; review gaps lower push_ready ──

def _lt(root, rid, reports=(), needs_report=True):
    """The landing-refusal reasons for a run holding *reports*."""
    import pipeline_validator as pv
    d = _make_run(root, rid, "direct")
    for rel, data in reports:
        _write_evidence(d, rel, data)
    return pv.landing_test_errors(d, rid, needs_report=needs_report)[0]


def test_landing_needs_a_runner_report_unless_the_diff_is_docs(ws):
    root, _ = ws
    errs = _lt(root, "run_lt0")
    assert len(errs) == 1 and "no runner report" in errs[0]
    assert _lt(root, "run_lt0d", needs_report=False) == []


def test_docs_only_paths_is_judged_on_the_diff():
    import pipeline_validator as pv
    assert pv.docs_only_paths(["backend/context/SOUL.md", "docs/a.md", "img/x.png"])
    assert not pv.docs_only_paths([])
    for code in ("backend/x.py", "backend/requirements.txt", "backend/experts/code/c.md",
                 "backend/skills/s_x/SKILL.md", ".claude/agents/a.md", "Makefile",
                 "desktop/icon.svg", "backend/tests/conftest.py"):
        assert not pv.docs_only_paths(["docs/a.md", code]), code


def test_is_docs_only_counts_tests_and_rename_sources():
    assert not cli._is_docs_only({"changed_files": [
        {"path": "backend/tests/test_a.py", "is_test": True}]})       # test code is code
    assert not cli._is_docs_only({"changed_files": [
        {"path": "docs/x.md", "from": "backend/x.py", "status": "R"}]})  # code renamed away
    assert cli._is_docs_only({"changed_files": [{"path": "docs/x.md", "status": "M"}]})


def test_landing_green_and_pre_existing_failures_land(ws):
    root, _ = ws
    ok = _c2("verify", "run_lt1", [{"nodeid": "a::b", "outcome": "passed"},
                                   {"nodeid": "a::c", "outcome": "failed"}],
             failures=[{"nodeid": "a::c", "classification": "pre-existing", "outcome": "failed"}])
    assert _lt(root, "run_lt1", reports=[("tests/latest-verify.json", ok)]) == []


def test_landing_refuses_red_unfinished_and_unreadable(ws):
    root, _ = ws
    red = _c2("verify", "run_lt2", [{"nodeid": "a::b", "outcome": "failed"}],
              failures=[{"nodeid": "a::b", "classification": "regression", "outcome": "failed"}])
    errs = _lt(root, "run_lt2", reports=[("tests/latest-verify.json", red)])
    assert len(errs) == 1 and "not red on HEAD" in errs[0] and "a::b" in errs[0]
    slow = _c2("verify", "run_lt3", [{"nodeid": "a::b", "outcome": "timeout"}],
               failures=[{"nodeid": "a::b", "classification": "timeout", "outcome": "timeout"}])
    errs = _lt(root, "run_lt3", reports=[("tests/latest-verify.json", slow)])
    assert len(errs) == 1 and "did not finish" in errs[0] and "continues" in errs[0]
    d = _make_run(root, "run_lt4", "direct")
    (d / "evidence" / "tests").mkdir(parents=True)
    (d / "evidence" / "tests" / "latest-verify.json").write_text("{not json")
    import pipeline_validator as pv
    errs, _soft = pv.landing_test_errors(d, "run_lt4")
    assert len(errs) == 1 and "cannot be read" in errs[0]


def test_a_newer_green_report_of_another_mode_does_not_hide_red(ws):
    root, _ = ws
    red = _c2("verify", "run_lt6", [{"nodeid": "a::b", "outcome": "failed"}],
              failures=[{"nodeid": "a::b", "classification": "new", "outcome": "failed"}],
              finished="2026-01-01T00:05:00+00:00")
    tdd = _c2("tdd", "run_lt6", [{"nodeid": "a::c", "outcome": "passed"}])
    errs = _lt(root, "run_lt6", reports=[("tests/latest-verify.json", red),
                                          ("tests/latest-tdd.json", tdd)])
    assert len(errs) == 1 and "latest-verify.json" in errs[0]


def test_unknown_failures_refuse_and_an_empty_run_lowers_push_ready(ws):
    root, _ = ws
    unk = _c2("verify", "run_lt7", [{"nodeid": "desktop::tsc", "outcome": "failed"},
                                   {"nodeid": "a::b", "outcome": "passed"}],
              failures=[{"nodeid": "desktop::tsc", "classification": "unknown",
                         "outcome": "failed"}])
    import pipeline_validator as pv
    d = _make_run(root, "run_lt7", "direct")
    _write_evidence(d, "tests/latest-verify.json", unk)
    refuse, _soft = pv.landing_test_errors(d, "run_lt7")
    assert refuse and "desktop::tsc" in refuse[0], refuse   # a broken build never lands
    # a backend 'unknown' (crashed shard, runner error) still refuses
    crash = _c2("verify", "run_lt9", [{"nodeid": "backend/tests/test_a.py::t", "outcome": "failed"}],
                failures=[{"nodeid": "backend/tests/test_a.py::t", "classification": "unknown",
                           "outcome": "failed"}])
    d9 = _make_run(root, "run_lt9", "direct")
    _write_evidence(d9, "tests/latest-verify.json", crash)
    refuse, _s = pv.landing_test_errors(d9, "run_lt9")
    assert refuse and "test_a.py::t" in refuse[0], refuse
    d = _make_run(root, "run_lt8", "direct")
    _write_evidence(d, "tests/latest-verify.json", _c2("verify", "run_lt8", []))
    refuse, soft = pv.landing_test_errors(d, "run_lt8")
    assert refuse == [] and any("no test passed" in x for x in soft), soft


def test_landing_refuses_a_regression_left_in_an_older_report(ws):
    root, _ = ws
    reg_old = _c2("regression", "run_lt5", [{"nodeid": "a::b", "outcome": "failed"}],
                  failures=[{"nodeid": "a::b", "classification": "regression", "outcome": "failed"}],
                  finished="2026-01-01T00:05:00+00:00")
    green = _c2("verify", "run_lt5", [{"nodeid": "a::b", "outcome": "passed"}])
    errs = _lt(root, "run_lt5", reports=[("tests/latest-regression.json", reg_old),
                                          ("tests/latest-verify.json", green)])
    assert len(errs) == 1 and "latest-regression.json" in errs[0]


def test_a_degrade_is_not_latched_into_verification_gaps():
    """Re-completing a degraded stage with the errors fixed clears it: the degrade lives
    on the stage record (read by the push_ready bound), never in the run-level latch."""
    assert cli._verification_gaps_of("build", ["build degraded: 1 gate error(s) recorded"],
                                      {"degraded": {"errors": ["x"]}}) == []


def test_a_refused_landing_reopens_deliver(ws):
    root, _ = ws
    d = _make_run(root, "run_reopen", "direct",
                  stages=[{"stage": "plan", "status": "completed"},
                          {"stage": "deliver", "status": "completed", "push_ready": False}],
                  stage_timing={"deliver": {"started_at": "2026-01-01T00:00:00+00:00",
                                            "completed_at": "2026-01-01T00:09:00+00:00"}})
    cli._reopen_after_refused_landing(d / "run.json")
    st = _run_state(root, "run_reopen")
    rec = next(r for r in st["stages"] if r["stage"] == "deliver")
    assert rec["status"] == "landing_refused" and rec["landing_refused_at"]
    assert "completed_at" not in st["stage_timing"]["deliver"]
    assert st["stage_timing"]["deliver"]["started_at"]
    plan = next(r for r in st["stages"] if r["stage"] == "plan")
    assert plan["status"] == "completed"   # only DELIVER is touched


def test_docs_only_deliver_needs_no_adversarial_review(ws, capsys):
    root, reg = ws
    _make_run(root, "run_docs_only", "direct", stages=["plan", "build"],
              dispatch_plan={"deliver": []})
    art = _tmpl("deliver")
    art["adversarial_review"]["profile_tier"] = "skipped"
    code, out = _complete(reg, capsys, "run_docs_only", "deliver", art, {}, check=True)
    msgs = json.dumps(out.get("errors", []))
    assert "profile_tier" not in msgs and "spawned" not in msgs and "meta_review" not in msgs, out
    assert any("documentation-only" in w for w in out["warnings"]), out["warnings"]
    # a missing plan (None) is not "no expert": the presence check still applies
    _make_run(root, "run_docs_unknown", "direct", stages=["plan", "build"])
    code, out = _complete(reg, capsys, "run_docs_unknown", "deliver", art, {}, check=True)
    assert any(e["code"] == "E_ADVERSARIAL_TIER" for e in out["errors"]), out


# ── a direct run may start at BUILD or DELIVER (no PLAN / BUILD paperwork) ──

def test_direct_run_starting_late_records_the_earlier_stages_skipped(ws, capsys):
    root, reg = ws
    _make_run(root, "run_late", "direct")
    code, out = _start(reg, capsys, "run_late", "build")
    assert code in (0, None), out
    recs = {r["stage"]: r for r in _run_state(root, "run_late")["stages"]}
    assert recs["plan"]["status"] == "skipped" and "started at build" in recs["plan"]["skip_reason"]
    code, out = _start(reg, capsys, "run_late", "deliver")
    assert code in (0, None), out
    recs = {r["stage"]: r for r in _run_state(root, "run_late")["stages"]}
    assert recs["build"]["status"] == "skipped" and recs["plan"]["status"] == "skipped"
    import pipeline_validator as pv
    assert pv._check_stage_order("deliver", "direct", _run_state(root, "run_late")["stages"])


def test_late_start_keeps_a_finished_stage_and_other_profiles(ws, capsys):
    root, reg = ws
    _make_run(root, "run_late2", "direct", stages=["plan"])
    _start(reg, capsys, "run_late2", "deliver")
    recs = {r["stage"]: r for r in _run_state(root, "run_late2")["stages"]}
    assert recs["plan"]["status"] == "completed" and recs["build"]["status"] == "skipped"
    _make_run(root, "run_full_late", "full")
    _start(reg, capsys, "run_full_late", "build")
    assert _run_state(root, "run_full_late")["stages"] == []   # only direct starts late
