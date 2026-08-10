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
# Context-ring SOFT compaction threshold: when measured context% crosses this,
# proactively compact at the next IDLE — BEFORE the slow turn. Soft-first
# (compact, never kill). The HARD notice (~85%, "start a new tab") already lives
# in prompt_builder.build_context_warning (AC2).
# # assumes: 60% of a 1M window (~600K tokens) is where per-turn latency starts
# climbing materially; compacting here keeps turns fast. Tune per model window.
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

# Cooldown between context-ring SOFT compactions (seconds). Prevents back-to-back
# compaction if context% stays high right after a SUCCESSFUL compact.
SOFT_COMPACT_COOLDOWN: float = 180.0

# Hang ceiling for the SOFT (never-kill) compaction path (run_37822fae). A real
# LLM /compact of ~600K tokens (SOFT_COMPACT_PCT=60% of a 1M window) legitimately
# takes well over 30s — the old 30s bound was COPIED from the proactive-restart
# KILL path (lifecycle_manager), where "proceed to kill on timeout" justifies a
# tight bound. On the soft path there is no kill, so a tight bound only abandons
# a slow-but-progressing compact mid-flight, leaving the in-flight /compact in a
# half-state. This ceiling exists ONLY to bound a genuine HANG (SDK deadlock), not
# to guillotine a slow compact. The soft path is awaited in the post-turn IDLE
# hook (user not blocked), so a generous ceiling is safe.
SOFT_COMPACT_HANG_S: float = 300.0

