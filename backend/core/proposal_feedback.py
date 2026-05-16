"""Proposal Quality Feedback Tracker — closes the DDD cultivation self-improvement loop.

Tracks per-channel precision (approved / generated) and auto-adjusts confidence
thresholds when a channel produces too many false positives. This ensures channels
that generate low-quality proposals self-correct over time.

Public symbols:
    - ProposalFeedbackTracker  — main tracker class
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Threshold bounds — never too aggressive, never too permissive
THRESHOLD_FLOOR = 0.5
THRESHOLD_CEILING = 0.95
PRECISION_THRESHOLD = 0.4  # Below this precision → tighten confidence
ADJUSTMENT_STEP = 0.15  # How much to raise threshold when precision is low


class ProposalFeedbackTracker:
    """Tracks per-channel proposal precision and adjusts confidence thresholds.

    Reads proposal JSON files from .artifacts/proposals/, groups by source_stage,
    and computes {generated, approved, rejected} per channel. When precision drops
    below 40%, recommends a tighter confidence threshold for that channel.
    """

    def compute_channel_stats(
        self,
        proposals_dir: Path,
        persist_to: Path | None = None,
    ) -> dict[str, dict[str, int]]:
        """Compute per-channel stats from proposal files.

        Args:
            proposals_dir: Directory containing proposal_*.json files
            persist_to: If provided, write channel_stats.json here

        Returns:
            Dict mapping channel name → {generated, approved, rejected}
        """
        stats: dict[str, dict[str, int]] = {}

        if not proposals_dir.is_dir():
            return stats

        for proposal_file in proposals_dir.glob("proposal_*.json"):
            try:
                data = json.loads(proposal_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            source = data.get("source_stage", "unknown")
            status = data.get("status", "pending")

            if source not in stats:
                stats[source] = {"generated": 0, "approved": 0, "rejected": 0}

            stats[source]["generated"] += 1
            if status == "approved":
                stats[source]["approved"] += 1
            elif status == "rejected":
                stats[source]["rejected"] += 1

        # Persist if requested
        if persist_to and stats:
            stats_file = persist_to / "channel_stats.json"
            try:
                stats_file.write_text(
                    json.dumps(stats, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                logger.warning("proposal_feedback: failed to persist stats: %s", exc)

        return stats

    def get_adjusted_threshold(
        self,
        channel: str,
        base_threshold: float,
        stats: dict[str, dict[str, int]],
    ) -> float:
        """Get confidence threshold for a channel, adjusted by precision.

        If channel precision < 40% → increase threshold by 0.15.
        Always bounded by [THRESHOLD_FLOOR, THRESHOLD_CEILING].

        Args:
            channel: Channel name (source_stage value)
            base_threshold: Default threshold for this channel
            stats: Channel stats from compute_channel_stats()

        Returns:
            Adjusted threshold (float between 0.5 and 0.95)
        """
        # Enforce floor on base
        threshold = max(base_threshold, THRESHOLD_FLOOR)

        channel_data = stats.get(channel)
        if not channel_data:
            return threshold

        generated = channel_data.get("generated", 0)
        if generated == 0:
            return threshold

        approved = channel_data.get("approved", 0)
        precision = approved / generated

        if precision < PRECISION_THRESHOLD:
            threshold += ADJUSTMENT_STEP

        # Enforce ceiling
        return min(threshold, THRESHOLD_CEILING)
