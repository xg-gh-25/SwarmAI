"""A session's state — one producer for the poll and the push (design S3).

The spinner, the "waiting for you" line, the queued badge and which prompt is
open are read from ONE snapshot of a session: ``session_state_entry(unit)``.
``GET /chat/sessions/streaming-state`` returns it for every session (the
fallback while the subscription is down), and ``publish(router, session_id)``
pushes it on the session's event hub as a ``session_state`` envelope (outside
any turn) whenever the unit changes state — so a tab learns a change when it
happens, not at its next poll.

Public: ``session_state_entry``, ``bg_snapshot``, ``publish``, ``snapshot``, ``forget``,
``ACTIVITIES``, ``derive_activity``, ``refresh_activity``.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

#: What a session is doing (docs/design/chat-turn-liveness.md S1). Not the
#: subprocess state: a turn is ``preparing`` from the moment it is admitted (slot,
#: recall, prompt build), ``starting`` while its CLI spawns, ``waiting`` once its
#: query is sent, ``streaming`` from its first produced event; ``stopping`` from a
#: user Stop until the subprocess is recycled.
ACTIVITIES = ("idle", "preparing", "starting", "waiting", "streaming",
              "waiting_input", "stopping")


def derive_activity(session_id: str, unit) -> str:
    """The session's activity from the facts that own it: the unit's Stop flag and
    state, and the hub's open turn and its phase. Synchronous, no I/O."""
    from core import session_events
    if unit is not None and getattr(unit, "_stopping", False) is True:
        return "stopping"
    state = getattr(getattr(unit, "state", None), "value", None)
    if state == "waiting_input":
        return "waiting_input"
    phase = session_events.hub().open_phase(session_id)
    if phase:
        return phase
    if state == "streaming":
        return "streaming"   # a CLI-initiated turn before its first event is published
    return "idle"


def refresh_activity(session_id: str, unit, *, publish: bool = True) -> "tuple[str, int]":
    """Derive the activity; when it changed, record it with a new seq and (publish)
    push a small ``session_activity`` envelope (outside any turn: no DB read, the
    full ``session_state`` snapshot goes out at state boundaries). Returns the
    current (activity, seq). Never raises."""
    from core import session_events
    hub = session_events.hub()
    try:
        activity = derive_activity(session_id, unit)
        seq = hub.set_activity(session_id, activity)
        if seq is not None and publish and not session_id.startswith("prewarm-"):
            hub.publish(session_id, {"type": "session_activity", "activity": activity,
                                     "activity_seq": seq}, turn_id="")
    except Exception as exc:  # noqa: BLE001 — a display fact must never break a turn
        logger.warning("session_state.activity_refresh_failed session=%s: %s", session_id, exc)
    return hub.activity_of(session_id)


def bg_snapshot(unit) -> list:
    from core.background_agents import BackgroundAgents
    reg = getattr(unit, "background_agents", None)
    try:
        return reg.snapshot() if isinstance(reg, BackgroundAgents) else []
    except Exception as exc:  # noqa: BLE001 — a display aid
        logger.debug("streaming-state: background agents snapshot failed: %s", exc)
        return []


async def session_state_entry(unit, router=None) -> dict:
    """One session's state: what the spinner, the "waiting for you" line and the
    prompts read. The ONE producer — the streaming-state endpoint and the
    `session_state` envelope the subscription carries (``publish`` below) are
    both this (design S3)."""
    # streaming is simply state==STREAMING; a client disconnect does not
    # change the unit's state (the turn runs to its own end).
    is_streaming = unit.state.value == "streaming"
    # Root-1 SSOT Phase 2 (L5): the frontend mirror reads these directly
    # instead of inferring. waiting_input surfaces a (possibly SSE-lost)
    # AskUserQuestion; pending_count drives the "queued" badge; pending_question
    # lets the FE RE-RENDER the question from authoritative state even when the
    # original ask_user_question SSE event was dropped (F5).
    try:
        from core import session_pending
        pending = await session_pending.list_pending(unit.session_id)
        # the list is capped: count only when it is full (one more read, rare)
        pending_count = (len(pending) if len(pending) < session_pending._LIST_MAX
                         else await session_pending.count_pending(unit.session_id))
    except Exception:
        logger.debug("pending list read failed session=%s", unit.session_id, exc_info=True)
        pending = []
        pending_count = 0

    # The unit's surfaced prompt (WAITING_INPUT), else the oldest open prompt
    # on the session's board -- the same source the session's stream reads,
    # so the tab and the stream can never disagree about what is open.
    pending_question = (
        getattr(unit, "_pending_question", None)
        if unit.state.value == "waiting_input" else None
    )
    if pending_question is None:
        try:
            from core.permission_manager import permission_manager as _pm
            for item in _pm.open_prompts(unit.session_id):
                if item.get("kind") == "ask_user_question":
                    pending_question = {
                        "tool_use_id": item.get("tool_use_id"),
                        "questions": item.get("questions", []),
                    }
                else:
                    pending_question = {
                        "tool_use_id": item.get("requestId"),
                        "request_id": item.get("requestId"),
                        "tool_call_id": item.get("toolUseId"),
                        "tool_name": item.get("toolName", "Bash"),
                        "tool_input": item.get("toolInput", {}),
                        "reason": item.get("reason", ""),
                        "options": item.get("options", ["approve", "deny"]),
                    }
                break
        except Exception:
            logger.debug("open prompt read failed session=%s", unit.session_id, exc_info=True)
            pending_question = None

    activity, activity_seq = refresh_activity(unit.session_id, unit)
    return {
        "streaming": is_streaming,
        "state": unit.state.value,
        # Whether a turn is in progress and in which step (docs/design/
        # chat-turn-liveness.md S1): ACTIVITIES, with a seq that only grows, so a
        # tab keeps the newest of a push and a slower poll. `streaming` above keeps
        # its old meaning (the subprocess streams) for the clients that read it.
        "activity": activity,
        "activity_seq": activity_seq,
        # pid = the owned Claude subprocess pid (unit.pid → _wrapper.pid),
        # None when no live subprocess. The session-health-probe's wedged
        # check needs it to sample tree CPU; a None pid → probe skips the
        # session (fail-safe). Additive field — frontend ignores unknowns.
        "pid": unit.pid,
        # waiting_input must also reflect a re-surfaced durable request, else
        # the frontend won't render the prompt it was just handed.
        "waiting_input": unit.state.value == "waiting_input" or pending_question is not None,
        "pending_count": pending_count,
        # the queue itself, FIFO (core/session_pending.list_pending); `claimed` rows
        # are being delivered and can no longer be cancelled
        "pending": pending,
        "pending_question": pending_question,
        "last_drained_seqs": getattr(unit, "_last_drained_seqs", []),
        # Background agents (core/background_agents.py) of this daemon run: the
        # live status is also pushed as `background_agents` envelopes on /events.
        "background_agents": bg_snapshot(unit),
        # whether the session still has background work (a sub-agent, a background
        # command, or a turn the CLI started and has not delivered yet): a Swarm Out Host
        # reads a session that is idle but has this as still running, not finished
        "background_work": getattr(unit, "has_background_work", False) is True,
        # steers written into the running turn and not yet read, and the turn's
        # segment keys (design chat-steer D5): only when there are any, so a turn
        # without a steer keeps the snapshot it always had
        **_steer_snapshot(unit),
        **_queued_reason(unit, router, pending),
    }


