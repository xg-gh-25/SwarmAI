"""DDD Auto-Approval Gate — additional criteria beyond is_safe_append().

Adds maturity, magnitude, precision, circuit breaker, and conflict checks
on top of the existing safe_append classification. Called from
_cultivate_proposals() to gate whether a "safe" proposal actually gets
auto-applied without human review.

Public symbols:
    - evaluate_auto_approval  — Check all 6 criteria, return ApprovalDecision
    - record_revert           — Record a manual revert (feeds circuit breaker)
    - ApprovalDecision        — Result dataclass
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ddd_cultivation import CultivationProposal

logger = logging.getLogger(__name__)

# Circuit breaker: max reverts before disabling auto-approval for a channel
_MAX_REVERTS_BEFORE_DISABLE = 3
_REVERT_WINDOW_DAYS = 7
_REVERT_LOG_FILENAME = "auto-approval-reverts.jsonl"


@dataclass
class ApprovalDecision:
    """Result of auto-approval evaluation."""
    approved: bool
    reason: str
    criteria_met: dict[str, bool]


def evaluate_auto_approval(
    proposal: "CultivationProposal",
    project_dir: Path,
) -> ApprovalDecision:
    """Evaluate whether a proposal can be auto-applied (no human review).

    All 6 criteria must pass. Returns ApprovalDecision with per-criterion breakdown.
    """
    checks = {
        "safe_target_doc": _check_safe_doc(proposal),
        "small_magnitude": _check_magnitude(proposal),
        "maturity_growing": _check_maturity(proposal, project_dir),
        "no_conflict": _check_no_conflict(proposal, project_dir),
        "circuit_breaker_ok": _check_circuit_breaker(proposal, project_dir),
        "channel_precision": True,  # Placeholder — needs feedback data to gate
    }

    approved = all(checks.values())
    if approved:
        reason = "all criteria met"
    else:
        failed = [k for k, v in checks.items() if not v]
        reason = f"blocked by: {failed}"

    return ApprovalDecision(approved=approved, reason=reason, criteria_met=checks)


def record_revert(source_channel: str, project_dir: Path) -> None:
    """Record a manual revert of an auto-applied proposal.

    When 3 reverts accumulate within 7 days for the same channel,
    the circuit breaker disables auto-approval for that channel.
    """
    log_path = project_dir / ".artifacts" / _REVERT_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "channel": source_channel,
        "reverted_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Private criteria checks ───────────────────────────────────────────────


def _check_safe_doc(proposal: "CultivationProposal") -> bool:
    """PRODUCT.md and PROJECT.md are NEVER auto-approved."""
    return proposal.target_doc in ("IMPROVEMENT.md", "TECH.md")


def _check_magnitude(proposal: "CultivationProposal") -> bool:
    """Content must be < 500 chars (small additive change)."""
    return len(proposal.content) < 500


def _check_maturity(proposal: "CultivationProposal", project_dir: Path) -> bool:
    """Target section must have maturity >= 'growing'.

    Reads the maturity annotation comment from the DDD doc.
    Format: <!-- maturity: growing | sources: N | ... -->
    """
    doc_path = project_dir / proposal.target_doc
    if not doc_path.exists():
        return False

    try:
        content = doc_path.read_text(encoding="utf-8")
    except OSError:
        return False

    # Find the section header
    section_re = re.compile(
        r"^## " + re.escape(proposal.target_section) + r"\s*$", re.MULTILINE
    )
    match = section_re.search(content)
    if not match:
        return False

    # Look for maturity annotation in the next 3 lines after header
    lines_after = content[match.end():match.end() + 500].splitlines()[:3]
    for line in lines_after:
        mat_match = re.search(r"maturity:\s*(\w+)", line)
        if mat_match:
            maturity = mat_match.group(1).lower()
            # growing, mature, evergreen are OK. sparse is NOT.
            return maturity in ("growing", "mature", "evergreen")

    # No maturity annotation found — treat as sparse (conservative)
    return False


def _check_no_conflict(proposal: "CultivationProposal", project_dir: Path) -> bool:
    """No other pending proposal targets the same section.

    Checks .artifacts/proposals/ for pending JSON files with same target.
    """
    proposals_dir = project_dir / ".artifacts" / "proposals"
    if not proposals_dir.exists():
        return True  # No pending proposals = no conflict

    for p_file in proposals_dir.glob("*.json"):
        try:
            data = json.loads(p_file.read_text(encoding="utf-8"))
            if (data.get("target_doc") == proposal.target_doc and
                data.get("target_section") == proposal.target_section and
                data.get("status") == "pending"):
                return False  # Conflict found
        except (json.JSONDecodeError, OSError):
            continue

    return True


def _check_circuit_breaker(proposal: "CultivationProposal", project_dir: Path) -> bool:
    """If 3+ reverts in last 7 days for this source_stage → disabled.

    Reads auto-approval-reverts.jsonl for recent revert entries.
    """
    log_path = project_dir / ".artifacts" / _REVERT_LOG_FILENAME
    if not log_path.exists():
        return True  # No reverts ever = OK

    cutoff = datetime.now(timezone.utc) - timedelta(days=_REVERT_WINDOW_DAYS)
    recent_reverts = 0

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("channel") != proposal.source_stage:
                        continue
                    reverted_at = datetime.fromisoformat(entry["reverted_at"])
                    if reverted_at.tzinfo is None:
                        reverted_at = reverted_at.replace(tzinfo=timezone.utc)
                    if reverted_at >= cutoff:
                        recent_reverts += 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
    except OSError:
        return True

    return recent_reverts < _MAX_REVERTS_BEFORE_DISABLE
