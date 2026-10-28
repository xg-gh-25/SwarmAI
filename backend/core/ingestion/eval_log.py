"""Eval record of every CDF outcome, for batch evaluation after launch (design §14.2).

- ``archive_candidates(conn, ids)`` copies each unit of these candidates (any state,
  written included) into ``ingestion_eval_log``. ``cleanup_expired`` calls it inside the
  same transaction that deletes the candidates, so a unit is in the live tables or the
  archive, never both and never neither.
- ``prune(conn, now)`` drops archive rows and ``extraction_attempts`` older than
  ``RETENTION_DAYS``.
- ``export(since, until)`` yields one dict per record from the live judge tables, the
  archive and the extractor's rejected units, each tagged with ``stage`` and ``origin``.
  Read-only; ``ingest eval-export`` writes it to JSONL.

Nothing here runs on the write path: archive and prune run in maintenance, export on
demand.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

RETENTION_DAYS = 180

_INBOX_FIELDS = ("source_ref", "lane", "payload", "payload_schema", "unit_kind",
                 "extraction_pass", "project_hint", "project_candidates", "evidence_root_ids")
_UNIT_FIELDS = ("claim_group", "decision", "destination", "project", "action",
                "attempts", "error_class", "merged_into", "updated_at")


def _json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _judge_record(inbox: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    record = {name: _json(inbox.get(name)) for name in _INBOX_FIELDS}
    record.update({name: _json(unit.get(name)) for name in _UNIT_FIELDS})
    return record


def _source(inbox: dict[str, Any]) -> str:
    return str(inbox.get("source_v2") or inbox.get("source") or "-")


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def archive_candidates(conn: sqlite3.Connection, ids: list[str]) -> int:
    """Copy every unit of ``ids`` into the archive. Caller holds the transaction."""
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    inboxes = {row["id"]: row for row in _rows(
        conn, f"SELECT * FROM ingestion_inbox WHERE id IN ({marks})", tuple(ids))}
    units = _rows(conn, f"SELECT * FROM ingestion_units WHERE candidate_id IN ({marks})", tuple(ids))
    for unit in units:
        inbox = inboxes[unit["candidate_id"]]
        conn.execute(
            """INSERT OR REPLACE INTO ingestion_eval_log(unit_id,at,source,state,reason,record)
               VALUES (?,?,?,?,?,?)""",
            (unit["unit_id"], inbox["created_at"], _source(inbox), unit["state"],
             unit.get("reason") or "-",
             json.dumps(_judge_record(inbox, unit), ensure_ascii=False, sort_keys=True)),
        )
    return len(units)


def prune(conn: sqlite3.Connection, *, now: datetime | None = None) -> int:
    """Drop archive rows and extraction attempts older than the retention window."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        dropped = conn.execute("DELETE FROM ingestion_eval_log WHERE at < ?", (cutoff,)).rowcount
        dropped += conn.execute(
            "DELETE FROM extraction_attempts WHERE started_at < ?", (cutoff,)).rowcount
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return dropped


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def export(conn: sqlite3.Connection, since: str, until: str | None = None) -> Iterator[dict[str, Any]]:
    """Yield every record with ``since <= at < until`` (ISO dates or timestamps).

    A table the worker has not created yet (older store, read-only here) is skipped.
    """
    upper = until or "9999"
    live = _rows(conn, """SELECT i.*, u.unit_id, u.state AS unit_state, u.reason AS unit_reason,
                                 u.claim_group, u.decision, u.destination, u.project, u.action,
                                 u.attempts, u.error_class, u.merged_into, u.updated_at
                          FROM ingestion_units u JOIN ingestion_inbox i ON i.id=u.candidate_id
                          WHERE i.created_at >= ? AND i.created_at < ?
                          ORDER BY i.created_at, u.unit_id""", (since, upper))
    for row in live:
        yield {"stage": "judge", "origin": "live", "unit_id": row["unit_id"],
               "at": row["created_at"], "source": _source(row), "state": row["unit_state"],
               "reason": row["unit_reason"] or "-", "record": _judge_record(row, row)}
    archived = _rows(conn, """SELECT * FROM ingestion_eval_log WHERE at >= ? AND at < ?
                             ORDER BY at, unit_id""", (since, upper)) if _has(conn, "ingestion_eval_log") else []
    for row in archived:
        yield {"stage": "judge", "origin": "archive", "unit_id": row["unit_id"],
               "at": row["at"], "source": row["source"], "state": row["state"],
               "reason": row["reason"], "record": _json(row["record"])}
    attempts = _rows(conn, """SELECT * FROM extraction_attempts WHERE started_at >= ?
                             AND started_at < ? AND units_invalid > 0
                             ORDER BY started_at, attempt_id""", (since, upper)) if _has(conn, "extraction_attempts") else []
    for row in attempts:
        for index, item in enumerate(_json(row["invalid_units"]) or []):
            yield {"stage": "extraction", "origin": "live",
                   "unit_id": f"{row['attempt_id']}#{index}", "at": row["started_at"],
                   "source": row["source"], "state": "rejected_at_source",
                   "reason": (item or {}).get("reason") or "-",
                   "record": {"raw": (item or {}).get("raw"),
                              "evidence_range": _json(row["evidence_range"]),
                              "extractor_version": row["extractor_version"]}}
