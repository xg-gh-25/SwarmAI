"""Shared utilities for the multi-session architecture.

Pure functions with no subprocess, routing, or hook logic.

Public symbols:

- ``FailureType``                        — Enum classifying why a session failed.
- ``classify_failure``                   — Structured failure classification (hook context + string fallback).
- ``_is_retriable_error``                — Classify transient SDK errors for auto-retry.
- ``_sanitize_sdk_error``                — Map raw SDK errors to user-friendly messages.
- ``_build_error_event``                 — Build a sanitized SSE error event dict.
- ``read_owner_pid``                     — Read SWARMAI_OWNER_PID from a process's environment.
- ``is_session_not_found_error``         — Detect stale --resume session ID failures.
- ``detect_tool_call_leak`` / ``strip_tool_call_leak`` / ``find_stream_leak_onset``
                                         — Tool-call-as-text leak detect / remove / live-stream onset.
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
    - KILLED      → standard backoff, never the OOM cooldown (a SIGKILL with no
                    memory pressure: killed from outside)
    - UNKNOWN     → standard exponential backoff (conservative)
    """

    OOM = "oom"
    RATE_LIMIT = "rate_limit"
    API_ERROR = "api_error"
    TIMEOUT = "timeout"
    ZOMBIE = "zombie"
    #: SIGKILL from outside the app with no memory pressure: not an OOM (no OOM
    #: counter, no global cooldown), and the user is not told it ran out of memory.
    KILLED = "killed"
    UNKNOWN = "unknown"


def _memory_pressure() -> "tuple[Optional[str], Optional[float]]":
    """(pressure level ok/warning/critical, percent used) of the host now, or
    (None, None) when it cannot be read (then a SIGKILL stays OOM). Tests patch it."""
    try:
        from .resource_monitor import resource_monitor
        mem = resource_monitor.system_memory()
        return mem.pressure_level, round(float(mem.percent_used), 1)
    except Exception as exc:  # noqa: BLE001 — unknown pressure
        logger.debug("classify_failure: memory pressure unreadable: %s", exc)
        return None, None


