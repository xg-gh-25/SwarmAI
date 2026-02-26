"""TSCC in-memory per-thread state manager.

This module provides the ``TSCCStateManager`` class, which maintains live
cognitive state for all active chat threads in an LRU-evicting
``OrderedDict``.  Each thread's state is guarded by a per-thread
``asyncio.Lock`` to prevent concurrent mutation from the SSE emission
path and the API router.

Key public symbols:

- ``TSCCStateManager``       — In-memory state store with LRU eviction
- ``VALID_TRANSITIONS``      — Allowed lifecycle state transitions
- ``InvalidTransitionError`` — Raised on illegal lifecycle transitions

The manager is instantiated once at app startup and shared across the
TSCC router and the agent execution path.

Requirements: 18.1, 18.2, 9.1-9.7, 4.3, 4.5, 5.1, 5.4, 6.3, 7.1, 12.1, 12.3
"""

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from schemas.tscc import (
    TSCCActiveCapabilities,
    TSCCContext,
    TSCCLiveState,
    TSCCSource,
    TSCCState,
)


# Valid lifecycle state transitions: {from_state: {to_states}}
VALID_TRANSITIONS: dict[str, set[str]] = {
    "new": {"active"},
    "active": {"paused", "failed", "cancelled", "idle"},
    "paused": {"active", "cancelled"},
    "failed": {"active", "cancelled"},
    "cancelled": {"active"},
    "idle": {"active"},
}


class InvalidTransitionError(ValueError):
    """Raised when a lifecycle state transition is not allowed."""
    pass


