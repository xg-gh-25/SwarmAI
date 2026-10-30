"""This machine's commit to a Hive (design docs/design/2026-10-30-hive-task-repos-from-host.md).

Real git, temporary repositories: a bare "remote" stands for the repository's URL
(``url.insteadOf``). Questions: (1) does the host ship only what the remote lacks when
the checkout tracks it, and do the task text's own commands, run as the agent would,
end on the declared branch at exactly the host's commit; (2) with no tracking branch,
does the whole branch go and do the commands still get there when the clone is refused;
(3) is a checkout that moved while packing refused; (4) does the Hive keep the upload as
``Hive/incoming/<sha>.bundle`` and nothing else, never through a link, refusing a body
that is not a bundle, removing only old regular bundle files; (5) does what the task
changed count from the declared commit, and say so when that commit was never fetched;
(6) does the dispatch send each bundle once, nothing when the remote has the commit, and
take a 200 only when it names the kept bundle.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from core import hive_repo_inbox as hi, repo_ship, task_repos

URL = "https://github.com/acme/lib.git"


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
    """remote (bare) with A; host checkout at C (B, C not pushed); git config mapping URL → remote."""
    remote = tmp_path / "remote.git"
    _g(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f'[url "{remote}"]\n\tinsteadOf = {URL}\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    host = tmp_path / "host"
    _g(tmp_path, "clone", "-q", URL, str(host))
    _g(host, "checkout", "-q", "-b", "main")
    a = _commit(host, "a")
    _g(host, "push", "-q", "origin", "main")
    b = _commit(host, "b")
    c = _commit(host, "c")
    ws = tmp_path / "hive"
    ws.mkdir()
    return {"remote": remote, "host": host, "ws": ws, "a": a, "b": b, "c": c, "gitconfig": gitconfig}


def _ship(w, full=False):
    st = repo_ship.state(w["host"])
    path, base, size = repo_ship.pack(w["host"], URL, st["branch"], st["sha"], full)
    return st, path, base


def _keep(ws: Path, sha: str, data: bytes) -> str:
    inbox = hi.Inbox(ws, sha)
    inbox.write(data)
    return inbox.commit()


def _sh(ws: Path, cmd: str) -> bool:
    return subprocess.run(cmd, shell=True, cwd=str(ws), capture_output=True).returncode == 0


def _agent(ws: Path, text: str) -> None:
    """Run the task text's commands for one repository the way they read."""
    m = re.search(r"1\. if (\S+) does not exist: (.+); otherwise: (.+)", text)
    d, clone, fetch_origin = m.groups()
    fallback = re.search(r"\(if the clone is refused and (\S+) exists: (.+)\)", text).groups()
    step2 = re.search(r"2\. if (\S+) exists: (.+)", text).groups()
    step3 = re.search(r"3\. (git .+)", text).group(1)
    if not (ws / d).exists():
        if not _sh(ws, clone) and (ws / fallback[0]).exists():
            assert _sh(ws, fallback[1])
    else:
        _sh(ws, fetch_origin)
    if (ws / step2[0]).exists():
        assert _sh(ws, step2[1])
    assert _sh(ws, step3)


def _declared(sha):
    return task_repos.parse([{"url": URL, "ref": "main", "sha": sha}])


