"""Concurrent isolation acceptance for Pipeline v1 (v13 §D4).

Methodology: two real engines/worktrees on one repo plus concurrent event writes.
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

from core.run_protocol import auth, git_tree
from core.run_protocol.engine import RunEngine


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q"); _git(repo, "config", "user.email", "a@b.c"); _git(repo, "config", "user.name", "T")
    (repo / "same.py").write_text("value='base'\n")
    (repo / "validations.json").write_text(json.dumps({"schema":1,"entries":[{
        "id":"none","paths":["docs/**"],"cwd":".","argv":["true"],"call_type":"lint"}]}))
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    return repo


def _engines(tmp_path, monkeypatch):
    data, ws = tmp_path / "data", tmp_path / "ws"; (ws / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data)); monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "wts")); monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = _repo(tmp_path)
    engines = [RunEngine.create(data_dir=data, workspace=ws, project="P", profile="direct", requirement="test change",
                session_id=f"s{i}", repos={"repo":str(repo)}, run_id=f"run_{i}") for i in (1,2)]
    for engine in engines: engine.ensure_worktrees()
    return repo, engines


def test_two_runs_editing_same_file_have_distinct_trees_and_events(tmp_path, monkeypatch):
    repo, (a, b) = _engines(tmp_path, monkeypatch)
    roots = [engine.effective_repos()["repo"] for engine in (a,b)]
    for root, value in zip(roots, ("a", "b")):
        __import__("pathlib").Path(root, "same.py").write_text(f"value='{value}'\n")
    trees = [git_tree.write_tree(root, (f".aidlc/deliverables/{engine.run_id}/",), {})
             for root, engine in zip(roots, (a,b))]
    assert trees[0] != trees[1]
    assert (repo / "same.py").read_text() == "value='base'\n"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: pair[0].store.append_new(
            writer=pair[0].authority.writer("cli"), kind="turn", at="now",
            payload={"message_id": pair[1]}), [(a,"a"),(b,"b")]))
    assert {row["run_id"] for row in a.store.read_events()} == {"run_1"}
    assert {row["run_id"] for row in b.store.read_events()} == {"run_2"}


def test_shared_checkout_changes_never_enter_isolated_tree(tmp_path, monkeypatch):
    repo, (engine, _other) = _engines(tmp_path, monkeypatch)
    root = engine.effective_repos()["repo"]
    before = git_tree.write_tree(root, (f".aidlc/deliverables/{engine.run_id}/",), {})
    (repo / "same.py").write_text("value='shared'\n")
    after = git_tree.write_tree(root, (f".aidlc/deliverables/{engine.run_id}/",), {})
    assert before == after


def test_authorization_cannot_cross_run_or_tree():
    oracle = auth.AuthorizationOracle()
    bindings = {"repo":{"code_tree":"1"*40,"delivery_tree":"2"*40,"report_sha":"3"*64}}
    request = auth.make_request(request_id="q", session_id="s1", run_id="run_1",
                                action="land", bindings=bindings)
    oracle.issue(request, auth_event_id="a"*64, decided_at=0, expires_at=100)
    assert not oracle.consume(request_id="q", session_id="s1", run_id="run_2",
                              action="land", bindings=bindings, now=1).ok
    changed = {"repo":{**bindings["repo"],"code_tree":"9"*40}}
    assert not oracle.consume(request_id="q", session_id="s1", run_id="run_1",
                              action="land", bindings=changed, now=1).ok


