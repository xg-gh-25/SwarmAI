"""Tests for proactive_intelligence module.

Validates Open Threads parsing, continue-from extraction, pattern detection,
and briefing assembly from synthetic MEMORY.md and DailyActivity data.
"""

import pytest
from pathlib import Path

# Direct import — avoids core/__init__.py which pulls in claude_agent_sdk
import importlib.util

import sys

_spec = importlib.util.spec_from_file_location(
    "proactive_intelligence",
    str(Path(__file__).resolve().parent.parent / "core" / "proactive_intelligence.py"),
)
_mod = importlib.util.module_from_spec(_spec)
# Register module so @dataclass can resolve __module__
sys.modules["proactive_intelligence"] = _mod
_spec.loader.exec_module(_mod)

build_session_briefing = _mod.build_session_briefing
_parse_open_threads = _mod._parse_open_threads
_parse_continue_hints = _mod._parse_continue_hints
_detect_patterns = _mod._detect_patterns
_build_suggestions = _mod._build_suggestions
_score_item = _mod._score_item
_detect_blocking = _mod._detect_blocking
_generate_reasoning = _mod._generate_reasoning
_format_suggestions = _mod._format_suggestions
ScoredItem = _mod.ScoredItem


# ── Fixtures ──

SAMPLE_MEMORY = """
## Open Threads

### P0 — Blocking
- 🔴 **Tab switching loses streaming content** (reported 4x: 3/13, 3/14)
  Status: diagnosed multiple times, partial fixes applied, not durably resolved.

### P1 — Important
- 🟡 **MCP servers not connecting in app** (reported 2x: 3/12, 3/13)
  Status: lib.rs PATH fix made. Needs rebuild & verify.
- 🟡 **Streaming feels non-streaming** (reported 1x: 3/14)
  Status: SDK set. Needs rebuild & verify.

### P2 — Nice to have
- 🔵 **Swarm Radar v2 redesign** — mockup approved, not started.

## COE Registry

- 2026-03-14: **tab switching** — Investigating.
- 2026-03-13: **streaming loss** — Investigating.

## Recent Context

- 2026-03-14: Birthday materials need git commit.
"""


class TestParseOpenThreads:
    def test_parses_all_priorities(self):
        threads = _parse_open_threads(SAMPLE_MEMORY)
        priorities = {t["priority"] for t in threads}
        assert "P0" in priorities
        assert "P1" in priorities
        assert "P2" in priorities

    def test_report_count_extracted(self):
        threads = _parse_open_threads(SAMPLE_MEMORY)
        p0 = [t for t in threads if t["priority"] == "P0"]
        assert len(p0) == 1
        assert p0[0]["report_count"] == 4

    def test_title_extracted(self):
        threads = _parse_open_threads(SAMPLE_MEMORY)
        titles = [t["title"] for t in threads]
        assert "Tab switching loses streaming content" in titles

    def test_status_extracted(self):
        threads = _parse_open_threads(SAMPLE_MEMORY)
        p0 = [t for t in threads if t["priority"] == "P0"][0]
        assert "diagnosed" in p0.get("status", "")

    def test_empty_memory(self):
        assert _parse_open_threads("") == []
        assert _parse_open_threads("## Some Other Section\nstuff") == []

    def test_p1_count(self):
        threads = _parse_open_threads(SAMPLE_MEMORY)
        p1 = [t for t in threads if t["priority"] == "P1"]
        assert len(p1) == 2

    def test_p2_simple_bullet(self):
        threads = _parse_open_threads(SAMPLE_MEMORY)
        p2 = [t for t in threads if t["priority"] == "P2"]
        assert len(p2) >= 1
        assert "Swarm Radar" in p2[0]["title"]


