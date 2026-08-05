"""Shared utilities for the multi-session architecture.

Pure functions with no subprocess, routing, or hook logic.

Public symbols:

- ``FailureType``                        — Enum classifying why a session failed.
- ``classify_failure``                   — Structured failure classification (hook context + string fallback).
- ``_is_retriable_error``                — Classify transient SDK errors for auto-retry.
- ``_sanitize_sdk_error``                — Map raw SDK errors to user-friendly messages.
- ``_build_error_event``                 — Build a sanitized SSE error event dict.
- ``fuzzy_title_matches_deliverable``    — Fuzzy text matching (shared by proactive + distillation).
- ``read_owner_pid``                     — Read SWARMAI_OWNER_PID from a process's environment.
- ``is_session_not_found_error``         — Detect stale --resume session ID failures.
"""
from __future__ import annotations

import logging
import platform
import re
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from config import settings


# ---------------------------------------------------------------------------
# Failure classification — structured failure types for retry intelligence
# ---------------------------------------------------------------------------

class FailureType(Enum):
    """Why a session failed.  Drives retry backoff strategy.

    Each type maps to a different recovery behaviour:

    - OOM         → 30s flat backoff, check spawn budget
    - RATE_LIMIT  → wait until resets_at (or 60s default)
    - API_ERROR   → standard exponential backoff
    - TIMEOUT     → exponential backoff, abandon --resume after 2x
    - ZOMBIE      → near-zero backoff (deterministic poison, respawn at once)
    - UNKNOWN     → standard exponential backoff (conservative)
    """

    OOM = "oom"
    RATE_LIMIT = "rate_limit"
    API_ERROR = "api_error"
    TIMEOUT = "timeout"
    ZOMBIE = "zombie"
    UNKNOWN = "unknown"


def classify_failure(
    error_str: str,
    hook_context: Optional[dict] = None,
    *,
    recycle_kill: bool = False,
) -> tuple[FailureType, dict]:
    """Classify a failure using hook-captured context first, string fallback second.

    Returns ``(failure_type, metadata)`` where metadata contains type-specific
    info (e.g. ``resets_at`` for rate limits, ``pressure_level`` for OOM).

    ``recycle_kill`` marks that the immediately-preceding kill was an
    APP-INITIATED fast recycle of a poisoned/corrupt subprocess (post-interrupt
    or clean-flush). Such a kill surfaces as ``exit code -9`` (SIGKILL) — the
    SAME string an OS/Jetsam OOM kill produces — but it is OUR kill, not memory
    pressure, so it must respawn near-instantly (ZOMBIE) instead of eating the
    OOM 30/60/120s backoff. RSS-driven kills do NOT set this flag (they are real
    memory pressure and keep the OOM cooldown).

    Priority order:
    1. Zombie / app-initiated recycle SIGKILL → ZOMBIE (fast respawn)
    2. Hook-captured ``_last_notification`` with rate limit info → RATE_LIMIT
    3. OOM string patterns + memory pressure heuristic → OOM
    4. Timeout string patterns → TIMEOUT
    5. API error string patterns → API_ERROR
    6. Fallback → UNKNOWN
    """
    error_lower = error_str.lower()
    metadata: dict = {}

    # ── 0. Zombie subprocess (deterministic poison after interrupt) ──
    # streaming_orchestrator raises RuntimeError("Zombie subprocess detected: ...")
    # when a reused subprocess returns an instant empty error_during_execution.
    # This is NOT transient — the subprocess is already dead/poisoned, so the
    # standard exponential backoff is pure dead time. Classify it first (the
    # message never overlaps OOM/rate-limit/timeout patterns) so compute_backoff
    # can respawn near-immediately.
    if "zombie subprocess detected" in error_lower:
        return FailureType.ZOMBIE, metadata

    # ── 0b. App-initiated fast recycle (flush_recycle / interrupt_recycle) ──
    # A poisoned/corrupt subprocess (left after a Stop/interrupt or a clean
    # pipe-flush) is force-killed by us; the SDK then reports "exit code -9".
    # That -9 is INDISTINGUISHABLE by string from an OS OOM SIGKILL, so it used
    # to fall through to the OOM branch below and get a 30/60/120s cooldown —
    # turning a recycle that should --resume in ~0.5s into tens of seconds of
    # dead air ("回答死/卡半路"). When the caller tells us this -9 was our own
    # recycle (NOT memory pressure), classify it ZOMBIE → near-instant respawn.
    # Gated on the SIGKILL signature so a coincident rate-limit/timeout that
    # happens to carry the flag is still classified on its real merits.
    if recycle_kill and any(
        p in error_lower for p in ("exit code -9", "exit code: -9", "exit code=-9", "sigkill", "signal 9")
    ):
        metadata["recycle_kill"] = True
        return FailureType.ZOMBIE, metadata

    # ── 1. Hook context: rate limit notification ──────────────
    if hook_context:
        notif = hook_context.get("_last_notification")
        if notif and _is_rate_limit_notification(notif, error_lower):
            metadata["notification_type"] = notif.get("type", "")
            metadata["message"] = notif.get("message", "")
            # Extract resets_at if present in the notification message
            resets_at = _extract_resets_at(notif.get("message", ""))
            if resets_at:
                metadata["resets_at"] = resets_at
            return FailureType.RATE_LIMIT, metadata

    # ── 2. OOM / SIGKILL detection (string + memory heuristic) ──
    oom_patterns = [
        "exit code -9", "exit code: -9", "exit code=-9",
        "sigkill", "signal 9", "killed by signal",
        "jetsam", "terminated process",
    ]
    if any(p in error_lower for p in oom_patterns):
        metadata["pattern_matched"] = True
        return FailureType.OOM, metadata

    # Memory pressure fallback (process died + system under pressure)
    try:
        from .resource_monitor import resource_monitor
        mem = resource_monitor.system_memory()
        if mem.pressure_level == "critical":
            metadata["pressure_level"] = mem.pressure_level
            metadata["percent_used"] = mem.percent_used
            return FailureType.OOM, metadata
    except Exception:
        pass

    # ── 3. Rate limit (string patterns, no hook context) ──────
    rate_limit_patterns = [
        r"rate.?limit", r"too many requests", r"throttl",
    ]
    if any(re.search(p, error_lower) for p in rate_limit_patterns):
        return FailureType.RATE_LIMIT, metadata

    # ── 4. Timeout ────────────────────────────────────────────
    if "timeout" in error_lower or "timed out" in error_lower:
        return FailureType.TIMEOUT, metadata

    # ── 5. API / transient errors ─────────────────────────────
    api_patterns = [
        r"service.?unavailable", r"internal.?server.?error",
        r"overloaded", r"capacity", r"econnreset",
        r"connection reset", r"broken pipe", r"epipe",
    ]
    if any(re.search(p, error_lower) for p in api_patterns):
        return FailureType.API_ERROR, metadata

    # ── 6. Fallback ───────────────────────────────────────────
    return FailureType.UNKNOWN, metadata


