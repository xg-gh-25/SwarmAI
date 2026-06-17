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
from .session_healing import HealthSensor, HealingLoop, TaskCheckpoint, get_process_rss_mb

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    from core.claude_environment import _ClaudeClientWrapper

logger = logging.getLogger(__name__)

# Dedicated executor for subprocess operations (process tree snapshots,
# pgrep, ps). Isolated from the default asyncio thread pool so that
# blocking waitpid/subprocess.run calls can NEVER starve health checks,
# aiosqlite, or other IO tasks that share the default executor.
# 8 workers = generous ceiling (max 4 sessions × 1 snapshot each + margin).
# Exported (not private) — lifecycle_manager.py imports this for same-pool
# usage in RSS monitoring and orphan reaping.
from concurrent.futures import ThreadPoolExecutor
subprocess_executor = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="subprocess"
)
# Legacy alias for internal callers
_subprocess_executor = subprocess_executor

# Module-level lock that serializes subprocess spawn operations.
# Held during _configure_claude_environment + wrapper.__aenter__() to
# prevent concurrent sessions from racing on os.environ mutations.
# INTENTIONALLY module-level (not per-instance): os.environ is process-global,
# so ALL spawns across ALL SessionUnit instances must serialize.
# If you need multiple SessionRouter instances (e.g., tests), mock this lock.
_spawn_lock = asyncio.Lock()

# Global OOM cooldown: after any session gets OOM-killed, ALL sessions must
# wait before retrying.  Prevents the death spiral where two sessions retry
# simultaneously, each spawning ~500MB, and both get killed again.
# Value: monotonic timestamp when the cooldown expires (0 = no cooldown).
_oom_cooldown_until: float = 0.0
_OOM_COOLDOWN_BASE: float = 30.0  # seconds, doubles per consecutive OOM
_OOM_COOLDOWN_CAP: float = 120.0  # max backoff cap for OOM retries

# Threshold for auto-recovery of stuck STREAMING sessions.
# If a new send() arrives while STREAMING and the last SDK event was
# less than this many seconds ago, the session is ACTIVELY PROCESSING
# (not stuck) — raise SessionBusyError instead of force-killing.
# Only force-kill when stall exceeds this value.
# See: 2026-04-02 diagnosis — auto_recover_stuck killed session with stall=1s.
AUTO_RECOVER_STALL_THRESHOLD: float = 180.0


# ── Streaming timeout resilience (2026-05-19) ────────────────────
# Circuit breaker for high-context timeout dead loops.
# See: Knowledge/Designs/2026-05-19-streaming-timeout-resilience-design.md

CIRCUIT_BREAKER_CONTEXT_THRESHOLD: int = 1_000_000  # tokens (must be above adaptive timeout kick-in at 900K)


def should_circuit_break_timeout(
    consecutive_timeouts: int,
    context_tokens: int,
) -> bool:
    """Return True if retry is structurally doomed (should stop retrying).

    High context (>800K tokens) + 2 consecutive timeouts = model inference
    time exceeds timeout cap. Retrying will produce the same result.
    """
    return (
        consecutive_timeouts >= 2
        and context_tokens > CIRCUIT_BREAKER_CONTEXT_THRESHOLD
    )


def build_context_too_large_event(
    context_tokens: int,
    consecutive_timeouts: int,
) -> dict:
    """Build SSE error event for CONTEXT_TOO_LARGE condition."""
    return {
        "type": "error",
        "code": "CONTEXT_TOO_LARGE",
        "message": (
            f"Session context is very large ({context_tokens // 1000}K tokens). "
            f"Model inference timed out {consecutive_timeouts}x. "
            f"Recommendation: start a new tab for fresh context, "
            f"or send a shorter follow-up message."
        ),
        "recoverable": True,
    }


