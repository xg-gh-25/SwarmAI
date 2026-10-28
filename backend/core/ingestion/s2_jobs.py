"""S2 extraction jobs: the session-end hook and Save to Memory ask, a worker thread does.

Design §12 r16.13 B. S2 on a large session is dozens of model calls (minutes), so it
must not run inside the 180 s session-end hook or an HTTP request.

- ``request(session_id, reason)`` records (or re-arms) one job per session and wakes
  the executor when one runs in this process. A request while the job runs sets
  ``again`` so the session is read once more after it finishes.
- ``claim(conn)`` / ``finish(conn, ...)`` move a job queued -> running -> done/failed.
- ``recover(conn)`` puts jobs left running by a crash back in the queue; after
  ``MAX_ATTEMPTS`` claims a job is ``failed`` with its last error. A failed attempt
  waits ``RETRY_DELAY`` x attempts before it can be claimed again: an immediate retry
  with nothing changed would fail the same way.
- ``set_waker(fn)`` is called by the executor so ``request`` can wake it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=5)
_WAKER: Callable[[], None] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_waker(fn: Callable[[], None] | None) -> None:
    global _WAKER
    _WAKER = fn


def request(session_id: str, reason: str) -> str:
    """Queue S2 for ``session_id``; returns the job's state after the request."""
    if not session_id:
        raise ValueError("session_id is required")
    from .api import connect_rw
    conn = connect_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT state FROM ingestion_s2_jobs WHERE session_id=?",
                               (session_id,)).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO ingestion_s2_jobs(session_id,reason,state,requested_at)
                       VALUES (?,?,'queued',?)""", (session_id, reason, _now()))
                state = "queued"
            elif row[0] == "running":
                conn.execute("UPDATE ingestion_s2_jobs SET again=1, reason=? WHERE session_id=?",
                             (reason, session_id))
                state = "running"
            elif row[0] == "queued":
                state = "queued"
            else:
                conn.execute(
                    """UPDATE ingestion_s2_jobs SET state='queued', reason=?, attempts=0,
                       requested_at=?, error=NULL, again=0, next_attempt_at=NULL
                       WHERE session_id=?""",
                    (reason, _now(), session_id))
                state = "queued"
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    if _WAKER is not None:
        _WAKER()
    return state


def claim(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Take the oldest queued job (queued -> running, attempts + 1)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """SELECT session_id, reason, attempts FROM ingestion_s2_jobs
               WHERE state='queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
               ORDER BY requested_at LIMIT 1""", (_now(),)).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            """UPDATE ingestion_s2_jobs SET state='running', attempts=attempts+1, started_at=?
               WHERE session_id=?""", (_now(), row[0]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"session_id": row[0], "reason": row[1], "attempts": int(row[2]) + 1}


def finish(conn: sqlite3.Connection, session_id: str, *, error: str | None = None) -> str:
    """Record the outcome; a failed attempt re-queues until MAX_ATTEMPTS."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT attempts, again FROM ingestion_s2_jobs WHERE session_id=?",
                           (session_id,)).fetchone()
        attempts, again = (int(row[0]), int(row[1])) if row else (MAX_ATTEMPTS, 0)
        if error is None:
            state = "queued" if again else "done"
        else:
            state = "failed" if attempts >= MAX_ATTEMPTS else "queued"
        retry_at = ((datetime.now(timezone.utc) + RETRY_DELAY * attempts).isoformat()
                    if error is not None and state == "queued" else None)
        conn.execute(
            """UPDATE ingestion_s2_jobs SET state=?, finished_at=?, error=?, next_attempt_at=?,
                   again=0, attempts=CASE WHEN ?='queued' AND ? IS NULL THEN 0 ELSE attempts END
               WHERE session_id=?""",
            (state, _now(), error, retry_at, state, error, session_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return state


def recover(conn: sqlite3.Connection) -> int:
    """Jobs left running by a stop or crash go back to the queue (or fail when spent)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """UPDATE ingestion_s2_jobs SET state='failed', finished_at=?,
                   error=COALESCE(error,'interrupted') WHERE state='running' AND attempts>=?""",
            (_now(), MAX_ATTEMPTS))
        moved = conn.execute(
            "UPDATE ingestion_s2_jobs SET state='queued' WHERE state='running'").rowcount
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return moved
