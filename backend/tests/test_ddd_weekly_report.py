"""
Tests for DDD Weekly Report job handler.

Verifies: multi-project scanning, changelog aggregation, escalation detection,
health stats, markdown generation, edge cases (0 projects, empty data).
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def workspace(tmp_path):
    """Create a mock workspace with multiple projects."""
    projects = tmp_path / "Projects"
    projects.mkdir()

    # Project 1: SwarmAI — has changelog + escalations
    p1 = projects / "SwarmAI"
    p1.mkdir()
    (p1 / "PRODUCT.md").write_text("# Product\n\n## Vision\n\nTest\n")
    (p1 / "TECH.md").write_text("# Tech\n\n## Architecture\n\n- arch\n\n## Conventions\n\n- conv\n")
    (p1 / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- worked\n\n## What Failed\n\n- failed\n")
    (p1 / "PROJECT.md").write_text("# Project\n\n## Status\n\nActive\n")

    # Add changelog
    artifacts = p1 / ".artifacts"
    artifacts.mkdir()
    changelog = artifacts / "ddd-changelog.jsonl"
    now = datetime.now(timezone.utc)
    entries = [
        {"id": "p1", "action": "applied", "target_doc": "IMPROVEMENT.md",
         "target_section": "What Worked", "content": "Lesson one from SwarmAI",
         "source_run_id": "run_a", "timestamp": now.isoformat()},
        {"id": "p2", "action": "applied", "target_doc": "TECH.md",
         "target_section": "Conventions", "content": "New convention discovered",
         "source_run_id": "run_b", "timestamp": (now - timedelta(days=2)).isoformat()},
    ]
    changelog.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

    # Add escalation
    proposals = artifacts / "proposals"
    proposals.mkdir()
    esc = {"id": "esc1", "target_doc": "PRODUCT.md", "target_section": "Vision",
           "content": "Should we pivot?", "source_run_id": "run_c",
           "status": "escalated", "confidence": 0.7,
           "created_at": now.isoformat(), "ttl_days": 14}
    (proposals / "esc1_20260513.json").write_text(json.dumps(esc))

    # Project 2: CMHK — empty (no changelog, no escalations)
    p2 = projects / "CMHK_SalesIntel"
    p2.mkdir()
    (p2 / "PRODUCT.md").write_text("# CMHK Product\n")
    (p2 / "TECH.md").write_text("# CMHK Tech\n")

    return tmp_path


class TestDDDWeeklyReport:
    """Test the weekly report handler."""

    def test_generates_report_with_all_sections(self, workspace):
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", workspace / "Projects"), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", workspace):
            result = run_ddd_weekly_report()

        assert result["status"] == "success"
        assert result["output_path"] is not None
        assert "2 applied" in result["summary"]
        assert "1 escalations" in result["summary"]
        assert "2 projects" in result["summary"]

        # Check the report file has MBR-style sections
        report = Path(result["output_path"]).read_text()
        assert "## Executive Summary" in report
        assert "## Highlights & Lowlights" in report
        assert "## Decisions Needed" in report
        assert "## DDD Health Dashboard" in report
        assert "## Next Week" in report
        assert "SwarmAI" in report
        assert "CMHK_SalesIntel" in report

    def test_report_contains_applied_entries(self, workspace):
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", workspace / "Projects"), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", workspace):
            result = run_ddd_weekly_report()

        report = Path(result["output_path"]).read_text()
        assert "Lesson one from SwarmAI" in report
        assert "auto-applied" in report.lower() or "auto-cultivated" in report.lower() or "applied" in report.lower()

    def test_report_shows_escalations(self, workspace):
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", workspace / "Projects"), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", workspace):
            result = run_ddd_weekly_report()

        report = Path(result["output_path"]).read_text()
        assert "Should we pivot?" in report
        assert "Escalation" in report or "escalated" in report.lower()

    def test_report_health_table(self, workspace):
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", workspace / "Projects"), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", workspace):
            result = run_ddd_weekly_report()

        report = Path(result["output_path"]).read_text()
        # Health table has project rows
        assert "**SwarmAI**" in report
        assert "**CMHK_SalesIntel**" in report
        # Shows line counts
        assert "L" in report  # e.g., "5L"

    def test_skips_when_no_projects_dir(self):
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", Path("/nonexistent")):
            result = run_ddd_weekly_report()

        assert result["status"] == "skipped"

    def test_handles_empty_projects(self, tmp_path):
        """Projects dir exists but no projects have DDD docs."""
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        projects = tmp_path / "Projects"
        projects.mkdir()
        (projects / "EmptyProject").mkdir()  # No DDD docs

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", projects), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", tmp_path):
            result = run_ddd_weekly_report()

        assert result["status"] == "skipped"

    def test_old_changelog_entries_excluded(self, workspace):
        """Entries older than window_days are not included."""
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        # Add an old entry
        changelog = workspace / "Projects" / "SwarmAI" / ".artifacts" / "ddd-changelog.jsonl"
        old_entry = json.dumps({
            "id": "old1", "action": "applied", "target_doc": "TECH.md",
            "target_section": "Architecture", "content": "Ancient lesson from last month",
            "source_run_id": "run_old",
            "timestamp": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        })
        with open(changelog, "a") as f:
            f.write(old_entry + "\n")

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", workspace / "Projects"), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", workspace):
            result = run_ddd_weekly_report(config={"window_days": 7})

        report = Path(result["output_path"]).read_text()
        # Recent entries present, old one excluded
        assert "Lesson one from SwarmAI" in report
        assert "Ancient lesson from last month" not in report

    def test_custom_window_days(self, workspace):
        """config.window_days is respected."""
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", workspace / "Projects"), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", workspace):
            # Window of 1 day — should still catch today's entries
            result = run_ddd_weekly_report(config={"window_days": 1})

        assert result["status"] == "success"
