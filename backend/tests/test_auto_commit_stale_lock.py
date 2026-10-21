"""Stale-git-lock cleanup by AGE, not git-process presence (run_bd863688).

Bug: _cleanup_stale_git_lock skipped removing .git/index.lock whenever
`pgrep -f "git.*{ws}"` matched ANY git process. A WEDGED/zombie git (stuck
hours, observed 2026-07-24: 3 procs 7h+/19h) matched too, so cleanup was
skipped forever → lock accumulated → concurrent auto-commit hooks saturated
the thread pool → all chat tabs froze.

Fix: lock AGE is the primary judge. A lock older than STALE_LOCK_AGE_SECONDS
(300s) is definitely dead (every live hook commit self-bounds at GIT_TIMEOUT=10s),
so it is deleted UNCONDITIONALLY — a wedged git can no longer block cleanup.
A young lock (<=300s) is removed only when no process named git runs in the
repo (_git_running_in, process identity: the old `pgrep -f` text match also hit
every claude CLI, whose command line names git and the workspace path).

Methodology: drive the REAL _cleanup_stale_git_lock against a real temp .git dir
+ a real lock file with a forced mtime; monkeypatch ONLY _git_running_in to
simulate "a git process exists".
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.auto_commit_hook import WorkspaceAutoCommitHook


@pytest.fixture
def ws_with_lock(tmp_path):
    """A workspace with a .git/index.lock; returns (ws_path, lock_file)."""
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    lock = ws / ".git" / "index.lock"
    lock.write_text("")
    return str(ws), lock


def _set_age(lock: Path, seconds: float) -> None:
    """Force the lock's mtime to `seconds` in the past."""
    t = time.time() - seconds
    os.utime(lock, (t, t))


def test_old_lock_removed_despite_live_git_process(ws_with_lock):
    """PRIMARY FIX: a >300s lock is deleted even when pgrep matches a git process.

    RED on old code (pgrep match → skip → lock survives).
    GREEN on the fix (age>300 → unconditional delete).
    """
    ws_path, lock = ws_with_lock
    _set_age(lock, 310)  # older than the 300s staleness threshold

    # Simulate a (wedged) git process existing: pgrep returns 0 (found).
    with patch("hooks.auto_commit_hook._git_running_in", return_value=True):
        WorkspaceAutoCommitHook._cleanup_stale_git_lock(ws_path)

    assert not lock.exists(), (
        "A 310s-old lock must be removed regardless of a matching git process "
        "(wedged git must not block cleanup)"
    )


def test_young_lock_kept_when_git_process_running(ws_with_lock):
    """A young lock (<=300s) with a live git process is NOT deleted."""
    ws_path, lock = ws_with_lock
    _set_age(lock, 10)  # fresh — could be a live commit

    with patch("hooks.auto_commit_hook._git_running_in", return_value=True):
        WorkspaceAutoCommitHook._cleanup_stale_git_lock(ws_path)

    assert lock.exists(), "A young lock held by a live git must NOT be deleted"


def test_young_orphan_lock_removed_when_no_git(ws_with_lock):
    """A young lock with NO git process is removed (existing orphan behavior)."""
    ws_path, lock = ws_with_lock
    _set_age(lock, 10)

    with patch("hooks.auto_commit_hook._git_running_in", return_value=False):
        WorkspaceAutoCommitHook._cleanup_stale_git_lock(ws_path)

    assert not lock.exists(), "A young orphan lock (no git) must be removed"


def test_no_lock_is_noop(ws_with_lock):
    """No lock file → no error, no subprocess call."""
    ws_path, lock = ws_with_lock
    lock.unlink()
    with patch("subprocess.run") as run:
        WorkspaceAutoCommitHook._cleanup_stale_git_lock(ws_path)
        run.assert_not_called()


def test_old_lock_deleted_out_from_under_us_is_safe(ws_with_lock):
    """TOCTOU: lock vanishing between age-read and unlink must not raise."""
    ws_path, lock = ws_with_lock
    _set_age(lock, 310)
    # Delete the lock during the getmtime/remove window by having the removal
    # already be gone: simulate via a race where unlink target is missing.
    real_remove = os.remove

    def _racing_remove(p):
        # Simulate another process already removed it.
        raise FileNotFoundError(p)

    with patch("os.remove", side_effect=_racing_remove):
        # Must swallow the error, not crash the hook.
        WorkspaceAutoCommitHook._cleanup_stale_git_lock(ws_path)


def test_a_claude_cli_naming_git_and_the_workspace_is_not_a_git_process(tmp_path):
    """The 10-14 → 10-21 bug: pgrep -f matched the claude CLI's command line."""
    import subprocess
    import sys
    from hooks.auto_commit_hook import _git_running_in
    ws = tmp_path / "ws"
    ws.mkdir()
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                          "--add-dir", f"git {ws}"], cwd=str(ws))
    try:
        assert _git_running_in(str(ws)) is False
    finally:
        p.kill()
        p.wait()


def test_git_timeout_ends_git_with_sigterm_first(tmp_path, monkeypatch):
    """A timed-out git is sent SIGTERM (git removes its own lock files then)
    before any SIGKILL."""
    import subprocess
    from hooks import auto_commit_hook as ach
    calls = []

    class _P:
        args = ["git"]
        returncode = None

        def __init__(self, *a, **k):
            self.n = 0

        def communicate(self, timeout=None):
            self.n += 1
            if self.n == 1:
                raise subprocess.TimeoutExpired("git", timeout)
            return ("", "")

        def terminate(self):
            calls.append("TERM")

        def kill(self):
            calls.append("KILL")

    monkeypatch.setattr(ach.subprocess, "Popen", _P)
    with pytest.raises(subprocess.TimeoutExpired):
        ach._git(["add", "-A"], str(tmp_path), 1)
    assert calls == ["TERM"]
