"""Direct-profile vertical test for Pipeline v1 RunEngine.

Methodology: temporary repo/workspace/data roots, real linked worktree, sealed runner
calls/final, event lifecycle, commit-tree landing, and reflection completion.
"""
from __future__ import annotations

import json
import subprocess

from core import hook_token
from core.run_protocol import git_tree
from core.run_protocol.engine import RunEngine
from core.run_protocol.runner import V1Runner


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _documents():
    requirements = {"requirements": [{"id": "R1", "acceptance": [{
        "id": "R1.1", "pattern": "event", "condition": "call runs",
        "response": "return value", "negative": False, "universal": False}]}]}
    design = {"tasks": [{"id": "T1", "file": "a.py", "symbol": "value",
                         "change": "return new value", "requirements": ["R1.1"],
                         "depends_on": [], "creates_test_files": []}],
              "exit_criteria": [{"id": "X1", "kind": "test",
                                 "ref": "tests/test_x.py::test_value", "covers": ["R1.1"]}],
              "no_contract_change_reason": "internal value only"}
    return {"requirements": requirements, "design": design}


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


def _runner(engine, wt):
    return V1Runner(run_dir=engine.run_dir, repo_root=wt, repo_name="repo",
                    run_id=engine.run_id, base=_git(wt, "rev-parse", "HEAD"),
                    code_tree=git_tree.write_tree(str(wt),
                        (f".aidlc/deliverables/{engine.run_id}/",), {}),
                    contract=engine.contract, python="python3")


def _finalize(runner, target):
    call = runner.record_call(
        call_type="test", argv=["python3", "-c", "pass"],
        tool_version="python3", target="tests/test_x.py::test_value")
    selection = {
        "files": ["tests/test_x.py"],
        "nodeids": ["tests/test_x.py::test_value"],
        "summary": {"selected": 1, "mode": target},
        "dependency_scope": {"tests/test_x.py::test_value": ["a.py"]},
        "test_dependencies": {"tests/test_x.py::test_value": ["a.py"]},
        "risk_rules": [], "rules_version": "1", "mode": target,
        "risk_status": "within_cap",
    }
    return runner.finalize(
        changed_paths=["a.py"], runner_code_hash="f" * 64,
        pipeline_code_hash=runner.contract["pipeline_code_hash"],
        runner_argv=["pipeline_test.py", "--mode", target],
        selection=selection, cache_keys=[],
        main_red={"sha": "e" * 64, "generated_at": "now"},
        trace=[{"exit": "X1", "kind": "test", "covers": ["R1.1"],
                "ref": "tests/test_x.py::test_value", "result": "pass",
                "seq": call["seq"]}])


