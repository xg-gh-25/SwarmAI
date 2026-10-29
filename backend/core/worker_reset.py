"""A Hive that becomes a worker sets aside what it wrote as a full SwarmAI (design
docs/design/2026-10-29-hive-run-record-and-worker-role.md §3.4).

Before the worker role (core/app_role), a Hive's own CDF, DailyActivity and session hooks
wrote into its private context files: what one dispatched task taught it is injected into
every later one, and on a worker nothing writes there any more to correct it. The Hive
seed never ships these files (``_PUBLIC_CONTEXT_SEED`` holds the public five only), so
what they hold beyond their templates is the Hive's own.

``set_aside(workspace)``: on the first start as a worker (no marker yet), each of them
that exists is MOVED (``os.replace``, never deleted or overwritten) into
``Hive/pre-worker/<utc time>/``, keeping its relative path; the context loader then
recreates the private files from their templates. The marker is written last, with the
list of what moved, so a start that fails half way moves the rest next time. A person's
instance and a reference Hive are never touched.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MARKER = Path(".context") / ".worker-role"
PRIVATE_CONTEXT = ("MEMORY.md", "EVOLUTION.md", "USER.md", "STEERING.md", "TOOLS.md", "TIMELINE.md")
DIRS = (Path("Knowledge") / "DailyActivity",)


def _targets(ws: Path) -> list[Path]:
    """What to move; nothing under a linked ``.context`` or ``Knowledge`` (it would carry
    content from outside the workspace)."""
    ctx = ws / ".context"
    out: list[Path] = []
    if ctx.is_dir() and not ctx.is_symlink():
        for name in PRIVATE_CONTEXT:
            stem = name[:-3]
            out += [ctx / name] + sorted(ctx.glob(f"{stem}-archive*.md"))
    for d in DIRS:
        if not any((ws / p).is_symlink() for p in [*d.parents][:-1]):
            out.append(ws / d)
    return [p for p in out if p.exists() and not p.is_symlink()]


def set_aside(workspace: Path) -> Optional[dict]:
    """Move a new worker's inherited cognition aside; returns what moved, or None when
    there was nothing to do (not a worker, or already done)."""
    from core.app_role import is_worker
    ws = Path(workspace)
    if not is_worker() or (ws / MARKER).exists():
        return None
    dest = ws / "Hive" / "pre-worker" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    moved: list[str] = []
    for src in _targets(ws):
        rel = src.relative_to(ws)
        to = dest / rel
        to.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, to)
        moved.append(rel.as_posix())
    marker = ws / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(timezone.utc).isoformat(), "moved_to": str(dest.relative_to(ws)),
              "moved": moved}
    tmp = marker.with_name(marker.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, marker)
    logger.warning("Worker Hive: set aside %d inherited cognition file(s) into %s", len(moved),
                   record["moved_to"])
    return record
