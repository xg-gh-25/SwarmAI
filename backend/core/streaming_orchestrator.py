"""Streaming Orchestrator — owns SDK response streaming and event formatting.

Extracted from session_unit.py as part of the strangler-fig refactoring.
This module contains the core streaming loop (~890 LOC):
- _stream_response(): sends query to SDK, reads response stream
- _read_formatted_response(): parses SDK messages into SSE events

Architecture:
- StreamingOrchestrator holds a parent reference to SessionUnit
- All SessionUnit state is accessed via self._parent.X
- SessionUnit retains a thin delegation method for _read_formatted_response
  (used by continue_with_permission and test files)
- Future: replace self._parent.X with typed callbacks for full decoupling

Design doc: Knowledge/Designs/2026-06-18-session-unit-strangler-fig-extraction-design.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, Protocol

from .compaction_guard import EscalationLevel
from .session_healing import get_process_rss_mb
from .ui_actions import (
    build_ui_command_event,
    build_surface_events_async,
    ensure_report_for_run,
    parse_completion_run_id,
    read_run_status,
    UI_ACTION_FULL_TOOL_NAME,
    SURFACE_OUTPUTS_FULL_TOOL_NAME,
)

if TYPE_CHECKING:
    from .session_unit import SessionUnit

logger = logging.getLogger(__name__)


def _is_blank_api_result(
    *,
    content_emitted: bool,
    is_error: bool,
    interrupted: bool,
    output_tokens: int,
    subtype: str,
) -> bool:
    """True when a non-error ResultMessage produced NOTHING renderable → retry.

    The "前端没渲染、后端正常" (blank turn) signature: the SDK returned a clean
    ResultMessage but no assistant content reached the user. By the time the
    orchestrator evaluates this, the error subtypes have already returned/raised
    upstream (``error_max_turns`` → turn_limit_reached; ``error_during_execution``
    / ``is_error`` → error path), so ``subtype`` here is only ever ``""`` or
    ``"success"`` — BOTH warrant a retry when empty.

    The original guard only fired on an empty subtype (``not subtype``), which let
    a ``subtype="success"`` envelope with 0 output tokens slip through silently.
    Observed live (run_7cf9da85 follow-up): session 2e87b27f, 2026-06-26 17:43 —
    a 68s turn returned input=0/output=0, subtype=success, no content; it passed
    both empty-guards, transitioned cleanly to IDLE, and the user saw a blank turn
    with no retry. This predicate is shared verbatim by the orchestrator guard and
    its regression test so the two can never drift (the project's recurring
    mirror-drift trap).

    Args mirror the orchestrator's locals at the result point. Returns True only
    for the blank-success/blank-empty case; any streamed content, any output
    tokens, an interrupt, an error, or an unrecognised subtype → False (do not
    retry).
    """
    return (
        not content_emitted
        and not is_error
        and not interrupted
        and output_tokens == 0
        and subtype in ("", "success")
    )


# ═══════════════════════════════════════════════════════════════════
# Callbacks Protocol (Phase 3 target — not used yet)
# ═══════════════════════════════════════════════════════════════════


class StreamingCallbacks(Protocol):
    """Interface contract between StreamingOrchestrator and SessionUnit.

    Phase 2 (current): Not used — orchestrator accesses parent directly.
    Phase 3 (future): SessionUnit implements this protocol; orchestrator
    calls through it instead of holding a parent reference.
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
# StreamingOrchestrator — Phase 2 (logic migrated, parent ref access)
# ═══════════════════════════════════════════════════════════════════


