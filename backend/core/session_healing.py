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
    except Exception as exc:  # noqa: BLE001
        # Never crash on monitoring failure — but 0 MB is a load-bearing lie for RSS:
        # it reads as a process using no memory, so a failing reader degrades to
        # "healthy" rather than "unknown" and any RSS-driven healing stops firing.
        logger.warning("RSS probe failed for pid=%s, reporting 0 MB: %s",
                       target_pid, exc)
        return 0

logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────

# RSS: if growth over last N turns exceeds this (MB), trigger
RSS_GROWTH_THRESHOLD_MB = 400
RSS_WINDOW = 10  # turns to measure growth

# Errors: consecutive tool/inference errors before trigger
ERROR_CASCADE_THRESHOLD = 3

# Turn limit: trigger heal this many turns before max_turns
TURN_APPROACH_BUFFER = 20

# Hard graceful floor (Root 2 / AC3, G2): an absolute last-resort stop this many
# turns before max_turns. Safety net for when self-heal cannot carry the wrap-up.
# While self-heal is SUCCEEDING, turn_approaching (-20) heals + resets turn_count
# before -5 is reached, so the floor stays dormant on that path. It still fires
# when self-heal is OFF, has exhausted its attempts, or is in cooldown — delivering
# a graceful wrap-up instead of a silent run to the CLI's hard error_max_turns
# (truncation). On the self-heal-ON-but-failing path the heal consumer still kills
# + --resumes with a rich checkpoint; the floor's added value is the OFF path.
# MUST be < TURN_APPROACH_BUFFER (closer to the limit than the graceful trigger).
HARD_FLOOR_BUFFER = 5

