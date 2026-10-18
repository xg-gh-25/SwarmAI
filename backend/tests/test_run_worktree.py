"""Tests for core.run_worktree, core.run_lock and core.hook_token
(docs/design/pipeline-run-worktree.md §4, §5, §7, §8, §15).

Methodology: real git in temp repos (a "main checkout" with its own empty hooks dir,
so the machine's global git hooks never run), WT_ROOT / ledger redirected into
tmp_path through SWARM_WORKTREE_ROOT / SWARM_WORKTREE_LEDGER. Invariants pinned:
- the worktree is derived from run_id and every use re-validates it;
- T excludes un-added files and B; un-added files block landing;
- sync rebases onto the moved integration branch and reports conflicts until the
  unmerged list is empty;
- landing is ff-only: keeps non-overlapping uncommitted main edits, refuses on
  overlap / moved branch, and always returns the worktree to HEAD == base;
- an unintegrated worktree is deleted only after a verified ledger snapshot;
- the run lock is re-entrant per thread and excludes other processes;
- a hook token is single-use, TTL-bound and bound to session, run and subcommand.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import hook_token, run_lock
from core import run_worktree as rw

RUN = "run_abc123"


def _g(cwd, *args, check=True):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError(f"git {args}: {p.stderr}")
    return p.stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    tmp = Path(os.path.realpath(tmp_path))
    main = tmp / "repos" / "swarmai"
    main.mkdir(parents=True)
    hooks = tmp / "hooks"
    hooks.mkdir()
    _g(main, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.com"),
                 ("core.hooksPath", str(hooks)), ("commit.gpgsign", "false")):
        _g(main, "config", k, v)
    (main / "a.py").write_text("a = 1\n")
    (main / "b.py").write_text("b = 1\n")
    (main / "c.py").write_text("c = 1\n")
    _g(main, "add", "-A")
    _g(main, "commit", "-q", "-m", "init")
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp / "wt"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp / "ledger"))
    return {"main": main, "tmp": tmp, "hooks": hooks}


def _commit_main(main, name, text, msg="main moves"):
    (main / name).write_text(text)
    _g(main, "add", name)
    _g(main, "commit", "-q", "-m", msg)
    return _g(main, "rev-parse", "HEAD")


# ── create / validate ──────────────────────────────────────────────────────────
def test_create_derives_path_and_branch(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    assert wt.is_dir() and e["state"] == "active"
    assert e["base"] == _g(env["main"], "rev-parse", "HEAD")
    assert _g(wt, "symbolic-ref", "HEAD") == f"refs/heads/run/{RUN}"
    assert rw.validate(RUN, e, env["main"]) == wt


def test_create_excludes_other_sessions_uncommitted_edits(env):
    (env["main"] / "a.py").write_text("a = 'someone else'\n")
    rw.create(RUN, "swarmai", env["main"])
    assert (rw.wt_path(RUN, "swarmai") / "a.py").read_text() == "a = 1\n"


def test_create_refuses_detached_main(env):
    _g(env["main"], "checkout", "-q", "--detach")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.create(RUN, "swarmai", env["main"])
    assert ei.value.code == "main_checkout_busy"


@pytest.mark.parametrize("field,value", [
    ("git_common_dir", "/elsewhere/.git"),
    ("integration_branch", "refs/heads/other"),
    ("repo_name", "../x"),
])
def test_validate_refuses_tampered_record(env, field, value):
    e = rw.create(RUN, "swarmai", env["main"])
    e[field] = value
    with pytest.raises(rw.WorktreeError) as ei:
        rw.validate(RUN, e, env["main"])
    assert ei.value.code == "worktree_invalid"


def test_validate_refuses_unregistered_dir(env):
    e = rw.create(RUN, "swarmai", env["main"])
    _g(env["main"], "worktree", "remove", "--force", str(rw.wt_path(RUN, "swarmai")))
    rw.wt_path(RUN, "swarmai").mkdir(parents=True)
    with pytest.raises(rw.WorktreeError):
        rw.validate(RUN, e, env["main"])


# ── change set ────────────────────────────────────────────────────────────────
def test_change_tree_tracks_edits_and_explicit_adds_only(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 2\n")
    (wt / "c.py").unlink()
    (wt / "probe.py").write_text("print('probe')\n")
    (wt / "new.py").write_text("new = 1\n")
    _g(wt, "add", "new.py")
    t = rw.change_tree(wt, RUN)
    assert rw.changed_paths(wt, e["base"], t) == ["a.py", "c.py", "new.py"]
    assert rw.untracked_new_files(wt, RUN) == ["probe.py"]
    os.makedirs(wt / ".aidlc" / "deliverables" / RUN)
    (wt / ".aidlc" / "deliverables" / RUN / "REPORT.md").write_text("b")
    assert rw.untracked_new_files(wt, RUN) == ["probe.py"]
    assert rw.change_tree(wt, RUN) == t
    # deleting the probe leaves T unchanged
    (wt / "probe.py").unlink()
    assert rw.change_tree(wt, RUN) == t


# ── sync ────────────────────────────────────────────────────────────────────────
def test_sync_noop_when_branch_unmoved(env):
    e = rw.create(RUN, "swarmai", env["main"])
    assert rw.sync(RUN, e, env["main"])["status"] == "noop"


def test_sync_rebases_onto_moved_branch_keeping_change_uncommitted(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 2\n")
    (wt / "probe.txt").write_text("x")
    c = _commit_main(env["main"], "b.py", "b = 2\n")
    res = rw.sync(RUN, e, env["main"])
    assert res["status"] == "rebased" and e["base"] == c
    assert _g(wt, "rev-parse", "HEAD") == c
    assert (wt / "b.py").read_text() == "b = 2\n" and (wt / "a.py").read_text() == "a = 2\n"
    assert rw.changed_paths(wt, c, rw.change_tree(wt, RUN)) == ["a.py"]
    assert (wt / "probe.txt").exists() and rw.untracked_new_files(wt, RUN) == ["probe.txt"]
    assert res["patch_changed"] is False


def test_sync_conflict_until_unmerged_list_is_empty(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 'run'\n")
    c = _commit_main(env["main"], "a.py", "a = 'main'\n")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.sync(RUN, e, env["main"])
    assert ei.value.code == "conflict" and ei.value.data["unmerged"] == ["a.py"]
    with pytest.raises(rw.WorktreeError):      # still unresolved
        rw.sync(RUN, e, env["main"])
    (wt / "a.py").write_text("a = 'both'\n")
    _g(wt, "add", "a.py")
    res = rw.sync(RUN, e, env["main"])
    assert res["status"] == "rebased" and e["base"] == c
    assert _g(wt, "rev-parse", "HEAD") == c and res["patch_changed"] is True
    assert rw.changed_paths(wt, c, rw.change_tree(wt, RUN)) == ["a.py"]


def test_sync_untracked_collision_restores_invariant(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 2\n")
    (wt / "n.py").write_text("mine\n")
    _commit_main(env["main"], "n.py", "theirs\n")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.sync(RUN, e, env["main"])
    assert ei.value.code == "untracked_collision"
    assert _g(wt, "rev-parse", "HEAD") == e["base"]
    assert rw.changed_paths(wt, e["base"], rw.change_tree(wt, RUN)) == ["a.py"]


# ── landing ─────────────────────────────────────────────────────────────────────
def _ready(env, edit=("a.py", "a = 2\n")):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / edit[0]).write_text(edit[1])
    return e, wt, rw.change_tree(wt, RUN)


def _write_b(wt):
    d = wt / ".aidlc" / "deliverables" / RUN
    d.mkdir(parents=True, exist_ok=True)
    (d / "REPORT.md").write_text("# report\n")
    return [f".aidlc/deliverables/{RUN}/REPORT.md"]


def test_land_ff_keeps_non_overlapping_main_edits(env):
    main = env["main"]
    (main / "b.py").write_text("b = 'staged by someone'\n")
    _g(main, "add", "b.py")
    (main / "c.py").write_text("c = 'unstaged by someone'\n")
    e, wt, t = _ready(env)
    res = rw.land(RUN, e, main, "msg\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>", t, write_b=_write_b)
    assert _g(main, "rev-parse", "HEAD") == res["sha"] and e["state"] == "integrated"
    assert (main / "a.py").read_text() == "a = 2\n"
    assert (main / "c.py").read_text() == "c = 'unstaged by someone'\n"
    assert "b.py" in _g(main, "diff", "--cached", "--name-only")
    assert sorted(res["files"]) == sorted(["a.py", f".aidlc/deliverables/{RUN}/REPORT.md"])
    assert _g(main, "show", "--name-only", "--format=", "HEAD").split() == sorted(res["files"])


def test_land_blocked_by_overlapping_uncommitted_edit(env):
    e, wt, t = _ready(env)
    (env["main"] / "a.py").write_text("a = 'someone'\n")
    before = _g(env["main"], "rev-parse", "HEAD")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.land(RUN, e, env["main"], "m", t)
    assert ei.value.code == "blocked_by_uncommitted"
    assert _g(env["main"], "rev-parse", "HEAD") == before
    assert (env["main"] / "a.py").read_text() == "a = 'someone'\n"
    assert _g(wt, "rev-parse", "HEAD") == e["base"] and e["state"] == "active"


@pytest.mark.parametrize("case,code", [
    ("moved", "needs_resync"), ("edited", "changed_since_review"),
    ("untracked", "untracked_new_files"),
])
def test_land_preconditions(env, case, code):
    e, wt, t = _ready(env)
    if case == "moved":
        _commit_main(env["main"], "b.py", "b = 9\n")
    elif case == "edited":
        (wt / "a.py").write_text("a = 3\n")
    else:
        (wt / "foo_helper.py").write_text("x = 1\n")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.land(RUN, e, env["main"], "m", t)
    assert ei.value.code == code
    assert _g(wt, "rev-parse", "HEAD") == e["base"]


def test_land_commit_timeout_returns_worktree_to_base(env, monkeypatch):
    hook = env["hooks"] / "pre-commit"
    hook.write_text("#!/bin/sh\nsleep 5\n")
    hook.chmod(0o755)
    monkeypatch.setattr(rw, "COMMIT_TIMEOUT_S", 1.0)
    e, wt, t = _ready(env)
    before = _g(env["main"], "rev-parse", "HEAD")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.land(RUN, e, env["main"], "m", t)
    assert ei.value.code == "commit_slow"
    assert _g(wt, "rev-parse", "HEAD") == e["base"]
    assert _g(env["main"], "rev-parse", "HEAD") == before


def test_land_rejects_gitlink_and_outside_symlink(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    os.symlink("/etc/passwd", wt / "evil")
    _g(wt, "add", "evil")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.land(RUN, e, env["main"], "m", rw.change_tree(wt, RUN))
    assert ei.value.code == "path_rejected"


# ── release / ledger / sweep ──────────────────────────────────────────────────
def test_release_integrated_removes_worktree_and_branch(env):
    e, wt, t = _ready(env)
    rw.land(RUN, e, env["main"], "m", t)
    state = {"worktrees": [e]}
    res = rw.release(RUN, state, {"swarmai": env["main"]}, "completed")
    assert res[0]["ok"] and e["state"] == "released" and not wt.exists()
    assert _g(env["main"], "branch", "--list", f"run/{RUN}") == ""
    assert str(wt) not in _g(env["main"], "worktree", "list")


def test_release_unintegrated_snapshots_verified_ledger(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 'wip'\n")
    (wt / "untracked.bin").write_bytes(b"\x00\x01")
    state = {"worktrees": [e]}
    res = rw.release(RUN, state, {"swarmai": env["main"]}, "cancelled")
    assert res[0]["ok"] and not wt.exists()
    ledger = Path(res[0]["ledger"])
    meta = json.loads((ledger / "meta.json").read_text())
    restore = env["tmp"] / "restore"
    _g(env["main"], "worktree", "add", "-q", "--detach", str(restore), meta["base"])
    _g(restore, "fetch", "-q", str(ledger / "worktree.bundle"),
       f"refs/swarm-ledger/{RUN}/swarmai:refs/restored")
    _g(restore, "read-tree", "-u", "--reset", "refs/restored")
    assert (restore / "a.py").read_text() == "a = 'wip'\n"
    assert (restore / "untracked.bin").read_bytes() == b"\x00\x01"


def test_release_keeps_worktree_when_snapshot_fails(env, monkeypatch):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 'wip'\n")

    def boom(*a, **k):
        raise rw.WorktreeError("snapshot_failed", "simulated")
    monkeypatch.setattr(rw, "_verify_bundle", boom)
    res = rw.release(RUN, {"worktrees": [e]}, {"swarmai": env["main"]}, "cancelled")
    assert not res[0]["ok"] and wt.exists() and e["state"] == "active"


def test_release_mid_rebase_keeps_conflict_work(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 'run'\n")
    _commit_main(env["main"], "a.py", "a = 'main'\n")
    with pytest.raises(rw.WorktreeError):
        rw.sync(RUN, e, env["main"])
    (wt / "a.py").write_text("a = 'half resolved'\n")
    res = rw.release(RUN, {"worktrees": [e]}, {"swarmai": env["main"]}, "abandoned")
    assert res[0]["ok"]
    diff = (Path(res[0]["ledger"]) / "changes.diff").read_text()
    assert "half resolved" in diff


def test_sweep_quarantines_unregistered_and_releases_terminal(env):
    ws = env["tmp"] / "ws"
    rd = ws / "Projects" / "P" / ".artifacts" / "runs" / RUN
    rd.mkdir(parents=True)
    e = rw.create(RUN, "swarmai", env["main"], workspace=ws)
    (rw.wt_path(RUN, "swarmai") / "a.py").write_text("a = 'wip'\n")
    (rd / "run.json").write_text(json.dumps({"id": RUN, "status": "cancelled",
                                             "worktree_mode": "isolated", "worktrees": [e]}))
    stray = rw.wt_root() / "run_stray1" / "swarmai"
    stray.mkdir(parents=True)
    rw.stamp_workspace("run_stray1", ws)
    rep = rw.sweep(ws, owner_active=lambda sid: False)
    assert len(rep["quarantined"]) == 1 and "swarmai-" in rep["quarantined"][0]
    assert not stray.exists() and not rw.wt_path(RUN, "swarmai").exists()
    data = json.loads((rd / "run.json").read_text())
    assert data["worktrees"][0]["state"] == "released" and data["worktrees"][0]["ledger"]


def test_sweep_never_touches_another_workspaces_worktrees(env):
    """A test / second install sweeping its own workspace must not see this install's
    live worktrees as "run record missing" (review HIGH-1)."""
    real_ws = env["tmp"] / "real"
    rw.create(RUN, "swarmai", env["main"], workspace=real_ws)
    other = env["tmp"] / "scratch"
    (other / "Projects").mkdir(parents=True)
    rep = rw.sweep(other, owner_active=lambda sid: False, release_orphans=True)
    assert rw.wt_path(RUN, "swarmai").is_dir() and not rep["released"] and not rep["quarantined"]


def test_release_refuses_symlinked_run_dir(env):
    """A planted symlink must never turn a release into deleting the main checkout
    (review HIGH-2)."""
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    _g(env["main"], "worktree", "remove", "--force", str(wt))
    import shutil as _sh
    _sh.rmtree(wt.parent)
    os.symlink(env["main"].parent, wt.parent)
    res = rw.release(RUN, {"worktrees": [e]}, {"swarmai": env["main"]}, "failed")
    assert not res[0]["ok"] and (env["main"] / ".git").exists() and (env["main"] / "a.py").exists()


def test_sync_recovers_from_aborted_rebase(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 'run'\n")
    _commit_main(env["main"], "a.py", "a = 'main'\n")
    with pytest.raises(rw.WorktreeError):
        rw.sync(RUN, e, env["main"])
    _g(wt, "rebase", "--abort")             # the model gives up on the conflict
    assert _g(wt, "rev-parse", "HEAD") == e["pre_sync_tip"]
    with pytest.raises(rw.WorktreeError) as ei:  # re-entry: invariant restored, conflict again
        rw.sync(RUN, e, env["main"])
    assert ei.value.code == "conflict"


def test_landed_already_detects_merge_without_record(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 2\n")
    _g(wt, "commit", "-qam", "n")
    n = _g(wt, "rev-parse", "HEAD")
    _g(env["main"], "merge", "-q", "--ff-only", n)
    assert rw.landed_already(RUN, e, env["main"]) == n


def test_sweep_leaves_run_of_active_owner(env):
    ws = env["tmp"] / "ws"
    rd = ws / "Projects" / "P" / ".artifacts" / "runs" / RUN
    rd.mkdir(parents=True)
    e = rw.create(RUN, "swarmai", env["main"], workspace=ws)
    (rd / "run.json").write_text(json.dumps({"id": RUN, "status": "cancelled", "session_id": "s1",
                                             "worktree_mode": "isolated", "worktrees": [e]}))
    rep = rw.sweep(ws, owner_active=lambda sid: sid == "s1")
    assert rw.wt_path(RUN, "swarmai").exists() and rep["kept"]


# ── marks ─────────────────────────────────────────────────────────────────────
def test_mark_and_diff_since(env):
    e = rw.create(RUN, "swarmai", env["main"])
    wt = rw.wt_path(RUN, "swarmai")
    (wt / "a.py").write_text("a = 2\n")
    rw.mark(RUN, e, env["main"], "cycle1")
    (wt / "b.py").write_text("b = 2\n")
    d = rw.diff_since(RUN, e, env["main"], "cycle1")
    assert "b.py" in d and "a.py" not in d
    assert "a.py" in rw.diff_since(RUN, e, env["main"], None)


# ── run lock ──────────────────────────────────────────────────────────────────
def test_run_lock_reentrant_and_excludes_other_process(tmp_path):
    with run_lock.run_lock(tmp_path):
        with run_lock.run_lock(tmp_path):      # re-entrant: no self-deadlock
            assert run_lock.is_held(tmp_path)
        code = ("import sys; sys.path.insert(0, %r); from core import run_lock as r;\n"
                "try:\n  r.acquire_for_process(%r, timeout=0.3); print('got')\n"
                "except r.RunLockTimeout: print('busy')\n") % (str(Path(__file__).parents[1]),
                                                              str(tmp_path))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert out.stdout.strip() == "busy", out.stderr
    assert not run_lock.is_held(tmp_path)


def test_update_run_is_atomic_read_modify_write(tmp_path):
    f = tmp_path / "run.json"
    f.write_text(json.dumps({"status": "running"}))
    run_lock.update_run(f, lambda d: d.update(status="cancelled"))
    assert json.loads(f.read_text())["status"] == "cancelled"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


# ── hook token ────────────────────────────────────────────────────────────────
def test_token_single_use_and_bound(tmp_path):
    n = hook_token.issue(tmp_path, "s1", RUN, "stage-start")
    assert not list((tmp_path / hook_token.TOKEN_DIR).glob("*.json"))[0].read_text().count(n)
    assert hook_token.consume(tmp_path, n, "s1", RUN, "stage-start") == (True, "")
    assert hook_token.consume(tmp_path, n, "s1", RUN, "stage-start")[0] is False   # replay


@pytest.mark.parametrize("sid,rid,sub,dt", [
    ("s2", RUN, "stage-start", 0), ("s1", "run_other", "stage-start", 0),
    ("s1", RUN, "stage-complete", 0), ("s1", RUN, "stage-start", 500),
])
def test_token_mismatch_or_expiry_refused(tmp_path, sid, rid, sub, dt):
    n = hook_token.issue(tmp_path, "s1", RUN, "stage-start", now=time.time() - dt)
    ok, why = hook_token.consume(tmp_path, n, sid, rid, sub)
    assert not ok and why


def test_token_forged_or_missing_refused(tmp_path):
    assert hook_token.consume(tmp_path, None, "s1", RUN, "x")[0] is False
    assert hook_token.consume(tmp_path, "0" * 64, "s1", RUN, "x")[0] is False
