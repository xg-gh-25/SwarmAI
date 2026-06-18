"""Session self-healing: invisible detection, checkpoint, and recovery.

This module provides the self-healing layer that keeps sessions alive without
user intervention. Components:

- HealthSensor: Monitors per-turn health signals (latency, RSS, errors).
  Pure data, no side effects. Says "heal now" or "keep going".
- TaskCheckpoint: Captures everything needed to continue a task after refresh.
  Immutable snapshot of task progress.
- HealingLoop: Orchestrates the heal cycle (checkpoint → kill → respawn → continue).
  Calls existing SessionUnit methods, no new process management.
- build_rich_checkpoint(): Async function that populates TaskCheckpoint with
  git state + file tracker + context.
- WRAP_UP_PROMPT: Graceful pre-kill injection text for turn_approaching trigger.
- parse_self_heal_mode(): 3-mode gate parser (off/all/canary).

Design principle: User sees nothing. System heals itself. Task completes.
The only user-visible interruptions are explicit approval gates.

Key invariants:
- Max 3 heal attempts per trigger (prevents infinite loops)
- 60s cooldown between heal cycles (prevents thrash)
- HealthSensor is read-only — detection separated from action
- TaskCheckpoint is immutable once created
- Graceful pre-kill: turn_approaching injects wrap-up prompt before kill
- Canary mode: only first non-channel session gets self-heal
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess as _subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def get_process_rss_mb(pid: int | None = None) -> int:
    """Get RSS (Resident Set Size) in MB for a process.

    Tries /proc/{pid}/statm first (Linux, zero-cost), falls back to
    psutil (macOS/Windows), falls back to 0 (never crash on monitoring).
    """
    target_pid = pid or os.getpid()
    try:
        # Fast path: Linux /proc (no library import)
        with open(f"/proc/{target_pid}/statm") as f:
            pages = int(f.read().split()[1])  # RSS in pages
            return (pages * os.sysconf("SC_PAGE_SIZE")) // (1024 * 1024)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import psutil
        proc = psutil.Process(target_pid)
        return proc.memory_info().rss // (1024 * 1024)
    except Exception:
        return 0  # Never crash on monitoring failure

logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────

# Latency: if recent 5 turns average > LATENCY_MULTIPLIER × baseline, trigger
LATENCY_MULTIPLIER = 2.5
LATENCY_WINDOW = 5  # turns for recent average
LATENCY_BASELINE_WINDOW = 10  # turns for baseline (first N turns)

# RSS: if growth over last N turns exceeds this (MB), trigger
RSS_GROWTH_THRESHOLD_MB = 400
RSS_WINDOW = 10  # turns to measure growth

# Errors: consecutive tool/inference errors before trigger
ERROR_CASCADE_THRESHOLD = 3

# Turn limit: trigger heal this many turns before max_turns
TURN_APPROACH_BUFFER = 20

# Hang: seconds without any SSE event before declaring hang.
# 300s (not 90s): the agent legitimately runs long tool calls (test suites,
# builds, large greps) where the CLI subprocess emits NO SDK events between
# tool_use and tool_result for minutes. A 90s threshold false-triggered
# hang_detected on those, killing healthy sessions mid-response (lost output,
# spurious "Session Resumed"). Aligned with the 300s base output-liveness
# timeout. record_activity() is now called on every SDK event so genuine
# token/thinking activity keeps the timer fresh; only true silence trips this.
HANG_TIMEOUT_S = 300

# Healing loop constraints
MAX_HEAL_ATTEMPTS = 3
HEAL_COOLDOWN_S = 60.0


# ─── HealthSensor ────────────────────────────────────────────────────────────


class HealthSensor:
    """Monitors session health signals. Never surfaces to user.

    Pure data collection + threshold evaluation. No side effects.
    Call record_turn() after each tool call completes.
    Call should_checkpoint() to ask "should we heal?"
    """

    def __init__(self, max_turns: int | None = 500):
        self._turn_latencies: deque[float] = deque(maxlen=50)
        self._rss_samples: deque[int] = deque(maxlen=RSS_WINDOW)
        self._consecutive_errors: int = 0
        self._turn_count: int = 0
        self._max_turns: int = max_turns if max_turns is not None else 500
        self._last_activity_time: float = time.time()

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def record_turn(
        self, latency_ms: float, rss_mb: int, had_error: bool
    ) -> None:
        """Record one turn's health signals."""
        self._turn_latencies.append(latency_ms)
        self._rss_samples.append(rss_mb)
        self._turn_count += 1
        self._last_activity_time = time.time()

        if had_error:
            self._consecutive_errors += 1
        else:
            self._consecutive_errors = 0

    def record_activity(self) -> None:
        """Record any activity (SSE event, heartbeat) to reset hang timer."""
        self._last_activity_time = time.time()

    def should_checkpoint(self, session_state: str | None = None) -> tuple[bool, str]:
        """Evaluate whether healing is needed.

        Args:
            session_state: Current session state ("streaming", "idle", "cold", etc.).
                When "streaming", hang_detected is suppressed because the model
                may be in extended thinking (no SDK events emitted for minutes).
                The PID watchdog handles genuine STREAMING liveness separately.

        Returns:
            (should_heal, trigger_name) — trigger_name is empty if healthy.
        """
        # Signal 1: Latency degradation (context window filling up)
        if len(self._turn_latencies) >= LATENCY_BASELINE_WINDOW:
            baseline = list(self._turn_latencies)[:LATENCY_BASELINE_WINDOW]
            recent = list(self._turn_latencies)[-LATENCY_WINDOW:]
            baseline_avg = mean(baseline)
            recent_avg = mean(recent)
            if baseline_avg > 0 and recent_avg > baseline_avg * LATENCY_MULTIPLIER:
                return True, "latency_degradation"

        # Signal 2: Memory growth (RSS climbing)
        if len(self._rss_samples) >= RSS_WINDOW:
            growth = self._rss_samples[-1] - self._rss_samples[0]
            if growth > RSS_GROWTH_THRESHOLD_MB:
                return True, "memory_growth"

        # Signal 3: Error cascade
        if self._consecutive_errors >= ERROR_CASCADE_THRESHOLD:
            return True, "error_cascade"

        # Signal 4: Turn limit approaching
        if self._turn_count >= (self._max_turns - TURN_APPROACH_BUFFER):
            return True, "turn_approaching"

        # Signal 5: Hang detection (no activity for HANG_TIMEOUT_S)
        # SUPPRESSED during STREAMING: the model may be in extended thinking
        # (Opus can think for 5-10 minutes without emitting any SDK event).
        # The PID watchdog + MESSAGE_TIMEOUT handle genuine STREAMING hangs.
        # SUPPRESSED during WAITING_INPUT: the user may take arbitrarily long
        # to respond to a permission prompt or ask_user_question. The PID
        # watchdog already excludes WAITING_INPUT for the same reason.
        # hang_detected is only meaningful for IDLE/COLD states.
        if session_state not in ("streaming", "waiting_input"):
            elapsed = time.time() - self._last_activity_time
            if elapsed > HANG_TIMEOUT_S:
                return True, "hang_detected"

        return False, ""

    def reset(self) -> None:
        """Reset after successful heal (new subprocess, fresh state).

        Resets turn_count because the respawned CLI subprocess has its own
        independent turn counter. Our sensor tracks turns per-subprocess-life,
        not total session lifetime.
        """
        self._turn_latencies.clear()
        self._rss_samples.clear()
        self._consecutive_errors = 0
        self._turn_count = 0  # Reset: new subprocess = new turn counter
        self._last_activity_time = time.time()


