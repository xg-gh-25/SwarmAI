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
