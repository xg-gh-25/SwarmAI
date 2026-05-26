"""ObservationMiner — session-end pattern extraction from ObservationRing.

Runs ONCE at session close (inside DailyActivityExtractionHook).
Pure heuristic analysis — no IO, no LLM, no network. <100ms for 200 observations.

Pattern types:
    RETRY     — Same tool+similar intent appears 3+ times with >50% error rate
    LONG_TOOL — Tool call >30s (optimization opportunity)

Public symbols:
    - Pattern            — extracted pattern dataclass
    - ObservationMiner   — stateless pattern extractor
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.observation_ring import Observation

logger = logging.getLogger(__name__)


@dataclass
class Pattern:
    """Extracted behavioral pattern from a session's observations."""

    type: str  # "RETRY" | "LONG_TOOL"
    confidence: float  # 0.0 - 1.0
    description: str  # Human-readable summary
    tool: str  # Primary tool involved
    session_id: str = ""  # Set by caller

    def to_dict(self) -> dict:
        """Serialize for JSONL storage."""
        return {
            "type": self.type,
            "confidence": self.confidence,
            "description": self.description,
            "tool": self.tool,
            "session_id": self.session_id,
            "ts": time.time(),
        }


class ObservationMiner:
    """Extract behavioral patterns from a session's completed observations.

    Design constraints:
        - No IO (pure computation over in-memory list)
        - <100ms for 200 observations
        - No LLM calls (heuristic only)
        - Returns patterns with confidence scores
        - Consumers decide what to do (miner doesn't act)
    """

    RETRY_THRESHOLD = 3  # Same tool+intent 3+ times
    LONG_TOOL_THRESHOLD_MS = 30_000  # >30s

    def mine(self, observations: list["Observation"]) -> list[Pattern]:
        """Run all pattern detectors. Returns deduplicated patterns."""
        if len(observations) < 5:
            return []

        patterns: list[Pattern] = []
        patterns.extend(self._detect_retries(observations))
        patterns.extend(self._detect_long_tools(observations))
        return patterns

    def _detect_retries(self, obs: list["Observation"]) -> list[Pattern]:
        """Detect repeated tool+intent combinations with high error rate."""
        # Group by (tool_name, intent_prefix_50chars)
        groups: dict[str, list[int]] = {}
        for i, o in enumerate(obs):
            key = f"{o.tool_name}:{o.intent[:50]}"
            groups.setdefault(key, []).append(i)

        patterns = []
        for key, indices in groups.items():
            if len(indices) < self.RETRY_THRESHOLD:
                continue

            statuses = [obs[i].result_status for i in indices]
            error_count = statuses.count("error")
            error_rate = error_count / len(statuses) if statuses else 0

            # Only flag if error rate > 50% (distinguishes retries from normal repeated use)
            if error_rate <= 0.5:
                continue

            tool_name = key.split(":")[0]
            intent_preview = key.split(":", 1)[1][:80] if ":" in key else key[:80]

            if error_count == len(statuses):
                desc = f"Stuck: {intent_preview} failed {len(indices)}x"
                confidence = 0.9
            else:
                desc = f"Retried {intent_preview} {len(indices)}x ({error_count} errors)"
                confidence = 0.7

            patterns.append(Pattern(
                type="RETRY",
                confidence=confidence,
                description=desc,
                tool=tool_name,
            ))
        return patterns

    def _detect_long_tools(self, obs: list["Observation"]) -> list[Pattern]:
        """Detect tool calls exceeding duration threshold."""
        patterns = []
        for o in obs:
            if o.duration_ms > self.LONG_TOOL_THRESHOLD_MS:
                patterns.append(Pattern(
                    type="LONG_TOOL",
                    confidence=0.6,
                    description=f"{o.tool_name} took {o.duration_ms / 1000:.1f}s: {o.intent[:80]}",
                    tool=o.tool_name,
                ))
        return patterns


def write_patterns(
    patterns: list[Pattern],
    session_id: str,
    output_path: Path | None = None,
) -> None:
    """Append patterns to patterns.jsonl (session-end operation).

    Args:
        patterns: Extracted patterns from ObservationMiner.mine()
        session_id: Current session ID for attribution
        output_path: Override path (default: ~/.swarm-ai/state/patterns.jsonl)
    """
    if not patterns:
        return

    if output_path is None:
        try:
            from jobs.paths import STATE_DIR
            output_path = STATE_DIR / "patterns.jsonl"
        except ImportError:
            return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(output_path, "a", encoding="utf-8") as f:
            for p in patterns:
                p.session_id = session_id
                f.write(json.dumps(p.to_dict(), ensure_ascii=False) + "\n")
    except OSError:
        logger.debug("Failed to write patterns to %s", output_path, exc_info=True)
