"""
Tests for jobs/handlers/memory_health.py — LLM-powered weekly maintenance.

Tests cover input gathering, prompt building, report application,
and DailyActivity summary writing. LLM calls are mocked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── Input Gathering ─────────────────────────────────────────────────

class TestInputGathering:
    def test_read_context_file_missing(self, tmp_path):
        from jobs.handlers.memory_health import _read_context_file
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            assert _read_context_file("NONEXISTENT.md") == ""

    def test_read_context_file_caps_at_8k(self, tmp_path):
        from jobs.handlers.memory_health import _read_context_file
        big_file = tmp_path / "BIG.md"
        big_file.write_text("x" * 20000)
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            content = _read_context_file("BIG.md")
            assert len(content) == 8000

    def test_get_recent_daily_activity(self, tmp_path):
        from jobs.handlers.memory_health import _get_recent_daily_activity
        daily_dir = tmp_path / "DailyActivity"
        daily_dir.mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (daily_dir / f"{today}.md").write_text("## Session\nDid stuff today")

        with patch("jobs.handlers.memory_health.DAILY_DIR", daily_dir):
            content = _get_recent_daily_activity(days=1)
            assert "Did stuff today" in content

    def test_get_recent_daily_activity_empty(self, tmp_path):
        from jobs.handlers.memory_health import _get_recent_daily_activity
        with patch("jobs.handlers.memory_health.DAILY_DIR", tmp_path / "nope"):
            assert _get_recent_daily_activity(days=7) == ""


# ── Prompt Building ─────────────────────────────────────────────────

class TestPromptBuilding:
    def test_prompt_includes_all_sections(self):
        from jobs.handlers.memory_health import _build_prompt
        prompt = _build_prompt(
            memory_md="## Recent Context\n- test entry",
            evolution_md="## Capabilities\n- E001",
            git_log="abc123 fix: something",
            daily_activity="## 2026-03-25\nDid work",
        )
        assert "MEMORY.md" in prompt
        assert "EVOLUTION.md" in prompt
        assert "Git Commits" in prompt
        assert "DailyActivity" in prompt
        assert "stale_memories" in prompt

    def test_prompt_handles_empty_inputs(self):
        from jobs.handlers.memory_health import _build_prompt
        prompt = _build_prompt("", "", "", "")
        assert "(no commits)" in prompt
        assert "(no activity)" in prompt


# ── Report Application ──────────────────────────────────────────────

class TestApplyReport:
    def test_removes_stale_memory(self, tmp_path):
        from jobs.handlers.memory_health import _remove_memory_entry
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(
            "## Recent Context\n\n"
            "- 2026-03-10: Old entry that is stale\n"
            "- 2026-03-25: Fresh entry\n"
        )
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            _remove_memory_entry("Recent Context", "2026-03-10: Old entry")

        content = memory_path.read_text()
        assert "Old entry" not in content
        assert "Fresh entry" in content

    def test_resolve_open_thread(self, tmp_path):
        from jobs.handlers.memory_health import _resolve_open_thread
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(
            "### P2 — Nice to have\n"
            "- 🔵 **Signal fetcher service** — not built yet\n"
            "\n"
            "### Resolved (archive)\n"
            "- ✅ Old resolved item\n"
        )
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            _resolve_open_thread("Signal fetcher service")

        content = memory_path.read_text()
        assert "✅" in content
        assert "auto-resolved" in content

    def test_apply_report_with_stale_and_resolved(self, tmp_path):
        from jobs.handlers.memory_health import _apply_report
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(
            "## Recent Context\n\n"
            "- 2026-03-01: Very old entry\n"
            "- 2026-03-25: Fresh\n"
            "\n### P2 — Nice to have\n"
            "- 🔵 **Test thread** — something\n"
            "\n### Resolved (archive)\n"
        )

        report = {
            "stale_memories": [{"entry_prefix": "2026-03-01: Very old", "reason": "old"}],
            "resolved_threads": [{"title": "Test thread", "evidence": "fixed in git"}],
            "archived_capabilities": [],
            "stale_decisions": [],
            "ddd_staleness": [],
            "summary": "Light maintenance needed",
        }

        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            actions = _apply_report(report, memory_path.read_text(), "")

        assert len(actions) >= 2
        assert any("Removed stale" in a for a in actions)
        assert any("Resolved thread" in a for a in actions)

    def test_apply_report_parse_error(self):
        from jobs.handlers.memory_health import _apply_report
        report = {"parse_error": True, "summary": "bad json"}
        actions = _apply_report(report, "", "")
        assert len(actions) == 1
        assert "parse error" in actions[0]

    def test_apply_report_empty(self):
        from jobs.handlers.memory_health import _apply_report
        report = {
            "stale_memories": [],
            "resolved_threads": [],
            "archived_capabilities": [],
            "stale_decisions": [],
            "summary": "All clear",
        }
        actions = _apply_report(report, "", "")
        assert len(actions) == 0


# ── Summary Writing ─────────────────────────────────────────────────

class TestSummaryWriting:
    def test_writes_to_daily_activity(self, tmp_path):
        from jobs.handlers.memory_health import _write_summary_to_daily
        with patch("jobs.handlers.memory_health.DAILY_DIR", tmp_path):
            _write_summary_to_daily(
                {"summary": "All healthy"},
                ["Removed 1 stale entry"],
            )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_file = tmp_path / f"{today}.md"
        assert daily_file.exists()
        content = daily_file.read_text()
        assert "Weekly Memory Health" in content
        assert "All healthy" in content
        assert "Removed 1 stale entry" in content

    def test_appends_to_existing_daily(self, tmp_path):
        from jobs.handlers.memory_health import _write_summary_to_daily
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_file = tmp_path / f"{today}.md"
        daily_file.write_text("## Existing content\nSome stuff\n")

        with patch("jobs.handlers.memory_health.DAILY_DIR", tmp_path):
            _write_summary_to_daily({"summary": "Done"}, ["Action 1"])

        content = daily_file.read_text()
        assert "Existing content" in content
        assert "Weekly Memory Health" in content

    def test_skips_when_nothing_to_report(self, tmp_path):
        from jobs.handlers.memory_health import _write_summary_to_daily
        with patch("jobs.handlers.memory_health.DAILY_DIR", tmp_path):
            _write_summary_to_daily({}, [])

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert not (tmp_path / f"{today}.md").exists()


# ── Full Run (Mocked LLM) ──────────────────────────────────────────

class TestFullRun:
    def test_dry_run(self, tmp_path):
        from jobs.handlers.memory_health import run_memory_health
        memory = tmp_path / "MEMORY.md"
        memory.write_text("## Recent Context\n- test\n")

        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path), \
             patch("jobs.handlers.memory_health.DAILY_DIR", tmp_path / "da"):
            result = run_memory_health(dry_run=True)

        assert result["status"] == "dry_run"

    def test_skips_when_no_context_files(self, tmp_path):
        from jobs.handlers.memory_health import run_memory_health
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path), \
             patch("jobs.handlers.memory_health.DAILY_DIR", tmp_path / "da"):
            result = run_memory_health()

        assert result["status"] == "skipped"

    def test_full_run_mocked(self, tmp_path):
        from jobs.handlers.memory_health import run_memory_health

        # Setup context files
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Recent Context\n\n"
            "- 2026-02-01: Ancient entry\n"
            "- 2026-03-25: Fresh entry\n"
            "\n### P2 — Nice to have\n"
            "- 🔵 **Stale thread** — done already\n"
            "\n### Resolved (archive)\n"
        )
        evolution = tmp_path / "EVOLUTION.md"
        evolution.write_text("## Capabilities\n- E001 test\n")

        mock_report = {
            "stale_memories": [
                {"entry_prefix": "2026-02-01: Ancient", "reason": ">30 days old"}
            ],
            "resolved_threads": [
                {"title": "Stale thread", "evidence": "done in git"}
            ],
            "archived_capabilities": [],
            "stale_decisions": [],
            "ddd_staleness": [],
            "summary": "1 stale memory, 1 resolved thread",
        }

        daily_dir = tmp_path / "da"
        daily_dir.mkdir()

        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path), \
             patch("jobs.handlers.memory_health.DAILY_DIR", daily_dir), \
             patch("jobs.handlers.memory_health.SWARMWS", tmp_path), \
             patch("jobs.handlers.memory_health._call_haiku", return_value=mock_report):
            result = run_memory_health()

        assert result["status"] == "success"
        assert len(result["actions"]) >= 2

        # Verify MEMORY.md was modified
        content = memory.read_text()
        assert "Ancient entry" not in content
        assert "Fresh entry" in content
        assert "auto-resolved" in content
