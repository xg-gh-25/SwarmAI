"""The Conductor's part in a Swarm Run that is not a wake (design
docs/design/2026-10-31-swarm-run.md §5.7; impl docs/design/2026-11-03-swarm-run-impl.md §1).

When a lead run ends (landed, abandoned or failed), each of its children that nothing
has closed yet goes with it: a child still running or waiting is stopped on its Hive,
then it is recorded as handled by its Swarm Run (``swarm_out_settle.set_handled``,
``by="run_end"``). The next refresh settles it as ``handled`` once it has stopped and its
lessons are in, and its Hive is told to release the workspace, by the rules every run
follows (core/swarm_out_settle.annotate).

Run after each refresh of the runs (core/swarm_out.run_refresh_loop), on the snapshot it
just made; it does not poll a Hive itself. A child whose Hive did not answer (state
unknown) waits for a refresh that knows its state, except one its Hive named no session
for (``no_session_id``): there is nothing to stop, so it is handled; a session that turns
up later ends its turn and then settles by the same rules. One closed by its run's end settles
once it is not running, a wait left open on its Hive included (nothing will answer it),
as a discarded run does. Deterministic, idempotent (a child already
handled, discarded or settled is skipped) and it never raises.

- ``end_children(data)`` — one pass; returns the dispatch ids it handled.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _open_children_of_ended_runs(data: Any) -> tuple[list[dict], set[str]]:
    """The children nothing has closed yet whose lead run has ended, and the ended lead
    runs. Reads the lead runs' audit logs: called off the event loop."""
    from core import swarm_out_settle, swarm_run
    out, ended = [], set()
    for r in (data or {}).get("runs") or []:
        if not swarm_run.is_child(r) or not r.get("dispatch_id"):
            continue
        lead = swarm_run.lead(r.get("parent_run"))
        if lead is None or not lead.ended:
            continue
        ended.add(lead.run_id)
        rec = swarm_out_settle.load(r["dispatch_id"])
        if rec.get("handled_at") or rec.get("abandoned_at") or rec.get("settled"):
            continue
        out.append(r)
    return out, ended


async def end_children(data: Any) -> list[str]:
    """Close the children of every lead run that has ended (see the module doc)."""
    from core import swarm_out, swarm_out_settle, swarm_run
    done: list[str] = []
    try:
        rows, ended = await asyncio.to_thread(_open_children_of_ended_runs, data)
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
        try:
            if state in ("running", "waiting"):
                # stopped first: once handled, the run counts as accepted and takes no stop
                await swarm_out.stop_run(did)
            await asyncio.to_thread(swarm_out_settle.set_handled, did,
                                    f"its Swarm Run {r.get('parent_run')} ended", None, by="run_end")
            done.append(did)
        except Exception as exc:  # noqa: BLE001 — the next refresh tries again
            logger.warning("swarm_run_conductor: closing %s failed (%s)", did, type(exc).__name__)
    for run_id in ended:           # an ended run's children no longer need its frozen Brain
        await asyncio.to_thread(swarm_run.drop_freeze, run_id)
    return done
