"""Untracked files in a pipeline delivery are listed in REPORT and the Run Audit.

A run's code tree takes in the worktree's untracked, non-ignored files (``tree.py``
``build_code_trees``), so a repo whose ignore rules miss its build or test output gets
that output committed. The delivery rule stays; the landing records those files and the
REPORT / run_audit.json show them so a person sees them before pushing.

What is tested:
- ``git_tree.untracked_files`` lists exactly the untracked, non-ignored files outside the
  reserved REPORT directory: not tracked, not staged, not ignored (X1), and only git's
  ignore rules decide, no file-name pattern (X2).
- REPORT has the section only when files were recorded, at most 20 per repo plus the
  count of the rest; run_audit.json keeps every path; a failed listing says so; a
  hostile file name stays plain text (X3).
- The audit shows the record of the landing that landed (a later prepare or attempt
  records its own), and ignores a record the landing did not write.
- The listing changes no verdict, including Token Cost when nothing was metered (X4).
- A real ``land`` (CLI, rig) with 200 untracked files and a person's ``run-land`` both
  record the files, still deliver them, and the COMPLETED audit rebuilt after the
  worktree is gone still lists them (X5, X6).
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import pytest

from core.run_protocol import audit, git_tree

pytestmark = pytest.mark.timeout(300)

RESERVED = (".aidlc/deliverables/run_x/",)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _repo(tmp_path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "T")
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _write(repo, name, text="x\n"):
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_untracked_files_lists_only_untracked_not_ignored(tmp_path):
    repo = _repo(tmp_path, {"a.py": "a = 1\n", ".gitignore": "*.log\n"})
    _write(repo, "a.py", "a = 2\n")                      # tracked, modified
    _write(repo, "staged.py")
    _git(repo, "add", "staged.py")                       # added to git, not committed
    _write(repo, "new.py")                               # untracked
    _write(repo, "dir with space/b c.txt")               # untracked, odd name
    _write(repo, "run.log")                              # ignored
    _write(repo, ".aidlc/deliverables/run_x/REPORT.md")  # the reserved REPORT dir
    _git(repo, "rm", "-q", "--cached", ".gitignore")     # out of the index, still in HEAD
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("*.log\n")
    assert git_tree.untracked_files(str(repo), RESERVED) == ["dir with space/b c.txt", "new.py"]


def test_untracked_files_uses_only_git_ignore_rules(tmp_path):
    repo = _repo(tmp_path, {"a.py": "a = 1\n"})          # no ignore rules at all
    _write(repo, "__pycache__/a.cpython-312.pyc")
    _write(repo, "node_modules/x/index.js")
    assert git_tree.untracked_files(str(repo), RESERVED) == [
        "__pycache__/a.cpython-312.pyc", "node_modules/x/index.js"]
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("node_modules/\n")
    assert git_tree.untracked_files(str(repo), RESERVED) == ["__pycache__/a.cpython-312.pyc"]


BLOBS: dict[str, bytes] = {}


def _metric(files=None, *, landing="att1", producer="cli", reason=None):
    payload = {"name": "delivery_untracked", "value": sum(map(len, (files or {}).values())),
               "landing": landing}
    if files is not None:
        digest = f"blob{len(BLOBS)}"
        BLOBS[digest] = json.dumps({"files": files}).encode()
        payload["files_blob"] = digest
    if reason:
        payload["reason"] = reason
    return {"kind": "metric", "producer": producer, "at": "2026-10-22T00:00:20+00:00",
            "stage": "deliver", "attempt": 1, "payload": payload}


def _events(*metrics, land=None):
    land = {"stage_attempt_event_id": "att1"} if land is None else land
    return ([{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}}]
            + list(metrics)
            + [{"kind": "land", "at": "2026-10-22T00:00:30+00:00", "payload": land}])


def _build(events):
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    return audit.build(run_id="run_x", projection=projection, events=events, findings=[],
                       contract=contract, open_checks={}, audit_through="COMPLETED",
                       repos_isolated=1,
                       untracked=audit.select_untracked(events, BLOBS.__getitem__))


HEADING = "## Files delivered without being added to git"


def test_report_section_lists_files_and_caps_per_repo():
    many = [f"out/f{i:02d}.bin" for i in range(25)]
    value = _build(_events(_metric({"repo": many, "docs": ["notes.txt"]})))
    assert value["details"]["untracked_in_delivery"]["files"] == {"docs": ["notes.txt"],
                                                                  "repo": many}
    report = audit.render(value, fmt="report")
    assert HEADING in report
    assert "- docs: notes.txt" in report
    assert "- repo: out/f19.bin" in report and "out/f20.bin" not in report
    assert "- repo: 5 more in run_audit.json" in report
    for metrics in ((), (_metric({}),)):                   # nothing recorded / nothing found
        quiet = audit.render(_build(_events(*metrics)), fmt="report")
        assert HEADING not in quiet and "Untracked files in this delivery" not in quiet
    failed = audit.render(_build(_events(_metric(
        {}, reason="untracked files could not be listed: x"))), fmt="report")
    assert HEADING not in failed
    assert "Untracked files in this delivery: not listed (untracked files could not be listed: x)" \
        in failed


def test_hostile_file_names_stay_plain_text():
    names = ["[x](https://evil.example).txt", "<img src=x onerror=alert(1)>.md",
             "line\n# Heading\n![i](http://e/x.png)"]
    report = audit.render(_build(_events(_metric({"repo": names}))), fmt="report")
    section = report.split(HEADING, 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("- repo:")]
    assert len(rows) == 3, section
    assert "\n# Heading" not in section
    for row in rows:
        # escaped as text: no raw HTML, and every link / image bracket carries a backslash
        assert "<" not in row and not re.search(r"(?<!\\)\]\(", row) and "![" not in row, row


def test_select_untracked_shows_the_landing_that_landed():
    a, b = _metric({"repo": ["a.txt"]}, landing="req_A"), _metric({"repo": ["b.txt"]}, landing="req_B")
    consumed = {"kind": "auth_consumed", "event_id": "c1", "payload": {"request_id": "req_A"}}
    manual = _events(a, b, consumed, land={"auth_consumed_event_id": "c1"})
    assert audit.select_untracked(manual, BLOBS.__getitem__)["files"] == {"repo": ["a.txt"]}
    first, second = (_metric({"repo": ["one.txt"]}, landing="att1"),
                     _metric({"repo": ["two.txt"]}, landing="att2"))
    normal = _events(first, second, land={"stage_attempt_event_id": "att1"})
    assert audit.select_untracked(normal, BLOBS.__getitem__)["files"] == {"repo": ["one.txt"]}
    before = _events(first, second)[:-1]                     # no land yet: the latest record
    assert audit.select_untracked(before, BLOBS.__getitem__)["files"] == {"repo": ["two.txt"]}
    forged = _metric({}, landing="att1", producer="hook")      # only the landing writes it
    assert audit.select_untracked(_events(first, forged), BLOBS.__getitem__)["files"] == {
        "repo": ["one.txt"]}


def test_listing_does_not_change_verdicts():
    without = _build(_events())
    listed = _build(_events(_metric({"repo": ["a", "b", "c"]})))
    assert listed["summary"] == without["summary"]          # incl. Token Cost: nothing metered
    assert listed["card_rows"] == without["card_rows"]


def test_normal_land_reports_untracked_files_after_completion(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import SWARM, Rig

    r = Rig(tmp_path, monkeypatch, profile="direct")
    try:
        r.create()
        assert r.state()["stage"] == "build"
        many = {f"build/output/generated_artifact_number_{i:03d}.txt": "x\n" for i in range(200)}
        r.edit({**r.CHANGE, "backend/core/helper.py": "def helper():\n    return 2\n",
                "backend/core/staged.py": "S = 1\n",
                "backend/core/__pycache__/mod.cpython-312.pyc": "bytecode\n", **many})
        _git(r.worktree, "add", "backend/core/staged.py")
        r._edited = True
        r.drive(until="deliver")
        r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": []}))
        worktree = r.worktree
        out = r.v1("land", "--message", "fix: swap the operands" + SWARM)
        assert out.get("ok") is True, out
        state = r.state()
        assert (state["status"], state["landing"]) == ("completed", "landed"), state
        assert not worktree.exists()
        landed = _git(r.repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        # delivered as before: the untracked file is in the commit, the ignored one is not
        assert "backend/core/helper.py" in landed and "backend/core/staged.py" in landed
        assert set(many) <= set(landed)
        assert not any("__pycache__" in name for name in landed)
        committed = (r.repo / ".aidlc" / "deliverables" / r.run_id / "REPORT.md").read_text()
        final = (r.engine().run_dir / "REPORT.md").read_text()
        for report in (committed, final):
            assert HEADING in report, report
            assert "- repo: backend/core/helper.py" in report
            assert "- repo: 181 more in run_audit.json" in report
            assert "staged.py" not in report and "\\_\\_pycache\\_\\_" not in report
        value = json.loads((r.engine().run_dir / "run_audit.json").read_text())
        assert value["audit_through"] == "COMPLETED"
        assert value["details"]["untracked_in_delivery"]["files"] == {
            "repo": ["backend/core/helper.py", *sorted(many)]}
    finally:
        r.close()


def test_run_land_reports_untracked_files(tmp_path, monkeypatch):
    from core.run_protocol import landing, request_ctx
    from core.run_protocol.engine import RunEngine
    from tests.test_pipeline_v1_rehearsal import _measured

    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = _repo(tmp_path, {"a.py": "value = 1\n", "tests/test_x.py": "def test_value(): pass\n"})
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P", profile="direct",
                              requirement="test change", session_id="s",
                              repos={"repo": str(repo)}, run_id="run_one")
    wt = Path(engine.ensure_stage_worktrees()["repo"])
    _write(wt, "a.py", "value = 2\n")
    _write(wt, "notes.txt", "left behind\n")
    report = {"reports": [{"repo": "repo", "commands": [
        {"command": "python3 -m pytest tests/test_x.py", "cwd": ".", "result": "pass"}],
        "tests_changed": ["tests/test_x.py"], "rationale": "test_value covers it"}]}
    assert engine.stage_complete(report)["state"]["stage"] == "review"
    assert engine.review_start()["reviewers"] == []
    assert engine.stage_complete()["state"]["stage"] == "test"
    assert engine.stage_complete(report)["state"]["stage"] == "deliver"
    prepared = engine.prepare_land_request(action="land", note="approved test")
    request_id = prepared["prepared"]["request"]["request_id"]
    landing.approve_from_event(engine.store, request_id, now=time.time(),
                               at="2026-10-23T00:00:00Z")
    with request_ctx.active(request_ctx.Request(session_id="s")):
        engine.execute_land_request(
            request_id, message="feat: value\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert (repo / "notes.txt").read_text() == "left behind\n"      # delivered as before
    committed = (repo / ".aidlc/deliverables/run_one/REPORT.md").read_text()
    assert HEADING in committed and "- repo: notes.txt" in committed
    _measured(engine)
    final = engine.reflect_complete()
    assert final["status"] == "completed"
    value = json.loads((engine.run_dir / "run_audit.json").read_text())
    assert value["audit_through"] == "COMPLETED"
    assert value["details"]["untracked_in_delivery"]["files"] == {"repo": ["notes.txt"]}
    assert "- repo: notes.txt" in (engine.run_dir / "REPORT.md").read_text()
