"""How CDF is doing right now, and whether what it writes is ever used.

- ``status`` is one read-only snapshot of ``ingestion.db``: the owner, the session-job
  queue and how long a due job has waited, extraction attempts, unit states, units and
  candidates that stopped moving, poisoned units by error class, unresolved events,
  today's writes, budgets and model calls (2026-10-29-cdf-post-launch.md §1).
- ``stalls`` is the part of it that needs a person: it feeds the Need-You overlay.
- ``north_star`` answers the question the rest cannot: of the entries CDF wrote, how
  many were later read or cited. An entry nobody reads changes no judgment, however
  well it was judged (2026-10-29: the read side bounds CDF's value).

Read-only; every function takes a connection or opens a read-only one.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

STUCK_AFTER = timedelta(hours=1)          # a unit or candidate that has not moved this long
JOB_OVERDUE_AFTER = timedelta(hours=2)     # a due session job that has not been taken
NORTH_STAR_WINDOW = timedelta(days=14)     # how long after its write an entry counts as used
_PER_TURN = (".context/MEMORY.md", ".context/EVOLUTION.md", ".context/STEERING.md",
             ".context/USER.md", ".context/TOOLS.md")
_USED_KINDS = ("read_by_agent", "cited_as_evidence")


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:       # a table an older store does not have yet
        return []


def _owner(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = _rows(conn, "SELECT epoch,pid,started_at FROM ingestion_owner WHERE id=1")
    if not rows:
        return {"pid": None, "alive": False, "started_at": None}
    epoch, pid, started = rows[0]
    try:
        import psutil
        alive: bool | None = bool(psutil.pid_exists(int(pid)))
    except Exception as exc:  # noqa: BLE001 - unknown is reported as unknown
        logger.debug("owner liveness unknown: %s", exc)
        alive = None
    return {"pid": int(pid), "epoch": int(epoch), "alive": alive, "started_at": started}


def stalls(conn: sqlite3.Connection, *, now: datetime | None = None) -> list[str]:
    """What has stopped moving, one line each; empty when CDF is healthy."""
    current = _now(now)
    before = (current - STUCK_AFTER).isoformat()
    out: list[str] = []
    owner = _owner(conn)
    if owner["pid"] is None or owner["alive"] is False:
        out.append("no live CDF owner: nothing is judged or written")
    stuck = _rows(conn, "SELECT state,COUNT(*) FROM ingestion_units WHERE state IN ('judged','writing') "
                        "AND updated_at<? GROUP BY state", (before,))
    for state, count in stuck:
        out.append(f"{count} unit(s) {state} for over an hour")
    candidates = _rows(conn, """SELECT COUNT(*) FROM ingestion_inbox i WHERE i.state='in_progress'
        AND COALESCE((SELECT MAX(u.updated_at) FROM ingestion_units u WHERE u.candidate_id=i.id),
                     i.created_at)<?""", (before,))
    if candidates and candidates[0][0]:
        out.append(f"{candidates[0][0]} candidate(s) in progress for over an hour")
    due = _rows(conn, "SELECT MIN(COALESCE(next_attempt_at, requested_at)) FROM ingestion_s2_jobs "
                      "WHERE state='queued' AND COALESCE(next_attempt_at, requested_at)<?",
                ((current - JOB_OVERDUE_AFTER).isoformat(),))
    if due and due[0][0]:
        waited = current - (_parse(due[0][0]) or current)
        out.append(f"a session job due {int(waited.total_seconds() // 60)} min ago has not started")
    running = _rows(conn, "SELECT COUNT(*) FROM ingestion_s2_jobs WHERE state='running' AND started_at<?",
                    (before,))
    if running and running[0][0]:
        out.append(f"{running[0][0]} session job(s) running for over an hour")
    return out


def north_star(conn: sqlite3.Connection, *, now: datetime | None = None,
               window: timedelta = NORTH_STAR_WINDOW,
               project: str | None = None) -> dict[str, Any]:
    """Of the entries CDF wrote, how many were read or cited within *window* of the write.

    An entry is mature once *window* has passed since its write; only mature entries
    give the rate (a young one has not had its chance yet). Per store group: the files
    injected every turn, the project DDDs, and Knowledge. "Recalled" (shown by recall
    or ``knowledge find``) is reported apart from "used" (read with Read/Grep or
    ``knowledge get``, or cited as evidence). For the per-turn files, ``injected_now``
    counts the entries the current injection selection holds. *project* keeps only the
    entries written into that project's DDD (``Projects/<project>/``): a Brain's own
    written-then-used rate (Brain Eval)."""
    current = _now(now)
    writes = _rows(conn, "SELECT unit_id,target_path,created_at FROM ingestion_writes "
                         "WHERE op_kind='ingestion_unit' AND step='done' AND unit_id IS NOT NULL")
    uses: dict[tuple[str, str], list[tuple[str, datetime]]] = defaultdict(list)
    for anchor, kind, at in _rows(conn, "SELECT anchor,kind,at FROM unit_usage WHERE anchor LIKE '%#cand:%'"):
        path, _, identity = str(anchor).partition("#")
        unit_id = identity.rsplit("#cand:", 1)[-1] if "#cand:" in identity else ""
        when = _parse(at)
        if unit_id and when:
            uses[(path, unit_id)].append((str(kind), when))
    # a file injected every turn reaches judgment by being selected, not by being read
    injected: set[tuple[str, str]] = set()
    for file_path, anchors in _rows(conn, """SELECT s.file_path,s.selected_anchors FROM injection_selection s
            WHERE s.id=(SELECT MAX(id) FROM injection_selection t WHERE t.file_path=s.file_path)"""):
        try:
            import json
            values = json.loads(anchors)
        except (TypeError, ValueError):
            continue
        for anchor in values if isinstance(values, list) else ():
            path, _, identity = str(anchor).partition("#")
            if "#cand:" in identity:
                injected.add((path, identity.rsplit("#cand:", 1)[-1]))
    groups: dict[str, Counter] = defaultdict(Counter)
    prefix = f"Projects/{project}/" if project else None
    for unit_id, path, created in writes:
        if prefix is not None and not str(path or "").startswith(prefix):
            continue
        written = _parse(created)
        if written is None:
            continue
        group = ("per_turn" if path in _PER_TURN else "timeline" if path.endswith("TIMELINE.md")
                 else "ddd" if path.startswith("Projects/") else "knowledge")
        counter = groups[group]
        mature = current - written >= window
        stage = "mature" if mature else "young"
        counter[f"{stage}_written"] += 1
        events = [(kind, when) for kind, when in uses.get((path, unit_id), ())
                  if when >= written and (not mature or when <= written + window)]
        if any(kind in _USED_KINDS for kind, _ in events):
            counter[f"{stage}_used"] += 1
        if any(kind == "recall_injected" for kind, _ in events):
            counter[f"{stage}_recalled"] += 1
        if group == "per_turn" and (path, unit_id) in injected:
            counter["injected_now"] += 1
    result: dict[str, Any] = {"window_days": window.days, "groups": {}}
    total = Counter()
    for group, counter in sorted(groups.items()):
        total.update(counter)
        result["groups"][group] = dict(counter)
    mature = total["mature_written"]
    result["mature_written"] = mature
    result["mature_used"] = total["mature_used"]
    result["used_rate"] = round(total["mature_used"] / mature, 3) if mature else None
    result["young_written"] = total["young_written"]
    result["young_used_so_far"] = total["young_used"]
    return result


def status(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """One snapshot of CDF (see the module docstring)."""
    current = _now(now)
    day = current.date().isoformat()
    since = (current - timedelta(hours=24)).isoformat()
    jobs = dict(_rows(conn, "SELECT state,COUNT(*) FROM ingestion_s2_jobs GROUP BY state"))
    due = _rows(conn, "SELECT MIN(COALESCE(next_attempt_at, requested_at)) FROM ingestion_s2_jobs "
                      "WHERE state='queued' AND COALESCE(next_attempt_at, requested_at)<=?",
                (current.isoformat(),))
    oldest_due = _parse(due[0][0]) if due and due[0][0] else None
    return {
        "at": current.isoformat(),
        "owner": _owner(conn),
        "stalls": stalls(conn, now=current),
        "session_jobs": {"by_state": jobs,
                         "oldest_due_wait_minutes": (int((current - oldest_due).total_seconds() // 60)
                                                     if oldest_due else None)},
        "extraction_24h": dict(_rows(conn, "SELECT status,COUNT(*) FROM extraction_attempts "
                                           "WHERE started_at>=? GROUP BY status", (since,))),
        "units": dict(_rows(conn, "SELECT state,COUNT(*) FROM ingestion_units GROUP BY state")),
        "poison_by_error": dict(_rows(conn, "SELECT COALESCE(error_class,reason,'-'),COUNT(*) "
                                            "FROM ingestion_units WHERE state='failed_poison' "
                                            "GROUP BY 1 ORDER BY 2 DESC")),
        "events_unresolved": dict(_rows(conn, "SELECT kind,COUNT(*) FROM ingestion_events "
                                              "WHERE resolved=0 GROUP BY kind")),
        "writes_today": dict(_rows(conn, "SELECT target_path,COUNT(*) FROM ingestion_writes "
                                         "WHERE op_kind='ingestion_unit' AND step='done' "
                                         "AND created_at>=? GROUP BY target_path ORDER BY 2 DESC",
                                   (day,))),
        "budget_today_bytes": dict(_rows(conn, "SELECT store,SUM(bytes) FROM ingestion_quota "
                                               "WHERE utc_day=? AND status IN ('reserved','committed') "
                                               "GROUP BY store", (day,))),
        "model_today": {source: {"calls": calls, "input_tokens": tin, "output_tokens": tout}
                        for source, calls, tin, tout in _rows(
                            conn, "SELECT source,calls,input_tokens,output_tokens FROM "
                                  "ingestion_llm_usage WHERE utc_day=?", (day,))},
        "north_star": north_star(conn, now=current),
    }


def render(snapshot: dict[str, Any]) -> str:
    """The snapshot as a few lines a person reads at a glance."""
    owner = snapshot["owner"]
    jobs = snapshot["session_jobs"]
    star = snapshot["north_star"]
    lines = [
        "CDF " + ("needs attention:" if snapshot["stalls"] else "is moving."),
        *[f"  ! {line}" for line in snapshot["stalls"]],
        f"owner: pid {owner['pid']} ({'alive' if owner['alive'] else 'not running' if owner['alive'] is False else 'unknown'})",
        f"session jobs: {jobs['by_state'] or 'none'}"
        + (f"; oldest due waited {jobs['oldest_due_wait_minutes']} min" if jobs["oldest_due_wait_minutes"] is not None else ""),
        f"extraction (24h): {snapshot['extraction_24h'] or 'none'}",
        f"units: {snapshot['units'] or 'none'}",
    ]
    if snapshot["poison_by_error"]:
        lines.append(f"poisoned by error: {snapshot['poison_by_error']}")
    if snapshot["events_unresolved"]:
        lines.append(f"unresolved events: {snapshot['events_unresolved']}")
    lines.append(f"written today: {snapshot['writes_today'] or 'nothing'}")
    lines.append(f"budget today (bytes): {snapshot['budget_today_bytes'] or 'none used'}")
    lines.append(f"model calls today: {snapshot['model_today'] or 'none'}")
    rate = star["used_rate"]
    lines.append(
        f"north star: of {star['mature_written']} entries written over {star['window_days']} days ago, "
        f"{star['mature_used']} were read or cited within {star['window_days']} days"
        + (f" ({rate:.0%})" if rate is not None else "")
        + f"; {star['young_written']} younger, {star['young_used_so_far']} used so far")
    for group, counts in star["groups"].items():
        lines.append(f"  {group}: {counts}")
    return "\n".join(lines)