def test_a_thin_bundle_and_the_task_text_put_the_hive_on_the_host_commit(world):
    st, path, base = _ship(world)
    assert (st["branch"], st["sha"], st["dirty"]) == ("main", world["c"], 0)
    assert base == world["a"]
    assert _keep(world["ws"], world["c"], path.read_bytes()) == f"Hive/incoming/{world['c']}.bundle"
    declared = _declared(world["c"])
    _agent(world["ws"], task_repos.task_text(declared))
    repo = world["ws"] / declared[0]["dir"]
    assert _g(repo, "rev-parse", "HEAD") == world["c"]
    assert _g(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert _g(repo, "config", "--get", "remote.origin.url") == URL
    # a second task on the same directory: the next commit, over the first
    d = _commit(world["host"], "d")
    st, path, _ = _ship(world)
    _keep(world["ws"], d, path.read_bytes())
    _agent(world["ws"], task_repos.task_text(_declared(d)))
    assert _g(repo, "rev-parse", "HEAD") == d


def test_with_no_tracking_branch_the_whole_branch_goes_and_works_without_the_remote(world):
    st, path, base = _ship(world, full=True)
    assert base is None
    _keep(world["ws"], world["c"], path.read_bytes())
    world["gitconfig"].write_text(f'[url "{world["ws"]}/nowhere.git"]\n\tinsteadOf = {URL}\n')  # clone refused
    declared = _declared(world["c"])
    _agent(world["ws"], task_repos.task_text(declared))
    repo = world["ws"] / declared[0]["dir"]
    assert _g(repo, "rev-parse", "HEAD") == world["c"]
    assert _g(repo, "config", "--get", "remote.origin.url") == URL


def test_a_checkout_that_moved_while_packing_is_refused(world):
    with pytest.raises(repo_ship.ShipError, match="moved while it was being packed"):
        repo_ship.pack(world["host"], URL, "main", world["b"])


def test_nothing_is_sent_when_the_remote_has_the_commit_and_the_text_still_gets_there(world):
    _g(world["host"], "push", "-q", "origin", "main")
    st = repo_ship.state(world["host"])
    assert repo_ship.pack(world["host"], URL, "main", st["sha"]) == (None, st["sha"], 0)
    declared = _declared(st["sha"])
    _agent(world["ws"], task_repos.task_text(declared))       # no bundle in Hive/incoming
    assert _g(world["ws"] / declared[0]["dir"], "rev-parse", "HEAD") == world["c"]


def test_the_hive_keeps_only_a_bundle_and_never_through_a_link(world, tmp_path):
    st, path, _ = _ship(world)
    data = path.read_bytes()
    with pytest.raises(hi.InboxError, match="not a git bundle"):
        _keep(world["ws"], world["c"], b"not a bundle at all")
    incoming = world["ws"] / "Hive" / "incoming"
    assert list(incoming.iterdir()) == []                      # the partial file is gone
    rel = _keep(world["ws"], world["c"], data)
    assert (world["ws"] / rel).read_bytes() == data
    assert sorted(p.name for p in incoming.iterdir()) == [f"{world['c']}.bundle"]
    # a linked incoming/ is refused and nothing is written where it points
    outside = tmp_path / "outside"
    outside.mkdir()
    for p in incoming.iterdir():
        p.unlink()
    incoming.rmdir()
    incoming.symlink_to(outside)
    with pytest.raises(hi.InboxError):
        _keep(world["ws"], world["c"], data)
    assert list(outside.iterdir()) == []
    with pytest.raises(hi.InboxError):
        hi.bundle_rel("../../etc")


def test_old_bundles_go_and_a_planted_link_is_neither_followed_nor_kept_for_it(world, tmp_path):
    st, path, _ = _ship(world)
    data = path.read_bytes()
    _keep(world["ws"], world["b"], data)                       # an earlier one
    incoming = world["ws"] / "Hive" / "incoming"
    old = incoming / f"{world['b']}.bundle"
    victim = tmp_path / "victim.bundle"
    victim.write_text("keep me")
    link = incoming / ("f" * 40 + ".bundle")
    link.symlink_to(victim)
    past = time.time() - hi.KEEP_S - 60
    os.utime(old, (past, past))
    os.utime(link, (past, past), follow_symlinks=False)
    _keep(world["ws"], world["c"], data)
    assert not old.exists()                                    # the old regular bundle is removed
    assert victim.read_text() == "keep me" and link.is_symlink()   # the link is not followed


def test_the_hive_endpoint_keeps_the_upload(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.hive_brains as hb
    app = FastAPI()
    app.include_router(hb.router, prefix="/api/hive")
    client = TestClient(app)
    params = {"url": URL, "branch": "main", "sha": world["c"]}
    assert client.put("/api/hive/repos", params=params, content=b"x").status_code == 404     # not a Hive
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setattr(hb, "_workspace", lambda: world["ws"])
    st, path, _ = _ship(world)
    data = path.read_bytes()
    r = client.put("/api/hive/repos", params=params, content=data)
    assert r.status_code == 200
    assert r.json() == {"bundle": f"Hive/incoming/{world['c']}.bundle", "sha": world["c"], "bytes": len(data)}
    assert (world["ws"] / r.json()["bundle"]).read_bytes() == data
    assert client.put("/api/hive/repos", params=params, content=b"not a bundle").status_code == 422
    assert client.put("/api/hive/repos", params={**params, "url": "file:///etc"}, content=data).status_code == 422
    assert client.put("/api/hive/repos", params={**params, "sha": "x"}, content=data).status_code == 422
    monkeypatch.setattr(repo_ship, "MAX_BUNDLE_BYTES", 10)
    assert client.put("/api/hive/repos", params=params, content=data).status_code == 413


def test_what_the_task_changed_counts_from_the_declared_commit(world, monkeypatch):
    st, path, _ = _ship(world, full=True)
    _keep(world["ws"], world["c"], path.read_bytes())
    declared = _declared(world["c"])
    _agent(world["ws"], task_repos.task_text(declared))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL")                          # facts read the origin as configured
    _commit(world["ws"] / declared[0]["dir"], "hive-work")
    fact = task_repos.repo_facts(str(world["ws"]), declared)[0]
    assert (fact["error"], fact["base"], fact["commits_total"]) == (None, world["c"], 1)
    with pytest.raises(task_repos.TaskRepoError):
        task_repos.parse([{"url": URL, "ref": "main", "sha": "not-a-sha"}])


def test_a_commit_the_task_never_fetched_is_said_not_guessed(world, monkeypatch):
    _g(world["ws"], "clone", "-q", URL, str(world["ws"] / task_repos.repo_dir(URL)))   # the agent skipped the bundle
    monkeypatch.delenv("GIT_CONFIG_GLOBAL")                          # facts read the origin as configured
    fact = task_repos.repo_facts(str(world["ws"]), _declared(world["c"]))[0]
    assert fact["error"] == task_repos.ERR_NO_COMMIT and fact["base"] is None
    assert task_repos.ERR_NO_COMMIT in task_repos.ERRORS


def test_the_dispatch_sends_each_bundle_once(world, monkeypatch):
    import core.swarm_out as so
    calls = []

    async def put(inst, r, path, size):
        calls.append((r["name"], path.stat().st_size == size))
        return 200, ""
    monkeypatch.setattr(so, "_put_repo", put)
    st = repo_ship.state(world["host"])
    repos = [{"name": "lib", "url": URL, "ref": "main", "sha": st["sha"], "checkout": str(world["host"])},
             {"name": "other", "url": "https://github.com/acme/other.git", "ref": "main", "sha": None}]
    asyncio.run(so._ship_repos({"name": "h"}, repos))
    assert calls == [("lib", True)]
    _g(world["host"], "push", "-q", "origin", "main")                   # the remote has it now
    asyncio.run(so._ship_repos({"name": "h"}, repos))
    assert len(calls) == 1
    _commit(world["host"], "e")
    repos[0]["sha"] = repo_ship.state(world["host"])["sha"]
    monkeypatch.setattr(so, "_put_repo", lambda *a: asyncio.sleep(0, (404, "")))
    with pytest.raises(so.SwarmOutError, match="update it once"):
        asyncio.run(so._ship_repos({"name": "h"}, repos))


def test_a_hive_answer_that_does_not_name_the_kept_bundle_is_not_taken(world, tmp_path, monkeypatch):
    import core.swarm_out as so
    answers = []

    class Resp:
        status_code = 200

        def json(self):
            return answers[-1]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def put(self, *a, **k):
            return Resp()
    monkeypatch.setattr(so, "_client", lambda inst, timeout: Client())
    f = tmp_path / "x.bundle"
    f.write_bytes(b"12345")
    r = {"name": "lib", "url": URL, "ref": "main", "sha": world["c"]}
    good = {"bundle": f"Hive/incoming/{world['c']}.bundle", "sha": world["c"], "bytes": 5}
    for wrong in ({**good, "bundle": "Hive/incoming/other.bundle"}, {**good, "bytes": 4}, {}):
        answers.append(wrong)
        assert asyncio.run(so._put_repo({"name": "h"}, r, f, 5)) == (500, "the Hive did not confirm the bundle")
    answers.append(good)
    assert asyncio.run(so._put_repo({"name": "h"}, r, f, 5)) == (200, "")


def test_an_ssh_binding_still_ships_only_what_the_remote_lacks(world):
    st = repo_ship.state(world["host"])
    path, base, _ = repo_ship.pack(world["host"], "git@github.com:acme/lib.git", "main", st["sha"])
    assert base == world["a"]
    path.unlink()
