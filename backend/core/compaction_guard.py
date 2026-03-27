"""CompactionGuard — Two-phase anti-loop protection for autonomous sessions.

Prevents the compaction amnesia loop where:
  compaction → agent forgets work → re-runs same tools → more tokens → compaction → repeat

Two-phase design:
  PASSIVE (before compaction) — records tool calls and context usage for work summary
    generation without triggering any warnings or interruptions.
  ACTIVE (after compaction detected) — monitors for loop patterns using set-overlap
    detection and single-tool repetition, with graduated escalation.

Graduated escalation:
  MONITORING  — no action needed
  SOFT_WARN   — first detection: remind agent of completed work
  HARD_WARN   — second detection: instruct agent to summarize and stop
  KILL        — third detection: caller interrupts the streaming session

Loop detection uses four complementary strategies:
  0. Consecutive identical calls (all phases, no minimum): same (tool, hash) pair
     repeated 3/5/7 consecutive times → SOFT_WARN/HARD_WARN/KILL. Catches small
     loops (6-8 calls) at low context where other detectors are blind.
  1. Diversity stall (all phases): sliding window of last 20/40 calls,
     fires when unique (tool, input_hash) ratio drops below 30%.
     Catches real loops (same files/patterns repeating) but NOT normal
     code research (reading different files, grepping different patterns).
  2. Set-overlap (ACTIVE only): >60% of post-compaction calls match pre-compaction baseline
  3. Single-tool repetition (ACTIVE only): any (tool_name, input_hash) pair appears ≥5 times

Bash command normalization:
  Bash tool calls are normalized before hashing — ``| tail -N`` suffixes,
  ``2>&1`` redirections, ``cd path &&`` prefixes, and ``rm -f lock;``
  cleanup preambles are stripped. This ensures that cosmetically different
  but semantically identical commands (e.g. the pytest dead-loop pattern)
  are recognized as the same operation by all three detectors.

Public symbols:

- ``CompactionGuard``   — Per-session guard, created once per SessionUnit.
- ``GuardPhase``        — Enum: PASSIVE, ACTIVE.
- ``EscalationLevel``   — Enum: MONITORING, SOFT_WARN, HARD_WARN, KILL.
- ``ToolRecord``        — Dataclass: tool_name, input_hash, input_detail, timestamp.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────

class GuardPhase(Enum):
    """Operational mode of the guard."""
    PASSIVE = "passive"
    ACTIVE = "active"


class EscalationLevel(Enum):
    """Both the internal state and the return value from check().

    MONITORING means "no action needed".
    """
    MONITORING = "monitoring"
    SOFT_WARN = "soft_warn"
    HARD_WARN = "hard_warn"
    KILL = "kill"


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class ToolRecord:
    """A single recorded tool call with full input for work summary."""
    tool_name: str
    input_hash: str
    input_detail: str   # First 200 chars of JSON-serialized input
    timestamp: float = field(default_factory=time.time)


# ── Constants ─────────────────────────────────────────────────────

_CONTEXT_ACTIVATION_PCT = 85
"""Context usage % threshold that gates loop detection in ACTIVE phase."""

_OVERLAP_THRESHOLD = 0.60
"""Fraction of post-compaction calls matching baseline to trigger loop detection."""

_MIN_POST_COMPACTION_CALLS = 5
"""Minimum post-compaction calls before set-overlap detection activates."""

_SINGLE_TOOL_REPEAT_LIMIT = 5
"""A single (tool_name, input_hash) pair repeated this many times → loop."""

_COMPACTION_DROP_THRESHOLD = 30
"""Context % drop between consecutive updates that triggers heuristic compaction detection."""

_STALL_WINDOW: int = 20
"""Sliding window size for diversity-based stall detection."""

_STALL_DIVERSITY_PCT: float = 0.30
"""If unique calls / window < this ratio, the agent is stalling.

