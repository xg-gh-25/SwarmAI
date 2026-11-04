"""A Hive's boot closes the turns its daemon's death left open (design
docs/design/2026-11-04-kirocrew-borrow.md §3.B1).

A daemon killed mid-turn (OOM, an instance restart) never runs ``_published_turn``'s
``finally`` (core/session_router): the turn has no ``turn_outcome`` and is not sealed into
its run record (core/hive_records), so the Host saw ``no_record`` and waited
``swarm_out_settle.INTERRUPTED_AFTER_S`` before calling it interrupted.

``close_open_turns(router)`` runs once, awaited, in the lifespan of a Hive
(``SWARMAI_MODE=hive``), before ``reopen_dangling_claims`` and before the Swarm Out bridge
starts: no turn can start yet. For each order (core/hive_workspace) bound to a session it
reads the session's newest rows, as ``SessionRouter._seal_turn_record`` does, and acts
only when all of these hold:

- the last turn (``swarm_out_results._is_turn_start``; one longer than the rows read
  starts before the oldest of them) started before this process did (``STARTED_AT``,
  taken once at import): a turn of this boot is never touched, whatever the start order
  becomes;
- that turn has no ``turn_outcome`` and the run record's sealed marker
  (``hive_records._sealed_through``) is behind the newest row (something was written
  and never sealed); or its last outcome is ``paused`` and something was written after
  it (its continuation, an answer or a permission writing into the same turn, was cut
  off); or its last outcome is ``paused``, nothing came after it and no pipeline
  decision of the session waits (``pipeline_human.pending_decisions``; that one is read
  from the run's state and still delivered). A paused turn was sealed at its own end,
  so the marker does not tell; its question card died with the process (the bridge's
  reconcile closes it): nobody can answer it. When the decisions cannot be read, such a
  turn is left as it is;
- nothing started in the session while its repositories were read (the newest row is
  the same and no turn writer runs there).

A turn whose last outcome is terminal (its own, or a ``daemon_stopped`` an earlier boot
wrote) while the marker is behind the newest row lost its seal: the daemon died between
the outcome and the seal, or the seal failed. It is sealed again, with no outcome written
(``unsealed``). A close or a seal counts only once the marker reached the newest row
(``_seal_and_check``), so a failed seal is retried at the next boot.

Otherwise it writes ``turn_outcome.daemon_stopped_block`` with the ``repos`` a normal end
computes (``SessionRouter._task_repo_facts``: the killed turn's commits still come back)
through a ``TurnWriter``, like a turn's own outcome, and seals the turn with
``SessionRouter._seal_turn_record`` (``hive_records.seal_turn`` under the session's seal
lock). The outcome's ``at`` is when the turn last wrote (``_ended_at``), not the boot's;
its gate refusals are marked partial unless the turn only sat on its question. A second run changes nothing: the turn's last outcome is then
``daemon_stopped`` (never written again) and the marker is at the newest row. Rows that cannot be read:
that session is skipped with a warning. It never reruns anything; a rerun stays the
dispatcher's call.

Public: ``STARTED_AT``, ``close_open_turns``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: when this process started, in the messages table's clock (naive local time)
STARTED_AT = datetime.now()


def _blocks(row: dict) -> list[dict]:
    c = row.get("content")
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def _open_turn(rows: list[dict], marker: Optional[str], paused_closes: bool) -> Optional[str]:
    """How the newest turn in *rows* (chronological, the newest ``TURN_ROWS``) was left
    open by the daemon's death, or None: ``open`` (no outcome, rows never sealed),
    ``continued`` (it paused, then its continuation — an answer or a permission, which
    writes into the same turn — was cut off), ``paused`` (it sat on its question),
    ``unsealed`` (it ended, but its seal never ran or failed: seal only).
    ``paused_closes``: no pipeline decision of the session waits (known)."""
    from core import hive_records
    from core.swarm_out_results import _is_turn_start
    newest = rows[-1].get("id") if rows and isinstance(rows[-1], dict) else None
    if not isinstance(newest, str):
        return None
    start = max((i for i, r in enumerate(rows) if _is_turn_start(r)), default=-1)
    if start < 0 and len(rows) < hive_records.TURN_ROWS:
        return None                                   # no turn in the session
    # a turn longer than the rows read started before the oldest of them
    # (hive_records.turn_slice reads it the same way)
    started = hive_records._at(rows[max(start, 0)].get("created_at"))
    if started is None or started >= STARTED_AT:
        return None                                   # a turn of this boot
    blocks = [b for r in rows[start + 1:] if r.get("role") == "assistant" for b in _blocks(r)]
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "turn_outcome"), default=-1)
    if last < 0:
        return "open" if marker != newest else None   # rows written and never sealed
    if blocks[last].get("ended") != "paused":
        # the turn ended (or a boot closed it) but its seal never ran or failed: seal
        # what is there, writing no outcome of its own
        return "unsealed" if marker != newest else None
    # a turn that paused was sealed at its own end, so the marker does not tell. Anything
    # written after its outcome is the continuation (the question was answered): cut off.
    # Else it sat on its question, which died with the process unless a pipeline
    # decision carries it.
    if last < len(blocks) - 1:
        return "continued"
    return "paused" if paused_closes else None


def _ended_at(rows: list[dict]) -> Optional[str]:
    """When the cut-off turn last wrote, in UTC like an outcome's: its newest row's time
    (a row appended to carries ``updated_at``), never before the turn's last outcome."""
    from core import hive_records
    times = [hive_records._at(rows[-1].get(k)) for k in ("created_at", "updated_at") if rows[-1].get(k)]
    out = [t.astimezone(timezone.utc) for t in times if t is not None]
    for r in rows:
        for b in _blocks(r) if r.get("role") == "assistant" else []:
            if b.get("type") == "turn_outcome" and isinstance(b.get("at"), str):
                try:
                    t = datetime.fromisoformat(b["at"].replace("Z", "+00:00"))
                except ValueError:
                    continue
                out.append(t if t.tzinfo else t.astimezone(timezone.utc))
    return max(out).isoformat() if out else None


