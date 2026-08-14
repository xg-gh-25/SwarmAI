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
    # PROJECTS.md removed 2026-08-14 (in-prompt index deleted) — no longer a context file.
}


def _owner_for(filename: str, user_customized: bool) -> str:
    """4-way owner category, with a safe 2-way fallback for unmapped files."""
    if filename in _OWNER_BY_FILE:
        return _OWNER_BY_FILE[filename]
    # Unknown context file (added after this map) — degrade to the dataclass's
    # own 2-way split rather than crash or mislabel.
    return "user" if user_customized else "system"


# NEW ARCHITECTURE (2026-08-14): NO file runs through selective injection anymore —
# live MEMORY.md is always full-injected (injected size == disk size). The old
# _SELECTIVE_FILES set, _injected_floor() stub, and the has_selective/injected_floor/
# injected_estimate token-block fields were always-inert and were removed (run_8f852625).


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


# Which files carry per-entry decay metadata (the lifecycle-governed brain files).
# Others (SOUL/AGENT/USER/…) are prose docs with no `<!-- ref | decay -->` entries,
# so their noise counts are meaninglessly zero — we skip them (health = null).
_LIFECYCLE_FILES = frozenset({"MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md"})


def _entry_health_counts(filename: str, content: str) -> "dict | None":
    """Per-file knowledge-health counts for the diagnostic panel (run_2816ab1c).

    Returns {active, dormant, archived, reclaimable, duplicate} for the three
    lifecycle-governed files, else None (prose files have no decay entries). This
    is the READ side of the reduce loop the UI surfaces — it uses the SAME
    predicates the write side (assess_decay / compute_entry_noise /
    reclaim_duplicate_entries) uses, so the numbers match what a sweep would act on.
    Never raises (O030: telemetry, not a gate)."""
    if filename not in _LIFECYCLE_FILES:
        return None
    try:
        from datetime import date
        from core.ddd_entry_lifecycle import (
            parse_entries, reclaim_noise_entries, reclaim_duplicate_entries,
            MEMORY_EVERGREEN_SECTIONS, KNOWLEDGE_EVERGREEN_SECTIONS,
            EVOLUTION_EVERGREEN_SECTIONS,
        )
        entries = parse_entries(content)
        if not entries:
            return None
        today = date.today()
        counts = {"active": 0, "dormant": 0, "archived": 0}
        for e in entries:
            counts[e.decay_state] = counts.get(e.decay_state, 0) + 1
        evergreen = {
            "MEMORY.md": MEMORY_EVERGREEN_SECTIONS,
            "KNOWLEDGE.md": KNOWLEDGE_EVERGREEN_SECTIONS,
            "EVOLUTION.md": EVOLUTION_EVERGREEN_SECTIONS,
        }.get(filename)
        # reclaimable + duplicate = what a sweep would ACTUALLY archive (dry_run, no
        # write). Both use the ACTION's own predicate (keep-class + evergreen
        # filtered), NOT the wider compute_entry_noise gauge — so the panel number
        # equals what the sweep removes (gate==action). A raw noise count would show
        # "5 reclaimable" while the sweep strips 0 (all keep-class) — a misleading
        # number, the exact honesty bug this project exists to fix. project_dir is
        # unused on the dry_run path, so any Path is fine.
        noise_r = reclaim_noise_entries(
            content, today, Path("/tmp"),
            evergreen_sections=evergreen, dry_run=True,
        )
        dup = reclaim_duplicate_entries(
            content, today, Path("/tmp"),
            evergreen_sections=evergreen, dry_run=True,
        )
        return {
            "active": counts.get("active", 0),
            "dormant": counts.get("dormant", 0),
            "archived": counts.get("archived", 0),
            "reclaimable": len(noise_r.candidates),
            "duplicate": len(dup.candidates),
        }
    except Exception as exc:  # never crash telemetry (O030)
        logger.debug("context_brain: entry-health calc failed for %s: %s", filename, exc)
        return None


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
        # NEW ARCHITECTURE (2026-08-14): selective injection was deleted — every
        # file is FULL-injected (disk size == prompt load). There is no "injected
        # floor below disk" to report, so the old has_selective/injected_floor/
        # injected_estimate fields were always-inert and are removed (run_8f852625).
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
                # Knowledge-health counts (run_2816ab1c): None for prose files;
                # {active,dormant,archived,reclaimable,duplicate} for the 3
                # lifecycle-governed brain files (MEMORY/EVOLUTION/KNOWLEDGE).
                "health_counts": _entry_health_counts(spec.filename, content),
            }
        )

    # Composition % over DISK total (headline is the conservative disk size — the
    # pct answers "share of the on-disk context", stable + query-independent).
    if total > 0:
        for row in per_file:
            row["pct"] = round(row["tokens"] / total * 100, 1)

    # Sort by priority ascending (P0 first) — matches assembly + the overlay stack.
    per_file.sort(key=lambda r: (r["priority"], r["name"]))

    return {
        "total_tokens": total,           # DISK total == prompt load (full-injected)
        "budget": _WARNING_THRESHOLD,
        "warning_threshold": _WARNING_THRESHOLD,
        "emergency_threshold": _EMERGENCY_THRESHOLD,
        # over_budget uses DISK total (conservative — flags a file that COULD be
        # trimmed even if selective currently masks it). The UI also shows the
        # injected_estimate so the user sees the real prompt load, not just disk.
        "over_budget": total > _WARNING_THRESHOLD,
        "per_file": per_file,
    }
