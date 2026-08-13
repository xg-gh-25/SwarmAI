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

class TestApplyReport:
    def test_removes_stale_memory(self, tmp_path):
        from jobs.handlers.memory_health import _remove_memory_entry
        memory_path = tmp_path / "MEMORY.md"
        file_content = (
            "## Recent Context\n\n"
            "- [RC01] 2026-03-10: Old entry that is stale | keywords\n"
            "- [RC02] 2026-03-25: Fresh entry | keywords\n"
        )
        memory_path.write_text(file_content)
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            # Prefix as LLM would return it (no markdown formatting)
            result = _remove_memory_entry("RC01 2026-03-10: Old entry that is stale")

        assert result is True
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


class TestResolveNonEmojiThread:
    """Defect 2 + neighbors: _resolve_open_thread must match an emoji-LESS
    bold-title thread, carry its trailing metadata comment, and return a bool."""

    def test_resolves_non_emoji_bold_thread(self, tmp_path):
        from jobs.handlers.memory_health import _resolve_open_thread
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Open Threads\n\n"
            "### P1 — Important (prod-verify pending)\n\n"
            " **DEAD-resume race — FIXED + DEPLOYED + VERIFIED:** all done, 0 recurrence.\n"
            "  <!-- ref:0 | last:none | decay:active | source:manual -->\n"
            "- 🟡 **Other thread** — still open\n"
            "\n### Resolved (archive)\n"
        )
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            result = _resolve_open_thread("DEAD-resume race")

        assert result is True, "must return True on a real match"
        content = memory.read_text()
        # The item moved into the Resolved section with a ✅ marker.
        resolved_idx = content.index("### Resolved (archive)")
        assert "DEAD-resume race" in content[resolved_idx:], "moved to Resolved"
        assert "✅" in content[resolved_idx:], "resolved entry carries ✅ marker"
        assert "auto-resolved" in content[resolved_idx:]
        # The trailing metadata comment is carried WITH it, not orphaned above.
        assert "source:manual" in content[resolved_idx:], "metadata carried to Resolved"
        assert content[:resolved_idx].count("source:manual") == 0, (
            "no orphaned metadata comment left in Open Threads"
        )
        # The other (emoji) thread stays open.
        assert "Other thread" in content[:resolved_idx]

    def test_returns_false_when_no_match(self, tmp_path):
        from jobs.handlers.memory_health import _resolve_open_thread
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Open Threads\n\n"
            "- 🟡 **Real thread** — open\n"
            "\n### Resolved (archive)\n"
        )
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            result = _resolve_open_thread("Nonexistent thread title")
        assert result is False

    def test_apply_report_logs_honest_not_matched(self, tmp_path):
        from jobs.handlers.memory_health import _apply_report
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Open Threads\n\n"
            "- 🟡 **Real thread** — open\n"
            "\n### Resolved (archive)\n"
        )
        report = {
            "stale_memories": [],
            "resolved_threads": [{"title": "Ghost thread", "evidence": "n/a"}],
            "archived_capabilities": [],
            "stale_decisions": [],
            "summary": "x",
        }
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            actions = _apply_report(report, memory.read_text(), "")
        assert any("not matched" in a.lower() for a in actions), (
            "unmatched thread must log a not-matched action, not a false success"
        )
        assert not any(a.startswith("Resolved thread:") for a in actions), (
            "must NOT log false success for an unmatched thread"
        )

    def test_emoji_thread_still_resolves(self, tmp_path):
        """Regression: the original emoji-case path must keep working."""
        from jobs.handlers.memory_health import _resolve_open_thread
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Open Threads\n\n"
            "- 🔵 **Signal fetcher service** — not built yet\n"
            "\n### Resolved (archive)\n"
            "- ✅ Old resolved item\n"
        )
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            result = _resolve_open_thread("Signal fetcher service")
        assert result is True
        content = memory.read_text()
        assert "✅" in content
        assert "auto-resolved" in content

    def test_scoping_prevents_false_positive_outside_open_threads(self, tmp_path):
        """Gate-2 BLOCKER#2 guard: a same-titled bold bullet OUTSIDE the
        Open Threads section must NOT be resolved — the match is scoped."""
        from jobs.handlers.memory_health import _resolve_open_thread
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Recent Context\n\n"
            "- **Widget refactor** — a decision note that merely mentions the title\n"
            "\n## Open Threads\n\n"
            "- 🟡 **Some other open thread** — still open\n"
            "\n### Resolved (archive)\n"
        )
        with patch("jobs.handlers.memory_health.CONTEXT_DIR", tmp_path):
            result = _resolve_open_thread("Widget refactor")
        # The bold bullet lives in ## Recent Context, NOT Open Threads → no match.
        assert result is False
        content = memory.read_text()
        # It must stay put in Recent Context, not be moved to Resolved.
        recent_idx = content.index("## Recent Context")
        ot_idx = content.index("## Open Threads")
        assert "Widget refactor" in content[recent_idx:ot_idx]


