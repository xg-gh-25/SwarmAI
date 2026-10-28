"""
Tests for jobs/handlers/memory_health.py — LLM-powered weekly maintenance.

Tests cover input gathering, prompt building, report application,
and DailyActivity summary writing. LLM calls are mocked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch



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

        # Phase 1 integrity checks still run (on empty content), but
        # Phase 2 LLM maintenance is skipped when no context files exist.
        assert result["status"] in ("skipped", "integrity_only")

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

        # Stage-B health reports findings but never mutates established corpus.
        content = memory.read_text()
        assert "Ancient entry" in content
        assert "Fresh entry" in content
        assert "auto-resolved" not in content

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
class TestExtractSection:
    """_extract_section: slice a `## <name>` section from a MEMORY.md string."""

    def test_extracts_named_section_to_next_h2(self):
        from jobs.handlers.memory_health import _extract_section
        text = (
            "## Alpha\nalpha body\n\n"
            "## Open Threads\n\n"
            "### P1\n- 🟡 **Thing** — detail\n\n"
            "## Trailer\ntrailer body\n"
        )
        section = _extract_section(text, "Open Threads")
        assert section.startswith("## Open Threads")
        assert "**Thing**" in section  # includes ### subsections
        assert "trailer body" not in section  # stops at next ## header
        assert "alpha body" not in section  # doesn't leak the prior section

    def test_extracts_to_eof_when_last_section(self):
        from jobs.handlers.memory_health import _extract_section
        text = "## Head\nx\n\n## Open Threads\n- item A\n- item B\n"
        section = _extract_section(text, "Open Threads")
        assert "item A" in section and "item B" in section

    def test_missing_section_returns_empty(self):
        from jobs.handlers.memory_health import _extract_section
        assert _extract_section("## Only\nbody\n", "Open Threads") == ""


class TestOpenThreadsInputCapped:
    """Meta-review MED: the appended Open Threads section must be bounded so it
    can't grow unbounded as the (append-only) section bloats."""

    def test_bloated_open_threads_is_capped_in_phase2_input(self, tmp_path):
        from jobs.handlers.memory_health import run_memory_health

        head = "## Recent Context\n\n" + ("- filler\n" * 900)
        # A bloated Open Threads section >> 8000 chars.
        top_marker = "TOP_OT_MARKER"
        bottom_marker = "BOTTOM_OT_MARKER_SHOULD_BE_TRUNCATED"
        ot_body = (
            f"- 🟡 **{top_marker}** — active top item\n"
            + ("- 🟡 **filler open thread line to bloat the section** — x\n" * 400)
            + f"- 🟡 **{bottom_marker}** — far past 8000 chars into the section\n"
        )
        assert len(ot_body) > 8000
        memory = tmp_path / "MEMORY.md"
        memory.write_text(head + "\n## Open Threads\n\n" + ot_body)
        daily_dir = tmp_path / "da"
        daily_dir.mkdir()

        captured = {}

        def fake_build_prompt(memory_md, evolution_md, git_log, daily_activity):
            captured["memory_md"] = memory_md
            return "PROMPT"

        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path), \
             patch("jobs.handlers.memory_health.DAILY_DIR", daily_dir), \
             patch("jobs.handlers.memory_health.SWARMWS", tmp_path), \
             patch("jobs.handlers.memory_health._build_prompt", side_effect=fake_build_prompt), \
             patch("jobs.handlers.memory_health._call_llm", return_value={"summary": "ok"}):
            run_memory_health()

        md = captured["memory_md"]
        assert "## Open Threads" in md
        assert top_marker in md, "top (active) Open Threads items must be included"
        assert bottom_marker not in md, "bloated tail must be truncated by the cap"


class TestPhase2SeesOpenThreads:
    """Defect 1: Phase 2 LLM input must contain the tail Open Threads section,
    not just full_memory[:8000]."""

    def test_open_threads_included_when_beyond_8k(self, tmp_path):
        from jobs.handlers.memory_health import run_memory_health

        # Build a MEMORY.md where Open Threads lives WELL past char 8000.
        marker = "UNIQUE_OT_MARKER_ZzZ"
        head = "## Recent Context\n\n" + ("- filler line padding\n" * 900)
        assert len(head) > 8000
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            head
            + "\n## Open Threads\n\n"
            + f"- 🟡 **{marker}** — an open thread far past the 8000 char cap\n"
        )
        daily_dir = tmp_path / "da"
        daily_dir.mkdir()

        captured = {}

        def fake_build_prompt(memory_md, evolution_md, git_log, daily_activity):
            captured["memory_md"] = memory_md
            return "PROMPT"

        # Stop after prompt is built — we only need to inspect the input.
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path), \
             patch("jobs.handlers.memory_health.DAILY_DIR", daily_dir), \
             patch("jobs.handlers.memory_health.SWARMWS", tmp_path), \
             patch("jobs.handlers.memory_health._build_prompt", side_effect=fake_build_prompt), \
             patch("jobs.handlers.memory_health._call_llm", return_value={"summary": "ok"}):
            run_memory_health()

        assert "memory_md" in captured, "_build_prompt was not called"
        assert "## Open Threads" in captured["memory_md"]
        assert marker in captured["memory_md"], (
            "Open Threads section past 8000 chars must be fed to the LLM"
        )

