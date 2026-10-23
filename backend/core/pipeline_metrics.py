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
- ``tokens`` — measured input+output tokens, or None when never measured.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from core.run_protocol import lifecycle

_DONE = frozenset({"completed", "degraded"})
NEEDS_YOU_PAUSE_KINDS = frozenset({"decision", "stopped", "worktree", "idle"})


def is_v1(run_id: str) -> bool:
    """A v1 run: its protected contract exists (never inferred from run.json)."""
    from config import get_app_data_dir
    from core.run_protocol.runner import is_v1_run
    try:
        return is_v1_run(get_app_data_dir(), run_id)
    except Exception:  # noqa: BLE001 — an unreadable contract is not a v1 run
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


def tokens(raw: Mapping[str, Any]) -> Optional[int]:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        return None
    values = [usage.get("input_tokens"), usage.get("output_tokens")]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return None
    return int(sum(values))
