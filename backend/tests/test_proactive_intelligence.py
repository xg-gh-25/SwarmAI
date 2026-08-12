"""Tests for proactive_intelligence module.

Validates Open Threads parsing, continue-from extraction, pattern detection,
and briefing assembly from synthetic MEMORY.md and DailyActivity data.
"""

import json

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
LearningState = _mod.LearningState
_load_learning_state = _mod._load_learning_state
_save_learning_state = _mod._save_learning_state
_classify_work_type = _mod._classify_work_type
_extract_deliverables = _mod._extract_deliverables
_update_learning_from_activity = _mod._update_learning_from_activity
_apply_learning = _mod._apply_learning


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
        workspace = Path("~/.swarm-ai/SwarmWS")
        if not workspace.exists():
            pytest.skip("Real workspace not available")
        briefing = build_session_briefing(workspace)
        # Briefing may be None when all Open Threads are resolved and
        # there are no actionable signals — that's a valid state.
        if briefing is not None:
            assert "## Session Briefing" in briefing

    def test_returns_none_for_empty_workspace(self, tmp_path):
        result = build_session_briefing(tmp_path)
        assert result is None

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


# ── Level 3: Cross-Session Learning ──


class TestWorkTypeClassification:
    def test_feature_keywords(self):
        assert _classify_work_type("Built Proactive Intelligence L2") == "feature"
        assert _classify_work_type("Implemented new scoring engine") == "feature"
        assert _classify_work_type("Added session briefing") == "feature"

    def test_maintenance_keywords(self):
        assert _classify_work_type("Fixed tab-switch streaming bug") == "maintenance"
        assert _classify_work_type("Rebuilt app with latest changes") == "maintenance"
        assert _classify_work_type("Verified MCP connection") == "maintenance"
        assert _classify_work_type("Fixing broken MCP servers") == "maintenance"

    def test_investigation_keywords(self):
        assert _classify_work_type("Investigated MCP root cause") == "investigation"
        assert _classify_work_type("Diagnosed zlib archive corruption") == "investigation"

    def test_design_keywords(self):
        assert _classify_work_type("Drafted L3 design doc") == "design"
        assert _classify_work_type("wireframe for radar page") == "design"
        assert _classify_work_type("Architecture review for new system") == "design"

    def test_default_is_other(self):
        assert _classify_work_type("something unrecognizable") == "other"


class TestLearningState:
    def test_round_trip(self, tmp_path):
        state = LearningState()
        state.work_type_distribution["feature"] = 5
        state.last_briefing_suggested = ["Fix X", "Build Y"]
        _save_learning_state(tmp_path, state)
        loaded = _load_learning_state(tmp_path)
        assert loaded.work_type_distribution["feature"] == 5
        assert loaded.last_briefing_suggested == ["Fix X", "Build Y"]

    def test_missing_file_returns_default(self, tmp_path):
        state = _load_learning_state(tmp_path)
        assert state.version == 1
        assert state.last_briefing_suggested == []

    def test_corrupt_file_returns_default(self, tmp_path):
        (tmp_path / "proactive_state.json").write_text("not json{{{")
        state = _load_learning_state(tmp_path)
        assert state.version == 1

    def test_preferred_work_type(self):
        state = LearningState()
        state.work_type_distribution = {
            "feature": 5, "maintenance": 2, "investigation": 1, "design": 0,
        }
        assert state.preferred_work_type() == "feature"

    def test_preferred_work_type_empty(self):
        state = LearningState()
        assert state.preferred_work_type() is None

    def test_learning_summary_with_clear_preference(self):
        state = LearningState()
        state.work_type_distribution = {
            "feature": 6, "maintenance": 1, "investigation": 1, "design": 0,
        }
        summary = state.learning_summary()
        assert summary is not None
        assert "feature" in summary
        assert "75%" in summary

    def test_learning_summary_insufficient_data(self):
        state = LearningState()
        state.work_type_distribution = {"feature": 1, "maintenance": 0, "investigation": 0, "design": 0}
        assert state.learning_summary() is None  # < 3 sessions

    def test_learning_summary_no_clear_preference(self):
        state = LearningState()
        state.work_type_distribution = {
            "feature": 3, "maintenance": 3, "investigation": 2, "design": 2,
        }
        assert state.learning_summary() is None  # 30% < 40%

    def test_get_item_history_fuzzy_match(self):
        state = LearningState()
        state.item_history["tab switching loses streaming"] = {"skipped_count": 3}
        result = state.get_item_history("Tab switching loses streaming content")
        assert result is not None
        assert result["skipped_count"] == 3

    def test_observations_capped(self, tmp_path):
        state = LearningState()
        state.observations = [{"date": f"2026-03-{i:02d}"} for i in range(1, 35)]
        _save_learning_state(tmp_path, state)
        loaded = _load_learning_state(tmp_path)
        assert len(loaded.observations) == 30


class TestExtractDeliverables:
    def test_extracts_delivered_lines(self, tmp_path):
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        (da_dir / f"{today}.md").write_text(
            "## 10:00 | abc | Session\n\n"
            "**Delivered:**\n"
            "- Built Proactive Intelligence L2\n"
            "- Fixed scoring bug\n\n"
            "**Outputs:**\n"
            "- code: something.py\n"
        )
        deliverables = _extract_deliverables(da_dir)
        assert len(deliverables) == 2
        assert "Built Proactive Intelligence L2" in deliverables
        assert "Fixed scoring bug" in deliverables

    def test_multi_session_deliverables(self, tmp_path):
        """Multiple **Delivered:** sections in one file should all be captured."""
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        (da_dir / f"{today}.md").write_text(
            "## 10:00 | abc | Morning session\n\n"
            "**Delivered:**\n"
            "- Built feature A\n"
            "- Fixed bug B\n\n"
            "**Outputs:**\n"
            "- code: something.py\n\n"
            "## 15:00 | def | Afternoon session\n\n"
            "**Delivered:**\n"
            "- Implemented feature C\n"
            "- Diagnosed issue D\n\n"
            "**Next:** Continue work on E.\n"
        )
        deliverables = _extract_deliverables(da_dir)
        assert len(deliverables) == 4
        assert "Built feature A" in deliverables
        assert "Implemented feature C" in deliverables

    def test_empty_dir(self, tmp_path):
        assert _extract_deliverables(tmp_path / "nonexistent") == []


