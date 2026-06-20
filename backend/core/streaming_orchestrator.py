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

if TYPE_CHECKING:
    from .session_unit import SessionState, SessionUnit

logger = logging.getLogger(__name__)


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
        parent_tool_use_id: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Stream a query through the SDK and yield formatted SSE events.

        Public entry point — replaces direct SessionUnit._stream_response() calls.
        Delegates to self._stream_response() which owns the query send logic.

        Args:
            query_content: User message text (str) or multimodal blocks (list).
            parent_tool_use_id: When set, message is a tool result response.

        Yields:
            Formatted SSE event dicts (text_delta, thinking_delta, tool_use, etc.)
        """
        async for event in self._stream_response(
            query_content, parent_tool_use_id=parent_tool_use_id
        ):
            yield event

    @property
    def stall_seconds(self) -> Optional[float]:
        """Proxy to parent's streaming_stall_seconds."""
        return self._parent.streaming_stall_seconds

    # ═══════════════════════════════════════════════════════════════
    # Migrated from session_unit.py — _stream_response
    # ═══════════════════════════════════════════════════════════════

    async def _stream_response(
        self,
        query_content: Any,
        parent_tool_use_id: str | None = None,
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
            parent_tool_use_id: When set, the message is linked to a prior
                tool_use block (e.g. AskUserQuestion response). The CLI uses
                this to route the answer as a tool result rather than a new
                conversation turn.
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
                    "parent_tool_use_id": parent_tool_use_id,
                }
                yield msg

            await self._parent._client.query(_multimodal_gen())
        elif parent_tool_use_id:
            # Tool result response (e.g. AskUserQuestion answer) — must use
            # the streaming protocol with parent_tool_use_id so the CLI treats
            # it as a tool result, not a new user message.
            async def _tool_result_gen():
                msg = {
                    "type": "user",
                    "message": {"role": "user", "content": query_content},
                    "parent_tool_use_id": parent_tool_use_id,
                }
                yield msg

            await self._parent._client.query(_tool_result_gen())
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
        INIT_TIMEOUT = 180.0    # First message: 180s (cross-region Bedrock)
        MESSAGE_TIMEOUT = self._parent._compute_message_timeout()  # Adaptive: scales with context

        is_resume = self._parent._sdk_session_id is not None
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
        _pending_file_changes: dict[str, str] = {}  # tool_use_id → file_path for Edit/Write

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
                done, pending = await asyncio.wait(
                    [sdk_task, perm_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
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

            # ── Permission request won the race ───────────────────
            if perm_task in done:
                try:
                    perm_request = perm_task.result()
                except Exception:
                    # Queue.get shouldn't fail, but be safe
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
                        yield {"type": "text_delta", "text": delta["text"], "index": event_data.get("index", 0)}
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
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

            # ── AssistantMessage: full content blocks ─────────────
            if isinstance(message, AssistantMessage):
                saw_assistant_message = True
                content_blocks = []
                for block in message.content:
                    if isinstance(block, TextBlock):
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
                        # ── Track sub-agent (Agent tool) for progress observability ──
                        if block.name == "Agent" and isinstance(block.input, dict):
                            _agent_label = block.input.get("description") or block.input.get("prompt") or ""
                            self._parent._active_agent_tools[block.id] = {
                                "label": _agent_label[:80],
                                "start_time": time.time(),
                            }
                        # ── Track file-modifying tools for file_changed events ──
                        if block.name in ("Edit", "Write", "NotebookEdit") and isinstance(block.input, dict):
                            _fp = block.input.get("file_path", "")
                            if _fp:
                                _pending_file_changes[block.id] = _fp
                        if block.name == "AskUserQuestion":
                            questions = block.input.get("questions", [])
                            # Root-1 SSOT Phase 2 (L4/F3): record the outstanding
                            # tool_use BEFORE the transition so the drain worker
                            # never injects a turn while this question is open, and
                            # the read API (L5) can re-surface the question if its
                            # SSE event is lost.
                            self._parent._pending_tool_use_id = block.id
                            self._parent._pending_question = {
                                "tool_use_id": block.id,
                                "questions": questions,
                            }
                            yield {
                                "type": "ask_user_question",
                                "toolUseId": block.id,
                                "questions": questions,
                                "sessionId": self._parent.session_id,
                            }
                            self._parent._transition(SessionState.WAITING_INPUT)
                            self._parent.last_used = time.time()
                            # Drain remaining messages until ResultMessage
                            # so the shared message queue is clean for the
                            # next receive_response() call in continue_with_answer.
                            # Without this, the stale ResultMessage from this
                            # turn would terminate the next response immediately.
                            try:
                                while True:
                                    drain_timeout = 5.0
                                    sdk_task = asyncio.ensure_future(
                                        asyncio.wait_for(
                                            _next_or_sentinel(),
                                            timeout=drain_timeout,
                                        )
                                    )
                                    # Cancel perm_task race — we only care about SDK
                                    perm_task_drain = asyncio.ensure_future(perm_queue.get())
                                    done, pending = await asyncio.wait(
                                        [sdk_task, perm_task_drain],
                                        return_when=asyncio.FIRST_COMPLETED,
                                    )
                                    for t in pending:
                                        t.cancel()
                                        try:
                                            await t
                                        except (asyncio.CancelledError, asyncio.TimeoutError):
                                            pass
                                    if sdk_task in done:
                                        try:
                                            drain_msg = sdk_task.result()
                                        except (asyncio.TimeoutError, Exception):
                                            break
                                        if drain_msg is _STREAM_EXHAUSTED:
                                            break
                                        if isinstance(drain_msg, ResultMessage):
                                            logger.debug(
                                                "session_unit: drained ResultMessage "
                                                "after AskUserQuestion (session=%s)",
                                                self._parent.session_id,
                                            )
                                            break
                                    else:
                                        # perm_task won — ignore, keep draining
                                        pass
                            except Exception as drain_err:
                                logger.warning(
                                    "session_unit: drain after AskUserQuestion "
                                    "failed: %s (session=%s)",
                                    drain_err, self._parent.session_id,
                                )
                            return
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
                        # ── Emit file_changed event for Edit/Write completions ──
                        _changed_path = _pending_file_changes.pop(block.tool_use_id, None)
                        if _changed_path and not getattr(block, "is_error", False):
                            # Flush accumulated content blocks first, then emit file_changed
                            if content_blocks:
                                self._parent._content_emitted = True
                                yield {
                                    "type": "assistant",
                                    "content": content_blocks,
                                    "model": getattr(message, "model", None),
                                }
                                content_blocks = []
                            yield {"type": "file_changed", "path": _changed_path}
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

                # ── Post-interrupt corruption detection ────────────
                # After a CompactionGuard interrupt, the CLI subprocess
                # may stay alive but return empty ResultMessages instantly
                # (<2s, no content).  The subprocess is "warm but broken."
                # Kill it so the retry logic can respawn a fresh process.
                # See: 2026-03-22 12:36:08 instant idle after interrupt.
                streaming_dur = (
                    time.time() - self._parent._streaming_start_time
                    if self._parent._streaming_start_time else None
                )
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

                # ── API empty response detection (any duration) ───────
                # Catches: Bedrock 429/503/timeout that returns a
                # ResultMessage with output_tokens=0 and no content
                # emitted.  The fast-empty guard above catches subprocess
                # corruption (<2s).  This catches API-level failures that
                # take longer (e.g. connection held open then dropped).
                # Raising triggers the existing retry loop in send().
                output_tok = (usage.get("output_tokens") or 0) if usage else 0
                if (
                    not self._parent._content_emitted
                    and not is_error
                    and not self._parent._interrupted
                    and output_tok == 0
                    and not subtype  # empty subtype = API didn't respond
                ):
                    logger.warning(
                        "session_unit.api_empty_response session_id=%s "
                        "duration=%.1fs output_tokens=0 subtype='%s' — "
                        "raising for retry",
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

                self._parent._transition(SessionState.IDLE)
                self._parent.last_used = time.time()
                self._parent._retry_count = 0

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
