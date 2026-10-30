"""Pipeline v1 read model: what the dashboard shows for one run (retirement R1b).

Every value comes from the run's projection (``run.json``, written by
``RunEngine.write_projection`` from the signed event chain) and is listed only for a
run whose protected contract exists. A value the projection does not carry is
``None``: nothing is estimated (AGENTS.md "Never guess").

Public symbols:
- ``is_v1`` — the run has a protected v1 contract (the one test for "v1 run").
- ``display_status`` — v1 lifecycle status in the dashboard vocabulary.
- ``pause_kind`` / ``needs_you`` — what the run needs from a person.
- ``progress`` — (stages done, stages in the profile).
- ``duration_minutes`` — created → finished, only once the run is finished.
- ``tokens`` — every token the run was billed for, or None when not fully measured.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Optional

from core.run_protocol import lifecycle

logger = logging.getLogger(__name__)

_DONE = frozenset({"completed", "degraded"})
NEEDS_YOU_PAUSE_KINDS = frozenset({"decision", "stopped", "worktree", "idle"})


def is_v1(run_id: str) -> bool:
    """A v1 run: its protected contract exists (never inferred from run.json)."""
    from config import get_app_data_dir
    from core.run_protocol.contract import is_v1_run
    try:
        return is_v1_run(get_app_data_dir(), run_id)
    except Exception as exc:  # noqa: BLE001 — an unreadable contract is not a v1 run
        logger.debug("run %s: contract unreadable, not listed as v1: %s", run_id, exc)
        return False


def display_status(raw: Mapping[str, Any]) -> str:
    status = str(raw.get("status") or "")
    return {"active": "running", "reflecting": "running",
            "awaiting_human": "paused"}.get(status, status or "unknown")


def pause_kind(raw: Mapping[str, Any]) -> Optional[str]:
    """'decision' / 'stopped' / 'worktree' / 'idle' (active, but the owner session is
    neither streaming nor waiting for input), else None
    (docs/design/pipeline-v1-human-loop.md)."""
    status = raw.get("status")
    if status == "awaiting_human":
        return "decision"
    if status == "paused":
        return "worktree" if raw.get("reason") == "worktree_unavailable" else "stopped"
    if status in ("active", "reflecting"):
        from core import pipeline_human
        owner = raw.get("session_id")
        if isinstance(owner, str) and owner and pipeline_human.owner_live(owner) is False:
            return "idle"
    return None


#: the abandon reason the dashboard's Cancel records: the person already decided
USER_CANCEL_REASON = "cancelled_by_user"


def needs_you(status: str, kind: Optional[str], reason: Optional[str] = None) -> bool:
    """A pause a person must act on, or a run that was abandoned without them (a run
    the user cancelled is their own decision, R5 review)."""
    if status == "abandoned":
        return reason != USER_CANCEL_REASON
    return kind in NEEDS_YOU_PAUSE_KINDS


def progress(raw: Mapping[str, Any]) -> tuple[int, int]:
    stages = raw.get("stages") if isinstance(raw.get("stages"), Mapping) else {}
    sequence = lifecycle.PROFILE_STAGES.get(str(raw.get("profile") or ""), ())
    return sum(1 for name in sequence if stages.get(name) in _DONE), len(sequence)


def _at(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def duration_minutes(raw: Mapping[str, Any]) -> Optional[float]:
    """Created → the terminal transition; None while the run is not finished."""
    start, end = _at(raw.get("created_at")), _at(raw.get("finished_at"))
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60, 1)


def _count(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def tokens(raw: Mapping[str, Any]) -> Optional[int]:
    """Every token the run was billed for: input (cache writes included), output and
    cache reads (``run_protocol.usage.measure``). The run's own ``billed_tokens``
    measurement wins; a run measured before that metric existed falls back to its
    audit's token cost when all three parts are there. Anything less is None, never
    input + output alone: with a warm cache that is about a hundredth of the bill
    (design 2026-10-29-usage-accounting)."""
    usage = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
    billed = _count(usage.get("billed_tokens"))
    if billed is not None:
        return int(billed)
    summary = raw.get("audit_summary") if isinstance(raw.get("audit_summary"), Mapping) else {}
    cost = summary.get("token_cost") if isinstance(summary.get("token_cost"), Mapping) else {}
    billed = _count(cost.get("billed_tokens"))
    if billed is not None:
        return int(billed)
    parts = [_count(cost.get(k)) for k in ("input_tokens", "output_tokens", "cache_read_tokens")]
    return None if any(p is None for p in parts) else int(sum(parts))
