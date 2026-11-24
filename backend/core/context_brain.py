"""C&M Global Brain — context token-block builder.

Single source of the LIVE, calibrated view the C&M overlay's Context tab + overview
rail consume: total prompt tokens, per-file token size + composition %, ownership,
priority, and lock (P0-P2 non-truncatable).

Design (run_5f7d4fe1): read-only telemetry for the Brain overlay UI. It joins the
canonical assembly metadata (`ContextDirectoryLoader.CONTEXT_FILES` — the SoT for
filename / priority / section_name / truncatable) with the CANONICAL
`ContextDirectoryLoader.estimate_tokens` (the SAME CJK-aware estimator prompt
assembly uses — NOT byte length, which diverges ~2.2x on CJK) and the real budget
thresholds.

Injected, not disk: a file's prompt load is what assembly puts in the prompt, and
that is not always the file. TIMELINE.md contributes only the current and previous
month's summaries plus one line counting its events (``select_timeline``); the
loader also removes metadata comments and the title line, skips an unfilled user
template, and strips MEMORY's generated index block. So each row is measured on
``ContextDirectoryLoader.render_section`` — the loader's own per-file step, the one
assembly uses — through ``section_text``, the same text the budget check counts. The
disk size is kept beside it as ``disk_tokens``; the total, the warning comparison,
the share and the health tag use the injected tokens.

Ownership note (Gate-1 finding, run_5f7d4fe1): `ContextFileSpec` carries NO owner
field — only `user_customized` (a 2-way system/customized split). The 4-way owner
category (system / user / agent / auto) the overlay shows is documented in
SWARMAI.md's "My 11 Context Files" table but is NOT on the dataclass. Rather than
mutate the SoT list (higher blast radius), the map lives HERE, keyed by
filename. If a file is unknown to the map, it falls back to the `user_customized`
2-way split (system vs user) so a newly-added context file never crashes the block.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from core.context_directory_loader import (
    CONTEXT_FILES,
    EFFECTIVE_1M_BUDGET,
    EMERGENCY_MARGIN,
    ContextDirectoryLoader,
)

logger = logging.getLogger(__name__)

# Budget thresholds — DERIVED from the budget SoT in context_directory_loader
# (run_72ca2a97), the SAME constants context_health_hook uses, so the overlay's
# token_block and the deep-check report can never disagree. A change to
# BUDGET_1M_MODEL or the ephemeral formula propagates here automatically.
# WARNING = 133K effective (150K base − 17K ephemeral); EMERGENCY = WARNING + 39K.
_WARNING_THRESHOLD = EFFECTIVE_1M_BUDGET
_EMERGENCY_THRESHOLD = EFFECTIVE_1M_BUDGET + EMERGENCY_MARGIN

# filename → 4-way owner category (rail display only — NO logic consumes it; a file's
# editability/lock comes from `truncatable`, not owner). This is the coarse display
# bucket for the authoritative ownership column in SWARMAI.md § "My 11 Context Files"
# (the SSOT).
# NOTE: SELF.md is authoritatively "runtime-owned" in that table (a write-PATH
# distinction: human/distill writes only), but is INTENTIONALLY bucketed to "system"
# here — on the rail it behaves like a system file (non-editable/locked). This is NOT
# a bug (XG-confirmed 2026-08-15); the 4-way rail model deliberately does not surface
# the finer runtime/system write-path split.
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
    # PROJECTS.md removed 2026-08-14 (in-prompt index deleted) — no longer a context file.
}


def _owner_for(filename: str, user_customized: bool) -> str:
    """4-way owner category, with a safe 2-way fallback for unmapped files."""
    if filename in _OWNER_BY_FILE:
        return _OWNER_BY_FILE[filename]
    # Unknown context file (added after this map) — degrade to the dataclass's
    # own 2-way split rather than crash or mislabel.
    return "user" if user_customized else "system"


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


# Which files are made of typed entries. Others (SOUL/AGENT/USER/…) are prose docs,
# so entry counts mean nothing for them — we skip them (health = null).
_LIFECYCLE_FILES = frozenset({"MEMORY.md", "EVOLUTION.md"})


def _entry_health_counts(filename: str, content: str) -> "dict | None":
    """Per-file knowledge-health counts for the diagnostic panel.

    Returns {entries, duplicate} for the three entry-structured files, else None
    (prose files have no entries). Every entry in a main file is in force (design
    2026-10-30-brain-driven §2.3), so there is no active/dormant split; ``duplicate``
    is what the exact-duplicate selector picks (read-only). Never raises (telemetry,
    not a gate)."""
    if filename not in _LIFECYCLE_FILES:
        return None
    try:
        from datetime import date
        from core.ddd_entry_lifecycle import parse_entries, reclaim_duplicate_entries
        entries = parse_entries(content)
        if not entries:
            return None
        dup = reclaim_duplicate_entries(content, date.today(), Path("/tmp"))
        return {"entries": len(entries), "duplicate": len(dup.candidates)}
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
          "total_tokens": int,           # sum of per-file INJECTED tokens
          "disk_tokens": int,            # sum of per-file disk tokens (secondary)
          "budget": int,                 # WARNING threshold = the assembly budget
          "warning_threshold": int,
          "emergency_threshold": int,
          "over_budget": bool,           # total_tokens (injected) > warning
          "per_file": [                  # sorted by priority ascending (P0 first)
            {"name","tokens","disk_tokens","pct","owner","priority","locked",
             "health","health_counts"}
          ],
        }

    ``tokens`` is what the file puts in the owner's prompt (0 when assembly skips
    it, e.g. an unfilled template); ``disk_tokens`` is the whole file.

    Never raises for a missing dir / unreadable file — a health telemetry read
    must not crash the endpoint (O030: observability, not a gate). A missing dir
    yields an empty-but-valid block (thresholds still present).
    """
    per_file: list[dict] = []
    total = 0
    disk_total = 0
    # The owner's prompt (no exclusions): the form the C&M overlay's person sees.
    loader = ContextDirectoryLoader(context_dir)

    for spec in CONTEXT_FILES:
        path = context_dir / spec.filename
        try:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("context_brain: read failed for %s: %s", spec.filename, exc)
            continue
        disk_tokens = ContextDirectoryLoader.estimate_tokens(content)
        try:
            body = loader.render_section(spec)
        except Exception as exc:  # never crash telemetry (O030); disk is the fallback
            logger.warning("context_brain: render failed for %s, counting disk: %s", spec.filename, exc)
            body = content
        tokens = (ContextDirectoryLoader.estimate_tokens(
            ContextDirectoryLoader.section_text(spec.section_name, body)) if body else 0)
        total += tokens
        disk_total += disk_tokens
        # File age in days from mtime (for the Health tag). Read defensively —
        # a stat failure must not crash the telemetry read (O030).
        try:
            mtime_days = max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)
        except OSError:
            mtime_days = 0.0
        per_file.append(
            {
                "name": spec.filename,
                "tokens": tokens,            # injected: what the prompt carries
                "disk_tokens": disk_tokens,  # the whole file on disk
                "pct": 0.0,  # filled after total is known (composition %)
                "owner": _owner_for(spec.filename, spec.user_customized),
                "priority": spec.priority,
                # P0-P2 (+SELF) are non-truncatable → the lock. `truncatable` is
                # the SoT for this exact property (context_directory_loader.py).
                "locked": not spec.truncatable,
                # Health tag: budget-relative size + mtime, severity-first
                # (oversized>growing>idle>fresh). Backend-derived so the UI invents
                # nothing (R30). budget is the same _WARNING_THRESHOLD used below;
                # sized on the injected tokens (a big TIMELINE whose events are not
                # injected is not "oversized" in the prompt).
                "health": _health_tag(tokens, _WARNING_THRESHOLD, mtime_days),
                # Knowledge-health counts: None for prose files; {entries, duplicate}
                # for the entry-structured files (MEMORY/EVOLUTION).
                "health_counts": _entry_health_counts(spec.filename, content),
            }
        )

    # Composition % over the INJECTED total: "this file's share of what the context
    # files put in the prompt" (query-independent, like the assembly itself).
    if total > 0:
        for row in per_file:
            row["pct"] = round(row["tokens"] / total * 100, 1)

    # Sort by priority ascending (P0 first) — matches assembly + the overlay stack.
    per_file.sort(key=lambda r: (r["priority"], r["name"]))

    return {
        "total_tokens": total,           # INJECTED total == the context files' prompt load
        "disk_tokens": disk_total,       # the files' size on disk (secondary)
        "budget": _WARNING_THRESHOLD,
        "warning_threshold": _WARNING_THRESHOLD,
        "emergency_threshold": _EMERGENCY_THRESHOLD,
        # over_budget compares what the prompt carries with the warning line: a file
        # that is big on disk but mostly not injected (TIMELINE's events) does not
        # push the prompt over it.
        "over_budget": total > _WARNING_THRESHOLD,
        "per_file": per_file,
    }
