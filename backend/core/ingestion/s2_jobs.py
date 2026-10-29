"""Session jobs: the session-end hook and Save to Memory ask, a worker thread does.

Design §12 r16.13 B and 2026-10-29-cdf-session-review.md. One job per session runs the
S2 user-utterance pass and the after-session review (S2 main pass when the session
cannot be resumed), never inside the 180 s session-end hook or an HTTP request.

- ``request(session_id, reason, sdk_session_id=, delay=)`` records (or re-arms) one job
  per session and wakes the executor when one runs in this process. ``delay`` is a
  not-before: the idle hook asks with ``REVIEW_DELAY`` and each new request pushes it
  later, so a review waits for 10 quiet minutes; the button asks with no delay. A
  request while the job runs sets ``again`` (with its own not-before) so the session is
  read once more after it finishes.
- ``set_watermark`` / the claimed job's ``reviewed_to`` keep where the review stopped.
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
from typing import Optional, Any, Callable

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=5)
# The idle hook fires after 120 s idle (LifecycleManager.IDLE_HOOK_GRACE): 8 more
# minutes makes the review wait for 10 quiet minutes.
REVIEW_DELAY = timedelta(minutes=8)
_WAKER: Callable[[], None] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_waker(fn: Callable[[], None] | None) -> None:
    global _WAKER
    _WAKER = fn


def known(session_id: str) -> bool:
    """Whether a session job was ever requested for ``session_id`` (any state)."""
    from .api import connect_rw
    conn = connect_rw()
    try:
        return conn.execute("SELECT 1 FROM ingestion_s2_jobs WHERE session_id=?",
                            (session_id,)).fetchone() is not None
    finally:
        conn.close()


def request(session_id: str, reason: str, *, sdk_session_id: str | None = None,
            delay: timedelta = timedelta(0)) -> str:
    """Queue a session job for ``session_id``; returns the job's state after the request."""
    if not session_id:
        raise ValueError("session_id is required")
    from .api import connect_rw
    not_before = (datetime.now(timezone.utc) + delay).isoformat() if delay else None
    conn = connect_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT state FROM ingestion_s2_jobs WHERE session_id=?",
                               (session_id,)).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO ingestion_s2_jobs(session_id,reason,state,requested_at,
                           next_attempt_at,sdk_session_id) VALUES (?,?,'queued',?,?,?)""",
                    (session_id, reason, _now(), not_before, sdk_session_id))
                state = "queued"
            elif row[0] == "running":
                conn.execute(
                    """UPDATE ingestion_s2_jobs SET again=1, reason=?, again_after=?,
                       sdk_session_id=COALESCE(?, sdk_session_id) WHERE session_id=?""",
                    (reason, not_before, sdk_session_id, session_id))
                state = "running"
            elif row[0] == "queued":
                # a later request pushes the not-before (more activity); no delay = now.
                # An explicit save (no delay) is never turned back into a delayed
                # session-end job by the idle hook that fires after it.
                conn.execute(
                    """UPDATE ingestion_s2_jobs SET
                       reason=CASE WHEN reason='save_button' THEN reason ELSE ? END,
                       next_attempt_at=CASE WHEN reason='save_button' THEN next_attempt_at ELSE ? END,
                       sdk_session_id=COALESCE(?, sdk_session_id) WHERE session_id=?""",
                    (reason, not_before, sdk_session_id, session_id))
                state = "queued"
            else:
                conn.execute(
                    """UPDATE ingestion_s2_jobs SET state='queued', reason=?, attempts=0,
                       requested_at=?, error=NULL, again=0, next_attempt_at=?,
                       sdk_session_id=COALESCE(?, sdk_session_id) WHERE session_id=?""",
                    (reason, _now(), not_before, sdk_session_id, session_id))
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
            """SELECT session_id, reason, attempts, sdk_session_id, reviewed_to
               FROM ingestion_s2_jobs
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
    return {"session_id": row[0], "reason": row[1], "attempts": int(row[2]) + 1,
            "sdk_session_id": row[3], "reviewed_to": row[4]}


def set_watermark(conn: sqlite3.Connection, session_id: str, reviewed_to: str) -> None:
    """Record the last message the review covered (the next review starts after it)."""
    conn.execute("UPDATE ingestion_s2_jobs SET reviewed_to=? WHERE session_id=?",
                 (reviewed_to, session_id))


def finish(conn: sqlite3.Connection, session_id: str, *, error: str | None = None) -> str:
    """Record the outcome; a failed attempt re-queues until MAX_ATTEMPTS."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT attempts, again, again_after FROM ingestion_s2_jobs WHERE session_id=?",
            (session_id,)).fetchone()
        attempts, again, again_after = ((int(row[0]), int(row[1]), row[2]) if row
                                        else (MAX_ATTEMPTS, 0, None))
        if error is None:
            state = "queued" if again else "done"
        elif again:
            state = "queued"    # a request that came during the failed run is new work
        else:
            state = "failed" if attempts >= MAX_ATTEMPTS else "queued"
        if error is not None and state == "queued":
            retry_at = (datetime.now(timezone.utc) + RETRY_DELAY * attempts).isoformat()
        else:
            retry_at = again_after if (error is None and again) else None
        conn.execute(
            """UPDATE ingestion_s2_jobs SET state=?, finished_at=?, error=?, next_attempt_at=?,
                   again=0, again_after=NULL,
                   attempts=CASE WHEN ?='queued' AND (? IS NULL OR ?) THEN 0 ELSE attempts END
               WHERE session_id=?""",
            (state, _now(), error, retry_at, state, error, again, session_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return state


def recover(conn: sqlite3.Connection, failed: Optional[list] = None) -> int:
    """Jobs left running by a stop or crash go back to the queue (or fail when spent);
    ``failed`` collects the session ids of the ones that failed."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if failed is not None:
            failed += [r[0] for r in conn.execute(
                "SELECT session_id FROM ingestion_s2_jobs WHERE state='running' AND attempts>=?",
                (MAX_ATTEMPTS,))]
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
