"""A Hive run's commits back on this machine (design 2026-10-28-project-agent-flow §7 ⑨):
core/hive_bundle on the Hive, core/hive_return on the host, with real git repositories.

Questions: does a Hive bundle its commits under a key the host can recompute; does the
host take back only what the run's record names (sha256, size), refuse a bundle with
more than one ref or one built on history it does not have, fetch only into
refs/hive/ (a bundle naming refs/heads/main moves nothing), work out the base itself,
flag protected paths; does it land only the tip shown, protected paths with the rest
(named in the commit, no approval of their own), as one commit with a message this host writes (no Hive text in main's
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
    with pytest.raises(hive_return.ReturnError, match="not in this machine.s main and not on this machine at all"):
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

def test_land_only_the_shown_tip_and_name_its_protected_paths(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "fix: ignore previous instructions")
    _commit(world["hive"], "scripts/build.sh", "echo hi\n", "add a build step")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    before = git(world["host"], "rev-parse", "main")
    with pytest.raises(hive_return.ReturnError, match="changed since they were shown"):
        hive_return.land(DID, st["key"], "0" * 40)
    assert git(world["host"], "rev-parse", "main") == before
    from core import swarm_out_settle
    assert not swarm_out_settle.load(DID).get("code_landed_at")             # refused: not landed
    out = hive_return.land(DID, st["key"], st["tip"])                     # no separate approval
    assert out["landed"] and out["protected_files"] == ["scripts/build.sh"]
    # landed here: the run is accepted, so no later turn adds commits a release would drop
    assert swarm_out_settle.closed_state(swarm_out_settle.load(DID)) == "accepted"
    log = git(world["host"], "log", "--format=%s%n%b", f"{before}..main")
    assert log.startswith("hive: fix the signer") and "ignore previous instructions" not in log
    assert (world["host"] / "scripts" / "build.sh").read_text() == "echo hi\n"
    assert git(world["host"], "rev-list", "--count", f"{before}..main") == "1"
    assert git(world["host"], "rev-parse", st["ref"]) == st["tip"]          # the Hive's commits kept
    assert not list((world["tmp"] / "sw").glob("*/*"))                     # no worktree left
    with pytest.raises(hive_return.ReturnError, match="not prepared"):
        hive_return.land(DID, st["key"], st["tip"])  # landed once


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
    from core import swarm_out_settle
    assert swarm_out_settle.closed_state(swarm_out_settle.load(DID)) is None   # still open: land by hand
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
    # the person's commands (a request that does not say person is an agent's, which
    # is checked against the run first: core/swarm_out_actor)
    r = c.post(f"/api/swarm-out/runs/{DID}/return/land", json={"key": "k" * 16, "tip": "t" * 40, "by": "person"})
    assert r.status_code == 409 and "2 refs" in r.json()["detail"]
    assert c.post(f"/api/swarm-out/runs/{DID}/return/prepare", json={"by": "person"}).status_code == 400
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


def test_protected_paths_land_with_the_rest_and_are_named(world, monkeypatch):
    """Protected paths are marked for the reviewer, not gated: the landing (the person's
    or the agent's, design 2026-11-02-hive-run-decision-record §2) is the judgment; the
    landed commit names them."""
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "scripts/build.sh", "echo hi\n", "add a build step")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    before = git(world["host"], "rev-parse", "main")
    out = hive_return.land(DID, st["key"], st["tip"], allow_git_exec=False)   # an agent's landing
    assert out["landed"] and out["protected_files"] == ["scripts/build.sh"]
    assert "Changes protected paths: scripts/build.sh" in git(world["host"], "log", "--format=%b", f"{before}..main")


def test_a_git_hook_from_a_hive_is_refused_to_the_agent(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], ".pre-commit-config.yaml", "repos: []\n", "a hook")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    before = git(world["host"], "rev-parse", "main")
    with pytest.raises(hive_return.ReturnError, match="make code run on their own"):
        hive_return.land(DID, st["key"], st["tip"], allow_git_exec=False)
    assert git(world["host"], "rev-parse", "main") == before
    assert hive_return.land(DID, st["key"], st["tip"])["landed"]   # the person may


#: one path of each kind in CI_AGENT_PROTECTED, nested agent files included
CI_AGENT_PATHS = [".github/workflows/ci.yml", ".gitlab-ci.yml", ".circleci/config.yml",
                  "azure-pipelines.yml", "Jenkinsfile", ".claude/settings.json", ".mcp.json",
                  ".cursor/rules/x.mdc", ".kiro/steering/x.md", "AGENTS.md", "pkg/sub/AGENTS.md",
                  "CLAUDE.md", "docs/CLAUDE.md", ".vscode/tasks.json", ".devcontainer/devcontainer.json"]


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "pkg/sub/AGENTS.md", ".claude/settings.json",
                                  ".mcp.json", ".gitlab-ci.yml"])
def test_ci_and_agent_config_files_are_held_for_the_person(world, monkeypatch, path):
    """A CI or agent-configuration file from a Hive is held for the person exactly like a
    git hook: the agent's landing is refused and main stays; the person's lands it and
    the landed commit names it."""
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "ordinary")
    _commit(world["hive"], path, "x: 1\n", "ci or agent config")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["protected_files"] == [path]
    before = git(world["host"], "rev-parse", "main")
    with pytest.raises(hive_return.ReturnError, match="make code run on their own") as e:
        hive_return.land(DID, st["key"], st["tip"], allow_git_exec=False)
    assert path in str(e.value)
    assert git(world["host"], "rev-parse", "main") == before
    assert hive_return.land(DID, st["key"], st["tip"])["landed"]   # the person may
    assert (world["host"] / path).read_text() == "x: 1\n"
    assert f"Changes protected paths: {path}" in git(world["host"], "log", "--format=%b", f"{before}..main")


def test_a_review_prepared_before_a_path_was_held_still_holds_it(world, monkeypatch):
    """A review state saved by an older version marked only GIT_PROTECTED: the agent's
    landing checks every file, not only the marked ones."""
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], ".github/workflows/ci.yml", "on: x\n", "ci")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    state = hive_return.load(DID, st["key"])
    state["protected_files"] = []
    for f in state["files"]:
        f["protected"] = False
    hive_return._save(DID, st["key"], state)
    with pytest.raises(hive_return.ReturnError, match=r"\.github/workflows/ci\.yml"):
        hive_return.land(DID, st["key"], st["tip"], allow_git_exec=False)


def test_ci_and_agent_config_files_are_marked_without_a_hive_return_table(world, monkeypatch):
    """The default set applies to a repository with no .swarmai/pipeline.toml at all."""
    from core import hive_return
    _no_network(monkeypatch, world)
    git(world["host"], "rm", "-q", "-r", ".swarmai")              # read from this machine's main
    git(world["host"], "commit", "-q", "-m", "no config")
    for p in CI_AGENT_PATHS:
        _commit(world["hive"], p, "x\n", p)
    _commit(world["hive"], "src/a.py", "a = 2\n", "ordinary")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert sorted(st["protected_files"]) == sorted(CI_AGENT_PATHS)
    assert {f["path"]: f["protected"] for f in st["files"]}["src/a.py"] is False


def test_the_person_only_set_matches_ci_and_agent_files_only():
    from core import hive_return
    held = CI_AGENT_PATHS + ["deep/a/b/.claude/hooks/x.sh", "Agents.md", ".GITHUB/Workflows/X.yml",
                             "svc/.mcp.json", ".github/actions/setup/action.yml", ".devcontainer.json",
                             ".pre-commit-config.yaml", "lib/.gitattributes", ".swarmai/review.md",
                             ".swarmai/pipeline.toml", "GEMINI.md", "pkg/GEMINI.md", ".gemini/settings.json",
                             ".codex/config.toml", ".github/copilot-instructions.md", ".clinerules",
                             ".windsurfrules", ".amazonq/rules/x.md", ".cursorrules", ".aider.conf.yml",
                             ".roo/rules/x.md", ".roomodes", ".continue/config.yaml", ".junie/guidelines.md",
                             ".roorules", ".roorules-code", ".kilocode/rules/x.md", ".continuerc.json"]
    assert hive_return.person_only(held) == held
    ordinary = ["src/a.py", "scripts/build.sh", "README.md", "docs/agents-guide.md", "MY_AGENTS.md",
                "AGENTS.md.bak", "github/workflows/ci.yml", ".github/CODEOWNERS", ".vscode/settings.json",
                "src/claude.py", "x.claude/y"]
    assert hive_return.person_only(ordinary) == []


def test_the_refusal_says_how_many_files_it_did_not_name():
    from core import hive_return
    paths = [f"pkg{i}/AGENTS.md" for i in range(12)]
    reason = hive_return.person_only_reason(paths)
    assert "pkg9/AGENTS.md" in reason and "pkg10/AGENTS.md" not in reason and reason.endswith(" and 2 more")
    assert "more" not in hive_return.person_only_reason(paths[:10])


def test_the_docs_name_the_person_only_set():
    """The texts an agent follows say what it never lands, and that [hive_return] only adds."""
    from core import hive_return, swarm_out_actor
    from core.run_protocol import child_merge
    for doc in (hive_return.__doc__, swarm_out_actor.__doc__, child_merge.__doc__):
        assert "PERSON_ONLY" in doc
    assert "no setting removes a person-only path" in hive_return.__doc__
    skill = (Path(__file__).resolve().parents[1] / "skills" / "s_swarm-out" / "SKILL.md").read_text()
    for word in (".github/workflows/", "AGENTS.md", ".mcp.json", "cannot remove these"):
        assert word in skill


def test_a_swarm_run_childs_commits_may_build_on_its_lead_runs_snapshot(world, monkeypatch, tmp_path):
    """Design 2026-10-31-swarm-run §5.4 (P2): a child dispatched from a snapshot of its
    lead run (``base``) brings commits whose prerequisite is that snapshot, not main.
    They are taken back for the lead run; any other run's are refused as before."""
    from core import hive_return
    _no_network(monkeypatch, world)
    host = world["host"]
    snap = git(host, "commit-tree", git(host, "write-tree"), "-p", "HEAD", "-m", "the lead run's contract")
    run = "run_0123456789ab"
    git(host, "update-ref", f"refs/swarm/snapshots/{run}/l1", snap)
    child = git(host, "commit-tree", git(host, "write-tree"), "-p", snap, "-m", "the child's work")
    git(host, "update-ref", "refs/tmp/child", child)
    f = tmp_path / "child.bundle"
    git(host, "bundle", "create", "-q", str(f), "refs/tmp/child", f"^{snap}")
    git(host, "update-ref", "-d", "refs/tmp/child")
    _hive_bundle(world, file=f)
    with pytest.raises(hive_return.ReturnError, match="not in this machine.s main"):
        asyncio.run(hive_return.prepare(DID, URL))
    world["row"]["parent_run"] = run
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["tip"] == child and git(host, "rev-parse", st["ref"]) == child
    world["row"]["parent_run"] = "run_ffffffffffff"                 # another run's snapshot does not count
    with pytest.raises(hive_return.ReturnError, match="not in this machine.s main"):
        asyncio.run(hive_return.prepare(DID, URL))


def test_a_swarm_run_child_is_reviewed_from_its_own_base_snapshot(world, monkeypatch, tmp_path):
    """A child starts from a snapshot of its lead run's worktree, which may hold the lead's
    own edits (a CI file, a tracked lock file an ignore pattern matches). What the child
    changed is what came after that snapshot: the lead's edits are never refused as the
    child's, and a tracked file is not "ignored" because a pattern matches its name."""
    from core import hive_return
    _no_network(monkeypatch, world)
    host = world["host"]
    _commit(host, ".gitignore", "*.lock\n", "ignore locks")
    (host / "uv.lock").write_text("v1\n")
    git(host, "add", "-f", "uv.lock")
    git(host, "commit", "-qm", "a tracked lock")
    git(host, "push", "-q", "origin", "main")
    # the lead run's worktree as its child's base: a CI job and a lock bump of its own
    (host / ".github" / "workflows").mkdir(parents=True)
    (host / ".github" / "workflows" / "release.yml").write_text("on: push\n")
    (host / "uv.lock").write_text("v2\n")
    git(host, "add", "-A", "-f")
    snap = git(host, "commit-tree", git(host, "write-tree"), "-p", "HEAD", "-m", "a child's base")
    git(host, "reset", "-q", "--hard", "HEAD")
    run = "run_0123456789ab"
    git(host, "update-ref", f"refs/swarm/snapshots/{run}/b1-lib", snap)
    git(host, "read-tree", snap)
    (host / "src" / "a.py").write_text("a = 2\n")
    git(host, "add", "src/a.py")
    child = git(host, "commit-tree", git(host, "write-tree"), "-p", snap, "-m", "the child's work")
    git(host, "read-tree", "HEAD")
    git(host, "checkout", "-q", "--", ".")
    git(host, "update-ref", "refs/tmp/child", child)
    f = tmp_path / "child.bundle"
    git(host, "bundle", "create", "-q", str(f), "refs/tmp/child", f"^{snap}")
    git(host, "update-ref", "-d", "refs/tmp/child")
    _hive_bundle(world, file=f)
    world["row"]["parent_run"] = run
    world["row"]["repos"][0]["sha"] = snap
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert [x["path"] for x in st["files"]] == ["src/a.py"] and st["protected_files"] == []
    # a lead snapshot that is not an ancestor of the returned tip is no start of it
    other = git(host, "commit-tree", git(host, "write-tree"), "-p", "HEAD", "-m", "unrelated")
    git(host, "update-ref", f"refs/swarm/snapshots/{run}/b2-lib", other)
    world["row"]["repos"][0]["sha"] = other
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["base"] != other and st["protected_files"] == [".github/workflows/release.yml"]
    # the run's chain is no child's start
    git(host, "update-ref", f"refs/swarm/snapshots/{run}/chain-lib", snap)
    git(host, "update-ref", "-d", f"refs/swarm/snapshots/{run}/b1-lib")
    world["row"]["repos"][0]["sha"] = snap
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["base"] != snap
    git(host, "update-ref", f"refs/swarm/snapshots/{run}/b1-lib", snap)
    # a child naming a start that is not one of its lead's snapshots is reviewed from main:
    # the lead's CI file then counts as the child's (the merge refuses it, PERSON_ONLY)
    world["row"]["repos"][0]["sha"] = git(host, "rev-parse", "HEAD")
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["protected_files"] == [".github/workflows/release.yml"]


def test_a_tracked_file_an_ignore_pattern_matches_may_be_changed(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    for repo in (world["host"], world["hive"]):
        (repo / ".gitignore").write_text("*.lock\n")
        (repo / "uv.lock").write_text("v1\n")
        git(repo, "add", "-f", ".gitignore", "uv.lock")
        git(repo, "commit", "-qm", "a tracked lock")
    git(world["host"], "push", "-q", "-f", "origin", "main")
    git(world["hive"], "remote", "set-url", "origin", str(world["origin"]))
    git(world["hive"], "fetch", "-q", "origin")
    git(world["hive"], "reset", "-q", "--hard", "origin/main")
    git(world["hive"], "remote", "set-url", "origin", URL)
    _commit(world["hive"], "uv.lock", "v2\n", "bump")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert [x["path"] for x in st["files"]] == ["uv.lock"]


def test_a_new_file_under_an_ignored_directory_is_refused_beside_a_tracked_one(world, monkeypatch):
    # only a path the base tracks escapes the ignore check: a new one beside it does not
    from core import hive_return
    _no_network(monkeypatch, world)
    for repo in (world["host"], world["hive"]):
        (repo / ".gitignore").write_text("vendor/\n")
        (repo / "vendor").mkdir(exist_ok=True)
        (repo / "vendor" / "keep.txt").write_text("v1\n")
        git(repo, "add", "-f", ".gitignore", "vendor/keep.txt")
        git(repo, "commit", "-qm", "a tracked vendored file")
    git(world["host"], "push", "-q", "-f", "origin", "main")
    git(world["hive"], "remote", "set-url", "origin", str(world["origin"]))
    git(world["hive"], "fetch", "-q", "origin")
    git(world["hive"], "reset", "-q", "--hard", "origin/main")
    git(world["hive"], "remote", "set-url", "origin", URL)
    (world["hive"] / "vendor" / "keep.txt").write_text("v2\n")
    (world["hive"] / "vendor" / "evil.pth").write_text("import os\n")
    git(world["hive"], "add", "-f", "vendor/keep.txt", "vendor/evil.pth")
    git(world["hive"], "commit", "-qm", "x")
    _hive_bundle(world)
    with pytest.raises(hive_return.ReturnError, match="vendor/evil.pth"):
        asyncio.run(hive_return.prepare(DID, URL))


def test_the_landed_commit_names_its_dispatch(world, monkeypatch):
    # a reader of the history tells this Hive run's commit from a person's: the landed
    # commit carries the repository's trailer and Hive-Dispatch in one trailer paragraph
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 3\n", "fix")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    before = git(world["host"], "rev-parse", "main")
    hive_return.land(DID, st["key"], st["tip"])
    trailers = git(world["host"], "log", "-1", "--format=%(trailers:only,unfold)", "main")
    assert f"Hive-Dispatch: {DID}" in trailers.splitlines()
    assert git(world["host"], "rev-list", "--count", f"{before}..main") == "1"


# ── a branch rewritten after the dispatch ────────────────────────────────────

def _rewritten(w, *, hive_file: str = "src/a.py", hive_text: str = "a = 2\n") -> dict:
    """main gets a config line and a feature, the Hive starts from them and commits; then
    main is rewritten (the config line scrubbed, the feature re-applied with a new sha):
    the bundle's prerequisite is the old feature commit, still in this machine's objects."""
    host = w["host"]
    start = git(host, "rev-parse", "main")
    _commit(host, "conf.txt", "host = real.example\nport = 1\n", "add the config")
    old = _commit(host, "src/b.py", "b = 1\n", "add the feature")
    git(host, "push", "-q", "origin", "main")
    git(w["hive"], "remote", "set-url", "origin", str(w["origin"]))
    git(w["hive"], "pull", "-q", "--ff-only", "origin", "main")
    git(w["hive"], "remote", "set-url", "origin", URL)
    hive_tip = _commit(w["hive"], hive_file, hive_text, "the Hive's fix")
    git(host, "checkout", "-q", "-B", "main", start)              # the scrub: one line differs
    _commit(host, "conf.txt", "host = example.invalid\nport = 1\n", "add the config")
    git(host, "cherry-pick", old)
    new = git(host, "rev-parse", "main")
    assert new != old
    return {"old": old, "new": new, "hive_tip": hive_tip}


def _key_of_dir() -> str:
    from core import hive_bundle
    return hive_bundle.key(DIR)


def test_a_rewritten_branch_brings_the_bundle_home_replayed_onto_the_mapped_base(world, monkeypatch):
    """The base the Hive built on was rewritten here (same patch, new sha; an earlier
    commit differs by one line): the commits come home replayed onto its equivalent, the
    review shows only the Hive's change (protected paths marked on the replayed diff), the
    landed tree is the branch plus exactly that diff, and a later turn replays to the same
    commits, so only what came after lands."""
    from core import hive_return
    _no_network(monkeypatch, world)
    host = world["host"]
    r = _rewritten(world)
    _commit(world["hive"], "scripts/x.sh", "echo\n", "a build step")
    _hive_bundle(world)
    main_before = git(host, "rev-parse", "main")
    st = asyncio.run(hive_return.prepare(DID, URL))
    # what the Hive built against that main no longer has is named: the scrubbed line's file
    assert st["base_mapped"] == {"from": r["old"], "to": r["new"], "differs": ["conf.txt"], "differs_total": 1}
    assert st["base"] == r["new"] and st["tip"] != st["hive_tip"]
    assert git(host, "rev-parse", st["ref"]) == st["tip"]                  # the reviewed tip
    assert git(host, "rev-parse", st["hive_ref"]) == st["hive_tip"]         # the Hive's own, kept
    assert git(host, "rev-parse", f"{st['tip']}~2") == r["new"]
    assert sorted(f["path"] for f in st["files"]) == ["scripts/x.sh", "src/a.py"]   # not conf.txt
    assert st["protected_files"] == ["scripts/x.sh"]
    assert [c["subject"] for c in st["commits"]] == ["a build step", "the Hive's fix"]
    assert git(host, "rev-parse", "main") == main_before                    # nothing moved yet
    assert git(host, "status", "--porcelain") == ""
    assert hive_return.load(DID, st["key"])["base_mapped"]["to"] == r["new"]
    review_diff = git(host, "diff", st["base"], st["tip"])
    out = hive_return.land(DID, st["key"], st["tip"])
    assert out["landed"]
    assert git(host, "diff", main_before, "main") == review_diff           # exactly what was reviewed
    assert (host / "conf.txt").read_text() == "host = example.invalid\nport = 1\n"
    msg = git(host, "log", "-1", "--format=%B", "main")
    assert f"replayed onto {r['new'][:12]}" in msg and f"at refs/hive/{DID}/{st['key']}-hive" in msg
    assert "The two bases differ in: conf.txt." in msg
    first = st["tip"]
    _commit(world["hive"], "src/c.py", "c = 1\n", "turn 2")
    _hive_bundle(world)
    st2 = asyncio.run(hive_return.prepare(DID, URL))
    assert st2["base"] == first and [f["path"] for f in st2["files"]] == ["src/c.py"]
    landed_head = git(host, "rev-parse", "main")
    assert hive_return.land(DID, st2["key"], st2["tip"])["landed"]
    assert git(host, "diff", "--name-only", landed_head, "main") == "src/c.py"


def test_a_rewritten_base_with_no_equivalent_is_refused_as_before(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    host = world["host"]
    r = _rewritten(world)
    git(host, "reset", "-q", "--hard", "main~1")                           # the feature changed too
    _commit(host, "src/b.py", "b = 2\n", "add the feature")
    _hive_bundle(world)
    before = git(host, "rev-parse", "main")
    with pytest.raises(hive_return.ReturnError) as e:
        asyncio.run(hive_return.prepare(DID, URL))
    assert str(e.value) == (f"the bundle builds on {r['old'][:12]}, which is not in this machine's main "
                            "(bring main up to date with its upstream, then take the commits back again)")
    assert git(host, "rev-parse", "main") == before and hive_return.load(DID, _key_of_dir()) is None


def test_a_rewritten_base_with_two_equivalents_is_refused(world, monkeypatch):
    """Two commits of main make the old base's change: the one with its author and subject
    is its equivalent; two of those are refused, naming them."""
    from core import hive_return
    _no_network(monkeypatch, world)
    host = world["host"]
    r = _rewritten(world)
    git(host, "revert", "--no-edit", "HEAD")
    git(host, "cherry-pick", "-n", r["old"])
    git(host, "commit", "-q", "-m", "the feature again, under another subject")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert (st["base_mapped"]["from"], st["base_mapped"]["to"]) == (r["old"], r["new"])   # the subject decided
    git(host, "revert", "--no-edit", "HEAD")
    git(host, "cherry-pick", r["old"])                                # same author and subject
    again = git(host, "rev-parse", "HEAD")
    with pytest.raises(hive_return.ReturnError, match="2 commits of main make the same change") as e:
        asyncio.run(hive_return.prepare(DID, URL))
    assert r["new"][:12] in str(e.value) and again[:12] in str(e.value)


def test_a_replay_that_conflicts_is_refused_naming_the_file(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    host = world["host"]
    r = _rewritten(world, hive_file="conf.txt", hive_text="host = real.example\nport = 2\n")
    _hive_bundle(world)
    before = git(host, "rev-parse", "main")
    ref = f"refs/hive/{DID}/{_key_of_dir()}"
    with pytest.raises(hive_return.ReturnError,
                       match=rf"conflicts in conf\.txt; take the change over by hand from {ref}-hive"):
        asyncio.run(hive_return.prepare(DID, URL))
    assert git(host, "rev-parse", f"{ref}-hive") == r["hive_tip"]           # kept for the takeover
    # the run's ref (a next round starts from it) never names the history main rewrote
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", ref], cwd=host,
                          capture_output=True).returncode != 0
    assert hive_return.load(DID, _key_of_dir()) is None and git(host, "rev-parse", "main") == before
    assert git(host, "status", "--porcelain") == ""


def test_a_second_rewrite_after_a_replayed_landing_still_takes_the_next_turn(world, monkeypatch):
    """main is rewritten again after a replayed landing (the landing too): the next turn's
    commits replay onto the newest equivalent, and the Hive tip that landed is found in
    that replay, so only what came after it is reviewed and landed."""
    from core import hive_return
    _no_network(monkeypatch, world)
    host = world["host"]
    r = _rewritten(world)
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    hive_return.land(DID, st["key"], st["tip"])
    landing = git(host, "rev-parse", "main")
    git(host, "checkout", "-q", "-B", "main", f"{r['new']}~2")               # the second scrub
    _commit(host, "conf.txt", "host = scrubbed.invalid\nport = 1\n", "add the config")
    git(host, "cherry-pick", r["new"])
    newer = git(host, "rev-parse", "main")
    git(host, "cherry-pick", landing)
    _commit(world["hive"], "src/c.py", "c = 1\n", "turn 2")
    _hive_bundle(world)
    st2 = asyncio.run(hive_return.prepare(DID, URL))
    assert st2["base_mapped"]["to"] == newer
    assert [f["path"] for f in st2["files"]] == ["src/c.py"]
    head = git(host, "rev-parse", "main")
    assert hive_return.land(DID, st2["key"], st2["tip"])["landed"]
    assert git(host, "diff", "--name-only", head, "main") == "src/c.py"


def test_a_replay_onto_more_than_one_rewritten_base_says_so_not_a_merge(world, monkeypatch):
    from core import hive_return
    r = _rewritten(world)
    git(world["hive"], "bundle", "create", "-q", str(world["tmp"] / "x.bundle"), f"{r['old']}..main")
    git(world["host"], "fetch", "-q", str(world["tmp"] / "x.bundle"), "main:refs/tmp/hive")
    two = {r["old"]: r["new"], f"{r['old']}~1": f"{r['new']}~1"}
    with pytest.raises(hive_return.ReturnError, match="build on 2 commits main rewrote") as e:
        hive_return._replay(world["host"], "refs/heads/main", "refs/tmp/hive", r["hive_tip"], two)
    assert "merge" not in str(e.value)


def test_a_person_only_path_replayed_onto_a_mapped_base_is_still_held(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    host = world["host"]
    _rewritten(world, hive_file=".githooks/pre-commit", hive_text="#!/bin/sh\necho hi\n")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["base_mapped"] and ".githooks/pre-commit" in st["protected_files"]
    before = git(host, "rev-parse", "main")
    with pytest.raises(hive_return.ReturnError, match=r"\.githooks/pre-commit"):
        hive_return.land(DID, st["key"], st["tip"], allow_git_exec=False)
    assert git(host, "rev-parse", "main") == before


def test_a_merge_built_on_a_rewritten_base_is_refused(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    hive = world["hive"]
    _rewritten(world)
    git(hive, "checkout", "-q", "-b", "side", "HEAD~1")
    _commit(hive, "src/d.py", "d = 1\n", "side work")
    git(hive, "checkout", "-q", "main")
    git(hive, "merge", "-q", "--no-edit", "side")
    _hive_bundle(world)
    with pytest.raises(hive_return.ReturnError, match="include a merge"):
        asyncio.run(hive_return.prepare(DID, URL))


def _host_has_hive_commits(r, w) -> None:
    git(w["hive"], "bundle", "create", "-q", str(w["tmp"] / "x.bundle"), f"{r['old']}..main")
    git(w["host"], "fetch", "-q", str(w["tmp"] / "x.bundle"), "main:refs/tmp/hive")


def test_a_named_base_the_commits_do_not_build_on_is_refused_not_replayed(world, monkeypatch):
    """The bundle's header is the Hive's: when it names an old commit while the commits
    really build on today's main, the branch's own commits are not pulled into the replay."""
    from core import hive_return
    r = _rewritten(world)
    host = world["host"]
    git(host, "checkout", "-q", "-b", "forged")
    forged = _commit(host, "src/f.py", "f = 1\n", "built on today's main")
    git(host, "checkout", "-q", "main")
    before = git(host, "rev-parse", "main")
    with pytest.raises(hive_return.ReturnError, match=f"do not build on {r['old'][:12]}"):
        hive_return._replay(host, "refs/heads/main", "refs/tmp/hive", forged, {r["old"]: r["new"]})
    assert git(host, "rev-parse", "main") == before


def test_a_named_base_whose_chain_reaches_the_branch_is_refused(world, monkeypatch):
    """Commits that build on the named base one after another, but one of them is already
    on the branch: refused, so no commit of the branch is replayed again."""
    from core import hive_return
    r = _rewritten(world)
    host = world["host"]
    git(host, "checkout", "-q", "-b", "side", r["old"])
    on_branch = _commit(host, "src/s.py", "s = 1\n", "a side commit")
    git(host, "checkout", "-q", "main")
    git(host, "merge", "-q", "--no-edit", "--no-ff", "-X", "ours", on_branch)
    with pytest.raises(hive_return.ReturnError, match="already in main"):
        hive_return._replay(host, "refs/heads/main", "refs/tmp/hive", on_branch, {r["old"]: r["new"]})


def test_a_replay_longer_than_the_limit_is_refused(world, monkeypatch):
    from core import hive_return
    r = _rewritten(world)
    _commit(world["hive"], "src/c.py", "c = 1\n", "second")
    _host_has_hive_commits(r, world)
    tip = git(world["hive"], "rev-parse", "main")
    monkeypatch.setattr(hive_return, "MAX_REPLAY_COMMITS", 1)
    with pytest.raises(hive_return.ReturnError, match="are 2 commits, more than the 1 replayed here"):
        hive_return._replay(world["host"], "refs/heads/main", "refs/tmp/hive", tip, {r["old"]: r["new"]})
    monkeypatch.setattr(hive_return, "MAX_REPLAY_COMMITS", 2)
    out, done = hive_return._replay(world["host"], "refs/heads/main", "refs/tmp/hive", tip, {r["old"]: r["new"]})
    assert len(done) == 2


# ── discarding after a refused take-back: the kept bundle holds the commits here ──

def _discard_row(w) -> dict:
    """The run's ``/runs`` row as the discard check reads it: stopped, final, its commits
    not landed (the public result says only that a bundle is available)."""
    rec = w["record"]["result"]["work"]["repos"][0]
    return {"dispatch_id": DID, "state": "stopped",
            "result": {"ended": "completed", "provisional": False,
                       "work": {"repos": [{"dir": rec["dir"], "commits_total": rec.get("commits_total") or 1,
                                           "bundle": {"available": True}}]}},
            "settle": {"why_not": ["verdict", "code"]}}


def _refused_by_a_replay_conflict(world, monkeypatch) -> dict:
    from core import hive_return
    _no_network(monkeypatch, world)
    r = _rewritten(world, hive_file="conf.txt", hive_text="host = real.example\nport = 2\n")
    _hive_bundle(world)
    with pytest.raises(hive_return.ReturnError, match="conflicts in conf.txt"):
        asyncio.run(hive_return.prepare(DID, URL))
    assert hive_return.load(DID, _key_of_dir()) is None                  # not prepared
    return r


def _bundle_file(world) -> Path:
    return world["tmp"] / "state" / "returns" / DID / f"{_key_of_dir()}.bundle"


def test_after_a_replay_conflict_the_kept_bundle_lets_an_agent_discard(world, monkeypatch):
    """The take-back was refused, but the verified bundle is kept here and its tip is at the
    ``-hive`` ref: discarding loses nothing. Without the tip here it refuses again."""
    from core import swarm_out_actor as act
    r = _refused_by_a_replay_conflict(world, monkeypatch)
    row = _discard_row(world)
    assert act.commits_here(DID, row["result"]) and act.discard_blocker(row) is None
    git(world["host"], "update-ref", "-d", f"refs/hive/{DID}/{_key_of_dir()}-hive")
    assert "not landed" in (act.discard_blocker(row) or "")             # the tip is not here
    git(world["host"], "update-ref", f"refs/hive/{DID}/{_key_of_dir()}-hive", r["old"])
    assert "not landed" in (act.discard_blocker(row) or "")             # another commit there


def test_a_kept_bundle_file_that_is_gone_refuses_the_discard(world, monkeypatch):
    from core import swarm_out_actor as act
    _refused_by_a_replay_conflict(world, monkeypatch)
    row = _discard_row(world)
    assert act.discard_blocker(row) is None
    _bundle_file(world).unlink()
    assert "not landed" in (act.discard_blocker(row) or "")


def test_a_kept_bundle_that_fails_verify_refuses_the_discard(world, monkeypatch):
    """The file is the one the result names (its sha256 and size), and its tip is here, but
    ``git bundle verify`` fails in the checkout (a prerequisite it names is not there)."""
    from core import swarm_out_actor as act
    _refused_by_a_replay_conflict(world, monkeypatch)
    row = _discard_row(world)
    assert act.discard_blocker(row) is None
    f = _bundle_file(world)
    data = f.read_bytes()
    head, sep, pack = data.partition(b"\n\n")
    lines = head.split(b"\n")
    i = next(n for n, ln in enumerate(lines) if ln.startswith(b"-"))
    lines[i] = b"-" + b"0" * 40 + b" never here"
    data = b"\n".join(lines) + sep + pack
    f.write_bytes(data)
    b = world["record"]["result"]["work"]["repos"][0]["bundle"]
    b.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    assert subprocess.run(["git", "bundle", "verify", "-q", str(f)], cwd=world["host"],
                          capture_output=True).returncode != 0
    assert "not landed" in (act.discard_blocker(row) or "")


def test_a_kept_bundle_of_an_earlier_turn_refuses_the_discard(world, monkeypatch):
    """A later turn brought new commits and the result names its bundle now: the file kept
    from the refused take-back holds only the earlier turn's commits."""
    from core import swarm_out_actor as act
    _refused_by_a_replay_conflict(world, monkeypatch)
    row = _discard_row(world)
    assert act.discard_blocker(row) is None
    _commit(world["hive"], "src/c.py", "c = 1\n", "turn 2")
    _hive_bundle(world)                                  # not taken back here
    assert "not landed" in (act.discard_blocker(_discard_row(world)) or "")


def test_prepared_and_landed_commits_still_let_an_agent_discard(world, monkeypatch):
    from core import hive_return, swarm_out_actor as act
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "the fix")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    row = _discard_row(world)
    assert act.discard_blocker(row) is None                              # prepared
    git(world["host"], "update-ref", "-d", st["ref"])
    assert "not landed" in (act.discard_blocker(row) or "")             # its ref is gone
    git(world["host"], "update-ref", st["ref"], st["tip"])
    assert hive_return.land(DID, st["key"], st["tip"])["landed"]
    assert act.discard_blocker(row) is None                              # landed


def test_an_earlier_turns_prepared_commits_do_not_let_an_agent_discard_a_later_turn(world, monkeypatch):
    """Turn 1 was taken back; the run was re-run and turn 2 sealed new commits nobody took
    back: the Hive workspace holds turn 2's only copy. Taking turn 2 back opens the way."""
    from core import hive_return, swarm_out_actor as act
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "turn 1")
    _hive_bundle(world)
    asyncio.run(hive_return.prepare(DID, URL))
    _bundle_file(world).unlink()                         # only the take-back says "here"
    assert act.discard_blocker(_discard_row(world)) is None
    _commit(world["hive"], "src/c.py", "c = 1\n", "turn 2")
    _hive_bundle(world)                                  # sealed, not taken back
    row = _discard_row(world)
    assert not act.commits_here(DID, row["result"])
    assert "not landed" in (act.discard_blocker(row) or "")
    asyncio.run(hive_return.prepare(DID, URL))           # turn 2 taken back
    _bundle_file(world).unlink()
    assert act.discard_blocker(row) is None


def test_an_earlier_turns_landed_commits_do_not_let_an_agent_discard_a_later_turn(world, monkeypatch):
    """Turn 1 landed; turn 2 sealed new commits and nobody took them back. The landed turn
    itself keeps its way out while it is the result's (re-preparing it is refused)."""
    from core import hive_return, swarm_out_actor as act
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "turn 1")
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    _bundle_file(world).unlink()                         # only the take-back says "here"
    assert hive_return.land(DID, st["key"], st["tip"])["landed"]
    _hive_bundle(world)                                  # a re-run with no new commit
    assert act.discard_blocker(_discard_row(world)) is None
    with pytest.raises(hive_return.ReturnError, match="already landed"):
        asyncio.run(hive_return.prepare(DID, URL))
    _commit(world["hive"], "src/c.py", "c = 1\n", "turn 2")
    _hive_bundle(world)
    row = _discard_row(world)
    assert "not landed" in (act.discard_blocker(row) or "")
    asyncio.run(hive_return.prepare(DID, URL))
    _bundle_file(world).unlink()
    assert act.discard_blocker(row) is None


def test_a_mapped_base_take_back_lets_an_agent_discard_only_its_own_turn(world, monkeypatch):
    """With a mapped base the state's tip is the replayed commit and the result's head the
    Hive's: prepared and landed still let an agent discard; a later turn sealed and not
    taken back does not. The kept bundle file is removed, so only the take-back says so
    (``kept_here`` would find the Hive's tip at the ``-hive`` ref)."""
    from core import hive_return, swarm_out_actor as act
    _no_network(monkeypatch, world)
    _rewritten(world)
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert st["tip"] != st["hive_tip"]
    _bundle_file(world).unlink()
    assert not hive_return.kept_here(DID, DIR)
    assert act.discard_blocker(_discard_row(world)) is None             # prepared
    assert hive_return.land(DID, st["key"], st["tip"])["landed"]
    assert act.discard_blocker(_discard_row(world)) is None             # landed
    _commit(world["hive"], "src/c.py", "c = 1\n", "turn 2")
    _hive_bundle(world)
    assert "not landed" in (act.discard_blocker(_discard_row(world)) or "")
