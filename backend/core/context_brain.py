"""C&M Global Brain — context token-block builder.

Single source of the LIVE, calibrated view the C&M overlay's Context tab + overview
rail consume: total prompt tokens, per-file token size + composition %, ownership,
priority, and lock (P0-P2 non-truncatable).

Design (run_5f7d4fe1): read-only telemetry for the Brain overlay UI. It joins the
canonical assembly metadata (`ContextDirectoryLoader.CONTEXT_FILES` — the SoT for
filename / priority / section_name / truncatable) with the CANONICAL
`ContextDirectoryLoader.estimate_tokens` (the SAME CJK-aware estimator prompt
assembly + `_check_token_budget` use — NOT byte length, which diverges ~2.2x on CJK)
and the real budget thresholds. This is why the overview number equals the prompt's
real load, not a naive char count.

Ownership note (Gate-1 finding, run_5f7d4fe1): `ContextFileSpec` carries NO owner
field — only `user_customized` (a 2-way system/customized split). The 4-way owner
category (system / user / agent / auto) the overlay shows is documented in
KNOWLEDGE.md's "11 Context Files" table but is NOT on the dataclass. Rather than
mutate the 12-entry SoT list (higher blast radius), the map lives HERE, keyed by
filename. If a file is unknown to the map, it falls back to the `user_customized`
2-way split (system vs user) so a newly-added context file never crashes the block.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from core.context_directory_loader import CONTEXT_FILES, ContextDirectoryLoader

logger = logging.getLogger(__name__)

# Budget thresholds — mirror context_health_hook.ContextHealthHook (SoT for the
# WARN/EMERGENCY lines). Kept in sync with that hook; if it changes, this follows.
_WARNING_THRESHOLD = 91_000
_EMERGENCY_THRESHOLD = 130_000

# filename → 4-way owner category. The KNOWLEDGE.md "11 Context Files" ownership
# column, made machine-readable HERE (not on ContextFileSpec — Gate-1).
_OWNER_BY_FILE: dict[str, str] = {
    "SWARMAI.md": "system",
    "IDENTITY.md": "system",
    "SOUL.md": "system",
    "SELF.md": "system",
    "AGENT.md": "system",
    "USER.md": "user",
    "STEERING.md": "user",
    "TOOLS.md": "user",
    "MEMORY.md": "agent",
    "EVOLUTION.md": "agent",
    "KNOWLEDGE.md": "auto",
    "PROJECTS.md": "auto",
}


def _owner_for(filename: str, user_customized: bool) -> str:
    """4-way owner category, with a safe 2-way fallback for unmapped files."""
    if filename in _OWNER_BY_FILE:
        return _OWNER_BY_FILE[filename]
    # Unknown context file (added after this map) — degrade to the dataclass's
    # own 2-way split rather than crash or mislabel.
    return "user" if user_customized else "system"


# Files that run through selective injection in a desktop session — their DISK
# size is NOT what actually reaches the prompt. MEMORY.md ≥30K → selective
# (memory_index.select_memory_sections). This is the SoT list for the overlay's
# "disk vs injected" honesty (run_5f040023): the UI must NOT imply a 64K MEMORY
# is 64K of prompt when selective cuts it to ~17K.
_SELECTIVE_FILES: frozenset[str] = frozenset({"MEMORY.md"})


def _injected_floor(filename: str, content: str) -> int | None:
    """The HONEST lower-bound of tokens a selective file actually injects.

    Telemetry is session-agnostic (no user query), and selective injection is
    query-dependent — so we CANNOT compute a real point-estimate of injected
    size (Gate-1 #4: a fabricated point-estimate repeats the very "UI number
    doesn't reflect reality" bug this fix exists to kill). What we CAN compute
    honestly is the FLOOR: the always-injected part (index + always-load
    sections), which selective injects regardless of the query. Returns None for
    non-selective files (their injected size == disk size, no floor needed).
    """
    if filename not in _SELECTIVE_FILES:
        return None
    try:
        from core.memory_index import (
            select_memory_sections, FULL_INJECTION_THRESHOLD,
        )
        disk_tokens = ContextDirectoryLoader.estimate_tokens(content)
        # BELOW the threshold, selective does NOT trigger — the file is
        # full-injected (and select_* may even ADD an index block, making its
        # output LARGER than disk). So there is no "floor below disk": injected
        # == disk. Return None (UI shows disk as-is, no selective cue). Only a
        # file that ACTUALLY exceeds the threshold has a meaningful floor.
        if disk_tokens < FULL_INJECTION_THRESHOLD:
            return None
        # Empty query → only the always-load floor is selected (no keyword
        # section hits). This is the guaranteed-minimum injection, a REAL number.
        floor = select_memory_sections(
            memory_content=content, user_message="",
            session_signals={}, context_percent_used=0.0,
        )
        floor_tokens = ContextDirectoryLoader.estimate_tokens(floor)
        # Defensive: floor can never exceed disk (selective only removes). If the
        # estimator disagrees at the margin, clamp to disk (never report >disk).
        return min(floor_tokens, disk_tokens)
    except Exception as exc:  # never crash telemetry (O030)
        logger.debug("context_brain: injected-floor calc failed for %s: %s", filename, exc)
        return None


def _health_tag(tokens: int, budget: int, mtime_days: float) -> str:
    """Single per-file Health tag, severity-first (Gate-1 precedence, run_d0ba3f69).

    oversized (>90% budget) > growing (>60% budget) > idle (>=14d) > fresh.
    Size severity outranks age: a big+old file is 'growing'/'oversized' (the
    actionable risk), not 'idle'. The 7-14d mtime band gets no time tag → falls
    through to 'fresh' (neutral) unless a size tag fires. budget<=0 → size tags
    can't fire (div-by-zero guard); age alone decides.
    """
    share = (tokens / budget) if budget > 0 else 0.0
    if share > 0.90:
        return "oversized"
    if share > 0.60:
        return "growing"
    if mtime_days >= 14:
        return "idle"
    return "fresh"


def build_context_token_block(context_dir: Path) -> dict:
    """Build the read-only token block for the C&M overlay.

    Args:
        context_dir: the workspace ``.context`` directory (where the assembled
            context files live).

    Returns:
        {
          "total_tokens": int,           # sum of per-file calibrated tokens
          "budget": int,                 # WARNING threshold = the assembly budget
          "warning_threshold": int,
          "emergency_threshold": int,
          "over_budget": bool,
          "per_file": [                  # sorted by priority ascending (P0 first)
            {"name","tokens","pct","owner","priority","locked"}
          ],
        }

    Never raises for a missing dir / unreadable file — a health telemetry read
    must not crash the endpoint (O030: observability, not a gate). A missing dir
    yields an empty-but-valid block (thresholds still present).
    """
    per_file: list[dict] = []
    total = 0

    for spec in CONTEXT_FILES:
        path = context_dir / spec.filename
        try:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("context_brain: read failed for %s: %s", spec.filename, exc)
            continue
        tokens = ContextDirectoryLoader.estimate_tokens(content)
        total += tokens
        # Selective injection honesty (run_5f040023): for a file that runs
        # through selective injection, its DISK size (`tokens`) is NOT what
        # reaches the prompt. `injected_floor` is the HONEST guaranteed-minimum
        # injected size (always-load part); None for non-selective files (where
        # injected == disk). The UI shows disk as the headline (conservative) +
        # "selective → ≥floor" as the honest actual-injection cue.
        floor = _injected_floor(spec.filename, content)
        # File age in days from mtime (for the Health tag). Read defensively —
        # a stat failure must not crash the telemetry read (O030).
        try:
            mtime_days = max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)
        except OSError:
            mtime_days = 0.0
        per_file.append(
            {
                "name": spec.filename,
                "tokens": tokens,
                "pct": 0.0,  # filled after total is known (composition %)
                "owner": _owner_for(spec.filename, spec.user_customized),
                "priority": spec.priority,
                # P0-P2 (+SELF) are non-truncatable → the lock. `truncatable` is
                # the SoT for this exact property (context_directory_loader.py).
                "locked": not spec.truncatable,
                # Health tag: budget-relative size + mtime, severity-first
                # (oversized>growing>idle>fresh). Backend-derived so the UI invents
                # nothing (R30). budget is the same _WARNING_THRESHOLD used below.
                "health": _health_tag(tokens, _WARNING_THRESHOLD, mtime_days),
                # Selective-injection honesty fields (run_5f040023):
                "has_selective": floor is not None,
                "injected_floor": floor,  # None = injected==disk (tokens)
            }
        )

    # Honest injected-estimate total (run_5f040023): disk total MINUS the amount
    # selective files shave off (disk − injected_floor for each selective file).
    # This is a REAL lower-bound of what reaches the prompt — NOT a fabricated
    # point-estimate. A selective file injects AT LEAST its floor; a query may add
    # a few more sections, so the true injected size is in [injected_estimate, total].
    injected_estimate = total
    for row in per_file:
        if row["has_selective"] and row["injected_floor"] is not None:
            injected_estimate -= (row["tokens"] - row["injected_floor"])

    # Composition % over DISK total (headline is the conservative disk size — the
    # pct answers "share of the on-disk context", stable + query-independent).
    if total > 0:
        for row in per_file:
            row["pct"] = round(row["tokens"] / total * 100, 1)

    # Sort by priority ascending (P0 first) — matches assembly + the overlay stack.
    per_file.sort(key=lambda r: (r["priority"], r["name"]))

    return {
        "total_tokens": total,           # DISK total (conservative headline)
        "injected_estimate": injected_estimate,  # honest lower-bound of prompt load
        "budget": _WARNING_THRESHOLD,
        "warning_threshold": _WARNING_THRESHOLD,
        "emergency_threshold": _EMERGENCY_THRESHOLD,
        # over_budget uses DISK total (conservative — flags a file that COULD be
        # trimmed even if selective currently masks it). The UI also shows the
        # injected_estimate so the user sees the real prompt load, not just disk.
        "over_budget": total > _WARNING_THRESHOLD,
        "per_file": per_file,
    }