def classify_failure(
    error_str: str,
    hook_context: Optional[dict] = None,
    *,
    recycle_kill: bool = False,
    stall_kill: bool = False,
) -> tuple[FailureType, dict]:
    """Classify a failure using hook-captured context first, string fallback second.

    Returns ``(failure_type, metadata)`` where metadata contains type-specific
    info (e.g. ``resets_at`` for rate limits, ``pressure_level`` for OOM).

    ``stall_kill`` marks that the -9 was the stall watchdog's own kill
    (``force_unstick_streaming``): a genuine hang, NOT memory pressure. It keeps
    a normal backoff but never counts as OOM (no OOM counter, no global
    cooldown that would hold every other tab's spawn).

    ``recycle_kill`` marks that the immediately-preceding kill was an
    APP-INITIATED fast recycle of a poisoned/corrupt subprocess (after an
    interrupt, or found unclean at the next send). Such a kill surfaces as
    ``exit code -9`` (SIGKILL) — the
    SAME string an OS/Jetsam OOM kill produces — but it is OUR kill, not memory
    pressure, so it must respawn near-instantly (ZOMBIE) instead of eating the
    OOM 30/60/120s backoff. RSS-driven kills do NOT set this flag (they are real
    memory pressure and keep the OOM cooldown).

    Priority order:
    1. Zombie / app-initiated recycle SIGKILL → ZOMBIE (fast respawn)
    2. Hook-captured ``_last_notification`` with rate limit info → RATE_LIMIT
    3. SIGKILL patterns: OOM under memory pressure (or jetsam), else KILLED;
       memory pressure heuristic → OOM
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

    # ── 0b. App-initiated fast recycle (interrupt_recycle / poison_guard_recycle) ──
    # A poisoned/corrupt subprocess (left after a Stop/interrupt, or found warm but
    # unclean at the next send) is force-killed by us; the SDK then reports "exit code -9".
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

    # ── 0c. Stall-watchdog kill: our own -9 on a hung stream, not OOM ──
    if stall_kill and any(
        p in error_lower for p in ("exit code -9", "exit code: -9", "exit code=-9", "sigkill", "signal 9")
    ):
        metadata["stall_kill"] = True
        return FailureType.UNKNOWN, metadata

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
        # A SIGKILL is OOM only with evidence of memory pressure (or jetsam named):
        # three CLIs killed at once at 72% memory were reported as "ran out of
        # memory" (2026-10-21 16:45) — someone else's kill. Unknown pressure keeps OOM.
        level, pct = _memory_pressure()
        if level is not None:
            metadata["pressure_level"] = level
            metadata["percent_used"] = pct
        sigkill_only = any(p in error_lower for p in (
            "exit code -9", "exit code: -9", "exit code=-9", "sigkill", "signal 9", "killed by signal"))
        if level == "ok" and sigkill_only and "jetsam" not in error_lower:
            # core/kill_audit: did THIS process SIGKILL a CLI just now? If not,
            # the kill came from outside (a test or tool sweeping processes).
            try:
                from core import kill_audit
                metadata["killed_by"] = "swarmai" if kill_audit.killed_by_us() else "external"
            except Exception:  # noqa: BLE001
                logger.debug("classify_failure: kill audit unreadable", exc_info=True)
            if metadata.get("killed_by") == "external":
                logger.warning("classify_failure: SIGKILL with no kill of ours on record "
                               "and memory pressure ok — an external kill")
            return FailureType.KILLED, metadata
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
        "update it in Settings → LLM Models, then send your message again.",
    ),
    (
        # Anthropic-direct auth failure at runtime — a key valid at setup but
        # later revoked/rotated. In API-key mode the STS pre-flight and the
        # /health auth check are both skipped, so this SDK error is the FIRST
        # signal. Map to an actionable, provider-correct message (never mwinit).
        r"(?:authentication_error|invalid.{0,20}(?:api[_-]?key|x-api-key)|\b401\b|permission_error|Unauthorized)",
        "Your API key isn't working.",
        "Anthropic keys are no longer offered: in Settings → LLM Models choose a Bedrock "
        "API key or Bedrock through the Swarm AWS account, then send your message again.",
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
#     ‹invoke name="Bash">
#     ‹parameter name="command">...
#
# DISCRIMINATION — the hard part.  This is a meta-heavy codebase: assistant turns
# routinely DOCUMENT, review, or self-explain this exact shape.  An early version
# keyed on a bare ``call`` line + ``‹invoke name=`` — but that is *precisely* what
# you type when explaining the bug, so it would wrongfully kill (kill + --resume)
# any turn that discusses the syntax.  Two structural facts separate a real leak
# from documentation:
#
#   1. Documentation lives in fenced code blocks (``` ... ```) or inline backtick
#      spans; a REAL leak is RAW text (the model believes it is making a real
#      call, so it does not fence it).  → strip fences + inline code first.
#   2. A real leak is the FULL body — ``‹invoke name="X">`` immediately followed
#      by ``‹parameter name=`` (or ``‹/invoke>``).  Keying on the body, NOT the
#      optional ``call`` prefix, also catches leaks that omit ``call`` (the 8th
#      DB case) and is robust to CRLF / casing.
#
# A third signal (no real ToolUseBlock in the same message) is checked by the
# caller (the orchestrator) — if the model DID emit a real tool_use, text that
# mentions ‹invoke> is discussion, not a leak.
#
# The regex has no nested quantifiers (``[^"]+`` is bounded by ``"``; ``\s*`` is
# bounded by literals) → no catastrophic backtracking.
#
# SINGLE-PASS mask (bug 2a fix, run_719886ee): fence and inline-code masking MUST
# run as ONE alternation, fence branch FIRST, never two sequential ``.sub()`` calls.
# Two passes corrupt text: the fence pass substitutes ``\x00N\x00`` sentinels, then
# the inline pass runs ``[^`]*`` over text that now contains ``\x00`` — a lone
# backtick in the remaining prose pairs ACROSS a sentinel, so the inline match
# straddles (and destroys) a masked fence span → the fence is not restored and a
# raw ``\x00`` leaks out (observed: ```` ```python…``` ```` → ``\x000\x00``). A single
# alternation with the fence alternative first means the greedy ```` ``` ```` block is
# matched and masked atomically before any inline ``` ` ``` can see it, and the already
# emitted sentinels are literal text the regex engine never re-enters.
#
# The inline branch is ``[^`\n]*`` (NO newline): markdown inline code is
# single-line, and forbidding newlines stops a LONE backtick in prose (e.g. the
# apostrophe-like ``Don`t``) from pairing with a backtick on a LATER line — in
# particular the opening backtick of a fenced block — which would otherwise mask
# (and destroy) everything in between, INCLUDING a raw leak sitting on that line.
# (``.*?`` on the fence branch still crosses newlines via DOTALL; only the inline
# branch is newline-bounded.)
_TOOL_CALL_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
# Case-SENSITIVE on purpose: the harness emits lowercase ‹invoke>/‹parameter>
# literally. Prose discussing it may use other casing; matching only the exact
# harness casing further narrows the false-positive surface. (\s spans the
# inter-token whitespace incl. CRLF.)
_TOOL_CALL_LEAK_RE = re.compile(
    r'<invoke\s+name="[^"]+"\s*>\s*(?:<parameter\s+name=|</invoke>)',
)

# CLEAN anchor (used by strip_tool_call_leak, NOT by the DETECT path). It is
# deliberately WIDER than _TOOL_CALL_LEAK_RE: a leak that is KILLED mid-stream
# persists to data.db TRUNCATED — the model got as far as ``‹invoke name="Bash">``
# before the subprocess was SIGKILLed, so there is NO following ``‹parameter`` /
# ``‹/invoke>`` that _TOOL_CALL_LEAK_RE requires. (Measured on data.db session
# a0f04b00: 8 persisted leaks, all of the bare ``court\n‹invoke name="Bash">``
# truncated shape — _TOOL_CALL_LEAK_RE returns False on every one.) The strip
# step runs on ALREADY-PERSISTED history (resume-context assembly), so it must
# catch the truncated residue, not only the complete live body. It greedily
# consumes any complete ``‹parameter>...‹/parameter>`` bodies + a trailing
# ``‹/invoke>`` when they ARE present, so a complete leak is removed whole.
#
# OPENER LINE: in every observed leak the model writes a garbled one-word opener
# line (``call`` / ``count`` / ``core`` / ``court``) right before ``‹invoke``
# (CLI transcripts 2026-10-16: 85/85 leaks). That word is part of the leak, not
# prose, so the optional ``^word\n`` prefix is consumed with it — otherwise the
# user sees a dangling ``count`` (MULTILINE only affects that ``^``).
#
# LINEAR-TIME (ReDoS fix): the strip is NOT one regex. A single regex with a
# lazy ``.*?‹/parameter>`` body rescans to the end of the text for every
# ``‹invoke`` whose parameter never closes — quadratic (measured: 120KB of
# crafted text = 1.4s, 240KB = 5.1s, synchronously on the event loop, freezing
# every session). ``_find_leak_spans`` matches the same shape with bounded
# regexes plus ``str.find`` for the body, and remembers when no ``‹/parameter>``
# remains so a failed body search happens at most once per text.
_TOOL_CALL_LEAK_HEAD_RE = re.compile(
    r'(?:^[ \t]*[A-Za-z]{2,16}[ \t]*\r?\n[ \t]*)?<invoke\s+name="[^"]*"\s*>',
    re.MULTILINE,
)
_TOOL_CALL_LEAK_PARAM_OPEN_RE = re.compile(r'\s*<parameter\s+name="[^"]*"\s*>')
_TOOL_CALL_LEAK_TAIL_RE = re.compile(r'\s*(?:</invoke>)?')
_PARAM_CLOSE = "</parameter>"


def _find_leak_spans(masked: str) -> list[tuple[int, int]]:
    """Return non-overlapping (start, end) spans of raw leaks in ``masked``.

    Same shape the old single regex matched: optional one-word opener line,
    ``‹invoke name="X">``, any number of COMPLETE ``‹parameter ...>…‹/parameter>``
    bodies (lazy: up to the first ``‹/parameter>``), optional ``‹/invoke>``.
    Runs in O(len(masked)).
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    no_close_left = False
    while True:
        # search(pos) resumes exactly where the previous leak ended, like
        # re.subn does (``^`` still only matches at a real line start).
        head = _TOOL_CALL_LEAK_HEAD_RE.search(masked, pos)
        if head is None:
            break
        end = head.end()
        while not no_close_left:
            param = _TOOL_CALL_LEAK_PARAM_OPEN_RE.match(masked, end)
            if param is None:
                break
            close = masked.find(_PARAM_CLOSE, param.end())
            if close == -1:
                no_close_left = True  # no later search can succeed either
                break
            end = close + len(_PARAM_CLOSE)
        end = _TOOL_CALL_LEAK_TAIL_RE.match(masked, end).end()
        spans.append((head.start(), end))
        pos = end
    return spans

