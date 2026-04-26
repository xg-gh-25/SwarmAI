"""SessionRouter — thin routing layer with dynamic concurrency cap enforcement.

Routes chat requests to the correct ``SessionUnit`` by session ID.
Enforces a dynamic concurrency cap computed from available system RAM
via ``ResourceMonitor.compute_max_tabs()`` by evicting idle units or
queuing requests when all slots are occupied by protected units.

This module contains ONLY routing and cap logic.  No subprocess lifecycle,
prompt building, or hook execution lives here.

Public symbols:

- ``SessionRouter``  — Main class; dispatches to SessionUnits.

Design reference:
    ``.kiro/specs/multi-session-rearchitecture/design.md`` §2 SessionRouter
    ``.kiro/specs/dynamic-tab-scaling/design.md`` §2 SessionRouter._acquire_slot()
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import re
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

# ── Pre-response recall (G3: post-first-message injection) ────────
# Activates RecallEngine L2/L3 using the user's actual query instead
# of generic proactive keywords.  Runs once per session, 150ms timeout.

_RECALL_TIMEOUT_S = 0.15  # 150ms hard timeout (generous for thread + DB)
# Recall budget is intentionally lower than the 15K default in recall_engine.py.
# This injection is additive to an already-assembled system prompt (~30-50K),
# so we cap at 8K to avoid pushing context over budget on large sessions.
_RECALL_MAX_TOKENS = 8_000

_STOP_WORDS: frozenset[str] = frozenset({
    "the", "this", "that", "with", "from", "what", "when", "where",
    "which", "about", "into", "than", "then", "them", "they", "been",
    "being", "have", "has", "had", "does", "did", "doing", "done",
    "will", "would", "could", "should", "shall", "might",
    "can", "may", "also", "just", "more", "most", "some", "any", "please",
    "help", "tell", "want", "need", "know", "like", "look", "show", "check",
    "all", "each", "every", "both", "few", "many", "much", "such",
    "very", "too", "quite", "rather", "only", "even", "still",
    "how", "why", "who", "you", "its", "our", "your", "their", "his", "her",
    "and", "but", "for", "nor", "not", "yet", "are", "was", "were",
    "let", "got", "get", "put", "see", "say", "said", "make", "made",
})


def _extract_query_keywords(message: str) -> str:
    """Extract searchable keywords from user message.  Pure NLP, no LLM.

    Returns a space-separated string of up to 18 terms suitable for
    FTS5 + vector search.  Returns empty string for messages too short
    to produce meaningful recall.
    """
    if not message or len(message.strip()) < 3:
        return ""

    text = message.strip()

    # Strip common conversational filler
    text = re.sub(
        r"^(hey|hi|hello|please|can you|could you|help me|help|swarm)\s+",
        "", text, flags=re.IGNORECASE,
    )

    if not text:
        return ""

    # Strip URLs and file paths before word extraction
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?:^|\s)[~/]\S+", " ", text)

    # Hyphenated compounds first — preserve "session-router", "pre-tool-use"
    # as single terms for better FTS5/vector recall on technical queries.
    compounds = re.findall(r"(?<![a-zA-Z_])([a-zA-Z_]\w+(?:-[a-zA-Z]\w+)+)", text)
    # Strip matched compounds from text to avoid double-counting
    text_stripped = text
    for c in compounds:
        text_stripped = text_stripped.replace(c, " ")

    # English words: keep substantive terms (>2 chars, not stop words).
    # Use [a-zA-Z_] anchor instead of \b — \b doesn't fire at CJK/ASCII
    # boundaries (e.g. "的Memory" misses "Memory" with \b).
    words = [
        w for w in re.findall(r"(?<![a-zA-Z_])([a-zA-Z_]\w{2,})(?!\w)", text_stripped)
        if w.lower() not in _STOP_WORDS
    ]

    # CJK + Kana + Hangul: keep contiguous runs as natural search terms
    cjk = re.findall(r"[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf\uac00-\ud7af]+", text)

    combined = compounds[:3] + words[:10] + cjk[:5]
    return " ".join(combined) if combined else ""


# Module-level cached embedding function — EmbeddingClient init involves
# boto3 client setup (~50ms).  Cache it across calls since it's stateless.
_cached_embed_fn: Any = None  # None = not yet probed, False = unavailable
_cached_embed_fn_probed: bool = False


def _get_cached_embed_fn():
    """Return cached EmbeddingClient.embed_text or None."""
    global _cached_embed_fn, _cached_embed_fn_probed
    if _cached_embed_fn_probed:
        return _cached_embed_fn if _cached_embed_fn else None
    try:
        from .embedding_client import EmbeddingClient
        client = EmbeddingClient()
        _cached_embed_fn = client.embed_text
    except (ImportError, RuntimeError):
        _cached_embed_fn = False  # Permanently unavailable
    _cached_embed_fn_probed = True
    return _cached_embed_fn if _cached_embed_fn else None


def _recall_for_query(query: str, max_tokens: int) -> str:
    """Run hybrid FTS5+vector recall against the Knowledge Library.

    Thin wrapper around existing RecallEngine infrastructure.
    Uses ``open_vec_db()`` context manager for thread-safe connection
    (this runs in ``asyncio.to_thread``).
    Returns formatted recalled content or empty string.
    """
    try:
        from .vec_db import open_vec_db
        from .knowledge_store import KnowledgeStore
        from .recall_engine import RecallEngine

        with open_vec_db() as conn:
            if conn is None:
                return ""

            store = KnowledgeStore(conn)

            # Include TranscriptStore for verbatim conversation recall (L3)
            additional_stores = []
            try:
                from .transcript_indexer import TranscriptStore
                ts = TranscriptStore(conn)
                ts.ensure_tables()
                additional_stores.append(ts)
            except Exception:
                pass  # Transcript recall unavailable — Knowledge-only is fine

            engine = RecallEngine(store, additional_stores=additional_stores)

            embed_fn = _get_cached_embed_fn()

            return engine.recall_knowledge(query, embed_fn=embed_fn, max_tokens=max_tokens)
    except Exception as exc:
        logger.debug("_recall_for_query failed: %s", exc)
        return ""


async def _maybe_inject_recall(
    user_message: str,
    options: Any,
    unit: SessionUnit,
) -> None:
    """Augment system prompt with recalled knowledge from user's actual query.

    Runs ONCE per session on the first user message.  Subsequent messages
    skip (the agent already has context from the first injection).

    Guard rails:
      - Once-per-session flag on unit._recall_injected
      - Channel sessions excluded (quick exchanges don't need deep recall)
      - 150ms hard timeout — recall is enhancement, not critical path
      - Any exception → skip silently, set flag to prevent retry
    """
    if unit._recall_injected:
        return

    # Channel sessions: skip recall, set flag
    if unit.is_channel_session:
        unit._recall_injected = True
        return

    # Extract keywords — skip if message too short/generic
    keywords = _extract_query_keywords(user_message)
    if not keywords:
        unit._recall_injected = True
        return

    try:
        recalled = await asyncio.wait_for(
            asyncio.to_thread(
                _recall_for_query,
                keywords,
                _RECALL_MAX_TOKENS,
            ),
            timeout=_RECALL_TIMEOUT_S,
        )
        if recalled:
            # Append to this options instance only — safe even if options
            # object is rebuilt on retry (system_prompt is a plain str,
            # so += creates a new str object rather than mutating in place).
            options.system_prompt = (
                options.system_prompt + f"\n\n## Recalled Knowledge\n{recalled}"
            )
    except asyncio.TimeoutError:
        logger.debug("Recall timed out (>%sms) for keywords: %s",
                      int(_RECALL_TIMEOUT_S * 1000), keywords[:80])
    except Exception as exc:
        logger.debug("Recall injection failed: %s", exc)
    finally:
        unit._recall_injected = True


def _get_access_hint(ext: str, filename: str) -> str:
    """Return file-type-specific guidance for how the agent should access the file."""
    ext_lower = ext.lower()
    if ext_lower == ".pdf":
        return "use Read tool to read this PDF"
    elif ext_lower in (".pptx", ".ppt"):
        return "use /s_pptx skill to extract slides and content"
    elif ext_lower in (".docx", ".doc"):
        return "use /s_docx skill to extract text and content"
    elif ext_lower in (".xlsx", ".xls"):
        return "use /s_xlsx skill to extract spreadsheet data"
    elif ext_lower in (".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"):
        return "use /s_whisper-transcribe skill to transcribe audio to text"
    elif ext_lower in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        return "video file — extract audio first with ffmpeg, then transcribe"
    elif ext_lower in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return "use Read tool to view this image"
    elif ext_lower in (".svg", ".bmp", ".tiff", ".tif", ".heic", ".heif"):
        return "non-native image format — use Read tool to view"
    elif ext_lower in (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
                        ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go",
                        ".java", ".sh", ".sql", ".html", ".css", ".toml"):
        return "use Read tool to read this text file"
    else:
        return f"use Read tool to access this file"


# MIME types that Claude API accepts natively in content blocks.
# Everything else must be converted to path hints even when SDK supports multimodal.
_CLAUDE_NATIVE_MIMES = {
    # Images
    "image/jpeg", "image/png", "image/gif", "image/webp",
    # Documents (PDF only)
    "application/pdf",
}


async def _convert_non_native_blocks_to_path_hints(
    content: list[dict],
    session_id: str | None,
) -> list[dict]:
    """Convert non-native image/document blocks when SDK supports multimodal.

    Passes through Claude-native blocks (jpeg/png/gif/webp images, PDF docs)
    and converts everything else (office docs, audio, video, non-native images)
    to path hints via the same save-to-Attachments mechanism.
    """
    converted: list[dict] = []
    non_native: list[dict] = []
    for block in content:
        block_type = block.get("type")
        if block_type in ("image", "document"):
            media_type = block.get("source", {}).get("media_type", "")
            if media_type in _CLAUDE_NATIVE_MIMES:
                converted.append(block)  # pass through natively
            else:
                non_native.append(block)
        else:
            converted.append(block)

    if non_native:
        # Reuse the same save-and-hint mechanism for non-native blocks
        hints = await _convert_unsupported_blocks_to_path_hints(
            non_native, session_id,
        )
        converted.extend(hints)

    return converted


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
                # Office documents
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                "application/vnd.ms-powerpoint": ".ppt",
                "application/msword": ".doc",
                "application/vnd.ms-excel": ".xls",
                # Audio/video
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
                "audio/wav": ".wav",
                "audio/ogg": ".ogg",
                "audio/flac": ".flac",
                "audio/aac": ".aac",
                "audio/webm": ".weba",
                "video/mp4": ".mp4",
                "video/quicktime": ".mov",
                "video/x-msvideo": ".avi",
                "video/x-matroska": ".mkv",
                "video/webm": ".webm",
                # Text (large text files also come through base64 path now)
                "text/plain": ".txt",
                "text/csv": ".csv",
                "application/csv": ".csv",
                "text/html": ".html",
                "text/markdown": ".md",
                "application/json": ".json",
                "application/xml": ".xml",
                "text/xml": ".xml",
                "application/x-yaml": ".yaml",
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
                # Generate file-type-specific guidance for the agent
                access_hint = _get_access_hint(ext, file_path.name)
                converted.append({
                    "type": "text",
                    "text": (
                        f"[Attached file: {file_path.name}] "
                        f"saved at {rel_path} — {access_hint}"
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
    """Routes chat requests to SessionUnits with dynamic concurrency cap.

    The concurrency limit is computed at runtime from available system RAM
    via ``ResourceMonitor.compute_max_tabs()`` (range [1, 4]).

    Public API surface consumed by ``routers/chat.py``.

    Invariants:

    - Thin layer: lookup + cap enforcement + delegate.
    - Never touches subprocess directly (delegates to SessionUnit).
    - Concurrency cap is the ONLY cross-unit concern.
    - STREAMING/WAITING_INPUT units are NEVER evicted.
    - Existing alive sessions are never killed when the dynamic limit shrinks.
    """

    QUEUE_TIMEOUT: float = 300.0  # 5 min — channel tasks can be complex

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
        self._slot_lock: asyncio.Lock = asyncio.Lock()
        self._queue: list[asyncio.Future] = []

    # ── Unit management ───────────────────────────────────────────

    @staticmethod
    async def _persist_assistant_blocks(
        session_id: str,
        blocks: list[dict],
        model: str | None,
        label: str = "",
    ) -> bool:
        """Save accumulated assistant content blocks to DB.

        Called from ``finally`` blocks in streaming methods to ensure
        partial content is persisted even on abort or error.

        The DB layer retries transient errors (SQLITE_BUSY) up to 3 times.
        If all retries fail, logs at ERROR level and returns False so the
        caller can notify the frontend.

        Returns:
            True if persisted successfully, False on failure.
        """
        if not blocks:
            return True
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
            return True
        except Exception as exc:
            logger.error(
                "Failed to save assistant message%s for session %s: %s "
                "(content may be lost on resume)",
                f" ({label})" if label else "", session_id, exc,
            )
            return False

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

    # ── Pre-warm (MeshClaw pattern) ──────────────────────────────

    async def prewarm_channel_session(
        self, agent_id: str, channel_context: Optional[dict] = None,
    ) -> Optional[str]:
        """Pre-warm an IDLE subprocess for the channel owner's first message.

        Spawns a CLI subprocess with the full system prompt so it's ready
        for instant adoption when the first real message arrives.  Eliminates
        ~4s cold-start latency after daemon restart.

        Best-effort: returns the temporary session_id on success, None on
        any failure.  Callers should NOT block on this.

        Parameters
        ----------
        agent_id:
            The agent ID to build config for.
        channel_context:
            Optional channel context dict (channel_type, is_owner, etc.)
            for Slack-specific system prompt sections.  Without this, the
            pre-warmed subprocess lacks Channel Security rules.

        Returns:
            Temporary session_id of the pre-warmed unit, or None.
        """
        from .agent_defaults import build_agent_config

        temp_session_id = f"prewarm-{uuid4()}"
        unit = SessionUnit(
            session_id=temp_session_id,
            agent_id=agent_id,
            on_state_change=self._on_unit_state_change,
        )
        # NOTE: unit is NOT registered in _units yet — deferred until spawn
        # succeeds.  This prevents the lifecycle reaper from seeing a
        # half-initialized unit during the async spawn window.

        try:
            agent_config = await build_agent_config(agent_id)
            if not agent_config:
                return None

            options = await self._prompt_builder.build_options(
                agent_config=agent_config,
                enable_skills=True,
                enable_mcp=True,
                channel_context=channel_context,
            )

            # Spawn subprocess → COLD → IDLE
            async for event in unit._ensure_spawned(options, self._config):
                if event.get("_abort"):
                    return None

            if unit.state == SessionState.IDLE:
                # Only register after spawn confirms IDLE — prevents reaper
                # from seeing a COLD/DEAD unit during the spawn window
                self._units[temp_session_id] = unit
                logger.info(
                    "session_router.prewarm_complete session_id=%s",
                    temp_session_id,
                )
                return temp_session_id

            # Unexpected state — don't register
            return None
        except Exception as exc:
            logger.warning("session_router.prewarm_failed: %s", exc)
            return None

    async def adopt_prewarmed_unit(
        self, prewarm_session_id: str, real_session_id: str,
    ) -> bool:
        """Re-key a pre-warmed unit to serve a real session.

        Atomically moves the unit from the temporary pre-warm key to the
        real session_id under _slot_lock.  The unit must be IDLE (alive
        subprocess) for adoption to succeed.

        Uses _slot_lock to prevent TOCTOU race when two coroutines
        (e.g., two simultaneous Slack DMs at startup) both try to adopt
        the same pre-warmed unit.

        Returns True on success, False if the unit doesn't exist, died,
        or was evicted.
        """
        async with self._slot_lock:
            unit = self._units.pop(prewarm_session_id, None)
            if unit is None:
                return False

            if unit.state != SessionState.IDLE:
                # Unit died or was evicted — put back and fail
                self._units[prewarm_session_id] = unit
                logger.info(
                    "session_router.adopt_prewarmed_skip state=%s (expected IDLE)",
                    unit.state.value,
                )
                return False

            unit.session_id = real_session_id
            self._units[real_session_id] = unit
            logger.info(
                "session_router.adopt_prewarmed %s → %s",
                prewarm_session_id, real_session_id,
            )
            return True

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

    # ── Pool counts ──────────────────────────────────────────

    @property
    def _channel_alive_count(self) -> int:
        """Number of alive channel session units."""
        return sum(1 for u in self._units.values() if u.is_alive and u.is_channel_session)

    @property
    def _chat_alive_count(self) -> int:
        """Number of alive chat (non-channel) session units."""
        return sum(1 for u in self._units.values() if u.is_alive and not u.is_channel_session)

    # ── Slot acquisition ─────────────────────────────────────

    async def _acquire_slot(self, requesting_unit: SessionUnit) -> str:
        """Acquire a concurrency slot. Delegates to pool-specific methods.

        Channel units and chat units have separate slot pools:
        - Channel: exactly 1 dedicated slot (serialized)
        - Chat: max_tabs - 1 slots

        Returns:
            "ready" — slot acquired, proceed with send
            "queued" — was queued, now ready
            "timeout" — queue timed out, all slots busy
        """
        # Fast path: already alive — no slot needed
        if requesting_unit.is_alive:
            return "ready"

        if requesting_unit.is_channel_session:
            return await self._acquire_channel_slot(requesting_unit)
        return await self._acquire_chat_slot(requesting_unit)

    async def _acquire_channel_slot(self, requesting_unit: SessionUnit) -> str:
        """Acquire the dedicated channel slot (exactly 1).

        If another channel is IDLE → evict it.
        If another channel is STREAMING → queue with timeout.
        Never touches chat slots.
        """
        from .resource_monitor import resource_monitor

        async with self._slot_lock:
            if self._channel_alive_count == 0:
                # Channel slot is free
                budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                if not budget.can_spawn and self.alive_count > 0:
                    # Try evicting an idle channel first
                    if await self._evict_idle(exclude=requesting_unit, channel_only=True):
                        resource_monitor.invalidate_cache()
                return "ready"

            # Another channel unit is alive — try evicting if IDLE
            if await self._evict_idle(exclude=requesting_unit, channel_only=True):
                return "ready"

        # Channel slot occupied by a protected (STREAMING) unit — queue
        deadline = time.monotonic() + self.QUEUE_TIMEOUT
        logger.info(
            "session_router: channel slot busy, queuing %s (timeout=%.0fs)",
            requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            try:
                self._slot_available.clear()
                await asyncio.wait_for(
                    self._slot_available.wait(), timeout=remaining,
                )
            except asyncio.TimeoutError:
                break

            async with self._slot_lock:
                if self._channel_alive_count == 0:
                    return "queued"
                if await self._evict_idle(exclude=requesting_unit, channel_only=True):
                    return "queued"

        logger.warning(
            "session_router: channel queue timeout for session %s after %.0fs",
            requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )
        return "timeout"

    async def _acquire_chat_slot(self, requesting_unit: SessionUnit) -> str:
        """Acquire a chat slot from the chat pool (max_tabs - 1).

        Never touches the dedicated channel slot.
        """
        from .resource_monitor import resource_monitor

        async with self._slot_lock:
            max_tabs = resource_monitor.compute_max_tabs()
            chat_max = max_tabs - 1  # Reserve 1 for channel

            if self._chat_alive_count < chat_max:
                # First tab is sacred — always allow at least one session
                if self.alive_count > 0:
                    budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                    if not budget.can_spawn:
                        logger.warning(
                            "session_router: slot available but spawn budget denied "
                            "session_id=%s reason=%s",
                            requesting_unit.session_id, budget.reason,
                        )
                        if await self._evict_idle(exclude=requesting_unit):
                            resource_monitor.invalidate_cache()
                            budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                            if budget.can_spawn:
                                return "ready"
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

            # Chat pool full — try evicting a chat IDLE unit
            if await self._evict_idle(exclude=requesting_unit):
                return "ready"

        # All chat slots occupied by protected units — queue with deadline
        deadline = time.monotonic() + self.QUEUE_TIMEOUT
        logger.info(
            "session_router: all chat slots occupied, queuing session %s (timeout=%.0fs)",
            requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # deadline exceeded

            try:
                self._slot_available.clear()
                await asyncio.wait_for(
                    self._slot_available.wait(), timeout=remaining,
                )
            except asyncio.TimeoutError:
                break  # deadline exceeded

            # Re-check under lock after wake
            async with self._slot_lock:
                max_tabs = resource_monitor.compute_max_tabs()
                chat_max = max_tabs - 1
                if self._chat_alive_count < chat_max:
                    budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                    if budget.can_spawn:
                        return "queued"
                if await self._evict_idle(exclude=requesting_unit):
                    return "queued"
            # Slot claimed by another coroutine — loop back to wait

        logger.warning(
            "session_router: queue timeout for session %s after %.0fs",
            requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )
        return "timeout"

    async def _evict_idle(
        self, exclude: SessionUnit, *, channel_only: bool = False,
    ) -> bool:
        """Evict the oldest IDLE unit to free a slot.

        Returns True if a unit was evicted, False if no IDLE units available.
        Only evicts units in IDLE state — STREAMING and WAITING_INPUT are
        protected (Rule 3).

        When *channel_only* is True, only channel IDLE units are eligible
        (used when acquiring a channel slot).  When False, only chat IDLE
        units are eligible — channel units are never evicted for chat
        (slot isolation guarantee).

        Fires lifecycle hooks before killing (Gap 1 fix) so that
        DailyActivity extraction, auto-commit, and distillation run
        for the evicted session's conversation.
        """
        idle_units = [
            u for u in self._units.values()
            if u.state == SessionState.IDLE
            and u is not exclude
            and u.is_channel_session == channel_only
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

        # Tag channel sessions so slot isolation works correctly.
        # channel_context is only set by ChannelGateway, never by chat tabs.
        # Owner messages bypass the channel slot — they use the chat pool
        # so they're never queued behind other users' channel requests.
        is_owner = channel_context.get("is_owner", False) if channel_context else False
        if channel_context and not is_owner and not unit.is_channel_session:
            unit.is_channel_session = True

        # ── Persist user message BEFORE slot acquisition ──
        # Critical: If slot acquisition times out (QUEUE_TIMEOUT), the
        # method returns early.  The user message MUST already be in DB
        # so that cold resume (Mechanism B) can inject it later.
        # Without this, the 3rd tab's message is silently lost.
        from database import db
        from .session_manager import session_manager

        user_content = content if content else (
            [{"type": "text", "text": user_message}] if user_message else None
        )
        if user_content:
            title = (user_message or "Chat")[:50]
            try:
                await session_manager.store_session(session_id, agent_id, title)
                await db.messages.put({
                    "id": str(uuid4()),
                    "session_id": session_id,
                    "role": "user",
                    "content": user_content,
                    "model": None,
                    "created_at": datetime.now().isoformat(),
                })
            except Exception as exc:
                # Non-fatal: proceed even if persist fails.  The message
                # will still be sent to the agent (just not in DB for
                # future cold resume).  Log at ERROR so it's visible.
                logger.error(
                    "Failed to persist user message for session %s: %s",
                    session_id, exc,
                )

        # Acquire concurrency slot — may queue with SSE indicator
        # Check if we need to queue BEFORE blocking, so we can emit the
        # queued event immediately (user sees "Waiting..." not silence)
        from .resource_monitor import resource_monitor as _rm_check
        _current_max = _rm_check.compute_max_tabs()
        needs_queue = (
            not unit.is_alive
            and self.alive_count >= _current_max
            and not any(
                u.state == SessionState.IDLE and u is not unit
                for u in self._units.values()
            )
        )
        if needs_queue:
            yield {"type": "queued", "position": 1, "estimatedWaitMs": self.QUEUE_TIMEOUT * 1000}

        slot_result = await self._acquire_slot(unit)
        if slot_result == "timeout":
            error_event = _build_error_event(
                code="QUEUE_TIMEOUT",
                message="All chat slots are busy. Please wait a moment and try again.",
                suggested_action="Your message is saved. Send again when a slot opens.",
            )
            # Include retry payload so frontend can re-send the exact message
            error_event["retryPayload"] = {
                "sessionId": session_id,
                "agentId": agent_id,
                "userMessage": user_message,
                "content": content,
            }
            yield error_event
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
        is_cold_resume = (
            unit.state == SessionState.COLD
            and unit._sdk_session_id is None
            and session_id is not None
        )
        # Log cold resume detection inputs for diagnostics (COE: 2026-04-02
        # resume context silently skipped — no visibility into why).
        logger.info(
            "cold_resume_check session_id=%s state=%s sdk_session=%s "
            "→ is_cold_resume=%s",
            session_id,
            unit.state.value if unit.state else "None",
            "set" if unit._sdk_session_id else "None",
            is_cold_resume,
        )
        # Channel TTL rotation: the gateway created a fresh session_id but
        # the prior session's messages should carry forward for continuity.
        # prior_session_id is set by gateway._resolve_session on TTL rotation.
        prior_session_id = (
            channel_context.get("prior_session_id") if channel_context else None
        )
        if is_cold_resume:
            # Check current session first (normal cold resume: app restart)
            resume_from = session_id
            msg_count = await db.messages.count_by_session(session_id)
            if msg_count <= 1 and prior_session_id:
                # TTL rotation: new session has no history, but the old one does.
                # Inject prior session's conversation for continuity.
                prior_count = await db.messages.count_by_session(prior_session_id)
                if prior_count > 0:
                    resume_from = prior_session_id
                    msg_count = prior_count + 1  # ensure > 1 check passes
            # msg_count > 1 because the current user message was already
            # persisted above (before slot acquisition).  A truly new session
            # has exactly 1 message (the one we just saved).  Cold resume
            # requires at least 2 (prior conversation + current message).
            logger.info(
                "cold_resume_decision session_id=%s msg_count=%d "
                "→ injecting=%s",
                session_id, msg_count, msg_count > 1,
            )
            if msg_count > 1:
                agent_config["needs_context_injection"] = True
                agent_config["resume_app_session_id"] = resume_from
                yield {"type": "session_resuming", "sessionId": session_id}

        # resume_session_id is the SDK's own session ID for Mechanism A (live
        # resume).  On cold resume this is always None — that's correct: the
        # subprocess is dead, so there's no SDK session to resume.  Instead,
        # cold resume injects prior conversation via system prompt (Mechanism B).
        # Use a STABLE mutable dict for session_context so hook closures
        # (dangerous_command_gate, pre_compact_hook) always see the current
        # session_id — even when the subprocess is reused across sends.
        # On first call, create the dict and store it on the unit.
        # On subsequent calls, update the existing dict in-place.
        if unit._hook_session_context is None:
            unit._hook_session_context = {"sdk_session_id": session_id}
        else:
            unit._hook_session_context["sdk_session_id"] = session_id

        options = await self._prompt_builder.build_options(
            agent_config=agent_config,
            enable_skills=enable_skills,
            enable_mcp=enable_mcp,
            resume_session_id=unit._sdk_session_id,
            session_context=unit._hook_session_context,
            channel_context=channel_context,
            editor_context=editor_context,
            extra_mcps=unit._extra_mcps or None,
        )

        # Copy system prompt metadata to registry for TSCC viewer
        _spm = agent_config.get("_system_prompt_metadata")
        if _spm and session_id:
            from . import session_registry
            session_registry.system_prompt_metadata[session_id] = _spm

        # Delegate to SessionUnit — stream response

        # ── Attachment persistence: save base64 files to Attachments/ ──
        # Claude CLI doesn't support multimodal content blocks via stdin.
        # Convert image/document blocks to text path hints, saving the file
        # data to SwarmWS/Attachments/{date}/ so they're browsable in the
        # Workspace Explorer and persist for the user.
        #
        # When _SDK_SUPPORTS_MULTIMODAL is True, we STILL convert non-native
        # blocks (office docs, audio, video) — Claude API only accepts
        # native images (jpeg/png/gif/webp) and PDF documents natively.
        if isinstance(query_content, list):
            if not _SDK_SUPPORTS_MULTIMODAL:
                # Convert ALL image/document blocks to path hints
                query_content = await _convert_unsupported_blocks_to_path_hints(
                    query_content, session_id,
                )
            else:
                # SDK supports multimodal — only convert non-native blocks
                query_content = await _convert_non_native_blocks_to_path_hints(
                    query_content, session_id,
                )

        # ── Resolve user text once (used by recall injection + shadow) ──
        _user_text = user_message or (
            query_content if isinstance(query_content, str) else ""
        )

        # ── G3: Pre-response recall injection ─────────────────────
        # Inject recalled knowledge based on user's actual first message.
        # Replaces the old proactive-keyword recall (in prompt_builder.py)
        # which used generic focus keywords before the user typed.
        if _user_text:
            await _maybe_inject_recall(
                user_message=_user_text,
                options=options,
                unit=unit,
            )

        # ── G3: Shadow recall — fire-and-forget quality validation ──
        # Runs recall against the user's actual message, logs results
        # to .context/recall_shadow.jsonl. Never blocks, never injects.
        if _user_text:
            from core.initialization_manager import initialization_manager
            _ws = initialization_manager.get_cached_workspace_path()
            if _ws:
                _task = asyncio.create_task(
                    _shadow_recall(
                        session_id=session_id,
                        user_message=_user_text,
                        working_directory=_ws,
                        is_channel=unit.is_channel_session,
                    ),
                )
                _task.add_done_callback(_shadow_task_done)

        # Stream response — persist each assistant message IMMEDIATELY.
        #
        # Why incremental (not accumulate-then-flush):
        #   SIGKILL (macOS jetsam / OOM) is non-catchable — Python's `finally`
        #   block does NOT execute.  If we only persist at stream end, all
        #   in-flight assistant content (text, tool_use, tool_result) is lost
        #   when the process is killed.  By persisting each AssistantMessage
        #   as it arrives, we guarantee crash recovery up to the last emitted
        #   message.  The cost is one small DB write per assistant turn — a
        #   typical conversation has 5-15 of these, each <10KB.
        try:
            async for event in unit.send(
                query_content=query_content,
                options=options,
                app_session_id=session_id,
                config=self._config,
            ):
                # Persist assistant content blocks immediately — crash-safe
                if event.get("type") == "assistant" and event.get("content"):
                    await self._persist_assistant_blocks(
                        session_id, event["content"], event.get("model"),
                    )

                yield event
        except Exception as send_err:
            # SessionBusyError: session is actively streaming, reject new send.
            # Yield structured error so frontend can queue the message.
            from .exceptions import SessionBusyError
            if isinstance(send_err, SessionBusyError):
                logger.info(
                    "session_router.session_busy session_id=%s — "
                    "yielding SESSION_BUSY error to frontend",
                    session_id,
                )
                # Delete the orphaned user message that was persisted before
                # slot acquisition.  Without this, cold resume would inject
                # a message that was never actually sent to the agent.
                if user_content:
                    try:
                        await db.messages.delete_last_user_message(session_id)
                        logger.info(
                            "session_router.deleted_orphan_msg session_id=%s",
                            session_id,
                        )
                    except Exception as del_exc:
                        logger.warning(
                            "session_router.orphan_msg_delete_failed "
                            "session_id=%s: %s",
                            session_id, del_exc,
                        )
                yield _build_error_event(
                    code="SESSION_BUSY",
                    message=str(send_err.message),
                    suggested_action=str(send_err.suggested_action),
                )
                return
            raise  # Re-raise non-SessionBusyError exceptions

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

        Persists each assistant message immediately (crash-safe).
        """
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return

        async for event in unit.continue_with_answer(answer):
            if event.get("type") == "assistant" and event.get("content"):
                await self._persist_assistant_blocks(
                    session_id, event["content"], event.get("model"),
                    label="answer",
                )
            yield event

    async def continue_with_cmd_permission(
        self, session_id: str, request_id: str, allowed: bool,
    ) -> AsyncIterator[dict]:
        """Delegate to SessionUnit.continue_with_permission().

        Persists each assistant message immediately (crash-safe).
        """
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return

        async for event in unit.continue_with_permission(request_id, allowed):
            if event.get("type") == "assistant" and event.get("content"):
                await self._persist_assistant_blocks(
                    session_id, event["content"], event.get("model"),
                    label="permission",
                )
            yield event

    async def compact_session(
        self, session_id: str, instructions: Optional[str] = None,
    ) -> dict:
        """Delegate to SessionUnit.compact()."""
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}
        return await unit.compact(instructions)

    async def enable_mcp_for_session(
        self, session_id: str, mcp_name: str,
    ) -> dict:
        """Activate a deferred MCP for a session via kill+respawn.

        The session must be IDLE (not streaming). Kills the subprocess so
        the next ``send()`` spawns fresh with the updated MCP list.
        The caller is responsible for updating the MCP config (e.g. changing
        the entry's tier from ``ondemand`` to ``always`` for this session).

        Returns dict with success status and message.
        """
        unit = self.get_unit(session_id)
        if unit is None:
            return {
                "success": False,
                "message": f"Session {session_id} not found",
            }
        try:
            await unit.reclaim_for_mcp_swap(mcp_name=mcp_name)
            logger.info(
                "Reclaimed session %s for MCP swap (requested: %s)",
                session_id, mcp_name,
            )
            return {
                "success": True,
                "message": f"Session reclaimed for MCP '{mcp_name}'. "
                           f"Next message will spawn with updated MCPs.",
            }
        except RuntimeError as exc:
            return {"success": False, "message": str(exc)}

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