def _queued_reason(unit, router, pending: list) -> dict:
    """Why the session's queue waits, while it holds a message not yet taken
    (frontend design 2026-10-25 §4): what the running turn waits on (``question`` /
    ``permission``) or ``busy``. The send's receipt carries the reason only to the
    tab that sent it; this one follows the state (a question answered) and reaches
    every tab and a reload. Only with the steer switch on: off, the snapshot keeps
    the shape it always had."""
    if router is None or not any(not p.get("claimed") for p in pending):
        return {}
    try:
        if not router._steer_enabled():
            return {}
        return {"queued_reason": router._steer_blocker(unit) or "busy"}
    except Exception:  # noqa: BLE001 — a display aid
        logger.debug("queued_reason failed session=%s", getattr(unit, "session_id", None), exc_info=True)
        return {}


def _steer_snapshot(unit) -> dict:
    out: dict = {}
    reader = getattr(unit, "_stream_reader", None)
    rows = getattr(unit, "_steer_rows", None) or {}
    waiting = {ts.uuid for ts in (reader._claims.values() if reader is not None else ())
               if getattr(ts, "kind", None) == "steer"}
    steering = [{"client_id": cid, "preview": preview}
                for uuid, (_row, cid, preview) in rows.items() if uuid in waiting]
    if steering:
        out["steering"] = steering
    segments = getattr(unit, "_turn_segments", None)
    if isinstance(segments, list) and segments:
        out["segments"] = list(segments)
    return out



#: per session, the number of the newest push started; an older one that finishes
#: later is dropped, so the last state published is the newest read
_LATEST: dict[str, int] = {}
#: sessions with a push scheduled whose read has not started: a burst of changes
#: (several queued sends) shares that one read instead of one read each
_SCHEDULED: dict[str, "asyncio.Task"] = {}


def _on_running_loop(task: "asyncio.Task") -> bool:
    """The task belongs to the loop running now (one left on a closed loop never
    runs, so it must not hold back the next push)."""
    try:
        return task.get_loop() is asyncio.get_running_loop()
    except RuntimeError:
        return False


def publish(router, session_id: str) -> None:
    """Push the session's state (scheduled: the entry reads the DB). Never raises."""
    unit = router.get_unit(session_id) if router is not None else None
    if unit is None:
        return
    waiting = _SCHEDULED.get(session_id)
    if waiting is not None and not waiting.done() and _on_running_loop(waiting):
        return   # the scheduled push has not read yet: it will see this change
    n = _LATEST[session_id] = _LATEST.get(session_id, 0) + 1

    async def go():
        _SCHEDULED.pop(session_id, None)   # changes from here on schedule a new push
        try:
            from . import session_events
            entry = await session_state_entry(unit, router)
            if _LATEST.get(session_id) != n:
                return   # a newer push is under way
            session_events.hub().publish(session_id, {"type": "session_state", "state": entry}, turn_id="")
        except Exception as exc:  # noqa: BLE001 — a display aid
            logger.debug("session_state.publish_failed session=%s: %s", session_id, exc)
    try:
        _SCHEDULED[session_id] = asyncio.get_running_loop().create_task(go())
    except RuntimeError:
        pass   # no loop (a sync test): nothing to push to


async def snapshot(router, session_id: str):
    """The state a (re)connecting subscription gets for a session: the poll's
    entry, or None when the session has no unit (the poll lists it not at all). A
    session relayed from a Hive (core/swarm_out_relay) gets the relay's cached entry."""
    from .session_router import PREWARM_SESSION_PREFIX
    from . import swarm_out_relay
    relayed = swarm_out_relay.manager().state_entry(session_id)
    if relayed is not None:
        return relayed   # a Hive session relayed here: the Hive's state, as last read
    unit = router.get_unit(session_id) if router is not None else None
    if unit is None or session_id.startswith(PREWARM_SESSION_PREFIX):
        return None
    return await session_state_entry(unit, router)


def forget(session_id: str) -> None:
    """Drop a released session's push counter (LifecycleManager release)."""
    _LATEST.pop(session_id, None)