def compute_backoff(
    failure_type: FailureType,
    metadata: dict,
    retry_count: int,
    base_backoff: float = 5.0,
) -> float:
    """Compute backoff seconds based on failure type.

    - OOM:        exponential 30/60/120s (capped at _OOM_COOLDOWN_CAP)
    - RATE_LIMIT: wait until resets_at, or 60s default, capped at 300s
    - TIMEOUT:    exponential (base * retry_count), capped at 60s
    - API_ERROR:  exponential (base * retry_count), capped at 60s
    - UNKNOWN:    exponential (base * retry_count), capped at 60s
    """
    if failure_type == FailureType.OOM:
        # Exponential backoff for OOM: 30s, 60s, 120s.
        # Flat 30s caused death spirals — two sessions retrying every 30s
        # would spawn simultaneously and get killed again immediately.
        from .session_unit import _OOM_COOLDOWN_BASE, _OOM_COOLDOWN_CAP
        return min(_OOM_COOLDOWN_BASE * (2 ** (retry_count - 1)), _OOM_COOLDOWN_CAP)

    if failure_type == FailureType.RATE_LIMIT:
        resets_at = metadata.get("resets_at")
        if resets_at:
            wait = max(0.0, resets_at - time.time())
            # Cap at 5 minutes — if resets_at is far future, don't block forever
            return min(wait + 2.0, 300.0)  # +2s buffer
        return 60.0  # Default rate limit backoff

    if failure_type == FailureType.ZOMBIE:
        # Deterministic poison: the subprocess is already killed and a fresh
        # one will --resume cleanly. No point waiting — respawn near-instantly.
        # A small non-zero value avoids a hot spin if the fresh subprocess also
        # zombies; MAX_RETRY_ATTEMPTS still bounds the loop.
        return 0.5

    # Exponential for everything else
    return min(base_backoff * retry_count, 60.0)