# ── G3: Shadow Recall — quality validation (no prompt injection) ─────
#
# Runs recall against the user's actual first message and logs
# results + timing to .context/recall_shadow.jsonl.  Fire-and-forget:
# never blocks the response stream, never injects into the prompt.
# Data collected here drives the decision to wire recall into production.

# Bounded dedup: track which sessions have been shadowed.
# OrderedDict[session_id → timestamp] with max 500 entries; FIFO eviction.
# O(1) insertion and eviction (vs O(n) min-scan with plain dict).
_shadowed_sessions: OrderedDict[str, float] = OrderedDict()
_SHADOW_MAX_ENTRIES = 500


def _shadow_task_done(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget shadow tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.debug("Shadow recall task failed (non-blocking): %s", exc)


async def _shadow_recall(
    session_id: str,
    user_message: str,
    working_directory: str,
    is_channel: bool,
) -> None:
    """Run recall in shadow mode — log results, never inject.

    Called as a fire-and-forget task from run_conversation().
    Tries FTS5-only first, then FTS5+embedding, logs both timings.
    """
    # AC5: Skip channel sessions (Slack = quick exchanges, no recall value)
    if is_channel:
        return None

    # Once per session — bounded dedup with FIFO eviction (O(1))
    if session_id in _shadowed_sessions:
        return None
    if len(_shadowed_sessions) >= _SHADOW_MAX_ENTRIES:
        _shadowed_sessions.popitem(last=False)  # O(1) FIFO eviction
    _shadowed_sessions[session_id] = time.monotonic()

    # Skip very short messages (greetings, "hi", etc.)
    # CJK characters are semantically dense — "评估下" (3 chars) is a valid query.
    # Use threshold 2 if CJK detected, 5 otherwise.
    text = user_message.strip() if user_message else ""
    _has_cjk = any("\u4e00" <= c <= "\u9fff" for c in text[:10])
    _min_len = 2 if _has_cjk else 5
    if len(text) < _min_len:
        return None

    wd = Path(working_directory)
    ctx_dir = wd / ".context"
    # Shadow log — temporary validation data. Delete or rotate after analysis
    # (expected lifecycle: 2-4 weeks, ~500 bytes/entry, ~50 entries/day).
    log_path = ctx_dir / "recall_shadow.jsonl"

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "query": text[:200],  # Cap for log readability
        "injected": False,  # Shadow mode — always False
    }

    try:
        # Resolve DB path — check workspace-local first, then global
        db_path = Path(working_directory) / "data.db"
        if not db_path.exists():
            db_path = Path.home() / ".swarm-ai" / "data.db"
        if not db_path.exists():
            entry["fts5"] = {"ms": 0, "hits": 0, "error": "no_db"}
            entry["embedding"] = {"ms": 0, "hits": 0, "error": "no_db"}
            _append_jsonl(log_path, entry)
            return None

        # Run in thread pool — sqlite3 is not async-safe
        fts5_result, embed_result = await asyncio.to_thread(
            _run_dual_recall, str(db_path), text,
        )
        entry["fts5"] = fts5_result
        entry["embedding"] = embed_result

    except Exception as exc:
        entry["error"] = str(exc)[:200]
        entry.setdefault("fts5", {"ms": 0, "hits": 0, "error": str(exc)[:100]})
        entry.setdefault("embedding", {"ms": 0, "hits": 0, "error": str(exc)[:100]})

    _append_jsonl(log_path, entry)
    return None