def _iso_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class TSCCStateManager:
    """In-memory per-thread TSCC state manager with LRU eviction.

    Maintains an ``OrderedDict`` of ``TSCCState`` objects keyed by
    ``thread_id``.  When the number of entries exceeds ``max_entries``,
    the least-recently-used entry is evicted.

    All mutating operations are guarded by a per-thread ``asyncio.Lock``
    to prevent data races between the SSE emission path and the REST API.

    Parameters
    ----------
    max_entries:
        Maximum number of thread states to keep in memory (default 200).
    """

    def __init__(self, max_entries: int = 200) -> None:
        self._states: OrderedDict[str, TSCCState] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._max_entries = max_entries

    def _get_lock(self, thread_id: str) -> asyncio.Lock:
        """Return the per-thread lock, creating one if needed."""
        if thread_id not in self._locks:
            self._locks[thread_id] = asyncio.Lock()
        return self._locks[thread_id]

    async def get_state(self, thread_id: str) -> Optional[TSCCState]:
        """Return the current TSCC state for a thread, or None.

        Moves the entry to the end of the OrderedDict (most-recently-used).
        """
        async with self._get_lock(thread_id):
            if thread_id not in self._states:
                return None
            self._states.move_to_end(thread_id)
            return self._states[thread_id]

    async def get_or_create_state(
        self,
        thread_id: str,
        project_id: Optional[str] = None,
        thread_title: str = "Untitled Thread",
    ) -> TSCCState:
        """Return existing state or create a default one.

        When ``project_id`` is None the scope is ``"workspace"`` with label
        ``"Workspace: SwarmWS (General)"``; otherwise scope is ``"project"``
        with label ``"Project: {thread_title}"``.

        LRU eviction occurs when the entry count reaches ``max_entries``.
        """
        async with self._get_lock(thread_id):
            if thread_id in self._states:
                self._states.move_to_end(thread_id)
                return self._states[thread_id]

            # Determine scope
            if project_id is None:
                scope_type = "workspace"
                scope_label = "Workspace: SwarmWS (General)"
            else:
                scope_type = "project"
                scope_label = f"Project: {thread_title}"

            state = TSCCState(
                thread_id=thread_id,
                project_id=project_id,
                scope_type=scope_type,
                last_updated_at=_iso_now(),
                lifecycle_state="new",
                live_state=TSCCLiveState(
                    context=TSCCContext(
                        scope_label=scope_label,
                        thread_title=thread_title,
                    ),
                    active_agents=[],
                    active_capabilities=TSCCActiveCapabilities(),
                    what_ai_doing=[],
                    active_sources=[],
                    key_summary=[],
                ),
            )

            # Evict LRU if at capacity (clean up orphaned lock too)
            if len(self._states) >= self._max_entries:
                evicted_id, _ = self._states.popitem(last=False)
                self._locks.pop(evicted_id, None)

            self._states[thread_id] = state
            return state

    async def apply_event(self, thread_id: str, event: dict) -> None:
        """Route a telemetry event to update the correct live_state fields.

        Silently ignores events for unknown threads so that telemetry
        emission never interrupts the agent SSE stream.

        Parameters
        ----------
        thread_id:
            The thread whose state should be updated.
        event:
            A telemetry event dict with ``type`` and ``data`` keys.
        """
        async with self._get_lock(thread_id):
            if thread_id not in self._states:
                return  # graceful no-op — don't interrupt SSE stream

            state = self._states[thread_id]
            self._states.move_to_end(thread_id)
            live = state.live_state
            event_type = event.get("type", "")
            data = event.get("data", {})

            if event_type == "agent_activity":
                agent_name = data.get("agent_name", "")
                description = data.get("description", "")
                # Deduplicated append to active_agents
                if agent_name and agent_name not in live.active_agents:
                    live.active_agents.append(agent_name)
                # FIFO update to what_ai_doing (max 4)
                if description:
                    live.what_ai_doing.append(description)
                    if len(live.what_ai_doing) > 4:
                        live.what_ai_doing = live.what_ai_doing[-4:]

            elif event_type == "tool_invocation":
                description = data.get("description", "")
                if description:
                    live.what_ai_doing.append(description)
                    if len(live.what_ai_doing) > 4:
                        live.what_ai_doing = live.what_ai_doing[-4:]

            elif event_type == "capability_activated":
                cap_type = data.get("cap_type", "")
                cap_name = data.get("cap_name", "")
                if cap_type == "skill" and cap_name not in live.active_capabilities.skills:
                    live.active_capabilities.skills.append(cap_name)
                elif cap_type == "mcp" and cap_name not in live.active_capabilities.mcps:
                    live.active_capabilities.mcps.append(cap_name)
                elif cap_type == "tool" and cap_name not in live.active_capabilities.tools:
                    live.active_capabilities.tools.append(cap_name)

            elif event_type == "sources_updated":
                source_path = data.get("source_path", "")
                origin = data.get("origin", "")
                # Deduplicate by (path, origin) tuple
                existing = {(s.path, s.origin) for s in live.active_sources}
                if (source_path, origin) not in existing:
                    live.active_sources.append(
                        TSCCSource(path=source_path, origin=origin)
                    )

            elif event_type == "summary_updated":
                key_summary = data.get("key_summary", [])
                live.key_summary = key_summary[:5]

            state.last_updated_at = _iso_now()

    async def set_lifecycle_state(self, thread_id: str, new_state: str) -> None:
        """Transition the lifecycle state, validating against the state machine.

        Parameters
        ----------
        thread_id:
            The thread whose lifecycle state should change.
        new_state:
            The target lifecycle state.

        Raises
        ------
        KeyError
            If no state exists for ``thread_id``.
        ValueError
            If the transition is not allowed.
        """
        async with self._get_lock(thread_id):
            if thread_id not in self._states:
                raise KeyError(f"No state for thread {thread_id}")

            state = self._states[thread_id]
            current = state.lifecycle_state
            allowed = VALID_TRANSITIONS.get(current, set())

            if new_state not in allowed:
                raise ValueError(
                    f"Invalid lifecycle transition: {current} -> {new_state}"
                )

            state.lifecycle_state = new_state
            state.last_updated_at = _iso_now()

    async def clear_state(self, thread_id: str) -> None:
        """Remove all state for a thread."""
        async with self._get_lock(thread_id):
            self._states.pop(thread_id, None)
            self._locks.pop(thread_id, None)
