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
from datetime import datetime
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING
from uuid import uuid4

from .session_unit import SessionState, SessionUnit

if TYPE_CHECKING:
    from .prompt_builder import PromptBuilder
    from .app_config_manager import AppConfigManager
    from .lifecycle_manager import LifecycleManager

logger = logging.getLogger(__name__)

# ── SDK multimodal support flag ────────────────────────────────────
# False = always convert image/document blocks to path hints.
# Claude Code CLI does not currently support image/document content blocks
# via stdin JSON.  When SDK support lands, flip this to True.
_SDK_SUPPORTS_MULTIMODAL: bool = False


async def _convert_unsupported_blocks_to_path_hints(
    content: list[dict],
    session_id: str | None,
) -> list[dict]:
    """Convert image/document content blocks to path hints.

    Saves base64 data to the agent's workspace under
    ``Attachments/{date}/{filename}`` so files are visible in the
    Workspace Explorer and persist across sessions.  The user controls
    cleanup — files are NOT auto-deleted.

    Text blocks are passed through unchanged.

    Args:
        content: List of content block dicts (image, document, or text).
        session_id: The effective session ID for logging.

    Returns:
        A new list with image/document blocks replaced by text path hints.
    """
    import base64
    from pathlib import Path
    from uuid import uuid4 as _uuid4

    converted: list[dict] = []
    for block in content:
        block_type = block.get("type")
        if block_type in ("image", "document"):
            source = block.get("source", {})
            data = source.get("data", "")
            media_type = source.get("media_type", "")

            ext_map = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/gif": ".gif",
                "image/webp": ".webp",
                "application/pdf": ".pdf",
            }
            ext = ext_map.get(media_type, ".bin")

            # Save to SwarmWS/Attachments/{date}/ for Workspace Explorer visibility
            from datetime import date as _date
            from core.initialization_manager import initialization_manager

            ws_path = initialization_manager.get_cached_workspace_path()
            if ws_path:
                date_str = _date.today().isoformat()
                attach_dir = Path(ws_path) / "Attachments" / date_str
            else:
                attach_dir = Path.home() / ".swarm-ai" / "SwarmWS" / "Attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)

            # Preserve original filename if provided by frontend
            original_name = block.get("_filename", "")
            if original_name:
                safe_name = Path(original_name).name
                candidate = attach_dir / safe_name
                if candidate.exists():
                    stem = candidate.stem
                    candidate = attach_dir / f"{stem}_{_uuid4().hex[:6]}{ext}"
                file_path = candidate
            else:
                file_path = attach_dir / f"{_uuid4()}{ext}"

            try:
                decoded = base64.b64decode(data)
                await asyncio.to_thread(file_path.write_bytes, decoded)
                logger.warning(
                    "SDK multimodal fallback: saved %s block to %s (session %s)",
                    block_type, file_path, session_id or "unknown",
                )
                rel_path = file_path.relative_to(ws_path) if ws_path else file_path
                converted.append({
                    "type": "text",
                    "text": (
                        f"[Attached {block_type}: {file_path.name}] "
                        f"saved at {rel_path} - use Read tool to access"
                    ),
                })
            except Exception as e:
                logger.error("Failed to save attachment for fallback: %s", e)
                converted.append({
                    "type": "text",
                    "text": f"[Failed to save {block_type} attachment for fallback delivery]",
                })
        else:
            converted.append(block)
    return converted


