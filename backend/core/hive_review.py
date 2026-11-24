"""When a dispatched session on a Hive asks for its review (its lessons, then ``closed``
in its run record, core/hive_records), on every carrier.

A Hive session's review waits for ``s2_jobs.REVIEW_DELAY`` of quiet, the end of a task
rather than a pause in one, for an interactive session. A **dispatched** session's turn
end is its task's end: the Host sent the task and follows the run, and a correction
comes as a new turn (whose end asks again). So its review is asked for as soon as the
turn ends, with no delay, and the run settles in minutes instead of an hour (with the
EC2 Hive held busy for that hour by the Host's idle stop, core/hive_idle).

- **dispatched** (``dispatched``): a work order prepared on this Hive names the session
  (core/hive_workspace ``bind``, at the session's first event; every dispatch carries one),
  or the Hive is an AgentCore microVM (core/hive_agentcore: it runs one dispatched
  session). Every other session, and every session off a Hive, keeps REVIEW_DELAY.
- **waiting** (``decision_waits``): a v1 pipeline run the session owns is
  ``awaiting_human`` (named by its run.json, ``pipeline_human.owned_runs``, and confirmed
  on its engine from the protected contract): a decision the Host or the person must
  answer. While it waits the review is not asked for and ``closed``
  is not sealed (core/hive_records ``on_session_job_end``), so the Host keeps showing
  the wait instead of taking the round as finished. Runs that cannot be read count as
  waiting: an unknown answer keeps the run open, and the idle hook asks again.

- ``request_review(router, sid, pm=None)`` — the turn's end (core/session_router, after
  the turn's entry is sealed): asks now when the session is dispatched and nothing
  waits (no streaming, no input wait, no background work, no open prompt or turn, no
  decision). Returns whether it asked; never raises.
- ``review_wanted(sid)`` — the idle hook's question for a dispatched session
  (core/ingestion/sources/s2_session): no job of it is open, its record is not closed,
  and no decision waits, so a turn ended without a review asked for.
- ``immediate(sid)`` — the session is reviewed this way (a dispatched one on a Hive).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def dispatched(session_id: Optional[str]) -> bool:
    """A work order prepared on this Hive names *session_id*. Blocking (reads the orders)."""
    from core import hive_records, hive_workspace
    if not hive_records.on_hive() or not hive_records.SESSION_RE.fullmatch(session_id or ""):
        return False
    ws = hive_records._workspace()
    return any((hive_workspace.load(ws, w) or {}).get("session_id") == session_id
               for w in hive_workspace._orders())


def immediate(session_id: Optional[str]) -> bool:
    """The session's review is asked for at its turn's end, with no delay: a dispatched
    session on a Hive (an AgentCore microVM runs only that one). Blocking."""
    from core import hive_agentcore, hive_records
    if not hive_records.on_hive() or not session_id:
        return False
    return hive_agentcore.active() or dispatched(session_id)


def decision_waits(session_id: Optional[str]) -> bool:
    """A pipeline run *session_id* owns waits on a decision. True when a run cannot be
    read. Blocking: call it in a thread.

    run.json (``owned_runs``) only names the candidates: anything in the workspace can
    write one. A run counts only when it has the protected contract (the app data dir,
    which no agent writes) and its engine, read from that contract and its event log alone
    (``_engine``), says it waits on a decision and is owned by the session. A run.json with
    no contract, a directory no contract could have, or a copy of a real run's directory
    elsewhere holds nothing; only the protected side failing to read counts as waiting."""
    if not session_id:
        return False
    from core import pipeline_human
    from core.run_protocol.contract import ContractError, contract_path
    try:
        for run_id in pipeline_human.owned_runs(session_id, status="awaiting_human"):
            try:
                contract_path(".", run_id)
            except ContractError:
                continue                         # a name no run is created under
            engine = _engine(run_id)
            if engine is not None and engine.owner_session() == session_id \
                    and engine.pending_decision() is not None:
                return True
        return False
    except Exception as exc:  # noqa: BLE001 — unknown: counted as waiting
        logger.warning("hive_review: the runs of %s are unreadable (%s): counted as waiting",
                       session_id, type(exc).__name__)
        return True


def _engine(run_id: str) -> Any:
    """The run's engine from its protected contract (its project) and event log, or None
    when it has no contract. Never resolved through the workspace's run directories
    (``RunEngine.load`` / ``find_run_dir``): a copy of the directory under another project
    would make that ambiguous, and what is waited on is decided by the protected side."""
    from config import get_app_data_dir
    from core import pipeline_human
    from core.run_protocol.contract import read_contract
    from core.run_protocol.engine import RunEngine
    from core.run_protocol.events import EventAuthority
    data_dir = Path(get_app_data_dir())
    contract = read_contract(data_dir, run_id)
    if contract is None:
        return None
    return RunEngine(data_dir=data_dir, workspace=Path(pipeline_human._workspace()),
                     project=str(contract.get("project") or ""), run_id=run_id,
                     authority=EventAuthority())


def _pm(pm: Any = None) -> Any:
    if pm is not None:
        return pm
    from core.permission_manager import permission_manager
    return permission_manager


async def request_review(router: Any, session_id: Optional[str], pm: Any = None) -> bool:
    """A turn of ``session_id`` ended and its entry is sealed: when the session is
    dispatched and nothing waits, request the session review now. Returns whether it
    asked."""
    from core import hive_records
    if not session_id or not hive_records.on_hive():
        return False
    try:
        from core.session_unit import SessionState
        from core import session_events
        # the file reads first, the live state after them: a turn that started during
        # the thread hop is seen, and no review is queued under it
        if not await asyncio.to_thread(_due_now, session_id):
            return False
        unit = getattr(router, "_units", {}).get(session_id)
        if unit is not None and (unit.state in (SessionState.STREAMING, SessionState.WAITING_INPUT)
                                 or getattr(unit, "has_background_work", False) is True):
            return False                         # its next turn end, or the idle hook, asks
        if _pm(pm).open_prompts(session_id) or session_events.hub().open_turn_id(session_id) is not None:
            return False
        from core.ingestion import s2_jobs
        await asyncio.to_thread(s2_jobs.request, session_id, "session_end",
                                sdk_session_id=getattr(unit, "_sdk_session_id", None),
                                delay=timedelta(0))
    except Exception as exc:  # noqa: BLE001 — the idle hook asks again (s2_session)
        logger.warning("hive_review: review of %s not requested (%s)", session_id, type(exc).__name__)
        return False
    return True


def _due_now(session_id: str) -> bool:
    return immediate(session_id) and not decision_waits(session_id)


def review_wanted(session_id: str, workspace: Optional[Path] = None) -> bool:
    """The idle hook's question for a dispatched session: no review job of it is open,
    its record is not closed and no decision waits, so a turn ended without one being
    asked for (background work or a wait at its end, a failed request). Blocking."""
    from core.ingestion import s2_jobs
    if s2_jobs.open_jobs(session_id) > 0 or decision_waits(session_id):
        return False
    from core import hive_agentcore, hive_records
    if not hive_records.SESSION_RE.fullmatch(session_id or ""):
        return True
    return not hive_agentcore._closed(workspace or hive_records._workspace(), session_id)
