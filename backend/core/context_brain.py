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
        per_file.append(
            {
                "name": spec.filename,
                "tokens": tokens,
                "pct": 0.0,  # filled after total is known
                "owner": _owner_for(spec.filename, spec.user_customized),
                "priority": spec.priority,
                # P0-P2 (+SELF) are non-truncatable → the lock. `truncatable` is
                # the SoT for this exact property (context_directory_loader.py).
                "locked": not spec.truncatable,
            }
        )

    # Composition % (guard div-by-zero for the empty-dir case).
    if total > 0:
        for row in per_file:
            row["pct"] = round(row["tokens"] / total * 100, 1)

    # Sort by priority ascending (P0 first) — matches assembly + the overlay stack.
    per_file.sort(key=lambda r: (r["priority"], r["name"]))

    return {
        "total_tokens": total,
        "budget": _WARNING_THRESHOLD,
        "warning_threshold": _WARNING_THRESHOLD,
        "emergency_threshold": _EMERGENCY_THRESHOLD,
        "over_budget": total > _WARNING_THRESHOLD,
        "per_file": per_file,
    }