# STREAM onset: a raw ``‹invoke name="`` at the START of a line, used on the
# live text_delta buffer (before the body is complete) to stop forwarding the
# leak to the UI while it streams. Line-anchored + applied to masked text only.
_TOOL_CALL_LEAK_ONSET_RE = re.compile(r'^[ \t]*<invoke\s+name="', re.MULTILINE)

# Sentinel used to MASK fenced / inline-code spans while operating on the raw
# text, then restore them verbatim. \x00 cannot appear in normal assistant text,
# so collisions are impossible.
_MASK_OPEN = "\x00"
_MASK_CLOSE = "\x00"


def _mask_doc_contexts(text: str) -> tuple[str, list[str]]:
    """Replace fenced (```...```) and inline (`...`) code spans with opaque
    sentinels, returning (masked_text, spans). The SINGLE SOURCE of the RP42
    orthogonal signal ("documentation lives in code spans; a real leak is raw
    unfenced text") — both ``detect_tool_call_leak`` and ``strip_tool_call_leak``
    consume it, so the fence/inline-strip rule is defined in exactly one place.

    The returned spans are restored by substituting ``\\x00<index>\\x00`` back to
    ``spans[index]`` — so a legitimate in-code reference to ``‹invoke ...>`` is
    preserved byte-for-byte, while a RAW leak outside any code span is exposed to
    the matcher.

    ``\\x00`` is the masking sentinel, so any PRE-EXISTING ``\\x00`` in the input is
    scrubbed FIRST — otherwise a literal ``\\x00<digit>\\x00`` byte sequence the model
    emitted would be reassembled by ``_restore_doc_contexts`` into one of the masked
    spans, silently duplicating/moving content into the resume prompt. ``\\x00`` (NUL)
    has no legitimate meaning in assistant prose, so dropping it is harmless and makes
    the round-trip collision structurally impossible. (NUL is absent in the
    overwhelming case, so the replace is a cheap no-op.)
    """
    text = text.replace("\x00", "")
    spans: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        spans.append(m.group(0))
        return f"{_MASK_OPEN}{len(spans) - 1}{_MASK_CLOSE}"

    # Single alternation pass (fence branch first) — see _TOOL_CALL_CODE_SPAN_RE.
    # NEVER split this back into two sequential .sub() calls (bug 2a).
    masked = _TOOL_CALL_CODE_SPAN_RE.sub(_stash, text)
    return masked, spans


