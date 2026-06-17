"""Session self-healing: invisible detection, checkpoint, and recovery.

This module provides the self-healing layer that keeps sessions alive without
user intervention. Three components:

- HealthSensor: Monitors per-turn health signals (latency, RSS, errors).
  Pure data, no side effects. Says "heal now" or "keep going".
- TaskCheckpoint: Captures everything needed to continue a task after refresh.
  Immutable snapshot of task progress.
- HealingLoop: Orchestrates the heal cycle (checkpoint → kill → respawn → continue).
  Calls existing SessionUnit methods, no new process management.

Design principle: User sees nothing. System heals itself. Task completes.
The only user-visible interruptions are explicit approval gates.

Key invariants:
- Max 3 heal attempts per trigger (prevents infinite loops)
- 60s cooldown between heal cycles (prevents thrash)
- HealthSensor is read-only — detection separated from action
- TaskCheckpoint is immutable once created
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

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

# Hang: seconds without any SSE event before declaring hang
HANG_TIMEOUT_S = 90

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

    def should_checkpoint(self) -> tuple[bool, str]:
        """Evaluate whether healing is needed.

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
