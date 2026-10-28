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
    monkeypatch.setattr(ddd_refresh, "_recent_commits", lambda p: [{"sha": "abc", "record": "full"}])
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
    monkeypatch.setattr(ddd_refresh, "_recent_commits", lambda p: rows)
    calls = []
    monkeypatch.setattr(ddd_refresh, "extract_commits", lambda *a, **k:
                        calls.append((a, k)) or [object()])
    result = ddd_refresh.run_ddd_refresh()
    assert result["units_queued"] == 1
    assert calls[0][0][0] == rows
    assert calls[0][1]["project_hint"] == "P"
    assert not (project / ".artifacts").exists()
