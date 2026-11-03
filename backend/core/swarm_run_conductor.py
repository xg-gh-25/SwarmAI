"""The Conductor's part in a Swarm Run that is not a wake (design
docs/design/2026-10-31-swarm-run.md §5.7; impl docs/design/2026-11-03-swarm-run-impl.md §1, §6).

Everything about a child's execution is its lead run's while that run goes on, and the
Conductor's once it does not: nothing of it is handed to the person (XG 2026-11-03: the
person reviews the requirement and judges the result; discarding, re-running, taking
over and cleaning up are the agents'). Two cases close a child here:

- its lead run has ended (landed, abandoned or failed);
- its lead run cannot be read (its audit log is gone or damaged) and has stayed so for
  ORPHAN_AFTER_S: nobody can command the child any more.

Then a child still running or waiting is stopped on its Hive and recorded as handled by
its Swarm Run (``swarm_out_settle.set_handled``, ``by="run_end"``). A child whose Hive
did not answer (state unknown) waits for a refresh that knows its state, except one its
Hive named no session for (``no_session_id``): there is nothing to stop, so it is
handled. A child closed here is kept stopped: seen running again (a turn that resumed,
an answer sent before the run ended that the Hive took afterwards) it is stopped again.
It settles once it is not running, a wait left open on its Hive included (nothing will
answer it), as a discarded run does; its Hive is then told to release the workspace by
the rules every run follows (core/swarm_out_settle.annotate).

Run after each refresh of the runs (core/swarm_out.run_refresh_loop), on the snapshot it
just made; it does not poll a Hive itself. Deterministic, idempotent and it never raises.

- ``end_children(data)`` — one pass; returns the dispatch ids it handled.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: how long a lead run stays unreadable before its children are closed as orphans (a
#: torn read of a log being written is not the end of a run)
ORPHAN_AFTER_S = 300.0
#: run_id -> when it was first seen unreadable (forgotten once it reads again)
_UNREADABLE_SINCE: dict[str, float] = {}


def _closed_run(run_id: str, now: float) -> Optional[str]:
    """Why *run_id*'s children are closed now (``ended`` / ``orphaned``), else None."""
    from core import swarm_run
    lead = swarm_run.lead(run_id)
    if lead is not None:
        _UNREADABLE_SINCE.pop(run_id, None)
        return "ended" if lead.ended else None
    since = _UNREADABLE_SINCE.setdefault(run_id, now)
    return "orphaned" if now - since >= ORPHAN_AFTER_S else None


def _children_to_close(data: Any, now: Optional[float] = None) -> tuple[list[dict], list[str], dict[str, str]]:
    """(children nothing has closed yet whose run is closed, children closed by their
    run that are running again, {run_id: why} of the closed runs). Reads the lead runs'
    audit logs and the settle records: called off the event loop."""
    from core import swarm_out_settle, swarm_run
    now = time.time() if now is None else now
    to_close: list[dict] = []
    running_again: list[str] = []
    why_of: dict[str, Optional[str]] = {}
    for r in (data or {}).get("runs") or []:
        if not swarm_run.is_child(r) or not r.get("dispatch_id"):
            continue
        run_id = str(r.get("parent_run"))
        if run_id not in why_of:
            why_of[run_id] = _closed_run(run_id, now)
        rec = swarm_out_settle.load(r["dispatch_id"])
        if rec.get("settled") or rec.get("abandoned_at"):
            continue
        if rec.get("handled_at"):
            if (rec.get("handled") or {}).get("by") == "run_end" and r.get("state") == "running":
                running_again.append(r["dispatch_id"])
            continue
        if why_of[run_id]:
            to_close.append(r)
    return to_close, running_again, {k: v for k, v in why_of.items() if v}


async def end_children(data: Any) -> list[str]:
    """Close the children of every lead run that has ended or cannot be read, and keep
    them stopped (see the module doc)."""
    from core import swarm_out, swarm_out_settle, swarm_run
    done: list[str] = []
    try:
        rows, running_again, closed = await asyncio.to_thread(_children_to_close, data)
    except Exception as exc:  # noqa: BLE001 — the next refresh looks again
        logger.warning("swarm_run_conductor: reading the children failed (%s)", type(exc).__name__)
        return done
    for r in rows:
        did = r["dispatch_id"]
        state = r.get("state")
        no_session = state == "unknown" and r.get("state_reason") == "no_session_id"
        if state not in ("running", "waiting", "stopped") and not no_session:
            # its Hive did not answer: a turn may still be going there. Closed once its
            # state is known (a later refresh), never recorded as handled while it may run
            continue
        run_id = str(r.get("parent_run"))
        note = (f"its Swarm Run {run_id} ended" if closed.get(run_id) == "ended"
                else f"its Swarm Run {run_id} can no longer be read: nobody can command it")
        try:
            if state in ("running", "waiting"):
                await swarm_out.stop_turn(did)
            await asyncio.to_thread(swarm_out_settle.set_handled, did, note, None, by="run_end")
            done.append(did)
        except Exception as exc:  # noqa: BLE001 — the next refresh tries again
            logger.warning("swarm_run_conductor: closing %s failed (%s)", did, type(exc).__name__)
    for did in running_again:
        try:
            await swarm_out.stop_turn(did)
        except Exception as exc:  # noqa: BLE001 — the next refresh tries again
            logger.warning("swarm_run_conductor: stopping %s again failed (%s)", did, type(exc).__name__)
    for run_id in closed:          # a closed run's children no longer need its frozen Brain
        await asyncio.to_thread(swarm_run.drop_freeze, run_id)
    return done
