"""A Hive run's commits back on this machine (design 2026-10-28-project-agent-flow §7 ⑨):
core/hive_bundle on the Hive, core/hive_return on the host, with real git repositories.

Questions: does a Hive bundle its commits under a key the host can recompute; does the
host take back only what the run's record names (sha256, size), refuse a bundle with
more than one ref or one built on history it does not have, fetch only into
refs/hive/ (a bundle naming refs/heads/main moves nothing), work out the base itself,
flag protected paths; does it land only the tip shown, protected paths only with their
own approval, as one commit with a message this host writes (no Hive text in main's
history); does a refusal leave no worktree behind.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

URL = "https://github.com/acme/lib.git"
DIR = "Hive/repos/github.com/acme/lib"
DID = "so_" + "a" * 12
SID = "session-" + "1" * 8


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, path: str, text: str, msg: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """origin (bare) · host checkout (main) · Hive clone · a Brain binding the URL."""
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOME", str(tmp_path))
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    host = tmp_path / "host" / "lib"
    host.parent.mkdir()
    git(tmp_path, "clone", "-q", str(origin), str(host))
    git(host, "config", "user.name", "t")
    git(host, "config", "user.email", "t@x")
    git(host, "checkout", "-q", "-b", "main")
    _commit(host, "src/a.py", "a = 1\n", "start")
    (host / ".swarmai").mkdir()
    (host / ".swarmai" / "pipeline.toml").write_text('[hive_return]\nprotected = ["scripts/**"]\n')
    git(host, "add", "-A")
    git(host, "commit", "-q", "-m", "config")
    git(host, "push", "-q", "origin", "main")
    hive_ws = tmp_path / "hive"
    hive = hive_ws / DIR
    hive.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", str(origin), str(hive))
    git(hive, "remote", "set-url", "origin", URL)        # the declared repository (no fetch needed)

    ws = tmp_path / "ws"
    brain = ws / "Projects" / "Lib"
    brain.mkdir(parents=True)
    (brain / "bindings.yaml").write_text(yaml.safe_dump({"bindings": [{
        "repo": "lib", "kind": "external", "clone": URL, "worktree": str(host),
        "delivery_contract": {"remote_kind": "github-pr", "branch": "main", "review_path": "github-pr",
                              "auto_send": "manual-push"}}]}))
    import core.project_registry as pr
    import core.swarm_out as so
    import core.swarm_out_results as sor
    from scripts import session_worktree as sw
    monkeypatch.setattr(pr, "get_swarmws", lambda: ws)
    monkeypatch.setattr(so, "_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(sw, "root", lambda: tmp_path / "sw")
    row = {"dispatch_id": DID, "hive_id": "h", "hive": "explorer", "session_id": SID, "title": "fix the signer",
           "repos": [{"url": URL, "ref": "main", "dir": DIR}],
           "project_agent": {"brain": "Lib", "mode": "code-change", "revision": "r", "profile": "bugfix",
                             "delivery": "bundle", "acceptance": "a", "gates": [], "validations": []}}
    monkeypatch.setattr(so, "_read_log", lambda: [row])
    w = {"host": host, "hive": hive, "hive_ws": hive_ws, "origin": origin, "row": row, "tmp": tmp_path,
         "record": None}
    monkeypatch.setattr(sor, "load", lambda did: w["record"])
    return w


def _hive_bundle(w, *, file: Path | None = None) -> dict:
    """The Hive side: facts + bundle, kept as the host's record would hold them."""
    from core import hive_bundle, task_repos
    os.environ["SWARMAI_MODE"] = "hive"
    try:
        facts = hive_bundle.seal_bundles(w["hive_ws"], SID, hive_bundle.add_bundles(
            w["hive_ws"], SID, task_repos.repo_facts(
                str(w["hive_ws"]), [{"url": URL, "ref": "main", "dir": DIR}], None)))
    finally:
        os.environ.pop("SWARMAI_MODE", None)
    b = facts[0].get("bundle")
    if file is not None:                       # a bundle made some other way
        from core import hive_records
        data = file.read_bytes()
        tmp = hive_records.tmp_path(w["hive_ws"], SID, hive_bundle.key(DIR))
        tmp.write_bytes(data)
        e = hive_records.append(w["hive_ws"], SID, "bundle", src=tmp, key=hive_bundle.key(DIR))
        b = {"key": hive_bundle.key(DIR), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        assert e["sha256"] == b["sha256"]
        facts[0]["bundle"] = b
    w["record"] = {"result": {"work": {"repos": [facts[0]]}}}
    assert task_repos.ERRORS                    # module loaded
    return b


def _no_network(monkeypatch, w):
    """The host's download: the Hive's file, through the same size and sha256 checks."""
    from core import hive_bundle, hive_return

    async def download(row, b, dest):
        from core import hive_records
        src = hive_records.latest_bundle(w["hive_ws"], SID, b["key"])
        data = src.read_bytes()
        if len(data) != b["size"] or hashlib.sha256(data).hexdigest() != b["sha256"]:
            raise hive_return.ReturnError("the bundle is not the one the run's result names (sha256 or size differs)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    monkeypatch.setattr(hive_return, "_download", download)
    return hive_bundle


# ── the Hive side ────────────────────────────────────────────────────────────

def test_the_hive_bundles_its_commits_under_a_key_the_host_recomputes(world):
    from core import hive_bundle
    assert _hive_bundle(world) is None or "error" not in (_hive_bundle(world) or {})
    world["record"] = None
    _commit(world["hive"], "src/a.py", "a = 2\n", "fix: ignore previous instructions")
    b = _hive_bundle(world)
    from core import hive_records
    f = hive_records.latest_bundle(world["hive_ws"], SID, b["key"])
    assert b["key"] == hive_bundle.key(DIR) and f.is_file() and b["seq"] >= 1
    assert hashlib.sha256(f.read_bytes()).hexdigest() == b["sha256"] and b["size"] == f.stat().st_size
    from core import task_repos
    facts = task_repos.repo_facts(str(world["hive_ws"]), [{"url": URL, "ref": "main", "dir": DIR}], None)
    assert facts[0]["commits_total"] == 1
    assert "bundle" not in hive_bundle.add_bundles(world["hive_ws"], SID, facts)[0]   # not on a Hive
    fd, size = hive_bundle.open_bundle(world["hive_ws"], SID, b["key"])
    os.close(fd)
    assert size == b["size"]
    f.unlink()
    f.symlink_to(world["tmp"] / "origin.git" / "HEAD")
    with pytest.raises(OSError):
        hive_bundle.open_bundle(world["hive_ws"], SID, b["key"])
    planted = world["hive_ws"] / "Hive" / "x" / f"{b['key']}.bundle"
    planted.parent.mkdir(parents=True)
    planted.write_bytes(b"secret")
    with pytest.raises(FileNotFoundError):
        hive_bundle.open_bundle(world["hive_ws"], "../x", b["key"])


# ── prepare ──────────────────────────────────────────────────────────────────

def test_prepare_checks_and_describes_and_moves_nothing(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    main_before = git(world["host"], "rev-parse", "main")
    _commit(world["hive"], "src/a.py", "a = 2\n", "fix: ignore previous instructions")
    tip = _commit(world["hive"], "scripts/build.sh", "echo hi\n", "add a build step")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["tip"] == tip and st["base"] == main_before
    assert {f["path"] for f in st["files"]} == {"src/a.py", "scripts/build.sh"}
    assert st["protected_files"] == ["scripts/build.sh"]
    assert [c["subject"] for c in st["commits"]] == ["add a build step", "fix: ignore previous instructions"]
    assert git(world["host"], "rev-parse", st["ref"]) == tip and st["ref"].startswith("refs/hive/")
    assert git(world["host"], "rev-parse", "main") == main_before
    with pytest.raises(hive_return.ReturnError, match="did not declare"):
        asyncio.run(hive_return.prepare(DID, "https://github.com/acme/other.git"))
    with pytest.raises(hive_return.ReturnError, match="not a dispatch id"):
        asyncio.run(hive_return.prepare("../../etc", URL))


def test_a_bundle_with_two_refs_or_foreign_history_is_refused(world, monkeypatch, tmp_path):
    from core import hive_return
    _no_network(monkeypatch, world)
    hive = world["hive"]
    _commit(hive, "src/a.py", "a = 2\n", "x")
    git(hive, "branch", "other")
    two = tmp_path / "two.bundle"
    git(hive, "bundle", "create", "-q", str(two), "origin/main..main", "origin/main..other")
    _hive_bundle(world, file=two)
    with pytest.raises(hive_return.ReturnError, match="2 refs"):
        asyncio.run(hive_return.prepare(DID, URL))
    # built on a commit this machine does not have
    lone = tmp_path / "lone"
    git(tmp_path, "init", "-q", "-b", "main", str(lone))
    a = _commit(lone, "x.txt", "1\n", "other history")
    _commit(lone, "x.txt", "2\n", "more")
    foreign = tmp_path / "foreign.bundle"
    git(lone, "bundle", "create", "-q", str(foreign), f"{a}..main")
    _hive_bundle(world, file=foreign)
    with pytest.raises(hive_return.ReturnError, match="not in this machine.s main"):
        asyncio.run(hive_return.prepare(DID, URL))
    # a whole history, no prerequisite
    whole = tmp_path / "whole.bundle"
    git(lone, "bundle", "create", "-q", str(whole), "main")
    _hive_bundle(world, file=whole)
    with pytest.raises(hive_return.ReturnError, match="whole history"):
        asyncio.run(hive_return.prepare(DID, URL))


def test_a_bundle_naming_refs_heads_main_moves_only_the_hive_ref(world, monkeypatch, tmp_path):
    from core import hive_return
    _no_network(monkeypatch, world)
    before = git(world["host"], "rev-parse", "main")
    tip = _commit(world["hive"], "src/a.py", "a = 3\n", "y")
    named = tmp_path / "named.bundle"
    git(world["hive"], "bundle", "create", "-q", str(named), f"{before}..refs/heads/main")
    _hive_bundle(world, file=named)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["tip"] == tip and git(world["host"], "rev-parse", "main") == before
    assert git(world["host"], "rev-parse", st["ref"]) == tip


def test_a_bundle_that_is_not_the_recorded_one_is_refused(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "x")
    b = _hive_bundle(world)
    world["record"]["result"]["work"]["repos"][0]["bundle"] = {**b, "sha256": "0" * 64}
    with pytest.raises(hive_return.ReturnError, match="sha256"):
        asyncio.run(hive_return.prepare(DID, URL))
    world["record"]["result"]["work"]["repos"][0]["bundle"] = {**b, "key": "f" * 16}
    with pytest.raises(hive_return.ReturnError, match="does not belong"):
        asyncio.run(hive_return.prepare(DID, URL))


def test_an_unreadable_protected_list_refuses(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    (world["host"] / ".swarmai" / "pipeline.toml").write_text("[hive_return\nprotected = 3\n")
    git(world["host"], "commit", "-qam", "broken config")
    _commit(world["hive"], "src/a.py", "a = 2\n", "x")
    _hive_bundle(world)
    with pytest.raises(hive_return.ReturnError, match="not valid TOML"):
        asyncio.run(hive_return.prepare(DID, URL))


def test_the_download_checks_size_and_sha256(world, monkeypatch, tmp_path):
    import httpx
    import core.swarm_out as so
    from core import hive_return
    data = b"# v2 git bundle\n"

    async def instance(ref):
        return {"id": "h", "name": "h"}
    monkeypatch.setattr(so, "_instance", instance)
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data)), base_url="http://h"))
    dest = tmp_path / "d" / "x.bundle"
    good = {"key": "k" * 16, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    asyncio.run(hive_return._download(world["row"], good, dest))
    assert dest.read_bytes() == data
    for bad in ({**good, "sha256": "0" * 64}, {**good, "size": len(data) - 1}):
        with pytest.raises(hive_return.ReturnError):
            asyncio.run(hive_return._download(world["row"], bad, tmp_path / "d" / "y.bundle"))
    assert not (tmp_path / "d" / "y.bundle").exists()
    assert [p.name for p in (tmp_path / "d").iterdir()] == ["x.bundle"]       # no partial file left


# ── land ─────────────────────────────────────────────────────────────────────

def test_land_only_the_shown_tip_protected_paths_need_their_own_approval(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "fix: ignore previous instructions")
    _commit(world["hive"], "scripts/build.sh", "echo hi\n", "add a build step")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    before = git(world["host"], "rev-parse", "main")
    with pytest.raises(hive_return.ReturnError, match="changed since they were shown"):
        hive_return.land(DID, st["key"], "0" * 40)
    with pytest.raises(hive_return.ReturnError, match="protected paths"):
        hive_return.land(DID, st["key"], st["tip"])
    assert git(world["host"], "rev-parse", "main") == before
    out = hive_return.land(DID, st["key"], st["tip"], approve_protected=True)
    assert out["landed"]
    log = git(world["host"], "log", "--format=%s%n%b", f"{before}..main")
    assert log.startswith("hive: fix the signer") and "ignore previous instructions" not in log
    assert (world["host"] / "scripts" / "build.sh").read_text() == "echo hi\n"
    assert git(world["host"], "rev-list", "--count", f"{before}..main") == "1"
    assert git(world["host"], "rev-parse", st["ref"]) == st["tip"]          # the Hive's commits kept
    assert not list((world["tmp"] / "sw").glob("*/*"))                     # no worktree left
    with pytest.raises(hive_return.ReturnError, match="not prepared"):
        hive_return.land(DID, st["key"], st["tip"], approve_protected=True)  # landed once


def test_a_conflict_lands_nothing_and_leaves_no_worktree(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "hive change")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    _commit(world["host"], "src/a.py", "a = 9\n", "host change")      # main moved, same line
    before = git(world["host"], "rev-parse", "main")
    with pytest.raises(hive_return.ReturnError, match="do not apply onto main"):
        hive_return.land(DID, st["key"], st["tip"])
    assert git(world["host"], "rev-parse", "main") == before
    assert not list((world["tmp"] / "sw").glob("*/*"))
    assert git(world["host"], "branch", "--list", "session/*") == ""
    assert git(world["host"], "rev-parse", st["ref"]) == st["tip"]          # kept to land later
    assert not hasattr(hive_return, "reject")       # not good enough: re-run or discard


# ── routes ───────────────────────────────────────────────────────────────────

def test_the_hive_serves_a_bundle_only_on_a_hive(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.hive_brains as hb
    _commit(world["hive"], "src/a.py", "a = 2\n", "x")
    b = _hive_bundle(world)
    monkeypatch.setattr(hb, "_workspace", lambda: world["hive_ws"])
    app = FastAPI()
    app.include_router(hb.router, prefix="/api/hive")
    c = TestClient(app)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    assert c.get(f"/api/hive/bundles/{SID}/{b['key']}").status_code == 404
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    r = c.get(f"/api/hive/bundles/{SID}/{b['key']}")
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == b["sha256"]
    assert c.get(f"/api/hive/bundles/{SID}/{'0' * 16}").status_code == 404
    assert c.get(f"/api/hive/bundles/..%2F..%2Fx/{b['key']}").status_code == 404


def test_the_return_routes_are_host_only_and_say_why_they_refuse(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core import hive_return
    from routers.swarm_out import router
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    c = TestClient(app)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)

    def refuse(*a, **k):
        raise hive_return.ReturnError("the bundle has 2 refs; one is expected")
    monkeypatch.setattr(hive_return, "land", refuse)
    r = c.post(f"/api/swarm-out/runs/{DID}/return/land", json={"key": "k" * 16, "tip": "t" * 40})
    assert r.status_code == 409 and "2 refs" in r.json()["detail"]
    assert c.post(f"/api/swarm-out/runs/{DID}/return/prepare", json={}).status_code == 400
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    for path in ("prepare", "land"):
        assert c.post(f"/api/swarm-out/runs/{DID}/return/{path}", json={"url": URL}).status_code == 403
    gone = "re" + "ject"                             # the route that no longer exists
    assert c.post(f"/api/swarm-out/runs/{DID}/return/{gone}", json={}).status_code in (404, 405)


# ── review round 1 ───────────────────────────────────────────────────────────

def _upstream_moves(w, tmp_path) -> str:
    """Someone else pushes to origin; the Hive starts from that; the host has not pulled."""
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(w["origin"]), str(other))
    up = _commit(other, "src/b.py", "b = 1\n", "upstream work")
    git(other, "push", "-q", "origin", "main")
    git(w["hive"], "remote", "set-url", "origin", str(w["origin"]))
    git(w["hive"], "pull", "-q", "origin", "main")
    git(w["hive"], "remote", "set-url", "origin", URL)
    git(w["host"], "fetch", "-q", "origin")          # origin/main has it, local main does not
    return up


def test_commits_built_on_upstream_work_the_host_has_not_pulled_are_refused(world, monkeypatch, tmp_path):
    from core import hive_return
    _no_network(monkeypatch, world)
    up = _upstream_moves(world, tmp_path)
    _commit(world["hive"], "src/a.py", "a = 2\n", "hive work")
    _hive_bundle(world)
    with pytest.raises(hive_return.ReturnError, match="bring main up to date"):
        asyncio.run(hive_return.prepare(DID, URL))
    git(world["host"], "merge", "-q", "--ff-only", "origin/main")
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["base"] == up and [f["path"] for f in st["files"]] == ["src/a.py"]   # not the upstream file


def test_the_checkout_must_be_the_runs_branch_in_a_main_checkout(world, monkeypatch, tmp_path):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "x")
    _hive_bundle(world)
    git(world["host"], "checkout", "-q", "-b", "feature")
    with pytest.raises(hive_return.ReturnError, match="check out main first"):
        asyncio.run(hive_return.prepare(DID, URL))
    git(world["host"], "checkout", "-q", "--detach")
    with pytest.raises(hive_return.ReturnError, match="detached HEAD"):
        asyncio.run(hive_return.prepare(DID, URL))
    git(world["host"], "checkout", "-q", "main")
    linked = tmp_path / "linked"
    git(world["host"], "worktree", "add", "-q", str(linked), "feature")
    import core.project_registry as pr
    b = pr.get_swarmws() / "Projects" / "Lib" / "bindings.yaml"
    b.write_text(b.read_text().replace(str(world["host"]), str(linked)))
    with pytest.raises(hive_return.ReturnError, match="linked worktree"):
        asyncio.run(hive_return.prepare(DID, URL))


