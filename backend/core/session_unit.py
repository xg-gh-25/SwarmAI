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

The CLI's message stream has ONE consumer per live client, a
``core.cli_stream_reader.CliStreamReader`` started in ``_spawn()`` and closed in
``_cleanup_internal()``. Each turn reads its own ``TurnStream`` from it: a user
send, compact or leak continuation claims its turn before writing
(``streaming_orchestrator.write_user_message``); a turn the CLI starts on its
own (a background task finished) is queued and run by the router's sink
through ``begin_injected_turn`` (IDLE → STREAMING, the same exit as a user
turn). ``send()`` writes only while the CLI is quiescent (``cli_quiescent``).
The ``interrupt()``, ``continue_with_answer()``, ``continue_with_permission()``,
and ``compact()`` methods are added by task 3.3.

No prompt-building, routing, or hook-execution logic lives here.

Design reference:
    ``.kiro/specs/multi-session-rearchitecture/design.md`` §1 SessionUnit
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
import traceback
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional

from .compaction_guard import EscalationLevel
from .retry_manager import RetryManager
from .streaming_orchestrator import StreamingOrchestrator
from .turn_phase import (
    STARTING as TURN_PHASE_STARTING,
    WAITING as TURN_PHASE_WAITING,
    turn_phase_event,
)
from .session_healing import (
    CHANNEL_WRAP_BUFFER,
    CHANNEL_WRAP_UP_PROMPT,
    DESKTOP_MAX_TURNS,
    HARD_FLOOR_BUFFER,
    WRAP_UP_PROMPT,
    HealthSensor,
    HealingLoop,
    RecoveryCoordinator,
    RecoveryVerdict,
    TaskCheckpoint,
    build_rich_checkpoint,
    is_self_heal_enabled,
    release_canary,
)

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


# ── Expected-kill log classification ──────────────────────────────────────
# Every refresh / release / orphan reap / retry recycle SIGKILLs the CLI via
# _force_kill, and the SDK's message reader then logs
# "ERROR Fatal error in message reader: Command failed with exit code -9".
# Those ERRORs are our own doing (16 in one day, all intentional) and bury the
# real ones. Downgrade that one SDK record to INFO when we SIGKILLed a CLI
# moments before; an unexplained -9 (jetsam/OOM/external kill) stays ERROR.
# A bounded ring of kill timestamps — log classification only, not session
# state (nothing reads it per session; it can only lower one log level).
from collections import deque as _deque

_INTENTIONAL_KILL_WINDOW_S = 10.0

#: Hang guard for ClaudeSDKClient.stop_task (one sub-agent): the control request
#: normally answers at once; past this it is reported as not stopped.
STOP_TASK_TIMEOUT_S = 5.0
_recent_intentional_kills: "_deque[float]" = _deque(maxlen=32)


def _note_intentional_kill() -> None:
    _recent_intentional_kills.append(time.monotonic())


