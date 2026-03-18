"""SessionUnit — one tab's complete subprocess lifecycle state machine.

Extracted from ``agent_manager.py`` as part of the multi-session
re-architecture (Phase 1).  Each ``SessionUnit`` owns exactly one
Claude CLI subprocess and manages its lifecycle through a 5-state
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
# This is the SessionUnit-local counterpart of _env_lock in
# claude_environment.py — both are used together during spawn to
# guarantee env isolation (Rule 6 from the design doc).
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
        from dataclasses import fields as _dc_fields

        # ClaudeAgentOptions may be a dataclass or a plain class.
        # Use getattr-based copy to be resilient to SDK changes.
        try:
            field_names = [f.name for f in _dc_fields(original_options)]
        except TypeError:
            # Not a dataclass — fall back to __dict__ copy
            field_names = list(vars(original_options).keys())

        kwargs = {name: getattr(original_options, name) for name in field_names}
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
                    self._transition(SessionState.DEAD)
                    self._cleanup_internal()
                    self._transition(SessionState.COLD)
                else:
                    self._transition(SessionState.DEAD)
                    self._cleanup_internal()
                    self._transition(SessionState.COLD)
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
                    self._transition(SessionState.DEAD)
                    self._cleanup_internal()
                    self._transition(SessionState.COLD)

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
                            self._transition(SessionState.DEAD)
                            self._cleanup_internal()
                            self._transition(SessionState.COLD)
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
                self._transition(SessionState.DEAD)
                self._cleanup_internal()
                self._transition(SessionState.COLD)
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
            self._transition(SessionState.DEAD)
            self._cleanup_internal()
            self._transition(SessionState.COLD)
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
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            ToolUseBlock,
        )

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

        # Read SDK response stream
        async for message in self._client.receive_response():
            # Capture SDK session ID from init message
            if hasattr(message, "session_id") and message.session_id:
                self._sdk_session_id = message.session_id

            # Yield raw message for the router to format
            yield {"source": "sdk", "message": message}

            # ── Detect interactive prompts (STREAMING → WAITING_INPUT) ──
            # AskUserQuestion is a ToolUseBlock with name="AskUserQuestion"
            # inside an AssistantMessage.  When detected, transition to
            # WAITING_INPUT and return — the caller (continue_with_answer)
            # will resume the stream later.
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if (
                        isinstance(block, ToolUseBlock)
                        and block.name == "AskUserQuestion"
                    ):
                        logger.info(
                            "AskUserQuestion detected for session %s, "
                            "transitioning to WAITING_INPUT",
                            self.session_id,
                        )
                        self._transition(SessionState.WAITING_INPUT)
                        self.last_used = time.time()
                        return

            # ── ResultMessage — response complete or error ──────────
            if isinstance(message, ResultMessage):
                is_error = getattr(message, "is_error", False)
                subtype = getattr(message, "subtype", None)

                if is_error or subtype == "error_during_execution":
                    # Extract error text from result or error attribute
                    error_text = str(
                        getattr(message, "result", "")
                        or getattr(message, "error", "")
                    )

                    # User-initiated interrupt — not a real error
                    if self._interrupted:
                        logger.info(
                            "ResultMessage error after interrupt for "
                            "session %s, treating as user stop",
                            self.session_id,
                        )
                        self._interrupted = False
                        self._transition(SessionState.IDLE)
                        self.last_used = time.time()
                        return

                    # Check if retriable — raise so send() retry loop
                    # handles it with exponential backoff
                    from .session_utils import _is_retriable_error

                    if _is_retriable_error(error_text):
                        raise RuntimeError(
                            f"Retriable SDK error: {error_text}"
                        )
                    # Non-retriable error result — still transition to
                    # IDLE (the subprocess is alive, just the query failed)

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

        Yields raw SDK messages for the router to format.
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

        # The SDK permission flow uses a different mechanism than query —
        # the permission decision is signaled via PermissionManager, not
        # via client.query(). For now, transition state and let the
        # existing streaming loop in _stream_response handle it.
        self._transition(SessionState.STREAMING)

        # The permission response is sent via the existing permission
        # manager signaling mechanism. The subprocess is already waiting
        # for the decision. We just need to resume reading the response.
        try:
            async for message in self._client.receive_response():
                if hasattr(message, "session_id") and message.session_id:
                    self._sdk_session_id = message.session_id
                yield {"source": "sdk", "message": message}

                from claude_agent_sdk import ResultMessage
                if isinstance(message, ResultMessage):
                    self._transition(SessionState.IDLE)
                    self.last_used = time.time()
                    return
        except Exception:
            self._transition(SessionState.DEAD)
            self._cleanup_internal()
            self._transition(SessionState.COLD)
            raise

        if self.state == SessionState.STREAMING:
            self._transition(SessionState.IDLE)
            self.last_used = time.time()

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
        """Reset internal fields after subprocess death.

        Called during DEAD → COLD transition.  Clears client, wrapper,
        and SDK session references so the unit is ready for reuse.
        """
        self._client = None
        self._wrapper = None
        self._sdk_session_id = None
        self._interrupted = False
        self._retry_count = 0