def test_direct_run_reaches_landed_completed(tmp_path, monkeypatch):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = _repo(tmp_path)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", session_id="s", repos={"repo": str(repo)},
                              run_id="run_one", risk_test_file_cap=20)
    assert engine.projection()["stage"] == "plan"
    started = engine.stage_start()
    wt = started["repos"]["repo"]
    ( __import__("pathlib").Path(wt) / "a.py").write_text("value = 2\n")
    assert engine.stage_complete(_documents())["state"]["stage"] == "build"

    runner = _runner(engine, wt); _finalize(runner, "tdd")
    assert engine.stage_complete()["state"]["stage"] == "review"
    assert engine.review_start()["reviewers"] == []
    assert engine.stage_complete()["state"]["stage"] == "test"

    runner = _runner(engine, wt); _finalize(runner, "regression")
    assert engine.stage_complete()["state"]["stage"] == "deliver"
    prepared = engine.prepare_land_request(action="land", note="approved test")
    request_id = prepared["prepared"]["request"]["request_id"]
    hook_token.stop_oracle()
    assert hook_token.start_oracle(52000 + __import__("os").getpid() % 1000) \
        or not hook_token.oracle_supported()
    try:
        token = hook_token.issue(
            engine.run_dir, "s", "run_one", f"run-land:{request_id}")
        assert token
        monkeypatch.setenv(hook_token.TOKEN_ENV, token)
        landed = engine.execute_land_request(
            request_id,
            message="feat: value\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    finally:
        hook_token.stop_oracle()
        monkeypatch.delenv(hook_token.TOKEN_ENV, raising=False)
    assert landed["state"]["status"] == "reflecting"
    assert (repo / "a.py").read_text() == "value = 2\n"
    assert (repo / ".aidlc/deliverables/run_one/REPORT.md").is_file()
    final = engine.reflect_complete()
    assert final["status"] == "completed" and final["landing"] == "human_override"
    audit = json.loads((engine.run_dir / "run_audit.json").read_text())
    assert audit["audit_through"] == "COMPLETED" and audit["card"].startswith("Run Audit")
    assert audit["summary"]["isolation"]["verdict"] == "PASS"
    assert "Audit through COMPLETED" in (engine.run_dir / "REPORT.md").read_text()

    assert all(row["verdict"] != "BLOCK" for row in audit["summary"].values())

def test_worktree_creation_failure_pauses_without_shared_fallback(tmp_path, monkeypatch):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    repo = _repo(tmp_path)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", session_id="s", repos={"repo": str(repo)},
                              run_id="run_pause")
    from core import run_worktree
    def fail(*args, **kwargs):
        raise run_worktree.WorktreeError("create_failed", "no space")
    monkeypatch.setattr(run_worktree, "create", fail)
    import pytest
    with pytest.raises(Exception, match="worktree unavailable"):
        engine.stage_start()
    state = engine.projection()
    assert state["status"] == "paused" and state["reason"] == "worktree_unavailable"
    assert engine.contract["mode"] == "isolated" and engine.effective_repos() == {}


def test_a_small_change_that_triggers_review_patterns_gets_correctness(tmp_path, monkeypatch):
    """v13 E1: the RP lens is part of correctness. A 2-line change whose diff hits a
    review pattern (here subprocess.Popen, RP1) still gets a correctness review, and
    its handout carries the engine-selected RP rows; a plain one still gets none."""
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = _repo(tmp_path)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", session_id="s", repos={"repo": str(repo)},
                              run_id="run_rp", risk_test_file_cap=20)
    wt = engine.stage_start()["repos"]["repo"]
    (__import__("pathlib").Path(wt) / "a.py").write_text(
        "import subprocess\nvalue = subprocess.Popen(['true'])\n")
    engine.stage_complete(_documents())
    _finalize(_runner(engine, wt), "tdd")
    assert engine.stage_complete()["state"]["stage"] == "review"
    started = engine.review_start()
    assert [r["id"] for r in started["reviewers"]] == ["correctness"]
    assert "RP1" in started["reviewers"][0]["reason"]
    prompt = started["handouts"][0]["prompt"]
    assert "REVIEW PATTERNS (RP) for this change" in prompt
    assert "| RP1 |" in prompt and "--patterns-rows" in prompt


def test_a_failed_pattern_selection_still_dispatches_correctness(tmp_path, monkeypatch):
    from core.run_protocol import review
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    monkeypatch.setattr(review, "review_patterns",
                        lambda paths, diff="": {"compact": "", "triggered": [], "error": "OSError: gone"})
    repo = _repo(tmp_path)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", session_id="s", repos={"repo": str(repo)},
                              run_id="run_rp2", risk_test_file_cap=20)
    wt = engine.stage_start()["repos"]["repo"]
    (__import__("pathlib").Path(wt) / "a.py").write_text("value = 2\n")
    engine.stage_complete(_documents())
    _finalize(_runner(engine, wt), "tdd")
    engine.stage_complete()
    started = engine.review_start()
    assert [r["id"] for r in started["reviewers"]] == ["correctness"]
    assert "could not select rows" in started["handouts"][0]["prompt"]
