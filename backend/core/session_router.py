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

    Graph enrichment: extracts entities from query, queries knowledge graph
    for related entry IDs/titles, appends them to enrich the recall context.
    This ensures graph-connected knowledge surfaces even when keyword/vector
    match is weak.

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

            recalled = engine.recall_knowledge(query, embed_fn=embed_fn, max_tokens=max_tokens)

            # Graph enrichment: append related entry context
            graph_context = _graph_enrich_recall(query)
            if graph_context and recalled:
                recalled = recalled + "\n\n" + graph_context
            elif graph_context:
                recalled = graph_context

            return recalled
    except Exception as exc:
        logger.debug("_recall_for_query failed: %s", exc)
        return ""


def _graph_enrich_recall(query: str) -> str:
    """Extract entities from query, find graph-connected knowledge entries.

    Returns a short context block of related entry IDs or empty string.
    Best-effort: any failure returns empty (never blocks recall).
    """
    try:
        import re as _re
        from pathlib import Path as _Path
        from .knowledge_graph import load_graph, query_related_entries

        graph_path = _Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / ".knowledge-graph.yaml"
        if not graph_path.exists():
            return ""

        # Extract entities from query
        entities: list[str] = []
        # File patterns
        files = _re.findall(r'\b([\w\-/]+\.(?:py|ts|tsx|rs|sh|md|yaml|json))\b', query)
        entities.extend(f for f in files if len(f) >= 6)
        # MEMORY entry IDs
        ids = _re.findall(r'\b(COE\d+|KD\d+|LL\d+|RC\d+|C\d{3,}|E\d{3,})\b', query)
        entities.extend(ids)
        # Module names (snake_case, ≥8 chars)
        modules = _re.findall(r'\b([\w_]{8,})\b', query)
        entities.extend(m for m in modules if "_" in m and not m.startswith("__"))

        if not entities:
            return ""

        rels = load_graph(graph_path)
        if not rels:
            return ""

        related = query_related_entries(rels, entities)
        if not related:
            return ""

        # Format as a brief context hint (not full content — just IDs for awareness)
        lines = ["## Graph-Connected Knowledge"]
        lines.append(f"Entities in query: {', '.join(entities[:5])}")
        lines.append(f"Related via knowledge graph: {', '.join(related[:10])}")
        return "\n".join(lines)
    except Exception:
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
                from jobs.paths import SWARMWS as _SWARMWS
                attach_dir = _SWARMWS / "Attachments"
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
    EVICTION_GRACE_SECONDS: int = 300  # 5 min — protect recently-active sessions
    # Max time a queued waker sleeps before re-evaluating, even if no _slot_available
    # wake arrives. Bounds two hazards exposed once the wake path became
    # grace-respecting (force=False): (1) a lost wakeup on the shared _slot_available
    # Event (a peer waker's clear() can swallow a set()), and (2) the case where a
    # within-grace idle becomes STALE while the waker sleeps — without re-polling it
    # would wait the full QUEUE_TIMEOUT instead of evicting the now-stale unit.
    # Re-polling turns "stall up to 300s" into "re-check every WAKE_REPOLL_SECONDS".
    WAKE_REPOLL_SECONDS: float = 5.0

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

        # Root-1 SSOT Phase 2 (L3): serial drain worker. Session ids whose
        # transition reached a clean IDLE are pushed here; a single long-lived
        # consumer coalesce-drains each session's pending messages ONE turn at a
        # time, OUTSIDE any transition stack (F1 — never re-enter send() from
        # within _transition / _on_unit_state_change). Lazily started on first
        # enqueue so it binds to the running event loop.
        self._drain_queue: asyncio.Queue[str] = asyncio.Queue()
        self._drain_worker_task: Optional[asyncio.Task] = None
        self._drain_enqueued: set[str] = set()  # de-dupe: at most one pending entry/session

        # Load persisted sdk_session_ids for lazy injection at unit creation.
        # Design §2B fix: old restore_session_state() iterated _units which is
        # empty at boot. This caches the mapping; get_or_create_unit() injects.
        self._persisted_sdk_ids: dict[str, str] = {}
        try:
            from .session_state_persistence import load_persisted_state
            from jobs.paths import APP_DATA_DIR

            state_file = APP_DATA_DIR / "session_state.json"
            self._persisted_sdk_ids = load_persisted_state(state_file)
            if self._persisted_sdk_ids:
                logger.info(
                    "Cached %d persisted session identities for lazy injection",
                    len(self._persisted_sdk_ids),
                )
        except Exception as exc:
            logger.debug("Could not load persisted session state: %s", exc)

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
        """Get existing or create new COLD SessionUnit.

        On creation, injects any persisted sdk_session_id from the cache
        (loaded at __init__ from session_state.json). This enables fast
        --resume after daemon restart without requiring boot-time restore
        on an empty _units dict.
        """
        unit = self._units.get(session_id)
        if unit is None:
            unit = SessionUnit(
                session_id=session_id,
                agent_id=agent_id,
                on_state_change=self._on_unit_state_change,
            )
            # Lazy-inject persisted sdk_session_id (Design §2B fix).
            # pop() ensures one-shot consumption — no stale reuse.
            persisted_id = self._persisted_sdk_ids.pop(session_id, None)
            if persisted_id:
                unit._sdk_session_id = persisted_id
                logger.info(
                    "Injected persisted sdk_session_id for session %s (fast --resume enabled)",
                    session_id,
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

            # Per-channel model override — same logic as run_conversation().
            # Must be applied BEFORE build_options() so resolve_model() picks
            # it up.  Without this, pre-warmed subprocess spawns with the
            # global default model and can't switch after spawn (SDK constraint).
            if channel_context and channel_context.get("model"):
                agent_config["model"] = channel_context["model"]

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

        _needs_queue = False
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
                        # Grace period blocked eviction — queue and wait for a
                        # slot to free naturally before force-killing. Eviction
                        # cost (800K token context lost) >> queue cost (60s wait).
                        # Evidence: 28 exit-9 kills in 24h from immediate force.
                        _needs_queue = True
                if not _needs_queue:
                    return "ready"

            if not _needs_queue:
                # Chat pool full — try evicting a chat IDLE unit
                if await self._evict_idle(exclude=requesting_unit):
                    return "ready"

        # All chat slots occupied OR budget-denied with grace block — queue with deadline
        deadline = time.monotonic() + self.QUEUE_TIMEOUT
        reason = "budget_denied_grace_block" if _needs_queue else "all_slots_occupied"
        logger.info(
            "session_router: queuing session %s reason=%s (timeout=%.0fs)",
            requesting_unit.session_id, reason, self.QUEUE_TIMEOUT,
        )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # deadline exceeded

            try:
                self._slot_available.clear()
                # Cap the wait at WAKE_REPOLL_SECONDS so a lost wakeup (shared
                # Event clear/set race) or a within-grace idle that ages into
                # stale does not leave the waker asleep until the full deadline.
                # On repoll timeout we loop and re-evaluate the slot/evict state
                # rather than giving up — only the OUTER deadline (remaining<=0)
                # breaks to the force=True last resort.
                await asyncio.wait_for(
                    self._slot_available.wait(),
                    timeout=min(remaining, self.WAKE_REPOLL_SECONDS),
                )
            except asyncio.TimeoutError:
                # Inner repoll elapsed but outer deadline may remain — loop back
                # to re-check. The top-of-loop `remaining <= 0` guard handles the
                # true deadline; this except no longer means "give up".
                continue

            # Re-check under lock after wake. Use force=FALSE here: a wake fires
            # whenever ANY session goes idle (_on_unit_state_change sets
            # _slot_available on streaming→idle). If we forced here, a queued
            # waker would force-kill a peer that *just* went idle (within grace)
            # the instant it answered — the 2026-06-21 incident: a queued
            # reconnect session force-killed a user's warm tab 66ms after it
            # answered (idle 0s), then the user waited 2m28s to re-acquire.
            # force=False lets the grace filter protect freshly-idled tabs; the
            # waker still progresses by evicting any STALE (>grace) idle, or via
            # the timeout last-resort below. Eviction cost (warm context lost +
            # cold resume) >> a bounded wait.
            async with self._slot_lock:
                max_tabs = resource_monitor.compute_max_tabs()
                chat_max = max_tabs - 1
                if self._chat_alive_count < chat_max:
                    budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                    if budget.can_spawn:
                        return "queued"
                if await self._evict_idle(exclude=requesting_unit, force=False):
                    return "queued"
            # Slot claimed by another coroutine — loop back to wait

        # Queue timed out — last resort: force-evict the longest-idle session
        # even if within grace period. Better than failing the user request.
        # MUST stay force=True — this is the SOLE anti-starvation guarantee once
        # the per-wake path (above) was made grace-respecting. Do not soften.
        async with self._slot_lock:
            if await self._evict_idle(exclude=requesting_unit, force=True):
                logger.info(
                    "session_router: queue timeout force-evict succeeded for %s",
                    requesting_unit.session_id,
                )
                return "queued"

        logger.warning(
            "session_router: queue timeout for session %s after %.0fs",
            requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )
        return "timeout"

    async def _evict_idle(
        self, exclude: SessionUnit, *, channel_only: bool = False,
        force: bool = False,
    ) -> bool:
        """Evict the oldest IDLE unit to free a slot.

        Returns True if a unit was evicted, False if no IDLE units available.
        Only evicts units in IDLE state — STREAMING and WAITING_INPUT are
        protected (Rule 3).

        When *channel_only* is True, only channel IDLE units are eligible
        (used when acquiring a channel slot).  When False, only chat IDLE
        units are eligible — channel units are never evicted for chat
        (slot isolation guarantee).

        Grace period (chat only, not channel):
        - Sessions idle < EVICTION_GRACE_SECONDS are protected from eviction
          unless *force* is True. This prevents ping-pong eviction when all
          3 chat slots are occupied by recently-active tabs.
        - *force=True* bypasses the grace period — used after queue timeout
          as a last resort.
        - Channel eviction (channel_only=True) always ignores grace period
          because there's exactly 1 channel slot with no user-visible tab.

        Fires lifecycle hooks before killing (Gap 1 fix) so that
        DailyActivity extraction, auto-commit, and distillation run
        for the evicted session's conversation.
        """
        idle_units = [
            u for u in self._units.values()
            if u.state == SessionState.IDLE
            and not u.is_post_disconnect_flushing  # Option B-soft: don't kill a long turn finishing post-disconnect
            and u is not exclude
            and u.is_channel_session == channel_only
        ]
        if not idle_units:
            return False

        # Grace period: for chat eviction (not channel), filter out
        # sessions that have been idle less than EVICTION_GRACE_SECONDS
        # unless force=True (queue timeout fallback).
        now = time.time()
        if not channel_only and not force:
            stale_units = [
                u for u in idle_units
                if (now - u.last_used) >= self.EVICTION_GRACE_SECONDS
            ]
            if not stale_units:
                # All sessions are "hot" (recently active) — refuse eviction.
                # Caller should queue and retry, or use force=True.
                logger.info(
                    "session_router.evict_blocked: all %d idle sessions within "
                    "grace period (%ds), refusing eviction",
                    len(idle_units), self.EVICTION_GRACE_SECONDS,
                )
                return False
            idle_units = stale_units

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
            "session_router.evict session_id=%s (idle %.0fs%s)",
            victim.session_id,
            now - victim.last_used,
            ", forced" if force else "",
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

        # Root-1 SSOT Phase 2 (L3): a transition INTO a clean IDLE is the trigger
        # to drain any pending messages that arrived while the session was busy.
        # Enqueue is non-blocking (put_nowait) and takes NO lock — this runs on
        # the streaming/hook stack, so we must NEVER drain (call send()) inline
        # here (F1 deadlock/recursion). The serial worker does the actual drain.
        if new_state == SessionState.IDLE:
            self.enqueue_drain(session_id)

    # ── Drain worker (Root-1 SSOT Phase 2, L3) ────────────────────

    def enqueue_drain(self, session_id: str) -> None:
        """Signal that ``session_id`` may have pending messages to drain.

        Non-blocking, lock-free, idempotent (a session already queued is not
        re-added). Lazily starts the serial drain worker on first call so it
        binds to the running loop. Safe to call from inside ``_transition``.
        """
        if session_id in self._drain_enqueued:
            return
        try:
            self._drain_enqueued.add(session_id)
            self._drain_queue.put_nowait(session_id)
        except Exception:
            self._drain_enqueued.discard(session_id)
            return
        if self._drain_worker_task is None or self._drain_worker_task.done():
            try:
                self._drain_worker_task = asyncio.ensure_future(self._drain_worker())
            except RuntimeError:
                # No running loop (e.g. unit tests calling enqueue synchronously
                # without a loop) — the worker will be started on a later enqueue
                # from within an async context.
                self._drain_worker_task = None

    async def _drain_worker(self) -> None:
        """Long-lived single consumer: drains ONE session at a time, serially,
        OUTSIDE any transition stack (F1). Never raises out — a per-session drain
        failure is logged and the loop continues."""
        while True:
            session_id = await self._drain_queue.get()
            self._drain_enqueued.discard(session_id)
            try:
                await self.drain_pending(session_id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "session_router.drain_worker_error session_id=%s: %s",
                    session_id, exc,
                )
            finally:
                self._drain_queue.task_done()

    async def drain_pending(self, session_id: str) -> None:
        """Coalesce-drain the whole pending set for one session as ONE turn.

        Precondition (ALL must hold, else no-op — re-driven on the next IDLE edge):
          - the unit exists, is alive, and is in a clean IDLE
          - NO outstanding tool_use (F3 — never start a new turn while an
            AskUserQuestion / cmd_permission awaits its tool_result)

        The whole pending set is CLAIMED atomically (claim_pending_batch: sent
        stays 0, claimed_at set). Outside any lock, combine_pending merges the set
        into one payload and send() delivers it; on success mark_sent_batch flips
        the set to sent=1, on failure rollback_claim_batch returns it to pending
        so it re-coalesces on the next IDLE (F4 — no message lost).
        """
        from . import session_pending

        unit = self._units.get(session_id)
        if unit is None or not unit.is_alive or unit.state != SessionState.IDLE:
            return
        if unit.has_outstanding_tool_use:
            return  # F3: a question is awaiting its answer — do not inject a turn

        claimed = await session_pending.claim_pending_batch(session_id)
        if not claimed:
            return

        user_text, content = session_pending.combine_pending(claimed)
        seqs = [c.pending_seq for c in claimed]

        # Degenerate guard (Gate-2 F1): a corrupt/empty pending row combines to
        # (None, None). Delivering it hits run_conversation's EMPTY_MESSAGE branch,
        # which would roll back → re-claim → same corrupt row → infinite drain loop.
        # Drop such rows as terminally undeliverable (mark sent so they leave the
        # queue) with a loud log — never silently, never loop.
        if not user_text and not content:
            await session_pending.mark_sent_batch(session_id, seqs)
            logger.error(
                "session_router.drain_dropped_corrupt session_id=%s seqs=%s — "
                "pending rows combined to empty payload; marked sent to avoid a "
                "drain loop (message content was unrecoverable)",
                session_id, seqs,
            )
            return

        try:
            # Deliver the coalesced turn. The rows already exist in the DB
            # (sent=0, claimed); run_conversation re-persisting a user row is
            # acceptable here because the claimed rows are flipped to sent=1 on
            # success — the drained turn IS this conversation turn.
            agen = self.run_conversation(
                agent_id=unit.agent_id,
                user_message=user_text,
                content=content,
                session_id=session_id,
                _drained_pending=True,
            )
            # CRITICAL (Gate-2 F1): run_conversation reports many failures as
            # YIELDED error events (SESSION_BUSY on a drain-vs-user race,
            # EMPTY_MESSAGE on a degenerate combine), not raised exceptions — it
            # yields and returns normally. We must NOT mark_sent on those: doing so
            # flips claimed→sent for a turn that never reached the subprocess →
            # permanent message loss. Gate mark_sent on an observed terminal
            # `result` AND the absence of any `error` event; otherwise roll back so
            # the set re-coalesces on the next clean IDLE (F4 — never lose a msg).
            delivered_ok = False
            saw_error: Optional[str] = None
            async for _evt in agen:
                _t = _evt.get("type") if isinstance(_evt, dict) else None
                if _t == "error":
                    saw_error = (_evt.get("code") or "error") if isinstance(_evt, dict) else "error"
                elif _t == "result":
                    delivered_ok = True
            if delivered_ok and saw_error is None:
                await session_pending.mark_sent_batch(session_id, seqs)
                logger.info(
                    "session_router.drained session_id=%s count=%d seqs=%s",
                    session_id, len(seqs), seqs,
                )
                # Record the drained seqs on the unit so the streaming-state read
                # API (L5) can surface "these pending rows were delivered" to the
                # frontend mirror (2A). Best-effort; FE also learns via count→0.
                try:
                    unit._last_drained_seqs = list(seqs)
                except Exception:
                    pass
            else:
                # Not actually delivered (error event or no result) — roll back to
                # pending so it re-coalesces, exactly like the raised-exception path.
                await session_pending.rollback_claim_batch(session_id, seqs)
                logger.warning(
                    "session_router.drain_not_delivered session_id=%s seqs=%s "
                    "error=%s delivered_ok=%s — rolled back to pending",
                    session_id, seqs, saw_error, delivered_ok,
                )
        except Exception as exc:
            await session_pending.rollback_claim_batch(session_id, seqs)
            logger.warning(
                "session_router.drain_send_failed session_id=%s seqs=%s: %s — "
                "rolled back to pending",
                session_id, seqs, exc,
            )

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
        client_id: Optional[str] = None,
        _drained_pending: bool = False,
    ) -> AsyncIterator[dict]:
        """Entry point for chat requests.

        ``_drained_pending`` (internal): set by the drain worker when delivering
        a coalesced pending turn. The rows already exist in the DB (sent=0,
        claimed); the worker flips them to sent=1 after delivery, so the pre-slot
        re-persist below is skipped to avoid a duplicate user row.

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

        # Sync the HealthSensor turn threshold to the channel CLI ceiling for
        # ALL channel sessions — keyed off channel_context, NOT the is_channel_session
        # slot flag. prompt_builder clamps the CLI to CHANNEL_MAX_TURNS for any
        # channel_context regardless of is_owner (it does not check is_owner), so
        # owner DMs ALSO run at 100. The slot flag deliberately excludes owners (for
        # pool routing), but the turn threshold must NOT — otherwise an owner channel
        # session keeps _max_turns=500 while the CLI dies at 100, making the
        # turn_approaching heal + channel wrap-up structurally unreachable (the exact
        # decoupled-threshold bug this fix exists to close).
        if channel_context:
            from .session_healing import CHANNEL_MAX_TURNS
            unit._health_sensor.set_max_turns(CHANNEL_MAX_TURNS)

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
        # Track the persisted row id so that, if the slot check rejects this
        # send (QUEUE_TIMEOUT / SESSION_BUSY), we can convert THIS exact row to a
        # pending message (Root-1 SSOT Phase 2 L2) instead of deleting it — the
        # drain worker then delivers it when the session next reaches IDLE.
        persisted_msg_id: Optional[str] = None
        if user_content and not _drained_pending:
            title = (user_message or "Chat")[:50]
            try:
                await session_manager.store_session(session_id, agent_id, title)
                _msg_metadata = {"client_id": client_id} if client_id else None
                persisted_msg_id = str(uuid4())
                await db.messages.put({
                    "id": persisted_msg_id,
                    "session_id": session_id,
                    "role": "user",
                    "content": user_content,
                    "model": None,
                    "created_at": datetime.now().isoformat(),
                    **({"metadata": _msg_metadata} if _msg_metadata else {}),
                })
            except Exception as exc:
                persisted_msg_id = None
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
        # Mirror _evict_idle's candidate filter: a unit that is IDLE but still
        # generating after an SSE disconnect is NOT evictable, so it must not
        # count as a free-able slot here — otherwise we skip the "queued"
        # indicator and then fail to evict, falling through to QUEUE_TIMEOUT
        # without ever telling the user they were waiting.
        needs_queue = (
            not unit.is_alive
            and self.alive_count >= _current_max
            and not any(
                u.state == SessionState.IDLE
                and not u.is_post_disconnect_flushing
                and u is not unit
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
                suggested_action="Your message is saved and will be sent automatically when a slot opens.",
            )
            # Root-1 SSOT Phase 2 (L2): unify QUEUE_TIMEOUT with SESSION_BUSY onto
            # the same pending-message path. Convert the pre-slot persisted row to
            # pending (sent=0) so the drain worker delivers it when a slot frees —
            # the message is durably queued server-side, not just handed back to
            # the frontend. retryPayload retained ONLY as the persist-failed fallback.
            queue_pending_seq: Optional[int] = None
            if user_content and persisted_msg_id:
                try:
                    from . import session_pending
                    queue_pending_seq = await session_pending.mark_pending_by_id(
                        session_id, persisted_msg_id,
                    )
                except Exception as pend_exc:
                    logger.warning(
                        "session_router.queue_mark_pending_failed session_id=%s: %s",
                        session_id, pend_exc,
                    )
            if queue_pending_seq is not None:
                error_event["pendingSeq"] = queue_pending_seq
                error_event["pendingId"] = persisted_msg_id
            else:
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

        # Per-channel model override: channel_context["model"] takes precedence
        # over the global default_model. This allows Slack to use Sonnet while
        # chat tabs use Opus (or vice versa).
        if channel_context and channel_context.get("model"):
            agent_config["model"] = channel_context["model"]

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
        # Channel sessions (Slack DM): prewarm spawns a fresh subprocess
        # with _sdk_session_id set but ZERO conversation history.  After
        # daemon restart the subprocess is new — it doesn't know about
        # prior turns.  Detect this by checking if the DB session has
        # prior messages that the current subprocess hasn't seen.
        needs_channel_resume = False
        if (
            channel_context
            and not is_cold_resume
            and session_id
            and not unit._channel_history_injected
        ):
            msg_count_db = await db.messages.count_by_session(session_id)
            # >1 because current message was already persisted above.
            # If DB has more messages than just this one, the subprocess
            # is missing context (prewarm or daemon restart).
            if msg_count_db > 1:
                needs_channel_resume = True
                logger.info(
                    "channel_resume_detected session_id=%s db_msgs=%d "
                    "— subprocess has no history, injecting",
                    session_id, msg_count_db,
                )
        # Log cold resume detection inputs for diagnostics (COE: 2026-04-02
        # resume context silently skipped — no visibility into why).
        logger.info(
            "cold_resume_check session_id=%s state=%s sdk_session=%s "
            "→ is_cold_resume=%s channel_resume=%s",
            session_id,
            unit.state.value if unit.state else "None",
            "set" if unit._sdk_session_id else "None",
            is_cold_resume,
            needs_channel_resume,
        )
        # Channel TTL rotation: the gateway created a fresh session_id but
        # the prior session's messages should carry forward for continuity.
        # prior_session_id is set by gateway._resolve_session on TTL rotation.
        prior_session_id = (
            channel_context.get("prior_session_id") if channel_context else None
        )
        if is_cold_resume or needs_channel_resume:
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
                "→ injecting=%s (cold=%s channel=%s)",
                session_id, msg_count, msg_count > 1,
                is_cold_resume, needs_channel_resume,
            )
            if msg_count > 1:
                agent_config["needs_context_injection"] = True
                agent_config["resume_app_session_id"] = resume_from
                unit._channel_history_injected = True
                # Insert resume boundary marker so frontend can render a
                # divider between old messages and the new interaction.
                # Without this, prior session messages appear as current
                # blue bubbles — confusing the user (BUG: 2026-06-17).
                await db.messages.put({
                    "id": str(uuid4()),
                    "session_id": session_id,
                    "role": "system",
                    "content": [{"type": "resume_boundary", "text": "Session resumed"}],
                    "model": None,
                    "created_at": datetime.now().isoformat(),
                })
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

        # G3 shadow recall REMOVED — recall is already live (wired into
        # prompt assembly via runtime_hooks). Shadow validation data is no
        # longer needed. See: 2026-05-02-evolution-activation-design.md.

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

                # Echo client_id in result event for frontend dedup (AC2)
                if client_id and event.get("type") == "result":
                    event["client_id"] = client_id

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
                # Root-1 SSOT Phase 2 (L2): the message is NO LONGER deleted.
                # The row persisted before slot acquisition (persisted_msg_id) is
                # converted to a pending message (sent=0) owned by the server-side
                # drain worker, which delivers it when the session next reaches a
                # clean IDLE. This makes the SessionBusyError text TRUE ("saved and
                # will be sent automatically") and removes the frontend's burden of
                # being the durability owner. The cold-resume sent=1 filter (L0/L1)
                # keeps the pending row out of replayed context until it drains.
                pending_seq: Optional[int] = None
                if user_content and persisted_msg_id:
                    try:
                        from . import session_pending
                        pending_seq = await session_pending.mark_pending_by_id(
                            session_id, persisted_msg_id,
                        )
                        logger.info(
                            "session_router.session_busy_pending session_id=%s "
                            "msg=%s seq=%s",
                            session_id, persisted_msg_id, pending_seq,
                        )
                    except Exception as pend_exc:
                        logger.warning(
                            "session_router.mark_pending_failed session_id=%s: %s",
                            session_id, pend_exc,
                        )
                busy_event = _build_error_event(
                    code="SESSION_BUSY",
                    message=str(send_err.message),
                    suggested_action=str(send_err.suggested_action),
                )
                # Truthful: the message is durably queued server-side and will be
                # auto-drained. We surface the pending id/seq so the mirror can
                # show "queued" deterministically. retryPayload is retained as a
                # last-resort fallback ONLY when the pending persist failed
                # (pending_seq is None) — otherwise the server owns delivery and
                # a frontend re-send would double-deliver (the drain handles it).
                if pending_seq is not None:
                    busy_event["pendingSeq"] = pending_seq
                    busy_event["pendingId"] = persisted_msg_id
                else:
                    busy_event["retryPayload"] = {
                        "sessionId": session_id,
                        "agentId": agent_id,
                        "userMessage": user_message,
                        "content": content,
                    }
                yield busy_event
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
        tool_use_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """Delegate to SessionUnit.continue_with_answer().

        Persists each assistant message immediately (crash-safe).

        Args:
            session_id: The session to continue.
            answer: JSON-encoded answer text.
            tool_use_id: The AskUserQuestion tool_use block ID so the CLI
                links this response back to the correct tool call.
        """
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return

        async for event in unit.continue_with_answer(answer, tool_use_id=tool_use_id):
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

    async def refresh_session(self, session_id: str) -> dict:
        """Refresh a session's context by killing subprocess for resume.

        User-triggered "same-tab restart": kills the subprocess but preserves
        _sdk_session_id so the next send() auto-resumes with structured context
        injection. Only works when session is IDLE (not streaming).

        Returns dict with success status and message.
        """
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}

        if unit.state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            return {
                "success": False,
                "message": "Cannot refresh while the AI is active. Stop or answer the pending question first.",
            }

        try:
            await unit.refresh_context()
            return {
                "success": True,
                "message": "Context refreshed. Next message will resume with summary.",
            }
        except Exception as exc:
            logger.error("refresh_session failed for %s: %s", session_id, exc)
            return {"success": False, "message": str(exc)}

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

    async def kill_rotated_channel_session(self, old_session_id: str) -> None:
        """Kill a channel SessionUnit that was rotated out by the gateway.

        Called by ChannelGateway._resolve_session() after TTL rotation creates
        a new session.  The old SessionUnit is no longer referenced by any
        channel_session row, but remains in-memory with is_channel_session=True
        — making it TTL-immune and potentially a zombie resource leak.

        No-op if the session doesn't exist or is already COLD/DEAD.
        """
        unit = self._units.get(old_session_id)
        if unit is None:
            return  # Not in router — already cleaned up
        if not unit.is_alive:
            return  # Already COLD/DEAD — no subprocess to kill
        logger.info(
            "session_router.kill_rotated_channel session_id=%s state=%s — "
            "cleaning up rotated channel session",
            old_session_id, unit.state.value,
        )
        await unit.kill()

    async def disconnect_all(self) -> None:
        """Kill all alive SessionUnits. Called at shutdown.

        Fires hooks before killing each unit so DailyActivity, auto-commit,
        and distillation run for every active conversation.

        Design §2C: Persists session state BEFORE killing so that sdk_session_ids
        survive daemon restart (enables fast --resume instead of cold resume).
        clear_session_identity() is intentionally NOT called — identity is
        preserved in the state file for restore on next startup.
        """
        # Root-1 SSOT Phase 2 (L3): cancel the serial drain worker so it doesn't
        # outlive the event loop on shutdown (a forever-blocking queue.get()).
        if self._drain_worker_task is not None and not self._drain_worker_task.done():
            self._drain_worker_task.cancel()
            try:
                await self._drain_worker_task
            except (asyncio.CancelledError, Exception):
                pass
            self._drain_worker_task = None

        # Persist IDLE session identities for fast resume after restart (§2B/2C)
        try:
            from .session_state_persistence import persist_session_state
            from jobs.paths import APP_DATA_DIR

            state_file = APP_DATA_DIR / "session_state.json"
            # Merge unconsumed cached IDs so they survive a second restart.
            count = persist_session_state(
                self._units, state_file, pending_ids=self._persisted_sdk_ids,
            )
            if count > 0:
                logger.info("Persisted %d session identities before shutdown", count)
        except Exception as exc:
            logger.warning("Failed to persist session state on shutdown: %s", exc)

        alive = [u for u in self._units.values() if u.is_alive]
        logger.info("session_router.disconnect_all: killing %d alive units", len(alive))
        for unit in alive:
            try:
                # Fire hooks before killing (shutdown fix)
                if self._lifecycle_manager and not unit._hooks_enqueued:
                    await self._lifecycle_manager.enqueue_hooks_for_unit(unit)
                    unit._hooks_enqueued = True
                await unit.kill()
                # NOTE: clear_session_identity() intentionally REMOVED (Design §2C).
                # sdk_session_id is preserved in session_state.json for fast resume.
            except Exception as exc:
                logger.warning(
                    "Failed to kill unit %s during disconnect_all: %s",
                    unit.session_id, exc,
                )



# G3 Shadow Recall REMOVED — recall is live, shadow validation no longer needed.
# See: Knowledge/Designs/2026-05-02-evolution-activation-design.md