class TestContinueHints:
    def test_extracts_next_lines(self, tmp_path):
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        (da_dir / "2026-03-14.md").write_text(
            "## 10:00 | abc123 | Session\n"
            "**Next:** Implement feature X.\n"
            "**Next:** Ongoing: some stale thing\n"
        )
        hints = _parse_continue_hints(da_dir)
        assert "Implement feature X." in hints
        # Ongoing hints should be filtered
        assert not any("Ongoing:" in h for h in hints)

    def test_deduplicates(self, tmp_path):
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        (da_dir / "2026-03-14.md").write_text(
            "**Next:** Do thing A.\n**Next:** Do thing A.\n"
        )
        hints = _parse_continue_hints(da_dir)
        assert hints.count("Do thing A.") == 1

    def test_empty_dir(self, tmp_path):
        assert _parse_continue_hints(tmp_path / "nonexistent") == []

    def test_max_files(self, tmp_path):
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        for i in range(5):
            (da_dir / f"2026-03-{10+i:02d}.md").write_text(f"**Next:** Task {i}.\n")
        hints = _parse_continue_hints(da_dir, max_files=2)
        # Should only read 2 most recent files (03-14, 03-13)
        assert len(hints) <= 2


class TestPatternDetection:
    def test_repeat_offender(self):
        threads = [{"title": "Bug X", "priority": "P0", "report_count": 4}]
        signals = _detect_patterns(threads, Path("/tmp"), SAMPLE_MEMORY)
        assert any("4x" in s for s in signals)

    def test_pending_rebuild(self):
        threads = [
            {"title": "Fix A", "priority": "P1", "report_count": 1, "status": "Needs rebuild & verify"},
            {"title": "Fix B", "priority": "P1", "report_count": 1, "status": "Needs rebuild & verify"},
        ]
        signals = _detect_patterns(threads, Path("/tmp"), SAMPLE_MEMORY)
        assert any("2 fix(es) pending rebuild" in s for s in signals)

    def test_coe_detection(self):
        signals = _detect_patterns([], Path("/tmp"), SAMPLE_MEMORY)
        assert any("COE" in s for s in signals)

    def test_uncommitted_work(self):
        signals = _detect_patterns([], Path("/tmp"), SAMPLE_MEMORY)
        assert any("Uncommitted" in s or "commit" in s.lower() for s in signals)


_detect_temporal_signals = _mod._detect_temporal_signals


class TestTemporalSignals:
    def test_session_gap_detected(self, tmp_path):
        """Gap of 3+ days should surface a signal."""
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        # Only file is 3 days ago
        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        (da_dir / f"{old_date}.md").write_text("**Next:** Something.\n")

        signals = _detect_temporal_signals([], da_dir)
        assert any("days since last session" in s for s in signals)

    def test_no_gap_for_today(self, tmp_path):
        """No gap signal when today's file exists."""
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        (da_dir / f"{today}.md").write_text("**Next:** Current work.\n")

        signals = _detect_temporal_signals([], da_dir)
        assert not any("days since last session" in s for s in signals)

    def test_first_session_of_day(self, tmp_path):
        """Surfaces 'first session today' when today's file doesn't exist."""
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        (da_dir / f"{yesterday}.md").write_text("stuff\n")

        signals = _detect_temporal_signals([], da_dir)
        assert any("First session today" in s for s in signals)

    def test_stale_p0_detected(self, tmp_path):
        """P0 with date reference >3 days ago should flag as stale."""
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        threads = [{
            "priority": "P0",
            "title": "Big bug",
            "report_count": 2,
            "status": "reported 3/10, still open"
        }]
        # Only triggers if 3/10 is >3 days from now
        from datetime import datetime
        ref_date = datetime(datetime.now().year, 3, 10)
        if (datetime.now() - ref_date).days >= 3:
            signals = _detect_temporal_signals(threads, da_dir)
            assert any("open" in s and "days" in s for s in signals)

    def test_p1_not_flagged_stale(self, tmp_path):
        """Staleness check only applies to P0, not P1."""
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        threads = [{
            "priority": "P1",
            "title": "Minor issue",
            "report_count": 1,
            "status": "from 3/01"
        }]
        signals = _detect_temporal_signals(threads, da_dir)
        assert not any("escalating" in s for s in signals)

    def test_empty_dir(self, tmp_path):
        """No crash on missing directory."""
        signals = _detect_temporal_signals([], tmp_path / "nonexistent")
        assert signals == []


