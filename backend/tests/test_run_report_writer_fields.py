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

def _schema_fields(stage: str, path: tuple[str, ...]) -> set[str]:
    from scripts.pipeline_validator import get_stage_schema

    info = get_stage_schema(stage)
    node = info.get("template") or {}
    for key in path:
        node = node.get(key) or {}
    fields = set(node.keys()) if isinstance(node, dict) else set()
    if not path:
        fields |= set(info.get("required") or []) | set(info.get("recommended") or [])
    return fields


@pytest.mark.parametrize("stage,path,fields", [
    (stage, path, fields) for (stage, path), fields in ac._REPORT_READS.items()
])
def test_every_field_the_report_reads_is_in_the_writer_schema(stage, path, fields):
    known = _schema_fields(stage, path)
    missing = sorted(set(fields) - known)
    assert not missing, (
        f"REPORT reads {missing} from {stage}{'.' + '.'.join(path) if path else ''}, "
        f"but the writer schema has no such field ({sorted(known)}). Either the writer "
        "renamed it (follow it) or the reader invented it (delete it)."
    )


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


def test_header_test_phrase():
    assert ac._report_tests_phrase({"tests_total": 2847, "failed": 0}) == "2847 run, 0 failed"
    assert ac._report_tests_phrase({}) is None


# ── adversarial + completion audit ────────────────────────────────────────────

def test_adversarial_summary_from_writer_counts():
    s = ac._report_adversarial_summary(
        {"spawned": True, "findings_total": 16, "findings_fixed": 14, "findings_remaining": 0})
    assert s == "16 found · 14 fixed · 0 open"


def test_adversarial_summary_absent_without_counts():
    assert ac._report_adversarial_summary({"spawned": True}) is None


def test_completion_audit_lines_from_writer_fields():
    lines = ac._report_completion_lines(
        {"all_green": True, "requirements_met": 9, "requirements_total": 9})
    assert "**Requirements met:** 9/9" in lines
    assert "**All green:** yes" in lines


def test_completion_audit_legacy_criteria_still_render():
    lines = ac._report_completion_lines({"criteria_met": ["a"], "criteria_unmet": ["b"]})
    assert "- [x] a" in lines and "- [ ] b" in lines


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


def test_commit_stats_from_git_numstat(repo):
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    st = ac._report_commit_stats([{"repo": str(repo), "sha": sha, "files": ["a.py", "b.py"]}])
    assert st == {"files": ["a.py", "b.py"], "added": 3, "removed": 1}


@pytest.mark.parametrize("bad_sha", ["--output=/tmp/x", "HEAD;rm -rf", "", "zzzzzzz"])
def test_malformed_sha_is_never_passed_to_git(repo, bad_sha):
    assert ac._report_commit_stats([{"repo": str(repo), "sha": bad_sha}]) is None


def test_vanished_repo_yields_no_stats(tmp_path):
    assert ac._report_commit_stats([{"repo": str(tmp_path / "gone"), "sha": "abcdef1"}]) is None


def test_no_commits_yields_no_stats():
    assert ac._report_commit_stats([]) is None


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


