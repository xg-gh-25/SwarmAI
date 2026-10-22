"""core/repo_changes — a commit's files and patches for the Canvas Changes tab.

Methodology: throwaway repos under tmp_path (a bare "origin" + a clone), the bindings
patched to them; no real repository is read.
Invariants: commit_files / commit_diff answer only for an allowed root, a hex sha and
a repo-relative path; the router maps bad input to 400.
"""
from __future__ import annotations

import subprocess

import pytest

from core import repo_changes as rc


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "swarmai"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "a@b.c"); _git(work, "config", "user.name", "T")
    (work / "a.py").write_text("x = 1\n"); _git(work, "add", "."); _git(work, "commit", "-qm", "base")
    _git(work, "push", "-q", "origin", "HEAD:main"); _git(work, "branch", "-u", "origin/main")
    real = str(work.resolve())
    monkeypatch.setattr(rc, "_bindings", lambda: {real: ("swarmai", "SwarmAI")})
    from core import turn_changes as tc
    monkeypatch.setattr(tc, "code_roots", lambda: [real])   # the other allowlist
    return work


def test_commit_files_and_diff(repo):
    (repo / "a.py").write_text("x = 9\n"); _git(repo, "commit", "-qam", "change")
    sha = _git(repo, "rev-parse", "HEAD")
    root = str(repo.resolve())
    assert rc.commit_files(root, sha) == [{"path": "a.py", "insertions": 1, "deletions": 1}]
    d = rc.commit_diff(root, sha, "a.py")
    assert "-x = 1" in d["diff"] and "+x = 9" in d["diff"] and d["truncated"] is False


@pytest.mark.parametrize("root,sha,path,msg", [
    ("/etc", "a" * 40, "a.py", "unknown repo"),
    (None, "HEAD;rm", "a.py", "bad sha"),
    (None, "a" * 40, "../secret", "bad path"),
    (None, "a" * 40, "-p", "bad path"),
    (None, "a" * 40, "/abs", "bad path"),
])
def test_bad_input_is_refused(repo, root, sha, path, msg):
    with pytest.raises(rc.RepoChangesError, match=msg):
        rc.commit_diff(root or str(repo.resolve()), sha, path)


def test_router_maps_bad_input_to_400(repo):
    import asyncio
    from fastapi import HTTPException
    from routers import canvas_changes as r
    with pytest.raises(HTTPException) as e:
        asyncio.run(r.get_diff(root="/etc", sha="a" * 40, path="x"))
    assert e.value.status_code == 400
