"""WAL disk-reclaim regression tests for code_intel GraphStore.

Tested behavior: after a bulk write, the on-disk ``-wal`` file must be reclaimed
(TRUNCATE checkpoint), NOT left to grow unboundedly. This is the 2.73GB-bloat root
cause (WAL file grew during a full re-index and SQLite never auto-shrinks it).

Methodology: drive the REAL GraphStore against a REAL temp SQLite file (no mocks —
the actual `.getsize(-wal)` on disk IS the property under test). Mutation-provable:
reverting the `checkpoint_truncate()` calls leaves a large WAL and fails these.
"""
import os
import sqlite3

import pytest

from core.code_intel.graph_store import GraphStore


def _wal_size(db_path) -> int:
    wal = str(db_path) + "-wal"
    return os.path.getsize(wal) if os.path.exists(wal) else 0


def _make_parse_results(n_files: int, nodes_per_file: int) -> list:
    """Synthetic parse results large enough to write real WAL frames."""
    out = []
    for f in range(n_files):
        fp = f"pkg/mod_{f}.py"
        nodes = [
            {
                "id": f"{fp}::sym_{i}",
                "name": f"sym_{i}",
                "node_type": "function",
                "file_path": fp,
                "line_start": i,
                "line_end": i + 1,
                "language": "python",
                "file_hash": f"hash_{f}",
            }
            for i in range(nodes_per_file)
        ]
        edges = [
            {
                "source_id": f"{fp}::sym_{i}",
                "target_id": f"{fp}::sym_{(i + 1) % nodes_per_file}",
                "edge_type": "calls",
                "line_number": i,
            }
            for i in range(nodes_per_file)
        ]
        out.append({"file_path": fp, "nodes": nodes, "edges": edges, "file_hash": f"hash_{f}"})
    return out


@pytest.fixture
def store(tmp_path):
    g = GraphStore(tmp_path / "code_intel.db")
    yield g
    g.close()


class TestWalReclaim:
    def test_bulk_insert_truncates_wal(self, store):
        """After bulk_insert the WAL file is reclaimed to ~0 (not left bloated)."""
        store.bulk_insert(_make_parse_results(50, 40))
        # data landed in the main DB
        assert store._conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()[0] == 2000
        # and the WAL file was truncated (checkpoint_truncate ran)
        assert _wal_size(store._db_path) == 0, "WAL file not reclaimed after bulk_insert"

    def test_checkpoint_truncate_reclaims_after_manual_growth(self, store):
        """checkpoint_truncate shrinks a grown WAL and preserves data."""
        store.bulk_insert(_make_parse_results(30, 30))
        # force WAL to hold uncheckpointed frames again
        for i in range(200):
            store._conn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES(?, ?)",
                (f"k_{i}", "x" * 500),
            )
        store._conn.commit()
        store.checkpoint_truncate()
        assert _wal_size(store._db_path) == 0
        # data intact
        assert store._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def test_checkpoint_truncate_nonfatal_when_reader_holds_wal(self, store, tmp_path):
        """A concurrent reader that pins the WAL must NOT crash checkpoint_truncate."""
        store.bulk_insert(_make_parse_results(20, 20))
        # A second connection with an OPEN read txn pins the WAL snapshot → TRUNCATE
        # returns busy=1. The method must swallow that and not raise.
        reader = sqlite3.connect(str(store._db_path) + "")
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM code_nodes").fetchone()
        # must not raise even though a reader holds the WAL
        store.checkpoint_truncate()  # no exception = pass
        reader.execute("COMMIT")
        reader.close()

    def test_autocheckpoint_pragma_set(self, store):
        """wal_autocheckpoint is bounded (not left at a value that never fires)."""
        val = store._conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        assert val == 2000
