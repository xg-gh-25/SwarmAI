"""Stale-git-lock cleanup by AGE, not git-process presence (run_bd863688).

Bug: _cleanup_stale_git_lock skipped removing .git/index.lock whenever
`pgrep -f "git.*{ws}"` matched ANY git process. A WEDGED/zombie git (stuck
hours, observed 2026-07-24: 3 procs 7h+/19h) matched too, so cleanup was
skipped forever → lock accumulated → concurrent auto-commit hooks saturated
the thread pool → all chat tabs froze.

Fix: lock AGE is the primary judge. A lock older than STALE_LOCK_AGE_SECONDS
(300s) is definitely dead (every live hook commit self-bounds at GIT_TIMEOUT=10s),
so it is deleted UNCONDITIONALLY — a wedged git can no longer block cleanup.
A young lock (<=300s) still honors the pgrep guard (may be a live commit).

Methodology: drive the REAL _cleanup_stale_git_lock against a real temp .git dir
+ a real lock file with a forced mtime; monkeypatch ONLY the pgrep subprocess
boundary to simulate "a git process exists".
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    fake = MagicMock()
    fake.returncode = 0
    with patch("subprocess.run", return_value=fake):
        WorkspaceAutoCommitHook._cleanup_stale_git_lock(ws_path)

    assert not lock.exists(), (
        "A 310s-old lock must be removed regardless of a matching git process "
        "(wedged git must not block cleanup)"
    )


def test_young_lock_kept_when_git_process_running(ws_with_lock):
    """A young lock (<=300s) with a live git process is NOT deleted."""
    ws_path, lock = ws_with_lock
    _set_age(lock, 10)  # fresh — could be a live commit

    fake = MagicMock()
    fake.returncode = 0  # git process found
    with patch("subprocess.run", return_value=fake):
        WorkspaceAutoCommitHook._cleanup_stale_git_lock(ws_path)

    assert lock.exists(), "A young lock held by a live git must NOT be deleted"


def test_young_orphan_lock_removed_when_no_git(ws_with_lock):
    """A young lock with NO git process is removed (existing orphan behavior)."""
    ws_path, lock = ws_with_lock
    _set_age(lock, 10)

    fake = MagicMock()
    fake.returncode = 1  # pgrep: no git process
    with patch("subprocess.run", return_value=fake):
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
