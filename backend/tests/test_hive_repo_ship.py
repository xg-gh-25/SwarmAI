"""This machine's commit to a Hive (design docs/design/2026-10-30-hive-task-repos-from-host.md).

Real git, temporary repositories: a bare "remote" stands for the repository's URL
(``url.insteadOf``). Questions: (1) does the host ship only what the remote lacks when
the checkout tracks it, and the Hive end on the declared branch at exactly the host's
commit; (2) when the Hive cannot get the history a thin bundle builds on, does it say
so (409) and does the whole branch then work with no network; (3) is a bundle that does
not hold the declared commit refused, and a checkout that moved while packing; (4) is a
repository whose own config sets commands git would run moved aside, not run; (5) when
the remote has the commit already, is nothing sent and the Hive still ends there;
(6) does what the task changed count from the declared commit; (7) does the dispatch
put each repository on the Hive before the task, and retry once with the whole branch on 409.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from core import hive_repo_prepare as hp, repo_ship, task_repos

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
    monkeypatch.setitem(hp._ENV, "GIT_CONFIG_GLOBAL", str(gitconfig))     # the Hive's git sees it too
    host = tmp_path / "host"
    _g(tmp_path, "clone", "-q", URL, str(host))
    _g(host, "checkout", "-q", "-b", "main")
    a = _commit(host, "a")
    _g(host, "push", "-q", "origin", "main")
    b = _commit(host, "b")
    c = _commit(host, "c")
    ws = tmp_path / "hive"
    ws.mkdir()
    cache = tmp_path / "cache"                       # the daemon's own directory, outside ws
    monkeypatch.setattr(hp, "cache_dir", lambda: cache)
    return {"remote": remote, "host": host, "ws": ws, "a": a, "b": b, "c": c, "gitconfig": gitconfig,
            "cache": cache}


def _ship(w, full=False):
    st = repo_ship.state(w["host"])
    path, base, size = repo_ship.pack(w["host"], URL, st["branch"], st["sha"], full)
    return st, path, base


def test_a_thin_bundle_puts_the_hive_on_the_host_commit(world):
    st, path, base = _ship(world)
    assert (st["branch"], st["sha"], st["dirty"]) == ("main", world["c"], 0)
    assert base == world["a"] and hp._prerequisites(path) == [world["a"]]
    out = hp.prepare(world["ws"], URL, "main", world["c"], path)
    repo = world["ws"] / out["dir"]
    assert out["head"] == world["c"] and _g(repo, "rev-parse", "HEAD") == world["c"]
    assert _g(repo, "symbolic-ref", "--short", "HEAD") == "main"
    # a second task on the same directory: the next commit, prepared over the first
    d = _commit(world["host"], "d")
    st, path, _ = _ship(world)
    assert hp.prepare(world["ws"], URL, "main", d, path)["head"] == d


def test_a_hive_without_the_history_asks_for_the_whole_branch(world, tmp_path):
    empty = tmp_path / "empty.git"
    _g(tmp_path, "init", "-q", "--bare", "-b", "main", str(empty))
    world["gitconfig"].write_text(f'[url "{empty}"]\n\tinsteadOf = {URL}\n')   # the Hive's remote lacks A
    # the host's tracking branch still says the remote has A
    st, path, base = _ship(world)
    assert base == world["a"]
    with pytest.raises(hp.NeedFull) as e:
        hp.prepare(world["ws"], URL, "main", world["c"], path)
    assert e.value.status == 409
    world["gitconfig"].write_text("")                                          # and no network at all
    st, path, base = _ship(world, full=True)
    assert base is None and hp._prerequisites(path) == []
    out = hp.prepare(world["ws"], URL, "main", world["c"], path)
    repo = world["ws"] / out["dir"]
    assert out["head"] == world["c"] and _g(repo, "remote", "get-url", "origin") == URL


def test_a_bundle_without_the_declared_commit_is_refused(world):
    st, path, _ = _ship(world)
    with pytest.raises(hp.PrepareError, match="does not hold the declared commit"):
        hp.prepare(world["ws"], URL, "main", world["b"], path)
    with pytest.raises(repo_ship.ShipError, match="moved while it was being packed"):
        repo_ship.pack(world["host"], URL, "main", world["b"])
    with pytest.raises(hp.PrepareError, match="not a git bundle"):
        bad = world["ws"].parent / "bad.bundle"
        bad.write_bytes(b"not a bundle")
        hp.prepare(world["ws"], URL, "main", world["c"], bad)


def test_the_next_task_gets_a_new_checkout_from_the_cache_and_the_last_one_is_kept(world):
    st, path, _ = _ship(world)
    out = hp.prepare(world["ws"], URL, "main", world["c"], path)
    repo = world["ws"] / out["dir"]
    (repo / "left-by-task-1").write_text("x")
    import shutil
    shutil.rmtree(world["remote"])                    # the history now comes from the cache only
    shas = []
    for name in ("d", "e"):
        shas.append(_commit(world["host"], name))
        st, path, _ = _ship(world)                    # thin: builds on A, which the cache has
        assert hp.prepare(world["ws"], URL, "main", shas[-1], path)["head"] == shas[-1]
    previous = repo.with_name(repo.name + ".previous")
    assert _g(repo, "rev-parse", "HEAD") == shas[1] and _g(previous, "rev-parse", "HEAD") == shas[0]
    assert not (previous / "left-by-task-1").exists()                 # only the last one is kept
    assert sorted(p.name for p in repo.parent.iterdir()) == [repo.name, repo.name + ".previous"]
    assert not list(world["cache"].glob(".stage-*"))                  # no staging left


def test_git_never_runs_in_what_the_agent_wrote(world, tmp_path):
    st, path, _ = _ship(world)
    out = hp.prepare(world["ws"], URL, "main", world["c"], path)
    repo = world["ws"] / out["dir"]
    marker, elsewhere = tmp_path / "ran", tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep").write_text("keep")
    # every way an earlier review found around a list of risky keys
    _g(repo, "config", "filter.x.smudge", f"touch {marker}")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* filter=x\n")
    _g(repo, "config", "core.worktree", str(elsewhere))
    _g(repo, "config", "extensions.worktreeConfig", "true")
    (repo / ".git" / "config.worktree").write_text(f'[filter "y"]\n\tsmudge = touch {marker}\n')
    _g(repo, "config", "--add", "remote.origin.url", str(tmp_path / "x"))
    _g(repo, "config", "remote.origin.uploadpack", f"touch {marker}; git-upload-pack")
    d = _commit(world["host"], "d")                  # a new file: a checkout would smudge it
    st, path, _ = _ship(world)
    assert hp.prepare(world["ws"], URL, "main", d, path)["head"] == d
    assert not marker.exists()
    assert sorted(p.name for p in elsewhere.iterdir()) == ["keep"]
    assert _g(repo, "config", "--get-all", "remote.origin.url") == URL      # a new repository
    assert subprocess.run(["git", "config", "--get", "core.worktree"], cwd=repo).returncode == 1


def test_a_symlink_in_place_of_the_directory_is_never_followed(world, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep").write_text("keep")
    target = world["ws"] / task_repos.repo_dir(URL)
    target.parent.mkdir(parents=True)
    target.symlink_to(elsewhere)
    st, path, _ = _ship(world)
    assert hp.prepare(world["ws"], URL, "main", world["c"], path)["head"] == world["c"]
    assert not target.is_symlink() and sorted(p.name for p in elsewhere.iterdir()) == ["keep"]


def test_a_bundle_header_that_never_ends_asks_for_the_whole_branch(world):
    p = world["ws"].parent / "long.bundle"
    p.write_bytes(b"# v2 git bundle\n" + b"-" + b"a" * 40 + b" x\n")
    with pytest.raises(hp.NeedFull):
        hp._prerequisites(p)


def test_nothing_is_sent_when_the_remote_has_the_commit(world):
    _g(world["host"], "push", "-q", "origin", "main")
    st = repo_ship.state(world["host"])
    assert repo_ship.pack(world["host"], URL, "main", st["sha"]) == (None, st["sha"], 0)
    assert hp.prepare(world["ws"], URL, "main", st["sha"], None)["head"] == world["c"]


def test_what_the_task_changed_counts_from_the_declared_commit(world, monkeypatch):
    st, path, _ = _ship(world, full=True)
    out = hp.prepare(world["ws"], URL, "main", world["c"], path)     # no remote-tracking ref at all
    monkeypatch.delenv("GIT_CONFIG_GLOBAL")                          # facts read the origin as configured
    _commit(world["ws"] / out["dir"], "hive-work")
    declared = task_repos.parse([{"url": URL, "ref": "main", "sha": world["c"]}])
    fact = task_repos.repo_facts(str(world["ws"]), declared)[0]
    assert (fact["error"], fact["base"], fact["commits_total"]) == (None, world["c"], 1)
    assert task_repos.task_text(declared).count("ready, on branch main at " + world["c"][:12]) == 1
    with pytest.raises(task_repos.TaskRepoError):
        task_repos.parse([{"url": URL, "ref": "main", "sha": "not-a-sha"}])


def test_the_dispatch_puts_each_repository_on_the_hive_first(world, monkeypatch):
    import core.swarm_out as so
    calls = []

    async def put(inst, r, path, size):
        prereqs = hp._prerequisites(path) if path else None
        calls.append(prereqs)
        return (409, "") if len(calls) == 1 else (200, "")
    monkeypatch.setattr(so, "_put_repo", put)
    st = repo_ship.state(world["host"])
    repos = [{"name": "lib", "url": URL, "ref": "main", "sha": st["sha"], "checkout": str(world["host"])},
             {"name": "other", "url": "https://github.com/acme/other.git", "ref": "main", "sha": None}]
    asyncio.run(so._ship_repos({"name": "h"}, repos))
    assert calls == [[world["a"]], []]                  # thin first, the whole branch after a 409
    monkeypatch.setattr(so, "_put_repo", lambda *a: asyncio.sleep(0, (404, "")))
    with pytest.raises(so.SwarmOutError, match="update it once"):
        asyncio.run(so._ship_repos({"name": "h"}, repos))


def test_the_hive_endpoint_prepares_from_the_upload(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.hive_brains as hb
    app = FastAPI()
    app.include_router(hb.router, prefix="/api/hive")
    client = TestClient(app)
    params = {"url": URL, "branch": "main", "sha": world["c"]}
    assert client.put("/api/hive/repos", params=params, content=b"").status_code == 404     # not a Hive
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setattr(hb, "_workspace", lambda: world["ws"])
    st, path, _ = _ship(world, full=True)
    r = client.put("/api/hive/repos", params=params, content=path.read_bytes())
    assert r.status_code == 200 and r.json()["head"] == world["c"]
    assert not list((world["cache"] / ".incoming").iterdir())          # the upload is removed
    assert not (world["ws"] / "Hive" / "repos" / ".incoming").exists()  # and never in the workspace
    monkeypatch.setattr(hp, "prepare",
                        lambda *a, **k: (_ for _ in ()).throw(hp.PrepareError("git fetch failed", 500)))
    r = client.put("/api/hive/repos", params=params, content=b"")
    assert r.status_code == 500 and r.json()["detail"] == "git fetch failed"   # no git output, no path
    assert client.put("/api/hive/repos", params={**params, "url": "file:///etc"}, content=b"").status_code == 422


def test_a_hive_answer_that_does_not_name_the_commit_is_not_taken(world, monkeypatch):
    import core.swarm_out as so

    class Resp:
        status_code = 200

        def json(self):
            return {"head": "0" * 40}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def put(self, *a, **k):
            return Resp()
    monkeypatch.setattr(so, "_client", lambda inst, timeout: Client())
    r = {"name": "lib", "url": URL, "ref": "main", "sha": world["c"]}
    assert asyncio.run(so._put_repo({"name": "h"}, r, None, 0)) == (500, "the Hive did not confirm the commit")


def test_an_ssh_binding_still_ships_only_what_the_remote_lacks(world):
    st = repo_ship.state(world["host"])
    path, base, _ = repo_ship.pack(world["host"], "git@github.com:acme/lib.git", "main", st["sha"])
    assert base == world["a"]
    path.unlink()


def test_git_output_stays_on_the_hive(tmp_path):
    import time
    with pytest.raises(hp.PrepareError) as e:
        hp._git(tmp_path, time.monotonic() + 30, "cat-file", "-e", "no-such-object")
    assert str(e.value) == "git cat-file failed" and str(tmp_path) not in str(e.value)


def test_the_checkout_shares_no_object_file_with_the_cache(world):
    st, path, _ = _ship(world)
    repo = world["ws"] / hp.prepare(world["ws"], URL, "main", world["c"], path)["dir"]
    files = [f for f in (repo / ".git" / "objects").rglob("*") if f.is_file()]
    assert files and all(f.stat().st_nlink == 1 for f in files)


def test_a_symlink_above_the_directory_is_refused_and_nothing_is_made_there(world, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (world["ws"] / "Hive").mkdir()
    (world["ws"] / "Hive" / "repos").symlink_to(elsewhere)
    st, path, _ = _ship(world)
    with pytest.raises(hp.PrepareError, match="outside"):
        hp.prepare(world["ws"], URL, "main", world["c"], path)
    assert list(elsewhere.iterdir()) == []


def test_a_directory_swapped_for_a_symlink_while_fetching_changes_nothing_outside(world, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "lib").mkdir(parents=True)
    (elsewhere / "lib" / "keep").write_text("keep")
    owner = (world["ws"] / task_repos.repo_dir(URL)).parent
    real_fill = hp._fill_cache

    def fill_then_swap(*a, **k):
        real_fill(*a, **k)
        owner.rename(owner.with_name("acme.x"))       # the agent, after the check
        owner.symlink_to(elsewhere)
    monkeypatch.setattr(hp, "_fill_cache", fill_then_swap)
    st, path, _ = _ship(world)
    hp.prepare(world["ws"], URL, "main", world["c"], path)
    assert sorted(p.name for p in elsewhere.iterdir()) == ["lib"] and (elsewhere / "lib" / "keep").exists()
    assert _g(owner.with_name("acme.x") / "lib", "rev-parse", "HEAD") == world["c"]


def test_the_cache_is_never_inside_the_workspace_and_never_shared(world):
    st, path, _ = _ship(world)
    with pytest.raises(hp.PrepareError, match="inside the workspace"):
        hp.prepare(world["ws"], URL, "main", world["c"], path, cache_root=world["ws"] / "cache")
    a = hp._cache_path(world["cache"], task_repos.repo_dir("https://github.com/a__b/c.git"))
    b = hp._cache_path(world["cache"], task_repos.repo_dir("https://github.com/a/b__c.git"))
    assert a != b