# Retry backoff after a FAILED/timed-out soft compaction (seconds). Distinct from
# SOFT_COMPACT_COOLDOWN (the post-SUCCESS cooldown): on failure we must NOT stamp
# the full 180s success-cooldown (that was the bug — a timed-out compact marked
# "handled" and wouldn't retry for 180s while context kept growing). A short
# backoff retries soon (fail-safe: context left fully intact, try again shortly)
# without hammering a persistently-failing compact every turn.
SOFT_COMPACT_FAIL_BACKOFF: float = 30.0

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
        # kill()). ``_client_io`` serializes the consumers of the SINGLE
        # ``self._client.receive_response()`` anyio channel: a post-turn
        # ``compact()`` holds it across its query+drain, and the foreground
        # turn takes a SHORT BARRIER at send()'s IDLE-entry (acquire→release,
        # NOT held across the streaming body — that would self-deadlock with the
        # in-loop CompactionGuard interrupt and break user Stop). Two concurrent
        # ``receive_response()`` drives would otherwise split the SDK stream and
        # co-starve → double timeout → kill+respawn (the 3-5min "freeze").
        # interrupt()/flush stay lock-FREE: they are control-channel calls, not
        # receive_response, and acquiring here would deadlock Stop against the
        # very turn it stops. The two background maintenance ops probe
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
        self._model_name: Optional[str] = None

        # ── Recall injection (G3: post-first-message) ─────────────
        # Set True after first-message recall runs (or is skipped).
        # Prevents re-running on subsequent messages in the same session.
        self._recall_injected: bool = False
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
        # Per-message tool-call-leak recovery flag (run_37008f2d). When the model
        # emits tool-call syntax as plain text (a "leak"), the 1st occurrence gets
        # ONE corrective-resume attempt (_handle_tool_call_leak injects a
        # descriptive correction into the next query, then --resume). This flag,
        # set True by that handler BEFORE its resume stream runs, makes a re-leak
        # DURING/AFTER recovery recognizable as the 2nd leak → routed to a clean
        # terminal (clear_identity, leak-specific event) instead of another resume.
        # That BOUNDS the self-reinforcing loop (a bare --resume replays the
        # poisoned SDK transcript → deterministic re-leak; log e9d7c08d leaked 2×).
        # Reset per-message at send() entry (NOT Layer-0), mirroring the
        # buffer-overflow flag — a fresh user turn gets a fresh recovery budget.
        self._tool_call_leak_recovery: bool = False

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

        # ── Proactive RSS restart cooldown ────────────────────────
        # Monotonic timestamp of last proactive compact→kill cycle.
        # Prevents repeated restarts within the PROACTIVE_COOLDOWN window.
        # Uses monotonic clock — immune to NTP sync / sleep-wake clock jumps.
        # -inf ensures first restart is never cooldown-blocked (monotonic()
        # can be < PROACTIVE_COOLDOWN on freshly booted CI runners).
        self._last_proactive_restart: float = float("-inf")

        # Monotonic timestamp of last context-ring SOFT compaction (Root 2 / AC1).
        # Separate from _last_proactive_restart: AC1 compacts WITHOUT killing,
        # so it has its own cooldown to avoid back-to-back compactions.
        self._last_soft_compact: float = float("-inf")

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
        # ⚠️ LOAD-BEARING INVARIANT / STALE-KEY DEPENDENCY (run_2aea0237 retro):
        # this field is NEVER reset to None — it is set once at init and only ever
        # UPDATED (never cleared) at router:2294. It therefore holds the LAST keyed
        # turn's client_id for the unit's whole lifetime. The readers (router:2505/
        # 2533) stamp `{_turn_client_id}-asst` on EVERY persisted assistant row.
        # Safety today rests on ONE premise: within a single unit, no *keyed* turn is
        # ever followed by a *keyless main-path* turn. If that premise breaks — a
        # future keyless send path on a desktop unit that previously had a keyed turn
        # — the keyless turn's row would be stamped with the PRIOR turn's key →
        # content attached to the wrong bubble (worse than a dup). Currently
        # unreachable: (a) all desktop send paths carry a key after run_2aea0237's
        # 5-entrance sweep (drain/retry/reconnect/continuation); (b) channel sessions
        # are a SEPARATE unit (is_channel_session, own channel_sessions mapping) that
        # is keyless for its whole life → stash stays None → rows get client_id=None
        # (safe no-match, never a stale key). The `if client_id` guard at router:2293
        # additionally prevents a keyless turn from CLOBBERING a valid stash. If a
        # new keyless-main-path desktop entrance is ever added, reset this to None at
        # that turn's admission (NOT pre-loop — pre-loop reset re-opens the
        # WAITING_INPUT/SessionBusyError clobber the 2293 guard closed).
        self._turn_client_id: Optional[str] = None
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
        """True while a post-disconnect pipe-flush task is still running — i.e.
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

        # Reset per-send state (retry counter + buffer overflow flag).
        # _buffer_overflow_recovery is per-message, not per-session:
        # each new user message should get a fresh recovery attempt
        # if it triggers a different buffer overflow.
        self._retry_count = 0
        # _interrupted already cleared in Layer 0 above
        self._buffer_overflow_recovery = False
        self._tool_call_leak_recovery = False  # per-message: fresh leak-recovery budget
        self._compaction_guard.reset()  # New user turn — reset tool tracking
        self._content_emitted = False   # Track if meaningful content is emitted
        self._active_agent_tools = {}  # Clear stale sub-agent progress on new turn
        self._open_tool_uses = {}  # Clear stale open-tool tracking (run_fb6e94a9)
        # Gate-2 F3 (belt-and-suspenders): a fresh user turn never carries a stale
        # outstanding-tool_use guard. Gate-2 F4: drop the stale last-drained hint so
        # the read API doesn't surface seqs from a previous turn indefinitely.
        self._pending_tool_use_id = None
        self._pending_question = None
        self._last_drained_seqs = []

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
        if (
            self.state == SessionState.IDLE
            and self._client is not None
            and not self._last_turn_clean
        ):
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
        if self.state == SessionState.IDLE and self._client is None:
            self._transition(SessionState.COLD)
        if self.state == SessionState.COLD:
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

        # Subprocess-IO barrier (run_4b74b764): if a post-turn compact() is
        # draining the SAME subprocess, wait for it to finish before this turn
        # drives receive_response — two consumers on the single SDK channel
        # co-starve. This is a SHORT barrier (acquire→immediately release): we do
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

        # IDLE → STREAMING
        self._transition(SessionState.STREAMING)
        self._model_name = getattr(options, "model", None)

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
            async for event in self._streaming_orchestrator.stream_query(query_content):
                if _capturing_wrapup_turn:
                    self._capture_wrapup_text(event)
                yield event
            # Success — reset counters (session is healthy)
            self._consecutive_oom_kills = 0
            self._consecutive_unstick_timeouts = 0

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

            # ── Tool-call leak — bounded corrective-resume (run_37008f2d) ──
            # A leak gets ONE corrective resume (1st) then a clean terminal (2nd),
            # NEVER the bare _retry_with_resume below (which replays the poisoned
            # transcript → deterministic re-leak loop). The dispatcher consumes the
            # error and we return — so a leak cannot reach :retriable or :crash.
            # Re-test the marker on a recovery fallthrough rather than delegating to
            # _is_retriable_error, so a re-leak NEVER slips to the bare resume.
            if "Tool-call XML leaked into text channel" in error_str:
                # Mirror the buffer-overflow pattern above (a `recovered` boolean,
                # NO for/else): the dispatcher's outcome is captured in flags so a
                # NON-leak transient that surfaces during the corrective resume
                # FALLS THROUGH to the generic retriable path below (OOM cooldown /
                # backoff), instead of being swallowed. Gate-2 (run_37008f2d) caught
                # a for/else footgun here: `else` with no `break` fired on every
                # completion, silently eating non-leak recovery errors.
                leak_handled = False  # turn is over (recovered / aborted / terminal)
                async for event in self._dispatch_leak_recovery(
                    error_str, query_content, options, config,
                ):
                    if event.get("_abort"):
                        leak_handled = True  # spawn failed during recovery
                        break
                    if event.get("_recovered"):
                        leak_handled = True  # corrective resume succeeded
                        break
                    if "_fallthrough_error" in event:
                        # Recovery stream raised. Re-point error context: if it
                        # RE-LEAKED, re-dispatch (flag now True → clean terminal);
                        # if NOT a leak, leave leak_handled False so the error
                        # falls through to the generic retriable/crash path below.
                        error_str = event["_fallthrough_error"]
                        tb_str = event.get("_fallthrough_tb", tb_str)
                        if "Tool-call XML leaked into text channel" in error_str:
                            async for ev2 in self._dispatch_leak_recovery(
                                error_str, query_content, options, config,
                            ):
                                yield ev2
                            leak_handled = True  # 2nd-leak terminal ended the turn
                        break
                    yield event
                else:
                    # Loop completed with NO break = the 2nd-leak terminal path
                    # (dispatcher yielded a terminal event, no sentinel). Turn over.
                    leak_handled = True
                if leak_handled:
                    return
                # else: a non-leak error surfaced during recovery — fall through to
                # the generic retriable/crash handling below with the updated
                # error_str (NOT swallowed).

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
                logger.warning(
                    "Session %s: --resume failed (session not found), "
                    "clearing stale sdk_session_id and falling back to cold resume",
                    self.session_id,
                )
                self._sdk_session_id = None
                # Retry without --resume: strip resume field from options
                from claude_agent_sdk import ClaudeAgentOptions as _Opts
                kwargs = dict(vars(options))
                kwargs.pop("resume", None)
                options_no_resume = _Opts(**kwargs)
                try:
                    await self._spawn(options_no_resume, config)
                    return  # success — cold resume path
                except Exception as fallback_exc:
                    error_str = str(fallback_exc)
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

    async def _handle_tool_call_leak(
        self,
        query_content: Any,
        options: "ClaudeAgentOptions",
        config: Optional[Any],
        error_str: str,
    ) -> AsyncIterator[dict]:
        """One corrective-resume for a tool-call leak (run_37008f2d).

        Implementation in RetryManager. Delegation stub.
        """
        async for event in self._retry_manager._handle_tool_call_leak(
            query_content, options, config, error_str
        ):
            yield event

    async def _dispatch_leak_recovery(
        self,
        error_str: str,
        query_content: Any,
        options: "ClaudeAgentOptions",
        config: Optional[Any],
    ) -> AsyncIterator[dict]:
        """Route a detected tool-call leak: 1st → corrective resume, 2nd → terminal.

        Called from send()'s except block when ``error_str`` carries the leak
        marker. BOUNDS the self-reinforcing leak→bare-resume→re-leak loop:

        - 1st leak (``_tool_call_leak_recovery`` False) → ``_handle_tool_call_leak``
          (inject a descriptive correction + --resume ONCE). The handler sets the
          flag True before its resume stream, so a re-leak during recovery is seen
          here as the 2nd leak.
        - 2nd consecutive leak (flag already True) → CLEAN TERMINAL: do NOT resume
          again (it would replay the same poisoned transcript). ``clear_identity``
          drops the poisoned --resume target so the user's NEXT turn starts fresh
          (not re-poisoned — Gate-1 check #4), then surface a leak-specific event.

        The dispatcher CONSUMES the leak error (the caller returns after it), so a
        leak never falls through to the bare ``_retry_with_resume`` at the generic
        retriable branch. ``_is_retriable_error`` still recognizes the leak string
        (INV3 preserved) as a backstop — the dispatcher is the primary route.

        Yields stream events; ``_abort`` / ``_recovered`` sentinels are consumed by
        send() exactly as the buffer-overflow path does. On the 2nd-leak terminal it
        yields a terminal error event then returns (no sentinel — turn is over).
        """
        from .session_utils import _build_error_event

        if not self._tool_call_leak_recovery:
            # 1st leak — one corrective-resume attempt.
            async for event in self._handle_tool_call_leak(
                query_content, options, config, error_str
            ):
                yield event
            return

        # 2nd consecutive leak — bounded. Drop the poisoned resume identity so the
        # next user turn does not --resume back into the same poison, then surface a
        # leak-specific terminal (NOT a generic CONVERSATION_ERROR).
        logger.warning(
            "session_unit.tool_call_leak_terminal session_id=%s — second "
            "consecutive leak after corrective resume; dropping resume identity "
            "and ending turn (bounded loop)",
            self.session_id,
        )
        await self._crash_to_cold_async(clear_identity=True)
        yield _build_error_event(
            code="TOOL_CALL_LEAK_UNRECOVERED",
            message=(
                "The AI repeatedly emitted a tool call as text instead of "
                "executing it, even after a correction. The turn was stopped to "
                "avoid a retry loop. Please re-send your request."
            ),
            detail="tool_call_leak: 2nd consecutive leak after corrective resume",
            suggested_action="Re-send the message (a fresh turn starts clean).",
        )

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
        # Root 2 / AC4: observability — log context-ring size + turn count, and
        # escalate to WARN when context% is near the soft cap so the amplifier is
        # visible BEFORE it bites (vs. the INFO debug line that's easy to miss).
        _ctx_pct = 0.0
        if input_tokens and input_tokens > 0:
            try:
                _win = PromptBuilder.get_model_context_window(self._model_name)
                _ctx_pct = (input_tokens / _win) * 100 if _win > 0 else 0.0
            except Exception:
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

        # (C) non-blocking yield (run_4b74b764): get_mcp_status() drives the same
        # subprocess. If a foreground turn holds _client_io, SKIP this round
        # WITHOUT consuming the one-shot flag, so the check still runs after the
        # turn releases (it is a one-time post-first-turn probe, not best-effort
        # discardable like soft-compact). Probe BEFORE the flag-set so a skipped
        # round is not mistaken for a completed check.
        if self._client_io.locked():
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

    async def _read_formatted_response(self) -> AsyncIterator[dict]:
        """Read SDK response stream and yield formatted SSE events.

        Implementation lives in StreamingOrchestrator. This delegation method
        exists because continue_with_permission() and several test files call
        it directly on the SessionUnit instance.
        """
        async for event in self._streaming_orchestrator._read_formatted_response():
            yield event

    def recover_from_disconnect(self) -> bool:
        """Transition STREAMING → a CLEAN IDLE after SSE client disconnect.

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
        # Arm the same rich recovery checkpoint the voluntary self-heal builds
        # so the lazy --resume on next send() starts with structured context.
        await self._arm_recovery_checkpoint("rss_proactive")
        await self.kill()

        self._last_proactive_restart = time.monotonic()

    async def _check_context_soft_compact(self) -> None:
        """Context-ring soft cap (Root 2 / AC1, G1): compact at IDLE if large.

        Called from the post-turn IDLE hook (alongside the RSS check). When the
        last measured context% crosses SOFT_COMPACT_PCT, proactively compact the
        conversation BEFORE the next (slow) turn. Soft-first: compact only, NEVER
        kill (that distinguishes it from the RSS proactive restart).

        Preconditions enforced here:
        - state must be IDLE (compact() requires it; the hook runs post-IDLE)
        - own cooldown (SOFT_COMPACT_COOLDOWN) to avoid back-to-back compactions
        Non-fatal — any failure is swallowed (must never block the stream).
        """
        if self.state != SessionState.IDLE:
            return
        # (C) non-blocking yield (run_4b74b764): if a foreground turn holds the
        # subprocess (its send() barrier or a racing compact owns _client_io),
        # SKIP this round rather than awaiting compact() — compact() would block
        # on the same lock and stall the post-turn hook (and the generator that
        # drives it). soft-compact is best-effort; the next IDLE hook retries.
        if self._client_io.locked():
            return
        tokens = getattr(self, "_last_known_context_tokens", 0) or 0
        if tokens <= 0:
            return
        if time.monotonic() - self._last_soft_compact < SOFT_COMPACT_COOLDOWN:
            return
        try:
            from .prompt_builder import PromptBuilder
            window = PromptBuilder.get_model_context_window(self._model_name)
        except Exception as exc:  # noqa: BLE001
            # Degrade-OBSERVABLE. Returning here disables SOFT COMPACTION for this
            # session, so context keeps growing until it fails hard at the window
            # boundary — a late, confusing failure whose actual cause (an unresolvable
            # model window) happened much earlier and said nothing.
            logger.warning("session_unit.soft_compact_skipped session_id=%s model=%s — "
                           "cannot resolve context window: %s",
                           self.session_id, self._model_name, exc)
            return
        if window <= 0:
            return
        pct = (tokens / window) * 100
        if pct < SOFT_COMPACT_PCT:
            return
        logger.info(
            "session_unit.context_soft_compact session_id=%s pct=%.0f%% "
            "tokens=%d window=%d — compacting (no kill)",
            self.session_id, pct, tokens, window,
        )
        # Re-entrancy stamp BEFORE awaiting (Gate-2 F5, run_37822fae): compact()
        # stays IDLE for its whole (long) duration and does NOT _transition, so the
        # state gate above provides NO mutual exclusion. Stamp the cooldown now so a
        # concurrent post-turn hook (or a racing manual /compact) is cooldown-blocked
        # and can't fire a second /compact at the same subprocess. Corrected below
        # to the fail-backoff if this attempt does not succeed.
        self._last_soft_compact = time.monotonic()

        # Bound a genuine HANG only (run_37822fae). The old 30s was copied from
        # the proactive-restart KILL path; on this soft (never-kill) path it only
        # guillotined a slow-but-progressing compact. SOFT_COMPACT_HANG_S is
        # generous (the post-turn hook isn't user-blocking) so a real LLM summary
        # of ~600K tokens can COMPLETE — carry task-needed context across.
        succeeded = False
        try:
            result = await asyncio.wait_for(
                self.compact(), timeout=SOFT_COMPACT_HANG_S
            )
            # Gate-2 F1: compact() SWALLOWS failures and returns {"success": False}
            # (no subprocess / not IDLE / internal SDK error) — it does NOT raise.
            # "did not raise" ≠ "compacted". Inspect the return value, else a
            # logical failure would stamp the full success cooldown = the very bug
            # this run fixes, for the MOST COMMON failure path.
            succeeded = bool(result and result.get("success"))
        except asyncio.TimeoutError:
            logger.warning(
                "session_unit.context_soft_compact timed out (>%.0fs) session_id=%s",
                SOFT_COMPACT_HANG_S, self.session_id,
            )
        except Exception as exc:
            logger.warning(
                "session_unit.context_soft_compact failed session_id=%s: %s",
                self.session_id, exc,
            )
        # Fail-SAFE cooldown (run_37822fae): SUCCESS keeps the full
        # SOFT_COMPACT_COOLDOWN (already stamped above). On FAILURE/timeout the
        # context is in an unknown-but-recoverable state (the in-flight /compact may
        # have completed, partially completed, or not — wait_for cancels the WAIT,
        # not the subprocess-side command). Stamping the full cooldown was the bug
        # (marked "handled", no retry for 180s while context grew). Back-date so
        # only SOFT_COMPACT_FAIL_BACKOFF remains: a near-term retry reconciles the
        # state without hammering a persistently-failing compact every turn.
        if not succeeded:
            self._last_soft_compact = time.monotonic() - max(
                0.0, SOFT_COMPACT_COOLDOWN - SOFT_COMPACT_FAIL_BACKOFF
            )

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
            # subprocess. Hold _client_io across query+receive_response so the
            # two cannot iterate the single SDK channel simultaneously and
            # co-starve. The foreground turn takes a short barrier on this lock
            # at send()'s IDLE-entry, so it waits for an in-flight compact here.
            async with self._client_io:
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
        self._client = None
        self._wrapper = None
        self._interrupted = False
        # NOTE: self._retry_count is intentionally NOT reset here.
        # It is reset in send() (line ~620) and on success (line ~1672).
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
        self._recall_keyword_misses = 0
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