def test_protected_paths_match_whatever_the_case(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "Scripts/x.sh", "echo\n", "x")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["protected_files"] == ["Scripts/x.sh"]


def test_a_later_turn_lands_only_what_came_after_the_landed_tip(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "turn 1")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    hive_return.land(DID, st["key"], st["tip"])
    with pytest.raises(hive_return.ReturnError, match="already landed"):
        asyncio.run(hive_return.prepare(DID, URL))
    first = st["tip"]
    tip = _commit(world["hive"], "src/c.py", "c = 1\n", "turn 2")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["base"] == first and [f["path"] for f in st["files"]] == ["src/c.py"]
    before = git(world["host"], "rev-parse", "main")
    assert hive_return.land(DID, st["key"], tip)["landed"]
    assert git(world["host"], "diff", "--name-only", before, "main") == "src/c.py"
    assert (world["host"] / "src" / "a.py").read_text() == "a = 2\n"


def test_a_dirty_checkout_is_said_before_the_review(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "x")
    _hive_bundle(world)
    (world["host"] / "src" / "a.py").write_text("local edit\n")
    assert asyncio.run(hive_return.prepare(DID, URL))["checkout_dirty"] is True


def test_a_download_that_fails_or_differs_says_so(world, monkeypatch, tmp_path):
    import httpx
    import core.swarm_out as so
    from core import hive_return

    async def instance(ref):
        return {"id": "h", "name": "h"}
    monkeypatch.setattr(so, "_instance", instance)

    def down(r):
        raise httpx.ConnectError("no")
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(down), base_url="http://h"))
    b = {"key": "k" * 16, "sha256": "0" * 64, "size": 3}
    with pytest.raises(hive_return.ReturnError, match="did not answer"):
        asyncio.run(hive_return._download(world["row"], b, tmp_path / "x.bundle"))
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"12345")), base_url="http://h"))
    with pytest.raises(hive_return.ReturnError, match="refresh the result"):
        asyncio.run(hive_return._download(world["row"], b, tmp_path / "x.bundle"))


