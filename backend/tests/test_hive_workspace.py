"""A dispatched task's workspace, prepared by the Hive's backend (core/hive_workspace,
design docs/design/2026-10-30-run-spec.md §4.3, invariant 5).

Real git, temporary repositories: a bare "remote" stands for the repository's URL
(``url.insteadOf`` in the config the module's git reads). Questions: (1) do two tasks on
one repository get their own worktree on their own branch at the host's commit, with the
shared clone untouched and each task's commits its own; (2) is a read-only repository a
detached worktree that is never bundled; (3) does a task with no repository get only its
outputs directory; (4) does a commit the Hive cannot find fail the order and leave
nothing; (5) does a filter driver or hook the agent wrote into the shared clone's config
run in the daemon at the next prepare (it must not); (6) is release refused while the
session runs, and does it remove only the order's directories, keeping the branch; does
the sweep release an old order; (7) does a whole-branch bundle stand in for an
unreachable remote; (8) does the task text name the worktrees and no git command;
(9) do the Hive routes answer only on a Hive, with the fixed reason.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from core import hive_bundle, hive_repo_inbox as hi, hive_workspace as hw, repo_ship, task_repos

URL = "https://github.com/acme/lib.git"
URL2 = "https://github.com/acme/docs.git"           # the same remote under a second name
W1, W2 = "so_" + "a" * 12, "so_" + "b" * 12


def _g(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd),
                          capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    _g(repo, "add", name)
    _g(repo, "commit", "-qm", name)
    return _g(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """remote (bare) with A pushed; host checkout at C (B, C not pushed)."""
    remote = tmp_path / "remote.git"
    _g(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f'[url "{remote}"]\n\tinsteadOf = {URL}\n\tinsteadOf = {URL2}\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setattr(hw, "GLOBAL_CONFIG", str(gitconfig))
    host = tmp_path / "host"
    _g(tmp_path, "clone", "-q", URL, str(host))
    _g(host, "checkout", "-q", "-b", "main")
    a = _commit(host, "a")
    _g(host, "push", "-q", "origin", "main")
    _commit(host, "b")
    c = _commit(host, "c")
    ws = tmp_path / "hive"
    ws.mkdir()
    return {"remote": remote, "host": host, "ws": ws, "a": a, "c": c, "gitconfig": gitconfig}


def _ship(w, full=False) -> str:
    st = repo_ship.state(w["host"])
    path, _base, _ = repo_ship.pack(w["host"], URL, st["branch"], st["sha"], full)
    if path is not None:
        inbox = hi.Inbox(w["ws"], st["sha"])
        inbox.write(path.read_bytes())
        inbox.commit()
        path.unlink()
    return st["sha"]


def _repo(sha, access="write", name="lib", url=URL):
    return {"url": url, "ref": "main", "sha": sha, "name": name, "access": access}


def _facts(world, monkeypatch, order):
    monkeypatch.delenv("GIT_CONFIG_GLOBAL")           # facts read the origin as configured
    return task_repos.repo_facts(str(world["ws"]), task_repos.parse(hw.task_repos_of(order)))


def test_two_tasks_on_one_repository_each_work_on_their_own_branch(world, monkeypatch):
    sha = _ship(world)
    o1 = hw.prepare(world["ws"], W1, [_repo(sha)])
    o2 = hw.prepare(world["ws"], W2, [_repo(sha)])
    clone = world["ws"] / task_repos.repo_dir(URL)
    shared_head, shared_branches = _g(clone, "rev-parse", "HEAD"), _g(clone, "branch", "--list", "main")
    t1, t2 = world["ws"] / o1["repos"][0]["dir"], world["ws"] / o2["repos"][0]["dir"]
    assert o1["repos"][0]["dir"] == f"Hive/work/{W1}/repos/lib"
    for t, w in ((t1, W1), (t2, W2)):
        assert _g(t, "rev-parse", "HEAD") == sha
        assert _g(t, "symbolic-ref", "--short", "HEAD") == f"hive/{w}"
        assert (t / "c").read_text() == "c"
    x = _commit(t1, "only-one")
    _commit(t2, "only-two")
    assert _g(clone, "rev-parse", "HEAD") == shared_head and _g(clone, "branch", "--list", "main") == shared_branches
    facts = _facts(world, monkeypatch, o1)
    assert (facts[0]["error"], facts[0]["base"], facts[0]["commits"]) == (None, sha, [x])
    assert facts[0]["access"] == "write"


def test_a_read_only_repository_is_detached_and_never_bundled(world, monkeypatch):
    sha = _ship(world)
    order = hw.prepare(world["ws"], W1, [_repo(sha, "read")])
    t = world["ws"] / order["repos"][0]["dir"]
    assert order["repos"][0]["branch"] is None
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=t).returncode != 0   # detached
    _commit(t, "not-coming-back")
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    w = hw.prepare(world["ws"], W2, [_repo(sha, "write")])
    tw = world["ws"] / w["repos"][0]["dir"]
    _commit(tw, "coming-back")
    fact = _facts(world, monkeypatch, order)[0]
    assert fact["commits_total"] == 1
    assert hive_bundle.make(world["ws"], "sess-0000000001", fact) is None
    wf = task_repos.repo_facts(str(world["ws"]), task_repos.parse(hw.task_repos_of(w)))[0]
    made = hive_bundle.make(world["ws"], "sess-0000000002", wf)
    assert made and made.get("tmp") and not made.get("error")          # the writable one is bundled


def test_a_task_with_no_repository_gets_only_its_outputs_directory(world):
    order = hw.prepare(world["ws"], W1, [])
    assert order["repos"] == [] and order["outputs"] == f"Outputs/{W1}"
    assert (world["ws"] / "Outputs" / W1).is_dir()
    assert sorted(p.name for p in (world["ws"] / "Hive" / "work" / W1).iterdir()) == ["order.json", "repos"]
    assert hw.load(world["ws"], W1)["work_id"] == W1


def test_a_commit_the_hive_cannot_find_fails_and_leaves_nothing(world):
    good = _ship(world)
    missing = "f" * 40
    with pytest.raises(hw.WorkError, match="other: the commit is not in the repository"):
        hw.prepare(world["ws"], W1, [_repo(good), _repo(missing, name="other", url=URL2)])
    assert not (world["ws"] / "Hive" / "work" / W1).exists()
    assert not (world["ws"] / "Outputs" / W1).exists()
    clone = world["ws"] / task_repos.repo_dir(URL)
    assert _g(clone, "branch", "--list", f"hive/{W1}") == ""          # the branch made for it is gone
    assert _g(clone, "worktree", "list").count("\n") == 0              # only the clone itself
    with pytest.raises(hw.WorkError):
        hw.prepare(world["ws"], W1, [_repo(good), _repo(good)])         # a repeated entry
    with pytest.raises(hw.WorkError):
        hw.prepare(world["ws"], "../x", [])


def test_what_the_agent_wrote_into_the_shared_config_never_runs_in_the_daemon(world, tmp_path):
    sha = _ship(world)
    order = hw.prepare(world["ws"], W1, [_repo(sha)])
    t = world["ws"] / order["repos"][0]["dir"]
    marker = tmp_path / "ran"
    # the agent, in its worktree: a filter driver and a hook in the shared config, a
    # committed attribute that names the filter, and the same in its global config
    _g(t, "config", "filter.evil.smudge", f"touch {marker}; cat")
    _g(t, "config", "core.hooksPath", str(tmp_path))
    (tmp_path / "post-checkout").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (tmp_path / "post-checkout").chmod(0o755)
    (t / ".gitattributes").write_text("* filter=evil\n")
    _g(t, "add", ".gitattributes")
    _g(t, "commit", "-qm", "attrs")
    evil_sha = _g(t, "rev-parse", "HEAD")
    # the agent's own ~/.gitconfig (the environment's global config; the module reads
    # hw.GLOBAL_CONFIG, which in production is /dev/null and here only maps the URL)
    agent_global = tmp_path / "agent-gitconfig"
    agent_global.write_text(world["gitconfig"].read_text() + f'[filter "evil"]\n\tsmudge = touch {marker}; cat\n')
    os.environ["GIT_CONFIG_GLOBAL"] = str(agent_global)
    assert not marker.exists()                         # nothing so far ran it
    o2 = hw.prepare(world["ws"], W2, [{**_repo(evil_sha, "read")}])
    assert not marker.exists()
    assert (world["ws"] / o2["repos"][0]["dir"] / ".gitattributes").read_text() == "* filter=evil\n"
    cfg = (world["ws"] / task_repos.repo_dir(URL) / ".git" / "config").read_text()
    assert "evil" not in cfg and "hooksPath" not in cfg and URL in cfg


def test_release_waits_for_the_session_and_removes_only_the_order(world):
    sha = _ship(world)
    order = hw.prepare(world["ws"], W1, [_repo(sha)])
    assert hw.bind(world["ws"], W1, "sess-1") and not hw.bind(world["ws"], W1, "sess-2")
    (world["ws"] / "Outputs" / W1 / "report.md").write_text("r")
    keep = world["ws"] / "Outputs" / "mine.md"
    keep.write_text("person's")
    with pytest.raises(hw.WorkError, match="still running"):
        hw.release(world["ws"], W1, lambda sid: sid == "sess-1")
    assert (world["ws"] / order["repos"][0]["dir"]).is_dir()
    assert hw.release(world["ws"], W1, lambda sid: False) is True
    clone = world["ws"] / task_repos.repo_dir(URL)
    assert not (world["ws"] / "Hive" / "work" / W1).exists() and not (world["ws"] / "Outputs" / W1).exists()
    assert keep.read_text() == "person's"
    assert _g(clone, "rev-parse", f"hive/{W1}") == sha                  # the branch stays
    assert f"Hive/work/{W1}" not in _g(clone, "worktree", "list")       # forgotten by the clone
    assert hw.release(world["ws"], W1, lambda sid: False) is False
    hw.prepare(world["ws"], W2, [])
    old = time.time() + (hw.RETENTION_DAYS + 1) * 86400
    assert hw.sweep(world["ws"], lambda sid: False, now=time.time()) == 0
    assert hw.sweep(world["ws"], lambda sid: False, now=old) == 1
    assert not (world["ws"] / "Hive" / "work" / W2).exists()


def test_a_whole_branch_bundle_stands_in_for_an_unreachable_remote(world):
    sha = _ship(world, full=True)
    world["gitconfig"].write_text(f'[url "{world["ws"]}/nowhere.git"]\n\tinsteadOf = {URL}\n')
    order = hw.prepare(world["ws"], W1, [_repo(sha)])
    t = world["ws"] / order["repos"][0]["dir"]
    assert _g(t, "rev-parse", "HEAD") == sha
    assert _g(world["ws"] / task_repos.repo_dir(URL), "config", "--get", "remote.origin.url") == URL


def test_the_task_text_names_the_worktrees_and_no_git_command(world):
    sha = _ship(world)
    order = hw.prepare(world["ws"], W1, [_repo(sha), _repo(sha, "read", name="docs", url=URL2)])
    text = task_repos.task_text(order)
    assert f"Hive/work/{W1}/repos/lib: lib at {sha[:12]} on branch hive/{W1}" in text
    assert f"Hive/work/{W1}/repos/docs: docs at {sha[:12]}, read-only" in text
    assert f"Outputs/{W1}/" in text
    assert "git " not in text and "clone " not in text.replace("Do not clone", "")
    assert task_repos.task_text(None) == ""


def test_parse_takes_a_work_orders_worktree_and_nothing_else(world):
    ok = task_repos.parse([{"url": URL, "ref": "main", "dir": f"Hive/work/{W1}/repos/lib",
                            "name": "lib", "access": "read"}])
    assert ok[0]["dir"] == f"Hive/work/{W1}/repos/lib" and ok[0]["access"] == "read"
    for bad in ({"dir": "Hive/work/so_x/repos/lib"}, {"dir": f"Hive/work/{W1}/repos/../x"},
                {"dir": f"Hive/work/{W1}/repos/lib", "access": "all"},
                {"dir": f"Hive/work/{W1}/repos/lib", "name": "other"}):
        with pytest.raises(task_repos.TaskRepoError):
            task_repos.parse([{"url": URL, "ref": "main", "name": "lib", "access": "read", **bad}])


def test_the_hive_routes(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import hive_brains
    app = FastAPI()
    app.include_router(hive_brains.router, prefix="/api/hive")
    client = TestClient(app)
    monkeypatch.setattr(hive_brains, "_workspace", lambda: world["ws"])
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    assert client.post("/api/hive/work", json={"work_id": W1, "repos": []}).status_code == 404
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    r = client.post("/api/hive/work", json={"work_id": W1, "repos": []})
    assert r.status_code == 200 and r.json()["outputs"] == f"Outputs/{W1}"
    r = client.post("/api/hive/work", json={"work_id": W1, "repos": []})
    assert (r.status_code, r.json()["detail"]) == (422, hw.ERR_EXISTS)
    hw.bind(world["ws"], W1, "sess-1")
    monkeypatch.setattr(hive_brains, "session_busy", lambda sid: True)
    assert client.post(f"/api/hive/work/{W1}/release").status_code == 409
    monkeypatch.setattr(hive_brains, "session_busy", lambda sid: False)
    assert client.post(f"/api/hive/work/{W1}/release").json() == {"released": True}


@pytest.mark.asyncio
async def test_a_session_starts_only_on_a_prepared_order_and_is_bound_to_it(async_client, tmp_path, monkeypatch):
    from routers import chat as chat_router
    import core.project_registry as preg
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(preg, "get_swarmws", lambda: ws)
    recorded = {}

    async def record(sid, repos):
        recorded[sid] = repos
    monkeypatch.setattr(chat_router, "_record_task_repos", record)
    ran = []

    class _Router:
        async def run_conversation(self, **kw):
            ran.append(kw)
            yield {"type": "session_start", "sessionId": "s-stub"}
            yield {"type": "result", "sessionId": "s-stub"}
    monkeypatch.setattr(chat_router, "_get_router", lambda: _Router())
    body = {"agent_id": "default", "message": "hi", "work_id": W1}
    r = await async_client.post("/api/chat/stream", json=body)
    assert r.status_code in (400, 422) and not ran                     # no such order here
    hw.prepare(ws, W1, [])
    r = await async_client.post("/api/chat/stream", json=body)
    assert r.status_code == 200 and len(ran) == 1
    assert hw.load(ws, W1)["session_id"] == "s-stub"
    assert recorded == {} or recorded == {"s-stub": []}                 # no repository: nothing to record
    r = await async_client.post("/api/chat/stream", json=body)
    assert r.status_code in (400, 422) and len(ran) == 1               # an order runs one session


def test_a_hives_review_reads_the_newest_worktree_of_a_repository(world, monkeypatch):
    sha = _ship(world)
    assert hw.latest_worktree(world["ws"], URL) is None
    hw.prepare(world["ws"], W1, [_repo(sha)])
    o2 = hw.prepare(world["ws"], W2, [_repo(sha, "read")])
    order = hw.load(world["ws"], W2)
    order["created_at"] = "2099-01-01T00:00:00Z"                     # the newer one
    (world["ws"] / "Hive" / "work" / W2 / "order.json").write_text(__import__("json").dumps(order))
    assert hw.latest_worktree(world["ws"], URL) == o2["repos"][0]["dir"]
    assert hw.latest_worktree(world["ws"], "https://github.com/acme/other.git") is None
    hw.release(world["ws"], W2, lambda sid: False)
    assert hw.latest_worktree(world["ws"], URL) == f"Hive/work/{W1}/repos/lib"