class TestRecallAccuracyCheck:
    """_check_recall_accuracy: type/section-anchored golden recall (not hardcoded keys).

    Regression guard for the stale-key bug: golden queries used to hardcode OLD-scheme
    entry keys (LL/KD/RC); the 7-type restructure (PRI/GUI/PIT...) made them false-miss
    (1/10). Fix anchors on section (derived from the MEMORY_PREFIX_TO_SECTION SSoT), so
    a future key rename does not re-break it, and separates the CJK content-coverage gaps
    into an informational bucket that does NOT count toward the pass threshold.
    """

    def _entry(self, key, summary, aliases=None):
        return {"key": key, "summary": summary, "aliases": aliases or []}

    def test_section_anchored_hit_passes(self):
        # A query whose top relevant hit lands in the expected SECTION passes,
        # regardless of the specific key number (proves not-key-hardcoded).
        from jobs.handlers.memory_health import _check_recall_accuracy
        entries = [
            self._entry("PRI11", "Memory sovereignty is a first principle — all memory self-owned",
                        ["sovereignty", "记忆主权"]),
            self._entry("PRI99", "some other principle unrelated"),
        ]
        # sovereignty query expects a Principles-section hit
        finding = _check_recall_accuracy(entries,
                                         queries=[("sovereignty", ["Principles"])],
                                         known_gaps=[])
        assert finding["status"] == "pass", finding

    def test_wrong_section_is_a_miss(self):
        # Teeth: a hit in the WRONG section must NOT count — preserves discriminative power
        # (this is what distinguishes section-anchoring from "any hit passes").
        from jobs.handlers.memory_health import _check_recall_accuracy
        entries = [
            self._entry("GUI11", "sovereignty appears here but this is a guideline entry",
                        ["sovereignty"]),
        ]
        finding = _check_recall_accuracy(entries,
                                         queries=[("sovereignty", ["Principles"])],
                                         known_gaps=[])
        assert finding["status"] == "fail", finding

    def test_red_on_scheme_change_is_avoided(self):
        # The KEY number is irrelevant — only the section (prefix→section via SSoT) matters.
        # Rename PRI11→PRI42: still passes. (The OLD by-key check would break here.)
        from jobs.handlers.memory_health import _check_recall_accuracy
        entries = [self._entry("PRI42", "Memory sovereignty principle", ["sovereignty"])]
        finding = _check_recall_accuracy(entries,
                                         queries=[("sovereignty", ["Principles"])],
                                         known_gaps=[])
        assert finding["status"] == "pass", finding

    def test_known_gap_not_counted_in_threshold(self):
        # A CJK content-coverage gap (0 hits) must NOT fail the check — it is informational.
        from jobs.handlers.memory_health import _check_recall_accuracy
        entries = [self._entry("PRI11", "Memory sovereignty principle", ["sovereignty"])]
        finding = _check_recall_accuracy(
            entries,
            queries=[("sovereignty", ["Principles"])],   # 1/1 counted → pass
            known_gaps=["越用越聪明"],                     # 0 hits, but informational only
        )
        assert finding["status"] == "pass", finding
        # the gap is reported, not silently swallowed
        assert "越用越聪明" in finding["detail"] or "known_gap" in finding.get("detail", "")

    def test_known_gap_promotion_signal(self):
        # If a "known gap" query STARTS hitting (content added), flip to warn — signal to
        # promote it into the counted set (Gate-1 fix #4: don't mask a future improvement).
        # NOTE: promotion is only surfaced when the counted set OTHERWISE PASSES (Gate-2 MED).
        from jobs.handlers.memory_health import _check_recall_accuracy
        entries = [
            self._entry("PRI11", "sovereignty 越用越聪明 the system gets smarter",
                        ["sovereignty", "越用越聪明"]),
        ]
        finding = _check_recall_accuracy(
            entries,
            queries=[("sovereignty", ["Principles"])],   # counted set passes 1/1
            known_gaps=["越用越聪明"],                     # now hits → promotion signal
        )
        assert finding["status"] == "warn", finding

    def test_regression_not_masked_by_gap_promotion(self):
        # Gate-2 MED (run_c77b084d): a REAL counted miss must NEVER be downgraded to
        # `warn` just because an unrelated known-gap started hitting. run_memory_health
        # surfaces only status=='fail' as an integrity failure — a masked fail is invisible.
        from jobs.handlers.memory_health import _check_recall_accuracy
        entries = [
            self._entry("GUI11", "sovereignty in the WRONG section 越用越聪明",
                        ["sovereignty", "越用越聪明"]),
        ]
        finding = _check_recall_accuracy(
            entries,
            queries=[("sovereignty", ["Principles"])],   # top hit is GUI (Guidelines) → MISS
            known_gaps=["越用越聪明"],                     # hits, but must NOT mask the miss
        )
        assert finding["status"] == "fail", finding

    def test_empty_counted_set_is_not_vacuous_pass(self):
        # Gate-2 LOW: an empty counted query set must NOT report `pass` (unprobed engine).
        from jobs.handlers.memory_health import _check_recall_accuracy
        finding = _check_recall_accuracy([], queries=[], known_gaps=[])
        assert finding["status"] == "fail", finding

    def test_no_old_scheme_keys_in_query_set(self):
        # AC1/AC4: the production golden set must not reference retired LL/KD/RC keys,
        # and must not include a query with no MEMORY content.
        from jobs.handlers.memory_health import _RECALL_QUERIES, _RECALL_KNOWN_GAPS
        all_q = [q for q, _ in _RECALL_QUERIES] + list(_RECALL_KNOWN_GAPS)
        # No expected value may be an old-scheme KEY (they are section names now)
        for q, expected in _RECALL_QUERIES:
            for e in expected:
                assert not __import__("re").match(r"^(LL|KD|RC)\d+$", e), \
                    f"{q}: expected {e!r} is an old-scheme key, must be a section name"
        assert "竞品分析的结论是什么" not in all_q, "query with no MEMORY content must be removed"

    def test_production_golden_set_passes_on_real_memory(self):
        # AC5: the real MEMORY.md must pass the recall check (engine is healthy).
        from pathlib import Path
        from jobs.handlers.memory_health import _check_recall_accuracy, _RECALL_QUERIES, _RECALL_KNOWN_GAPS
        from core.memory_index import _parse_index_entries
        mem = Path.home() / ".swarm-ai/SwarmWS/.context/MEMORY.md"
        if not mem.exists():
            import pytest
            pytest.skip("real MEMORY.md not present")
        entries = _parse_index_entries(mem.read_text())
        finding = _check_recall_accuracy(entries, queries=_RECALL_QUERIES,
                                         known_gaps=_RECALL_KNOWN_GAPS)
        assert finding["status"] == "pass", finding
