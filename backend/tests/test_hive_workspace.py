"""A dispatched task's workspace, prepared by the Hive's backend (core/hive_workspace,
design docs/design/2026-10-30-run-spec.md §4.3, invariant 5).

Real git, temporary repositories: a bare "remote" stands for the repository's URL
(``url.insteadOf`` in the config the module's git reads). Questions: (1) do two tasks on
one repository get their own clone on their own branch at the host's commit, with the
mirror untouched and each task's commits its own; (2) is a read-only repository a
detached clone that is never bundled; (3) does a task with no repository get only its
outputs directory; (4) does a commit the Hive cannot find fail the order and leave
nothing; (5) does what the agent configures (its clone's config, its global config, a
``commondir`` planted in a mirror) run in or steer the daemon's git (it must not);
(6) is release refused while the session runs, and does it remove only the order's
files; does the sweep release an order idle for 30 days and keep one in use; (7) does a
whole-branch bundle stand in for an unreachable remote; (8) does the task text name the
clones and no git command; (9) do the Hive routes answer only on a Hive, with the fixed
reason; (10) does an order start one session only.
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
    mirror = hw._state() / "mirrors" / hw._mirror_rel(URL)
    refs = _g(mirror, "for-each-ref", "--format=%(refname) %(objectname)")
    t1, t2 = world["ws"] / o1["repos"][0]["dir"], world["ws"] / o2["repos"][0]["dir"]
    assert (t1 / ".git").is_dir() and not (t1 / ".git" / "commondir").exists()   # its own .git
    assert o1["repos"][0]["dir"] == f"Hive/work/{W1}/repos/lib"
    for t, w in ((t1, W1), (t2, W2)):
        assert _g(t, "rev-parse", "HEAD") == sha
        assert _g(t, "symbolic-ref", "--short", "HEAD") == f"hive/{w}"
        assert (t / "c").read_text() == "c"
    x = _commit(t1, "only-one")
    _commit(t2, "only-two")
    assert _g(mirror, "for-each-ref", "--format=%(refname) %(objectname)") == refs
    assert subprocess.run(["git", "cat-file", "-e", x], cwd=t2).returncode != 0   # not in the other task's
    assert _g(t1, "config", "--get", "remote.origin.url") == URL
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
    assert sorted(p.name for p in (world["ws"] / "Hive" / "work" / W1).iterdir()) == ["repos"]
    assert hw.load(world["ws"], W1)["work_id"] == W1


def test_a_commit_the_hive_cannot_find_fails_and_leaves_nothing(world):
    good = _ship(world)
    missing = "f" * 40
    with pytest.raises(hw.WorkError, match="other: the commit is not in the repository"):
        hw.prepare(world["ws"], W1, [_repo(good), _repo(missing, name="other", url=URL2)])
    assert not (world["ws"] / "Hive" / "work" / W1).exists()
    assert not (world["ws"] / "Outputs" / W1).exists()
    assert hw.load(world["ws"], W1) is None and not hw._order_path(W1).exists()
    with pytest.raises(hw.WorkError):
        hw.prepare(world["ws"], W1, [_repo(good), _repo(good)])         # a repeated entry
    with pytest.raises(hw.WorkError):
        hw.prepare(world["ws"], "../x", [])


def test_what_the_agent_configures_never_runs_in_or_steers_the_daemons_git(world, tmp_path):
    sha = _ship(world)
    order = hw.prepare(world["ws"], W1, [_repo(sha)])
    t = world["ws"] / order["repos"][0]["dir"]
    marker = tmp_path / "ran"
    # the agent, in its clone: a filter driver and a hook in its config, a committed
    # attribute that names the filter, and the same filter in its global config
    _g(t, "config", "filter.evil.smudge", f"touch {marker}; cat")
    _g(t, "config", "core.hooksPath", str(tmp_path))
    (tmp_path / "post-checkout").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (tmp_path / "post-checkout").chmod(0o755)
    (t / ".gitattributes").write_text("* filter=evil\n")
    _g(t, "add", ".gitattributes")
    _g(t, "commit", "-qm", "attrs")
    evil_sha = _g(t, "rev-parse", "HEAD")
    agent_global = tmp_path / "agent-gitconfig"
    agent_global.write_text(world["gitconfig"].read_text() + f'[filter "evil"]\n\tsmudge = touch {marker}; cat\n')
    os.environ["GIT_CONFIG_GLOBAL"] = str(agent_global)    # the module reads hw.GLOBAL_CONFIG only
    _g(t, "push", "-q", str(hw._state() / "mirrors" / hw._mirror_rel(URL)), f"HEAD:refs/heads/x")
    assert not marker.exists()                              # nothing so far ran it
    o2 = hw.prepare(world["ws"], W2, [_repo(evil_sha, "read")])
    assert not marker.exists()
    assert (world["ws"] / o2["repos"][0]["dir"] / ".gitattributes").read_text() == "* filter=evil\n"
    # a mirror pointed at another repository's config or objects is refused, not used
    mirror = hw._state() / "mirrors" / hw._mirror_rel(URL)
    for planted in ("commondir", "objects/info/alternates"):
        (mirror / planted).write_text(str(t / ".git") + "\n")
        with pytest.raises(hw.WorkError, match="mirror is not one this Hive made"):
            hw.prepare(world["ws"], "so_" + "c" * 12, [_repo(sha)])
        (mirror / planted).unlink()
    assert not marker.exists()


def test_release_waits_for_the_session_and_removes_only_the_order(world, tmp_path):
    sha = _ship(world)
    order = hw.prepare(world["ws"], W1, [_repo(sha)])
    assert hw.bind(world["ws"], W1, "sess-1") and not hw.bind(world["ws"], W1, "sess-2")
    (world["ws"] / "Outputs" / W1 / "report.md").write_text("r")
    keep = world["ws"] / "Outputs" / "mine.md"
    keep.write_text("person's")
    with pytest.raises(hw.WorkError, match="still running"):
        hw.release(world["ws"], W1, lambda sid: sid == "sess-1")
    assert (world["ws"] / order["repos"][0]["dir"]).is_dir()
    other = hw.prepare(world["ws"], W2, [_repo(sha)])
    assert hw.release(world["ws"], W1, lambda sid: False) is True
    assert not (world["ws"] / "Hive" / "work" / W1).exists() and not (world["ws"] / "Outputs" / W1).exists()
    assert hw.load(world["ws"], W1) is None
    assert keep.read_text() == "person's"
    assert (world["ws"] / other["repos"][0]["dir"]).is_dir() and hw.load(world["ws"], W2)   # untouched
    assert (hw._state() / "mirrors" / hw._mirror_rel(URL)).is_dir()
    assert hw.release(world["ws"], W1, lambda sid: False) is False
    # a link on the way (here: Outputs itself) is never followed by the removal
    outside = tmp_path / "outside"
    import shutil
    shutil.move(str(world["ws"] / "Outputs"), str(outside))
    (world["ws"] / "Outputs").symlink_to(outside)
    assert (world["ws"] / "Outputs" / W2).is_dir()
    hw.release(world["ws"], W2, lambda sid: False)
    assert (outside / W2).is_dir()


def test_the_sweep_releases_an_idle_order_and_keeps_one_in_use(world):
    sha = _ship(world)
    hw.prepare(world["ws"], W1, [])
    used = hw.prepare(world["ws"], W2, [_repo(sha)])
    later = time.time() + (hw.RETENTION_DAYS + 1) * 86400
    assert hw.sweep(world["ws"], lambda sid: False, now=time.time()) == 0
    t = world["ws"] / used["repos"][0]["dir"]
    _commit(t, "still-working")
    os.utime(t / ".git" / "logs" / "HEAD", (later - 60, later - 60))   # worked on just now, then
    assert hw.sweep(world["ws"], lambda sid: False, now=later) == 1
    assert not (world["ws"] / "Hive" / "work" / W1).exists() and t.is_dir()


def test_a_whole_branch_bundle_stands_in_for_an_unreachable_remote(world):
    sha = _ship(world, full=True)
    world["gitconfig"].write_text(f'[url "{world["ws"]}/nowhere.git"]\n\tinsteadOf = {URL}\n')
    order = hw.prepare(world["ws"], W1, [_repo(sha)])
    t = world["ws"] / order["repos"][0]["dir"]
    assert _g(t, "rev-parse", "HEAD") == sha
    assert _g(hw._state() / "mirrors" / hw._mirror_rel(URL), "config", "--get", "remote.origin.url") == URL
    assert _g(t, "config", "--get", "remote.origin.url") == URL


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
    order = hw.load(world["ws"], W1)
    order["prepared_at"] = 1.0                                       # the older one
    hw._write_order(order)
    assert hw.latest_worktree(world["ws"], URL) == o2["repos"][0]["dir"]
    assert hw.latest_worktree(world["ws"], "https://github.com/acme/other.git") is None
    hw.release(world["ws"], W2, lambda sid: False)
    assert hw.latest_worktree(world["ws"], URL) == f"Hive/work/{W1}/repos/lib"


def test_a_tampered_mirror_is_rewritten_and_no_remote_helper_runs(world, tmp_path, monkeypatch):
    sha = _ship(world)
    hw.prepare(world["ws"], W1, [_repo(sha)])
    mirror = hw._state() / "mirrors" / hw._mirror_rel(URL)
    marker = tmp_path / "helper-ran"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    helper = bindir / "git-remote-evil"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    helper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    included = tmp_path / "inc"
    included.write_text('[url "evil::x"]\n\tinsteadOf = https://\n')
    cfg = mirror / "config"
    cfg.write_text(cfg.read_text().replace(URL, "evil::x") + f"[include]\n\tpath = {included}\n")
    hw.prepare(world["ws"], W2, [_repo(sha)])
    assert not marker.exists()
    text = cfg.read_text()
    assert "evil" not in text and "include" not in text and URL in text
    # the transport allowlist holds even where a config would ask for a helper
    assert not hw._ok(hw._git(mirror, "fetch", "evil::x"))
    assert not marker.exists()


def test_the_daemons_git_sees_no_secret_and_never_follows_a_link(world, tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_SECRET_TOKEN", "s3cret")
    env = hw.git_env()
    assert "HIVE_SECRET_TOKEN" not in env and env["PATH"] and env["GIT_CONFIG_GLOBAL"] == hw.GLOBAL_CONFIG
    sha = _ship(world)
    # the Host's bundle swapped for a link: not followed, not copied
    from core.hive_repo_inbox import bundle_rel
    b = world["ws"] / bundle_rel(sha)
    elsewhere = tmp_path / "elsewhere.bundle"
    b.rename(elsewhere)
    b.symlink_to(elsewhere)
    assert hw._copy_bundle(world["ws"], sha) is None
    # a task clone whose place holds a link: refused, nothing written through it
    hw.prepare(world["ws"], W1, [])
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (world["ws"] / hw.repo_rel(W1, "lib")).symlink_to(target_dir)
    mirror = hw._state() / "mirrors" / hw._mirror_rel(URL)
    with pytest.raises(hw.WorkError):
        hw._task_clone(mirror if mirror.exists() else world["remote"], {**_repo(world["a"]), "name": "lib"},
                       world["ws"], W1)
    assert list(target_dir.iterdir()) == []
