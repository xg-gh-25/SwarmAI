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
    line_number INTEGER,
    UNIQUE(source_id, target_id, edge_type, line_number)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON code_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON code_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_nodes_file   ON code_nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_name   ON code_nodes(name);

CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(
    name, id, file_path,
    content='code_nodes', content_rowid=rowid,
    tokenize='porter unicode61'
);

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
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # ── lifecycle ────────────────────────────────────────────────────────

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
                    d.get("file_hash"),
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
            "SELECT id, file_path, node_type, name FROM code_nodes ORDER BY file_path"
        ).fetchall()
        modules: dict[str, list[dict]] = {}
        for row in rows:
            parts = row[1].split("/")
            prefix = "/".join(parts[:2]) if len(parts) > 2 else parts[0] if parts else ""
            modules.setdefault(prefix, []).append(
                {"id": row[0], "file_path": row[1], "node_type": row[2], "name": row[3]}
            )
        return modules

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
            "last_indexed": last_indexed,
        }

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
        # Quote individual tokens for FTS5 safety.
        tokens = safe.split()
        fts_query = " ".join(f'"{t}"' for t in tokens if t)
        if not fts_query:
            return []
        try:
            rows = self._conn.execute(
                "SELECT name, id, file_path, rank "
                "FROM code_fts WHERE code_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS table out of sync — fall back to keyword search.
            logger.warning("FTS5 query failed, falling back to keyword search")
            return self.keyword_search(query, limit)
        return [
            {"name": r[0], "id": r[1], "file_path": r[2], "rank": r[3]}
            for r in rows
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

    def bulk_insert(self, parse_results: list) -> None:
        """Insert all results from a full repo parse.

        Each element of *parse_results* should be a dict (or ParseResult) with
        keys ``file_path``, ``nodes``, ``edges``, and ``file_hash``.
        FTS is rebuilt at the end.
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
                self._remove_file(fpath)
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

    # ── internal helpers ─────────────────────────────────────────────────

    def _remove_file(self, file_path: str) -> None:
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
