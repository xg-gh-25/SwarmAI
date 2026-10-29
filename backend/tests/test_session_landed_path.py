"""Tests for session_landings.landed_path — a removed session worktree's file opens
from the main checkout (design docs/design/2026-10-25-landings-consumers.md D1).

What is tested (real git repos, landings made by the real session_worktree CLI):
- land + remove: the worktree path maps to the same file in the main checkout,
  with this session's id or with none (an unambiguous name);
- fail closed: another session's key, two keys for one name without a session id, two
  bound checkouts whose basenames are equal or differ only in case, a name with no
  stamp, a ``..`` path, a path that still exists, a pipeline-run worktree path;
- the same session landing one name twice still maps (one branch, one checkout).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess

import pytest

from core import session_landings as sl

SID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"
T1 = "0123456789abcdef0123456789abcdef"


def _g(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    data = tmp_path / "appdata"
    (data / "SwarmWS").mkdir(parents=True)
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    monkeypatch.setenv("SWARM_SESSION_WORKTREE_ROOT", str(tmp_path / "swt"))
    # landed_path maps only a checkout under $HOME; the repos live in tmp_path, which is
    # under the system temp dir, not under the real home (the four mapping tests failed
    # for that reason alone, and the outside-$HOME test passed without testing anything)
    monkeypatch.setenv("HOME", os.path.realpath(tmp_path))
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    sl._LANDED_PATHS.clear()


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "a.py").write_text("a = 1\n")
    _g(path, "add", "-A")
    _g(path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init")
    return os.path.realpath(path)


def _cli(monkeypatch, sid, *args):
    from scripts import session_worktree as sw
    monkeypatch.setenv("SWARM_APP_SESSION_ID", sid)
    monkeypatch.setenv("SWARM_TURN_ID", T1)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sw.main(list(args))
    return json.loads(buf.getvalue())


def _land_and_remove(monkeypatch, repo, name, *, sid=SID, fname="b.py"):
    wt = _cli(monkeypatch, sid, "create", repo, "--name", name)["path"]
    with open(os.path.join(wt, fname), "w") as f:
        f.write("b\n")
    _g(wt, "add", "-A")
    _g(wt, "-c", "core.hooksPath=/dev/null", "commit", "-qm", f"add {fname}")
    assert _cli(monkeypatch, sid, "land", wt)["landed"]
    _cli(monkeypatch, sid, "remove", wt)
    assert not os.path.exists(wt)
    return wt


def test_a_removed_worktree_file_opens_from_main(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo])
    wt = _land_and_remove(monkeypatch, repo, "fix1")
    gone = os.path.join(wt, "b.py")
    assert sl.landed_path(gone, SID) == os.path.join(repo, "b.py")
    assert sl.landed_path(gone) == os.path.join(repo, "b.py"), "one key: unambiguous"
    assert sl.landed_path(gone, OTHER) is None, "another session's key"
    assert sl.landed_path(os.path.join(wt, "missing.py"), SID) is None
    assert sl.landed_path(os.path.join(wt, "..", "..", "proj", "a.py"), SID) is None
    assert sl.landed_path(os.path.join(repo, "a.py"), SID) is None, "an existing path"


def test_two_keys_for_one_name_need_the_session_id(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo])
    wt = _land_and_remove(monkeypatch, repo, "same", sid=SID)
    _land_and_remove(monkeypatch, repo, "same", sid=OTHER, fname="c.py")
    gone = os.path.join(wt, "b.py")
    assert sl.landed_path(gone) is None
    assert sl.landed_path(gone, SID) == os.path.join(repo, "b.py")


def test_one_session_landing_one_name_twice_still_maps(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo])
    _land_and_remove(monkeypatch, repo, "again")
    wt = _land_and_remove(monkeypatch, repo, "again", fname="c.py")
    assert sl.landed_path(os.path.join(wt, "b.py")) == os.path.join(repo, "b.py")


@pytest.mark.parametrize("second", ["proj", "PROJ"])
def test_two_bound_checkouts_with_one_name_map_nothing(tmp_path, monkeypatch, second):
    repo = _repo(tmp_path / "a" / "proj")
    twin = _repo(tmp_path / "b" / second)
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo, twin])
    wt = _land_and_remove(monkeypatch, repo, "fix1")
    assert sl.landed_path(os.path.join(wt, "b.py"), SID) is None


def test_a_name_never_landed_maps_nothing(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo])
    from scripts import session_worktree as sw
    gone = os.path.join(str(sw.root()), "nolanding", "proj", "a.py")
    assert sl.landed_path(gone, SID) is None


def test_a_pipeline_worktree_path_is_never_mapped(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo])
    wt = _land_and_remove(monkeypatch, repo, "fix1")
    from core import run_worktree
    monkeypatch.setattr(run_worktree, "run_for_path", lambda p: ("run_x", "proj"))
    assert sl.landed_path(os.path.join(wt, "b.py"), SID) is None


def test_a_checkout_outside_home_is_not_mapped(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo])
    wt = _land_and_remove(monkeypatch, repo, "fix1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert sl.landed_path(os.path.join(wt, "b.py"), SID) is None


def test_the_cache_follows_the_branch_not_only_its_tip(tmp_path, monkeypatch):
    """Two branches at one commit have different reflogs: switching re-reads."""
    repo = _repo(tmp_path / "proj")
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo])
    wt = _land_and_remove(monkeypatch, repo, "fix1")
    gone = os.path.join(wt, "b.py")
    assert sl.landed_path(gone, SID) == os.path.join(repo, "b.py")
    _g(repo, "checkout", "-q", "-b", "other")          # same commit, a fresh reflog
    assert sl.landed_path(gone, SID) is None
