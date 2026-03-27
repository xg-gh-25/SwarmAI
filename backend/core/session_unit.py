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
import subprocess
import time
import traceback
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional

from .compaction_guard import EscalationLevel

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


def _kill_child_pids(parent_pid: int) -> int:
    """SIGKILL all direct children of *parent_pid*. Returns count killed.

    Uses ``pgrep -P`` to enumerate children.  Best-effort — failures
    are silently swallowed since the caller retries with a second pass.
    """
    killed = 0
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.strip().split("\n"):
            child_pid_str = line.strip()
            if not child_pid_str:
                continue
            try:
                child_pid = int(child_pid_str)
                os.kill(child_pid, signal.SIGKILL)
                killed += 1
            except (ValueError, ProcessLookupError, PermissionError):
                pass
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return killed


# ---------------------------------------------------------------------------
# OOM / SIGKILL detection
# ---------------------------------------------------------------------------

# Patterns that indicate the subprocess was killed by the OS (jetsam / OOM-killer).
# Multiple patterns guard against SDK error message format changes — if any
# single pattern matches, we treat it as OOM.  The spawn-budget fallback
# (checked separately) catches cases where ALL patterns miss.
_OOM_PATTERNS = [
    "exit code -9",          # SDK format variant 1
    "exit code: -9",         # SDK format variant 2
    "exit code=-9",          # Defensive: possible future format
    "sigkill",               # Generic SIGKILL mention
    "signal 9",              # Numeric signal reference
    "killed by signal",      # Linux OOM-killer phrasing
    "jetsam",                # macOS memory pressure killer
    "terminated process",    # "Cannot write to terminated process"
]


