"""Streaming Orchestrator — facade for session_unit.py strangler-fig extraction.

Phase 1 (current): Pure delegation layer. StreamingOrchestrator.stream_query()
calls parent._stream_response() directly. Zero behavior change. This proves
the interface boundary before moving logic in Phase 2.

Phase 2 (future): _read_formatted_response() body moves here. Callbacks replace
direct field access. session_unit.py shrinks from 4160 → ~2860 lines.

Phase 3 (future): Cleanup vestigial delegation, decouple fully.

Design doc: Knowledge/Designs/2026-06-18-session-unit-strangler-fig-extraction-design.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional, Protocol

if TYPE_CHECKING:
    from .session_unit import SessionUnit

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Callbacks Protocol (Phase 2 will use these; Phase 1 uses parent ref)
# ═══════════════════════════════════════════════════════════════════


class StreamingCallbacks(Protocol):
    """Interface contract between StreamingOrchestrator and SessionUnit.

    Phase 1: Not used (orchestrator calls parent directly).
    Phase 2: SessionUnit implements this protocol; orchestrator calls through it
    instead of holding a parent reference.
    """

    def on_state_transition(self, new_state: Any) -> None:
        """Notify parent of state change (e.g., STREAMING → IDLE)."""
        ...

    async def on_kill_needed(self) -> None:
        """Request parent to kill the subprocess."""
        ...

    def get_session_id(self) -> str:
        """Get the owning session's ID."""
        ...


# ═══════════════════════════════════════════════════════════════════
# StreamingOrchestrator — Phase 1 (pure delegation)
# ═══════════════════════════════════════════════════════════════════


class StreamingOrchestrator:
    """Facade for streaming orchestration logic.

    Phase 1 architecture:
    - Holds a reference to the parent SessionUnit
    - stream_query() delegates directly to parent._stream_response()
    - No logic lives here yet — this is the seam for future extraction

    Phase 2 target:
    - _read_formatted_response() body moves here
    - State mutations happen via callbacks
    - SessionUnit becomes thin orchestration + lifecycle

    Instantiated in SessionUnit.__init__. Callers (send, retry, overflow,
    continue_with_answer) call self._streaming_orchestrator.stream_query()
    instead of self._stream_response() directly.
    """

    __slots__ = ("_parent", "_session_id")

    def __init__(self, parent: "SessionUnit") -> None:
        """Initialize with parent SessionUnit reference.

        Args:
            parent: The owning SessionUnit instance. In Phase 1, all calls
                delegate directly to parent methods. In Phase 2, this will
                be replaced with a callbacks protocol.
        """
        self._parent = parent
        self._session_id = parent.session_id

    async def stream_query(
        self,
        query_content: Any,
        parent_tool_use_id: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Stream a query through the SDK and yield formatted SSE events.

        Phase 1: Pure delegation to parent._stream_response().
        Phase 2: Will contain the streaming loop logic directly.

        Args:
            query_content: User message text (str) or multimodal blocks (list).
            parent_tool_use_id: When set, message is a tool result response.

        Yields:
            Formatted SSE event dicts (text_delta, thinking_delta, tool_use, etc.)
        """
        async for event in self._parent._stream_response(
            query_content, parent_tool_use_id=parent_tool_use_id
        ):
            yield event

    @property
    def stall_seconds(self) -> Optional[float]:
        """Proxy to parent's streaming_stall_seconds for Phase 2 preparation."""
        return self._parent.streaming_stall_seconds
