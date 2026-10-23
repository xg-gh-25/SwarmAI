"""A session's state — one producer for the poll and the push (design S3).

The spinner, the "waiting for you" line, the queued badge and which prompt is
open are read from ONE snapshot of a session: ``session_state_entry(unit)``.
``GET /chat/sessions/streaming-state`` returns it for every session (the
fallback while the subscription is down), and ``publish(router, session_id)``
pushes it on the session's event hub as a ``session_state`` envelope (outside
any turn) whenever the unit changes state — so a tab learns a change when it
happens, not at its next poll.

Public: ``session_state_entry``, ``bg_snapshot``, ``publish``, ``snapshot``, ``forget``.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def bg_snapshot(unit) -> list:
    from core.background_agents import BackgroundAgents
    reg = getattr(unit, "background_agents", None)
    try:
        return reg.snapshot() if isinstance(reg, BackgroundAgents) else []
    except Exception as exc:  # noqa: BLE001 — a display aid
        logger.debug("streaming-state: background agents snapshot failed: %s", exc)
        return []


async def session_state_entry(unit) -> dict:
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
        pending_count = await session_pending.count_pending(unit.session_id)
    except Exception:
        logger.debug("pending count read failed session=%s", unit.session_id, exc_info=True)
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

    return {
        "streaming": is_streaming,
        "state": unit.state.value,
        # pid = the owned Claude subprocess pid (unit.pid → _wrapper.pid),
        # None when no live subprocess. The session-health-probe's wedged
        # check needs it to sample tree CPU; a None pid → probe skips the
        # session (fail-safe). Additive field — frontend ignores unknowns.
        "pid": unit.pid,
        # waiting_input must also reflect a re-surfaced durable request, else
        # the frontend won't render the prompt it was just handed.
        "waiting_input": unit.state.value == "waiting_input" or pending_question is not None,
        "pending_count": pending_count,
        "pending_question": pending_question,
        "last_drained_seqs": getattr(unit, "_last_drained_seqs", []),
        # Background agents (core/background_agents.py) of this daemon run: the
        # live status is also pushed as `background_agents` envelopes on /events.
        "background_agents": bg_snapshot(unit),
    }



#: per session, the number of the newest push started; an older one that finishes
#: later is dropped, so the last state published is the newest read
_LATEST: dict[str, int] = {}


def publish(router, session_id: str) -> None:
    """Push the session's state (scheduled: the entry reads the DB). Never raises."""
    unit = router.get_unit(session_id) if router is not None else None
    if unit is None:
        return
    n = _LATEST[session_id] = _LATEST.get(session_id, 0) + 1

    async def go():
        try:
            from . import session_events
            entry = await session_state_entry(unit)
            if _LATEST.get(session_id) != n:
                return   # a newer push is under way
            session_events.hub().publish(session_id, {"type": "session_state", "state": entry}, turn_id="")
        except Exception as exc:  # noqa: BLE001 — a display aid
            logger.debug("session_state.publish_failed session=%s: %s", session_id, exc)
    try:
        asyncio.get_running_loop().create_task(go())
    except RuntimeError:
        pass   # no loop (a sync test): nothing to push to


async def snapshot(router, session_id: str):
    """The state a (re)connecting subscription gets for a session: the poll's
    entry, or None when the session has no unit (the poll lists it not at all)."""
    from .session_router import PREWARM_SESSION_PREFIX
    unit = router.get_unit(session_id) if router is not None else None
    if unit is None or session_id.startswith(PREWARM_SESSION_PREFIX):
        return None
    return await session_state_entry(unit)


def forget(session_id: str) -> None:
    """Drop a released session's push counter (LifecycleManager release)."""
    _LATEST.pop(session_id, None)