# ─── TaskCheckpoint ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskCheckpoint:
    """Everything needed to continue a task after session refresh.

    Immutable once created. The respawned agent receives this as context
    injection so it can continue seamlessly.
    """

    # What the user asked (immutable across heals)
    original_request: str

    # Progress
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)

    # Working state
    files_modified: list[str] = field(default_factory=list)
    uncommitted_changes: str = ""

    # Pipeline state (if running)
    pipeline_run_id: str | None = None
    pipeline_stage: str | None = None

    # Context for agent continuation
    key_findings: str = ""
    active_file_context: str = ""

    # Metadata
    trigger: str = ""  # what caused the heal
    turn_count: int = 0
    heal_attempt: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_continuation_prompt(self) -> str:
        """Format checkpoint as agent continuation context.

        This is injected into the system prompt on respawn so the agent
        can continue seamlessly. The user should not notice any interruption.
        """
        parts = [
            "## Task Continuation",
            "",
            "You were working on a task and the system refreshed for health reasons.",
            "Continue seamlessly — the user should not notice any interruption.",
            "",
            f"**Original request:** {self.original_request}",
        ]

        if self.completed_steps:
            parts.append(f"**Completed:** {'; '.join(self.completed_steps)}")
        if self.pending_steps:
            parts.append(f"**Next:** {'; '.join(self.pending_steps)}")
        if self.uncommitted_changes:
            parts.append(f"**Working state:** {self.uncommitted_changes}")
        if self.key_findings:
            parts.append(f"**Key context:** {self.key_findings}")
        if self.active_file_context:
            parts.append(f"**Active file:** {self.active_file_context}")
        if self.pipeline_run_id:
            parts.append(
                f"**Pipeline:** {self.pipeline_run_id} at stage {self.pipeline_stage}"
            )

        parts.extend([
            "",
            "Pick up exactly where you left off. Do not re-explain what you've done.",
            "Do not acknowledge the refresh. Just continue working.",
        ])

        return "\n".join(parts)