def test_a_too_large_change_is_never_bundled(world, monkeypatch):
    from core import hive_bundle
    _commit(world["hive"], "src/a.py", "a = 2\n" * 1000, "big")
    monkeypatch.setattr(hive_bundle, "MAX_BUNDLE_BYTES", 10)
    calls = []
    real = hive_bundle._git
    monkeypatch.setattr(hive_bundle, "_git", lambda cwd, *a: calls.append(a[:2]) or real(cwd, *a))
    b = _hive_bundle(world)
    assert b["error"] == hive_bundle.ERR_TOO_LARGE
    assert ("bundle", "create") not in calls                 # refused before writing anything
    assert not [p for p in (world["hive_ws"] / "Hive" / "records").rglob("*")
                if p.is_file() and p.name != ".lock"]


def test_a_slow_bundle_keeps_the_facts(world, monkeypatch):
    import time as _t
    from core import hive_bundle, task_repos
    from core.session_router import SessionRouter
    import database, types
    _commit(world["hive"], "src/a.py", "a = 2\n", "x")

    class _Sessions:
        async def get(self, sid):
            return {"metadata": {"task_repos": [{"url": URL, "ref": "main"}]}, "created_at": None}
    monkeypatch.setattr(database, "db", types.SimpleNamespace(sessions=_Sessions()))
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: world["hive_ws"])
    monkeypatch.setattr(hive_bundle, "add_bundles", lambda *a: _t.sleep(2) or a[2])
    monkeypatch.setenv("SWARMAI_MODE", "hive")

    class _Self:
        TASK_REPO_FACTS_TIMEOUT_S = 30
        TASK_REPO_BUNDLES_TIMEOUT_S = 0.2
    facts = asyncio.run(SessionRouter._task_repo_facts(_Self(), SID))
    assert facts[0]["commits_total"] == 1 and facts[0]["error"] is None
    assert facts[0]["bundle"] == {"key": hive_bundle.key(DIR), "error": hive_bundle.ERR_FAILED}
    assert task_repos.ERR_TIMEOUT not in str(facts)

    class _ReadOnly:
        async def get(self, sid):
            return {"metadata": {"task_repos": [{"url": URL, "ref": "main", "access": "read"}]}, "created_at": None}
    monkeypatch.setattr(database, "db", types.SimpleNamespace(sessions=_ReadOnly()))
    facts = asyncio.run(SessionRouter._task_repo_facts(_Self(), SID))
    assert facts[0]["commits_total"] == 1 and "bundle" not in facts[0]     # a read-only one is never bundled