def _restore_doc_contexts(masked: str, spans: list[str]) -> str:
    """Inverse of ``_mask_doc_contexts`` — restore every masked span verbatim."""
    if not spans:
        return masked
    return re.sub(
        r"\x00(\d+)\x00",
        lambda m: spans[int(m.group(1))],
        masked,
    )


def detect_tool_call_leak(text: str) -> bool:
    """Return True if ``text`` contains leaked tool-call XML in the text channel.

    Detects the harness tool-call BODY — ``‹invoke name="...">`` immediately
    followed by ``‹parameter name=`` or ``‹/invoke>`` — emitted as RAW assistant
    text instead of a real tool_use block.

    False-positive-safe on documentation: fenced code blocks and inline-backtick
    spans (where assistant prose shows the syntax) are masked before matching
    (via the shared ``_mask_doc_contexts`` — the single source of the fence/inline
    rule), so a turn that merely *explains* the leak shape is not flagged. This
    matters because this is a meta-heavy codebase whose own turns routinely
    discuss this exact syntax — a false positive would wrongfully kill a
    legitimate turn.

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
    # Mask documentation contexts so prose that SHOWS the syntax is ignored;
    # a real leak is raw unmasked text. (Masking, not deletion, keeps the shared
    # helper usable by the strip path which must restore the spans.)
    masked, _spans = _mask_doc_contexts(text)
    return _TOOL_CALL_LEAK_RE.search(masked) is not None


def strip_tool_call_leak(text: str) -> str:
    """Return ``text`` with any RAW tool-call leak fragments removed, preserving
    legitimate fenced/inline-code references byte-for-byte.

    This is the WRITE-side companion to ``detect_tool_call_leak`` (which is a
    read-only bool). It exists because leaked ``‹invoke ...>`` text that reaches
    data.db gets spliced back into a resumed session's system prompt verbatim
    (``context_injector.build_resume_context``), re-inducing the model to leak
    again — a self-reinforcing loop. Cleaning the fragment at the point it enters
    the prompt cuts that loop.

    Two differences from the detector, both deliberate:

    1. **Wider anchor.** It uses ``_find_leak_spans`` (linear time), which matches a
       bare ``‹invoke name="X">`` even when the body is TRUNCATED (no following
       ``‹parameter`` / ``‹/invoke>``) — the shape a mid-stream kill leaves in the
       DB. The detector's narrower full-body regex would miss exactly the
       persisted residue this function must clean.
    2. **Per-MATCH, not per-line.** Fenced/inline spans are MASKED first, the leak
       is removed from the raw remainder, then the spans are restored verbatim —
       so a text block that contains BOTH a legitimate `` `‹invoke ...>` `` inline
       reference AND a raw leak keeps the reference and drops only the leak. A
       line-granular ``grep -v``-style exclusion would drop the whole line and
       lose the legitimate reference (the per-line false-negative class recorded
       in IMPROVEMENT.md 2026-08-31).

    Pure function (str -> str). Idempotent: cleaning already-clean text (or text
    whose only ``‹invoke>`` references are in code spans) returns it unchanged.

    Args:
        text: assistant text that may contain a raw leak fragment.

    Returns:
        The text with raw leak fragments (and their one-word opener line)
        REMOVED — no placeholder: a leak is a failed tool call, not content, so
        nothing of it is shown to the user or fed back on resume. Fenced /
        inline-code references preserved verbatim.
    """
    if not text or "<invoke" not in text:
        return text  # fast path — the overwhelming majority of blocks
    masked, spans = _mask_doc_contexts(text)
    leaks = _find_leak_spans(masked)
    if not leaks:
        # Nothing matched outside code spans — restore and return unchanged
        # (preserves the original byte-for-byte, incl. the masked spans).
        return _restore_doc_contexts(masked, spans)
    parts, last = [], 0
    for start, end in leaks:
        parts.append(masked[last:start])
        last = end
    parts.append(masked[last:])
    return _restore_doc_contexts("".join(parts), spans)


# Raised by the orchestrator on a repeated leak; send() catches it and runs the
# one-shot transcript-clean restart. Deliberately NOT a retriable pattern.
LEAK_RESTART_MARKER = "Tool-call leak poison restart"
# Suffix on the same marker when the restart is for a glitch prefix (the tool
# call itself ran fine, so the continuation prompt must not say it failed).
LEAK_RESTART_KIND_PREFIX = "kind=prefix"


# ── Glitch-prefix shape (second shape of the same model glitch) ──────────────
# At a text→tool_use transition the model sometimes corrupts the call opener into
# a word and leaves it at the start of a narration line ("core 跑测试…",
# "count 显示 2 paused", "core GREEN change 3") or alone on a line ("core"). The
# real tool call still runs. Once such a line is in the CLI history the model
# copies it (measured: ~31% of later calls vs ~0.5% onset), so it is stripped
# from display/persist and cleaned from the transcript.
#
# Matching is deliberately narrow, all on code-span-masked text:
# - only these lowercase words, at a line start;
# - prefix form: word, optional colon/comma, then a CJK char or an UPPERCASE
#   ASCII letter (English prose starts a sentence with "Count", not "count X");
# - bare form: the word alone on its line.
# The word list is the single source shared with
# scripts/tool_call_glitch_metrics.py; add a new word here when the metric shows one.
GLITCH_WORDS = ("count", "court", "core", "card", "care", "call", "coute")
_GLITCH_ALT = "|".join(GLITCH_WORDS)
_GLITCH_PREFIX_RE = re.compile(
    rf"(?m)^([ \t]*)(?:{_GLITCH_ALT})[ \t]*[:：,，]?[ \t]*(?=[\u4e00-\u9fffA-Z])"
)
_GLITCH_BARE_LINE_RE = re.compile(rf"(?m)^[ \t]*(?:{_GLITCH_ALT})[ \t]*(?:\r?\n|\Z)")


def _has_glitch_word(text: str) -> bool:
    """Cheap pre-check before masking (hot path: every assistant text block)."""
    return any(w in text for w in GLITCH_WORDS)


def detect_glitch_prefix(text: str) -> bool:
    """True if ``text`` has a glitch-prefix or bare glitch-word line outside code."""
    if not text or not _has_glitch_word(text):
        return False
    masked, _spans = _mask_doc_contexts(text)
    return bool(_GLITCH_PREFIX_RE.search(masked) or _GLITCH_BARE_LINE_RE.search(masked))


def strip_glitch_prefix(text: str) -> str:
    """Remove glitch prefix words and bare glitch-word lines; keep everything else.

    Code spans are masked and restored verbatim. Idempotent. A text that was only
    a bare word becomes "" (callers that cannot store an empty block substitute a
    filler).
    """
    if not text or not _has_glitch_word(text):
        return text
    masked, spans = _mask_doc_contexts(text)
    cleaned = _GLITCH_BARE_LINE_RE.sub("", masked)
    cleaned = _GLITCH_PREFIX_RE.sub(r"\1", cleaned)
    if cleaned == masked:
        return _restore_doc_contexts(masked, spans)
    # Removing a bare line can leave 3+ newlines; keep paragraphs tidy.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip("\n") if cleaned.strip() else ""
    return _restore_doc_contexts(cleaned, spans)


def find_stream_leak_onset(buf: str) -> int:
    """Return the offset in ``buf`` where a raw tool-call leak STARTS, else -1.

    For the live text_delta path: ``buf`` is the text streamed so far for one
    content block, so the leak body may still be incomplete (the full-body
    ``detect_tool_call_leak`` cannot fire yet). The onset is a line-start
    ``‹invoke name="`` outside code spans; text inside an unclosed ``` fence
    (still streaming) is treated as documentation, never as a leak.

    A false positive only hides provisional streamed text: the authoritative
    ``assistant`` event that follows replaces it on the frontend.
    """
    if not buf or "<invoke" not in buf:
        return -1
    # An odd ``` count = a fence is still open: everything after its opener is
    # documentation-in-progress. Cut on the RAW text (the inline-code branch of
    # the mask would otherwise eat the lone opener as an empty `` span).
    if buf.count("```") % 2 == 1:
        buf = buf[: buf.rfind("```")]
    masked, spans = _mask_doc_contexts(buf)
    m = _TOOL_CALL_LEAK_ONSET_RE.search(masked)
    if m is None:
        return -1
    return len(_restore_doc_contexts(masked[: m.start()], spans))


def _build_error_event(
    code: str,
    message: str,
    *,
    detail: Optional[str] = None,
    suggested_action: Optional[str] = None,
    terminal: bool = False,
) -> dict:
    """Build a sanitized SSE error event dict.

    When ``settings.debug`` is True the full *detail* string (typically a
    Python traceback) is included verbatim.  In production mode the detail
    is stripped of tracebacks, file paths with line numbers, and library
    version strings so that internal implementation details are never
    leaked to the frontend.

    ``terminal=True`` marks the error as PERMANENTLY undeliverable — a
    non-retriable failure (the caller has already run it past
    ``_is_retriable_error``) for which re-sending the SAME request will only
    reproduce the SAME error. The pending-drain finalize path reads this flag
    to STOP rolling the message back to pending (which would re-drive it on
    every IDLE edge forever). It does NOT affect how the event is shown to the
    user — the code/message/error fields are unchanged, so a terminal error is
    surfaced exactly like any other, never silently dropped.
    """
    event: dict = {"type": "error", "code": code, "message": message, "error": message}
    if terminal:
        event["terminal"] = True
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
    # psutil reads the environment with sysctl (no fork, ~0.2 ms). The `ps eww`
    # below forks this large process once per pid: the orphan reaper, which asks
    # for every pgrep match, ran past its 30s budget 11 times on 2026-10-21 and
    # skipped the cycle. A denied / vanished process keeps the old answer (None
    # when unreadable), so the fail-safe rules of the callers are unchanged.
    try:
        import psutil
        env = psutil.Process(pid).environ()
        val = env.get("SWARMAI_OWNER_PID")
        return int(val) if val and val.isdigit() else None
    except (ImportError, OSError, ValueError):
        pass  # fall through to ps / procfs
    except Exception as exc:  # noqa: BLE001 — psutil.AccessDenied / NoSuchProcess / ZombieProcess
        if type(exc).__name__ in ("NoSuchProcess", "ZombieProcess"):
            return None
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
