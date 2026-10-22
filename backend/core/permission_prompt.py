"""A command-permission prompt is durable: stored with its turn, never only live.

Incident 2026-10-22 (session e4f3397f): a CLI-initiated turn (a background agent
finished) hit the dangerous-command gate on its first call. The prompt existed only
as a live ``cmd_permission_request`` event; it was never stored, and the frontend's
15s mirror deliberately did not re-surface permission prompts. The tab showed
nothing, the session sat in WAITING_INPUT, two later agent results and two user
messages queued behind it, and the user saw "no response" for 18 minutes.

The prompt now lives with its turn like any block, and its state is decided by the
backend at read time, never by a stored flag alone:

- ``permission_block(event)``  — the stored block for a ``cmd_permission_request``
  event (the same shape the frontend builds from the event, so the two dedupe).
- ``note_decision(request_id, decision)`` — the decision is with its hook; until its
  record lands a reload reads it from here, not as ``expired`` (synchronous).
- ``record_decision(session_id, request_id, decision)`` — writes ``approve`` /
  ``deny`` onto the stored block when the user decides.
- ``live_request_ids(unit, session_id)`` — the requests a hook is blocked on NOW.
- ``annotate(messages, live)`` — a stored prompt with no decision that is not live
  any more (the turn was stopped, the process restarted, it timed out) reads
  ``decision: "expired"``: never a dead card with live-looking buttons.

@exports permission_block, note_decision, record_decision, live_request_ids, annotate, etag_part, BLOCK_TYPE
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

BLOCK_TYPE = "cmd_permission_request"
DECISIONS = ("approve", "deny")


def permission_block(event: dict) -> Optional[dict]:
    """The block to store for a ``cmd_permission_request`` event, or None."""
    rid = event.get("requestId")
    if not isinstance(rid, str) or not rid:
        return None
    tool_input = event.get("toolInput")
    return {
        "type": BLOCK_TYPE,
        "requestId": rid,
        "toolName": str(event.get("toolName") or "Bash"),
        "toolInput": tool_input if isinstance(tool_input, dict) else {},
        "reason": str(event.get("reason") or ""),
        "options": list(event.get("options") or ["approve", "deny"]),
    }


async def record_decision(session_id: str, request_id: str, decision: str) -> bool:
    """Write the user's decision onto the stored prompt. True when a row changed."""
    if decision not in DECISIONS or not request_id:
        return False
    try:
        from database import db
        ok = await db.messages.update_permission_decision(session_id, request_id, decision)
        _IN_FLIGHT.pop(str(request_id), None)   # the block carries it now
        return ok
    except Exception as exc:  # noqa: BLE001 — the decision itself already went to the hook
        logger.warning("permission_prompt.record_decision_failed session=%s request=%s: %s",
                       session_id, request_id, exc)
        return False


def live_request_ids(unit: Any, session_id: str) -> set[str]:
    """The permission requests a hook of this session is blocked on now: the unit's
    surfaced prompt (WAITING_INPUT) and the open prompts on its board (the
    source the session's stream and the streaming-state read use)."""
    live: set[str] = set()
    try:
        state = getattr(getattr(unit, "state", None), "value", None)
        pq = getattr(unit, "_pending_question", None)
        if state == "waiting_input" and isinstance(pq, dict) and pq.get("request_id"):
            live.add(str(pq["request_id"]))
    except Exception:  # noqa: BLE001
        logger.debug("permission_prompt.live_unit_read_failed", exc_info=True)
    try:
        from core.permission_manager import permission_manager as _pm
        for item in _pm.open_prompts(session_id):
            rid = item.get("requestId")
            if rid:
                live.add(str(rid))
    except Exception:  # noqa: BLE001
        logger.debug("permission_prompt.live_store_read_failed", exc_info=True)
    return live


# Decisions handed to their hook whose record onto the stored block has not landed
# yet (record_decision runs in a background task). Set synchronously by
# note_decision on the decision path, dropped when the record lands. Bounded.
_IN_FLIGHT: "dict[str, str]" = {}
_IN_FLIGHT_MAX = 256


def note_decision(request_id: str, decision: str) -> None:
    """The decision is with its hook; its record onto the stored block follows.
    Synchronous, so a history read before the record shows it decided."""
    if decision not in DECISIONS or not request_id:
        return
    if len(_IN_FLIGHT) >= _IN_FLIGHT_MAX:
        _IN_FLIGHT.pop(next(iter(_IN_FLIGHT)))
    _IN_FLIGHT[str(request_id)] = decision


def _decided(request_id: Any) -> "str | None":
    return _IN_FLIGHT.get(str(request_id)) if request_id else None


def annotate(messages: Iterable[dict], live: set[str]) -> None:
    """In place: a stored prompt with no decision whose request is not live reads the
    decision the permission manager holds for it (its record onto the block has not
    landed yet), else ``decision: "expired"`` (display only; the row is not changed)."""
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            continue  # an undecoded row: nothing to read
        for blk in content or []:
            if (isinstance(blk, dict) and blk.get("type") == BLOCK_TYPE
                    and not blk.get("decision") and blk.get("requestId") not in live):
                blk["decision"] = _decided(blk.get("requestId")) or "expired"


def etag_part(live: set[str]) -> str:
    """What the live prompt set adds to the history ETag: a prompt going from live to
    decided / expired changes the body without changing the row count."""
    return ",".join(sorted(live)) if live else "-"


__all__ = ["permission_block", "note_decision", "record_decision", "live_request_ids", "annotate", "etag_part",
           "BLOCK_TYPE", "DECISIONS"]