# Per-platform CLI turn ceilings. Single source of truth within the healing
# module so the HealthSensor threshold can never drift from the real limit the
# CLI enforces (prompt_builder applies the SAME values: desktop 500, channel 100).
# Desktop = generous (long pipeline runs); channel = unattended safety cap.
# These MUST match prompt_builder.py's platform defaults — if you change one,
# change both. (Root cause of the original bug: HealthSensor hardcoded 500 while
# the CLI actually ran at 100, making turn_approaching structurally unreachable.)
DESKTOP_MAX_TURNS = 500
CHANNEL_MAX_TURNS = 100

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

    # Time cap for young session immunity (PE HIGH-2): even with turn_count<3,
    # immunity expires after 3 minutes so permanently-stuck sessions can be healed.
    _YOUNG_IMMUNITY_MAX_AGE_S: float = 180.0

    def __init__(self, max_turns: int | None = DESKTOP_MAX_TURNS):
        self._rss_samples: deque[int] = deque(maxlen=RSS_WINDOW)
        self._consecutive_errors: int = 0
        self._turn_count: int = 0
        self._max_turns: int = max_turns if max_turns is not None else DESKTOP_MAX_TURNS
        self._last_activity_time: float = time.time()
        self._created_at: float = time.time()

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def record_turn(
        self, latency_ms: float, rss_mb: int, had_error: bool
    ) -> None:
        """Record one turn's health signals.

        ``latency_ms`` is accepted for caller compatibility
        (streaming_orchestrator.py passes it) but is no longer stored — the
        latency_degradation signal that consumed it was removed (run_099724ca).
        """
        del latency_ms  # signal removed; param kept for caller compat
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

    def set_max_turns(self, max_turns: int) -> None:
        """Re-point the turn-limit threshold after construction.

        Needed because ``is_channel_session`` is set on the SessionUnit AFTER
        ``__init__`` (by SessionRouter on the first real send), so the sensor is
        born with the desktop default and must be synced to the channel ceiling
        once the session is tagged. ``_max_turns`` is the single shared source for
        BOTH ``turn_approaching`` (here, :max_turns-TURN_APPROACH_BUFFER) and the
        channel wrap-up (session_unit.py, :max_turns-CHANNEL_WRAP_BUFFER), so this
        one setter keeps both consumers correct.
        """
        self._max_turns = max_turns

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
        # ── Scoped immunity for young sessions (PE F6, Design §3A) ──────
        # Sessions with <3 turns haven't invested enough for latency/memory/turn
        # healing to provide value. BUT hang_detected and error_cascade MUST still
        # fire — a genuinely stuck subprocess needs recovery regardless of age.
        # PE HIGH-2: Time cap ensures permanently-stuck sessions (e.g., WAITING_INPUT
        # that never gets a turn) don't stay immune forever.
        _young = (
            self._turn_count < 3
            and (time.time() - self._created_at) < self._YOUNG_IMMUNITY_MAX_AGE_S
        )

        # Signal 1 (latency_degradation) was REMOVED (run_099724ca): it force-
        # killed healthy IDLE sessions between turns on RELATIVE completed-turn
        # latency (recent-5 > 2.5x opening-10), with no absolute floor and no
        # resource co-signal. The kill->--resume response made latency WORSE
        # (replays full context, 2x multiplier), and every real cause of rising
        # latency has a correct owner elsewhere: context-bloat -> soft-compact
        # (session_unit._check_context_soft_compact, no kill), memory -> RSS
        # proactive restart (_check_rss_and_proactive_restart) + Signal 2 below,
        # legitimately-heavier work -> no action needed. A live+SSE-emitting
        # slow turn is not a hang; hang_detected (Signal 5) + the turn floors
        # are the real safety nets.

        # Signal 2: Memory growth (RSS climbing)
        if not _young and len(self._rss_samples) >= RSS_WINDOW:
            growth = self._rss_samples[-1] - self._rss_samples[0]
            if growth > RSS_GROWTH_THRESHOLD_MB:
                return True, "memory_growth"

        # Signal 3: Error cascade — NOT immune (PE F6: genuine errors need recovery)
        if self._consecutive_errors >= ERROR_CASCADE_THRESHOLD:
            return True, "error_cascade"

        # Signal 4a: Hard graceful floor (Root 2 / AC3) — checked BEFORE
        # turn_approaching so the more-urgent floor wins when both apply.
        # At max_turns-5 we are past the graceful window; this is the absolute
        # last-resort stop. Primary value on the self-heal-OFF path (where
        # turn_approaching never healed): a graceful wrap-up with a preserved
        # conclusion instead of a silent run to CLI error_max_turns.
        if not _young and self._turn_count >= (self._max_turns - HARD_FLOOR_BUFFER):
            return True, "turn_hard_floor"

        # Signal 4: Turn limit approaching
        if not _young and self._turn_count >= (self._max_turns - TURN_APPROACH_BUFFER):
            return True, "turn_approaching"

        # Signal 5: Hang detection — NOT immune (PE F6: stuck subprocess needs kill)
        # WHITELIST: hang_detected ONLY fires for IDLE and COLD states.
        # All other states have dedicated liveness mechanisms:
        # - STREAMING: PID watchdog + MESSAGE_TIMEOUT + circuit breaker
        # - WAITING_INPUT: user takes arbitrary time for permission prompts
        # - DEAD: already dead, nothing to detect
        # - None (no state passed): backward compat — allow detection
        # Whitelist is future-proof: adding new states won't accidentally
        # trigger hang detection (blacklist would require updating on every
        # new state addition).
        if session_state in ("idle", "cold", None):
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
        # Observability: per-trigger breakdown for monitoring false positive rate
        self._trigger_counts: dict[str, int] = {}
        self._last_triggers: deque[tuple[float, str]] = deque(maxlen=20)

    @property
    def heal_attempts(self) -> int:
        return self._heal_attempts

    @property
    def total_heals(self) -> int:
        return self._total_heals

    @property
    def trigger_counts(self) -> dict[str, int]:
        """Per-trigger-type count for observability dashboard."""
        return dict(self._trigger_counts)

    @property
    def recent_triggers(self) -> list[tuple[float, str]]:
        """Last 20 heal triggers (timestamp, trigger_name) for rate analysis."""
        return list(self._last_triggers)

    def can_heal(self) -> tuple[bool, str]:
        """Check if healing is allowed (attempts + cooldown)."""
        if self._heal_attempts >= MAX_HEAL_ATTEMPTS:
            return False, "max_attempts_exhausted"

        elapsed = time.time() - self._last_heal_time
        if elapsed < HEAL_COOLDOWN_S and self._last_heal_time > 0:
            return False, f"cooldown_active ({HEAL_COOLDOWN_S - elapsed:.0f}s remaining)"

        return True, ""

    def record_heal_start(self, trigger: str = "") -> None:
        """Record that a heal cycle is starting.

        Args:
            trigger: The trigger name (e.g. "hang_detected", "memory_growth").
                Used for observability — tracks per-trigger frequency to detect
                false positive patterns.
        """
        self._heal_attempts += 1
        self._last_heal_time = time.time()
        self._total_heals += 1
        # Observability: track per-trigger breakdown
        if trigger:
            self._trigger_counts[trigger] = self._trigger_counts.get(trigger, 0) + 1
            self._last_triggers.append((time.time(), trigger))
        logger.info(
            "self_heal.start attempt=%d/%d total_heals=%d trigger=%s "
            "trigger_history=%s",
            self._heal_attempts,
            MAX_HEAL_ATTEMPTS,
            self._total_heals,
            trigger or "unknown",
            {k: v for k, v in self._trigger_counts.items()},
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


# ─── RecoveryCoordinator (R3) ────────────────────────────────────────────────
# Single recovery DECISION authority. The hang-class audit (run_d73c3e9a) found
# 8 kill paths each owning their own breaker and deciding independently. This is
# the one brain they will all eventually route through. R3 migrates the first,
# highest-frequency trigger (self-heal) — the other 7 follow one per run (R3a–g).
#
# Strangler-fig (STEERING #4): this DELEGATES to a HealingLoop it holds; it does
# NOT replace it. HealingLoop is unchanged so its 5 test files stay green. The
# Coordinator owns the DECISION (may-we-recover + what-kind + escalation); the
# kill MECHANICS stay in SessionUnit (unified later in R4 RecoveryTransaction).

from enum import Enum


class RecoveryVerdict(Enum):
    """What the Coordinator decided about a recovery request.

    Seven verdicts cover the four decision shapes across all 8 kill paths
    (validated R3a). The original five (R3) are unchanged; the two additions are
    the verdicts the un-migrated triggers will need — declared now so R3b–g add
    policies, not re-touch this enum (additive, zero risk to shipped self-heal).
    """
    SKIP = "skip"                      # guard failed (disabled / user-stopped / protected state)
    DEFER = "defer"                    # allowed eventually, but cooling down — try later
    PROCEED_GRACEFUL = "proceed_graceful"  # heal, but inject wrap-up first (two-phase)
    PROCEED_KILL = "proceed_kill"      # heal now (kill → COLD → --resume PRESERVED)
    ESCALATE = "escalate"              # breaker tripped — recovery itself is failing
    # ── R3a additions (not yet emitted by a migrated trigger; R3b–g use them) ──
    PROCEED_INTERRUPT = "proceed_interrupt"  # warm, non-destructive (tool-hang tier 1)
    PROCEED_KILL_HARD = "proceed_kill_hard"  # kill + DROP --resume identity
    #                                          (streaming-timeout circuit-break, OOM limit).
    #   The PROCEED_KILL vs PROCEED_KILL_HARD split is the single most
    #   safety-relevant distinction in recovery: keep vs drop conversation context.


@dataclass
class RecoveryDecision:
    verdict: RecoveryVerdict
    reason: str = ""


@dataclass
class RecoveryContext:
    """Inputs a policy needs to decide. Superset across shapes — a given policy
    reads only the fields its shape uses (attempt-breaker ignores now/last_recovery;
    cooldown-threshold ignores graceful_pending). Decision-in-coordinator,
    state-in-caller: the caller passes timestamps it owns; the policy is stateless
    w.r.t. cooldown (it reads now/last_recovery, never writes them)."""
    trigger: str
    enabled: bool
    user_stopped: bool
    state: str
    graceful_pending: bool = False
    now: float = 0.0
    last_recovery: float = 0.0
    cooldown_s: float = 0.0
    # GracefulEscalation ladder inputs (R3d/R3e): caller owns the attempt
    # counter + the escalation threshold; the policy reads them to pick
    # base-vs-escalated verdict. Ignored by the other three shapes.
    attempt: int = 0
    threshold: int = 0


# Triggers that get the graceful two-phase wrap-up (subprocess still healthy,
# turn buffer exists). Everything else heals immediately.
_GRACEFUL_TRIGGERS = frozenset({"turn_approaching"})


def _universal_guard(ctx: "RecoveryContext") -> "RecoveryDecision | None":
    """The ONLY truly cross-policy guards: never recover if self-heal is disabled
    or the user stopped this turn. Protected-STATES are NOT here — they are
    policy-specific (self-heal protects WAITING_INPUT; stuck-WAITING TARGETS it),
    so each policy declares its own state eligibility. Returns a SKIP decision to
    short-circuit, or None to let the policy decide."""
    if not ctx.enabled:
        # Reason kept verbatim from R3 ("self_heal_disabled") for strict parity —
        # though no current consumer reads decision.reason on the SKIP path.
        return RecoveryDecision(RecoveryVerdict.SKIP, "self_heal_disabled")
    if ctx.user_stopped:
        return RecoveryDecision(RecoveryVerdict.SKIP, "user_stopped_current_turn")
    return None


class RecoveryPolicy:
    """A recovery decision shape. decide(ctx) -> RecoveryDecision.

    Each policy owns its gate (attempt-breaker / cooldown-threshold / bare-threshold
    / graceful-escalation) AND its state eligibility. The Coordinator dispatches to
    the trigger's policy; the universal guard (enabled/user_stopped) is applied by
    the Coordinator before dispatch. Four shapes were validated against all 8 kill
    paths (R3a); R3a implements two, R3b–g add the other two."""

    def decide(self, ctx: "RecoveryContext") -> "RecoveryDecision":  # pragma: no cover - interface
        raise NotImplementedError


_ATTEMPT_BREAKER_PROTECTED_STATES = frozenset({"waiting_input"})


class AttemptBreakerPolicy(RecoveryPolicy):
    """Self-heal's shape: attempt-breaker (max N + cooldown) + escalate-to-user.
    A PURE EXTRACT of the R3 decide() logic — same HealingLoop calls, same order,
    same verdicts. Protects WAITING_INPUT (user is mid-answer). Holds the breaker
    + the one-shot terminal signal (moved here verbatim from the Coordinator)."""

    def __init__(self, healing_loop: "HealingLoop"):
        self._loop = healing_loop
        self._terminal_reached: bool = False
        self._terminal_signal_count: int = 0

    @property
    def terminal_reached(self) -> bool:
        return self._terminal_reached

    @property
    def terminal_signal_count(self) -> int:
        return self._terminal_signal_count

    def on_success(self) -> None:
        self._terminal_reached = False

    def decide(self, ctx: "RecoveryContext") -> "RecoveryDecision":
        guard = _universal_guard(ctx)
        if guard is not None:
            return guard
        # Policy-specific state guard (NOT a coordinator constant).
        if ctx.state in _ATTEMPT_BREAKER_PROTECTED_STATES:
            return RecoveryDecision(RecoveryVerdict.SKIP, f"protected_state:{ctx.state}")

        can_heal, reason = self._loop.can_heal()
        if not can_heal:
            if self._loop.should_escalate():
                if not self._terminal_reached:
                    self._terminal_reached = True
                    self._terminal_signal_count += 1
                    logger.warning(
                        "recovery_coordinator.terminal_reached trigger=%s "
                        "attempts=%d/%d — recovery exhausted, escalate to user",
                        ctx.trigger, self._loop.heal_attempts, MAX_HEAL_ATTEMPTS,
                    )
                return RecoveryDecision(RecoveryVerdict.ESCALATE, reason)
            return RecoveryDecision(RecoveryVerdict.DEFER, reason)

        if ctx.trigger in _GRACEFUL_TRIGGERS and not ctx.graceful_pending:
            return RecoveryDecision(RecoveryVerdict.PROCEED_GRACEFUL, "graceful_phase_1")
        return RecoveryDecision(RecoveryVerdict.PROCEED_KILL, "")


class CooldownThresholdPolicy(RecoveryPolicy):
    """RSS-proactive's shape: cooldown-gated threshold. No attempt-breaker, no
    escalation, no graceful — within cooldown → DEFER, past cooldown → PROCEED_KILL.
    Stateless w.r.t. the cooldown timestamp: reads ctx.now/ctx.last_recovery, the
    caller owns + writes the timestamp. Does NOT impose a protected-state set (RSS
    fires in IDLE; the threshold check that gates it lives in the caller)."""

    def __init__(self, cooldown_s: float = 0.0):
        # Default cooldown for callers that construct with a fixed value + pass a
        # context without cooldown_s. The context value wins when provided (>0),
        # keeping the policy stateless across differing-cooldown callers.
        self._default_cooldown_s = cooldown_s

    def decide(self, ctx: "RecoveryContext") -> "RecoveryDecision":
        guard = _universal_guard(ctx)
        if guard is not None:
            return guard
        cooldown_s = ctx.cooldown_s if ctx.cooldown_s > 0 else self._default_cooldown_s
        elapsed = ctx.now - ctx.last_recovery
        if elapsed < cooldown_s:
            return RecoveryDecision(
                RecoveryVerdict.DEFER,
                f"cooldown active ({cooldown_s - elapsed:.0f}s remaining)",
            )
        return RecoveryDecision(RecoveryVerdict.PROCEED_KILL, "")


class BareThresholdPolicy(RecoveryPolicy):
    """RSS-streaming (#3-T1) and stuck-WAITING (#7) shape: bare threshold.
    No cooldown, no attempt-breaker, no escalation, no graceful. The CALLER
    owns the threshold measurement (RSS>7GB / waited>timeout) and only invokes
    the policy once the breach is already established; the policy answers the
    narrow question "given the breach, may I kill in THIS state?".

    State eligibility is policy-configured, NOT a coordinator constant — this is
    the mechanism that lets RSS-streaming TARGET ``streaming`` while stuck-WAITING
    TARGETS ``waiting_input`` (the exact opposite of self-heal, which PROTECTS it).
    Default ``eligible_states=None`` → eligible in any non-guarded state.
    Stateless: holds no breaker/timestamp, so one instance is reusable across
    callers with differing thresholds."""

    def __init__(self, eligible_states: "frozenset[str] | None" = None):
        self._eligible_states = eligible_states

    def decide(self, ctx: "RecoveryContext") -> "RecoveryDecision":
        guard = _universal_guard(ctx)
        if guard is not None:
            return guard
        if self._eligible_states is not None and ctx.state not in self._eligible_states:
            return RecoveryDecision(
                RecoveryVerdict.SKIP, f"ineligible_state:{ctx.state}"
            )
        return RecoveryDecision(RecoveryVerdict.PROCEED_KILL, "")


class GracefulEscalationPolicy(RecoveryPolicy):
    """streaming-timeout (#4) and tool-hang (#6) shape: escalating ladder.
    A two-tier verdict: ``attempt <= threshold`` → ``base`` (the gentle action),
    ``attempt > threshold`` → ``escalated`` (the destructive action). The CALLER
    owns the attempt counter + threshold (it already tracks them as circuit
    breakers today); the policy owns only which rung of the ladder applies.

    The base/escalated verdicts are injected, NOT hardcoded — this is the
    truly-universal escalation shape, while the SPECIFIC verdicts differ per
    trigger (PIT06: share the shape, dispatch the difference):
      - M3 streaming-timeout: base=PROCEED_KILL (keep --resume),
        escalated=PROCEED_KILL_HARD (drop identity, break the resume-loop).
      - M4 tool-hang: base=PROCEED_INTERRUPT (warm, non-destructive),
        escalated=PROCEED_KILL (force kill the hung subprocess).

    Stateless: holds only its two verdict constants; the counter lives in the
    caller, so one instance is safe to construct per call."""

    def __init__(self, *, base: "RecoveryVerdict", escalated: "RecoveryVerdict"):
        self._base = base
        self._escalated = escalated

    def decide(self, ctx: "RecoveryContext") -> "RecoveryDecision":
        guard = _universal_guard(ctx)
        if guard is not None:
            return guard
        if ctx.attempt > ctx.threshold:
            return RecoveryDecision(
                self._escalated, f"escalated (attempt {ctx.attempt} > {ctx.threshold})"
            )
        return RecoveryDecision(
            self._base, f"base (attempt {ctx.attempt} <= {ctx.threshold})"
        )


class RecoveryCoordinator:
    """Thin decision authority over recovery. Delegates breaker state to a
    HealingLoop (injected, not created) — so existing HealingLoop tests are
    untouched and there is exactly ONE breaker per session, never two.

    decide() answers "may I recover, and what kind?"; SessionUnit still performs
    the checkpoint + kill (mechanics). record_*() passthroughs keep the single
    breaker authoritative regardless of who calls them.
    """

    def __init__(self, healing_loop: "HealingLoop"):
        self._loop = healing_loop
        # R3a: the self-heal decision now lives in a policy (per-trigger dispatch).
        # AttemptBreakerPolicy is a pure extract of the R3 decide() logic + owns
        # the one-shot terminal signal. The Coordinator stays the single authority
        # and keeps the SAME public API (decide / terminal_* / record_* / heal_attempts).
        self._attempt_policy = AttemptBreakerPolicy(healing_loop)
        # RSS-proactive shape (R3a). Cooldown value is passed per-call (the unit
        # owns PROACTIVE_COOLDOWN), so this policy is reusable/stateless.
        self._cooldown_policy = CooldownThresholdPolicy(cooldown_s=0.0)

    # ── observability (decision #3 backend half — UI-ready, no SSE yet) ──
    @property
    def terminal_recovery_reached(self) -> bool:
        """True once the breaker has tripped (recovery itself is failing).
        R4 maps this to a user-facing inline `recovery_exhausted` SSE event."""
        return self._attempt_policy.terminal_reached

    @property
    def terminal_signal_count(self) -> int:
        """How many times the terminal signal fired (must be exactly 1 per
        exhaustion episode — never spam the user)."""
        return self._attempt_policy.terminal_signal_count

    # ── the decision (self-heal — dispatches to the attempt-breaker policy) ──
    def decide(
        self,
        trigger: str,
        *,
        enabled: bool,
        user_stopped: bool,
        state: str,
        graceful_pending: bool,
    ) -> RecoveryDecision:
        """Self-heal recovery decision. Unchanged public API (R3). Now dispatches
        to AttemptBreakerPolicy — behavior is identical (pure extract)."""
        ctx = RecoveryContext(
            trigger=trigger, enabled=enabled, user_stopped=user_stopped,
            state=state, graceful_pending=graceful_pending,
        )
        return self._attempt_policy.decide(ctx)

    # ── RSS-proactive decision (R3a — dispatches to the cooldown policy) ──
    def decide_rss(
        self,
        *,
        now: float,
        last_recovery: float,
        cooldown_s: float,
        enabled: bool,
        user_stopped: bool,
        state: str,
    ) -> RecoveryDecision:
        """RSS-proactive recovery decision: cooldown-gated. The caller still owns
        the RSS threshold measurement + the cooldown timestamp; the Coordinator
        owns only the cooldown DECISION (within → DEFER, past → PROCEED_KILL).
        No attempt-breaker, no escalation imposed — RSS never had them.
        cooldown passed via context (stateless policy — no per-call mutation)."""
        ctx = RecoveryContext(
            trigger="rss_proactive", enabled=enabled, user_stopped=user_stopped,
            state=state, now=now, last_recovery=last_recovery, cooldown_s=cooldown_s,
        )
        return self._cooldown_policy.decide(ctx)

    # ── bare-threshold decision (R3b/M1 + M2 — dispatches to the bare policy) ──
    def decide_bare(
        self,
        *,
        trigger: str,
        enabled: bool,
        user_stopped: bool,
        state: str,
        eligible_states: "frozenset[str] | None" = None,
    ) -> RecoveryDecision:
        """Bare-threshold recovery decision: no cooldown, no breaker, no
        escalation. The CALLER owns the threshold measurement (RSS>7GB for
        RSS-streaming #3-T1, waited>timeout for stuck-WAITING #7); the
        Coordinator owns only the may-I-kill-in-this-state verdict.

        ``eligible_states`` lets the caller TARGET a state (M2 stuck-WAITING
        passes ``{"waiting_input"}``); None = any non-guarded state (M1
        RSS-streaming, gated upstream by the STREAMING-only unit list)."""
        ctx = RecoveryContext(
            trigger=trigger, enabled=enabled, user_stopped=user_stopped,
            state=state,
        )
        return BareThresholdPolicy(eligible_states=eligible_states).decide(ctx)

    # ── graceful-escalation decision (R3d/M3 + R3e/M4) ──
    def decide_graceful(
        self,
        *,
        trigger: str,
        enabled: bool,
        user_stopped: bool,
        state: str,
        attempt: int,
        threshold: int,
        base: RecoveryVerdict,
        escalated: RecoveryVerdict,
    ) -> RecoveryDecision:
        """Escalating-ladder recovery decision. The CALLER owns the attempt
        counter + threshold (its existing circuit breaker); the Coordinator owns
        the base-vs-escalated verdict. base/escalated are injected so the SAME
        shape serves M3 (KILL→KILL_HARD) and M4 (INTERRUPT→KILL) — PIT06: share
        the shape, dispatch the trigger-specific verdicts."""
        ctx = RecoveryContext(
            trigger=trigger, enabled=enabled, user_stopped=user_stopped,
            state=state, attempt=attempt, threshold=threshold,
        )
        return GracefulEscalationPolicy(base=base, escalated=escalated).decide(ctx)

    # ── breaker lifecycle passthroughs (delegate to the ONE held loop) ──
    def record_heal_start(self, trigger: str = "") -> None:
        self._loop.record_heal_start(trigger=trigger)

    def record_heal_success(self) -> None:
        self._loop.record_heal_success()
        self._attempt_policy.on_success()  # fresh budget — clear terminal state

    def record_heal_failure(self, reason: str) -> None:
        self._loop.record_heal_failure(reason)

    @property
    def heal_attempts(self) -> int:
        return self._loop.heal_attempts


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
