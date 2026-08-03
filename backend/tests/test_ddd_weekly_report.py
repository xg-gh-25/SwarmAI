"""
Tests for DDD Weekly Report job handler.

Verifies: multi-project scanning, changelog aggregation, escalation detection,
health stats, markdown generation, edge cases (0 projects, empty data).
"""

import json
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

    # Project 2: ClientOrg — empty (no changelog, no escalations)
    p2 = projects / "SalesIntel"
    p2.mkdir()
    (p2 / "PRODUCT.md").write_text("# ClientOrg Product\n")
    (p2 / "TECH.md").write_text("# ClientOrg Tech\n")

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
        assert "SalesIntel" in report

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
        assert "**SalesIntel**" in report
        # Shows line counts
        assert "L" in report  # e.g., "5L"

    def test_surfaces_created_section_drift(self, tmp_path):
        """When changelog entries have created_section=true, the report must
        surface them (Health Dashboard line + Next Week reconcile action) so the
        operator knows a DDD doc template drifted from ROUTING_TABLE and a section
        was auto-created. Without this, drift auto-heals silently forever."""
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        projects = tmp_path / "Projects"
        projects.mkdir()
        p = projects / "SwarmAI"
        p.mkdir()
        (p / "TECH.md").write_text("# Tech\n\n## Architecture\n\n- a\n## Runtime Traps\n\n- t\n")
        (p / "IMPROVEMENT.md").write_text("# L\n\n## What Worked\n\n- w\n")
        artifacts = p / ".artifacts"
        artifacts.mkdir()
        now = datetime.now(timezone.utc)
        # Neutral content/run-id so the assertion can't be satisfied by the
        # fixture's own wording leaking into the Change Log table (true RED).
        entries = [
            {"id": "c1", "action": "applied", "created_section": True,
             "target_doc": "TECH.md", "target_section": "Runtime Traps",
             "content": "lesson alpha", "source_run_id": "run_aaa",
             "timestamp": now.isoformat()},
            {"id": "c2", "action": "applied", "created_section": False,
             "target_doc": "IMPROVEMENT.md", "target_section": "What Worked",
             "content": "lesson beta", "source_run_id": "run_bbb",
             "timestamp": now.isoformat()},
        ]
        (artifacts / "ddd-changelog.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n")

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", projects), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", tmp_path):
            result = run_ddd_weekly_report()

        report = Path(result["output_path"]).read_text()
        # A dedicated drift line must be emitted (structural marker, not incidental
        # content). It names the auto-created section so the operator can reconcile.
        assert "auto-created section" in report.lower(), (
            "Report must emit a dedicated 'auto-created section' drift line")
        assert "TECH.md" in report and "Runtime Traps" in report, (
            "Drift line must name the doc + section to reconcile")
        # Only the created_section=true entry counts toward drift, not the normal one.
        assert "1 section" in report.lower() or "1 auto-created" in report.lower(), (
            "Report should count exactly 1 auto-created section")

    def test_created_section_markdown_sanitized(self, tmp_path):
        """A corrupted changelog with a pipe/backtick/newline in the section name
        must not break the markdown table/bullet (adversarial LOW). _read_changelog
        does not re-validate against the whitelist, so render must sanitize."""
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        projects = tmp_path / "Projects"
        projects.mkdir()
        p = projects / "SwarmAI"
        p.mkdir()
        (p / "TECH.md").write_text("# T\n\n## Architecture\n\n- a\n")
        artifacts = p / ".artifacts"
        artifacts.mkdir()
        now = datetime.now(timezone.utc)
        entry = {"id": "c1", "action": "applied", "created_section": True,
                 "target_doc": "TECH.md", "target_section": "Evil | Pipe`tick",
                 "content": "x", "source_run_id": "run_x", "timestamp": now.isoformat()}
        (artifacts / "ddd-changelog.jsonl").write_text(json.dumps(entry) + "\n")

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", projects), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", tmp_path):
            result = run_ddd_weekly_report()

        report = Path(result["output_path"]).read_text()
        # Raw pipe must be escaped in the drift bullet (no unescaped '| Pipe').
        assert "Evil \\| Pipe" in report, "pipe must be escaped in drift line"
        # Backtick neutralized (no stray backtick from the section name).
        assert "Pipe`tick" not in report, "backtick must be neutralized"

    def test_no_created_section_line_when_none(self, workspace):
        """The drift line must NOT appear when no section was auto-created
        (avoid noise — the shared fixture has created_section absent/false)."""
        from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report

        with patch("jobs.handlers.ddd_weekly_report.PROJECTS_DIR", workspace / "Projects"), \
             patch("jobs.handlers.ddd_weekly_report.SWARMWS", workspace):
            result = run_ddd_weekly_report()

        report = Path(result["output_path"]).read_text()
        assert "auto-created section" not in report.lower(), (
            "No drift line should appear when nothing was auto-created")

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
