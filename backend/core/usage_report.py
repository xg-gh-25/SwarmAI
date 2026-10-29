"""The usage view's figures (design
docs/design/2026-10-29-usage-accounting.md §5 and §11c, mockup
Knowledge/Designs/mockups/2026-10-30-usage-view.html).

Everything is read from the usage ledger (core/usage_accounting): its spend rows, of
every machine (this host and the Hives it pulls) and every surface, are what was spent.
Attribution rows (a pipeline run's share of the session it ran in) split that spend and
are never added to a total. Days are this machine's local calendar.

The view answers, top down: how much this window spent and how that compares with the
window before it; where it went (by surface, feature, model or machine, each with its
billed tokens, cache hit rate, calls and spend per call); which sessions, pipeline runs,
jobs and Hive runs spent the most; what a turn costs; and what the ledger cannot see
(tokens with no price, imported estimates, Hives that have not reported).

- ``report(window, conn, now=None)`` — the view's figures for ``"7d"``, ``"30d"`` or ``"all"``
- ``connect(db_path=None)`` — a read-only connection to the ledger's database
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Optional

from core.usage_accounting import KINDS, billed, cache_hit_rate

WINDOWS: dict[str, Optional[int]] = {"7d": 7, "30d": 30, "all": None}
TOP = 8
#: the edges of the turn-cost table, USD: < 1, 1–5, 5–15, ≥ 15
TURN_EDGES = (1.0, 5.0, 15.0)
#: the surfaces whose calls are a user's turns (a session's chat or channel result)
TURN_SURFACES = frozenset({"chat", "channel"})
DIMENSIONS = ("surface", "feature", "model", "machine")
_COLS = ("at_utc", "machine", "surface", "feature", "session_id", "run_id", "stage", "job_id",
         "dispatch_id", "model", "input", "output", "cache_read", "cache_write", "cost_usd",
         "imported", "dedup_key")


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    if db_path is None:
        import database
        db_path = str(database.db.db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _midnight(day: date) -> str:
    """Local midnight of ``day`` as the ledger's UTC text (rows compare as strings)."""
    return datetime.combine(day, dtime()).astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")


def _today(now: Optional[datetime]) -> date:
    return (now or datetime.now()).astimezone().date()


def _local_day(at_utc: str) -> str:
    return datetime.fromisoformat(at_utc).astimezone().date().isoformat()


def call_key(row: Mapping[str, Any]) -> str:
    """The call a row belongs to. A CLI result writes one row per model, keyed
    ``cli:<tracker>:<session>:<pid>:<seq>:<model>``; on a Hive's rows the host keeps only
    ``hive:<hive>:<row id>``, so their result is the rows of one session (or job) written
    in the same second. Every other row is a call of its own."""
    key = row["dedup_key"]
    if key.startswith("cli:"):
        return key.rsplit(":", 1)[0]
    owner = row.get("session_id") or row.get("job_id")
    if key.startswith("hive:") and owner:
        return f"hive-call:{row.get('machine')}:{owner}:{row.get('at_utc')}"
    return key


def _total(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    kinds = {k: sum(int(r[k] or 0) for r in rows) for k in KINDS}
    unpriced = [r for r in rows if r["cost_usd"] is None and billed(r) > 0]
    return {"cost_usd": round(sum(r["cost_usd"] for r in rows if r["cost_usd"] is not None), 2),
            "billed": billed(kinds), **kinds, "cache_hit_rate": _rate(kinds),
            "rows": len(rows), "calls": len({call_key(r) for r in rows}),
            "unpriced_calls": len({call_key(r) for r in unpriced}),
            "unpriced_tokens": sum(billed(r) for r in unpriced)}


def _rate(kinds: Mapping[str, int]) -> Optional[float]:
    rate = cache_hit_rate(kinds)
    return None if rate is None else round(rate, 4)


def _grouped(rows: Iterable[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], Any]) -> list[dict[str, Any]]:
    groups: dict[Any, list] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    out = [{"key": k, **_total(g)} for k, g in groups.items()]
    out.sort(key=lambda g: (-g["cost_usd"], -g["billed"]))
    return out


