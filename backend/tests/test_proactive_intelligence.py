"""Tests for proactive_intelligence module.

Validates Open Threads parsing, continue-from extraction, pattern detection,
and briefing assembly from synthetic MEMORY.md and DailyActivity data.
"""

import pytest
from pathlib import Path

# Direct import — avoids core/__init__.py which pulls in claude_agent_sdk
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "proactive_intelligence",
    str(Path(__file__).resolve().parent.parent / "core" / "proactive_intelligence.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_session_briefing = _mod.build_session_briefing
_parse_open_threads = _mod._parse_open_threads
_parse_continue_hints = _mod._parse_continue_hints
_detect_patterns = _mod._detect_patterns


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


class TestBuildSessionBriefing:
    def test_full_briefing_with_real_workspace(self):
        workspace = Path("/Users/gawan/.swarm-ai/SwarmWS")
        if not workspace.exists():
            pytest.skip("Real workspace not available")
        briefing = build_session_briefing(workspace)
        assert briefing is not None
        assert "## Session Briefing" in briefing
        assert "Blockers:" in briefing

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
        # Even with malformed data, should return None, not raise
        context_dir = tmp_path / ".context"
        context_dir.mkdir()
        (context_dir / "MEMORY.md").write_text("garbage \x00\x01\x02 data")
        result = build_session_briefing(tmp_path)
        # Should not raise — either None or some partial result
        assert result is None or isinstance(result, str)