def _is_rate_limit_notification(notif: dict, error_lower: str) -> bool:
    """Check if a notification represents a rate limit event."""
    notif_type = notif.get("type", "").lower()
    message = notif.get("message", "").lower()
    # Notification type is "rate_limit" or message contains rate limit keywords
    if "rate" in notif_type and "limit" in notif_type:
        return True
    if "rate" in message and "limit" in message:
        return True
    if "throttl" in message or "too many requests" in message:
        return True
    # Also match if the error itself contains rate-limit keywords
    # and we have a recent notification (any type — the notification
    # confirms the CLI was communicating rate limit context).
    # Use word-boundary-aware patterns to avoid "rate" matching
    # inside "generate", "separate", etc.
    if re.search(r"rate.?limit", error_lower) or "throttl" in error_lower or "too many requests" in error_lower:
        return True
    return False


def _extract_resets_at(message: str) -> Optional[float]:
    """Extract Unix timestamp from rate limit notification message.

    Looks for patterns like 'resets at <timestamp>' or 'retry after <seconds>'.
    Returns Unix timestamp or None.
    """
    # Pattern: "resets at 1234567890" or "resets_at: 1234567890"
    m = re.search(r"resets?\s*(?:at|_at)[:\s]+(\d{10,13})", message)
    if m:
        ts = int(m.group(1))
        # If milliseconds, convert to seconds
        if ts > 1e12:
            ts = ts / 1000
        return float(ts)

    # Pattern: "retry after 60" (seconds from now)
    m = re.search(r"retry\s*(?:after|in)[:\s]+(\d+)", message, re.IGNORECASE)
    if m:
        return time.time() + int(m.group(1))

    return None


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
    (
        # Bedrock BEARER-TOKEN expiry at runtime (bedrock_api_key mode). A
        # short-term AWS_BEARER_TOKEN_BEDROCK expires in <=12h, but the daemon is
        # 24/7 — so an always-on session WILL hit mid-stream expiry. The STS
        # pre-flight is skipped for bedrock_api_key (no sigv4 identity), so this
        # bedrock-runtime error is the FIRST signal. It must NOT be mapped to the
        # generic "ada credentials update" hint — the recovery is re-entering the
        # bearer token. Ordered BEFORE the Anthropic pattern so the Bedrock-specific
        # ExpiredToken/bearer signature wins. (Meta-review HIGH, run_9d9f7dff.)
        r"(?:ExpiredToken(?:Exception)?|bedrock.{0,40}(?:bearer|token)|(?:bearer|token).{0,40}(?:expired|invalid)|AWS_BEARER_TOKEN_BEDROCK)",
        "Your Bedrock API key (bearer token) has expired or isn't working.",
        "Bedrock bearer tokens are short-lived (max 12h). Generate a new one and "
        "update it in Settings → AI & Models, then send your message again.",
    ),
    (
        # Anthropic-direct auth failure at runtime — a key valid at setup but
        # later revoked/rotated. In API-key mode the STS pre-flight and the
        # /health auth check are both skipped, so this SDK error is the FIRST
        # signal. Map to an actionable, provider-correct message (never mwinit).
        r"(?:authentication_error|invalid.{0,20}(?:api[_-]?key|x-api-key)|\b401\b|permission_error|Unauthorized)",
        "Your API key isn't working.",
        "Update your Anthropic API key in Settings → AI & Models, then send your message again.",
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


def _is_retriable_error(raw_error: str, tb_str: str = "") -> bool:
    """Check if this SDK error is transient and should be auto-retried.

    When True, the error event should NOT be yielded to the frontend —
    the auto-retry path will handle the UX with a softer "reconnecting"
    indicator instead.

    Covers two categories:

    1. Process-level failures (OOM kill, broken pipe) — the CLI died
    2. Bedrock API transient errors (throttling, overload, 5xx) — the
       API returned a retriable status code but the CLI didn't retry
       internally

    Args:
        raw_error: The error message string.
        tb_str: Optional traceback string.  When provided, used to
            detect fatal (non-retriable) conditions that share error
            messages with transient ones (e.g. zlib errors from
            PyInstaller archive corruption vs. network corruption).
    """
    # ── Fatal: PyInstaller archive corruption ──────────────────────
    # zlib errors from pyimod01_archive.extract() mean the frozen
    # binary's bytecode archive is damaged.  Retrying reads the same
    # corrupted data — only a daemon restart (fresh _MEI extraction)
    # fixes this.  Must check BEFORE the retriable zlib patterns below.
    if tb_str and (
        "pyimod01_archive" in tb_str or "pyimod02_importers" in tb_str
    ):
        logger.error(
            "PyInstaller archive corruption detected — NOT retriable. "
            "Daemon restart required. Error: %s",
            raw_error[:200],
        )
        return False

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
        # zlib decompression errors — corrupted/truncated gzip HTTP response
        # from Bedrock or Anthropic API.  Transient network issue; retry
        # with --resume restores conversation and re-sends the query.
        r"decompressing data",
        r"incorrect header check",
        # API returned valid ResultMessage but with zero content — transient
        # failure where Bedrock/Anthropic accepted the request but returned
        # nothing (429 converted to empty response, connection drop, etc.)
        r"API returned empty response",
        # Bedrock transient internal-5xx surfaced as an is_error ResultMessage
        # with the literal text "The system encountered an unexpected error
        # during processing. Try your request again." Semantically identical to
        # internal.?server.?error above; Bedrock just uses this wording for a
        # generic transient internal failure. Almost always succeeds on the next
        # --resume attempt (prod: session 9d00e432, 2026-08-05). Match the
        # durable noun phrase ONLY (not a broad "unexpected error"/"try again")
        # so a genuinely permanent error is never swept into auto-retry.
        r"unexpected error during processing",
        # Bedrock API timeout — cross-region latency + cache miss can
        # exceed the API gateway timeout.  Retry succeeds because prompt
        # cache was created on the first attempt (5min TTL); the second
        # attempt gets a cache hit and completes within timeout.
        # Circuit breaker stops after 2 consecutive timeouts + >1M context.
        r"operation timed out",
        # Tool-call XML leaked into the text channel — the model emitted
        # tool-call syntax (a "call" line + <invoke name=...>) as plain text
        # instead of a real tool_use block, so the turn ends with no tool
        # execution.  Kill + --resume gives the model a fresh attempt to
        # emit a proper tool_use.  See detect_tool_call_leak() below.
        r"Tool-call XML leaked into text channel",
    ]
    for pattern in retriable_patterns:
        if re.search(pattern, raw_error, re.IGNORECASE):
            return True
    return False


