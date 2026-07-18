"""Tests for GraphStore — SQLite graph store with CTE-based queries."""

import sqlite3
import time

import pytest

from core.code_intel.graph_store import GraphStore, _sanitize_name


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path):
    """Create a fresh GraphStore in a temp directory."""
    db = tmp_path / "test_graph.db"
    gs = GraphStore(db)
    yield gs
    gs.close()


def _make_node(
    node_id: str = "n1",
    file_path: str = "src/foo.py",
    node_type: str = "function",
    name: str = "do_stuff",
    line_start: int = 1,
    line_end: int = 10,
    language: str = "python",
    is_export: int = 1,
    is_entry_point: int = 0,
    file_hash: str | None = None,
) -> dict:
    return {
        "id": node_id,
        "file_path": file_path,
        "node_type": node_type,
        "name": name,
        "line_start": line_start,
        "line_end": line_end,
        "language": language,
        "is_export": is_export,
        "is_entry_point": is_entry_point,
        "file_hash": file_hash,
    }


def _make_edge(
    source: str = "n1",
    target: str = "n2",
    edge_type: str = "calls",
    confidence: float = 1.0,
    line_number: int | None = None,
) -> dict:
    return {
        "source_id": source,
        "target_id": target,
        "edge_type": edge_type,
        "confidence": confidence,
        "line_number": line_number,
    }


# ── Schema creation ──────────────────────────────────────────────────────


