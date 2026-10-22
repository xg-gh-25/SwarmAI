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
    """Through the runner's writer: runs made by run-create seal their evidence."""
    import pipeline_test
    pipeline_test._write_json(run_dir / "evidence" / rel, data)


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
    # the template shows a sample consumer (K1 load_x); these fixture runs change no
    # shared contract, so they say so instead of carrying the sample
    a["implementation_constraints"] = {"no_contract_change_reason": "fixture run: no shared contract changes"}
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
        assert rec.get("push_ready") is False and needle in rec["push_ready_reason"]


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


# ── derived fields from evidence (item 2) ──────────────────────────────────


# ── push_ready upper bound (item 2) ────────────────────────────────────────

def _clean_deliver():
    return _tmpl("deliver")


def _green_ev():
    return _c2("regression", "r", [{"nodeid": "a::b", "outcome": "passed"}])


def test_push_ready_needs_runner_evidence_not_an_artifact_claim():
    """quality.tests_pass / test.passed are model-written: a code profile ships only on a
    runner report (quality audit r2)."""
    art = _clean_deliver()
    art["quality"]["tests_pass"] = True
    ok, reasons = pv.push_ready_upper_bound(art, "full")
    assert ok is False and any("no runner evidence" in r for r in reasons)
    assert pv.push_ready_upper_bound(art, "direct", docs_only=True)[0] is True  # docs diff, no runner
    assert pv.push_ready_upper_bound(art, "docs")[0] is False  # the profile NAME exempts nothing


def test_push_ready_bound_exit_criteria_strict_only():
    unmet = {"met": False, "unmet": ["X1"]}
    assert pv.push_ready_upper_bound(_clean_deliver(), "direct", exit_result=unmet,
                                     test_evidence=_green_ev())[0] is False
    assert pv.push_ready_upper_bound(_clean_deliver(), "trivial", exit_result=unmet,
                                     test_evidence=_green_ev())[0] is True
    failing_ev = _c2("regression", "r", [{"nodeid": "a::b", "outcome": "failed"}],
                     failures=[{"nodeid": "a::b", "classification": "new", "summary": "x"}])
    assert pv.push_ready_upper_bound(_clean_deliver(), "full", test_evidence=failing_ev)[0] is False


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


def _rt_claimed_deliver():
    art = _clean_deliver()
    art["adversarial_review"]["findings"] = [_specialist_high()]
    art["adversarial_review"][_RT] = True          # the claim the marker must back
    return art


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


# ── adversarial-review fixes: HIGH-1 … MED-6, LOW ──────────────────────────

def _run_update_args(run_id, **kw):
    ns = SimpleNamespace(project=PROJECT, run_id=run_id, status=None, stage_json=None,
                         taste_decision=None, profile=None, ddd_checksums=None,
                         files_touched=None, add_required_expert=None, force=False,
                         reason=None, force_checkpoint=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


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


# ── DELIVER generates REPORT.md itself; goal_cycle is an advanceable state ──

def test_pipeline_states_cover_every_profile_stage():
    from core.artifact_registry import PIPELINE_STATES
    for name, stages in PIPELINE_PROFILES.items():
        assert set(stages) <= set(PIPELINE_STATES), name
    assert PIPELINE_STATES[:8] == tuple(PIPELINE_PROFILES["full"])  # full's order kept


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


# ── H1 / H5: review rounds counted from completion markers, enforced at stage-complete ──


def _budget_run(root, rid):
    _make_run(root, rid, "direct",
              stage_timing={"plan": {"started_at": "2026-01-01T00:00:00+00:00"}})


# ── gate speed: evidence handed to the gate, not re-read by it ──


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


def test_report_tokens_label_for_transcript_usage():
    tx = {"source": "transcript", "tokens": 1234567, "cache_read_tokens": 9_000_000,
          "cost_usd": None}
    assert cli._report_tokens_label(tx) == "1,234,567 (measured per response, cache read 9,000,000)"
    settled = {**tx, "cost_usd": 12.5, "cost_excludes_current_turn": True}
    label = cli._report_tokens_label(settled)
    assert label.startswith("1,234,567 (measured per response, cache read 9,000,000; ")
    assert "$12.50 settled" in label and "excludes the turn in flight" in label
    assert "估算" not in label


    # (stage-complete recording the new id: test_stage_commands_record_the_transcript_id)


# ── exit_evidence lists (run_a87bfe3f) ─────────────────────────────────────────
# stages/test.md describes fixes/unfixed per item, so agents write lists; the record
# schema wants strings. 4+ runs were rejected on exactly this shape. The parse step
# joins a list of strings into one string; an empty or all-blank list becomes '' so
# a manual exit criterion it names stays unmet (never a placeholder that looks like
# evidence).

class TestExitEvidenceLists:


    def test_test_template_shows_string_evidence(self):
        tmpl = pv.STAGE_RECORD_TEMPLATES["test"]["exit_evidence"]
        assert tmpl and all(isinstance(v, str) for v in tmpl.values())
        assert {"wtf", "fixes", "unfixed"} <= set(tmpl)


# ── one landing decision: a gate error degrades the stage on the FIRST try ──


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
    assert pv.docs_only_paths(["docs/a.md", "img/x.png", "README.md", "docs/context/a.md"])
    assert not pv.docs_only_paths([])
    # backend/context/ holds the governed sources of every session's system prompt:
    # agent-behaviour prose, reviewed like skills and experts (also by absolute path).
    for code in ("backend/x.py", "backend/requirements.txt", "backend/experts/code/c.md",
                 "backend/skills/s_x/SKILL.md", ".claude/agents/a.md", "Makefile",
                 "desktop/icon.svg", "backend/tests/conftest.py",
                 "backend/context/SOUL.md", "backend/context/STEERING.md",
                 "backend/context/SECURITY-BASELINE.md",
                 "/Users/u/.swarm-ai/worktrees/run_x/swarmai/backend/context/AGENT.md"):
        assert not pv.docs_only_paths(["docs/a.md", code]), code


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


# ── a direct run always goes through PLAN (XG 2026-10-20); BUILD may be skipped ──


def test_a_docs_profile_run_editing_code_is_not_push_ready():
    import pipeline_validator as pv
    art = _clean_deliver()
    art["quality"]["tests_pass"] = True
    ok, reasons = pv.push_ready_upper_bound(art, "docs", docs_only=pv.docs_only_paths(
        ["backend/scripts/catalog.py", "README.md"]))
    assert ok is False and any("no runner evidence" in r for r in reasons)