class TestScoring:
    def test_p0_beats_p1(self):
        p0 = ScoredItem(title="A", priority="P0")
        p1 = ScoredItem(title="B", priority="P1")
        assert _score_item(p0) > _score_item(p1)

    def test_p1_beats_p2(self):
        p1 = ScoredItem(title="A", priority="P1")
        p2 = ScoredItem(title="B", priority="P2")
        assert _score_item(p1) > _score_item(p2)

    def test_staleness_adds_score(self):
        fresh = ScoredItem(title="A", priority="P1", days_open=0)
        stale = ScoredItem(title="B", priority="P1", days_open=4)
        assert _score_item(stale) > _score_item(fresh)

    def test_staleness_capped(self):
        very_stale = ScoredItem(title="A", priority="P1", days_open=100)
        assert _score_item(very_stale) == 40 + 30  # P1(40) + cap(30)

    def test_frequency_adds_score(self):
        once = ScoredItem(title="A", priority="P1", report_count=1)
        many = ScoredItem(title="B", priority="P1", report_count=4)
        assert _score_item(many) > _score_item(once)

    def test_frequency_capped(self):
        extreme = ScoredItem(title="A", priority="P1", report_count=20)
        # P1(40) + freq_cap(40) = 80
        assert _score_item(extreme) == 80

    def test_blocking_bonus(self):
        normal = ScoredItem(title="A", priority="P1")
        blocker = ScoredItem(title="B", priority="P1", blocks_others=True)
        assert _score_item(blocker) - _score_item(normal) == 30

    def test_momentum_bonus(self):
        cold = ScoredItem(title="A", priority="P1")
        warm = ScoredItem(title="B", priority="P1", from_continue_hint=True)
        assert _score_item(warm) - _score_item(cold) == 15

    def test_combined_score(self):
        """P0 + 4 reports + 2 days + no blocking + no momentum."""
        item = ScoredItem(
            title="Tab bug", priority="P0",
            report_count=4, days_open=2,
        )
        # P0(100) + stale(10) + freq(24) = 134
        assert _score_item(item) == 134


class TestBlocking:
    def test_blocking_keyword_detected(self):
        threads = [
            {"title": "Fix A", "priority": "P1", "status": "blocking other work", "report_count": 1},
        ]
        blocking_map, counts = _detect_blocking(threads)
        assert blocking_map.get("Fix A") is True

    def test_rebuild_blocking(self):
        threads = [
            {"title": "Rebuild needed", "priority": "P1", "status": "pending", "report_count": 1},
            {"title": "Fix X", "priority": "P1", "status": "Needs rebuild & verify", "report_count": 1},
            {"title": "Fix Y", "priority": "P1", "status": "Needs rebuild & verify", "report_count": 1},
        ]
        blocking_map, counts = _detect_blocking(threads)
        assert blocking_map.get("Rebuild needed") is True

    def test_no_false_positives(self):
        threads = [
            {"title": "Normal bug", "priority": "P1", "status": "investigating", "report_count": 1},
        ]
        blocking_map, _ = _detect_blocking(threads)
        assert not blocking_map.get("Normal bug")


class TestBuildSuggestions:
    def test_threads_converted_and_ranked(self):
        threads = [
            {"title": "P2 thing", "priority": "P2", "report_count": 1},
            {"title": "P0 bug", "priority": "P0", "report_count": 3},
            {"title": "P1 fix", "priority": "P1", "report_count": 2},
        ]
        ranked = _build_suggestions(threads, [], [])
        assert ranked[0].title == "P0 bug"
        assert ranked[0].score > ranked[1].score

    def test_continue_hint_gets_momentum(self):
        threads = [
            {"title": "Task A", "priority": "P1", "report_count": 1},
        ]
        hints = ["Task A next step"]  # won't match title[:30] exactly
        ranked = _build_suggestions(threads, ["Continue Task A work"], [])
        # The hint should be added as separate item if no match
        assert len(ranked) >= 1

    def test_hint_not_duplicated_with_thread(self):
        threads = [
            {"title": "MCP servers not connecting", "priority": "P1", "report_count": 2},
        ]
        hints = ["MCP servers not connecting — investigate root cause"]
        ranked = _build_suggestions(threads, hints, [])
        mcp_items = [r for r in ranked if "MCP" in r.title]
        assert len(mcp_items) == 1  # not duplicated

    def test_empty_input(self):
        assert _build_suggestions([], [], []) == []

    def test_tiebreak_is_deterministic(self):
        threads = [
            {"title": "Bug B", "priority": "P1", "report_count": 1},
            {"title": "Bug A", "priority": "P1", "report_count": 1},
        ]
        ranked = _build_suggestions(threads, [], [])
        # Same score, same priority — alphabetical tiebreak
        assert ranked[0].title == "Bug A"
        assert ranked[1].title == "Bug B"


