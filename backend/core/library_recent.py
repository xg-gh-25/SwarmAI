"""What changed in the Library lately, and who wrote it (GET /api/library/recent).

A walk of ``Knowledge/`` for the files changed in the last N days (default 7) and the N
days before, without the files that are not content:

- ``*.lock`` flock sidecars (``utils.file_lock.md_lock`` keeps one beside every markdown
  file it guards; they must stay on disk, they are just not news);
- ``*.jsonl`` machine twins of a markdown file;
- sqlite files and their ``-shm`` / ``-wal`` / ``-journal`` sidecars;
- everything under ``Knowledge/Library/mounts/`` (a mount's own index, not knowledge);
- dotfiles and dot-directories.

Who wrote a file (``source``) comes from CDF's own write journal, not from its folder: a
path CDF wrote within the window (``ingestion.db`` ``ingestion_writes``, an
``ingestion_unit`` write that reached ``done``) is ``cdf``, with ``cdf_units`` the number
of such writes to it; otherwise a file under JobResults / DailyActivity / Archives is
``job``; everything else is ``chat``. The journal is read read-only; a missing or
unreadable ``ingestion.db`` only means no ``cdf`` attribution, never an error.

Key exports: ``scan_recent``, ``cdf_writes_since``, ``is_recent_noise``, ``ITEM_CAP``.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ITEM_CAP = 200
JOB_CATEGORIES = frozenset({"JobResults", "DailyActivity", "Archives"})
_SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_SQLITE_SIDECARS = ("-shm", "-wal", "-journal")
_MOUNTS_PARTS = ("Library", "mounts")


def is_recent_noise(rel_parts: tuple[str, ...]) -> bool:
    """True for a Knowledge-relative path that is infrastructure, not content."""
    if not rel_parts:
        return True
    if any(p.startswith(".") for p in rel_parts):
        return True
    if rel_parts[:2] == _MOUNTS_PARTS:
        return True
    name = rel_parts[-1].lower()
    if name.endswith((".lock", ".jsonl")):
        return True
    if name.endswith(_SQLITE_SUFFIXES):
        return True
    return any(name.endswith(s + side) for s in _SQLITE_SUFFIXES for side in _SQLITE_SIDECARS)


def cdf_writes_since(since: float) -> dict[str, int]:
    """{workspace-relative path: done CDF unit writes since *since* (epoch s)} for paths
    under Knowledge/. Read-only; {} when ingestion.db is missing or unreadable."""
    from core.ingestion.api import connect_ro
    # A write's `updated_at` is stamped when its step turns 'done' (writer.py), i.e. when
    # the file was written; `created_at` is when it was planned, which can fall before
    # the window. Stamps are datetime.isoformat() in UTC: "...:32.697264+00:00", or
    # "...:32+00:00" when the microseconds are 0. A seconds cutoff orders both right
    # ("+" < "."), so the string compare is the time compare.
    cutoff = datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds")
    try:
        with closing(connect_ro()) as conn:
            rows = conn.execute(
                "SELECT target_path, COUNT(*) FROM ingestion_writes "
                "WHERE step = 'done' AND op_kind = 'ingestion_unit' "
                "AND target_path LIKE 'Knowledge/%' AND updated_at >= ? "
                "GROUP BY target_path",
                (cutoff,),
            ).fetchall()
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — attribution is best effort, never an error
        logger.warning("library recent: ingestion.db unreadable, no cdf attribution: %s", exc)
        return {}
    return {str(path): int(n) for path, n in rows}


def _walk(kdir: Path):
    """(Knowledge-relative parts, path) of every content file, pruning noise dirs."""
    for root, dirs, files in os.walk(kdir):
        rel_root = Path(root).relative_to(kdir).parts
        dirs[:] = [d for d in dirs if not d.startswith(".") and (*rel_root, d)[:2] != _MOUNTS_PARTS]
        for name in files:
            parts = (*rel_root, name)
            if not is_recent_noise(parts):
                yield parts, Path(root) / name


def _source(path: str, category: str, cdf: dict[str, int]) -> str:
    if path in cdf:
        return "cdf"
    if category in JOB_CATEGORIES:
        return "job"
    return "chat"


def scan_recent(kdir: Path, days: int = 7, now: float | None = None,
                cdf: dict[str, int] | None = None) -> dict:
    """The recent feed: this window's items newest first (capped at ITEM_CAP), and the
    counts this window / the window before / by source, taken before the cap."""
    now = time.time() if now is None else now
    span = days * 24 * 3600
    cutoff, prev_cutoff = now - span, now - 2 * span
    if cdf is None:
        cdf = cdf_writes_since(cutoff)
    items: list[dict] = []
    last_week = 0
    for parts, p in _walk(kdir):
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_mtime < prev_cutoff:
            continue
        if st.st_mtime < cutoff:
            last_week += 1
            continue
        rel = "/".join(parts)
        category = parts[0] if len(parts) > 1 else "(root)"
        path = f"Knowledge/{rel}"
        source = _source(path, category, cdf)
        items.append({
            "path": path,
            "category": category,
            "mtime": st.st_mtime,
            "size": st.st_size,
            "source": source,
            "cdf_units": cdf.get(path, 0) if source == "cdf" else 0,
        })
    items.sort(key=lambda it: it["mtime"], reverse=True)
    by_source = {"cdf": 0, "chat": 0, "job": 0}
    for it in items:
        by_source[it["source"]] += 1
    return {
        "window_days": days,
        "count": len(items),
        "this_week": len(items),
        "last_week": last_week,
        "by_source": by_source,
        "items": items[:ITEM_CAP],
    }
