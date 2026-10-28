"""Single-owner and epoch fencing for the CDF worker.

- ``acquire`` takes the process-wide non-blocking owner lock.
- ``Owner.adopt`` re-epochs unfinished database work atomically.
- ``Owner.claim_unit_step`` and ``claim_write_step`` enforce stale-owner fencing.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from config import get_app_data_dir
from utils.file_lock import flock_exclusive_nb, flock_unlock

from .store import connect_rw

_TERMINAL_UNITS = ("merged", "written", "reinforced", "rejected", "failed_poison", "expired_unprocessed")
_TERMINAL_WRITES = ("done", "abandoned")


class Owner:
    """The held owner lock and its monotonically increasing epoch."""

    def __init__(self, lock_file: IO[str], epoch: int) -> None:
        self._lock_file = lock_file
        self.epoch = epoch
        self._released = False

    @classmethod
    def acquire(cls) -> "Owner | None":
        """Acquire ownership without waiting, returning ``None`` if already held."""
        lock_path = get_app_data_dir() / "state" / "ingestion_owner.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
        try:
            flock_exclusive_nb(lock_file)
        except (BlockingIOError, OSError):
            lock_file.close()
            return None
        try:
            conn = connect_rw()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT epoch FROM ingestion_owner WHERE id=1").fetchone()
                epoch = (int(row[0]) if row else 0) + 1
                conn.execute(
                    """INSERT INTO ingestion_owner(id, epoch, pid, started_at)
                       VALUES (1, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET epoch=excluded.epoch,
                           pid=excluded.pid, started_at=excluded.started_at""",
                    (epoch, os.getpid(), datetime.now(timezone.utc).isoformat()),
                )
                conn.execute("COMMIT")
            finally:
                conn.close()
            return cls(lock_file, epoch)
        except BaseException:
            flock_unlock(lock_file)
            lock_file.close()
            raise

    def adopt(self, conn: sqlite3.Connection) -> None:
        """Atomically assign every unfinished unit and write to this epoch."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            unit_marks = ",".join("?" for _ in _TERMINAL_UNITS)
            write_marks = ",".join("?" for _ in _TERMINAL_WRITES)
            conn.execute(
                f"UPDATE ingestion_units SET owner_epoch=? WHERE state NOT IN ({unit_marks})",
                (self.epoch, *_TERMINAL_UNITS),
            )
            conn.execute(
                f"UPDATE ingestion_writes SET owner_epoch=? WHERE step NOT IN ({write_marks})",
                (self.epoch, *_TERMINAL_WRITES),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def claim_unit_step(
        self,
        conn: sqlite3.Connection,
        unit_id: str,
        from_state: str,
        to_state: str,
        **fields: Any,
    ) -> bool:
        """Advance one unit only if its state and owner epoch still match."""
        assignments = ["state=?", "updated_at=?"]
        values: list[Any] = [to_state, datetime.now(timezone.utc).isoformat()]
        allowed = {
            "decision", "destination", "project", "action", "reason", "merged_into",
            "attempts", "next_attempt_at", "error_class", "error_fingerprint",
        }
        for name, value in fields.items():
            if name not in allowed:
                raise ValueError(f"unsupported unit field: {name}")
            assignments.append(f"{name}=?")
            values.append(value)
        values.extend((unit_id, from_state, self.epoch))
        cursor = conn.execute(
            f"UPDATE ingestion_units SET {', '.join(assignments)} "
            "WHERE unit_id=? AND state=? AND owner_epoch=?",
            values,
        )
        return cursor.rowcount == 1

    def claim_write_step(
        self, conn: sqlite3.Connection, op_id: str, from_step: str, *, step_owner: str
    ) -> bool:
        """Fence a writer filesystem step before its side effect."""
        cursor = conn.execute(
            """UPDATE ingestion_writes SET step_owner=?, updated_at=?
               WHERE op_id=? AND step=? AND owner_epoch=?""",
            (step_owner, datetime.now(timezone.utc).isoformat(), op_id, from_step, self.epoch),
        )
        return cursor.rowcount == 1

    def release(self) -> None:
        """Release ownership after callers have joined worker resources."""
        if self._released:
            return
        self._released = True
        flock_unlock(self._lock_file)
        self._lock_file.close()

    def __enter__(self) -> "Owner":
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def acquire() -> Owner | None:
    """Module-level ownership entry point used by FastAPI lifespan."""
    return Owner.acquire()