def _is_oom_signal(error_str: str) -> bool:
    """Detect whether an error indicates an OOM / SIGKILL subprocess death.

    Uses a multi-pattern approach so we don't silently regress if the
    Claude SDK changes its error message format.  Also checks the
    spawn budget as a heuristic fallback — if the system is currently
    under memory pressure AND the process died, it's very likely OOM
    even if the error message doesn't match any known pattern.

    Returns True if OOM is likely, False otherwise.
    """
    error_lower = error_str.lower()

    # Primary: explicit pattern match
    for pattern in _OOM_PATTERNS:
        if pattern in error_lower:
            return True

    # Fallback heuristic: process died + system is under memory pressure.
    # This catches the case where the SDK changes its error format but
    # the system is clearly memory-constrained.
    try:
        from .resource_monitor import resource_monitor
        mem = resource_monitor.system_memory()
        if mem.pressure_level == "critical":
            logger.info(
                "OOM heuristic: no pattern match but memory pressure is "
                "critical (%.1f%%) — treating as OOM",
                mem.percent_used,
            )
            return True
    except Exception:
        pass  # Resource monitor unavailable — rely on patterns only

    return False


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
        # True when this unit serves channel conversations (Slack, Feishu, etc.)
        # Channel units use a dedicated slot pool, separate from chat tabs.
        self.is_channel_session: bool = False

        # ── Internal — not part of public interface ──────────────
        self._client: Optional[ClaudeSDKClient] = None
        self._wrapper: Optional[_ClaudeClientWrapper] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._sdk_session_id: Optional[str] = None
        self._interrupted: bool = False
        self._retry_count: int = 0
        self._model_name: Optional[str] = None

        # ── Hook tracking ─────────────────────────────────────────
        # True after hooks enqueued for current IDLE period.
        # Reset on every STREAMING transition so next IDLE fires fresh.
        self._hooks_enqueued: bool = False

        # ── Memory watermark ──────────────────────────────────────
        # Peak tree RSS (CLI + all MCP children) observed during lifetime.
        # Updated every maintenance cycle by LifecycleManager.
        # Logged on session kill/evict for post-mortem analysis.
        self._peak_tree_rss_bytes: int = 0

        # ── Lifecycle response counter ─────────────────────────────────
        # Counts ResultMessages received since this SessionUnit was created
        # (i.e., since app launch for restored tabs).  Used to detect
        # "first response after resume" — context warnings are adjusted
        # to explain accumulated tokens come from a previous conversation.
        self._lifecycle_response_count: int = 0

        # ── Buffer overflow recovery ────────────────────────────────
        # Set True when a tool response exceeds the CLI's 10MB JSONRPC
        # buffer.  On the next send, a recovery instruction is prepended
        # to the user message telling the agent to use progressive
        # processing (fetch items one-at-a-time).  Max 1 recovery per
        # message — if the second attempt also overflows, surface error.
        self._buffer_overflow_recovery: bool = False

        # ── Streaming timeout ────────────────────────────────────────
        # Updated on every yielded event during STREAMING.  The
        # LifecycleManager checks this to detect stuck streams that
        # never produced a ResultMessage (e.g. SDK hang, Bedrock timeout).
        self._last_event_time: Optional[float] = None
        self._streaming_start_time: Optional[float] = None

        # ── Compaction loop guard (3-layer anti-loop) ──────────────
        from .compaction_guard import CompactionGuard
        self._compaction_guard: CompactionGuard = CompactionGuard()

        # ── Hook session context ──────────────────────────────────────
        # Mutable dict shared with hook closures (dangerous_command_gate,
        # pre_compact_hook).  Hooks capture this dict BY REFERENCE, so
        # updating it in-place before each send() ensures hooks always
        # use the current session_id — even when the subprocess is reused
        # across multiple run_conversation() calls.
        self._hook_session_context: Optional[dict] = None

        # ── Zombie detection — set True when meaningful content emitted ──
        self._content_emitted: bool = False

        # ── Resource observability ─────────────────────────────────
        self._last_error_type: Optional[str] = None  # FailureType.value: "oom" | "rate_limit" | "api_error" | "timeout" | "unknown"
        self._last_metrics: Optional[Any] = None      # ProcessMetrics from health_check

        # ── Observability callback ───────────────────────────────
        self._on_state_change: Optional[
            Callable[[str, SessionState, SessionState], None]
        ] = on_state_change

        # ── SSE stop notification ─────────────────────────────────
        self._stop_event: asyncio.Event = asyncio.Event()

        # ── Send generation counter (stale-interrupt guard) ────────
        # Monotonically incremented at the start of each send().
        # interrupt() captures this at entry and skips state
        # transitions / kills if the generation has advanced — meaning
        # a new send() started while the old interrupt was in-flight.
        self._send_generation: int = 0

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

    @property
    def stop_event(self) -> asyncio.Event:
        """Per-session event signaling SSE consumers to stop."""
        return self._stop_event

    # ── State management ─────────────────────────────────────────

    # Valid state transitions.  Any transition not listed here is a bug.
    _VALID_TRANSITIONS: dict[SessionState, set[SessionState]] = {
        SessionState.COLD: {SessionState.IDLE, SessionState.DEAD},
        SessionState.IDLE: {SessionState.STREAMING, SessionState.COLD, SessionState.DEAD},
        SessionState.STREAMING: {SessionState.IDLE, SessionState.WAITING_INPUT, SessionState.COLD, SessionState.DEAD},
        SessionState.WAITING_INPUT: {SessionState.STREAMING, SessionState.IDLE, SessionState.COLD, SessionState.DEAD},
        SessionState.DEAD: {SessionState.COLD},  # resurrection after cleanup
    }

    def _transition(self, new_state: SessionState) -> None:
        """Atomic state transition with validation and structured logging.

        Raises ``RuntimeError`` if the transition is not in
        ``_VALID_TRANSITIONS`` — this catches bugs like COLD→STREAMING
        (which skips spawn) at the source rather than surfacing as
        a mysterious "No client available" downstream.

        Log format::

            INFO session_unit.transition session_id={id} from={old} to={new} pid={pid}

        If an ``_on_state_change`` callback is registered it is invoked
        **after** the state has been updated, so observers always see
        the new state.
        """
        old_state = self.state

        # Validate transition
        valid = self._VALID_TRANSITIONS.get(old_state, set())
        if new_state not in valid:
            raise RuntimeError(
                f"Invalid state transition {old_state.value}→{new_state.value} "
                f"for session {self.session_id}. "
                f"Valid from {old_state.value}: {sorted(s.value for s in valid)}"
            )

        self.state = new_state

        # Reset hook tracking when entering STREAMING — the next IDLE
        # period is a fresh conversation turn that deserves its own hooks.
        if new_state == SessionState.STREAMING:
            self._hooks_enqueued = False
            self._streaming_start_time = time.time()
            self._last_event_time = time.time()

        # Clear streaming timestamps when leaving STREAMING
        if old_state == SessionState.STREAMING and new_state != SessionState.STREAMING:
            self._streaming_start_time = None
            self._last_event_time = None

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
    STREAMING_TIMEOUT_SECONDS: float = 300.0  # 5 min with no SDK events → stuck

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

        # ── Layer 0: Advance generation + clear stale interrupt state ─
        # Must happen BEFORE anything else so a concurrent interrupt()
        # that resumes from an await sees the bumped counter and aborts.
        # All three clears are synchronous (no await between them), so
        # no other coroutine can interleave and re-set them.
        self._send_generation += 1
        self._stop_event.clear()
        self._interrupted = False

        # If a previous request got stuck (SDK never sent ResultMessage),
        # the unit stays in STREAMING forever.  Instead of rejecting the
        # new message with an error, force-recover to COLD and proceed.
        # The user never sees an error — just a slightly longer response.
        if self.state == SessionState.STREAMING:
            stall = self.streaming_stall_seconds
            logger.warning(
                "session_unit.auto_recover_stuck session_id=%s state=%s "
                "stall=%.0fs — forcing COLD before retry",
                self.session_id, self.state.value,
                stall or 0,
            )
            await self.force_unstick_streaming()
            # After force_unstick, state is COLD — fall through to spawn

        if self.state == SessionState.WAITING_INPUT:
            raise RuntimeError(
                f"Cannot send() in state {self.state.value} — "
                f"a permission prompt is pending "
                f"(session_id={self.session_id})"
            )

        if self.state not in (SessionState.COLD, SessionState.IDLE):
            raise RuntimeError(
                f"Cannot send() in state {self.state.value} "
                f"(session_id={self.session_id})"
            )

        # Reset per-send state (retry counter + buffer overflow flag).
        # _buffer_overflow_recovery is per-message, not per-session:
        # each new user message should get a fresh recovery attempt
        # if it triggers a different buffer overflow.
        self._retry_count = 0
        # _interrupted already cleared in Layer 0 above
        self._buffer_overflow_recovery = False
        self._compaction_guard.reset()  # New user turn — reset tool tracking
        self._content_emitted = False   # Track if meaningful content is emitted

        # Spawn if needed (COLD → IDLE under _spawn_lock + _env_lock)
        if self.state == SessionState.COLD:
            async for event in self._ensure_spawned(options, config):
                if event.get("_abort"):
                    return  # spawn failed after retries
                yield event

        # IDLE → STREAMING
        self._transition(SessionState.STREAMING)
        self._model_name = getattr(options, "model", None)

        try:
            async for event in self._stream_response(query_content):
                yield event
        except Exception as exc:
            error_str = str(exc)
            tb_str = traceback.format_exc()
            logger.error(
                "Error during streaming for session %s: %s",
                self.session_id, error_str[:200],
            )

            # ── Buffer overflow — recoverable via progressive processing ──
            if "maximum buffer size" in error_str and not self._buffer_overflow_recovery:
                recovered = False
                async for event in self._handle_buffer_overflow(
                    query_content, options, config, error_str,
                ):
                    if event.get("_abort"):
                        return  # spawn failed during recovery
                    if event.get("_recovered"):
                        recovered = True
                        continue
                    if "_fallthrough_error" in event:
                        # Recovery stream raised — update error context so
                        # the retry check below uses the recovery exception.
                        error_str = event["_fallthrough_error"]
                        tb_str = event.get("_fallthrough_tb", tb_str)
                        continue
                    yield event
                if recovered:
                    return
                # Recovery stream failed — error_str updated via
                # _fallthrough_error sentinel from _handle_buffer_overflow.
                # Fall through to retry/error handling below.

            # ── Retry loop for retriable errors ──────────────────
            if _is_retriable_error(error_str) and self._retry_count < self.MAX_RETRY_ATTEMPTS:
                async for event in self._retry_with_resume(
                    query_content, options, config, error_str, tb_str,
                ):
                    if event.get("_abort"):
                        return  # retries exhausted or resource denied
                    yield event
                return

            # ── Non-retriable error — crash to DEAD ──────────────
            await self._crash_to_cold_async(clear_identity=True)
            friendly, suggested = _sanitize_sdk_error(error_str)
            yield _build_error_event(
                code="CONVERSATION_ERROR",
                message=friendly,
                detail=tb_str,
                suggested_action=suggested,
            )

    # ── Extracted helpers from send() ───────────────────────────────
    # These are async generators (yield events) called via
    # ``async for event in self._method(): yield event`` in send().
    # They share the same instance state — no new concurrency patterns.
    # Sentinel keys (_abort, _recovered) are internal flow-control
    # signals consumed by send() and never yielded to callers.

    async def _ensure_spawned(
        self,
        options: ClaudeAgentOptions,
        config: Optional[Any],
    ) -> AsyncIterator[dict]:
        """Spawn subprocess if COLD, with retry loop on retriable errors.

        Yields status events during retries.  If all retries fail, yields
        a terminal error event with ``_abort: True`` so the caller can
        ``return`` without yielding it to the SSE stream.

        State on success: IDLE (spawned and ready).
        State on failure: COLD (all retries exhausted).
        """
        from .session_utils import (
            _build_error_event,
            _is_retriable_error,
            _sanitize_sdk_error,
            classify_failure,
            compute_backoff,
        )

        try:
            await self._spawn(options, config)
            return  # success — state is IDLE
        except Exception as exc:
            error_str = str(exc)

        if _is_retriable_error(error_str):
            logger.warning(
                "Retriable error during spawn for session %s, "
                "will retry (attempt %d/%d): %s",
                self.session_id,
                self._retry_count + 1,
                self.MAX_RETRY_ATTEMPTS,
                error_str[:120],
            )
            await self._crash_to_cold_async()
            while (
                _is_retriable_error(error_str)
                and self._retry_count < self.MAX_RETRY_ATTEMPTS
            ):
                self._retry_count += 1
                failure_type, failure_meta = classify_failure(
                    error_str, self._hook_session_context,
                )
                # Spawn retries use 15s base (heavier than stream retries)
                # because each spawn starts a full CLI process.
                backoff = compute_backoff(
                    failure_type, failure_meta,
                    self._retry_count, base_backoff=15.0,
                )
                logger.info(
                    "session_unit.spawn_retry session_id=%s "
                    "attempt=%d/%d backoff=%.1fs failure=%s",
                    self.session_id, self._retry_count,
                    self.MAX_RETRY_ATTEMPTS, backoff, failure_type.value,
                )
                yield {
                    "type": "status",
                    "message": f"Reconnecting (attempt {self._retry_count}/{self.MAX_RETRY_ATTEMPTS})...",
                    "code": "RETRY_SPAWN",
                }
                await asyncio.sleep(backoff)
                try:
                    await self._spawn(options, config)
                    return  # success — state is IDLE
                except Exception as retry_exc:
                    error_str = str(retry_exc)
                    await self._crash_to_cold_async()

            # All retries exhausted
            friendly, suggested = _sanitize_sdk_error(error_str)
            yield _build_error_event(
                code="SPAWN_FAILED",
                message=friendly,
                detail=error_str,
                suggested_action=suggested,
            )
            yield {"_abort": True}
        else:
            # Non-retriable spawn error
            await self._crash_to_cold_async(clear_identity=True)
            friendly, suggested = _sanitize_sdk_error(error_str)
            yield _build_error_event(
                code="SPAWN_FAILED",
                message=friendly,
                detail=traceback.format_exc(),
                suggested_action=suggested,
            )
            yield {"_abort": True}

    async def _handle_buffer_overflow(
        self,
        query_content: Any,
        options: ClaudeAgentOptions,
        config: Optional[Any],
        error_str: str,
    ) -> AsyncIterator[dict]:
        """Recover from CLI 10MB JSONRPC buffer overflow.

        Respawns with ``--resume`` and injects a progressive-processing
        instruction so the agent fetches items one-at-a-time.

        Yields stream events on success, or an error event + ``_abort``
        sentinel on spawn failure.  Yields ``_recovered: True`` as final
        event on success so the caller knows to return.

        Does NOT increment ``_retry_count`` — buffer overflow is strategy
        correction, not a transient failure.
        """
        from .session_utils import (
            _build_error_event,
            _sanitize_sdk_error,
        )

        logger.warning(
            "session_unit.buffer_overflow session_id=%s — "
            "will inject progressive processing recovery",
            self.session_id,
        )
        self._buffer_overflow_recovery = True
        resume_sid = self._sdk_session_id
        await self._crash_to_cold_async()
        await asyncio.sleep(2.0)  # brief cooldown

        retry_options = self._build_retry_options(options, resume_sid)
        try:
            await self._spawn(retry_options, config)
        except Exception as spawn_exc:
            # Capture traceback immediately — awaits in async generators
            # can clear sys.exc_info() before format_exc() runs.
            spawn_tb = traceback.format_exc()
            await self._crash_to_cold_async(clear_identity=True)
            friendly, suggested = _sanitize_sdk_error(str(spawn_exc))
            yield _build_error_event(
                code="SPAWN_FAILED",
                message=friendly,
                detail=spawn_tb,
                suggested_action=suggested,
            )
            yield {"_abort": True}
            return

        self._transition(SessionState.STREAMING)

        # Build recovered query with progressive-processing instruction
        recovery_prefix = (
            "[System: Your previous tool call returned a response "
            "exceeding the 10MB buffer limit. Use progressive "
            "processing for this task:\n"
            "- Fetch items ONE at a time (never batch multiple "
            "files/images in a single tool call)\n"
            "- After each fetch, extract key findings as compact text\n"
            "- After all items processed, synthesize your findings\n"
            "- For large text files, use offset/limit to read in "
            "chunks of 500 lines\n"
            "- If you already processed some items before the error, "
            "continue where you left off — do not re-fetch items "
            "you already analyzed\n"
            "Do not attempt to fetch all items in a single tool "
            "call again.]\n\n"
        )
        if isinstance(query_content, str):
            recovered_query = recovery_prefix + query_content
        elif isinstance(query_content, list):
            recovered_query = [
                {"type": "text", "text": recovery_prefix},
                *query_content,
            ]
        else:
            recovered_query = recovery_prefix + str(query_content)

        try:
            async for event in self._stream_response(recovered_query):
                yield event
            yield {"_recovered": True}
        except Exception as recovery_exc:
            # Recovery failed — propagate the NEW exception details back
            # to send() so the retry check uses the recovery error, not
            # the original "maximum buffer size" string.
            logger.warning(
                "Buffer overflow recovery failed for session %s: %s",
                self.session_id, str(recovery_exc)[:200],
            )
            yield {
                "_fallthrough_error": str(recovery_exc),
                "_fallthrough_tb": traceback.format_exc(),
            }

    async def _retry_with_resume(
        self,
        query_content: Any,
        options: ClaudeAgentOptions,
        config: Optional[Any],
        initial_error_str: str,
        initial_tb_str: str,
    ) -> AsyncIterator[dict]:
        """Retry loop with failure-aware backoff and ``--resume``.

        Handles failure-type-aware backoff (OOM → 30s flat, rate limit →
        wait for reset, else → exponential), spawn budget re-check after
        backoff, and ``--resume`` flag for conversation context restoration.

        Yields stream events on success.  Yields error event + ``_abort``
        sentinel when all retries are exhausted or resources denied.

        On success, the generator returns normally (caller should also
        return to exit ``send()``).  The ``_retry_count`` is managed
        here and reset to 0 in ``_read_formatted_response`` on success.
        """
        from .session_utils import (
            FailureType,
            _build_error_event,
            _is_retriable_error,
            _sanitize_sdk_error,
            classify_failure,
            compute_backoff,
        )

        error_str = initial_error_str
        # Capture SDK session ID before cleanup for --resume
        resume_session_id = self._sdk_session_id
        _consecutive_timeouts = 0

        while (
            _is_retriable_error(error_str)
            and self._retry_count < self.MAX_RETRY_ATTEMPTS
        ):
            self._retry_count += 1

            # ── Structured failure classification ─────────────
            # Hook-captured context (rate limits, notifications)
            # takes priority over string pattern matching.
            failure_type, failure_meta = classify_failure(
                error_str, self._hook_session_context,
            )
            self._last_error_type = failure_type.value

            # Track consecutive timeouts to abandon --resume
            if failure_type == FailureType.TIMEOUT:
                _consecutive_timeouts += 1
            else:
                _consecutive_timeouts = 0

            # After 2 consecutive timeouts with --resume, the resume target
            # is likely broken (e.g., OOM'd predecessor, corrupted session).
            # Abandon resume and start fresh to avoid sitting in a 10-minute
            # retry loop watching nothing happen.
            if _consecutive_timeouts >= 2 and resume_session_id:
                logger.warning(
                    "session_unit: %d consecutive timeouts with --resume, "
                    "abandoning resume for session %s",
                    _consecutive_timeouts, self.session_id,
                )
                resume_session_id = None

            # Failure-type-aware backoff:
            # OOM → 30s flat, Rate limit → wait for reset, else → exponential
            backoff = compute_backoff(
                failure_type, failure_meta,
                self._retry_count, self.RETRY_BACKOFF_SECONDS,
            )

            logger.info(
                "Retry %d/%d for session %s after %.1fs backoff "
                "(resume=%s, failure=%s, meta=%s)",
                self._retry_count,
                self.MAX_RETRY_ATTEMPTS,
                self.session_id,
                backoff,
                resume_session_id,
                failure_type.value,
                {k: v for k, v in failure_meta.items() if k != "message"},
            )

            await self._crash_to_cold_async()

            # Clear hook failure context after reading — prevents stale
            # context from a previous failure leaking into the next retry.
            if self._hook_session_context:
                self._hook_session_context.pop("_last_notification", None)
                self._hook_session_context.pop("_stop_info", None)

            await asyncio.sleep(backoff)

            # Re-check spawn budget after backoff
            try:
                from .resource_monitor import resource_monitor
                budget = resource_monitor.spawn_budget()
                if not budget.can_spawn:
                    logger.warning(
                        "Retry %d aborted: spawn budget denied "
                        "post-backoff session_id=%s reason=%s",
                        self._retry_count, self.session_id,
                        budget.reason,
                    )
                    await self._crash_to_cold_async(clear_identity=True)
                    yield _build_error_event(
                        code="RESOURCE_EXHAUSTED",
                        message=(
                            "Not enough memory to restart the AI service. "
                            "Close unused tabs or apps to free memory."
                        ),
                        suggested_action=(
                            "Close idle chat tabs to free memory, "
                            "then send your message again."
                        ),
                    )
                    yield {"_abort": True}
                    return
            except Exception:
                pass  # Budget check failed — proceed with retry

            retry_options = self._build_retry_options(
                options, resume_session_id,
            )

            try:
                await self._spawn(retry_options, config)
            except Exception as spawn_exc:
                # Capture traceback immediately — awaits in async generators
                # can clear sys.exc_info() before format_exc() runs.
                spawn_tb = traceback.format_exc()
                error_str = str(spawn_exc)
                if _is_retriable_error(error_str):
                    logger.warning(
                        "Retry %d spawn failed (retriable): %s",
                        self._retry_count, error_str[:120],
                    )
                    continue
                else:
                    await self._crash_to_cold_async(clear_identity=True)
                    friendly, suggested = _sanitize_sdk_error(error_str)
                    yield _build_error_event(
                        code="SPAWN_FAILED",
                        message=friendly,
                        detail=spawn_tb,
                        suggested_action=suggested,
                    )
                    yield {"_abort": True}
                    return

            self._transition(SessionState.STREAMING)

            try:
                async for event in self._stream_response(query_content):
                    yield event
                return  # success
            except Exception as retry_exc:
                error_str = str(retry_exc)
                logger.warning(
                    "Retry %d failed for session %s: %s",
                    self._retry_count,
                    self.session_id,
                    error_str[:200],
                )
                continue

        # All retries exhausted
        await self._crash_to_cold_async(clear_identity=True)
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
        yield {"_abort": True}

    def _emit_post_stream_metadata(
        self, usage: dict, *, num_turns: int = 1,
    ) -> list[dict]:
        """Build context-warning and TSCC metadata events after a result.

        Returns a list of events (0–2 items) rather than yielding, so
        the caller can iterate with a simple ``for`` loop.  Never raises
        — failures are silently swallowed since metadata must never block
        the response stream.

        Args:
            usage: Aggregated usage dict from ``ResultMessage.usage``.
            num_turns: Number of agentic turns (API calls) in this response.
                The SDK aggregates input tokens across ALL turns, but the
                context window is only as full as the LAST turn's input.
                We divide by ``num_turns`` to estimate per-call context.
        """
        events: list[dict] = []

        # Context usage warning (ok/warn/critical)
        # IMPORTANT: SDK usage is AGGREGATED across all agentic turns.
        # If the agent makes N tool calls, the SDK sums input_tokens from
        # all N API requests.  But the context window capacity is per-call,
        # not cumulative.  Divide by num_turns for the correct estimate.
        if usage:
            from .prompt_builder import PromptBuilder
            total = PromptBuilder.sum_usage_input_tokens(usage)
            turns = max(num_turns, 1)
            input_tokens = (total // turns) if total > 0 else None
        else:
            input_tokens = None
        logger.info(
            "session_unit.context_ring_debug session_id=%s "
            "usage_keys=%s raw_total=%s per_turn_est=%s "
            "num_turns=%d model=%s",
            self.session_id,
            list(usage.keys()) if usage else "NO_USAGE",
            PromptBuilder.sum_usage_input_tokens(usage) if usage else 0,
            input_tokens,
            num_turns,
            self._model_name,
        )
        if input_tokens and input_tokens > 0:
            try:
                from .prompt_builder import PromptBuilder
                # On the first response of a resumed session, the SDK reports
                # ALL accumulated tokens from the previous conversation.
                # Pass this context so the warning message explains the source.
                is_resumed_first = (
                    self._lifecycle_response_count <= 1
                    and self._sdk_session_id is not None
                )
                warning_evt = PromptBuilder.build_context_warning(
                    input_tokens, self._model_name,
                    is_resumed_first=is_resumed_first,
                )
                logger.info(
                    "session_unit.context_warning_built session_id=%s "
                    "pct=%s level=%s",
                    self.session_id,
                    warning_evt.get("pct") if warning_evt else "NONE",
                    warning_evt.get("level") if warning_evt else "NONE",
                )
                if warning_evt:
                    events.append(warning_evt)
            except Exception as exc:
                logger.warning(
                    "session_unit.context_warning_failed session_id=%s: %s",
                    self.session_id, exc,
                )

            # Feed context usage to compaction guard
            try:
                self._compaction_guard.update_context_usage(
                    input_tokens, self._model_name
                )
                level = self._compaction_guard.check()
                if level != EscalationLevel.MONITORING:
                    guard_evt = self._compaction_guard.build_guard_event(level)
                    if guard_evt:
                        events.append(guard_evt)
            except Exception:
                pass  # Never block on guard failure

        # System prompt metadata for TSCC popover
        try:
            from . import session_registry
            spm = session_registry.system_prompt_metadata.get(
                self.session_id
            )
            if spm:
                events.append({"type": "system_prompt_metadata", **spm})
        except Exception:
            pass  # Never block on metadata failure

        return events

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

        # Pre-spawn memory gate — check BEFORE acquiring locks
        # to avoid holding locks while waiting or failing.
        from .resource_monitor import resource_monitor
        budget = resource_monitor.spawn_budget()
        if not budget.can_spawn:
            from .exceptions import ResourceExhaustedException
            logger.warning(
                "session_unit.spawn BLOCKED session_id=%s reason=%s",
                self.session_id, budget.reason,
            )
            raise ResourceExhaustedException(
                message=budget.reason,
                detail=(
                    f"available={budget.available_mb:.0f}MB, "
                    f"cost={budget.estimated_cost_mb:.0f}MB, "
                    f"headroom={budget.headroom_mb:.0f}MB"
                ),
            )

        # ── Sanitize: strip null bytes from system prompt & model ────
        # Null bytes (\x00) are invalid in POSIX process arguments and
        # environment variables.  If any creep into the system prompt
        # (e.g. from binary files read during context assembly, corrupt
        # DB entries, or __pycache__ .pyc files in .claude/skills/),
        # subprocess.Popen raises "embedded null byte" at spawn time.
        # Defense-in-depth: strip them here regardless of source.
        if options.system_prompt and "\x00" in options.system_prompt:
            logger.warning(
                "session_unit.spawn: stripping %d null bytes from system_prompt "
                "(session_id=%s)",
                options.system_prompt.count("\x00"), self.session_id,
            )
            options.system_prompt = options.system_prompt.replace("\x00", "")

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
        self._compaction_guard.reset_all()  # Fresh subprocess — full reset
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
        MESSAGE_TIMEOUT = self.STREAMING_TIMEOUT_SECONDS  # 5 min between messages

        is_resume = self._sdk_session_id is not None
        is_first_message = True
        saw_assistant_message = False  # Track if LLM actually responded

        # ── Permission queue watcher ──────────────────────────────
        # The dangerous_command_gate hook blocks inside PreToolUse
        # awaiting a user decision.  While it blocks, the SDK cannot
        # produce new messages.  We race the SDK iterator against the
        # PermissionManager session queue so we can surface the
        # cmd_permission_request to the frontend via SSE.
        from core.permission_manager import permission_manager as _pm
        perm_queue = _pm.get_session_queue(self.session_id)

        response_iter = self._client.receive_response().__aiter__()
        _STREAM_EXHAUSTED = object()  # Sentinel: iterator is done

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
                    self.session_id,
                    perm_request.get("requestId", "?"),
                    str(perm_request.get("toolInput", {}).get("command", ""))[:60],
                )
                yield {
                    "type": "cmd_permission_request",
                    "requestId": perm_request["requestId"],
                    "sessionId": perm_request.get("sessionId", self.session_id),
                    "toolName": perm_request.get("toolName", "Bash"),
                    "toolInput": perm_request.get("toolInput", {}),
                    "reason": perm_request.get("reason", ""),
                    "options": perm_request.get("options", ["approve", "deny"]),
                }
                self._transition(SessionState.WAITING_INPUT)
                self.last_used = time.time()
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
                    phase, self.session_id, current_timeout, is_resume,
                )
                raise RuntimeError(
                    f"Streaming timeout ({phase}): no SDK response for "
                    f"{current_timeout:.0f}s (session_id={self.session_id}, "
                    f"resume={is_resume})"
                )

            # ── Heartbeat: track liveness for diagnostics ──────────
            self._last_event_time = time.time()

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
                        self._content_emitted = True
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
                        content_blocks.append({"type": "thinking", "thinking": block.thinking})
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
                        # ── Record tool call for compaction guard ──
                        self._compaction_guard.record_tool_call(
                            block.name, block.input
                        )
                        level = self._compaction_guard.check()
                        if level != EscalationLevel.MONITORING:
                            guard_event = self._compaction_guard.build_guard_event(level)
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
                                    self.session_id, level.value,
                                )
                                await self.interrupt()
                                return
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
                    self._content_emitted = True
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
                self._lifecycle_response_count += 1
                usage = getattr(message, "usage", None) or {}
                logger.info(
                    "session_unit.result_usage session_id=%s "
                    "raw_usage=%s input_tokens=%s model=%s "
                    "lifecycle_response=%d",
                    self.session_id,
                    usage,
                    usage.get("input_tokens") if usage else None,
                    self._model_name,
                    self._lifecycle_response_count,
                )
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

                # ── Context usage & metadata bridge ────────────────
                result_num_turns = getattr(message, "num_turns", 1) or 1
                for meta_event in self._emit_post_stream_metadata(
                    usage, num_turns=result_num_turns,
                ):
                    yield meta_event

                # ── Post-interrupt corruption detection ────────────
                # After a CompactionGuard interrupt, the CLI subprocess
                # may stay alive but return empty ResultMessages instantly
                # (<2s, no content).  The subprocess is "warm but broken."
                # Kill it so the retry logic can respawn a fresh process.
                # See: 2026-03-22 12:36:08 instant idle after interrupt.
                streaming_dur = (
                    time.time() - self._streaming_start_time
                    if self._streaming_start_time else None
                )
                if (
                    streaming_dur is not None
                    and streaming_dur < 2.0
                    and not self._content_emitted
                    and not is_error
                    and saw_assistant_message  # Only degraded if LLM tried to respond
                ):
                    logger.warning(
                        "session_unit.empty_result_detected "
                        "session_id=%s duration=%.3fs — subprocess "
                        "degraded after interrupt, killing for respawn",
                        self.session_id, streaming_dur,
                    )
                    await self.kill()
                    raise RuntimeError(
                        f"Empty result from degraded subprocess: "
                        f"stream ended in {streaming_dur:.1f}s with no "
                        f"content (session_id={self.session_id})"
                    )

                self._transition(SessionState.IDLE)
                self.last_used = time.time()
                self._retry_count = 0
                return

        # Stream ended without a result message.
        if self.state == SessionState.STREAMING:
            # ── Zombie detection ──────────────────────────────────
            # If the stream ended very fast (< 2s) with no content,
            # the subprocess is likely dead (e.g. corrupted after
            # interrupt).  Kill it so the caller's retry logic can
            # respawn a fresh process with --resume.
            streaming_dur = (
                time.time() - self._streaming_start_time
                if self._streaming_start_time else 0.0
            )
            if streaming_dur < 2.0 and not self._content_emitted:
                logger.warning(
                    "session_unit.zombie_detected session_id=%s "
                    "duration=%.3fs content_emitted=False — killing "
                    "subprocess for respawn",
                    self.session_id, streaming_dur,
                )
                await self.kill()
                raise RuntimeError(
                    f"Zombie subprocess detected: stream ended in "
                    f"{streaming_dur:.1f}s with no content "
                    f"(session_id={self.session_id})"
                )

            self._transition(SessionState.IDLE)
            self.last_used = time.time()

    # ── SSE disconnect recovery ─────────────────────────────────────

    def recover_from_disconnect(self) -> bool:
        """Transition STREAMING → IDLE after SSE client disconnect.

        Returns True if the transition happened.  No-op if not STREAMING.

        This is the public API for ``chat.py``'s disconnect handler —
        avoids calling ``_transition()`` from outside the unit.
        """
        if self.state != SessionState.STREAMING:
            return False
        self._transition(SessionState.IDLE)
        self.last_used = time.time()
        return True

    async def flush_subprocess_pipe(self, timeout: float = 3.0) -> None:
        """Interrupt the CLI subprocess to flush stale pipe events.

        Called after ``recover_from_disconnect()`` as a background task.
        The unit is IDLE; the subprocess may still be running a tool
        whose stdout output would contaminate the next ``send()``.

        Bypasses ``interrupt()`` which is state-gated on STREAMING.
        If the client interrupt times out, kills the subprocess for
        a clean respawn on next ``send()``.
        """
        if self.state != SessionState.IDLE or self._client is None:
            return
        try:
            await asyncio.wait_for(self._client.interrupt(), timeout=timeout)
            logger.info(
                "session_unit.flush_pipe session_id=%s — pipe flushed",
                self.session_id,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "session_unit.flush_pipe session_id=%s — interrupt timed out, "
                "killing for clean respawn",
                self.session_id,
            )
            await self.kill()

    # ── Interactive methods (task 3.3) ─────────────────────────────

    async def interrupt(self, timeout: float = 5.0) -> bool:
        """Interrupt active query. SDK interrupt() with kill fallback.

        State transitions:

        - STREAMING → IDLE (interrupt succeeded, subprocess warm)
        - STREAMING → DEAD → COLD (interrupt timed out, subprocess killed)
        - WAITING_INPUT → IDLE (interrupt succeeded)

        Returns True if subprocess stayed alive (IDLE).

        **Stale-interrupt guard:** Captures ``_send_generation`` at entry.
        If a new ``send()`` starts while this method is awaiting
        ``_client.interrupt()``, the generation advances and this method
        skips all state transitions and kills — preventing the stale
        interrupt from destroying the new stream's subprocess.

        Parameters
        ----------
        timeout:
            Seconds to wait for SDK ``interrupt()`` before falling back
            to killing the subprocess.
        """
        if self.state not in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            return self.is_alive

        # Capture generation BEFORE any mutation.  If send() runs while
        # we're awaiting below, it bumps _send_generation — our snapshot
        # becomes stale and we bail out instead of killing the new stream.
        gen_at_entry = self._send_generation

        self._stop_event.set()
        self._interrupted = True

        if self._client is None:
            # No client — just transition to DEAD (no race: no subprocess)
            self._transition(SessionState.DEAD)
            self._cleanup_internal()
            self._transition(SessionState.COLD)
            return False

        # Capture client reference — send() may replace self._client
        # with a new subprocess while we're awaiting.
        client = self._client

        try:
            await asyncio.wait_for(client.interrupt(), timeout=timeout)

            # ── Stale-interrupt check ─────────────────────────────
            if self._send_generation != gen_at_entry:
                logger.info(
                    "session_unit.interrupt stale (gen %d→%d) — new send() "
                    "started, skipping state transition session_id=%s",
                    gen_at_entry, self._send_generation, self.session_id,
                )
                # Undo mutations we made before the await — send() already
                # cleared these, but clear again defensively in case a
                # second stale interrupt re-set them.
                self._stop_event.clear()
                self._interrupted = False
                return self.is_alive

            # Guard: _read_formatted_response may have already transitioned
            # STREAMING → IDLE via the _interrupted check before we get here.
            # IDLE → IDLE is not a valid transition, so skip if already IDLE.
            if self.state != SessionState.IDLE:
                self._transition(SessionState.IDLE)
            self.last_used = time.time()
            # Clear stop event so the next send()'s SSE stream doesn't
            # immediately see a stale set() from this interrupt.  send()
            # also clears it, but clearing here prevents the race where
            # the SSE heartbeat loop checks stop_event between interrupt()
            # return and the next send().
            self._stop_event.clear()
            logger.info(
                "session_unit.interrupt succeeded session_id=%s pid=%s",
                self.session_id, self.pid,
            )
            return True
        except asyncio.TimeoutError:
            # ── Stale-interrupt check before kill ─────────────────
            if self._send_generation != gen_at_entry:
                logger.info(
                    "session_unit.interrupt stale timeout (gen %d→%d) — "
                    "new send() started, not killing session_id=%s",
                    gen_at_entry, self._send_generation, self.session_id,
                )
                self._stop_event.clear()
                self._interrupted = False
                return self.is_alive
            logger.warning(
                "session_unit.interrupt timed out after %.1fs, killing "
                "session_id=%s pid=%s",
                timeout, self.session_id, self.pid,
            )
            await self.kill()
            return False
        except Exception as exc:
            # ── Stale-interrupt check before kill ─────────────────
            if self._send_generation != gen_at_entry:
                logger.info(
                    "session_unit.interrupt stale error (gen %d→%d) — "
                    "new send() started, not killing session_id=%s",
                    gen_at_entry, self._send_generation, self.session_id,
                )
                self._stop_event.clear()
                self._interrupted = False
                return self.is_alive
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

        # User responded — reset compaction guard so tool counts
        # don't accumulate across the permission/answer boundary.
        self._compaction_guard.reset()
        self._content_emitted = False  # Reset zombie detection for new stream
        self._transition(SessionState.STREAMING)

        try:
            async for event in self._stream_response(answer):
                yield event
        except Exception:
            await self._crash_to_cold_async(clear_identity=False)
            raise

    async def continue_with_permission(
        self, request_id: str, allowed: bool,
    ) -> AsyncIterator[dict]:
        """Continue after cmd_permission_request.

        State: WAITING_INPUT → STREAMING → IDLE/WAITING_INPUT.

        Yields formatted SSE events (same format as send/_stream_response).

        The dangerous_command_gate hook is blocking inside PreToolUse,
        awaiting ``PermissionManager.wait_for_permission_decision()``.
        We signal the decision here, which unblocks the hook. The hook
        returns allow/deny to the SDK, the SDK continues processing,
        and we resume reading the response stream.
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

        # Signal the blocked hook — this unblocks
        # dangerous_command_gate's await on wait_for_permission_decision().
        from core.permission_manager import permission_manager as _pm
        decision = "approve" if allowed else "deny"
        _pm.set_permission_decision(request_id, decision)
        logger.info(
            "session_unit.permission_decision session_id=%s "
            "request_id=%s decision=%s",
            self.session_id, request_id, decision,
        )

        # User responded — reset compaction guard so tool counts
        # don't accumulate across the permission boundary.
        self._compaction_guard.reset()
        self._content_emitted = False  # Reset zombie detection for new stream
        self._transition(SessionState.STREAMING)

        try:
            async for event in self._read_formatted_response():
                yield event
        except Exception:
            await self._crash_to_cold_async(clear_identity=False)
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

        # Inject work summary so post-compaction agent
        # knows what it already did and doesn't re-run completed tools.
        work_summary = self._compaction_guard.work_summary()
        combined_instructions = "\n\n".join(
            part for part in [instructions, work_summary] if part
        )

        command = "/compact"
        if combined_instructions:
            command = f"/compact {combined_instructions}"

        try:
            await self._client.query(
                prompt=command,
                session_id=self._sdk_session_id or "default",
            )
            async for _msg in self._client.receive_response():
                pass  # Drain response
            self.last_used = time.time()
            # Transition guard to ACTIVE — post-compaction loop detection enabled
            self._compaction_guard.activate()
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

            # Collect per-process metrics (non-blocking, best-effort)
            try:
                from .resource_monitor import resource_monitor
                self._last_metrics = resource_monitor.process_metrics(
                    pid=pid,
                    session_id=self.session_id,
                    state=self.state.value,
                )
            except Exception:
                pass  # Never let metrics collection break health_check

            return True
        except ProcessLookupError:
            logger.warning(
                "session_unit.health_check: pid %d dead for session %s",
                pid, self.session_id,
            )
            if self.is_alive:
                await self._crash_to_cold_async(clear_identity=False)
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
        """Best-effort force-kill of the owned subprocess and its children.

        Uses process group kill (SIGKILL to entire pgid) to prevent
        grandchild orphans (e.g. MCP servers spawned by Claude CLI).
        Falls back to plain os.kill if pgid lookup fails.

        SAFETY: Only uses killpg if the child's pgid differs from our
        own — otherwise we'd kill the entire backend + Tauri app.
        The Claude SDK subprocess inherits the parent's pgid unless
        spawned with ``start_new_session=True``, so this guard is
        critical.
        """
        pid = self.pid
        if pid:
            try:
                pgid = os.getpgid(pid)
                my_pgid = os.getpgid(os.getpid())
                if pgid != my_pgid:
                    # Safe: child has its own process group
                    os.killpg(pgid, signal.SIGKILL)
                    logger.info(
                        "session_unit.force_kill_pg session_id=%s pid=%d pgid=%d",
                        self.session_id, pid, pgid,
                    )
                else:
                    # UNSAFE: child shares our pgid — killpg would kill us too.
                    # Two-pass child kill to prevent MCP server orphans:
                    # Pass 1: enumerate and kill known children
                    # Pass 2: kill parent, then re-enumerate for stragglers

                    # Pass 1: kill known children (MCP servers)
                    pass1 = _kill_child_pids(pid)
                    # Kill parent (stops it from spawning new children)
                    os.kill(pid, signal.SIGKILL)
                    # Pass 2: kill any stragglers spawned between pass 1 and parent kill
                    pass2 = _kill_child_pids(pid)
                    logger.info(
                        "session_unit.force_kill_children session_id=%s pid=%d "
                        "pass1=%d pass2=%d (shared pgid=%d)",
                        self.session_id, pid, pass1, pass2, pgid,
                    )
            except (ProcessLookupError, PermissionError):
                logger.debug(
                    "Process %d already dead for session %s",
                    pid, self.session_id,
                )
            except OSError:
                # pgid lookup failed — fall back to direct kill
                try:
                    os.kill(pid, signal.SIGKILL)
                    logger.info(
                        "session_unit.force_kill session_id=%s pid=%d (fallback)",
                        self.session_id, pid,
                    )
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    logger.warning(
                        "Failed to kill pid %d for session %s: %s",
                        pid, self.session_id, exc,
                    )

        # Also try graceful wrapper cleanup
        if self._wrapper is not None:
            try:
                await asyncio.wait_for(
                    self._wrapper.__aexit__(None, None, None),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Wrapper __aexit__ timed out after 5s for session %s",
                    self.session_id,
                )
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
        self._model_name = None
        self._peak_tree_rss_bytes = 0
        # Don't reset _lifecycle_response_count — it tracks across the
        # full unit lifetime (resume awareness persists through kill/restart).

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

    async def _crash_to_cold_async(self, *, clear_identity: bool = False) -> None:
        """Async transition DEAD → COLD with proper wrapper cleanup.

        Calls ``await _force_kill()`` which properly closes the wrapper's
        file descriptors via ``__aexit__()`` before clearing references.

        Args:
            clear_identity: If True, also clears ``_sdk_session_id``
                via ``_full_cleanup()`` (non-retriable crashes).
        """
        self._transition(SessionState.DEAD)
        await self._force_kill()
        if clear_identity:
            self._full_cleanup()
        else:
            self._cleanup_internal()
        self._transition(SessionState.COLD)

    @property
    def streaming_stall_seconds(self) -> Optional[float]:
        """Seconds since last SDK event while in STREAMING state.

        Returns ``None`` if not currently streaming or no events yet.
        Used by ``LifecycleManager`` to detect stuck streams.
        """
        if self.state != SessionState.STREAMING:
            return None
        if self._last_event_time is None:
            # Streaming but no events yet — measure from streaming start
            if self._streaming_start_time is not None:
                return time.time() - self._streaming_start_time
            return None
        return time.time() - self._last_event_time

    async def force_unstick_streaming(self) -> None:
        """Force a stuck STREAMING session back to COLD.

        Kills the subprocess and transitions STREAMING → DEAD → COLD,
        preserving ``_sdk_session_id`` so the next ``send()`` can resume
        the conversation via ``--resume``.

        Called by ``LifecycleManager._check_streaming_timeout()`` and
        by ``send()`` auto-recovery when the previous request left the
        unit stuck in STREAMING.

        Uses ``_crash_to_cold_async()`` which calls
        ``_force_kill()`` to properly close wrapper file descriptors
        via ``__aexit__()``.
        """
        if self.state != SessionState.STREAMING:
            return
        logger.warning(
            "session_unit.force_unstick session_id=%s pid=%s "
            "stall=%.0fs — forcing COLD for recovery",
            self.session_id,
            self.pid,
            self.streaming_stall_seconds or 0,
        )
        await self._crash_to_cold_async(clear_identity=False)

    def clear_session_identity(self) -> None:
        """Clear ``_sdk_session_id`` so the unit cannot resume.

        Called by ``SessionRouter.disconnect_all()`` after ``kill()``
        to ensure shutdown fully cleans up session identity.
        """
        self._sdk_session_id = None