20 calls with <6 unique (tool, input_hash) pairs = stalling.
Example stall: Read A, Read A, Read A... (1 unique / 20 = 5%)
Example healthy: Read A, Grep B, Read C, Read D... (20 unique / 20 = 100%)
"""

_STALL_ESCALATION_WINDOW: int = 40
"""Larger window — if diversity still low at this count, escalate to HARD_WARN."""

_CONSEC_SOFT: int = 3
"""Consecutive identical (tool, hash) pairs to trigger SOFT_WARN."""

_CONSEC_HARD: int = 5
"""Consecutive identical (tool, hash) pairs to trigger HARD_WARN."""

_CONSEC_KILL: int = 7
"""Consecutive identical (tool, hash) pairs to trigger KILL."""


# ── Escalation ordering (for strict one-step progression) ────────

_ESCALATION_ORDER = [
    EscalationLevel.MONITORING,
    EscalationLevel.SOFT_WARN,
    EscalationLevel.HARD_WARN,
    EscalationLevel.KILL,
]


class CompactionGuard:
    """Per-session compaction amnesia loop guard.

    Two-phase design: PASSIVE (no interference) → ACTIVE (after compaction).
    Graduated escalation: MONITORING → SOFT_WARN → HARD_WARN → KILL.

    Thread-safety: NOT thread-safe. SessionUnit methods are already
    serialized by ``_lock``, so this is fine.
    """

    # ── Init & Properties ────────────────────────────────────────

    def __init__(self) -> None:
        # Phase
        self._phase: GuardPhase = GuardPhase.PASSIVE

        # Escalation
        self._escalation: EscalationLevel = EscalationLevel.MONITORING

        # Context tracking
        self._context_pct: float = 0.0
        self._context_tokens: int = 0
        self._prev_context_pct: float = 0.0  # For heuristic compaction detection
        self._context_window: int = 200_000

        # Tool tracking — sets and sequences
        self._pre_compaction_set: set[tuple[str, str]] = set()
        self._rolling_baseline_set: set[tuple[str, str]] = set()
        self._post_compaction_sequence: list[tuple[str, str]] = []

        # Full records for work summary (all phases)
        self._tool_records: list[ToolRecord] = []

        # Cached pattern description from last _detect_loop() call
        self._last_pattern_desc: str = ""

        # Diversity-based stall detection — sliding window of recent calls
        self._recent_calls: list[tuple[str, str]] = []

        # Consecutive identical call detection — fires at low call counts
        self._last_pair: tuple[str, str] | None = None
        self._consec_count: int = 0

    @property
    def phase(self) -> GuardPhase:
        """Current guard phase (PASSIVE or ACTIVE)."""
        return self._phase

    @property
    def escalation(self) -> EscalationLevel:
        """Current escalation level."""
        return self._escalation

    @property
    def context_pct(self) -> float:
        """Current context usage percentage."""
        return self._context_pct

    # ── Static helpers ──────────────────────────────────────────

    # Bash normalization patterns — compiled once, reused per call.
    # These strip cosmetic shell variations that make semantically
    # identical commands hash differently (the pytest dead-loop bug).
    _RE_OUTPUT_REDIRECT = re.compile(r"\s*2>&1\s*")
    _RE_PIPE_TAIL = re.compile(r"\s*\|\s*(?:tail|head)\s+[^\|;]*$")
    _RE_LEADING_CLEANUP = re.compile(
        r"^(?:rm\s+-f\s+\S+\s*(?:2>/dev/null)?\s*[;&]+\s*)+"
    )
    _RE_CD_PREFIX = re.compile(
        r"^cd\s+\S+\s*(?:&&|;)\s*"
    )
    _RE_MULTI_SPACE = re.compile(r"\s+")

    @staticmethod
    def _normalize_bash_command(cmd: str) -> str:
        """Normalize a Bash command string to a canonical form for hashing.

        Strips cosmetic variations that agents add between retries:
        - ``2>&1`` redirections
        - ``| tail -N`` / ``| head -N`` suffixes
        - Leading ``rm -f /path/to/lock;`` cleanup prefixes
        - Leading ``cd /some/path &&`` prefixes
        - Consecutive whitespace collapsed to single space

        Examples::

            'cd /foo && rm -f lock; python -m pytest tests/ -x -q 2>&1 | tail -30'
            → 'python -m pytest tests/ -x -q'

            'python -m pytest tests/ -x -q 2>&1 | tail -20'
            → 'python -m pytest tests/ -x -q'

        Both hash identically, so the guard sees them as the same operation.
        """
        s = cmd.strip()
        # Order matters: strip outer wrappers first, then inner decorators.
        # Tail/head suffix and redirections first (rightmost).
        s = CompactionGuard._RE_PIPE_TAIL.sub("", s)
        s = CompactionGuard._RE_OUTPUT_REDIRECT.sub(" ", s)
        # Left-side prefixes may be nested (cd ... && rm -f ...; cmd).
        # Loop until no more prefixes are stripped (max 5 to avoid infinite).
        for _ in range(5):
            prev = s
            s = CompactionGuard._RE_CD_PREFIX.sub("", s.strip())
            s = CompactionGuard._RE_LEADING_CLEANUP.sub("", s.strip())
            if s == prev:
                break
        s = CompactionGuard._RE_MULTI_SPACE.sub(" ", s).strip()
        return s

    @staticmethod
    def _hash_input(tool_input: dict | str | None) -> str:
        """Deterministic hash of tool input for deduplication.

        For Bash tool calls, normalizes the command string before hashing
        so that cosmetic variations (``| tail -N``, ``2>&1``, ``cd path &&``)
        produce the same hash. This prevents the pytest dead-loop where the
        agent re-runs the same command with different tail suffixes and the
        guard sees each as a unique call.

        For dicts: sorts keys and hashes the JSON.
        For strings: hashes directly.
        For None: returns a fixed hash.
        """
        if tool_input is None:
            return "none"
        if isinstance(tool_input, str):
            raw = tool_input
        else:
            try:
                # Bash tool normalization: {"command": "...", ...}
                # Normalize the command value before hashing the whole dict.
                if isinstance(tool_input, dict) and "command" in tool_input:
                    normalized = dict(tool_input)
                    normalized["command"] = CompactionGuard._normalize_bash_command(
                        str(normalized["command"])
                    )
                    # Drop description — it varies per call but doesn't
                    # change the semantic operation.
                    normalized.pop("description", None)
                    raw = json.dumps(normalized, sort_keys=True, default=str)
                else:
                    raw = json.dumps(tool_input, sort_keys=True, default=str)
            except (TypeError, ValueError):
                raw = str(tool_input)
        return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:12]

    def _compute_activation_pct(self, window: int) -> float:
        """Compute context activation threshold scaled to window size.

        Linear interpolation: 200K → 85%, 1M → 40%.
        For windows ≤ 200K, returns 85.0 (original behavior).
        For windows > 1M, clamps at 40.0.
        """
        if window <= 200_000:
            return 85.0
        ratio = min((window - 200_000) / 800_000, 1.0)
        return 85.0 - (ratio * 45.0)

    # ── record_tool_call() ───────────────────────────────────────

    def record_tool_call(
        self, tool_name: str, tool_input: dict | str | None
    ) -> None:
        """Record a tool call for loop detection and work summary.

        Hashes the input, creates a ToolRecord with the first 200 chars
        of JSON-serialized input as input_detail, appends to tracking
        structures.
        """
        try:
            input_hash = self._hash_input(tool_input)

            # Build input_detail: first 200 chars of JSON-serialized input
            try:
                if tool_input is None:
                    detail = ""
                elif isinstance(tool_input, str):
                    detail = tool_input[:200]
                else:
                    detail = json.dumps(tool_input, sort_keys=True, default=str)[:200]
            except Exception:
                detail = str(tool_input)[:200] if tool_input else ""

            record = ToolRecord(
                tool_name=tool_name,
                input_hash=input_hash,
                input_detail=detail,
            )

            pair = (tool_name, input_hash)

            # Append to post-compaction sequence (used for loop detection)
            self._post_compaction_sequence.append(pair)

            # Add to rolling baseline set (used for heuristic compaction detection)
            self._rolling_baseline_set.add(pair)

            # Append full record for work summary
            self._tool_records.append(record)

            # Consecutive identical call tracking
            if pair == self._last_pair:
                self._consec_count += 1
            else:
                self._last_pair = pair
                self._consec_count = 1

            # Sliding window for diversity-based stall detection
            self._recent_calls.append(pair)
            if len(self._recent_calls) > _STALL_ESCALATION_WINDOW:
                self._recent_calls = self._recent_calls[-_STALL_ESCALATION_WINDOW:]

        except Exception:
            logger.exception("compaction_guard.record_tool_call failed")

    # ── update_context_usage() ───────────────────────────────────

    def update_context_usage(
        self,
        input_tokens: int,
        model: Optional[str] = None,
    ) -> None:
        """Update context usage from SDK usage data.

        Computes context_pct, detects heuristic compaction (≥30pt drop),
        and auto-calls activate() if a drop is detected.

        Called after each ResultMessage with usage metrics.
        """
        try:
            if input_tokens <= 0:
                return

            try:
                from .prompt_builder import PromptBuilder
                window = PromptBuilder.get_model_context_window(model)
            except Exception:
                window = 200_000  # Safe fallback

            self._context_window = window
            new_pct = (input_tokens / window) * 100 if window > 0 else 0.0
            self._context_tokens = input_tokens
            self._context_pct = new_pct

            # Snapshot rolling baseline BEFORE checking for drop
            pre_drop_snapshot = set(self._rolling_baseline_set)

            # Heuristic compaction detection: ≥30pt drop
            if (
                self._prev_context_pct - new_pct
                >= _COMPACTION_DROP_THRESHOLD
            ):
                logger.info(
                    "compaction_guard.heuristic_compaction_detected "
                    "prev_pct=%.1f new_pct=%.1f drop=%.1f",
                    self._prev_context_pct,
                    new_pct,
                    self._prev_context_pct - new_pct,
                )
                # Use the pre-drop snapshot as the baseline
                self._rolling_baseline_set = pre_drop_snapshot
                self.activate()

            # Update prev after detection check
            self._prev_context_pct = new_pct

        except Exception:
            logger.exception("compaction_guard.update_context_usage failed")

    # ── activate() ───────────────────────────────────────────────

    def activate(self) -> None:
        """Transition PASSIVE → ACTIVE. Snapshot pre-compaction baseline.

        No-op if already ACTIVE (idempotent). Copies _rolling_baseline_set
        to _pre_compaction_set and clears _post_compaction_sequence.
        """
        try:
            if self._phase == GuardPhase.ACTIVE:
                logger.debug("compaction_guard.activate called but already ACTIVE")
                return

            self._phase = GuardPhase.ACTIVE
            self._pre_compaction_set = set(self._rolling_baseline_set)
            self._post_compaction_sequence = []

            logger.info(
                "compaction_guard.activated baseline_size=%d context_pct=%.1f",
                len(self._pre_compaction_set),
                self._context_pct,
            )
        except Exception:
            logger.exception("compaction_guard.activate failed")

    # ── _detect_loop() ───────────────────────────────────────────

    def _detect_loop(self) -> bool:
        """Detect loop via set-overlap and single-tool repetition.

        Returns True if a loop pattern is detected. Caches the detection
        reason in ``_last_pattern_desc`` for ``_build_pattern_description()``.
        """
        seq = self._post_compaction_sequence
        total = len(seq)
        self._last_pattern_desc = ""

        # Set-overlap detection
        if total >= _MIN_POST_COMPACTION_CALLS:
            overlap_count = sum(
                1 for pair in seq if pair in self._pre_compaction_set
            )
            if overlap_count / total > _OVERLAP_THRESHOLD:
                logger.warning(
                    "compaction_guard.loop_detected set_overlap "
                    "overlap=%d total=%d pct=%.1f%%",
                    overlap_count,
                    total,
                    (overlap_count / total) * 100,
                )
                self._last_pattern_desc = (
                    f"{overlap_count * 100 // total}% of post-compaction "
                    f"tool calls match pre-compaction baseline "
                    f"({overlap_count}/{total} calls)"
                )
                return True

        # Single-tool repetition detection
        if seq:
            counter = Counter(seq)
            most_common_pair, most_common_count = counter.most_common(1)[0]
            if most_common_count >= _SINGLE_TOOL_REPEAT_LIMIT:
                logger.warning(
                    "compaction_guard.loop_detected single_tool_repeat "
                    "tool=%s hash=%s count=%d",
                    most_common_pair[0],
                    most_common_pair[1],
                    most_common_count,
                )
                self._last_pattern_desc = (
                    f"Tool {most_common_pair[0]} called {most_common_count} times "
                    f"with identical input"
                )
                return True

        return False

    # ── _is_stalled() ────────────────────────────────────────────

    def _is_stalled(self) -> tuple[bool, int, int, int]:
        """Detect stall via low diversity in the sliding window of recent calls.

        A stall means the agent is repeating the same operations — reading
        the same files, grepping the same patterns. This is the BEHAVIORAL
        signal of a dead loop, not the tool classification signal.

        Returns (is_stalled, window_size, unique_count, total_calls).
        Two tiers:
        - _STALL_WINDOW (20) calls with <30% diversity → soft stall
        - _STALL_ESCALATION_WINDOW (40) calls with <30% diversity → hard stall
        """
        total = len(self._recent_calls)
        if total < _STALL_WINDOW:
            return False, 0, 0, total

        # Check the standard window first
        window = self._recent_calls[-_STALL_WINDOW:]
        unique = len(set(window))
        threshold = int(_STALL_WINDOW * _STALL_DIVERSITY_PCT)

        if unique < threshold:
            return True, _STALL_WINDOW, unique, total

        return False, _STALL_WINDOW, unique, total

    def _is_hard_stalled(self) -> bool:
        """Escalation-window stall: 40 calls with low diversity."""
        total = len(self._recent_calls)
        if total < _STALL_ESCALATION_WINDOW:
            return False
        window = self._recent_calls[-_STALL_ESCALATION_WINDOW:]
        unique = len(set(window))
        threshold = int(_STALL_ESCALATION_WINDOW * _STALL_DIVERSITY_PCT)
        return unique < threshold

    # ── _is_consecutive_repeat() ────────────────────────────────

    def _is_consecutive_repeat(self) -> EscalationLevel:
        """Detect consecutive identical tool calls — the low-context loop killer.

        Unlike diversity stall (needs 20+ calls) and set-overlap (needs
        compaction + high context), this fires on just 3 consecutive
        identical calls. Designed for the pytest dead-loop pattern where
        the agent re-runs the same command 6-8 times at <10% context.

        Returns the escalation level this detector alone would recommend.
        Does NOT mutate self._escalation — caller decides.

        Thresholds: 3 → SOFT_WARN, 5 → HARD_WARN, 7 → KILL.
        """
        if self._consec_count >= _CONSEC_KILL:
            return EscalationLevel.KILL
        if self._consec_count >= _CONSEC_HARD:
            return EscalationLevel.HARD_WARN
        if self._consec_count >= _CONSEC_SOFT:
            return EscalationLevel.SOFT_WARN
        return EscalationLevel.MONITORING

    # ── check() ──────────────────────────────────────────────────

    def check(self) -> EscalationLevel:
        """Check all layers and return the current escalation level.

        Detection strategy (priority order):

        0. Consecutive identical calls (all phases, no minimum window):
           - 3 consecutive identical (tool, hash) → SOFT_WARN
           - 5 consecutive → HARD_WARN
           - 7 consecutive → KILL
           Catches small loops (6-8 calls) at low context, no compaction needed.

        1. Diversity-based stall (all phases):
           - 20 calls with <30% diversity (< 6 unique) → SOFT_WARN
           - 40 calls with <30% diversity (< 12 unique) → HARD_WARN
           - PASSIVE phase caps at HARD_WARN (no KILL without compaction evidence)

        2. Context threshold + loop detection (ACTIVE only):
           - Set-overlap >60% matching baseline → escalate one step
           - Single-tool repetition ≥5 → escalate one step
           - Can reach KILL

        The dynamic threshold scales with context window size:
        200K → 85%, 1M → 40% (via _compute_activation_pct).

        Wraps in try/except — on error, returns MONITORING. Guard must
        never block streaming.
        """
        try:
            # Already at KILL: stay there
            if self._escalation == EscalationLevel.KILL:
                return EscalationLevel.KILL

            # ── Layer 0: Consecutive identical call detection (all phases) ──
            consec_level = self._is_consecutive_repeat()
            if consec_level != EscalationLevel.MONITORING:
                consec_idx = _ESCALATION_ORDER.index(consec_level)
                current_idx = _ESCALATION_ORDER.index(self._escalation)
                if consec_idx > current_idx:
                    tool_name = self._last_pair[0] if self._last_pair else "unknown"
                    logger.warning(
                        "compaction_guard.consecutive_repeat "
                        "tool=%s count=%d → %s",
                        tool_name,
                        self._consec_count,
                        consec_level.value,
                    )
                    self._escalation = consec_level
                    self._last_pattern_desc = (
                        f"Same {tool_name} command repeated "
                        f"{self._consec_count} consecutive times"
                    )
                    return consec_level

            # ── Layer 1: Diversity-based stall detection (all phases) ──
            stalled, window_sz, unique_ct, total_calls = self._is_stalled()
            if stalled:
                if self._is_hard_stalled():
                    # 40+ calls with low diversity — serious stall
                    if self._escalation in (
                        EscalationLevel.MONITORING,
                        EscalationLevel.SOFT_WARN,
                    ):
                        new_level = EscalationLevel.HARD_WARN
                        logger.warning(
                            "compaction_guard.diversity_stall "
                            "unique=%d/%d (%.0f%%) over %d calls → %s",
                            unique_ct, window_sz,
                            (unique_ct / window_sz * 100) if window_sz else 0,
                            total_calls, new_level.value,
                        )
                        self._escalation = new_level
                        self._last_pattern_desc = (
                            f"Low call diversity: {unique_ct} unique operations "
                            f"in last {_STALL_ESCALATION_WINDOW} calls — "
                            f"agent is repeating the same operations"
                        )
                        return new_level
                else:
                    # 20+ calls with low diversity — early warning
                    if self._escalation == EscalationLevel.MONITORING:
                        new_level = EscalationLevel.SOFT_WARN
                        logger.warning(
                            "compaction_guard.diversity_stall "
                            "unique=%d/%d (%.0f%%) over %d calls → %s",
                            unique_ct, window_sz,
                            (unique_ct / window_sz * 100) if window_sz else 0,
                            total_calls, new_level.value,
                        )
                        self._escalation = new_level
                        self._last_pattern_desc = (
                            f"Low call diversity: {unique_ct} unique operations "
                            f"in last {window_sz} calls — "
                            f"agent may be repeating operations"
                        )
                        return new_level

            # ── PASSIVE phase: diversity stall is the only detector ────
            if self._phase == GuardPhase.PASSIVE:
                return EscalationLevel.MONITORING

            # ── ACTIVE phase: context threshold + loop detection ───────
            activation_pct = self._compute_activation_pct(self._context_window)
            if self._context_pct < activation_pct:
                return EscalationLevel.MONITORING

            # ACTIVE + ctx ≥ threshold: run loop detection
            if self._detect_loop():
                # Escalate one step
                current_idx = _ESCALATION_ORDER.index(self._escalation)
                next_idx = min(current_idx + 1, len(_ESCALATION_ORDER) - 1)
                new_level = _ESCALATION_ORDER[next_idx]

                logger.warning(
                    "compaction_guard.escalation old=%s new=%s context_pct=%.1f",
                    self._escalation.value,
                    new_level.value,
                    self._context_pct,
                )
                self._escalation = new_level
                return new_level

            # No loop detected
            return EscalationLevel.MONITORING

        except Exception:
            logger.exception("compaction_guard.check failed")
            return EscalationLevel.MONITORING

    # ── work_summary() ───────────────────────────────────────────

    def work_summary(self) -> str:
        """Generate a structured work summary for post-compaction injection.

        Returns a human-readable summary of all tool calls recorded,
        grouped by tool name (sorted by count descending), with up to
        5 representative input_detail strings per group (truncated to
        200 chars). Includes "CRITICAL: Do NOT re-run" instructions.

        Returns empty string if no tool records exist.
        """
        try:
            if not self._tool_records:
                return ""

            # Group records by tool_name
            groups: dict[str, list[ToolRecord]] = defaultdict(list)
            for rec in self._tool_records:
                groups[rec.tool_name].append(rec)

            # Sort by count descending
            sorted_groups = sorted(
                groups.items(), key=lambda x: -len(x[1])
            )

            lines = [
                "[Post-Compaction Work Summary]",
                f"Tools executed ({len(self._tool_records)} total calls):",
                "",
            ]

            for tool_name, records in sorted_groups:
                count = len(records)
                lines.append(f"  {tool_name}: ×{count}")

                # Up to 5 representative input details
                try:
                    seen_details: list[str] = []
                    for rec in records:
                        detail = rec.input_detail[:200] if rec.input_detail else ""
                        if detail and len(seen_details) < 5:
                            seen_details.append(detail)
                    for detail in seen_details:
                        lines.append(f"    - {detail}")
                except Exception:
                    lines.append(f"    <{tool_name}(...)>")

                lines.append("")

            lines.extend([
                "CRITICAL: Do NOT re-run the tools listed above.",
                "If all work is complete, summarize results and STOP.",
                "If work remains, describe what's left and ask the user for instruction.",
                "DO NOT start new tool calls without user confirmation.",
            ])
            return "\n".join(lines)

        except Exception:
            logger.exception("compaction_guard.work_summary failed")
            return ""

    # ── build_guard_event() ──────────────────────────────────────

    def build_guard_event(self, level: EscalationLevel) -> dict | None:
        """Build SSE event dict for a given escalation level.

        Returns None for MONITORING (caller skips). For SOFT_WARN,
        HARD_WARN, KILL: returns dict with type="compaction_guard",
        subtype, context_pct, message, and pattern_description.
        """
        try:
            if level == EscalationLevel.MONITORING:
                return None

            # Build pattern description from detection state
            pattern_desc = self._build_pattern_description()

            messages = {
                EscalationLevel.SOFT_WARN: (
                    f"⚠️ Loop pattern detected at {self._context_pct:.0f}% context. "
                    f"Review completed work before continuing."
                ),
                EscalationLevel.HARD_WARN: (
                    f"🛑 Loop pattern persists at {self._context_pct:.0f}% context. "
                    f"Summarize your work and stop — do not start new tool calls."
                ),
                EscalationLevel.KILL: (
                    f"❌ Amnesia loop confirmed at {self._context_pct:.0f}% context. "
                    f"Session will be interrupted to prevent further resource waste."
                ),
            }

            return {
                "type": "compaction_guard",
                "subtype": level.value,
                "context_pct": round(self._context_pct, 1),
                "message": messages.get(level, "Guard event"),
                "pattern_description": pattern_desc,
            }

        except Exception:
            logger.exception("compaction_guard.build_guard_event failed")
            return None

    def _build_pattern_description(self) -> str:
        """Return the cached pattern description from the last _detect_loop() call."""
        return self._last_pattern_desc

    # ── reset() and reset_all() ──────────────────────────────────

    def reset(self) -> None:
        """Reset per-turn tracking for a new user message.

        Clears post-compaction sequence but preserves escalation level,
        phase, pre-compaction baseline, context_pct, and tool_records.
        """
        try:
            self._post_compaction_sequence = []
            self._last_pattern_desc = ""
        except Exception:
            logger.exception("compaction_guard.reset failed")

    def reset_all(self) -> None:
        """Full reset (subprocess respawn): back to PASSIVE, clear everything.

        Resets phase to PASSIVE, escalation to MONITORING, context to zero,
        and clears all tracking collections.
        """
        try:
            self._phase = GuardPhase.PASSIVE
            self._escalation = EscalationLevel.MONITORING
            self._context_pct = 0.0
            self._context_tokens = 0
            self._prev_context_pct = 0.0
            self._pre_compaction_set = set()
            self._rolling_baseline_set = set()
            self._post_compaction_sequence = []
            self._tool_records = []
            self._recent_calls = []
            self._context_window = 200_000
            self._last_pair = None
            self._consec_count = 0
        except Exception:
            logger.exception("compaction_guard.reset_all failed")