class TestSchemaCreation:
    def test_tables_exist(self, store):
        """New database should have all required tables."""
        tables = {
            r[0]
            for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "code_nodes" in tables
        assert "code_edges" in tables
        assert "graph_meta" in tables
        # FTS tables have internal names; check the virtual table.
        assert "code_fts" in tables

    def test_wal_mode(self, store):
        """Database should be in WAL journal mode."""
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


# ── Node/Edge upsert ────────────────────────────────────────────────────


class TestUpsert:
    def test_upsert_nodes(self, store):
        n1 = _make_node("n1", name="alpha")
        n2 = _make_node("n2", name="beta")
        count = store.upsert_nodes([n1, n2])
        assert count == 2
        rows = store._conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()
        assert rows[0] == 2

    def test_upsert_nodes_idempotent(self, store):
        n1 = _make_node("n1", name="alpha")
        store.upsert_nodes([n1])
        store.upsert_nodes([n1])  # same id
        rows = store._conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()
        assert rows[0] == 1

    def test_upsert_edges(self, store):
        store.upsert_nodes([_make_node("n1"), _make_node("n2")])
        count = store.upsert_edges([_make_edge("n1", "n2")])
        assert count == 1

    def test_upsert_edges_idempotent(self, store):
        store.upsert_nodes([_make_node("n1"), _make_node("n2")])
        e = _make_edge("n1", "n2", line_number=5)
        store.upsert_edges([e])
        store.upsert_edges([e])
        rows = store._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()
        assert rows[0] == 1

    def test_upsert_edges_idempotent_null_line(self, store):
        """NULL line_number edges (synthesized / file-level) must dedup too.

        SQLite treats each NULL as DISTINCT in a UNIQUE index, so an edge with
        line_number=None used to insert byte-identical duplicates that inflated
        callers/impact counts (codegraph bug #1034). The identity index folds
        NULL via IFNULL(line_number,-1) so these dedup.
        """
        store.upsert_nodes([_make_node("n1"), _make_node("n2")])
        e = _make_edge("n1", "n2", edge_type="references", line_number=None)
        store.upsert_edges([e])
        store.upsert_edges([e])
        rows = store._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()
        assert rows[0] == 1

    def test_null_line_migration_dedups_existing(self, tmp_path):
        """The schema-version migration dedups pre-existing NULL-line dup rows.

        Simulates an OLD-schema DB (inline UNIQUE on nullable line_number, which
        let NULL-line duplicates through). Opening it via GraphStore must run the
        migration: fold NULL, dedup keeping MAX(confidence), install the identity
        index as sole authority.
        """
        db = tmp_path / "old_schema.db"
        # Build an OLD-schema edges table with the original inline UNIQUE
        # (source,target,edge_type,line_number) — NULL line lets dups through.
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE code_edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                line_number INTEGER,
                UNIQUE(source_id, target_id, edge_type, line_number)
            );
            """
        )
        # Two byte-identical NULL-line edges + a lower-confidence third — the old
        # UNIQUE allowed all three because NULL != NULL.
        conn.executemany(
            "INSERT INTO code_edges (source_id,target_id,edge_type,confidence,line_number) "
            "VALUES (?,?,?,?,?)",
            [
                ("a", "b", "references", 0.6, None),
                ("a", "b", "references", 0.9, None),
                ("a", "b", "references", 0.5, None),
            ],
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0] == 3
        conn.close()

        # Opening via GraphStore triggers the migration.
        gs = GraphStore(db)
        try:
            n = gs._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]
            assert n == 1, f"migration should dedup 3 NULL-line dups to 1, got {n}"
            # Dedup keeps MAX(confidence).
            conf = gs._conn.execute(
                "SELECT confidence FROM code_edges WHERE source_id='a'"
            ).fetchone()[0]
            assert conf == 0.9, f"dedup must keep MAX(confidence)=0.9, got {conf}"
            # The identity index is now sole authority — a re-insert dedups.
            gs.upsert_edges(
                [_make_edge("a", "b", edge_type="references", line_number=None)]
            )
            n2 = gs._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]
            assert n2 == 1, f"post-migration NULL-line re-insert must dedup, got {n2}"
        finally:
            gs.close()

    def test_migration_is_atomic_on_midway_failure(self, tmp_path):
        """A migration that fails mid-rebuild must roll back FULLY — original data
        survives, no orphaned code_edges_legacy, version NOT recorded.

        Guards the Gate-2 HIGH: sqlite3.executescript() implicitly commits, so the
        rebuild must run as per-statement execute() inside one real transaction. We
        force a failure partway (a BEFORE-INSERT trigger on the freshly-created
        code_edges that RAISEs) and assert all-or-nothing. Because sqlite3.Connection
        is a C type (can't monkeypatch .execute), we inject the failure via a
        subclass of GraphStore that plants the aborting trigger between the CREATE
        and the INSERT...SELECT of the rebuild.
        """
        import core.code_intel.graph_store as G

        db = tmp_path / "old_schema.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE code_edges (
                source_id TEXT NOT NULL, target_id TEXT NOT NULL, edge_type TEXT NOT NULL,
                confidence REAL DEFAULT 1.0, line_number INTEGER,
                UNIQUE(source_id, target_id, edge_type, line_number)
            );
            CREATE TABLE graph_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        conn.execute(
            "INSERT INTO code_edges (source_id,target_id,edge_type,confidence,line_number) "
            "VALUES ('a','b','calls',1.0,5)"
        )
        conn.commit()
        conn.close()

        # sqlite3.Connection is a C type — .execute is read-only, can't monkeypatch.
        # Wrap the connection in a thin Python proxy for the duration of the
        # migration so we can raise on the rebuild's INSERT...SELECT (after ALTER
        # RENAME + CREATE have already run inside the transaction). The proxy
        # delegates everything else to the real connection.
        class _FlakyConn:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *a, **k):
                if "INSERT INTO code_edges" in sql and "SELECT" in sql:
                    raise sqlite3.OperationalError("simulated mid-rebuild failure")
                return self._real.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._real, name)

        class _FlakyGraphStore(G.GraphStore):
            def _migrate_schema(self):
                real = self._conn
                self._conn = _FlakyConn(real)
                try:
                    return super()._migrate_schema()
                finally:
                    self._conn = real

        gs = _FlakyGraphStore(db)  # migration runs in __init__, hits the forced failure
        try:
            # All-or-nothing: the ORIGINAL table + row must still be intact.
            n = gs._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]
            assert n == 1, f"failed migration must roll back — original row lost, got {n}"
            orphan = gs._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_edges_legacy'"
            ).fetchone()
            assert orphan is None, "failed migration must not orphan code_edges_legacy"
            assert gs.get_meta("edge_identity_version") != str(G.GraphStore._EDGE_IDENTITY_VERSION), \
                "a failed migration must NOT stamp the version as migrated"
        finally:
            gs.close()


# ── Blast radius (bidirectional CTE) ────────────────────────────────────


class TestBlastRadius:
    def _build_chain(self, store):
        """Build: A -> B -> C -> D (linear call chain)."""
        nodes = [
            _make_node("A", name="a_func"),
            _make_node("B", name="b_func"),
            _make_node("C", name="c_func"),
            _make_node("D", name="d_func"),
        ]
        edges = [
            _make_edge("A", "B"),
            _make_edge("B", "C"),
            _make_edge("C", "D"),
        ]
        store.upsert_nodes(nodes)
        store.upsert_edges(edges)

    def test_blast_radius_depth_1(self, store):
        self._build_chain(store)
        result = store.blast_radius(["B"], max_depth=1)
        ids = {nid for nid, _ in result}
        # B itself (depth 0), A (backward 1), C (forward 1)
        assert "B" in ids
        assert "A" in ids
        assert "C" in ids
        # D is 2 hops forward, should not appear at depth 1.
        assert "D" not in ids

    def test_blast_radius_depth_2(self, store):
        self._build_chain(store)
        result = store.blast_radius(["B"], max_depth=2)
        ids = {nid for nid, _ in result}
        assert ids == {"A", "B", "C", "D"}

    def test_blast_radius_empty(self, store):
        assert store.blast_radius([]) == []


# ── Dead-code detection ──────────────────────────────────────────────────


class TestFindDeadCode:
    def test_finds_unreferenced(self, store):
        store.upsert_nodes([
            _make_node("used", name="used_fn", file_path="src/lib.py"),
            _make_node("unused", name="orphan_fn", file_path="src/lib.py"),
        ])
        store.upsert_edges([_make_edge("caller", "used")])
        dead = store.find_dead_code()
        dead_ids = {d["id"] for d in dead}
        assert "unused" in dead_ids
        assert "used" not in dead_ids

    def test_excludes_test_files(self, store):
        store.upsert_nodes([
            _make_node("t1", name="test_fn", file_path="tests/test_foo.py"),
        ])
        dead = store.find_dead_code()
        assert all(d["id"] != "t1" for d in dead)

    def test_excludes_entry_points(self, store):
        store.upsert_nodes([
            _make_node("ep", name="main", is_entry_point=1, file_path="src/main.py"),
        ])
        dead = store.find_dead_code()
        assert all(d["id"] != "ep" for d in dead)


# ── FTS5 search ──────────────────────────────────────────────────────────


class TestFTS:
    def test_search_symbols(self, store):
        store.upsert_nodes([
            _make_node("n1", name="calculate_total"),
            _make_node("n2", name="calculate_tax"),
            _make_node("n3", name="send_email"),
        ])
        store.rebuild_fts()
        results = store.search_symbols("calculate")
        names = {r["name"] for r in results}
        assert "calculate_total" in names
        assert "calculate_tax" in names
        assert "send_email" not in names

    def test_search_empty_query(self, store):
        assert store.search_symbols("") == []

    def test_search_injection_safe(self, store):
        """Special FTS characters should be stripped, not cause errors."""
        store.upsert_nodes([_make_node("n1", name="safe_func")])
        store.rebuild_fts()
        # These should not raise or produce unexpected results.
        store.search_symbols('OR "drop table"')
        store.search_symbols("NOT *")

    def test_prod_symbol_outranks_test_symbol(self, store):
        """run_fc313f42: a verbose TEST symbol name must NOT out-rank the real prod
        symbol on an NL query — test symbols are down-weighted (not excluded)."""
        store.upsert_nodes([
            _make_node("p1", file_path="backend/core/task_manager.py", name="cancel_task"),
            # a test whose name packs MORE query terms than the prod symbol
            _make_node("t1", file_path="backend/tests/test_x.py",
                       name="test_task_cancellation_and_cleanup_completed"),
        ])
        store.rebuild_fts()
        results = store.search_symbols("task cancellation cleanup")
        names = [r["name"] for r in results]
        assert "cancel_task" in names and "test_task_cancellation_and_cleanup_completed" in names, \
            "both must be returned (down-weight, not exclude)"
        assert names.index("cancel_task") < names.index("test_task_cancellation_and_cleanup_completed"), \
            "prod symbol must rank above the verbose test symbol"

    def test_test_symbols_still_returned(self, store):
        """Down-weight must NOT exclude — a query that only matches test symbols
        still returns them."""
        store.upsert_nodes([
            _make_node("t1", file_path="backend/tests/test_x.py", name="test_only_widget"),
        ])
        store.rebuild_fts()
        results = store.search_symbols("widget")
        assert any(r["name"] == "test_only_widget" for r in results), \
            "a test-only match must still be returned"

    def test_test_intent_query_not_penalized(self, store):
        """Gate-2 HIGH (run_fc313f42): a query that WANTS tests ('...tested', 'test
        ...') must NOT down-weight test symbols — else 'how is X tested' returned
        ZERO test symbols. The strongly-matching test symbol must rank #1."""
        store.upsert_nodes([
            _make_node("p1", file_path="backend/core/task_manager.py", name="cancel_task"),
            _make_node("t1", file_path="backend/tests/test_x.py",
                       name="test_task_cancellation_and_cleanup"),
        ])
        store.rebuild_fts()
        # explicit test intent → test symbol keeps its natural (higher) rank
        res = store.search_symbols("how is task cancellation tested")
        names = [r["name"] for r in res]
        assert names[0] == "test_task_cancellation_and_cleanup", \
            "a test-intent query must surface the test symbol first (no penalty)"
        # control: same match WITHOUT test intent → prod wins (penalty applies)
        res2 = store.search_symbols("task cancellation cleanup")
        n2 = [r["name"] for r in res2]
        assert n2.index("cancel_task") < n2.index("test_task_cancellation_and_cleanup"), \
            "a non-test-intent query still demotes the test symbol"


# ── Keyword search ───────────────────────────────────────────────────────


class TestKeywordSearch:
    def test_keyword_basic(self, store):
        store.upsert_nodes([
            _make_node("n1", name="parse_json"),
            _make_node("n2", name="parse_xml"),
            _make_node("n3", name="render_html"),
        ])
        results = store.keyword_search("parse")
        names = {r["name"] for r in results}
        assert "parse_json" in names
        assert "parse_xml" in names
        assert "render_html" not in names

    def test_keyword_empty(self, store):
        assert store.keyword_search("") == []


# ── Meta get/set ─────────────────────────────────────────────────────────


class TestMeta:
    def test_set_and_get(self, store):
        store.set_meta("version", "1.0")
        assert store.get_meta("version") == "1.0"

    def test_get_missing(self, store):
        assert store.get_meta("nonexistent") is None

    def test_overwrite(self, store):
        store.set_meta("k", "v1")
        store.set_meta("k", "v2")
        assert store.get_meta("k") == "v2"


# ── Atomic file replacement ─────────────────────────────────────────────


class TestStoreFileNodesEdges:
    def test_replaces_old_data(self, store):
        # Insert initial data for file.
        old_node = _make_node("old1", file_path="src/a.py", name="old_fn")
        store.upsert_nodes([old_node])
        store.rebuild_fts()

        # Replace with new data.
        new_node = _make_node("new1", file_path="src/a.py", name="new_fn")
        new_edge = _make_edge("new1", "other", edge_type="calls")
        store.store_file_nodes_edges("src/a.py", [new_node], [new_edge], "hash123")

        nodes = store.get_nodes_by_file("src/a.py")
        assert len(nodes) == 1
        assert nodes[0]["id"] == "new1"

    def test_atomic_rollback(self, store):
        """If insertion fails mid-way, old data should be preserved."""
        node = _make_node("keep", file_path="src/b.py", name="keeper")
        store.upsert_nodes([node])
        store.rebuild_fts()

        # Try to replace with a bad node (missing required field).
        bad_node = {"id": "bad", "file_path": "src/b.py"}  # incomplete
        with pytest.raises(Exception):
            store.store_file_nodes_edges("src/b.py", [bad_node], [], "badhash")

        # Original node should still exist.
        nodes = store.get_nodes_by_file("src/b.py")
        assert len(nodes) == 1
        assert nodes[0]["id"] == "keep"


# ── Incremental update ──────────────────────────────────────────────────


class TestIncrementalUpdate:
    def test_skip_unchanged(self, store, tmp_path):
        """Files with matching hash should be skipped."""
        # Create a real file.
        src = tmp_path / "src"
        src.mkdir()
        f = src / "mod.py"
        f.write_text("x = 1")

        import hashlib
        fhash = hashlib.sha256(f.read_bytes()).hexdigest()

        # Pre-populate with a node that has the same hash.
        node = _make_node("n1", file_path="src/mod.py", file_hash=fhash)
        store.upsert_nodes([node])

        result = store.incremental_update(tmp_path, ["src/mod.py"])
        assert "src/mod.py" in result["skipped"]
        assert "src/mod.py" not in result["updated"]

    def test_deleted_file_cleaned(self, store, tmp_path):
        """Deleted files should have their nodes removed."""
        node = _make_node("d1", file_path="gone.py")
        store.upsert_nodes([node])

        result = store.incremental_update(tmp_path, ["gone.py"])
        assert "gone.py" in result["updated"]
        # Node should be removed.
        nodes = store.get_nodes_by_file("gone.py")
        assert len(nodes) == 0


# ── Batch query with >450 items ─────────────────────────────────────────


class TestBatchQuery:
    def test_large_batch(self, store):
        """Verify _batch_query correctly handles >450 items."""
        nodes = [
            _make_node(f"n{i}", name=f"fn_{i}", file_path=f"src/f{i}.py")
            for i in range(600)
        ]
        store.upsert_nodes(nodes)

        ids = [f"n{i}" for i in range(600)]
        tmpl = "SELECT id FROM code_nodes WHERE id IN ({placeholders})"
        results = GraphStore._batch_query(store._conn, tmpl, ids)
        assert len(results) == 600


# ── Transaction rollback on error ────────────────────────────────────────


class TestTransactionRollback:
    def test_rollback_preserves_state(self, store):
        """A failed transaction should not corrupt existing data."""
        store.upsert_nodes([_make_node("safe", name="safe_fn")])

        # Force a failure inside store_file_nodes_edges.
        with pytest.raises(Exception):
            store.store_file_nodes_edges(
                "other.py",
                [{"id": "x"}],  # incomplete node — will KeyError
                [],
                "h",
            )

        # The safe node should survive.
        count = store._conn.execute(
            "SELECT COUNT(*) FROM code_nodes WHERE id = 'safe'"
        ).fetchone()[0]
        assert count == 1


# ── find_dependents ──────────────────────────────────────────────────────


class TestFindDependents:
    def test_finds_importing_files(self, store):
        store.upsert_nodes([
            _make_node("lib_fn", file_path="lib/utils.py", name="helper"),
            _make_node("app_fn", file_path="app/main.py", name="run"),
        ])
        store.upsert_edges([_make_edge("app_fn", "lib_fn", edge_type="imports")])
        deps = store.find_dependents("lib/utils.py")
        assert "app/main.py" in deps

    def test_excludes_self(self, store):
        store.upsert_nodes([
            _make_node("a", file_path="x.py"),
            _make_node("b", file_path="x.py"),
        ])
        store.upsert_edges([_make_edge("a", "b")])
        deps = store.find_dependents("x.py")
        assert "x.py" not in deps


# ── Codebase summary ────────────────────────────────────────────────────


class TestCodebaseSummary:
    def test_summary_structure(self, store):
        store.upsert_nodes([
            _make_node("n1", language="python"),
            _make_node("n2", language="python", file_path="src/bar.py"),
            _make_node("n3", language="javascript", file_path="web/app.js"),
        ])
        store.upsert_edges([_make_edge("n1", "n2")])
        summary = store.get_codebase_summary()
        assert summary["total_nodes"] == 3
        assert summary["total_edges"] == 1
        assert summary["languages"]["python"] == 2
        assert summary["languages"]["javascript"] == 1


# ── Clear ────────────────────────────────────────────────────────────────


class TestClear:
    def test_clear_removes_all(self, store):
        store.upsert_nodes([_make_node("n1")])
        store.upsert_edges([_make_edge("n1", "n2")])
        store.set_meta("k", "v")
        store.clear()
        assert store._conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()[0] == 0
        assert store._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0] == 0
        assert store.get_meta("k") is None


# ── Sanitize name ────────────────────────────────────────────────────────


class TestSanitizeName:
    def test_strips_unsafe(self):
        assert _sanitize_name("hello;DROP TABLE") == "helloDROP TABLE"

    def test_preserves_safe(self):
        assert _sanitize_name("my_func") == "my_func"

    def test_truncates(self):
        long = "a" * 300
        assert len(_sanitize_name(long)) == 256


class TestFullRebuildPersistsFileHash:
    """Gate-2 HIGH (run_4602932d): full-rebuild path (upsert_nodes via bulk_insert)
    must persist the raw-file sha as file_hash — CodeNode carries it as `sha256`,
    not `file_hash`, so without the fallback every node stored file_hash=NULL and
    the graded-incremental NONE-detection was dead on the first run after a full
    rebuild. This test uses a dataclass-like node exposing `sha256` (as CodeNode
    does) and asserts the stored file_hash equals it."""

    def test_sha256_maps_to_file_hash(self, store):
        from dataclasses import dataclass

        @dataclass
        class _N:
            id: str = "a.py::f"
            file_path: str = "a.py"
            node_type: str = "function"
            name: str = "f"
            line_start: int = 1
            line_end: int = 3
            language: str = "python"
            is_export: bool = True
            is_entry_point: bool = False
            sha256: str | None = "deadbeefhash"
            # deliberately NO file_hash attribute — mirrors real CodeNode

        store.upsert_nodes([_N()])
        rows = store.get_nodes_by_file("a.py")
        assert rows and rows[0]["file_hash"] == "deadbeefhash", \
            "full-rebuild must persist sha256 into file_hash (NONE-detection depends on it)"

    def test_explicit_file_hash_still_wins(self, store):
        """A dict node passing explicit file_hash keeps it (fallback only fills NULL)."""
        store.upsert_nodes([{
            "id": "b.py::g", "file_path": "b.py", "node_type": "function", "name": "g",
            "line_start": 1, "line_end": 2, "language": "python",
            "is_export": 1, "is_entry_point": 0, "file_hash": "explicit123",
        }])
        rows = store.get_nodes_by_file("b.py")
        assert rows[0]["file_hash"] == "explicit123"


class TestModuleMapEntryPoint:
    """run_4344d341: get_module_map dropped is_entry_point (SELECT omitted it), so
    json_exporter._build_entry_points always saw None → entry_points exported as []
    despite the DB carrying is_entry_point=1 nodes."""

    def test_module_map_carries_is_entry_point(self, store):
        store.upsert_nodes([
            _make_node("backend/core/a.py::ep", file_path="backend/core/a.py",
                       name="ep", is_entry_point=1),
            _make_node("backend/core/a.py::plain", file_path="backend/core/a.py",
                       name="plain", is_entry_point=0),
        ])
        mm = store.get_module_map()
        nodes = mm.get("backend/core", [])
        by_name = {n["name"]: n for n in nodes}
        assert by_name["ep"].get("is_entry_point") == 1, \
            "get_module_map must carry is_entry_point so entry_points can be exported"
        assert by_name["plain"].get("is_entry_point") == 0


class TestModuleEdges:
    """run_4344d341: code-intel.json emitted edges=0 — no module-level edge
    aggregation existed. get_module_edges rolls code_edges up to 2-level module
    prefixes (the architectural skeleton), NOT a raw 25K-edge dump."""

    def test_aggregates_cross_module_edges(self, store):
        store.upsert_nodes([
            _make_node("backend/core/a.py::f", file_path="backend/core/a.py", name="f"),
            _make_node("backend/jobs/b.py::g", file_path="backend/jobs/b.py", name="g"),
            _make_node("backend/jobs/c.py::h", file_path="backend/jobs/c.py", name="h"),
        ])
        store.upsert_edges([
            _make_edge("backend/core/a.py::f", "backend/jobs/b.py::g"),
            _make_edge("backend/core/a.py::f", "backend/jobs/c.py::h"),
        ])
        me = store.get_module_edges()
        pair = {(e["from"], e["to"]): e for e in me}
        assert ("backend/core", "backend/jobs") in pair, \
            "cross-module edges must aggregate to module-prefix pairs"
        assert pair[("backend/core", "backend/jobs")]["count"] == 2, \
            "two edges core→jobs must aggregate to count=2"

    def test_excludes_intra_module_edges(self, store):
        store.upsert_nodes([
            _make_node("backend/core/a.py::f", file_path="backend/core/a.py", name="f"),
            _make_node("backend/core/a.py::g", file_path="backend/core/a.py", name="g"),
        ])
        store.upsert_edges([_make_edge("backend/core/a.py::f", "backend/core/a.py::g")])
        me = store.get_module_edges()
        assert all(e["from"] != e["to"] for e in me), \
            "intra-module edges must be excluded (skeleton = cross-module only)"

    # ── confidence enrichment + god-node guard (run_2392a203) ─────────────
    # Ported from graphify: every module edge carries a confidence label
    # (EXTRACTED/INFERRED) + score, and a god-node guard drops confidence<=0.5
    # bare/unresolved targets (str/mkdir/get) whose _mod_of would otherwise
    # fabricate a module. See graph_store.get_module_edges + parser.py:1044.

    def test_each_edge_carries_confidence_label_and_score(self, store):
        """AC1: every module_edge has confidence (EXTRACTED|INFERRED) + confidence_score."""
        store.upsert_nodes([
            _make_node("backend/core/a.py::f", file_path="backend/core/a.py", name="f"),
            _make_node("backend/jobs/b.py::g", file_path="backend/jobs/b.py", name="g"),
        ])
        store.upsert_edges([
            _make_edge("backend/core/a.py::f", "backend/jobs/b.py::g", confidence=1.0),
        ])
        me = store.get_module_edges()
        assert me, "expected at least one kept module edge"
        e = me[0]
        assert e["confidence"] in ("EXTRACTED", "INFERRED"), \
            "each module_edge must carry a confidence label (AMBIGUOUS is guarded out)"
        assert "confidence_score" in e and isinstance(e["confidence_score"], (int, float)), \
            "each module_edge must carry a numeric confidence_score"
        assert e["confidence"] == "EXTRACTED" and e["confidence_score"] == 1.0, \
            "a qualified (1.0) edge → EXTRACTED, score 1.0"

    def test_god_node_guard_excludes_bare_low_confidence_target(self, store):
        """AC2 (the load-bearing test): a confidence<=0.5 bare target (str/mkdir)
        must NOT appear as a fake module endpoint. This is the mutation anchor —
        remove the guard and this edge reappears."""
        store.upsert_nodes([
            _make_node("backend/core/a.py::f", file_path="backend/core/a.py", name="f"),
        ])
        store.upsert_edges([
            # bare unresolved builtin call: target has no "::", confidence 0.5
            _make_edge("backend/core/a.py::f", "str", confidence=0.5),
            _make_edge("backend/core/a.py::f", "mkdir", confidence=0.5),
        ])
        me = store.get_module_edges()
        endpoints = {e["to"] for e in me} | {e["from"] for e in me}
        assert "str" not in endpoints and "mkdir" not in endpoints, \
            "god-node guard must drop confidence<=0.5 bare targets (fake modules)"
        assert me == [], "an all-bare (<=0.5) pair must vanish entirely under the guard"

    def test_resolved_edge_survives_guard(self, store):
        """Guard boundary: a 0.6 (INFERRED) qualified cross-module edge is KEPT."""
        store.upsert_nodes([
            _make_node("backend/core/a.py::f", file_path="backend/core/a.py", name="f"),
            _make_node("backend/jobs/b.py::g", file_path="backend/jobs/b.py", name="g"),
        ])
        store.upsert_edges([
            _make_edge("backend/core/a.py::f", "backend/jobs/b.py::g", confidence=0.6),
        ])
        me = store.get_module_edges()
        pair = {(e["from"], e["to"]): e for e in me}
        assert ("backend/core", "backend/jobs") in pair, \
            "a resolved 0.6 edge must survive the guard (only <=0.5 is dropped)"
        assert pair[("backend/core", "backend/jobs")]["confidence"] == "INFERRED", \
            "0.6 → INFERRED label"
        assert pair[("backend/core", "backend/jobs")]["confidence_score"] == 0.6

    def test_mixed_pair_takes_max_confidence(self, store):
        """AC (Gate-1 required): a pair with a 0.6 AND a 1.0 edge → MAX → EXTRACTED,
        score 1.0. Locks the MAX-vs-MIN choice so it is intentional."""
        store.upsert_nodes([
            _make_node("backend/core/a.py::f", file_path="backend/core/a.py", name="f"),
            _make_node("backend/jobs/b.py::g", file_path="backend/jobs/b.py", name="g"),
            _make_node("backend/jobs/c.py::h", file_path="backend/jobs/c.py", name="h"),
        ])
        store.upsert_edges([
            _make_edge("backend/core/a.py::f", "backend/jobs/b.py::g", confidence=0.6),
            _make_edge("backend/core/a.py::f", "backend/jobs/c.py::h", confidence=1.0),
        ])
        me = store.get_module_edges()
        pair = {(e["from"], e["to"]): e for e in me}
        edge = pair[("backend/core", "backend/jobs")]
        assert edge["confidence_score"] == 1.0, "MAX confidence across the pair"
        assert edge["confidence"] == "EXTRACTED", "score>=1.0 → EXTRACTED"
        assert edge["count"] == 2, "both kept edges counted"
