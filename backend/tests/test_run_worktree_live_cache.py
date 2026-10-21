"""Tests for the (mtime_ns, size)-keyed run.json cache behind active_worktree_roots.

- a second call with no run.json change parses no file (one stat per run, no read)
- a run that becomes live, or goes terminal, is seen on the next call (file changed)
- a deleted run.json drops out of the result and out of the cache
- a half-written (invalid JSON) run.json is not cached and is read again next call
"""
import json
import os

import pytest

from core import run_worktree as rw


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "wt"))
    rw._LIVE_SUMMARY.clear()
    yield
    rw._LIVE_SUMMARY.clear()


def _write_run(ws, rid, status, repo="swarmai", mtime=None):
    d = ws / "Projects" / "P" / ".artifacts" / "runs" / rid
    d.mkdir(parents=True, exist_ok=True)
    f = d / "run.json"
    f.write_text(json.dumps({"id": rid, "status": status, "worktree_mode": "isolated",
                             "worktrees": [{"repo_name": repo, "state": "active"}]}))
    if mtime is not None:
        os.utime(f, ns=(mtime, mtime))
    (rw.wt_root() / rid / repo).mkdir(parents=True, exist_ok=True)
    return f


def _count_reads(monkeypatch):
    calls = []
    real = rw.Path.read_text

    def spy(self, *a, **k):
        if self.name == "run.json":
            calls.append(str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(rw.Path, "read_text", spy)
    return calls


def test_unchanged_runs_are_not_parsed_again(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_run(ws, "run_a", "running")
    for i in range(5):
        _write_run(ws, f"run_d{i}", "done")
    reads = _count_reads(monkeypatch)
    first = rw.active_worktree_roots(ws)
    assert [r for _p, r in first] == ["swarmai"]
    assert len(reads) == 6
    assert rw.active_worktree_roots(ws) == first
    assert len(reads) == 6            # second call: stat only, no read


def test_status_change_is_seen(tmp_path):
    ws = tmp_path / "ws"
    _write_run(ws, "run_a", "done", mtime=1_000_000_000)
    assert rw.active_worktree_roots(ws) == []
    _write_run(ws, "run_a", "running", mtime=2_000_000_000)
    assert len(rw.active_worktree_roots(ws)) == 1
    _write_run(ws, "run_a", "done", mtime=3_000_000_000)
    assert rw.active_worktree_roots(ws) == []


def test_deleted_run_drops_out(tmp_path):
    ws = tmp_path / "ws"
    f = _write_run(ws, "run_a", "running")
    assert len(rw.active_worktree_roots(ws)) == 1
    f.unlink()
    assert rw.active_worktree_roots(ws) == []
    assert str(f) not in rw._LIVE_SUMMARY


def test_invalid_json_is_not_cached(tmp_path):
    ws = tmp_path / "ws"
    f = _write_run(ws, "run_a", "running")
    good = f.read_text()
    f.write_text("{half")
    assert rw.active_worktree_roots(ws) == []
    assert str(f) not in rw._LIVE_SUMMARY
    f.write_text(good)
    assert len(rw.active_worktree_roots(ws)) == 1
