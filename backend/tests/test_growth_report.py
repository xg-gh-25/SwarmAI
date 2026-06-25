"""Tests for EvalService.growth_report — the "what I changed / evolved / grew"
self-evolution growth surface (run_448a4f7f, D2/D3).

The growth report is the mentor-facing window into autonomous self-shaping: it
surfaces what the agent recorded/judged/proposed about itself AND — the headline
— any constitution (SOUL/AGENT/STEERING) writes, git-tracked and visible. This
replaces "ask permission to record" with "report after the fact." The pure
formatter is unit-testable on synthetic inputs; the git-gather is a thin adapter.
"""

from core.eval_service import EvalService


class TestGrowthReportFormatter:
    """Pure formatter: (records, proposals, constitution_commits) -> report dict."""

    def test_constitution_change_is_headline(self):
        """A SOUL/AGENT/STEERING commit MUST surface as a headline — this is the
        agent's self-shaping made visible+reversible (the mentor's mirror)."""
        report = EvalService._format_growth_report(
            autonomous_records=[],
            proposals=[],
            constitution_commits=[
                {"hash": "abc1234", "file": "AGENT.md",
                 "subject": "add R16b pre-write reflex", "date": "2026-06-25"},
            ],
        )
        assert report["constitution_changes"], "constitution commit must appear"
        assert report["constitution_changes"][0]["file"] == "AGENT.md"
        assert report["has_constitution_change"] is True
        # headline ranking: constitution changes are the lead section
        assert report["headline"] and "AGENT.md" in report["headline"]

    def test_no_constitution_change_no_headline(self):
        report = EvalService._format_growth_report(
            autonomous_records=[{"class": "CLASS_A", "count": 5}],
            proposals=[],
            constitution_commits=[],
        )
        assert report["has_constitution_change"] is False
        # still reports growth (records), just no constitution headline
        assert report["autonomous_records"]

    def test_surfaces_autonomous_records_and_proposals(self):
        report = EvalService._format_growth_report(
            autonomous_records=[{"class": "CLASS_A", "count": 5}],
            proposals=[{"id": "CLASS_A:rule", "kind": "rule", "source_class": "CLASS_A"}],
            constitution_commits=[],
        )
        assert any(r["class"] == "CLASS_A" for r in report["autonomous_records"])
        assert any(p["id"] == "CLASS_A:rule" for p in report["proposals"])

    def test_empty_growth_is_honest_not_fabricated(self):
        """No activity → an explicit empty report, not invented progress."""
        report = EvalService._format_growth_report(
            autonomous_records=[], proposals=[], constitution_commits=[],
        )
        assert report["has_constitution_change"] is False
        assert report["autonomous_records"] == []
        assert report["proposals"] == []
        assert report["headline"] == "" or "no" in report["headline"].lower()


class TestGrowthReportBriefingLines:
    """The briefing surface: constitution changes render as a flagged headline."""

    def test_briefing_lines_flag_constitution_change(self):
        report = {
            "has_constitution_change": True,
            "constitution_changes": [
                {"hash": "abc1234", "file": "SOUL.md",
                 "subject": "refine P1", "date": "2026-06-25"},
            ],
            "autonomous_records": [],
            "proposals": [],
            "headline": "1 constitution change: SOUL.md",
        }
        lines = EvalService._growth_briefing_lines(report)
        assert any("SOUL.md" in ln for ln in lines)
        # constitution change must be visually flagged (not buried)
        assert any("constitution" in ln.lower() or "🧬" in ln or "grew" in ln.lower()
                   for ln in lines)

    def test_briefing_lines_empty_when_no_growth(self):
        report = {
            "has_constitution_change": False, "constitution_changes": [],
            "autonomous_records": [], "proposals": [], "headline": "",
        }
        lines = EvalService._growth_briefing_lines(report)
        assert lines == []
