"""Tests for jobs.handlers.ddd_refresh — L4 autonomous DDD refresh.

Tests: staleness detection, context gathering, proposal output, integration
with maintenance job.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from jobs.handlers.ddd_refresh import (
    run_ddd_refresh,
    _check_staleness,
    _gather_project_context,
    _write_proposal,
)


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    """Create a minimal project structure."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    proj = projects / "TestProject"
    proj.mkdir()
    (proj / "TECH.md").write_text("# TestProject Tech\n\n## Stack\nPython, React\n")
    (proj / "IMPROVEMENT.md").write_text("# Improvement\n\n## What Worked\n- Tests\n")

    # Patch PROJECTS_DIR
    monkeypatch.setattr("jobs.handlers.ddd_refresh.PROJECTS_DIR", projects)
    monkeypatch.setattr("jobs.handlers.ddd_refresh.SWARMWS", tmp_path)
    return proj


class TestCheckStaleness:
    def test_fresh_doc_not_stale(self, project_dir):
        """TECH.md updated today should not be stale."""
        result = _check_staleness(project_dir)
        assert result["stale"] is False
        assert result["age_days"] == 0

    def test_old_doc_without_commits_not_stale(self, project_dir, monkeypatch):
        """Old TECH.md but no recent commits → not stale."""
        import os
        tech = project_dir / "TECH.md"
        old_time = (datetime.now() - timedelta(days=30)).timestamp()
        os.utime(tech, (old_time, old_time))

        monkeypatch.setattr(
            "jobs.handlers.ddd_refresh._count_recent_commits", lambda days: 0
        )
        result = _check_staleness(project_dir)
        assert result["stale"] is False


class TestGatherProjectContext:
    def test_reads_tech_md(self, project_dir, monkeypatch):
        monkeypatch.setattr("jobs.handlers.ddd_refresh._find_swarmai_root", lambda: None)
        # Patch the import inside _gather_project_context
        monkeypatch.setattr(
            "core.engine_metrics.collect_ddd_change_suggestions",
            lambda ws: [],
        )
        context = _gather_project_context(
            project_dir, {"age_days": 10, "commit_count": 5}
        )
        assert "TestProject" in context["project_name"]
        assert "Python, React" in context["current_tech_md"]
        assert "Tests" in context.get("current_improvement_md", "")


class TestWriteProposal:
    def test_writes_markdown_and_json(self, project_dir):
        proposal = {
            "no_changes": False,
            "summary": "Updated Stack section",
            "confidence": 8,
            "tech_md_updates": [
                {
                    "section": "## Stack",
                    "action": "modify",
                    "current_text": "Python, React",
                    "proposed_text": "Python 3.12, React 18, FastAPI",
                    "reason": "Added specific versions",
                }
            ],
            "improvement_md_updates": [],
        }
        _write_proposal(project_dir, proposal)

        artifacts = project_dir / ".artifacts"
        assert artifacts.is_dir()

        md_files = list(artifacts.glob("ddd-refresh-*.md"))
        json_files = list(artifacts.glob("ddd-refresh-*.json"))
        assert len(md_files) == 1
        assert len(json_files) == 1

        md_content = md_files[0].read_text()
        assert "Updated Stack section" in md_content
        assert "Python 3.12, React 18, FastAPI" in md_content
        assert "8/10" in md_content

    def test_creates_artifacts_dir(self, tmp_path):
        """Should create .artifacts/ if it doesn't exist."""
        proj = tmp_path / "NewProject"
        proj.mkdir()
        proposal = {"summary": "test", "confidence": 5, "tech_md_updates": []}
        _write_proposal(proj, proposal)
        assert (proj / ".artifacts").is_dir()


class TestRunDddRefresh:
    def test_skips_when_no_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jobs.handlers.ddd_refresh.PROJECTS_DIR", tmp_path / "nope")
        result = run_ddd_refresh()
        assert result["status"] == "skipped"

    def test_skips_fresh_projects(self, project_dir, monkeypatch):
        """Fresh TECH.md should not trigger a refresh."""
        monkeypatch.setattr(
            "jobs.handlers.ddd_refresh._check_staleness",
            lambda d: {"stale": False, "age_days": 1, "commit_count": 0},
        )
        result = run_ddd_refresh()
        assert result["status"] == "success"
        assert result["proposals_written"] == 0

    def test_dry_run_doesnt_call_llm(self, project_dir, monkeypatch):
        """Dry run should detect staleness but not call LLM."""
        monkeypatch.setattr(
            "jobs.handlers.ddd_refresh._check_staleness",
            lambda d: {"stale": True, "age_days": 14, "commit_count": 10},
        )
        result = run_ddd_refresh(dry_run=True)
        assert result["status"] == "success"
        assert result["proposals_written"] == 0  # dry run doesn't write
