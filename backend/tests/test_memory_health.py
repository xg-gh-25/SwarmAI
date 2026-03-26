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

    def test_prompt_includes_gap_analysis_fields(self):
        from jobs.handlers.memory_health import _build_prompt
        prompt = _build_prompt("mem", "evo", "git", "daily")
        assert "capability_gaps" in prompt
        assert "stale_corrections" in prompt
        assert "occurrences" in prompt
        assert "suggested_action" in prompt


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

    def test_apply_report_with_capability_gaps(self):
        from jobs.handlers.memory_health import _apply_report
        report = {
            "stale_memories": [],
            "resolved_threads": [],
            "archived_capabilities": [],
            "stale_decisions": [],
            "capability_gaps": [
                {
                    "pattern": "pytest memory crashes",
                    "evidence": ["3/22: crash", "3/25: crash again"],
                    "occurrences": 3,
                    "suggested_action": "build skill",
                    "priority": "high",
                },
                {
                    "pattern": "DDD doc drift",
                    "evidence": ["3/24: manual fix"],
                    "occurrences": 2,
                    "suggested_action": "add steering rule",
                    "priority": "medium",
                },
            ],
            "stale_corrections": [
                {"id": "C003", "reason": "MCP code deleted"},
            ],
            "summary": "2 gaps, 1 stale correction",
        }
        actions = _apply_report(report, "", "")
        assert any("gap [high]" in a.lower() for a in actions)
        assert any("pytest memory" in a for a in actions)
        assert any("C003" in a for a in actions)

    def test_apply_report_caps_gaps_at_5(self):
        from jobs.handlers.memory_health import _apply_report
        report = {
            "stale_memories": [], "resolved_threads": [],
            "archived_capabilities": [], "stale_decisions": [],
            "capability_gaps": [
                {"pattern": f"gap_{i}", "occurrences": 1, "priority": "low"}
                for i in range(10)
            ],
            "summary": "many gaps",
        }
        actions = _apply_report(report, "", "")
        gap_actions = [a for a in actions if "Capability gap" in a]
        assert len(gap_actions) == 5  # Capped at 5


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
             patch("jobs.handlers.memory_health._call_llm", return_value=mock_report):
            result = run_memory_health()

        assert result["status"] == "success"
        assert len(result["actions"]) >= 2

        # Verify MEMORY.md was modified
        content = memory.read_text()
        assert "Ancient entry" not in content
        assert "Fresh entry" in content
        assert "auto-resolved" in content

    def test_full_run_with_capability_gaps(self, tmp_path):
        """Full run including capability gap detection and health_findings output."""
        from jobs.handlers.memory_health import run_memory_health

        memory = tmp_path / "MEMORY.md"
        memory.write_text("## Recent Context\n- 2026-03-25: test\n")
        evolution = tmp_path / "EVOLUTION.md"
        evolution.write_text("## Corrections\n### C003\n- old correction\n")

        mock_report = {
            "stale_memories": [],
            "resolved_threads": [],
            "archived_capabilities": [],
            "stale_decisions": [],
            "capability_gaps": [
                {
                    "pattern": "pytest OOM crashes",
                    "evidence": ["3/22: macOS crash", "3/25: memory crash"],
                    "occurrences": 3,
                    "suggested_action": "build memory-guard skill",
                    "priority": "high",
                },
            ],
            "stale_corrections": [
                {"id": "C003", "reason": "MCP conflation code deleted"},
            ],
            "summary": "1 capability gap detected, 1 stale correction",
        }

        daily_dir = tmp_path / "da"
        daily_dir.mkdir()
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()

        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path), \
             patch("jobs.handlers.memory_health.DAILY_DIR", daily_dir), \
             patch("jobs.handlers.memory_health.SWARMWS", tmp_path), \
             patch("jobs.paths.JOBS_DATA_DIR", jobs_dir), \
             patch("jobs.handlers.memory_health._call_llm", return_value=mock_report):
            result = run_memory_health()

        assert result["status"] == "success"
        assert len(result["capability_gaps"]) == 1
        assert result["capability_gaps"][0]["pattern"] == "pytest OOM crashes"
        assert len(result["stale_corrections"]) == 1

        # Verify health_findings.json was written with gap data
        findings = json.loads((jobs_dir / "health_findings.json").read_text())
        mem_health = findings["memory_health"]
        assert len(mem_health["capability_gaps"]) == 1
        assert mem_health["capability_gaps"][0]["priority"] == "high"
        assert len(mem_health["stale_corrections"]) == 1


# ── Proactive Intelligence Integration ────────────────────────────


class TestBriefingIntegration:
    """Test that capability gaps surface in session briefing."""

    def test_gaps_in_health_highlights(self, tmp_path):
        from core.proactive_intelligence import _get_health_highlights

        findings = {
            "timestamp": "2026-03-26T00:00:00",
            "findings": [],
            "memory_health": {
                "actions": [],
                "summary": "1 gap",
                "capability_gaps": [
                    {
                        "pattern": "pytest memory crashes",
                        "occurrences": 3,
                        "suggested_action": "build skill",
                        "priority": "high",
                    },
                ],
                "stale_corrections": [
                    {"id": "C003", "reason": "code deleted"},
                ],
                "timestamp": "2026-03-26T00:00:00",
            },
        }

        jobs_dir = tmp_path / "Services" / "swarm-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "health_findings.json").write_text(json.dumps(findings))

        lines = _get_health_highlights(str(tmp_path))
        assert any("[gap/high]" in line for line in lines)
        assert any("pytest memory" in line for line in lines)
        assert any("[stale-correction]" in line for line in lines)
        assert any("C003" in line for line in lines)

    def test_no_gaps_no_extra_lines(self, tmp_path):
        from core.proactive_intelligence import _get_health_highlights

        findings = {
            "timestamp": "2026-03-26T00:00:00",
            "findings": [],
            "memory_health": {
                "actions": ["Removed 1 entry"],
                "summary": "clean",
                "capability_gaps": [],
                "stale_corrections": [],
                "timestamp": "2026-03-26T00:00:00",
            },
        }

        jobs_dir = tmp_path / "Services" / "swarm-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "health_findings.json").write_text(json.dumps(findings))

        lines = _get_health_highlights(str(tmp_path))
        assert not any("[gap" in line for line in lines)
        assert not any("[stale-correction]" in line for line in lines)

    def test_gaps_capped_at_3_in_briefing(self, tmp_path):
        from core.proactive_intelligence import _get_health_highlights

        findings = {
            "timestamp": "2026-03-26T00:00:00",
            "findings": [],
            "memory_health": {
                "actions": [],
                "summary": "many gaps",
                "capability_gaps": [
                    {"pattern": f"gap {i}", "occurrences": 2, "priority": "low"}
                    for i in range(10)
                ],
                "stale_corrections": [],
                "timestamp": "2026-03-26T00:00:00",
            },
        }

        jobs_dir = tmp_path / "Services" / "swarm-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "health_findings.json").write_text(json.dumps(findings))

        lines = _get_health_highlights(str(tmp_path))
        gap_lines = [l for l in lines if "[gap/" in l]
        assert len(gap_lines) == 3  # Capped at 3 in briefing
