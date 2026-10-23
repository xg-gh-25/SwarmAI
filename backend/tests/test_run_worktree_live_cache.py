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
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
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
    # a v1 run: its repos come from the protected contract, never run.json
    c = ws.parent / "data" / "state" / "pipeline_agent_audit" / "runs" / rid / "contract.json"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(json.dumps({"run_id": rid, "protocol_version": 1, "expected_repos": [repo]}))
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
    _write_run(ws, "run_a", "active")
    for i in range(5):
        _write_run(ws, f"run_d{i}", "completed")
    reads = _count_reads(monkeypatch)
    first = rw.active_worktree_roots(ws)
    assert [r for _p, r in first] == ["swarmai"]
    assert len(reads) == 6
    assert rw.active_worktree_roots(ws) == first
    assert len(reads) == 6            # second call: stat only, no read


def test_status_change_is_seen(tmp_path):
    ws = tmp_path / "ws"
    _write_run(ws, "run_a", "completed", mtime=1_000_000_000)
    assert rw.active_worktree_roots(ws) == []
    _write_run(ws, "run_a", "active", mtime=2_000_000_000)
    assert len(rw.active_worktree_roots(ws)) == 1
    _write_run(ws, "run_a", "completed", mtime=3_000_000_000)
    assert rw.active_worktree_roots(ws) == []


def test_deleted_run_drops_out(tmp_path):
    ws = tmp_path / "ws"
    f = _write_run(ws, "run_a", "active")
    assert len(rw.active_worktree_roots(ws)) == 1
    f.unlink()
    assert rw.active_worktree_roots(ws) == []
    assert str(f) not in rw._LIVE_SUMMARY


def test_invalid_json_is_not_cached(tmp_path):
    ws = tmp_path / "ws"
    f = _write_run(ws, "run_a", "active")
    good = f.read_text()
    f.write_text("{half")
    assert rw.active_worktree_roots(ws) == []
    assert str(f) not in rw._LIVE_SUMMARY
    f.write_text(good)
    assert len(rw.active_worktree_roots(ws)) == 1
