"""CompactionGuard — Two-phase anti-loop protection for autonomous sessions.

Prevents the compaction amnesia loop where:
  compaction → agent forgets work → re-runs same tools → more tokens → compaction → repeat

Two-phase design:
  PASSIVE (before compaction) — records tool calls and context usage for work summary
    generation without triggering any warnings or interruptions.
  ACTIVE (after compaction detected) — monitors for loop patterns using set-overlap
    detection and single-tool repetition, with graduated escalation.

Graduated escalation (ACTIVE phase only, gated by 85% context threshold):
  MONITORING  — no action needed
  SOFT_WARN   — first detection: remind agent of completed work
  HARD_WARN   — second detection: instruct agent to summarize and stop
  KILL        — third detection: caller interrupts the streaming session

Loop detection uses two complementary strategies:
  1. Set-overlap: >60% of post-compaction calls match pre-compaction baseline (min 5 calls)
  2. Single-tool repetition: any (tool_name, input_hash) pair appears ≥5 times

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

PRODUCTIVE_TOOLS: set[str] = {"Edit", "Write", "MultiEdit", "Bash", "NotebookEdit"}
"""Tools that produce output — progress indicators."""

_NONPRODUCTIVE_SOFT_WARN: int = 15
"""Consecutive non-productive calls before SOFT_WARN."""

_NONPRODUCTIVE_HARD_WARN: int = 30
"""Consecutive non-productive calls before HARD_WARN."""


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

        # Progress tracking — productive vs non-productive
        self._consecutive_nonproductive: int = 0
        self._has_productive_call: bool = False

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

    # ── Static helper (preserved from original) ──────────────────

    @staticmethod
    def _hash_input(tool_input: dict | str | None) -> str:
        """Deterministic hash of tool input for deduplication.

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

            # Progress tracking — productive vs non-productive
            if tool_name in PRODUCTIVE_TOOLS:
                self._consecutive_nonproductive = 0
                self._has_productive_call = True
            else:
                self._consecutive_nonproductive += 1

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

    # ── check() ──────────────────────────────────────────────────

    def check(self) -> EscalationLevel:
        """Check all layers and return the current escalation level.

        PASSIVE → always MONITORING (no interference).
        ACTIVE + progress stall (15/30 non-productive) → SOFT_WARN/HARD_WARN.
        ACTIVE + ctx < dynamic threshold → MONITORING.
        ACTIVE + ctx ≥ dynamic threshold → run _detect_loop().
          Loop detected → escalate one step (MONITORING→SOFT_WARN→HARD_WARN→KILL).
          No loop → MONITORING.
        After KILL, subsequent calls continue returning KILL.

        The dynamic threshold scales with context window size:
        200K → 85%, 1M → 40% (via _compute_activation_pct).

        Wraps in try/except — on error, returns MONITORING. Guard must
        never block streaming.
        """
        try:
            # PASSIVE phase: no interference
            if self._phase == GuardPhase.PASSIVE:
                return EscalationLevel.MONITORING

            # Already at KILL: stay there
            if self._escalation == EscalationLevel.KILL:
                return EscalationLevel.KILL

            # Progress-based detection — fires regardless of context %
            # Only in ACTIVE phase (after compaction detected)
            if self._phase == GuardPhase.ACTIVE:
                progress_level = None
                if (
                    self._consecutive_nonproductive >= _NONPRODUCTIVE_HARD_WARN
                    and self._escalation.value in ("monitoring", "soft_warn")
                ):
                    progress_level = EscalationLevel.HARD_WARN
                elif (
                    self._consecutive_nonproductive >= _NONPRODUCTIVE_SOFT_WARN
                    and self._escalation == EscalationLevel.MONITORING
                ):
                    progress_level = EscalationLevel.SOFT_WARN

                if progress_level is not None:
                    logger.warning(
                        "compaction_guard.progress_escalation nonproductive=%d → %s",
                        self._consecutive_nonproductive, progress_level.value,
                    )
                    self._escalation = progress_level
                    self._last_pattern_desc = (
                        f"{self._consecutive_nonproductive} consecutive non-productive "
                        f"tool calls with zero Edit/Write/Bash"
                    )
                    return progress_level

            # ACTIVE but below context threshold: no detection
            activation_pct = self._compute_activation_pct(self._context_window)
            if self._context_pct < activation_pct:
                return EscalationLevel.MONITORING

            # ACTIVE + ctx ≥ 85%: run loop detection
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
            self._consecutive_nonproductive = 0
            self._has_productive_call = False
            self._context_window = 200_000
        except Exception:
            logger.exception("compaction_guard.reset_all failed")