def test_report_renders_measured_values_and_no_invented_ones(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    project, run_id = "P", "run_fields01"
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    run_data = {
        "id": run_id, "project": project, "profile": "full", "requirement": "r",
        "status": "completed", "created_at": "2026-10-17T09:00:00+00:00",
        "updated_at": "2026-10-17T10:00:00+00:00", "completed_at": "2026-10-17T10:00:00+00:00",
        "commits": [{"repo": str(repo), "sha": sha, "files": ["a.py", "b.py"]}],
        "stages": [
            {"stage": "build", "status": "completed", "artifact_id": "art_b",
             "files_changed": ["only_one.py"],
             "tdd": {"tests_generated": 63, "red_failures": 41, "green_pass": True}},
            {"stage": "test", "status": "completed", "artifact_id": "art_t"},
            {"stage": "deliver", "status": "completed", "artifact_id": "art_d"},
        ],
        "taste_decisions": [],
    }
    run_dir = _write(tmp_path, project, run_id, run_data, {
        "art_b": ("changeset", {"files_changed": ["only_one.py"], "tdd": {"tests_generated": 63}}),
        "art_t": ("test_report", {"passed": True, "failed": 0, "tests_total": 2847,
                                  "tests_new": 105, "regressions": 0, "layers": {}}),
        "art_d": ("delivery", {"title": "T", "adversarial_review": {
            "spawned": True, "profile_tier": "full", "findings_total": 16,
            "findings_fixed": 14, "findings_remaining": 0, "findings": []},
            "completion_audit": {"all_green": True, "requirements_met": 9,
                                 "requirements_total": 9}}),
    })
    reg = ac.ArtifactRegistry(ac._get_workspace())
    ac.cmd_run_report(SimpleNamespace(project=project, run_id=run_id), reg)
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")

    assert "**Tests:** 2847 run, 0 failed" in text
    assert "Tests: 0/0" not in text and "**Tests:** 0/0" not in text
    assert "**Files:** 2" in text, "file count must come from the run's commit, not BUILD's list"
    assert "+3 / -1" in text
    assert "Confidence" not in text, "numeric confidence was retired; no stage writes it"
    assert "16 found · 14 fixed · 0 open" in text
    assert "verdict: ?" not in text
    assert "Requirements met:** 9/9" in text
    assert "(No completion audit data)" not in text


def test_unfixable_gap_is_surfaced_as_a_known_gap(tmp_path, monkeypatch):
    """deliver.md: "unfixable_gaps > 0 → WARNING (note in report, user decides)".

    The report used to read only ``criteria_unmet`` here, so a run whose real-app
    check could not be done inside the pipeline showed "None identified."
    """
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    project, run_id = "P", "run_gaps01"
    run_data = {
        "id": run_id, "project": project, "profile": "full", "requirement": "r",
        "status": "completed", "created_at": "2026-10-17T09:00:00+00:00",
        "updated_at": "2026-10-17T10:00:00+00:00", "completed_at": "2026-10-17T10:00:00+00:00",
        "stages": [{"stage": "deliver", "status": "completed", "artifact_id": "art_d"}],
        "taste_decisions": [],
    }
    run_dir = _write(tmp_path, project, run_id, run_data, {
        "art_d": ("delivery", {"title": "T", "adversarial_review": {"spawned": True},
                               "completion_audit": {"all_green": True, "requirements_met": 9,
                                                    "requirements_total": 10, "gaps": 0,
                                                    "unfixable_gaps": 1}}),
    })
    reg = ac.ArtifactRegistry(ac._get_workspace())
    ac.cmd_run_report(SimpleNamespace(project=project, run_id=run_id), reg)
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    gaps = text.split("## 10. Known Gaps", 1)[1]
    assert "could not be closed inside the pipeline" in gaps
    assert "None identified" not in gaps
    assert "**Tests:** not recorded" in text, "no test stage ran — say so, never 0/0"


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


def test_spawned_review_without_counts_is_not_reported_as_zero(tmp_path, monkeypatch):
    text = _report_for(tmp_path, monkeypatch, "run_nocount", review={"approved": True},
                       adversarial={"spawned": True, "profile_tier": "full"})
    assert "findings not recorded (approved)" in text
    assert "Findings: 0" not in text and "| 0 findings" not in text
    assert "Sub-agent spawned: ✅ | findings not recorded" in text


def test_present_empty_findings_list_is_a_measured_zero(tmp_path, monkeypatch):
    text = _report_for(tmp_path, monkeypatch, "run_zero", review={"findings": [], "approved": True},
                       adversarial={"spawned": True, "profile_tier": "full", "findings": []})
    assert "| 0 findings (approved)" in text
    assert "Findings: 0" in text


# ── the contract list must match what the read helpers actually read ──────────

#: read helper -> the (stage, path) it reads. A new helper must be added here.
_HELPER_SCOPE = {
    "_report_test_metrics": ("test", ()),
    "_report_adversarial_summary": ("deliver", ("adversarial_review",)),
    "_report_completion_lines": ("deliver", ("completion_audit",)),
    "_report_build_tests_written": ("build", ("tdd",)),
}


@pytest.mark.parametrize("fn_name,scope", list(_HELPER_SCOPE.items()))
def test_every_field_a_read_helper_gets_is_listed(fn_name, scope):
    """Parse the helper and collect every ``.get("<field>")`` — by AST, not by grep,
    so a field named in a comment or docstring does not count. Each must be in
    _REPORT_READS (or the legacy list) for that scope, which the contract test above
    pins to the writer schema. Without this, the list could drift from the code."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(getattr(ac, fn_name)))
    got = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get" and node.args
        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
    }
    assert got, f"{fn_name} reads no fields — the parse found nothing to check"
    allowed = set(ac._REPORT_READS.get(scope, ())) | set(ac._REPORT_LEGACY_READS.get(scope, ()))
    assert got <= allowed, (
        f"{fn_name} reads {sorted(got - allowed)} which _REPORT_READS[{scope}] does not "
        "list — add it there (and the contract test will check it against the writer)."
    )


def test_shared_commit_counts_only_the_runs_own_files(repo):
    """A credited commit can also carry another session's files: stats follow the
    record's `files`, never the whole commit (retro Gate-2 of ba1c8a22, MEDIUM-1)."""
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    st = ac._report_commit_stats([{"repo": str(repo), "sha": sha, "files": ["b.py"]}])
    assert st == {"files": ["b.py"], "added": 1, "removed": 0}
    # files: [] = nothing in this commit is ours → not measured from it
    assert ac._report_commit_stats([{"repo": str(repo), "sha": sha, "files": []}]) is None
    # legacy record without `files` → the whole commit, as before
    whole = ac._report_commit_stats([{"repo": str(repo), "sha": sha}])
    assert whole == {"files": ["a.py", "b.py"], "added": 3, "removed": 1}
