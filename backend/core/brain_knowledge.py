"""What a Brain has learned: the CDF-written entries of its main files, by layer and type
(design docs/design/2026-10-30-brain-change-feed.md).

Read from the files and the records, never from git: a user's own Brains are not in git
(``Projects/*`` is ignored), so a git-based review showed nothing for them.

- A **project Brain** is its four canonical documents (``2-understanding/{PRODUCT,TECH,
  IMPROVEMENT,PROJECT}.md``). The **General Brain** is the top-level Markdown files of
  ``Knowledge/Library`` (CDF writes one per subject, ``contracts_v2.route_general``); a
  file with no CDF entry simply contributes none.
- **One entry** is a unit whose identity is ``cand:<unit id>`` and whose text carries the
  ``<!-- cand:`` marker (a hand-written entry is not shown), that is not an old withdrawal
  tombstone, and that is not the object of an active ``supersedes`` relation.
- Each entry: anchor, doc, section, type, layer (from the type), title, text, when it was
  written (the write record, else the evaluation archive, else unknown), where it came
  from (source and reference of its candidate), and the entry it adds to (an active
  ``derived_from`` relation whose object is still in a file).
- A gap (``[gap:<kind>]``) is listed apart: it is no cognition type. An entry of any
  other type (no layer, not a gap) is not shown, and so not counted either: every count
  is of what the page lists.

Per-file parse results are cached by (path, workspace, mtime, size): a file that did not change is
not parsed again. A file that is not UTF-8 is skipped (logged), never fails the view.

Public: ``brain_files``, ``knowledge``, ``week_counts``, ``LAYERS``.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

LAYERS = ("meta", "cognitive", "operational")
RANGES = {"week": timedelta(days=7), "month": timedelta(days=30)}
_MARKER = re.compile(r"<!--\s*cand:")
_TOMBSTONE = re.compile(r"^- \[redacted\] \*\*Withdrawn\*\*")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TYPE = re.compile(r"^\s*-\s*\[([a-z]+)(?::([a-z_-]+))?\]")
_cache: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def brain_files(workspace: Path, name: str) -> list[Path]:
    """The main files of Brain *name*: the General Brain's top-level Library documents,
    or a project's four canonical documents (only those that exist). A withdrawal is
    matched against these paths, so a file whose last CDF entry was withdrawn keeps it."""
    from core.project_agent import general_brain_dir, is_general_name
    if is_general_name(name):
        lib = general_brain_dir(workspace)
        return sorted(p for p in lib.glob("*.md") if p.is_file()) if lib.is_dir() else []
    from core.ddd_paths import ddd_path
    from core.project_registry import DDD_CANONICAL_DOCS
    project = workspace / "Projects" / name
    return [p for doc in DDD_CANONICAL_DOCS if (p := ddd_path(project, doc)).is_file()]


def _parse(path: Path, workspace: Path) -> list[dict[str, Any]]:
    """The CDF entries and gaps of one file (cached by mtime and size)."""
    from core.ingestion.units import parse_units
    try:
        st = path.stat()
    except OSError:
        return []
    key, stamp = f"{workspace}\0{path}", (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        units = parse_units(path, root=workspace)
    except ValueError as exc:       # not UTF-8 (UnicodeDecodeError is a ValueError)
        logger.warning("brain knowledge: %s skipped (%s)", path, exc)
        return []
    out = []
    for u in units:
        if not u.identity.startswith("cand:") or not _MARKER.search(u.text):
            continue
        first = u.text.lstrip().split("\n", 1)[0]
        if _TOMBSTONE.match(first):
            continue
        m = _TYPE.match(first)
        kind = (m.group(1) if m else (u.entry_type or "")) or ""
        out.append({
            "anchor": u.anchor, "unit_id": u.identity[len("cand:"):], "doc": path.name,
            "path": u.path, "section": u.section, "type": kind,
            "gap_kind": (m.group(2) if m and kind == "gap" else None),
            "title": u.title, "text": _plain(u.text),
        })
    with _cache_lock:
        _cache[key] = (stamp, out)
    return out


def _plain(text: str) -> str:
    """The entry without its marker comments, whole."""
    return "\n".join(line.rstrip() for line in _COMMENT.sub("", text).strip().splitlines()).strip()


def _shown(entry: dict[str, Any]) -> bool:
    """An entry the page lists: one of the seven types (it has a layer) or a gap."""
    return entry["type"] == "gap" or _layer(entry["type"]) is not None


def _entries(workspace: Path, name: str) -> list[dict[str, Any]]:
    """The entries the page lists, so ``knowledge`` and ``week_counts`` count the same."""
    return [e for p in brain_files(workspace, name) for e in _parse(p, workspace) if _shown(e)]


def _records(conn: sqlite3.Connection | None, entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """unit id → {written_at, collected, source, source_ref} from the write record, else
    the evaluation archive (collected time only)."""
    if conn is None or not entries:
        return {}
    ids = [e["unit_id"] for e in entries]
    out: dict[str, dict[str, Any]] = {}
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        marks = ",".join("?" * len(chunk))
        for uid, at, src, ref in conn.execute(
                f"""SELECT u.unit_id,
                           (SELECT MAX(w.updated_at) FROM ingestion_writes w WHERE w.unit_id=u.unit_id
                              AND w.op_kind='ingestion_unit' AND w.step='done'),
                           COALESCE(i.source_v2, i.source), i.source_ref
                    FROM ingestion_units u
                    LEFT JOIN ingestion_inbox i ON i.id=u.candidate_id
                    WHERE u.unit_id IN ({marks})""", chunk):
            out[uid] = {"written_at": at, "collected": False, "source": src, "source_ref": ref}
        missing = [u for u in chunk if not (out.get(u) or {}).get("written_at")]
        if missing and _has(conn, "ingestion_eval_log"):
            m2 = ",".join("?" * len(missing))
            for uid, at, src in conn.execute(
                    f"SELECT unit_id, at, source FROM ingestion_eval_log WHERE unit_id IN ({m2})", missing):
                rec = out.setdefault(uid, {"source": src, "source_ref": None})
                rec.update(written_at=at, collected=True)
                rec["source"] = rec.get("source") or src
    return out


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def _relations(conn: sqlite3.Connection | None) -> tuple[set[str], dict[str, str]]:
    """(objects of an active supersedes, subject → object of an active derived_from)."""
    if conn is None or not _has(conn, "ingestion_relations"):
        return set(), {}
    replaced, adds = set(), {}
    for s, p, o in conn.execute(
            "SELECT subject_anchor, predicate, object_anchor FROM ingestion_relations WHERE inactive=0"):
        if p == "supersedes":
            replaced.add(o)
        elif p == "derived_from":
            adds.setdefault(s, o)
    return replaced, adds


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _connect():
    from core.ingestion.store import connect_ro
    try:
        return connect_ro()
    except (OSError, sqlite3.Error):
        return None


def knowledge(workspace: Path, name: str, since: str = "week",
              now: datetime | None = None) -> dict[str, Any]:
    """``{"layers": [{"layer", "items"}...], "gaps": [...], "counts": {"week", "all"},
    "withdrawn": {"count", "items"}}`` for Brain *name*, newest first; *since* is
    ``week`` / ``month`` / ``all`` (an entry of unknown time shows only under ``all``,
    last)."""
    now = now or datetime.now(timezone.utc)
    since = since if since in ("week", "month", "all") else "week"
    entries = _entries(workspace, name)
    conn = _connect()
    try:
        recs = _records(conn, entries)
        replaced, adds = _relations(conn)
        withdrawn = _withdrawn(conn, workspace, name)
    finally:
        if conn is not None:
            conn.close()
    by_anchor = {e["anchor"]: e for e in entries}
    shown: list[dict[str, Any]] = []
    for e in entries:
        if e["anchor"] in replaced:
            continue
        rec = recs.get(e["unit_id"], {})
        item = dict(e, written_at=rec.get("written_at"), collected=bool(rec.get("collected")),
                    source=rec.get("source"), source_ref=rec.get("source_ref"))
        target = by_anchor.get(adds.get(e["anchor"], ""))
        item["adds_to"] = target["title"] if target else None
        shown.append(item)
    week_from = now - RANGES["week"]
    counts = {"week": sum(1 for i in shown if (_when(i["written_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= week_from),
              "all": len(shown)}
    lower = now - RANGES[since] if since in RANGES else None

    def keep(item: dict[str, Any]) -> bool:
        at = _when(item["written_at"])
        return lower is None or (at is not None and at >= lower)

    kept = [i for i in shown if keep(i)]
    dated = [i for i in kept if _when(i["written_at"])]
    picked = (sorted(dated, key=lambda i: _when(i["written_at"]), reverse=True)
              + [i for i in kept if not _when(i["written_at"])])
    layers = [{"layer": layer, "items": [i | {"layer": layer} for i in picked
                                          if _layer(i["type"]) == layer]} for layer in LAYERS]
    gaps = [i for i in picked if i["type"] == "gap"]
    return {"brain": name, "since": since,
            "layers": layers, "gaps": gaps, "counts": counts, "withdrawn": withdrawn}


def _layer(kind: str) -> str | None:
    from core.ingestion.contracts_v2 import TYPE_TO_LAYER
    return TYPE_TO_LAYER.get(kind)


def _withdrawn(conn: sqlite3.Connection | None, workspace: Path, name: str) -> dict[str, Any]:
    """The withdrawals from this Brain's files that a person made (an undone one and a
    cleanup by the agent excluded), newest first: title, type, when, why."""
    if conn is None:
        return {"count": 0, "items": []}
    from core.ingestion import withdrawn as wd
    files = {str(p.relative_to(workspace)) for p in brain_files(workspace, name)}
    if not files:
        return {"count": 0, "items": []}
    items = []
    for path, at, absent, relations in conn.execute(
            "SELECT target_path, created_at, anchors_absent, relations FROM ingestion_writes "
            "WHERE op_kind='user_redact' AND step='done' ORDER BY created_at DESC"):
        if path not in files:
            continue
        try:
            item = (json.loads(relations or "[]") or [{}])[0]
            original = (json.loads(absent or "[]") or [""])[0]
        except (TypeError, ValueError, IndexError):
            continue
        if item.get("status") == wd.UNDONE or str(item.get("actor") or "user") != "user" \
                or item.get("status") != "withdrawn":
            continue
        first = str(original).lstrip().split("\n", 1)[0]
        m, title = _TYPE.match(first), re.search(r"\*\*(.+?)\*\*", first)
        items.append({"path": path, "doc": Path(path).name, "at": at, "reason": item.get("reason") or "",
                      "title": title.group(1) if title else first[:120],
                      "type": m.group(1) if m else "",
                      "written_at": item.get("written_at"), "source": item.get("source")})
    return {"count": len(items), "items": items}


def week_counts(workspace: Path, names: Iterable[str], now: datetime | None = None) -> dict[str, int]:
    """{brain: entries written in the last 7 days} for every *name*, in one pass."""
    now = now or datetime.now(timezone.utc)
    names = list(names)
    per = {n: _entries(workspace, n) for n in names}
    conn = _connect()
    try:
        recs = _records(conn, [e for es in per.values() for e in es])
        replaced, _adds = _relations(conn)
    finally:
        if conn is not None:
            conn.close()
    week_from = now - RANGES["week"]
    out = {}
    for n, es in per.items():
        out[n] = sum(1 for e in es if e["anchor"] not in replaced
                     and (_when(recs.get(e["unit_id"], {}).get("written_at")) or week_from - timedelta(1)) >= week_from)
    return out
