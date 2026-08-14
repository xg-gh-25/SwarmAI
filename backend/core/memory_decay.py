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
    decay_usage_counts      — Write-time exponential decay of .memory-usage.json

    (run_3cb6b9ae Cycle-3 #2 removed the numeric-ID usage→ref bridge —
    get_archive_candidates / scan_session_for_memory_refs / bump_entry_references /
    build_usage_ref_map + the 5-field _META_RE + index-ID regexes — dead once the
    in-prompt index block was removed. The .memory-usage.json PRODUCER lives in
    context_health_hook._track_memory_usage and is KEPT.)
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

# run_3cb6b9ae Cycle-3 (#2): the numeric-ID usage→ref BRIDGE was removed. It keyed
# off `- [KD01]`/`- [PRI01]`-shape lines that ONLY the deleted in-prompt index block
# carried (#6). With the index gone, the bridge (scan_session_for_memory_refs →
# bump_entry_references → build_usage_ref_map) matched zero body entries and returned
# {} permanently — dead by starvation. Removed: `_ENTRY_ID_RE`, 5-field `_META_RE`,
# `_ENTRY_HEADER_RE`, `scan_session_for_memory_refs`, `bump_entry_references`,
# `build_usage_ref_map`, `_INDEX_ID_TITLE_RE`, `USAGE_REF_THRESHOLD`,
# `get_archive_candidates` (0 callers). KEPT: `compute_decay_score` (live, used by
# the caps + size-valve eviction), `compute_stability` (called BY compute_decay_score),
# and `decay_usage_counts` + `.memory-usage.json` producer
# `context_health._track_memory_usage` (live — feeds the loops-health
# `memory_precision` signal; NOT part of the dead bridge).


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