async def _close_one(router: Any, workspace: Any, session_id: str, waiting: Optional[set]) -> bool:
    from core import hive_records, turn_outcome
    from core.turn_writer import TurnWriter
    from database import db
    # rows that cannot be read raise: close_open_turns skips the session with a warning
    rows = await db.messages.list_by_session_paginated(session_id, limit=hive_records.TURN_ROWS)
    rows = [r for r in rows or [] if isinstance(r, dict)]
    marker = await asyncio.to_thread(
        lambda: hive_records._sealed_through(hive_records.session_dir(workspace, session_id)))
    how = _open_turn(rows, marker, waiting is not None and session_id not in waiting)
    if how is None:
        return False
    if how == "unsealed":
        return await _seal_and_check(router, workspace, session_id, how)
    repos = await router._task_repo_facts(session_id)
    # nothing may have started in the session meanwhile (the start order of the boot keeps
    # turns out; this keeps a row of the outcome out of a turn that started anyway)
    newest = await db.messages.list_by_session_paginated(session_id, limit=1)
    if TurnWriter.running(session_id) is not None or not newest \
            or newest[-1].get("id") != rows[-1].get("id"):
        logger.warning("hive_boot: %s moved on while its open turn was read: left as it is", session_id)
        return False
    writer = TurnWriter(router, session_id, row_key=None, label="daemon-stopped")
    try:
        written = await writer.record_outcome(
            # a turn that sat on its question counted its refusals in its paused outcome
            turn_outcome.daemon_stopped_block(repos, at=_ended_at(rows), partial=how != "paused"))
    finally:
        await writer.close()
    if not written:
        return False
    return await _seal_and_check(router, workspace, session_id, how)


async def _seal_and_check(router: Any, workspace: Any, session_id: str, how: str) -> bool:
    """Seal the session's turn, then count it only when the record's sealed marker reached
    the newest row: ``_seal_turn_record`` logs and swallows its own failures, so a seal that
    did not happen is seen here and the next boot seals it again (``unsealed``)."""
    from core import hive_records
    from database import db
    await router._seal_turn_record(session_id, datetime.now().isoformat())
    newest = await db.messages.list_by_session_paginated(session_id, limit=1)
    marker = await asyncio.to_thread(
        lambda: hive_records._sealed_through(hive_records.session_dir(workspace, session_id)))
    if not newest or marker != newest[-1].get("id"):
        logger.warning("hive_boot: the %s turn of %s was not sealed: the next boot tries again",
                       how, session_id)
        return False
    logger.info("hive_boot: %s the %s turn of %s", "sealed" if how == "unsealed" else
                "closed as daemon_stopped", how, session_id)
    return True


async def close_open_turns(router: Any) -> int:
    """Close each turn the daemon's death left open (module doc); how many it closed.
    Only on a Hive. Never raises: a failure is logged per session."""
    from core import hive_records, hive_workspace, pipeline_human
    from core.project_registry import get_swarmws
    if not hive_records.on_hive():
        return 0
    if router is None:
        logger.warning("hive_boot: no session router: open turns are left open")
        return 0
    workspace = get_swarmws()

    def bound_sessions() -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for wid in hive_workspace._orders():
            sid = (hive_workspace.load(workspace, wid) or {}).get("session_id")
            if isinstance(sid, str) and hive_records.SESSION_RE.fullmatch(sid) and sid not in seen:
                seen.add(sid)
                out.append(sid)
        return out

    try:
        sessions = await asyncio.to_thread(bound_sessions)
    except Exception as exc:  # noqa: BLE001 — never holds the boot
        logger.warning("hive_boot: the orders could not be read (%s)", type(exc).__name__)
        return 0
    if not sessions:
        return 0
    waiting: Optional[set] = None
    try:
        waiting = {d.get("session_id") for d in await asyncio.to_thread(pipeline_human.pending_decisions)
                   if isinstance(d, dict)}
    except Exception as exc:  # noqa: BLE001 — paused turns are then left as they are
        logger.warning("hive_boot: pipeline decisions unreadable (%s): paused turns are left open",
                       type(exc).__name__)
    closed = 0
    for sid in sessions:
        try:
            closed += await _close_one(router, workspace, sid, waiting)
        except Exception as exc:  # noqa: BLE001 — one session never stops the others
            logger.warning("hive_boot: the open turn of %s, if any, was left open (%s)", sid,
                           type(exc).__name__)
    return closed