# ── security review ──────────────────────────────────────────────────────────

def test_a_file_under_an_ignored_or_linked_directory_is_refused(world, monkeypatch, tmp_path):
    from core import hive_return
    _no_network(monkeypatch, world)
    (world["host"] / ".gitignore").write_text(".venv/\n")
    git(world["host"], "add", ".gitignore")
    git(world["host"], "commit", "-qm", "ignore venv")
    git(world["host"], "push", "-q", "origin", "main")
    git(world["hive"], "remote", "set-url", "origin", str(world["origin"]))
    git(world["hive"], "pull", "-q", "origin", "main")
    git(world["hive"], "remote", "set-url", "origin", URL)
    (world["hive"] / ".venv" / "lib").mkdir(parents=True)
    (world["hive"] / ".venv" / "lib" / "evil.pth").write_text("import os\n")
    git(world["hive"], "add", "-f", ".venv/lib/evil.pth")
    git(world["hive"], "commit", "-qm", "x")
    _hive_bundle(world)
    with pytest.raises(hive_return.ReturnError, match="may not"):
        asyncio.run(hive_return.prepare(DID, URL))
    assert not (world["host"] / ".venv").exists()


def test_git_behaviour_files_are_protected_in_every_repository(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], ".gitattributes", "* filter=lfs\n", "x")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["protected_files"] == [".gitattributes"]


