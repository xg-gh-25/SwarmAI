"""Tool failure evolution trigger hook.

Watches for repeated tool/command failures during a session and injects
a system-level nudge into the agent's context, prompting the self-evolution
skill to fire a reactive trigger. This converts the biggest prompt-dependent
gap (trigger detection) into a code-assisted mechanism.

The hook does NOT run the evolution loop itself — it only detects the
pattern and emits a nudge. The agent's self-evolution skill handles the
actual evolution loop.

Key public symbols:

- ``ToolFailureTracker``       — Per-session failure counter (no global state)
- ``check_tool_failure``       — Stateless check function called from the
                                  message processing loop
- ``format_evolution_nudge``   — Builds the nudge text for the agent
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# A tool/command is considered a "repeated failure" after this many
# consecutive failures with the same error signature.
FAILURE_THRESHOLD = 2

# Cooldown in seconds between nudges for the same failure pattern.
NUDGE_COOLDOWN_SECONDS = 120


@dataclass
class FailureRecord:
    """Tracks a specific failure pattern."""
    signature: str
    count: int = 0
    last_seen: float = 0.0
    last_nudged: float = 0.0


class ToolFailureTracker:
    """Per-session tracker for repeated tool/command failures.

    Instantiated per session — no shared mutable state. Stored in
    ``_active_sessions[sid]["failure_tracker"]``.

    Tracks failure signatures (tool_name + error substring) and fires
    a nudge when the same signature appears FAILURE_THRESHOLD times.
    """

    def __init__(self) -> None:
        self._failures: dict[str, FailureRecord] = {}
        self._total_nudges: int = 0
        self._max_nudges_per_session: int = 3

    def record_failure(self, tool_name: str, error_text: str) -> str | None:
        """Record a tool failure and return a nudge message if threshold met.

        Args:
            tool_name: Name of the failed tool (e.g. "Bash", "Write").
            error_text: Error message or first 200 chars of stderr.

        Returns:
            A nudge message string if the failure threshold is met and
            cooldown has elapsed, otherwise None.
        """
        if self._total_nudges >= self._max_nudges_per_session:
            return None

        sig = _failure_signature(tool_name, error_text)
        now = time.monotonic()

        if sig not in self._failures:
            self._failures[sig] = FailureRecord(signature=sig)

        rec = self._failures[sig]
        rec.count += 1
        rec.last_seen = now

        if rec.count < FAILURE_THRESHOLD:
            return None

        # Check cooldown
        if now - rec.last_nudged < NUDGE_COOLDOWN_SECONDS:
            return None

        rec.last_nudged = now
        self._total_nudges += 1

        nudge = format_evolution_nudge(tool_name, error_text, rec.count)
        logger.info(
            "Evolution nudge fired: tool=%s, failures=%d, sig=%s",
            tool_name, rec.count, sig[:60],
        )
        return nudge

    def reset_signature(self, tool_name: str, error_text: str) -> None:
        """Reset a failure signature after successful tool use.

        Called when a tool succeeds to clear the consecutive failure count,
        preventing stale failures from triggering nudges later.
        """
        sig = _failure_signature(tool_name, error_text)
        self._failures.pop(sig, None)

    def reset_tool(self, tool_name: str) -> None:
        """Reset all failure signatures for a tool after any success."""
        prefix = f"{tool_name.lower()}:"
        to_remove = [k for k in self._failures if k.startswith(prefix)]
        for k in to_remove:
            del self._failures[k]


def _failure_signature(tool_name: str, error_text: str) -> str:
    """Derive a stable signature from tool name + error text.

    Uses the first 100 chars of the error (lowercased, stripped) to
    group similar failures without being too specific (line numbers,
    timestamps vary).
    """
    error_key = error_text.strip().lower()[:100]
    return f"{tool_name.lower()}:{error_key}"


def format_evolution_nudge(tool_name: str, error_text: str, failure_count: int) -> str:
    """Build the nudge text injected into the agent's context.

    This is NOT a user-visible message — it's a system-level hint that
    the self-evolution skill should detect and act on.
    """
    return (
        f"[EVOLUTION TRIGGER — REACTIVE] "
        f"Tool '{tool_name}' has failed {failure_count} times consecutively "
        f"with: {error_text[:200]}. "
        f"This is a recurring capability gap. "
        f"Check your self-evolution skill instructions and consider entering "
        f"the evolution loop (reactive trigger). "
        f"Read /tmp/swarm-evo-triggers-$SESSION_ID to check your trigger budget."
    )


def check_tool_result_for_failure(
    tool_name: str,
    tool_result: str,
    is_error: bool,
    tracker: ToolFailureTracker,
) -> str | None:
    """Stateless check called from the message processing loop.

    Args:
        tool_name: Name of the tool that was called.
        tool_result: The tool's output text.
        is_error: Whether the tool reported an error.
        tracker: The session's ToolFailureTracker instance.

    Returns:
        A nudge message if evolution should be triggered, else None.
    """
    if is_error:
        return tracker.record_failure(tool_name, tool_result)
    else:
        # Success — reset failure count for this tool
        tracker.reset_tool(tool_name)
        return None
