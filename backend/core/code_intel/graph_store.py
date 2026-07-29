"""SQLite graph store for code intelligence — nodes, edges, and CTE queries.

Adapted from code-review-graph patterns: WAL mode, FTS5 content tables,
batched IN clauses, bidirectional CTE traversal for blast-radius analysis.

Public symbols:

- ``GraphStore``  — SQLite-backed graph of code nodes and dependency edges.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .parser import CodeEdge, CodeNode, ParseResult

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
    as json_exporter._is_test_path, run_4344d341)."""
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
"""


# ── GraphStore ───────────────────────────────────────────────────────────


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
        # check_same_thread=False: GraphStore is cached at module level and
        # accessed from both the main asyncio thread (code_intel_hook) and
        # BackgroundHookExecutor threads (context_health_hook). WAL mode +
        # busy_timeout handle concurrent access at the SQLite level.
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
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
        if not self._conn:
            return
        try:
            row = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            # row = (busy, log_pages, checkpointed_pages); busy=1 → a reader blocked it
            if row and row[0]:
                logger.debug("WAL checkpoint(TRUNCATE) busy — reader held WAL, will retry next bulk write")
        except Exception as e:
            logger.debug("WAL checkpoint(TRUNCATE) skipped: %s", e)

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

    def store_file_nodes_edges(
        self,
        file_path: str,
        nodes: list,
        edges: list,
        file_hash: str,
    ) -> None:
        """Atomically replace all nodes/edges for *file_path*.

        Deletes old data and inserts new data inside a single
        ``BEGIN IMMEDIATE`` transaction so readers never see a partial state.
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

            # Delete old nodes and their edges.
            old_ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT id FROM code_nodes WHERE file_path = ?",
                    (file_path,),
                ).fetchall()
            ]
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
                        tmpl.replace("{placeholders}", ph),
                        batch + batch,
                    )
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

            if not in_txn:
                self._conn.commit()
        except Exception:
            if not in_txn:
                self._conn.rollback()
            raise

    # ── FTS rebuild ──────────────────────────────────────────────────────

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

    def blast_radius(
        self,
        changed_node_ids: list[str],
        max_depth: int = 2,
    ) -> list[tuple[str, int]]:
        """Bidirectional traversal from *changed_node_ids*.

        Combines forward (callees via source_id -> target_id) and backward
        (callers via target_id -> source_id) reachability up to *max_depth*
        hops.  Uses a temp table for seed IDs so the CTE stays clean.

        Returns:
            List of ``(node_id, depth)`` tuples (depth 0 = seed nodes).
        """
        if not changed_node_ids:
            return []

        cur = self._conn.cursor()
        cur.execute("CREATE TEMP TABLE IF NOT EXISTS _seeds (id TEXT PRIMARY KEY)")
        cur.execute("DELETE FROM _seeds")
        for start in range(0, len(changed_node_ids), _BATCH_SIZE):
            batch = changed_node_ids[start : start + _BATCH_SIZE]
            cur.executemany("INSERT OR IGNORE INTO _seeds VALUES (?)", [(i,) for i in batch])

        # Forward CTE: follow edges source -> target.
        # Backward CTE: follow edges target -> source.
        sql = f"""
            WITH RECURSIVE
            forward(nid, depth) AS (
                SELECT id, 0 FROM _seeds
                UNION
                SELECT e.target_id, f.depth + 1
                FROM forward f
                JOIN code_edges e ON e.source_id = f.nid
                WHERE f.depth < ?
            ),
            backward(nid, depth) AS (
                SELECT id, 0 FROM _seeds
                UNION
                SELECT e.source_id, b.depth + 1
                FROM backward b
                JOIN code_edges e ON e.target_id = b.nid
                WHERE b.depth < ?
            )
            SELECT nid, MIN(depth) AS depth FROM (
                SELECT nid, depth FROM forward
                UNION ALL
                SELECT nid, depth FROM backward
            )
            GROUP BY nid
            ORDER BY depth
        """
        rows = cur.execute(sql, (max_depth, max_depth)).fetchall()
        cur.execute("DROP TABLE IF EXISTS _seeds")
        return [(r[0], r[1]) for r in rows]

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
    # (has "::", an EXACT reference), 0.8 = single-candidate cross-file resolved,
    # 0.6 = regex-fallback inferred call, 0.5 = bare/unresolved/N-candidate
    # ambiguous. A 0.5 edge is DEFINITIONALLY bare (parser.py:1044 sets 0.5 iff
    # the target has no "::"), so `_mod_of` turns its bare target (`str`, `mkdir`,
    # `get`) into a FABRICATED single-token module — 87% of the pre-guard module
    # skeleton was such garbage. Excluding <=0.5 edges from the AGGREGATION (not
    # from code_edges — they are legit per-symbol data) is graphify's god-node
    # guard: len(candidates) != 1 -> bail (extract.py:2107).
    _MODULE_EDGE_MIN_CONFIDENCE = 0.5

    def get_module_edges(self) -> list[dict]:
        """Aggregate code_edges up to 2-level module-prefix pairs — the compact
        ARCHITECTURAL SKELETON (which module calls which), NOT a raw per-symbol
        edge dump. run_4344d341: code-intel.json emitted edges=0 because no
        module-level aggregation existed and a raw dump (~25K edges) would bloat
        the readable JSON 10x. Cross-module only (intra-module edges excluded —
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

        # Last indexed timestamp
        last_indexed = self.get_meta("last_full_index") or self.get_meta("last_incremental_update")

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

    def get_edges_by_file(self, file_path: str) -> list[dict]:
        """Return the edges ORIGINATING from *file_path* (source_id ∈ this file).

        ⚠️ Population must mirror the parser's edge-emission rule (parser.py:~483:
        ``CodeEdge(source_id=enclosing_func, ...)`` — the source is always a def IN
        the file being parsed). The `code_edges` table has NO `file_path` column
        (edges are associated to a file only via node ids), and
        `store_file_nodes_edges` DELETEs a *superset* (source OR target in the
        file's nodes) — but that superset sweeps in INBOUND calls from OTHER files,
        which the parser never re-emits for THIS file. Using the superset here
        would make the OLD signature (superset) and the NEW signature (parser
        outbound-only) permanently unequal → every file grades STRUCTURAL → the
        grading optimization silently no-ops (Gate-1 #2, run_4602932d). So this
        reader filters to OUTBOUND edges only, matching the parser exactly.
        """
        rows = self._conn.execute(
            "SELECT e.source_id, e.target_id, e.edge_type, e.confidence, e.line_number "
            "FROM code_edges e "
            "WHERE e.source_id IN (SELECT id FROM code_nodes WHERE file_path = ?)",
            (file_path,),
        ).fetchall()
        return [
            {
                "source_id": r[0], "target_id": r[1], "edge_type": r[2],
                "confidence": r[3], "line_number": r[4],
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

    def count_callers_by_file(self, file_path: str) -> dict[str, int]:
        """Count how many callers target each node in *file_path*.

        Returns:
            Dict mapping node_id -> caller count.
        """
        rows = self._conn.execute(
            "SELECT n.id, COUNT(e.source_id) "
            "FROM code_nodes n "
            "LEFT JOIN code_edges e ON e.target_id = n.id "
            "WHERE n.file_path = ? "
            "GROUP BY n.id",
            (file_path,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ── search ───────────────────────────────────────────────────────────

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

    def clear(self) -> None:
        """Delete all nodes, edges, FTS data, and metadata."""
        self._conn.execute("DELETE FROM code_edges")
        self._conn.execute("DELETE FROM code_nodes")
        self._conn.execute(
            "INSERT INTO code_fts(code_fts) VALUES('delete-all')"
        )
        self._conn.execute("DELETE FROM graph_meta")
        self._conn.commit()

    def bulk_insert(self, parse_results: list, repo_root: str | Path | None = None) -> None:
        """Insert all results from a full repo parse.

        Each element of *parse_results* should be a dict (or ParseResult) with
        keys ``file_path``, ``nodes``, ``edges``, and ``file_hash``.
        FTS is rebuilt at the end.

        ``repo_root``: when provided, persisted into ``graph_meta`` as the absolute
        ``repo_root`` key. This is REQUIRED for the code_intel hook to work — its
        ``_build_context`` converts an absolute file path to repo-relative via
        ``get_meta("repo_root")``; if unset, injection is silently dead for every
        file, and ``context_health_hook``'s incremental reindex (which also gates
        on this key) never engages. All prod full-rebuild callers MUST pass it —
        this is the single choke-point every create/full-rebuild path funnels
        through, so stamping it here (not per-caller) makes it drift-proof.
        Optional for back-compat with test callers that set_meta by hand.
        """
        for pr in parse_results:
            d = pr if isinstance(pr, dict) else pr.__dict__
            self.upsert_nodes(d["nodes"])
            self.upsert_edges(d["edges"])
        self.rebuild_fts()

        # Layer 2: resolve bare call targets across files
        try:
            from .parser import resolve_bare_targets
            resolved = resolve_bare_targets(self)
            if resolved:
                logger.info("Cross-file resolution: %d bare targets resolved", resolved)
        except Exception as e:
            logger.debug("Cross-file resolution skipped: %s", e)

        # Cleanup: remove orphan edges whose target doesn't exist as a node.
        # These are calls to builtins/stdlib that regex captured but which are
        # not in the graph. Without this, blast_radius CTE traverses phantoms.
        try:
            orphan_count = self._conn.execute(
                "SELECT COUNT(*) FROM code_edges "
                "WHERE target_id NOT IN (SELECT id FROM code_nodes)"
            ).fetchone()[0]
            if orphan_count:
                self._conn.execute(
                    "DELETE FROM code_edges "
                    "WHERE target_id NOT IN (SELECT id FROM code_nodes)"
                )
                self._conn.commit()
                logger.info("Removed %d orphan edges (target not in nodes)", orphan_count)
        except Exception as e:
            logger.debug("Orphan edge cleanup skipped: %s", e)

        # Full rebuild wrote the whole graph into the WAL — reclaim it now so the
        # -wal file doesn't stay multi-GB on disk (the 2.73GB-bloat root cause).
        self.checkpoint_truncate()

        # Persist repo_root so the code_intel hook can resolve absolute file paths
        # to repo-relative (get_meta("repo_root") in _build_context). Without this,
        # injection is silently dead for every file. set_meta is INSERT OR REPLACE,
        # so re-stamping on every rebuild is idempotent AND self-healing (backfills
        # a db that predates this fix on its next full rebuild).
        if repo_root:
            self.set_meta("repo_root", str(Path(repo_root).resolve()))

    def incremental_update(
        self,
        repo_root: str | Path,
        changed_files: list[str],
    ) -> dict:
        """Incremental graph update for *changed_files*.

        Steps:
            1. Hash check — skip files whose content hash hasn't changed.
            2. Atomic replace — ``store_file_nodes_edges`` per file.
            3. Expand dependents — find files that reference changed symbols.
            4. Re-parse — (caller is responsible for parsing; we just record).
            5. FTS rebuild.
            6. Update meta with timestamp.

        Returns:
            Dict with keys: ``updated``, ``skipped``, ``dependents``.
        """
        repo_root = Path(repo_root)
        updated: list[str] = []
        skipped: list[str] = []

        for fpath in changed_files:
            full = repo_root / fpath
            if not full.exists():
                # File was deleted — remove its nodes/edges.
                self.remove_file(fpath)
                updated.append(fpath)
                continue

            content_hash = hashlib.sha256(full.read_bytes()).hexdigest()

            # Check if hash matches any existing node for this file.
            existing = self._conn.execute(
                "SELECT file_hash FROM code_nodes WHERE file_path = ? LIMIT 1",
                (fpath,),
            ).fetchone()
            if existing and existing[0] == content_hash:
                skipped.append(fpath)
                continue

            updated.append(fpath)

        # Find dependent files (files that import from changed files).
        dependent_files = set()
        for fpath in updated:
            deps = self.find_dependents(fpath)
            dependent_files.update(deps)
        # Don't re-include files already being updated.
        dependent_files -= set(updated)

        # Rebuild FTS after all changes.
        self.rebuild_fts()

        # Record the update timestamp.
        self.set_meta("last_incremental_update", str(time.time()))

        # Reclaim WAL disk space after the batch of writes (non-fatal if a reader
        # holds the WAL — retried on the next update).
        self.checkpoint_truncate()

        return {
            "updated": updated,
            "skipped": skipped,
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

    def resolve_bare_targets(self, qualified_separator: str) -> int:
        """Resolve unresolved calls/extends/references targets to real node ids.

        A target qualifies as unresolved if it does NOT match any code_node id —
        either bare (no separator, e.g. `helper`) OR qualified-but-dangling (has a
        separator but points at nothing, e.g. `base::Animal` when the real node is
        `base.py::Animal` — the import-map/node-id mismatch). Both resolve by the
        target's bare NAME via a global name-to-qualified-id lookup:
          - 1 candidate: resolve directly (confidence 0.8)
          - N candidates: prefer the one whose file is imported by the caller
          - 0 or ambiguous: leave unresolved (orphan-cleanup drops it)

        Covers edge_type IN (calls, extends, references) — extends/references were
        added by the dead-code false-positive fixes (run_8e023234). Returns count
        of resolved edges.
        """
        all_nodes = self._conn.execute(
            "SELECT id, name FROM code_nodes"
        ).fetchall()
        name_to_ids: dict[str, list[str]] = {}
        for node_id, name in all_nodes:
            name_to_ids.setdefault(name, []).append(node_id)

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
        bare_edges = self._conn.execute(
            "SELECT rowid, source_id, target_id FROM code_edges e "
            "WHERE e.edge_type IN ('calls', 'extends', 'references') "
            "AND NOT EXISTS (SELECT 1 FROM code_nodes n WHERE n.id = e.target_id)"
        ).fetchall()

        if not bare_edges:
            return 0

        file_imports: dict[str, set[str]] = {}
        import_edges = self._conn.execute(
            "SELECT source_id, target_id FROM code_edges WHERE edge_type = 'imports'"
        ).fetchall()
        for src, tgt in import_edges:
            src_file = src.split(qualified_separator)[0] if qualified_separator in src else src
            tgt_file = tgt.split(qualified_separator)[0] if qualified_separator in tgt else tgt
            file_imports.setdefault(src_file, set()).add(tgt_file)

        resolved_count = 0
        for rowid, source_id, target_id in bare_edges:
            # bare name = last separator-segment (handles bare `Animal` and dangling
            # `base::Animal` → `Animal`). CodeNode.name for a method is the bare
            # method name (id is `file::Cls.method`, name is `method`), so if the
            # segment still carries a `Cls.` prefix (a dangling qualified METHOD
            # target), fall back to the part after the last dot (Gate-2 recall LOW).
            target_name = target_id.rsplit(qualified_separator, 1)[-1]
            candidates = name_to_ids.get(target_name, [])
            if not candidates and "." in target_name:
                candidates = name_to_ids.get(target_name.rsplit(".", 1)[-1], [])

            if len(candidates) == 1:
                self._conn.execute(
                    "UPDATE code_edges SET target_id = ?, confidence = 0.8 WHERE rowid = ?",
                    (candidates[0], rowid)
                )
                resolved_count += 1
            elif len(candidates) > 1:
                caller_file = source_id.split(qualified_separator)[0]
                imported_files = file_imports.get(caller_file, set())
                matching = [c for c in candidates
                           if c.split(qualified_separator)[0] in imported_files]
                if len(matching) == 1:
                    self._conn.execute(
                        "UPDATE code_edges SET target_id = ?, confidence = 0.8 WHERE rowid = ?",
                        (matching[0], rowid)
                    )
                    resolved_count += 1

        self._conn.commit()
        return resolved_count

    # ── internal helpers ─────────────────────────────────────────────────

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

    def get_routes_for_handler(self, node_id: str) -> list[dict]:
        """Return all routes associated with a handler node.

        Args:
            node_id: The handler_node_id to look up.

        Returns:
            List of route dicts matching the handler.
        """
        rows = self._conn.execute(
            "SELECT id, method, path, handler_node_id, framework, "
            "file_path, line_number, middleware "
            "FROM code_routes WHERE handler_node_id = ? ORDER BY path",
            (node_id,),
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
