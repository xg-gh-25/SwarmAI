"""A Brain's Eval: two questions about its knowledge (design
docs/design/2026-11-01-brain-eval-and-runs.md §2.2). A Brain holds knowledge only, so
nothing here measures runs as runs (their autonomy and cost live in Pipelines and
Swarm Out); runs are read only to see whether the Brain's knowledge was used.

- **health** — is what the Brain holds right and clean: of the entries written into the
  Brain that the CDF health judge scored (destinations DDD and GAP, the person's verdict
  over the judge's), the share judged useful, against the target; the entries judged
  weak / withdraw that are still in force (where they are); exact duplicates across the
  main files (PRODUCT / TECH / IMPROVEMENT / PROJECT, which the per-file sweep does not
  compare); the open gaps and how old the oldest is.
- **used** — did the knowledge reach the work: of the Brain's pipeline runs that wrote a
  REQUIREMENTS or DESIGN_SPEC, the share whose ``knowledge_refs`` cite an entry of this
  Brain that is still in its main files (only DDD ids: a ref may also name a Library
  passage or a pipeline lesson); missed use, a run's REFLECT lesson that CDF found to say
  the same as an entry the Brain already held (S3 units judged ``reinforce``); and the
  share of entries written more than the north-star window ago that were later read or
  cited (``status.north_star`` for this project).

A rate needs ``MIN_SAMPLE`` items: below it the rate is None with the count (shown as
"too few (n)"); a store that cannot be read gives None, never 0. Reads only:
``cdf_health.db`` and ``ingestion.db`` are opened read-only, run files and DDD docs are
read from disk.

Public: ``compute(name, *, workspace, now)``; ``snapshot(name, *, workspace, refresh)``
(the panel's read: computed once and kept ``SNAPSHOT_TTL``).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.project_registry import DDD_CANONICAL_DOCS

logger = logging.getLogger(__name__)

MIN_SAMPLE = 10
WINDOW = timedelta(days=30)
TARGET = 0.9
MAIN_DOCS = DDD_CANONICAL_DOCS
SHOWN = 20                     # drill-down rows per list
SNAPSHOT_TTL = 600.0
_DOC_FILES = ("requirements.json", "design_spec.json")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _app_dir() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir())


def _rate(hit: int, n: int) -> float | None:
    return round(hit / n, 4) if n >= MIN_SAMPLE else None


# ── health ──────────────────────────────────────────────────────────────────────

def _verdicts(project: str, since: datetime) -> list[sqlite3.Row] | None:
    conn = _ro(_app_dir() / "cdf_health.db")
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT * FROM verdicts WHERE project=? AND dest IN ('DDD','GAP') AND written_at >= ? "
            "ORDER BY written_at DESC", (project, since.isoformat())).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _texts(root: Path) -> list[tuple[str, str]]:
    """The Brain's docs as (path relative to the workspace, text)."""
    out = []
    for path in sorted(root.rglob("*.md")):
        if ".artifacts" in path.parts:
            continue
        try:
            out.append((str(path.relative_to(root.parent.parent)), path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return out


def _where(unit_id: str, texts: list[tuple[str, str]]) -> tuple[str, int] | None:
    """Where an entry is in force now: the file and line carrying its ``cand:`` marker."""
    marker = re.compile(rf"cand:{re.escape(unit_id)}(?![\w.-])")
    for rel, text in texts:
        found = marker.search(text)
        if found:
            return rel, text.count("\n", 0, found.start()) + 1
    return None


def _duplicates(units: list) -> dict:
    """Exact duplicates among the main files' entries (``content_signature`` with the
    markers removed): groups of two or more, and the extra copies."""
    from core.ddd_entry_lifecycle import content_signature
    groups: dict[str, list] = defaultdict(list)
    for u in units:
        sig = content_signature(_COMMENT.sub("", u.text))
        if sig:
            groups[sig].append(u)
    dups = [g for g in groups.values() if len(g) > 1]
    dups.sort(key=lambda g: -len(g))
    return {"groups": len(dups), "extra": sum(len(g) - 1 for g in dups),
            "shown": [{"head": g[0].first_line(g[0].text),
                       "places": [{"rel": u.rel, "line": u.line} for u in g]} for g in dups[:SHOWN]]}


def _gaps(project: str, now: datetime) -> dict:
    from core.ingestion.gaps import all_open_gaps
    rows = all_open_gaps(project)
    dated = sorted((r for r in rows if r.get("date")), key=lambda r: r["date"])
    oldest = dated[0] if dated else None
    days = None
    if oldest:
        try:
            days = (now.date() - datetime.fromisoformat(oldest["date"]).date()).days
        except ValueError:
            days = None
    return {"open": len(rows), "oldest_days": days, "oldest_title": oldest["title"] if oldest else None}


def health(project: str, root: Path | None, now: datetime) -> dict:
    rows = _verdicts(project, now - WINDOW)
    out: dict[str, Any] = {"target": TARGET, "min_sample": MIN_SAMPLE}
    if rows is None:
        out.update(rate=None, scored=None, since=None, counts=None, flagged=[])
    else:
        eff = [(r, r["user_verdict"] or r["verdict"]) for r in rows]
        counts = {v: sum(e == v for _, e in eff) for v in ("useful", "weak", "withdraw")}
        texts = _texts(root) if root is not None else []
        flagged = []
        for r, e in eff:
            if e == "useful":
                continue
            where = _where(r["unit_id"], texts)
            if where is None:
                continue                      # already gone from the Brain: nothing to handle
            flagged.append({"unit_id": r["unit_id"], "dest": r["dest"], "title": r["title"],
                            "verdict": e, "klass": r["klass"], "reason_en": r["reason_en"],
                            "reason_zh": r["reason_zh"], "by_person": bool(r["user_verdict"]),
                            "rel": where[0], "line": where[1]})
        out.update(rate=_rate(counts["useful"], len(rows)), scored=len(rows),
                   since=min((r["written_at"] for r in rows), default=None), counts=counts,
                   flagged=flagged[:SHOWN], flagged_total=len(flagged))
    units = []
    if root is not None:
        from core.addressing import ddd_units
        units = [u for u in ddd_units(project).units
                 if u.store == "doc" and u.kind == "entry" and u.doc in MAIN_DOCS]
    out["entries"] = len(units) if root is not None else None
    out["duplicates"] = _duplicates(units) if root is not None else None
    out["gaps"] = _gaps(project, now) if root is not None else None
    return out


# ── used ────────────────────────────────────────────────────────────────────────

def _refs(run_dir: Path) -> tuple[bool, list[str]]:
    """(wrote a document that can cite, the ids it cited)."""
    wrote, refs = False, []
    for name in _DOC_FILES:
        path = run_dir / "artifacts" / name
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        wrote = True
        for ref in (doc.get("knowledge_refs") if isinstance(doc, dict) else None) or []:
            if isinstance(ref, str) and ref not in refs:
                refs.append(ref)
    return wrote, refs


def _missed(project: str) -> list[dict] | None:
    conn = _ro(_app_dir() / "ingestion.db")
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT u.unit_id, i.source_ref, i.payload, u.updated_at FROM ingestion_units u "
            "JOIN ingestion_inbox i ON i.id = u.candidate_id "
            "WHERE i.source='S3' AND u.action='reinforce' AND u.project=? "
            "ORDER BY u.updated_at DESC", (project,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    from core.ingestion.health import title_of
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        out.append({"unit_id": r["unit_id"], "run_id": r["source_ref"], "at": r["updated_at"],
                    "title": title_of(payload if isinstance(payload, dict) else {})})
    return out


def _written_used(project: str, now: datetime) -> dict | None:
    conn = _ro(_app_dir() / "ingestion.db")
    if conn is None:
        return None
    try:
        from core.ingestion.status import north_star
        ns = north_star(conn, now=now, project=project)
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return {"rate": ns.get("used_rate") if (ns.get("mature_written") or 0) >= MIN_SAMPLE else None,
            "mature_written": ns.get("mature_written") or 0, "mature_used": ns.get("mature_used") or 0,
            "young_written": ns.get("young_written") or 0, "window_days": ns.get("window_days")}


def used(project: str, root: Path | None, workspace: Path, now: datetime) -> dict:
    from core import run_index
    in_force: dict[str, Any] = {}
    if root is not None:
        from core.addressing import ddd_units
        in_force = {u.id: u for u in ddd_units(project).units
                    if u.store == "doc" and u.kind == "entry" and u.doc in MAIN_DOCS}
    runs = []
    for r in run_index.recent(workspace, days=WINDOW.days):
        if r.path.parents[3].name != (root.name if root is not None else project):
            continue
        wrote, refs = _refs(r.path.parent)
        if not wrote:
            continue
        data = r.data if isinstance(r.data, dict) else {}
        cited = [{"id": i, "head": in_force[i].first_line(in_force[i].text), "rel": in_force[i].rel,
                  "line": in_force[i].line} for i in refs if i in in_force]
        runs.append({"run_id": r.path.parent.name, "created_at": data.get("created_at"),
                     "requirement": str(data.get("requirement") or "")[:200], "cited": cited,
                     "other_refs": len(refs) - len(cited)})
    runs.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    citing = sum(1 for x in runs if x["cited"])
    missed = _missed(project)
    return {"min_sample": MIN_SAMPLE, "runs": len(runs), "citing": citing,
            "rate": _rate(citing, len(runs)) if root is not None else None,
            "since": min((x["created_at"] for x in runs if x.get("created_at")), default=None),
            "shown": runs[:SHOWN], "missed": None if missed is None else missed[:SHOWN],
            "missed_total": None if missed is None else len(missed),
            "written_used": _written_used(project, now)}


# ── entry points ────────────────────────────────────────────────────────────────

def compute(name: str, *, workspace: Path, now: datetime | None = None) -> dict:
    from core.addressing import project_root
    current = _now(now)
    root = project_root(name)
    project = root.name if root is not None else name
    return {"project": project, "computed_at": current.isoformat(), "window_days": WINDOW.days,
            "health": health(project, root, current), "used": used(project, root, workspace, current)}


_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()


def snapshot(name: str, *, workspace: Path, refresh: bool = False) -> dict:
    """``compute`` once per ``SNAPSHOT_TTL`` per Brain: the panel reads the snapshot."""
    key = name.lower()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and not refresh and time.monotonic() - hit[0] < SNAPSHOT_TTL:
        return hit[1]
    result = compute(name, workspace=workspace)
    with _LOCK:
        _CACHE[key] = (time.monotonic(), result)
    return result