def _get_children(pid: int) -> list[int]:
    """Get direct child PIDs via ``pgrep -P``. Best-effort."""
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True, text=True, timeout=3,
        )
        children = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line:
                try:
                    children.append(int(line))
                except ValueError:
                    pass
        return children
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _snapshot_process_table() -> dict[int, int]:
    """Snapshot the entire process table as {pid: ppid} in one call.

    Uses ``ps -eo pid,ppid`` which is a single fork — O(1) subprocess
    invocations regardless of tree depth.  Falls back to empty dict
    on failure.

    Parsing is defensive: skips any line that doesn't contain exactly
    two integer fields, handling header variations across macOS/Linux.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid"],
            capture_output=True, text=True, timeout=5,
        )
        table: dict[int, int] = {}
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
                table[pid] = ppid
            except ValueError:
                continue  # header line or malformed — skip
        return table
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}


def _snapshot_descendant_tree(parent_pid: int) -> list[int]:
    """Snapshot the full descendant tree of *parent_pid* WITHOUT killing.

    Returns PIDs in bottom-up order (deepest leaves first) so the caller
    can kill them in one atomic sweep — no reparenting race.

    Uses a single ``ps -eo pid,ppid`` call to snapshot the entire process
    table, then builds the tree in-memory.  This is O(1) subprocess
    invocations regardless of tree depth (previous approach was O(N)
    pgrep calls for N nodes).
    """
    table = _snapshot_process_table()
    if not table:
        # Fallback to pgrep-based approach if ps fails
        return _snapshot_descendant_tree_pgrep(parent_pid)

    # Build children map from the process table
    children_map: dict[int, list[int]] = {}
    for pid, ppid in table.items():
        children_map.setdefault(ppid, []).append(pid)

    # BFS to collect all descendants
    to_kill: list[int] = []
    stack = list(children_map.get(parent_pid, []))
    visited: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in visited:
            continue
        visited.add(pid)
        to_kill.append(pid)
        stack.extend(children_map.get(pid, []))

    # Reverse: deepest descendants first (bottom-up kill order)
    to_kill.reverse()
    return to_kill


def _snapshot_descendant_tree_pgrep(parent_pid: int) -> list[int]:
    """Fallback: snapshot tree via per-node pgrep -P calls.

    Used when ``ps -eo pid,ppid`` fails (shouldn't happen on macOS/Linux).
    """
    to_kill: list[int] = []
    stack = _get_children(parent_pid)
    visited: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in visited:
            continue
        visited.add(pid)
        to_kill.append(pid)
        stack.extend(_get_children(pid))

    to_kill.reverse()
    return to_kill


def _kill_pids(pids: list[int]) -> int:
    """SIGKILL a list of PIDs. Returns count successfully killed."""
    killed = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except (ProcessLookupError, PermissionError):
            pass
    return killed


def _kill_child_pids(parent_pid: int) -> int:
    """SIGKILL all descendants of *parent_pid* (recursive). Returns count killed.

    Two-phase approach to prevent orphan creation:
    1. SNAPSHOT: enumerate the entire tree via recursive pgrep -P
    2. KILL: bottom-up in one sweep (leaves first, no reparenting race)

    The old approach interleaved enum+kill per level, which caused children
    to reparent to pid=1 when their parent was killed mid-enumeration.
    """
    tree = _snapshot_descendant_tree(parent_pid)
    return _kill_pids(tree)


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
        # True when this unit serves channel conversations (Slack, etc.)
        # Channel units use a dedicated slot pool, separate from chat tabs.
        self.is_channel_session: bool = False
        # True after the first message with history injection has been
        # processed.  Prevents re-injecting on every subsequent message
        # within the same daemon lifecycle (channel resume fix).
        self._channel_history_injected: bool = False

        # ── Internal — not part of public interface ──────────────
        self._client: Optional[ClaudeSDKClient] = None
        self._wrapper: Optional[_ClaudeClientWrapper] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._sdk_session_id: Optional[str] = None
        self._interrupted: bool = False
        self._retry_count: int = 0
        self._model_name: Optional[str] = None

        # ── Recall injection (G3: post-first-message) ─────────────
        # Set True after first-message recall runs (or is skipped).
        # Prevents re-running on subsequent messages in the same session.
        self._recall_injected: bool = False

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

        # ── Lazy MCP: per-session overrides ──────────────────────────
        # MCP names added via enable_mcp_for_session().  On next spawn,
        # these names are passed to load_mcp_config_tiered(extra_always=...)
        # so they load regardless of their tier in mcp-dev.json.
        self._extra_mcps: set[str] = set()

        # ── MCP health detection ─────────────────────────────────────
        # Set of MCP server names that were passed to the CLI at spawn.
        # Used by post-spawn health check to detect failed MCPs.
        self._configured_mcps: set[str] = set()
        # Flag: health check runs only ONCE per session (first ResultMessage).
        self._mcp_health_checked: bool = False

        # ── Sub-agent progress observability ─────────────────────
        # Tracks active sub-agent(s) for progress observability.
        # Keys = tool_use_id, values = {label, start_time}.
        # Set when ToolUseBlock(name="Agent") arrives, removed when matching
        # ToolResultBlock arrives. Cleared at every turn boundary (send,
        # interrupt, turn_limit) to prevent cross-turn stale banners.
        # Frontend polls via GET /sessions/{id}/sub-agent-progress.
        self._active_agent_tools: dict[str, dict] = {}

        # ── Proactive RSS restart cooldown ────────────────────────
        # Monotonic timestamp of last proactive compact→kill cycle.
        # Prevents repeated restarts within the PROACTIVE_COOLDOWN window.
        # Uses monotonic clock — immune to NTP sync / sleep-wake clock jumps.
        # -inf ensures first restart is never cooldown-blocked (monotonic()
        # can be < PROACTIVE_COOLDOWN on freshly booted CI runners).
        self._last_proactive_restart: float = float("-inf")

        # ── Resource observability ─────────────────────────────────
        self._last_error_type: Optional[str] = None  # FailureType.value: "oom" | "rate_limit" | "api_error" | "timeout" | "unknown"
        self._last_metrics: Optional[Any] = None      # ProcessMetrics from health_check

        # ── Observability callback ───────────────────────────────
        self._on_state_change: Optional[
            Callable[[str, SessionState, SessionState], None]
        ] = on_state_change

        # ── SSE stop notification ─────────────────────────────────
        self._stop_event: asyncio.Event = asyncio.Event()

        # ── Pipe flush background task ────────────────────────────
        # Tracks the fire-and-forget flush_subprocess_pipe() task so
        # send() can cancel it before starting a new stream.  Without
        # this, the flush's _client.interrupt() races with the new stream.
        self._pipe_flush_task: Optional[asyncio.Task] = None

        # ── Send generation counter (stale-interrupt guard) ────────
        # Monotonically incremented at the start of each send().
        # interrupt() captures this at entry and skips state
        # transitions / kills if the generation has advanced — meaning
        # a new send() started while the old interrupt was in-flight.
        self._send_generation: int = 0

        # ── Self-healing (invisible session refresh) ────────────────
        # HealthSensor monitors per-turn metrics and triggers heal when
        # degradation is detected. HealingLoop orchestrates the refresh
        # cycle. Together they prevent sessions from crashing — the system
        # heals itself without user intervention.
        self._health_sensor: HealthSensor = HealthSensor(max_turns=500)
        self._healing_loop: HealingLoop = HealingLoop()
        # Checkpoint built before heal-kill, consumed by next spawn to
        # inject continuation context. None = no pending heal context.
        self._heal_checkpoint: TaskCheckpoint | None = None

        # ── OOM tracking (persists across send() calls) ───────────
        # Counts consecutive OOM kills for this session.  NOT reset in
        # send() — prevents the death spiral where OOM → retry → OOM
        # loops forever because _retry_count resets each send().
        # Reset only on successful stream completion.
        self._consecutive_oom_kills: int = 0
        self._OOM_KILL_LIMIT: int = 3  # After 3 consecutive OOMs, stop retrying

        # ── PID Watchdog (out-of-band subprocess death detection) ──
        # Polls os.kill(pid, 0) while STREAMING/WAITING_INPUT.
        # Detects external kills (jetsam, OOM) that pipe can't detect.
        self._pid_watchdog_task: Optional[asyncio.Task] = None
        self._PID_WATCHDOG_INTERVAL: float = 5.0  # seconds between polls

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

        # Same-state transitions are no-ops — safe under concurrent access.
        # Multiple coroutines (kill(), lifecycle reap, crash recovery) may
        # race to the same target state; the first one wins, the rest skip.
        if old_state == new_state:
            return

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
            # Start PID watchdog for out-of-band death detection
            self._start_pid_watchdog()

        # Clear streaming timestamps when leaving STREAMING
        if old_state == SessionState.STREAMING and new_state != SessionState.STREAMING:
            self._streaming_start_time = None
            self._last_event_time = None

        # Stop PID watchdog when leaving any watchable state to a non-watchable one.
        # Watchable = STREAMING, WAITING_INPUT. Non-watchable = IDLE, COLD, DEAD.
        # Note: STREAMING → WAITING_INPUT does NOT stop the watchdog (still watchable).
        _watchable = (SessionState.STREAMING, SessionState.WAITING_INPUT)
        if old_state in _watchable and new_state not in _watchable:
            self._stop_pid_watchdog()

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

    # ── PID Watchdog ─────────────────────────────────────────────

    def _start_pid_watchdog(self) -> None:
        """Start background task that polls subprocess liveness.

        Only starts if a PID is available (subprocess spawned).
        The watchdog polls os.kill(pid, 0) every _PID_WATCHDOG_INTERVAL
        seconds. If the process is gone (ProcessLookupError), it
        transitions the session to DEAD — enabling auto-recovery.

        This is the out-of-band detection mechanism: when the pipe
        hangs (jetsam kill, OOM), the stream reader blocks forever.
        The watchdog detects death independently.
        """
        pid = self.pid
        if pid is None:
            return  # No subprocess to watch

        # Cancel existing watchdog if any (shouldn't happen, but defensive)
        self._stop_pid_watchdog()

        self._pid_watchdog_task = asyncio.ensure_future(
            self._pid_watchdog_loop(pid)
        )

    def _stop_pid_watchdog(self) -> None:
        """Cancel the PID watchdog task if running."""
        if self._pid_watchdog_task is not None:
            self._pid_watchdog_task.cancel()
            self._pid_watchdog_task = None

    async def _pid_watchdog_loop(self, pid: int) -> None:
        """Poll subprocess PID until death or cancellation.

        Two detection mechanisms:
        1. Process existence: os.kill(pid, 0) — detects external kills
           (jetsam, OOM, manual SIGKILL).
        2. Output liveness: _last_event_time staleness — detects API hangs
           where the subprocess is alive but blocked on network I/O (the
           asyncio.wait_for timeout cannot cancel native pipe reads).

        On detection:
        - Transitions to DEAD (fast signal — 5s detection)
        - Full cleanup happens through the existing error path:
          streaming timeout fires → RuntimeError → send() catches →
          _crash_to_cold_async(). The watchdog is the SIGNAL; the
          streaming error handler is the CLEANUP.
        """
        try:
            while True:
                await asyncio.sleep(self._PID_WATCHDOG_INTERVAL)

                # ── Check 1: Process existence ────────────────────────
                try:
                    os.kill(pid, 0)
                    # Process is alive — continue to liveness check
                except PermissionError:
                    # Process exists but we can't signal it — treat as alive
                    pass
                except ProcessLookupError:
                    # Process is GONE — external kill (jetsam, OOM, manual)
                    logger.warning(
                        "session_unit.pid_watchdog_death session_id=%s pid=%d "
                        "— subprocess externally killed, transitioning to DEAD",
                        self.session_id, pid,
                    )
                    # Only transition if still in a watchable state
                    if self.state in (
                        SessionState.STREAMING,
                        SessionState.WAITING_INPUT,
                    ):
                        self._transition(SessionState.DEAD)
                    return

                # ── Check 2: Output liveness ──────────────────────────
                # Only applies when STREAMING and _last_event_time is set
                # (set to time.time() on STREAMING entry, updated on each
                # SDK event). Acts as a backstop if the stream reader's
                # asyncio.wait_for timeout cannot cancel the native pipe
                # read. WAITING_INPUT is excluded because the user may
                # take arbitrarily long to respond.
                if self.state == SessionState.STREAMING:
                    last_event = self._last_event_time
                    if last_event is not None:
                        silence = time.time() - last_event
                        timeout = self._compute_message_timeout()
                        if silence > timeout:
                            logger.error(
                                "session_unit.output_liveness_timeout "
                                "session_id=%s pid=%d silence=%.0fs "
                                "timeout=%.0fs — killing subprocess",
                                self.session_id, pid, silence, timeout,
                            )
                            # Prevent self-cancellation: _transition(DEAD)
                            # calls _stop_pid_watchdog() which would cancel
                            # THIS task before _force_kill() completes.
                            # Nulling the reference makes the stop a no-op.
                            self._pid_watchdog_task = None
                            self._transition(SessionState.DEAD)
                            await self._force_kill()
                            return

        except asyncio.CancelledError:
            return  # Normal shutdown

    # ── Constants ─────────────────────────────────────────────────

    MAX_RETRY_ATTEMPTS: int = 3
    RETRY_BACKOFF_SECONDS: float = 5.0
    STREAMING_TIMEOUT_SECONDS: float = 300.0  # 5 min base (adaptive via _compute_message_timeout)

    # ── Adaptive timeout ─────────────────────────────────────────

    def _compute_message_timeout(self) -> float:
        """Timeout that scales with context size.

        Empirical: Opus 4.6 TTFT scales roughly with context tokens.
        At 2M tokens, inference can take 400-600s — a fixed 300s guarantees
        timeout → retry → timeout dead loops.

        Formula: max(300, context_tokens / 3000), capped at 900s.
        """
        BASE_TIMEOUT = 300.0
        MAX_TIMEOUT = 900.0
        TOKENS_PER_SECOND = 3000  # Conservative model throughput estimate

        estimated_context = getattr(self, "_last_known_context_tokens", 0) or 0
        computed = max(BASE_TIMEOUT, estimated_context / TOKENS_PER_SECOND)
        return min(computed, MAX_TIMEOUT)

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

        # Cancel any in-flight pipe flush from a prior SSE disconnect.
        # The flush sends a JSON "interrupt" control request to the CLI
        # subprocess (NOT an OS signal).  If the flush times out (5s), it
        # kills the subprocess → transitions to DEAD → COLD.
        #
        # We must await the task completion to prevent two races:
        # (1) flush timeout → kill() → unit in DEAD state when our send()
        #     checks state at line 775 → RuntimeError "Cannot send() in
        #     state dead" → frontend shows "Connection interrupted".
        # (2) flush's interrupt is in-flight → subprocess receives both
        #     the old interrupt AND our new send_message → confused state.
        #
        # Awaiting costs <1ms when flush already completed (common path),
        # and at most 5s when flush is mid-timeout (rare, but prevents
        # user-visible error that forces resend).
        if self._pipe_flush_task and not self._pipe_flush_task.done():
            # CRITICAL: Do NOT cancel — let the flush complete so the pipe
            # is drained of stale response data.  Cancelling leaves old
            # response bytes in the subprocess stdout pipe, which then get
            # yielded as part of the NEW response (cross-turn bleed P0 bug).
            #
            # The flush itself has a 5s internal timeout + generation guard,
            # so worst case we wait ~5s here.  If it finishes faster (common
            # case: <100ms when subprocess already idle), we proceed instantly.
            try:
                await asyncio.wait_for(self._pipe_flush_task, timeout=5.0)
            except asyncio.TimeoutError:
                # Flush didn't complete in 5s — cancel and force-kill
                # the subprocess for a clean slate on next spawn.
                self._pipe_flush_task.cancel()
                try:
                    await self._pipe_flush_task
                except (asyncio.CancelledError, Exception):
                    pass
                # Kill subprocess to ensure no stale pipe data remains
                if self._client is not None:
                    logger.warning(
                        "session_unit.pipe_flush_timeout session_id=%s — "
                        "killing subprocess for clean respawn",
                        self.session_id,
                    )
                    await self.kill()
            except asyncio.CancelledError:
                raise  # Propagate — caller (HTTP request) was aborted
            except Exception:
                pass  # Task completed with error — pipe is clean either way
            self._pipe_flush_task = None

        # ── STREAMING state handling — three cases ─────────────────────
        #
        # HISTORY (this section has been fixed 4+ times):
        #   2026-04-02: Added SessionBusyError to stop force_unstick killing
        #               active streams (SSE disconnect race).
        #   2026-05-14: Added frontend poll recovery for SESSION_BUSY.
        #   2026-06-01: Added interrupt-wait path below (this fix).
        #
        # BUG (2026-06-01): "Must send twice after stop to resume"
        #   User clicks Stop → frontend fire-and-forget POST /chat/stop
        #   → backend interrupt() begins (async, takes 1-5s)
        #   → user sends new message BEFORE interrupt() completes
        #   → send() sees state=STREAMING + stall<180s → SessionBusyError
        #   → frontend shows "Connection interrupted"
        #   → second message arrives after interrupt() is done → works.
        #
        # ROOT CAUSE: Frontend stopSession() is fire-and-forget (correct
        # for UX — user sees "Stopped" immediately). But backend's
        # interrupt() is async (awaits client.interrupt() up to 5s).
        # No synchronization between "stop completed" and "next send".
        #
        # WHY THIS FIX: We distinguish "STREAMING because the model is
        # actively responding" (→ reject with SessionBusyError) from
        # "STREAMING because interrupt() hasn't finished yet" (→ wait).
        # The signal is _stop_event.is_set() — interrupt() sets it at
        # entry (line ~2593) and clears it on completion (line ~2635).
        # If set, we know an interrupt is in flight — just wait for it.
        #
        # WHY NOT FIX FRONTEND: Making stopSession() await would block
        # the UI for 1-5s on every stop. Current UX is correct.
        # WHY NOT FIX INTERRUPT SPEED: client.interrupt() latency is
        # SDK-controlled (sends SIGINT, waits for graceful shutdown).
        #
        if self.state == SessionState.STREAMING:
            if self._stop_event.is_set():
                # ── Case 1: Interrupt in progress ─────────────────────
                # _stop_event is ONLY set by interrupt() at entry and
                # cleared on completion. If set → interrupt() is mid-await.
                # Wait for it to finish (state → IDLE or COLD).
                logger.info(
                    "session_unit.awaiting_interrupt_completion "
                    "session_id=%s — stop_event set, waiting for "
                    "interrupt to finish before send()",
                    self.session_id,
                )
                # Poll state with short sleeps — interrupt() will transition
                # STREAMING → IDLE within its 5s timeout, or kill → COLD.
                # Budget: 6s > interrupt timeout (5s) to avoid racing.
                for _ in range(60):  # 60 × 100ms = 6s max
                    await asyncio.sleep(0.1)
                    if self.state != SessionState.STREAMING:
                        break
                if self.state == SessionState.STREAMING:
                    # Interrupt didn't complete in 6s — force recovery.
                    # This shouldn't happen (interrupt timeout = 5s + kill),
                    # but defensive against edge cases.
                    logger.warning(
                        "session_unit.interrupt_wait_timeout "
                        "session_id=%s — forcing COLD after 6s wait",
                        self.session_id,
                    )
                    await self.force_unstick_streaming()
                # Fall through — state is now IDLE or COLD
            else:
                # ── Case 2 & 3: Genuinely streaming (no interrupt) ────
                stall = self.streaming_stall_seconds
                if stall is not None and stall < AUTO_RECOVER_STALL_THRESHOLD:
                    # Case 2: Actively streaming — reject, frontend queues.
                    from .exceptions import SessionBusyError
                    logger.info(
                        "session_unit.active_streaming_rejected "
                        "session_id=%s stall=%.0fs (threshold=%.0fs) "
                        "— rejecting send, frontend should queue",
                        self.session_id, stall, AUTO_RECOVER_STALL_THRESHOLD,
                    )
                    raise SessionBusyError(
                        detail=(
                            f"Session {self.session_id} is actively streaming "
                            f"(last event {stall:.0f}s ago, threshold "
                            f"{AUTO_RECOVER_STALL_THRESHOLD:.0f}s). "
                            f"Queue the message on the frontend."
                        ),
                    )
                # Case 3: Stuck (no events for >180s) — force kill + respawn.
                logger.warning(
                    "session_unit.auto_recover_stuck session_id=%s state=%s "
                    "stall=%.0fs (threshold=%.0fs) — forcing COLD before retry",
                    self.session_id, self.state.value,
                    stall or 0, AUTO_RECOVER_STALL_THRESHOLD,
                )
                await self.force_unstick_streaming()
                # After force_unstick, state is COLD — fall through to spawn

        if self.state == SessionState.WAITING_INPUT:
            # Frontend crashed or user abandoned the question — auto-recover.
            # Kill subprocess and transition to COLD so we can resume.
            logger.warning(
                "session_unit.auto_recover_waiting_input session_id=%s "
                "— frontend sent new message while WAITING_INPUT, "
                "forcing COLD for recovery (abandoned ask_user_question)",
                self.session_id,
            )
            await self.force_unstick_waiting_input()
            # After force_unstick, state is COLD — fall through to spawn

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
        self._active_agent_tools = {}  # Clear stale sub-agent progress on new turn

        # Spawn if needed (COLD → IDLE under _spawn_lock + _env_lock)
        # Also respawn if IDLE but client is gone (CLI exited after
        # error_max_turns — state stayed IDLE for hooks/slots, but
        # process is dead). Transition to COLD first so _ensure_spawned
        # works correctly.
        if self.state == SessionState.IDLE and self._client is None:
            self._transition(SessionState.COLD)
        if self.state == SessionState.COLD:
            async for event in self._ensure_spawned(options, config):
                if event.get("_abort"):
                    return  # spawn failed after retries
                yield event

        # IDLE → STREAMING
        self._transition(SessionState.STREAMING)
        self._model_name = getattr(options, "model", None)

        # ── Heal checkpoint injection (invisible to user) ─────────
        # If a self-heal just happened, prepend continuation context to the
        # user's query so the agent knows to continue seamlessly.
        if self._heal_checkpoint is not None:
            continuation = self._heal_checkpoint.to_continuation_prompt()
            if isinstance(query_content, str):
                query_content = f"{continuation}\n\n---\n\n{query_content}"
            self._heal_checkpoint = None  # Consumed — don't re-inject

        try:
            async for event in self._stream_response(query_content):
                yield event
            # Success — reset OOM counter (session is healthy)
            self._consecutive_oom_kills = 0

            # ── Self-healing check (invisible to user) ────────────
            # After successful stream, check if session health is degrading.
            # If so, proactively heal (kill → respawn) so next turn starts fresh.
            # User sees nothing — this happens between turns, not mid-stream.
            should_heal, trigger = self._health_sensor.should_checkpoint()
            if should_heal:
                can_heal, reason = self._healing_loop.can_heal()
                if can_heal:
                    logger.info(
                        "session_unit.self_heal trigger=%s session_id=%s turn=%d",
                        trigger, self.session_id, self._health_sensor.turn_count,
                    )
                    self._healing_loop.record_heal_start()
                    # Build TaskCheckpoint before kill (captures current context)
                    self._heal_checkpoint = TaskCheckpoint(
                        original_request=str(query_content)[:500] if query_content else "",
                        trigger=trigger,
                        turn_count=self._health_sensor.turn_count,
                        heal_attempt=self._healing_loop.heal_attempts,
                    )
                    # Kill subprocess but keep _sdk_session_id (for --resume).
                    # _crash_to_cold_async(clear_identity=False) = kill + COLD
                    # so next send() re-spawns with --resume context.
                    await self._crash_to_cold_async(clear_identity=False)
                    self._health_sensor.reset()
                    self._healing_loop.record_heal_success()
                    # Next send() will detect state=COLD → _ensure_spawned
                    # The _heal_checkpoint is consumed by _ensure_spawned to
                    # inject continuation context into the new subprocess.
                elif self._healing_loop.should_escalate():
                    logger.warning(
                        "session_unit.self_heal_exhausted session_id=%s "
                        "trigger=%s attempts=%d",
                        self.session_id, trigger, self._healing_loop.heal_attempts,
                    )
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
            if _is_retriable_error(error_str, tb_str) and self._retry_count < self.MAX_RETRY_ATTEMPTS:
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

        # ── Resume injection: COLD + _sdk_session_id → spawn with --resume
        # Covers ALL kill-then-respawn paths (proactive restart, eviction,
        # OOM crash, streaming timeout) — not just the retry path.
        # This fixes an entire class of context-loss bugs where kill()
        # preserved _sdk_session_id but the next spawn didn't use it.
        if self._sdk_session_id:
            options = self._build_retry_options(options, self._sdk_session_id)
            # No fixed sleep needed here — _force_kill() now polls for
            # process exit via _await_process_exit() before returning.
            # The old 1.5s sleep was a timing guess that failed on slow
            # machines and wasted latency on fast ones.

        try:
            await self._spawn(options, config)
            return  # success — state is IDLE
        except Exception as exc:
            error_str = str(exc)
            spawn_tb_str = traceback.format_exc()

        if _is_retriable_error(error_str, spawn_tb_str):
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
                _is_retriable_error(error_str, spawn_tb_str)
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
        # No fixed sleep needed — _crash_to_cold_async() calls _force_kill()
        # which polls for process exit before returning.

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

        Handles failure-type-aware backoff (OOM → exponential 30/60/120s,
        rate limit → wait for reset, else → exponential), global OOM
        cooldown to prevent parallel retry storms, spawn budget re-check
        after backoff, and ``--resume`` flag for conversation context
        restoration.

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

        global _oom_cooldown_until

        error_str = initial_error_str
        # Capture SDK session ID before cleanup for --resume
        resume_session_id = self._sdk_session_id
        _consecutive_timeouts = 0

        _tb_str = initial_tb_str or ""
        while (
            _is_retriable_error(error_str, _tb_str)
            and self._retry_count < self.MAX_RETRY_ATTEMPTS
        ):
            self._retry_count += 1

            # ── Structured failure classification ─────────────
            failure_type, failure_meta = classify_failure(
                error_str, self._hook_session_context,
            )
            self._last_error_type = failure_type.value

            # ── Fix 3: Per-session OOM counter (persists across send()) ──
            if failure_type == FailureType.OOM:
                self._consecutive_oom_kills += 1

                # OOM cooldown is handled by _oom_cooldown_until (global,
                # module-level in session_unit). spawn_budget checks memory
                # numbers only — no duplicate cooldown in resource_monitor.

                # ── Fix 5: Notify frontend about OOM ─────────────
                yield {
                    "type": "status",
                    "message": (
                        f"Memory pressure detected — the AI process was killed by the system "
                        f"(attempt {self._consecutive_oom_kills}). "
                        f"Close unused tabs or apps to free memory."
                    ),
                    "code": "OOM_DETECTED",
                }

                # Stop retrying after too many consecutive OOMs
                if self._consecutive_oom_kills >= self._OOM_KILL_LIMIT:
                    logger.warning(
                        "session_unit: %d consecutive OOM kills for session %s — "
                        "giving up (system cannot sustain this session)",
                        self._consecutive_oom_kills, self.session_id,
                    )
                    await self._crash_to_cold_async(clear_identity=True)
                    yield _build_error_event(
                        code="OOM_LIMIT_REACHED",
                        message=(
                            "The AI service keeps running out of memory. "
                            "Close other tabs and apps to free memory, "
                            "then try again."
                        ),
                        suggested_action=(
                            "Close idle chat tabs, quit memory-heavy apps "
                            "(Chrome, Slack), then send your message again."
                        ),
                    )
                    yield {"_abort": True}
                    return

                # ── Fix 2: Global OOM cooldown ────────────────────
                # Set a global cooldown so OTHER sessions also wait.
                # Protected by _spawn_lock to prevent TOCTOU: two sessions
                # both reading cooldown < now, both spawning, both dying.
                cooldown_secs = min(
                    _OOM_COOLDOWN_BASE * (2 ** (self._consecutive_oom_kills - 1)),
                    _OOM_COOLDOWN_CAP,
                )
                async with _spawn_lock:
                    _oom_cooldown_until = time.monotonic() + cooldown_secs
                logger.info(
                    "session_unit: global OOM cooldown set for %.0fs "
                    "(session=%s, consecutive_ooms=%d)",
                    cooldown_secs, self.session_id,
                    self._consecutive_oom_kills,
                )

            # Track consecutive timeouts to abandon --resume
            if failure_type == FailureType.TIMEOUT:
                _consecutive_timeouts += 1
            else:
                _consecutive_timeouts = 0

            # ── Circuit breaker: stop retrying if structurally doomed ──
            # High context + repeated timeouts = model inference time exceeds
            # our timeout cap. Retrying produces the same result every time.
            context_tokens = getattr(self, "_last_known_context_tokens", 0) or 0
            if should_circuit_break_timeout(_consecutive_timeouts, context_tokens):
                logger.warning(
                    "session_unit.circuit_breaker session_id=%s "
                    "context=%d tokens, consecutive_timeouts=%d — "
                    "stopping retry (structurally doomed)",
                    self.session_id, context_tokens, _consecutive_timeouts,
                )
                yield build_context_too_large_event(context_tokens, _consecutive_timeouts)
                # Exit retry loop — let session go IDLE, user sees the error
                break

            # After 2 consecutive timeouts with --resume, the resume target
            # is likely broken.  Abandon resume and start fresh.
            if _consecutive_timeouts >= 2 and resume_session_id:
                logger.warning(
                    "session_unit: %d consecutive timeouts with --resume, "
                    "abandoning resume for session %s",
                    _consecutive_timeouts, self.session_id,
                )
                resume_session_id = None

            # Failure-type-aware backoff:
            # OOM → exponential 30/60/120s, Rate limit → wait for reset
            backoff = compute_backoff(
                failure_type, failure_meta,
                self._retry_count, self.RETRY_BACKOFF_SECONDS,
            )

            # ── Fix 2: Respect global OOM cooldown ────────────────
            # If another session set a cooldown, wait at least that long.
            # Read under _spawn_lock to prevent TOCTOU with the write side.
            async with _spawn_lock:
                now = time.monotonic()
                oom_deadline = _oom_cooldown_until
            if oom_deadline > now:
                remaining_cooldown = oom_deadline - now
                if remaining_cooldown > backoff:
                    logger.info(
                        "session_unit: extending backoff %.0fs → %.0fs "
                        "(global OOM cooldown, session=%s)",
                        backoff, remaining_cooldown, self.session_id,
                    )
                    backoff = remaining_cooldown

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

            yield {
                "type": "status",
                "message": f"Reconnecting (attempt {self._retry_count}/{self.MAX_RETRY_ATTEMPTS})...",
                "code": "RETRY_SPAWN",
            }

            await self._crash_to_cold_async()

            # Clear hook failure context after reading
            if self._hook_session_context:
                self._hook_session_context.pop("_last_notification", None)
                self._hook_session_context.pop("_stop_info", None)

            await asyncio.sleep(backoff)

            # Re-check spawn budget AND slot availability after backoff.
            # Retries bypass session_router._acquire_slot(), so we must
            # enforce the concurrency limit here to prevent OOM cascades
            # from 3+ simultaneous CLI processes (COE: 2026-04-12).
            try:
                from .resource_monitor import resource_monitor
                max_tabs = resource_monitor.compute_max_tabs()

                # Count alive sessions from the registry (if available).
                # This prevents retries from spawning beyond the slot limit.
                alive_exceeds_limit = False
                _alive = 0
                try:
                    from . import session_registry
                    router = session_registry.session_router
                    if router:
                        _alive = router.alive_count
                    if router and _alive >= max_tabs:
                        alive_exceeds_limit = True
                        logger.warning(
                            "Retry %d aborted: alive_count=%d >= max_tabs=%d "
                            "session_id=%s — retry would exceed slot limit",
                            self._retry_count, router.alive_count,
                            max_tabs, self.session_id,
                        )
                except Exception as exc:
                    logger.warning("Retry slot guard unavailable: %s", exc)

                budget = resource_monitor.spawn_budget(alive_count=_alive)
                if not budget.can_spawn or alive_exceeds_limit:
                    reason = (
                        f"alive_count >= max_tabs ({max_tabs})"
                        if alive_exceeds_limit else budget.reason
                    )
                    logger.warning(
                        "Retry %d aborted: %s "
                        "post-backoff session_id=%s",
                        self._retry_count, reason, self.session_id,
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
                spawn_tb = traceback.format_exc()
                error_str = str(spawn_exc)
                _tb_str = spawn_tb
                if _is_retriable_error(error_str, spawn_tb):
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

            self._active_agent_tools = {}  # Clear ghost entries from crashed attempt
            self._transition(SessionState.STREAMING)

            try:
                async for event in self._stream_response(query_content):
                    yield event
                # Success — reset OOM counter
                self._consecutive_oom_kills = 0
                return
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
        # Track context size for adaptive timeout (L1 resilience)
        if input_tokens and input_tokens > 0:
            self._last_known_context_tokens = input_tokens
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

    async def _check_mcp_health(self) -> Optional[dict]:
        """Check MCP server health after first response and return warning event.

        Calls ``get_mcp_status()`` on the CLI subprocess to discover which
        configured MCPs actually connected.  Compares against
        ``_configured_mcps`` (captured at spawn).

        Returns:
            A warning event dict if MCPs failed, or ``None`` if all healthy.
            Never raises — failures are silently logged.
        """
        if self._mcp_health_checked:
            return None
        self._mcp_health_checked = True

        if not self._configured_mcps or self._client is None:
            return None

        try:
            status_response = await self._client.get_mcp_status()
        except Exception as exc:
            logger.debug(
                "session_unit.mcp_health_check_failed session_id=%s: %s",
                self.session_id, exc,
            )
            return None

        mcp_servers = status_response.get("mcpServers", [])

        # If CLI returned no servers at all, the control command may not be
        # supported (old SDK version) — skip rather than false-alarm.
        if not mcp_servers:
            logger.debug(
                "session_unit.mcp_health_check_empty session_id=%s "
                "(CLI returned no MCP status — skipping)",
                self.session_id,
            )
            return None

        # Build set of non-failed MCP names.
        # "pending" = still initializing (don't alert — not failed yet).
        # "disabled" = intentionally off (don't alert — user choice).
        # Only alert on "failed" or "needs-auth" — definitive failures.
        non_failed: set[str] = set()
        failed_servers: list[dict] = []
        for server in mcp_servers:
            name = server.get("name", "")
            status = server.get("status", "")
            if status in ("connected", "pending", "disabled"):
                non_failed.add(name)
            elif status in ("failed", "needs-auth"):
                failed_servers.append(server)

        # Compare: which configured MCPs are definitively missing/failed?
        missing = self._configured_mcps - non_failed
        if not missing:
            logger.info(
                "session_unit.mcp_health_ok session_id=%s configured=%d ok=%d",
                self.session_id, len(self._configured_mcps),
                len(non_failed),
            )
            return None

        # Build warning message
        missing_names = sorted(missing)
        names_str = ", ".join(missing_names)

        # Try to identify a remediation hint from error messages
        hints: set[str] = set()
        for server in failed_servers:
            if server.get("name") in missing:
                error = server.get("error", "")
                if "midway" in error.lower() or "auth" in error.lower():
                    hints.add("mwinit -s")
                elif "enoent" in error.lower() or "not found" in error.lower():
                    hints.add("check MCP binary path")
                elif "timeout" in error.lower() or "connect" in error.lower():
                    hints.add("check network connectivity")

        msg = f"⚠️ MCP servers failed to load: {names_str}. These tools are unavailable this session."
        if hints:
            msg += f" Try: {'; '.join(sorted(hints))}."

        logger.warning(
            "session_unit.mcp_health_warning session_id=%s missing=%s",
            self.session_id, missing_names,
        )

        return {
            "type": "mcp_health_warning",
            "level": "warn",
            "message": msg,
            "missing_servers": missing_names,
        }

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
        _alive = 0
        try:
            from . import session_registry
            router = session_registry.session_router
            if router:
                _alive = router.alive_count
        except Exception:
            pass  # Best-effort — penalty is 0 if registry unavailable
        budget = resource_monitor.spawn_budget(alive_count=_alive)
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

        # ── Capture configured MCP names for post-spawn health check ──
        # options.mcp_servers is a dict when MCPs are configured.
        # Store the names so the health check can compare against
        # what the CLI actually connected to.
        if isinstance(options.mcp_servers, dict) and options.mcp_servers:
            self._configured_mcps = set(options.mcp_servers.keys())
            self._mcp_health_checked = False
        else:
            self._configured_mcps = set()

        logger.info(
            "session_unit.spawn session_id=%s pid=%s mcps_configured=%d",
            self.session_id,
            self.pid,
            len(self._configured_mcps),
        )

        # COLD → IDLE (subprocess is alive and ready)
        self._compaction_guard.reset_all()  # Fresh subprocess — full reset
        if self.state == SessionState.COLD:
            self._transition(SessionState.IDLE)

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
                    "parent_tool_use_id": parent_tool_use_id,
                }
                yield msg

            await self._client.query(_multimodal_gen())
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

            await self._client.query(_tool_result_gen())
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
        MESSAGE_TIMEOUT = self._compute_message_timeout()  # Adaptive: scales with context

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
                            self._content_emitted = True
                    elif isinstance(block, ToolUseBlock):
                        # ── Track sub-agent (Agent tool) for progress observability ──
                        if block.name == "Agent" and isinstance(block.input, dict):
                            _agent_label = block.input.get("description") or block.input.get("prompt") or ""
                            self._active_agent_tools[block.id] = {
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
                            yield {
                                "type": "ask_user_question",
                                "toolUseId": block.id,
                                "questions": questions,
                                "sessionId": self.session_id,
                            }
                            self._transition(SessionState.WAITING_INPUT)
                            self.last_used = time.time()
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
                                                self.session_id,
                                            )
                                            break
                                    else:
                                        # perm_task won — ignore, keep draining
                                        pass
                            except Exception as drain_err:
                                logger.warning(
                                    "session_unit: drain after AskUserQuestion "
                                    "failed: %s (session=%s)",
                                    drain_err, self.session_id,
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
                        # ── Clear sub-agent progress when Agent tool completes ──
                        self._active_agent_tools.pop(block.tool_use_id, None)
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
                                self._content_emitted = True
                                yield {
                                    "type": "assistant",
                                    "content": content_blocks,
                                    "model": getattr(message, "model", None),
                                }
                                content_blocks = []
                            yield {"type": "file_changed", "path": _changed_path}
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
                    self._active_agent_tools = {}  # Clear stale sub-agent progress
                    num_turns = getattr(message, "num_turns", None)
                    logger.info(
                        "session_unit.turn_limit_reached session_id=%s "
                        "num_turns=%s subtype=%s",
                        self.session_id, num_turns, subtype,
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
                    self._transition(SessionState.IDLE)
                    self.last_used = time.time()
                    # CLI exited after error_max_turns (exit code 1).
                    # Clear process references so next send() knows to
                    # respawn (instead of writing to dead pipe → crash →
                    # retry). Keep _sdk_session_id intact so respawn uses
                    # --resume and preserves conversation context.
                    self._client = None
                    self._wrapper = None
                    # Still emit usage/metadata for this completed segment
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
                    # Yield result event so frontend knows the turn ended
                    yield {
                        "type": "result",
                        "subtype": "turn_limit_reached",
                        "stop_reason": "turn_limit",
                        "session_id": self.session_id,
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
                                    session_id=self.session_id,
                                    source="cli",
                                    input_tokens=usage.get("input_tokens") or 0,
                                    output_tokens=usage.get("output_tokens") or 0,
                                    cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                    cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                    cost_usd=getattr(message, "total_cost_usd", None),
                                    model=self._model_name,
                                )
                            )
                        except Exception:
                            pass  # fire-and-forget — never break streaming

                    for meta_event in self._emit_post_stream_metadata(
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

                    if self._interrupted:
                        self._interrupted = False
                        self._transition(SessionState.IDLE)
                        self.last_used = time.time()
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
                        self.session_id, is_error, subtype, error_text,
                    )
                    friendly, suggested = _sanitize_sdk_error(error_text)
                    yield _build_error_event(
                        code="SDK_ERROR", message=friendly, suggested_action=suggested,
                    )
                    # Clear stale sub-agent progress (matches
                    # turn_limit_reached and interrupt paths).
                    self._active_agent_tools = {}
                    # Transition to IDLE — session is not broken, just this
                    # turn failed.  User can retry.  Matches the interrupted
                    # path (line ~2484) which also transitions to IDLE.
                    self._transition(SessionState.IDLE)
                    self.last_used = time.time()
                    # If CLI subprocess died (the error it returned may have
                    # been its dying gasp), clear client refs so next send()
                    # respawns instead of writing to a dead pipe.
                    # _sdk_session_id is preserved for --resume on respawn.
                    if self._pid:
                        try:
                            os.kill(self._pid, 0)  # signal 0 = liveness check
                        except (ProcessLookupError, OSError):
                            # Process is dead — clear refs
                            self._client = None
                            self._wrapper = None
                    # Still track usage for cost accounting (even failed
                    # turns consume input tokens for the prompt).
                    self._lifecycle_response_count += 1
                    usage = getattr(message, "usage", None) or {}
                    logger.info(
                        "session_unit.result_usage session_id=%s "
                        "raw_usage=%s input_tokens=%s model=%s "
                        "lifecycle_response=%d (ERROR path)",
                        self.session_id,
                        usage,
                        usage.get("input_tokens") if usage else None,
                        self._model_name,
                        self._lifecycle_response_count,
                    )
                    # Yield result event so frontend knows the turn ended
                    yield {
                        "type": "result",
                        "subtype": "sdk_error",
                        "stop_reason": "error",
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
                    # Persist token usage (fire-and-forget)
                    if usage:
                        try:
                            import database
                            asyncio.get_running_loop().create_task(
                                database.db.record_token_usage(
                                    session_id=self.session_id,
                                    source="cli",
                                    input_tokens=usage.get("input_tokens") or 0,
                                    output_tokens=usage.get("output_tokens") or 0,
                                    cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                    cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                    cost_usd=getattr(message, "total_cost_usd", None),
                                    model=self._model_name,
                                )
                            )
                        except Exception:
                            pass  # fire-and-forget
                    # Emit context metadata so frontend updates context
                    # ring/bar — especially important for timeout errors
                    # that correlate with large contexts.
                    for meta_event in self._emit_post_stream_metadata(
                        usage, num_turns=getattr(message, "num_turns", 1) or 1,
                    ):
                        yield meta_event
                    return

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
                        self.session_id, cache_create,
                    )

                stop_reason = getattr(message, "stop_reason", None) or ""
                subtype = getattr(message, "subtype", "") or ""
                yield {
                    "type": "result",
                    "subtype": subtype,
                    "stop_reason": stop_reason,
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

                # ── Persist token usage (fire-and-forget) ─────────
                if usage:
                    try:
                        import database
                        asyncio.get_running_loop().create_task(
                            database.db.record_token_usage(
                                session_id=self.session_id,
                                source="cli",
                                input_tokens=usage.get("input_tokens") or 0,
                                output_tokens=usage.get("output_tokens") or 0,
                                cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                cost_usd=getattr(message, "total_cost_usd", None),
                                model=self._model_name,
                            )
                        )
                    except Exception:
                        pass  # fire-and-forget — never break streaming

                # ── Context usage & metadata bridge ────────────────
                result_num_turns = getattr(message, "num_turns", 1) or 1
                for meta_event in self._emit_post_stream_metadata(
                    usage, num_turns=result_num_turns,
                ):
                    yield meta_event

                # ── Health sensor: record turn metrics ─────────────
                # Duration and fresh RSS feed the self-healing system.
                # Uses get_process_rss_mb() for real-time sampling instead
                # of _peak_tree_rss_bytes (which only updates every 60s via
                # LifecycleManager). Falls back to peak if sampling returns 0.
                turn_duration = getattr(message, "duration_ms", 0) or 0
                fresh_rss = get_process_rss_mb(self._pid) if self._pid else 0
                turn_rss = fresh_rss or (self._peak_tree_rss_bytes // (1024 * 1024))
                self._health_sensor.record_turn(
                    latency_ms=float(turn_duration),
                    rss_mb=turn_rss,
                    had_error=False,
                )

                # ── MCP health check (first response only) ────────
                if not self._mcp_health_checked and self._configured_mcps:
                    try:
                        mcp_warning = await self._check_mcp_health()
                        if mcp_warning:
                            yield mcp_warning
                    except Exception as mcp_exc:
                        logger.debug(
                            "session_unit.mcp_health_check_error "
                            "session_id=%s: %s",
                            self.session_id, mcp_exc,
                        )

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

                # ── API empty response detection (any duration) ───────
                # Catches: Bedrock 429/503/timeout that returns a
                # ResultMessage with output_tokens=0 and no content
                # emitted.  The fast-empty guard above catches subprocess
                # corruption (<2s).  This catches API-level failures that
                # take longer (e.g. connection held open then dropped).
                # Raising triggers the existing retry loop in send().
                output_tok = (usage.get("output_tokens") or 0) if usage else 0
                if (
                    not self._content_emitted
                    and not is_error
                    and not self._interrupted
                    and output_tok == 0
                    and not subtype  # empty subtype = API didn't respond
                ):
                    logger.warning(
                        "session_unit.api_empty_response session_id=%s "
                        "duration=%.1fs output_tokens=0 subtype='%s' — "
                        "raising for retry",
                        self.session_id,
                        streaming_dur or 0,
                        subtype,
                    )
                    raise RuntimeError(
                        f"API returned empty response (output_tokens=0, "
                        f"duration={(streaming_dur or 0):.1f}s) — likely "
                        f"transient 429/503/timeout "
                        f"(session_id={self.session_id})"
                    )

                self._transition(SessionState.IDLE)
                self.last_used = time.time()
                self._retry_count = 0

                # ── Proactive RSS check (Trigger B: post-turn) ────
                # Now in IDLE — check if process tree RSS is too high.
                # If so, compact → kill → lazy resume on next send().
                try:
                    await self._check_rss_and_proactive_restart()
                except Exception as rss_exc:
                    logger.debug(
                        "session_unit.post_turn_rss_check failed "
                        "(non-fatal): %s", rss_exc,
                    )

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

    def schedule_pipe_flush(
        self, loop: asyncio.AbstractEventLoop, cleanup_coro=None,
    ) -> None:
        """Schedule a background pipe flush and track the task.

        Called by the router layer after ``recover_from_disconnect()``.
        Stores the task reference so ``send()`` can cancel it if the user
        immediately sends a new message.

        Parameters
        ----------
        loop:
            The running event loop to schedule on.
        cleanup_coro:
            Optional coroutine to use as the task body.  If None, calls
            ``self.flush_subprocess_pipe()`` directly.  The router passes
            its own wrapper that adds error suppression.
        """
        coro = cleanup_coro if cleanup_coro is not None else self.flush_subprocess_pipe()
        task = loop.create_task(coro)
        self._pipe_flush_task = task

    async def flush_subprocess_pipe(self, timeout: float = 3.0) -> None:
        """Interrupt the CLI subprocess to flush stale pipe events.

        Called after ``recover_from_disconnect()`` as a background task.
        The unit is IDLE; the subprocess may still be running a tool
        whose stdout output would contaminate the next ``send()``.

        Bypasses ``interrupt()`` which is state-gated on STREAMING.
        If the client interrupt times out, kills the subprocess for
        a clean respawn on next ``send()``.

        Generation-guarded: if ``send()`` starts (advancing
        ``_send_generation``) between our state check and the actual
        interrupt call, we bail out — the new stream owns the subprocess.
        """
        if self.state != SessionState.IDLE or self._client is None:
            return

        # Capture generation — if send() starts while we're awaiting,
        # generation advances and we must NOT interrupt the new stream.
        gen_at_entry = self._send_generation

        try:
            await asyncio.wait_for(self._client.interrupt(), timeout=timeout)

            # Post-interrupt generation check: if send() started during
            # the await, our interrupt hit the new stream — log but don't
            # escalate (send() already handles the recovery).
            if self._send_generation != gen_at_entry:
                logger.info(
                    "session_unit.flush_pipe session_id=%s — send() started "
                    "during flush (gen %d→%d), skipping state changes",
                    self.session_id, gen_at_entry, self._send_generation,
                )
                return

            logger.info(
                "session_unit.flush_pipe session_id=%s — pipe flushed",
                self.session_id,
            )
        except asyncio.CancelledError:
            # Cancelled externally (e.g. timeout wrapper or session teardown).
            logger.info(
                "session_unit.flush_pipe session_id=%s — cancelled externally",
                self.session_id,
            )
            return
        except asyncio.TimeoutError:
            # Only kill if no new send() has started
            if self._send_generation != gen_at_entry:
                logger.info(
                    "session_unit.flush_pipe session_id=%s — timeout but "
                    "send() started (gen %d→%d), not killing",
                    self.session_id, gen_at_entry, self._send_generation,
                )
                return
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
        self._active_agent_tools = {}  # Clear stale sub-agent progress on interrupt

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
            # Clear interrupted flag — without this, a stale _interrupted=True
            # could contaminate the next send()'s _read_formatted_response if
            # it somehow checks before send()'s Layer 0 clears it.
            self._interrupted = False
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
        self, answer: str, tool_use_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """Continue after ask_user_question.

        State: WAITING_INPUT → STREAMING → IDLE/WAITING_INPUT.

        Yields raw SDK messages for the router to format.

        Args:
            answer: JSON-encoded answer text from the user.
            tool_use_id: The AskUserQuestion tool_use block ID. When provided,
                the answer is sent with ``parent_tool_use_id`` so the CLI links
                it back to the correct tool call (required for the SDK to treat
                the answer as a tool result rather than a new user message).
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
        logger.info(
            "session_unit.continue_with_answer session_id=%s "
            "tool_use_id=%s answer_len=%d state=%s",
            self.session_id, tool_use_id, len(answer), self.state.value,
        )
        self._compaction_guard.reset()
        self._content_emitted = False  # Reset zombie detection for new stream
        self._active_agent_tools = {}  # Clear stale sub-agent progress
        self._transition(SessionState.STREAMING)

        try:
            async for event in self._stream_response(
                answer, parent_tool_use_id=tool_use_id,
            ):
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
        self._active_agent_tools = {}  # Clear stale sub-agent progress
        self._transition(SessionState.STREAMING)

        try:
            async for event in self._read_formatted_response():
                yield event
        except Exception:
            await self._crash_to_cold_async(clear_identity=False)
            raise

    async def reclaim_for_mcp_swap(self, mcp_name: Optional[str] = None) -> None:
        """Kill subprocess to prepare for MCP hot-swap.

        Called when the session needs a different set of MCP servers.
        Kills the current subprocess (IDLE → COLD), so the next
        ``send()`` call will spawn a fresh subprocess with the new
        MCP configuration.

        If *mcp_name* is provided, it's added to ``_extra_mcps`` so
        ``load_mcp_config_tiered(extra_always=...)`` forces it to load
        on the next spawn — regardless of its tier in mcp-dev.json.

        State: IDLE → DEAD → COLD.
        Raises RuntimeError if not in IDLE state.
        """
        if self.state != SessionState.IDLE:
            raise RuntimeError(
                f"Cannot reclaim for MCP swap in state {self.state.value} "
                f"(session_id={self.session_id})"
            )
        if mcp_name:
            self._extra_mcps.add(mcp_name)
            logger.info(
                "Session %s: added '%s' to extra_mcps (total: %s)",
                self.session_id, mcp_name, self._extra_mcps,
            )
        await self.kill()

    # ── Proactive RSS-based restart ────────────────────────────────
    # Threshold and cooldown for proactive compact→kill cycle.
    # When a single session's process tree RSS exceeds this threshold
    # while IDLE, we compact (to generate a checkpoint), then kill
    # the subprocess.  The next send() will lazy-restart with --resume.
    # This prevents macOS jetsam from OOM-killing the entire backend.
    # Threshold: 1.8GB.  Normal steady-state is 1.4-1.6GB (verified from
    # lifecycle_manager logs 2026-04-12).  Old 1.2GB was below steady-state,
    # causing every session to be proactively killed after each response.
    PROACTIVE_RSS_THRESHOLD: int = 1_800_000_000  # 1.8GB
    PROACTIVE_COOLDOWN: float = 180.0  # 3 minutes
    # STREAMING RSS kill threshold: if a STREAMING session exceeds this,
    # lifecycle_manager kills it immediately.  Closes the adaptive timeout
    # blind spot (up to 900s) where proactive_rss_restart can't act.
    STREAMING_RSS_KILL_THRESHOLD: int = 3_000_000_000  # 3GB

    async def _check_rss_and_proactive_restart(self) -> None:
        """Proactive restart: if tree RSS > threshold, compact → kill.

        Called after each agent turn completes (STREAMING → IDLE) and
        by LifecycleManager's 60s maintenance loop.

        Sequence:
        1. Check cooldown — skip if last restart was < 3 minutes ago
        2. Measure process tree RSS via psutil
        3. If above threshold: compact() → kill()
        4. Unit ends in COLD state with _sdk_session_id preserved
        5. Next send() lazy-restarts with --resume

        Non-fatal — if compact() fails, kill() still proceeds.
        If RSS measurement fails, silently skips.
        """
        if time.monotonic() - self._last_proactive_restart < self.PROACTIVE_COOLDOWN:
            return

        pid = self.pid
        if not pid:
            return

        try:
            from .resource_monitor import resource_monitor
            loop = asyncio.get_running_loop()
            tree_rss = await loop.run_in_executor(
                _subprocess_executor,
                resource_monitor.process_tree_rss, pid,
            )
        except Exception:
            return  # psutil failure — skip silently

        if tree_rss <= self.PROACTIVE_RSS_THRESHOLD:
            return

        logger.warning(
            "session_unit.proactive_restart session_id=%s "
            "tree_rss=%dMB > threshold=%dMB — compact → kill → lazy resume",
            self.session_id,
            tree_rss // (1024 * 1024),
            self.PROACTIVE_RSS_THRESHOLD // (1024 * 1024),
        )

        # Step 1: compact to generate checkpoint (best-effort, 30s timeout)
        # compact() internally calls client.query() + receive_response()
        # with no timeout — if CLI hangs during compact, this would block
        # the entire proactive restart (and maintenance loop if called from
        # lifecycle_manager).  30s is generous for a summarization call.
        try:
            await asyncio.wait_for(self.compact(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(
                "session_unit.proactive_restart compact timed out "
                "session_id=%s — proceeding to kill",
                self.session_id,
            )
        except Exception as exc:
            logger.warning(
                "session_unit.proactive_restart compact failed "
                "session_id=%s: %s — proceeding to kill",
                self.session_id, exc,
            )

        # Step 2: kill → COLD (preserves _sdk_session_id for lazy resume)
        await self.kill()

        self._last_proactive_restart = time.monotonic()

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

        Safe to call multiple times or from any state.  Tolerates
        concurrent kills — if another coroutine transitions the state
        during ``await _force_kill()``, we skip the redundant transition
        instead of raising RuntimeError.
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

        L2 FIX: Tree snapshot and kill sweep use subprocess.run / os.kill
        (blocking I/O).  Previously ran directly in this async method,
        starving health checks and aiosqlite on the default thread pool.
        Now runs on _subprocess_executor (dedicated pool) to prevent
        priority inversion with the default executor.
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
                    # Snapshot-then-kill: enumerate the ENTIRE tree first,
                    # then kill bottom-up in one sweep + kill parent last.
                    # This prevents the reparenting race where killing a
                    # middle node (zsh) orphans its children (pytest/workers).
                    #
                    # Offload to dedicated subprocess executor:
                    # _snapshot_descendant_tree calls subprocess.run which
                    # blocks. Using _subprocess_executor (not default pool)
                    # prevents priority inversion with health/aiosqlite.
                    loop = asyncio.get_running_loop()
                    tree = await loop.run_in_executor(
                        _subprocess_executor, _snapshot_descendant_tree, pid
                    )
                    # Offload kill sweep to same executor — 17× os.kill can
                    # take non-trivial time under process pressure.
                    tree_killed = await loop.run_in_executor(
                        _subprocess_executor, _kill_pids, tree
                    )
                    # Kill parent last (after all children are dead)
                    os.kill(pid, signal.SIGKILL)
                    logger.info(
                        "session_unit.force_kill_tree session_id=%s pid=%d "
                        "tree_size=%d tree_killed=%d (shared pgid=%d)",
                        self.session_id, pid, len(tree), tree_killed, pgid,
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

        # Poll for process exit instead of relying on fixed sleeps downstream.
        # SIGKILL is immediate on macOS/Linux but the OS needs time to reap
        # the zombie entry and release file locks (e.g. session lock file).
        if pid:
            await self._await_process_exit(pid, timeout=3.0)

        # Also try graceful wrapper cleanup
        if self._wrapper is not None:
            try:
                await asyncio.wait_for(
                    self._wrapper.__aexit__(None, None, None),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Wrapper __aexit__ timed out after 10s for session %s",
                    self.session_id,
                )
            except Exception:
                logger.debug(
                    "Wrapper cleanup error for session %s (expected)",
                    self.session_id,
                )

    @staticmethod
    async def _await_process_exit(pid: int, timeout: float = 3.0) -> None:
        """Poll for process exit with backoff, up to *timeout* seconds.

        Replaces fixed ``asyncio.sleep(1.5)`` cooldowns with a deterministic
        check: on fast machines exits in <10ms, on slow/loaded machines waits
        up to *timeout* before giving up (non-fatal — the process is already
        SIGKILLed so it *will* die, we just can't confirm reap timing).
        """
        deadline = asyncio.get_event_loop().time() + timeout
        interval = 0.05  # start at 50ms, double each poll
        while asyncio.get_event_loop().time() < deadline:
            try:
                os.kill(pid, 0)  # 0-signal = existence check
            except ProcessLookupError:
                return  # process fully reaped — safe to proceed
            except PermissionError:
                return  # pid recycled to another user — original is gone
            await asyncio.sleep(interval)
            interval = min(interval * 2, 0.5)  # cap at 500ms between polls
        # Timeout — process still shows as alive (zombie or slow reap).
        # Not fatal: SIGKILL is delivered, locks will release momentarily.
        logger.debug(
            "session_unit._await_process_exit: pid %d still visible after %.1fs "
            "(zombie reap pending — proceeding anyway)",
            pid, timeout,
        )

    def _cleanup_internal(self) -> None:
        """Reset transient subprocess fields after subprocess death.

        Called during DEAD → COLD transition.  Clears client, wrapper,
        and subprocess-specific state so the unit is ready for reuse.

        Preserves ``_sdk_session_id`` so that evicted units can resume
        via ``--resume`` when the user returns to the tab.

        IMPORTANT: Does NOT reset ``_retry_count``.  This method is
        called inside retry loops (via ``_crash_to_cold_async``).
        Resetting the counter here caused an infinite retry loop
        (COE: 2026-04-02 retry counter reset bug — 26 retries in 4min
        instead of capping at MAX_RETRY_ATTEMPTS=3).  The retry counter
        is reset only at ``send()`` entry and on successful completion.
        """
        self._client = None
        self._wrapper = None
        self._interrupted = False
        # NOTE: self._retry_count is intentionally NOT reset here.
        # It is reset in send() (line ~620) and on success (line ~1672).
        self._model_name = None
        self._peak_tree_rss_bytes = 0
        # Don't reset _lifecycle_response_count — it tracks across the
        # full unit lifetime (resume awareness persists through kill/restart).
        # Reset channel history injection flag — the new subprocess
        # won't have any conversation history, so it needs re-injection.
        self._channel_history_injected = False
        # Reset recall injection flag — new subprocess needs fresh recall.
        self._recall_injected = False

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

    async def force_unstick_waiting_input(self) -> None:
        """Force a stuck WAITING_INPUT session back to COLD.

        When the frontend crashes after receiving an ask_user_question
        event, the user can never submit the answer. The session stays
        stuck in WAITING_INPUT forever, blocking all subsequent sends.

        This method kills the subprocess and transitions to COLD,
        preserving ``_sdk_session_id`` so the next ``send()`` resumes
        the conversation via ``--resume``.
        """
        if self.state != SessionState.WAITING_INPUT:
            return
        logger.warning(
            "session_unit.force_unstick_waiting_input session_id=%s pid=%s "
            "— frontend never answered, forcing COLD for recovery",
            self.session_id,
            self.pid,
        )
        await self._crash_to_cold_async(clear_identity=False)

    async def refresh_context(self) -> None:
        """User-triggered context refresh — kill subprocess for resume.

        Same mechanism as force_unstick but explicitly user-initiated.
        Preserves _sdk_session_id so the next send() triggers --resume
        with structured context injection (50-100K tokens of conversation
        summary). Works from any non-STREAMING state.

        After this call, state = COLD and next send() will auto-resume.
        """
        logger.info(
            "session_unit.refresh_context session_id=%s state=%s "
            "— user-triggered context refresh",
            self.session_id,
            self.state.value if self.state else "None",
        )
        if self.state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            raise RuntimeError(
                "Cannot refresh while streaming"
                if self.state == SessionState.STREAMING
                else "Cannot refresh while waiting for user input"
            )
        # If already COLD (no subprocess), nothing to kill — just return
        if self.state == SessionState.COLD:
            return
        await self._crash_to_cold_async(clear_identity=False)

    def clear_session_identity(self) -> None:
        """Clear ``_sdk_session_id`` so the unit cannot resume.

        Called by ``SessionRouter.disconnect_all()`` after ``kill()``
        to ensure shutdown fully cleans up session identity.
        """
        self._sdk_session_id = None
