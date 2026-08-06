"""Memory Decay — Ebbinghaus exponential forgetting + Hebbian potentiation.

Inspired by MemPalace v3.4.0's dynamics system. Implements usage-based decay
scoring for MEMORY.md entries so that distillation/pruning decisions are
informed by actual access patterns rather than age alone.

Mathematical model:
- Ebbinghaus: score = exp(-days_since_last_ref / stability)
- Hebbian: each co-access event strengthens the connection
- Cepeda spacing effect: distributed sessions > massed burst
- Floor: entries never fully vanish (STRENGTH_FLOOR = 0.05)

Public API:
    STRENGTH_FLOOR          — Minimum decay score (entries never fully forgotten)
    STABILITY_BASE          — Initial decay resistance for new entries
    MAX_STABILITY           — Cap on stability growth
    EntryDecayInfo          — Dataclass for per-entry decay state
    compute_decay_score     — Ebbinghaus decay with stability modifier
    compute_stability       — Stability from ref count + spacing
    get_archive_candidates  — Filter entries below threshold + min age
    scan_session_for_memory_refs — Detect MEMORY IDs in session messages
    bump_entry_references   — Update inline metadata comments
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

# ── Constants ────────────────────────────────────────────────────────────────

STRENGTH_FLOOR = 0.05       # Never fully forgotten (MemPalace: same value)
STABILITY_BASE = 5.0        # Initial decay resistance (days)
SPACING_BONUS = 0.3         # Stability gain per distinct session (Cepeda effect)
VOLUME_FACTOR = 0.2         # Diminishing log(1+ref_count) contribution
MAX_STABILITY = 10.0        # Cap so no entry becomes immortal

# Sections whose entries are immune to decay/archival. Derived from the
# MEMORY_SECTIONS SSoT (evergreen flag) rather than hardcoded — the old literal
# {"Key Decisions", "COE Registry"} referenced "Key Decisions", a section
# removed in PRI01, so it protected nothing real (R3 write-governance fix). The
# SSoT deliberately makes Decisions NON-evergreen (decisions decay); evergreen =
# {Principles, Corrections, COE Registry, Open Threads, Standing Preferences}.
from core.ddd_entry_lifecycle import MEMORY_EVERGREEN_SECTIONS as PERMANENT_SECTIONS

# Regex for MEMORY entry IDs (KD01, LL03, RC15, COE02, OT01)
_ENTRY_ID_RE = re.compile(r"\b((?:KD|LL|RC|COE|OT)\d{2,3})\b")

# Metadata comment format (extends ddd_entry_lifecycle convention)
_META_RE = re.compile(
    r"^(\s*)<!-- ref:(\d+) \| last:([\w\-]+) \| decay:(\w+) \| sessions:(\d+) -->$",
    re.MULTILINE,
)

# Entry header pattern (matches MEMORY.md bullet format)
# Aligned with _ENTRY_ID_RE to prevent asymmetric matching
_ENTRY_HEADER_RE = re.compile(
    r"^- \[((?:KD|LL|RC|COE|OT)\d{2,3})\]",
    re.MULTILINE,
)


# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class EntryDecayInfo:
    """Per-entry decay state for scoring and filtering."""

    entry_id: str
    ref_count: int = 0
    sessions_referenced: int = 0
    last_referenced: Optional[date] = None
    created: Optional[date] = None
    section: str = ""


# ── Core Math ────────────────────────────────────────────────────────────────


def compute_stability(ref_count: int, sessions_referenced: int) -> float:
    """Compute decay resistance from access patterns.

    Spacing effect (Cepeda): sessions_referenced contributes linearly (strong).
    Volume: ref_count contributes logarithmically (diminishing returns).

    Higher stability = slower exponential decay.
    """
    spacing = sessions_referenced * SPACING_BONUS
    volume = math.log1p(ref_count) * VOLUME_FACTOR
    return min(MAX_STABILITY, STABILITY_BASE + spacing + volume)


def compute_decay_score(
    ref_count: int,
    sessions_referenced: int,
    last_referenced: Optional[date],
    created: Optional[date],
    today: date,
) -> float:
    """Ebbinghaus exponential decay with stability modifier.

    Returns a score in [STRENGTH_FLOOR, 1.0]:
    - 1.0 = just referenced (fully alive)
    - ~0.5 = at half-life
    - 0.05 = floor (dim but queryable)

    Formula: score = max(FLOOR, exp(-days_since / stability))
    """
    # Determine days since last meaningful access
    if last_referenced is not None:
        days_since = (today - last_referenced).days
    elif created is not None:
        days_since = (today - created).days
    else:
        # No date info at all — treat as very old
        days_since = 365

    # Clamp: if days_since <= 0, score is 1.0 (just referenced)
    if days_since <= 0:
        return 1.0

    stability = compute_stability(ref_count, sessions_referenced)
    score = math.exp(-days_since / stability)
    return max(STRENGTH_FLOOR, score)


# ── Archive Candidates ───────────────────────────────────────────────────────


def get_archive_candidates(
    entries: list[EntryDecayInfo],
    today: date,
    score_threshold: float = 0.1,
    min_age_days: int = 60,
) -> list[EntryDecayInfo]:
    """Return entries that are archive candidates.

    Criteria (ALL must be true):
    1. Decay score < score_threshold
    2. Entry age > min_age_days
    3. Entry NOT in a PERMANENT section
    """
    candidates = []
    for entry in entries:
        # Permanent sections are immune
        if entry.section in PERMANENT_SECTIONS:
            continue

        # Check age
        if entry.created is not None:
            age_days = (today - entry.created).days
            if age_days < min_age_days:
                continue
        # If no creation date, can't verify age → skip (conservative)
        else:
            continue

        # Compute decay score
        score = compute_decay_score(
            ref_count=entry.ref_count,
            sessions_referenced=entry.sessions_referenced,
            last_referenced=entry.last_referenced,
            created=entry.created,
            today=today,
        )

        if score < score_threshold:
            candidates.append(entry)

    return candidates


# ── Session Scanning ─────────────────────────────────────────────────────────


def scan_session_for_memory_refs(
    messages: list[dict],
    entry_ids: set[str],
) -> set[str]:
    """Scan session messages for MEMORY entry identifiers.

    Looks for patterns like KD01, LL08, RC03, COE05, OT01 in message content.
    Only returns IDs that exist in the provided entry_ids set.
    """
    found: set[str] = set()
    for msg in messages:
        content = msg.get("content", "")
        # Handle list-type content blocks (Anthropic Messages API format)
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if not content or not isinstance(content, str):
            continue
        matches = _ENTRY_ID_RE.findall(content)
        for match in matches:
            if match in entry_ids:
                found.add(match)
    return found


# ── Metadata Bumping ─────────────────────────────────────────────────────────


def bump_entry_references(
    memory_content: str,
    referenced_ids: set[str],
    today: date,
) -> str:
    """Update ref:N, last:date, sessions:N metadata for referenced entries.

    For each referenced entry:
    1. If metadata comment exists anywhere after header → update it in place
    2. If no metadata comment → insert one directly after the header line

    Bumping always resets decay state to 'active' (referenced = alive).

    Note: Callers must serialize access to MEMORY.md (e.g., via locked_write)
    to prevent concurrent bump_entry_references calls from losing updates.
    """
    if not referenced_ids:
        return memory_content

    lines = memory_content.split("\n")
    result_lines: list[str] = []
    today_str = today.isoformat()

    # Track which entries we've seen headers for but haven't found metadata yet
    pending_entry: str | None = None
    pending_header_idx: int = -1

    for i, line in enumerate(lines):
        # Check if this line is a MEMORY entry header with an ID we care about
        header_match = _ENTRY_HEADER_RE.match(line)
        if header_match and header_match.group(1) in referenced_ids:
            # If we had a pending entry without metadata, insert metadata now
            if pending_entry is not None:
                # Insert after the header (at pending_header_idx + 1)
                result_lines.insert(
                    pending_header_idx + 1,
                    f"  <!-- ref:1 | last:{today_str} | decay:active | sessions:1 -->",
                )
                pending_entry = None

            result_lines.append(line)
            pending_entry = header_match.group(1)
            pending_header_idx = len(result_lines) - 1
            continue

        # Check if this line is a metadata comment for the pending entry
        if pending_entry is not None:
            meta_match = _META_RE.match(line)
            if meta_match:
                # Update existing metadata in place
                indent = meta_match.group(1)
                ref_count = int(meta_match.group(2)) + 1
                old_last = meta_match.group(3)  # previous last-referenced date
                # Only increment sessions if last_referenced date differs (Cepeda dedup)
                old_sessions = int(meta_match.group(5))
                sessions = old_sessions + (1 if old_last != today_str else 0)
                result_lines.append(
                    f"{indent}<!-- ref:{ref_count} | last:{today_str} "
                    f"| decay:active | sessions:{sessions} -->"
                )
                pending_entry = None
                continue

            # If we hit another entry header, the previous entry had no metadata
            next_header = _ENTRY_HEADER_RE.match(line)
            if next_header:
                # Insert metadata for the pending entry
                result_lines.insert(
                    pending_header_idx + 1,
                    f"  <!-- ref:1 | last:{today_str} | decay:active | sessions:1 -->",
                )
                pending_entry = None
                # Now handle this line — re-check if it's a header we care about
                if next_header.group(1) in referenced_ids:
                    result_lines.append(line)
                    pending_entry = next_header.group(1)
                    pending_header_idx = len(result_lines) - 1
                    continue

        result_lines.append(line)

    # Handle trailing pending entry (entry at end of file without metadata)
    if pending_entry is not None:
        result_lines.insert(
            pending_header_idx + 1,
            f"  <!-- ref:1 | last:{today_str} | decay:active | sessions:1 -->",
        )

    return "\n".join(result_lines)


# ── R2-real usage→ref bridge (run_77504e11) ──────────────────────────────────
# Connects the LIVE per-entry usage signal (.memory-usage.json, written by
# context_health_hook._track_memory_usage from real transcript [ID] citations)
# to body-entry ref_count. ref is consumed by ddd_entry_lifecycle's
# _is_reclaimable_noise (ref!=0 → protected from physical strip). So a
# genuinely-used entry survives reclaim — the honest replacement for the
# removed toxic prose-bump. NOT wired to assess_decay (which no longer
# reads ref — see ddd_entry_lifecycle R2-prime).
#
# Join: usage is keyed by INDEX-ID; the index block carries `- [ID] Title | ...`.
# The ID prefix (SP/GUI/PIT/...) maps deterministically to a SECTION via
# MEMORY_PREFIX_TO_SECTION, so we key the result by (section, title) — NOT title
# alone. This kills the cross-section title-collision false-protect Gate-2 found:
# "Customer/Account output" exists in BOTH evergreen Standing Preferences (SP01,
# heavy usage) AND non-evergreen Guidelines (GUI165, light usage); a bare-title
# join lent SP01's usage to the reclaimable GUI165 entry. (section, title) keeps
# them distinct, and the lifecycle's evergreen-skip drops the SP01 copy anyway.
# Threshold: only usage >= threshold earns a ref, so reclaim is NOT neutered for
# the long tail. Damping: log2 so a 142-use entry → ref ~7, never a raw monopoly.
# KNOWN follow-up (Gate-2 Finding 2): _track_memory_usage is all-time cumulative,
# so ref is a one-way ratchet — recency-windowing the usage signal is a separate
# signal-quality epic. The (section,title) fix removes the acute risk (generic
# titles like "Correction"/"DISCUSSION" now resolve to their OWN section, not a
# collision), but a once-hot-now-cold entry stays protected until that lands.

_INDEX_ID_TITLE_RE = re.compile(r"^- \[([A-Z]{2,3}\d{2,3})\]\s+(.+)$", re.MULTILINE)
USAGE_REF_THRESHOLD = 10  # min cumulative usage to earn reclaim-protection

# ── Write-time decay of the usage signal (run_81f6d20c) ──────────────────────
# .memory-usage.json counted every [ID] citation cumulatively and NEVER
# decremented — a one-way ratchet, so a once-hot-now-cold entry stayed
# reclaim-protected (usage>=USAGE_REF_THRESHOLD) forever and the file grew
# unbounded. decay_usage_counts is the fix: the producer applies it ONCE per
# calendar day (gated by a sidecar last_decay date) so counts exponentially
# fade. A cold entry sinks below USAGE_REF_THRESHOLD (loses protection — the
# actual ratchet break) and eventually below USAGE_EPSILON (dropped — file
# hygiene + true window-out). half-life > the producer's 7d scan window
# (context_health_hook.py:1417) so an entry re-cited within a few weeks stays
# protected; the producer counts EVERY citation occurrence per transcript, so
# active entries get multi-point bursts that clear the threshold easily.
# Pure function (no I/O) — the producer owns sidecar read/write + write order.
USAGE_HALFLIFE_DAYS = 30.0  # count halves every 30 idle days
USAGE_EPSILON = 0.5         # below this a faded key is dropped from the file


def decay_usage_counts(
    usage: "dict[str, float]",
    days_elapsed: int,
    halflife_days: float = USAGE_HALFLIFE_DAYS,
    epsilon: float = USAGE_EPSILON,
) -> "dict[str, float]":
    """Exponentially decay every usage count by elapsed idle days; drop faded keys.

    factor = 0.5 ** (days_elapsed / halflife_days). Each value is multiplied by
    factor; keys whose decayed value falls below ``epsilon`` are removed entirely
    (the window-out that breaks the cumulative ratchet and bounds file growth).

    ``days_elapsed <= 0`` returns a shallow copy unchanged — idempotent for
    same-day re-runs (the producer runs on every session close, but the sidecar
    last_decay gate means decay is requested at most once per calendar day; this
    guard is the second line of defence against double-decay).

    Pure: no I/O, no mutation of the input dict.
    """
    if days_elapsed <= 0:
        return dict(usage)
    factor = 0.5 ** (days_elapsed / halflife_days)
    decayed: dict[str, float] = {}
    for key, value in usage.items():
        new_value = value * factor
        if new_value >= epsilon:
            decayed[key] = new_value
    return decayed


def build_usage_ref_map(
    memory_content: str,
    usage_counts: "dict[str, int]",
    threshold: int = USAGE_REF_THRESHOLD,
) -> "dict[tuple[str, str], int]":
    """Map (section, title) → log-damped ref, for body entries whose index-ID
    has usage >= threshold. Returns only the genuinely-used entries (others stay
    ref:0 → reclaim-eligible).

    Keyed by (section, title) via the ID-prefix→section map so two same-titled
    entries in different sections never cross-assign usage (Gate-2 Finding 1/3).
    Splits the index line on the FIRST ` | ` only. Same (section,title) keeps the
    max ref. Idempotent: pure function of current usage (SET semantics).
    """
    import math
    from core.ddd_entry_lifecycle import MEMORY_PREFIX_TO_SECTION

    def _prefix(eid: str) -> str:
        m = re.match(r"^([A-Z]{2,3})", eid)
        return m.group(1) if m else ""

    # Parse index block ID → title.
    start = memory_content.find("MEMORY_INDEX_START")
    end = memory_content.find("MEMORY_INDEX_END")
    if start == -1 or end == -1 or end < start:
        return {}
    index_block = memory_content[start:end]

    ref_map: dict[tuple[str, str], int] = {}
    for m in _INDEX_ID_TITLE_RE.finditer(index_block):
        entry_id, rest = m.group(1), m.group(2)
        usage = usage_counts.get(entry_id, 0)
        if usage < threshold:
            continue
        # Title is everything before the first ' | ' (alias delimiter).
        title = rest.split(" | ", 1)[0].strip()
        if not title:
            continue
        section = MEMORY_PREFIX_TO_SECTION.get(_prefix(entry_id))
        if not section:
            continue  # unknown prefix → cannot place safely, skip
        damped = round(math.log2(usage + 1))
        if damped < 1:
            continue
        key = (section, title)
        ref_map[key] = max(ref_map.get(key, 0), damped)
    return ref_map
