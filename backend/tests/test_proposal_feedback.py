"""Tests for ProposalFeedbackTracker — per-channel precision tracking + threshold adjustment.

Verifies:
- AC4: channel_stats.json tracks per-channel counts
- AC5: When precision < 40%, threshold tightens
"""
import json
from pathlib import Path

import pytest


class TestComputeChannelStats:
    """AC4: channel_stats.json tracks per-channel {generated, approved, rejected}."""

    def test_import_and_instantiate(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        assert hasattr(tracker, "compute_channel_stats")

    def test_empty_proposals_dir(self, tmp_path):
        from core.proposal_feedback import ProposalFeedbackTracker

        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()

        tracker = ProposalFeedbackTracker()
        stats = tracker.compute_channel_stats(proposals_dir)
        assert stats == {}

    def test_counts_by_source_stage(self, tmp_path):
        from core.proposal_feedback import ProposalFeedbackTracker

        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()

        # 3 rejected from code_intel_feed, 2 approved from signal_ddd_bridge
        for i in range(3):
            (proposals_dir / f"proposal_code_{i}.json").write_text(json.dumps({
                "source_stage": "code_intel_feed",
                "status": "rejected",
            }))
        for i in range(2):
            (proposals_dir / f"proposal_signal_{i}.json").write_text(json.dumps({
                "source_stage": "signal_ddd_bridge",
                "status": "approved",
            }))

        tracker = ProposalFeedbackTracker()
        stats = tracker.compute_channel_stats(proposals_dir)

        assert stats["code_intel_feed"]["rejected"] == 3
        assert stats["code_intel_feed"]["generated"] == 3
        assert stats["signal_ddd_bridge"]["approved"] == 2
        assert stats["signal_ddd_bridge"]["generated"] == 2

    def test_pending_counted_as_generated(self, tmp_path):
        from core.proposal_feedback import ProposalFeedbackTracker

        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()

        (proposals_dir / "proposal_1.json").write_text(json.dumps({
            "source_stage": "reflect_feed",
            "status": "pending",
        }))

        tracker = ProposalFeedbackTracker()
        stats = tracker.compute_channel_stats(proposals_dir)

        assert stats["reflect_feed"]["generated"] == 1
        assert stats["reflect_feed"]["approved"] == 0
        assert stats["reflect_feed"]["rejected"] == 0

    def test_persist_stats(self, tmp_path):
        from core.proposal_feedback import ProposalFeedbackTracker

        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()
        artifacts_dir = tmp_path / ".artifacts"
        artifacts_dir.mkdir()

        (proposals_dir / "proposal_p1.json").write_text(json.dumps({
            "source_stage": "ch1",
            "status": "approved",
        }))

        tracker = ProposalFeedbackTracker()
        tracker.compute_channel_stats(proposals_dir, persist_to=artifacts_dir)

        stats_file = artifacts_dir / "channel_stats.json"
        assert stats_file.exists()
        data = json.loads(stats_file.read_text())
        assert "ch1" in data


class TestThresholdAdjustment:
    """AC5: When precision < 40%, threshold increases."""

    def test_high_precision_no_adjustment(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"test_channel": {"generated": 10, "approved": 8, "rejected": 2}}

        threshold = tracker.get_adjusted_threshold("test_channel", 0.7, stats)
        # 80% precision — no adjustment needed
        assert threshold == 0.7

    def test_low_precision_increases_threshold(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        # 30% precision (3 approved, 7 rejected out of 10 decided)
        stats = {"test_channel": {"generated": 12, "approved": 3, "rejected": 7}}

        threshold = tracker.get_adjusted_threshold("test_channel", 0.7, stats)
        # Should increase by 0.15 (precision = 3/10 = 30% < 40%)
        assert threshold == pytest.approx(0.85)

    def test_threshold_caps_at_095(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"test_channel": {"generated": 10, "approved": 1, "rejected": 9}}

        threshold = tracker.get_adjusted_threshold("test_channel", 0.9, stats)
        # 0.9 + 0.15 would be 1.05, but cap at 0.95
        assert threshold == 0.95

    def test_threshold_floor_at_050(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        # No stats for channel — should return base but never below 0.5
        stats = {}

        threshold = tracker.get_adjusted_threshold("unknown", 0.3, stats)
        assert threshold == 0.5

    def test_unknown_channel_returns_base(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"other_channel": {"generated": 10, "approved": 5, "rejected": 5}}

        threshold = tracker.get_adjusted_threshold("unknown", 0.7, stats)
        # Unknown channel, no data — return max(base, floor)
        assert threshold == 0.7
