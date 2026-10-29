"""S6-4d (design 2026-10-24-swarmai-usable-by-anyone.md §3.3.2, §3.3.4): REFLECT keeps
lessons a project cannot take, and the five-dimension audit reads the agent's own
validation reports instead of the deleted runner's final.

What is tested:
- REFLECT: a lesson for a project that has no DDD doc, a project directory that does
  not exist, or another project that does not exist is kept in the run's own REFLECT
  record (outcome ``kept_in_run``), not rejected, not degraded, and never raises; a
  destination that walks out of ``Projects/`` is still rejected (``route`` called
  without the run's repos; with them such a lesson goes to the shared Library:
  tests/test_reflect_library.py);
- the run directory's REPORT lists the kept lessons (escaped, as plain text);
- the trace matrix has no result column (no runner, no pass semantics); the audit
  details and the REPORT list each TEST report's commands and results;
- Quality counts a finding closed by a reported test as closed only when R2 judged
  the test ADEQUATE, and reports how many were confirmed by review.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.run_protocol import reflect, reflect_job, trace
from core.run_protocol.audit import build, render

pytestmark = pytest.mark.skip(reason="r16.5 removed REFLECT routing and kept-lesson writes")

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
    # A project with a DDD doc is still only a source candidate; CDF writes it.
    doc = tmp_path / "Projects" / "R" / "2-understanding" / "TECH.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# TECH\n\n## Conventions\n\n- existing\n")
    from types import SimpleNamespace
    from core.ingestion import api
    original = doc.read_text()
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(api, "enqueue", lambda *a, **k: SimpleNamespace(status="queued"))
        assert _route(tmp_path, "domain:R:TECH:Conventions", project="R") == "queued"
    assert doc.read_text() == original


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
                 contract=contract, open_checks={},
                 audit_through="COMPLETED", commits={"repo": "a" * 40}, **kw)


def test_report_lists_kept_lessons_as_plain_text():
    lessons = [{"id": "L1", "statement": "Use a [link](http://x) *never*",
                "outcome": {"status": "kept_in_run", "result": "kept_in_run"}},
               {"id": "L2", "statement": "applied one", "outcome": {"status": "applied"}}]
    report = render(_audit(findings=[], lessons=lessons), fmt="report")
    section = report.split("## Lessons kept in this run")[1]
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


# --- review of f84035312 ------------------------------------------------------------

def test_an_unreadable_test_report_blob_does_not_break_the_completed_audit(tmp_path):
    """The audit refresh runs after reflect_complete is logged: a TEST report whose
    blob cannot be read must show as unreadable, not raise (that left run.json,
    REPORT.md and the card stale behind a completed log)."""
    from core.run_protocol import engine as eng
    from core.run_protocol.store import BlobUnreadable

    class Store:
        def read_blob(self, digest):
            raise BlobUnreadable("blob gone")

    fake = type("E", (), {"store": Store(), "run_id": "run_x"})()
    event = {"kind": "validation_report",
             "payload": {"stage": "test", "repo": "repo",
                         "report_json": {"blob": "a" * 64}}}
    reports = eng.RunEngine._test_reports(fake, [event])
    assert reports["repo"].get("unreadable"), reports
    audit = _audit(findings=[], test_reports=reports)
    report = render(audit, fmt="report")
    assert "repo" in report and "could not be read" in report


def test_a_project_name_must_match_the_directory_exactly(tmp_path):
    """On a case-insensitive file system `foo` would open `Foo`; on a case-sensitive
    one it would not. Only the exact directory name is that project."""
    doc = tmp_path / "Projects" / "Foo" / "2-understanding" / "TECH.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# TECH\n\n## Conventions\n")
    assert _route(tmp_path, "domain:foo:TECH:Conventions", project="Foo") == "kept_in_run"
    assert "worker pool" not in doc.read_text()
    assert _route(tmp_path, "MEMORY", project="foo") == "kept_in_run"


@pytest.mark.parametrize("name", ["..", ".", "a/b", "", "a\\b"])
def test_a_project_name_that_is_not_one_plain_component_is_rejected(tmp_path, name):
    (tmp_path / "Projects").mkdir()
    assert _route(tmp_path, f"domain:{name}:TECH:Conventions") == "rejected_unknown_project"


def test_a_partial_proposal_write_leaves_no_file_behind(tmp_path, monkeypatch):
    """A write that fails part-way must not leave a prefix that the retry then
    reads as a different proposal (rejected_integrity)."""
    import os as _os
    (tmp_path / "Projects" / "P").mkdir(parents=True)
    real = _os.write
    calls = {"n": 0}

    def flaky(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            return real(fd, bytes(data)[:5])
        raise OSError("disk full")
    import os
    monkeypatch.setattr(os, "write", flaky)
    with pytest.raises(OSError):
        _route(tmp_path, "MEMORY")
    monkeypatch.setattr(os, "write", real)
    assert _route(tmp_path, "MEMORY").startswith("queued:")
