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
  3. Single-tool repetition (ACTIVE only): one call identity (tool, input and result)
     appears ≥5 times

What a call is: a call's identity is (tool, input hash) until its result arrives
(``record_tool_result``, by tool_use_id), then (tool, input hash + ":" + a fingerprint of
the result with durations, clock times, sizes and ids removed). Every detector and the
rolling baseline compare that identity, so one resumable command whose output keeps
changing is progress, while the same command returning the same output is still a loop.
A call whose result has not arrived (the one being checked) or never arrives is compared
on tool and input, as before. A result for a call the guard did not record (a
sub-agent's) is ignored.

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

from model_registry import DEFAULT_CONTEXT_WINDOW

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
"""A single call identity (tool, input hash and, once it arrived, result) repeated this many times → loop."""

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

_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "Read", "Grep", "ListDir", "Glob",
    "ReadFile", "GrepSearch", "ListDirectory",
    "FileSearch", "ReadCode", "ReadMultipleFiles",
})
"""Tools that only read data — tolerate longer consecutive runs before warning."""

_CONSEC_SOFT_READONLY: int = 5
"""Consecutive identical read-only (tool, hash) pairs to trigger SOFT_WARN."""

_CONSEC_HARD_READONLY: int = 8
"""Consecutive identical read-only (tool, hash) pairs to trigger HARD_WARN."""

_CONSEC_KILL_READONLY: int = 10
"""Consecutive identical read-only (tool, hash) pairs to trigger KILL."""

_GRACE_WINDOW: int = 3
"""Calls after an escalation event before the next escalation is allowed."""

_RESULT_EDGE_CHARS: int = 4000
"""A result fingerprint reads the first and last this many characters of the result."""

_PENDING_MAX: int = 256
"""Calls awaiting a result kept at most (a result that never arrives is dropped first)."""

# What in a result changes without the work moving: temporary paths (pytest's numbered
# tmp_path, tempfile names), addresses and hex ids (0x7f.., git and object hashes),
# UUIDs, host:port pairs and port numbers, ISO timestamps, epoch timestamps (a 10-digit
# second count from 1.6e9 or its 13-digit millisecond form, or a 9-13 digit number after
# ts= / time= / epoch=), random seeds (pytest-randomly prints a new one every run), clock
# times, durations, memory sizes, process and session ids. Any other number (a count of
# cases done, rows processed, an epoch counter) is kept, so it reads as progress; the one
# exception is a bare count between 1.6e9 and 2e9, which reads as a timestamp. Paths go
# first: a temp path holds digits the later patterns would otherwise cut into pieces.
_RESULT_NOISE = [re.compile(p, re.I) for p in (
    r"(?:/private)?/(?:tmp|var/folders)/\S*",
    r"\bpytest-(?:of-\S+?|\d+)\b",
    r"\btmp[\w.-]{6,}",
    r"\b0x[0-9a-f]+\b",
    r"(?:\blocalhost|\b\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-f]*:[0-9a-f:]*\]):\d+",
    r"\bport\s*[=:#]?\s*\d+",
    r"(?<![.\w])(?:ts|time|timestamp|epoch)\s*[=:]\s*\d{9,13}(?:\.\d+)?\b",
    r"\b(?:random(?:ly)?[_ -]?)?seed\s*[=:]?\s*\d+",
    r"\b1[6-9]\d{8}(?:\d{3})?\b",
    r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?",
    r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b",
    r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds?|m|mins?|minutes?|h|hrs?|hours?)\b",
    r"\b\d+(?:\.\d+)?\s?(?:[kmgt]i?b)\b",
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b",
    r"\b(?:pid|session(?:[_ -]?id)?)\s*[=:#]?\s*\S+",
)]


