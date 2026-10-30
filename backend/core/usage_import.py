"""Bring the usage recorded before the ledger into it, once and idempotently (design
docs/design/2026-10-29-usage-accounting.md §7).

Two old records, both left exactly as they are (read only):

- ``token_usage`` (data.db): one row per CLI result or recorded Bedrock call. Its
  ``cost_usd`` is the CLI process's running total and it has no pid, so a row's cost is
  its increase over the previous row of the same session (``turn_outcome.cost_increase``,
  the rule its other readers use); a new process whose first total is above the old
  one's last is then undercounted, which is why these rows are ``imported=1`` (shown as
  an estimate). Timestamps are local; the ledger's are UTC.
- ``ingestion_llm_usage`` (ingestion.db): the CDF's Bedrock calls per UTC day and source.

Both tables were still written for a while after the ledger existed (``token_usage``
is no longer written: every reader uses the ledger), so only what came before the
ledger's first live row is brought in: ``token_usage`` rows strictly older
than it, ``ingestion_llm_usage`` days before its day whole, and of that day only what
the ledger's own CDF rows of the day do not already hold. Every row has a key made from
its source, so running this again (each start) writes nothing new.

``run(data_db, ingestion_db)`` → counts. Called off the event loop at startup.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _utc(local_ts: str) -> Optional[str]:
    """A local-naive ``YYYY-MM-DDTHH:MM:SS`` as UTC ISO (None when unparseable)."""
    try:
        t = datetime.fromisoformat(str(local_ts))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.astimezone()                       # naive = this machine's local time
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _cutover(conn: sqlite3.Connection) -> str:
    """When the ledger began recording live: its first live row, or now."""
    row = conn.execute("SELECT MIN(at_utc) FROM usage_ledger WHERE imported = 0").fetchone()
    return row[0] if row and row[0] else datetime.now(timezone.utc).isoformat(timespec="seconds")


def _token_usage_rows(conn: sqlite3.Connection, cutover: str) -> list[dict[str, Any]]:
    from core.turn_outcome import cost_increase
    out: list[dict[str, Any]] = []
    prev: dict[str, float] = {}
    for rid, sid, ts, source, i, o, cr, cw, cost, model in conn.execute(
            "SELECT id, session_id, timestamp, source, input_tokens, output_tokens, cache_read_tokens, "
            "cache_create_tokens, cost_usd, model FROM token_usage ORDER BY id"):
        at = _utc(ts)
        d = None
        if cost is not None and source == "cli":
            c = float(cost)
            d = cost_increase((sid, prev[sid]) if sid in prev else None, sid, c)
            if sid is not None:
                prev[sid] = c
        if at is None or at >= cutover:
            continue
        cli = source == "cli"
        out.append({
            "at_utc": at, "machine": "host", "surface": "chat" if cli else "background",
            "feature": None if cli else "unattributed", "session_id": sid, "model": model,
            "input": i or 0, "output": o or 0, "cache_read": cr or 0, "cache_write": cw or 0,
            "cost_usd": d, "cost_source": "cli" if d is not None else "none",
            "kind": "spend", "imported": 1, "dedup_key": f"import:token_usage:{rid}",
        })
    return out


def _cdf_rows(conn: sqlite3.Connection, ingestion_db: Path, cutover: str) -> list[dict[str, Any]]:
    if not Path(ingestion_db).is_file():
        return []
    cut_day = cutover[:10]
    src = sqlite3.connect(f"file:{ingestion_db}?mode=ro", uri=True, timeout=5.0)
    try:
        days = src.execute("SELECT utc_day, source, input_tokens, output_tokens FROM ingestion_llm_usage "
                           "WHERE utc_day <= ?", (cut_day,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        src.close()
    out: list[dict[str, Any]] = []
    for day, source, i, o in days:
        feature = f"cdf.{str(source).lower()}" if source and source != "-" else "cdf"
        i, o = int(i or 0), int(o or 0)
        if day == cut_day:                         # the ledger already holds part of this day
            have = conn.execute(
                "SELECT COALESCE(SUM(input),0), COALESCE(SUM(output),0) FROM usage_ledger "
                "WHERE imported = 0 AND feature = ? AND substr(at_utc,1,10) = ?", (feature, day)).fetchone()
            i, o = max(0, i - have[0]), max(0, o - have[1])
            if not (i or o):
                continue
        out.append({
            "at_utc": f"{day}T12:00:00+00:00", "machine": "host", "surface": "cdf", "feature": feature,
            "input": i, "output": o, "cache_read": 0, "cache_write": 0, "cost_usd": None,
            "cost_source": "none", "kind": "spend", "imported": 1,
            "dedup_key": f"import:cdf:{day}:{source}",
        })
    return out


def run(data_db: Path, ingestion_db: Path) -> dict[str, int]:
    """Import what came before the ledger; returns {"token_usage": n, "cdf": n} written."""
    from database.sqlite import usage_ledger_insert_sql, usage_ledger_values
    conn = sqlite3.connect(f"file:{data_db}?mode=rw", uri=True, timeout=10.0)
    try:
        has = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                           "('usage_ledger','token_usage')").fetchall()
        if {r[0] for r in has} != {"usage_ledger", "token_usage"}:
            return {"token_usage": 0, "cdf": 0}
        cutover = _cutover(conn)
        counts = {}
        for name, rows in (("token_usage", _token_usage_rows(conn, cutover)),
                           ("cdf", _cdf_rows(conn, ingestion_db, cutover))):
            before = conn.total_changes
            for start in range(0, len(rows), 1000):     # short write transactions
                conn.executemany(usage_ledger_insert_sql(), usage_ledger_values(rows[start:start + 1000]))
                conn.commit()
            counts[name] = conn.total_changes - before
        return counts
    finally:
        conn.close()
