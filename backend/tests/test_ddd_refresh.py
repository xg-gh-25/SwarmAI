"""Tests for the scheduled S4 adapter in ``jobs.handlers.ddd_refresh``.

The retired staleness proposal writer is intentionally absent. These tests prove the
job reads complete commit records and delegates without writing project artifacts.
"""
from __future__ import annotations

from jobs.handlers import ddd_refresh


def test_skips_when_no_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(ddd_refresh, "PROJECTS_DIR", tmp_path / "missing")
    assert ddd_refresh.run_ddd_refresh()["status"] == "skipped"


def test_dry_run_reads_commits_but_does_not_enqueue(tmp_path, monkeypatch):
    projects = tmp_path / "Projects"; project = projects / "P"; project.mkdir(parents=True)
    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setattr(ddd_refresh, "PROJECTS_DIR", projects)
    monkeypatch.setattr(ddd_refresh, "governed_repo_roots", lambda p: [repo])
    monkeypatch.setattr(ddd_refresh, "_new_commits", lambda p, seen: ([{"sha": "abc", "record": "full"}], "h"))
    monkeypatch.setattr(ddd_refresh, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(ddd_refresh, "extract_commits", lambda *a, **k:
                        (_ for _ in ()).throw(AssertionError("dry run enqueued")))
    result = ddd_refresh.run_ddd_refresh(dry_run=True)
    assert result["commits_seen"] == 1 and result["units_queued"] == 0
    assert not (project / ".artifacts").exists()


def test_job_delegates_complete_rows_to_s4(tmp_path, monkeypatch):
    projects = tmp_path / "Projects"; project = projects / "P"; project.mkdir(parents=True)
    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setattr(ddd_refresh, "PROJECTS_DIR", projects)
    monkeypatch.setattr(ddd_refresh, "SWARMWS", tmp_path)
    monkeypatch.setattr(ddd_refresh, "governed_repo_roots", lambda p: [repo])
    rows = [{"sha": "abc", "record": "complete commit and patch"}]
    monkeypatch.setattr(ddd_refresh, "_new_commits", lambda p, seen: (rows, "h"))
    monkeypatch.setattr(ddd_refresh, "_state_path", lambda: tmp_path / "state.json")
    calls = []
    monkeypatch.setattr(ddd_refresh, "extract_commits", lambda *a, **k:
                        calls.append((a, k)) or [object()])
    result = ddd_refresh.run_ddd_refresh()
    assert result["units_queued"] == 1
    assert calls[0][0][0] == rows
    assert calls[0][1]["project_hint"] == "P"
    assert not (project / ".artifacts").exists()



# ── the window is "since the last scan", not a fixed day: the job runs weekly ─────

import subprocess


def _commit(repo, name):
    (repo / name).write_text(name, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", name],
                   cwd=repo, check=True, capture_output=True)


def test_each_scan_reads_the_commits_since_the_last_one(tmp_path, monkeypatch):
    projects = tmp_path / "Projects"; (projects / "P").mkdir(parents=True)
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    _commit(repo, "a"); _commit(repo, "b")
    monkeypatch.setattr(ddd_refresh, "PROJECTS_DIR", projects)
    monkeypatch.setattr(ddd_refresh, "SWARMWS", tmp_path)
    monkeypatch.setattr(ddd_refresh, "governed_repo_roots", lambda p: [repo])
    monkeypatch.setattr(ddd_refresh, "_state_path", lambda: tmp_path / "state" / "s4.json")
    seen = []
    monkeypatch.setattr(ddd_refresh, "extract_commits", lambda rows, **k: seen.append(len(rows)) or [])
    assert ddd_refresh.run_ddd_refresh(dry_run=True)["commits_seen"] == 2
    assert not (tmp_path / "state" / "s4.json").exists()        # a dry run marks nothing
    ddd_refresh.run_ddd_refresh()
    _commit(repo, "c")
    ddd_refresh.run_ddd_refresh()
    ddd_refresh.run_ddd_refresh()
    assert seen == [2, 1, 0]


def test_a_rewritten_history_falls_back_to_the_time_of_the_last_scan(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    _commit(repo, "a")
    rows, head = ddd_refresh._new_commits(repo, {"head": "0" * 40, "at": "2000-01-01T00:00:00+00:00"})
    assert len(rows) == 1 and head
    from datetime import datetime, timedelta, timezone
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()   # the form run_ddd_refresh stores
    rows, _ = ddd_refresh._new_commits(repo, {"head": "0" * 40, "at": later})
    assert rows == []


def test_s4_reads_large_batches_in_several_calls(monkeypatch):
    from core.ingestion.sources import s4_code
    calls = []
    monkeypatch.setattr(s4_code.api_v2, "register_root", lambda *a: "root")
    monkeypatch.setattr(s4_code.common, "extract_and_enqueue", lambda *a, **k: calls.append(a[1]) or [a[1]])
    big = "x" * (s4_code.BATCH_CHARS // 2 + 1)
    rows = [{"sha": f"s{i}", "repository": "/r", "record": big} for i in range(3)]
    out = s4_code.extract_commits(rows, run_landed_commits=set())
    assert calls == ["s0", "s1", "s2"] and len(out) == 3
    small = [{"sha": f"t{i}", "repository": "/r", "record": "y"} for i in range(3)]
    calls.clear()
    s4_code.extract_commits(small, run_landed_commits=set())
    assert calls == ["t0,t1,t2"]
