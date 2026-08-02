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
async def list_mounts(scope: Optional[str] = Query(default=None)) -> dict:
    """Registered mount points + live health (real registry read).

    Default (scope=None) lists ALL mounts — the overlay shows the whole shelf
    (GLOBAL + any per-project mounts) regardless of active project. Pass an
    explicit scope to filter."""
    try:
        import sqlite3
        from jobs.paths import DB_PATH
        from core.library_mounts import LibraryMounts
        if not DB_PATH.exists():
            return {"count": 0, "mounts": [], "registry_ready": False}
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            store = LibraryMounts(conn)
            store.ensure_table()
            rows = store.list_mounts(scope=scope)
            mounts = [
                {
                    "id": r["id"], "path": r["path"], "kind": r["kind"],
                    "health": r["health"], "enabled": bool(r["enabled"]),
                    "last_synced": r["last_synced"], "briefing": r["briefing"],
                }
                for r in rows
            ]
            return {"count": len(mounts), "mounts": mounts, "registry_ready": True}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — overlay tolerates an empty list
        logger.warning("library mounts list failed: %s", exc)
        return {"count": 0, "mounts": [], "registry_ready": False}


@router.post("/mounts")
async def register_mount(path: str = Query(...), scope: str = Query(default="GLOBAL")) -> dict:
    """Register an external directory as a mount AND index it by kind.

    The +Add Folder button lands here. Judges the kind from the dir's contents
    (code if it has parseable source, else docs), registers it in library_mounts,
    then dispatches indexing:
      - code → index_code_mount inline (per-mount graph; returns symbol count)
      - docs → return a chat-handoff (the agent must WALK + judge which files are
        worth a briefing card — that's semantic, it belongs in chat via s_library,
        not a mechanical endpoint).
    Never copies the directory (index-not-warehouse). 400 if the path isn't a dir.
    """
    src = Path(path).expanduser()
    if not src.is_dir():
        raise HTTPException(status_code=400, detail=f"{path} is not a directory (a single file goes to the Inbox; only directories are mounted)")
    try:
        import sqlite3
        from jobs.paths import DB_PATH
        from core.library_mounts import LibraryMounts, judge_mount_kind, index_code_mount, is_protected_system_path
        # SECURITY (Gate-2 #1): same guard as the Inbox endpoint — a caller-supplied
        # path under a protected system root (/etc, ~/.ssh-adjacent, /var, ...) must
        # NOT be indexed into a searchable graph (host-content exfiltration).
        if is_protected_system_path(str(src)):
            raise HTTPException(status_code=400, detail=f"{path} is under a protected system path — system directories cannot be mounted (exfiltration guard).")
        kind = judge_mount_kind(str(src))
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            store = LibraryMounts(conn)
            store.ensure_table()
            mid = store.add_mount(scope=scope, path=str(src), kind=kind)
            if kind == "code":
                result = index_code_mount(store, mid)
                return {"id": mid, "kind": kind, "status": result.get("status"),
                        "symbols": result.get("symbols", 0)}
            # docs → hand to chat (semantic file-judging is not a mechanical job)
            return {"id": mid, "kind": kind, "status": "registered",
                    "next": f"In chat, say: brief the docs dir {src} — the agent will "
                            f"walk it, judge which files matter, and write briefing cards."}
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("register_mount failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"mount failed: {exc}")


@router.get("/search")
async def library_search(q: str = Query(...), scope: str = Query(default="GLOBAL")) -> dict:
    """Search the library the SAME way recall does (the Guide tab's promise made
    real): recall_all over the library (Native Knowledge/ + docs-mount cards) +
    codeintel (project graph + code mounts) domains. Empty q → empty hits."""
    if not q.strip():
        return {"query": q, "hits": []}
    try:
        from core.recall_multi import recall_all
        result = recall_all(q, project=scope, domains=("library", "codeintel"))
        hits: list[dict] = []
        for domain in ("library", "codeintel"):
            for h in (result.buckets.get(domain) or []):
                hits.append({
                    "domain": domain,
                    "title": h.get("heading") or h.get("name") or h.get("source") or "",
                    "source": h.get("source") or h.get("file_path") or h.get("mount_path") or "",
                    "content": (h.get("content") or "")[:400],
                    "mount_id": h.get("mount_id"),
                })
        return {"query": q, "scope": scope, "count": len(hits), "hits": hits}
    except Exception as exc:  # noqa: BLE001 — search must degrade to empty, never 500 the overlay
        logger.warning("library search failed: %s", exc)
        return {"query": q, "hits": [], "error": "search unavailable"}


@router.post("/inbox")
async def drop_to_inbox(source_path: str = Query(...)) -> dict:
    """Copy a SINGLE existing file into Knowledge/Inbox/ (the one copy-in exception;
    directories are mounted, never copied). Returns the landed relative path."""
    from core.library_inbox import copy_to_inbox
    kdir = _knowledge_dir()
    try:
        landed = copy_to_inbox(kdir, Path(source_path))
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "landed", "path": f"Knowledge/{landed.relative_to(kdir).as_posix()}"}


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
