"""scripts/session_worktree after a squash landing (real git in a temporary repository).

A Hive run's commits land on this machine as one new single-parent commit
(core/hive_return ``land``: merge-tree --write-tree + commit-tree, then a fast-forward), so
a local verification worktree that holds the same commits is never an ancestor of the
base, and a rebase of it conflicts (the squash has another patch-id). Questions:

1. does ``remove`` remove such a worktree, and ``land`` answer "already landed" without
   rebasing, when merging the branch into the base would change nothing?
2. does ``remove`` still refuse a branch with content the base does not have (nothing
   removed), and ``land`` still land it through its rebase?
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess

import pytest


def _g(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", *a], check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    data = tmp_path / "appdata"
    (data / "SwarmWS").mkdir(parents=True)
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: data / "SwarmWS")
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for name in ("a.py", "b.py", "c.py"):
        (path / name).write_text(f"{name[0]} = 1\n")
    _g(path, "add", "-A")
    _g(path, "commit", "-qm", "init")
    return path


def _cli(*args):
    from scripts import session_worktree as sw
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sw.main(list(args))
    return rc, json.loads(buf.getvalue())


def _commit(wt, name, text, msg):
    with open(os.path.join(wt, name), "w") as f:
        f.write(text)
    _g(wt, "commit", "-qam", msg)


def _squash_land(repo, branch, msg="squash-landed"):
    """What return-land does: the branch's whole change as ONE new commit on main."""
    _g(repo, "merge", "--squash", "-q", branch)
    _g(repo, "commit", "-qm", msg)


def _verification_worktree(tmp_path, name):
    repo = _repo(tmp_path / "proj")
    rc, out = _cli("create", str(repo), "--name", name)
    assert rc == 0, out
    wt = out["path"]
    _commit(wt, "a.py", "a = 2\n", "hive commit 1")
    _commit(wt, "b.py", "b = 2\n", "hive commit 2")
    return repo, wt, out["branch"]


def test_remove_and_land_after_a_squash_landing(tmp_path):
    repo, wt, branch = _verification_worktree(tmp_path, "sq1")
    _squash_land(repo, branch)
    # the base moved on after the landing (another session)
    (repo / "c.py").write_text("c = 2\n")
    _g(repo, "commit", "-qam", "later work")
    head = _g(repo, "rev-parse", "main").strip()
    assert subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", branch, "main"]).returncode == 1

    rc, out = _cli("land", wt)
    assert rc == 0, out
    assert out["landed"] is False and out["already_landed"] is True
    assert _g(repo, "rev-parse", "main").strip() == head              # nothing moved
    assert "hive commit 2" in _g(wt, "log", "-1", "--format=%s")       # not rebased
    assert not os.path.exists(os.path.join(wt, ".git", "rebase-merge"))

    rc, out = _cli("remove", wt)
    assert rc == 0, out
    assert not os.path.exists(wt)
    assert branch not in _g(repo, "branch", "--list")
    assert (repo / "a.py").read_text() == "a = 2\n" and (repo / "c.py").read_text() == "c = 2\n"


def test_remove_still_refuses_a_branch_with_unlanded_content(tmp_path):
    repo, wt, branch = _verification_worktree(tmp_path, "sq2")
    _squash_land(repo, branch)
    _commit(wt, "c.py", "c = 9  # not landed\n", "after the landing")
    tip = _g(wt, "rev-parse", "HEAD").strip()

    rc, out = _cli("remove", wt)
    assert rc == 1 and "not merged" in out["error"]
    assert os.path.exists(wt) and branch in _g(repo, "branch", "--list")
    assert _g(wt, "rev-parse", "HEAD").strip() == tip

    # land takes its rebase path: the squashed commits' content is there, the new one lands
    rc, out = _cli("land", wt)
    assert rc == 0, out
    assert out["landed"] is True and "already_landed" not in out
    assert (repo / "c.py").read_text() == "c = 9  # not landed\n"
    rc, out = _cli("remove", wt)
    assert rc == 0, out



def test_remove_keeps_a_squashed_branch_that_moved_after_the_check(tmp_path, monkeypatch):
    """A commit made in the worktree after remove judged the branch (another turn of its
    session) is never deleted with it: the branch is deleted only at the tip it judged."""
    from scripts import session_worktree as sw
    repo, wt, branch = _verification_worktree(tmp_path, "sq3")
    _squash_land(repo, branch)
    real = sw._content_in_base

    def judged_then_moved(main, b, base):
        got = real(main, b, base)
        _commit(wt, "c.py", "c = 7  # made meanwhile\n", "meanwhile")
        return got
    monkeypatch.setattr(sw, "_content_in_base", judged_then_moved)
    rc, out = _cli("remove", wt)
    assert rc == 1 and "moved" in out["error"]
    assert branch in _g(repo, "branch", "--list")
    assert "meanwhile" in _g(repo, "log", "-1", "--format=%s", branch)