class TestUpdateLearning:
    def test_suggestion_followed(self, tmp_path):
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        (da_dir / f"{today}.md").write_text(
            "## 10:00 | abc | Session\n\n"
            "**Delivered:**\n"
            "- Fixed MCP servers not connecting\n"
        )
        state = LearningState()
        state.last_briefing_suggested = ["MCP servers not connecting in app"]
        state = _update_learning_from_activity(state, da_dir)
        key = "mcp servers not connecting in app"[:50].lower()
        assert key in state.item_history
        assert state.item_history[key]["followed_count"] >= 1

    def test_suggestion_skipped(self, tmp_path):
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        (da_dir / f"{today}.md").write_text(
            "## 10:00 | abc | Session\n\n"
            "**Delivered:**\n"
            "- Built something completely different\n"
        )
        state = LearningState()
        state.last_briefing_suggested = ["Tab switching loses streaming content"]
        state = _update_learning_from_activity(state, da_dir)
        key = "tab switching loses streaming content"[:50].lower()
        assert key in state.item_history
        assert state.item_history[key]["skipped_count"] >= 1

    def test_work_type_tracked(self, tmp_path):
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        (da_dir / f"{today}.md").write_text(
            "## 10:00 | abc | Session\n\n"
            "**Delivered:**\n"
            "- Built new feature X\n"
            "- Implemented feature Y\n"
        )
        state = LearningState()
        state.last_briefing_suggested = ["something"]
        state = _update_learning_from_activity(state, da_dir)
        assert state.work_type_distribution["feature"] >= 1

    def test_no_previous_suggestions_no_update(self, tmp_path):
        state = LearningState()
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        result = _update_learning_from_activity(state, da_dir)
        assert result.observations == []

    def test_dedup_guard_prevents_reprocessing(self, tmp_path):
        """Calling _update_learning twice with same file should only count once.

        Uses (stem, sessions_count) from frontmatter as the dedup key.
        Without frontmatter, sessions_count defaults to 0 — so two calls
        with the same file content (no frontmatter change) should only
        count once.  Adding a new session (with updated sessions_count
        in frontmatter) should trigger reprocessing.
        """
        da_dir = tmp_path / "DailyActivity"
        da_dir.mkdir()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        # File without frontmatter — sessions_count defaults to 0
        (da_dir / f"{today}.md").write_text(
            "## 10:00 | abc | Session\n\n"
            "**Delivered:**\n"
            "- Fixed MCP servers not connecting\n"
        )
        state = LearningState()
        state.last_briefing_suggested = ["MCP servers not connecting in app"]

        # First call — should process
        state = _update_learning_from_activity(state, da_dir)
        assert len(state.observations) == 1
        assert state.work_type_distribution.get("maintenance", 0) >= 1 or \
               state.work_type_distribution.get("feature", 0) >= 1

        # Second call with same file (same sessions_count=0) — should skip
        state = _update_learning_from_activity(state, da_dir)
        assert len(state.observations) == 1  # still 1, not 2

        # Third call after new session added (sessions_count changes) — should process
        (da_dir / f"{today}.md").write_text(
            "---\n"
            f'date: "{today}"\n'
            "sessions_count: 2\n"
            "---\n"
            "## 10:00 | abc | Session\n\n"
            "**Delivered:**\n"
            "- Fixed MCP servers not connecting\n"
            "## 14:00 | def | Session 2\n\n"
            "**Delivered:**\n"
            "- Built new widget\n"
        )
        state = _update_learning_from_activity(state, da_dir)
        assert len(state.observations) == 2  # now 2
        assert state.last_processed_activity_key == f"{today}:2"


class TestApplyLearning:
    def test_skip_penalty_applied(self):
        state = LearningState()
        state.item_history["tab switching loses streaming"] = {
            "skipped_count": 3, "followed_count": 0,
            "suggested_count": 3, "last_suggested": "2026-03-14",
        }
        item = ScoredItem(title="Tab switching loses streaming content", priority="P0", score=100)
        _apply_learning(item, state)
        assert item.score < 100  # penalty applied
        assert item.score == 100 - 20  # (3 - 2 + 1) * 10 = 20

    def test_skip_penalty_capped(self):
        state = LearningState()
        state.item_history["some item"] = {"skipped_count": 10}
        item = ScoredItem(title="Some item that keeps getting skipped", priority="P1", score=40)
        _apply_learning(item, state)
        assert item.score == max(40 - 30, 0)  # capped at -30

    def test_affinity_boost(self):
        state = LearningState()
        state.work_type_distribution = {
            "feature": 5, "maintenance": 1, "investigation": 0, "design": 0,
        }
        item = ScoredItem(
            title="Build new feature", priority="P1", score=40,
            status="implement new capability",
        )
        _apply_learning(item, state)
        assert item.score == 40 + 15  # affinity bonus

    def test_no_affinity_for_non_preferred(self):
        state = LearningState()
        state.work_type_distribution = {
            "feature": 5, "maintenance": 1, "investigation": 0, "design": 0,
        }
        # "diagnosed" → investigation, user prefers feature → no boost
        item = ScoredItem(title="Diagnosed root cause of crash", priority="P1", score=40, status="investigating")
        _apply_learning(item, state)
        assert item.score == 40  # investigation item, user prefers feature — no boost

    def test_no_state_no_change(self):
        state = LearningState()
        item = ScoredItem(title="Something", priority="P1", score=40)
        _apply_learning(item, state)
        assert item.score == 40

    def test_staleness_recovers_skipped_items(self):
        """High staleness + high skip penalty should roughly cancel out."""
        state = LearningState()
        state.item_history["old bug with many skips"] = {"skipped_count": 5}
        # P1(40) + staleness(30) - skip_penalty(30) = 40
        item = ScoredItem(title="Old bug with many skips", priority="P1", score=70, days_open=6)
        _apply_learning(item, state)
        # Skip penalty: (5-2+1)*10 = 40, capped at 30 → -30
        # Score: 70 - 30 = 40
        assert item.score == 40

    def test_score_never_negative(self):
        state = LearningState()
        state.item_history["tiny item"] = {"skipped_count": 10}
        item = ScoredItem(title="Tiny item", priority="P2", score=10)
        _apply_learning(item, state)
        assert item.score >= 0