# NOTE: A per-turn raw tool-call count/duration budget (Root 2 / AC6) was
# removed 2026-06-21. Counting total calls per turn cannot distinguish a
# legitimate high-diversity deep-research turn (many *distinct* reads/searches)
# from a runaway loop — it false-fired on healthy work and the resulting
# interrupt poisoned the subprocess (instant error_during_execution on reuse).
# Runaways are caught by the right signal — lack of progress — via the
# consecutive-repeat (Layer 0) and diversity-stall (Layer 1) detectors below,
# which fire on repetition/low information gain, not volume.


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
        # Loop detections already logged this turn (_log_detection_once)
        self._logged_detections: set = set()
        # Phase
        self._phase: GuardPhase = GuardPhase.PASSIVE

        # Escalation
        self._escalation: EscalationLevel = EscalationLevel.MONITORING

        # Context tracking
        self._context_pct: float = 0.0
        self._context_tokens: int = 0
        self._prev_context_pct: float = 0.0  # For heuristic compaction detection
        self._context_window: int = DEFAULT_CONTEXT_WINDOW

        # Tool tracking — sets and sequences
        self._pre_compaction_set: set[tuple[str, str]] = set()
        # (tool, input hash) of every pre-compaction call, and of those whose result had
        # not arrived at compaction; rebuilt only when _pre_compaction_set is replaced
        self._pre_index_of: set | None = None
        self._pre_bases: set[tuple[str, str]] = set()
        self._pre_bare: set[tuple[str, str]] = set()
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

        # Calls awaiting their result, by tool_use_id: (tool, input hash), and the ids
        # of the entries of _recent_calls / _post_compaction_sequence (same order)
        self._pending: dict[str, tuple[str, str]] = {}
        self._recent_ids: list[Optional[str]] = []
        self._post_ids: list[Optional[str]] = []

        # Grace period — pause between escalation levels
        self._grace_calls_remaining: int = 0

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

    @staticmethod
    def _classify_tool(tool_name: str) -> str:
        """Return 'read_only' if tool is in _READ_ONLY_TOOLS, else 'write_execute'."""
        return "read_only" if tool_name in _READ_ONLY_TOOLS else "write_execute"

    @staticmethod
    def _get_consec_thresholds(tool_name: str) -> tuple[int, int, int]:
        """Return (soft, hard, kill) consecutive thresholds for the given tool.

        Read-only tools get higher thresholds (5, 8, 10) to tolerate normal
        code research patterns. Write/execute tools keep the stricter (3, 5, 7).
        """
        if tool_name in _READ_ONLY_TOOLS:
            return (_CONSEC_SOFT_READONLY, _CONSEC_HARD_READONLY, _CONSEC_KILL_READONLY)
        return (_CONSEC_SOFT, _CONSEC_HARD, _CONSEC_KILL)

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
        self, tool_name: str, tool_input: dict | str | None,
        tool_use_id: Optional[str] = None,
    ) -> None:
        """Record a tool call for loop detection and work summary.

        Hashes the input, creates a ToolRecord with the first 200 chars
        of JSON-serialized input as input_detail, appends to tracking
        structures. With *tool_use_id*, ``record_tool_result`` can later give
        the call its result-aware identity.
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
            self._post_ids.append(tool_use_id)

            # Add to rolling baseline set (used for heuristic compaction detection)
            self._rolling_baseline_set.add(pair)

            # Append full record for work summary
            self._tool_records.append(record)

            # Sliding window for diversity-based stall detection
            self._recent_calls.append(pair)
            self._recent_ids.append(tool_use_id)
            if len(self._recent_calls) > _STALL_ESCALATION_WINDOW:
                self._recent_calls = self._recent_calls[-_STALL_ESCALATION_WINDOW:]
                self._recent_ids = self._recent_ids[-_STALL_ESCALATION_WINDOW:]

            # Consecutive identical call tracking: the run of earlier calls with this
            # tool and input that share one result (a call without a result matches)
            self._last_pair = pair
            self._consec_count = self._streak(pair)

            if tool_use_id:
                self._pending[tool_use_id] = pair
                while len(self._pending) > _PENDING_MAX:
                    self._pending.pop(next(iter(self._pending)))

        except Exception:
            logger.exception("compaction_guard.record_tool_call failed")

    def _streak(self, pair: tuple[str, str]) -> int:
        """How many of the latest calls in the window repeat *pair*: same tool and input,
        and, among those whose result arrived, one and the same result."""
        tool, inp = pair
        streak, ref = 0, None
        for t, h in reversed(self._recent_calls):
            base, _sep, fp = h.partition(":")
            if t != tool or base != inp:
                break
            if fp:
                if ref is None:
                    ref = fp
                elif fp != ref:
                    break
            streak += 1
        return streak

    @staticmethod
    def _fingerprint(content: object, is_error: bool = False) -> str:
        """A short hash of a tool result with what changes without progress removed."""
        if isinstance(content, list):
            parts = []
            for block in content:
                text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
                parts.append(text if isinstance(text, str) else str(block))
            text = "\n".join(parts)
        else:
            text = "" if content is None else str(content)
        if len(text) > 2 * _RESULT_EDGE_CHARS:
            text = text[:_RESULT_EDGE_CHARS] + "\n" + text[-_RESULT_EDGE_CHARS:]
        for pattern in _RESULT_NOISE:
            text = pattern.sub("#", text)
        text = ("error:" if is_error else "") + " ".join(text.split())
        return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()[:12]

    def record_tool_result(self, tool_use_id: Optional[str], content: object,
                           is_error: bool = False) -> None:
        """Give a recorded call its result-aware identity. A result for a call this
        guard did not record (a sub-agent's, or one dropped by a reset) is ignored."""
        try:
            pair = self._pending.pop(tool_use_id, None) if tool_use_id else None
            if pair is None:
                return
            keyed = (pair[0], f"{pair[1]}:{self._fingerprint(content, bool(is_error))}")
            for ids, calls in ((self._recent_ids, self._recent_calls),
                               (self._post_ids, self._post_compaction_sequence)):
                for i in range(len(ids) - 1, -1, -1):
                    if ids[i] == tool_use_id:
                        calls[i] = keyed
                        break
            if not any(p == pair for p in self._pending.values()):
                self._rolling_baseline_set.discard(pair)
            self._rolling_baseline_set.add(keyed)
        except Exception:
            logger.exception("compaction_guard.record_tool_result failed")

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
                window = DEFAULT_CONTEXT_WINDOW  # Safe fallback

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
            self._post_ids = []

            logger.info(
                "compaction_guard.activated baseline_size=%d context_pct=%.1f",
                len(self._pre_compaction_set),
                self._context_pct,
            )
        except Exception:
            logger.exception("compaction_guard.activate failed")

    # ── _detect_loop() ───────────────────────────────────────────

    def _log_detection_once(self, key, msg: str, *args) -> None:
        """Log a loop detection the first time *key* is seen: _detect_loop runs on
        every later call and re-detects the same pattern (388 identical lines by
        2026-10-21)."""
        seen = self._logged_detections
        if key in seen:
            return
        seen.add(key)
        logger.warning(msg, *args)

    @staticmethod
    def _resolved(seq: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """*seq* with each call still awaiting its result read as the latest earlier
        call of the same tool and input whose result arrived (as ``_streak`` does), so
        the call in flight is not a different call from the ones it repeats."""
        latest: dict[tuple[str, str], tuple[str, str]] = {}
        out: list[tuple[str, str]] = []
        for tool, h in seq:
            base, sep, _fp = h.partition(":")
            if sep:
                latest[(tool, base)] = (tool, h)
                out.append((tool, h))
            else:
                out.append(latest.get((tool, base), (tool, h)))
        return out

    def _pre_index(self) -> tuple[set, set]:
        """The bases of the pre-compaction set, and its entries without a result."""
        if self._pre_index_of is not self._pre_compaction_set:
            self._pre_index_of = self._pre_compaction_set
            self._pre_bases = {(t, h.partition(":")[0]) for t, h in self._pre_compaction_set}
            self._pre_bare = {(t, h) for t, h in self._pre_compaction_set if ":" not in h}
        return self._pre_bases, self._pre_bare

    def _detect_loop(self) -> bool:
        """Detect loop via set-overlap and single-tool repetition.

        Returns True if a loop pattern is detected. Caches the detection
        reason in ``_last_pattern_desc`` for ``_build_pattern_description()``.
        """
        seq = self._resolved(self._post_compaction_sequence)
        total = len(seq)
        self._last_pattern_desc = ""

        # Set-overlap detection. When either side of a match has no result (a call still
        # waiting after compaction, or one still waiting when compaction came), the two
        # overlap by tool and input alone.
        if total >= _MIN_POST_COMPACTION_CALLS:
            pre_bases, pre_bare = self._pre_index()
            overlap_count = sum(
                1 for pair in seq
                if pair in self._pre_compaction_set
                or (":" not in pair[1] and pair in pre_bases)
                or (pair[0], pair[1].partition(":")[0]) in pre_bare
            )
            if overlap_count / total > _OVERLAP_THRESHOLD:
                self._log_detection_once("set_overlap",
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
                self._log_detection_once((most_common_pair, "single"),
                    "compaction_guard.loop_detected single_tool_repeat "
                    "tool=%s hash=%s count=%d",
                    most_common_pair[0],
                    most_common_pair[1],
                    most_common_count,
                )
                self._last_pattern_desc = (
                    f"Tool {most_common_pair[0]} called {most_common_count} times "
                    f"with identical input and result"
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
        identical calls for write/execute tools. Designed for the pytest
        dead-loop pattern where the agent re-runs the same command 6-8
        times at <10% context.

        Read-only tools get higher thresholds (5/8/10) to tolerate normal
        code research patterns (reading files, grepping patterns).

        Returns the escalation level this detector alone would recommend.
        Does NOT mutate self._escalation — caller decides.

        Thresholds (write/execute): 3 → SOFT_WARN, 5 → HARD_WARN, 7 → KILL.
        Thresholds (read-only):     5 → SOFT_WARN, 8 → HARD_WARN, 10 → KILL.
        """
        if self._last_pair is None:
            return EscalationLevel.MONITORING

        tool_name = self._last_pair[0]
        soft, hard, kill = self._get_consec_thresholds(tool_name)

        if self._consec_count >= kill:
            return EscalationLevel.KILL
        if self._consec_count >= hard:
            return EscalationLevel.HARD_WARN
        if self._consec_count >= soft:
            return EscalationLevel.SOFT_WARN
        return EscalationLevel.MONITORING

    # ── check() ──────────────────────────────────────────────────

    def check(self) -> EscalationLevel:
        """Check all layers and return the current escalation level.

        Detection strategy (priority order):

        0. Consecutive identical calls (all phases, no minimum window):
           - 3 consecutive identical calls (tool, input and, once it arrived,
             result) → SOFT_WARN
           - 5 consecutive → HARD_WARN
           - 7 consecutive → KILL
           Catches small loops (6-8 calls) at low context, no compaction needed.

        1. Diversity-based stall (all phases):
           - 20 calls with <30% diversity (< 6 unique) → SOFT_WARN
           - 40 calls with <30% diversity (< 12 unique) → HARD_WARN
           - PASSIVE phase caps at HARD_WARN (no KILL without compaction evidence)

        2. Context threshold + loop detection (ACTIVE only):
           - Set-overlap >60% matching baseline → SOFT_WARN
           - Single-tool repetition ≥5 (same tool, input and result) → SOFT_WARN
           - Capped at SOFT_WARN: post-compaction re-reads are expected, so
             this layer never interrupts the self-continuing turn

        The dynamic threshold scales with context window size:
        200K → 85%, 1M → 40% (via _compute_activation_pct).

        Wraps in try/except — on error, returns MONITORING. Guard must
        never block streaming.
        """
        try:
            # Already at KILL: stay there
            if self._escalation == EscalationLevel.KILL:
                return EscalationLevel.KILL

            # Grace period check — BEFORE any detector
            if self._grace_calls_remaining > 0:
                self._grace_calls_remaining -= 1
                return self._escalation  # Hold at current level

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
                    self._grace_calls_remaining = _GRACE_WINDOW
                    self._last_pattern_desc = (
                        f"Same {tool_name} command with the same result repeated "
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
                        self._grace_calls_remaining = _GRACE_WINDOW
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
                        self._grace_calls_remaining = _GRACE_WINDOW
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
                # WARN-ONLY after compaction (2026-10-17). Re-reading files and
                # re-running checks after a compaction is EXPECTED — the summary
                # dropped their detail — so baseline overlap is a weak loop signal.
                # Escalating it to HARD_WARN/KILL made the orchestrator interrupt()
                # the self-continuing turn, i.e. compaction stopped the work. This
                # layer now caps at SOFT_WARN (a hint, never an interrupt). Real
                # loops are still stopped by Layer 0 (identical consecutive calls)
                # and Layer 1 (diversity stall), which run in every phase.
                if self._escalation != EscalationLevel.MONITORING:
                    # Already warned. Report only this layer's capped signal —
                    # returning a stored Layer 0/1 HARD_WARN from here would let
                    # overlap alone re-trigger an interrupt on every later call.
                    return EscalationLevel.SOFT_WARN
                new_level = EscalationLevel.SOFT_WARN

                logger.warning(
                    "compaction_guard.escalation old=%s new=%s context_pct=%.1f",
                    self._escalation.value,
                    new_level.value,
                    self._context_pct,
                )
                self._escalation = new_level
                self._grace_calls_remaining = _GRACE_WINDOW
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

        Clears post-compaction sequence and grace counter but preserves
        escalation level, phase, pre-compaction baseline, context_pct,
        and tool_records.
        """
        try:
            self._post_compaction_sequence = []
            self._post_ids = []
            self._last_pattern_desc = ""
            self._grace_calls_remaining = 0
            self._logged_detections = set()
        except Exception:
            logger.exception("compaction_guard.reset failed")

    def reset_all(self) -> None:
        """Full reset (subprocess respawn): back to PASSIVE, clear everything.

        Resets phase to PASSIVE, escalation to MONITORING, context to zero,
        and clears all tracking collections including grace state.
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
            self._context_window = DEFAULT_CONTEXT_WINDOW
            self._last_pair = None
            self._consec_count = 0
            self._pending = {}
            self._recent_ids = []
            self._post_ids = []
            self._grace_calls_remaining = 0
            self._logged_detections = set()
        except Exception:
            logger.exception("compaction_guard.reset_all failed")

