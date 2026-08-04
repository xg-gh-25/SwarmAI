"""Proposal Quality Feedback Tracker v2 — closes the DDD cultivation self-improvement loop.

V2 upgrades over V1:
- RejectionReason taxonomy (7 categories)
- Per-reason breakdown enables targeted channel fixes
- Auto-classification when user doesn't provide reason
- Self-correction loop: every 10 rejections → apply top-1 fix
- Anti-runaway: thresholds only increase, ceiling 0.95

Public symbols:
    - RejectionReason         — enum of rejection categories
    - ProposalFeedbackTracker — main tracker class (compute stats, adjust thresholds)
"""
import json
import logging
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Threshold bounds — never too aggressive, never too permissive
THRESHOLD_FLOOR = 0.5
THRESHOLD_CEILING = 0.95
PRECISION_THRESHOLD = 0.4  # Below this precision → tighten confidence
ADJUSTMENT_STEP = 0.15  # How much to raise threshold when precision is low
SELF_CORRECTION_BATCH = 10  # Apply fix every N rejections from same channel


class RejectionReason(Enum):
    """Why a cultivation proposal was rejected.

    Each reason maps to a specific channel fix that prevents recurrence.
    """
    FALSE_POSITIVE = "false_positive"      # Detection was wrong
    STALE_CONTEXT = "stale_context"        # Info correct but already known/outdated
    WRONG_SECTION = "wrong_section"        # Right info, wrong DDD doc/section
    TOO_GRANULAR = "too_granular"          # Info too detailed for the doc level
    WRONG_PROJECT = "wrong_project"        # Routed to wrong project
    DUPLICATE = "duplicate"                # Already captured elsewhere
    JUDGMENT_NEEDED = "judgment_needed"    # Needs human rewrite, not auto-apply


# Maps rejection reason → which adjustment to apply
REASON_FIX_MAP: dict[str, str] = {
    "false_positive": "raise_confidence_threshold",
    "stale_context": "extend_dedupe_window",
    "wrong_section": "add_negative_routing_example",
    "too_granular": "increase_min_change_size",
    "wrong_project": "add_disambiguation_rule",
    "duplicate": "enable_cross_proposal_hash",
    "judgment_needed": "demote_to_suggest_only",
}


