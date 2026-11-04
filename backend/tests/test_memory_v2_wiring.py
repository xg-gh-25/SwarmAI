"""Integration tests for Memory Architecture v2 production wiring (W1-W5).

Verifies that the core modules (temporal validity,
multi-store RecallEngine) are actually wired into production hooks.
"""
import sqlite3
from unittest.mock import patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────


def _make_conn():
    """In-memory sqlite-vec connection."""
    conn = sqlite3.connect(":memory:")
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not installed")
    return conn


# ── W1: transcript indexing is gone (schema v13) ─────────────────────


class TestW1TranscriptIndexRemoved:
    """transcript_chunks was dropped: nothing may write or read it again."""

    def test_hook_no_longer_indexes_transcripts(self):
        import inspect
        from hooks.context_health_hook import ContextHealthHook
        assert not hasattr(ContextHealthHook, "_sync_transcript_index")
        assert "transcript_index" not in inspect.getsource(ContextHealthHook._light_refresh)

    def test_v13_migration_drops_the_tables(self, tmp_path):
        import asyncio
        from database.sqlite import SQLiteDatabase

        db_file = tmp_path / "t.db"
        asyncio.run(SQLiteDatabase(str(db_file)).initialize())
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE transcript_chunks(id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("CREATE VIRTUAL TABLE transcript_fts USING fts5("
                     "content, content=transcript_chunks, content_rowid=id)")
        conn.execute("PRAGMA user_version = 12")
        conn.commit()
        conn.close()

        asyncio.run(SQLiteDatabase(str(db_file)).initialize())
        conn = sqlite3.connect(str(db_file))
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN "
            "('transcript_chunks', 'transcript_fts', 'transcript_chunks_index_state')")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert names == set(), names
        assert version == 19   # v13 dropped them; v14-v19 chain on


# ── W2: select_memory_sections applies temporal weight ───────────────


class TestW2TemporalWeightInScoring:
    """Verify _entry_temporal_weight is called during section scoring."""

    def test_body_scorer_strips_superseded_entries(self):
        """NEW ARCHITECTURE (2026-08-14): the index-based _keyword_section_scores
        was DELETED. Superseded handling now lives in the body-BM25 scorer
        (_section_body_scores), which STRIPS superseded entries from each section
        body BEFORE scoring (via _strip_superseded_entries) — stronger than the old
        0.1x SUPERSEDED_WEIGHT down-weight. Asserts the BEHAVIOR: a section whose
        only query-matching entry is superseded scores 0 (stripped)."""
        from core.memory_index import _section_body_scores
        sections = {
            "Guidelines": (
                "- [GUI01] **zephyr quartz unique-token lesson** — detail\n"
                "  <!-- ref:0 | last:none | decay:active -->\n"
            ),
        }
        # active: the entry is scored → non-empty
        active = _section_body_scores("zephyr quartz", sections, superseded_keys=set())
        assert active.get("Guidelines", 0) > 0
        # superseded: GUI01 stripped before scoring → section no longer matches
        stripped = _section_body_scores("zephyr quartz", sections, superseded_keys={"GUI01"})
        assert stripped.get("Guidelines", 0) == 0

    def test_superseded_entry_scores_lower(self):
        """An entry with superseded_by metadata should score lower."""
        from core.memory_index import _entry_temporal_weight

        # Active entry
        active_weight = _entry_temporal_weight(
            "- [KD01] 2026-04-01 Decision text\n"
            "  <!-- valid_from: 2026-04-01 | superseded_by: null | confidence: high -->"
        )
        # Superseded entry
        superseded_weight = _entry_temporal_weight(
            "- [KD02] 2026-03-15 Old decision\n"
            "  <!-- valid_from: 2026-03-15 | superseded_by: KD01 | confidence: high -->"
        )

        assert active_weight == pytest.approx(1.0)
        assert superseded_weight == pytest.approx(0.1)
        assert superseded_weight < active_weight


# ── W3: distillation_hook adds valid_from ────────────────────────────




# ── W4: memory_health uses mark_entry_superseded ─────────────────────


# ── W5: prompt_builder RecallEngine gets additional_stores ───────────


class TestW5RecallFallbackStores:
    """The legacy fallback recall reads the library only: no transcript store, and
    no MemoryRecallStore (it read a memory_entries table nothing writes; design
    2026-10-26 D4 -- live MEMORY is the unified path's context_files leg)."""

    def test_recall_for_query_has_no_dead_stores(self):
        import inspect
        from core.session_router import _recall_for_query
        source = inspect.getsource(_recall_for_query)
        assert "TranscriptStore" not in source and "transcript_indexer" not in source
        assert "memory_recall_store" not in source