class TestReasoning:
    def test_generates_reason_for_repeat_bug(self):
        items = [ScoredItem(title="Tab bug", priority="P0", report_count=4, score=134)]
        reasoning = _generate_reasoning(items)
        assert "4x" in reasoning

    def test_generates_reason_for_blocker(self):
        items = [ScoredItem(title="Build", priority="P1", blocks_others=True, blocked_count=3, score=70)]
        reasoning = _generate_reasoning(items)
        assert "blocks" in reasoning

    def test_generates_reason_for_stale(self):
        items = [ScoredItem(title="Old bug", priority="P0", days_open=5, score=100)]
        reasoning = _generate_reasoning(items)
        assert "5 days" in reasoning

    def test_empty_on_no_interesting_items(self):
        items = [ScoredItem(title="New thing", priority="P2", score=10)]
        reasoning = _generate_reasoning(items)
        assert reasoning == ""


class TestFormatSuggestions:
    def test_top_3_shown(self):
        items = [
            ScoredItem(title=f"Item {i}", priority="P1", score=100 - i * 10)
            for i in range(5)
        ]
        focus, bg = _format_suggestions(items)
        assert "1." in focus
        assert "2." in focus
        assert "3." in focus
        assert "Item 3" in bg  # 4th item in background
        assert "Item 4" in bg

    def test_dominant_item_shows_fewer(self):
        items = [
            ScoredItem(title="Dominant", priority="P0", score=150),
            ScoredItem(title="Weak", priority="P2", score=10),
        ]
        focus, bg = _format_suggestions(items)
        assert "Dominant" in focus
        # With >30 gap, should show max 2 in focus
        assert "Weak" in focus or "Weak" in bg

    def test_includes_reasoning(self):
        items = [ScoredItem(title="Bug X", priority="P0", report_count=4, score=134)]
        focus, _ = _format_suggestions(items)
        assert "Why this order" in focus

    def test_empty_returns_empty(self):
        focus, bg = _format_suggestions([])
        assert focus == ""
        assert bg == ""


class TestBuildSessionBriefing:
    def test_full_briefing_with_real_workspace(self):
        workspace = Path("/Users/gawan/.swarm-ai/SwarmWS")
        if not workspace.exists():
            pytest.skip("Real workspace not available")
        briefing = build_session_briefing(workspace)
        assert briefing is not None
        assert "## Session Briefing" in briefing
        assert "Suggested focus" in briefing

    def test_returns_none_for_empty_workspace(self, tmp_path):
        result = build_session_briefing(tmp_path)
        assert result is None

    def test_briefing_under_token_budget(self):
        workspace = Path("/Users/gawan/.swarm-ai/SwarmWS")
        if not workspace.exists():
            pytest.skip("Real workspace not available")
        briefing = build_session_briefing(workspace)
        if briefing:
            tokens = len(briefing) // 4
            assert tokens < 500, f"Briefing too large: {tokens} tokens"

    def test_never_raises(self, tmp_path):
        context_dir = tmp_path / ".context"
        context_dir.mkdir()
        (context_dir / "MEMORY.md").write_text("garbage \x00\x01\x02 data")
        result = build_session_briefing(tmp_path)
        assert result is None or isinstance(result, str)

    def test_l2_format_with_synthetic_data(self, tmp_path):
        """Full pipeline test with controlled data."""
        context_dir = tmp_path / ".context"
        context_dir.mkdir()
        da_dir = tmp_path / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True)

        (context_dir / "MEMORY.md").write_text(SAMPLE_MEMORY)
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        (da_dir / f"{today}.md").write_text(
            "## 10:00 | abc | Session\n"
            "**Next:** Investigate MCP root cause.\n"
        )

        briefing = build_session_briefing(tmp_path)
        assert briefing is not None
        assert "Suggested focus" in briefing
        assert "Tab switching" in briefing  # P0 should be top
