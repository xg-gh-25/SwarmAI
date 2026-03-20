"""CompactionGuard — 3-layer anti-loop protection for autonomous sessions.

Prevents the compaction amnesia loop where:
  compaction → agent forgets work → re-runs same tools → more tokens → compaction → repeat

Three layers, each catching what the previous misses:

Layer 1 — Context-Aware Throttle (proactive):
    At 70% context usage: yields a throttle_warning SSE event.
    At 85%: yields throttle_hard_stop event; caller should interrupt.
    Prevents compaction from happening in the first place.

Layer 2 — Circuit Breaker (reactive):
    Tracks (tool_name, hash(tool_input)) during streaming.
    3 exact repeats → loop detected.
    8 same-tool calls (any input) → loop detected, unless whitelisted.
    Yields loop_warning SSE event; caller should interrupt.

Layer 3 — Post-Compaction Recovery:
    Captures tool call history as a structured work summary.
    Injected via /compact instructions so the agent knows what it already did.

Public symbols:

- ``CompactionGuard``  — Per-session guard, created once per SessionUnit.
- ``LoopAction``       — Enum: NONE, WARN, HARD_STOP.

Usage in SessionUnit:
    guard = CompactionGuard()
    guard.record_tool_call(tool_name, tool_input)
    guard.update_context_usage(input_tokens, model)
    action = guard.check()  # returns LoopAction
    summary = guard.work_summary()  # for /compact instructions
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


class LoopAction(Enum):
    """Action to take based on guard checks."""
    NONE = "none"               # All clear
    THROTTLE_WARN = "warn"      # Context at 70%+ — warn agent
    THROTTLE_STOP = "stop"      # Context at 85%+ — hard stop
    LOOP_DETECTED = "loop"      # Circuit breaker triggered


# Tools that legitimately repeat with different inputs.
# Subject to exact-repeat detection but exempt from same-tool-name threshold.
_REPEAT_WHITELISTED_TOOLS = frozenset({
    "Read", "Glob", "Grep", "WebFetch", "Agent",
    "ListMcpResourcesTool", "ReadMcpResourceTool",
})

# Thresholds
_EXACT_REPEAT_LIMIT = 3   # Same (tool, input_hash) × 3 → loop
_TOOL_NAME_LIMIT = 8      # Same tool_name × 8 (any inputs) → loop
_CONTEXT_WARN_PCT = 70
_CONTEXT_STOP_PCT = 85


@dataclass
class ToolCall:
    """A single recorded tool call."""
    tool_name: str
    input_hash: str
    timestamp: float = field(default_factory=time.time)


class CompactionGuard:
    """Per-session 3-layer anti-loop guard.

    Create one per SessionUnit. Reset on new user message via ``reset()``.
    The guard tracks tool calls within a single agent turn (between user
    messages). A "turn" starts when the user sends a message and ends
    when the agent produces a ResultMessage or the session is interrupted.

    Thread-safety: NOT thread-safe. SessionUnit methods are already
    serialized by ``_lock``, so this is fine.
    """

    def __init__(self) -> None:
        self._tool_calls: list[ToolCall] = []
        self._exact_counts: Counter = Counter()  # (tool_name, input_hash) → count
        self._name_counts: Counter = Counter()    # tool_name → count
        self._context_pct: float = 0.0
        self._context_tokens: int = 0
        self._warned_throttle: bool = False  # Only warn once per turn
        self._warned_stop: bool = False      # Only hard-stop once per turn
        self._warned_loop: bool = False      # Only fire circuit breaker once per turn

    # ── Layer 1: Context Tracking ─────────────────────────────────

    def update_context_usage(
        self,
        input_tokens: int,
        model: Optional[str] = None,
    ) -> None:
        """Update context usage from SDK usage data.

        Called after each ResultMessage with usage metrics.
        """
        if input_tokens <= 0:
            return
        try:
            from .prompt_builder import PromptBuilder
            window = PromptBuilder.get_model_context_window(model)
        except Exception:
            window = 200_000  # Safe fallback
        self._context_tokens = input_tokens
        self._context_pct = (input_tokens / window) * 100 if window > 0 else 0

    @property
    def context_pct(self) -> float:
        """Current context usage percentage."""
        return self._context_pct

    # ── Layer 2: Tool Call Tracking ───────────────────────────────

    def record_tool_call(self, tool_name: str, tool_input: dict | str | None) -> None:
        """Record a tool call for circuit breaker tracking.

        Args:
            tool_name: The tool name (e.g., "Bash", "Read", "Edit").
            tool_input: The tool input (dict from SDK, or string).
        """
        input_hash = self._hash_input(tool_input)
        call = ToolCall(tool_name=tool_name, input_hash=input_hash)
        self._tool_calls.append(call)
        self._exact_counts[(tool_name, input_hash)] += 1
        self._name_counts[tool_name] += 1

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

    # ── Combined Check ────────────────────────────────────────────

    def check(self) -> LoopAction:
        """Check all layers and return the highest-priority action.

        Call this after each tool call or result message.
        Returns the most urgent action needed.
        """
        # Layer 1: Context throttle (highest priority — prevents root cause)
        if self._context_pct >= _CONTEXT_STOP_PCT and not self._warned_stop:
            self._warned_stop = True
            self._warned_throttle = True  # Suppress WARN — STOP subsumes it
            return LoopAction.THROTTLE_STOP
        if self._context_pct >= _CONTEXT_WARN_PCT and not self._warned_throttle:
            self._warned_throttle = True
            return LoopAction.THROTTLE_WARN

        # Layer 2: Circuit breaker
        if not self._warned_loop:
            # Check exact repeats: same (tool, input) × 3
            for (tool_name, _hash), count in self._exact_counts.items():
                if count >= _EXACT_REPEAT_LIMIT:
                    self._warned_loop = True
                    logger.warning(
                        "compaction_guard.loop_detected exact_repeat "
                        "tool=%s count=%d hash=%s",
                        tool_name, count, _hash,
                    )
                    return LoopAction.LOOP_DETECTED

            # Check tool-name repeats: same tool × 8 (non-whitelisted)
            for tool_name, count in self._name_counts.items():
                if (
                    count >= _TOOL_NAME_LIMIT
                    and tool_name not in _REPEAT_WHITELISTED_TOOLS
                ):
                    self._warned_loop = True
                    logger.warning(
                        "compaction_guard.loop_detected tool_name_repeat "
                        "tool=%s count=%d",
                        tool_name, count,
                    )
                    return LoopAction.LOOP_DETECTED

        return LoopAction.NONE

    # ── Layer 3: Work Summary ─────────────────────────────────────

    def work_summary(self) -> str:
        """Generate a structured work summary for post-compaction injection.

        Returns a human-readable summary of all tool calls recorded
        this turn, suitable for injection via /compact instructions.
        """
        if not self._tool_calls:
            return ""

        # Group tool calls by name with counts
        tool_groups: dict[str, int] = defaultdict(int)
        for call in self._tool_calls:
            tool_groups[call.tool_name] += 1

        lines = [
            "[Post-Compaction Work Summary]",
            f"Tools executed this turn ({len(self._tool_calls)} total calls):",
        ]
        for name, count in sorted(tool_groups.items(), key=lambda x: -x[1]):
            lines.append(f"  - {name}: ×{count}")

        lines.extend([
            "",
            "CRITICAL: Do NOT re-run the tools listed above.",
            "If all work is complete, summarize results and STOP.",
            "If work remains, describe what's left and ask the user for instruction.",
            "DO NOT start new tool calls without user confirmation.",
        ])
        return "\n".join(lines)

    # ── SSE Event Builders ────────────────────────────────────────

    def build_throttle_warning_event(self) -> dict:
        """Build SSE event for context throttle warning (70%+)."""
        return {
            "type": "loop_guard",
            "subtype": "throttle_warning",
            "context_pct": round(self._context_pct, 1),
            "context_tokens": self._context_tokens,
            "message": (
                f"⚠️ Context {self._context_pct:.0f}% full. "
                f"Summarize completed work and wait for user instruction."
            ),
        }

    def build_throttle_stop_event(self) -> dict:
        """Build SSE event for context hard stop (85%+)."""
        return {
            "type": "loop_guard",
            "subtype": "throttle_stop",
            "context_pct": round(self._context_pct, 1),
            "context_tokens": self._context_tokens,
            "message": (
                f"🛑 Context {self._context_pct:.0f}% full — "
                f"session will be interrupted to prevent compaction loop."
            ),
        }

    def build_loop_warning_event(self) -> dict:
        """Build SSE event for circuit breaker trigger."""
        # Find the offending tool
        offender = "unknown"
        max_exact = 0
        for (tool_name, _hash), count in self._exact_counts.items():
            if count > max_exact:
                max_exact = count
                offender = tool_name

        return {
            "type": "loop_guard",
            "subtype": "loop_detected",
            "tool_name": offender,
            "repeat_count": max_exact,
            "total_tool_calls": len(self._tool_calls),
            "message": (
                f"🔄 Loop detected: {offender} called {max_exact}× "
                f"with identical arguments. Session will be interrupted."
            ),
        }

    # ── Lifecycle ─────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset for a new user turn.

        Call at the start of each send() — the user is actively
        engaged, so tool repeat tracking resets.
        Context usage is NOT reset — it persists across turns.
        """
        self._tool_calls.clear()
        self._exact_counts.clear()
        self._name_counts.clear()
        self._warned_throttle = False
        self._warned_stop = False
        self._warned_loop = False

    def reset_all(self) -> None:
        """Full reset including context tracking.

        Call on session restart (COLD → IDLE).
        """
        self.reset()
        self._context_pct = 0.0
        self._context_tokens = 0
