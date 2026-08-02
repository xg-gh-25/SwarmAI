"""Library API — the agent's bookshelf (Native store + Mount points).

Run 5 (overlay-first) scope: read-only endpoints that back the Library overlay's
Browse + Recent views over the EXISTING Native store (`Knowledge/`). Mounts are
introduced by later cycles; `GET /mounts` returns an empty list until then.

Design: Knowledge/Designs/2026-08-02-library-mount-points-design.md
- Library is an INDEX, not a warehouse. Native = `Knowledge/` (already in recall).
- All counts/sizes are LIVE filesystem reads — never baked (R30). A 0-file
  category returns 0, proving the read is live, not fabricated.

Endpoints (Run 5):
    GET /api/library/native  — Knowledge/ top-level categories + live file counts
    GET /api/library/recent  — last-7-days add/edit feed across Knowledge/
    GET /api/library/mounts  — registered mount points (empty until the mount runs)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.initialization_manager import initialization_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])

# Directories under Knowledge/ that are system/flow-log noise, not browsable
# user knowledge (mirrors knowledge_store._SKIP_DIRS intent). Kept local so this
# read-only view never depends on the indexer's internals.
_SKIP_CATEGORIES = {"__pycache__", ".git", ".DS_Store"}
_RECENT_WINDOW_SECONDS = 7 * 24 * 3600


def _knowledge_dir() -> Path:
    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        raise HTTPException(status_code=503, detail="Workspace not initialized")
    kdir = Path(ws_path) / "Knowledge"
    if not kdir.is_dir():
        raise HTTPException(status_code=404, detail="Knowledge/ directory not found")
    return kdir


@router.get("/native")
async def native_categories() -> dict:
    """Live category list for the Native store (`Knowledge/`).

    One entry per top-level subdirectory: {name, file_count, total_bytes}.
    Counts are computed live (rglob) so a category with 0 files reports 0 —
    never a baked number (R30). Files at Knowledge/ root are grouped under a
    synthetic "(root)" category.
    """
    kdir = _knowledge_dir()
    categories: list[dict] = []

    # Root-level loose files → a synthetic "(root)" category.
    root_files = [p for p in kdir.iterdir() if p.is_file() and not p.name.startswith(".")]
    if root_files:
        categories.append({
            "name": "(root)",
            "file_count": len(root_files),
            "total_bytes": sum(_safe_size(p) for p in root_files),
        })

    for sub in sorted(kdir.iterdir()):
        if not sub.is_dir() or sub.name in _SKIP_CATEGORIES:
            continue
        files = [p for p in sub.rglob("*") if p.is_file() and not p.name.startswith(".")]
        categories.append({
            "name": sub.name,
            "file_count": len(files),
            "total_bytes": sum(_safe_size(p) for p in files),
        })

    return {
        "source": "native",
        "root": "Knowledge/",
        "category_count": len(categories),
        "categories": categories,
    }


@router.get("/recent")
async def recent_feed(days: int = Query(default=7, ge=1, le=30)) -> dict:
    """Last-N-days add/edit feed across `Knowledge/` (default 7).

    Each item: {path, category, mtime, size, source}. `source` is a coarse tag
    derived from the containing category (session backflow lands in Notes;
    job output in JobResults) — NOT a fabricated review-queue signal (the design
    explicitly rejects a fake "Pending review"). Sorted newest-first, capped.
    """
    kdir = _knowledge_dir()
    cutoff = time.time() - days * 24 * 3600
    items: list[dict] = []

    for p in kdir.rglob("*"):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        rel = p.relative_to(kdir)
        category = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        items.append({
            "path": f"Knowledge/{rel.as_posix()}",
            "category": category,
            "mtime": mtime,
            "size": _safe_size(p),
            "source": _source_tag(category),
        })

    items.sort(key=lambda it: it["mtime"], reverse=True)
    return {
        "window_days": days,
        "count": len(items),
        "items": items[:200],  # cap the feed; not a silent truncation of value
    }


@router.get("/mounts")
async def list_mounts() -> dict:
    """Registered mount points + health.

    Run 5: the mount registry does not exist yet, so this returns an empty list.
    The later mount-registry cycle replaces this stub with a real read over the
    `library_mounts` store. Kept as a live endpoint so the overlay's Mounted
    section renders a real (empty) state, not a hardcoded placeholder.
    """
    return {"count": 0, "mounts": [], "registry_ready": False}


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _source_tag(category: str) -> str:
    """Coarse provenance tag from the category (no fabricated review state)."""
    c = category.lower()
    if c in ("notes",):
        return "session"
    if c in ("jobresults", "signals", "dailybriefs", "dailyactivity"):
        return "job"
    return "you"