# ---------------------------------------------------------------------------
# Tool-call XML leak detection
# ---------------------------------------------------------------------------
#
# Under long / meta-heavy sessions the model occasionally emits the harness
# tool-call SYNTAX as plain text instead of a real tool_use block.  The SDK
# parses it as a TextBlock, so the raw XML is persisted to the messages DB and
# rendered on screen as a half-finished tag, and the turn ends with no tool
# execution (user sees "response stopped mid-way").  Empirically (8 DB-confirmed
# cases, 2026-06-24) the leak takes the harness's literal call body:
#
#     call
#     <invoke name="Bash">
#     <parameter name="command">...
#
# DISCRIMINATION — the hard part.  This is a meta-heavy codebase: assistant turns
# routinely DOCUMENT, review, or self-explain this exact shape.  An early version
# keyed on a bare ``call`` line + ``<invoke name=`` — but that is *precisely* what
# you type when explaining the bug, so it would wrongfully kill (kill + --resume)
# any turn that discusses the syntax.  Two structural facts separate a real leak
# from documentation:
#
#   1. Documentation lives in fenced code blocks (``` ... ```) or inline backtick
#      spans; a REAL leak is RAW text (the model believes it is making a real
#      call, so it does not fence it).  → strip fences + inline code first.
#   2. A real leak is the FULL body — ``<invoke name="X">`` immediately followed
#      by ``<parameter name=`` (or ``</invoke>``).  Keying on the body, NOT the
#      optional ``call`` prefix, also catches leaks that omit ``call`` (the 8th
#      DB case) and is robust to CRLF / casing.
#
# A third signal (no real ToolUseBlock in the same message) is checked by the
# caller (the orchestrator) — if the model DID emit a real tool_use, text that
# mentions <invoke> is discussion, not a leak.
#
# The regex has no nested quantifiers (``[^"]+`` is bounded by ``"``; ``\s*`` is
# bounded by literals) → no catastrophic backtracking.
_TOOL_CALL_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_TOOL_CALL_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Case-SENSITIVE on purpose: the harness emits lowercase <invoke>/<parameter>
# literally. Prose discussing it may use other casing; matching only the exact
# harness casing further narrows the false-positive surface. (\s spans the
# inter-token whitespace incl. CRLF.)
_TOOL_CALL_LEAK_RE = re.compile(
    r'<invoke\s+name="[^"]+"\s*>\s*(?:<parameter\s+name=|</invoke>)',
)


