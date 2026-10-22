"""core/repo_changes — the Canvas Changes tab's repo state (read-only git).

Methodology: throwaway repos under tmp_path (a bare "origin" + a clone), allowed_roots
patched to them; no real repository is read.
Invariants: a repo row reports branch, ahead-of-upstream, dirty files, extra
worktrees and the unpushed commits with +/-; a clean, pushed repo is not a row;
commit_files / commit_diff answer only for an allowed root, a hex sha and a
repo-relative path; nothing writes the index (GIT_OPTIONAL_LOCKS=0, read-only verbs).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

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
    return work


def test_a_clean_pushed_repo_is_not_a_row(repo):
    assert rc.repos() == []


def test_row_reports_ahead_dirty_worktrees_and_commits(repo, tmp_path):
    (repo / "a.py").write_text("x = 2\ny = 3\n"); _git(repo, "commit", "-qam", "feat: two")
    (repo / "b.md").write_text("new\n")
    _git(repo, "worktree", "add", "-q", "-b", "kiro/x", str(tmp_path / "wt"))
    [row] = rc.repos()
    assert (row["name"], row["project"], row["branch"]) == ("swarmai", "SwarmAI", "main")
    assert row["upstream"] == "origin/main" and row["ahead"] == 1
    assert row["dirty_count"] == 1 and row["dirty"] == [{"path": "b.md", "status": "?"}]
    assert [w["branch"] for w in row["worktrees"]] == ["kiro/x"]
    [c] = row["commits"]
    assert c["subject"] == "feat: two" and (c["insertions"], c["deletions"], c["files"]) == (2, 1, 1)


def test_a_removed_worktree_dir_is_prunable(repo, tmp_path):
    import shutil
    _git(repo, "worktree", "add", "-q", "-b", "kiro/y", str(tmp_path / "wt2"))
    shutil.rmtree(tmp_path / "wt2")
    [row] = rc.repos()
    assert row["worktrees"][0]["prunable"] is True


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


def test_reading_never_writes_the_index(repo):
    (repo / "a.py").write_text("x = 5\n")
    index = Path(repo) / ".git" / "index"
    before = index.stat().st_mtime_ns
    rc.repos()
    assert index.stat().st_mtime_ns == before
    assert not (Path(repo) / ".git" / "index.lock").exists()


def test_router_maps_bad_input_to_400_and_lists_repos(repo):
    import asyncio
    from fastapi import HTTPException
    from routers import canvas_changes as r
    with pytest.raises(HTTPException) as e:
        asyncio.run(r.get_diff(root="/etc", sha="a" * 40, path="x"))
    assert e.value.status_code == 400
    assert asyncio.run(r.get_changes()) == {"repos": []}