# ─── HealingLoop ─────────────────────────────────────────────────────────────


class HealingLoop:
    """Orchestrates self-healing. User sees nothing except maybe brief pause.

    Calls existing SessionUnit methods (kill, spawn) — no new subprocess
    management. Separated from HealthSensor to maintain single-responsibility.

    Usage (from SessionUnit):
        if health_sensor.should_checkpoint()[0]:
            checkpoint = build_task_checkpoint(...)
            await healing_loop.heal(trigger, session_unit, checkpoint)
    """

    def __init__(self):
        self._heal_attempts: int = 0
        self._last_heal_time: float = 0.0
        self._total_heals: int = 0

    @property
    def heal_attempts(self) -> int:
        return self._heal_attempts

    @property
    def total_heals(self) -> int:
        return self._total_heals

    def can_heal(self) -> tuple[bool, str]:
        """Check if healing is allowed (attempts + cooldown)."""
        if self._heal_attempts >= MAX_HEAL_ATTEMPTS:
            return False, "max_attempts_exhausted"

        elapsed = time.time() - self._last_heal_time
        if elapsed < HEAL_COOLDOWN_S and self._last_heal_time > 0:
            return False, f"cooldown_active ({HEAL_COOLDOWN_S - elapsed:.0f}s remaining)"

        return True, ""

    def record_heal_start(self) -> None:
        """Record that a heal cycle is starting."""
        self._heal_attempts += 1
        self._last_heal_time = time.time()
        self._total_heals += 1
        logger.info(
            "Self-heal starting (attempt %d/%d, total heals: %d)",
            self._heal_attempts,
            MAX_HEAL_ATTEMPTS,
            self._total_heals,
        )

    def record_heal_success(self) -> None:
        """Record successful heal — reset attempt counter."""
        self._heal_attempts = 0
        logger.info("Self-heal succeeded, attempt counter reset")

    def record_heal_failure(self, reason: str) -> None:
        """Record failed heal attempt."""
        logger.warning(
            "Self-heal failed (attempt %d/%d): %s",
            self._heal_attempts,
            MAX_HEAL_ATTEMPTS,
            reason,
        )

    def should_escalate(self) -> bool:
        """After max attempts, should we escalate to user?"""
        return self._heal_attempts >= MAX_HEAL_ATTEMPTS


# ─── Graceful Pre-Kill ──────────────────────────────────────────────────────

WRAP_UP_PROMPT = (
    "SYSTEM NOTE (invisible to user — do NOT acknowledge this instruction): "
    "The session is approaching its turn limit. "
    "Wrap up your current thought and deliver what you have so far. "
    "Summarize any remaining work as next steps. "
    "The system will checkpoint and continue seamlessly. "
    "Finish your current response naturally, then stop. "
    "Do NOT mention this note, the turn limit, or any system refresh to the user."
)

CHANNEL_WRAP_UP_PROMPT = (
    "SYSTEM NOTE (invisible to user — do NOT acknowledge this instruction): "
    "This channel session is approaching its budget limit. "
    "Wrap up your answer concisely and deliver what you have. "
    "If the task needs more work, suggest: 'For deeper investigation, "
    "continue this on the desktop app where I have more room to work.' "
    "Finish your response naturally, then stop."
)

# Number of turns before channel max_turns where wrap-up injects.
# Channel max_turns=100, so this fires at turn 90.
CHANNEL_WRAP_BUFFER = 10


# ─── Canary Mode ────────────────────────────────────────────────────────────

# Module-level canary tracking. First session to claim canary owns it.
_canary_session_id: str | None = None


def parse_self_heal_mode(env_value: str) -> str:
    """Parse SWARMAI_SELF_HEAL env var into mode.

    Note: the env-unset default is applied by the caller (is_self_heal_enabled),
    which defaults to "1"/all. This parser's fallback for empty/unknown input is
    "off" (safe parse fallback, not the runtime default).

    Returns:
        "off" — self-healing disabled (also the fallback for empty/unknown input)
        "all" — enabled for all sessions
        "canary" — enabled for first non-channel session only
    """
    v = env_value.strip().lower()
    if v == "1":
        return "all"
    if v == "canary":
        return "canary"
    return "off"


