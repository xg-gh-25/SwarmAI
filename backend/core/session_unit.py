"""SessionUnit — one tab's complete subprocess lifecycle state machine.

Part of the multi-session re-architecture.  Each ``SessionUnit`` owns
exactly one Claude CLI subprocess and manages its lifecycle through a 5-state
machine: COLD → STREAMING → IDLE → DEAD → COLD.

Public symbols:

- ``SessionState``   — Enum of the 5 lifecycle states.
- ``SessionUnit``    — Per-tab state machine with subprocess ownership.
- ``_spawn_lock``    — Module-level ``asyncio.Lock`` for env isolation
                       during subprocess spawn (Rule 6).

This module contains state management and subprocess lifecycle logic:
``send()``, ``_spawn()``, ``_stream_response()``, and ``kill()``.
The ``interrupt()``, ``continue_with_answer()``, ``continue_with_permission()``,
and ``compact()`` methods are added by task 3.3.

No prompt-building, routing, or hook-execution logic lives here.

Design reference:
    ``.kiro/specs/multi-session-rearchitecture/design.md`` §1 SessionUnit
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import traceback
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    from core.claude_environment import _ClaudeClientWrapper

logger = logging.getLogger(__name__)

# Module-level lock that serializes subprocess spawn operations.
# Held during _configure_claude_environment + wrapper.__aenter__() to
# prevent concurrent sessions from racing on os.environ mutations.
# INTENTIONALLY module-level (not per-instance): os.environ is process-global,
# so ALL spawns across ALL SessionUnit instances must serialize.
# If you need multiple SessionRouter instances (e.g., tests), mock this lock.
_spawn_lock = asyncio.Lock()


class SessionState(Enum):
    """Lifecycle states for a single chat-tab subprocess.

    State transition table (see design.md for full details):

        COLD  →  STREAMING       send() — spawns subprocess
        IDLE  →  STREAMING       send() — reuses subprocess
        STREAMING → IDLE         Response complete
        STREAMING → WAITING_INPUT  Permission prompt / ask_user_question
        WAITING_INPUT → STREAMING  User answers
        STREAMING → DEAD         Crash / kill
        WAITING_INPUT → DEAD     Crash / kill
        IDLE → DEAD              TTL expired / evicted
        DEAD → COLD              Cleanup complete
    """

    COLD = "cold"
    IDLE = "idle"
    STREAMING = "streaming"
    WAITING_INPUT = "waiting_input"
    DEAD = "dead"


class SessionUnit:
    """One tab's complete subprocess lifecycle.

    Invariants:

    - Only one ``SessionUnit`` per ``session_id``.
    - State transitions are atomic (no intermediate states visible).
    - A crash in this unit never affects other units.
    - ``_env_lock`` is held only during subprocess spawn, released
      after ``wrapper.__aenter__()`` completes.

    Parameters
    ----------
    session_id:
        Stable app-level session ID (from the frontend).
    agent_id:
        Agent configuration ID.
    on_state_change:
        Optional callback invoked after every state transition.
        Signature: ``(session_id, old_state, new_state) -> None``.
        Intended for Radar / observability events.
    """

    def __init__(
        self,
        session_id: str,
        agent_id: str,
        *,
        on_state_change: Optional[
            Callable[[str, SessionState, SessionState], None]
        ] = None,
    ) -> None:
        # ── Public identity ──────────────────────────────────────
        self.session_id: str = session_id
        self.agent_id: str = agent_id
        self.state: SessionState = SessionState.COLD
        self.created_at: float = time.time()
        self.last_used: float = time.time()

        # ── Internal — not part of public interface ──────────────
        self._client: Optional[ClaudeSDKClient] = None
        self._wrapper: Optional[_ClaudeClientWrapper] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._sdk_session_id: Optional[str] = None
        self._interrupted: bool = False
        self._retry_count: int = 0

        # ── Hook tracking ─────────────────────────────────────────
        # True after hooks enqueued for current IDLE period.
        # Reset on every STREAMING transition so next IDLE fires fresh.
        self._hooks_enqueued: bool = False

        # ── Observability callback ───────────────────────────────
        self._on_state_change: Optional[
            Callable[[str, SessionState, SessionState], None]
        ] = on_state_change

    # ── Properties ────────────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        """Subprocess is alive (IDLE, STREAMING, or WAITING_INPUT)."""
        return self.state in (
            SessionState.IDLE,
            SessionState.STREAMING,
            SessionState.WAITING_INPUT,
        )

    @property
    def is_protected(self) -> bool:
        """Cannot be evicted (STREAMING or WAITING_INPUT)."""
        return self.state in (
            SessionState.STREAMING,
            SessionState.WAITING_INPUT,
        )

    @property
    def pid(self) -> Optional[int]:
        """PID of the owned subprocess, if alive.

        Delegates to ``_ClaudeClientWrapper.pid`` which is captured
        immediately after subprocess spawn.
        """
        if self._wrapper is not None:
            return self._wrapper.pid
        return None

    # ── State management ─────────────────────────────────────────

    def _transition(self, new_state: SessionState) -> None:
        """Atomic state transition with structured logging.

        Log format::

            INFO session_unit.transition session_id={id} from={old} to={new} pid={pid}

        If an ``_on_state_change`` callback is registered it is invoked
        **after** the state has been updated, so observers always see
        the new state.
        """
        old_state = self.state
        self.state = new_state

        # Reset hook tracking when entering STREAMING — the next IDLE
        # period is a fresh conversation turn that deserves its own hooks.
        if new_state == SessionState.STREAMING:
            self._hooks_enqueued = False

        logger.info(
            "session_unit.transition session_id=%s from=%s to=%s pid=%s",
            self.session_id,
            old_state.value,
            new_state.value,
            self.pid,
        )
        if self._on_state_change is not None:
            try:
                self._on_state_change(self.session_id, old_state, new_state)
            except Exception:
                # Observability callbacks must never break state transitions.
                logger.exception(
                    "session_unit._on_state_change callback failed "
                    "session_id=%s from=%s to=%s",
                    self.session_id,
                    old_state.value,
                    new_state.value,
                )

    def __repr__(self) -> str:
        return (
            f"SessionUnit(session_id={self.session_id!r}, "
            f"agent_id={self.agent_id!r}, "
            f"state={self.state.value!r}, "
            f"pid={self.pid})"
        )

    # ── Constants ─────────────────────────────────────────────────

    MAX_RETRY_ATTEMPTS: int = 3
    RETRY_BACKOFF_SECONDS: float = 5.0

    # ── Subprocess lifecycle ─────────────────────────────────────

    @staticmethod
    def _build_retry_options(
        original_options: ClaudeAgentOptions,
        resume_session_id: Optional[str],
    ) -> ClaudeAgentOptions:
        """Build options for a retry attempt with ``--resume`` flag.

        Creates a shallow copy of the original options and sets the
        ``resume`` field to the SDK session ID from the failed
        subprocess.  This tells the fresh subprocess to restore
        conversation context from the previous session (Req 10.5).

        If ``resume_session_id`` is None (e.g. the subprocess died
        before the init message), returns the original options
        unchanged — the retry will start a fresh conversation.
        """
        if not resume_session_id:
            return original_options

        from claude_agent_sdk import ClaudeAgentOptions as _Opts

        # ClaudeAgentOptions is a dataclass — use vars() for shallow copy
        kwargs = dict(vars(original_options))
        kwargs["resume"] = resume_session_id
        return _Opts(**kwargs)

    async def send(
        self,
        query_content: Any,
        options: ClaudeAgentOptions,
        app_session_id: Optional[str] = None,
        config: Optional[Any] = None,
    ) -> AsyncIterator[dict]:
        """Send a message.  Spawns subprocess if COLD, reuses if IDLE.

        State transitions:

        - COLD → STREAMING (spawn new subprocess)
        - IDLE → STREAMING (reuse existing subprocess)
        - STREAMING → IDLE on success
        - STREAMING → WAITING_INPUT on permission prompt
        - STREAMING → DEAD on unrecoverable crash

        Yields raw SDK messages wrapped in dicts.  The caller
        (SessionRouter) is responsible for full SSE event formatting.

        Retry logic: up to ``MAX_RETRY_ATTEMPTS`` retries with
        exponential backoff (5s, 10s, 15s) for retriable errors.
        Each retry spawns a fresh subprocess using ``--resume`` to
        restore conversation context.  Retry state is scoped entirely
        to this unit — no global cooldown.

        Parameters
        ----------
        query_content:
            The user message (str for text, list for multimodal).
        options:
            Pre-built ``ClaudeAgentOptions`` from PromptBuilder.
        app_session_id:
            Stable app-level session ID for persistence.
        config:
            ``AppConfigManager`` instance for environment configuration.
            Required when state is COLD (needs subprocess spawn).
        """
        from .session_utils import (
            _build_error_event,
            _is_retriable_error,
            _sanitize_sdk_error,
        )

        if self.state not in (SessionState.COLD, SessionState.IDLE):
            raise RuntimeError(
                f"Cannot send() in state {self.state.value} "
                f"(session_id={self.session_id})"
            )

        # Reset per-send retry counter
        self._retry_count = 0
        self._interrupted = False

        # Spawn if needed (COLD → IDLE under _spawn_lock + _env_lock)
        if self.state == SessionState.COLD:
            try:
                await self._spawn(options, config)
            except Exception as exc:
                error_str = str(exc)
                if _is_retriable_error(error_str):
                    logger.warning(
                        "Retriable error during spawn for session %s: %s",
                        self.session_id, error_str[:120],
                    )
                    # Fall through to retry loop below
                    self._crash_to_cold()
                else:
                    self._crash_to_cold(clear_identity=True)
                    friendly, suggested = _sanitize_sdk_error(error_str)
                    yield _build_error_event(
                        code="SPAWN_FAILED",
                        message=friendly,
                        detail=traceback.format_exc(),
                        suggested_action=suggested,
                    )
                    return

        # IDLE → STREAMING
        self._transition(SessionState.STREAMING)

        try:
            async for event in self._stream_response(query_content):
                yield event
        except Exception as exc:
            error_str = str(exc)
            logger.error(
                "Error during streaming for session %s: %s",
                self.session_id, error_str[:200],
            )

            # ── Retry loop for retriable errors ──────────────────
            if _is_retriable_error(error_str) and self._retry_count < self.MAX_RETRY_ATTEMPTS:
                # Capture the SDK session ID before cleanup so we can
                # pass --resume on the retry subprocess to restore
                # conversation context (Requirement 10.5).
                resume_session_id = self._sdk_session_id

                while (
                    _is_retriable_error(error_str)
                    and self._retry_count < self.MAX_RETRY_ATTEMPTS
                ):
                    self._retry_count += 1
                    backoff = self.RETRY_BACKOFF_SECONDS * self._retry_count
                    logger.info(
                        "Retry %d/%d for session %s after %.1fs backoff "
                        "(resume=%s)",
                        self._retry_count,
                        self.MAX_RETRY_ATTEMPTS,
                        self.session_id,
                        backoff,
                        resume_session_id,
                    )

                    # Clean up dead subprocess
                    self._crash_to_cold()

                    await asyncio.sleep(backoff)

                    # Build retry options with --resume flag to restore
                    # conversation context from the previous subprocess.
                    retry_options = self._build_retry_options(
                        options, resume_session_id,
                    )

                    # Spawn fresh subprocess for retry
                    try:
                        await self._spawn(retry_options, config)
                    except Exception as spawn_exc:
                        error_str = str(spawn_exc)
                        if _is_retriable_error(error_str):
                            logger.warning(
                                "Retry %d spawn failed (retriable): %s",
                                self._retry_count, error_str[:120],
                            )
                            continue  # Try next retry
                        else:
                            # Non-retriable spawn error — give up
                            self._crash_to_cold(clear_identity=True)
                            friendly, suggested = _sanitize_sdk_error(error_str)
                            yield _build_error_event(
                                code="SPAWN_FAILED",
                                message=friendly,
                                detail=traceback.format_exc(),
                                suggested_action=suggested,
                            )
                            return

                    self._transition(SessionState.STREAMING)

                    try:
                        async for event in self._stream_response(query_content):
                            yield event
                        # Success — break out of retry loop
                        return
                    except Exception as retry_exc:
                        error_str = str(retry_exc)
                        logger.warning(
                            "Retry %d failed for session %s: %s",
                            self._retry_count,
                            self.session_id,
                            error_str[:200],
                        )
                        continue  # Try next retry

                # All retries exhausted
                self._crash_to_cold(clear_identity=True)
                yield _build_error_event(
                    code="ALL_RETRIES_EXHAUSTED",
                    message=(
                        "The AI service couldn't start after multiple attempts. "
                        "This is usually temporary."
                    ),
                    suggested_action=(
                        "Your conversation is saved. Wait a moment, "
                        "then send your message again."
                    ),
                )
                return

            # ── Non-retriable error — crash to DEAD ──────────────
            self._crash_to_cold(clear_identity=True)
            friendly, suggested = _sanitize_sdk_error(error_str)
            yield _build_error_event(
                code="CONVERSATION_ERROR",
                message=friendly,
                detail=traceback.format_exc(),
                suggested_action=suggested,
            )

    async def _spawn(self, options: ClaudeAgentOptions, config: Optional[Any] = None) -> None:
        """Spawn a subprocess under ``_spawn_lock`` + ``_env_lock``.

        Acquires the module-level ``_spawn_lock`` first (serializes all
        SessionUnit spawns), then the ``_env_lock`` from
        ``claude_environment.py`` (serializes environment variable
        mutations + subprocess creation).  Both locks are released after
        ``wrapper.__aenter__()`` so the subprocess has inherited its
        own copy of ``os.environ``.

        State: COLD → IDLE (on success).

        Parameters
        ----------
        options:
            Pre-built ``ClaudeAgentOptions``.
        config:
            ``AppConfigManager`` for environment configuration.
            If None, environment configuration is skipped (assumes
            env vars are already set).
        """
        from .claude_environment import (
            _ClaudeClientWrapper,
            _configure_claude_environment,
            _env_lock,
        )

        async with _spawn_lock:
            async with _env_lock:
                if config is not None:
                    _configure_claude_environment(config)
                wrapper = _ClaudeClientWrapper(options=options)
                client = await wrapper.__aenter__()

        self._wrapper = wrapper
        self._client = client
        self.last_used = time.time()

        logger.info(
            "session_unit.spawn session_id=%s pid=%s",
            self.session_id,
            self.pid,
        )

        # COLD → IDLE (subprocess is alive and ready)
        if self.state == SessionState.COLD:
            self._transition(SessionState.IDLE)

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
        """
        if self._client is None:
            raise RuntimeError(
                f"No client available for session {self.session_id}"
            )

        # Send the query
        if isinstance(query_content, list):
            # Multimodal content — wrap in async generator
            async def _multimodal_gen():
                msg = {
                    "type": "user",
                    "message": {"role": "user", "content": query_content},
                    "parent_tool_use_id": None,
                }
                yield msg

            await self._client.query(_multimodal_gen())
        else:
            await self._client.query(query_content)

        logger.info(
            "Query sent for session %s, reading response...",
            self.session_id,
        )

        # Read and format the SDK response stream
        async for event in self._read_formatted_response():
            yield event

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
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ToolUseBlock,
            ToolResultBlock,
        )
        from claude_agent_sdk.types import StreamEvent

        try:
            from core.tool_summarizer import summarize_tool_use, get_tool_category, truncate_tool_result
            _has_tool_summarizer = True
        except ImportError:
            _has_tool_summarizer = False
        async for message in self._client.receive_response():
            # Capture SDK session ID from init message
            if hasattr(message, "session_id") and message.session_id:
                self._sdk_session_id = message.session_id

            # ── SystemMessage: session init metadata ──────────────
            if isinstance(message, SystemMessage):
                if message.subtype == "init":
                    self._sdk_session_id = message.data.get("session_id")
                    yield {
                        "type": "session_start",
                        "sessionId": self.session_id,
                    }
                continue  # Don't forward other system messages

            # ── StreamEvent: token-by-token streaming ─────────────
            if isinstance(message, StreamEvent):
                event_data = message.event
                event_type = event_data.get("type", "")
                if event_type == "content_block_delta":
                    delta = event_data.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
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
                content_blocks = []
                for block in message.content:
                    if isinstance(block, TextBlock):
                        content_blocks.append({"type": "text", "text": block.text})
                    elif isinstance(block, ToolUseBlock):
                        if block.name == "AskUserQuestion":
                            questions = block.input.get("questions", [])
                            yield {
                                "type": "ask_user_question",
                                "toolUseId": block.id,
                                "questions": questions,
                                "sessionId": self.session_id,
                            }
                            self._transition(SessionState.WAITING_INPUT)
                            self.last_used = time.time()
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
                    elif isinstance(block, ToolResultBlock):
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
                if content_blocks:
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

                if is_error or subtype == "error_during_execution":
                    error_text = str(
                        getattr(message, "result", "")
                        or getattr(message, "error", "")
                    )

                    if self._interrupted:
                        self._interrupted = False
                        self._transition(SessionState.IDLE)
                        self.last_used = time.time()
                        return

                    from .session_utils import _is_retriable_error
                    if _is_retriable_error(error_text):
                        raise RuntimeError(f"Retriable SDK error: {error_text}")

                    # Non-retriable error — yield error event
                    from .session_utils import _sanitize_sdk_error, _build_error_event
                    friendly, suggested = _sanitize_sdk_error(error_text)
                    yield _build_error_event(
                        code="SDK_ERROR", message=friendly, suggested_action=suggested,
                    )

                # Yield result event with usage metrics
                usage = getattr(message, "usage", None) or {}
                yield {
                    "type": "result",
                    "session_id": self.session_id,
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

                self._transition(SessionState.IDLE)
                self.last_used = time.time()
                self._retry_count = 0
                return

        # Stream ended without a result message — treat as success
        if self.state == SessionState.STREAMING:
            self._transition(SessionState.IDLE)
            self.last_used = time.time()

    # ── Interactive methods (task 3.3) ─────────────────────────────

    async def interrupt(self, timeout: float = 5.0) -> bool:
        """Interrupt active query. SDK interrupt() with kill fallback.

        State transitions:

        - STREAMING → IDLE (interrupt succeeded, subprocess warm)
        - STREAMING → DEAD → COLD (interrupt timed out, subprocess killed)
        - WAITING_INPUT → IDLE (interrupt succeeded)

        Returns True if subprocess stayed alive (IDLE).

        Parameters
        ----------
        timeout:
            Seconds to wait for SDK ``interrupt()`` before falling back
            to killing the subprocess.
        """
        if self.state not in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            return self.is_alive

        self._interrupted = True

        if self._client is None:
            # No client — just transition to DEAD
            self._transition(SessionState.DEAD)
            self._cleanup_internal()
            self._transition(SessionState.COLD)
            return False

        try:
            await asyncio.wait_for(self._client.interrupt(), timeout=timeout)
            self._transition(SessionState.IDLE)
            self.last_used = time.time()
            logger.info(
                "session_unit.interrupt succeeded session_id=%s pid=%s",
                self.session_id, self.pid,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "session_unit.interrupt timed out after %.1fs, killing "
                "session_id=%s pid=%s",
                timeout, self.session_id, self.pid,
            )
            await self.kill()
            return False
        except Exception as exc:
            logger.warning(
                "session_unit.interrupt failed for session %s: %s",
                self.session_id, exc,
            )
            await self.kill()
            return False

    async def continue_with_answer(
        self, answer: str,
    ) -> AsyncIterator[dict]:
        """Continue after ask_user_question.

        State: WAITING_INPUT → STREAMING → IDLE/WAITING_INPUT.

        Yields raw SDK messages for the router to format.
        """
        if self.state != SessionState.WAITING_INPUT:
            raise RuntimeError(
                f"Cannot continue_with_answer in state {self.state.value} "
                f"(session_id={self.session_id})"
            )
        if self._client is None:
            raise RuntimeError(
                f"No client for continue_with_answer "
                f"(session_id={self.session_id})"
            )

        self._transition(SessionState.STREAMING)

        try:
            async for event in self._stream_response(answer):
                yield event
        except Exception:
            self._transition(SessionState.DEAD)
            self._cleanup_internal()
            self._transition(SessionState.COLD)
            raise

    async def continue_with_permission(
        self, request_id: str, allowed: bool,
    ) -> AsyncIterator[dict]:
        """Continue after cmd_permission_request.

        State: WAITING_INPUT → STREAMING → IDLE/WAITING_INPUT.

        Yields formatted SSE events (same format as send/_stream_response).
        """
        if self.state != SessionState.WAITING_INPUT:
            raise RuntimeError(
                f"Cannot continue_with_permission in state {self.state.value} "
                f"(session_id={self.session_id})"
            )
        if self._client is None:
            raise RuntimeError(
                f"No client for continue_with_permission "
                f"(session_id={self.session_id})"
            )

        # The SDK permission flow: the subprocess is already waiting for
        # the decision (signaled via PermissionManager). We just need to
        # resume reading the response stream with the same formatting as
        # _stream_response.
        self._transition(SessionState.STREAMING)

        try:
            async for event in self._read_formatted_response():
                yield event
        except Exception:
            self._transition(SessionState.DEAD)
            self._cleanup_internal()
            self._transition(SessionState.COLD)
            raise

    async def reclaim_for_mcp_swap(self) -> None:
        """Kill subprocess to prepare for MCP hot-swap.

        Called when the session needs a different set of MCP servers.
        Kills the current subprocess (IDLE → COLD), so the next
        ``send()`` call will spawn a fresh subprocess with the new
        MCP configuration.

        State: IDLE → DEAD → COLD.
        Raises RuntimeError if not in IDLE state.
        """
        if self.state != SessionState.IDLE:
            raise RuntimeError(
                f"Cannot reclaim for MCP swap in state {self.state.value} "
                f"(session_id={self.session_id})"
            )
        await self.kill()

    async def compact(self, instructions: Optional[str] = None) -> dict:
        """Trigger /compact on the subprocess.

        State: IDLE → IDLE (subprocess stays warm).

        Returns dict with success status and message.
        """
        if self.state != SessionState.IDLE:
            return {
                "success": False,
                "message": f"Cannot compact in state {self.state.value}",
            }
        if self._client is None:
            return {
                "success": False,
                "message": "No active subprocess",
            }

        command = "/compact"
        if instructions:
            command = f"/compact {instructions}"

        try:
            await self._client.query(
                prompt=command,
                session_id=self._sdk_session_id or "default",
            )
            async for _msg in self._client.receive_response():
                pass  # Drain response
            self.last_used = time.time()
            return {"success": True, "message": "Session compacted"}
        except Exception as exc:
            logger.error(
                "Compact failed for session %s: %s",
                self.session_id, exc,
            )
            return {"success": False, "message": str(exc)}

    async def health_check(self) -> bool:
        """Check if the subprocess is still alive.

        Returns True if the subprocess PID exists, False otherwise.
        If the subprocess is dead, transitions to DEAD → COLD.
        """
        pid = self.pid
        if pid is None:
            return self.state == SessionState.COLD

        try:
            os.kill(pid, 0)  # Signal 0 = existence check
            return True
        except ProcessLookupError:
            logger.warning(
                "session_unit.health_check: pid %d dead for session %s",
                pid, self.session_id,
            )
            if self.is_alive:
                self._transition(SessionState.DEAD)
                self._cleanup_internal()
                self._transition(SessionState.COLD)
            return False

    async def kill(self) -> None:
        """Force-kill subprocess and clean up.

        State: any → DEAD → COLD.

        Safe to call multiple times or from any state.
        """
        if self.state in (SessionState.COLD, SessionState.DEAD):
            # Already dead or never started — just ensure COLD
            if self.state == SessionState.DEAD:
                self._cleanup_internal()
                self._transition(SessionState.COLD)
            return

        self._transition(SessionState.DEAD)
        await self._force_kill()
        self._cleanup_internal()
        self._transition(SessionState.COLD)

    async def _force_kill(self) -> None:
        """Best-effort force-kill of the owned subprocess."""
        pid = self.pid
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                logger.info(
                    "session_unit.force_kill session_id=%s pid=%d",
                    self.session_id, pid,
                )
            except ProcessLookupError:
                logger.debug(
                    "Process %d already dead for session %s",
                    pid, self.session_id,
                )
            except OSError as exc:
                logger.warning(
                    "Failed to kill pid %d for session %s: %s",
                    pid, self.session_id, exc,
                )

        # Also try graceful wrapper cleanup
        if self._wrapper is not None:
            try:
                await self._wrapper.__aexit__(None, None, None)
            except Exception:
                logger.debug(
                    "Wrapper cleanup error for session %s (expected)",
                    self.session_id,
                )

    def _cleanup_internal(self) -> None:
        """Reset transient subprocess fields after subprocess death.

        Called during DEAD → COLD transition.  Clears client, wrapper,
        and retry state so the unit is ready for reuse.

        Preserves ``_sdk_session_id`` so that evicted units can resume
        via ``--resume`` when the user returns to the tab.
        """
        self._client = None
        self._wrapper = None
        self._interrupted = False
        self._retry_count = 0

    def _full_cleanup(self) -> None:
        """Full cleanup for non-retriable crashes where the session should NOT be resumable.

        Calls ``_cleanup_internal()`` to clear transient subprocess
        fields, then also clears ``_sdk_session_id`` so the next
        conversation starts completely fresh (no ``--resume``).

        Use this instead of ``_cleanup_internal()`` on non-retriable
        error paths (spawn failure, all retries exhausted, streaming
        crash) where resuming the old session would be meaningless.
        """
        self._cleanup_internal()
        self._sdk_session_id = None

    def _crash_to_cold(self, *, clear_identity: bool = False) -> None:
        """Transition DEAD → COLD with appropriate cleanup.

        Consolidates the repeated ``DEAD → cleanup → COLD`` pattern
        used in ``send()`` error paths.

        Args:
            clear_identity: If ``True``, also clears ``_sdk_session_id``
                via ``_full_cleanup()`` (non-retriable crashes).
                If ``False``, uses ``_cleanup_internal()`` which
                preserves ``_sdk_session_id`` for resume (retriable
                errors and eviction).
        """
        self._transition(SessionState.DEAD)
        if clear_identity:
            self._full_cleanup()
        else:
            self._cleanup_internal()
        self._transition(SessionState.COLD)

    def clear_session_identity(self) -> None:
        """Clear ``_sdk_session_id`` so the unit cannot resume.

        Called by ``SessionRouter.disconnect_all()`` after ``kill()``
        to ensure shutdown fully cleans up session identity.
        """
        self._sdk_session_id = None
