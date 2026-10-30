"""Open gaps of a project, and closing the ones a pipeline run fixed.

Design ``docs/design/2026-10-30-cdf-gap-lifecycle.md``. A gap is an entry CDF wrote to
a project's IMPROVEMENT "What to Watch For" with a ``gap kind:`` and its
``<!-- cand:<id>.unit -->`` marker; its id is that candidate id. It is open while its
type marker is not ``[x]`` (the done mark every reader already skips: ``addressing``,
``recall_multi``).

- ``open_gaps``: the open gaps a run is shown (``pipeline-v1 events``), at most
  GAP_LIST_TOP. ``open_gap_ids``: every open gap of the project, what an event may
  close (``reflect.prepare`` checks it, and S3 enqueue again: the report file is
  writable after prepare).
- ``close_gap``: mark one gap ``[x]`` through the governed-edit path (journaled, archived
  first, optimistic on the file's revision), with who closed it. The cand marker is
  kept, so the entry's anchor and id stay the same.
- ``close_declared``: for every written S3 event whose report declared ``closes``, close
  each of those gaps once, recording each result. Called right after the event is
  written and by the daily maintenance, which retries a close the user's concurrent
  edit made stale.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts_v2 import GAP_LIST_TOP

logger = logging.getLogger(__name__)

SECTION = "What to Watch For"
_PREFIX = re.compile(r"^- \[([^\]\s]+)\] ")
_KIND = re.compile(r"gap kind:\s*([a-z_]+)")
_TITLE = re.compile(r"\*\*(.+?)\*\*")
_NOTE_UNSAFE = re.compile(r"<!--|-->|[\r\n]+|(?i:cand):")
CLOSED_EVENT = "gaps_closed"


def _improvement(project: str | None) -> Path | None:
    from core.addressing import project_root
    from core.ddd_paths import ddd_path
    from core.project_registry import canonical_project_name
    # the folder's own spelling: the path is recorded (journal, anchors), and a name in
    # another case opens the folder on macOS but not on Linux
    root = project_root(canonical_project_name(project or ""))
    if root is None:
        return None
    path = ddd_path(root, "IMPROVEMENT.md")
    return path if path.is_file() and not path.is_symlink() else None


def _gap_id(unit: Any) -> str | None:
    identity = str(unit.identity)
    if not identity.startswith("cand:cand-"):
        return None
    return identity[len("cand:"):].removesuffix(".unit")


def _gap_units(path: Path) -> list[Any]:
    from config import get_swarmws
    from .units import parse_units
    return [unit for unit in parse_units(path, root=get_swarmws())
            if unit.section == SECTION and "gap kind:" in unit.text and _gap_id(unit)]


def _is_open(unit: Any) -> bool:
    match = _PREFIX.match(unit.text)
    return bool(match) and match.group(1).lower() != "x"


def is_closed_gap(anchor: str, text: str) -> bool:
    """A closed gap: a done (``[x]``) gap entry of an IMPROVEMENT "What to Watch For"
    (the dedup reads it as a recurrence, not a duplicate; any other done item is not)."""
    path, _, rest = str(anchor).partition("#")
    return (path.endswith("IMPROVEMENT.md") and rest.startswith(SECTION + "#")
            and "gap kind:" in text and bool(_PREFIX.match(text.lstrip()))
            and _PREFIX.match(text.lstrip()).group(1).lower() == "x")


def _view(unit: Any) -> dict[str, str]:
    title = _TITLE.search(unit.text)
    kind = _KIND.search(unit.text)
    return {"id": str(_gap_id(unit)), "title": title.group(1).strip() if title else "",
            "gap_kind": kind.group(1) if kind else ""}


def run_query(run_dir: Path | str | None) -> str:
    """The text a run's open-gap list is ranked by: its REQUIREMENTS, else nothing."""
    if run_dir is None:
        return ""
    try:
        return (Path(run_dir) / "REQUIREMENTS.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _open_units(project: str | None) -> list[Any]:
    """The open gap entries of *project*; none (logged) when its IMPROVEMENT cannot be
    read: a broken file never stops a run from listing its events or delivering."""
    path = _improvement(project)
    if path is None:
        return []
    try:
        return [unit for unit in _gap_units(path) if _is_open(unit)]
    except (OSError, UnicodeDecodeError, ValueError):
        logger.warning("open gaps of %s not readable", project, exc_info=True)
        return []


def open_gap_ids(project: str | None) -> set[str]:
    """Every open gap id of *project*: what an event may close. The list a run is shown
    is capped; what it may close is not (the cap is about the run's attention, and the
    list recomputed later would not be the one shown once another gap is written)."""
    return {str(_gap_id(unit)) for unit in _open_units(project)}


def open_gaps(project: str | None, query: str = "") -> list[dict[str, str]]:
    """The open gaps a run is shown, at most GAP_LIST_TOP: all of them when they fit,
    else the ones BM25 ranks highest for *query* (ties and no query: newest first)."""
    units = _open_units(project)
    if len(units) > GAP_LIST_TOP:
        from core.memory_index import _bm25_scores
        order = {str(_gap_id(u)): i for i, u in enumerate(units)}
        scores = _bm25_scores(query, {str(_gap_id(u)): u.text for u in units}) if query else {}
        units.sort(key=lambda u: (-scores.get(str(_gap_id(u)), 0.0), -order[str(_gap_id(u))]))
        units = sorted(units[:GAP_LIST_TOP], key=lambda u: order[str(_gap_id(u))])
    return [_view(unit) for unit in units]


def close_gap(project: str | None, gap_id: str, *, closed_by: str, note: str,
              on: str | None = None) -> str:
    """Withdraw one open gap a run fixed (design 2026-10-30-brain-driven §2.2): it leaves the
    main file, and its text, the closing note and status ``closed`` go into the records,
    where recurrence detection reads them (``withdrawn``). Return closed | already_closed |
    not_found | unparseable | stale (only ``stale`` is worth trying again)."""
    from .governed_edit import StaleRevision, withdraw
    path = _improvement(project)
    if path is None:
        return "not_found"
    data = path.read_bytes()
    if b"\r" in data:
        return "unparseable"                  # the parser's offsets are of \n text
    unit = next((u for u in _gap_units(path) if _gap_id(u) == gap_id), None)
    if unit is None:
        return "already_closed" if _closed_before(gap_id) else "not_found"
    if not _is_open(unit):
        return "already_closed"
    if data[unit.byte_start:unit.byte_end] != unit.text.encode("utf-8"):
        return "stale"                        # the parse is of another version
    day = on or datetime.now(timezone.utc).date().isoformat()
    reason = _NOTE_UNSAFE.sub(" ", f"已由 {closed_by} 关闭：{note}，{day}").strip()
    try:
        withdraw(unit.anchor, reason[:500], actor=f"cdf:{closed_by}", status="closed")
    except (StaleRevision, ValueError):
        return "stale"
    return "closed"


def _closed_before(gap_id: str) -> bool:
    """Whether a gap with this id was already withdrawn as closed (the records)."""
    from .api import connect_ro
    from .withdrawn import records
    try:
        conn = connect_ro()
    except Exception:  # noqa: BLE001 -- unreadable records: not known closed
        return False
    try:
        return any(r.status == "closed" and gap_id in r.text for r in records(conn))
    finally:
        conn.close()


def withdraw_ticked(workspace: Path, *, files: "list[Path] | None" = None) -> int:
    """Withdraw every entry ticked ``- [x]`` from the main cognition files: a gap as
    ``closed``, anything else as ``done`` (design 2026-10-30-brain-driven §2.2: a finished
    item is mechanically not in force). Returns how many were withdrawn; one that a
    concurrent edit made stale is left for the next pass."""
    from .governed_edit import StaleRevision, withdraw
    from .units import parse_units
    targets = files if files is not None else main_cognition_files(workspace)
    count = 0
    for path in targets:
        try:
            units = parse_units(path, root=workspace)
        except (OSError, ValueError):
            logger.warning("withdraw_ticked: %s unreadable", path, exc_info=True)
            continue
        for unit in units:
            if not unit.text.lstrip().startswith("- [x]"):
                continue
            status = "closed" if "gap kind:" in unit.text else "done"
            try:
                withdraw(unit.anchor, "ticked done", actor="cdf:maintenance", status=status)
                count += 1
            except (StaleRevision, ValueError, PermissionError):
                logger.info("withdraw_ticked: %s left for the next pass", unit.anchor, exc_info=True)
    return count


def main_cognition_files(workspace: Path) -> list[Path]:
    """The four DDD documents of every project, plus MEMORY and EVOLUTION."""
    from core.project_registry import DDD_CANONICAL_DOCS
    from core.ddd_paths import ddd_path
    out: list[Path] = []
    projects = workspace / "Projects"
    if projects.is_dir():
        for project in sorted(p for p in projects.iterdir() if p.is_dir() and not p.name.startswith(".")):
            out += [p for doc in DDD_CANONICAL_DOCS
                    if (p := ddd_path(project, "understanding") / doc).is_file()]
    out += [p for n in ("MEMORY.md", "EVOLUTION.md") if (p := workspace / ".context" / n).is_file()]
    return out


_DECLARED = """SELECT u.unit_id, i.payload, i.project_snapshot, i.source_ref
    FROM ingestion_units u JOIN ingestion_inbox i ON u.candidate_id = i.id
    WHERE u.state = 'written' AND COALESCE(i.source_v2, i.source) = 'S3'
      AND i.extraction_pass = 'main' AND i.unit_kind = 'event'
      AND json_extract(i.payload, '$.closes') IS NOT NULL {one}
    ORDER BY u.unit_id"""
_RECORD = """INSERT INTO ingestion_events(at,kind,detail) SELECT ?,?,?
    WHERE NOT EXISTS (SELECT 1 FROM ingestion_events WHERE kind = ?
      AND json_extract(detail, '$.unit_id') = ? AND json_extract(detail, '$.gap') = ?)"""


def _recorded(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    rows = conn.execute("SELECT json_extract(detail,'$.unit_id'), json_extract(detail,'$.gap') "
                        "FROM ingestion_events WHERE kind = ?", (CLOSED_EVENT,))
    return {(str(a), str(b)) for a, b in rows}


# the run lane (right after a write) and the scheduler's maintenance both call
# close_declared, in the one daemon process; one at a time, so a gap closed by one is
# never recorded as "already_closed" by the other
_LOCK = threading.Lock()


def close_declared(conn: sqlite3.Connection, unit_id: str | None = None) -> int:
    with _LOCK:
        return _close_declared(conn, unit_id)


def _close_declared(conn: sqlite3.Connection, unit_id: str | None = None) -> int:
    """Close the gaps written S3 run events declared; return how many were closed.

    Only a run's own report (S3, main pass) can close a gap, only an open gap of the
    run's project, and only when the run landed (``closes`` is dropped at enqueue
    otherwise). Each gap's result is recorded once, as it is reached; a ``stale`` one
    (the file changed under it) is not, so the next call retries it. An error on one
    gap is recorded as that gap's result and never stops the others."""
    sql = _DECLARED.format(one="AND u.unit_id = ?" if unit_id else "")
    rows = conn.execute(sql, (unit_id,) if unit_id else ()).fetchall()
    done = _recorded(conn) if rows else set()
    closed = 0
    for row in rows:
        payload = json.loads(row[1] or "{}")
        snapshot = json.loads(row[2] or "{}")
        project = snapshot.get("project") if isinstance(snapshot, dict) else None
        for gap in dict.fromkeys(payload.get("closes") or []):
            if (str(row[0]), gap) in done:
                continue
            try:
                result = close_gap(project, gap, closed_by=str(row[3]),
                                   note=str(payload.get("title") or ""))
            except Exception as exc:  # noqa: BLE001 — recorded, never retried forever
                logger.warning("CDF gap close %s of %s refused", gap, row[0], exc_info=True)
                result = f"error:{type(exc).__name__}"
            if result == "stale":
                logger.info("CDF gap close %s of %s is stale; retried later", gap, row[0])
                continue
            closed += result == "closed"
            detail = json.dumps({"unit_id": row[0], "gap": gap, "run_id": row[3],
                                 "project": project, "result": result},
                                ensure_ascii=False, sort_keys=True)
            conn.execute(_RECORD, (datetime.now(timezone.utc).isoformat(), CLOSED_EVENT,
                                   detail, CLOSED_EVENT, row[0], gap))
            conn.commit()
    return closed


__all__ = ["open_gaps", "open_gap_ids", "close_gap", "close_declared", "is_closed_gap",
           "run_query", "SECTION"]
