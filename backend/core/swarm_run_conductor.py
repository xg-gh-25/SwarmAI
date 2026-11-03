"""The Conductor's part in a Swarm Run that is not a wake (design
docs/design/2026-10-31-swarm-run.md §5.7; impl docs/design/2026-11-03-swarm-run-impl.md §1).

When a lead run ends (landed, abandoned or failed), each of its children that nothing
has closed yet goes with it: a child still running or waiting is stopped on its Hive,
then it is recorded as handled by its Swarm Run (``swarm_out_settle.set_handled``,
``by="run_end"``). The next refresh settles it as ``handled`` once it has stopped and its
lessons are in, and its Hive is told to release the workspace, by the rules every run
follows (core/swarm_out_settle.annotate).

Run after each refresh of the runs (core/swarm_out.run_refresh_loop), on the snapshot it
just made; it does not poll a Hive itself. Deterministic, idempotent (a child already
handled, discarded or settled is skipped) and it never raises.

- ``end_children(data)`` — one pass; returns the dispatch ids it handled.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _open_children_of_ended_runs(data: Any) -> list[dict]:
    from core import swarm_out_settle, swarm_run
    out = []
    for r in (data or {}).get("runs") or []:
        if not swarm_run.is_child(r) or not r.get("dispatch_id"):
            continue
        lead = swarm_run.lead(r.get("parent_run"))
        if lead is None or not lead.ended:
            continue
        rec = swarm_out_settle.load(r["dispatch_id"])
        if rec.get("handled_at") or rec.get("abandoned_at") or rec.get("settled"):
            continue
        out.append(r)
    return out


async def end_children(data: Any) -> list[str]:
    """Close the children of every lead run that has ended (see the module doc)."""
    from core import swarm_out, swarm_out_settle
    done: list[str] = []
    try:
        rows = await asyncio.to_thread(_open_children_of_ended_runs, data)
    except Exception as exc:  # noqa: BLE001 — the next refresh looks again
        logger.warning("swarm_run_conductor: reading the children failed (%s)", type(exc).__name__)
        return done
    for r in rows:
        did = r["dispatch_id"]
        try:
            if r.get("state") in ("running", "waiting"):
                # stopped first: once handled, the run counts as accepted and takes no stop
                await swarm_out.stop_run(did)
            await asyncio.to_thread(swarm_out_settle.set_handled, did,
                                    f"its Swarm Run {r.get('parent_run')} ended", None, by="run_end")
            done.append(did)
        except Exception as exc:  # noqa: BLE001 — the next refresh tries again
            logger.warning("swarm_run_conductor: closing %s failed (%s)", did, type(exc).__name__)
    return done