def detect_tool_call_leak(text: str) -> bool:
    """Return True if ``text`` contains leaked tool-call XML in the text channel.

    Detects the harness tool-call BODY — ``<invoke name="...">`` immediately
    followed by ``<parameter name=`` or ``</invoke>`` — emitted as RAW assistant
    text instead of a real tool_use block.

    False-positive-safe on documentation: fenced code blocks and inline-backtick
    spans (where assistant prose shows the syntax) are stripped before matching,
    so a turn that merely *explains* the leak shape is not flagged.  This matters
    because this is a meta-heavy codebase whose own turns routinely discuss this
    exact syntax — a false positive would wrongfully kill a legitimate turn.

    Pure function (str -> bool) so it can be unit-tested in isolation.  Called
    from the StreamingOrchestrator TextBlock branch (the DB-persist gate), which
    additionally gates on the absence of a real ToolUseBlock in the message.

    Args:
        text: The assistant text block content to inspect.

    Returns:
        True if a raw tool-call leak body is present (outside code fences).
    """
    if not text or "<invoke" not in text:
        return False  # fast path — the overwhelming majority of blocks
    # Strip documentation contexts so prose that SHOWS the syntax is ignored;
    # a real leak is raw unfenced text.
    stripped = _TOOL_CALL_FENCE_RE.sub("", text)
    stripped = _TOOL_CALL_INLINE_CODE_RE.sub("", stripped)
    return _TOOL_CALL_LEAK_RE.search(stripped) is not None


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


# ---------------------------------------------------------------------------
# Process ownership — single source of truth for SWARMAI_OWNER_PID reads
# ---------------------------------------------------------------------------

def read_owner_pid(pid: int) -> int | None:
    """Read SWARMAI_OWNER_PID from a process's environment.

    Uses ``ps eww -o command= -p <pid>`` on macOS to get the full
    command line with environment variables.  Falls back to
    ``/proc/<pid>/environ`` on Linux.

    Returns the owner PID as int, or None if not found.

    This is the single source of truth — used by both
    ``lifecycle_manager._is_owned_orphan()`` and
    ``session_registry.kill_all_claude_processes()``.
    """
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["ps", "eww", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            match = re.search(r"SWARMAI_OWNER_PID=(\d+)", result.stdout)
            if match:
                return int(match.group(1))
        except (subprocess.TimeoutExpired, OSError):
            pass
    else:
        try:
            environ_path = Path(f"/proc/{pid}/environ")
            if environ_path.exists():
                content = environ_path.read_bytes()
                for entry in content.split(b"\x00"):
                    if entry.startswith(b"SWARMAI_OWNER_PID="):
                        return int(entry.split(b"=", 1)[1])
        except (OSError, ValueError):
            pass

    return None


# ---------------------------------------------------------------------------
# Session-not-found detection — for stale --resume fallback (PE F11)
# ---------------------------------------------------------------------------

_SESSION_NOT_FOUND_PATTERNS = [
    # CLI-specific resume failure patterns (anchored to avoid false positives
    # from MCP/tool errors that happen to contain "session" — PE HIGH-1).
    re.compile(r"(?:failed to|cannot|unable to)\s+(?:load|resume|restore).*session", re.IGNORECASE),
    re.compile(r"session\s+(?:file|data)\s+not\s+found", re.IGNORECASE),
    re.compile(r"no\s+such\s+session\s+(?:file|id)", re.IGNORECASE),
    re.compile(r"ENOENT.*\.claude[/\\].*session", re.IGNORECASE),
    re.compile(r"resume.*session.*(?:not found|does not exist|missing)", re.IGNORECASE),
]


def is_session_not_found_error(error_str: str) -> bool:
    """Detect if a spawn/resume error indicates a stale sdk_session_id.

    When ``--resume`` is used with a session ID that no longer exists on disk
    (e.g., after a long daemon downtime or OS temp cleanup), the CLI emits
    a "session not found" type error. In this case, the correct recovery is
    to clear ``_sdk_session_id`` and fall back to cold resume.

    Patterns are anchored to CLI resume-specific contexts (PE HIGH-1):
    generic "session not found" from MCP servers or user tools will NOT match.

    Returns True if the error matches a known CLI resume-failure pattern.
    """
    return any(p.search(error_str) for p in _SESSION_NOT_FOUND_PATTERNS)
