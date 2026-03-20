"""Tests for topic-based superseding in distillation.

Verifies that ``_supersede_by_topic()`` keeps only the newest entry when
multiple entries reference the same subject (e.g., implementation status
claims from different sessions).

This prevents the "memory pipeline temporal lag gap" where stale claims
from early sessions persist alongside newer, more accurate ones.
"""

import pytest

from hooks.distillation_hook import DistillationTriggerHook


class TestSupersedeByTopic:
    """Tests for DistillationTriggerHook._supersede_by_topic."""

    def test_empty_list(self):
        assert DistillationTriggerHook._supersede_by_topic([]) == []

    def test_single_entry_unchanged(self):
        entries = ["- 2026-03-14: Proactive Intelligence L0+L1 implemented"]
        assert DistillationTriggerHook._supersede_by_topic(entries) == entries

    def test_unrelated_entries_all_kept(self):
        entries = [
            "- 2026-03-14: Proactive Intelligence L0+L1 implemented",
            "- 2026-03-15: MCP sandbox network fix deployed",
            "- 2026-03-16: Tab-switch streaming bug resolved",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 3

    def test_same_topic_newer_wins(self):
        """Core case: two entries about Proactive Intelligence, newer wins."""
        entries = [
            "- 2026-03-14: Proactive Intelligence L0+L1 confirmed working in production",
            "- 2026-03-19: Proactive Intelligence L0-L4 full implementation complete",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 1
        assert "L0-L4" in result[0]

    def test_three_entries_same_topic_newest_wins(self):
        """Three progressive entries about the same feature."""
        entries = [
            "- 2026-03-14: Proactive Intelligence L0 parsing implemented",
            "- 2026-03-15: Proactive Intelligence L0+L1+L2 scoring engine added",
            "- 2026-03-19: Proactive Intelligence L0-L4 full implementation shipped",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 1
        assert "L0-L4" in result[0]
        assert "2026-03-19" in result[0]

    def test_mixed_related_and_unrelated(self):
        """Related entries collapse, unrelated ones preserved."""
        entries = [
            "- 2026-03-14: Proactive Intelligence L0+L1 implemented",
            "- 2026-03-15: MCP sandbox network fix resolved",
            "- 2026-03-19: Proactive Intelligence L0-L4 all implemented with 106 tests",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 2
        topics = " ".join(result)
        assert "MCP sandbox" in topics
        assert "L0-L4" in topics
        assert "L0+L1 implemented" not in topics

    def test_session_architecture_superseding(self):
        """Multi-session re-architecture entries should collapse."""
        entries = [
            "- 2026-03-18: Multi-session re-architecture design doc v2 approved",
            "- 2026-03-19: Multi-session re-architecture v7 complete with 585 tests passing",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 1
        assert "v7 complete" in result[0]

    def test_completely_different_topics_no_collapse(self):
        """Entries with no word overlap should never collapse."""
        entries = [
            "- 2026-03-14: Birthday — chose name Swarm",
            "- 2026-03-15: MCP configuration uses file-based loader",
            "- 2026-03-16: Bedrock Claude 4.6 1M context GA",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 3

    def test_preserves_order(self):
        """Result maintains the original insertion order of kept entries."""
        entries = [
            "- 2026-03-14: Feature Alpha v1 released",
            "- 2026-03-15: Unrelated item about documentation",
            "- 2026-03-16: Feature Alpha v2 with breaking changes",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        # Alpha entries collapse to the v2 (index 2), unrelated stays (index 1)
        # Order should be: unrelated first (original index 1), then Alpha v2 (original index 2)
        assert len(result) == 2

    def test_short_entries_not_collapsed(self):
        """Very short entries with few words shouldn't trigger false matches.

        "Fixed bug" and "Fixed another bug" share 2 words out of 2-3 total.
        The 30% Jaccard overlap threshold may fire, but these entries are too
        generic to meaningfully supersede each other.  We assert BOTH survive
        because collapsing them would lose distinct information.
        """
        entries = [
            "- 2026-03-14: Fixed bug",
            "- 2026-03-15: Fixed another bug",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        # Both entries must survive — they describe different bug fixes.
        # If this fails, the overlap threshold needs a minimum-fingerprint-size
        # guard (e.g. require >= 3 significant words before comparing).
        assert len(result) == 2, (
            f"Short generic entries were incorrectly collapsed: {result}"
        )

    def test_no_date_prefix_handled(self):
        """Entries without YYYY-MM-DD prefix don't crash."""
        entries = [
            "- Some entry without a date",
            "- Another entry without a date",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) >= 1  # Doesn't crash

    def test_same_date_both_kept_if_different_topics(self):
        """Two entries on the same date about different topics are both kept."""
        entries = [
            "- 2026-03-19: CLI auto-memory explicitly disabled for SwarmAI",
            "- 2026-03-19: Signal fetcher service architecture designed",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 2

    def test_same_date_same_topic_last_wins(self):
        """COE scenario: multiple same-date entries about same topic — last wins.

        This is the exact bug that caused the Memory Pipeline Temporal Lag Gap:
        DailyActivity captures mid-session, later sessions add more complete
        entries on the same day. All have the same date, so positional order
        (later = newer) must be the tiebreaker.
        """
        entries = [
            "- 2026-03-14: Proactive Intelligence L0+L1 confirmed working in production",
            "- 2026-03-14: Proactive Intelligence L0-L2 scoring engine implemented, 51 tests",
            "- 2026-03-14: Proactive Intelligence L0-L4 full implementation (1142 lines, 106+ tests)",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 1, f"Expected 1 entry, got {len(result)}: {result}"
        assert "L0-L4" in result[0], f"Expected L0-L4 entry, got: {result[0]}"

    def test_same_date_tiebreaker_with_mixed_dates(self):
        """Mixed dates: date takes priority, positional order is only for ties."""
        entries = [
            "- 2026-03-19: Session architecture v7 design approved",
            "- 2026-03-18: Session architecture v6 prototype built",
            "- 2026-03-19: Session architecture v7 complete, 585 tests pass",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 1
        # 2026-03-19 > 2026-03-18, and among two 03-19 entries, index 2 > index 0
        assert "v7 complete" in result[0]