class _ExpectedKillLogFilter(logging.Filter):
    """Downgrade the SDK's reader ERROR for a CLI we just SIGKILLed on purpose."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR and "exit code -9" in record.getMessage():
            now = time.monotonic()
            match = next(
                (t for t in _recent_intentional_kills if now - t <= _INTENTIONAL_KILL_WINDOW_S),
                None,
            )
            if match is not None:
                # One kill excuses ONE error: a real jetsam/OOM -9 in another
                # session right after our kill must still surface as ERROR.
                try:
                    _recent_intentional_kills.remove(match)
                except ValueError:
                    pass  # consumed concurrently by another record — fine
                record.levelno = logging.INFO
                record.levelname = "INFO"
                record.msg = f"{record.getMessage()} (expected: SwarmAI killed this CLI on purpose)"
                record.args = None
        return True


logging.getLogger("claude_agent_sdk._internal.query").addFilter(_ExpectedKillLogFilter())

# Dedicated pool for HEAVY psutil process-tree walks (process_tree_rss ~= 107ms
# each, measured run_409392d4). The maintenance loop gathers these per-session
# RSS reads concurrently; a dedicated pool keeps that gather burst from
# saturating subprocess_executor and starving force_kill's tree-snapshot/kill
# sweep (which stays on subprocess_executor). max_workers bounds how many
# concurrent psutil walks run at once (CPU/mem), sized for the max concurrent
# session count with headroom.
rss_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="rss"
)
_rss_executor = rss_executor

# ── Load-amplifier caps (Root 2) ───────────────────────────────────
# Context-ring warning threshold (context %). ORIGINALLY the proactive
# soft-compaction trigger; that path was REMOVED (run_2b1957f8) because holding
# _client_io for a 300s /compact at 60% froze the user's next send() at the turn
# boundary. The constant is RETAINED as the log-level boundary for
# context_ring_debug (WARN at/above this %, INFO below) — a pure observability
# signal, no compaction. Context is now managed by CLI autocompact + manual
# refresh. The HARD notice (~85%, "start a new tab") lives in
# prompt_builder.build_context_warning (AC2).
SOFT_COMPACT_PCT: int = 60

# Long single-turn heartbeat: emit a "still working" notice every interval once
# a step's wall-clock exceeds this, so a legitimately long/slow step reads as
# EXPECTED rather than a hang (reduces frontend "mark idle" desync + SSE-blip
# disconnects), and the user can make an informed decision to Stop a genuine
# hang in seconds instead of staring at a dead spinner.
# # assumes: 60s is short enough that a stuck tool (e.g. an unbounded grep) is
# visibly flagged quickly, but long enough that normal fast tools never bubble.
# This is a VISIBILITY signal only — it never kills anything (no false-kill
# risk). Auto-abort of provably-stuck tools IS implemented separately: see the
# "Tool-aware hang tier v2 — CPU-liveness gated (run_fb6e94a9)" constants below
# (TOOL_HANG_OPEN_S / CPU_LIVE_EPSILON / TOOL_HANG_HARD_CEILING_S) and the PID
# watchdog. That path warm-interrupts a tool whose process tree burns ~0 CPU and
# works headless (no human required) — this heartbeat is the visibility layer
# on top of it, not a substitute for it.
LONG_TURN_HEARTBEAT_S: float = 60.0

# SOFT_COMPACT_COOLDOWN / SOFT_COMPACT_HANG_S / SOFT_COMPACT_FAIL_BACKOFF REMOVED
# (run_2b1957f8) — they only parameterized the deleted proactive soft-compact
# path. Context is now managed by CLI autocompact + manual refresh.

# Module-level lock that serializes subprocess spawn operations.
# Held during _configure_claude_environment + wrapper.__aenter__() to
# prevent concurrent sessions from racing on os.environ mutations.
# INTENTIONALLY module-level (not per-instance): os.environ is process-global,
# so ALL spawns across ALL SessionUnit instances must serialize.
# If you need multiple SessionRouter instances (e.g., tests), mock this lock.
_spawn_lock = asyncio.Lock()


def _enable_stream_reader_options(options: Any) -> None:
    """Turn on the two CLI outputs the stream reader routes by.

    - ``--replay-user-messages``: the CLI echoes every user message we write,
      with our uuid, where it consumes it — how the reader tells which turn a
      message landed in (core/cli_stream_reader.py).
    - ``CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1``: ``session_state_changed``
      frames (running / idle / requires_action) reach the stream instead of
      being kept inside the SDK; ``idle`` is the quiescence signal.

    Set on the options of every spawn (the reader exists for every spawned
    client), per subprocess via ``options.env`` rather than the daemon's env.
    """
    extra = dict(getattr(options, "extra_args", None) or {})
    extra["replay-user-messages"] = None
    options.extra_args = extra
    env = dict(getattr(options, "env", None) or {})
    env["CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"] = "1"
    options.env = env

# ── Resume-degrade observability (run_af851c26) ──────────────────────────────
# The persisted-`_sdk_session_id` fast-resume path can strand a history-bearing
# tab when the CLI transcript is gone (long downtime / ~/.claude tmp cleanup /
# host reboot) → --resume fails session-not-found. The OLD fallback silently
# cold-spawned a HISTORY-LESS subprocess (options built on a non-cold-resume
# turn) → the entire conversation was lost for that subprocess's life, with NO
# signal. This counter makes that data-loss-recovery event LOUD (mirrors
# context_injector._resume_stats). Bumped ONLY in the session-not-found fallback
# branch of _ensure_spawned — NOT in _crash_to_cold_async (called from ~6
# unrelated sites; would over-count every refresh/give-up — Gate-1 Check5).
_resume_degrade_count: dict[str, int] = {
    # --resume hit session-not-found; we dropped the stale id + re-armed a true
    # cold-resume so the DB history is re-injected next turn (recovered, not lost).
    "session_not_found_recovered": 0,
}


def get_resume_degrade_count() -> dict[str, int]:
    """Read the resume-degrade counters (test/observability)."""
    return dict(_resume_degrade_count)


def reset_resume_degrade_count() -> None:
    """Reset counters (test isolation)."""
    for k in _resume_degrade_count:
        _resume_degrade_count[k] = 0

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

# ── R6 Step B: concurrent-streaming admission cap (peak-OOM guard) ──────────
# The two-limit split (design §9.4): spawn_budget governs how many sessions may
# EXIST (idle ≈ 600-800MB → cheap → follows real RAM, NO ceiling). This separate
# cap governs how many may STREAM AT ONCE (streaming ≈ 2GB peak → expensive →
# locked). They are ORTHOGONAL — Step B does NOT touch spawn_budget's penalty
# (R6a lesson: _CONCURRENT_PENALTY_FACTOR IS the COE05 simultaneous-peak floor;
# changing it reopens COE05). 3027ea6c failed by conflating both into one number.
#
# _streaming_count is the single source of truth, mutated ONLY inside
# _transition() (the one sync state chokepoint — verified: :779 self.state= is
# the ONLY direct assignment, so every STREAMING entry/exit, incl kill/crash/
# disconnect, routes through it → the counter cannot leak). A new send() turn
# waits (poll) when the count is at the cap; continue_with_answer / permission /
# retry paths COUNT toward the cap but are NEVER blocked (a user waiting on their
# own answer must not queue behind others). Poll (not Event) is deliberate:
# _transition is sync, and an Event notify from sync code is the lost-wakeup race
# that bit run_002eca4c. A 100ms poll on a rare (>cap concurrent) condition is
# negligible and has zero lost-wakeup surface.
_streaming_count: int = 0


def _get_streaming_count() -> int:
    """Current number of sessions in STREAMING state (daemon-wide)."""
    return _streaming_count


# Threshold for a "dumb spawn": a subprocess that entered STREAMING but
# produced ZERO SDK events since spawn (_last_event_time is None) — alive
# but silent, with no open tool_use. This is distinct from slow inference
# (events flowing, just slow) which the adaptive 600-1800s
# _compute_message_timeout legitimately tolerates. A dumb spawn produces
# nothing — not even a first token — so it must be recovered on a much
# shorter window. Resume spawns get 2x (they replay the full conversation
# before the first token — see GUI66), still far below the 1800s ceiling.
# Evidence: pid 33855 / session 89b71059 (2026-06-25) — STREAMING with zero
# events for 15+ min; the only backstop was the 1800s adaptive timeout, so
# the frontend spinner spun indefinitely (run_6c482b10).
DUMB_SPAWN_TIMEOUT_SECONDS: float = 120.0


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
    def _via_psutil() -> dict[int, int]:
        table: dict[int, int] = {}
        try:
            import psutil

            for proc in psutil.process_iter(attrs=["pid", "ppid"]):
                info = proc.info
                table[int(info["pid"])] = int(info["ppid"])
        except (ImportError, OSError):
            return {}
        except psutil.Error:
            pass
        return table

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
        if table:
            return table
        if result.returncode != 0:
            # Sandboxed macOS runtimes can deny /bin/ps while still allowing the
            # native process APIs used by psutil. Keep cleanup functional there.
            return _via_psutil()
        return table
    except subprocess.TimeoutExpired:
        return {}
    except (FileNotFoundError, OSError):
        return _via_psutil()


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
        logger.debug("_is_oom_signal: suppressed exception on a fail-safe path", exc_info=True)
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
    on_cli_event:
        Optional callback for what the CLI does on its own, between turns.
        Signature: ``(session_id, kind) -> None`` with kind ``"injected"`` (a
        CLI-initiated turn is queued, see ``take_injected_turn``) or ``"idle"``
        (the CLI reported idle: pending messages may be delivered). Called from
        the stream reader's task; must not block.
    """

    def __init__(
        self,
        session_id: str,
        agent_id: str,
        *,
        on_state_change: Optional[
            Callable[[str, SessionState, SessionState], None]
        ] = None,
        on_cli_event: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        # ── Public identity ──────────────────────────────────────
        self.session_id: str = session_id
        self.agent_id: str = agent_id
        self.state: SessionState = SessionState.COLD
        self.created_at: float = time.time()
        self.last_used: float = time.time()
        # True when this unit serves channel conversations (Slack, etc.)
        # Channel units use a dedicated slot pool, separate from chat tabs.
        # NOTE: this is deliberately False for an OWNER messaging via a channel
        # (owner uses the chat pool for routing) — do NOT use it as a
        # "trusted local desktop" signal. Use _has_channel_context for that.
        self.is_channel_session: bool = False
        # True when this unit is driven by ANY channel_context (owner OR
        # non-owner). The correct "true local desktop tab" test is
        # `not _has_channel_context` — a desktop tab never carries a
        # channel_context, while owner-over-channel DOES (is_owner=True).
        # Load-bearing for the open-canvas-file abs-path gate: an absolute host
        # path may reach the resolver ONLY on a genuine local desktop session,
        # never on any channel (C041 information-leak defense).
        self._has_channel_context: bool = False
        # True after the first message with history injection has been
        # processed.  Prevents re-injecting on every subsequent message
        # within the same daemon lifecycle (channel resume fix).
        self._channel_history_injected: bool = False

        # ── Internal — not part of public interface ──────────────
        self._client: Optional[ClaudeSDKClient] = None
        self._wrapper: Optional[_ClaudeClientWrapper] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        # Subprocess-IO serialization lock (run_4b74b764). SEPARATE from
        # ``self._lock`` (which is the kill/recovery transaction lock — see
        # kill()). ``_client_io`` serializes the turns that write to the CLI:
        # ``compact()`` holds it across its write and its turn's drain, and the
        # foreground turn takes a SHORT BARRIER at send()'s IDLE-entry
        # (acquire→release, NOT held across the streaming body — that would
        # self-deadlock with the in-loop CompactionGuard interrupt and break user
        # Stop); the injected-turn sink does not start while it is held. The
        # stream itself has one consumer (the CliStreamReader); before it, two
        # concurrent ``receive_response()`` drives split the SDK stream and
        # co-starved (the 3-5min "freeze", run_4b74b764) — a unit without a
        # reader (test fakes) still reads that way.
        # interrupt()/flush stay lock-FREE: they are control-channel calls, and
        # acquiring here would deadlock Stop against the very turn it stops. The two background maintenance ops probe
        # ``_client_io.locked()`` and SKIP (never block) when a turn holds it.
        self._client_io: asyncio.Lock = asyncio.Lock()
        self._sdk_session_id: Optional[str] = None
        self._interrupted: bool = False
        # Durable "this turn was stopped by the user" flag. Unlike _interrupted
        # (which _read_formatted_response clears mid-stream), this survives until
        # the next send()'s Layer 0 reset, so the post-stream self-heal check can
        # tell a user Stop apart from a clean completion. A user Stop must NEVER
        # be followed by a proactive self-heal kill/respawn (that defeats Stop).
        self._user_stopped_current_turn: bool = False
        self._retry_count: int = 0
        # Per-turn SDK spawn (__aenter__ handshake) wall time in ms, for the TTFT
        # probe to attribute cold-start cost away from send+infer (run_8149838f).
        # Set ONLY by _spawn (i.e. only when a cold spawn actually ran); reset to
        # None at the single new-turn admission point in send(), so a warm turn
        # (no spawn) relays None and a prior cold turn's value can never leak into
        # a later turn's log. Pure observability — never read by control flow.
        self._last_turn_spawn_ms: Optional[float] = None
        self._model_name: Optional[str] = None
        # Set at send() from options.cwd (the CLI's working directory).
        self._cwd: Optional[str] = None

        # ── Recall injection (G3: post-first-message) ─────────────
        # Set True after first-message recall runs (or is skipped).
        # Prevents re-running on subsequent messages in the same session.
        self._recall_injected: bool = False
        # TSCC recall snapshot (read-only panel). Initialized here for lifecycle
        # symmetry with _recall_injected so a never-recalled unit has a defined
        # attribute (a future direct read won't AttributeError); the live read in
        # session_router uses getattr(..., None) so None == "no snapshot".
        self._recall_snapshot: Optional[dict] = None
        # 阶段二 prompt-builder 两分: on a WARM-reuse turn recall cannot ride the
        # (discarded) system_prompt, so _maybe_inject_recall stashes the recall
        # block here for send() to prefix onto query_content via
        # _prepend_dynamic_context_to_query. None = no warm recall this turn (cold
        # turn wrote system_prompt instead, or no recall ran). send() clears it at
        # the top of each turn; the router read uses getattr(..., None) too.
        self._recall_query_block: Optional[str] = None
        # resume-context-injection去根 (run_d108b914): on a COLD-resume turn the
        # resume block (prior conversation summary, up to 150K) is stashed here by
        # prompt_builder (when SWARM_RESUME_VIA_QUERY is on) instead of appended to
        # options.system_prompt, so send() can prefix it onto query_content via
        # _prepend_resume_to_query. Riding the query means the #13/#15 fallback
        # (session-not-found → strip resume from options → respawn) keeps the block.
        # None = no resume this turn (steady-state / old system_prompt path). Cleared
        # per-turn by the ROUTER (session_router.send → the transfer-assign
        # `unit._resume_query_block = agent_config.get(...)`, which writes None on a
        # non-cold turn) and on teardown by _cleanup_internal below.
        self._resume_query_block: Optional[str] = None
        # TSCC/security-scan alignment (run_380413c5): the ACTUAL query-channel
        # prefix delivered onto query_content THIS turn — the resume block on a
        # cold-resume turn, or the recall+SENSE block on a warm-reuse turn, or None.
        # Set UNCONDITIONALLY every send by the ROUTER (writes None when no prefix,
        # mirroring _resume_query_block's clear-on-transfer semantics — so a stale
        # prior-turn prefix can never leak into full_text). Read at the EVERY-TURN
        # metadata publish (_emit_post_stream_metadata) to compose
        # full_text = base_system_prompt + separator + this prefix, so the TSCC
        # "System Prompt" modal AND the security-scan panel see the prompt the model
        # actually received (previously full_text = options.system_prompt only, so a
        # query-channel block — up to ~150K resume history, highest PII risk — was
        # invisible to both). Cleared on teardown by _cleanup_internal.
        self._delivered_query_prefix: Optional[str] = None
        # TSCC system-prompt metadata AWAITING DELIVERY. The router stashes the
        # freshly-built metadata here; _spawn() publishes it to
        # session_registry.system_prompt_metadata at the moment the prompt is
        # actually handed to the CLI. Build-time publication was wrong: a warm
        # reuse rebuilds options and DISCARDS them, so publishing at build time
        # replaced turn 1's real prompt with a turn-2 prompt that was never sent.
        # Delivery-time publication makes that structurally impossible.
        self._pending_prompt_metadata: Optional[dict] = None
        # Recall snapshot armed for THIS turn's prompt, republished by _spawn if a
        # recycle inside the same send drops the registry entry. Armed by the
        # router only when recall actually ran this turn — otherwise a respawn on
        # a later turn would resurrect an old snapshot next to a prompt that
        # carries no recall block.
        self._pending_recall_snapshot: Optional[dict] = None
        # Own once-guard for runtime DDD injection (run_91bc0651 M2): separate
        # from _recall_injected so signal-1 (deterministic editor path) fires
        # regardless of the keyword-recall gate.
        self._ddd_injected: bool = False
        # recall#5 cap (run_a16d61ad): a zero-keyword opener no longer burns the
        # guard (so a later substantive message can still recall), but we bound the
        # number of keyword-less turns that re-run _extract_query_keywords, so a
        # session that NEVER produces keywords doesn't pay the regex cost every turn
        # forever. After this many keyword-less turns, latch the guard closed.
        self._recall_keyword_misses: int = 0
        # Shared active-project detection cache (run_6ebf6479): resolved ONCE per
        # recall by _maybe_inject_recall and read by BOTH recall legs, so detection
        # (a blocking iterdir) runs once and both legs agree on the same project.
        # Reset with the other recall guards in _cleanup_internal (fresh subprocess
        # = fresh detection). (project, signal) tuple or None.
        self._active_project: Optional[tuple[Optional[str], str]] = None
        # NOTE: the M2 background-vector-task fields (run_e9b15722) were removed in
        # run_4d06640b — recall now runs both legs synchronously to completion, so
        # there is no background task / pending-result / teardown-cancel to track.

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
        # Tool-call leak poison tracking. _leak_count = leaks in SDK session
        # _leak_sid since its CLI transcript was last cleaned (the orchestrator
        # counts; a new sid resets). From the 2nd leak, send() runs ONE restart
        # per user message: kill → strip leak text from the transcript →
        # --resume → continue. _leak_restart_eligible is True only while
        # send()'s primary stream runs, so continue_with_* / retry streams
        # (which have no restart handler) never raise for it.
        self._leak_sid: Optional[str] = None
        self._leak_count: int = 0
        self._leak_restart_eligible: bool = False
        self._leak_restart_done: bool = False
        # Set by the orchestrator when the CLI history holds something the model
        # must not see again: the glitch prefix (no safe mid-turn restart came),
        # or a degenerate repetition run that was cut off mid-block
        # (core.output_degeneration). The next send() recycles any warm process
        # and cleans the transcript (leak_transcript) before --resume reads it.
        self._transcript_poisoned: bool = False

        # ── Streaming timeout ────────────────────────────────────────
        # Updated on every yielded event during STREAMING.  The
        # LifecycleManager checks this to detect stuck streams that
        # never produced a ResultMessage (e.g. SDK hang, Bedrock timeout).
        self._last_event_time: Optional[float] = None
        # Progress clock (distinct from _last_event_time): advanced ONLY on real
        # content progress (text/thinking deltas, AssistantMessage, sub-agent
        # tool_result), NEVER on framing / SystemMessage(init) / sub-agent noise.
        # streaming_stall_seconds reads THIS so the lifecycle watchdog measures
        # "content stopped flowing" not "the SDK pipe went quiet" — the latter
        # stays fresh on non-content messages and made real freezes undetectable
        # (假 streaming → spinner pinned + _streaming_count slot leak). The
        # any-event _last_event_time is left for the PID watchdog / HealthSensor
        # / dumb-spawn discriminator, whose "any event = subprocess alive"
        # semantics are correct and must stay wide.
        self._last_progress_time: Optional[float] = None
        self._streaming_start_time: Optional[float] = None
        # Usage of the LAST main-agent API request this turn (AssistantMessage.
        # usage, set by the orchestrator). The real current context size — the
        # ResultMessage usage is summed over all requests, and dividing it by
        # num_turns under-reported (a real 904K peak never reached 85% critical).
        self._last_call_usage: Optional[dict] = None
        # The backend's current context-usage snapshot (core.context_usage shape),
        # the ONLY authority behind the chat-input context ring. Stamped with the
        # sdk_session_id it was measured under: once that identity changes or is
        # cleared (refresh, true cold resume, a new CLI session) the snapshot is
        # simply invisible to context_usage_snapshot() — no clear-site has to
        # remember to invalidate it.
        self._context_usage: Optional[dict] = None
        self._context_usage_sdk_id: Optional[str] = None
        # True once a snapshot was recorded during the current turn (per-call or
        # compaction): turn end then re-emits it instead of the summed-average
        # fallback, which would overwrite a post-compaction size with a
        # pre-compaction estimate.
        self._context_recorded_this_turn: bool = False

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

        # ── Per-session system-prompt cache (run_1dc710db) ──────────────
        # The system prompt built at this unit's FIRST spawn, reused on later turns
        # to skip the ~85K re-assembly (build_session_briefing ~1.1s) that a warm
        # reuse discards anyway. The router seeds/reads this; a resume turn
        # (needs_context_injection) bypasses the cache to rebuild with prior
        # conversation. Always a real, complete prompt (never a placeholder), so any
        # spawn path that consumes `options` gets a valid prompt.
        self._cached_system_prompt: Optional[str] = None

        # ── Zombie detection — set True when meaningful content emitted ──
        self._content_emitted: bool = False

        # ── Resume-poison guard (fail-closed cleanliness flag) ──────────
        # True ONLY after a turn reaches the successful ResultMessage
        # completion (streaming_orchestrator success path). Cleared on every
        # STREAMING entry (_transition). send() checks it before reusing a
        # warm IDLE subprocess: NOT clean → the subprocess may be poisoned by
        # a prior soft-interrupt / SSE-disconnect that left the CLI in corrupt
        # turn-state (PIT01) → recycle-before-reuse instead of feeding the
        # poisoned process a first message that returns an instant empty
        # error_during_execution (zombie). Default False = fail-closed: a turn
        # that did NOT reach the success transition (interrupt / disconnect /
        # error / max_turns) is presumed poisoned, path-agnostically. This is
        # a per-instance attribute (NEVER module/class level) so the guard
        # never crosses sessions/tabs.
        self._last_turn_clean: bool = False

        # ── Prewarm-adopt warm-reuse bridge (Gate-1 #5, run_f107f442) ─
        # TRANSIENT one-shot: True only between adopt_prewarmed_unit() re-keying
        # this unit's session_id (which DESTROYS the `prewarm-` prefix that both
        # warm-reuse exemption sites key on) and the first message reaching
        # STREAMING. Without it, an adopted unit's first message hits
        # poison_guard recycle (kill+respawn) → the whole prewarm benefit is
        # lost. Honored at BOTH exemption sites (session_router._is_warm_reuse +
        # the poison_guard below), CLEARED at STREAMING entry so the SECOND
        # message sees the normal poison_guard again.
        # NOT the rejected persistent `is_prewarm` identity field
        # (IMPROVEMENT.md:1400) — a self-clearing one-turn bridge, not a
        # replacement for the prefix authority anywhere else.
        self._adopted_prewarm_fresh: bool = False

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
        # Re-probe budget for the health check when a configured MCP is still
        # "pending" at check time (run_a7b35b68): background MCP init (SDK default,
        # since NONBLOCKING=0 was removed) means a slow MCP may not have reached a
        # TERMINAL status (connected/failed) by the first ResultMessage. Consuming
        # the one-shot on a "pending" would permanently mask a slow-FAILING MCP.
        # So a check that sees any configured MCP still pending does NOT finalize —
        # it un-sets the one-shot for a bounded number of later turns until status
        # is terminal (or the budget is exhausted, then it finalizes on what it has).
        self._mcp_pending_reprobes_left: int = 3

        # ── Active sub-agents (Agent tool) ───────────────────────
        # Keys = tool_use_id, values = {label, start_time}.
        # Set when ToolUseBlock(name="Agent") arrives, removed when matching
        # ToolResultBlock arrives. Cleared at every turn boundary (send,
        # interrupt, turn_limit). In-process state for diagnostics; the UI
        # no longer polls it (2026-10-18: the timer banner and its endpoint
        # were removed — sub-agent status streams as `subagent_progress` and
        # nests under the Agent step).
        self._active_agent_tools: dict[str, dict] = {}
        # SDK task ids of the sub-agents running in THIS turn (joined on a running
        # subagent_progress, left on a terminal one; cleared at every turn boundary).
        # The allowlist for stop_task: a request-supplied id reaches the SDK only if
        # this session reported it running (chat turn spec v6 §7.3).
        self._live_task_ids: set[str] = set()

        # ── CLI stream reader (core/cli_stream_reader.py) ─────────
        # The ONE consumer of the live client's SDK message stream, from _spawn to
        # _cleanup_internal. Every turn reads its frames from a TurnStream the
        # reader routes to: _turn_stream is the one the current turn reads (a
        # user send, compact, a leak continuation, or a CLI-initiated turn), kept
        # across a WAITING_INPUT pause so continue_with_answer/permission resume
        # the same stream. A unit with no reader (a test fake set as _client
        # without _spawn) reads client.receive_response() as before.
        self._stream_reader: Optional[Any] = None
        self._turn_stream: Optional[Any] = None
        # CLI-initiated turns waiting for the router's sink (FIFO).
        self._injected_turns: "_deque[Any]" = _deque()
        self._on_cli_event: Optional[Callable[[str, str], None]] = on_cli_event

        # ── Tool-hang tracking (run_fb6e94a9) ─────────────────────
        # tool_use_id → monotonic-ish start time (time.time()) for EVERY tool
        # the model emits, cleared when its ToolResultBlock arrives. The PID
        # watchdog reads this to distinguish a stuck tool from live thinking.
        # _tool_hang_interrupted is the once-per-episode guard so the watchdog
        # interrupts a stuck tool exactly once (cleared on STREAMING entry and
        # on any ToolResultBlock). Reset at every turn boundary alongside
        # _active_agent_tools (GC15 matched-cleanup clist).
        # tool_use_id → (start_time, tool_name). Value is a tuple so the
        # watchdog can apply a per-tool-type open window (Agent/Bash run long).
        self._open_tool_uses: dict[str, tuple[float, str]] = {}
        self._tool_hang_interrupted: bool = False
        # Wall-clock of the last SUCCESSFUL warm tool-hang interrupt; gates the
        # force-kill backstop for TOOL_HANG_INTERRUPT_GRACE_S so interrupt()
        # can land before the destructive path fires. None = no pending grace.
        self._tool_hang_interrupt_at: Optional[float] = None
        # Dedicated tool-hang escalation counter (run_fb6e94a9). Separate from
        # _consecutive_unstick_timeouts (which is context-token gated and reset
        # by the send() success path) so repeated wedged-tool episodes actually
        # accumulate toward a hard-error escalation (AC4).
        self._tool_hang_episodes: int = 0
        # Tool-FREE deadline extensions used THIS attempt (run_dcd668a6). Reset
        # per STREAMING entry — bounds total extension per attempt; across
        # retries the upstream retry cap bounds it. See TOOL_FREE_* constants.
        self._tool_free_extensions: int = 0

        # ── Proactive RSS restart cooldown ────────────────────────
        # Monotonic timestamp of last proactive compact→kill cycle.
        # Prevents repeated restarts within the PROACTIVE_COOLDOWN window.
        # Uses monotonic clock — immune to NTP sync / sleep-wake clock jumps.
        # -inf ensures first restart is never cooldown-blocked (monotonic()
        # can be < PROACTIVE_COOLDOWN on freshly booted CI runners).
        self._last_proactive_restart: float = float("-inf")

        # (_last_soft_compact REMOVED, run_2b1957f8 — proactive soft-compact path deleted.)

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


        # ── Root-1 SSOT Phase 2: outstanding tool_use + pending question ──
        # When the agent emits an AskUserQuestion / cmd_permission tool_use the
        # SDK conversation is WAITING for that tool_use's result. While one is
        # outstanding, the drain worker MUST NOT inject a new turn (F3 — that is
        # the abandoned-ask bug). The streaming-orchestrator (L4) sets
        # _pending_tool_use_id + _pending_question on the WAITING_INPUT emit, and
        # the answer/permission-continue path clears them.
        self._pending_tool_use_id: Optional[str] = None
        self._pending_question: Optional[dict] = None
        # Originating turn's client_id (set in the send loop AFTER admission —
        # session_router.py:2293, guarded `if client_id`). Continuation paths
        # (answer/permission) run on THIS same unit and reuse it so their persisted
        # assistant rows carry the SAME `{client_id}-asst` correlation key as the
        # main-path rows. Without this, a continuation row is keyless and a
        # reconcile-tail cut landing on it produces a duplicate bubble (run_9bbf1761).
        #
        # TURN-LIFECYCLE FIELD (run_2aea0237 + audit Finding-1 root-fix): the
        # continuation-row correlation key for the CURRENTLY-open turn. It shares the
        # exact lifecycle of _pending_tool_use_id / _pending_question above — set for
        # a turn, meaningful only while that turn's question is open, and RESET to
        # None at the single new-turn admission point in send() (alongside the
        # _pending_* reset). The readers (router continuation persists) stamp
        # `{_turn_client_id}-asst` on every persisted assistant row; because send()
        # zeroes it before any yield, EVERY entrance (main / drain worker / channel)
        # starts clean and the router's in-loop `if client_id` write fills the right
        # key — a keyless turn simply leaves it None (safe no-match), so a drain turn
        # can never inherit a prior keyed turn's cid → answer content never attaches
        # to the wrong bubble. The `if client_id` guard at router additionally
        # prevents an intruding send during WAITING_INPUT from clobbering the open
        # turn's stash (that send RAISES SessionBusyError before the reset, upstream).
        self._turn_client_id: Optional[str] = None
        # The turn's products (content / knowledge files written), by path — read and
        # filled by the router's three loops (main / answer / permission) and emitted
        # as ONE turn_deliverables block at the turn's single `result`. Same lifecycle
        # as _turn_client_id: reset at send() admission, so a pause for a question or
        # a permission (no `result`) keeps what was produced before it.
        self._turn_files: dict = {}
        # The turn's code changes (core/turn_changes): a start snapshot of the code
        # trees + the files the turn wrote → ONE turn_changes block at `result`. Same
        # lifecycle as _turn_files.
        self._turn_changes = None
        # Pipeline runs whose alignment this turn has shown (reset per turn at send()).
        self._pipeline_runs_announced: set = set()
        # Seqs of the most recently drained pending set, surfaced to the frontend
        # mirror by the streaming-state read API (L5/2A). Best-effort hint.
        self._last_drained_seqs: list[int] = []

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
        self._health_sensor: HealthSensor = HealthSensor(max_turns=DESKTOP_MAX_TURNS)
        self._healing_loop: HealingLoop = HealingLoop()
        # R3: route the self-heal DECISION through the one recovery authority.
        # Delegates to _healing_loop (the breaker) — strangler-fig, HealingLoop
        # unchanged. Other 7 kill paths migrate one per run (R3a–g).
        self._recovery_coordinator: RecoveryCoordinator = RecoveryCoordinator(
            self._healing_loop
        )
        # High-water mark for the one-shot recovery_exhausted SSE event. The
        # Coordinator's terminal_signal_count climbs by exactly 1 per exhaustion
        # episode (monotonic; on_success resets the bool, not the count). We
        # surface the user-facing event only when the count ADVANCES past this
        # mark — so the recurring ESCALATE verdict (fires every tick while the
        # breaker holds) yields the toast exactly once per episode, and a fresh
        # episode after a successful heal re-fires. (run_d8dce02a, decision #3)
        self._last_recovery_exhausted_signal: int = 0
        # Checkpoint built before heal-kill, consumed by next spawn to
        # inject continuation context. None = no pending heal context.
        self._heal_checkpoint: TaskCheckpoint | None = None
        # Set True when the most recent kill was an APP-INITIATED fast recycle
        # (flush_recycle / interrupt_recycle of a poisoned subprocess). Read by
        # classify_failure so the resulting "exit code -9" is treated as ZOMBIE
        # (near-instant respawn) instead of OOM (30/60/120s backoff). Consumed
        # (reset) at classification and cleared at each new turn. RSS-driven
        # kills do NOT set this — they are real memory pressure (keep OOM).
        self._recycle_kill_pending: bool = False
        # Set by force_unstick_streaming: the next "exit code -9" is the stall
        # watchdog's own kill, classified as a hang, never OOM. Consumed once.
        self._stall_kill_pending: bool = False
        # Set by send(): what a permission/answer continuation re-spawns with
        # when its process dies mid-continuation (2026-10-20 13:22).
        self._last_send_options: Any = None
        self._last_send_config: Any = None
        # Graceful pre-kill: when turn_approaching fires, set this flag
        # so next send() injects WRAP_UP_PROMPT before the actual kill.
        # One turn of grace for the agent to finish its thought.
        self._graceful_wrap_pending: bool = False

        # Instance-scoped buffer for the agent's wrap-up conclusion captured
        # during a turn_approaching graceful wrap-up turn. Fed into the heal
        # checkpoint (leads key_findings) then cleared one-shot. Instance attr
        # ONLY — never module-level (3.5 / anti-pattern #1).
        self._wrapup_conclusion: str = ""

        # ── Channel wrap-up (one-shot, Gap #13) ────────────────────
        # Set to True after CHANNEL_WRAP_UP_PROMPT is injected. Prevents
        # re-injection on every subsequent turn past the threshold.
        self._channel_wrap_injected: bool = False

        # ── Desktop hard-floor wrap-up (one-shot, Root 2 / AC3) ────
        # Set after the hard-floor WRAP_UP_PROMPT is injected on a desktop
        # session at max_turns - HARD_FLOOR_BUFFER. Independent of self-heal.
        self._hard_floor_wrap_injected: bool = False

        # ── Long-turn heartbeat throttle (Root 2 / AC5) ────────────
        # Last elapsed-seconds value at which a "still_working" notice was
        # emitted for the current turn. Reset on STREAMING entry.
        self._last_heartbeat_elapsed: float = 0.0

        # ── Resume-fallback context preservation ─────────────────
        # Stable app-level session ID for DB queries in the abandon-
        # fallback path of _retry_with_resume.  Set at send() entry.
        self._app_session_id: Optional[str] = None

        # ── OOM tracking (persists across send() calls) ───────────
        # Counts consecutive OOM kills for this session.  NOT reset in
        # send() — prevents the death spiral where OOM → retry → OOM
        # loops forever because _retry_count resets each send().
        # Reset only on successful stream completion.
        self._consecutive_oom_kills: int = 0
        self._OOM_KILL_LIMIT: int = 3  # After 3 consecutive OOMs, stop retrying

        # Consecutive force_unstick_streaming() calls without a successful
        # streaming response in between. Used as a circuit breaker to stop
        # the dead loop: timeout → force_unstick → resume → timeout again.
        # Reset on successful stream completion (alongside _consecutive_oom_kills).
        self._consecutive_unstick_timeouts: int = 0

        # ② zero-token hang guard (run_22393305): consecutive 'wedged' CPU
        # verdicts seen by the lifecycle watchdog's EARLY resume-branch consult
        # (before the full ~1800s adaptive ceiling). Requires TWO consecutive
        # wedged verdicts across ~60s loop ticks before an early force_unstick —
        # a single 6s CPU sample could misread a CPU-idle I/O-bound resume
        # replay as wedged (Gate-1 residual risk, XG decision A). ANY 'working'/
        # 'unknown' verdict (or a successful stream) RESETS it to 0, so only a
        # SUSTAINED zero-CPU hang accumulates to the kill. Reset alongside
        # _consecutive_unstick_timeouts on a healthy stream.
        self._resume_wedged_strikes: int = 0

        # ── PID Watchdog (out-of-band subprocess death detection) ──
        # Polls os.kill(pid, 0) while STREAMING/WAITING_INPUT.
        # Detects external kills (jetsam, OOM) that pipe can't detect.
        self._pid_watchdog_task: Optional[asyncio.Task] = None
        self._PID_WATCHDOG_INTERVAL: float = 5.0  # seconds between polls

        # ── Streaming Orchestrator ─────────────────────────────────
        # Owns _stream_response() and _read_formatted_response() logic.
        # See: core/streaming_orchestrator.py
        self._streaming_orchestrator = StreamingOrchestrator(parent=self)

        # ── Retry Manager ─────────────────────────────────────────
        # Owns retry_with_resume(), handle_buffer_overflow(), and
        # inject_abandon_continuation() logic.
        # See: core/retry_manager.py
        self._retry_manager = RetryManager(parent=self)

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
    def has_outstanding_tool_use(self) -> bool:
        """True when the agent emitted a tool_use (AskUserQuestion /
        cmd_permission) that is still awaiting its tool_result.

        Root-1 SSOT Phase 2 (F3): the drain worker must NOT start a new turn
        while a tool_use is outstanding — injecting a fresh user message then
        would corrupt the conversation (the abandoned-ask → --resume replay bug).
        Set on the WAITING_INPUT emit, cleared when the answer/permission result
        is delivered.
        """
        return self._pending_tool_use_id is not None

    @property
    def cli_quiescent(self) -> bool:
        """True when a new user message may be written to the CLI now: its last
        session state was idle, no background task finished since, no turn is
        open or claimed (``CliStreamReader.quiescent``), and no CLI-initiated turn
        waits for the sink (so its rows land before the next user turn's). True
        with no reader.
        (getattr: some fixtures build a unit with __new__, skipping __init__.)"""
        r = getattr(self, "_stream_reader", None)
        if r is None:
            return True
        # A dead reader is quiescent (nothing runs on it; the next write respawns),
        # but complete CLI turns already queued still go to the sink first.
        return r.quiescent and not self._queued_injected()

    @property
    def has_background_work(self) -> bool:
        """True while the CLI has a live background task (command or subagent) or
        a CLI-initiated turn is waiting for the sink. Routine reclaim of a unit
        someone can still see (the TTL, the RSS proactive restart, channel-pool
        eviction) skips such an IDLE unit: killing it loses the task and the turn it
        will start. An orphan (no window owns it) is not exempt, nor is
        memory-pressure survival. A dead reader reports no background work."""
        r = getattr(self, "_stream_reader", None)
        return self._queued_injected() or (r is not None and bool(r.background_tasks))

    def _queued_injected(self) -> bool:
        """True while a CLI-initiated turn waits for the sink. Drops streams the
        reader transferred to a user claim (the notification race): that turn is
        the claimant's now, and leaving it queued would hold the CLI "busy" until
        the next idle report (a pending message would wait for it)."""
        q = getattr(self, "_injected_turns", None)
        if not q:
            return False
        while q and getattr(q[0], "transferred", False):
            q.popleft()
        return any(not getattr(ts, "transferred", False) for ts in q)

    def _start_stream_reader(self, client: Any) -> None:
        """Create and start the one consumer of ``client``'s message stream.

        Every ClaudeSDKClient has ``receive_messages()``. A client without it (a
        test double that only scripts ``receive_response()``) gets no reader and
        is read per turn as before — loudly, because in production that is the
        path where a CLI-initiated turn stalls."""
        old = getattr(self, "_stream_reader", None)
        if old is not None:
            # Never two consumers of one stream (I1): every respawn path tears the
            # old client down first, but a missed path must not leave it reading.
            old.close()
            self._stream_reader = None
        if not callable(getattr(client, "receive_messages", None)):
            logger.warning(
                "session_unit.no_stream_reader session_id=%s client=%s — client has "
                "no receive_messages(); reading per turn (CLI-initiated turns will "
                "not be read until the next send)",
                self.session_id, type(client).__name__,
            )
            self._stream_reader = None
            return
        from .cli_stream_reader import CliStreamReader
        self._stream_reader = CliStreamReader(
            client, self.session_id,
            on_injected=self._on_reader_injected,
            on_idle=self._on_reader_idle,
            on_error=self._on_reader_error,
        )
        self._stream_reader.start()

    def _on_reader_injected(self, ts: Any) -> None:
        """Reader callback: the CLI started a turn nobody claimed."""
        self._injected_turns.append(ts)
        self._emit_cli_event("injected")

    def _on_reader_idle(self) -> None:
        """Reader callback: the CLI reported idle."""
        self._emit_cli_event("idle")

    def _on_reader_error(self) -> None:
        """Reader callback: the stream failed or ended (the subprocess is gone).

        Turns the CLI finished before it died are still queued with their frames:
        hand them to the sink first, then re-drive the pending drain, whose send()
        respawns the dead subprocess with --resume (see send())."""
        if self._queued_injected():
            self._emit_cli_event("injected")
        self._emit_cli_event("idle")

    def _emit_cli_event(self, kind: str) -> None:
        cb = self._on_cli_event
        if cb is None:
            return
        try:
            cb(self.session_id, kind)
        except Exception:  # noqa: BLE001 — a callback must never break the reader
            logger.exception("session_unit.cli_event_callback_failed session_id=%s kind=%s",
                             self.session_id, kind)

    def _record_gate_mark(self) -> None:
        """Remember what the quiescence gate saw (reader, finished-task count) for the
        claim of the turn this gate admits (``write_user_message``): a task that
        finishes between the gate and the write then counts as after the claim."""
        r = getattr(self, "_stream_reader", None)
        self._gate_mark = (r, r.notif_count) if r is not None else None

    async def _end_abandoned_turn(self, err: Any) -> AsyncIterator[dict]:
        """End a turn the reader abandoned while the CLI is alive (I4), never through
        the non-retriable crash (which drops the --resume identity).

        - our own teardown already moved the unit out of the turn: end quietly, as the
          old per-turn stream simply ended;
        - the CLI never took the message: back to IDLE and raise SessionBusyError, so
          the router stores it as pending and the next idle delivers it;
        - the turn ran and was cut short: back to IDLE (identity and subprocess kept)
          with a non-terminal TURN_ABANDONED error.
        """
        from .cli_stream_reader import is_message_not_consumed
        from .session_utils import _build_error_event
        if self.state not in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            logger.info("session_unit.turn_abandoned_after_teardown session_id=%s state=%s",
                        self.session_id, self.state.value)
            return
        self._transition(SessionState.IDLE)
        if is_message_not_consumed(err):
            from .exceptions import SessionBusyError
            raise SessionBusyError(
                detail=(f"Session {self.session_id}: the CLI did not take this message. "
                        f"It has been queued and will be sent when the CLI is idle."),
            )
        logger.warning("session_unit.turn_abandoned session_id=%s: %s", self.session_id, err)
        yield _build_error_event(
            code="TURN_ABANDONED",
            message="The response stopped before it finished.",
            detail=str(err),
            suggested_action="Send your message again to continue.",
        )

    def take_injected_turn(self) -> Optional[Any]:
        """Pop the next CLI-initiated turn the sink may run, or None.

        Only from a clean IDLE with no compact holding the subprocess: a turn
        queued while the unit was busy waits for the next IDLE edge. A stream
        that was transferred to a user claim (the notification race) is skipped.
        """
        if self.state != SessionState.IDLE or self._client is None or self._client_io.locked():
            return None
        r = self._stream_reader
        while self._injected_turns:
            ts = self._injected_turns.popleft()
            if r is not None and r.start_sink(ts):
                return ts
        return None

    def begin_injected_turn(self, ts: Any, client_id: Optional[str]) -> None:
        """IDLE → STREAMING for a CLI-initiated turn whose frames are in ``ts``.

        Same per-turn reset as a user send, so the turn's tracking starts clean;
        the orchestrator's result handling returns the unit to IDLE exactly as
        for a user turn.
        """
        if self.state != SessionState.IDLE:
            raise RuntimeError(
                f"Cannot begin an injected turn in state {self.state.value} "
                f"(session_id={self.session_id})")
        self._send_generation += 1
        self._stop_event.clear()
        self._interrupted = False
        self._user_stopped_current_turn = False
        self._reset_turn_state()
        self._turn_client_id = client_id
        self._turn_stream = ts
        self._transition(SessionState.STREAMING)
        self._health_sensor.record_activity()

    def _reset_turn_state(self) -> None:
        """Per-turn state reset at a new-turn admission point (a user send or a
        CLI-initiated turn, ``begin_injected_turn``). Synchronous, no await."""
        # Reset per-send state (retry counter + buffer overflow flag).
        # _buffer_overflow_recovery is per-message, not per-session:
        # each new user message should get a fresh recovery attempt
        # if it triggers a different buffer overflow.
        self._retry_count = 0
        # Clear last turn's spawn timing at the new-turn admission point
        # (same lifecycle as the per-turn fields around it). A warm turn never
        # reaches _spawn, so this reset is what guarantees it relays None — not a
        # stale cold-turn value — to the TTFT probe (run_8149838f).
        self._last_turn_spawn_ms = None
        # _interrupted is cleared by the caller (send() Layer 0 / begin_injected_turn)
        self._buffer_overflow_recovery = False
        self._leak_restart_done = False  # per-message: one leak restart budget
        self._compaction_guard.reset()  # New user turn — reset tool tracking
        self._last_call_usage = None  # an interrupted turn's last usage must not leak in
        self._context_recorded_this_turn = False
        self._content_emitted = False   # Track if meaningful content is emitted
        self._active_agent_tools = {}  # Clear stale sub-agent progress on new turn
        self._live_task_ids = set()  # an earlier turn's task ids are not stoppable
        self._open_tool_uses = {}  # Clear stale open-tool tracking (run_fb6e94a9)
        # Gate-2 F3 (belt-and-suspenders): a fresh user turn never carries a stale
        # outstanding-tool_use guard. Gate-2 F4: drop the stale last-drained hint so
        # the read API doesn't surface seqs from a previous turn indefinitely.
        self._pending_tool_use_id = None
        self._pending_question = None
        self._last_drained_seqs = []
        # ROOT-FIX (audit Finding 1): the continuation-row correlation key
        # (_turn_client_id) shares the SAME turn lifecycle as the pending-question
        # fields above — it is meaningful only WHILE this turn's question is open.
        # Reset it here, at the new-turn admission point, so EVERY entrance
        # (main send / drain worker / channel / a CLI-initiated turn) starts from
        # None and the caller fills in the correct key (the router for a send,
        # begin_injected_turn for a CLI-initiated turn). This is why a keyless drain
        # turn no longer inherits a prior keyed turn's cid → answer content can never
        # attach to the wrong bubble.
        # SAFE PLACEMENT (send): this point is downstream of the WAITING_INPUT
        # busy-guard (which RAISES before here for an intruding send, preserving the
        # pending turn's stash) and BEFORE the first yield (so the router's in-loop
        # stash write always runs after this reset, never races it);
        # begin_injected_turn runs only from a clean IDLE. Continuations run via
        # continue_with_answer/continue_with_permission — SEPARATE methods that never
        # re-enter send() — so this reset cannot drop a stash a continuation needs.
        self._turn_client_id = None
        # Same lifecycle: the products of a turn paused for a question / permission
        # ride the unit until that turn's `result` (router continuation loops).
        self._turn_files = {}
        try:
            from core.turn_changes import TurnChanges
            self._turn_changes = TurnChanges.start()
        except Exception as exc:  # noqa: BLE001 — a display aid never blocks a send
            logger.debug("turn_changes start skipped: %s", exc)
            self._turn_changes = None
        # Pipeline runs this turn has shown the alignment of (streaming_orchestrator
        # _pipeline_stage_events): each new turn announces a running run once more.
        self._pipeline_runs_announced = set()

    def _has_live_outstanding_waiter(self) -> bool:
        """True iff the outstanding tool_use has a REAL awaiting hook coroutine.

        ``has_outstanding_tool_use`` only reports whether ``_pending_tool_use_id``
        is set — it CANNOT tell a genuinely-open prompt (a hook still blocked on
        ``wait_for_permission_decision`` / ``wait_for_answer``) apart from a
        DEAD-waiter zombie (the hook coroutine was cancelled — e.g. SDK
        control_cancel_request — which pops the request store in its ``finally``
        but leaves ``_pending_tool_use_id`` stranded). That desync is the
        approve-into-void deadlock (run_65f317db).

        This method closes the gap by consulting the ACTUAL waiter managers, which
        are respawn-immune: the event is registered on entry to the wait coroutine
        and popped in its ``finally`` (decision / timeout / cancel), so a True here
        means a real coroutine is blocked RIGHT NOW to receive the decision.

        Which manager owns the id is disambiguated by ``_pending_question`` shape
        (set alongside ``_pending_tool_use_id`` on the same WAITING_INPUT emit),
        NOT by blind-OR-ing both managers (id-namespace disjointness is incidental,
        not an invariant — see streaming_orchestrator.py surface paths):
          - permission prompt → ``_pending_question`` carries ``request_id``
          - ask_user_question → ``_pending_question`` carries ``questions``
        """
        tuid = self._pending_tool_use_id
        if tuid is None:
            return False
        pq = self._pending_question or {}
        if "request_id" in pq:
            from core.permission_manager import permission_manager as _pm
            return _pm.has_live_waiter(tuid)
        if "questions" in pq:
            from core.ask_question_manager import ask_question_manager as _aqm
            return _aqm.has_live_waiter(tuid)
        # Unknown/absent shape (defensive): fall back to OR-ing both. A False here
        # would wrongly reap a live prompt; only reap when BOTH managers agree the
        # waiter is gone.
        from core.permission_manager import permission_manager as _pm
        from core.ask_question_manager import ask_question_manager as _aqm
        return _pm.has_live_waiter(tuid) or _aqm.has_live_waiter(tuid)

    async def reap_dead_waiting_input(self) -> bool:
        """Recover a WAITING_INPUT session whose waiter coroutine is DEAD.

        The single-predicate recovery for the approve-into-void deadlock
        (run_65f317db, SSA Gate-1): a session is a dead-waiter zombie iff it is
        ``WAITING_INPUT`` AND has an outstanding tool_use AND has NO live waiter.
        In that state the prompt can never be answered (the hook that would
        receive the decision is gone), so we reap it via the blessed
        ``force_unstick_waiting_input`` recovery (arm checkpoint →
        crash_to_cold(clear_identity=False) → next send() resumes with --resume;
        ``_cleanup_internal`` clears ``_pending_tool_use_id`` so the drain guard
        self-heals).

        Returns True iff a reap happened. Idempotent + race-safe:
        ``force_unstick_waiting_input`` no-ops if state left WAITING_INPUT, and the
        live-waiter re-check here means a prompt whose real waiter just
        (re)registered is NOT reaped. Called from THREE chokepoints — send()
        (self-heal on next message), the lifecycle tick (self-heal with no message
        or endpoint hit), and the approve endpoints (recover an approve-into-void).
        """
        if self.state != SessionState.WAITING_INPUT:
            return False
        if not self.has_outstanding_tool_use:
            return False
        if self._has_live_outstanding_waiter():
            return False
        logger.warning(
            "session_unit.reap_dead_waiting_input session_id=%s tool_use=%s — "
            "WAITING_INPUT with a DEAD waiter (no live hook to receive a decision); "
            "forcing COLD for recovery (approve-into-void deadlock reap)",
            self.session_id, self._pending_tool_use_id,
        )
        await self.force_unstick_waiting_input()
        return True

    # Root-1 SSOT Phase 2 (L6, Option B): is_generating_after_disconnect (the
    # post-disconnect "still generating" limbo property) is DELETED. A disconnect
    # now yields a clean IDLE — there is no flag to consult. The streaming-state
    # mirror reports streaming = (state == STREAMING).

    @property
    def is_post_disconnect_flushing(self) -> bool:
        """DEAD: always False since a client disconnect stopped interrupting turns
        (core/session_events) — nothing calls schedule_pipe_flush any more; kept
        until the follow-up removes this cluster and its readers.

        Was: True while a post-disconnect pipe-flush task is still running — i.e.
        the subprocess was left alive after an SSE disconnect (Option B-soft, 1A)
        and is finishing a long tool-loop whose output persists to DB.

        Eviction/slot logic uses this so a clean-IDLE-but-still-flushing unit is
        not killed mid-turn (replaces the deleted is_generating_after_disconnect
        guard at the eviction sites — same protection, derived from the real task
        instead of a manually-managed flag)."""
        t = self._pipe_flush_task
        return t is not None and not t.done()

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

        # R6 Step B: maintain the daemon-wide concurrent-streaming counter HERE,
        # inside the one sync state chokepoint, so every STREAMING entry/exit —
        # including kill / crash / disconnect (all route through _transition) —
        # keeps it accurate. old_state != new_state is guaranteed here (the
        # same-state early-return above already fired), so each STREAMING entry
        # increments exactly once and its matching exit decrements exactly once.
        global _streaming_count
        if new_state == SessionState.STREAMING and old_state != SessionState.STREAMING:
            _streaming_count += 1
        elif old_state == SessionState.STREAMING and new_state != SessionState.STREAMING:
            _streaming_count = max(0, _streaming_count - 1)  # clamp: never negative

        # Reset hook tracking when entering STREAMING — the next IDLE
        # period is a fresh conversation turn that deserves its own hooks.
        if new_state == SessionState.STREAMING:
            self._hooks_enqueued = False
            # Fail-closed: a turn is presumed poisoned until it reaches the
            # success-result transition that re-blesses it. Clearing here (the
            # single STREAMING-entry chokepoint, NOT in send()'s reset batch)
            # is what keeps the warm-reuse fast path alive for clean turns
            # while forcing a recycle for interrupted/disconnected ones.
            self._last_turn_clean = False
            # One-shot prewarm-adopt bridge (Gate-1 #5): a turn has now started,
            # so the re-key window is over — clear the bridge so the NEXT message
            # sees the normal poison_guard. Cleared at the SAME chokepoint as
            # _last_turn_clean for symmetry (both are per-turn STREAMING-entry state).
            self._adopted_prewarm_fresh = False
            # ② zero-token hang guard (run_22393305): the resume early-wedged
            # strike counter is PER-TURN state — a new streaming turn deserves a
            # fresh two-tick debounce budget. Resetting HERE (the single
            # STREAMING-entry chokepoint) is the root reset point: it covers EVERY
            # re-entry (force_unstick→respawn, healthy continuation, AND a new turn
            # that re-enters STREAMING without the prior turn completing a healthy
            # stream — the adversarial-found gap the force_unstick-only reset
            # missed). The force_unstick reset + healthy-stream reset remain as
            # defense-in-depth, but THIS is what makes "two CONSECUTIVE wedged
            # ticks" a per-turn invariant rather than a cross-turn accumulation.
            self._resume_wedged_strikes = 0
            # ONE timestamp for both — the dumb-spawn watchdog discriminates
            # "no event since spawn" by `_last_event_time <= _streaming_start_time`
            # (lifecycle_manager._check_streaming_timeout). Two separate
            # time.time() calls would make _last_event_time microseconds GREATER
            # than _streaming_start_time, so the discriminator would read every
            # fresh spawn as "events already flowing" and the watchdog would
            # never fire (run_6c482b10 adversarial HIGH). Capture once.
            _stream_entry_ts = time.time()
            self._streaming_start_time = _stream_entry_ts
            self._last_event_time = _stream_entry_ts
            # Progress clock starts unset: until the first REAL content event,
            # streaming_stall_seconds falls back to _streaming_start_time, which
            # preserves the dumb-spawn "no token since spawn" detection.
            self._last_progress_time = None
            # Root 2 / AC5: fresh heartbeat throttle for this turn.
            self._last_heartbeat_elapsed = 0.0
            # Fresh tool-hang episode for this turn (run_fb6e94a9).
            self._tool_hang_interrupted = False
            self._tool_hang_interrupt_at = None
            # Fresh tool-free extension budget for this attempt (run_dcd668a6).
            self._tool_free_extensions = 0
            # Start PID watchdog for out-of-band death detection
            self._start_pid_watchdog()

        # Clear streaming timestamps when leaving STREAMING
        if old_state == SessionState.STREAMING and new_state != SessionState.STREAMING:
            self._streaming_start_time = None
            self._last_event_time = None
            self._last_progress_time = None

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

    async def _await_streaming_slot(self) -> None:
        """Block a NEW send() turn until a concurrent-streaming slot is free.

        R6 Step B admission gate (peak-OOM guard). Returns immediately when the
        daemon-wide streaming count is below MAX_CONCURRENT_STREAMS; otherwise
        polls every _STREAM_ADMIT_POLL_INTERVAL until a slot frees or
        _STREAM_ADMIT_TIMEOUT elapses. On timeout it PROCEEDS rather than failing
        the user (the cap is a soft peak-OOM hedge backed by the hard per-session
        7GB RSS kill + system-pressure last-resort kill, not a correctness gate)
        — but logs so sustained saturation is observable.

        Poll (not asyncio.Event): _transition decrements the counter from sync
        code; an Event.set() from there is the lost-wakeup race that bit
        run_002eca4c. A 100ms poll on a rare (>cap concurrent streams) condition
        is negligible cost and has zero lost-wakeup surface.
        """
        if _get_streaming_count() < self.MAX_CONCURRENT_STREAMS:
            return
        waited = 0.0
        logger.info(
            "session_unit.stream_admit_wait session_id=%s streaming=%d/%d — queuing new turn",
            self.session_id, _get_streaming_count(), self.MAX_CONCURRENT_STREAMS,
        )
        while _get_streaming_count() >= self.MAX_CONCURRENT_STREAMS:
            if waited >= self._STREAM_ADMIT_TIMEOUT:
                logger.warning(
                    "session_unit.stream_admit_timeout session_id=%s streaming=%d/%d "
                    "after %.0fs — proceeding (RSS kills remain the hard OOM guard)",
                    self.session_id, _get_streaming_count(),
                    self.MAX_CONCURRENT_STREAMS, waited,
                )
                return
            await asyncio.sleep(self._STREAM_ADMIT_POLL_INTERVAL)
            waited += self._STREAM_ADMIT_POLL_INTERVAL

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

                        # ── Tool-aware hang tier v2 (run_fb6e94a9) ────
                        # A genuinely stuck tool = an OPEN tool_use past its
                        # per-tool open window WHOSE PROCESS TREE IS BURNING
                        # ~0 CPU. event-silence ALONE is NOT proof of a hang:
                        # a single long tool (Bash build, Agent sub-agent)
                        # emits no SDK events whether wedged or healthy. The
                        # tree-CPU-delta probe is the discriminator — a busy
                        # tool (incl. a healthy Agent sub-agent running its
                        # own CLI child) shows CPU > epsilon and is NEVER
                        # interrupted. Escape is interrupt(autonomous=True)
                        # (warm, subprocess preserved), NOT _force_kill.
                        oldest = self._oldest_open_tool()
                        if (
                            oldest is not None
                            and not self._tool_hang_interrupted
                        ):
                            tool_age, tool_name, tool_id = oldest
                            window = self._tool_open_window(tool_name)
                            if tool_age > window:
                                await self._maybe_escape_wedged_tool(
                                    pid, tool_age, tool_name, tool_id
                                )
                                continue

                        # Suppress the destructive backstop during the grace
                        # window after a warm tool-hang interrupt (run_fb6e94a9)
                        # — give interrupt() time to end the turn before we
                        # resort to killing the subprocess.
                        in_interrupt_grace = (
                            self._tool_hang_interrupt_at is not None
                            and (time.time() - self._tool_hang_interrupt_at)
                            < self.TOOL_HANG_INTERRUPT_GRACE_S
                        )
                        # The force-kill backstop owns ONLY tool-FREE API hangs.
                        # While a tool is open, liveness is governed by the
                        # CPU-probe tier above (which never destroys a working
                        # tool). Killing here would resurrect v1's bug: a healthy
                        # long tool (event-silent but CPU-busy) force-killed at
                        # the 300s silence mark. So skip the backstop whenever a
                        # tool is still open (run_fb6e94a9).
                        tool_open = bool(self._open_tool_uses)
                        if silence > timeout and not in_interrupt_grace and not tool_open:
                            # ── Tool-FREE CPU-liveness gate (run_dcd668a6) ──
                            # This backstop is the SECOND tool-free kill path
                            # (the orchestrator wait_for is the first). Both MUST
                            # consult the SAME liveness authority, or gating only
                            # one leaves a split-brain door open (a CPU-busy turn
                            # the orchestrator spared would still be killed here).
                            # Spare ONLY a PROVABLY-working process; kill on
                            # WEDGED and UNKNOWN alike, or if past the absolute
                            # per-attempt hard ceiling (a busy-loop that pegs CPU
                            # forever is never 'wedged' — the ceiling is its
                            # last-resort bound). UNKNOWN kills here because this
                            # backstop is the last resort (the native read the
                            # orchestrator wait_for cannot cancel) — an
                            # unmeasurable process must not be spared.
                            if silence <= self.TOOL_FREE_HARD_CEILING_S:
                                verdict = await self._tool_free_hang_verdict(pid)
                                # Post-await state re-check (Gate-2 CRITICAL):
                                # the verdict await lasts ~SAMPLES×INTERVAL. During
                                # it the orchestrator (a SEPARATE task) can move the
                                # session STREAMING → WAITING_INPUT (a permission /
                                # ask_user_question arrived) or → IDLE/DEAD (turn
                                # ended). The pre-verdict STREAMING guard is stale
                                # by now; killing here would destroy a session
                                # legitimately waiting for user input. If we are no
                                # longer STREAMING, this backstop has nothing to do.
                                if self.state != SessionState.STREAMING:
                                    continue
                                # Spare ONLY a PROVABLY-working process. 'wedged'
                                # AND 'unknown' both fall through to the kill —
                                # this backstop is the last resort (the native
                                # read the orchestrator wait_for can't cancel), so
                                # an unmeasurable process must not be spared here.
                                # This also keeps the two tool-free paths
                                # symmetric: the orchestrator likewise only
                                # EXTENDS on 'working' and raises otherwise.
                                if verdict == "working":
                                    logger.warning(
                                        "session_unit.output_liveness_working "
                                        "session_id=%s pid=%d silence=%.0fs "
                                        "— subprocess CPU-busy, NOT killing",
                                        self.session_id, pid, silence,
                                    )
                                    continue
                            logger.error(
                                "session_unit.output_liveness_timeout "
                                "session_id=%s pid=%d silence=%.0fs "
                                "timeout=%.0fs — killing subprocess",
                                self.session_id, pid, silence, timeout,
                            )
                            # Track timeout for circuit breaker — this is
                            # another path that can form a dead loop just
                            # like force_unstick_streaming(). Without this,
                            # the watchdog kills bypass the circuit breaker.
                            self._consecutive_unstick_timeouts += 1
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

    # ── Tool-aware hang tiers v2 — CPU-liveness gated (run_fb6e94a9) ──
    # v1's event-silence discriminator was UNSOUND: _last_event_time refreshes
    # only per-message, so a single long tool (Bash build, Agent sub-agent) is
    # event-silent whether wedged OR healthy. v2 adds a SECONDARY liveness
    # signal — tree CPU delta — and ONLY interrupts a tool whose entire process
    # tree is burning ~0 CPU (a genuine deadlock). A busy tool (incl. a healthy
    # Agent sub-agent running its own CLI child) shows CPU > epsilon and is
    # NEVER interrupted.
    TOOL_HANG_SOFT_S: float = 120.0       # surface "still running, Stop to recover"
    TOOL_HANG_OPEN_S: float = 600.0       # quick tools: probe CPU after this long
    TOOL_HANG_OPEN_S_LONG: float = 1800.0 # long-window tools (see below)
    # Tools that get the LONG window. Two reasons a tool belongs here:
    #   (1) legitimately CPU-heavy and long — Agent (sub-agent), Bash (builds)
    #   (2) I/O-WAIT tools — they sit at CPU=0 while blocked on the network, so
    #       the CPU-liveness probe CANNOT prove they're alive (option A: the CPU
    #       signal can't distinguish "wedged" from "waiting on a slow endpoint";
    #       widen the window so a healthy network wait isn't interrupted early).
    #       Covers WebFetch and ALL MCP tools (dynamic names: mcp__server__tool).
    _LONG_TOOLS: frozenset = frozenset({"Agent", "Bash", "WebFetch", "WebSearch"})
    _LONG_TOOL_PREFIXES: tuple = ("mcp__",)
    CPU_PROBE_INTERVAL_S: float = 2.0     # tree-CPU sampling window
    CPU_LIVE_EPSILON: float = 0.05        # cpu-seconds delta above which = "working"
    _TOOL_HANG_EPISODE_LIMIT: int = 2     # warm interrupts before force-kill escalation
    # Absolute ceiling: a single tool open longer than this is force-killed
    # regardless of CPU liveness — the last-resort bound for a tool that pegs
    # CPU forever (infinite loop in user code) which the CPU probe would never
    # flag as wedged. Generous (> longest legit Agent/Bash run) to avoid killing
    # genuine long work; the user-facing soft signal fires long before this.
    TOOL_HANG_HARD_CEILING_S: float = 3600.0  # 1 hour
    # After a SUCCESSFUL warm interrupt, suppress the destructive force-kill
    # backstop this long to let interrupt() end the turn (STREAMING → IDLE).
    TOOL_HANG_INTERRUPT_GRACE_S: float = 60.0

    # ── Tool-FREE hang tier — CPU-liveness gated (run_dcd668a6) ──────
    # The tool-OPEN tier above only guards a hang WHILE a tool_use is open. A
    # tool-FREE API hang (no tool open — pure Bedrock inference silent for
    # MESSAGE_TIMEOUT) was force-killed by a PURE WALL-CLOCK timer with NO
    # liveness check (streaming_orchestrator wait_for + the _pid_watchdog
    # output-liveness backstop below). That false-killed a CPU-BUSY-but-slow
    # turn (long extended-thinking on large context). This tier applies the
    # SAME tree-CPU discriminator to the tool-free path: at the timeout mark,
    # if the subprocess tree is burning CPU it is WORKING (extend, bounded),
    # if it is ~0 CPU across samples it is WEDGED (kill as before). Multi-sample
    # (not single) so a transient CPU dip during slow streaming is not mistaken
    # for a wedge. Reuses CPU_PROBE_INTERVAL_S + CPU_LIVE_EPSILON above.
    TOOL_FREE_VERDICT_SAMPLES: int = 3    # consecutive ~0-CPU windows => wedged
    TOOL_FREE_MAX_EXTENSIONS: int = 4     # bounded deadline extensions per attempt
    # Absolute per-ATTEMPT ceiling: a tool-free turn silent this long is
    # force-killed REGARDLESS of CPU (a busy-loop that pegs CPU forever would
    # never be flagged 'wedged'). Per-STREAMING-entry (reset each attempt);
    # total across retries is separately bounded by retry_manager
    # MAX_RETRY_ATTEMPTS + should_circuit_break_timeout. Generous (> longest
    # legit tool-free inference) so genuine long thinking is not killed.
    TOOL_FREE_HARD_CEILING_S: float = 1800.0  # 30 min

    # ── Tool-hang helpers (run_fb6e94a9) ─────────────────────────

    def has_open_tools(self) -> bool:
        """True if this session currently has ≥1 tool_use open (executing).

        Public accessor over the internal ``_open_tool_uses`` tracker (populated
        on ToolUseBlock start, popped on its ToolResultBlock). Used by Canvas
        Layer-2 live-surfacing (surface_injection) to gate: only surface a
        workspace write LIVE while the active session's agent is actually running
        a tool — so a background-job write during an idle-tool chat is not
        mis-attributed. Covers ALL tools (Bash/Write/Agent); for a sub-agent the
        parent's Agent-tool stays open across the whole sub-agent run. Fail-safe:
        never raises (a missing tracker reads as no open tools)."""
        return bool(getattr(self, "_open_tool_uses", None))

    def _oldest_open_tool(self) -> Optional[tuple[float, str, str]]:
        """(age_seconds, tool_name, tool_id) of the oldest open tool_use.

        Returns None when no tool is open. The watchdog uses age to decide
        WHEN to probe CPU liveness, tool_name to pick a per-tool open window
        (Agent/Bash run legitimately long), and tool_id to re-verify identity
        after the probe sleep.
        """
        if not self._open_tool_uses:
            return None
        # value = (start_time, name); oldest = smallest start_time.
        oldest_id = min(
            self._open_tool_uses,
            key=lambda k: self._open_tool_uses[k][0],
        )
        start, name = self._open_tool_uses[oldest_id]
        return (time.time() - start, name, oldest_id)

    def _tool_open_window(self, tool_name: str) -> float:
        """Per-tool open window before CPU-liveness probing kicks in.

        Long window for CPU-heavy tools (Agent/Bash) AND I/O-wait tools
        (WebFetch, MCP) — the latter sit at CPU=0 while blocked on the
        network, where the CPU probe can't tell "waiting" from "wedged".
        """
        if tool_name in self._LONG_TOOLS:
            return self.TOOL_HANG_OPEN_S_LONG
        if tool_name.startswith(self._LONG_TOOL_PREFIXES):
            return self.TOOL_HANG_OPEN_S_LONG
        return self.TOOL_HANG_OPEN_S

    async def _maybe_escape_wedged_tool(
        self, pid: int, tool_age: float, tool_name: str, tool_id: str,
    ) -> None:
        """Probe tree CPU; warm-interrupt ONLY a genuinely wedged (0-CPU) tool.

        Called from the PID watchdog when an open tool_use has exceeded its
        per-tool open window. Samples cumulative tree CPU twice over
        CPU_PROBE_INTERVAL_S:

        - delta >= CPU_LIVE_EPSILON → the tool (or a descendant — Bash child,
          Agent sub-agent CLI) is actively working → DO NOTHING. This is the
          healthy-long-tool case v1 wrongly killed.
        - delta <  CPU_LIVE_EPSILON → nothing in the tree is doing work →
          genuine deadlock → interrupt(autonomous=True) (warm).
        - CPU unreadable (None) → FAIL SAFE: cannot prove dead → do nothing.
          The existing _compute_message_timeout force-kill backstop still
          owns a true API hang as the last resort.

        ``tool_id`` is the tool_use id sampled at call time; after the probe
        sleep we re-verify it is STILL the oldest open tool, else a tool that
        completed mid-sleep could be falsely attributed an interrupt (v2 MED).
        """
        from .resource_monitor import resource_monitor

        # Absolute hard ceiling: a tool open this long is force-killed REGARDLESS
        # of CPU (v2 MED — a tool pegging CPU forever, e.g. an infinite loop in
        # user code, would otherwise never be escaped since busy-CPU suppresses
        # both the warm tier and the open-tool backstop gate).
        if tool_age > self.TOOL_HANG_HARD_CEILING_S:
            logger.error(
                "session_unit.tool_hang_ceiling session_id=%s pid=%d tool=%s "
                "age=%.0fs > ceiling=%.0fs — force-killing regardless of CPU",
                self.session_id, pid, tool_name, tool_age,
                self.TOOL_HANG_HARD_CEILING_S,
            )
            self._pid_watchdog_task = None  # prevent self-cancel
            self._transition(SessionState.DEAD)
            await self._force_kill()
            return

        cpu0 = resource_monitor.tree_cpu_seconds(pid)
        if cpu0 is None:
            return  # fail-safe: cannot measure → never interrupt
        await asyncio.sleep(self.CPU_PROBE_INTERVAL_S)
        # State may have changed during the sleep (turn ended, killed).
        if self.state != SessionState.STREAMING:
            return
        # The sampled tool may have completed during the sleep — re-verify it
        # is still open AND still the oldest, else bail (v2 MED stale-tool race).
        current = self._oldest_open_tool()
        if current is None or tool_id not in self._open_tool_uses:
            return
        cpu1 = resource_monitor.tree_cpu_seconds(pid)
        if cpu1 is None:
            return
        delta = cpu1 - cpu0
        if delta >= self.CPU_LIVE_EPSILON:
            # Tool is actively working — not a hang. Leave it alone.
            logger.debug(
                "session_unit.tool_live session_id=%s tool=%s age=%.0fs "
                "cpu_delta=%.3f — working, not interrupting",
                self.session_id, tool_name, tool_age, delta,
            )
            return

        logger.warning(
            "session_unit.tool_hang_interrupt session_id=%s pid=%d tool=%s "
            "age=%.0fs cpu_delta=%.3f (< %.2f) — wedged, interrupting (warm)",
            self.session_id, pid, tool_name, tool_age, delta,
            self.CPU_LIVE_EPSILON,
        )
        # Dedicated escalation counter (NOT the context-gated unstick counter).
        # Incremented BEFORE the interrupt: escalation must fire even if a prior
        # interrupt failed (a wedged tool that ignores warm interrupts must still
        # reach the force-kill escalation). The tier-gating LATCH, by contrast, is
        # set only AFTER a SUCCESSFUL interrupt (below) — a failed interrupt must
        # not latch the tier off, or the hard-ceiling + escalation become
        # unreachable for a still-wedged tool (F2).
        self._tool_hang_episodes += 1

        # R3e (M4): the INTERRUPT-vs-force-kill escalation DECISION routes through
        # the one recovery authority (GracefulEscalationPolicy) — same shape as M3
        # streaming-timeout, different ladder: base=PROCEED_INTERRUPT (warm,
        # non-destructive), escalated=PROCEED_KILL (force-kill for fresh respawn).
        # The caller still owns the episode counter + limit + the increment-before
        # semantics above; the Coordinator owns only the ladder verdict.
        from .session_healing import RecoveryVerdict
        try:
            _decision = self._recovery_coordinator.decide_graceful(
                trigger="tool_hang",
                enabled=True,
                user_stopped=self._user_stopped_current_turn,
                state=self.state.value,
                attempt=self._tool_hang_episodes,
                threshold=self._TOOL_HANG_EPISODE_LIMIT,
                base=RecoveryVerdict.PROCEED_INTERRUPT,
                escalated=RecoveryVerdict.PROCEED_KILL,
            )
        except Exception:
            # The policy is pure (no I/O), so this is defensive: a coordinator
            # failure must NOT leave the speculative increment (line above)
            # inflated, which would skew the next escalation boundary. Back it
            # out and bail — the hard-ceiling backstop + _compute_message_timeout
            # force-kill still own a truly-wedged tool as last resort.
            self._tool_hang_episodes -= 1
            raise

        if _decision.verdict is RecoveryVerdict.SKIP:
            # User stopped this turn — back out the speculative episode increment
            # (this was not a genuine escalation episode) and leave it alone.
            self._tool_hang_episodes -= 1
            return

        # AC4 escalation: if warm interrupts keep failing to clear the hang
        # (the model reroutes straight back into a wedging tool), a warm
        # interrupt is no longer helping — escalate to the destructive
        # force-kill so the retry path can respawn fresh, instead of looping.
        if _decision.verdict is RecoveryVerdict.PROCEED_KILL:
            logger.error(
                "session_unit.tool_hang_escalate session_id=%s episodes=%d "
                "> limit=%d — force-killing for fresh respawn",
                self.session_id, self._tool_hang_episodes,
                self._TOOL_HANG_EPISODE_LIMIT,
            )
            self._pid_watchdog_task = None  # prevent self-cancel (see backstop)
            self._transition(SessionState.DEAD)
            await self._force_kill()
            return

        # PROCEED_INTERRUPT (base rung): warm, non-destructive interrupt.
        try:
            ok = await self.interrupt(autonomous=True)
        except Exception as exc:
            logger.error(
                "session_unit.tool_hang_interrupt_failed session_id=%s: %s",
                self.session_id, f"{type(exc).__name__}: {exc}",
            )
            return
        # Arm the backstop grace window AND the once-per-episode latch ONLY on a
        # SUCCESSFUL interrupt — a failed interrupt must not suppress the
        # force-kill backstop (adversarial v1 MED) NOR latch the tier off (F2:
        # the latch gates the whole tier via `not self._tool_hang_interrupted`
        # in the watchdog; latching it on a failed interrupt makes the hard
        # ceiling + escalation unreachable for a still-wedged tool).
        if ok:
            self._tool_hang_interrupt_at = time.time()
            # Once-per-episode guard (cleared on STREAMING entry + ToolResultBlock).
            self._tool_hang_interrupted = True

    # ── Tool-FREE hang verdict (run_dcd668a6) ────────────────────

    async def _tool_free_hang_verdict(self, pid: int) -> str:
        """CPU-liveness verdict for a TOOL-FREE streaming hang.

        The tool-free sibling of ``_maybe_escape_wedged_tool``'s CPU probe.
        Called at the MESSAGE_TIMEOUT/INIT_TIMEOUT mark (from the streaming
        orchestrator's TimeoutError handler and the ``_pid_watchdog_loop``
        output-liveness backstop) to decide: is the subprocess genuinely
        wedged, or alive-but-slow (heavy inference)?

        Samples cumulative tree CPU across ``TOOL_FREE_VERDICT_SAMPLES``
        windows of ``CPU_PROBE_INTERVAL_S`` each:

        - ANY window with delta >= ``CPU_LIVE_EPSILON`` → return ``'working'``
          EARLY (the tree is burning CPU — it's inferring, not hung). Multi-
          sample (not single) so a transient CPU dip during slow streaming is
          not mistaken for a wedge.
        - ALL windows < epsilon (and every sample readable) → ``'wedged'``.
        - ANY sample unreadable (``tree_cpu_seconds`` → None) → ``'unknown'``
          (FAIL SAFE — the caller treats unknown as "cannot prove alive" and
          falls back to its current kill/raise behavior; never hang forever on
          an unmeasurable process).

        Uses ``await asyncio.sleep`` between samples so it NEVER sync-blocks the
        shared event loop — other sessions' reads and heartbeats proceed during
        the ~``SAMPLES × INTERVAL`` sampling window. Pure judge: performs no
        kill/interrupt and mutates no state.

        If the session leaves STREAMING mid-sampling, returns ``'wedged'`` — the
        turn is no longer live, so the caller's kill/raise path (cleanup) is the
        correct outcome; returning 'working' there would let a dead session
        re-extend forever (adversarial round-3 finding).
        """
        from .resource_monitor import resource_monitor

        saw_unknown = False
        for _ in range(max(1, self.TOOL_FREE_VERDICT_SAMPLES)):
            if self.state != SessionState.STREAMING:
                return "wedged"
            cpu0 = resource_monitor.tree_cpu_seconds(pid)
            await asyncio.sleep(self.CPU_PROBE_INTERVAL_S)
            if self.state != SessionState.STREAMING:
                return "wedged"
            cpu1 = resource_monitor.tree_cpu_seconds(pid)
            if cpu0 is None or cpu1 is None:
                saw_unknown = True
                continue
            if (cpu1 - cpu0) >= self.CPU_LIVE_EPSILON:
                return "working"
        return "unknown" if saw_unknown else "wedged"

    # ── Adaptive timeout ─────────────────────────────────────────

    def _compute_message_timeout(self) -> float:
        """Timeout that scales with context size and resume state.

        Empirical: Opus 4.6 TTFT scales roughly with context tokens.
        At 2M tokens, inference can take 400-600s — a fixed 300s guarantees
        timeout → retry → timeout dead loops.

        Formula: max(300, context_tokens / 3000), capped at 900s.
        Resume multiplier: --resume sessions need extra time for context
        deserialization + extended thinking on large conversation history.
        Apply 2x multiplier when _sdk_session_id is set (cap at 1800s).
        """
        BASE_TIMEOUT = 300.0
        MAX_TIMEOUT = 900.0
        RESUME_MULTIPLIER = 2.0
        RESUME_MAX_TIMEOUT = 1800.0
        TOKENS_PER_SECOND = 3000  # Conservative model throughput estimate

        estimated_context = getattr(self, "_last_known_context_tokens", 0) or 0
        computed = max(BASE_TIMEOUT, estimated_context / TOKENS_PER_SECOND)
        computed = min(computed, MAX_TIMEOUT)

        # Resume sessions: context deserialization + extended thinking on
        # large conversation history takes significantly longer. The SDK
        # replays the full conversation before inference begins.
        if getattr(self, "_sdk_session_id", None):
            computed = min(computed * RESUME_MULTIPLIER, RESUME_MAX_TIMEOUT)

        return computed

    def _compute_init_timeout(self) -> float:
        """First-message (init) timeout (run_4b74b764, Part B).

        180s is right for a FRESH spawn (the subprocess just needs to emit its
        init/system message). But a ``--resume`` first message replays the FULL
        conversation before inference begins — at 2.4M restored tokens that
        easily exceeds 180s, so a fixed floor guillotines a healthy resume →
        kill → respawn (the ``init_timeout`` events in the logs). For resume
        sessions floor the init timeout at the adaptive message timeout (already
        resume-multiplied + capped); fresh sessions keep the fast 180s.

        SINGLE SOURCE of the init-timeout policy: the streaming orchestrator
        calls THIS (no inline re-derivation), so a regression test exercising
        this method actually guards the shipped behavior.
        """
        FRESH_INIT_TIMEOUT = 180.0
        if getattr(self, "_sdk_session_id", None):
            return max(FRESH_INIT_TIMEOUT, self._compute_message_timeout())
        return FRESH_INIT_TIMEOUT

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

    def _should_poison_guard_recycle(self) -> bool:
        """The poison_guard predicate (real, testable — extracted so a test can
        drive it instead of recomputing the boolean inline, which would be
        vacuous test-theater; Gate-2, run_f107f442).

        Recycle a warm IDLE subprocess before reuse IFF it may be poisoned:
        IDLE ∧ live client ∧ last-turn-NOT-clean ∧ NOT fresh-prewarm. This is the
        EXACT COMPLEMENT of session_router._is_warm_reuse.

        `_is_fresh_prewarm` (Gate-1 #5, R27): a fresh `prewarm-` unit OR a
        just-adopted unit (prefix already re-keyed away, bridged by the one-shot
        `_adopted_prewarm_fresh` which is cleared at STREAMING entry) has no
        corrupt turn-state to recycle. A genuine turn-2+ zombie (prefix gone AND
        bridge cleared) still recycles correctly.
        """
        from .session_router import PREWARM_SESSION_PREFIX
        _is_fresh_prewarm = (
            self.session_id.startswith(PREWARM_SESSION_PREFIX)
            or self._adopted_prewarm_fresh
        )
        return (
            self.state == SessionState.IDLE
            and self._client is not None
            and not self._last_turn_clean
            and not _is_fresh_prewarm
        )

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
            LEAK_RESTART_MARKER,
            _build_error_event,
            _is_retriable_error,
            _sanitize_sdk_error,
        )

        # ── Layer 0: Advance generation + clear stale interrupt state ─
        # Must happen BEFORE anything else so a concurrent interrupt()
        # that resumes from an await sees the bumped counter and aborts.
        # All four clears are synchronous (no await between them), so
        # no other coroutine can interleave and re-set them.
        self._send_generation += 1
        self._stop_event.clear()
        self._interrupted = False
        # New user turn — clear the durable "stopped" flag so a prior Stop does
        # not suppress self-heal on this genuinely new turn.
        self._user_stopped_current_turn = False
        # Clear stale recovery checkpoint from a prior aborted kill
        # (e.g. RSS spike armed checkpoint → spike subsided → kill didn't fire).
        # Prevents an unrelated later restart from injecting stale context.
        self._heal_checkpoint = None
        # Clear any stale recycle-kill marker so a prior turn's recycle can't
        # mislabel a genuine OOM on this turn as ZOMBIE.
        self._recycle_kill_pending = False

        # Store app_session_id for downstream use by _retry_with_resume's
        # abandon-fallback path (build_resume_context needs the stable
        # persistence key, not the transient sdk_session_id).
        self._app_session_id = app_session_id
        self._last_send_options = options
        self._last_send_config = config

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
            # Cancel the flush task — it's safe now because flush no longer
            # kills the subprocess (2026-06-20 SSE reliability fix).  The
            # subprocess may still be executing a tool call; SDK turn
            # serialization ensures our new send() is queued until the
            # current turn completes.
            #
            # The old "cross-turn bleed P0" concern (stale pipe data) is
            # no longer applicable: we're not interrupting/killing the
            # subprocess, so its current turn will complete normally and
            # the SDK manages turn boundaries.
            self._pipe_flush_task.cancel()
            try:
                await self._pipe_flush_task
            except (asyncio.CancelledError, Exception):
                logger.debug("send: suppressed exception on a fail-safe path", exc_info=True)
                pass
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
            # Root-1 SSOT Phase 2 (L4, F3): a new message arriving while a
            # tool_use (AskUserQuestion / cmd_permission) is OUTSTANDING must NOT
            # be auto-treated as the answer, and must NOT kill→COLD→--resume (the
            # abandoned-ask replay bug). Reject as SessionBusyError so the router
            # converts it to a pending message (sent=0); the drain worker delivers
            # it only AFTER the question is answered and the session returns to a
            # clean IDLE with no outstanding tool_use (drain_pending precondition).
            if self.has_outstanding_tool_use:
                # Dead-waiter reap FIRST (run_65f317db): if the outstanding
                # tool_use has NO live waiter (the hook coroutine was cancelled —
                # e.g. SDK control_cancel_request — leaving _pending_tool_use_id
                # stranded), the prompt can NEVER be answered. Recover to COLD
                # instead of raising SessionBusyError forever (the approve-into-void
                # deadlock: without this, every subsequent send() on this session
                # raised SessionBusyError permanently). reap returns True + leaves
                # state COLD → fall through to spawn-with-resume below.
                if await self.reap_dead_waiting_input():
                    pass  # state is now COLD — fall through to spawn
                else:
                    # A GENUINELY-open prompt (live waiter) — queue the message.
                    from .exceptions import SessionBusyError
                    logger.info(
                        "session_unit.waiting_input_pending session_id=%s "
                        "tool_use=%s — new message queued (not auto-answered, F3)",
                        self.session_id, self._pending_tool_use_id,
                    )
                    raise SessionBusyError(
                        detail=(
                            f"Session {self.session_id} is waiting for an answer to a "
                            f"pending question. Your message has been queued and will "
                            f"be sent after the question is resolved."
                        ),
                    )
            else:
                # No outstanding tool_use but stuck in WAITING_INPUT (frontend
                # crashed mid-question / stale state) — genuinely abandoned,
                # recover to COLD.
                logger.warning(
                    "session_unit.auto_recover_waiting_input session_id=%s "
                    "— WAITING_INPUT with no outstanding tool_use (abandoned), "
                    "forcing COLD for recovery",
                    self.session_id,
                )
                await self.force_unstick_waiting_input()
                # After force_unstick, state is COLD — fall through to spawn

        if self.state == SessionState.DEAD:
            # DEAD is recoverable at send-time, NOT a dead-end. Two DEAD sources
            # both strand the unit here until lifecycle_manager's 60s _cleanup_dead
            # loop happens to run — so the FIRST resume reliably failed:
            #   (a) a flush_recycle / interrupt_recycle whose _crash_to_cold_async
            #       was ORPHANED mid-teardown — send() itself cancels the in-flight
            #       _pipe_flush_task above (~:1575), and the CancelledError unwinds
            #       out of the locked DEAD→…→COLD sequence, releasing the lock with
            #       the unit still DEAD (COLD never reached by that path).
            #   (b) a watchdog kill (_pid_watchdog_loop / _maybe_escape_wedged_tool)
            #       that sets DEAD + force_kills then returns WITHOUT a lock or a
            #       COLD transition — a legitimately STABLE DEAD whose only other
            #       exit is the 60s loop.
            # Drive recovery ourselves, mirroring force_unstick_streaming /
            # force_unstick_waiting_input above. _crash_to_cold_async is idempotent
            # (no-ops if already COLD) and acquires self._lock, so it SERIALIZES
            # against any genuinely in-flight kill/teardown (no double-kill) rather
            # than blindly racing it. clear_identity=False preserves _sdk_session_id,
            # so the COLD→_ensure_spawned path below respawns WITH --resume (resume
            # rides solely on _sdk_session_id being set). If a prior give-up already
            # cleared the identity, the COLD path simply spawns fresh — also correct.
            logger.warning(
                "session_unit.auto_recover_dead session_id=%s — DEAD at send() "
                "(orphaned recycle or watchdog kill); forcing COLD for resume "
                "instead of waiting for the 60s lifecycle cleanup",
                self.session_id,
            )
            await self._crash_to_cold_async(clear_identity=False)
            # After recovery, state is COLD — fall through to spawn-with-resume.

        if self.state not in (SessionState.COLD, SessionState.IDLE):
            raise RuntimeError(
                f"Cannot send() in state {self.state.value} "
                f"(session_id={self.session_id})"
            )

        self._reset_turn_state()

        # ── Resume-poison guard (fail-closed) — recycle-before-reuse ────
        # If we are about to REUSE a warm IDLE subprocess that did NOT end its
        # last turn via the success-result transition, it may be poisoned by a
        # prior soft-interrupt / SSE-disconnect (PIT01): the CLI is in corrupt
        # turn-state and the first message would return an instant empty
        # error_during_execution (zombie) → kill+retry → "first resume fails".
        # The existing flush/interrupt recycle runs AFTER an `await interrupt()`
        # and is cancel-raced by this very send() (it cancels the in-flight
        # flush task above), so it cannot be relied on. Recycle eagerly HERE,
        # before reuse, via the blessed kill path (clear_identity=False preserves
        # --resume identity); the COLD branch below then spawns a clean process
        # WITH --resume. Clean turns skip this entirely → warm fast path intact.
        # MUST be checked here (after the reset batches, before reuse) and the
        # flag MUST NOT be in those reset batches, or every reuse looks poisoned.
        # _force_kill is anchored to self.pid only — it never touches a sibling
        # session's subprocess tree (per-session isolation).
        # Gate-1 F1: exempt a fresh PREWARM unit by its session-id PREFIX, NOT by
        # `_sdk_session_id is None`. A first-message turn that ended unclean
        # (crash, interrupt) leaves a NORMAL unit at the identical shape
        # (IDLE + client + _last_turn_clean=False + _sdk_session_id=None) — that
        # is a real zombie the guard MUST still recycle. Only a `prewarm-` id is a
        # genuinely never-used, adoptable warm subprocess; recycling it would
        # defeat the prewarm. The prefix is the ONLY signal that distinguishes the
        # two, so the exemption keys on it (lazy import avoids the session_router↔
        # session_unit circular dependency — same pattern as lifecycle_manager).
        # Glitch-poisoned history from the previous turn (see _transcript_poisoned):
        # clean it before the CLI reads it again, so the model can't copy it.
        # (getattr: some fixtures build a unit with __new__, skipping __init__.)
        if getattr(self, "_transcript_poisoned", False):
            await self._clean_poisoned_transcript()

        if self._should_poison_guard_recycle():
            logger.info(
                "session_unit.poison_guard_recycle session_id=%s — warm IDLE "
                "subprocess not clean (last turn did not complete via result); "
                "recycling to COLD before reuse to avoid zombie first-send",
                self.session_id,
            )
            await self._arm_recovery_checkpoint("poison_guard_recycle")
            await self._crash_to_cold_async(clear_identity=False)

        # Spawn if needed (COLD → IDLE under _spawn_lock + _env_lock)
        # Also respawn if IDLE but client is gone (CLI exited after
        # error_max_turns — state stayed IDLE for hooks/slots, but
        # process is dead). Transition to COLD first so _ensure_spawned
        # works correctly.
        _reader = getattr(self, "_stream_reader", None)
        if self.state == SessionState.IDLE and _reader is not None and _reader.dead:
            # The CLI's stream failed or ended between turns: the subprocess is gone.
            # Respawn with --resume (identity kept) instead of writing into it.
            logger.warning(
                "session_unit.dead_stream_respawn session_id=%s error=%s",
                self.session_id, type(_reader.error).__name__,
            )
            await self._crash_to_cold_async(clear_identity=False)
        if self.state == SessionState.IDLE and self._client is None:
            self._transition(SessionState.COLD)
        if self.state == SessionState.COLD:
            yield turn_phase_event(TURN_PHASE_STARTING)
            async for event in self._ensure_spawned(options, config):
                if event.get("_abort"):
                    return  # spawn failed after retries
                yield event
            # A clean respawn consumed the recycle marker's only legitimate
            # purpose (classifying THIS recycle's own SIGKILL as ZOMBIE via a
            # failed retry). Now that we have a fresh, alive subprocess, the
            # marker must NOT survive into the new stream — otherwise a genuine
            # OOM (-9) on THIS turn would be misclassified ZOMBIE (skipping the
            # 30/60/120s backoff + the _consecutive_oom_kills increment that
            # drives the OOM circuit breaker). The send-entry clear at the top
            # of send() runs BEFORE the poison-guard arm, so it cannot cover
            # this intra-turn recycle→respawn→OOM path. (Gate-2 MED, run_ed9647c5)
            if self.state == SessionState.IDLE:
                self._recycle_kill_pending = False

        # R6 Step B: concurrent-streaming admission gate (peak-OOM guard).
        # A NEW user turn waits if the daemon is already at MAX_CONCURRENT_STREAMS
        # simultaneous streams. continue_with_answer / continue_with_permission /
        # retry paths deliberately do NOT call this — they count toward the cap
        # but a user waiting on their own answer must never queue behind others.
        await self._await_streaming_slot()

        # Subprocess-IO barrier (run_4b74b764): if a compact() is running its
        # turn on the SAME subprocess, wait for it to finish before this turn
        # writes — two turns written at once fold into each other. This is a SHORT barrier (acquire→immediately release): we do
        # NOT hold _client_io across the streaming body, because the read loop
        # self-interrupts via the CompactionGuard and a user Stop must be able to
        # interrupt() (a lock-free control call) without waiting on this turn.
        # Acquiring then releasing guarantees serialization vs compact (which
        # holds the lock for its whole drain) without locking the long stream.
        async with self._client_io:
            pass

        # ── Post-wait state re-check (dead→streaming race guard) ─────────
        # send() holds NO self._lock across _await_streaming_slot() and the
        # _client_io barrier above — by design (the long stream must not hold
        # the lock; see the barrier comment). So a concurrent refresh_context()
        # / kill() / release (all take self._lock and drive the unit
        # →DEAD→COLD) can flip state OUT of a streamable state DURING those two
        # awaits. If we then blindly _transition(STREAMING), it raises
        # RuntimeError (DEAD/COLD→STREAMING is not in _VALID_TRANSITIONS) and
        # the exception escapes into the chat stream as a raw
        # "Invalid state transition …→streaming" (observed 2026-08-09,
        # sessions e31ffa19 / 52034139). This is a check-then-act TOCTOU that
        # the send-entry DEAD-recovery at ~:1863 cannot cover — the flip
        # happens AFTER that check, during the waits below it.
        #
        # The re-check and the _transition are in ONE synchronous block with
        # NO await between them — under asyncio's cooperative scheduling that
        # makes them atomic (no concurrent coroutine can interleave), so this
        # closes the window rather than merely narrowing it. We do NOT attempt
        # an inline re-spawn here (Gate-1 Axis-2: re-running _ensure_spawned
        # after the waits risks a double-spawn / _spawn_lock contention). A
        # refresh leaves the unit cleanly COLD with resume identity intact, so
        # a clean abort here makes the user's resend a clean cold-resume — the
        # correct recovery, not a half-spawned limbo. Mirror the existing
        # _ensure_spawned bail shape: yield a user-facing error + {_abort}.
        if self.state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            # Busy, not refreshed: a CLI-initiated turn (the router's sink) or a
            # concurrent send took the unit during the waits above. Raised, so the
            # router stores the message as pending (a yielded error would drop it).
            from .exceptions import SessionBusyError
            if getattr(self, "_injected_turns", None):
                self._emit_cli_event("injected")
            raise SessionBusyError(
                detail=(
                    f"Session {self.session_id} started another turn while this "
                    f"message was waiting. It has been queued and will be sent next."
                ),
            )
        if self.state != SessionState.IDLE:
            logger.warning(
                "session_unit.send_aborted_state_flip session_id=%s state=%s "
                "— a concurrent refresh/release/kill flipped the session out of "
                "IDLE during the streaming-slot/io wait; aborting cleanly before "
                "the STREAMING transition (resend will cold-resume)",
                self.session_id, self.state.value if self.state else "None",
            )
            yield _build_error_event(
                code="SESSION_BUSY",
                message="This session was refreshed or released while starting — send again to continue.",
                suggested_action="Send your message again; it will resume with a fresh context.",
            )
            yield {"_abort": True}
            return

        # Quiescence gate (I3): the CLI may be running a turn of its own (a
        # background task finished) or about to. Writing now would fold this
        # message into that turn. SessionBusyError makes the router store it as
        # pending; the reader's idle report re-drives the drain. Same synchronous
        # block as the state re-check and the transition (no await between).
        self._record_gate_mark()
        if not self.cli_quiescent:
            from .exceptions import SessionBusyError
            if self._injected_turns:
                # A CLI-initiated turn may have been passed over while this send
                # held the _client_io barrier: hand it to the sink again.
                self._emit_cli_event("injected")
            logger.info(
                "session_unit.send_deferred_cli_busy session_id=%s — the CLI is "
                "running or about to run a turn of its own; message queued",
                self.session_id,
            )
            raise SessionBusyError(
                detail=(
                    f"Session {self.session_id} is finishing work it started in the "
                    f"background. Your message has been queued and will be sent "
                    f"when it is done."
                ),
            )

        # IDLE → STREAMING
        self._transition(SessionState.STREAMING)
        self._model_name = getattr(options, "model", None)
        # The CLI's working directory: where a Bash script's outputs land when its
        # command names no path (streaming_orchestrator's post-Bash product sweep).
        _cwd = getattr(options, "cwd", None)
        self._cwd = str(_cwd) if _cwd else None

        # Fix #5: Capture original query before any system injections
        # so checkpoint stores the real user request, not modified text.
        _original_user_query = str(query_content)[:500] if query_content else ""

        # ── Graceful pre-kill injection (AC2) ──────────────────────
        # If turn_approaching was detected last turn, inject wrap-up prompt
        # so agent finishes naturally before the actual kill on next check.
        # One-shot: clear flag after injection (Fix #3: prevent stuck flag).
        _capturing_wrapup_turn = False
        if self._graceful_wrap_pending and isinstance(query_content, str):
            query_content = f"{query_content}\n\n---\n\n{WRAP_UP_PROMPT}"
            # This turn IS the wrap-up turn: capture the agent's emitted text
            # into _wrapup_conclusion so it can lead key_findings (GAP 2 / 2.5).
            _capturing_wrapup_turn = True
            self._wrapup_conclusion = ""  # fresh buffer for this wrap-up turn
            # Don't clear the flag here — cleared on actual kill or timeout.
            # The flag stays True so the heal check knows to proceed with kill.
            logger.info(
                "session_unit.self_heal_wrap_injected session_id=%s turn=%d",
                self.session_id, self._health_sensor.turn_count,
            )

        # ── Channel budget-aware wrap-up (Gap #13) ─────────────────
        # For channel sessions approaching turn limit, inject wrap-up prompt
        # suggesting the user continue on desktop. Independent of self-heal
        # (which may be disabled). Fires ONCE (one-shot guard).
        if (
            self.is_channel_session
            and isinstance(query_content, str)
            and not self._channel_wrap_injected  # One-shot: never re-inject
            and not self._graceful_wrap_pending  # Don't conflict with self-heal
            and self._health_sensor.turn_count
            >= (self._health_sensor._max_turns - CHANNEL_WRAP_BUFFER)
        ):
            query_content = f"{query_content}\n\n---\n\n{CHANNEL_WRAP_UP_PROMPT}"
            self._channel_wrap_injected = True  # One-shot consumed
            logger.info(
                "session_unit.channel_wrap_injected session_id=%s turn=%d max=%d",
                self.session_id,
                self._health_sensor.turn_count,
                self._health_sensor._max_turns,
            )

        # ── Desktop hard-floor graceful wrap-up (Root 2 / AC3, G2) ─
        # Absolute last-resort stop for DESKTOP sessions at max_turns-5.
        # Fires when self-heal can't carry the wrap-up — OFF, exhausted, or in
        # cooldown (not "unreachable when self-heal is ON"): without this, such a
        # session runs silently to the CLI's error_max_turns and truncates. Inject
        # a wrap-up so the agent delivers a non-empty conclusion before the hard
        # limit. One-shot owned HERE (the predicate is read-only); skipped when
        # self-heal already owns the wrap-up (_graceful_wrap_pending) or the
        # channel path handled it.
        if self._should_inject_hard_floor_wrap() and isinstance(query_content, str):
            query_content = f"{query_content}\n\n---\n\n{WRAP_UP_PROMPT}"
            self._hard_floor_wrap_injected = True  # One-shot consumed
            # Capture the agent's wrap-up text so the conclusion is non-empty
            # (AC3: no conclusion_len=0). Mirrors the self-heal capture path.
            _capturing_wrapup_turn = True
            self._wrapup_conclusion = ""
            logger.info(
                "session_unit.hard_floor_wrap_injected session_id=%s turn=%d max=%d",
                self.session_id,
                self._health_sensor.turn_count,
                self._health_sensor._max_turns,
            )

        # ── Heal checkpoint injection (invisible to user) ─────────
        # If a self-heal just happened, prepend continuation context to the
        # user's query so the agent knows to continue seamlessly.
        if self._heal_checkpoint is not None:
            continuation = self._heal_checkpoint.to_continuation_prompt()
            if isinstance(query_content, str):
                query_content = f"{continuation}\n\n---\n\n{query_content}"
            self._heal_checkpoint = None  # Consumed — don't re-inject

        try:
            self._leak_restart_eligible = not self._leak_restart_done
            # Pre-first-token phase for the UI: the query goes to the model now.
            yield turn_phase_event(TURN_PHASE_WAITING)
            try:
                async for event in self._streaming_orchestrator.stream_query(query_content):
                    if _capturing_wrapup_turn:
                        self._capture_wrapup_text(event)
                    yield event
            finally:
                self._leak_restart_eligible = False
            # Success — reset counters (session is healthy)
            self._consecutive_oom_kills = 0
            self._consecutive_unstick_timeouts = 0
            # ② zero-token hang guard: a healthy stream clears any accumulated
            # wedged strikes (the hang resolved on its own / a new turn began).
            self._resume_wedged_strikes = 0

            # ── Self-healing check (invisible to user) ────────────
            # After successful stream, check if session health is degrading.
            # If so, proactively heal (kill → respawn) so next turn starts fresh.
            # User sees nothing — this happens between turns, not mid-stream.
            #
            # Gate: SWARMAI_SELF_HEAL=1 (all, default) | 0 (off) | canary (first tab only)
            # Default is ON now that the recovery path is hardened (rich checkpoint
            # on every respawn + --resume context fallback). Set "0" to disable.
            _self_heal_enabled = is_self_heal_enabled(
                self.session_id, is_channel=bool(getattr(self, "_channel_id", None))
            )
            should_heal, trigger = self._health_sensor.should_checkpoint(
                session_state=self.state.value
            )
            # Fix #3: clear stale graceful flag if sensor no longer triggering
            if not should_heal and self._graceful_wrap_pending:
                self._graceful_wrap_pending = False
                self._wrapup_conclusion = ""  # don't leak into a later heal
            # R3: the self-heal DECISION now goes through the one recovery
            # authority. The Coordinator encapsulates the guard (enabled /
            # user-stopped / protected-state) + breaker (can_heal) + graceful
            # policy + escalation. It DELEGATES to _healing_loop, so behavior is
            # identical — this is a decision-routing change, not a behavior change.
            if should_heal:
                decision = self._recovery_coordinator.decide(
                    trigger,
                    enabled=_self_heal_enabled,
                    user_stopped=self._user_stopped_current_turn,
                    state=self.state.value,
                    graceful_pending=self._graceful_wrap_pending,
                )
                if decision.verdict is RecoveryVerdict.PROCEED_GRACEFUL:
                    # turn_approaching phase 1: set flag so next send() injects
                    # wrap-up prompt; the actual kill happens on the phase-2 pass.
                    self._graceful_wrap_pending = True
                    logger.info(
                        "session_unit.self_heal_graceful_pending "
                        "trigger=%s session_id=%s turn=%d "
                        "(will inject wrap-up prompt on next send)",
                        trigger, self.session_id, self._health_sensor.turn_count,
                    )
                elif decision.verdict is RecoveryVerdict.PROCEED_KILL:
                    # Actual heal: checkpoint → kill → respawn on next send()
                    logger.info(
                        "session_unit.self_heal trigger=%s session_id=%s turn=%d",
                        trigger, self.session_id, self._health_sensor.turn_count,
                    )
                    # Route lifecycle through the Coordinator (delegates to the
                    # same HealingLoop + manages terminal-signal state). Parity:
                    # identical breaker effect, plus the terminal flag stays in sync.
                    self._recovery_coordinator.record_heal_start(trigger=trigger)
                    # Build rich TaskCheckpoint (git floor + history enrichment).
                    # Workspace dir from standard path (same as context_injector)
                    _ws_dir = str(Path.home() / ".swarm-ai" / "SwarmWS")
                    # ── Per-trigger wrap-up policy (intentional) ──────
                    # turn_approaching → graceful two-phase wrap-up (handled
                    #   above: subprocess still healthy, ~20-turn buffer exists).
                    # memory_growth / error_cascade / hang_detected → immediate
                    #   kill (an extra turn would be slow, risk OOM, likely also
                    #   fail, or go unanswered). (latency_degradation removed —
                    #   run_099724ca: it false-killed healthy slow-but-alive turns.)
                    # For EVERY trigger the checkpoint is still enriched from
                    # history here (2.1, 2.4) before the kill.
                    _enrichment = await self._derive_heal_enrichment()
                    _conclusion = self._wrapup_conclusion
                    self._heal_checkpoint = await build_rich_checkpoint(
                        original_request=_original_user_query,
                        working_dir=_ws_dir,
                        file_tracker_paths=list(getattr(self, "_files_touched", [])),
                        turn_count=self._health_sensor.turn_count,
                        trigger=trigger,
                        heal_attempt=self._recovery_coordinator.heal_attempts,
                        agent_conclusion=_conclusion,
                        completed_steps=_enrichment.get("completed_steps"),
                        pending_steps=_enrichment.get("pending_steps"),
                        key_findings=_enrichment.get("key_findings", ""),
                        pipeline_run_id=getattr(self, "_pipeline_run_id", None),
                        pipeline_stage=getattr(self, "_pipeline_stage", None),
                    )
                    # Observability: field names + lengths only (no content/PII)
                    _cp = self._heal_checkpoint
                    logger.info(
                        "session_unit.self_heal_checkpoint session_id=%s "
                        "trigger=%s fields=[completed=%d,pending=%d,findings=%d,"
                        "pipeline=%s,active_file=%d] conclusion_len=%d "
                        "approx_chars=%d",
                        self.session_id, trigger,
                        len(_cp.completed_steps), len(_cp.pending_steps),
                        len(_cp.key_findings), bool(_cp.pipeline_run_id),
                        len(_cp.active_file_context), len(_conclusion),
                        len(_cp.to_continuation_prompt()),
                    )
                    logger.info(
                        "session_unit.self_heal_wrap_outcome session_id=%s "
                        "captured=%s len=%d",
                        self.session_id, bool(_conclusion), len(_conclusion),
                    )
                    self._graceful_wrap_pending = False
                    self._wrapup_conclusion = ""  # one-shot: consumed
                    # Kill subprocess but keep _sdk_session_id (for --resume).
                    # Fix #9: wrap in try/except to prevent inconsistent state
                    try:
                        await self._crash_to_cold_async(clear_identity=False)
                        self._health_sensor.reset()
                        self._recovery_coordinator.record_heal_success()
                    except Exception as heal_exc:
                        self._heal_checkpoint = None  # Discard unusable
                        self._wrapup_conclusion = ""  # don't leak into a later heal
                        self._recovery_coordinator.record_heal_failure(str(heal_exc))
                        logger.error(
                            "session_unit.self_heal_failed session_id=%s: %s",
                            self.session_id, str(heal_exc)[:200],
                        )
                    # Next send() will detect state=COLD → _ensure_spawned
                elif decision.verdict is RecoveryVerdict.ESCALATE:
                    # Breaker tripped — recovery itself is failing. Surface the
                    # Coordinator's one-shot terminal signal to the USER as an
                    # in-band recovery_exhausted SSE event (decision #3, pulled
                    # forward from R4) so the session is not a silent dead-end.
                    logger.warning(
                        "session_unit.self_heal_exhausted session_id=%s "
                        "trigger=%s attempts=%d terminal=%s",
                        self.session_id, trigger, self._healing_loop.heal_attempts,
                        self._recovery_coordinator.terminal_recovery_reached,
                    )
                    _rex_event = self._maybe_recovery_exhausted_event(trigger)
                    if _rex_event is not None:
                        yield _rex_event
                # DEFER (cooldown) and SKIP (guard) → no action this tick.
        except Exception as exc:
            error_str = str(exc)
            tb_str = traceback.format_exc()
            logger.error(
                "Error during streaming for session %s: %s",
                self.session_id, error_str[:200],
            )

            # ── Repeated tool-call leak — clean transcript + resume (once) ──
            # The orchestrator raises this only on the primary stream and only
            # once per message, so it can never loop. On success the user sees
            # the turn continue; nothing is shown about the restart.
            if LEAK_RESTART_MARKER in error_str:
                from .session_utils import LEAK_RESTART_KIND_PREFIX

                recovered = False
                async for event in self._retry_manager._handle_leak_poison_restart(
                    options, config,
                    kind="prefix" if LEAK_RESTART_KIND_PREFIX in error_str else "leak",
                ):
                    if event.get("_abort"):
                        return  # spawn failed (error already yielded)
                    if event.get("_recovered"):
                        recovered = True
                        continue
                    if "_fallthrough_error" in event:
                        error_str = event["_fallthrough_error"]
                        tb_str = event.get("_fallthrough_tb", tb_str)
                        continue
                    yield event
                if recovered:
                    return
                # Resumed stream failed — fall through to the normal handling.

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

            # ── A turn the reader abandoned (CLI alive) — never the crash path ──
            from .cli_stream_reader import is_turn_abandoned
            if is_turn_abandoned(error_str):
                async for event in self._end_abandoned_turn(error_str):
                    yield event
                return

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

        # ── Credential pre-flight (Gate-1 fix) ───────────────────────────
        # Before spawning the CLI subprocess, check credentials. An EXPIRED
        # token makes the SDK stall retrying the failing credential_process
        # (the "spinner spins forever" bug). Fail FAST with an actionable
        # error instead. FAIL-OPEN on "unknown" (network blip / non-auth STS
        # error) — only a DEFINITIVE "expired" blocks, so a transient blip
        # never falsely blocks a valid session. "valid" also falls through.
        try:
            from . import session_registry
            from .app_config_manager import AppConfigManager
            from .auth_remediation import remediation_for

            _cfg = AppConfigManager.instance()
            _use_bedrock = _cfg.get("use_bedrock", True)
            _auth_method = _cfg.get("auth_method")
            # Skip the AWS STS check when the active auth uses NO sigv4 identity:
            #   - Anthropic-direct (API-key) mode: use_bedrock=False, no AWS at all
            #   - Bedrock API-key mode: use_bedrock=True BUT auth is a bearer token
            #     (AWS_BEARER_TOKEN_BEDROCK), which has no STS-resolvable identity —
            #     an STS call would falsely report "expired" and block the spawn.
            # In both cases STS is meaningless; skip it entirely (AC4/AC5).
            if not _use_bedrock or _auth_method == "bedrock_api_key":
                logger.debug(
                    "session_unit.preflight session_id=%s use_bedrock=%s "
                    "auth_method=%s — skipping AWS STS check (no sigv4 identity)",
                    self.session_id, _use_bedrock, _auth_method,
                )
            else:
                _region = _cfg.get("aws_region", "us-east-1")
                _auth = await session_registry.get_credential_validator().check(_region)
                if _auth == "expired":
                    # Remediation must match the actual method — use_bedrock can't
                    # tell ada from sso, so read the persisted auth_method (AC5).
                    _rem = remediation_for(_cfg.get("auth_method"))
                    logger.warning(
                        "session_unit.preflight session_id=%s creds expired "
                        "(method=%s) — aborting spawn (avoids invoke stall)",
                        self.session_id, _cfg.get("auth_method"),
                    )
                    yield _build_error_event(
                        code="CREDENTIALS_EXPIRED",
                        message=_rem["message"],
                        suggested_action=_rem["fix_text"],
                    )
                    yield {"_abort": True}
                    return
        except Exception as exc:  # noqa: BLE001 — pre-flight must never block spawn on its own bug
            # A bug in the pre-flight itself must not become a self-inflicted
            # outage. Log and fall through to the normal spawn path.
            logger.warning(
                "session_unit.preflight session_id=%s check raised %s — "
                "proceeding to spawn (fail-open)",
                self.session_id,
                type(exc).__name__,
            )

        # ── Resume injection: COLD + _sdk_session_id → spawn with --resume
        # Covers ALL kill-then-respawn paths (proactive restart, eviction,
        # OOM crash, streaming timeout) — not just the retry path.
        # This fixes an entire class of context-loss bugs where kill()
        # preserved _sdk_session_id but the next spawn didn't use it.
        _attempted_resume = False
        if self._sdk_session_id:
            options = self._build_retry_options(options, self._sdk_session_id)
            _attempted_resume = True
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

        # ── Fallback chain (PE F11, Design §2B): stale sdk_session_id ──
        # If --resume failed because the session file no longer exists on disk,
        # clear the stale ID and retry as cold resume (no --resume flag).
        # This prevents infinite retry loops on invalid session IDs.
        if _attempted_resume:
            from .session_utils import is_session_not_found_error
            if is_session_not_found_error(error_str):
                # ── Restart silent-history-loss fix (run_af851c26) ──────────
                # A persisted `_sdk_session_id` (re-injected after a graceful
                # restart, session_router.py:1850) made `is_cold_resume` FALSE
                # upstream → `options` was built on a NON-cold-resume turn and
                # carries NO DB history block. The OLD code here cold-spawned
                # that history-less `options` and landed IDLE+warm → the entire
                # conversation was silently lost for that subprocess's life.
                #
                # Instead: re-arm a TRUE cold resume (reuse refresh_context's
                # proven pattern) — transition to COLD + clear identity so the
                # NEXT send()'s run_conversation sees `is_cold_resume=True` and
                # `build_resume_context` re-injects the DB history. Do NOT spawn
                # a history-less subprocess here. The current turn bails via the
                # TWO-FRAME emit below — the ERROR frame carries `history_dropped`
                # (it is the one that gets FORWARDED), which the router (owner of
                # `persisted_msg_id`) uses on its forward path to DURABLY SAVE the
                # current user message as a pending (sent=0) row. A bare `_abort`
                # sentinel follows only to end the turn (it carries nothing).
                #
                # ⚠️ RECOVERY IS NEXT-INTERACTION, NOT AUTONOMOUS (Gate-2, run_af851c26):
                # the unit is left COLD, and the drain worker only fires on a
                # transition INTO IDLE (session_router `_on_unit_state_change`) —
                # nothing drives COLD→IDLE autonomously. So the saved message is
                # delivered, WITH full DB history re-injected, on the user's NEXT
                # send (which spawns COLD→IDLE and coalesces the pending row). The
                # pre-fix DATA LOSS is fixed (message + history both survive in the
                # DB); what remains is a one-interaction latency, not silent loss.
                # Do NOT claim "sends automatically". (Gate-1 Check1/Check5.)
                logger.warning(
                    "Session %s: --resume failed (session not found) — stale "
                    "persisted sdk_session_id, re-arming cold resume to recover "
                    "DB history (was silently lost pre-fix)",
                    self.session_id,
                )
                _resume_degrade_count["session_not_found_recovered"] += 1
                # clear_identity=True drops _sdk_session_id → next send() is a
                # true cold resume (is_cold_resume requires id is None).
                await self._crash_to_cold_async(clear_identity=True)
                # TWO FRAMES (run_2d2e3258, mirrors CREDENTIALS_EXPIRED ~2560):
                # a SINGLE dict carrying BOTH error fields AND `_abort` dies in
                # send()'s `if event.get("_abort"): return` (~2099) BEFORE it is
                # yielded up — so the frame never reaches run_conversation, the
                # user never sees the reassurance, AND the router's
                # history_dropped→mark_pending never fires (silent data-loss).
                # Fix: frame 1 = the error event carrying `history_dropped` but
                # NO `_abort` → send() FORWARDS it (its `yield event`) → the
                # router both renders it to the user AND runs mark_pending on the
                # forward path. frame 2 = a bare `{_abort}` sentinel → send()
                # swallows it (return), ending the turn. history_dropped MUST ride
                # frame 1 (the forwarded one), never frame 2 (the swallowed one),
                # or mark_pending never runs and the message strands sent=1.
                event = _build_error_event(
                    "SESSION_RECOVERING",
                    "This conversation's history is being restored — your last "
                    "message is saved and will be sent when you continue.",
                )
                event["history_dropped"] = True  # frame1: router → mark_pending_by_id
                yield event
                yield {"_abort": True}  # frame2: bare sentinel, send() swallows
                return

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
                _recycle = self._recycle_kill_pending
                self._recycle_kill_pending = False
                failure_type, failure_meta = classify_failure(
                    error_str, self._hook_session_context,
                    recycle_kill=_recycle,
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
        options: "ClaudeAgentOptions",
        config: Optional[Any],
        error_str: str,
    ) -> AsyncIterator[dict]:
        """Recover from CLI 10MB JSONRPC buffer overflow.

        Implementation in RetryManager. Delegation stub.
        """
        async for event in self._retry_manager._handle_buffer_overflow(
            query_content, options, config, error_str
        ):
            yield event

    async def _retry_continuation(
        self, exc: Exception, tb_str: str,
    ) -> AsyncIterator[dict]:
        """A permission/answer continuation's process died: resume like send() does.

        Before this, the continue paths only crashed to COLD and re-raised, so a
        killed CLI left the turn stopped until the user typed again (366718fc,
        2026-10-20 13:22). Same retry as send(): --resume, then the leak-restart
        continuation prompt, since the user's own message was already answered.
        Non-retriable errors, or no send() options yet, keep the old behaviour.
        """
        from .session_utils import _is_retriable_error

        error_str = str(exc)
        if (
            self._last_send_options is None
            or not _is_retriable_error(error_str, tb_str)
            or self._retry_count >= self.MAX_RETRY_ATTEMPTS
        ):
            await self._crash_to_cold_async(clear_identity=False)
            raise exc
        logger.warning(
            "session_unit.continuation_retry session_id=%s error=%s",
            self.session_id, error_str[:120],
        )
        async for event in self._retry_with_resume(
            StreamingOrchestrator._GLITCH_CONTINUATION_PROMPT,
            self._last_send_options, self._last_send_config, error_str, tb_str,
        ):
            if event.get("_abort"):
                return
            yield event

    async def _retry_with_resume(
        self,
        query_content: Any,
        options: "ClaudeAgentOptions",
        config: Optional[Any],
        initial_error_str: str,
        initial_tb_str: str,
    ) -> AsyncIterator[dict]:
        """Retry loop with failure-aware backoff and --resume.

        Implementation in RetryManager. Delegation stub.
        """
        async for event in self._retry_manager._retry_with_resume(
            query_content, options, config, initial_error_str, initial_tb_str
        ):
            yield event

    async def _inject_abandon_continuation(
        self,
        query_content: Any,
    ) -> tuple[Any, bool]:
        """Build enriched continuation on retry timeout.

        Implementation in RetryManager. Delegation stub.
        """
        return await self._retry_manager._inject_abandon_continuation(query_content)

    # ── Context-usage snapshot (the context ring's single authority) ──────

    def _record_context_usage(
        self,
        tokens: Optional[int],
        *,
        basis: str,
        force: bool = False,
        is_resumed_first: bool = False,
    ) -> Optional[dict]:
        """Record the current context size; return the SSE event to emit.

        Called by the orchestrator after every main-agent request and at turn
        end. One API call can arrive as several AssistantMessages with the same
        usage, so None is returned (nothing to emit) when the token count, basis
        and session identity are all unchanged — unless ``force``. No I/O — safe
        on the streaming path.
        """
        from .context_usage import build_snapshot

        snap = build_snapshot(
            tokens, self._model_name, source="live", basis=basis,
            is_resumed_first=is_resumed_first,
        )
        if snap is None:
            return None
        prev = self._context_usage
        unchanged = (
            prev is not None
            and prev.get("tokensEst") == snap["tokensEst"]
            and prev.get("basis") == basis
            and self._context_usage_sdk_id == self._sdk_session_id
        )
        self._context_usage = snap
        self._context_usage_sdk_id = self._sdk_session_id
        self._context_recorded_this_turn = True
        if unchanged and not force:
            return None
        return dict(snap)

    def _record_compact_boundary(self, metadata: Optional[dict]) -> dict:
        """Record the CLI's post-compaction size; return the SSE event.

        Without a reported size the pre-compaction value is no longer true: the
        live snapshot is dropped (the read endpoint then derives the size from
        the CLI transcript, which records the compaction) and the event clears
        the ring until the next request measures it.
        """
        from .context_usage import none_snapshot

        md = metadata if isinstance(metadata, dict) else {}
        # The last request's usage predates the compaction: it must not win at
        # turn end over the size the compaction just produced.
        self._last_call_usage = None
        post = md.get("post_tokens")
        if isinstance(post, int) and post > 0:
            evt = self._record_context_usage(post, basis="post_compact", force=True)
            if evt is not None:
                return evt
        self._invalidate_context_usage()
        self._context_recorded_this_turn = True
        return none_snapshot()

    def _invalidate_context_usage(self) -> None:
        """Forget the live snapshot: its value may no longer be true and the
        backend cannot say what replaced it. The read endpoint then derives the
        size from the CLI transcript (what --resume reloads)."""
        self._context_usage = None
        self._context_usage_sdk_id = None

    def context_usage_snapshot(self) -> Optional[dict]:
        """The live snapshot, or None when there is none for the CURRENT identity.

        A snapshot measured under another (or no) sdk_session_id describes a
        conversation the next spawn will not carry, so it is never served.
        """
        snap = self._context_usage
        if snap is None or self._context_usage_sdk_id is None:
            return None
        if self._context_usage_sdk_id != self._sdk_session_id:
            return None
        return dict(snap)

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
        # Prefer the LAST request's real usage (set from AssistantMessage.usage by
        # the orchestrator); the summed-÷-num_turns average is only a fallback
        # (it under-reports a growing context). Consumed once per result.
        from .prompt_builder import PromptBuilder
        _last_call = getattr(self, "_last_call_usage", None)
        self._last_call_usage = None
        _last_call_tokens = (
            PromptBuilder.sum_usage_input_tokens(_last_call) if _last_call else 0
        )
        if _last_call_tokens > 0:
            input_tokens = _last_call_tokens
        elif usage:
            total = PromptBuilder.sum_usage_input_tokens(usage)
            turns = max(num_turns, 1)
            input_tokens = (total // turns) if total > 0 else None
        else:
            input_tokens = None
        # Track context size for adaptive timeout (L1 resilience)
        if input_tokens and input_tokens > 0:
            self._last_known_context_tokens = input_tokens
        # Root 2 / AC4: observability — log context-ring size + turn count, and
        # escalate to WARN when context% is near the soft cap so the amplifier is
        # visible BEFORE it bites (vs. the INFO debug line that's easy to miss).
        _ctx_pct = 0.0
        if input_tokens and input_tokens > 0:
            try:
                _win = PromptBuilder.get_model_context_window(self._model_name)
                _ctx_pct = (input_tokens / _win) * 100 if _win > 0 else 0.0
            except Exception:
                logger.debug("_emit_post_stream_metadata: suppressed exception on a fail-safe path", exc_info=True)
                _ctx_pct = 0.0
        _log = logger.warning if _ctx_pct >= SOFT_COMPACT_PCT else logger.info
        _log(
            "session_unit.context_ring_debug session_id=%s "
            "usage_keys=%s raw_total=%s per_turn_est=%s pct=%.0f%% "
            "turn_count=%d num_turns=%d model=%s",
            self.session_id,
            list(usage.keys()) if usage else "NO_USAGE",
            PromptBuilder.sum_usage_input_tokens(usage) if usage else 0,
            input_tokens,
            _ctx_pct,
            self._health_sensor.turn_count,
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
                # Record into the backend snapshot (the ring's single authority)
                # and re-emit it at turn end even if the last per-call emission
                # already carried the same value: the turn-end event is the
                # resumed-session message's only carrier. Without a last-call
                # usage, a snapshot recorded earlier THIS turn (e.g. a compaction
                # with no request after it) is newer truth than the summed
                # average, so it is re-emitted unchanged.
                _current = self.context_usage_snapshot()
                if _last_call_tokens <= 0 and self._context_recorded_this_turn:
                    warning_evt = _current
                else:
                    warning_evt = self._record_context_usage(
                        input_tokens,
                        basis="last_call" if _last_call_tokens > 0 else "turn_average",
                        force=True,
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
                logger.debug("_emit_post_stream_metadata: suppressed exception on a fail-safe path", exc_info=True)
                pass  # Never block on guard failure

        # Compaction visibility notice (CLI-native autocompact — approach A).
        # The PreCompact hook (hook_builder._pre_compact_hook) sets an INDEPENDENT
        # flag `_compact_notice_pending` on this same session_context dict when the
        # SDK compacts the context window. We surface a user-visible
        # `context_compacted` SSE event here (same result-tail path as
        # context_warning above) carrying a fixed notice. The flag is INDEPENDENT
        # of `_compacted` on purpose: post_compact_injection (runtime_hooks) owns
        # `_compacted` with its own fire-once reset — reusing it would make the two
        # consumers race for one flag and drop one signal. We clear ONLY our own
        # flag; `_compacted` is left untouched for post_compact_injection. This is
        # pure visibility: no compaction is triggered here, no `_client_io` lock is
        # held (that was the deleted proactive soft-compact, run_2b1957f8).
        try:
            ctx = self._hook_session_context
            if ctx and ctx.get("_compact_notice_pending"):
                from .prompt_builder import PromptBuilder
                notice_evt = PromptBuilder.build_compact_notice(
                    ctx.get("_compact_trigger")
                )
                events.append(notice_evt)
                # Fire-once: clear so the notice is not re-emitted next turn.
                ctx["_compact_notice_pending"] = False
                logger.info(
                    "session_unit.compact_notice_emitted session_id=%s trigger=%s",
                    self.session_id,
                    notice_evt.get("trigger"),
                )
        except Exception as exc:
            logger.warning(
                "session_unit.compact_notice_failed session_id=%s: %s",
                self.session_id, exc,
            )

        # System prompt metadata for TSCC popover
        try:
            from . import session_registry
            spm = session_registry.system_prompt_metadata.get(
                self.session_id
            )
            if spm:
                # TSCC/security-scan alignment (run_380413c5): recompose full_text
                # HERE — this runs at the end of EVERY response (cold AND warm),
                # unlike _spawn() which fires ONLY on a COLD turn (Gate-1 P5). A
                # warm-reuse turn's recall+SENSE ride the query and _spawn never
                # runs, so without this the warm-turn query-channel context would
                # stay invisible to the "System Prompt" modal + the security-scan
                # panel. Recompose from the STORED base (never from the prior
                # full_text) so consecutive turns do not compound prefixes; persist
                # the recomposed value so the two read-only panel endpoints
                # (which read the registry, not this SSE event) see it too.
                from .session_router import _compose_full_text
                _base = spm.get("base_full_text")
                if _base is None:
                    # Legacy entry published before this field existed → treat the
                    # existing full_text as the base (best-effort, no compounding
                    # because we overwrite base_full_text with it now).
                    _base = spm.get("full_text", "") or ""
                    spm["base_full_text"] = _base
                spm["full_text"] = _compose_full_text(
                    _base, self._delivered_query_prefix
                )
                session_registry.system_prompt_metadata[self.session_id] = spm
                events.append({"type": "system_prompt_metadata", **spm})
        except Exception:
            logger.debug("_emit_post_stream_metadata: suppressed exception on a fail-safe path", exc_info=True)
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

        # (C) non-blocking yield (run_4b74b764): get_mcp_status() drives the same
        # subprocess. If a foreground turn holds _client_io, SKIP this round
        # WITHOUT consuming the one-shot flag, so the check still runs after the
        # turn releases (it is a one-time post-first-turn probe, not best-effort
        # discardable like soft-compact). Probe BEFORE the flag-set so a skipped
        # round is not mistaken for a completed check.
        if self._client_io.locked():
            return None

        if not self._configured_mcps or self._client is None:
            # Nothing to check / no subprocess — finalize (a session with no
            # configured MCPs has no health question to answer).
            self._mcp_health_checked = True
            return None

        # Do NOT consume the one-shot until we have a SUCCESSFUL, non-empty status
        # response (run_a7b35b68): a transient get_mcp_status() exception or an
        # empty response is "could not determine", NOT "healthy". Consuming the
        # one-shot on those would permanently mask a genuinely-failing MCP — the
        # exact bug this feature exists to prevent, widened by the re-probe adding
        # more turns where a transient status-query failure could burn the flag.
        # So these two paths return WITHOUT setting the flag → the check re-runs
        # next turn (same skip-and-retry semantics as the _client_io.locked() path).
        # Transient-retry is bounded by the SAME re-probe budget as the pending
        # path, so a PERSISTENTLY broken get_mcp_status (not transient) cannot
        # re-probe every turn forever (STEERING #2 — no unbounded probe): on
        # budget exhaustion the flag is consumed and the check stops.
        try:
            status_response = await self._client.get_mcp_status()
        except Exception as exc:
            if self._mcp_pending_reprobes_left > 0:
                self._mcp_pending_reprobes_left -= 1
                logger.debug(
                    "session_unit.mcp_health_check_failed session_id=%s: %s "
                    "(transient — retry next turn, reprobes_left=%d)",
                    self.session_id, exc, self._mcp_pending_reprobes_left,
                )
                return None
            # Budget exhausted → give up probing (consume the one-shot).
            self._mcp_health_checked = True
            logger.debug(
                "session_unit.mcp_health_check_failed session_id=%s: %s "
                "(reprobe budget exhausted — giving up)",
                self.session_id, exc,
            )
            return None

        mcp_servers = status_response.get("mcpServers", [])

        # If CLI returned no servers at all, the control command may not be
        # supported (old SDK version) — skip rather than false-alarm. Do NOT
        # consume the one-shot (bounded by the re-probe budget, same as above).
        if not mcp_servers:
            if self._mcp_pending_reprobes_left > 0:
                self._mcp_pending_reprobes_left -= 1
                logger.debug(
                    "session_unit.mcp_health_check_empty session_id=%s "
                    "(no MCP status — retry next turn, reprobes_left=%d)",
                    self.session_id, self._mcp_pending_reprobes_left,
                )
                return None
            self._mcp_health_checked = True
            return None

        # We have a real status response → this IS a completed check. Consume the
        # one-shot now (the pending-reprobe branch below may un-set it again).
        self._mcp_health_checked = True

        # Build set of non-failed MCP names.
        # "pending" = still initializing (don't alert — not failed yet).
        # "disabled" = intentionally off (don't alert — user choice).
        # Only alert on "failed" or "needs-auth" — definitive failures.
        non_failed: set[str] = set()
        failed_servers: list[dict] = []
        pending_names: set[str] = set()
        for server in mcp_servers:
            name = server.get("name", "")
            status = server.get("status", "")
            if status in ("connected", "pending", "disabled"):
                non_failed.add(name)
                if status == "pending":
                    pending_names.add(name)
            elif status in ("failed", "needs-auth"):
                failed_servers.append(server)

        # Re-probe if a CONFIGURED MCP is still "pending" (run_a7b35b68): under
        # background init a slow MCP may not be terminal yet. Consuming the one-shot
        # now would mask a slow MCP that ultimately FAILS. Un-set the flag (bounded
        # by the re-probe budget) so a later turn re-checks once status is terminal.
        # Budget-exhausted → fall through and finalize on the best info we have
        # (pending still counts as non-failed, so no false alarm — just stop probing).
        configured_pending = self._configured_mcps & pending_names
        if configured_pending and self._mcp_pending_reprobes_left > 0:
            self._mcp_pending_reprobes_left -= 1
            self._mcp_health_checked = False  # un-consume the one-shot for a re-probe
            logger.info(
                "session_unit.mcp_health_pending session_id=%s pending=%s reprobes_left=%d",
                self.session_id, sorted(configured_pending),
                self._mcp_pending_reprobes_left,
            )
            return None

        # Compare: which configured MCPs are definitively missing/failed?
        missing = self._configured_mcps - non_failed
        if not missing:
            # `ok` MUST be counted over the SAME set as `configured` (run_2f19c4e1).
            # It used to be len(non_failed), which counts every non-failed server in
            # the CLI's whole response — including servers this session did NOT
            # configure (user/global-scope entries the CLI reports anyway). That made
            # the pair non-comparable and produced the self-contradictory
            # "configured=4 ok=5" seen in production, which was then cited as
            # evidence that background MCP init was healthy. Intersecting with
            # _configured_mcps makes ok <= configured an invariant, so this line can
            # only ever read as "N of N configured MCPs are up".
            logger.info(
                "session_unit.mcp_health_ok session_id=%s configured=%d ok=%d",
                self.session_id, len(self._configured_mcps),
                len(self._configured_mcps & non_failed),
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
            logger.debug("_spawn: suppressed exception on a fail-safe path", exc_info=True)
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

        # ── Cold-spawn instrumentation (run_924de37c, measure-only) ──────
        # Attribute the unexplained pre-model cold-start latency to a real seam.
        # Timestamps are CAPTURED inside the lock (to bound the right work) but
        # the log is EMITTED only AFTER both locks release below — formatting a
        # log line inside the critical section would widen the very _spawn_lock
        # we are measuring (and serialize every other tab's spawn). Locals are
        # pre-declared to None so the emit is robust on the config-None path and
        # so a raising __aenter__ (which propagates before the emit) leaves no
        # half-referenced local. perf_counter (monotonic) is a ~ns clock read.
        _enable_stream_reader_options(options)

        _t0 = time.perf_counter()
        _t_lock = _t_cfg0 = _t_cfg1 = _t_w0 = _t_w1 = None
        async with _spawn_lock:
            _t_lock = time.perf_counter()
            # One subprocess per unit (2026-10-20, a878efb9): a retry in its backoff
            # and the user's next send both found the unit COLD and both queued
            # here; each spawned a CLI, the second overwrote self._client and the
            # first became an orphan that kept streaming into the same chat and
            # run. Whoever gets the lock second finds the first one's client and
            # uses it: a stale retry then bails on _send_generation (retry_manager).
            if self._client is not None:
                logger.warning(
                    "session_unit.spawn_skipped_live_client session_id=%s pid=%s — "
                    "another path already spawned this unit's subprocess; reusing it",
                    self.session_id, self.pid,
                )
                return
            async with _env_lock:
                if config is not None:
                    _t_cfg0 = time.perf_counter()
                    _configure_claude_environment(config)
                    _t_cfg1 = time.perf_counter()
                wrapper = _ClaudeClientWrapper(options=options)
                _t_w0 = time.perf_counter()
                client = await wrapper.__aenter__()
                _t_w1 = time.perf_counter()

        self._wrapper = wrapper
        self._client = client
        self.last_used = time.time()
        # The client's one stream consumer, for its whole life (I1). Started
        # before the unit leaves COLD, so no frame (and no control request
        # queued behind one) ever waits for a turn to start reading.
        self._start_stream_reader(client)

        # Emit AFTER lock release (see note above). Guarded deltas: config-None
        # → configure_ms=0.0; a seam never reached → its ms stays 0.0.
        _lock_wait_ms = (_t_lock - _t0) * 1000 if _t_lock is not None else 0.0
        _configure_ms = (_t_cfg1 - _t_cfg0) * 1000 if (_t_cfg0 is not None and _t_cfg1 is not None) else 0.0
        _wrapper_aenter_ms = (_t_w1 - _t_w0) * 1000 if (_t_w0 is not None and _t_w1 is not None) else 0.0
        _total_ms = (time.perf_counter() - _t0) * 1000
        logger.info(
            "spawn_perf session_id=%s lock_wait_ms=%.1f configure_ms=%.1f "
            "wrapper_aenter_ms=%.1f total_ms=%.1f",
            self.session_id, _lock_wait_ms, _configure_ms,
            _wrapper_aenter_ms, _total_ms,
        )
        # Relay the cold-spawn wall time to the TTFT probe so it can attribute the
        # handshake cost away from send+infer (run_8149838f). Set here = set ONLY on
        # a cold spawn; a warm turn resets this to None at send() admission, so the
        # probe never mistakes a prior turn's spawn for this turn's inference. Pure
        # observability — nothing in the control flow reads this field.
        self._last_turn_spawn_ms = _total_ms

        # ── Publish TSCC prompt metadata AT DELIVERY ──────────────────
        # This is the ONLY place options.system_prompt reaches the CLI: send()
        # rebuilds options every turn, but a warm reuse never passes them here.
        # So this is the only point where "the prompt the panel shows" and "the
        # prompt the model got" are guaranteed to be the same string.
        # full_text is read off the options we just handed over, which is why it
        # includes the "## Recalled Knowledge" block the router appends AFTER
        # build_options, and any --resume rewrite from _build_retry_options.
        # Publishing here rather than at build time is what stops a rebuilt,
        # discarded turn-2 prompt from clobbering turn 1's real one.
        #
        # The recall snapshot rides along for one narrow but real case: a
        # recycle-before-reuse inside THIS send tears down the subprocess after
        # the router already copied the snapshot, and _cleanup_internal drops the
        # registry entry (correct — at that instant no prompt is in force). The
        # armed pending value lets the respawn restore the snapshot that matches
        # the prompt we are about to deliver. It is armed only on a turn where
        # recall actually ran, so a respawn on a later turn cannot resurrect it.
        try:
            from . import session_registry
            _pending = self._pending_prompt_metadata
            if _pending is not None and self.session_id:
                # TSCC/security-scan alignment (run_380413c5): store the BASE prompt
                # (options.system_prompt — under flag ON the resume block is NOT in
                # here, it rides the query) so _emit_post_stream_metadata can
                # recompose full_text = base + this-turn's delivered query-prefix on
                # EVERY turn (cold AND warm). Compose here too so a spawn-only turn
                # (no post-stream metadata emit) still publishes the full picture.
                from .session_router import _compose_full_text
                _base = options.system_prompt or ""
                _pending["base_full_text"] = _base
                _pending["full_text"] = _compose_full_text(
                    _base, self._delivered_query_prefix
                )
                session_registry.system_prompt_metadata[self.session_id] = _pending
                # One-shot: a later respawn must publish ITS own turn's metadata,
                # never re-publish this one.
                self._pending_prompt_metadata = None
            _pending_recall = self._pending_recall_snapshot
            if _pending_recall is not None and self.session_id:
                session_registry.recall_snapshot[self.session_id] = _pending_recall
                self._pending_recall_snapshot = None
        except Exception:  # noqa: BLE001 — a panel publish must never break spawn
            logger.debug(
                "session_unit.spawn session_id=%s — prompt metadata publish skipped",
                self.session_id, exc_info=True,
            )

        # ── Capture configured MCP names for post-spawn health check ──
        # options.mcp_servers is a dict when MCPs are configured.
        # Store the names so the health check can compare against
        # what the CLI actually connected to.
        if isinstance(options.mcp_servers, dict) and options.mcp_servers:
            self._configured_mcps = set(options.mcp_servers.keys())
            self._mcp_health_checked = False
            self._mcp_pending_reprobes_left = 3  # fresh re-probe budget per spawn
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

    async def _read_formatted_response(self) -> AsyncIterator[dict]:
        """Read SDK response stream and yield formatted SSE events.

        Implementation lives in StreamingOrchestrator. This delegation method
        exists because continue_with_permission() and several test files call
        it directly on the SessionUnit instance.
        """
        async for event in self._streaming_orchestrator._read_formatted_response():
            yield event

    def recover_from_disconnect(self) -> bool:
        """DEAD: no caller since a client disconnect stopped interrupting turns
        (core/session_events); kept until the follow-up removes this cluster.

        Transition STREAMING → a CLEAN IDLE after SSE client disconnect.

        Returns True if the transition happened.  No-op if not STREAMING.

        Root-1 SSOT Phase 2 (L6, Option B-soft): there is NO post-disconnect
        "generating" limbo flag any more. The transition produces a TRUE IDLE,
        which (a) fires _on_unit_state_change → enqueue_drain, so any messages the
        user queued during the turn coalesce-drain once the subprocess is free, and
        (b) lets the streaming-state mirror report state==IDLE without a special
        case. The subprocess is NOT killed here: the caller (chat.py) still
        schedules ``flush_subprocess_pipe`` which soft-interrupts and — on timeout —
        LEAVES THE SUBPROCESS ALIVE so a legitimate long tool-loop finishes and its
        output persists to DB for reconciliation (1A: long turns survive a transient
        SSE blip; lifecycle TTL handles a truly-stuck process).
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
        """Attempt a soft interrupt of the CLI subprocess after SSE disconnect.

        Called after ``recover_from_disconnect()`` as a background task.
        The unit is IDLE; the subprocess may still be running a tool
        whose stdout output would contaminate the next ``send()``.

        **Critical design choice (2026-06-20):** On timeout, we do NOT kill
        the subprocess. The subprocess is likely executing a tool call whose
        output will be persisted to DB by session_router._persist_assistant_blocks
        when it completes. Killing it destroys output that the frontend can
        recover via reconciliation polling.

        Instead: log and leave alive. The subprocess will either:
        (a) finish the tool call → ResultMessage → transition happens normally, or
        (b) become truly stuck → lifecycle_manager's 12hr TTL handles it.

        If the user sends a new message before the tool finishes, send()
        already handles the "subprocess busy" case (waits for current turn
        to complete via SDK's built-in turn serialization).

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
            # ── Recycle-on-flush (PIT01 zombie fix, disconnect path) ────────
            # A CLEAN interrupt return means the turn actually stopped (no tool
            # mid-flight — that path times out and is handled below). The
            # subprocess is now in the same corrupt turn-state as a user Stop,
            # so the next send() would reuse a poisoned process → zombie. Recycle
            # it via the same blessed kill path (preserves --resume identity).
            # Only on the IDLE-alive state: if the flush already drove us elsewhere
            # (DEAD/COLD via a concurrent kill), there is nothing warm to recycle.
            if self.state == SessionState.IDLE and self._client is not None:
                await self._arm_recovery_checkpoint("flush_recycle")
                await self._crash_to_cold_async(clear_identity=False)
                logger.info(
                    "session_unit.flush_recycle session_id=%s — poisoned "
                    "subprocess recycled to COLD after clean flush",
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
            # Don't kill — subprocess is likely still executing a tool call.
            # Its output will be persisted to DB when it finishes, and the
            # frontend reconciliation will recover the content.
            if self._send_generation != gen_at_entry:
                logger.info(
                    "session_unit.flush_pipe session_id=%s — timeout but "
                    "send() started (gen %d→%d), not killing",
                    self.session_id, gen_at_entry, self._send_generation,
                )
                return
            logger.info(
                "session_unit.flush_pipe session_id=%s — interrupt timed out, "
                "leaving subprocess alive (tool call likely in progress, "
                "output will persist to DB for reconciliation recovery)",
                self.session_id,
            )

    # ── Interactive methods (task 3.3) ─────────────────────────────

    async def stop_task(self, task_id: str) -> dict:
        """Stop ONE sub-agent of the running turn; the turn itself goes on.

        Allowlisted: the SDK is called only while the turn is active (STREAMING /
        WAITING_INPUT) and only for an id in ``_live_task_ids`` — a task this session
        reported running in its current turn. Any other id (unknown, finished, from an
        earlier turn) is refused without touching the SDK. Never sets the user-stop
        flag or the stop event: self-heal eligibility and the stream are unchanged.
        The SDK then reports the task's end (``TaskUpdatedMessage`` killed →
        ``subagent_progress`` stopped), which the chat shows on the agent's row.

        Returns ``{"stopped": bool, "reason": str}``; the reason is generic (no SDK
        error text reaches the client).
        """
        if self.state not in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            return {"stopped": False, "reason": "the turn is not running"}
        client = self._client
        if client is None or task_id not in self._live_task_ids:
            return {"stopped": False, "reason": "no such running agent in this turn"}
        try:
            await asyncio.wait_for(client.stop_task(task_id), timeout=STOP_TASK_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — timeout or SDK error: report, keep the turn
            logger.warning("stop_task failed session_id=%s task=%s: %s",
                           self.session_id, task_id, type(exc).__name__)
            return {"stopped": False, "reason": "the agent could not be stopped"}
        logger.info("stop_task session_id=%s task=%s", self.session_id, task_id)
        return {"stopped": True, "reason": ""}

    async def interrupt(self, timeout: float = 5.0, autonomous: bool = False) -> bool:
        """Interrupt active query. SDK interrupt() with kill fallback.

        ``autonomous`` (run_fb6e94a9): when True, this is a system-initiated
        interrupt (e.g. the watchdog escaping a wedged tool), NOT a user Stop.
        In that case we do NOT set ``_user_stopped_current_turn`` — a user Stop
        suppresses the post-turn self-heal, but an autonomous tool-hang escape
        is exactly a turn that MIGHT still need healing, so self-heal must stay
        eligible. Default False preserves the user-Stop semantics for all
        existing callers.

        State transitions (success path):

        - autonomous=False (user Stop / compaction): STREAMING → IDLE →
          (recycle) → COLD. A soft interrupt leaves the subprocess in a
          corrupt turn-state, so it is immediately recycled via
          ``_crash_to_cold_async(clear_identity=False)`` (PIT01 zombie fix).
          ``_sdk_session_id`` is preserved, so the next ``send()`` respawns
          clean WITH ``--resume``. The subprocess does NOT stay warm.
        - autonomous=True (tool-hang watchdog only): STREAMING → IDLE,
          subprocess stays WARM and reusable so the model can reroute
          mid-stream without a respawn (recycling would collapse the warm
          base rung of the escalation ladder).
        - timeout / error: STREAMING → DEAD → COLD (subprocess force-killed).
        - WAITING_INPUT → IDLE (then the same autonomous-gated recycle).

        Returns True if the interrupt SUCCEEDED (the turn stopped) — this is
        NOT a subprocess-alive signal. After a non-autonomous interrupt the
        subprocess has been recycled to COLD, so callers needing the live
        process state must re-read ``is_alive`` (``interrupt_session`` does
        this), never the return value. Returns False if the interrupt timed
        out or errored and the subprocess was force-killed.

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
        # Durable signal: this turn was stopped by the user. Survives until the
        # next send()'s Layer 0 reset so the post-stream self-heal check can skip
        # — a user Stop must never be followed by a proactive heal kill/respawn.
        # Autonomous interrupts (watchdog tool-hang escape) skip this: the hung
        # turn should remain eligible for self-heal (run_fb6e94a9).
        if not autonomous:
            self._user_stopped_current_turn = True
        self._active_agent_tools = {}  # Clear stale sub-agent progress on interrupt
        self._live_task_ids = set()  # the turn is stopped: no per-agent stop after it
        self._open_tool_uses = {}  # Clear stale open-tool tracking (run_fb6e94a9)

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
            # Release the outstanding-tool_use guard (GAP2, audit 2026-06-23).
            # A Stop during WAITING_INPUT leaves the subprocess alive → IDLE, but
            # without clearing these the drain worker's `if has_outstanding_tool_use:
            # return` no-ops forever (a message queued after the Stop never
            # delivers until the next kill/TTL). Cleared ONLY here in the
            # success→IDLE branch: the stale-interrupt guard above returns before
            # reaching this point (so a concurrent new send's state is preserved),
            # and the timeout/error branches kill()→_cleanup_internal which already
            # clears. Mirrors the _active_agent_tools clear above + the 4 other
            # _pending_tool_use_id teardown sites.
            self._pending_tool_use_id = None
            self._pending_question = None
            logger.info(
                "session_unit.interrupt succeeded session_id=%s pid=%s",
                self.session_id, self.pid,
            )

            # ── Recycle-on-interrupt (PIT01 zombie fix) ─────────────────────
            # A soft interrupt leaves the CLI subprocess in a corrupt turn-state.
            # If left warm (IDLE), the next send() reuses it and the CLI returns
            # an INSTANT empty error_during_execution → the zombie detector kills
            # + respawns (a ~10s stall on the user's next turn). Every zombie in
            # the daemon log is preceded by a user Stop. So for a USER Stop we
            # recycle the poisoned subprocess NOW via the blessed kill path
            # (_crash_to_cold_async: real kill + FD cleanup, holds _lock).
            # clear_identity=False preserves _sdk_session_id, so the next send()
            # (COLD → _ensure_spawned at line ~1617) respawns clean WITH --resume
            # and the conversation continues seamlessly.
            #
            # AUTONOMOUS interrupts (autonomous=True) are EXCLUDED: the tool-hang
            # watchdog (line ~1284) deliberately leaves the process warm and does
            # NOT return — it lets the model reroute mid-stream without a full
            # respawn. Recycling there would collapse the warm "base rung" into
            # the escalated kill rung.
            #
            # NOTE on compaction: the CompactionGuard escalation
            # (streaming_orchestrator.py ~754) calls interrupt() with NO autonomous
            # arg → autonomous=False → it RECYCLES, and that is CORRECT: compaction
            # `return`s immediately after the interrupt (the turn ENDS — no warm
            # reroute), and compaction_guard.py's own note confirms a compaction
            # interrupt poisons the subprocess (instant error_during_execution on
            # reuse) — i.e. it IS a PIT01 source. So recycle covers user Stop AND
            # compaction (both end the turn); only the watchdog (keeps streaming)
            # stays warm.
            #
            # _arm_recovery_checkpoint enriches the next send()'s resume context
            # with "where I left off" (NO new kill — it only annotates the kill
            # that's about to happen here).
            if not autonomous:
                await self._arm_recovery_checkpoint("interrupt_recycle")
                await self._crash_to_cold_async(clear_identity=False)
                logger.info(
                    "session_unit.interrupt_recycle session_id=%s — poisoned "
                    "subprocess recycled to COLD (resume id preserved)",
                    self.session_id,
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
            answer: JSON-encoded answer dict from the user.
            tool_use_id: The AskUserQuestion tool_use block ID (== SDK block.id).
                Used to signal ``ask_question_manager.set_answer(tool_use_id, ...)``,
                which unblocks the ask_question_gate PreToolUse hook awaiting this
                exact id. The hook then injects the answers via ``updatedInput`` and
                the SAME SDK stream continues (resumed below via
                ``_read_formatted_response``). Falls back to ``_pending_tool_use_id``.
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

        # User responded — reset compaction guard's loop-detection state.
        logger.info(
            "session_unit.continue_with_answer session_id=%s "
            "tool_use_id=%s answer_len=%d state=%s",
            self.session_id, tool_use_id, len(answer), self.state.value,
        )
        self._compaction_guard.reset()
        self._content_emitted = False  # Reset zombie detection for new stream
        self._active_agent_tools = {}  # Clear stale sub-agent progress
        self._open_tool_uses = {}  # Clear stale open-tool tracking (run_fb6e94a9)

        # Signal the blocked ask_question_gate hook with the user's answers.
        # The hook is blocked inside PreToolUse awaiting
        # ask_question_manager.wait_for_answer(tool_use_id). Setting the answer
        # unblocks it; the hook returns permissionDecision:"allow" +
        # updatedInput.answers, the CLI's AskUserQuestion call() returns the real
        # answers, and the SAME SDK stream continues — which we resume reading via
        # _read_formatted_response() below (mirrors continue_with_permission).
        #
        # This REPLACES the old stream_query(answer, parent_tool_use_id=...) path,
        # which started a SEPARATE query as a tool_result — that path raced the
        # CLI's headless self-resolution and lost (the answer landed on an
        # already-resolved tool). There is now ONE answer-delivery path (no dual
        # path / COE10 class).
        from core.ask_question_manager import ask_question_manager as _aqm
        try:
            parsed_answers = json.loads(answer) if answer else {}
            if not isinstance(parsed_answers, dict):
                parsed_answers = {}
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "session_unit.continue_with_answer: answer is not valid JSON "
                "(session=%s) — passing empty answers", self.session_id,
            )
            parsed_answers = {}

        # Root-1 SSOT Phase 2 (L4): the question is being answered — clear the
        # outstanding-tool_use guard so the drain worker may resume on the next
        # clean IDLE (F3). Cleared on the proper answer path, never on a new send.
        # NOTE: read tool_use_id for set_answer BEFORE clearing the guard.
        _answer_tool_use_id = tool_use_id or self._pending_tool_use_id
        self._pending_tool_use_id = None
        self._pending_question = None
        self._transition(SessionState.STREAMING)

        if _answer_tool_use_id:
            # NOTE on empty parsed_answers: we do NOT reject here. The UX-level
            # empty-answer rejection (EMPTY_ANSWER) lives at the HTTP edge
            # (routers/chat.py) where the user can resubmit. By this chokepoint
            # the answer is committed; resolving the hook with whatever we have
            # (even {}) lets the agent proceed ("no selection") rather than
            # WEDGING the session on a blocked hook until the 4h answer timeout. The
            # channel gateway builds a non-empty answers dict before calling this.
            _aqm.set_answer(_answer_tool_use_id, parsed_answers)
        else:
            logger.warning(
                "session_unit.continue_with_answer: no tool_use_id to signal "
                "(session=%s) — hook may time out", self.session_id,
            )

        try:
            async for event in self._read_formatted_response():
                yield event
        except Exception as exc:
            async for event in self._retry_continuation(exc, traceback.format_exc()):
                yield event

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
        # Flip status OUT of "pending" BEFORE signalling. Otherwise a
        # /sessions/streaming-state poll landing between set_permission_decision
        # and the awaiting coroutine's finally would see status=="pending" + a
        # still-live waiter and re-surface an already-decided prompt
        # (approve-into-void).
        _pm.update_pending_request(request_id, {"status": decision})
        _pm.set_permission_decision(request_id, decision)
        logger.info(
            "session_unit.permission_decision session_id=%s "
            "request_id=%s decision=%s",
            self.session_id, request_id, decision,
        )

        # User responded — reset compaction guard's loop-detection state.
        self._compaction_guard.reset()
        self._content_emitted = False  # Reset zombie detection for new stream
        self._active_agent_tools = {}  # Clear stale sub-agent progress
        self._open_tool_uses = {}  # Clear stale open-tool tracking (run_fb6e94a9)
        # Root-1 SSOT Phase 2 (L4): permission resolved — clear the
        # outstanding-tool_use guard so the drain worker may resume (F3).
        self._pending_tool_use_id = None
        self._pending_question = None
        self._transition(SessionState.STREAMING)

        try:
            async for event in self._read_formatted_response():
                yield event
        except Exception as exc:
            async for event in self._retry_continuation(exc, traceback.format_exc()):
                yield event

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
    #
    # ┌─────────────────────────────────────────────────────────────┐
    # │ THRESHOLD SIZING RATIONALE (2026-06-17)                     │
    # │                                                             │
    # │ Memory model (measured, not theoretical):                   │
    # │   - CLI base (Node.js V8):          ~300 MB                │
    # │   - 7 MCP sub-processes:            ~350 MB                │
    # │   - Conversation context (800K):    ~600-1500 MB           │
    # │   - Normal IDLE steady-state:       750 MB - 1.5 GB        │
    # │   - Normal STREAMING peak:          2.5 - 4.5 GB           │
    # │     (V8 JSON.stringify doubles RAM: source + buffer)        │
    # │                                                             │
    # │ History:                                                    │
    # │   1.2 GB (2026-04) — below steady-state, killed every turn │
    # │   1.8 GB (2026-04) — OK for 128K budget, broke at 800K    │
    # │   3.5 GB (2026-06) — current. Covers 800K budget IDLE.    │
    # │                                                             │
    # │ Safety layers (defense in depth):                           │
    # │   L1: This threshold — proactive compact+kill, IDLE only   │
    # │   L2: STREAMING_RSS_KILL — emergency kill during streaming │
    # │   L3: lifecycle_manager system pressure (>90%) — any state │
    # │   L4: macOS jetsam — OS-level kill at memory crisis        │
    # │                                                             │
    # │ 36 GB machine: 3.5 GB = 10%. Two sessions at threshold =  │
    # │ 20%. macOS reclaims caches first; huge headroom.           │
    # └─────────────────────────────────────────────────────────────┘
    PROACTIVE_RSS_THRESHOLD: int = 3_500_000_000  # 3.5GB
    PROACTIVE_COOLDOWN: float = 180.0  # 3 minutes

    # ┌─────────────────────────────────────────────────────────────┐
    # │ STREAMING RSS KILL THRESHOLD (2026-06-17)                   │
    # │                                                             │
    # │ Purpose: kill STREAMING sessions that are truly leaking,    │
    # │ NOT sessions doing normal large-context API calls.          │
    # │                                                             │
    # │ Why 7 GB:                                                   │
    # │   - Normal peak during 800K-token API call: 3-4.5 GB       │
    # │   - After call completes, drops back to ~750 MB            │
    # │   - Pattern is SAWTOOTH (peak→drop), not MONOTONIC         │
    # │   - 7 GB = ~1.5x worst normal peak = true leak signal      │
    # │                                                             │
    # │ Evidence (2026-06-17 backend-daemon.log):                   │
    # │   22:52 764MB → 22:53 2739MB → 22:55 3322MB → 22:57 764MB │
    # │   This is normal. Old 3GB threshold killed at 22:56.       │
    # │   Session was interrupted 4x in 15 min = user-facing bug.  │
    # │                                                             │
    # │ On 36 GB: 7 GB = 19%. Even 2 sessions peaking = 38%.      │
    # │ Still far below any memory-pressure crisis.                │
    # └─────────────────────────────────────────────────────────────┘
    STREAMING_RSS_KILL_THRESHOLD: int = 7_000_000_000  # 7GB

    # R6 Step B: concurrent-streaming admission cap (peak-OOM guard). Class attrs
    # (not module-level) so tests can patch.object(SessionUnit, ...) and the
    # _await_streaming_slot helper reads them via self. See the module-level
    # _streaming_count / _get_streaming_count for the daemon-wide counter.
    MAX_CONCURRENT_STREAMS: int = int(
        os.environ.get("SWARMAI_MAX_CONCURRENT_STREAMS", "3")
    )
    _STREAM_ADMIT_POLL_INTERVAL: float = 0.1   # seconds between cap re-checks
    _STREAM_ADMIT_TIMEOUT: float = 120.0       # max wait for a streaming slot

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
        # R3a: the cooldown DECISION now routes through the one recovery
        # authority (CooldownThresholdPolicy). The Coordinator owns the
        # cooldown verdict; this method still owns the RSS threshold
        # measurement + the compact/kill mechanics + the timestamp write.
        # enabled=True/user_stopped=False: RSS-proactive is not gated by the
        # self-heal env flag and runs post-turn (no user turn in flight). The
        # threshold check below is the RSS-specific gate the policy intentionally
        # does not model.
        from .session_healing import RecoveryVerdict
        _rss_decision = self._recovery_coordinator.decide_rss(
            now=time.monotonic(),
            last_recovery=self._last_proactive_restart,
            cooldown_s=self.PROACTIVE_COOLDOWN,
            enabled=True,
            user_stopped=False,
            state=self.state.value,
        )
        if _rss_decision.verdict is not RecoveryVerdict.PROCEED_KILL:
            return  # DEFER (cooling down) or SKIP — no proactive restart this tick

        pid = self.pid
        if not pid:
            return
        if self.has_background_work:
            return  # a live background task / queued CLI-initiated turn (I5)

        try:
            from .resource_monitor import resource_monitor
            loop = asyncio.get_running_loop()
            # Heavy psutil tree walk (~107ms) → dedicated _rss_executor, off the
            # shared subprocess_executor that force_kill uses (run_409392d4).
            tree_rss = await loop.run_in_executor(
                _rss_executor,
                resource_monitor.process_tree_rss, pid,
            )
        except Exception as exc:  # noqa: BLE001
            # Was "skip silently". Skipping is still right — a failed RSS read must not
            # kill a healthy session — but this is the CONSUMER side of the same
            # load-bearing lie process_tree_rss carries: silence here means the
            # proactive-restart guard simply never fires, and a session can grow past
            # PROACTIVE_RSS_THRESHOLD with nothing anywhere recording that the check
            # stopped running. (process_tree_rss now logs its own failures; this covers
            # the executor-submission path, which it cannot see.)
            logger.warning("session_unit.rss_check_skipped session_id=%s — proactive "
                           "restart will not fire this cycle: %s", self.session_id, exc)
            return

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
        # compact() internally writes /compact and drains its turn
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
        # Arm the same rich recovery checkpoint the voluntary self-heal builds
        # so the lazy --resume on next send() starts with structured context.
        await self._arm_recovery_checkpoint("rss_proactive")
        await self.kill()

        self._last_proactive_restart = time.monotonic()

    # _check_context_soft_compact REMOVED (run_2b1957f8). Proactive soft-compact
    # held _client_io for up to 300s at 60% context, blocking the user's next
    # send() at the turn boundary ("streaming freezes at 60%"). Context is now
    # managed by the CLI's built-in autocompact (autoCompactWindow, 75% of the
    # window — prompt_builder) + manual refresh — neither holds our _client_io lock.
    # The RSS proactive-restart path (_check_rss_and_proactive_restart, which owns
    # its own compact() call above) is INDEPENDENT and unaffected.

    def _should_inject_hard_floor_wrap(self) -> bool:
        """AC3 (G2) predicate: should a desktop hard-floor wrap-up be injected?

        TRULY pure (read-only) so it is forced-testable (STEERING #11) and safe to
        evaluate more than once — the single caller (``send()``) owns the one-shot
        commit + log. True when, on a DESKTOP session, turn_count has reached
        max_turns - HARD_FLOOR_BUFFER, the wrap hasn't been injected yet, and
        self-heal isn't already wrapping up.

        Reachability: when self-heal is *succeeding*, turn_approaching (-20) heals +
        resets turn_count before -5 is reached, so the floor stays dormant. It is a
        last-resort net that DOES fire when self-heal is OFF, exhausted, or in
        cooldown — not "unreachable on the self-heal-ON path."
        """
        if self.is_channel_session:
            return False
        if self._hard_floor_wrap_injected:
            return False
        if self._graceful_wrap_pending:
            return False
        if self._health_sensor.turn_count < (
            self._health_sensor._max_turns - HARD_FLOOR_BUFFER
        ):
            return False
        return True

    def _maybe_build_elapsed_heartbeat(self) -> Optional[dict]:
        """AC5 (G3): build a 'still working' notice for a long-running turn.

        Event-driven (called when an SDK event arrives during STREAMING): if the
        current turn's wall-clock exceeds LONG_TURN_HEARTBEAT_S and we haven't
        emitted a notice for this interval, return a notice event. Returns None
        otherwise. One notice per LONG_TURN_HEARTBEAT_S interval (no spam).

        The notice is an SSE/UI event — never written to the system prompt
        (byte-stability invariant). Reset _last_heartbeat_elapsed on STREAMING
        entry so each turn starts fresh.
        """
        if self.state != SessionState.STREAMING:
            return None
        if self._streaming_start_time is None:
            return None
        elapsed = time.time() - self._streaming_start_time
        if elapsed < LONG_TURN_HEARTBEAT_S:
            return None
        # One notice per heartbeat interval: only emit if we've crossed into a
        # new multiple of the interval since the last emission.
        interval_idx = int(elapsed // LONG_TURN_HEARTBEAT_S)
        last_idx = int(self._last_heartbeat_elapsed // LONG_TURN_HEARTBEAT_S)
        if interval_idx <= last_idx:
            return None
        self._last_heartbeat_elapsed = elapsed
        minutes = int(elapsed // 60)
        # AC1 (run_fb6e94a9): if a specific tool has been open a long time,
        # name it and tell the user they can Stop to recover — turns a silent
        # spinner into an actionable signal.
        oldest = self._oldest_open_tool()
        if oldest is not None and oldest[0] > self.TOOL_HANG_SOFT_S:
            tool_age, tool_name, _tool_id = oldest
            tmin = int(tool_age // 60)
            return {
                "type": "still_working",
                "elapsedSeconds": int(elapsed),
                "toolName": tool_name,
                "message": (
                    f"⏳ {tool_name} has been running {tmin}m with no output. "
                    f"Still working — press Stop to recover if this looks stuck."
                ),
            }
        return {
            "type": "still_working",
            "elapsedSeconds": int(elapsed),
            "message": f"Still working — {minutes}m elapsed on this step.",
        }

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
        _reader = getattr(self, "_stream_reader", None)
        if _reader is not None and _reader.dead:
            return {"success": False, "message": "The CLI stream ended; nothing to compact"}
        self._record_gate_mark()
        if not self.cli_quiescent:
            return {
                "success": False,
                "message": "The CLI is running a turn of its own; compact later",
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
            # Serialize the subprocess drain (run_4b74b764). compact() stays
            # IDLE→IDLE and does NOT _transition, so the state gate gives NO
            # mutual exclusion against a concurrent send() reusing the same
            # subprocess. Hold _client_io across the write and the turn's drain
            # so a user turn is never written into the compact turn. The foreground turn takes a short barrier on this lock
            # at send()'s IDLE-entry, so it waits for an in-flight compact here.
            # A manual /compact never passes through the turn's SSE stream, so
            # the context snapshot is updated here from the drained
            # compact_boundary. Without a boundary — or when the drain aborts,
            # including a CancelledError from a caller's wait_for timeout, which
            # `except Exception` does not see — whether the CLI compacted is
            # unknown, so the live value of THIS identity is dropped (the
            # transcript is truth); see the finally below.
            _boundary_md: Optional[dict] = None
            _compact_identity = self._sdk_session_id
            _recorded = False
            try:
                async with self._client_io:
                    from .streaming_orchestrator import write_user_message
                    _stream = await write_user_message(
                        self, command, kind="compact",
                        session_id=self._sdk_session_id or "default",
                    )
                    async for _msg in _stream:
                        if getattr(_msg, "subtype", None) == "compact_boundary":
                            _data = getattr(_msg, "data", None)
                            _md = _data.get("compact_metadata") if isinstance(_data, dict) else None
                            _boundary_md = _md if isinstance(_md, dict) else {}
                if _boundary_md is not None:
                    self._record_compact_boundary(_boundary_md)
                    _recorded = True
                    # Same record as the streamed path (core/compact_summary.py):
                    # the divider + summary appear with the next history load.
                    # Clear the PreCompact fallback flag like the streamed
                    # boundary does, or the next turn adds a second divider.
                    from . import compact_summary as _cs
                    _ctx = self._hook_session_context
                    if isinstance(_ctx, dict):
                        _ctx["_compact_notice_pending"] = False
                    _rec = _cs.build_record(_boundary_md, _cs.take_summary(_ctx))
                    if not _rec["summary"]:
                        _cs.mark_unsummarized(_ctx, _rec["id"])
                    await _cs.persist(self.session_id, _rec)
            finally:
                if not _recorded and self._context_usage_sdk_id == _compact_identity:
                    self._invalidate_context_usage()
                # A CLI-initiated turn queued while this compact held the
                # subprocess has no IDLE edge to wait for (compact stays IDLE):
                # hand it to the sink now that _client_io is released.
                if getattr(self, "_injected_turns", None):
                    self._emit_cli_event("injected")
            self.last_used = time.time()
            # Transition guard to ACTIVE — post-compaction loop detection enabled
            self._compaction_guard.activate()
            return {"success": True, "message": "Session compacted"}
        except Exception as exc:
            # Denoise (dead→streaming race family): compact() drains the SAME
            # subprocess but does NOT hold self._lock, so a concurrent
            # refresh_context()/kill()/release (all take the lock and drive the
            # unit →DEAD→COLD) can kill the subprocess mid-drain. That surfaces
            # here as an "exit code -9" — but WE killed it on purpose, it is not
            # a compact failure. Distinguish by state: compact() itself never
            # transitions (IDLE→IDLE), so a state of DEAD/COLD at except-time
            # means a concurrent kill fired → log at INFO. A genuine drain
            # failure leaves the unit IDLE (subprocess alive) → keep ERROR loud.
            if self.state in (SessionState.DEAD, SessionState.COLD):
                logger.info(
                    "session_unit.compact_aborted session_id=%s state=%s "
                    "— subprocess killed during drain by a concurrent "
                    "refresh/release/kill (not a compact failure): %s",
                    self.session_id,
                    self.state.value if self.state else "None",
                    exc,
                )
            else:
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
                logger.debug("health_check: suppressed exception on a fail-safe path", exc_info=True)
                pass  # Never let metrics collection break health_check

            return True
        except ProcessLookupError:
            logger.warning(
                "session_unit.health_check: pid %d dead for session %s",
                pid, self.session_id,
            )
            if self.is_alive:
                await self._arm_recovery_checkpoint("watchdog")
                await self._crash_to_cold_async(clear_identity=False)
            return False

    async def kill(self) -> None:
        """Force-kill subprocess and clean up.

        State: any → DEAD → COLD.

        Safe to call multiple times or from any state. R4: shares the
        ``self._lock`` recovery transaction with ``_crash_to_cold_async`` so
        the two are MUTUALLY exclusive — ``kill()`` is the other kill entry
        point (used by the lifecycle loop's RSS/TTL/pressure paths) while
        ``_crash_to_cold_async`` is used by the per-session streaming/retry
        paths. Without a shared lock these two tasks could both pass the
        state guard and both run ``_force_kill`` on the same pid (the audit's
        cross-task TOCTOU). Holding the SAME lock closes it for ALL kill paths,
        making the R4 "single recovery transaction" invariant true end-to-end.
        """
        async with self._lock:
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
            # Before the kill: the SDK reader logs its "exit code -9" ERROR
            # within milliseconds of the signal (see _ExpectedKillLogFilter).
            _note_intentional_kill()
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

        # Also try graceful wrapper cleanup.
        # Concurrency-safe (run_02bc6dd1 / 簇A WS1): capture the ref and null
        # self._wrapper in ONE await-free block BEFORE awaiting __aexit__. The
        # null assignment is synchronous, so only the FIRST concurrent caller
        # wins the wrapper; a racing _force_kill (the lock-free PID watchdog vs a
        # _lock-holding _crash_to_cold_async/kill) sees None and skips. Without
        # this, both callers passed the not-None check and both invoked
        # __aexit__ on the same non-reentrant anyio wrapper (TOCTOU double-free).
        wrapper_ref = self._wrapper
        self._wrapper = None
        if wrapper_ref is not None:
            try:
                await asyncio.wait_for(
                    wrapper_ref.__aexit__(None, None, None),
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
        _reader = getattr(self, "_stream_reader", None)
        if _reader is not None:
            # Fails any open or waiting turn stream; a later claim raises.
            _reader.close()
        self._stream_reader = None
        self._turn_stream = None
        self._injected_turns = _deque()
        self._client = None
        self._wrapper = None
        self._interrupted = False
        self._live_task_ids = set()  # the subprocess is gone: so are its tasks
        # NOTE: self._retry_count is intentionally NOT reset here.
        # It is reset at each new turn (_reset_turn_state) and on success.
        self._model_name = None
        self._peak_tree_rss_bytes = 0
        # CRITICAL (Gate-2 F3): clear the outstanding-tool_use guard on EVERY
        # teardown (kill / crash / force_unstick / eviction all route through here),
        # not just the proper answer paths. A WAITING_INPUT session that is
        # abandoned (120-min force_unstick) or crashed would otherwise keep
        # _pending_tool_use_id set → has_outstanding_tool_use stuck True →
        # drain_pending no-ops for that session FOREVER (every queued message
        # silently never delivered). Clearing here makes the guard self-heal.
        self._pending_tool_use_id = None
        self._pending_question = None
        if self._pipe_flush_task is not None:
            self._pipe_flush_task.cancel()
            self._pipe_flush_task = None
        # Don't reset _lifecycle_response_count — it tracks across the
        # full unit lifetime (resume awareness persists through kill/restart).
        # Reset channel history injection flag — the new subprocess
        # won't have any conversation history, so it needs re-injection.
        self._channel_history_injected = False
        # Reset recall injection flag — new subprocess needs fresh recall.
        self._recall_injected = False
        self._ddd_injected = False
        # Reset the per-turn resume query stash (run_d108b914): a stale block must
        # not leak onto a later turn's query prefix. send() also clears it per-turn,
        # but reset on teardown too so a recycle mid-send never carries it forward.
        self._resume_query_block = None
        # TSCC/security-scan alignment (run_380413c5): clear the delivered
        # query-channel prefix on teardown too (send() writes it every turn, but a
        # recycle mid-send must not carry a stale prefix into the next turn's
        # full_text).
        self._delivered_query_prefix = None
        # Clear the TSCC recall snapshot, on the unit AND in the registry. The
        # subprocess is gone, so no prompt is in force and there is no recall to
        # report; leaving the registry entry behind paired a pre-restart snapshot
        # with a post-restart prompt that carries no recall block (review
        # run_abab234c, MED #9 — the unit attribute was cleared but the registry
        # copy the panel actually reads was not).
        #
        # Safe against the same-send recycle case: if recall ran THIS turn, the
        # router armed _pending_recall_snapshot, and the respawn in this same
        # send republishes it. _pending_recall_snapshot is deliberately NOT
        # cleared here for that reason.
        self._recall_snapshot = None
        try:
            from . import session_registry
            session_registry.recall_snapshot.pop(self.session_id, None)
        except Exception:  # noqa: BLE001 — teardown must never raise on cleanup
            logger.debug("_cleanup_internal: suppressed exception on a fail-safe path", exc_info=True)
            pass
        self._recall_keyword_misses = 0
        # Reset shared active-project cache (run_6ebf6479) — a new subprocess must
        # re-detect (the editor/project context may have changed across the restart).
        self._active_project = None
        # Release canary ownership if this session held it (Fix #1: canary leak)
        release_canary(self.session_id)

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

    _WRAPUP_CAP = 4000

    def _maybe_recovery_exhausted_event(self, trigger: str) -> Optional[dict]:
        """Return a one-shot ``recovery_exhausted`` SSE event when the recovery
        breaker has JUST tripped, else ``None``.

        Surfaces the Coordinator's terminal signal to the user (decision #3):
        self-heal has given up, so the session is a silent dead-end unless the
        user is told. Gated on the ``terminal_signal_count`` DELTA, not the
        ``ESCALATE`` verdict — the verdict recurs every tick while the breaker
        holds, but the signal count advances exactly once per exhaustion episode
        (and again on a fresh episode after a successful heal, since the count is
        monotonic). The high-water mark makes the yield idempotent across the
        repeated ESCALATE ticks. Never raises — must not break the stream loop.
        """
        try:
            sig = self._recovery_coordinator.terminal_signal_count
            if sig <= self._last_recovery_exhausted_signal:
                return None
            self._last_recovery_exhausted_signal = sig
            return {
                "type": "recovery_exhausted",
                # self.session_id is ALWAYS set; _sdk_session_id is None until a
                # spawn assigns it (Gate-1 #5 correction).
                "sessionId": self.session_id,
                "message": (
                    "Automatic recovery for this session has failed repeatedly "
                    "and has stopped. Start a fresh session to continue — your "
                    "conversation history is preserved."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            # "never break streaming" stands — but note WHAT is being dropped: this
            # builds the user-facing "automatic recovery has stopped" notice. Losing it
            # silently leaves the user with a session that has given up and no message
            # saying so, which is the one case where the missing event is the whole point.
            logger.warning("session_unit.recovery_exhausted_event_failed session_id=%s "
                           "trigger=%s — user will not be told recovery stopped: %s",
                           self.session_id, trigger, exc)
            return None

    def _capture_wrapup_text(self, event: dict) -> None:
        """Accumulate assistant text emitted during the graceful wrap-up turn.

        Appends text blocks from ``assistant`` SSE events into the instance-scoped
        ``_wrapup_conclusion`` buffer, bounded to ``_WRAPUP_CAP`` chars. Never raises
        — capture must never break the streaming loop (Property 3).
        """
        try:
            if not isinstance(event, dict) or event.get("type") != "assistant":
                return
            for block in event.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text") or ""
                    if not text:
                        continue
                    remaining = self._WRAPUP_CAP - len(self._wrapup_conclusion)
                    if remaining <= 0:
                        break
                    self._wrapup_conclusion += text[:remaining]
        except Exception:
            logger.debug("_capture_wrapup_text: suppressed exception on a fail-safe path", exc_info=True)
            pass  # capture is best-effort; never break streaming

    async def _derive_heal_enrichment(self) -> dict:
        """Derive completed/pending/findings for the heal checkpoint from history.

        Reuses the same summary engine as ``context_injector.build_resume_context``
        (``SummarizationPipeline`` over the session's DB messages), keyed by
        ``_app_session_id`` (never the SDK id — 3.5). Budget-bounded (capped fetch +
        3s timeout) and fully guarded: returns ``{}`` on ANY failure or when
        ``_app_session_id`` is unavailable, so enrichment can never raise into the
        streaming loop (2.6 / Property 3).
        """
        app_session_id = getattr(self, "_app_session_id", None)
        if not app_session_id:
            return {}
        try:
            async def _derive() -> dict:
                from database import db
                from .summarization import SummarizationPipeline

                raw_messages = await db.messages.list_by_session_paginated(
                    app_session_id, limit=60
                )
                if not raw_messages:
                    return {}
                summary = await SummarizationPipeline().summarize(raw_messages)
                completed = [s for s in summary.decisions if s][:8]
                pending = [
                    s for s in (summary.open_questions or summary.topics) if s
                ][:8]
                findings = ""
                if summary.decisions:
                    findings = "; ".join(summary.decisions[:2])[:300]
                elif summary.topics:
                    findings = "; ".join(summary.topics[:2])[:300]
                return {
                    "completed_steps": completed,
                    "pending_steps": pending,
                    "key_findings": findings,
                }

            return await asyncio.wait_for(_derive(), timeout=3.0)
        except Exception:
            logger.debug(
                "session_unit.self_heal_enrichment_failed session_id=%s",
                self.session_id, exc_info=True,
            )
            return {}

    async def _clean_poisoned_transcript(self) -> None:
        """Clean a poisoned CLI transcript (glitch prefix / degenerate
        repetition run) before the next turn reads it.

        The live CLI holds its history in memory, so a warm process is recycled
        first (identity kept → the following spawn uses --resume on the cleaned
        file). One-shot: the flag is cleared up front. Never raises.
        """
        from .leak_transcript import clean_transcript_leaks, find_transcript

        self._transcript_poisoned = False
        sid = self._sdk_session_id
        if not sid:
            return  # no --resume identity → the next spawn won't read it
        try:
            if self.is_alive:
                await self._arm_recovery_checkpoint("glitch_clean_recycle")
                await self._crash_to_cold_async(clear_identity=False)
            path = await asyncio.to_thread(find_transcript, sid)
            cleaned = (
                await asyncio.to_thread(clean_transcript_leaks, path) if path else 0
            )
            logger.warning(
                "session_unit.transcript_cleaned session_id=%s "
                "transcript_found=%s blocks_cleaned=%d",
                self.session_id, path is not None, cleaned,
            )
        except Exception as exc:  # noqa: BLE001 — cleaning must not block send()
            logger.warning(
                "session_unit.tool_call_glitch_clean_failed session_id=%s: %s",
                self.session_id, exc,
            )

    async def _arm_recovery_checkpoint(
        self, trigger: str, *, allow_wrapup: bool = False
    ) -> None:
        """Arm the rich recovery checkpoint before an INVOLUNTARY keep-resume kill.

        Mirrors the voluntary self-heal checkpoint so that ANY ``kill → COLD``
        path which preserves ``_sdk_session_id`` (RSS proactive restart,
        streaming RSS kill, stuck-STREAMING / stuck-WAITING_INPUT recovery,
        watchdog death) gives the next ``send()`` the same structured
        "where I left off" context the gated self-heal already builds. This
        adds NO new kill — it only enriches recovery on kills that already
        happen, so any respawn (not just gated self-heal) resumes with context.

        Idempotent + additive:
        - Returns immediately if a checkpoint is already armed — never clobbers
          a richer voluntary checkpoint that has not yet been consumed.
        - Keyed ONLY by this session: ``_derive_heal_enrichment`` keys off
          ``_app_session_id``. No new module-level state.
        - Fully guarded: enrichment failure / missing app_session_id / build
          failure all degrade to a no-op. NEVER raises into the kill path.
        - ``allow_wrapup`` folds the agent's wrap-up conclusion into the
          checkpoint when one exists; involuntary callers leave it False.

        Mirrors the self-heal logging style: field names + lengths only,
        never raw content/PII.
        """
        # Tag fast-recycle kills so the resulting SIGKILL ("exit code -9") is
        # classified ZOMBIE (~0.5s respawn) not OOM (30/60/120s backoff). Set
        # BEFORE the idempotency early-return so it reflects the latest kill
        # reason even when a checkpoint is already armed. ONLY the poisoned/
        # corrupt-turn-state recycles (flush/interrupt/poison_guard) — NOT
        # rss_*/stuck_*/watchdog (those are real memory pressure / genuine
        # hangs that keep their own 30/60/120s backoff semantics).
        self._recycle_kill_pending = trigger in (
            "flush_recycle", "interrupt_recycle", "poison_guard_recycle",
        )

        if self._heal_checkpoint is not None:
            return  # Don't clobber a richer voluntary checkpoint
        try:
            enrich = await self._derive_heal_enrichment()
            conclusion = (
                self._wrapup_conclusion
                if (allow_wrapup and getattr(self, "_wrapup_conclusion", ""))
                else ""
            )
            _ws_dir = str(Path.home() / ".swarm-ai" / "SwarmWS")
            _turn_count = getattr(
                getattr(self, "_health_sensor", None), "turn_count", 0
            )
            self._heal_checkpoint = await build_rich_checkpoint(
                original_request=getattr(self, "_last_user_query", "") or "",
                working_dir=_ws_dir,
                file_tracker_paths=list(getattr(self, "_files_touched", [])),
                turn_count=_turn_count,
                trigger=trigger,
                agent_conclusion=conclusion,
                completed_steps=enrich.get("completed_steps"),
                pending_steps=enrich.get("pending_steps"),
                key_findings=enrich.get("key_findings", ""),
                pipeline_run_id=getattr(self, "_pipeline_run_id", None),
                pipeline_stage=getattr(self, "_pipeline_stage", None),
            )
            _cp = self._heal_checkpoint
            logger.info(
                "session_unit.recovery_checkpoint_armed session_id=%s "
                "trigger=%s fields=[completed=%d,pending=%d,findings=%d,"
                "pipeline=%s,active_file=%d] conclusion_len=%d approx_chars=%d",
                self.session_id, trigger,
                len(_cp.completed_steps), len(_cp.pending_steps),
                len(_cp.key_findings), bool(_cp.pipeline_run_id),
                len(_cp.active_file_context), len(conclusion),
                len(_cp.to_continuation_prompt()),
            )
        except Exception:
            logger.debug(
                "session_unit.recovery_checkpoint_arm_failed session_id=%s "
                "trigger=%s", self.session_id, trigger, exc_info=True,
            )
            return

    async def _crash_to_cold_async(self, *, clear_identity: bool = False) -> None:
        """Async transition DEAD → COLD with proper wrapper cleanup.

        Calls ``await _force_kill()`` which properly closes the wrapper's
        file descriptors via ``__aexit__()`` before clearing references.

        Args:
            clear_identity: If True, also clears ``_sdk_session_id``
                via ``_full_cleanup()`` (non-retriable crashes).

        R4 (RecoveryTransaction): this and ``kill()`` are the two kill entry
        points, and BOTH hold ``self._lock`` so they are mutually exclusive —
        the per-session streaming/retry paths converge here; the lifecycle
        loop's RSS/TTL/pressure paths go through ``kill()``. Two async tasks
        can reach a kill concurrently: the LifecycleManager background loop and
        the per-session streaming task. The shared lock serializes them. The previous implementation was LOCKLESS —
        between ``_transition(DEAD)`` and the final ``_transition(COLD)`` a second
        task could interleave and run the whole sequence again (double
        ``_force_kill``, double cleanup, thrash). This is the TOCTOU window the
        hang-class audit flagged.

        The transaction now holds ``self._lock`` (previously dead code) across
        the entire arm→DEAD→kill→cleanup→COLD sequence and is IDEMPOTENT: a unit
        already COLD (no subprocess to kill) returns immediately. Result: N
        concurrent callers ⇒ exactly ONE teardown, the rest no-op. The kill
        MECHANICS are unchanged — only the atomicity is new.
        """
        async with self._lock:
            # Idempotent: already torn down (another task won the race, or this
            # unit never spawned). Nothing to KILL — but a clear_identity=True
            # call on an already-COLD unit must STILL drop the --resume identity.
            # The retry-exhausted / give-up paths (retry_manager give-up, buffer
            # overflow, budget denial) reach here AFTER a failed re-spawn already
            # left the unit COLD; skipping the identity drop would silently revive
            # the doomed --resume the give-up was meant to break (adversarial M5
            # Q3 regression). _full_cleanup's other resets are no-ops on an
            # already-clean unit, so clearing _sdk_session_id is the load-bearing
            # part to honor here.
            if self.state == SessionState.COLD:
                if clear_identity:
                    self._sdk_session_id = None
                return
            self._transition(SessionState.DEAD)
            await self._force_kill()
            if clear_identity:
                self._full_cleanup()
            else:
                self._cleanup_internal()
            self._transition(SessionState.COLD)

    @property
    def streaming_stall_seconds(self) -> Optional[float]:
        """Seconds since the last REAL CONTENT PROGRESS while STREAMING.

        Reads ``_last_progress_time`` (advanced only on text/thinking deltas,
        AssistantMessage, sub-agent tool_result) — NOT ``_last_event_time``
        (which any SDK message refreshes). This is the discriminator that makes
        the lifecycle watchdog measure "content stopped" instead of "pipe went
        quiet": a frozen turn whose subprocess keeps emitting framing/non-content
        messages would keep _last_event_time fresh forever (假 streaming →
        spinner pinned + slot leak); the progress clock goes stale correctly.

        Falls back to ``_streaming_start_time`` before the first progress event
        (preserves dumb-spawn "no token since spawn" detection). Returns ``None``
        if not streaming.
        """
        if self.state != SessionState.STREAMING:
            return None
        if self._last_progress_time is None:
            # No real progress yet — measure from streaming start (dumb-spawn).
            if self._streaming_start_time is not None:
                return time.time() - self._streaming_start_time
            return None
        return time.time() - self._last_progress_time

    # Circuit breaker threshold for force_unstick_streaming.
    # After this many consecutive unstick attempts without a successful
    # streaming response, stop preserving session identity (no more --resume).
    # This breaks the dead loop: timeout → unstick → resume → same timeout.
    _UNSTICK_CIRCUIT_BREAKER_THRESHOLD: int = 2

    # ② zero-token hang guard (run_22393305): the lifecycle watchdog's EARLY
    # resume-branch wedged consult fires only once stall exceeds this
    # intermediate threshold (2× DUMB_SPAWN, i.e. the same floor the resume
    # dumb-spawn branch already uses) AND the subprocess is provably wedged on
    # RESUME_WEDGED_STRIKE_THRESHOLD consecutive ticks. Below the stall floor the
    # verdict is not consulted at all (short-stall no-op). This recovers a
    # b5e495cb-class zero-token hang near ~240s+ one tick instead of ~1800s,
    # WITHOUT lowering any existing timeout and WITHOUT ever killing a 'working'
    # (CPU-busy) legitimate large replay.
    RESUME_EARLY_WEDGED_MIN_STALL_S: float = DUMB_SPAWN_TIMEOUT_SECONDS * 2
    RESUME_WEDGED_STRIKE_THRESHOLD: int = 2

    async def force_unstick_streaming(self) -> None:
        """Force a stuck STREAMING session back to COLD.

        Kills the subprocess and transitions STREAMING → DEAD → COLD.
        On first/second attempt, preserves ``_sdk_session_id`` so the next
        ``send()`` can resume via ``--resume``.

        Circuit breaker: after _UNSTICK_CIRCUIT_BREAKER_THRESHOLD consecutive
        unstick attempts without a successful stream in between, clears
        session identity (no --resume). This breaks the dead loop where
        --resume sessions repeatedly time out on the same large context.

        Called by ``LifecycleManager._check_streaming_timeout()`` and
        by ``send()`` auto-recovery when the previous request left the
        unit stuck in STREAMING.
        """
        if self.state != SessionState.STREAMING:
            return

        self._consecutive_unstick_timeouts += 1

        # R3d (M3): the KILL-vs-KILL_HARD escalation DECISION now routes through
        # the one recovery authority (GracefulEscalationPolicy). The caller still
        # owns the attempt counter (_consecutive_unstick_timeouts) + threshold;
        # the Coordinator owns the ladder verdict:
        #   attempt <= threshold  → PROCEED_KILL      (preserve --resume)
        #   attempt >  threshold  → PROCEED_KILL_HARD (drop identity, break loop)
        # The PROCEED_KILL vs PROCEED_KILL_HARD split = keep vs drop conversation
        # context, the single most safety-relevant recovery distinction.
        from .session_healing import RecoveryVerdict
        _decision = self._recovery_coordinator.decide_graceful(
            trigger="streaming_timeout",
            enabled=True,
            user_stopped=self._user_stopped_current_turn,
            state=self.state.value,
            attempt=self._consecutive_unstick_timeouts,
            threshold=self._UNSTICK_CIRCUIT_BREAKER_THRESHOLD,
            base=RecoveryVerdict.PROCEED_KILL,
            escalated=RecoveryVerdict.PROCEED_KILL_HARD,
        )

        if _decision.verdict is RecoveryVerdict.SKIP:
            # User stopped this turn — back out the increment (this was not a
            # genuine unstick attempt) and leave the session alone.
            self._consecutive_unstick_timeouts -= 1
            return

        # ② zero-token hang guard: a genuine unstick (not a user-stop SKIP) ENDS
        # this wedge episode — clear the strike counter so the next episode, if
        # the respawn re-wedges on a resume, gets a FRESH two-tick debounce
        # instead of re-killing on its first wedged tick (the stale counter would
        # otherwise collapse the intended two-consecutive-wedged guard to one on
        # every episode after the first). The healthy-stream reset (~:2396) only
        # fires if a turn SUCCEEDS in between; this covers the kill→respawn→re-wedge
        # path where no success intervenes.
        self._resume_wedged_strikes = 0

        if _decision.verdict is RecoveryVerdict.PROCEED_KILL_HARD:
            # Circuit breaker tripped: stop retrying with --resume (structurally
            # doomed — timeout → unstick → resume → same timeout).
            logger.warning(
                "session_unit.force_unstick_circuit_breaker session_id=%s "
                "consecutive_unsticks=%d > threshold=%d — "
                "clearing identity (no more --resume)",
                self.session_id,
                self._consecutive_unstick_timeouts,
                self._UNSTICK_CIRCUIT_BREAKER_THRESHOLD,
            )
            await self._arm_recovery_checkpoint("stuck_streaming_circuit_break")
            await self._crash_to_cold_async(clear_identity=True)
            # Reset counter after clearing identity — the next session starts
            # fresh (no --resume), so it won't hit the same timeout loop.
            # Without this reset, the lifecycle manager would permanently skip
            # this session even after it gets a clean start.
            self._consecutive_unstick_timeouts = 0
            return

        # PROCEED_KILL (base rung): kill but preserve --resume identity.
        logger.warning(
            "session_unit.force_unstick session_id=%s pid=%s "
            "stall=%.0fs attempt=%d — forcing COLD for recovery",
            self.session_id,
            self.pid,
            self.streaming_stall_seconds or 0,
            self._consecutive_unstick_timeouts,
        )
        await self._arm_recovery_checkpoint("stuck_streaming")
        # read by the retry's turn_notice: this -9 is the stall watchdog, not a crash
        self._last_unstick_at = time.monotonic()
        # and by classify_failure: not OOM (consumed once by the retry)
        self._stall_kill_pending = True
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
        await self._arm_recovery_checkpoint("stuck_waiting_input")
        await self._crash_to_cold_async(clear_identity=False)

    async def refresh_context(self) -> None:
        """User-triggered context refresh — kill subprocess + drop resume identity.

        Explicitly user-initiated (the "Refresh Context" button). Uses
        ``clear_identity=True`` to DROP ``_sdk_session_id`` — this is the
        load-bearing choice: with the SDK session id gone, the next ``send()``
        does NOT take the SDK ``--resume`` path (which would replay the FULL
        transcript, defeating the button's purpose). Instead it becomes a TRUE
        cold resume — ``session_router.is_cold_resume`` (which requires
        ``_sdk_session_id is None``) turns True, so ``needs_context_injection``
        is set and ``build_resume_context`` injects a STRUCTURED conversation
        summary (mechanism B, ~50-100K tokens) into the fresh system prompt.

        Net effect = the AI restarts on a summary, shedding the bloated
        transcript — which is exactly what "refresh context" means. Works from
        any non-STREAMING state.

        After this call, state = COLD and next send() auto-resumes via
        mechanism-B summary injection.
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
        # clear_identity=True → drops _sdk_session_id → next send() is a cold
        # resume → mechanism-B structured-summary injection (see docstring).
        await self._crash_to_cold_async(clear_identity=True)

    def clear_session_identity(self) -> None:
        """Clear ``_sdk_session_id`` so the unit cannot resume.

        Called by ``SessionRouter.disconnect_all()`` after ``kill()``
        to ensure shutdown fully cleans up session identity.
        """
        self._sdk_session_id = None