class SessionRouter:
    """Routes chat requests to SessionUnits. Enforces MAX_CONCURRENT=2.

    Public API surface consumed by ``routers/chat.py``.

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
        self._lifecycle_manager = None  # Set by session_registry after init
        self._slot_available: asyncio.Event = asyncio.Event()
        self._slot_available.set()  # Initially available
        self._queue: list[asyncio.Future] = []

    # ── Unit management ───────────────────────────────────────────

    @staticmethod
    async def _persist_assistant_blocks(
        session_id: str,
        blocks: list[dict],
        model: str | None,
        label: str = "",
    ) -> None:
        """Save accumulated assistant content blocks to DB.

        Called from ``finally`` blocks in streaming methods to ensure
        partial content is persisted even on abort or error.
        """
        if not blocks:
            return
        from database import db
        try:
            await db.messages.put({
                "id": str(uuid4()),
                "session_id": session_id,
                "role": "assistant",
                "content": blocks,
                "model": model,
                "created_at": datetime.now().isoformat(),
            })
        except Exception as exc:
            logger.warning(
                "Failed to save assistant message%s for session %s: %s",
                f" ({label})" if label else "", session_id, exc,
            )

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
            # Slot available by count — also check spawn budget so we
            # don't spawn into memory pressure even if under MAX_CONCURRENT.
            from .resource_monitor import resource_monitor
            budget = resource_monitor.spawn_budget()
            if not budget.can_spawn:
                logger.warning(
                    "session_router: slot available but spawn budget denied "
                    "session_id=%s reason=%s",
                    requesting_unit.session_id, budget.reason,
                )
                # Try to free memory via eviction first
                if await self._evict_idle(exclude=requesting_unit):
                    resource_monitor.invalidate_cache()
                    budget = resource_monitor.spawn_budget()
                    if budget.can_spawn:
                        return "ready"
                # Still not enough — raise instead of silently spawning
                from .exceptions import ResourceExhaustedException
                raise ResourceExhaustedException(
                    message=budget.reason,
                    detail=(
                        f"available={budget.available_mb:.0f}MB, "
                        f"cost={budget.estimated_cost_mb:.0f}MB, "
                        f"headroom={budget.headroom_mb:.0f}MB"
                    ),
                )
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

        Fires lifecycle hooks before killing (Gap 1 fix) so that
        DailyActivity extraction, auto-commit, and distillation run
        for the evicted session's conversation.
        """
        idle_units = [
            u for u in self._units.values()
            if u.state == SessionState.IDLE and u is not exclude
        ]
        if not idle_units:
            return False

        # Resource-aware eviction: prefer the unit consuming the most
        # memory (RSS) so the freed slot gives maximum headroom for the
        # incoming spawn.  Falls back to oldest-idle when metrics are
        # unavailable (e.g. psutil not installed).
        def _eviction_key(u: SessionUnit) -> tuple:
            metrics = getattr(u, "_last_metrics", None)
            rss = metrics.rss_bytes if metrics else 0
            # Primary: highest RSS first (negative for descending sort)
            # Secondary: oldest idle first (ascending last_used)
            return (-rss, u.last_used)

        idle_units.sort(key=_eviction_key)
        victim = idle_units[0]
        logger.info(
            "session_router.evict session_id=%s (idle %.0fs)",
            victim.session_id,
            time.time() - victim.last_used,
        )

        # Fire hooks before killing — Gap 1 fix
        if self._lifecycle_manager and not victim._hooks_enqueued:
            await self._lifecycle_manager.enqueue_hooks_for_unit(victim)
            victim._hooks_enqueued = True

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

    # ── Public API ────────────────────────────────────────────────

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
        """Entry point for chat requests.

        1. Get or create SessionUnit
        2. Build options via PromptBuilder
        3. Acquire slot (evict IDLE if needed, queue if full)
        4. Delegate to SessionUnit.send()
        5. Yield SSE events
        """
        from .session_utils import _build_error_event

        # Resolve session_id — use provided or generate
        if session_id is None:
            session_id = str(uuid4())

        unit = self.get_or_create_unit(session_id, agent_id)

        # Acquire concurrency slot — may queue with SSE indicator
        # Check if we need to queue BEFORE blocking, so we can emit the
        # queued event immediately (user sees "Waiting..." not silence)
        needs_queue = (
            not unit.is_alive
            and self.alive_count >= self.MAX_CONCURRENT
            and not any(
                u.state == SessionState.IDLE and u is not unit
                for u in self._units.values()
            )
        )
        if needs_queue:
            yield {"type": "queued", "position": 1, "estimatedWaitMs": self.QUEUE_TIMEOUT * 1000}

        slot_result = await self._acquire_slot(unit)
        if slot_result == "timeout":
            yield _build_error_event(
                code="QUEUE_TIMEOUT",
                message="Both chat slots are busy. Please wait a moment and try again.",
                suggested_action="Your conversation is saved. The other tabs are still processing.",
            )
            return

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

        # Detect cold-start resume (Mechanism B): subprocess is gone but
        # session has prior messages in DB (app restarted or session evicted).
        # Set context injection flags so build_system_prompt() injects
        # prior conversation into the system prompt.
        #
        # Why _sdk_session_id is None here:
        #   On cold resume the CLI subprocess has been killed (app restart or
        #   eviction).  The SDK session ID only exists while a subprocess is
        #   alive — it's assigned by SessionUnit._spawn() and cleared on kill.
        #   A None value distinguishes cold resume (Mechanism B: inject prior
        #   conversation into system prompt) from live resume (Mechanism A:
        #   pass resume=sdk_session_id to the SDK so the CLI restores its own
        #   conversation state).  See also: resume_session_id kwarg below.
        from database import db
        is_cold_resume = (
            unit.state == SessionState.COLD
            and unit._sdk_session_id is None
            and session_id is not None
        )
        if is_cold_resume:
            msg_count = await db.messages.count_by_session(session_id)
            if msg_count > 0:
                agent_config["needs_context_injection"] = True
                agent_config["resume_app_session_id"] = session_id
                yield {"type": "session_resuming", "sessionId": session_id}

        # resume_session_id is the SDK's own session ID for Mechanism A (live
        # resume).  On cold resume this is always None — that's correct: the
        # subprocess is dead, so there's no SDK session to resume.  Instead,
        # cold resume injects prior conversation via system prompt (Mechanism B).
        options = await self._prompt_builder.build_options(
            agent_config=agent_config,
            enable_skills=enable_skills,
            enable_mcp=enable_mcp,
            resume_session_id=unit._sdk_session_id,
            channel_context=channel_context,
            editor_context=editor_context,
        )

        # Copy system prompt metadata to registry for TSCC viewer
        _spm = agent_config.get("_system_prompt_metadata")
        if _spm and session_id:
            from . import session_registry
            session_registry.system_prompt_metadata[session_id] = _spm

        # Delegate to SessionUnit — wrap with message persistence
        from .session_manager import session_manager

        # Save user message to DB

        user_content = content if content else [{"type": "text", "text": user_message}]
        title = (user_message or "Chat")[:50]
        await session_manager.store_session(session_id, agent_id, title)
        await db.messages.put({
            "id": str(uuid4()),
            "session_id": session_id,
            "role": "user",
            "content": user_content,
            "model": None,
            "created_at": datetime.now().isoformat(),
        })

        # ── Attachment persistence: save base64 files to Attachments/ ──
        # Claude CLI doesn't support multimodal content blocks via stdin.
        # Convert image/document blocks to text path hints, saving the file
        # data to SwarmWS/Attachments/{date}/ so they're browsable in the
        # Workspace Explorer and persist for the user.
        if (
            not _SDK_SUPPORTS_MULTIMODAL
            and isinstance(query_content, list)
        ):
            query_content = await _convert_unsupported_blocks_to_path_hints(
                query_content, session_id,
            )

        # Stream response and accumulate assistant content for DB persistence
        assistant_blocks: list[dict] = []
        assistant_model: str | None = None

        try:
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
        finally:
            # Save assistant message to DB — runs on normal completion,
            # abort (GeneratorExit), and errors.  Ensures partial
            # streaming content is persisted even if the user clicks Stop.
            await self._persist_assistant_blocks(
                session_id, assistant_blocks, assistant_model,
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
        """Delegate to SessionUnit.continue_with_answer().

        Accumulates assistant content blocks and persists them to DB
        after the stream completes (same pattern as run_conversation).
        """
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return

        assistant_blocks: list[dict] = []
        assistant_model: str | None = None

        try:
            async for event in unit.continue_with_answer(answer):
                if event.get("type") == "assistant" and event.get("content"):
                    for block in event["content"]:
                        assistant_blocks.append(block)
                    if event.get("model"):
                        assistant_model = event["model"]
                yield event
        finally:
            await self._persist_assistant_blocks(
                session_id, assistant_blocks, assistant_model, label="answer",
            )

    async def continue_with_cmd_permission(
        self, session_id: str, request_id: str, allowed: bool,
    ) -> AsyncIterator[dict]:
        """Delegate to SessionUnit.continue_with_permission().

        Accumulates assistant content blocks and persists them to DB
        after the stream completes (same pattern as run_conversation).
        """
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return

        assistant_blocks: list[dict] = []
        assistant_model: str | None = None

        try:
            async for event in unit.continue_with_permission(request_id, allowed):
                if event.get("type") == "assistant" and event.get("content"):
                    for block in event["content"]:
                        assistant_blocks.append(block)
                    if event.get("model"):
                        assistant_model = event["model"]
                yield event
        finally:
            await self._persist_assistant_blocks(
                session_id, assistant_blocks, assistant_model, label="permission",
            )

    async def compact_session(
        self, session_id: str, instructions: Optional[str] = None,
    ) -> dict:
        """Delegate to SessionUnit.compact()."""
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}
        return await unit.compact(instructions)

    async def disconnect_all(self) -> None:
        """Kill all alive SessionUnits. Called at shutdown.

        Fires hooks before killing each unit so DailyActivity, auto-commit,
        and distillation run for every active conversation.
        """
        alive = [u for u in self._units.values() if u.is_alive]
        logger.info("session_router.disconnect_all: killing %d alive units", len(alive))
        for unit in alive:
            try:
                # Fire hooks before killing (shutdown fix)
                if self._lifecycle_manager and not unit._hooks_enqueued:
                    await self._lifecycle_manager.enqueue_hooks_for_unit(unit)
                    unit._hooks_enqueued = True
                await unit.kill()
                unit.clear_session_identity()
            except Exception as exc:
                logger.warning(
                    "Failed to kill unit %s during disconnect_all: %s",
                    unit.session_id, exc,
                )
