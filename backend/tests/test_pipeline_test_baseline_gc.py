"""Baseline-worktree eviction in ``backend/scripts/pipeline_test.py``.

The runner keeps one detached worktree per HEAD sha under
``<cache>/test-baseline/<sha>`` to classify failures against HEAD. Every commit is a
new HEAD, so without eviction they pile up (a full checkout each).

Methodology: a throwaway git repo (hooks off) with several commits; real
``git worktree add`` per sha; last-use is set through the ``<sha>.used`` mtime.
Properties:
- at most BASELINE_KEEP worktrees remain, the most recently used ones;
- the sha in use (``keep_sha``) is never evicted;
- a worktree used within the idle window is never evicted (another run may be in it);
- a worktree whose per-sha lock is held is never evicted;
- an evicted sha loses its worktree registration, ``.used`` marker and outcome file.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pipeline_test.py"
_spec = importlib.util.spec_from_file_location("pipeline_test_gc", _SCRIPT)
pt = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("pipeline_test_gc", pt)
_spec.loader.exec_module(pt)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
         "-c", "user.email=t@x.invalid", "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True, timeout=60).stdout


@pytest.fixture
def rig(tmp_path):
    """Repo with 4 commits, each checked out as a baseline worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    shas = []
    for i in range(4):
        (repo / "f.txt").write_text(f"{i}\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(repo, "rev-parse", "HEAD").strip())
    base = tmp_path / "cache" / "test-baseline"
    base.mkdir(parents=True)
    old = time.time() - pt.BASELINE_IDLE_S - 600
    for i, sha in enumerate(shas):
        _git(repo, "worktree", "add", "-q", "--detach", str(base / sha), sha)
        used = base / f"{sha}.used"
        used.touch()
        os.utime(used, (old + i, old + i))  # c0 oldest … c3 newest, all idle
        (base.parent / f"baseline-{sha}-v{pt.RUNNER_VERSION}.json").write_text("{}")
    return repo, base, shas


def _registered(repo: Path) -> set[str]:
    out = _git(repo, "worktree", "list", "--porcelain")
    return {Path(ln.split(" ", 1)[1]).name for ln in out.splitlines() if ln.startswith("worktree ")}


def test_keeps_the_most_recent_and_the_sha_in_use(rig):
    repo, base, shas = rig
    gone = pt._evict_baselines(repo, base, keep_sha=shas[0])
    assert set(gone) == {shas[1], shas[2]}
    left = {d.name for d in base.iterdir() if d.is_dir()}
    assert left == {shas[0], shas[3]}  # in-use sha + the newest other
    assert not _registered(repo) & set(gone)
    for sha in gone:
        assert not (base / f"{sha}.used").exists()
        assert not (base.parent / f"baseline-{sha}-v{pt.RUNNER_VERSION}.json").exists()
        assert not (base / f"{sha}.lock").exists()


def test_recently_used_worktree_is_never_evicted(rig):
    repo, base, shas = rig
    os.utime(base / f"{shas[0]}.used", None)  # c0 used just now by another run
    gone = pt._evict_baselines(repo, base, keep_sha=shas[3])
    assert shas[0] not in gone
    assert (base / shas[0]).is_dir()


def test_locked_worktree_is_never_evicted(rig):
    repo, base, shas = rig
    fd = pt._try_lock(base / f"{shas[1]}.lock", 0)
    try:
        gone = pt._evict_baselines(repo, base, keep_sha=shas[3])
    finally:
        pt._unlock(fd)
    assert shas[1] not in gone and (base / shas[1]).is_dir()
    assert shas[0] in gone


def test_no_keep_sha_keeps_the_newest_n(rig):
    repo, base, shas = rig
    gone = pt._evict_baselines(repo, base, keep_sha=None)
    assert set(gone) == {shas[0], shas[1]}


def test_missing_cache_dir_is_a_noop(tmp_path):
    assert pt._evict_baselines(tmp_path, tmp_path / "nope", keep_sha=None) == []