class ProposalFeedbackTracker:
    """Tracks per-channel proposal precision with rejection reason breakdown.

    Reads proposal JSON files from .artifacts/proposals/, groups by source_stage,
    and computes {generated, approved, rejected, rejection_breakdown} per channel.
    When precision drops below threshold, applies targeted fixes based on the
    dominant rejection reason.
    """

    def compute_channel_stats(
        self,
        proposals_dir: Path,
        persist_to: Path | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Compute per-channel stats with rejection reason breakdown.

        Args:
            proposals_dir: Directory containing proposal_*.json files
            persist_to: If provided, write channel_stats.json here

        Returns:
            Dict mapping channel name → {generated, approved, rejected,
            rejection_breakdown, precision, adjustments_applied}
        """
        stats: dict[str, dict[str, Any]] = {}

        if not proposals_dir.is_dir():
            return stats

        # Scan BOTH the live dir AND archive/ (run_419ff7d4): the stale-proposal
        # reclaim sweep MOVES terminal proposals to proposals/archive/, so the
        # reject-precision counter must keep counting them or precision silently
        # degrades after every sweep. Archived proposals are terminal, so they only
        # add to the historical approved/rejected tallies — never to live "pending".
        archive_dir = proposals_dir / "archive"
        proposal_files = list(proposals_dir.glob("proposal_*.json"))
        if archive_dir.is_dir():
            proposal_files += list(archive_dir.glob("proposal_*.json"))

        for proposal_file in proposal_files:
            try:
                data = json.loads(proposal_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            source = data.get("source_stage", "unknown")
            status = data.get("status", "pending")

            if source not in stats:
                stats[source] = {
                    "generated": 0,
                    "approved": 0,
                    "rejected": 0,
                    "rejection_breakdown": {},
                }

            stats[source]["generated"] += 1
            if status == "approved":
                stats[source]["approved"] += 1
            elif status == "rejected":
                stats[source]["rejected"] += 1
                # Track rejection reason
                reason = data.get("rejection_reason", "")
                if reason:
                    breakdown = stats[source]["rejection_breakdown"]
                    breakdown[reason] = breakdown.get(reason, 0) + 1

        # Compute precision for each channel
        for channel_data in stats.values():
            approved = channel_data.get("approved", 0)
            rejected = channel_data.get("rejected", 0)
            decided = approved + rejected
            channel_data["precision"] = (
                round(approved / decided, 3) if decided > 0 else 1.0
            )

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
        stats: dict[str, dict[str, Any]],
    ) -> float:
        """Get confidence threshold for a channel, adjusted by precision.

        V2: adjustment is based on the DOMINANT rejection reason, not just
        overall precision. This enables targeted fixes rather than blanket
        threshold increases.

        Anti-runaway: threshold can only increase (never decrease automatically).
        Always bounded by [THRESHOLD_FLOOR, THRESHOLD_CEILING].

        Returns:
            Adjusted threshold (float between 0.5 and 0.95)
        """
        # Enforce floor on base
        threshold = max(base_threshold, THRESHOLD_FLOOR)

        channel_data = stats.get(channel)
        if not channel_data:
            return threshold

        # Compute precision over decided proposals only (not pending)
        approved = channel_data.get("approved", 0)
        rejected = channel_data.get("rejected", 0)
        decided = approved + rejected
        if decided == 0:
            return threshold

        precision = approved / decided

        if precision < PRECISION_THRESHOLD:
            # V2: check dominant reason for targeted adjustment
            breakdown = channel_data.get("rejection_breakdown", {})
            dominant_reason = self._get_dominant_reason(breakdown)

            if dominant_reason == "false_positive":
                # FP-heavy → stronger threshold increase
                threshold += ADJUSTMENT_STEP
            elif dominant_reason in ("stale_context", "duplicate"):
                # Staleness/duplicate → moderate increase
                threshold += ADJUSTMENT_STEP * 0.7
            else:
                # Other reasons → standard increase
                threshold += ADJUSTMENT_STEP * 0.5

        # Anti-runaway: enforce ceiling
        return min(threshold, THRESHOLD_CEILING)

    def check_self_correction(
        self,
        channel: str,
        stats: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Check if a channel has accumulated enough rejections to trigger a fix.

        Returns a fix recommendation if rejections >= SELF_CORRECTION_BATCH,
        or None if not yet triggered.

        Returns:
            {reason, fix_type, channel, rejection_count} or None
        """
        channel_data = stats.get(channel)
        if not channel_data:
            return None

        rejected = channel_data.get("rejected", 0)
        if rejected < SELF_CORRECTION_BATCH:
            return None

        breakdown = channel_data.get("rejection_breakdown", {})
        dominant_reason = self._get_dominant_reason(breakdown)
        if not dominant_reason:
            return None

        fix_type = REASON_FIX_MAP.get(dominant_reason, "unknown")

        return {
            "channel": channel,
            "reason": dominant_reason,
            "fix_type": fix_type,
            "rejection_count": rejected,
            "breakdown": breakdown,
        }

    def auto_classify_rejection(
        self,
        proposal_data: dict[str, Any],
        ddd_content: str | None = None,
    ) -> str:
        """Auto-classify a rejection reason when user doesn't provide one.

        Classification rules (in priority order):
        1. Proposal content already in DDD → duplicate
        2. Source data >7d old → stale_context
        3. Confidence < 0.6 → false_positive
        4. Default → judgment_needed

        Args:
            proposal_data: The proposal JSON data
            ddd_content: Current content of the target DDD doc (for duplicate check)

        Returns:
            RejectionReason value string
        """
        # Rule 1: duplicate check
        if ddd_content:
            proposed = proposal_data.get("proposed_content", "")
            if proposed and proposed.strip() in ddd_content:
                return RejectionReason.DUPLICATE.value

        # Rule 2: stale context
        created_at = proposal_data.get("created_at", "")
        if created_at:
            try:
                from datetime import timezone
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                # Normalize to UTC for comparison (PE-5: avoids naive/aware mismatch)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - created > timedelta(days=7):
                    return RejectionReason.STALE_CONTEXT.value
            except (ValueError, TypeError):
                pass

        # Rule 3: low confidence
        confidence = proposal_data.get("confidence", 1.0)
        if confidence < 0.6:
            return RejectionReason.FALSE_POSITIVE.value

        # Rule 4: default
        return RejectionReason.JUDGMENT_NEEDED.value

    @staticmethod
    def _get_dominant_reason(breakdown: dict[str, int]) -> str:
        """Get the most frequent rejection reason from breakdown dict."""
        if not breakdown:
            return ""
        return max(breakdown, key=lambda k: breakdown[k])
