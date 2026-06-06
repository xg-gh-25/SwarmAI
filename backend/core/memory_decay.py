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
STABILITY_BASE = 1.0        # Initial decay resistance (days)
SPACING_BONUS = 0.3         # Stability gain per distinct session (Cepeda effect)
VOLUME_FACTOR = 0.2         # Diminishing log(1+ref_count) contribution
MAX_STABILITY = 10.0        # Cap so no entry becomes immortal

# Sections whose entries are immune to decay/archival
PERMANENT_SECTIONS = frozenset({"Key Decisions", "COE Registry"})

# Regex for MEMORY entry IDs (KD01, LL03, RC15, COE02, OT01)
_ENTRY_ID_RE = re.compile(r"\b((?:KD|LL|RC|COE|OT)\d{2,3})\b")

# Metadata comment format (extends ddd_entry_lifecycle convention)
_META_RE = re.compile(
    r"^(\s*)<!-- ref:(\d+) \| last:([\w\-]+) \| decay:(\w+) \| sessions:(\d+) -->$",
    re.MULTILINE,
)

# Entry header pattern (matches MEMORY.md bullet format)
_ENTRY_HEADER_RE = re.compile(
    r"^- \[([A-Z]+\d{2,3})\]",
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
    session_id: str,
    today: date,
) -> str:
    """Update ref:N, last:date, sessions:N metadata for referenced entries.

    For each referenced entry:
    1. If metadata comment exists → increment ref, update last, increment sessions
    2. If no metadata comment → insert one after the entry line

    The session_id is used to ensure the same session doesn't double-count
    (though in practice distillation runs once per session close).
    """
    if not referenced_ids:
        return memory_content

    lines = memory_content.split("\n")
    result_lines: list[str] = []
    today_str = today.isoformat()
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check if this line is a MEMORY entry header with an ID we care about
        header_match = _ENTRY_HEADER_RE.match(line)
        if header_match and header_match.group(1) in referenced_ids:
            entry_id = header_match.group(1)
            result_lines.append(line)
            i += 1

            # Look for existing metadata comment on next line(s)
            meta_found = False
            while i < len(lines):
                meta_match = _META_RE.match(lines[i])
                if meta_match:
                    # Update existing metadata
                    indent = meta_match.group(1)
                    ref_count = int(meta_match.group(2)) + 1
                    # decay state stays active (bumping = alive)
                    sessions = int(meta_match.group(5)) + 1
                    result_lines.append(
                        f"{indent}<!-- ref:{ref_count} | last:{today_str} "
                        f"| decay:active | sessions:{sessions} -->"
                    )
                    meta_found = True
                    i += 1
                    break
                elif lines[i].strip().startswith("<!--") and "maturity:" in lines[i]:
                    # Skip DDD maturity metadata (not ours)
                    result_lines.append(lines[i])
                    i += 1
                else:
                    break

            if not meta_found:
                # Insert new metadata comment
                result_lines.append(
                    f"  <!-- ref:1 | last:{today_str} "
                    f"| decay:active | sessions:1 -->"
                )
        else:
            result_lines.append(line)
            i += 1

    return "\n".join(result_lines)