def _run_dual_recall(db_path: str, query: str) -> tuple[dict, dict]:
    """Run FTS5-only and FTS5+embedding recall, return timing dicts.

    Runs synchronously on a thread-pool worker (called from asyncio.to_thread).

    Both paths create their own connections — the singleton ``get_vec_conn()``
    is designed for main-thread use and is NOT safe to call from arbitrary
    thread-pool workers.  ``open_vec_db()`` context manager creates and closes
    a fresh vec-enabled connection per call, which is thread-safe.
    """
    import sqlite3
    import time

    from .recall_engine import RecallEngine
    from .knowledge_store import KnowledgeStore
    from .vec_db import open_vec_db

    # ── Path 1: FTS5-only (plain connection, no vec extension needed) ──
    fts5_result: dict[str, Any] = {"ms": 0, "hits": 0}
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        store = KnowledgeStore(conn)
        engine = RecallEngine(store)

        try:
            t0 = time.perf_counter()
            fts5_text = engine.recall_knowledge(query, embed_fn=None, max_tokens=4000)
            t1 = time.perf_counter()

            fts5_result["ms"] = round((t1 - t0) * 1000, 1)
            fts5_result["hits"] = fts5_text.count("**[") if fts5_text else 0
            fts5_result["chars"] = len(fts5_text) if fts5_text else 0
        except Exception as exc:
            fts5_result["error"] = str(exc)[:100]
    finally:
        conn.close()

    # ── Path 2: FTS5 + Embedding (fresh vec-enabled connection per call) ──
    embed_result: dict[str, Any] = {"ms": 0, "hits": 0}
    with open_vec_db(Path(db_path)) as vec_conn:
        if vec_conn is None:
            embed_result["error"] = "no_sqlite_vec"
            return fts5_result, embed_result

        store = KnowledgeStore(vec_conn)
        engine = RecallEngine(store)

        try:
            embed_fn = _get_cached_embed_fn()
            if embed_fn is None:
                embed_result["error"] = "no_bedrock"
            else:
                t0 = time.perf_counter()
                embed_text = engine.recall_knowledge(query, embed_fn=embed_fn, max_tokens=4000)
                t1 = time.perf_counter()

                embed_result["ms"] = round((t1 - t0) * 1000, 1)
                embed_result["hits"] = embed_text.count("**[") if embed_text else 0
                embed_result["chars"] = len(embed_text) if embed_text else 0
        except Exception as exc:
            embed_result["error"] = str(exc)[:100]

    return fts5_result, embed_result


_JSONL_MAX_LINES = 1000


def _append_jsonl(path: Path, entry: dict) -> None:
    """Append a JSON line, rotating to keep at most _JSONL_MAX_LINES entries."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
        # Rotate: if file exceeds max lines, keep the newest half.
        # Check only every ~100 writes (stat is cheap, rewrite is not).
        try:
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) > _JSONL_MAX_LINES:
                keep = lines[-(_JSONL_MAX_LINES // 2):]
                path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except Exception:
            pass  # Rotation failure is non-critical
    except Exception:
        pass  # Shadow mode — never crash
