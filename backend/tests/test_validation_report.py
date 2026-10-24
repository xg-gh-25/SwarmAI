"""Validation reports (S6-1a, design 2026-10-24-swarmai-usable-by-anyone §3.3.3).

Methodology: the schema parser directly; then a real direct run (temporary repo,
workspace, data dir, linked worktree) whose BUILD stage-complete carries a report.
Invariants:
  - exactly one report per expected repo; fail / not_run need a reason; unknown keys
    and wrong repos are refused with a message naming the field;
  - the event's code_tree is the tree the engine computes for the worktree now, not a
    value the agent supplied (an agent-supplied tree is an unknown key, refused);
  - an invalid report records nothing; the report blob reads back intact;
  - the REPORT section is derived from the events alone.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.run_protocol import git_tree
from core.run_protocol import validation_report as vr
from core.run_protocol.engine import EngineError, RunEngine


def _ok(repo="repo", **over):
    row = {"repo": repo, "commands": [
        {"command": "npm test", "cwd": ".", "result": "pass"},
        {"command": "cargo clippy", "cwd": ".", "result": "not_run",
         "reason": "clippy is not installed here"}],
        "tests_changed": ["src/x.test.ts"], "rationale": "x.test.ts fails before the fix"}
    row.update(over)
    return row


def test_parse_accepts_one_report_per_repo_and_normalises():
    rows = vr.parse({"reports": [_ok("a"), _ok("b")]}, ["a", "b"])
    assert [r["repo"] for r in rows] == ["a", "b"]
    assert rows[0]["commands"][1]["reason"] == "clippy is not installed here"


@pytest.mark.parametrize("artifact,needle", [
    ({}, "reports"),
    ({"reports": []}, "missing a report for ['repo']"),
    ({"reports": [_ok(), _ok()]}, "appears twice"),
    ({"reports": [_ok("other")]}, "must be one of"),
    ({"reports": [_ok(code_tree="a" * 40)]}, "unknown keys"),
    ({"reports": [_ok(commands=[{"command": "make", "cwd": ".", "result": "fail"}])]},
     "reason"),
    ({"reports": [_ok(commands=[{"command": "make", "cwd": ".", "result": "green"}])]},
     "result must be one of"),
    ({"reports": [_ok(rationale="  ")]}, "rationale"),
])
def test_parse_refuses_with_a_message_naming_the_problem(artifact, needle):
    with pytest.raises(vr.ReportError, match=None) as ei:
        vr.parse(artifact, ["repo"])
    assert needle in str(ei.value)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


@pytest.fixture
def build_run(tmp_path, monkeypatch):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q"); _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "T")
    (repo / "a.js").write_text("module.exports = 1\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", requirement="change", session_id="s",
                              repos={"repo": str(repo)}, run_id="run_vr")
    wt = engine.ensure_stage_worktrees()["repo"]
    (Path(wt) / "a.js").write_text("module.exports = 2\n")
    return engine, wt


def _reports(engine):
    return [e for e in engine.store.read_events() if e["kind"] == "validation_report"]


def test_build_report_is_recorded_on_the_engines_tree(build_run):
    engine, wt = build_run
    engine.stage_complete({"reports": [_ok()]})
    (event,) = _reports(engine)
    p = event["payload"]
    tree = git_tree.write_tree(str(wt), (f".aidlc/deliverables/{engine.run_id}/",), {})
    assert p["stage"] == "build" and p["repo"] == "repo" and p["code_tree"] == tree
    assert (p["n_pass"], p["n_fail"], p["n_not_run"]) == (1, 0, 1)
    assert vr.read(engine.store, event)["tests_changed"] == ["src/x.test.ts"]
    lines = vr.report_lines(engine.store.read_events())
    assert any("build · repo @ " + tree[:12] in line and "1 通过" in line for line in lines)


def test_invalid_report_records_nothing(build_run):
    engine, _wt = build_run
    with pytest.raises(EngineError, match="validation report"):
        engine.stage_complete({"reports": [_ok(rationale="")]})
    assert _reports(engine) == []
    assert engine.projection()["stage"] == "build"


def test_old_path_without_reports_still_works(build_run):
    """S6-1a keeps the runner path: a BUILD completion without "reports" writes no
    report event (the runner's checks decide as before)."""
    engine, _wt = build_run
    engine.stage_complete(None)
    assert _reports(engine) == []
