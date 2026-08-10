"""Observation hook factories — PreToolUse recorder + PostToolUse completer.

Registers at the END of the hook chain (after security gates). Records tool
invocations into the session's ObservationRing. Also emits real-time DDD
cultivation events for qualifying observations (project file edits, corrections).

Public symbols:
    - create_observation_recorder   — PreToolUse hook factory
    - create_observation_completer  — PostToolUse hook factory
    - register_observation_hooks    — Wire both hooks into a HookRegistry
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level cached imports (avoid per-call import lock contention)
_CultivationEvent = None
_EventType = None
_get_dispatcher_fn = None


def _ensure_cultivation_imports():
    """Lazy one-time import of cultivation dispatcher symbols."""
    global _CultivationEvent, _EventType, _get_dispatcher_fn
    if _get_dispatcher_fn is not None:
        return True
    try:
        from core.cultivation_dispatcher import (
            CultivationEvent,
            EventType,
            get_dispatcher as _gd,
        )
        _CultivationEvent = CultivationEvent
        _EventType = EventType
        _get_dispatcher_fn = _gd
        return True
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE. False disables the DDD cultivation dispatcher for the
        # process; every downstream event then no-ops and the knowledge engine looks
        # merely idle rather than broken.
        logger.warning("cultivation imports unavailable; DDD event dispatch is "
                       "disabled for this process: %s", exc)
        return False


def get_dispatcher():
    """Get the cultivation EventDispatcher singleton. Returns None if unavailable."""
    if not _ensure_cultivation_imports():
        return None
    return _get_dispatcher_fn()


def create_observation_recorder(session_context: dict):
    """Factory: PreToolUse hook that records tool intent into ObservationRing.

    Guarantees:
        - CRITICAL: Must ALWAYS return {} — never "block"
        - Completes in <0.1ms (pure memory append, no IO)
        - Exceptions caught internally (never propagates)
    """
    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        try:
            ring = session_context.get("_observations")
            if ring is None:
                return {}
            tool_name = ""
            tool_input = {}
            if isinstance(input_data, dict):
                tool_name = input_data.get("tool_name", "")
                tool_input = input_data.get("tool_input", {})
            elif hasattr(input_data, "tool_name"):
                tool_name = getattr(input_data, "tool_name", "")
                tool_input = getattr(input_data, "tool_input", {}) or {}
            # Ensure tool_input is a dict (defensive)
            if not isinstance(tool_input, dict):
                tool_input = {}
            if tool_name:
                ring.record_pre(str(tool_use_id), tool_name, tool_input)
        except Exception:
            logger.debug("observation_recorder: exception (suppressed)", exc_info=True)
        return {}  # CRITICAL: Must ALWAYS return {} — never "block"

    return _hook


def create_observation_completer(session_context: dict):
    """Factory: PostToolUse hook that completes observation + emits DDD events.

    Guarantees:
        - CRITICAL: Must ALWAYS return {} — never "block"
        - Ring update: <0.1ms (pure memory mutation)
        - DDD emit: put_nowait (O(1), non-yielding)
        - Exceptions caught internally
    """
    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        try:
            ring = session_context.get("_observations")
            if ring is None:
                return {}

            # Extract error from input_data
            error = None
            if isinstance(input_data, dict):
                error = input_data.get("error")
            elif hasattr(input_data, "error"):
                error = getattr(input_data, "error", None)

            # Complete observation in ring — returns the completed Observation
            completed_obs = ring.record_post(str(tool_use_id), error)

            # Emit DDD event if qualifying (uses returned obs directly — no stale lookup)
            if completed_obs is not None:
                _maybe_emit_ddd_event_sync(completed_obs, session_context)

        except Exception:
            logger.debug("observation_completer: exception (suppressed)", exc_info=True)
        return {}  # CRITICAL: Must ALWAYS return {} — never "block"

    return _hook


def _maybe_emit_ddd_event_sync(obs, session_context: dict) -> None:
    """Emit cultivation event if observation qualifies.

    Uses put_nowait (non-yielding, O(1)) instead of await queue.put().
    This guarantees the hook never yields to the event loop for DDD emission.
    """
    dispatcher = get_dispatcher()
    if dispatcher is None:
        return

    # Ensure we have the event classes cached
    if not _ensure_cultivation_imports():
        return

    # Rule 1: File edit in a project directory → CODE_CHANGE event
    if obs.tool_name in ("Edit", "Write") and obs.result_status == "success":
        for f in obs.files:
            if "/Projects/" in f or "/swarmai/" in f:
                try:
                    event = _CultivationEvent(
                        type=_EventType.GIT_COMMIT,
                        source="observation_stream",
                        payload={"files": obs.files, "intent": obs.intent},
                        priority=2,
                    )
                    dispatcher.emit_nowait(event)  # Dedup + non-yielding
                except Exception:
                    pass
                break

    # Rule 2: Correction detected (from session_context flag)
    if session_context.get("_correction_just_detected"):
        try:
            event = _CultivationEvent(
                type=_EventType.DAILY_ACTIVITY,
                source="observation_stream",
                payload={"type": "correction", "intent": obs.intent},
                priority=1,
            )
            dispatcher.emit_nowait(event)  # Dedup + non-yielding
        except Exception:
            pass
        session_context["_correction_just_detected"] = False


# Module-level ring registry — allows daily_activity_hook to access session rings
# Keyed by session_id, cleaned on session end.
_session_rings: dict[str, "ObservationRing"] = {}


def get_session_ring(session_id: str):
    """Get the ObservationRing for a session (used by daily_activity_hook)."""
    return _session_rings.get(session_id)


def _cleanup_session_ring(session_id: str) -> None:
    """Remove ring for a closed session (prevents memory leak)."""
    _session_rings.pop(session_id, None)


def register_observation_hooks(registry, session_context: dict) -> None:
    """Register observation hooks at END of PreToolUse/PostToolUse chains.

    Called from register_runtime_hooks() after all other hooks are registered.
    """
    from core.observation_ring import ObservationRing

    # Initialize ring in session context (one per session)
    if "_observations" not in session_context:
        ring = ObservationRing()
        session_context["_observations"] = ring
        # Register in module-level dict for cross-hook access
        sid = session_context.get("sdk_session_id", "")
        if sid:
            _session_rings[sid] = ring

    registry.register(
        "PreToolUse",
        create_observation_recorder(session_context),
        "observation_recorder",
    )
    registry.register(
        "PostToolUse",
        create_observation_completer(session_context),
        "observation_completer",
    )
