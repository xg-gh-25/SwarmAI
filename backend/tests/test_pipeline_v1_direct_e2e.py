"""Direct-profile vertical test for Pipeline v1 RunEngine.

Methodology: temporary repo/workspace/data roots, real linked worktree, the agent's
validation reports for BUILD / TEST (S6-1), event lifecycle, commit-tree landing, and
reflection completion.
"""
from __future__ import annotations

import json
import subprocess

from core.run_protocol.engine import RunEngine


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q"); _git(repo, "config", "user.email", "a@b.c"); _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("value = 1\n")
    (repo / "tests").mkdir(); (repo / "tests/test_x.py").write_text("def test_value(): pass\n")
    (repo / "validations.json").write_text(json.dumps({"schema": 1, "entries": [{
        "id": "docs", "paths": ["docs/**"], "cwd": ".",
        "argv": ["python", "-c", "pass"], "call_type": "lint"}]}))
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    return repo


def _report(result="pass"):
    cmd = {"command": "python3 -m pytest tests/test_x.py", "cwd": ".", "result": result}
    if result != "pass":
        cmd["reason"] = "assertion failed"
    return {"reports": [{"repo": "repo", "commands": [cmd], "tests_changed": ["tests/test_x.py"],
                         "rationale": "test_value covers the change"}]}


def test_direct_run_reaches_landed_completed(tmp_path, monkeypatch):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = _repo(tmp_path)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", requirement="test change", session_id="s", repos={"repo": str(repo)},
                              run_id="run_one")
    assert engine.projection()["stage"] == "build"      # direct has no PLAN
    wt = engine.ensure_stage_worktrees()["repo"]
    ( __import__("pathlib").Path(wt) / "a.py").write_text("value = 2\n")

    assert engine.stage_complete()["state"]["stage"] == "build"   # no report: BX-3
    assert engine.stage_complete(_report())["state"]["stage"] == "review"
    assert engine.review_start()["reviewers"] == []
    assert engine.stage_complete()["state"]["stage"] == "test"

    failed = engine.stage_complete(_report("fail"))       # a failed command blocks TEST
    assert failed["ok"] is False and failed["state"]["stage"] == "test"
    assert engine.stage_complete(_report())["state"]["stage"] == "deliver"
    prepared = engine.prepare_land_request(action="land", note="approved test")
    request_id = prepared["prepared"]["request"]["request_id"]
    from core.run_protocol import landing, request_ctx
    # the daemon's hook records the person's approval; the CLI then lands as the owner
    landing.approve_from_event(engine.store, request_id,
                               now=__import__("time").time(), at="2026-10-23T00:00:00Z")
    with request_ctx.active(request_ctx.Request(session_id="s")):
        landed = engine.execute_land_request(
            request_id, message="feat: value\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert landed["state"]["status"] == "reflecting"
    assert (repo / "a.py").read_text() == "value = 2\n"
    assert (repo / ".aidlc/deliverables/run_one/REPORT.md").is_file()
    from tests.test_pipeline_v1_rehearsal import _measured
    _measured(engine)      # measure-usage (D3): an unmeasured run is a Token Cost BLOCK
    final = engine.reflect_complete()
    assert final["status"] == "completed" and final["landing"] == "human_override"
    audit = json.loads((engine.run_dir / "run_audit.json").read_text())
    assert audit["audit_through"] == "COMPLETED" and audit["card"].startswith("Run Audit")
    assert audit["summary"]["isolation"]["verdict"] == "PASS"
    final_report = (engine.run_dir / "REPORT.md").read_text()
    assert "Final audit through COMPLETED" in final_report
    # the person's request survives the COMPLETED rebuild (derived from signed events)
    assert f"人工落地：request {request_id}" in final_report

    assert all(row["verdict"] != "BLOCK" for row in audit["summary"].values())

def test_worktree_creation_failure_pauses_without_shared_fallback(tmp_path, monkeypatch):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    repo = _repo(tmp_path)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", requirement="test change", session_id="s", repos={"repo": str(repo)},
                              run_id="run_pause")
    from core import run_worktree
    def fail(*args, **kwargs):
        raise run_worktree.WorktreeError("create_failed", "no space")
    monkeypatch.setattr(run_worktree, "create", fail)
    import pytest
    with pytest.raises(Exception, match="worktree unavailable"):
        engine.ensure_stage_worktrees()
    state = engine.projection()
    assert state["status"] == "paused" and state["reason"] == "worktree_unavailable"
    assert engine.contract["mode"] == "isolated" and engine.effective_repos() == {}


def test_a_small_change_that_triggers_review_patterns_gets_correctness(tmp_path, monkeypatch):
    """v13 E1 / S6-4b: a 2-line change whose diff hits a row of the repo's own
    .swarmai/review.md (here subprocess.Popen) still gets a correctness review, and
    its handout carries the engine-selected rows."""
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = _repo(tmp_path)
    (repo / ".swarmai").mkdir()
    (repo / ".swarmai/review.md").write_text(
        "| # | Pattern | Trigger | Verify | Example |\n|---|---|---|---|---|\n"
        "| RP1 | **subprocess timeout orphan** | Popen `match: glob:*.py kw:popen` | killed | "
        "ffmpeg runs forever |\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "review guidance")
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", requirement="test change", session_id="s", repos={"repo": str(repo)},
                              run_id="run_rp")
    wt = engine.ensure_stage_worktrees()["repo"]
    (__import__("pathlib").Path(wt) / "a.py").write_text(
        "import subprocess\nvalue = subprocess.Popen(['true'])\n")
    assert engine.stage_complete(_report())["state"]["stage"] == "review"
    started = engine.review_start()
    assert [r["id"] for r in started["reviewers"]] == ["correctness"]
    assert "RP1" in started["reviewers"][0]["reason"]
    prompt = __import__("pathlib").Path(started["handouts"][0]["path"]).read_text(encoding="utf-8")
    assert "REPOSITORY REVIEW PATTERNS" in prompt
    assert "| RP1 |" in prompt and ".swarmai/review.md" in prompt


def test_a_failed_pattern_selection_still_dispatches_correctness(tmp_path, monkeypatch):
    from core.run_protocol import review
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    monkeypatch.setattr(review, "review_patterns", lambda repos: {
        "blocks": {"repo": "repo: the engine could not read the pattern table"},
        "triggered": [], "security": [], "error": "repo: OSError: gone"})
    repo = _repo(tmp_path)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", requirement="test change", session_id="s", repos={"repo": str(repo)},
                              run_id="run_rp2")
    wt = engine.ensure_stage_worktrees()["repo"]
    (__import__("pathlib").Path(wt) / "a.py").write_text("value = 2\n")
    engine.stage_complete(_report())
    started = engine.review_start()
    assert [r["id"] for r in started["reviewers"]] == ["correctness"]
    assert "could not read the pattern table" in __import__("pathlib").Path(started["handouts"][0]["path"]).read_text(encoding="utf-8")