def _turns(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """What one turn (a session's CLI result) cost: percentiles and the table by cost."""
    calls: dict[str, float] = defaultdict(float)
    for r in rows:
        if r["surface"] in TURN_SURFACES and r["cost_usd"] is not None:
            calls[call_key(r)] += r["cost_usd"]
    costs = sorted(calls.values())
    n, total = len(costs), sum(costs)
    edges = (0.0, *TURN_EDGES, float("inf"))
    buckets = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [c for c in costs if lo <= c < hi]
        buckets.append({"from": lo, "to": None if hi == float("inf") else hi, "turns": len(sel),
                        "cost_usd": round(sum(sel), 2), "share": round(sum(sel) / total, 4) if total else None})

    def rank(q: float) -> float:                     # nearest rank
        return costs[max(0, math.ceil(q * n) - 1)]

    return {"turns": n,
            "p50": round(rank(0.5), 2) if n else None,
            "p90": round(rank(0.9), 2) if n else None,
            "max": round(costs[-1], 2) if n else None,
            "buckets": buckets}


def _top(rows: list, key: Callable[[Mapping[str, Any]], Optional[str]], limit: int = TOP) -> list[dict[str, Any]]:
    return [g for g in _grouped([r for r in rows if key(r)], key) if g["cost_usd"] > 0 or g["billed"] > 0][:limit]


def report(window: str, conn: sqlite3.Connection, now: Optional[datetime] = None, *,
           session_titles: Optional[Callable[[list[str]], Mapping[str, str]]] = None,
           run_info: Optional[Callable[[str], Optional[Mapping[str, Any]]]] = None,
           dispatch_titles: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """The view's figures for ``window`` (``WINDOWS``). The lookups name what the
    ledger holds only an id for; each is optional (a missing title stays None)."""
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}")
    days, today = WINDOWS[window], _today(now)
    since = _midnight(today - timedelta(days=days - 1)) if days else None
    before = _midnight(today - timedelta(days=2 * days - 1)) if days else None
    cols = ", ".join(_COLS)
    spend = [dict(r) for r in conn.execute(
        f"SELECT {cols} FROM usage_ledger WHERE kind='spend' AND at_utc >= ? ORDER BY at_utc", (before or "",))]
    attribution = [dict(r) for r in conn.execute(
        f"SELECT {cols} FROM usage_ledger WHERE kind='attribution' AND at_utc >= ?", (since or "",))]
    cur = [r for r in spend if since is None or r["at_utc"] >= since]
    prev = [r for r in spend if since is not None and r["at_utc"] < since]

    # the daily bars: the window and the one before it (all days, for "all")
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in spend:
        if r["cost_usd"] is not None:
            by_day[_local_day(r["at_utc"])][r["surface"]] += r["cost_usd"]
    first = (today - timedelta(days=2 * days - 1)) if days else (
        date.fromisoformat(min(by_day)) if by_day else today)
    daily, d = [], first
    while d <= today:
        surfaces = by_day.get(d.isoformat(), {})
        daily.append({"day": d.isoformat(), "cost_usd": round(sum(surfaces.values()), 2),
                      "by_surface": {s: round(v, 2) for s, v in sorted(surfaces.items())},
                      "in_window": since is None or _midnight(d) >= since})
        d += timedelta(days=1)

    total = _total(cur)
    turns = _turns(cur)
    # this host's sessions (a Hive's are under "hive": their ids are not sessions here)
    sessions = _top(cur, lambda r: r["session_id"] if r["surface"] in TURN_SURFACES and r["machine"] == "host" else None)
    titles = session_titles([s["key"] for s in sessions]) if session_titles and sessions else {}
    for s in sessions:
        s["title"] = titles.get(s["key"])
    runs = _top(attribution, lambda r: r["run_id"])
    for g in runs:
        info = run_info(g["key"]) if run_info else None
        g["title"] = (info or {}).get("title")
        g["report_path"] = (info or {}).get("report_path")
    jobs = _top(cur, lambda r: r["job_id"] if r["surface"] == "job" else None)
    hive = _top(cur, lambda r: (r["dispatch_id"] or r["session_id"] or r["machine"]) if r["machine"] != "host" else None)
    for g in hive:
        sample = next(r for r in cur if r["machine"] != "host" and (r["dispatch_id"] or r["session_id"] or r["machine"]) == g["key"])
        g.update(machine=sample["machine"], dispatch_id=sample["dispatch_id"], session_id=sample["session_id"],
                 title=(dispatch_titles or {}).get(sample["dispatch_id"] or ""))

    top_share = round(sum(s["cost_usd"] for s in sessions) / total["cost_usd"], 4) if total["cost_usd"] and sessions else None
    big = turns["buckets"][-1]
    imported = [r for r in cur if r["imported"]]
    first_live = conn.execute("SELECT MIN(at_utc) FROM usage_ledger WHERE imported=0 AND machine='host'").fetchone()[0]
    hives = [r[0] for r in conn.execute("SELECT DISTINCT machine FROM usage_ledger WHERE machine != 'host' ORDER BY 1")]
    return {
        "window": window, "since": since, "today": today.isoformat(),
        "total": total, "previous": _total(prev) if since else None,
        "days": days or len(daily),
        "daily": daily,
        "breakdown": {dim: _grouped(cur, lambda r, dim=dim: r[dim]) for dim in DIMENSIONS},
        "attribution": {**_total(attribution), "runs": len({r["run_id"] for r in attribution if r["run_id"]})},
        "turns": turns,
        "top": {"sessions": sessions, "runs": runs, "jobs": jobs, "hive": hive},
        "concentration": {"top_sessions": len(sessions), "top_sessions_share": top_share,
                          "big_turn_usd": TURN_EDGES[-1], "big_turns": big["turns"],
                          "big_turns_turn_share": round(big["turns"] / turns["turns"], 4) if turns["turns"] else None,
                          "big_turns_cost_share": big["share"]},
        "gaps": {"unpriced": [{"surface": g["key"], "calls": g["unpriced_calls"], "tokens": g["unpriced_tokens"]}
                              for g in _grouped(cur, lambda r: r["surface"]) if g["unpriced_tokens"]],
                 "imported_cost_usd": round(sum(r["cost_usd"] or 0 for r in imported), 2),
                 "imported_calls": len({call_key(r) for r in imported}),
                 "live_since": first_live, "hives_reporting": hives},
    }
