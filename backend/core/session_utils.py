"""Shared utilities for the multi-session architecture.

Pure functions with no subprocess, routing, or hook logic.

Public symbols:

- ``_is_retriable_error``                — Classify transient SDK errors for auto-retry.
- ``_sanitize_sdk_error``                — Map raw SDK errors to user-friendly messages.
- ``_build_error_event``                 — Build a sanitized SSE error event dict.
- ``fuzzy_title_matches_deliverable``    — Fuzzy text matching (shared by proactive + distillation).
"""
from __future__ import annotations

import re
from typing import Optional

from config import settings


# ---------------------------------------------------------------------------
# SDK error sanitization — translate raw CLI errors to user-friendly messages
# ---------------------------------------------------------------------------

# Patterns: (regex, friendly_message, suggested_action)
_SDK_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    (
        r"(?:Cannot write to terminated process|Command failed with exit code -9|exit code: -9)",
        "The AI service connection was interrupted.",
        "This is usually temporary. Your conversation is saved — just send your message again.",
    ),
    (
        r"exit code: -(?:6|11|15)",
        "The AI service process ended unexpectedly.",
        "Your conversation is saved. Send your message again to continue.",
    ),
    (
        r"(?:SIGTERM|SIGKILL|signal \d+)",
        "The AI service was stopped by the system.",
        "This can happen during high memory usage. Your conversation is saved.",
    ),
    (
        r"(?:broken pipe|connection reset|EPIPE|ECONNRESET)",
        "Lost connection to the AI service.",
        "Reconnecting automatically. If this persists, try restarting the app.",
    ),
]


def _sanitize_sdk_error(raw_error: str) -> tuple[str, Optional[str]]:
    """Map raw SDK error strings to user-friendly messages.

    Returns ``(friendly_message, suggested_action)``.  If no pattern
    matches, returns the original message with a generic suggestion.
    """
    for pattern, friendly, action in _SDK_ERROR_PATTERNS:
        if re.search(pattern, raw_error, re.IGNORECASE):
            return friendly, action
    # No match — return original but add a generic suggestion
    return raw_error, "Your conversation is saved. Send your message again to continue."


def _is_retriable_error(raw_error: str) -> bool:
    """Check if this SDK error is transient and should be auto-retried.

    When True, the error event should NOT be yielded to the frontend —
    the auto-retry path will handle the UX with a softer "reconnecting"
    indicator instead.

    Covers two categories:

    1. Process-level failures (OOM kill, broken pipe) — the CLI died
    2. Bedrock API transient errors (throttling, overload, 5xx) — the
       API returned a retriable status code but the CLI didn't retry
       internally
    """
    retriable_patterns = [
        # Process-level failures
        r"exit code: -9",
        r"Cannot write to terminated process",
        r"Command failed with exit code -9",
        r"broken pipe",
        r"EPIPE",
        # Bedrock / Anthropic API transient errors
        r"throttl",
        r"too many requests",
        r"rate.?limit",
        r"service.?unavailable",
        r"internal.?server.?error",
        r"overloaded",
        r"capacity",
        r"ECONNRESET",
        r"connection reset",
        r"SDK_SUBPROCESS_TIMEOUT",
        # Streaming timeout — SDK hung without producing messages
        # Format: "Streaming timeout (init|streaming): no SDK response ..."
        r"Streaming timeout.*no SDK response",
        # Zombie subprocess — stream ended instantly with no content after interrupt
        r"Zombie subprocess detected",
        # Null byte in CLI arguments — intermittent, caused by concurrent file
        # reads during context assembly or env pollution.  The null byte is
        # stripped on retry (defense-in-depth in session_unit._spawn).
        r"embedded null byte",
    ]
    for pattern in retriable_patterns:
        if re.search(pattern, raw_error, re.IGNORECASE):
            return True
    return False


def _build_error_event(
    code: str,
    message: str,
    *,
    detail: Optional[str] = None,
    suggested_action: Optional[str] = None,
) -> dict:
    """Build a sanitized SSE error event dict.

    When ``settings.debug`` is True the full *detail* string (typically a
    Python traceback) is included verbatim.  In production mode the detail
    is stripped of tracebacks, file paths with line numbers, and library
    version strings so that internal implementation details are never
    leaked to the frontend.
    """
    event: dict = {"type": "error", "code": code, "message": message, "error": message}
    if suggested_action:
        event["suggested_action"] = suggested_action
    if detail:
        if settings.debug:
            event["detail"] = detail
        else:
            # Sanitize: drop lines that expose internal implementation details.
            sanitized_lines: list[str] = []
            for line in detail.splitlines():
                stripped = line.strip()
                if stripped.startswith("Traceback (most recent call last)"):
                    continue
                if stripped.startswith("File \"") and ".py\", line" in stripped:
                    continue
                if stripped and all(c in "^~ " for c in stripped):
                    continue
                sanitized_lines.append(line)
            sanitized = "\n".join(sanitized_lines).strip()
            if sanitized:
                event["detail"] = sanitized
    return event



# ---------------------------------------------------------------------------
# Fuzzy text matching — shared by proactive_intelligence + distillation_hook
# ---------------------------------------------------------------------------

def fuzzy_title_matches_deliverable(
    title: str,
    deliverables: list[str],
    deliv_word_sets: list[set[str]] | None = None,
) -> bool:
    """Check if a thread/item title fuzzy-matches any deliverable.

    Matching heuristics (same as COE matching):
    - Substring match in either direction
    - ≥50% word overlap between title words and deliverable words

    Parameters
    ----------
    title:
        The thread/item title to check.
    deliverables:
        Lowercased deliverable strings.
    deliv_word_sets:
        Pre-computed word sets for each deliverable (optimization).
        If None, computed on the fly.

    Returns True if any deliverable matches.
    """
    title_lower = title.lower()
    title_words = set(title_lower.split())

    if deliv_word_sets is None:
        deliv_word_sets = [set(d.split()) for d in deliverables]

    for d_idx, d_text in enumerate(deliverables):
        # Substring match
        if title_lower in d_text or d_text in title_lower:
            return True
        # Fuzzy word overlap (≥50% of title words in deliverable)
        overlap = title_words & deliv_word_sets[d_idx]
        if len(overlap) >= max(1, len(title_words) // 2):
            return True

    return False
