"""Per-session injection queue registry for Layer-2 Canvas live-surfacing.

WHY THIS EXISTS
---------------
Layer-1 (streaming_orchestrator._build_file_write_events) emits ``file_changed``
SSE events for files the PARENT agent writes with its own tools — but the Claude
SDK filters out sub-agent *sidechain* messages (claude_agent_sdk/types.py:1600),
so a file written by a sub-agent / CLI subprocess / hook is invisible to Layer-1.
Layer-2 is a daemon filesystem watcher (workspace_surface_watcher.py) that catches
those author-agnostic writes. But a daemon watcher fires with NO chat turn of its
own, and there is NO persistent server→frontend push channel (the chat SSE stream
is per-turn; the workspace explorer is a 30s poll). This module bridges the gap:
it lets the watcher hand a ``file_changed`` event to an ALREADY-OPEN per-turn SSE
stream, which then carries it to the frontend.

THE ATTRIBUTION RULE (prevents simultaneous ambiguity + in-flight gate)
-----------------------------------------------------------------------
A watcher event carries no writer identity (the kernel gives path + event only).
Rather than GUESS which tab a write belongs to by wall-clock/time-window
correlation — which is exactly the run_4de279ca cross-tab bleed — we attribute an
event ONLY when (a) there is exactly ONE non-channel session STREAMING, AND (b)
that session currently has an in-flight tool (unit.has_open_tools()). With 0 or 2+
streaming, or an idle-tool streaming session, we DROP the event (the
pipeline-finish sweep_run_changes remains the author-agnostic fallback).

This PREVENTS SIMULTANEOUS AMBIGUITY (two candidate tabs at once → never chosen).
It is NOT literally "structurally impossible": a residual TIME-SHIFT window remains
— A's tool-run write is enqueued, A ends, B starts streaming within the ~2s
watchfiles debounce, and the event could then resolve to B. The window is narrow
(A-ends → B-starts → batch-fires all inside ~2s) and the in-flight gate shrinks it
further (B must ALSO have a tool open), but it is a residual, not zero. Do not
re-inflate this to "impossible" (run_bfbbe0fd corrected the overclaim).

The in-flight gate (run_bfbbe0fd) narrows attribution to writes plausibly caused by
the active agent: only surface LIVE while the sole streaming session is running a
tool. For a sub-agent the parent's Agent-tool stays open across the whole sub-agent
run (its result arrives at sub-agent completion), so a mid-run write is surfaced;
a background-job write during an idle-tool chat is NOT. A write whose tool has
already CLOSED is dropped HERE at publish (gate → None) and never enters the
queue — so it is surfaced only by the pipeline-finish sweep_run_changes, NOT by
the SSE handler's final drain (the final drain only re-delivers events that
already PASSED this gate but landed after the last in-loop drain; do not
overclaim it as an end-of-turn catch-up for tool-closed writes — corrected in an
integration audit). Losing a live pop is strictly better than leaking a write
into the wrong tab.

DESIGN
------
- ``_QUEUES``: module-level ``{session_id: asyncio.Queue}``. The SSE handler
  (chat.py message_generator) registers its session's queue on turn start and
  unregisters in a ``finally`` (so a cancelled/crashed turn never leaks a queue).
- ``publish_file_event``: resolves the sole streaming session and, if it has a
  registered queue, enqueues the event (non-blocking). Returns the count delivered
  (0 or 1) — never raises (a surfacing failure must never crash a turn or the
  watcher).
- ``drain_nowait``: the SSE handler drains all pending events between SDK messages.

All functions are pure/deterministic given the router + registry — unit-testable
without watchfiles or a live SSE stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# session_id → queue of file_changed event dicts awaiting injection into that
# session's live SSE stream. Module-level (process-global) by design: the watcher
# task and the SSE handlers live in the same daemon event loop.
_QUEUES: dict[str, "asyncio.Queue[dict]"] = {}

# Bound each queue so a pathological burst can't grow memory without limit. A
# full queue drops the oldest-unread (the surface is best-effort, not a ledger).
_MAX_QUEUE = 256

# Latch so the "surfacing is disabled" warning is emitted once per process rather
# than once per file event (see resolve_sole_streaming_session's import guard).
_WARNED_SESSION_STATE_IMPORT: set[bool] = set()


def register(session_id: str) -> "asyncio.Queue[dict]":
    """Register (or return the existing) injection queue for a session.

    Called by the SSE handler at turn start. Idempotent — a re-register returns
    the same queue so an in-flight event is not lost across a re-entrant call.
    """
    q = _QUEUES.get(session_id)
    if q is None:
        q = asyncio.Queue(maxsize=_MAX_QUEUE)
        _QUEUES[session_id] = q
    return q


def unregister(session_id: str) -> None:
    """Drop a session's injection queue. Called in the SSE handler's ``finally``
    (fires on normal completion AND on CancelledError) — so a dropped/crashed
    turn can never leak a queue. Safe to call for an unregistered id."""
    _QUEUES.pop(session_id, None)


def drain_nowait(session_id: str) -> list[dict]:
    """Return all currently-queued events for a session, emptying the queue.

    Non-blocking: the SSE handler calls this between SDK messages to interleave
    watcher events into the live stream. Returns [] if no queue or nothing
    pending."""
    q = _QUEUES.get(session_id)
    if q is None:
        return []
    out: list[dict] = []
    while True:
        try:
            out.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


def resolve_sole_streaming_session(router: Any) -> Optional[str]:
    """Return the session_id of the ONE eligible STREAMING session, else None.

    Two conditions (both required): exactly one non-channel unit is STREAMING
    AND that unit has an in-flight tool (unit.has_open_tools()). 0 or 2+ streaming
    → None (never guess between tabs — prevents simultaneous ambiguity, though a
    narrow ~2s time-shift residual remains; see module docstring). Idle-tool sole
    streaming → None (in-flight gate — a background write is not mis-attributed).
    Reads ``unit.state``/``unit.is_channel_session``/``unit.session_id``/
    ``unit.has_open_tools()``. Fail-safe: any error → None (do not surface).
    """
    # Import here (not module-top) to avoid a heavy import at daemon boot and to
    # keep this module unit-testable with a fake router.
    try:
        from core.session_unit import SessionState
    except Exception as exc:  # pragma: no cover - import guard
        # WARNING, not debug: this import either always works or always fails, so a
        # failure here does not degrade surfacing — it disables the feature ENTIRELY for
        # the life of the process while every call returns the same legitimate-looking
        # "no sole streaming session". Warn ONCE so a total outage is visible without
        # emitting a line per file event.
        if not _WARNED_SESSION_STATE_IMPORT:
            _WARNED_SESSION_STATE_IMPORT.add(True)
            logger.warning(
                "SessionState import failed; file-event surfacing is disabled for this "
                "process: %s", exc)
        return None

    try:
        streaming = [
            u for u in router.list_units()
            if getattr(u, "state", None) == SessionState.STREAMING
            and not getattr(u, "is_channel_session", False)
        ]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("resolve_sole_streaming_session failed: %s", exc)
        return None

    if len(streaming) != 1:
        return None
    unit = streaming[0]
    # In-flight gate (run_bfbbe0fd): only surface LIVE while the sole streaming
    # session is running a tool — so a background-job write during an idle-tool
    # chat is not mis-attributed. A sub-agent write is covered because the
    # parent's Agent-tool stays open across the whole sub-agent run. Fail-safe:
    # a missing accessor reads as "no open tool" → do not surface.
    has_open = getattr(unit, "has_open_tools", None)
    if not callable(has_open) or not has_open():
        return None
    return getattr(unit, "session_id", None)


def publish_file_event(event: dict, router: Any = None) -> int:
    """Route a file_changed event to the sole streaming session's queue.

    Returns the number of queues the event was delivered to (0 or 1). NEVER
    raises — a live-surfacing failure must not crash the watcher or a turn.

    Drops (returns 0) when: no single streaming session (0 or 2+), the resolved
    session has no registered SSE queue, or its queue is full.
    """
    try:
        if router is None:
            from core import session_registry
            router = getattr(session_registry, "session_router", None)
        if router is None:
            return 0

        session_id = resolve_sole_streaming_session(router)
        if session_id is None:
            return 0

        q = _QUEUES.get(session_id)
        if q is None:
            return 0

        try:
            q.put_nowait(event)
            return 1
        except asyncio.QueueFull:
            # Best-effort surface: drop the oldest unread, enqueue the newest.
            try:
                q.get_nowait()
                q.put_nowait(event)
                return 1
            except Exception as exc:  # noqa: BLE001
                # DEBUG, matching the outer handler: 0 means "surfaced to nobody", and
                # the caller is free to ignore that. But this is the drop-oldest RETRY
                # failing after the queue was already full, so it is the path where an
                # event is genuinely lost — worth a line even if it is expected to be
                # rare under a consumer that has stopped reading.
                logger.debug("publish_file_event drop-oldest retry failed: %s", exc)
                return 0
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("publish_file_event failed: %s", exc)
        return 0


def _reset_for_test() -> None:
    """Test-only: clear the registry. Not for production use."""
    _QUEUES.clear()