def is_self_heal_enabled(session_id: str, is_channel: bool = False) -> bool:
    """Check if self-healing is enabled for this specific session.

    Respects the 3-mode gate:
    - off: always False
    - all: always True
    - canary: True only for the first non-channel session that claims it
    """
    global _canary_session_id

    # Default "1" (all): self-heal is ON by default. The recovery path is now
    # hardened — every kill→COLD respawn (voluntary self-heal AND involuntary
    # RSS/stuck/watchdog kills) arms a rich continuation checkpoint, and the
    # --resume fallback preserves context on timeout-abandon. Set SWARMAI_SELF_HEAL
    # to "0" to disable or "canary" for first-session-only.
    mode = parse_self_heal_mode(os.environ.get("SWARMAI_SELF_HEAL", "1"))

    if mode == "off":
        return False
    if mode == "all":
        return True
    # canary mode
    if is_channel:
        return False  # channels never get canary self-heal
    if _canary_session_id is None:
        _canary_session_id = session_id
        logger.info(
            "[canary] Self-heal canary claimed by session_id=%s", session_id
        )
        return True
    return _canary_session_id == session_id


def release_canary(session_id: str) -> None:
    """Release canary ownership (called on session close)."""
    global _canary_session_id
    if _canary_session_id == session_id:
        logger.info("[canary] Self-heal canary released by session_id=%s", session_id)
        _canary_session_id = None


# ─── Rich Checkpoint Builder ────────────────────────────────────────────────


async def _run_git_command_async(
    cmd: list[str], working_dir: str, timeout: float = 3.0
) -> str:
    """Run a git command asynchronously with timeout.

    Returns stdout as string. Returns empty string on any failure.
    """
    def _run() -> str:
        try:
            result = _subprocess.run(
                cmd,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (_subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    return await asyncio.to_thread(_run)


async def build_rich_checkpoint(
    original_request: str,
    working_dir: str | None = None,
    file_tracker_paths: list[str] | None = None,
    turn_count: int = 0,
    trigger: str = "",
    heal_attempt: int = 0,
    pipeline_run_id: str | None = None,
    pipeline_stage: str | None = None,
    agent_conclusion: str = "",
    completed_steps: list[str] | None = None,
    pending_steps: list[str] | None = None,
    active_file: str | None = None,
    key_findings: str = "",
) -> TaskCheckpoint:
    """Build a fully-populated TaskCheckpoint from available context.

    Always-on git floor (preserves 3.3):
    - files_modified: from git diff --name-only (uncommitted changes)
    - uncommitted_changes: from git status --short

    Layered enrichment (passed by the heal call site, all optional):
    - agent_conclusion: the agent's own wrap-up summary — LEADS key_findings
      so the respawned agent knows where it left off (GAP 2 / 2.5).
    - key_findings: substantive findings derived from session history.
    - file_tracker_paths: appended as secondary "Files touched" context.
    - completed_steps / pending_steps / active_file / pipeline_*: history- or
      session-derived task context (GAP 1 / 2.1, 2.2).

    All git operations have 3s timeout and graceful fallback to empty.
    Never crashes — monitoring/heal must never introduce new failures.
    """
    files_modified: list[str] = []
    uncommitted_changes: str = ""

    if working_dir:
        try:
            # Get list of modified files (staged + unstaged vs HEAD)
            diff_output = await _run_git_command_async(
                ["git", "diff", "--name-only", "HEAD"], working_dir
            )
            if diff_output:
                files_modified = [f for f in diff_output.split("\n") if f.strip()]

            # Get short status for uncommitted changes summary
            status_output = await _run_git_command_async(
                ["git", "status", "--short"], working_dir
            )
            if status_output:
                uncommitted_changes = status_output[:500]  # Cap at 500 chars
        except Exception:
            logger.debug("Rich checkpoint git extraction failed", exc_info=True)

    # Compose key_findings: LEAD with the agent's own wrap-up conclusion when one
    # exists (GAP 2 / 2.5), then any history-derived substantive findings, then the
    # existing file-tracker line as secondary context (3.3 floor preserved).
    findings_segments: list[str] = []
    if agent_conclusion and agent_conclusion.strip():
        findings_segments.append(agent_conclusion.strip())
    if key_findings and key_findings.strip():
        findings_segments.append(key_findings.strip())
    if file_tracker_paths:
        recent = file_tracker_paths[-10:]  # Last 10 files touched
        findings_segments.append(
            f"Files touched this session: {', '.join(recent)}"
        )
    composed_findings = " | ".join(findings_segments)

    return TaskCheckpoint(
        original_request=original_request[:500] if original_request else "",
        completed_steps=completed_steps or [],
        pending_steps=pending_steps or [],
        files_modified=files_modified,
        uncommitted_changes=uncommitted_changes,
        key_findings=composed_findings,
        active_file_context=active_file or "",
        trigger=trigger,
        turn_count=turn_count,
        heal_attempt=heal_attempt,
        pipeline_run_id=pipeline_run_id,
        pipeline_stage=pipeline_stage,
    )
