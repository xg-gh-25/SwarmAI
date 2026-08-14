"""Shared recall DB connection helper (FTS5, pure keyword — NO vector).

Centralizes the sqlite3 connection pattern used by the Knowledge Library,
Transcript index, and Recall Engine. Recall is pure FTS5+BM25 (the sqlite-vec
vector leg was removed 2026-08-14 — see PRI11: FTS5-only, zero-embedding is the
intended architecture). This module NO LONGER loads any vector extension; it is
a plain sqlite3 connection factory with the WAL + busy_timeout PRAGMAs the recall
callers rely on.

Filename kept as ``vec_db`` for import-path stability across the many callers;
the "vec" is historical — there is no vector code here anymore.

Public symbols:

- ``open_vec_db``   — Context manager yielding a sqlite3.Connection (FTS5-ready).
                      ALWAYS yields a real connection (never None on success) —
                      the old "None if sqlite-vec missing" path was a silent
                      single-point-of-failure that killed FTS5 recall when an
                      unrelated dependency was absent (Gate-1 run_f8675981).
- ``get_vec_conn``  — Module-level singleton connection (reused across calls
                      within the same process). Faster for hot paths.
- ``VEC_AVAILABLE`` — Retained as ``True`` for caller-compat; there is no vector
                      dependency anymore, so a connection is always obtainable.
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# Recall is FTS5-only — no vector extension to probe. Kept True for the handful
# of callers that still branch on it (they all treat True as "DB obtainable").
VEC_AVAILABLE = True

# Default database path (all SwarmAI data lives under ~/.swarm-ai/)
from jobs.paths import DB_PATH as _DEFAULT_DB_PATH

# Module-level singleton connection (thread-safe init via lock).
_singleton_conn: Optional[sqlite3.Connection] = None
_singleton_lock = threading.Lock()


def get_vec_conn(db_path: Optional[Path] = None) -> Optional[sqlite3.Connection]:
    """Get a module-level singleton sqlite3 connection (FTS5-ready).

    Faster than ``open_vec_db()`` for hot paths (session start, recall).
    The connection is NOT closed — it lives for the process lifetime.
    Callers must NOT close the returned connection.

    Validates connection health on each call — if the connection is dead
    (e.g. DB file deleted), recreates it transparently. Returns None only on a
    genuine connect failure.
    """
    global _singleton_conn

    # Fast path: existing connection — validate it's still alive
    if _singleton_conn is not None:
        try:
            _singleton_conn.execute("SELECT 1")
            return _singleton_conn
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            logger.debug("Singleton recall connection dead, recreating")
            _singleton_conn = None

    with _singleton_lock:
        # Double-check after lock
        if _singleton_conn is not None:
            try:
                _singleton_conn.execute("SELECT 1")
                return _singleton_conn
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                _singleton_conn = None

        path = db_path or _DEFAULT_DB_PATH
        try:
            conn = sqlite3.connect(str(path), check_same_thread=False)
            # WAL mode enables concurrent reads during writes — required
            # since check_same_thread=False allows multi-task access.
            conn.execute("PRAGMA journal_mode=WAL")
            _singleton_conn = conn
        except Exception:
            logger.debug("Failed to create singleton recall connection")
            return None
    return _singleton_conn


@contextmanager
def open_vec_db(
    db_path: Optional[Path] = None,
    busy_timeout_ms: int = 30000,
) -> Generator[Optional[sqlite3.Connection], None, None]:
    """Open a plain sqlite3 connection (FTS5-ready) for recall/indexing.

    Usage::

        from core.vec_db import open_vec_db

        with open_vec_db() as conn:
            if conn is None:
                return  # genuine connect failure (rare)
            # ... use conn with the FTS5 tables ...

    Args:
        db_path: Override database path (default: ~/.swarm-ai/data.db).
        busy_timeout_ms: SQLite busy_timeout (default 30000). A LATENCY-BOUNDED
            caller (e.g. synchronous recall under a wait_for disaster cap) MUST
            pass a value SHORTER than its cap, so a sqlite write-lock wait cannot
            out-hang the cap — otherwise the cap is theater (asyncio wait_for
            cannot cancel a to_thread C-level sqlite block). (run_4d06640b B3)

    Yields:
        A sqlite3.Connection, or None ONLY on a genuine connect failure. Unlike
        the old sqlite-vec version, a missing optional dependency no longer
        yields None — recall never silently dies for that reason now.

    Note:
        For hot paths (session start), prefer ``get_vec_conn()`` which
        reuses a singleton connection.
    """
    path = db_path or _DEFAULT_DB_PATH
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        # busy_timeout: WAL allows concurrent readers but a SINGLE writer. Writers
        # (context_health index sync) can overlap; without a timeout the loser gets
        # an immediate SQLITE_BUSY → OperationalError, aborting a batch mid-sync.
        # Default 30s waits the lock out; a latency-bounded caller passes a shorter
        # value so a lock wait cannot exceed its disaster cap (run_4d06640b).
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        yield conn
    except Exception:
        logger.debug("Failed to open recall DB connection")
        yield None
    finally:
        conn.close()
