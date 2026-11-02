"""One changed file of a Hive run's commits, both versions, on this machine
(core/hive_return.file_versions; GET /api/swarm-out/runs/{id}/return/file).

Questions: does a file the prepared review lists come back at the reviewed base and at
the Hive's tip (None on the side where it does not exist, no content for a binary or a
too-large one), read from git's objects only (the checkout and its branch untouched);
is a path the review does not list, or one with ``..``, absolute or with a control
character refused before git is asked (400); are commits not taken back yet a 409.

The repositories and the prepared state are test_hive_return's own (real git).
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from tests.test_hive_return import DID, URL, _commit, _hive_bundle, _no_network, git, world  # noqa: F401


def _client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.swarm_out import router
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    return TestClient(app)


def _get(c, path, url=URL):
    return c.get(f"/api/swarm-out/runs/{DID}/return/file", params={"url": url, "path": path})


def _changes(w):
    """On the host's main (and the Hive's clone): docs/old.md. Then the Hive's commits:
    src/a.py changed, src/new.py added, docs/old.md deleted, assets/logo.bin binary."""
    host, hive = w["host"], w["hive"]
    _commit(host, "docs/old.md", "old\n", "docs")
    git(host, "push", "-q", "origin", "main")
    git(hive, "fetch", "-q", str(w["origin"]), "main:refs/remotes/origin/main")
    git(hive, "merge", "-q", "--ff-only", "refs/remotes/origin/main")
    _commit(hive, "src/a.py", "a = 2\n", "change a")
    _commit(hive, "src/new.py", "new = 1\n", "add new")
    git(hive, "rm", "-q", "docs/old.md")
    git(hive, "commit", "-q", "-m", "drop old")
    (hive / "assets").mkdir()
    (hive / "assets" / "logo.bin").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + b"\0" * 64)
    git(hive, "add", "-A")
    git(hive, "commit", "-q", "-m", "add logo")


def test_file_versions_for_listed_paths(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _changes(world)
    _hive_bundle(world)
    st = asyncio.run(hive_return.prepare(DID, URL))
    assert {f["path"] for f in st["files"]} == {"src/a.py", "src/new.py", "docs/old.md", "assets/logo.bin"}
    main_before = git(world["host"], "rev-parse", "main")

    v = hive_return.file_versions(DID, URL, "src/a.py")
    assert v == {"path": "src/a.py", "base_sha": st["base"], "tip_sha": st["tip"],
                 "base": "a = 1\n", "tip": "a = 2\n", "base_exists": True,
                 "tip_exists": True, "binary": False, "too_large": False}
    added = hive_return.file_versions(DID, URL, "src/new.py")
    assert added["base"] is None and added["tip"] == "new = 1\n" and not added["base_exists"]
    deleted = hive_return.file_versions(DID, URL, "docs/old.md")
    assert deleted["base"] == "old\n" and deleted["tip"] is None and not deleted["tip_exists"]
    binary = hive_return.file_versions(DID, URL, "assets/logo.bin")
    assert binary["binary"] is True and binary["base"] is None and binary["tip"] is None

    monkeypatch.setattr(hive_return, "FILE_VERSION_MAX_BYTES", 3)
    big = hive_return.file_versions(DID, URL, "src/a.py")
    assert big["too_large"] is True and big["base"] is None and big["tip"] is None
    monkeypatch.setattr(hive_return, "FILE_VERSION_MAX_BYTES", 1 << 20)

    # read from objects only: the checkout, its branch and its index are as they were
    assert git(world["host"], "rev-parse", "main") == main_before
    assert git(world["host"], "status", "--porcelain") == ""
    assert not (world["host"] / "src" / "new.py").exists()

    # the route, and the review still readable once landed
    c = _client(monkeypatch)
    r = _get(c, "src/a.py")
    assert r.status_code == 200 and r.json()["base"] == "a = 1\n" and r.json()["tip"] == "a = 2\n"
    hive_return.land(DID, st["key"], st["tip"])
    r = _get(c, "docs/old.md")
    assert r.status_code == 200 and r.json()["base"] == "old\n" and r.json()["tip"] is None


def test_file_versions_refuses_unlisted_paths(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "change a")
    _hive_bundle(world)
    asyncio.run(hive_return.prepare(DID, URL))
    c = _client(monkeypatch)
    calls: list = []
    real = hive_return._git
    monkeypatch.setattr(hive_return, "_git", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (calls.append(a), run(*a, **k))[1])
    bad = [".swarmai/pipeline.toml",                  # in the repository, not in the review
           "src/b.py", "../lib/src/a.py", "src/../src/a.py", "./src/a.py", "src//a.py",
           "/etc/passwd", str(world["host"] / "src" / "a.py"), "src/a.py\0", "src/\x07a.py",
           "src/a‮.py", ""]
    for p in bad:
        with pytest.raises(hive_return.ReturnPathError):
            hive_return.file_versions(DID, URL, p)
        assert _get(c, p).status_code == 400, p
    assert calls == []                                 # nothing read for any of them
    assert issubclass(hive_return.ReturnPathError, hive_return.ReturnError)
    assert _get(c, "src/a.py").status_code == 200 and calls  # the listed one is read


def test_file_versions_needs_prepare(world, monkeypatch):
    from core import hive_return
    _no_network(monkeypatch, world)
    _commit(world["hive"], "src/a.py", "a = 2\n", "change a")
    _hive_bundle(world)                                # a bundle, never taken back
    c = _client(monkeypatch)
    with pytest.raises(hive_return.ReturnError, match="not taken back") as e:
        hive_return.file_versions(DID, URL, "src/a.py")
    assert not isinstance(e.value, hive_return.ReturnPathError)
    r = _get(c, "src/a.py")
    assert r.status_code == 409 and "take them back first" in r.json()["detail"]
    assert _get(c, "src/a.py", url="https://github.com/acme/other.git").status_code == 409
    assert _get(c, "src/a.py", url="").status_code == 400
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    assert _get(c, "src/a.py").status_code == 403
