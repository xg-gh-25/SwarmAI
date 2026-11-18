"""When each stage of a run was entered and where its time went, from the run's events.

A read model for the Pipelines overlay's Line (design: the factory-line overlays,
Knowledge/Designs/2026-11-18-swarmai-factory-line-overlays-mockup.html). It replays the
same validated events the fold reads, one at a time, with the lifecycle's own
``_initial`` / ``_apply``, and watches the projection's ``stage`` and ``status`` change.
So every place that moves a run to another stage (the next stage, a REVIEW reopen, a
DELIVER reopen, a restore) is seen without touching the state machine: the fold and its
result stay exactly as they are.

For each stage the run entered:

- ``entered_at``: the ``at`` of the first event after which the run was in that stage;
- ``active_s``: seconds the run spent in it while ``active`` or ``reflecting``;
- ``wait_s``: seconds it spent in it while ``awaiting_human`` (a decision is open: the
  person owes an answer). ``paused`` (a user stop, a lost session) counts as neither.

An interval is measured between two consecutive events' ``at``; one whose ends cannot be
read (no time, not ISO, no UTC offset) is left out, never estimated. A run still going is measured up to its last event,
never up to "now". A stage the run never entered has no entry.

Exports: stage_timeline
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from core.run_protocol import lifecycle

#: the statuses in which an agent is working on the run's current stage
_WORKING = frozenset({"active", "reflecting"})
#: the status in which the run waits on a person's answer
_WAITING = "awaiting_human"


def _when(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # a time without an offset cannot be compared with the others: unreadable
    return when if when.tzinfo is not None else None


def stage_timeline(events: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per stage entered: ``{"entered_at", "active_s", "wait_s"}`` (see the module doc).

    *events* is the run's verified chain, as ``lifecycle.fold`` takes it; a chain the
    lifecycle refuses raises ``lifecycle.LifecycleError`` (the caller leaves the timeline
    out)."""
    if not events or events[0].get("kind") != "run_create":
        return {}
    run = lifecycle._initial(events[0])      # noqa: SLF001 -- the fold's own first step
    out: dict[str, dict[str, Any]] = {}

    def enter(stage: str, at: Any) -> None:
        if stage not in out:
            out[stage] = {"entered_at": at if isinstance(at, str) and at else None,
                          "active_s": 0.0, "wait_s": 0.0}

    enter(run["stage"], events[0].get("at"))
    prev_at = _when(events[0].get("at"))
    for event in events[1:]:
        # the interval since the previous event belongs to the state before this one
        now = _when(event.get("at"))
        stage, status = run["stage"], run["status"]
        if prev_at is not None and now is not None and now >= prev_at:
            seconds = (now - prev_at).total_seconds()
            if status in _WORKING:
                out[stage]["active_s"] += seconds
            elif status == _WAITING:
                out[stage]["wait_s"] += seconds
        lifecycle._apply(run, event)          # noqa: SLF001 -- the fold's own step
        if run["stage"] != stage:
            enter(run["stage"], event.get("at"))
        prev_at = now
    for entry in out.values():
        entry["active_s"] = round(entry["active_s"], 1)
        entry["wait_s"] = round(entry["wait_s"], 1)
    return out
