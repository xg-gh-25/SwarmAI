"""Resume a turn the backend dropped because its SSE stream disconnected.

Only a user Stop stops a turn. When the SSE client disconnects for any other
reason (the frontend's stall timer after a 322 s daemon freeze, a network drop,
a reload), chat.py's disconnect recovery flushes the CLI and the turn used to end
there with "Stopped · the last call did not finish" (97df0539, 2026-10-21 10:53).
Now the router schedules a continuation: after a grace period it sends the
continuation prompt through ``run_conversation`` with ``--resume``, without a
user row, so the chat shows the same turn carrying on. The frontend's
streaming-state reconcile picks it up like any backend-driven turn.

Rules:
- a user Stop (``mark_user_stop``) or a newer message (``_send_generation``
  moved) cancels it; so does a released unit;
- at most ``LIMIT`` continuations per user turn; past that the turn stops with a
  persisted ``turn_notice`` ``gave_up`` / ``disconnected``;
- one pending continuation per unit.

Public: ``schedule(router, unit)``, ``mark_user_stop(unit)``, ``LIMIT``,
``GRACE_SECONDS``, ``PROMPT``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

LIMIT = 3
GRACE_SECONDS = 5.0
#: the frontend aborts the SSE fetch BEFORE it posts /chat/stop; a stop landing
#: this long after the disconnect still counts as the reason for it
STOP_WINDOW_SECONDS = 30.0
PROMPT = "Continue the task where you left off."
_FLUSH_WAIT_SECONDS = 15.0


def mark_user_stop(unit: Any) -> None:
    """The user pressed Stop: no continuation for this turn."""
    unit._user_stop_at = time.monotonic()
    task = getattr(unit, "_auto_continue_task", None)
    if task is not None and not task.done():
        task.cancel()


def _stopped_by_user(unit: Any, since: float) -> bool:
    at = getattr(unit, "_user_stop_at", None)
    return isinstance(at, (int, float)) and at >= since - STOP_WINDOW_SECONDS


def schedule(router: Any, unit: Any) -> bool:
    """Schedule one continuation for ``unit``'s dropped turn. False if not scheduled."""
    task = getattr(unit, "_auto_continue_task", None)
    if task is not None and not task.done():
        return False
    gen = getattr(unit, "_send_generation", 0)
    # A disconnect of a turn the user started resets the budget; one of our own
    # continuations dropping again counts against it.
    if gen != getattr(unit, "_auto_continue_gen", None):
        unit._auto_continue_count = 0
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    unit._auto_continue_task = loop.create_task(_run(router, unit, gen, time.monotonic()))
    return True


async def _run(router: Any, unit: Any, gen: int, since: float) -> None:
    sid = unit.session_id
    try:
        flush = getattr(unit, "_pipe_flush_task", None)
        if flush is not None and not flush.done():
            await asyncio.wait({flush}, timeout=_FLUSH_WAIT_SECONDS)
        await asyncio.sleep(GRACE_SECONDS)
        if router.get_unit(sid) is not unit:
            return  # released / replaced
        if _stopped_by_user(unit, since):
            logger.info("auto_continue.skip session_id=%s reason=user_stop", sid)
            return
        if getattr(unit, "_send_generation", 0) != gen:
            logger.info("auto_continue.skip session_id=%s reason=newer_message", sid)
            return
        if getattr(unit, "has_outstanding_tool_use", False):
            return  # a question / permission is waiting on the user
        state = getattr(getattr(unit, "state", None), "value", None)
        if state not in ("idle", "cold"):
            logger.info("auto_continue.skip session_id=%s reason=state_%s", sid, state)
            return
        count = getattr(unit, "_auto_continue_count", 0)
        if count >= LIMIT:
            logger.warning("auto_continue.gave_up session_id=%s after=%d", sid, count)
            await _persist_notice(sid, "gave_up")
            return
        unit._auto_continue_count = count + 1
        logger.warning(
            "auto_continue.start session_id=%s attempt=%d/%d", sid, count + 1, LIMIT,
        )
        # the dropped call is accounted for; the chat shows no "Stopped" once the
        # continuation adds output after this notice
        await _persist_notice(sid, "restarted")
        agen = router.run_conversation(
            agent_id=unit.agent_id, user_message=PROMPT, session_id=sid,
            _auto_continue=True,
        )
        # the send bumps _send_generation; record it so a drop of THIS
        # continuation counts against the same budget
        first = True
        async for _evt in agen:
            if first:
                unit._auto_continue_gen = getattr(unit, "_send_generation", 0)
                first = False
        if first:  # nothing streamed (busy / refused): still ours
            unit._auto_continue_gen = getattr(unit, "_send_generation", 0)
    except asyncio.CancelledError:
        # A disconnect while this continuation streams cancels its consumer like
        # any SSE turn would; chat.py's next recovery schedules the next attempt.
        raise
    except Exception as exc:  # never raise out of a background task
        logger.warning("auto_continue.failed session_id=%s: %s", sid, exc)


async def _persist_notice(session_id: str, reason: str) -> None:
    from datetime import datetime
    from uuid import uuid4

    from database import db

    from .turn_notice import turn_notice_event

    evt = turn_notice_event(reason, "disconnected")
    try:
        await db.messages.put({
            "id": str(uuid4()), "session_id": session_id, "role": "assistant",
            "content": evt["content"], "model": None,
            "created_at": datetime.now().isoformat(),
        })
    except Exception as exc:
        logger.warning("auto_continue.notice_failed session_id=%s: %s", session_id, exc)
