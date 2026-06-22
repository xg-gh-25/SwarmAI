"""Evolution Pipeline v3 Phase 1 — governance router.

Takes a :class:`JudgmentClassification` and routes it by ``counter_state``
(the asymmetric-autonomy decision, design §7):

  - counter_state="counted"  (operational / low-risk)
        -> ``tracker.record(class)`` ONCE. Auto-counts toward the 3x threshold.
  - counter_state="pending_confirm"  (cognitive / CLASS_*)
        -> append to the pending-confirm queue (flock-safe JSON), build a SOUL
           Intake Gate brief, and return it. ``tracker.record`` is NEVER called.
           The 3x counter for judgment patterns is human-verified by construction —
           a misclassification cannot silently mature toward a governance proposal.

Safety invariants (design §9):
  - NEVER writes to SOUL/AGENT/STEERING. Only the pending queue is persisted.
  - The pending queue is the P3 dashboard's input; promotion happens there,
    through the human Intake Gate, which fires record() on accept (not here).

Key public symbols:
    route_classification — route one classification; returns Intake brief | None

Design: Knowledge/Designs/2026-06-22-evolution-pipeline-v3-governance-routing-design.md
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Budget reality (verified against SOUL/AGENT/STEERING 2026-06-22): the router's
# Intake brief surfaces what must retire if a slot is full. SOUL principles 5/5,
# STEERING 15/15 are full; AGENT rules have room. So cognitive proposals almost
# always land as an L1 rule (AGENT) or a gate — rarely a new principle.
_BUDGET_NOTE = (
    "Principles 5/5 (full), AGENT rules <=25, STEERING 15/15 (full). "
    "Full slot -> propose retire-one before promote."
)


def _default_pending_path() -> Path:
    return Path.home() / ".swarm-ai" / "state" / "governance_pending.json"


def _flock(fd, exclusive=True):
    try:
        from utils.file_lock import flock_exclusive

        flock_exclusive(fd)
    except ImportError:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _funlock(fd):
    try:
        from utils.file_lock import flock_unlock

        flock_unlock(fd)
    except ImportError:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _append_pending(item: dict, pending_path: Path) -> None:
    """Append one item to the pending-confirm queue under an exclusive lock.

    Re-reads the queue under lock (parallel sessions may have appended), then
    writes atomically (tmp + replace). Same pattern as correction_tracker.
    """
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = pending_path.with_suffix(".json.lock")
    fd = open(lock_path, "w")
    try:
        _flock(fd)
        queue: list = []
        if pending_path.exists():
            try:
                loaded = json.loads(pending_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    queue = loaded
            except (json.JSONDecodeError, OSError):
                queue = []  # corrupt -> start fresh (item still gets appended)
        queue.append(item)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=pending_path.parent, suffix=".tmp", delete=False, encoding="utf-8"
        )
        try:
            json.dump(queue, tmp, indent=2, ensure_ascii=False)
            tmp.close()
            Path(tmp.name).replace(pending_path)
        except Exception:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            raise
    finally:
        _funlock(fd)
        fd.close()


def _build_intake_brief(jc) -> dict:
    """Build the SOUL Intake Gate brief for a cognitive classification.

    Matches the 4-field protocol the human already expects:
    {classify, parent, conflict, budget}.
    """
    return {
        "classify": "Rule|Gate",  # cognitive almost never a new Principle (budget full)
        "parent": jc.parent_principle or "unknown",
        "conflict": f"check existing rules for {jc.class_name} before promote",
        "budget": _BUDGET_NOTE,
        "class_name": jc.class_name,
        "evidence": jc.evidence,
        "confidence": jc.confidence,
        "correction_ref": jc.correction_ref,
    }


def route_classification(jc, tracker, pending_path: Path | None = None) -> dict | None:
    """Route one classification by its counter_state.

    Args:
        jc: a JudgmentClassification, or None (degraded — safe no-op).
        tracker: a CorrectionClassTracker (record() called only for "counted").
        pending_path: override for the pending-confirm queue (tests inject tmp).

    Returns:
        The SOUL Intake Gate brief (dict) for cognitive classifications,
        or None for operational / None input.
    """
    if jc is None:
        return None

    pending_path = pending_path or _default_pending_path()

    if jc.counter_state == "counted":
        # Operational / low-risk: auto-count toward the 3x threshold.
        try:
            label = jc.class_name or jc.axis  # operational has no CLASS -> use axis
            tracker.record(label, evidence=(jc.evidence[0] if jc.evidence else ""))
        except Exception as exc:  # noqa: BLE001 — counting must never break routing
            logger.debug("router record() degraded: %s: %s", type(exc).__name__, exc)
        return None

    # counter_state == "pending_confirm": cognitive. Park, NEVER auto-count.
    item = {
        "correction_ref": jc.correction_ref,
        "axis": jc.axis,
        "class_name": jc.class_name,
        "parent_principle": jc.parent_principle,
        "evidence": jc.evidence,
        "confidence": jc.confidence,
        "status": "pending_confirm",
    }
    try:
        _append_pending(item, pending_path)
    except Exception as exc:  # noqa: BLE001 — degrade-to-log
        logger.debug("router pending append degraded: %s: %s", type(exc).__name__, exc)
    return _build_intake_brief(jc)