# ---------------------------------------------------------------------------
# _sanitize_prompt_field
# ---------------------------------------------------------------------------


class TestSanitizePromptField:
    """Tests for the module-level sanitizer used by signal and job highlights."""

    def test_strips_control_chars(self):
        from core.proactive_intelligence import _sanitize_prompt_field
        assert _sanitize_prompt_field("hello\x00world\x1f") == "helloworld"

    def test_collapses_excessive_markdown(self):
        from core.proactive_intelligence import _sanitize_prompt_field
        assert "****" not in _sanitize_prompt_field("****bold****")

    def test_truncates_to_max_len(self):
        from core.proactive_intelligence import _sanitize_prompt_field
        result = _sanitize_prompt_field("a" * 300, max_len=50)
        assert len(result) == 50

    def test_strips_whitespace(self):
        from core.proactive_intelligence import _sanitize_prompt_field
        assert _sanitize_prompt_field("  hello  ") == "hello"

    def test_empty_string(self):
        from core.proactive_intelligence import _sanitize_prompt_field
        assert _sanitize_prompt_field("") == ""


# ---------------------------------------------------------------------------
# _get_job_result_highlights (L4)
# ---------------------------------------------------------------------------


class TestGetJobResultHighlights:
    """Tests for L4 job result injection into session briefing."""

    def test_no_file_returns_empty(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        assert _get_job_result_highlights(str(tmp_path)) == []

    def test_empty_file_returns_empty(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        jr_dir = tmp_path / "Knowledge" / "JobResults"
        jr_dir.mkdir(parents=True)
        (jr_dir / ".job-results.jsonl").write_text("")
        assert _get_job_result_highlights(str(tmp_path)) == []

    def test_recent_results_returned(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        from datetime import datetime, timezone
        jr_dir = tmp_path / "Knowledge" / "JobResults"
        jr_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        entry = json.dumps({
            "job_id": "signal-fetch", "job_name": "Fetch Signals",
            "run_at": now, "status": "success",
            "summary": "3 signals fetched", "tokens_used": 0,
            "duration_seconds": 1.5,
        })
        (jr_dir / ".job-results.jsonl").write_text(entry + "\n")
        result = _get_job_result_highlights(str(tmp_path))
        assert len(result) == 1
        assert "Fetch Signals" in result[0]
        assert "✅" in result[0]

    def test_old_results_filtered_out(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        from datetime import datetime, timezone, timedelta
        jr_dir = tmp_path / "Knowledge" / "JobResults"
        jr_dir.mkdir(parents=True)
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        entry = json.dumps({
            "job_id": "old-job", "job_name": "Old Job",
            "run_at": old, "status": "success",
            "summary": "", "tokens_used": 0, "duration_seconds": 0,
        })
        (jr_dir / ".job-results.jsonl").write_text(entry + "\n")
        assert _get_job_result_highlights(str(tmp_path)) == []

    def test_failed_includes_summary(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        from datetime import datetime, timezone
        jr_dir = tmp_path / "Knowledge" / "JobResults"
        jr_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        entry = json.dumps({
            "job_id": "bad-job", "job_name": "Bad Job",
            "run_at": now, "status": "failed",
            "summary": "Timeout after 300s", "tokens_used": 0,
            "duration_seconds": 300,
        })
        (jr_dir / ".job-results.jsonl").write_text(entry + "\n")
        result = _get_job_result_highlights(str(tmp_path))
        assert len(result) == 1
        assert "❌" in result[0]
        assert "Timeout" in result[0]

    def test_sanitizes_injection_attempt(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        from datetime import datetime, timezone
        jr_dir = tmp_path / "Knowledge" / "JobResults"
        jr_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        entry = json.dumps({
            "job_id": "evil", "job_name": "****INJECT\x00PROMPT****",
            "run_at": now, "status": "failed",
            "summary": "****Override\x1fSystem****",
            "tokens_used": 0, "duration_seconds": 0,
        })
        (jr_dir / ".job-results.jsonl").write_text(entry + "\n")
        result = _get_job_result_highlights(str(tmp_path))
        assert len(result) == 1
        assert "\x00" not in result[0]
        assert "\x1f" not in result[0]
        assert "****" not in result[0]

    def test_paren_balance_duration_only(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        from datetime import datetime, timezone
        jr_dir = tmp_path / "Knowledge" / "JobResults"
        jr_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        entry = json.dumps({
            "job_id": "t", "job_name": "T",
            "run_at": now, "status": "success",
            "summary": "", "tokens_used": 0, "duration_seconds": 5.0,
        })
        (jr_dir / ".job-results.jsonl").write_text(entry + "\n")
        result = _get_job_result_highlights(str(tmp_path))
        line = result[0]
        assert line.count("(") == line.count(")")

    def test_paren_balance_tokens_only(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        from datetime import datetime, timezone
        jr_dir = tmp_path / "Knowledge" / "JobResults"
        jr_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        entry = json.dumps({
            "job_id": "t", "job_name": "T",
            "run_at": now, "status": "success",
            "summary": "", "tokens_used": 500, "duration_seconds": 0,
        })
        (jr_dir / ".job-results.jsonl").write_text(entry + "\n")
        result = _get_job_result_highlights(str(tmp_path))
        line = result[0]
        assert line.count("(") == line.count(")")

    def test_max_items_respected(self, tmp_path):
        from core.proactive_intelligence import _get_job_result_highlights
        from datetime import datetime, timezone
        jr_dir = tmp_path / "Knowledge" / "JobResults"
        jr_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        lines = []
        for i in range(10):
            lines.append(json.dumps({
                "job_id": f"j{i}", "job_name": f"Job {i}",
                "run_at": now, "status": "success",
                "summary": "", "tokens_used": 0, "duration_seconds": 0,
            }))
        (jr_dir / ".job-results.jsonl").write_text("\n".join(lines) + "\n")
        result = _get_job_result_highlights(str(tmp_path), max_items=3)
        assert len(result) == 3


# ── Pipeline Auto-Resume Tests ──


# ── Pipeline Auto-Resume Tests ──

_get_paused_pipeline_highlights = _mod._get_paused_pipeline_highlights


class TestPipelineAutoResume:
    """Test auto-resume directive generation for paused/crashed pipelines."""

    def _make_run(self, workspace, project="TestProj", run_id="run_abc",
                  status="paused", requirement="Fix bug", resume_attempts=0,
                  next_stage="build", updated_at=None):
        """Helper: create a minimal run.json for testing."""
        from datetime import datetime, timezone, timedelta
        runs_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        if updated_at is None:
            # Default: 5 minutes ago (past any cooldown window)
            updated_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        run_data = {
            "id": run_id,
            "status": status,
            "requirement": requirement,
            "resume_attempts": resume_attempts,
            "updated_at": updated_at,
            "stages": [
                {"stage": "evaluate", "status": "completed"},
                {"stage": "think", "status": "completed"},
            ],
            # Use the REAL production crash reason (session_crash_auto_detected —
            # underscored, so it escapes the whole-word `crash` true-trigger and is
            # treated as a crash → auto-resume, per D3). The old fixture used the loose
            # "session crash" (space), which `\bcrash\b` matches as a deliberate
            # true-trigger — that would now (correctly) be suppressed from auto-resume.
            "checkpoint": {"next_stage": next_stage, "reason": "session_crash_auto_detected"},
        }
        run_file = runs_dir / "run.json"
        run_file.write_text(json.dumps(run_data, indent=2))
        return run_file

    def test_auto_resume_directive_emitted(self, tmp_path):
        """Paused pipeline with attempts < 3 produces AUTO-RESUME directive."""
        self._make_run(tmp_path, resume_attempts=0)
        result = _get_paused_pipeline_highlights(tmp_path)
        assert len(result) == 1
        assert "AUTO-RESUME" in result[0]
        assert "attempt 1/3" in result[0]
        assert "run-resume" in result[0]

    def test_resume_increments_counter(self, tmp_path):
        """Each auto-resume directive increments resume_attempts in run.json."""
        run_file = self._make_run(tmp_path, resume_attempts=1)
        _get_paused_pipeline_highlights(tmp_path)
        # Re-read the file — should now be 2
        updated = json.loads(run_file.read_text())
        assert updated["resume_attempts"] == 2

    def _make_run_reason(self, workspace, reason, run_id="run_del",
                         project="TestProj", resume_attempts=0):
        """Helper: a paused run with a specific checkpoint.reason."""
        from datetime import datetime, timezone, timedelta
        runs_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_data = {
            "id": run_id, "status": "paused", "requirement": "Fix bug",
            "resume_attempts": resume_attempts,
            "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "stages": [{"stage": "evaluate", "status": "completed"},
                       {"stage": "plan", "status": "completed"}],
            "checkpoint": {"next_stage": "build", "reason": reason},
        }
        run_file = runs_dir / "run.json"
        run_file.write_text(json.dumps(run_data, indent=2))
        return run_file

    def test_deliberate_pause_NOT_auto_resumed(self, tmp_path):
        """D3 (run_57929039): a run paused ON PURPOSE (Gate BLOCK / FALSIFIED — a
        true-trigger reason) must NOT be surfaced as an AUTO-RESUME candidate. It is
        awaiting a human decision; auto-resuming would blow past the very decision it
        stopped for."""
        self._make_run_reason(tmp_path, reason="Gate 1 BLOCK: plan targets a symptom")
        result = _get_paused_pipeline_highlights(tmp_path)
        joined = "\n".join(result)
        assert "AUTO-RESUME" not in joined, \
            f"a deliberate Gate-BLOCK pause must not be an auto-resume candidate: {result}"

    def test_deliberate_pause_does_NOT_increment_resume_attempts(self, tmp_path):
        """A deliberate pause must NOT burn the run's resume_attempts budget — that
        budget is for crash recovery, not for a run deliberately awaiting a decision."""
        run_file = self._make_run_reason(
            tmp_path, reason="Gate 1 BLOCK: awaiting a decision", resume_attempts=0)
        _get_paused_pipeline_highlights(tmp_path)
        after = json.loads(run_file.read_text())
        assert after["resume_attempts"] == 0, \
            f"deliberate pause wrongly incremented resume_attempts to {after['resume_attempts']}"

    def test_deliberate_pause_surfaces_awaiting_decision(self, tmp_path):
        """A deliberate pause should still be VISIBLE — surfaced as AWAITING DECISION
        (not silently dropped), so the user knows a run needs them."""
        self._make_run_reason(tmp_path, reason="Gate 2 BLOCK: adversarial found a HIGH")
        result = _get_paused_pipeline_highlights(tmp_path)
        joined = "\n".join(result)
        assert "AWAITING DECISION" in joined or "awaiting" in joined.lower(), \
            f"a deliberate pause must surface as awaiting-decision, got: {result}"

    def test_crash_pause_STILL_auto_resumes(self, tmp_path):
        """Regression guard: a genuine crash (session_crash_auto_detected — NOT a
        true-trigger) must STILL auto-resume. D3 only suppresses DELIBERATE pauses."""
        self._make_run_reason(tmp_path, reason="session_crash_auto_detected")
        result = _get_paused_pipeline_highlights(tmp_path)
        assert any("AUTO-RESUME" in ln for ln in result), \
            f"a real crash must still auto-resume: {result}"

    def test_awaiting_decision_does_NOT_crowd_out_auto_resume(self, tmp_path):
        """Gate-2 MEDIUM (run_57929039): AWAITING-DECISION lines live in a SEPARATE
        always-shown bucket, so a flood of fresh deliberate pauses cannot crowd a
        genuine crash-recovery AUTO-RESUME directive out of the max_items cap. Create
        several fresh deliberate pauses + one crash orphan; the AUTO-RESUME directive
        must STILL appear (default max_items=3 would otherwise be filled by the pauses)."""
        # Create the crash orphan FIRST (oldest mtime) so assertion #1 is load-bearing:
        # if awaiting shared the recency-sorted directive cap, the 5 fresher deliberate
        # pauses would sort ahead and evict the older crash directive from max_items=3.
        self._make_run_reason(tmp_path, reason="session_crash_auto_detected",
                              run_id="run_crash", project="CrashProj")
        for i in range(5):
            self._make_run_reason(tmp_path, reason=f"Gate 1 BLOCK: decision {i}",
                                  run_id=f"run_del{i}", project=f"Del{i}")
        result = _get_paused_pipeline_highlights(tmp_path)
        joined = "\n".join(result)
        assert any("AUTO-RESUME" in ln and "run_crash" in ln for ln in result), \
            f"the crash-recovery AUTO-RESUME directive was crowded out: {result}"
        # And the deliberate pauses are still all visible (separate bucket, own cap).
        assert joined.count("AWAITING DECISION") == 5, \
            f"all 5 deliberate pauses must surface as awaiting-decision: {result}"

    def test_exhausted_attempts_shows_informational(self, tmp_path):
        """After 3 attempts, shows informational warning, not directive."""
        self._make_run(tmp_path, resume_attempts=3)
        result = _get_paused_pipeline_highlights(tmp_path)
        # Filter by substring (robust) rather than positional [0] — the exhausted
        # summary is appended after any directives.
        summaries = [ln for ln in result if "Manual intervention" in ln]
        assert len(summaries) == 1
        assert "AUTO-RESUME" not in summaries[0]
        assert "exhausted" in summaries[0]

    def test_exhausted_id_list_bounded(self, tmp_path):
        """Summary caps displayed ids at 10 (+N more) but count stays exact."""
        for i in range(14):
            self._make_run(tmp_path, project=f"Ex{i:02d}", run_id=f"run_ex{i:02d}",
                           resume_attempts=3)
        result = _get_paused_pipeline_highlights(tmp_path)
        summary = next(ln for ln in result if "Manual intervention" in ln)
        assert "14 pipeline" in summary  # exact count, regardless of cap
        assert "+4 more" in summary  # 14 - 10 cap
        # Exactly 10 ids rendered (iterdir order is arbitrary — count, don't name)
        assert summary.count("run_ex") == 10

    def test_running_orphan_transitions_to_paused(self, tmp_path):
        """A 'running' pipeline in a new session gets marked 'paused' first."""
        # Create a run with no pre-existing checkpoint reason, old enough to pass cooldown
        from datetime import datetime, timezone, timedelta
        runs_dir = tmp_path / "Projects" / "TestProj" / ".artifacts" / "runs" / "run_orphan"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_data = {
            "id": "run_orphan",
            "status": "running",
            "requirement": "Test orphan",
            "resume_attempts": 0,
            "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "stages": [{"stage": "evaluate", "status": "completed"}],
            "checkpoint": {"next_stage": "build"},  # No "reason" field
        }
        run_file = runs_dir / "run.json"
        run_file.write_text(json.dumps(run_data, indent=2))

        result = _get_paused_pipeline_highlights(tmp_path)
        # Should emit auto-resume directive
        assert "AUTO-RESUME" in result[0]
        # run.json should now show status=paused with crash reason
        updated = json.loads(run_file.read_text())
        assert updated["status"] == "paused"
        assert updated["checkpoint"]["reason"] == "session_crash_auto_detected"

    def test_old_runs_ignored(self, tmp_path):
        """Runs older than 24h are not surfaced."""
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self._make_run(tmp_path, updated_at=old_time)
        result = _get_paused_pipeline_highlights(tmp_path)
        assert len(result) == 0

    def test_old_running_orphan_not_mutated(self, tmp_path):
        """A 'running' orphan older than 24h is NOT mutated (freshness before mutation)."""
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        run_file = self._make_run(tmp_path, status="running", updated_at=old_time)
        _get_paused_pipeline_highlights(tmp_path)
        # Should not have been mutated — still "running"
        data = json.loads(run_file.read_text())
        assert data["status"] == "running"

    def test_first_attempt_no_cooldown(self, tmp_path):
        """First resume attempt (from 0) has no cooldown — immediate recovery."""
        from datetime import datetime, timezone, timedelta
        # Just crashed 2 seconds ago — attempt 0 should still fire immediately
        recent = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
        self._make_run(tmp_path, updated_at=recent, resume_attempts=0)
        result = _get_paused_pipeline_highlights(tmp_path)
        assert len(result) == 1
        assert "AUTO-RESUME" in result[0]

    def test_cooldown_skips_too_recent(self, tmp_path):
        """Second attempt is skipped if within 30s cooldown window."""
        from datetime import datetime, timezone, timedelta
        # 10 seconds ago — within 30s cooldown for attempt 1
        recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self._make_run(tmp_path, updated_at=recent, resume_attempts=1)
        result = _get_paused_pipeline_highlights(tmp_path)
        # Should NOT emit directive (cooldown not elapsed)
        assert len(result) == 0

    def test_cooldown_respects_attempt_level(self, tmp_path):
        """Higher attempt levels have longer cooldown (60s for attempt 2)."""
        from datetime import datetime, timezone, timedelta
        # 45 seconds ago — past 30s (attempt 1 cooldown) but within 60s (attempt 2)
        recent = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
        self._make_run(tmp_path, updated_at=recent, resume_attempts=2)
        result = _get_paused_pipeline_highlights(tmp_path)
        # Should NOT emit (45s < 60s cooldown for attempt 2)
        assert len(result) == 0

    def test_max_items_respected(self, tmp_path):
        """Only top N items returned."""
        for i in range(5):
            self._make_run(tmp_path, project=f"Proj{i}", run_id=f"run_{i}")
        result = _get_paused_pipeline_highlights(tmp_path, max_items=2)
        assert len(result) == 2

    def test_exhausted_not_dropped_by_max_items(self, tmp_path):
        """All exhausted runs surface in ONE summary line, never truncated by max_items.

        Regression: directives and exhausted runs shared the [:max_items] cap,
        so exhausted stale runs vanished from the briefing (14 existed, 3 shown).
        """
        for i in range(5):  # 5 > default max_items=3
            self._make_run(
                tmp_path, project=f"Ex{i}", run_id=f"run_ex{i}", resume_attempts=3
            )
        result = _get_paused_pipeline_highlights(tmp_path)
        # No directives → exactly one collapsed summary line
        assert len(result) == 1
        summary = result[0]
        assert "exhausted" in summary
        assert "Manual intervention" in summary
        assert "5 pipeline" in summary  # count is reported
        # Every exhausted run id is listed — none silently dropped
        for i in range(5):
            assert f"run_ex{i}" in summary

    def test_exhausted_summary_coexists_with_capped_directives(self, tmp_path):
        """Directives stay capped at max_items AND exhausted summary still appears.

        Proves the two concerns are decoupled: rate-limiting directives (STEERING #1)
        does not cause exhausted stale to be dropped.
        """
        for i in range(4):  # 4 directives > max_items=2
            self._make_run(tmp_path, project=f"Dir{i}", run_id=f"run_dir{i}",
                           resume_attempts=0)
        for i in range(3):  # 3 exhausted
            self._make_run(tmp_path, project=f"Ex{i}", run_id=f"run_ex{i}",
                           resume_attempts=3)
        result = _get_paused_pipeline_highlights(tmp_path, max_items=2)
        directives = [ln for ln in result if "AUTO-RESUME" in ln]
        summaries = [ln for ln in result if "Manual intervention" in ln]
        assert len(directives) == 2  # directives capped
        assert len(summaries) == 1  # one collapsed summary
        for i in range(3):
            assert f"run_ex{i}" in summaries[0]  # all exhausted listed


class TestPipelineSupersedeAndActiveSkip:
    """run_0c8e007a: gauge must not surface (a) the currently-active run, or
    (b) a paused run already superseded by a newer completed run. Both were
    false-alarm sources: the gauge read dirty data without a freshness/supersede
    check (same disease class as finding-1/M4-3/noise-gate)."""

    def _make_run(self, workspace, project="TestProj", run_id="run_abc",
                  status="paused", requirement="Fix bug", resume_attempts=0,
                  next_stage="build", updated_at=None, minutes_ago=5,
                  reason="session_crash_auto_detected"):
        from datetime import datetime, timezone, timedelta
        runs_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        if updated_at is None:
            updated_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        run_data = {
            "id": run_id, "status": status, "requirement": requirement,
            "resume_attempts": resume_attempts, "updated_at": updated_at,
            "stages": [{"stage": "evaluate", "status": "completed"}],
            "checkpoint": {"next_stage": next_stage, "reason": reason},
        }
        run_file = runs_dir / "run.json"
        run_file.write_text(json.dumps(run_data, indent=2))
        return run_file

    # ── AC2: active-run skip (running + fresh = executing this session) ──
    def test_active_running_run_not_surfaced(self, tmp_path):
        """A 'running' run updated seconds ago is actively executing — must NOT
        be surfaced as an AUTO-RESUME directive."""
        self._make_run(tmp_path, status="running", run_id="run_active",
                       updated_at=None, minutes_ago=0)  # ~now
        result = _get_paused_pipeline_highlights(tmp_path)
        assert result == [], f"active run should not be surfaced, got: {result}"

    def test_active_running_run_not_mutated(self, tmp_path):
        """The active run's resume_attempts/status must NOT be mutated by the gauge."""
        run_file = self._make_run(tmp_path, status="running", run_id="run_active",
                                  resume_attempts=0, minutes_ago=0)
        _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(run_file.read_text())
        assert data["status"] == "running", "active run must not be transitioned to paused"
        assert data["resume_attempts"] == 0, "active run attempts must not be incremented"

    def test_crashed_running_run_still_surfaced(self, tmp_path):
        """A 'running' run NOT fresh (older than active threshold, but <24h) is a
        genuine crash orphan — must STILL be surfaced (don't over-suppress)."""
        self._make_run(tmp_path, status="running", run_id="run_crashed",
                       minutes_ago=30)  # >600s, <24h
        result = _get_paused_pipeline_highlights(tmp_path)
        assert any("AUTO-RESUME" in ln for ln in result), \
            f"crashed orphan should still surface, got: {result}"

    # ── AC1 + AC4: superseded paused run skipped + marked ──
    def test_superseded_paused_run_not_surfaced(self, tmp_path):
        """A paused run with a newer same-project COMPLETED run is superseded —
        must NOT be surfaced."""
        from datetime import datetime, timezone, timedelta
        older = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        newer = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        # resume_attempts=3 (exhausted): a crash orphan is supersede-eligible only
        # after its resume budget is used up (Plan C, Gate-1 Attack-1 fix).
        self._make_run(tmp_path, run_id="run_old_paused", status="paused",
                       resume_attempts=3, updated_at=older)
        self._make_run(tmp_path, run_id="run_new_done", status="completed",
                       updated_at=newer)
        result = _get_paused_pipeline_highlights(tmp_path)
        assert result == [], f"superseded paused run should not surface, got: {result}"

    def test_superseded_paused_run_marked_abandoned(self, tmp_path):
        """AC4: the superseded paused run is marked status=abandoned with
        abandon_reason=superseded_by_<id> (reusing the existing convention)."""
        from datetime import datetime, timezone, timedelta
        older = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        newer = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        sup = self._make_run(tmp_path, run_id="run_old_paused", status="paused",
                             resume_attempts=3, updated_at=older)
        self._make_run(tmp_path, run_id="run_new_done", status="completed",
                       updated_at=newer)
        _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(sup.read_text())
        assert data["status"] == "abandoned", f"expected abandoned, got {data['status']}"
        assert data.get("abandon_reason") == "superseded_by_run_new_done", \
            f"expected superseded_by marker, got {data.get('abandon_reason')}"

    # ── ① deliberate-pause guard (run_17e3399c) — supersede must NOT archive a
    #    run paused awaiting a human decision, even when a newer completed run exists.
    #    Plan C: reuse artifact_cli._checkpoint_reason_has_true_trigger (word-boundary,
    #    single-source vocab) + gate crash orphans on resume_attempts>=MAX. ──
    def _sibling_done(self, tmp_path):
        """Create a newer COMPLETED sibling so newest_completed_ts>run_ts is true
        (makes the supersede branch reachable — without it the branch never fires)."""
        from datetime import datetime, timezone, timedelta
        newer = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self._make_run(tmp_path, run_id="run_new_done", status="completed",
                       updated_at=newer)

    def test_deliberate_pause_not_superseded(self, tmp_path):
        """AC1: a paused run whose checkpoint.reason is a deliberate human-decision
        signal (L2/Gate BLOCK/judgment) is NEVER archived by recency — the user
        must still be reminded to decide. THE BUG FIX."""
        from datetime import datetime, timezone, timedelta
        older = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        sup = self._make_run(tmp_path, run_id="run_l2", status="paused",
                             resume_attempts=3, updated_at=older,
                             reason="L2 BLOCK (STEERING#7 collision): needs XG decision")
        self._sibling_done(tmp_path)
        result = _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(sup.read_text())
        assert data["status"] == "paused", \
            f"deliberate-pause run must NOT be archived, got {data['status']}"
        assert any("run_l2" in ln for ln in result), \
            f"deliberate-pause run must still surface, got: {result}"

    def test_crash_orphan_resume_budget_not_superseded(self, tmp_path):
        """AC3: a crash orphan with resume_attempts<MAX is NOT archived — it must
        finish its auto-resume budget first (Gate-1 Attack-1)."""
        from datetime import datetime, timezone, timedelta
        older = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        sup = self._make_run(tmp_path, run_id="run_fresh_crash", status="paused",
                             resume_attempts=0, updated_at=older,
                             reason="session_crash_auto_detected")
        self._sibling_done(tmp_path)
        result = _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(sup.read_text())
        assert data["status"] == "paused", \
            f"fresh crash orphan must keep resume budget, got {data['status']}"
        assert any("AUTO-RESUME" in ln for ln in result), \
            f"fresh crash orphan must still auto-resume, got: {result}"

    def test_empty_reason_run_still_superseded(self, tmp_path):
        """AC4: a paused run with EMPTY reason IS archived (no leak — Gate-1
        Attack-3). Empty is not a deliberate-decision signal."""
        from datetime import datetime, timezone, timedelta
        older = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        sup = self._make_run(tmp_path, run_id="run_empty", status="paused",
                             resume_attempts=3, updated_at=older, reason="")
        self._sibling_done(tmp_path)
        _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(sup.read_text())
        assert data["status"] == "abandoned", \
            f"empty-reason run must be archived (no leak), got {data['status']}"

    def test_substring_trap_not_false_matched_as_deliberate(self, tmp_path):
        """AC5: a reason containing 'l2'/'crash' as a SUBSTRING but not a whole word
        (e.g. 'model2 render crash') must NOT be treated as deliberate — it is
        archived. Proves the word-boundary matcher (vs substring) is in use; this
        test FAILS under a naive substring denylist (Gate-1 Attack-3 / Item-3)."""
        from datetime import datetime, timezone, timedelta
        older = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        sup = self._make_run(tmp_path, run_id="run_trap", status="paused",
                             resume_attempts=3, updated_at=older,
                             reason="model2 render pipeline crashed mid-step")
        self._sibling_done(tmp_path)
        _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(sup.read_text())
        assert data["status"] == "abandoned", \
            f"substring-trap reason must NOT be preserved as deliberate, got {data['status']}"

    # ── AC3: precision — genuine open run still surfaced ──
    def test_genuine_open_paused_run_still_surfaced(self, tmp_path):
        """A paused run with NO newer completed run is genuinely open — must
        still surface. This is the precision guard against over-suppression."""
        self._make_run(tmp_path, run_id="run_genuine", status="paused",
                       resume_attempts=0)
        result = _get_paused_pipeline_highlights(tmp_path)
        assert any("AUTO-RESUME" in ln for ln in result), \
            f"genuine open run must still surface, got: {result}"

    def test_older_completed_does_not_supersede(self, tmp_path):
        """A completed run OLDER than the paused run does NOT supersede it —
        the paused work came after, so it's still open."""
        from datetime import datetime, timezone, timedelta
        old_done = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        recent_paused = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self._make_run(tmp_path, run_id="run_old_done", status="completed",
                       updated_at=old_done)
        self._make_run(tmp_path, run_id="run_paused", status="paused",
                       updated_at=recent_paused, resume_attempts=0)
        result = _get_paused_pipeline_highlights(tmp_path)
        assert any("AUTO-RESUME" in ln for ln in result), \
            "paused run newer than the completed run must still surface"

    def test_completed_in_other_project_does_not_supersede(self, tmp_path):
        """Supersede is scoped per-project: a newer completed run in a DIFFERENT
        project does not supersede this project's paused run."""
        from datetime import datetime, timezone, timedelta
        newer = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        self._make_run(tmp_path, project="ProjA", run_id="run_paused",
                       status="paused", resume_attempts=0)
        self._make_run(tmp_path, project="ProjB", run_id="run_done",
                       status="completed", updated_at=newer)
        result = _get_paused_pipeline_highlights(tmp_path)
        assert any("AUTO-RESUME" in ln for ln in result), \
            "cross-project completed run must not supersede"

    # ── Gate-2 HIGH regression: a slow-but-LIVE running run must NEVER be
    #    archived even if an older completed twin exists. Supersede is
    #    paused-only; a stalled running run goes through orphan-transition. ──
    def test_slow_running_run_not_archived_by_supersede(self, tmp_path):
        """A 'running' run older than the active window (slow stage, e.g. BUILD)
        with a newer completed run in the same project must NOT be marked
        abandoned — it's live, not superseded. (Gate-2 HIGH, run_0c8e007a.)"""
        from datetime import datetime, timezone, timedelta
        # running run last wrote run.json 5 min ago (slow stage > 90s window)
        run_old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        # a completed run in the same project, NEWER than the running run's write
        done_newer = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        live = self._make_run(tmp_path, run_id="run_live", status="running",
                              updated_at=run_old)
        self._make_run(tmp_path, run_id="run_done", status="completed",
                       updated_at=done_newer)
        _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(live.read_text())
        assert data["status"] != "abandoned", \
            "a slow-but-live running run must NOT be archived as superseded"

    def test_equal_timestamp_does_not_supersede(self, tmp_path):
        """Boundary: a completed run with the EXACT same updated_at as a paused
        run does NOT supersede it (strict-greater guard — avoids false-archive
        on same-second unrelated writes)."""
        from datetime import datetime, timezone, timedelta
        same = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        paused = self._make_run(tmp_path, run_id="run_paused", status="paused",
                               updated_at=same, resume_attempts=0)
        self._make_run(tmp_path, run_id="run_done", status="completed",
                       updated_at=same)
        result = _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(paused.read_text())
        assert data["status"] == "paused", "equal-ts must not supersede"
        assert any("AUTO-RESUME" in ln for ln in result), \
            "equal-ts paused run still surfaces"

    def test_malformed_updated_at_not_archived(self, tmp_path):
        """A paused run with an unparseable updated_at (run_ts=None) is NOT
        archived (can't establish supersede ordering) — it falls through and
        surfaces rather than being silently destroyed."""
        from datetime import datetime, timezone, timedelta
        newer = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        bad = self._make_run(tmp_path, run_id="run_bad", status="paused",
                            updated_at="not-a-date", resume_attempts=0)
        self._make_run(tmp_path, run_id="run_done", status="completed",
                       updated_at=newer)
        _get_paused_pipeline_highlights(tmp_path)
        data = json.loads(bad.read_text())
        assert data["status"] != "abandoned", \
            "malformed-date run must not be archived (no supersede ordering)"

    # --- mtime pre-filter (IO optimization, run_885eb466) ---
    # The function only cares about runs updated in the last 24h, but used to
    # json.loads EVERY run.json first (343 files / 1.5MB in prod = 569ms warm).
    # A coarse st_mtime gate (2× max_age = 48h buffer) skips the read entirely
    # for long-dead runs. The 48h buffer guarantees the 24h-window semantics are
    # byte-identical: any run whose updated_at COULD be <24h is always let through
    # to the exact content-level check; only runs untouched on disk for >48h are
    # pre-skipped (their updated_at, if present, is necessarily >24h → already
    # skipped; if absent, a 48h-cold orphan is correctly not surfaced).

    def _set_mtime(self, run_file, *, hours_ago):
        import os
        import time as _time
        ts = _time.time() - hours_ago * 3600
        os.utime(run_file, (ts, ts))

    def test_mtime_old_run_skipped_without_read(self, tmp_path, monkeypatch):
        """A run whose file mtime is >48h old is pre-filtered: json.loads is
        never called on it (the whole point of the optimization)."""
        # A run with a FRESH updated_at in CONTENT, but an OLD file mtime.
        # Pre-filter must skip it on mtime alone, before reading content.
        from datetime import datetime, timezone, timedelta
        fresh = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        rf = self._make_run(tmp_path, run_id="run_coldfile", updated_at=fresh)
        self._set_mtime(rf, hours_ago=50)

        real_loads = json.loads
        read_paths = []
        orig_read_text = Path.read_text

        def _tracking_read_text(self, *a, **k):
            if self.name == "run.json":
                read_paths.append(str(self))
            return orig_read_text(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", _tracking_read_text)
        result = _get_paused_pipeline_highlights(tmp_path)
        # The cold-mtime run.json must NOT have been read at all.
        assert not any("run_coldfile" in p for p in read_paths), \
            "run with >48h mtime must be skipped before json.loads"
        assert result == [], "cold-mtime run must not surface"

    def test_mtime_buffer_keeps_24h_to_48h_window_intact(self, tmp_path):
        """A run with mtime in the 24-48h buffer band but a FRESH updated_at is
        still surfaced — the coarse gate must not narrow the real 24h window."""
        from datetime import datetime, timezone, timedelta
        fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        rf = self._make_run(tmp_path, run_id="run_buffer", updated_at=fresh,
                            resume_attempts=0)
        self._set_mtime(rf, hours_ago=36)  # inside 48h buffer, file looks old
        result = _get_paused_pipeline_highlights(tmp_path)
        assert len(result) == 1 and "AUTO-RESUME" in result[0], \
            "fresh-content run within 48h buffer must still surface"

    def test_mtime_fresh_file_still_surfaces(self, tmp_path):
        """Sanity: a normal fresh run (mtime=now) is unaffected by the gate."""
        self._make_run(tmp_path, run_id="run_fresh", resume_attempts=0)
        result = _get_paused_pipeline_highlights(tmp_path)
        assert len(result) == 1 and "AUTO-RESUME" in result[0]


# --- M3b: Recurrence Radar (zone-gated) (run_123a6530) ---
