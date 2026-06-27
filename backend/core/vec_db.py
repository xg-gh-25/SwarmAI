"""Shared sqlite-vec connection helper.

Centralizes the sqlite3 + sqlite-vec extension loading pattern used by
the Knowledge Library, Memory Embeddings, and Recall Engine.  All callers
use ``open_vec_db()`` as a context manager to get a connection with the
vector extension pre-loaded.

Public symbols:

- ``open_vec_db``   — Context manager yielding a sqlite3.Connection with
                      sqlite-vec loaded.  Returns None if sqlite-vec is
                      not installed.
- ``get_vec_conn``  — Get a module-level singleton connection (reused
                      across calls within the same process).  Faster than
                      open_vec_db for hot paths like session start.
- ``VEC_AVAILABLE`` — Boolean flag indicating sqlite-vec availability.
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# Probe once at import time — avoids repeated ImportError overhead.
try:
    import sqlite_vec as _sqlite_vec

    VEC_AVAILABLE = True
except ImportError:
    _sqlite_vec = None  # type: ignore[assignment]
    VEC_AVAILABLE = False

# Default database path (all SwarmAI data lives under ~/.swarm-ai/)
from jobs.paths import DB_PATH as _DEFAULT_DB_PATH

# Module-level singleton connection (thread-safe init via lock).
# Reused across calls to avoid repeated connect + load_extension overhead
# (~10ms each).  The connection is read-only safe for concurrent use from
# a single thread (Python GIL protects sqlite3 module internals).
_singleton_conn: Optional[sqlite3.Connection] = None
_singleton_lock = threading.Lock()


def get_vec_conn(db_path: Optional[Path] = None) -> Optional[sqlite3.Connection]:
    """Get a module-level singleton sqlite-vec connection.

    Faster than ``open_vec_db()`` for hot paths (session start, recall).
    The connection is NOT closed — it lives for the process lifetime.
    Callers must NOT close the returned connection.

    Validates connection health on each call — if the connection is dead
    (e.g. DB file deleted), recreates it transparently.

    Returns None if sqlite-vec is unavailable.
    """
    global _singleton_conn
    if not VEC_AVAILABLE:
        return None

    # Fast path: existing connection — validate it's still alive
    if _singleton_conn is not None:
        try:
            _singleton_conn.execute("SELECT 1")
            return _singleton_conn
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            # Connection is dead — recreate below
            logger.debug("Singleton vec connection dead, recreating")
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
            conn.enable_load_extension(True)
            _sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            _singleton_conn = conn
        except Exception:
            logger.debug("Failed to create singleton vec connection")
            return None
    return _singleton_conn


@contextmanager
def open_vec_db(
    db_path: Optional[Path] = None,
    busy_timeout_ms: int = 30000,
) -> Generator[Optional[sqlite3.Connection], None, None]:
    """Open a sqlite3 connection with sqlite-vec extension loaded.

    Usage::

        from core.vec_db import open_vec_db

        with open_vec_db() as conn:
            if conn is None:
                return  # sqlite-vec not installed
            # ... use conn with vector tables ...

    Args:
        db_path: Override database path (default: ~/.swarm-ai/data.db).
        busy_timeout_ms: SQLite busy_timeout (default 30000). A LATENCY-BOUNDED
            caller (e.g. synchronous recall under a wait_for disaster cap) MUST
            pass a value SHORTER than its cap, so a sqlite write-lock wait cannot
            out-hang the cap — otherwise the cap is theater (asyncio wait_for
            cannot cancel a to_thread C-level sqlite block). (run_4d06640b B3)

    Yields:
        A sqlite3.Connection with sqlite-vec loaded, or None if the
        extension is unavailable.  The connection is automatically
        closed when the context manager exits.

    Note:
        For hot paths (session start), prefer ``get_vec_conn()`` which
        reuses a singleton connection.
    """
    if not VEC_AVAILABLE:
        yield None
        return

    path = db_path or _DEFAULT_DB_PATH
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        # busy_timeout: WAL allows concurrent readers but a SINGLE writer. The
        # memory-embedding recovery drain (context_health_hook) and other writers
        # can overlap on memory_vec; without a timeout the loser gets an immediate
        # SQLITE_BUSY → OperationalError, aborting a batch mid-drain. Default 30s
        # waits the lock out; a latency-bounded caller passes a shorter value so a
        # lock wait cannot exceed its disaster cap (run_4d06640b). (run_e9b15722)
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        conn.enable_load_extension(True)
        _sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        yield conn
    except Exception:
        logger.debug("Failed to load sqlite-vec extension")
        yield None
    finally:
        conn.close()
