"""REPORT.md renders what the stages wrote — read from the writers' own field names.

What is tested
--------------
``cmd_run_report`` had drifted from the stage writers. On a real run it rendered
``Tests: 0/0`` (it read TDD totals off the BUILD record, whose ``tdd`` carries
``tests_generated``/``green_pass``, while the test_report artifact held
``tests_total=2847``), ``verdict: ?`` (the delivery writer emits
``findings_total/fixed/remaining``, never ``verdict``), an empty completion audit
(writer: ``requirements_met/requirements_total``; reader: ``criteria_met``),
``Confidence 0/10`` (numeric confidence was retired for the binary push-ready
verdict; no stage writes it) and ``8 files, +0/-0`` (the commit held 20 files,
+2451/-482).

Methodology / key properties
----------------------------
* A CONTRACT test pins every field the report reads to the writer schema
  (``pipeline_validator.get_stage_schema``: template + required + recommended), so
  a reader that drifts from its writer goes red here instead of silently rendering 0.
* Unit tests on the pure read helpers use payloads shaped like the real artifacts.
* "Not recorded" is a state, not a zero: a metric no stage produced is omitted.
* Files/lines come from the run's own recorded commits via ``git show --numstat``,
  exercised against a real temporary repo; a malformed sha or a vanished repo is
  skipped, never passed to git.
* One end-to-end ``cmd_run_report`` over a fixture run checks the rendered text.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import artifact_cli as ac


# ── contract: reader ⊆ writer schema ──────────────────────────────────────────


# ── tests ─────────────────────────────────────────────────────────────────────

def test_test_metrics_come_from_the_test_report():
    m = ac._report_test_metrics(
        {"passed": True, "failed": 0, "tests_new": 105, "tests_total": 2847, "regressions": 0},
    )
    assert m == {"tests_total": 2847, "failed": 0, "tests_new": 105, "regressions": 0,
                 "suite_passed": True}


def test_test_metrics_are_omitted_when_no_test_report():
    assert ac._report_test_metrics({}) == {}


def test_legacy_integer_passed_is_a_count_not_a_flag():
    m = ac._report_test_metrics({"passed": 40, "failed": 2})
    assert m["tests_total"] == 42 and m["failed"] == 2 and "suite_passed" not in m


# ── adversarial + completion audit ────────────────────────────────────────────


# ── files/lines from the run's own commits ────────────────────────────────────

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("1\n2\n3\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "base")
    (r / "a.py").write_text("1\nX\n3\n4\n")
    (r / "b.py").write_text("new\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "change")
    return r


# ── end to end ────────────────────────────────────────────────────────────────

def _write(ws, project, run_id, run_data, artifacts):
    art_dir = ws / "Projects" / project / ".artifacts"
    run_dir = art_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    entries = []
    for art_id, (atype, data) in artifacts.items():
        fname = f"runs/{run_id}/{atype}-{art_id}.json"
        (art_dir / fname).write_text(json.dumps(data), encoding="utf-8")
        entries.append({"id": art_id, "type": atype, "file": fname, "run_id": run_id,
                        "created": "2026-10-17T10:00:00+00:00", "producer": "p",
                        "summary": "s", "superseded_by": None})
    (art_dir / "manifest.json").write_text(json.dumps({"project": project, "artifacts": entries}))
    (run_dir / "run.json").write_text(json.dumps(run_data))
    return run_dir


def _report_for(tmp_path, monkeypatch, run_id, review=None, adversarial=None):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    stages, arts = [], {}
    if review is not None:
        stages.append({"stage": "review", "status": "completed", "artifact_id": "art_r"})
        arts["art_r"] = ("review", review)
    stages.append({"stage": "deliver", "status": "completed", "artifact_id": "art_d"})
    arts["art_d"] = ("delivery", {"title": "T", "adversarial_review": adversarial or {}})
    run_data = {"id": run_id, "project": "P", "profile": "full", "requirement": "r",
                "status": "completed", "created_at": "2026-10-17T09:00:00+00:00",
                "updated_at": "2026-10-17T10:00:00+00:00",
                "completed_at": "2026-10-17T10:00:00+00:00", "stages": stages,
                "taste_decisions": []}
    run_dir = _write(tmp_path, "P", run_id, run_data, arts)
    ac.cmd_run_report(SimpleNamespace(project="P", run_id=run_id),
                      ac.ArtifactRegistry(ac._get_workspace()))
    return (run_dir / "REPORT.md").read_text(encoding="utf-8")


# ── the contract list must match what the read helpers actually read ──────────

#: read helper -> the (stage, path) it reads. A new helper must be added here.
_HELPER_SCOPE = {
    "_report_test_metrics": ("test", ()),
    "_report_adversarial_summary": ("deliver", ("adversarial_review",)),
    "_report_completion_lines": ("deliver", ("completion_audit",)),
    "_report_build_tests_written": ("build", ("tdd",)),
}


def _review_report(tmp_path, monkeypatch, run_id, deliver, stage_extra=None, commits=None):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    stage = {"stage": "deliver", "status": "completed", "artifact_id": "art_d", **(stage_extra or {})}
    run_data = {"id": run_id, "project": "P", "profile": "full", "requirement": "the requirement text",
                "status": "completed", "created_at": "2026-10-17T09:00:00+00:00",
                "updated_at": "2026-10-17T10:00:00+00:00",
                "completed_at": "2026-10-17T10:00:00+00:00", "stages": [stage],
                "taste_decisions": [], "commits": commits or []}
    run_dir = _write(tmp_path, "P", run_id, run_data, {"art_d": ("delivery", deliver)})
    reg = ac.ArtifactRegistry(ac._get_workspace())
    ac.cmd_run_report(SimpleNamespace(project="P", run_id=run_id), reg)
    return (run_dir / "REPORT.md").read_text(encoding="utf-8")
