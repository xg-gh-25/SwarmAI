"""S6-4d (design 2026-10-24-swarmai-usable-by-anyone.md §3.3.2, §3.3.4): REFLECT keeps
lessons a project cannot take, and the five-dimension audit reads the agent's own
validation reports instead of the deleted runner's final.

What is tested:
- REFLECT: a lesson for a project that has no DDD doc, a project directory that does
  not exist, or another project that does not exist is kept in the run's own REFLECT
  record (outcome ``kept_in_run``), not rejected, not degraded, and never raises; a
  destination that walks out of ``Projects/`` is still rejected;
- the run directory's REPORT lists the kept lessons (escaped, as plain text);
- the trace matrix has no result column (no runner, no pass semantics); the audit
  details and the REPORT list each TEST report's commands and results;
- Quality counts a finding closed by a reported test as closed only when R2 judged
  the test ADEQUATE, and reports how many were confirmed by review.
"""
from __future__ import annotations

from pathlib import Path

from core.run_protocol import reflect, reflect_job, trace
from core.run_protocol.audit import build, render

LESSON = {"id": "L1", "statement": "Give background work its own worker pool so a "
          "blocking call never starves the interactive path.",
          "relation": {"kind": "new", "entry": None}}


def _route(ws: Path, destination: str, project: str = "P") -> str:
    return reflect_job.route(LESSON, destination, workspace=ws, project=project,
                             run_id="run_x")


def test_lessons_a_project_cannot_take_are_kept_in_the_run(tmp_path):
    (tmp_path / "Projects" / "P").mkdir(parents=True)            # a project, no DDD docs
    assert _route(tmp_path, "domain:P:TECH:Conventions") == "kept_in_run"
    assert _route(tmp_path, "domain:Q:TECH:Conventions") == "kept_in_run"   # no project Q
    assert _route(tmp_path, "MEMORY", project="Nope") == "kept_in_run"      # no project dir
    assert _route(tmp_path, "domain:..:TECH:Conventions") == "rejected_unknown_project"
    # a project that has the doc still gets the lesson written
    doc = tmp_path / "Projects" / "R" / "2-understanding" / "TECH.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# TECH\n\n## Conventions\n\n- existing\n")
    assert _route(tmp_path, "domain:R:TECH:Conventions", project="R") in reflect.APPLIED_RESULTS
    assert "worker pool" in doc.read_text()


def test_kept_lessons_are_not_a_degraded_reflect(tmp_path):
    (tmp_path / "Projects" / "P").mkdir(parents=True)
    doc = {"schema": 1, "lessons": [{**LESSON, "destination": "domain:P:TECH:Conventions"}]}
    out = reflect.process(doc, judge=lambda _l: "keep",
                          writer=lambda lesson, dest: _route(tmp_path, dest))
    assert out["reflect"] == "ok"
    assert out["lessons"][0]["outcome"]["status"] == "kept_in_run"


def _audit(**kw):
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
              {"kind": "validation_report", "at": "2026-10-22T00:00:20+00:00",
               "payload": {"stage": "test", "repo": "repo", "code_tree": "1" * 40,
                           "n_pass": 1, "n_fail": 1, "n_not_run": 0}}]
    return build(run_id="run_x", projection=projection, events=events, repos_isolated=1,
                 contract=contract, push={"push_ready": True, "reasons": {}},
                 audit_through="COMPLETED", commits={"repo": "a" * 40}, **kw)


def test_report_lists_kept_lessons_as_plain_text():
    lessons = [{"id": "L1", "statement": "Use a [link](http://x) *never*",
                "outcome": {"status": "kept_in_run", "result": "kept_in_run"}},
               {"id": "L2", "statement": "applied one", "outcome": {"status": "applied"}}]
    report = render(_audit(findings=[], lessons=lessons), fmt="report")
    section = report.split("## 没有写进项目 DDD 的经验")[1]
    assert "L1" in section and "L2" not in section
    assert "[link](http://x)" not in section and "\\[link\\]" in section


def test_trace_matrix_has_no_result_and_reports_list_commands():
    design = {"exit_criteria": [{"id": "X1", "kind": "test", "ref": "pkg/a_test.go TestA",
                                 "covers": ["R1.1"]}], "tasks": []}
    m = trace.matrix(design)
    assert "results" not in m["rows"]["R1.1"]["exits"][0]
    reports = {"repo": {"commands": [{"command": "go test ./pkg", "result": "pass"},
                                     {"command": "go vet ./...", "result": "fail",
                                      "reason": "shadowed err"}]}}
    audit = _audit(findings=[], design=design, test_reports=reports)
    assert audit["details"]["test_reports"]["repo"][1]["result"] == "fail"
    report = render(audit, fmt="report")
    assert "go test ./pkg" in report and "go vet ./..." in report and "shadowed err" in report


def test_quality_counts_a_reported_test_closure_only_when_review_confirmed_it():
    base = {"kind": "behavior", "severity": "HIGH", "file": "a.go", "line": 1,
            "claim": "c", "expert": "correctness"}
    confirmed = {**base, "canonical_id": "CF-1", "status": "closed_proven", "r2_status": "ADEQUATE"}
    pending = {**base, "canonical_id": "CF-2", "status": "closed_proven", "r2_status": None}
    q = _audit(findings=[confirmed, pending])["summary"]["quality"]
    assert (q["findings_closed"], q["findings_confirmed"], q["findings_total"]) == (1, 1, 2)
    assert q["verdict"] == "WARN"


def test_a_run_in_a_project_without_ddd_keeps_its_lesson_and_reports_it(tmp_path, monkeypatch):
    """End to end through the CLI rig: the rig's project has no DDD docs. REFLECT ends
    normally (not degraded), the lesson is kept in the run, and the run's REPORT lists it."""
    import json
    from tests.pipeline_v1_rig import Rig
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    try:
        r.create()
        r.drive(until="deliver")
        evidence = [r.events()[0]["event_id"]]
        lesson = {"id": "L1", "type": "guideline", "scope": "domain:P",
                  "statement": LESSON["statement"], "tell": "a blocking call on a shared pool",
                  "evidence": evidence, "relation": {"kind": "new", "entry": None}}
        out = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": [lesson]}))
        assert out.get("ok") is True, out
        from tests.pipeline_v1_rig import SWARM
        from core.run_protocol import reflect_job as job
        landed = r.v1("land", "--message", "fix: swap the operands" + SWARM)
        assert landed.get("ok") is True, landed
        job.process_run(r.engine())            # the daemon's background REFLECT
        state = r.state()
        assert state["status"] == "completed", state
        assert "reflect_complete" in r.kinds() and "reflect_exhausted" not in r.kinds()
        engine = r.engine()
        rows = json.loads((engine.run_dir / "artifacts" / "reflect.json").read_text())["lessons"]
        assert rows[0]["outcome"]["status"] == "kept_in_run", rows
        report = (engine.run_dir / "REPORT.md").read_text()
        assert "## 没有写进项目 DDD 的经验" in report and "worker pool" in report
    finally:
        r.close()