class StreamingOrchestrator:
    """Owns SDK response streaming and event formatting logic.

    Architecture:
    - Holds a reference to the parent SessionUnit
    - _stream_response() and _read_formatted_response() live here
    - All SessionUnit state accessed via self._parent.X
    - stream_query() is the public entry point

    Entry points:
    - stream_query(): called by SessionUnit.send(), _retry_with_resume(),
      _handle_buffer_overflow(), continue_with_answer()
    - _read_formatted_response(): called via SessionUnit delegation stub
      by continue_with_permission() and test files

    Future: replace self._parent.X with typed callbacks for full decoupling.
    """

    # No __slots__ — allows unittest.mock.patch.object() on instances.
    # Memory cost is negligible (one instance per session).

    def __init__(self, parent: "SessionUnit") -> None:
        """Initialize with parent SessionUnit reference.

        Args:
            parent: The owning SessionUnit instance. All field accesses go
                through self._parent.X until Phase 3 decouples via callbacks.
        """
        self._parent = parent
        self._session_id = parent.session_id

    async def stream_query(
        self,
        query_content: Any,
    ) -> AsyncIterator[dict]:
        """Stream a query through the SDK and yield formatted SSE events.

        Public entry point — replaces direct SessionUnit._stream_response() calls.
        Delegates to self._stream_response() which owns the query send logic.

        Args:
            query_content: User message text (str) or multimodal blocks (list).

        Yields:
            Formatted SSE event dicts (text_delta, thinking_delta, tool_use, etc.)
        """
        async for event in self._stream_response(query_content):
            yield event

    @property
    def stall_seconds(self) -> Optional[float]:
        """Proxy to parent's streaming_stall_seconds."""
        return self._parent.streaming_stall_seconds

    def _auto_surfaced_run_ids(self) -> set:
        """Session-scoped set of run_ids whose finish-time Canvas surface has already
        fired this session (run_beff6754) — the idempotency guard shared by BOTH the
        explicit surface_run_outputs branch and the backend-auto completion
        observation, so a run is surfaced AT MOST ONCE regardless of which path fired.

        Lazily attached to the parent SessionUnit via getattr rather than declared in
        SessionUnit.__init__ — that class is CRITICAL (500+ callers) and this state is
        orchestrator-owned; a lazy attr keeps the change fully local to this module.
        A per-run-id set (not per-turn) is correct: it dedups auto-vs-explicit within a
        session while still surfacing a genuinely different run, and a re-run of the
        SAME completed run_id (retry) correctly does not re-pop the Canvas."""
        seen = getattr(self._parent, "_auto_surfaced_run_ids_set", None)
        if seen is None:
            seen = set()
            self._parent._auto_surfaced_run_ids_set = seen
        return seen

    def _clear_completed_sub_agents(self, message: Any) -> None:
        """Remove tracking entries for sub-agents (Agent tool) that completed.

        Sub-agent (Agent tool) results are delivered by the SDK in a
        parent-level ``UserMessage`` (tool_result blocks live in user-typed
        turns per the Anthropic protocol), NOT in the ``AssistantMessage``
        that the main loop's ToolResultBlock branch handles. Because the loop
        had no UserMessage handler, ``_active_agent_tools`` entries were never
        popped — leaving ``count`` frozen, the elapsed timer climbing forever,
        and the progress label stale (the "Spec compliance review" bug).

        This is CLEANUP ONLY — it never yields events and never renders
        content. ``_active_agent_tools`` only ever holds **Agent-tool** ids
        (populated under ``block.name == "Agent"``), so ``pop(id, None)`` is a
        safe no-op for any unrelated tool_result (e.g. a parent's own
        Edit/Write result) — this helper leaves those untouched. NOTE: a
        parent's own Edit/Write result ALSO arrives via UserMessage (per the
        Anthropic protocol), and its ``file_changed`` emit is handled INLINE in
        the UserMessage branch of ``_read_formatted_response`` (guarded on
        ``_pending_file_changes`` membership), NOT here and NOT in the
        AssistantMessage branch (run_0520a394).

        Args:
            message: An SDK message (typically UserMessage). Its ``content``
                may be a plain string or a list of content blocks.
        """
        from claude_agent_sdk import ToolResultBlock

        content = getattr(message, "content", None)
        if not isinstance(content, list):
            return  # string content (or none) carries no tool_result blocks
        for block in content:
            if isinstance(block, ToolResultBlock):
                self._parent._active_agent_tools.pop(block.tool_use_id, None)
                # Agent-tool results arrive HERE (UserMessage), not in the
                # AssistantMessage ToolResultBlock branch (PIT03). Clear the
                # open-tool tracker on this path too, else a sub-agent id
                # lingers and could false-trip the tool-hang tier (run_fb6e94a9).
                self._parent._open_tool_uses.pop(block.tool_use_id, None)
                self._parent._tool_hang_interrupted = False
                # Clear the grace window too — it was armed for a specific
                # interrupt; unrelated tool completion must not keep the
                # destructive backstop suppressed (adversarial v1 MED).
                self._parent._tool_hang_interrupt_at = None
                # A completed tool = the session recovered + made progress, so
                # reset the escalation counter — it must count CONSECUTIVE
                # unrecovered wedges, not lifetime-separate ones (adversarial v2 MED).
                self._parent._tool_hang_episodes = 0

    # ═══════════════════════════════════════════════════════════════
    # Migrated from session_unit.py — _stream_response
    # ═══════════════════════════════════════════════════════════════

    async def _stream_response(
        self,
        query_content: Any,
    ) -> AsyncIterator[dict]:
        """Send query and yield raw SDK messages.

        Reads ``client.receive_response()`` and yields each message
        as-is.  Handles state transitions:

        - On ``result`` message → STREAMING → IDLE
        - On ``ask_user_question`` / ``cmd_permission_request`` →
          STREAMING → WAITING_INPUT
        - On error → raises exception for caller to handle

        The caller (``send()``) is responsible for retry logic and
        error event construction.

        Args:
            query_content: User message text (str) or multimodal blocks (list).
        """
        if self._parent._client is None:
            raise RuntimeError(
                f"No client available for session {self._parent.session_id}"
            )

        # ── Sanitize query content: strip null bytes ─────────────
        if isinstance(query_content, str) and "\x00" in query_content:
            logger.warning("session_unit: stripping null bytes from query_content")
            query_content = query_content.replace("\x00", "")
        elif isinstance(query_content, list):
            for block in query_content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    if "\x00" in block["text"]:
                        logger.warning("session_unit: stripping null bytes from content block")
                        block["text"] = block["text"].replace("\x00", "")

        # Send the query
        if isinstance(query_content, list):
            # Multimodal content — wrap in async generator
            async def _multimodal_gen():
                msg = {
                    "type": "user",
                    "message": {"role": "user", "content": query_content},
                    # Always None — the tool-result routing path was removed when
                    # continue_with_answer migrated to _read_formatted_response().
                    # Key retained to preserve the exact SDK message shape.
                    "parent_tool_use_id": None,
                }
                yield msg

            await self._parent._client.query(_multimodal_gen())
        else:
            await self._parent._client.query(query_content)

        logger.info(
            "Query sent for session %s, reading response...",
            self._parent.session_id,
        )

        # Reset hang timer — stream just started; don't carry stale
        # _last_activity_time from a previous turn that ended 90s+ ago.
        self._parent._health_sensor.record_activity()

        # Read and format the SDK response stream
        async for event in self._read_formatted_response():
            yield event


    # run_cce6f4b9: event-driven WRITE surfacing is restored here as
    # _build_file_write_events (below), the symmetric twin of _build_file_delete_events.
    # It replaces the per-turn git sweep (sweep_turn_delta) that run_4de279ca had made
    # the sole authority — that sweep was the tab-isolation/trigger/cost regression root
    # cause. Both emits fire at the ToolResult(success) point (see stream loop).

    async def _build_file_write_events(
        self, raw_paths: list[str], resolve_cache: "dict[str, dict | None]"
    ) -> list[dict]:
        """Turn a tool's WRITTEN raw path(s) into unified file_changed(operation=written)
        events — the EVENT-DRIVEN live-surfacing emit (run_cce6f4b9, the symmetric twin
        of _build_file_delete_events). Restores the per-tool emit that run_4de279ca
        replaced with a per-turn global git sweep (the tab-isolation/trigger/cost
        regression root cause).

        For each raw path: resolve its PHYSICAL absolute path ONCE (per-turn cache),
        then classify via `needs_human_review` (the SOLE verdict authority — same one the
        delete path uses). Resolution runs off-loop (bare-name branch may os.walk) but
        ONLY for these deliverable-candidate writes — reads/greps never reach here.

        Two gates, both mirroring the delete twin:
          - Layer-1 emit gate (run_6ebe2d09): a WRITTEN path that fails to resolve is NOT
            a real file (a just-written deliverable always exists on disk), so resolve=None
            means garbage (e.g. a mis-parsed Bash `>` fragment) → DROP.
          - review-worthy gate: needs_human_review.review_worthy is False for machine
            noise (process / gitignored) → DROP. The TRUE `kind` (content/knowledge/source)
            is CARRIED on the event; the FRONTEND gates rail-vs-pop by kind (useReferencedFiles
            drops process/source from the rail; useCanvasAutoSurface pops only content/knowledge).
            The backend does NOT gate on kind — it emits every review-worthy write with its
            real kind, exactly as the pre-run_4de279ca emit did.

        Fail-safe: each path's work is independent (gather); one path's exception never
        sinks the batch. On a needs_human_review error, fall back to kind="content" so a
        resolved deliverable still surfaces (mirrors the pre-regression fallback).
        """
        from core.needs_human_review import needs_human_review
        from core.project_registry import get_swarmws
        from routers.workspace_api import resolve_path_to_physical

        ws_root = get_swarmws()

        async def _resolve_one(raw: str) -> "dict | None":
            if raw in resolve_cache:
                return resolve_cache[raw]
            try:
                r = await asyncio.to_thread(resolve_path_to_physical, raw, ws_root)
            except Exception:  # noqa: BLE001 — fail-safe: unresolvable → dropped below
                r = None
            resolve_cache[raw] = r
            return r

        async def _build_one(raw: str) -> "dict | None":
            resolved = await _resolve_one(raw)
            # Layer-1 emit gate: unresolvable → not a real file → DROP.
            if not resolved:
                return None
            # Unified review verdict. needs_human_review does its own owning-tree
            # resolution on the ABSOLUTE path + a `git check-ignore` subprocess → off-loop
            # via to_thread. Fail-safe: on error, fall back to kind="content" (still
            # review-worthy) so a resolved deliverable is not silently dropped.
            try:
                verdict = await asyncio.to_thread(
                    needs_human_review, resolved["absolute"], "written"
                )
                if not verdict.review_worthy:
                    return None  # machine noise / gitignored → never surface
                kind = verdict.kind
            except Exception as _verdict_err:  # noqa: BLE001
                # Fail-safe to kind="content" (a resolved deliverable still surfaces),
                # but LOG loudly — a silent demote would hide a real crash in
                # needs_human_review (_owning_tree / _classify_kind / git) behind a
                # plausible-looking content row (operational MED, run_cce6f4b9).
                logger.warning(
                    "file_write_events: needs_human_review errored for %s: %s — "
                    "surfacing as kind=content (fail-safe)",
                    resolved.get("absolute"), f"{type(_verdict_err).__name__}: {_verdict_err}",
                )
                kind = "content"
            return {
                "type": "file_changed",
                "path": resolved["relative"],
                "absolutePath": resolved["absolute"],
                "kind": kind,
                "operation": "written",
            }

        built = await asyncio.gather(
            *(_build_one(raw) for raw in raw_paths),
            return_exceptions=True,
        )
        # Drop None (unresolvable / not-review-worthy) AND any Exception (a single path's
        # failure must not sink the batch). gather preserves input order.
        return [ev for ev in built if isinstance(ev, dict)]

    async def _build_file_delete_events(
        self, raw_paths: list[str], resolve_cache: "dict[str, dict | None]"
    ) -> list[dict]:
        """Turn a tool's DELETED raw path(s) into unified file_changed(operation=deleted)
        events so the frontend can REMOVE stale rail rows (G1, run_5a7be540).

        Unlike a write, a deleted file no longer exists on disk, so
        resolve_path_to_physical (which requires is_file) usually returns None — a
        relative path or a plain-name delete can't be stat-relativized after the
        fact. That is fine: the delete event carries the BEST path form we have
        (resolved relative if the resolve happened to succeed — e.g. an absolute
        path still structurally under a Projects/ symlink — else the raw path for
        BOTH path and absolutePath). The FRONTEND removes conservatively (anchored
        match on path/absolutePath), so a delete that matches nothing is a harmless
        no-op (a stale row lingers == today's behavior; safe direction).

        run_4de279ca (Gate-2 F6/F7): the bookkeeping filter uses the SAME git-based
        authority as writes (`needs_human_review`), NOT the old hardcoded
        `_BOOKKEEPING_DIRS` denylist — so a delete and a write of the same path get
        the SAME verdict (no two-authority divergence). `needs_human_review` is
        PATH-based (git check-ignore matches path patterns; `.resolve()` works on a
        non-existent path) — it never stats the file, so a deleted (gone) path
        classifies fine. Only a review-worthy path emits a delete event (drops its
        stale rail row); machine noise never had a row to drop.
        """
        from core.needs_human_review import needs_human_review
        from core.project_registry import get_swarmws
        from routers.workspace_api import resolve_path_to_physical

        ws_root = get_swarmws()
        events: list[dict] = []
        for raw in raw_paths:
            # needs_human_review runs a `git check-ignore` SUBPROCESS → off-loop via
            # to_thread (RP53: a sync subprocess here would block the daemon's shared
            # event loop for every deleted path — a `rm *.tmp` batch = N blocking spawns
            # freezing /health). Mirrors _build_file_write_events' off-loop verdict; a
            # verdict error fails-safe to "surface it" (a stale row lingering ==
            # pre-G1 behavior, the safe direction for a DELETE).
            try:
                _worthy = (await asyncio.to_thread(
                    needs_human_review, raw, "written"
                )).review_worthy
            except Exception:  # noqa: BLE001 — fail-safe: emit the delete (drop the row)
                _worthy = True
            if not _worthy:
                continue
            if raw in resolve_cache:
                resolved = resolve_cache[raw]
            else:
                try:
                    resolved = await asyncio.to_thread(resolve_path_to_physical, raw, ws_root)
                except Exception:
                    resolved = None
                resolve_cache[raw] = resolved
            path = resolved["relative"] if resolved else raw
            abs_path = resolved["absolute"] if resolved else raw
            events.append({
                "type": "file_changed",
                "path": path,
                "absolutePath": abs_path,
                "relevance": "incidental",
                "kind": "content",
                "operation": "deleted",
            })
        return events

    # ═══════════════════════════════════════════════════════════════
    # Migrated from session_unit.py — _read_formatted_response
    # ═══════════════════════════════════════════════════════════════

    async def _read_formatted_response(self) -> AsyncIterator[dict]:
        """Read SDK response stream and yield formatted SSE events.

        Shared by ``_stream_response`` (after query) and
        ``continue_with_permission`` / ``continue_with_answer``
        (resume after user input).

        Handles state transitions:
        - On result → STREAMING → IDLE
        - On ask_user_question → STREAMING → WAITING_INPUT
        - On error → raises for caller to handle
        """
        # Lazy import to avoid circular dependency (session_unit → streaming_orchestrator → session_unit)
        from .session_unit import SessionState  # noqa: F811

        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ToolUseBlock,
            ToolResultBlock,
            UserMessage,
        )
        from claude_agent_sdk.types import StreamEvent, ThinkingBlock

        try:
            from core.tool_summarizer import summarize_tool_use, get_tool_category, truncate_tool_result
            _has_tool_summarizer = True
        except ImportError:
            _has_tool_summarizer = False

        # ── Per-message timeout: structurally prevents hanging ─────
        # The SDK async iterator can hang forever if the subprocess
        # stops producing messages (no ResultMessage, no error, nothing).
        # Wrap each __anext__() call with a timeout so the stream
        # CANNOT stay stuck.  On timeout, we raise — the caller's
        # retry logic handles recovery with --resume.
        #
        # First message uses a shorter timeout because the subprocess
        # should send an init/system message quickly after spawn.
        # 180s accommodates cross-region Bedrock + --resume session restore.
        # Single timeout for both fresh and resume — simpler, fewer states.
        MESSAGE_TIMEOUT = self._parent._compute_message_timeout()  # Adaptive: scales with context
        # First-message timeout (run_4b74b764, Part B). Policy lives in ONE place
        # (_compute_init_timeout): resume sessions floor at the adaptive timeout
        # (heavy conversation replay before inference), fresh keep the fast 180s.
        is_resume = self._parent._sdk_session_id is not None
        INIT_TIMEOUT = self._parent._compute_init_timeout()
        # Poll interval for surfacing "still working" heartbeats during a
        # silent wait. Must be small enough to bubble a long/stuck step
        # promptly; the heartbeat itself throttles to one notice per
        # LONG_TURN_HEARTBEAT_S. Does NOT cancel the in-flight SDK read.
        HEARTBEAT_POLL_S = 30.0

        # (is_resume computed above for the adaptive INIT_TIMEOUT floor)
        is_first_message = True
        saw_assistant_message = False  # Track if LLM actually responded

        # ── Permission queue watcher ──────────────────────────────
        # The dangerous_command_gate hook blocks inside PreToolUse
        # awaiting a user decision.  While it blocks, the SDK cannot
        # produce new messages.  We race the SDK iterator against the
        # PermissionManager session queue so we can surface the
        # cmd_permission_request to the frontend via SSE.
        from core.permission_manager import permission_manager as _pm
        perm_queue = _pm.get_session_queue(self._parent.session_id)

        response_iter = self._parent._client.receive_response().__aiter__()
        _STREAM_EXHAUSTED = object()  # Sentinel: iterator is done
        # tool_use_id → list[raw_path] WRITTEN by that tool (Write/Edit/NotebookEdit
        # file_path + Bash `>`/tee/cp/mv-DEST). Emits operation=written at the ToolResult
        # (success) point — the EVENT-DRIVEN live-surfacing emit (run_cce6f4b9), which
        # rides the WRITING session's own stream (so it is stamped with the correct
        # session → tab-isolation correct by construction, unlike the deleted global sweep).
        _pending_file_changes: dict[str, list[str]] = {}
        # tool_use_id → list[raw_path] DELETED by that tool (Bash rm / mv-SRC). Emits
        # operation=deleted at the ToolResult point so the rail drops stale rows (G1,
        # run_5a7be540). Separate from _pending_file_changes because one Bash command
        # can both write and delete.
        _pending_file_deletes: dict[str, list[str]] = {}
        # tool_use_id → run_id for a Bash `run-update --status completed` command
        # (run_beff6754). Emits the finish-time Canvas surface at the ToolResult point
        # IF run.json confirms status==completed — the backend-auto replacement for the
        # agent-discipline surface_run_outputs call (which docs-only runs skipped, so
        # Canvas never auto-opened on the most common pipeline).
        _pending_completion: dict[str, str] = {}
        # Per-TURN resolution cache (perf directive): raw_path → resolved dict|None.
        # Turn-scoped (local to this method), so no cross-session leak; keyed by the
        # raw path string. Only deliverable-candidate writes are ever resolved —
        # reads/greps never reach here, so they never pay the os.walk bare-name cost.
        _resolve_cache: dict[str, "dict | None"] = {}

        # run_cce6f4b9: the per-turn git-snapshot live-surfacing path (turn-START
        # porcelain_snapshot baseline + turn-END sweep_turn_delta) was DELETED here — it
        # was the root cause of the Canvas tab-isolation leak (global tree diff, no session
        # scope), the non-progressive trigger (turn-end batch), and the per-turn full-tree
        # scan cost. Live WRITE surfacing is now event-driven: _build_file_write_events at
        # the ToolResult(success) point below (symmetric with the delete emit). Non-parent
        # writes (sub-agent/skill/CLI/hook — SDK-sidechain-filtered) surface at PIPELINE
        # FINISH via sweep_run_changes / surface_run_outputs (the retained author-agnostic
        # fallback), an XG-accepted tradeoff for removing the per-turn regression.

        async def _next_or_sentinel():
            """Wrap __anext__ so StopAsyncIteration doesn't leak into Task.

            Python converts StopAsyncIteration inside a Task into
            RuntimeError('async generator raised StopAsyncIteration').
            Wrapping it here returns a sentinel instead, which the
            caller checks after task.result().
            """
            try:
                return await response_iter.__anext__()
            except StopAsyncIteration:
                return _STREAM_EXHAUSTED

        while True:
            current_timeout = INIT_TIMEOUT if is_first_message else MESSAGE_TIMEOUT

            # Race: SDK message vs permission request from hook
            sdk_task = asyncio.ensure_future(
                asyncio.wait_for(
                    _next_or_sentinel(),
                    timeout=current_timeout,
                )
            )
            perm_task = asyncio.ensure_future(perm_queue.get())

            try:
                # Heartbeat polling: wake every HEARTBEAT_POLL_S to surface a
                # "still working" notice during a long or stuck wait, WITHOUT
                # cancelling the in-flight sdk_task — cancelling it would abort
                # the __anext__ read mid-message and could lose/corrupt a
                # message. sdk_task and perm_task persist across ticks; only the
                # tick timer is re-armed. This is what makes a silent hang
                # visible (the heartbeat is otherwise event-driven and never
                # fires when no SDK events arrive).
                while True:
                    tick_task = asyncio.ensure_future(asyncio.sleep(HEARTBEAT_POLL_S))
                    try:
                        done, pending = await asyncio.wait(
                            [sdk_task, perm_task, tick_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except Exception:
                        tick_task.cancel()
                        raise
                    if tick_task in done and sdk_task not in done and perm_task not in done:
                        # Neither a message nor a permission arrived this tick —
                        # surface a heartbeat (throttled internally) and keep
                        # waiting on the SAME sdk_task/perm_task.
                        try:
                            hb = self._parent._maybe_build_elapsed_heartbeat()
                            if hb is not None:
                                yield hb
                        except Exception as hb_exc:
                            logger.debug(
                                "streaming_orchestrator.heartbeat_failed session_id=%s: %s",
                                getattr(self._parent, "session_id", "?"),
                                f"{type(hb_exc).__name__}: {hb_exc}",
                            )
                        continue
                    # A real task finished — drop the tick timer and proceed.
                    tick_task.cancel()
                    try:
                        await tick_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    done.discard(tick_task)
                    pending.discard(tick_task)
                    break
            except Exception:
                # Cleanup on unexpected errors
                sdk_task.cancel()
                perm_task.cancel()
                raise

            # Cancel the loser
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, StopAsyncIteration, asyncio.TimeoutError):
                    pass

            # ── Permission request OR ask_user_question won the race ──
            if perm_task in done:
                try:
                    perm_request = perm_task.result()
                except Exception:
                    # Queue.get shouldn't fail, but be safe
                    continue

                # ── AskUserQuestion item (kind-discriminated) ─────────
                # The ask_question_gate hook enqueues {kind:"ask_user_question",
                # tool_use_id, questions} on the SAME per-session queue. It blocks
                # awaiting ask_question_manager.wait_for_answer(tool_use_id). Surface
                # the question to the frontend; the answer arrives via
                # continue_with_answer → set_answer(tool_use_id) which unblocks the
                # hook (the hook then injects answers via updatedInput).
                if perm_request.get("kind") == "ask_user_question":
                    from core.ask_question_manager import ask_question_manager as _aqm
                    _tuid = perm_request.get("tool_use_id")
                    # Kind-aware drop-guard: an ask item's waiter lives in the
                    # AskQuestionManager, NOT permission_manager. A stale item whose
                    # hook was cancelled (respawn/timeout) has no live waiter — drop
                    # it so the user can't "answer into a void".
                    if not _tuid or not _aqm.has_live_waiter(_tuid):
                        logger.info(
                            "session_unit: dropping stale ask_user_question %s "
                            "(no live waiter) session_id=%s",
                            _tuid, self._parent.session_id,
                        )
                        continue
                    questions = perm_request.get("questions", [])
                    logger.info(
                        "session_unit.ask_question_surfaced session_id=%s "
                        "tool_use_id=%s questions=%d",
                        self._parent.session_id, _tuid, len(questions),
                    )
                    # Track the outstanding tool_use so the drain worker won't
                    # inject a turn while the question is open (keyed on block.id).
                    self._parent._pending_tool_use_id = _tuid
                    self._parent._pending_question = {
                        "tool_use_id": _tuid,
                        "questions": questions,
                    }
                    yield {
                        "type": "ask_user_question",
                        "toolUseId": _tuid,
                        "questions": questions,
                        "sessionId": self._parent.session_id,
                    }
                    self._parent._transition(SessionState.WAITING_INPUT)
                    self._parent.last_used = time.time()
                    return

                # The per-session queue survives respawn (keyed by session_id,
                # only dropped on session end), so a stale request whose hook was
                # cancelled can be replayed here. Surfacing it would let the user
                # "approve" into a void with no awaiting hook. Drop any request
                # with no live waiter (its wait_for_permission_decision already
                # popped the event in its finally on cancellation/timeout).
                from core.permission_manager import permission_manager as _pm_live
                _req_id = perm_request.get("requestId")
                if not _req_id or not _pm_live.has_live_waiter(_req_id):
                    logger.info(
                        "session_unit: dropping stale permission request %s "
                        "(no live waiter) session_id=%s",
                        _req_id, self._parent.session_id,
                    )
                    continue

                logger.info(
                    "session_unit.permission_surfaced session_id=%s "
                    "request_id=%s command=%s",
                    self._parent.session_id,
                    perm_request.get("requestId", "?"),
                    str(perm_request.get("toolInput", {}).get("command", ""))[:60],
                )
                # Root-1 SSOT Phase 2 (L4/F3): a permission prompt is also an
                # outstanding tool_use — track it so the drain worker won't inject
                # a turn while it's open. Keyed by requestId (the permission's id).
                self._parent._pending_tool_use_id = perm_request["requestId"]
                self._parent._pending_question = {
                    "tool_use_id": perm_request["requestId"],
                    "request_id": perm_request["requestId"],
                    "tool_name": perm_request.get("toolName", "Bash"),
                    "tool_input": perm_request.get("toolInput", {}),
                    "reason": perm_request.get("reason", ""),
                    "options": perm_request.get("options", ["approve", "deny"]),
                }
                yield {
                    "type": "cmd_permission_request",
                    "requestId": perm_request["requestId"],
                    "sessionId": perm_request.get("sessionId", self._parent.session_id),
                    "toolName": perm_request.get("toolName", "Bash"),
                    "toolInput": perm_request.get("toolInput", {}),
                    "reason": perm_request.get("reason", ""),
                    "options": perm_request.get("options", ["approve", "deny"]),
                }
                self._parent._transition(SessionState.WAITING_INPUT)
                self._parent.last_used = time.time()
                return

            # ── SDK message won the race ──────────────────────────
            try:
                message = sdk_task.result()
                if message is _STREAM_EXHAUSTED:
                    break
                is_first_message = False
            except asyncio.TimeoutError:
                phase = "init" if is_first_message else "streaming"
                logger.error(
                    "session_unit.%s_timeout session_id=%s — "
                    "no SDK message for %.0fs (resume=%s), breaking stream",
                    phase, self._parent.session_id, current_timeout, is_resume,
                )
                raise RuntimeError(
                    f"Streaming timeout ({phase}): no SDK response for "
                    f"{current_timeout:.0f}s (session_id={self._parent.session_id}, "
                    f"resume={is_resume})"
                )

            # ── Heartbeat: track liveness for diagnostics ──────────
            # Reset BOTH liveness timers on every SDK event:
            #  - _last_event_time → pid_watchdog output-liveness check
            #  - health_sensor.record_activity() → HealthSensor hang_detected
            # Without the record_activity() call, the hang timer only advanced
            # on per-tool record_turn(), so an active stream (tokens/thinking)
            # or a single long tool call could trip the 90s hang detector and
            # trigger a spurious self-heal mid-response.
            self._parent._last_event_time = time.time()
            self._parent._health_sensor.record_activity()

            # ── Long-turn heartbeat (Root 2 / AC5) ─────────────────
            # If this turn has been running a long time, surface a "still
            # working" notice so the FE reads it as expected, not a hang.
            # Event-driven (fires on the next SDK event after the threshold);
            # one notice per interval. Never written to the system prompt.
            try:
                hb = self._parent._maybe_build_elapsed_heartbeat()
                if hb is not None:
                    yield hb
            except Exception as hb_exc:
                # Best-effort — never break the stream — but log so a
                # persistently-throwing heartbeat (e.g. an attribute rename) is
                # visible rather than silently swallowed for hours.
                logger.debug(
                    "streaming_orchestrator.heartbeat_failed session_id=%s: %s",
                    getattr(self._parent, "session_id", "?"),
                    f"{type(hb_exc).__name__}: {hb_exc}",
                )

            # Capture SDK session ID from init message
            if hasattr(message, "session_id") and message.session_id:
                self._parent._sdk_session_id = message.session_id

            # ── SystemMessage: session init metadata ──────────────
            if isinstance(message, SystemMessage):
                if message.subtype == "init":
                    self._parent._sdk_session_id = message.data.get("session_id")
                    yield {
                        "type": "session_start",
                        "sessionId": self._parent.session_id,
                    }
                continue  # Don't forward other system messages

            # ── StreamEvent: token-by-token streaming ─────────────
            if isinstance(message, StreamEvent):
                event_data = message.event
                event_type = event_data.get("type", "")
                if event_type == "content_block_delta":
                    delta = event_data.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        self._parent._content_emitted = True
                        self._parent._last_progress_time = time.time()  # real content
                        yield {"type": "text_delta", "text": delta["text"], "index": event_data.get("index", 0)}
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        self._parent._last_progress_time = time.time()  # real thinking
                        yield {"type": "thinking_delta", "thinking": delta["thinking"], "index": event_data.get("index", 0)}
                elif event_type == "content_block_start":
                    block = event_data.get("content_block", {})
                    if block.get("type") == "thinking":
                        yield {"type": "thinking_start", "index": event_data.get("index", 0)}
                    elif block.get("type") == "text":
                        yield {"type": "text_start", "index": event_data.get("index", 0)}
                elif event_type == "content_block_stop":
                    yield {"type": "content_block_stop", "index": event_data.get("index", 0)}
                continue

            # ── UserMessage: tool_result blocks (Anthropic protocol) ─
            # Per the Anthropic protocol a tool_result is carried by a role=user
            # turn, so the parent's own Edit/Write/Bash results AND sub-agent
            # (Agent) results both arrive HERE via UserMessage (the AssistantMessage
            # ToolResultBlock branch handles server/advisor tool results, not these
            # client-tool executions). Two disjoint jobs on this branch:
            #   (a) sub-agent cleanup — pop _active_agent_tools/_open_tool_uses so
            #       count/timer/label don't freeze (via _clear_completed_sub_agents).
            #   (b) file DELETE emit — a Bash rm/mv-SRC result lands here; emit
            #       operation=deleted so the rail drops the stale row. (run_4de279ca:
            #       the WRITE emit that used to live here is GONE — the turn-end git
            #       sweep is now the sole write-surfacing authority, author-agnostic.
            #       Deletes still need this explicit emit because a deleted path is
            #       skipped by the sweep, cf. run_5a7be540.)
            if isinstance(message, UserMessage):
                self._parent._last_progress_time = time.time()  # sub-agent progress
                self._clear_completed_sub_agents(message)
                # (b) write + delete emit (run_cce6f4b9). List-guard mirrors
                # _clear_completed_sub_agents (string content carries no blocks). Parent
                # AND sub-agent tool results arrive here as UserMessage ToolResultBlocks;
                # emitting on success stamps the WRITING session (correct tab isolation).
                _um_content = getattr(message, "content", None)
                if isinstance(_um_content, list):
                    for _blk in _um_content:
                        if not isinstance(_blk, ToolResultBlock):
                            continue
                        _tuid = getattr(_blk, "tool_use_id", None)
                        if not getattr(_blk, "is_error", False):
                            if _tuid in _pending_file_changes:
                                _um_writes = _pending_file_changes.pop(_tuid, None)
                                if _um_writes:
                                    _wr_events = await self._build_file_write_events(
                                        _um_writes, _resolve_cache
                                    )
                                    for _ev in _wr_events:
                                        yield _ev
                            if _tuid in _pending_file_deletes:
                                _um_dels = _pending_file_deletes.pop(_tuid, None)
                                if _um_dels:
                                    _del_events = await self._build_file_delete_events(
                                        _um_dels, _resolve_cache
                                    )
                                    for _ev in _del_events:
                                        yield _ev
                            # ── Backend-auto Canvas surface on pipeline COMPLETE ──
                            # (run_beff6754) A successful `run-update --status completed`
                            # tool_result → confirm run.json ACTUALLY reached completed
                            # (a gate-BLOCKED completion still exits 0, so the command
                            # string is not proof), then fire build_surface_events so the
                            # REPORT.md kind=knowledge event auto-opens Canvas WITHOUT the
                            # agent calling surface_run_outputs (which docs-only runs skip).
                            # Idempotent vs an explicit surface (per-session run-id set);
                            # fully fail-safe (any error → no surface, turn survives).
                            if _tuid in _pending_completion:
                                _um_rid = _pending_completion.pop(_tuid, None)
                                try:
                                    _um_seen = self._auto_surfaced_run_ids()
                                    if _um_rid and _um_rid not in _um_seen:
                                        _um_status = await asyncio.to_thread(
                                            read_run_status, _um_rid
                                        )
                                        if _um_status == "completed":
                                            await ensure_report_for_run(_um_rid)
                                            # glob+read+stat — off-loop entry point
                                            # keeps the hot streaming turn free.
                                            _um_surf = await build_surface_events_async(_um_rid)
                                            for _ev in _um_surf:
                                                yield _ev
                                            _um_seen.add(_um_rid)
                                        else:
                                            # Observability (Gate-2 meta-review): a
                                            # completion COMMAND whose run.json is not
                                            # actually 'completed' (gate BLOCKED it — CLI
                                            # still exited 0) drops the surface. debug, not
                                            # warning: a non-completed run-update is normal
                                            # + frequent, only useful when chasing "Canvas
                                            # didn't open after my pipeline finished".
                                            logger.debug(
                                                "auto-surface skipped: run %r status=%r "
                                                "(not 'completed')", _um_rid, _um_status,
                                            )
                                except Exception as _e:  # noqa: BLE001 — hot-path fail-safe
                                    logger.warning(
                                        "auto-surface on completion failed for %r: %s",
                                        _um_rid, _e,
                                    )
                        else:
                            # FAILED tool result: discard any pending write/delete/completion
                            # tracking so it can never surface later (symmetric with the
                            # AssistantMessage ToolResult branch). Turn-local dicts, so this
                            # is hygiene, not a cross-turn leak — but the two branches MUST
                            # behave identically.
                            _pending_file_changes.pop(_tuid, None)
                            _pending_file_deletes.pop(_tuid, None)
                            _pending_completion.pop(_tuid, None)
                continue

            # ── AssistantMessage: full content blocks ─────────────
            if isinstance(message, AssistantMessage):
                saw_assistant_message = True
                self._parent._last_progress_time = time.time()  # real content blocks
                content_blocks = []
                # ── Third leak signal: does this message have a REAL tool_use? ─
                # If the model emitted an actual ToolUseBlock, any <invoke> text
                # in a sibling TextBlock is the model TALKING about a tool call,
                # not leaking one — so the text-shape guard must NOT fire. The
                # leak is defined by malformed text WITH NO real tool call.
                _has_real_tool_use = any(
                    isinstance(b, ToolUseBlock) for b in message.content
                )
                for block in message.content:
                    if isinstance(block, TextBlock):
                        # ── Tool-call XML leak guard ──────────────────────
                        # The model occasionally emits tool-call SYNTAX
                        # (<invoke name=...><parameter ...>) as RAW plain text
                        # instead of a real tool_use block. The SDK hands it over
                        # as a TextBlock, so without this guard the raw XML
                        # persists to the messages DB, renders as half-finished
                        # XML, and the turn ends with no tool execution
                        # ("response stopped mid-way"). This is the DB-persist
                        # gate — drop the block (don't append/yield it), then
                        # kill + raise a retriable error so send() respawns with
                        # --resume and the model re-attempts with a proper
                        # tool_use. detect_tool_call_leak strips code fences /
                        # inline backticks (documentation), and we additionally
                        # require NO real tool_use in this message — both guard
                        # against false-firing on a turn that discusses the
                        # syntax. NOTE: the same text may have already streamed
                        # live via text_delta; the kill+resume + turn-end
                        # reconcile replaces that transient on-screen partial.
                        # DB persistence (the durable harm) happens only on the
                        # yielded `assistant` event, which we skip here.
                        from .session_utils import detect_tool_call_leak

                        if not _has_real_tool_use and detect_tool_call_leak(block.text):
                            logger.warning(
                                "session_unit.tool_call_leak_detected "
                                "session_id=%s — model emitted tool-call XML as "
                                "text (block_len=%d); dropping block + "
                                "killing for --resume respawn",
                                self._parent.session_id, len(block.text),
                            )
                            # Exception-safe: guarantee the retriable RuntimeError
                            # is raised even if kill() throws — otherwise a kill()
                            # error would propagate as a NON-retriable error and
                            # bypass the intended --resume retry path.
                            try:
                                await self._parent.kill()
                            except Exception as kill_exc:
                                logger.warning(
                                    "session_unit.tool_call_leak_kill_failed "
                                    "session_id=%s: %s",
                                    self._parent.session_id,
                                    f"{type(kill_exc).__name__}: {kill_exc}",
                                )
                            raise RuntimeError(
                                f"Tool-call XML leaked into text channel "
                                f"(session_id={self._parent.session_id}) — "
                                f"retrying with --resume for a proper tool_use"
                            )
                        content_blocks.append({"type": "text", "text": block.text})
                    elif isinstance(block, ThinkingBlock):
                        # Skip content-free thinking blocks. Bedrock CAN emit
                        # thinking blocks with empty/whitespace content under
                        # certain conditions (signature-only, redacted reasoning)
                        # — persisting them pollutes the DB and renders ghost
                        # widgets. NOTE: this is conditional, NOT universal.
                        # Verified 2026-06-01 (v1.17.5, claude-opus-4-8): under
                        # adaptive thinking the common case is FULL plaintext
                        # (529 non-empty deltas vs 7 empty in one turn; 12/12
                        # blocks had 51-985 chars of content). The empty-block
                        # path is the rare exception, not the rule.
                        # See: Knowledge/Notes/2026-06-01-thinking-block-7layer-diagnosis.md
                        if block.thinking and block.thinking.strip():
                            content_blocks.append({
                                "type": "thinking",
                                "thinking": block.thinking,
                                # Preserve signature — required to replay thinking
                                # to the API on any future multi-turn reconstruction.
                                "signature": getattr(block, "signature", ""),
                            })
                        else:
                            # The model DID respond (it produced a thinking block,
                            # just with redacted/empty content). Mark content as
                            # emitted so zombie-detection (streaming_dur<2s +
                            # not _content_emitted → kill+retry) and empty-result
                            # guards don't false-fire on a legitimate Opus 4.8
                            # turn whose only block is empty thinking. Skipping the
                            # block must not also remove the proof that the LLM
                            # answered.
                            self._parent._content_emitted = True
                    elif isinstance(block, ToolUseBlock):
                        # ── Track EVERY open tool_use for hang detection (run_fb6e94a9) ──
                        # Records emission time; cleared when the matching
                        # ToolResultBlock arrives. The PID watchdog reads this
                        # to tell a stuck tool from genuine thinking. Skip
                        # AskUserQuestion — it intentionally blocks on the user
                        # (the hook owns its lifecycle) and would false-trip.
                        if block.name != "AskUserQuestion":
                            # Store (start_time, tool_name) so the watchdog can
                            # apply a per-tool-type open window (run_fb6e94a9):
                            # Agent/Bash legitimately run long, so they get a
                            # longer window before the CPU-liveness probe.
                            self._parent._open_tool_uses[block.id] = (
                                time.time(), block.name,
                            )
                        # ── Track sub-agent (Agent tool) for progress observability ──
                        if block.name == "Agent" and isinstance(block.input, dict):
                            _agent_label = block.input.get("description") or block.input.get("prompt") or ""
                            self._parent._active_agent_tools[block.id] = {
                                "label": _agent_label[:80],
                                "start_time": time.time(),
                            }
                        # ── File-change tracking (run_cce6f4b9: event-driven emit) ──
                        # Track WRITE + DELETE targets keyed by tool_use_id; the matching
                        # ToolResult(success) emits file_changed at that point (mid-stream,
                        # on the writing session's own stream → correct session stamp / tab
                        # isolation). This RESTORES the per-tool emit that run_4de279ca
                        # replaced with a per-turn global git sweep (the regression root
                        # cause). Resolution + verdict happen at the ToolResult point (a
                        # failed tool never surfaces; a read/grep never pays resolution).
                        #   (a) Write/Edit/NotebookEdit → block.input.file_path
                        #   (b) Bash `>`/tee/cp/mv-DEST  → parse_bash_write_targets
                        #   (b') Bash rm/mv-SRC          → parse_bash_delete_targets
                        # A single Bash command may BOTH write and delete (e.g. `mv A B`):
                        # separate dicts, both keyed by the same block.id.
                        if block.name in ("Edit", "Write", "NotebookEdit") and isinstance(block.input, dict):
                            _fp = block.input.get("file_path", "")
                            if _fp:
                                _pending_file_changes[block.id] = [_fp]
                        elif block.name == "Bash" and isinstance(block.input, dict):
                            from core.file_change_classifier import (
                                parse_bash_write_targets,
                                parse_bash_delete_targets,
                            )
                            _cmd = block.input.get("command", "")
                            _write_targets = parse_bash_write_targets(_cmd)
                            if _write_targets:
                                _pending_file_changes[block.id] = _write_targets
                            # G1 (run_5a7be540): a Bash rm/mv-SRC deletes files → track
                            # them so the ToolResult(success) emits operation=deleted and
                            # the rail drops the stale row.
                            _del_targets = parse_bash_delete_targets(_cmd)
                            if _del_targets:
                                _pending_file_deletes[block.id] = _del_targets
                            # ── Backend-auto Canvas surface on pipeline COMPLETE ──
                            # (run_beff6754) If this Bash command is a `run-update
                            # --status completed`, register its run_id; the matching
                            # SUCCESSFUL ToolResult confirms run.json status and fires
                            # build_surface_events — so the REPORT.md kind=knowledge
                            # event auto-opens Canvas WITHOUT the agent having to call
                            # surface_run_outputs (which docs-only runs skip). This is
                            # only a PRE-FILTER; run.json is the authority at emit time.
                            _completion_run_id = parse_completion_run_id(_cmd)
                            if _completion_run_id:
                                _pending_completion[block.id] = _completion_run_id
                        # ── UI-action (ACT): agent drives its own UI (Run 2) ──
                        # The agent calls the ui_action tool; we observe it here,
                        # validate cmd against the fail-closed allowlist, and yield
                        # an ADDITIVE ui_command SSE event (the SDK still delivers the
                        # tool's normal result to the agent — this does not replace
                        # it). Mirrors the file_changed emit. The frontend derives its
                        # own event+target from cmd; a non-allowlisted cmd yields None
                        # here → nothing emitted (fail-closed at the source).
                        if block.name == UI_ACTION_FULL_TOOL_NAME and isinstance(block.input, dict):
                            # allow_abs: absolute host paths may reach the resolver
                            # ONLY on a genuine local-desktop session. _has_channel_context
                            # is True for ANY channel (owner-over-channel included), so
                            # `not _has_channel_context` == true local desktop. NOT
                            # is_channel_session (that is False for owner-over-channel →
                            # would leak abs paths into the owner's remote stream, C041).
                            # getattr default True → if the flag were ever absent, we
                            # treat it as "has channel context" → allow_abs False →
                            # fail CLOSED (reject abs). The attr is always init'd in
                            # SessionUnit.__init__, so this is belt-and-suspenders.
                            _has_chan = getattr(self._parent, "_has_channel_context", True)
                            _ui_ev = build_ui_command_event(
                                block.input.get("cmd"),
                                block.input.get("path"),
                                allow_abs=not _has_chan,
                            )
                            if _ui_ev is not None:
                                yield _ui_ev
                            # fall through: do NOT `continue` — the SDK runs the tool
                            # and returns its ack result to the agent normally.
                        # ── Finish-time PR-review batch (run_b8ea6d5c, CHANNEL A) ──
                        # The agent calls surface_run_outputs(run_id) at pipeline
                        # COMPLETE; we observe it here (same pattern as ui_action) and
                        # yield N additive file_changed events — one per committed
                        # source file, kind=source-final — so N persistent OUTPUTS rows
                        # appear at once. The SDK still delivers the tool's ack. This is
                        # the ONLY way N rows reach the rail (run-commit CLI can't emit).
                        if block.name == SURFACE_OUTPUTS_FULL_TOOL_NAME and isinstance(block.input, dict):
                            # ORDERING ROOT-FIX (run_f1fbf37d): guarantee the run's
                            # REPORT.md exists BEFORE we build the batch. The report
                            # is produced by a SEPARATE agent step (run-report); if
                            # surface_run_outputs is called before it, the report row
                            # is silently dropped. The consumer now ensures its own
                            # input — regenerate a missing/stub report in-process
                            # (off-loop, fail-safe: never raises) so build_surface_events
                            # (still PURE) finds it and emits the report row.
                            _sf_run_id = block.input.get("run_id")
                            # Idempotency (run_beff6754): surface a run AT MOST ONCE per
                            # session regardless of which path fires. SYMMETRIC guard —
                            # the completion-observation branch checks-before-emit, so
                            # this branch must too, else a completion that surfaced FIRST
                            # (backend-auto) followed by an explicit surface_run_outputs
                            # would double-emit (Gate-2 correctness finding). In the
                            # common flow (explicit surface precedes completion) the set
                            # is empty here, so this still emits normally.
                            _sf_seen = self._auto_surfaced_run_ids()
                            _sf_already = isinstance(_sf_run_id, str) and _sf_run_id in _sf_seen
                            if not _sf_already:
                                await ensure_report_for_run(_sf_run_id)
                                # off-loop entry point (glob+read+stat) — keeps the
                                # shared daemon loop (+/health) free mid-streaming-turn.
                                _sf_events = await build_surface_events_async(_sf_run_id)
                                for _sf_ev in _sf_events:
                                    yield _sf_ev
                                if isinstance(_sf_run_id, str) and _sf_run_id:
                                    _sf_seen.add(_sf_run_id)
                            # fall through: SDK runs the tool, returns its ack normally.
                        if block.name == "AskUserQuestion":
                            # The ask_question_gate PreToolUse hook intercepts this
                            # tool call BEFORE the CLI self-resolves it: it enqueues
                            # {kind:"ask_user_question", tool_use_id, questions} on the
                            # per-session queue and BLOCKS on wait_for_answer. The
                            # perm_task branch above surfaces the question SSE and
                            # transitions to WAITING_INPUT; continue_with_answer →
                            # set_answer unblocks the hook, which injects answers via
                            # updatedInput. So here we only SKIP the tool_use block —
                            # do NOT render it as a content widget, do NOT self-resolve
                            # or drain (the old headless self-resolution bug). The hook
                            # owns the lifecycle; the stream continues normally.
                            continue
                        if _has_tool_summarizer:
                            summary = summarize_tool_use(block.name, block.input)
                            category = get_tool_category(block.name)
                        else:
                            summary = f"{block.name}(...)"
                            category = "unknown"
                        content_blocks.append({
                            "type": "tool_use", "id": block.id,
                            "name": block.name, "summary": summary, "category": category,
                        })
                        # ── Record tool call for compaction guard ──
                        self._parent._compaction_guard.record_tool_call(
                            block.name, block.input
                        )
                        level = self._parent._compaction_guard.check()
                        if level != EscalationLevel.MONITORING:
                            guard_event = self._parent._compaction_guard.build_guard_event(level)
                            if guard_event:
                                yield guard_event
                            if level in (
                                EscalationLevel.HARD_WARN,
                                EscalationLevel.KILL,
                            ):
                                # Flush accumulated content blocks before
                                # interrupting — otherwise text/tool_use blocks
                                # from earlier in this AssistantMessage are lost.
                                if content_blocks:
                                    yield {
                                        "type": "assistant",
                                        "content": content_blocks,
                                        "model": getattr(message, "model", None),
                                    }
                                logger.warning(
                                    "compaction_guard.interrupt "
                                    "session_id=%s action=%s",
                                    self._parent.session_id, level.value,
                                )
                                await self._parent.interrupt()
                                return
                    elif isinstance(block, ToolResultBlock):
                        # ── Clear sub-agent progress when Agent tool completes ──
                        self._parent._active_agent_tools.pop(block.tool_use_id, None)
                        # ── Clear open-tool tracking; the tool produced a result ──
                        # (run_fb6e94a9). A completed tool resets the once-per-
                        # episode interrupt guard so a LATER stuck tool in the
                        # same turn can still be escaped.
                        self._parent._open_tool_uses.pop(block.tool_use_id, None)
                        self._parent._tool_hang_interrupted = False
                        self._parent._tool_hang_interrupt_at = None
                        # Recovery + progress → reset escalation counter (v2 MED).
                        self._parent._tool_hang_episodes = 0
                        block_content = str(block.content) if block.content else ""
                        if _has_tool_summarizer:
                            truncated, was_truncated = truncate_tool_result(block_content)
                        else:
                            truncated = block_content[:2000]
                            was_truncated = len(block_content) > 2000
                        content_blocks.append({
                            "type": "tool_result", "tool_use_id": block.tool_use_id,
                            "content": truncated, "is_error": getattr(block, "is_error", False),
                            "truncated": was_truncated,
                        })
                        # ── Emit file WRITE + DELETE event(s) (run_cce6f4b9) ──
                        # Event-driven live surfacing: a Write/Edit/Bash tool result here
                        # emits operation=written (restored per-tool emit, replacing the
                        # deleted per-turn git sweep); a rm/mv-SRC result emits
                        # operation=deleted (drops the stale rail row — the sweep can't see
                        # a gone file). Both fire only on a SUCCESSFUL result and are
                        # stamped by the writing session (correct tab isolation).
                        _fc_events: list[dict] = []
                        if not getattr(block, "is_error", False):
                            _written_paths = _pending_file_changes.pop(block.tool_use_id, None)
                            if _written_paths:
                                _fc_events += await self._build_file_write_events(
                                    _written_paths, _resolve_cache
                                )
                            _deleted_paths = _pending_file_deletes.pop(block.tool_use_id, None)
                            if _deleted_paths:
                                _fc_events += await self._build_file_delete_events(
                                    _deleted_paths, _resolve_cache
                                )
                            # ── Backend-auto Canvas surface on pipeline COMPLETE ──
                            # (run_beff6754) A successful `run-update --status completed`
                            # tool_result: confirm run.json ACTUALLY reached completed
                            # (a gate-BLOCKED completion still exits 0 — the command
                            # string is not proof), then fire build_surface_events so the
                            # REPORT.md kind=knowledge event auto-opens Canvas. Idempotent
                            # vs an explicit surface_run_outputs (per-session run-id set).
                            # Fully fail-safe: any error degrades to prior behavior (no
                            # surface) and never breaks the streaming turn.
                            _completion_rid = _pending_completion.pop(block.tool_use_id, None)
                            if _completion_rid:
                                try:
                                    _seen = self._auto_surfaced_run_ids()
                                    if _completion_rid not in _seen:
                                        _status = await asyncio.to_thread(
                                            read_run_status, _completion_rid
                                        )
                                        if _status == "completed":
                                            await ensure_report_for_run(_completion_rid)
                                            # off-loop entry point (glob+read)
                                            _fc_events += await build_surface_events_async(
                                                _completion_rid
                                            )
                                            _seen.add(_completion_rid)
                                except Exception as _e:  # noqa: BLE001 — hot-path fail-safe
                                    logger.warning(
                                        "auto-surface on completion failed for %r: %s",
                                        _completion_rid, _e,
                                    )
                        else:
                            # a FAILED tool result: discard any pending tracking (never
                            # surface a write/delete/completion that errored) so it can't
                            # leak later.
                            _pending_file_changes.pop(block.tool_use_id, None)
                            _pending_file_deletes.pop(block.tool_use_id, None)
                            _pending_completion.pop(block.tool_use_id, None)
                        if _fc_events:
                            # Flush accumulated content blocks first (ordering:
                            # the tool_result must precede its file_changed).
                            if content_blocks:
                                self._parent._content_emitted = True
                                yield {
                                    "type": "assistant",
                                    "content": content_blocks,
                                    "model": getattr(message, "model", None),
                                }
                                content_blocks = []
                            for _ev in _fc_events:
                                yield _ev
                if content_blocks:
                    self._parent._content_emitted = True
                    yield {
                        "type": "assistant",
                        "content": content_blocks,
                        "model": getattr(message, "model", None),
                    }
                continue

            # ── ResultMessage — response complete or error ──────────
            if isinstance(message, ResultMessage):
                is_error = getattr(message, "is_error", False)
                subtype = getattr(message, "subtype", None)

                # ── Turn limit reached (NOT a real error) ─────────
                # CLI emits is_error=True + subtype="error_max_turns"
                # when the configured max_turns limit is hit. This is a
                # graceful pause, not an error — the agent completed its
                # last tool call successfully but the CLI won't start
                # another API roundtrip. The user can Resume to continue.
                #
                # BUG FIX (2026-06-01): Previously this fell through to
                # the error path, yielding an error event that caused the
                # frontend to show "Interrupted" and potentially clear
                # streamed content. Now we emit a distinct event type so
                # the frontend can show "Turn limit reached" and preserve
                # all previously streamed content.
                #
                # Evidence: run_bbe3f167 — pipeline hit 101 turns (CLI
                # default maxTurns=100), emitted error_max_turns, frontend
                # showed "Interrupted" and user had to manually Resume.
                if is_error and subtype == "error_max_turns":
                    self._parent._active_agent_tools = {}  # Clear stale sub-agent progress
                    num_turns = getattr(message, "num_turns", None)
                    logger.info(
                        "session_unit.turn_limit_reached session_id=%s "
                        "num_turns=%s subtype=%s",
                        self._parent.session_id, num_turns, subtype,
                    )
                    # Yield a non-error event — frontend preserves content
                    yield {
                        "type": "turn_limit_reached",
                        "num_turns": num_turns,
                        "message": (
                            "Turn limit reached — send a message to continue."
                        ),
                    }
                    # Transition to IDLE (not error) — session is healthy,
                    # user can send the next message to continue work.
                    self._parent._transition(SessionState.IDLE)
                    self._parent.last_used = time.time()
                    # CLI exited after error_max_turns (exit code 1).
                    # Clear process references so next send() knows to
                    # respawn (instead of writing to dead pipe → crash →
                    # retry). Keep _sdk_session_id intact so respawn uses
                    # --resume and preserves conversation context.
                    self._parent._client = None
                    self._parent._wrapper = None
                    # Still emit usage/metadata for this completed segment
                    self._parent._lifecycle_response_count += 1
                    usage = getattr(message, "usage", None) or {}
                    logger.info(
                        "session_unit.result_usage session_id=%s "
                        "raw_usage=%s input_tokens=%s model=%s "
                        "lifecycle_response=%d",
                        self._parent.session_id,
                        usage,
                        usage.get("input_tokens") if usage else None,
                        self._parent._model_name,
                        self._parent._lifecycle_response_count,
                    )
                    # Yield result event so frontend knows the turn ended
                    yield {
                        "type": "result",
                        "subtype": "turn_limit_reached",
                        "stop_reason": "turn_limit",
                        "session_id": self._parent.session_id,
                        "duration_ms": getattr(message, "duration_ms", 0),
                        "total_cost_usd": getattr(message, "total_cost_usd", None),
                        "num_turns": num_turns,
                        "usage": {
                            "input_tokens": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                        } if usage else None,
                    }
                    # Persist token usage (same as normal result path)
                    if usage:
                        try:
                            import database
                            asyncio.get_running_loop().create_task(
                                database.db.record_token_usage(
                                    session_id=self._parent.session_id,
                                    source="cli",
                                    input_tokens=usage.get("input_tokens") or 0,
                                    output_tokens=usage.get("output_tokens") or 0,
                                    cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                    cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                    cost_usd=getattr(message, "total_cost_usd", None),
                                    model=self._parent._model_name,
                                )
                            )
                        except Exception:
                            pass  # fire-and-forget — never break streaming

                    for meta_event in self._parent._emit_post_stream_metadata(
                        usage, num_turns=num_turns or 1,
                    ):
                        yield meta_event
                    return

                if is_error or subtype == "error_during_execution":
                    error_text = str(
                        getattr(message, "result", "")
                        or getattr(message, "error", "")
                    )

                    from .session_utils import (
                        _is_retriable_error,
                        _sanitize_sdk_error,
                        _build_error_event,
                    )

                    if self._parent._interrupted:
                        self._parent._interrupted = False
                        self._parent._transition(SessionState.IDLE)
                        self._parent.last_used = time.time()
                        # Still yield the error so the user sees what
                        # went wrong — silently swallowing SDK errors
                        # causes blank responses (e.g. unknown slash
                        # commands).  Only suppress if error_text is
                        # genuinely empty (pure cancellation).
                        if error_text.strip():
                            friendly, suggested = _sanitize_sdk_error(
                                error_text
                            )
                            yield _build_error_event(
                                code="SDK_ERROR",
                                message=friendly,
                                suggested_action=suggested,
                            )
                        return

                    # ── Poisoned-subprocess self-heal (Layer 2) ───────
                    # A subprocess that was previously interrupted (by the
                    # CompactionGuard ladder or a user Stop) can be left in a
                    # corrupt turn-state.  When the NEXT send() reuses it, it
                    # returns an INSTANT error_during_execution with no error
                    # detail and no streamed content (≈ms after query).  This is
                    # the SAME failure as the zombie detector below (stream ended
                    # instantly after interrupt) but shaped as an error
                    # ResultMessage instead of an empty stream — so it bypassed
                    # that guard, was treated as non-retriable (empty text), and
                    # the dead subprocess was reused on every retry, producing
                    # the "response stops half-way / must send several times"
                    # loop.  Route it into the SAME kill + --resume respawn: a
                    # fresh subprocess loads the conversation from disk, not the
                    # poisoned in-memory state.  Guarded tightly (empty text +
                    # no content + <2s) so a genuine mid-generation error — which
                    # has real text or arrives after streamed content — is still
                    # surfaced, never silently retried.
                    streaming_dur = (
                        time.time() - self._parent._streaming_start_time
                        if self._parent._streaming_start_time else 0.0
                    )
                    if (
                        subtype == "error_during_execution"
                        and not error_text.strip()
                        and not self._parent._content_emitted
                        and streaming_dur < 2.0
                    ):
                        logger.warning(
                            "session_unit.zombie_via_error session_id=%s "
                            "duration=%.3fs subtype=%s error_text=empty "
                            "content_emitted=False — killing for --resume respawn",
                            self._parent.session_id, streaming_dur, subtype,
                        )
                        await self._parent.kill()
                        # Same retriable signal as the empty-stream zombie path
                        # (matches the r"Zombie subprocess detected" pattern in
                        # _is_retriable_error) → send() respawns with --resume.
                        raise RuntimeError(
                            f"Zombie subprocess detected: error_during_execution "
                            f"with no content in {streaming_dur:.1f}s "
                            f"(session_id={self._parent.session_id})"
                        )

                    if _is_retriable_error(error_text):
                        raise RuntimeError(f"Retriable SDK error: {error_text}")

                    # Non-retriable error — yield error event and RETURN.
                    # BUG FIX (2026-06-14): Previously this had no return
                    # statement, causing fall-through to the normal result
                    # path below.  Result: (1) backend log showed normal
                    # streaming→idle with result_usage, zero error signal;
                    # (2) frontend received BOTH an error event AND a
                    # normal result event (double-delivery); (3) the
                    # output_tokens=0 empty-response guard (line ~2621)
                    # was bypassed because it checks `not is_error`.
                    # Evidence: session e2c335b9 2026-06-14 16:57-17:08,
                    # two turns with 392 and 0 output tokens showed as
                    # normal completions in logs.
                    logger.warning(
                        "session_unit.sdk_error session_id=%s is_error=%s "
                        "subtype=%s error_text=%.200s",
                        self._parent.session_id, is_error, subtype, error_text,
                    )
                    friendly, suggested = _sanitize_sdk_error(error_text)
                    yield _build_error_event(
                        code="SDK_ERROR", message=friendly, suggested_action=suggested,
                    )
                    # Clear stale sub-agent progress (matches
                    # turn_limit_reached and interrupt paths).
                    self._parent._active_agent_tools = {}
                    # Transition to IDLE — session is not broken, just this
                    # turn failed.  User can retry.  Matches the interrupted
                    # path (line ~2484) which also transitions to IDLE.
                    self._parent._transition(SessionState.IDLE)
                    self._parent.last_used = time.time()
                    # If CLI subprocess died (the error it returned may have
                    # been its dying gasp), clear client refs so next send()
                    # respawns instead of writing to a dead pipe.
                    # _sdk_session_id is preserved for --resume on respawn.
                    if self._parent.pid:
                        try:
                            os.kill(self._parent.pid, 0)  # signal 0 = liveness check
                        except (ProcessLookupError, OSError):
                            # Process is dead — clear refs
                            self._parent._client = None
                            self._parent._wrapper = None
                    # Still track usage for cost accounting (even failed
                    # turns consume input tokens for the prompt).
                    self._parent._lifecycle_response_count += 1
                    usage = getattr(message, "usage", None) or {}
                    logger.info(
                        "session_unit.result_usage session_id=%s "
                        "raw_usage=%s input_tokens=%s model=%s "
                        "lifecycle_response=%d (ERROR path)",
                        self._parent.session_id,
                        usage,
                        usage.get("input_tokens") if usage else None,
                        self._parent._model_name,
                        self._parent._lifecycle_response_count,
                    )
                    # Yield result event so frontend knows the turn ended
                    yield {
                        "type": "result",
                        "subtype": "sdk_error",
                        "stop_reason": "error",
                        "session_id": self._parent.session_id,
                        "duration_ms": getattr(message, "duration_ms", 0),
                        "total_cost_usd": getattr(message, "total_cost_usd", None),
                        "num_turns": getattr(message, "num_turns", 1),
                        "usage": {
                            "input_tokens": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                        } if usage else None,
                    }
                    # Persist token usage (fire-and-forget)
                    if usage:
                        try:
                            import database
                            asyncio.get_running_loop().create_task(
                                database.db.record_token_usage(
                                    session_id=self._parent.session_id,
                                    source="cli",
                                    input_tokens=usage.get("input_tokens") or 0,
                                    output_tokens=usage.get("output_tokens") or 0,
                                    cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                    cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                    cost_usd=getattr(message, "total_cost_usd", None),
                                    model=self._parent._model_name,
                                )
                            )
                        except Exception:
                            pass  # fire-and-forget
                    # Emit context metadata so frontend updates context
                    # ring/bar — especially important for timeout errors
                    # that correlate with large contexts.
                    for meta_event in self._parent._emit_post_stream_metadata(
                        usage, num_turns=getattr(message, "num_turns", 1) or 1,
                    ):
                        yield meta_event
                    return

                # Yield result event with usage metrics
                self._parent._lifecycle_response_count += 1
                usage = getattr(message, "usage", None) or {}
                logger.info(
                    "session_unit.result_usage session_id=%s "
                    "raw_usage=%s input_tokens=%s model=%s "
                    "lifecycle_response=%d",
                    self._parent.session_id,
                    usage,
                    usage.get("input_tokens") if usage else None,
                    self._parent._model_name,
                    self._parent._lifecycle_response_count,
                )

                # ── Observability: cache miss detection ───────────
                # A cache miss (cache_read=0) with large context means
                # full prompt was re-sent to Bedrock — latency spike
                # risk and potential timeout trigger.  Log for
                # post-mortem diagnosis.
                cache_read = usage.get("cache_read_input_tokens") or 0
                cache_create = usage.get("cache_creation_input_tokens") or 0
                if cache_read == 0 and cache_create > 50_000:
                    logger.info(
                        "session_unit.cache_miss session_id=%s "
                        "cache_creation=%d cache_read=0 — full prompt "
                        "sent (latency risk)",
                        self._parent.session_id, cache_create,
                    )

                stop_reason = getattr(message, "stop_reason", None) or ""
                subtype = getattr(message, "subtype", "") or ""

                # Log stop_reason for observability — especially important
                # for detecting max_tokens truncation (model forced to stop
                # mid-sentence). Distinct from end_turn (model chose to stop).
                if stop_reason and stop_reason != "end_turn":
                    logger.warning(
                        "session_unit.non_standard_stop session_id=%s "
                        "stop_reason=%s subtype=%s output_tokens=%s "
                        "content_emitted=%s",
                        self._parent.session_id,
                        stop_reason,
                        subtype,
                        (usage.get("output_tokens") or 0) if usage else 0,
                        self._parent._content_emitted,
                    )

                # ── Retry guards — MUST run BEFORE yielding `result` ──
                # A `result` event is the definitive turn-end signal for the
                # frontend (it stops the spinner, marks the tab idle, clears
                # pending state). If we yield it and THEN raise-for-retry, the
                # frontend finalizes the turn while the backend respawns and
                # keeps streaming — a torn state: the retry's `session_start`
                # arrives in idle mode (reducer no-op, no setIsStreaming(true))
                # so the retried content streams into a tab the UI thinks is
                # done. Raising HERE means a blank/degraded turn never reaches
                # the frontend as a turn-end; send() respawns and the retry's
                # session_start lands while still STREAMING → UI stays armed.
                streaming_dur = (
                    time.time() - self._parent._streaming_start_time
                    if self._parent._streaming_start_time else None
                )
                output_tok = (usage.get("output_tokens") or 0) if usage else 0

                # (a) Post-interrupt corruption: warm-but-broken subprocess
                # returns an empty ResultMessage instantly (<2s, no content).
                # See: 2026-03-22 12:36:08 instant idle after interrupt.
                if (
                    streaming_dur is not None
                    and streaming_dur < 2.0
                    and not self._parent._content_emitted
                    and not is_error
                    and saw_assistant_message  # Only degraded if LLM tried to respond
                ):
                    logger.warning(
                        "session_unit.empty_result_detected "
                        "session_id=%s duration=%.3fs — subprocess "
                        "degraded after interrupt, killing for respawn",
                        self._parent.session_id, streaming_dur,
                    )
                    await self._parent.kill()
                    raise RuntimeError(
                        f"Empty result from degraded subprocess: "
                        f"stream ended in {streaming_dur:.1f}s with no "
                        f"content (session_id={self._parent.session_id})"
                    )

                # (b) API empty / blank-success no-op at any duration:
                # Bedrock 429/503/timeout (empty subtype) OR a subtype="success"
                # envelope with 0 output tokens and no content — the live
                # blank-turn bug (session 2e87b27f 2026-06-26 17:43). Raising
                # triggers the send() retry loop (string matched by
                # _is_retriable_error). Predicate shared with the regression
                # test via _is_blank_api_result so the two can never drift.
                if _is_blank_api_result(
                    content_emitted=self._parent._content_emitted,
                    is_error=is_error,
                    interrupted=self._parent._interrupted,
                    output_tokens=output_tok,
                    subtype=subtype,
                ):
                    logger.warning(
                        "session_unit.api_empty_response session_id=%s "
                        "duration=%.1fs output_tokens=0 subtype='%s' — "
                        "raising for retry (before result yield)",
                        self._parent.session_id,
                        streaming_dur or 0,
                        subtype,
                    )
                    raise RuntimeError(
                        f"API returned empty response (output_tokens=0, "
                        f"duration={(streaming_dur or 0):.1f}s) — likely "
                        f"transient 429/503/timeout "
                        f"(session_id={self._parent.session_id})"
                    )

                # run_cce6f4b9: the TURN-END git sweep (sweep_turn_delta) was DELETED —
                # live WRITE surfacing is now the event-driven per-tool emit above
                # (_build_file_write_events at each ToolResult), which is session-correct
                # and progressive. Non-parent writes (sub-agent/skill/CLI/hook) surface at
                # PIPELINE FINISH via sweep_run_changes / surface_run_outputs. Nothing to
                # emit here anymore — go straight to the `result` yield.

                yield {
                    "type": "result",
                    "subtype": subtype,
                    "stop_reason": stop_reason,
                    "session_id": self._parent.session_id,
                    "duration_ms": getattr(message, "duration_ms", 0),
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "num_turns": getattr(message, "num_turns", 1),
                    "usage": {
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                    } if usage else None,
                }

                # ── Persist token usage (fire-and-forget) ─────────
                if usage:
                    try:
                        import database
                        asyncio.get_running_loop().create_task(
                            database.db.record_token_usage(
                                session_id=self._parent.session_id,
                                source="cli",
                                input_tokens=usage.get("input_tokens") or 0,
                                output_tokens=usage.get("output_tokens") or 0,
                                cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                cost_usd=getattr(message, "total_cost_usd", None),
                                model=self._parent._model_name,
                            )
                        )
                    except Exception:
                        pass  # fire-and-forget — never break streaming

                # ── Context usage & metadata bridge ────────────────
                result_num_turns = getattr(message, "num_turns", 1) or 1
                for meta_event in self._parent._emit_post_stream_metadata(
                    usage, num_turns=result_num_turns,
                ):
                    yield meta_event

                # ── Health sensor: record turn metrics ─────────────
                # Duration and fresh RSS feed the self-healing system.
                # Uses get_process_rss_mb() for real-time sampling instead
                # of _peak_tree_rss_bytes (which only updates every 60s via
                # LifecycleManager). Falls back to peak if sampling returns 0.
                turn_duration = getattr(message, "duration_ms", 0) or 0
                fresh_rss = get_process_rss_mb(self._parent.pid) if self._parent.pid else 0
                turn_rss = fresh_rss or (self._parent._peak_tree_rss_bytes // (1024 * 1024))
                self._parent._health_sensor.record_turn(
                    latency_ms=float(turn_duration),
                    rss_mb=turn_rss,
                    had_error=False,
                )

                # ── MCP health check (first response only) ────────
                if not self._parent._mcp_health_checked and self._parent._configured_mcps:
                    try:
                        mcp_warning = await self._parent._check_mcp_health()
                        if mcp_warning:
                            yield mcp_warning
                    except Exception as mcp_exc:
                        logger.debug(
                            "session_unit.mcp_health_check_error "
                            "session_id=%s: %s",
                            self._parent.session_id, mcp_exc,
                        )

                # ── Post-interrupt corruption & blank-result retry guards ──
                # MOVED earlier — these now run BEFORE the `result` yield above
                # (a blank/degraded turn must never reach the frontend as a
                # turn-end, or the retry streams into a UI that thinks it's
                # done). See the "Retry guards" block above the yield.

                self._parent._transition(SessionState.IDLE)
                self._parent.last_used = time.time()
                self._parent._retry_count = 0
                # Resume-poison guard: this is the ONE clean-completion point —
                # a real ResultMessage reached the user after the blank/degraded
                # retry guards above did NOT fire. Bless the subprocess so the
                # next send() may reuse it warm (fast path). Every other turn end
                # (interrupt / disconnect / error / max_turns) leaves the flag
                # False (set on STREAMING entry), forcing a recycle-before-reuse.
                self._parent._last_turn_clean = True

                # ── Proactive RSS check (Trigger B: post-turn) ────
                # Now in IDLE — check if process tree RSS is too high.
                # If so, compact → kill → lazy resume on next send().
                try:
                    await self._parent._check_rss_and_proactive_restart()
                except Exception as rss_exc:
                    logger.debug(
                        "session_unit.post_turn_rss_check failed "
                        "(non-fatal): %s", rss_exc,
                    )

                # ── Context-ring soft compaction (Root 2 / AC1) ───
                # Also in IDLE — if the context ring is large (>SOFT_COMPACT_PCT),
                # compact BEFORE the next slow turn. Soft-first (compact, no kill).
                # Skipped automatically if the RSS path above already killed
                # (state would no longer be IDLE).
                try:
                    await self._parent._check_context_soft_compact()
                except Exception as soft_exc:
                    logger.debug(
                        "session_unit.post_turn_soft_compact failed "
                        "(non-fatal): %s", soft_exc,
                    )

                return

        # Stream ended without a result message.
        if self._parent.state == SessionState.STREAMING:
            # ── Zombie detection ──────────────────────────────────
            # If the stream ended very fast (< 2s) with no content,
            # the subprocess is likely dead (e.g. corrupted after
            # interrupt).  Kill it so the caller's retry logic can
            # respawn a fresh process with --resume.
            streaming_dur = (
                time.time() - self._parent._streaming_start_time
                if self._parent._streaming_start_time else 0.0
            )
            if streaming_dur < 2.0 and not self._parent._content_emitted:
                logger.warning(
                    "session_unit.zombie_detected session_id=%s "
                    "duration=%.3fs content_emitted=False — killing "
                    "subprocess for respawn",
                    self._parent.session_id, streaming_dur,
                )
                await self._parent.kill()
                raise RuntimeError(
                    f"Zombie subprocess detected: stream ended in "
                    f"{streaming_dur:.1f}s with no content "
                    f"(session_id={self._parent.session_id})"
                )

            self._parent._transition(SessionState.IDLE)
            self._parent.last_used = time.time()

    # ── SSE disconnect recovery ─────────────────────────────────────


    # ═══════════════════════════════════════════════════════════════
    # End of migrated methods
    # ═══════════════════════════════════════════════════════════════
