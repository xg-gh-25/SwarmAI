"""SessionRouter — thin routing layer with concurrency cap enforcement.

Routes chat requests to the correct ``SessionUnit`` by session ID.
Enforces the concurrency cap (MAX_CONCURRENT=2) by evicting idle units
or queuing requests when all slots are occupied by protected units.

This module contains ONLY routing and cap logic.  No subprocess lifecycle,
prompt building, or hook execution lives here.

Public symbols:

- ``SessionRouter``  — Main class; dispatches to SessionUnits.

Design reference:
    ``.kiro/specs/multi-session-rearchitecture/design.md`` §2 SessionRouter
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING

from .session_unit import SessionState, SessionUnit

if TYPE_CHECKING:
    from .prompt_builder import PromptBuilder
    from .app_config_manager import AppConfigManager

logger = logging.getLogger(__name__)


class SessionRouter:
    """Routes chat requests to SessionUnits. Enforces MAX_CONCURRENT=2.

    Public API matches current AgentManager surface for zero-change
    migration of ``routers/chat.py``.

    Invariants:

    - Thin layer: lookup + cap enforcement + delegate.
    - Never touches subprocess directly (delegates to SessionUnit).
    - Concurrency cap is the ONLY cross-unit concern.
    - STREAMING/WAITING_INPUT units are NEVER evicted.
    """

    MAX_CONCURRENT: int = 2
    QUEUE_TIMEOUT: float = 60.0

    def __init__(
        self,
        prompt_builder: "PromptBuilder",
        config: Optional["AppConfigManager"] = None,
    ) -> None:
        self._units: dict[str, SessionUnit] = {}
        self._prompt_builder = prompt_builder
        self._config = config
        self._slot_available: asyncio.Event = asyncio.Event()
        self._slot_available.set()  # Initially available
        self._queue: list[asyncio.Future] = []

    # ── Unit management ───────────────────────────────────────────

    def get_unit(self, session_id: str) -> Optional[SessionUnit]:
        """Look up a SessionUnit by session_id."""
        return self._units.get(session_id)

    def get_or_create_unit(
        self, session_id: str, agent_id: str,
    ) -> SessionUnit:
        """Get existing or create new COLD SessionUnit."""
        unit = self._units.get(session_id)
        if unit is None:
            unit = SessionUnit(
                session_id=session_id,
                agent_id=agent_id,
                on_state_change=self._on_unit_state_change,
            )
            self._units[session_id] = unit
            logger.info(
                "session_router.create_unit session_id=%s agent_id=%s",
                session_id, agent_id,
            )
        return unit

    def list_units(self) -> list[SessionUnit]:
        """Return all registered SessionUnits."""
        return list(self._units.values())

    @property
    def alive_count(self) -> int:
        """Number of units with alive subprocesses."""
        return sum(1 for u in self._units.values() if u.is_alive)

    def has_active_session(self, session_id: str) -> bool:
        """Check if a session has an alive subprocess."""
        unit = self._units.get(session_id)
        return unit is not None and unit.is_alive

    # ── Slot management ───────────────────────────────────────────

    async def _acquire_slot(self, requesting_unit: SessionUnit) -> str:
        """Acquire a concurrency slot. Evict IDLE or queue with timeout.

        Returns:
            "ready" — slot acquired, proceed with send
            "queued" — was queued, now ready (caller should have yielded queued event)
            "timeout" — queue timed out, both slots busy
        """
        if requesting_unit.is_alive:
            return "ready"

        if self.alive_count < self.MAX_CONCURRENT:
            return "ready"

        # Try to evict the oldest IDLE unit
        if await self._evict_idle(exclude=requesting_unit):
            return "ready"

        # All slots occupied by protected units — queue
        logger.info(
            "session_router: all %d slots occupied, queuing session %s (timeout=%.0fs)",
            self.MAX_CONCURRENT, requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )
        try:
            self._slot_available.clear()
            await asyncio.wait_for(
                self._slot_available.wait(), timeout=self.QUEUE_TIMEOUT,
            )
            return "queued"
        except asyncio.TimeoutError:
            logger.warning(
                "session_router: queue timeout for session %s after %.0fs",
                requesting_unit.session_id, self.QUEUE_TIMEOUT,
            )
            return "timeout"

    async def _evict_idle(self, exclude: SessionUnit) -> bool:
        """Evict the oldest IDLE unit to free a slot.

        Returns True if a unit was evicted, False if no IDLE units available.
        Only evicts units in IDLE state — STREAMING and WAITING_INPUT are
        protected (Rule 3).
        """
        idle_units = sorted(
            [
                u for u in self._units.values()
                if u.state == SessionState.IDLE and u is not exclude
            ],
            key=lambda u: u.last_used,
        )
        if not idle_units:
            return False

        victim = idle_units[0]
        logger.info(
            "session_router.evict session_id=%s (idle %.0fs)",
            victim.session_id,
            time.time() - victim.last_used,
        )
        await victim.kill()
        return True

    def _on_unit_state_change(
        self, session_id: str, old_state: SessionState, new_state: SessionState,
    ) -> None:
        """Callback from SessionUnit state transitions.

        When a unit transitions from a protected state to IDLE or COLD,
        signal the slot_available event so queued requests can proceed.
        """
        if old_state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            if new_state in (SessionState.IDLE, SessionState.COLD, SessionState.DEAD):
                self._slot_available.set()

    # ── Public API (matches AgentManager surface) ─────────────────

    async def run_conversation(
        self,
        agent_id: str,
        user_message: Optional[str] = None,
        content: Optional[list[dict]] = None,
        session_id: Optional[str] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
        channel_context: Optional[dict] = None,
        editor_context: Optional[dict] = None,
        agent_config: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """Entry point — same signature as AgentManager.run_conversation.

        1. Get or create SessionUnit
        2. Build options via PromptBuilder
        3. Acquire slot (evict IDLE if needed, queue if full)
        4. Delegate to SessionUnit.send()
        5. Yield SSE events
        """
        from .session_utils import _build_error_event

        # Resolve session_id — use provided or generate
        if session_id is None:
            from uuid import uuid4
            session_id = str(uuid4())

        unit = self.get_or_create_unit(session_id, agent_id)

        # Acquire concurrency slot — may queue with SSE indicator
        slot_result = await self._acquire_slot(unit)
        if slot_result == "timeout":
            yield _build_error_event(
                code="QUEUE_TIMEOUT",
                message="Both chat slots are busy. Please wait a moment and try again.",
                suggested_action="Your conversation is saved. The other tabs are still processing.",
            )
            return
        if slot_result == "queued":
            yield {"type": "queued", "position": 1, "estimatedWaitMs": self.QUEUE_TIMEOUT * 1000}

        # Build query content
        query_content: Any
        if content and len(content) > 0:
            query_content = content
        elif user_message:
            query_content = user_message
        else:
            yield _build_error_event(
                code="EMPTY_MESSAGE",
                message="No message content provided.",
            )
            return

        # Build SDK options via PromptBuilder
        if agent_config is None:
            from .agent_defaults import build_agent_config
            agent_config = await build_agent_config(agent_id)

        options = await self._prompt_builder.build_options(
            agent_config=agent_config,
            enable_skills=enable_skills,
            enable_mcp=enable_mcp,
            resume_session_id=unit._sdk_session_id if unit.is_alive else None,
            channel_context=channel_context,
        )

        # Delegate to SessionUnit — wrap with message persistence
        from .session_manager import session_manager
        from database import db

        # Save user message to DB
        user_content = content if content else [{"type": "text", "text": user_message}]
        title = (user_message or "Chat")[:50]
        await session_manager.store_session(session_id, agent_id, title)
        await db.messages.create(
            session_id=session_id,
            role="user",
            content=user_content,
        )

        # Stream response and accumulate assistant content for DB persistence
        assistant_blocks: list[dict] = []
        assistant_model: str | None = None

        async for event in unit.send(
            query_content=query_content,
            options=options,
            app_session_id=session_id,
            config=self._config,
        ):
            # Accumulate assistant content for DB save
            if event.get("type") == "assistant" and event.get("content"):
                for block in event["content"]:
                    assistant_blocks.append(block)
                if event.get("model"):
                    assistant_model = event["model"]

            yield event

        # Save assistant message to DB after stream completes
        if assistant_blocks:
            await db.messages.create(
                session_id=session_id,
                role="assistant",
                content=assistant_blocks,
                model=assistant_model,
            )

    async def interrupt_session(self, session_id: str) -> dict:
        """Delegate to SessionUnit.interrupt()."""
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}
        survived = await unit.interrupt()
        return {
            "success": True,
            "message": "Interrupted" if survived else "Killed (interrupt timed out)",
            "subprocess_alive": survived,
        }

    async def continue_with_answer(
        self, session_id: str, answer: str,
    ) -> AsyncIterator[dict]:
        """Delegate to SessionUnit.continue_with_answer()."""
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return
        async for event in unit.continue_with_answer(answer):
            yield event

    async def continue_with_cmd_permission(
        self, session_id: str, request_id: str, allowed: bool,
    ) -> AsyncIterator[dict]:
        """Delegate to SessionUnit.continue_with_permission()."""
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return
        async for event in unit.continue_with_permission(request_id, allowed):
            yield event

    async def compact_session(
        self, session_id: str, instructions: Optional[str] = None,
    ) -> dict:
        """Delegate to SessionUnit.compact()."""
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}
        return await unit.compact(instructions)

    async def disconnect_all(self) -> None:
        """Kill all alive SessionUnits. Called at shutdown."""
        alive = [u for u in self._units.values() if u.is_alive]
        logger.info("session_router.disconnect_all: killing %d alive units", len(alive))
        for unit in alive:
            try:
                await unit.kill()
            except Exception as exc:
                logger.warning(
                    "Failed to kill unit %s during disconnect_all: %s",
                    unit.session_id, exc,
                )
