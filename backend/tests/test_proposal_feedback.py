"""Tests for ProposalFeedbackTracker — per-channel precision tracking + threshold adjustment.

Verifies:
- AC4: channel_stats.json tracks per-channel counts
- AC5: When precision < 40%, threshold tightens
"""
import json

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
    """AC5: When precision < 40%, threshold increases (reason-aware in v2)."""

    def test_high_precision_no_adjustment(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"test_channel": {
            "generated": 10, "approved": 8, "rejected": 2,
            "rejection_breakdown": {"false_positive": 2},
        }}

        threshold = tracker.get_adjusted_threshold("test_channel", 0.7, stats)
        # 80% precision — no adjustment needed
        assert threshold == 0.7

    def test_low_precision_fp_dominant(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        # 30% precision, dominant reason = false_positive
        stats = {"test_channel": {
            "generated": 12, "approved": 3, "rejected": 7,
            "rejection_breakdown": {"false_positive": 5, "stale_context": 2},
        }}

        threshold = tracker.get_adjusted_threshold("test_channel", 0.7, stats)
        # FP-dominant → full ADJUSTMENT_STEP (0.15)
        assert threshold == pytest.approx(0.85)

    def test_low_precision_stale_dominant(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"test_channel": {
            "generated": 12, "approved": 3, "rejected": 7,
            "rejection_breakdown": {"stale_context": 5, "false_positive": 2},
        }}

        threshold = tracker.get_adjusted_threshold("test_channel", 0.7, stats)
        # Stale-dominant → 0.7 * ADJUSTMENT_STEP = 0.105
        assert threshold == pytest.approx(0.7 + 0.15 * 0.7, abs=0.01)

    def test_threshold_caps_at_095(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"test_channel": {
            "generated": 10, "approved": 1, "rejected": 9,
            "rejection_breakdown": {"false_positive": 9},
        }}

        threshold = tracker.get_adjusted_threshold("test_channel", 0.9, stats)
        # 0.9 + 0.15 would be 1.05, but cap at 0.95
        assert threshold == 0.95

    def test_threshold_floor_at_050(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {}

        threshold = tracker.get_adjusted_threshold("unknown", 0.3, stats)
        assert threshold == 0.5

    def test_unknown_channel_returns_base(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"other_channel": {
            "generated": 10, "approved": 5, "rejected": 5,
            "rejection_breakdown": {},
        }}

        threshold = tracker.get_adjusted_threshold("unknown", 0.7, stats)
        assert threshold == 0.7


class TestRejectionReasonBreakdown:
    """V2: rejection reason tracking + per-reason breakdown."""

    def test_breakdown_tracked_in_stats(self, tmp_path):
        from core.proposal_feedback import ProposalFeedbackTracker

        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()

        (proposals_dir / "proposal_1.json").write_text(json.dumps({
            "source_stage": "code_intel_feed",
            "status": "rejected",
            "rejection_reason": "false_positive",
        }))
        (proposals_dir / "proposal_2.json").write_text(json.dumps({
            "source_stage": "code_intel_feed",
            "status": "rejected",
            "rejection_reason": "stale_context",
        }))
        (proposals_dir / "proposal_3.json").write_text(json.dumps({
            "source_stage": "code_intel_feed",
            "status": "rejected",
            "rejection_reason": "false_positive",
        }))

        tracker = ProposalFeedbackTracker()
        stats = tracker.compute_channel_stats(proposals_dir)

        breakdown = stats["code_intel_feed"]["rejection_breakdown"]
        assert breakdown["false_positive"] == 2
        assert breakdown["stale_context"] == 1

    def test_precision_computed(self, tmp_path):
        from core.proposal_feedback import ProposalFeedbackTracker

        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()

        for i in range(3):
            (proposals_dir / f"proposal_a{i}.json").write_text(json.dumps({
                "source_stage": "ch1", "status": "approved",
            }))
        for i in range(7):
            (proposals_dir / f"proposal_r{i}.json").write_text(json.dumps({
                "source_stage": "ch1", "status": "rejected",
                "rejection_reason": "false_positive",
            }))

        tracker = ProposalFeedbackTracker()
        stats = tracker.compute_channel_stats(proposals_dir)

        assert stats["ch1"]["precision"] == pytest.approx(0.3, abs=0.01)


class TestSelfCorrection:
    """V2: self-correction triggers after N rejections."""

    def test_no_trigger_below_batch(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"ch1": {
            "generated": 8, "approved": 2, "rejected": 6,
            "rejection_breakdown": {"false_positive": 6},
        }}

        result = tracker.check_self_correction("ch1", stats)
        assert result is None  # < 10 rejections

    def test_trigger_at_batch_threshold(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"ch1": {
            "generated": 15, "approved": 3, "rejected": 12,
            "rejection_breakdown": {"false_positive": 8, "stale_context": 4},
        }}

        result = tracker.check_self_correction("ch1", stats)
        assert result is not None
        assert result["channel"] == "ch1"
        assert result["reason"] == "false_positive"  # dominant
        assert result["fix_type"] == "raise_confidence_threshold"

    def test_dominant_reason_maps_to_correct_fix(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        stats = {"ch1": {
            "generated": 20, "approved": 5, "rejected": 15,
            "rejection_breakdown": {"duplicate": 10, "false_positive": 5},
        }}

        result = tracker.check_self_correction("ch1", stats)
        assert result["reason"] == "duplicate"
        assert result["fix_type"] == "enable_cross_proposal_hash"


class TestAutoClassification:
    """V2: auto-classify rejection reason when user doesn't provide one."""

    def test_duplicate_when_content_in_ddd(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        proposal = {"proposed_content": "## Key Decision\nUse async everywhere"}
        ddd_content = "# TECH\n\n## Key Decision\nUse async everywhere\n\n## Stack"

        reason = tracker.auto_classify_rejection(proposal, ddd_content)
        assert reason == "duplicate"

    def test_stale_when_old(self):
        from core.proposal_feedback import ProposalFeedbackTracker
        from datetime import datetime, timedelta, timezone

        tracker = ProposalFeedbackTracker()
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        proposal = {"created_at": old_date, "confidence": 0.9}

        reason = tracker.auto_classify_rejection(proposal, "")
        assert reason == "stale_context"

    def test_false_positive_when_low_confidence(self):
        from core.proposal_feedback import ProposalFeedbackTracker

        tracker = ProposalFeedbackTracker()
        proposal = {"confidence": 0.4, "created_at": ""}

        reason = tracker.auto_classify_rejection(proposal, "different content")
        assert reason == "false_positive"

    def test_default_judgment_needed(self):
        from core.proposal_feedback import ProposalFeedbackTracker
        from datetime import datetime, timezone

        tracker = ProposalFeedbackTracker()
        recent = datetime.now(timezone.utc).isoformat()
        proposal = {"confidence": 0.9, "created_at": recent, "proposed_content": "new stuff"}

        reason = tracker.auto_classify_rejection(proposal, "completely different")
        assert reason == "judgment_needed"