def test_landing_writes_no_worktree_and_runs_no_hook(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    hooks = world["host"] / ".git" / "hooks"
    marker = world["tmp"] / "hook-ran"
    for h in ("post-merge", "pre-commit", "post-checkout", "reference-transaction"):
        (hooks / h).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / h).chmod(0o755)
    _commit(world["hive"], "src/a.py", "a = 2\n", "x")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    out = hive_return.land(DID, st["key"], st["tip"])
    assert out["landed"] and not marker.exists()
    assert (world["host"] / "src" / "a.py").read_text() == "a = 2\n"
    assert f"swarm-land hive/{DID}" in git(world["host"], "reflog", "-1", "--format=%gs", "main")


def test_more_files_than_the_review_shows_do_not_land_from_here(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    monkeypatch.setattr(hive_return, "MAX_FILES_SHOWN", 1)
    _commit(world["hive"], "src/a.py", "a = 2\n", "x")
    _commit(world["hive"], "src/b.py", "b = 2\n", "y")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    with pytest.raises(hive_return.ReturnError, match="more than the review shows"):
        hive_return.land(DID, st["key"], st["tip"])


def test_a_path_with_bidi_characters_is_refused(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a‮.py", "x\n", "x")
    _hive_bundle(world)
    with pytest.raises(hive_return.ReturnError, match="control characters"):
        asyncio.run(hive_return.prepare(DID, URL))


def test_a_session_id_the_hive_named_oddly_is_not_put_into_a_url(world):
    from core import hive_return
    world["row"]["session_id"] = "..%2f..%2fbrains"
    with pytest.raises(hive_return.ReturnError, match="oddly"):
        asyncio.run(hive_return.prepare(DID, URL))
