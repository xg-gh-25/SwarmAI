"""SQLite graph store for code intelligence — nodes, edges, and CTE queries.

Adapted from code-review-graph patterns: WAL mode, FTS5 content tables,
batched IN clauses, bidirectional CTE traversal for blast-radius analysis.

Public symbols:

- ``GraphStore``  — SQLite-backed graph of code nodes and dependency edges.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# SQLite hard limit is 999 variables; stay well below.
_BATCH_SIZE = 450

# Regex for sanitising symbol names against prompt injection.
_UNSAFE_RE = re.compile(r"[^\w.*?_ -]", re.ASCII)


def _sanitize_name(name: str) -> str:
    """Strip characters that could escape SQL or FTS queries."""
    return _UNSAFE_RE.sub("", name)[:256]


# FTS `rank` is negative (more-negative = better). This positive penalty is ADDED
# to test-file symbol rows so they sort below comparable prod symbols WITHOUT being
# excluded (run_fc313f42). CALIBRATED to the live DB (Gate-2 HIGH): the whole
# relevant BM25 spread is only ~5-11 and the typical prod-vs-test gap is ~3-4, so a
# large penalty (8.0) shoved a strongly-matching test past ~30 weak prod symbols.
# 3.0 is a TIE-BREAKER: demotes a test roughly tied with a prod symbol, but a
# genuinely-stronger test still surfaces. AND the penalty is SKIPPED entirely when
# the query itself carries test intent (see _query_wants_tests) — else "how is X
# tested" returned ZERO test symbols in the top-8 (the exact regression Gate-2 found).
_TEST_SYMBOL_RANK_PENALTY = 3.0

# Query tokens that signal the user WANTS test symbols — penalty is skipped then.
_TEST_INTENT_TOKENS = frozenset({"test", "tests", "tested", "testing", "spec",
                                 "fixture", "fixtures", "mock", "mocks", "conftest"})


def _query_wants_tests(query: str) -> bool:
    """True if the query explicitly signals test intent — then test symbols must
    NOT be down-weighted (Gate-2 HIGH: 'how is X tested' must return tests)."""
    toks = {t for t in _sanitize_name(query).lower().split() if t}
    return bool(toks & _TEST_INTENT_TOKENS)


def _is_test_symbol_path(file_path: str) -> bool:
    """True if a symbol's file is a TEST file — segment-anchored, NOT a bare 'test'
    substring (which false-matches attestation.py / latest_run.py; the same lesson
    as analysis.is_test_path, run_4344d341)."""
    fp = (file_path or "").lower()
    segments = fp.split("/")
    if "tests" in segments or "test" in segments or "__tests__" in segments:
        return True
    fname = fp.rsplit("/", 1)[-1]
    return (fname.startswith("test_") or fname.endswith("_test.py")
            or fname == "conftest.py" or ".test." in fname or ".spec." in fname)


# ── Schema DDL ───────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS code_nodes (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    language TEXT NOT NULL,
    is_export INTEGER DEFAULT 1,
    is_entry_point INTEGER DEFAULT 0,
    file_hash TEXT,
    indexed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS code_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    line_number INTEGER
);

-- NOTE: the edge-identity UNIQUE INDEX (idx_edges_identity) is NOT created here.
-- It is owned exclusively by _migrate_schema(), because on a LEGACY DB the table
-- still holds NULL-line duplicate rows and creating a UNIQUE index here would raise
-- IntegrityError before the migration can dedup. _migrate_schema dedups first, then
-- installs the index as the sole edge-identity authority (see its docstring).

CREATE INDEX IF NOT EXISTS idx_edges_source ON code_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON code_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_nodes_file   ON code_nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_name   ON code_nodes(name);

CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(
    name, id, file_path,
    content='code_nodes', content_rowid=rowid,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS code_routes (
    id TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    handler_node_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line_number INTEGER,
    middleware TEXT,
    FOREIGN KEY (handler_node_id) REFERENCES code_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_routes_path ON code_routes(path);
CREATE INDEX IF NOT EXISTS idx_routes_handler ON code_routes(handler_node_id);

CREATE TABLE IF NOT EXISTS graph_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Imports, kept apart from code_edges: the graph has no file nodes, so a file→file
-- edge would be dropped at once by the orphan cleanup. One row per imported name;
-- dst_file is the repo file (or, for Go, the package directory with a trailing "/")
-- it resolves to, NULL for an outside module (resolve_imports).
CREATE TABLE IF NOT EXISTS code_imports (
    src_file TEXT NOT NULL,
    name TEXT NOT NULL,
    module TEXT NOT NULL,
    line INTEGER,
    dst_file TEXT
);
CREATE INDEX IF NOT EXISTS idx_imports_src ON code_imports(src_file);
CREATE INDEX IF NOT EXISTS idx_imports_dst ON code_imports(dst_file);
"""


# Languages whose parser types every method as node_type "method" (a class body, a
# go receiver, a rust impl): only there may a value's method call be restricted to
# methods (resolve_bare_targets).
_METHOD_TYPED_LANGS = frozenset({"python", "typescript", "javascript", "java", "kotlin",
                                 "go", "rust", "ruby", "csharp"})


# ── When a graph was indexed ───────────────────────────────────────────────
# One format for the two markers (``last_full_index`` after a whole-repo build,
# ``last_incremental_update`` after a batch): ISO 8601 in UTC, written only by
# GraphStore.mark_indexed. Graphs written before held an epoch float in one and ISO
# in the other, and each reader parsed one format; parse_index_time reads both.

