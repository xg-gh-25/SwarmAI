"""session_outputs — which files each chat session produced (the Canvas Artifacts tab).

The ONE record of "session S wrote file F", written by the backend AS THE TURN RUNS
(session_router, on every ``file_changed`` of a rail kind), so the Canvas reads a
single source: live rows and a reloaded / restarted session come from the same file.
It records attribution only. WHAT a file is (product role, label, favorite) stays with
the workspace product catalog (core/product_registry); canvas_session joins the two.

Store: ``<app data>/state/canvas_sessions/<session_id>.json`` (outside SwarmWS, so the
workspace watcher never sees its writes), one small file per session:
``{"version": 1, "rows": {absolutePath: {path, absolutePath, kind, firstAt, lastAt,
writes, deleted, baseRef?}}, "backfilled": bool}``. Writes are flock-serialized and
atomic (temp + replace). At most MAX_ROWS rows per session (oldest lastAt dropped).

Ordering contract: ``record`` and ``read`` run on the single-worker ``canvas_session``
pool (core/executors) and the router SUBMITS the write before it forwards the event to
the client; a refresh the client sends on that event is queued behind the write, so it
always sees the row.

Kinds recorded = the rail kinds (frontend railSsot.isRailKind): content, knowledge,
source-final, external-diff, external-nodiff. A ``deleted`` operation marks the row
(the rail shows it struck through); a later write clears the mark.

Public: ``record``, ``read``, ``backfill``, ``forget``, ``external_paths``, ``RAIL_KINDS``, ``MAX_ROWS``, ``store_dir``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

RAIL_KINDS = frozenset({"content", "knowledge", "source-final", "external-diff", "external-nodiff"})
MAX_ROWS = 300
_VERSION = 1
_SID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def store_dir() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "state" / "canvas_sessions"


def _path(session_id: str) -> Optional[Path]:
    if not isinstance(session_id, str) or not _SID.fullmatch(session_id) or session_id.startswith("."):
        return None
    return store_dir() / f"{session_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("rows"), dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        logger.warning("session_outputs: unreadable %s: %s", p, exc)
    return {"version": _VERSION, "rows": {}}


def _mutate(session_id: str, fn) -> None:
    p = _path(session_id)
    if p is None:
        return
    from utils.file_lock import flock_exclusive, flock_unlock
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(f".{p.name}.lock"), "w") as lock:
        flock_exclusive(lock)
        try:
            data = _load(p)
            if fn(data) is False:
                return
            rows = data["rows"]
            if len(rows) > MAX_ROWS:
                for key in sorted(rows, key=lambda k: rows[k].get("lastAt", ""))[: len(rows) - MAX_ROWS]:
                    rows.pop(key)
            fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.stem}-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, p)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        finally:
            flock_unlock(lock)


def _apply(rows: dict, ev: dict, at: str) -> bool:
    """Fold one file_changed event into *rows*; False when it is not a rail row."""
    ap = ev.get("absolutePath") or ev.get("path")
    if not isinstance(ap, str) or not ap:
        return False
    if ev.get("operation") == "deleted":
        row = rows.get(ap)
        if row is None:
            return False
        row.update(deleted=True, lastAt=at)
        return True
    if ev.get("kind") not in RAIL_KINDS:
        return False
    row = rows.get(ap)
    if row is None:
        row = rows[ap] = {"path": str(ev.get("path") or ap), "absolutePath": ap, "kind": ev["kind"],
                          "firstAt": at, "lastAt": at, "writes": 0, "deleted": False}
    row.update(kind=ev["kind"], lastAt=at, deleted=False, writes=row.get("writes", 0) + 1)
    if isinstance(ev.get("baseRef"), str) and ev["baseRef"]:
        row["baseRef"] = ev["baseRef"]
    return True


def record(session_id: str, events: Iterable[dict], at: Optional[str] = None) -> None:
    """Record the rail rows of *events* (file_changed dicts) for *session_id*. Blocking."""
    evs = [e for e in events if isinstance(e, dict)]
    if not evs:
        return
    stamp = at or _now()

    def fn(data: dict):
        changed = False
        for ev in evs:
            changed = _apply(data["rows"], ev, stamp) or changed
        return changed

    _mutate(session_id, fn)


def read(session_id: str) -> dict:
    """``{"rows": [...], "backfilled": bool}`` (rows newest lastAt first). Blocking."""
    p = _path(session_id)
    data = _load(p) if p else {"rows": {}}
    rows = sorted(data["rows"].values(), key=lambda r: r.get("lastAt", ""), reverse=True)
    return {"rows": rows, "backfilled": bool(data.get("backfilled"))}


def backfill(session_id: str, turns: Iterable[tuple[str, list[dict]]]) -> None:
    """One-time import for a session recorded before this store existed: *turns* are
    ``(created_at, turn_deliverables files)`` oldest first. Rows already recorded live
    win; marks the session backfilled so it never runs again. Blocking."""
    turns = list(turns)

    def fn(data: dict):
        if data.get("backfilled"):
            return False
        live = set(data["rows"])
        for at, files in turns:
            for f in files:
                ap = f.get("absolutePath") or f.get("path") if isinstance(f, dict) else None
                if ap and ap not in live:
                    _apply(data["rows"], {**f, "operation": "written"}, str(at))
        data["backfilled"] = True
        return True

    _mutate(session_id, fn)


EXTERNAL_KINDS = frozenset({"external-diff", "external-nodiff"})


def external_paths(session_id: str) -> set[str]:
    """Resolved absolute paths of the session's external rows (outside SwarmWS):
    the paths the live emit gate admitted for this session (session_registry.
    surfaced_paths), kept across a restart for GET /workspace/file. Blocking."""
    out = set()
    for r in read(session_id)["rows"]:
        if r.get("kind") in EXTERNAL_KINDS:
            try:
                out.add(str(Path(r["absolutePath"]).resolve()))
            except (OSError, RuntimeError, KeyError):
                continue
    return out


def forget(session_id: str) -> None:
    """Delete a session's record (the session was deleted). Blocking, never raises."""
    p = _path(session_id)
    if p is None:
        return
    for q in (p, p.with_name(f".{p.name}.lock")):
        try:
            q.unlink(missing_ok=True)
        except OSError:
            logger.debug("session_outputs: could not remove %s", q, exc_info=True)