def parse_index_time(value) -> float | None:
    """A marker value as a POSIX timestamp: ISO 8601 (the format written now) or an
    epoch number (older graphs). None when absent or unreadable."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ── Thread-safe connection wrapper ─────────────────────────────────────────


class _MaterializedCursor:
    """A cursor whose rows are already fetched into memory.

    Returned by ``_LockingConnection.execute``. Because the rows are materialized
    UNDER the connection lock (inside ``execute``), the subsequent ``.fetchall()``
    / ``.fetchone()`` / iteration touch only this in-memory list — they never
    re-enter the real sqlite3 connection, so an ``execute(...).fetchall()`` chain
    is atomic w.r.t. a concurrent writer without the caller having to hold the lock
    across the fetch. ``lastrowid`` / ``rowcount`` are snapshotted for writes.
    """

    __slots__ = ("_rows", "_i", "lastrowid", "rowcount")

    def __init__(self, rows: list, lastrowid, rowcount):
        self._rows = rows
        self._i = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchall(self):
        rest = self._rows[self._i:]
        self._i = len(self._rows)
        return rest

    def fetchone(self):
        if self._i < len(self._rows):
            row = self._rows[self._i]
            self._i += 1
            return row
        return None

    def fetchmany(self, size: int = 1):
        rest = self._rows[self._i:self._i + size]
        self._i += len(rest)
        return rest

    def __iter__(self):
        return iter(self._rows[self._i:])


class _LockingConnection:
    """Serializes ALL access to a single sqlite3.Connection behind one RLock.

    WHY (R1, run_071e54c8 — adversarial Gate-2 HIGH): the GraphStore is cached at
    module level (``__init__._graph_cache``) so its ONE connection is reached
    concurrently from SEVERAL threads across DIFFERENT executor pools — code_intel
    READS (``routers/code_intel.py`` on ``asyncio.to_thread``; ``recall_multi`` on the
    bounded ``executors "io"`` pool) and the incremental WRITES of ``ensure_fresh``
    (the reindex job, ``impact``). This is NOT an exhaustive list
    and the pool a caller uses is IRRELEVANT to the hazard: any 2 threads sharing one
    ``sqlite3.Connection`` object is the danger, regardless of which pool they came from.
    A single ``sqlite3.Connection`` is NOT safe for concurrent use across threads
    even with ``check_same_thread=False``: Python only drops the ownership
    *assertion*, it adds no locking, so two threads sharing the connection corrupt
    the shared cursor/statement state → ``Recursive use of cursors not allowed`` /
    ``SQLITE_MISUSE`` (reproduced by test_graph_store::TestConnectionThreadSafety).
    WAL mode + busy_timeout do NOT fix this — they solve multi-CONNECTION / multi-
    PROCESS contention, never two threads on one Connection OBJECT.

    A reentrant lock (RLock) is required because the store's own methods nest
    (e.g. ``store_file_nodes_edges`` → ``upsert_nodes``), so the same thread must be
    able to re-acquire. ``execute`` materializes rows under the lock (see
    ``_MaterializedCursor``) so the ``execute(...).fetchall()`` idiom stays atomic.
    """

    def __init__(self, conn: "sqlite3.Connection", lock: "threading.RLock") -> None:
        self._conn = conn
        self._lock = lock
        # Inside GraphStore.transaction(): the store's own commits wait for the outer
        # one, and a rollback is remembered (the transaction then fails as a whole).
        self._defer_commit = 0
        self._rolled_back = False

    def execute(self, sql: str, params=None) -> _MaterializedCursor:
        with self._lock:
            cur = self._conn.execute(sql, params) if params is not None \
                else self._conn.execute(sql)
            # Materialize under the lock. DDL/DML cursors yield no rows; fetchall()
            # on them is a cheap no-op ([]), and lastrowid/rowcount are captured.
            rows = cur.fetchall()
            return _MaterializedCursor(rows, cur.lastrowid, cur.rowcount)

    def executemany(self, sql: str, seq) -> _MaterializedCursor:
        with self._lock:
            cur = self._conn.executemany(sql, seq)
            return _MaterializedCursor([], cur.lastrowid, cur.rowcount)

    def executescript(self, script: str) -> _MaterializedCursor:
        with self._lock:
            cur = self._conn.executescript(script)
            return _MaterializedCursor([], cur.lastrowid, cur.rowcount)

    def commit(self) -> None:
        with self._lock:
            if self._defer_commit:
                return
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            if self._defer_commit:
                self._rolled_back = True
            self._conn.rollback()

    def cursor(self):
        """Return a RAW sqlite3 cursor. ⚠️ DANGER: the returned cursor is NOT
        lock-guarded — any statement run on it bypasses the serialization this
        proxy exists to provide (R1 race, run_071e54c8). A multi-statement
        read-modify-write on a raw cursor MUST be wrapped by the caller in
        ``with graph._conn_lock:`` for the WHOLE sequence (see
        core.code_intel.resolve_router_prefixes, the sanctioned lock-holding RMW
        caller). Prefer ``.execute()`` on
        this proxy (locked + row-materialized) for anything that isn't a
        TEMP-TABLE/RMW sequence. Do NOT add a new raw-cursor caller without the lock.
        """
        with self._lock:
            return self._conn.cursor()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name):
        # Delegate any OTHER attribute (in_transaction, iterdump, backup, …) to the
        # real conn. NOTE: SQL-executing methods (execute/executemany/executescript/
        # commit/rollback/cursor) are ALL overridden above with explicit locking, so
        # __getattr__ only ever reaches non-executing accessors — it cannot leak an
        # unlocked SQL path. A property read like in_transaction is benign.
        return getattr(self._conn, name)


# ── GraphStore ───────────────────────────────────────────────────────────



def _write_locked(method):
    """Run a GraphStore write method with the connection lock held for its WHOLE body.

    ``_LockingConnection`` locks one statement at a time. A write that spans several
    statements (delete a file's rows, re-insert them, prune) would otherwise let
    another thread on the same connection read the half-done state, commit it, or
    roll it back. The lock is re-entrant, so these methods may nest; when the
    outermost one raises, the open transaction is rolled back.
    """
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._conn_lock:
            depth = getattr(self, "_write_depth", 0)
            self._write_depth = depth + 1
            try:
                return method(self, *args, **kwargs)
            except BaseException:
                # The outermost write that fails rolls back whatever transaction is
                # open on the shared connection. Left open, the next write would see
                # in_transaction, skip its own BEGIN/rollback, and the next commit
                # anywhere would make this failed write's partial deletes permanent
                # (while the open transaction held the database write lock).
                if depth == 0 and self._conn.in_transaction:
                    try:
                        self._conn.rollback()
                    except sqlite3.Error as exc:
                        logger.warning("graph write rollback failed: %s", exc)
                raise
            finally:
                self._write_depth = depth
    return wrapper


class GraphStore:
    """SQLite-backed graph of code symbols (nodes) and their relationships (edges).

    Uses WAL mode for concurrent reads, FTS5 for symbol search, and recursive
    CTEs for transitive dependency traversal (blast radius, callers, etc.).
    """

    def __init__(self, db_path: Path) -> None:
        """Open or create the graph database at *db_path*.

        Sets WAL journal mode and a 5-second busy timeout so concurrent
        readers/writers don't immediately fail.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: GraphStore is cached at module level, so its one
        # connection is reached concurrently from several threads across different
        # executor pools (code_intel reads on to_thread/"io"; incremental writes
        # from ensure_fresh) —
        # not an exhaustive list, and the pool is irrelevant: any 2 threads on one
        # Connection object is the hazard.
        # check_same_thread=False only DISABLES sqlite3's ownership assertion;
        # it adds NO locking, and a single Connection is NOT safe for concurrent
        # cross-thread use (shared cursor/statement state → SQLITE_MISUSE). WAL +
        # busy_timeout do NOT cover this (they solve multi-connection/-process
        # contention, not two threads on one Connection object). So every access
        # goes through _LockingConnection, which serializes on this RLock and
        # materializes rows under the lock (keeps execute(...).fetchall() atomic).
        self._conn_lock = threading.RLock()
        _raw_conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn = _LockingConnection(_raw_conn, self._conn_lock)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        # Bound the WAL's auto-reset cadence. NOTE: autocheckpoint (PASSIVE) resets
        # the WAL *header* so frames are reused, but it NEVER shrinks the WAL FILE on
        # disk — after a large re-index the file stays multi-GB forever (observed
        # 2.73GB vs a 64MB DB). Only an explicit TRUNCATE checkpoint reclaims the
        # file; see checkpoint_truncate(), called at the tail of every bulk write.
        self._conn.execute("PRAGMA wal_autocheckpoint=2000")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        self._migrate_schema()

    # ── schema migration ─────────────────────────────────────────────────

    # Current edge-identity schema version. Bump when the code_edges identity
    # contract changes. v2 = separate UNIQUE INDEX with IFNULL(line_number,-1)
    # replacing the legacy inline UNIQUE(source,target,edge_type,line_number)
    # that let NULL-line edges duplicate (codegraph #1034).
    _EDGE_IDENTITY_VERSION = 2

    def _migrate_schema(self) -> None:
        """Bring an existing DB's edge-identity contract up to the current version.

        code_intel.db is a rebuildable derived cache with no ALTER-based migration
        framework — schema is (re)declared via ``CREATE ... IF NOT EXISTS`` on every
        open, which is a NO-OP on an existing table. So a table that was created with
        the OLD inline ``UNIQUE(source_id,target_id,edge_type,line_number)`` keeps that
        constraint forever; merely adding the new ``idx_edges_identity`` index would
        leave TWO conflicting identity rules (the inline one treats NULL-line rows as
        distinct; the index folds them) and make ``INSERT OR REPLACE`` ambiguous
        (Gate-1 #1/#2). The only correct fix on a legacy DB is to REBUILD the table
        without the inline UNIQUE, deduping NULL-line rows in the process.

        Idempotent + fail-safe: keyed on ``graph_meta.edge_identity_version``; a
        fresh DB (already built from the new DDL) just records the version and does
        no work. Any failure rolls back and is swallowed — a stale identity contract
        degrades dedup, it must never break opening the store (the next full reindex
        rebuilds cleanly).
        """
        try:
            current = self.get_meta("edge_identity_version")
            if current is not None and int(current) >= self._EDGE_IDENTITY_VERSION:
                return  # already migrated

            # Detect the legacy inline UNIQUE by inspecting the stored table DDL.
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='code_edges'"
            ).fetchone()
            table_sql = (row[0] if row else "") or ""
            has_inline_unique = "UNIQUE(source_id, target_id, edge_type, line_number)" in table_sql

            if not has_inline_unique:
                # Fresh DB (or already-rebuilt table): no dedup needed, just ensure
                # the identity index exists. _SCHEMA_SQL no longer creates it.
                self._conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_identity "
                    "ON code_edges(source_id, target_id, edge_type, IFNULL(line_number, -1))"
                )
                self._conn.commit()
            else:
                # Rebuild the table WITHOUT the inline UNIQUE, deduping on the folded
                # identity key and keeping MAX(confidence) per group.
                #
                # ATOMICITY (Gate-2 HIGH, multi-specialist confirmed + observed): the
                # rebuild MUST be one all-or-nothing transaction, else a mid-way
                # failure leaves a half-migrated DB (legacy renamed away, new table
                # not built) that the swallow-except below would hide. sqlite3's
                # `executescript()` IMPLICITLY COMMITS any pending transaction before
                # it runs — so a `BEGIN` + `executescript` does NOT wrap the rebuild
                # (verified: rows survived a rollback). We therefore issue each
                # statement via `execute()` inside a single explicit transaction.
                #
                # CONCURRENCY (Gate-2 HIGH): `BEGIN IMMEDIATE` takes the write lock up
                # front, so if a second daemon/hook thread opens the same DB
                # concurrently it blocks on busy_timeout instead of racing into a
                # double ALTER TABLE RENAME. The loser, once it acquires the lock,
                # re-reads the DDL — the inline UNIQUE is gone (winner rebuilt it) →
                # it takes the no-op branch. Re-checked below to be race-safe.
                self._conn.execute("BEGIN IMMEDIATE")
                # Re-read DDL under the write lock: a concurrent migrator may have
                # rebuilt the table between our unlocked check and acquiring the lock.
                row2 = self._conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='code_edges'"
                ).fetchone()
                if row2 and "UNIQUE(source_id, target_id, edge_type, line_number)" in (row2[0] or ""):
                    for stmt in (
                        "DROP INDEX IF EXISTS idx_edges_identity",
                        "ALTER TABLE code_edges RENAME TO code_edges_legacy",
                        """CREATE TABLE code_edges (
                               source_id TEXT NOT NULL,
                               target_id TEXT NOT NULL,
                               edge_type TEXT NOT NULL,
                               confidence REAL DEFAULT 1.0,
                               line_number INTEGER
                           )""",
                        """INSERT INTO code_edges (source_id, target_id, edge_type, confidence, line_number)
                               SELECT source_id, target_id, edge_type, MAX(confidence), line_number
                               FROM code_edges_legacy
                               GROUP BY source_id, target_id, edge_type, IFNULL(line_number, -1)""",
                        "DROP TABLE code_edges_legacy",
                        """CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_identity
                               ON code_edges(source_id, target_id, edge_type, IFNULL(line_number, -1))""",
                        "CREATE INDEX IF NOT EXISTS idx_edges_source ON code_edges(source_id)",
                        "CREATE INDEX IF NOT EXISTS idx_edges_target ON code_edges(target_id)",
                    ):
                        self._conn.execute(stmt)
                self._conn.commit()

            self.set_meta("edge_identity_version", str(self._EDGE_IDENTITY_VERSION))
        except Exception as e:
            try:
                self._conn.rollback()
            except Exception:
                pass
            logger.warning("code_edges schema migration skipped: %s", e)
            # Consistency backstop (Gate-2 MED): a rollback should undo everything,
            # but if the DB is somehow left half-migrated (legacy table orphaned),
            # do NOT record the version — leave the store in a state the next open
            # will re-attempt, rather than silently stamping it "migrated".
            try:
                orphan = self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_edges_legacy'"
                ).fetchone()
                if orphan:
                    logger.error(
                        "code_edges migration left an orphaned code_edges_legacy table "
                        "— NOT recording version; next open will retry. Manual reindex advised."
                    )
                    return
            except Exception:
                pass

    # ── lifecycle ────────────────────────────────────────────────────────

    def checkpoint_truncate(self) -> None:
        """Checkpoint the WAL and TRUNCATE the on-disk WAL file to reclaim space.

        Data-safe and online: TRUNCATE first flushes all committed WAL frames into
        the main DB, then shrinks the ``-wal`` file to zero. Called at the tail of
        bulk writes (``bulk_insert`` / ``incremental_update``) because those are the
        ops that balloon the WAL; per-commit checkpointing would throttle throughput.

        Non-fatal: if a concurrent reader holds the WAL, TRUNCATE reports ``busy!=0``
        and leaves the file — that is fine, the next bulk write retries. A failure
        here must NEVER break indexing, so all errors are swallowed with a debug log
        (the WAL merely stays large; correctness is unaffected).
        """
        if not self._conn or getattr(self._conn, "_defer_commit", 0):
            # inside transaction(): nothing is committed yet, the caller checkpoints
            return
        try:
            row = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            # row = (busy, log_pages, checkpointed_pages); busy=1 → a reader blocked it
            if row and row[0]:
                logger.debug("WAL checkpoint(TRUNCATE) busy — reader held WAL, will retry next bulk write")
        except Exception as e:
            logger.debug("WAL checkpoint(TRUNCATE) skipped: %s", e)

    @contextlib.contextmanager
    def transaction(self):
        """Run a multi-step write as ONE SQLite transaction (``BEGIN IMMEDIATE``).

        The store's methods commit as they go; inside this block those commits wait
        for the end, so a reader in another process (the daemon reading a graph a CLI
        or a job is rebuilding) sees the graph before or after, never half of it, and a
        writer killed in the middle leaves the old graph. A rollback inside the block
        (a failed write method) fails the whole transaction.
        """
        conn = self._conn
        with self._conn_lock:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            conn._defer_commit += 1
            conn._rolled_back = False
            try:
                yield self
            except BaseException:
                conn._defer_commit -= 1
                if conn.in_transaction:
                    conn.rollback()
                raise
            conn._defer_commit -= 1
            if conn._rolled_back:
                if conn.in_transaction:
                    conn.rollback()
                raise sqlite3.OperationalError("a write inside the transaction rolled back")
            conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        """Safety net — close connection if not explicitly closed."""
        try:
            self.close()
        except Exception:
            pass

    # ── meta helpers ─────────────────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        """Return a metadata value, or *None* if the key does not exist."""
        row = self._conn.execute(
            "SELECT value FROM graph_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Insert or replace a metadata key/value pair."""
        self._conn.execute(
            "INSERT OR REPLACE INTO graph_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    # ── batch helpers (CRG pattern) ──────────────────────────────────────

    @staticmethod
    def _batch_query(
        conn: sqlite3.Connection,
        sql_template: str,
        ids: list[str],
        batch_size: int = _BATCH_SIZE,
    ) -> list[sqlite3.Row | tuple]:
        """Execute *sql_template* in batches to respect the SQLite variable limit.

        *sql_template* must contain a single ``{placeholders}`` token that will
        be replaced with ``?,?,?,...`` for each batch.
        """
        results: list = []
        for start in range(0, len(ids), batch_size):
            batch = ids[start : start + batch_size]
            placeholders = ",".join("?" * len(batch))
            sql = sql_template.replace("{placeholders}", placeholders)
            results.extend(conn.execute(sql, batch).fetchall())
        return results

    # ── node CRUD ────────────────────────────────────────────────────────

    def upsert_nodes(self, nodes: list) -> int:
        """Batch insert/update *nodes*. Returns count of rows upserted.

        Each element must be a dict or dataclass with fields matching the
        ``code_nodes`` table columns.
        """
        sql = """
            INSERT OR REPLACE INTO code_nodes
                (id, file_path, node_type, name, line_start, line_end,
                 language, is_export, is_entry_point, file_hash, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        count = 0
        now = time.time()
        for start in range(0, len(nodes), _BATCH_SIZE):
            batch = nodes[start : start + _BATCH_SIZE]
            rows = []
            for n in batch:
                d = n if isinstance(n, dict) else n.__dict__
                rows.append((
                    d["id"],
                    d["file_path"],
                    d["node_type"],
                    _sanitize_name(d["name"]),
                    d["line_start"],
                    d["line_end"],
                    d["language"],
                    int(d.get("is_export", 1)),
                    int(d.get("is_entry_point", 0)),
                    # CodeNode carries the raw-file sha as `sha256`, not `file_hash`
                    # (parser.py). Fall back so the FULL-REBUILD path persists a real
                    # file hash — else every node stores file_hash=NULL and the graded
                    # incremental NONE-detection (byte_changed = content_hash != old_hash)
                    # is dead on the first run after a full rebuild (Gate-2 HIGH,
                    # run_4602932d). Dicts that pass explicit file_hash are unaffected.
                    d.get("file_hash") or d.get("sha256"),
                    d.get("indexed_at", now),
                ))
            self._conn.executemany(sql, rows)
            count += len(rows)
        self._conn.commit()
        return count

    # ── edge CRUD ────────────────────────────────────────────────────────

    def upsert_edges(self, edges: list) -> int:
        """Batch insert/update *edges*. Returns count of rows upserted.

        Each element must be a dict or dataclass with fields: source_id,
        target_id, edge_type, and optionally confidence, line_number.
        """
        sql = """
            INSERT OR REPLACE INTO code_edges
                (source_id, target_id, edge_type, confidence, line_number)
            VALUES (?, ?, ?, ?, ?)
        """
        count = 0
        for start in range(0, len(edges), _BATCH_SIZE):
            batch = edges[start : start + _BATCH_SIZE]
            rows = []
            for e in batch:
                d = e if isinstance(e, dict) else e.__dict__
                rows.append((
                    d["source_id"],
                    d["target_id"],
                    d["edge_type"],
                    d.get("confidence", 1.0),
                    d.get("line_number"),
                ))
            self._conn.executemany(sql, rows)
            count += len(rows)
        self._conn.commit()
        return count

    # ── atomic file replacement (CRG pattern) ────────────────────────────

    @_write_locked
    def store_file_nodes_edges(
        self,
        file_path: str,
        nodes: list,
        edges: list,
        file_hash: str,
        imports: list | None = None,
    ) -> None:
        """Atomically replace all nodes/edges (and, when given, the imports) for
        *file_path*. *imports* is the file's ``ImportRef`` list; None leaves its
        stored imports as they are (a caller that did not scan them).

        Deletes old data and inserts new data inside a single
        ``BEGIN IMMEDIATE`` transaction so readers never see a partial state.

        Edges: the file's OUTBOUND edges are replaced (the parser re-emits exactly
        those). INBOUND edges from other files are kept when their target node still
        exists after the replace — node ids are ``file::Qualified.name``, so an edit
        that keeps a symbol keeps its id — and dropped only when the target symbol is
        gone. Deleting every inbound edge (the old behaviour) emptied
        ``find_callers`` for the file's symbols after any incremental re-store, since
        nothing re-emits other files' edges until the next full rebuild.
        """
        # CRG pattern: avoid nested BEGIN if already in a transaction.
        in_txn = self._conn.in_transaction
        try:
            if not in_txn:
                self._conn.execute("BEGIN IMMEDIATE")

            # Delete old FTS entries for nodes in this file.
            old_rowids = self._conn.execute(
                "SELECT rowid FROM code_nodes WHERE file_path = ?",
                (file_path,),
            ).fetchall()
            for (rowid,) in old_rowids:
                self._conn.execute(
                    "INSERT INTO code_fts(code_fts, rowid, name, id, file_path) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (
                        rowid,
                        *self._conn.execute(
                            "SELECT name, id, file_path FROM code_nodes "
                            "WHERE rowid = ?",
                            (rowid,),
                        ).fetchone(),
                    ),
                )

            # Delete old nodes and their OUTBOUND edges. Inbound edges are settled
            # after the new nodes are in (below): kept if their target survives.
            old_ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT id FROM code_nodes WHERE file_path = ?",
                    (file_path,),
                ).fetchall()
            ]
            if old_ids:
                tmpl = "DELETE FROM code_edges WHERE source_id IN ({placeholders})"
                for start in range(0, len(old_ids), _BATCH_SIZE):
                    batch = old_ids[start : start + _BATCH_SIZE]
                    ph = ",".join("?" * len(batch))
                    self._conn.execute(tmpl.replace("{placeholders}", ph), batch)
            self._conn.execute(
                "DELETE FROM code_nodes WHERE file_path = ?", (file_path,)
            )

            # Insert new nodes.
            now = time.time()
            node_sql = """
                INSERT OR REPLACE INTO code_nodes
                    (id, file_path, node_type, name, line_start, line_end,
                     language, is_export, is_entry_point, file_hash, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            for n in nodes:
                d = n if isinstance(n, dict) else n.__dict__
                self._conn.execute(node_sql, (
                    d["id"],
                    d["file_path"],
                    d["node_type"],
                    _sanitize_name(d["name"]),
                    d["line_start"],
                    d["line_end"],
                    d["language"],
                    int(d.get("is_export", 1)),
                    int(d.get("is_entry_point", 0)),
                    file_hash,
                    d.get("indexed_at", now),
                ))

            # Insert new edges.
            edge_sql = """
                INSERT OR REPLACE INTO code_edges
                    (source_id, target_id, edge_type, confidence, line_number)
                VALUES (?, ?, ?, ?, ?)
            """
            for e in edges:
                d = e if isinstance(e, dict) else e.__dict__
                self._conn.execute(edge_sql, (
                    d["source_id"],
                    d["target_id"],
                    d["edge_type"],
                    d.get("confidence", 1.0),
                    d.get("line_number"),
                ))

            # Insert new FTS entries.
            for n in nodes:
                d = n if isinstance(n, dict) else n.__dict__
                rowid = self._conn.execute(
                    "SELECT rowid FROM code_nodes WHERE id = ?", (d["id"],)
                ).fetchone()
                if rowid:
                    self._conn.execute(
                        "INSERT INTO code_fts(rowid, name, id, file_path) "
                        "VALUES (?, ?, ?, ?)",
                        (rowid[0], _sanitize_name(d["name"]), d["id"], d["file_path"]),
                    )

            # Inbound edges whose target symbol no longer exists in this file.
            new_ids = {
                (n if isinstance(n, dict) else n.__dict__)["id"] for n in nodes
            }
            vanished = [i for i in old_ids if i not in new_ids]
            if vanished:
                tmpl = "DELETE FROM code_edges WHERE target_id IN ({placeholders})"
                for start in range(0, len(vanished), _BATCH_SIZE):
                    batch = vanished[start : start + _BATCH_SIZE]
                    ph = ",".join("?" * len(batch))
                    self._conn.execute(tmpl.replace("{placeholders}", ph), batch)

            if imports is not None:
                self._replace_imports(file_path, imports)

            if not in_txn:
                self._conn.commit()
        except Exception:
            if not in_txn:
                self._conn.rollback()
            raise

    # ── imports (design 2026-10-27 §5.8 G3) ──────────────────────────────

    def _replace_imports(self, file_path: str, imports: list) -> None:
        """The file's import rows replaced (inside the caller's transaction);
        dst_file is left NULL for ``resolve_imports``."""
        self._conn.execute("DELETE FROM code_imports WHERE src_file = ?", (file_path,))
        rows = []
        for ref in imports or []:
            d = ref if isinstance(ref, dict) else ref.__dict__
            rows.append((file_path, d["name"], d["module"], d.get("line")))
        if rows:
            self._conn.executemany(
                "INSERT INTO code_imports (src_file, name, module, line) VALUES (?, ?, ?, ?)", rows)

    @_write_locked
    def replace_imports(self, file_path: str, imports: list) -> None:
        """Replace one file's import rows on their own (a re-store that keeps the
        file's symbols, e.g. only an import line changed)."""
        self._replace_imports(file_path, imports)
        self._conn.commit()

    def resolve_imports(self, files: list[str] | None = None) -> int:
        """Point import rows at the repo file (Go: package directory) they import.

        Resolves the rows of *files* only (an incremental re-store), or every row (None:
        a full rebuild). Scoped like ``resolve_bare_targets``: another connection may be
        part-way through a rebuild, and a suffix that looks unique there could be
        recorded for good; the full rebuild's own pass re-resolves every row, so a
        file that appears later, or a second file with the same name, is accounted for
        there. The rule per language, against the paths the graph knows (``code_nodes``
        plus every importing file):
        - Python: ``from a.b import c`` → ``a/b.py``, ``a/b/__init__.py`` or (``c`` a
          submodule) ``a/b/c.py``; leading dots are relative to the importing file;
        - TS/JS: a relative module (``./x``, ``../x``) → ``x.ts``/``.tsx``/``.js``/…
          or ``x/index.*``; a package name stays outside;
        - Java: ``a.b.C`` → a file ending ``a/b/C.java``;
        - Go: the import path → the repo directory it ends with.
        A path matched only by its suffix must be unique; anything else stays NULL
        (an outside module, or ambiguous). Returns the number of rows resolved.
        """
        import posixpath
        known = {r[0] for r in self._conn.execute(
            "SELECT DISTINCT file_path FROM code_nodes UNION SELECT DISTINCT src_file FROM code_imports")}
        by_name: dict[str, list[str]] = {}     # basename → paths (suffix matches)
        dirs: set[str] = set()
        for f in known:
            by_name.setdefault(f.rsplit("/", 1)[-1], []).append(f)
            parts = f.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))

        def unique(cands: list[str], pool) -> str | None:
            for c in cands:
                if c in pool:
                    return c
            for c in cands:
                hits = [f for f in by_name.get(c.rsplit("/", 1)[-1], ()) if f.endswith("/" + c)]
                if len(hits) == 1:
                    return hits[0]
            return None

        def resolve(src: str, name: str, module: str) -> str | None:
            ext = posixpath.splitext(src)[1].lower()
            here = posixpath.dirname(src)
            if ext in (".py", ".pyi"):
                dots = len(module) - len(module.lstrip("."))
                path = module.lstrip(".").replace(".", "/")
                if dots:
                    base = here
                    for _ in range(dots - 1):
                        base = posixpath.dirname(base)
                    path = posixpath.join(base, path) if path else base
                    pool = known
                    cands = ([f"{path}/{name}.py", f"{path}.py", f"{path}/__init__.py"]
                             if path else [f"{name}.py"])
                    return next((c for c in cands if c in pool), None)
                cands = [f"{path}/{name}.py", f"{path}.py", f"{path}/__init__.py"]
                return unique(cands, known)
            if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                if not module.startswith("."):
                    return None
                path = posixpath.normpath(posixpath.join(here, module))
                cands = [path] + [path + e for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")] + \
                        [f"{path}/index{e}" for e in (".ts", ".tsx", ".js", ".jsx")]
                return next((c for c in cands if c in known), None)
            if ext == ".java":
                return unique([module.replace(".", "/") + ".java"], known)
            if ext == ".go":
                hits = [d for d in dirs if module == d or module.endswith("/" + d)]
                if not hits:
                    return None
                best = max(hits, key=len)
                if sum(1 for d in hits if len(d) == len(best)) > 1:
                    return None
                return best + "/"
            return None

        cols = "SELECT rowid, src_file, name, module, dst_file FROM code_imports"
        if files is None:
            rows = list(self._conn.execute(cols))
        else:
            rows = []
            for start in range(0, len(files), _BATCH_SIZE):
                batch = list(files[start: start + _BATCH_SIZE])
                ph = ",".join("?" * len(batch))
                rows += list(self._conn.execute(f"{cols} WHERE src_file IN ({ph})", batch))
        done = 0
        for rowid, src, name, module, old in rows:
            dst = resolve(src, name, module)
            if dst == src:
                dst = None
            if dst != old:
                self._conn.execute("UPDATE code_imports SET dst_file = ? WHERE rowid = ?", (dst, rowid))
            done += dst is not None
        return done

    def node_lines(self, ids: list[str]) -> dict[str, int]:
        """``{node id: line_start}`` for *ids* (absent ids omitted)."""
        out: dict[str, int] = {}
        for start in range(0, len(ids), _BATCH_SIZE):
            batch = list(ids[start: start + _BATCH_SIZE])
            ph = ",".join("?" * len(batch))
            for node_id, line in self._conn.execute(
                    f"SELECT id, line_start FROM code_nodes WHERE id IN ({ph})", batch):
                if line:
                    out[node_id] = int(line)
        return out

    def mark_indexed(self, *, full: bool, head: str | None = None) -> None:
        """Stamp when the graph was indexed (and, given *head*, at which commit) — the
        one writer of the time markers (see ``parse_index_time``)."""
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.set_meta("last_full_index" if full else "last_incremental_update", stamp)
        if head:
            self.set_meta("last_indexed_commit", head)

    def indexed_at(self) -> float | None:
        """When the graph was last indexed (the newer of the two markers), as a POSIX
        timestamp; None when never stamped."""
        times = [parse_index_time(self.get_meta(k))
                 for k in ("last_full_index", "last_incremental_update")]
        times = [t for t in times if t is not None]
        return max(times) if times else None

    def imported_files(self, src_file: str) -> set[str]:
        """The repo files (Go: ``dir/``) *src_file* imports."""
        return {r[0] for r in self._conn.execute(
            "SELECT DISTINCT dst_file FROM code_imports WHERE src_file = ? AND dst_file IS NOT NULL",
            (src_file,))}

    # ── FTS rebuild ──────────────────────────────────────────────────────

    @_write_locked
    def rebuild_fts(self) -> None:
        """Full FTS5 rebuild — delete all FTS rows, re-insert from code_nodes.

        CRG pattern: content-table FTS requires explicit sync.
        """
        self._conn.execute(
            "INSERT INTO code_fts(code_fts) VALUES('delete-all')"
        )
        self._conn.execute(
            "INSERT INTO code_fts(rowid, name, id, file_path) "
            "SELECT rowid, name, id, file_path FROM code_nodes"
        )
        self._conn.commit()

    # ── CTE traversal ────────────────────────────────────────────────────

    def find_callers(self, node_id: str, depth: int = 1) -> list[tuple[str, int]]:
        """Find direct and transitive callers of *node_id* up to *depth* hops.

        Returns:
            List of ``(caller_node_id, hop_distance)`` tuples.
        """
        sql = """
            WITH RECURSIVE callers(nid, d) AS (
                SELECT ?, 0
                UNION
                SELECT e.source_id, c.d + 1
                FROM callers c
                JOIN code_edges e ON e.target_id = c.nid
                WHERE c.d < ?
            )
            SELECT nid, d FROM callers WHERE d > 0 ORDER BY d
        """
        rows = self._conn.execute(sql, (node_id, depth)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def direct_callers(self, node_ids) -> list[tuple[str, str, str, float]]:
        """``(source_id, target_id, edge_type, confidence)`` of every edge into
        *node_ids* (calls, references, extends …), one hop."""
        ids = list(dict.fromkeys(node_ids))
        out: list[tuple[str, str, str, float]] = []
        for start in range(0, len(ids), _BATCH_SIZE):
            batch = ids[start : start + _BATCH_SIZE]
            ph = ",".join("?" * len(batch))
            out.extend((r[0], r[1], r[2], r[3] if r[3] is not None else 1.0)
                       for r in self._conn.execute(
                           "SELECT source_id, target_id, edge_type, confidence FROM code_edges "
                           f"WHERE target_id IN ({ph})", batch).fetchall())
        return out

    # ── dead-code detection ──────────────────────────────────────────────

    def find_dead_code(self) -> list[dict]:
        """Exported, non-entry-point nodes with zero incoming edges.

        Excludes test files (path containing ``test``) and ``__init__.py``
        re-exports so only genuinely unreferenced symbols are flagged.
        """
        sql = """
            SELECT n.id, n.file_path, n.node_type, n.name
            FROM code_nodes n
            WHERE n.is_export = 1
              AND n.is_entry_point = 0
              AND n.file_path NOT LIKE '%test%'
              AND n.file_path NOT LIKE '%__init__.py'
              AND NOT EXISTS (
                  SELECT 1 FROM code_edges e WHERE e.target_id = n.id
              )
            ORDER BY n.file_path, n.name
        """
        return [
            {"id": r[0], "file_path": r[1], "node_type": r[2], "name": r[3]}
            for r in self._conn.execute(sql).fetchall()
        ]

    # ── module map ───────────────────────────────────────────────────────

    def get_module_map(self) -> dict[str, list[dict]]:
        """Group all nodes by their 2-level directory prefix.

        E.g. ``backend/core/foo.py`` -> key ``backend/core``.
        """
        rows = self._conn.execute(
            "SELECT id, file_path, node_type, name, is_entry_point "
            "FROM code_nodes ORDER BY file_path"
        ).fetchall()
        modules: dict[str, list[dict]] = {}
        for row in rows:
            parts = row[1].split("/")
            prefix = "/".join(parts[:2]) if len(parts) > 2 else parts[0] if parts else ""
            modules.setdefault(prefix, []).append(
                {"id": row[0], "file_path": row[1], "node_type": row[2],
                 "name": row[3], "is_entry_point": row[4]}
            )
        return modules

    # God-node guard threshold (ported from graphify, run_2392a203).
    # code_edges.confidence semantics (parser.py): 1.0 = target is qualified
    # (has "::", an EXACT reference), 0.95 = matched by its module path, 0.9 = the
    # source states the receiver's class, 0.8 = single-candidate cross-file resolved
    # by name, 0.6 = regex-fallback inferred call, 0.5 = bare/unresolved/N-candidate
    # ambiguous, 0.4 = a call on a value of unknown type resolved by name only. A 0.5 edge is DEFINITIONALLY bare (parser.py:1044 sets 0.5 iff
    # the target has no "::"), so `_mod_of` turns its bare target (`str`, `mkdir`,
    # `get`) into a FABRICATED single-token module — 87% of the pre-guard module
    # skeleton was such garbage. Excluding <=0.5 edges from the AGGREGATION (not
    # from code_edges — they are legit per-symbol data) is graphify's god-node
    # guard: len(candidates) != 1 -> bail (extract.py:2107).
    _MODULE_EDGE_MIN_CONFIDENCE = 0.5

    def get_module_edges(self) -> list[dict]:
        """Aggregate code_edges up to 2-level module-prefix pairs — the compact
        ARCHITECTURAL SKELETON (which module calls which), NOT a raw per-symbol
        edge dump (~25K edges would bury the architecture in a readable report). Cross-module only (intra-module edges excluded —
        they're implementation detail, not architecture).

        Confidence-aware (run_2392a203, ported from graphify): each code_edge
        carries a per-symbol ``confidence`` float; this rolls up to a
        per-module-pair label + score and applies a god-node guard.

        - **God-node guard:** edges with confidence <= ``_MODULE_EDGE_MIN_CONFIDENCE``
          (0.5 bare/unresolved targets like ``str``/``mkdir``/``get``) are EXCLUDED,
          so a bare builtin never becomes a fake module endpoint.
        - **confidence_score:** the MAX float confidence among the pair's kept edges
          ("is there ANY solid evidence this module link is real"). MAX not MIN:
          one exact edge shouldn't be masked by a weaker sibling.
        - **confidence label:** ``EXTRACTED`` if score >= 1.0 (an exact/qualified
          reference), else ``INFERRED`` (0.6–0.99, resolved-but-deduced).
          ``AMBIGUOUS`` (<=0.5) never appears — it is guarded out.

        Returns list of {"from", "to", "count", "confidence", "confidence_score"}
        sorted by count desc. The module prefix mirrors get_module_map: 2-level
        dir prefix of the node's file, derived from the ``file::symbol`` id.
        """
        def _mod_of(node_id: str) -> str:
            # node id is "<file_path>::<symbol>"; module = 2-level dir prefix of file
            fpath = node_id.split("::", 1)[0]
            parts = fpath.split("/")
            if len(parts) > 2:
                return "/".join(parts[:2])
            return parts[0] if parts else ""

        rows = self._conn.execute(
            "SELECT source_id, target_id, confidence FROM code_edges"
        ).fetchall()
        # per module pair: [count of kept edges, max confidence]
        agg: dict[tuple[str, str], list] = {}
        for src, tgt, conf in rows:
            score = conf if conf is not None else 1.0
            # god-node guard: drop bare/unresolved edges (fake-module endpoints)
            if score <= self._MODULE_EDGE_MIN_CONFIDENCE:
                continue
            sm, tm = _mod_of(src), _mod_of(tgt)
            if not sm or not tm or sm == tm:
                continue  # skip intra-module + unresolvable
            entry = agg.get((sm, tm))
            if entry is None:
                agg[(sm, tm)] = [1, score]
            else:
                entry[0] += 1
                if score > entry[1]:
                    entry[1] = score
        return [
            {
                "from": sm, "to": tm, "count": n,
                "confidence": "EXTRACTED" if score >= 1.0 else "INFERRED",
                "confidence_score": round(score, 2),
            }
            for (sm, tm), (n, score) in sorted(
                agg.items(), key=lambda kv: kv[1][0], reverse=True
            )
        ]

    # ── codebase summary ─────────────────────────────────────────────────

    def counts(self) -> dict:
        """The cheap facts a status line needs: symbols, edges, files, languages and
        when it was indexed. No dead-code scan, no module map (``get_codebase_summary``
        does those; the daemon's status reads must not)."""
        langs = dict(self._conn.execute(
            "SELECT language, COUNT(*) FROM code_nodes GROUP BY language").fetchall())
        nodes = self._conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()[0]
        edges = self._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]
        files = self._conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM code_nodes").fetchone()[0]
        when = self.indexed_at()
        return {"nodes": nodes, "edges": edges, "files": files,
                "languages": {k or "unknown": v for k, v in langs.items()},
                "last_indexed": (datetime.fromtimestamp(when, timezone.utc)
                                 .isoformat(timespec="seconds") if when else None),
                "indexed_commit": self.get_meta("last_indexed_commit")}

    def get_codebase_summary(self) -> dict:
        """Compact codebase overview (~100 tokens).

        Returns language breakdown, module stats, top-connected nodes,
        entry point count, dead code count, and last indexed timestamp.
        """
        lang_rows = self._conn.execute(
            "SELECT language, COUNT(*) FROM code_nodes GROUP BY language"
        ).fetchall()
        node_count = self._conn.execute(
            "SELECT COUNT(*) FROM code_nodes"
        ).fetchone()[0]
        edge_count = self._conn.execute(
            "SELECT COUNT(*) FROM code_edges"
        ).fetchone()[0]
        file_count = self._conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM code_nodes"
        ).fetchone()[0]
        entry_count = self._conn.execute(
            "SELECT COUNT(*) FROM code_nodes WHERE is_entry_point = 1"
        ).fetchone()[0]
        dead_count = len(self.find_dead_code())

        # Module-level function/class counts
        raw_modules = self.get_module_map()
        module_stats = {}
        for mod_name, mod_nodes in raw_modules.items():
            fn = sum(1 for n in mod_nodes if n.get("node_type") in ("function", "method"))
            cls = sum(1 for n in mod_nodes if n.get("node_type") == "class")
            module_stats[mod_name] = {"function_count": fn, "class_count": cls,
                                       "file_count": len({n["file_path"] for n in mod_nodes})}

        # Top connected nodes (most callers)
        top_rows = self._conn.execute(
            "SELECT n.name, n.file_path, COUNT(e.source_id) AS callers "
            "FROM code_nodes n "
            "JOIN code_edges e ON e.target_id = n.id "
            "GROUP BY n.id ORDER BY callers DESC LIMIT 5"
        ).fetchall()
        top_connected = [{"name": r[0], "file_path": r[1], "callers": r[2]} for r in top_rows]

        # Last indexed (ISO 8601, UTC) — the newer of the two markers, either format.
        ts = self.indexed_at()
        last_indexed = (datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
                        if ts is not None else None)

        # Routes (for session-start briefing)
        try:
            routes = self.get_routes()
        except Exception:
            routes = []  # Table may not exist in older DBs

        return {
            "languages": {r[0]: r[1] for r in lang_rows},
            "total_nodes": node_count,
            "total_edges": edge_count,
            "total_files": file_count,
            "module_count": len(raw_modules),
            "modules": module_stats,
            "top_connected": top_connected,
            "entry_point_count": entry_count,
            "dead_code_count": dead_count,
            "routes": routes,
            "last_indexed": last_indexed,
        }

    # ── graph visualization ────────────────────────────────────────────────

    def get_graph_data(self, limit: int = 300) -> dict:
        """Return top-N most-connected nodes + their edges for visualization.

        Nodes are ranked by edge count (PageRank proxy). Only edges between
        included nodes are returned (no dangling references). Self-loops excluded.

        Thread-safe: uses a dedicated connection (not shared self._conn).

        Returns:
            {"nodes": [{id, name, type, module, file_path}],
             "edges": [{source, target, type}]}
        """
        # Thread safety: create a fresh read-only connection for this query.
        # GraphStore._conn is shared across threads via the module cache —
        # concurrent .execute() on a shared connection = undefined behavior.
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        try:
            # CTE approach: pre-compute degree in one pass (faster than correlated subquery)
            # Handles limit <= 0 safely (SQLite LIMIT -1 = unlimited, which we don't want)
            safe_limit = max(limit, 1)
            node_rows = conn.execute(
                "WITH degree AS ("
                "  SELECT id, "
                "    (SELECT COUNT(*) FROM code_edges WHERE source_id = id) + "
                "    (SELECT COUNT(*) FROM code_edges WHERE target_id = id) AS deg "
                "  FROM code_nodes"
                ") "
                "SELECT n.id, n.name, n.node_type, n.file_path, d.deg "
                "FROM code_nodes n JOIN degree d ON d.id = n.id "
                "ORDER BY d.deg DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()

            node_ids = set()
            nodes = []
            for r in node_rows:
                node_id, name, node_type, file_path = r[0], r[1], r[2], r[3]
                node_ids.add(node_id)
                # Derive module from file_path (2-level prefix)
                parts = file_path.split("/") if file_path else []
                module = "/".join(parts[:2]) if len(parts) > 2 else parts[0] if parts else ""
                nodes.append({
                    "id": node_id,
                    "name": name,
                    "type": node_type,
                    "module": module,
                    "file_path": file_path,
                })

            if not node_ids:
                return {"nodes": [], "edges": []}

            # Fix cross-batch edge loss: query ALL edges where source is in node_ids,
            # then filter target membership in Python. Single query, no batching needed
            # for the source side (SQLite handles IN(...) up to SQLITE_MAX_VARIABLE_NUMBER=999).
            # For limit > 999, batch the source query but filter target in Python.
            all_edges: list[tuple] = []
            id_list = list(node_ids)
            for start in range(0, len(id_list), 900):
                batch = id_list[start:start + 900]
                placeholders = ",".join("?" * len(batch))
                # DISTINCT eliminates duplicate edges from different line_numbers
                edge_rows = conn.execute(
                    f"SELECT DISTINCT source_id, target_id, edge_type FROM code_edges "
                    f"WHERE source_id IN ({placeholders})",
                    batch,
                ).fetchall()
                all_edges.extend(edge_rows)

            # Filter: target must be in node_ids AND exclude self-loops
            edges = [
                {"source": r[0], "target": r[1], "type": r[2]}
                for r in all_edges
                if r[1] in node_ids and r[0] != r[1]
            ]

            return {"nodes": nodes, "edges": edges}

        finally:
            conn.close()

    # ── file queries ─────────────────────────────────────────────────────

    def get_nodes_by_file(self, file_path: str) -> list[dict]:
        """Return all nodes belonging to *file_path*."""
        rows = self._conn.execute(
            "SELECT id, file_path, node_type, name, line_start, line_end, "
            "language, is_export, is_entry_point, file_hash "
            "FROM code_nodes WHERE file_path = ? ORDER BY line_start",
            (file_path,),
        ).fetchall()
        return [
            {
                "id": r[0], "file_path": r[1], "node_type": r[2], "name": r[3],
                "line_start": r[4], "line_end": r[5], "language": r[6],
                "is_export": bool(r[7]), "is_entry_point": bool(r[8]),
                "file_hash": r[9],
            }
            for r in rows
        ]

    def get_full_graph(self) -> dict:
        """Return the ENTIRE graph — all nodes + all edges, NO limit.

        Unlike ``get_graph_data`` (top-N by degree, for display) this returns every
        row, so structural passes (clustering, whole-graph analysis) see the full
        graph. Confines the ``code_nodes``/``code_edges`` schema coupling to this one
        accessor instead of spreading raw ``_conn`` reads across modules
        (run_93e78bcd). Edges are returned VERBATIM including low-confidence ones
        (e.g. ``dynamic_sql_write:*`` at 0.4) — a consumer that wants a confidence
        floor filters itself; clustering deliberately keeps them (structural signal).
        """
        nodes = [
            {"id": r[0], "file_path": r[1], "node_type": r[2], "name": r[3],
             "language": r[4], "is_export": bool(r[5]), "is_entry_point": bool(r[6])}
            for r in self._conn.execute(
                "SELECT id, file_path, node_type, name, language, is_export, "
                "is_entry_point FROM code_nodes"
            ).fetchall()
        ]
        edges = [
            {"source_id": r[0], "target_id": r[1], "edge_type": r[2],
             "confidence": r[3], "line_number": r[4]}
            for r in self._conn.execute(
                "SELECT source_id, target_id, edge_type, confidence, line_number "
                "FROM code_edges"
            ).fetchall()
        ]
        return {"nodes": nodes, "edges": edges}

    def count_files(self) -> int:
        """Number of distinct files currently in the graph.

        The denominator for grading.classify_changeset's FULL_PCT share test
        (run_4602932d). Returns 0 for an empty graph (share test then disabled).
        """
        return self._conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM code_nodes"
        ).fetchone()[0]

    def count_edges(self) -> int:
        """Total edge count — a cheap pre-check so a consumer can bail BEFORE
        materializing the whole graph (get_full_graph) on a pathological repo."""
        return self._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]

    def search_symbols(self, query: str, limit: int = 20) -> list[dict]:
        """FTS5 search over symbol names.

        The query is double-quoted to prevent injection through FTS operators.
        """
        safe = _sanitize_name(query).strip()
        if not safe:
            return []
        # Quote individual tokens for FTS5 safety, then OR-join so a multi-word
        # query matches symbols containing ANY term (ranked by BM25), not only
        # symbols containing ALL terms. Space-join was an implicit AND →
        # multi-word symbol queries returned near-0 (R3, run_c730a9c0). Per-term
        # quoting keeps FTS5 keywords (OR/NEAR) as phrase-literals. Single token
        # → OR-of-one → identical to the old behavior.
        tokens = safe.split()
        fts_query = " OR ".join(f'"{t}"' for t in tokens if t)
        if not fts_query:
            return []
        # Fetch MORE than `limit` so the test-symbol demotion below can pull a
        # prod symbol up from beyond the raw-BM25 cutoff (run_fc313f42). Bounded
        # so a hot-path query can't scan unboundedly.
        fetch_n = min(max(limit * 4, limit + 20), 200)
        try:
            rows = self._conn.execute(
                "SELECT name, id, file_path, rank "
                "FROM code_fts WHERE code_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (fts_query, fetch_n),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS table out of sync — fall back to keyword search.
            logger.warning("FTS5 query failed, falling back to keyword search")
            return self.keyword_search(query, limit)
        # Test-symbol demotion (run_fc313f42): FTS `rank` is negative (more-negative
        # = better). A verbose TEST symbol name can pack more query terms than the
        # real prod symbol and out-rank it (test symbols are the majority of matches).
        # Add a small tie-breaker penalty to test-file rows so they sort BELOW
        # COMPARABLE prod symbols — WITHOUT excluding them (a test-only query still
        # returns tests). SKIP the penalty when the query itself wants tests (Gate-2
        # HIGH: "how is X tested" must return tests, not bury them).
        penalty = 0.0 if _query_wants_tests(query) else _TEST_SYMBOL_RANK_PENALTY
        adjusted = [
            (r[0], r[1], r[2], r[3] + (penalty
                                       if _is_test_symbol_path(r[2]) else 0.0))
            for r in rows
        ]
        adjusted.sort(key=lambda t: t[3])  # ascending: most-negative (best) first
        return [
            {"name": a[0], "id": a[1], "file_path": a[2], "rank": a[3]}
            for a in adjusted[:limit]
        ]

    def keyword_search(self, query: str, limit: int = 20) -> list[dict]:
        """LIKE-based fallback search on node names."""
        safe = _sanitize_name(query).strip()
        if not safe:
            return []
        pattern = f"%{safe}%"
        rows = self._conn.execute(
            "SELECT id, file_path, node_type, name "
            "FROM code_nodes WHERE name LIKE ? "
            "ORDER BY name LIMIT ?",
            (pattern, limit),
        ).fetchall()
        return [
            {"id": r[0], "file_path": r[1], "node_type": r[2], "name": r[3]}
            for r in rows
        ]

    # ── bulk operations ──────────────────────────────────────────────────

    @_write_locked
    def clear(self) -> None:
        """Delete all nodes, edges, routes, imports, FTS data, and metadata. (Routes
        used to survive, so a rebuild kept the routes of deleted handlers.)"""
        self._conn.execute("DELETE FROM code_routes")
        self._conn.execute("DELETE FROM code_edges")
        self._conn.execute("DELETE FROM code_nodes")
        self._conn.execute("DELETE FROM code_imports")
        self._conn.execute(
            "INSERT INTO code_fts(code_fts) VALUES('delete-all')"
        )
        self._conn.execute("DELETE FROM graph_meta")
        self._conn.commit()

    @_write_locked
    def resolve_and_prune(self, files: list[str] | None = None) -> int:
        """Resolve cross-file call targets, then drop edges that still point at nothing.

        Shared by the full rebuild (``bulk_insert``) and the incremental re-store
        (``incremental_update``, through ``build.update_files``), so a cross-file
        call stored incrementally resolves exactly as it would in a full rebuild:
        before this, only ``bulk_insert`` ran it and incrementally stored
        ``module::name`` targets dangled until the next full rebuild.

        Orphans are calls to builtins/stdlib (or ambiguous names) that are not in the
        graph; without the cleanup, the callers walk traverses phantoms. *files*
        limits the cleanup to edges whose source is in those files (the incremental
        callers pass the files they re-stored); None means the whole graph, which only
        a full rebuild of this graph may use. Returns the number of resolved edges.
        Never raises. A failed resolution skips the cleanup (unresolved is not
        orphaned). Inside ``transaction()`` a failed cleanup rolls back, which fails
        the whole transaction: the rebuild raises and the old graph stays.
        """
        resolved = 0
        try:
            from .parser import resolve_bare_targets
            resolved = resolve_bare_targets(self, files=files)
            if resolved:
                logger.info("Cross-file resolution: %d bare targets resolved", resolved)
        except Exception as e:
            # Not resolved is not orphaned: the prune below would delete every
            # cross-file edge still waiting for this resolution (it once cut a real
            # graph's edges in half without a trace). Keep them for the next run.
            logger.warning("Cross-file resolution failed, orphan cleanup skipped: %s: %s",
                           type(e).__name__, e)
            if not getattr(self._conn, "_defer_commit", 0):
                self._rollback_open_txn()     # inside transaction() the caller commits
            return 0
        try:
            # Scoped to the re-stored files' outbound edges when *files* is given:
            # an incremental update must not judge edges of files it did not touch.
            base = "DELETE FROM code_edges WHERE target_id NOT IN (SELECT id FROM code_nodes)"
            if files is None:
                orphan_count = self._conn.execute(base).rowcount
            else:
                orphan_count = 0
                for start in range(0, len(files), _BATCH_SIZE):
                    batch = list(files[start : start + _BATCH_SIZE])
                    ph = ",".join("?" * len(batch))
                    orphan_count += self._conn.execute(
                        base + " AND source_id IN (SELECT id FROM code_nodes "
                        f"WHERE file_path IN ({ph}))", batch).rowcount
            self._conn.commit()
            if orphan_count > 0:
                logger.info("Removed %d orphan edges (target not in nodes)", orphan_count)
        except Exception as e:
            logger.debug("Orphan edge cleanup skipped: %s", e)
            self._rollback_open_txn()
        return resolved

    def _rollback_open_txn(self) -> None:
        """Roll back a transaction a swallowed write error left open. Never raises."""
        try:
            if self._conn.in_transaction:
                self._conn.rollback()
        except sqlite3.Error as exc:
            logger.warning("graph write rollback failed: %s", exc)

    @_write_locked
    def bulk_insert(self, parse_results: list, repo_root: str | Path | None = None) -> None:
        """Insert all results from a full repo parse.

        Each element of *parse_results* should be a dict (or ParseResult) with
        keys ``file_path``, ``nodes``, ``edges``, and ``file_hash``.
        FTS is rebuilt at the end.

        ``repo_root``: when provided, persisted into ``graph_meta`` as the absolute
        ``repo_root`` key. Freshness, ownership checks and the incremental update all
        read it, so every full rebuild passes it (``build.rebuild``): stamping it here,
        the one place every full write funnels through, keeps it from drifting.
        Optional for test callers that set_meta by hand.
        """
        for pr in parse_results:
            d = pr if isinstance(pr, dict) else pr.__dict__
            self.upsert_nodes(d["nodes"])
            self.upsert_edges(d["edges"])
            # Imports of a file with symbols only — the incremental path keeps the
            # same rule (a file with no symbols has no call to disambiguate).
            if d.get("imports") and d.get("file_path") and d.get("nodes"):
                self._replace_imports(d["file_path"], d["imports"])
        self._conn.commit()
        self.rebuild_fts()
        self.resolve_and_prune()

        # Full rebuild wrote the whole graph into the WAL — reclaim it so the -wal
        # file doesn't stay multi-GB on disk (the 2.73GB-bloat root cause). Inside
        # transaction() this is a no-op; build.rebuild checkpoints after the commit.
        self.checkpoint_truncate()

        # set_meta is INSERT OR REPLACE: re-stamping on every rebuild is idempotent
        # and backfills a db that predates the key on its next full rebuild.
        if repo_root:
            self.set_meta("repo_root", str(Path(repo_root).resolve()))

    def incremental_update(
        self,
        repo_root: str | Path,
        changed_files: list[str],
    ) -> dict:
        """Re-store *changed_files* (repo-relative) so the graph matches the disk.

        Per file: a deleted file, or one that is now a symlink to a file outside the
        repo, loses its nodes, edges and routes; an unchanged file
        (same content hash as stored) or a path that is not a regular file (a submodule
        bump) is skipped; otherwise it is parsed and its nodes
        and outbound edges replaced via ``store_file_nodes_edges`` (inbound edges whose
        target survives are kept there). A file that parses to no symbols but had some
        is purged. A file whose read, parse or store fails goes to ``failed`` (its
        store is rolled back) and the rest of the batch goes on. Then the re-stored
        files' cross-file targets are resolved and their orphan edges dropped
        (``resolve_and_prune(files=...)``) and FTS is rebuilt; the update time is
        recorded only when nothing failed.

        Before this, the method only hash-checked and recorded a timestamp — it never
        parsed — so the file watcher made the graph look fresh while its symbols and
        edges stayed as they were at the last full rebuild.

        Returns a dict with ``updated``, ``skipped``, ``failed``, ``dependents``
        (files with callers into the updated files, for callers that want them).
        """
        from .parser import parse_file, resolves_inside

        repo_root = Path(repo_root)
        updated: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        for fpath in changed_files:
            full = repo_root / fpath
            # Deleted, or a symlink to a file outside the repo (its target is not the
            # repo's content and is never read): either way it leaves the graph.
            if not full.exists() or not resolves_inside(repo_root, full):
                try:
                    self.remove_file(fpath)
                    self.delete_routes_for_file(fpath)
                    updated.append(fpath)
                except sqlite3.Error as exc:
                    logger.warning("incremental_update: removing %s failed: %s", fpath, exc)
                    failed.append(fpath)
                continue

            if not full.is_file():
                # A submodule bump or other non-regular path: nothing to parse. Not a
                # failure, or the caller's commit marker would never move past it.
                skipped.append(fpath)
                continue
            try:
                content_hash = hashlib.sha256(full.read_bytes()).hexdigest()
            except OSError:
                failed.append(fpath)
                continue

            existing = self._conn.execute(
                "SELECT file_hash FROM code_nodes WHERE file_path = ? LIMIT 1",
                (fpath,),
            ).fetchone()
            if existing and existing[0] == content_hash:
                skipped.append(fpath)
                continue

            try:
                result = parse_file(full, repo_root)
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop the batch
                logger.debug("incremental_update: parse failed for %s: %s", fpath, exc)
                failed.append(fpath)
                continue
            try:
                if result.nodes:
                    self.store_file_nodes_edges(fpath, result.nodes, result.edges, content_hash,
                                                imports=result.imports)
                    updated.append(fpath)
                elif existing:
                    # Edited down to no symbols: its old nodes are stale.
                    self.remove_file(fpath)
                    updated.append(fpath)
                else:
                    skipped.append(fpath)
            except sqlite3.Error as exc:
                # e.g. "database is locked" while another process rebuilds: this file
                # stays as it was (the store is one transaction); the rest go on.
                logger.warning("incremental_update: storing %s failed: %s", fpath, exc)
                failed.append(fpath)

        dependent_files: set[str] = set()
        for fpath in updated:
            dependent_files.update(self.find_dependents(fpath))
        dependent_files -= set(updated)

        index_ok = True
        if updated:
            self.resolve_and_prune(files=updated)  # never raises
            try:
                self.rebuild_fts()
            except sqlite3.Error as exc:
                logger.warning("incremental_update: FTS rebuild failed: %s", exc)
                index_ok = False
        if not failed and index_ok:
            # Only a batch that fully landed marks the graph fresh: a failed file is
            # still stale, and freshness readers trust this stamp.
            self.mark_indexed(full=False)

        # Reclaim WAL disk space after the batch of writes (non-fatal if a reader
        # holds the WAL — retried on the next update).
        self.checkpoint_truncate()

        return {
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "dependents": sorted(dependent_files),
        }

    def find_dependents(self, file_path: str, max_hops: int = 2) -> list[str]:
        """Find files that import or call symbols defined in *file_path*.

        BFS up to *max_hops* levels, capped at 500 results to stay bounded.
        """
        # Seed: all node IDs in the given file.
        seed_ids = [
            r[0]
            for r in self._conn.execute(
                "SELECT id FROM code_nodes WHERE file_path = ?", (file_path,)
            ).fetchall()
        ]
        if not seed_ids:
            return []

        visited_ids: set[str] = set(seed_ids)
        frontier = list(seed_ids)
        dependent_files: set[str] = set()

        for _hop in range(max_hops):
            if not frontier or len(dependent_files) >= 500:
                break
            next_frontier: list[str] = []
            # Callers: edges where target is one of our frontier nodes.
            tmpl = (
                "SELECT DISTINCT e.source_id FROM code_edges e "
                "WHERE e.target_id IN ({placeholders})"
            )
            caller_rows = self._batch_query(self._conn, tmpl, frontier)
            for (caller_id,) in caller_rows:
                if caller_id not in visited_ids:
                    visited_ids.add(caller_id)
                    next_frontier.append(caller_id)
            frontier = next_frontier

        # Resolve node IDs to file paths.
        all_ids = list(visited_ids - set(seed_ids))
        if all_ids:
            tmpl = (
                "SELECT DISTINCT file_path FROM code_nodes "
                "WHERE id IN ({placeholders})"
            )
            file_rows = self._batch_query(self._conn, tmpl, all_ids)
            dependent_files = {r[0] for r in file_rows}

        dependent_files.discard(file_path)
        return sorted(dependent_files)[:500]

    # ── bare target resolution (Layer 2) ──────────────────────────────────

    def resolve_bare_targets(self, qualified_separator: str,
                             files: list[str] | None = None) -> int:
        """Resolve unresolved calls/extends/references targets to real node ids.

        A target qualifies as unresolved if it does NOT match any code_node id —
        either bare (no separator, e.g. `helper`) OR qualified-but-dangling (has a
        separator but points at nothing, e.g. `base::Animal` when the real node is
        `base.py::Animal` — the import-map/node-id mismatch). Both resolve by the
        target's bare NAME via a global name-to-qualified-id lookup:
          - 1 candidate: resolve directly (confidence 0.8)
          - N candidates: prefer the one whose file is imported by the caller
          - 0 or ambiguous: leave unresolved (orphan-cleanup drops it)
        Before that, what the source states wins (``parser`` confidence bands):
          - a typed target ``Cls.m`` resolves by its class and up its bases
            (``TYPED_CALL_CONFIDENCE``); if the class is unknown, by the name ``m``,
            marked ``NAME_ONLY_CONFIDENCE``;
          - a dotted-path reference resolves only to a file at that path, never by
            the bare name (``PATH_REFERENCE_CONFIDENCE``);
          - a dangling module-qualified name resolves to the one file whose path
            ends with that module.
        A call on a value of unknown type resolved by its name alone keeps
        ``NAME_ONLY_CONFIDENCE`` (not 0.8), so a reader can tell it from a stated link.

        Imports are resolved first (``resolve_imports``; the import table is kept apart
        from the edges). Covers edge_type IN (calls, extends, references) — extends/references were
        added by the dead-code false-positive fixes (run_8e023234). *files* limits
        the edges resolved to those whose source is in those files (incremental
        callers); None resolves the whole graph (a full rebuild). Returns count
        of resolved edges.
        """
        # Imports first, whether or not any target is unresolved: the table is read
        # by this resolution and by imported_files().
        self.resolve_imports(files)
        all_nodes = self._conn.execute(
            "SELECT id, name, node_type, language FROM code_nodes"
        ).fetchall()
        name_to_ids: dict[str, list[str]] = {}
        # A value's method call may land on: a method, or any definition of a
        # language whose parser does not tell methods apart (c/c++ members defined
        # outside their class, php, swift, …).
        member_ok: set[str] = set()
        for node_id, name, node_type, language in all_nodes:
            name_to_ids.setdefault(name, []).append(node_id)
            if node_type == "method" or language not in _METHOD_TYPED_LANGS:
                member_ok.add(node_id)

        # Resolve targets for calls AND the two edge types added by the dead-code
        # FP fixes (run_8e023234): `extends` (Sub->Base) and `references`. TWO
        # kinds of unresolved target qualify:
        #   1. BARE — no separator (`Animal`, `helper`): the classic Layer-1 leftover.
        #   2. QUALIFIED-BUT-DANGLING — has a separator but matches NO node
        #      (`base::Animal` from `from base import Animal`, where the real node
        #      is `base.py::Animal`). This import-map/node-id mismatch was a
        #      PRE-EXISTING bug that made cross-file `calls` targets dangle too; it
        #      surfaced via `extends` (a base class stayed "dead" cross-file). Both
        #      resolve by the target's bare NAME (last path segment) — unique-only.
        # An ambiguous name (N>1 defs) is LEFT unresolved → orphan-cleanup drops it
        # (the structural FP guard). Resolving a dangling target is strictly safe:
        # it currently points at nothing and would be orphan-deleted regardless.
        # Push the "unresolved" anti-join into SQL so we don't materialize the full
        # (already-resolved) edge set in Python every re-index — SQLite evaluates
        # `target_id NOT IN (SELECT id FROM code_nodes)` and returns ONLY the
        # unresolved rows (bare `Animal` + dangling `base::Animal` alike). This is
        # exactly the old `NOT LIKE '%::%'` intent, but correct for the dangling
        # case too (a dangling target HAS a separator, so NOT LIKE would wrongly
        # skip it). Perf: O(unresolved) not O(all edges) (Gate-2 perf finding).
        # NOT EXISTS (not NOT IN): NULL-safe (a NULL code_nodes.id would make
        # `NOT IN (…, NULL)` never-true and silently disable ALL resolution — Gate-2
        # red-team LOW) and a friendly anti-join plan. Returns ONLY unresolved rows.
        bare_sql = (
            "SELECT rowid, source_id, target_id, confidence, edge_type FROM code_edges e "
            "WHERE e.edge_type IN ('calls', 'extends', 'references') "
            "AND NOT EXISTS (SELECT 1 FROM code_nodes n WHERE n.id = e.target_id)"
        )
        if files is None:
            bare_edges = self._conn.execute(bare_sql).fetchall()
        else:
            # Only the re-stored files' edges: another connection may be part-way
            # through a rebuild, and a name whose second definition has not landed
            # yet would look unique there and resolve to the wrong node for good.
            bare_edges = []
            for start in range(0, len(files), _BATCH_SIZE):
                batch = list(files[start : start + _BATCH_SIZE])
                ph = ",".join("?" * len(batch))
                bare_edges.extend(self._conn.execute(
                    bare_sql + " AND e.source_id IN (SELECT id FROM code_nodes "
                    f"WHERE file_path IN ({ph}))", batch).fetchall())

        if not bare_edges:
            return 0

        # What each caller file imports (code_imports, resolved at the top): picks
        # between same-named definitions. The parser never produced 'imports' edges,
        # so this branch used to have nothing to read.
        file_imports: dict[str, set[str]] = {}
        for src_file, dst in self._conn.execute(
                "SELECT src_file, dst_file FROM code_imports WHERE dst_file IS NOT NULL"):
            file_imports.setdefault(src_file, set()).add(dst)

        def _imported(cand_file: str, imported: set[str]) -> bool:
            if cand_file in imported:
                return True
            # a Go import names a package directory ("dir/"): its files, not deeper
            return any(d.endswith("/") and cand_file.startswith(d) and "/" not in cand_file[len(d):]
                       for d in imported)

        from .parser import (BUILTIN_NAME_CONFIDENCE, MEMBER_CALL_CONFIDENCE,
                             NAME_ONLY_CONFIDENCE, PATH_REFERENCE_CONFIDENCE,
                             TYPED_CALL_CONFIDENCE)
        sep = qualified_separator
        # "Cls.m" → ids ending in "::Cls.m" (a class member named with its class)
        by_member: dict[str, list[str]] = {}
        for node_id, _name, _t, _l in all_nodes:
            if sep in node_id:
                by_member.setdefault(node_id.split(sep, 1)[1], []).append(node_id)
        # a class id → the ids of its bases (resolved extends edges)
        bases: dict[str, list[str]] = {}
        node_ids = {r[0] for r in all_nodes}
        for src, dst in self._conn.execute(
                "SELECT source_id, target_id FROM code_edges WHERE edge_type = 'extends'"):
            if dst in node_ids:
                bases.setdefault(src, []).append(dst)

        def _in_module(cand: str, module: str) -> bool:
            """Whether candidate *cand*'s file is *module*: a repo-relative path with or
            without its suffix, a dotted path turned into ``a/b/c``, or an import's
            ``./x`` / ``../lib/x`` (compared by what follows the dots). A package's
            ``__init__`` / ``index`` file is the package."""
            if not module:
                return True
            mod = module.replace("\\", "/")
            while mod.startswith(("./", "../")):
                mod = mod.split("/", 1)[1]
            mod = mod.strip("/")
            last = mod.rsplit("/", 1)[-1]
            if "." in last:
                mod = mod.rsplit(".", 1)[0]
            stem = cand.split(sep, 1)[0].rsplit(".", 1)[0]
            for s_ in (stem, stem.removesuffix("/__init__"), stem.removesuffix("/index")):
                if s_ == mod or s_.endswith("/" + mod):
                    return True
            return False

        def _class_member(module: str, cls: str, member: str) -> str | None:
            """``cls.member`` in *module* (or anywhere when unique), up its bases."""
            classes = [c for c in by_member.get(cls, []) if _in_module(c, module)]
            if not classes and module:
                classes = by_member.get(cls, [])
            if len(classes) != 1:
                exact = [c for c in by_member.get(f"{cls}.{member}", []) if _in_module(c, module)]
                return exact[0] if len(exact) == 1 else None
            frontier, seen = [classes[0]], set()
            for _ in range(8):
                nxt = []
                for c in frontier:
                    if c in seen:
                        continue
                    seen.add(c)
                    cand = f"{c}.{member}"
                    if cand in node_ids:
                        return cand
                    nxt.extend(bases.get(c, []))
                if not nxt:
                    return None
                frontier = nxt
            return None

        def _set(rowid: int, target: str, conf: float) -> None:
            try:
                self._conn.execute(
                    "UPDATE code_edges SET target_id = ?, confidence = ? WHERE rowid = ?",
                    (target, conf, rowid))
            except sqlite3.IntegrityError:
                # The resolved edge exists already (two spellings of one call on one
                # line, e.g. a typed `mod::Store.save` and a bare `save`): keep one,
                # with the higher confidence.
                row = self._conn.execute(
                    "SELECT source_id, edge_type, line_number FROM code_edges WHERE rowid = ?",
                    (rowid,)).fetchone()
                if row is not None:
                    self._conn.execute(
                        "UPDATE code_edges SET confidence = MAX(confidence, ?) "
                        "WHERE source_id = ? AND target_id = ? AND edge_type = ? "
                        "AND IFNULL(line_number, -1) = IFNULL(?, -1)",
                        (conf, row[0], target, row[1], row[2]))
                self._conn.execute("DELETE FROM code_edges WHERE rowid = ?", (rowid,))

        resolved_count = 0
        for rowid, source_id, target_id, confidence, edge_type in bare_edges:
            module, _, symbol = target_id.rpartition(sep)
            # 1. A target the source typed (`Store.save`): by its class, up the bases.
            if confidence is not None and abs(confidence - TYPED_CALL_CONFIDENCE) < 1e-9 \
                    and "." in symbol:
                cls, _, member = symbol.rpartition(".")
                hit = _class_member(module, cls, member)
                if hit:
                    _set(rowid, hit, TYPED_CALL_CONFIDENCE)
                    resolved_count += 1
                    continue
                if edge_type != "calls":
                    continue      # a reference to a member its class lacks: no guess
                target_id, confidence = member, MEMBER_CALL_CONFIDENCE   # fall back: by name
            # 2. A symbol named by its dotted path: only a file at that path.
            elif confidence is not None and abs(confidence - PATH_REFERENCE_CONFIDENCE) < 1e-9:
                if "." in symbol:
                    cls, _, member = symbol.rpartition(".")
                    hit = _class_member(module, cls, member)
                else:
                    cands = [c for c in name_to_ids.get(symbol, []) if _in_module(c, module)]
                    hit = cands[0] if len(cands) == 1 else None
                if hit:
                    _set(rowid, hit, PATH_REFERENCE_CONFIDENCE)
                    resolved_count += 1
                continue
            # 3. A module-qualified name that dangles (`core/x::f` from an import):
            #    the file whose path ends with that module first.
            elif module and "." not in symbol:
                cands = [c for c in name_to_ids.get(symbol, []) if _in_module(c, module)]
                if len(cands) == 1:
                    _set(rowid, cands[0], 1.0)
                    resolved_count += 1
                    continue

            # 4. By the bare name, as before. bare name = last separator-segment
            # (handles bare `Animal` and dangling `base::Animal` → `Animal`).
            # CodeNode.name for a method is the bare method name (id is
            # `file::Cls.method`, name is `method`), so if the segment still carries a
            # `Cls.` prefix, fall back to the part after the last dot.
            target_name = target_id.rsplit(sep, 1)[-1]
            candidates = name_to_ids.get(target_name, [])
            if not candidates and "." in target_name:
                candidates = name_to_ids.get(target_name.rsplit(".", 1)[-1], [])
            by_name_only = confidence is not None and confidence <= MEMBER_CALL_CONFIDENCE
            if by_name_only:
                # a call on a value (f.Close()) names a method, never a free function,
                # and is not the caller itself (`self.db.load()` inside Store.load)
                candidates = [c for c in candidates if c in member_ok and c != source_id]
            resolved_conf = (BUILTIN_NAME_CONFIDENCE if by_name_only and confidence is not None
                             and confidence <= BUILTIN_NAME_CONFIDENCE + 1e-6
                             else NAME_ONLY_CONFIDENCE if by_name_only else 0.8)

            if len(candidates) == 1:
                _set(rowid, candidates[0], resolved_conf)
                resolved_count += 1
            elif len(candidates) > 1:
                caller_file = source_id.split(sep)[0]
                imported_files = file_imports.get(caller_file, set())
                matching = [c for c in candidates
                            if _imported(c.split(sep)[0], imported_files)]
                if len(matching) == 1:
                    _set(rowid, matching[0], resolved_conf)
                    resolved_count += 1

        self._conn.commit()
        return resolved_count

    # ── internal helpers ─────────────────────────────────────────────────

    @_write_locked
    def remove_file(self, file_path: str) -> None:
        """Remove all nodes, edges, and FTS entries for a deleted file."""
        # 1. Delete FTS entries (must happen before node deletion — needs rowid)
        old_rowids = self._conn.execute(
            "SELECT rowid, name, id, file_path FROM code_nodes WHERE file_path = ?",
            (file_path,),
        ).fetchall()
        for rowid, name, node_id, fp in old_rowids:
            try:
                self._conn.execute(
                    "INSERT INTO code_fts(code_fts, rowid, name, id, file_path) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (rowid, name, node_id, fp),
                )
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                pass  # FTS out of sync — rebuild_fts will fix

        # 2. Delete edges referencing these nodes
        old_ids = [r[2] for r in old_rowids]  # node IDs
        if old_ids:
            tmpl = (
                "DELETE FROM code_edges "
                "WHERE source_id IN ({placeholders}) "
                "OR target_id IN ({placeholders})"
            )
            for start in range(0, len(old_ids), _BATCH_SIZE):
                batch = old_ids[start : start + _BATCH_SIZE]
                ph = ",".join("?" * len(batch))
                self._conn.execute(
                    tmpl.replace("{placeholders}", ph), batch + batch
                )

        # 3. Delete nodes
        self._conn.execute(
            "DELETE FROM code_nodes WHERE file_path = ?", (file_path,)
        )
        # 4. Its own imports go; imports of it no longer resolve.
        self._conn.execute("DELETE FROM code_imports WHERE src_file = ?", (file_path,))
        self._conn.execute("UPDATE code_imports SET dst_file = NULL WHERE dst_file = ?", (file_path,))
        # A failure part-way is rolled back by _write_locked.
        self._conn.commit()

    # ── route CRUD ──────────────────────────────────────────────────────────

    def delete_routes_for_file(self, file_path: str) -> int:
        """Delete all routes belonging to a specific file.

        Called before re-inserting routes to prevent stale phantom routes
        from persisting when decorators are removed from source code.
        """
        cur = self._conn.execute(
            "DELETE FROM code_routes WHERE file_path = ?", (file_path,)
        )
        self._conn.commit()
        return cur.rowcount

    def insert_routes(self, routes: list) -> int:
        """Batch insert routes into code_routes table.

        Each element must be a dict or dataclass with fields matching the
        ``code_routes`` table columns. Existing routes with same ID are replaced.

        Returns count of rows inserted.
        """
        sql = """
            INSERT OR REPLACE INTO code_routes
                (id, method, path, handler_node_id, framework, file_path,
                 line_number, middleware)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        count = 0
        for start in range(0, len(routes), _BATCH_SIZE):
            batch = routes[start : start + _BATCH_SIZE]
            rows = []
            for r in batch:
                d = r if isinstance(r, dict) else r.__dict__
                middleware = d.get("middleware")
                if isinstance(middleware, list):
                    middleware = ",".join(middleware)
                rows.append((
                    d["id"],
                    d["method"],
                    d["path"],
                    d["handler_node_id"],
                    d["framework"],
                    d["file_path"],
                    d.get("line_number"),
                    middleware,
                ))
            self._conn.executemany(sql, rows)
            count += len(rows)
        self._conn.commit()
        return count

    def get_routes(self, file_path: str | None = None) -> list[dict]:
        """Return routes, optionally filtered by file_path.

        Returns:
            List of dicts with keys: id, method, path, handler_node_id,
            framework, file_path, line_number, middleware.
        """
        if file_path:
            rows = self._conn.execute(
                "SELECT id, method, path, handler_node_id, framework, "
                "file_path, line_number, middleware "
                "FROM code_routes WHERE file_path = ? ORDER BY path",
                (file_path,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, method, path, handler_node_id, framework, "
                "file_path, line_number, middleware "
                "FROM code_routes ORDER BY path",
            ).fetchall()

        return [
            {
                "id": r[0], "method": r[1], "path": r[2],
                "handler_node_id": r[3], "framework": r[4],
                "file_path": r[5], "line_number": r[6],
                "middleware": r[7],
            }
            for r in rows
        ]
