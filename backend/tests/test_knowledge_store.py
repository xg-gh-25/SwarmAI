"""Tests for knowledge_store.py — Library Indexing (Phase 1).

Tests chunking, delta-sync, FTS5 search, and table management for the
Knowledge/ directory indexing system.
"""

import hashlib
import sqlite3
import textwrap

import pytest


# ── Helper: create an in-memory SQLite connection with sqlite-vec loaded ──

def _make_conn():
    """Create an in-memory SQLite conn with sqlite-vec loaded."""
    conn = sqlite3.connect(":memory:")
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not installed")
    return conn


# ── chunk_markdown ──

class TestChunkMarkdown:
    """Test markdown chunking by heading."""

    def test_single_section(self):
        from core.knowledge_store import chunk_markdown

        md = textwrap.dedent("""\
        # Title

        Some content here.
        More content.
        """)
        chunks = chunk_markdown(md, "test.md")
        assert len(chunks) >= 1
        assert chunks[0]["content"].strip() != ""

    def test_multiple_h2_sections(self):
        from core.knowledge_store import chunk_markdown

        md = textwrap.dedent("""\
        # Main Title

        Intro text.

        ## Section One

        Content of section one.

        ## Section Two

        Content of section two.
        """)
        chunks = chunk_markdown(md, "test.md")
        # Should produce at least 2 chunks (one per ## heading)
        assert len(chunks) >= 2
        headings = [c["heading"] for c in chunks if c.get("heading")]
        assert any("Section One" in h for h in headings)
        assert any("Section Two" in h for h in headings)

    def test_archive_file_chunks_at_bullet_level(self):
        """run_3cb6b9ae Cycle-4 (#4): a *-archive*.md file's size-archived section
        (many `- [type] **title**` bullets under one `## Section (size-archived DATE)`
        heading) must chunk PER-ENTRY, not collapse into one section blob — else an
        archived entry is recallable only as a coarse section-wide chunk, losing the
        per-entry precision the live recall slicer provides."""
        from core.knowledge_store import chunk_markdown

        md = textwrap.dedent("""\
        # Memory Archive -- 2026-08

        ## Guidelines (size-archived 2026-08-14)

        - [guideline] **First archived rule** — body one about caching prefixes (2026-07-01)
          <!-- ref:0 | last:2026-07-01 | decay:active | source:auto -->
        - [guideline] **Second archived rule** — body two about session routing (2026-07-02)
          <!-- ref:0 | last:2026-07-02 | decay:active | source:auto -->
        - [guideline] **Third archived rule** — body three about token budgets (2026-07-03)
          <!-- ref:0 | last:2026-07-03 | decay:active | source:auto -->
        """)
        chunks = chunk_markdown(md, ".context/MEMORY-archive-2026-08.md")
        # Each of the 3 bullet entries should be its own chunk (not 1 section blob).
        entry_chunks = [c for c in chunks if "archived rule" in c["content"]]
        assert len(entry_chunks) >= 3, (
            f"archive not chunked per-entry: got {len(entry_chunks)} entry chunks "
            f"(collapsed into a section blob?)"
        )
        # A specific entry must be recallable as its OWN chunk (content isolated).
        second = [c for c in chunks if "Second archived rule" in c["content"]]
        assert len(second) == 1, "the specific entry is not an isolated chunk"
        assert "First archived rule" not in second[0]["content"], (
            "entry chunk bleeds into neighbors (not truly per-entry)"
        )
        # Each entry chunk carries its metadata line (span kept whole).
        assert "<!-- ref:" in second[0]["content"], "entry chunk lost its metadata"

    def test_non_archive_file_still_chunks_by_heading(self):
        """Regression: a NON-archive file must keep section-level chunking (the
        bullet-level split is archive-only, no ceremony tax elsewhere)."""
        from core.knowledge_store import chunk_markdown
        md = textwrap.dedent("""\
        ## Section One
        - bullet a
        - bullet b
        ## Section Two
        - bullet c
        """)
        chunks = chunk_markdown(md, "Notes/regular.md")
        # section-level: 2 chunks (one per heading), bullets NOT split out
        assert len(chunks) == 2, f"non-archive file over-chunked: {len(chunks)}"

    def test_archive_wrapped_bracket_line_not_phantom_chunk(self):
        """Gate-2 MED (run_3cb6b9ae): a col-0 `[see also](url)` continuation inside an
        archived entry body must NOT phantom-split into its own chunk — the entry-start
        bracket rule is narrowed to a lowercase `[type]` tag, not any `[Letter`."""
        from core.knowledge_store import chunk_markdown
        md = textwrap.dedent("""\
        # Memory Archive -- 2026-08

        ## Guidelines (size-archived 2026-08-14)

        - [guideline] **Wrapped rule** — body one, see the reference (2026-07-01)
        [see also](http://example.com) this is a CONTINUATION not an entry
          <!-- ref:0 | last:2026-07-01 | decay:active | source:auto -->
        - [guideline] **Second rule** — body two (2026-07-02)
          <!-- ref:0 | last:2026-07-02 | decay:active | source:auto -->
        """)
        chunks = chunk_markdown(md, ".context/MEMORY-archive-2026-08.md")
        # The `[see also]` line must stay with its parent entry, not be its own chunk.
        phantom = [c for c in chunks if c["content"].lstrip().startswith("## ")
                   and "see also" in c["content"].split("\n", 1)[-1].lstrip()[:12]]
        assert not phantom, "wrapped [see also] line phantom-split into its own chunk"
        wrapped = [c for c in chunks if "Wrapped rule" in c["content"]]
        assert len(wrapped) == 1 and "see also" in wrapped[0]["content"], (
            "the continuation line was severed from its parent entry"
        )

    def test_incidental_archive_path_not_bullet_chunked(self):
        """Gate-2 MED: `-archive` as an incidental path substring (not an archive
        FILENAME) must NOT trigger bullet-chunking."""
        from core.knowledge_store import chunk_markdown
        md = textwrap.dedent("""\
        ## Tasks
        - task a
        - task b
        """)
        # incidental "-archive" in a DIR name, not the archive filename pattern
        chunks = chunk_markdown(md, "Notes/design-archive-notes/live.md")
        assert len(chunks) == 1, f"incidental -archive path over-chunked: {len(chunks)}"

    def test_daily_activity_format(self):
        """DailyActivity files use ## HH:MM | session_id format."""
        from core.knowledge_store import chunk_markdown

        md = textwrap.dedent("""\
        ## 15:06 | abc12345 | Working on memory system

        **What happened:**
        - Built knowledge_store.py
        - Added FTS5 support

        ## 16:30 | def67890 | Fixed a bug

        **What happened:**
        - Fixed the delta sync
        """)
        chunks = chunk_markdown(md, "DailyActivity/2026-04-01.md")
        assert len(chunks) >= 2

    def test_preserves_source_file(self):
        from core.knowledge_store import chunk_markdown

        chunks = chunk_markdown("# Hello\nWorld", "Notes/test.md")
        assert all(c["source_file"] == "Notes/test.md" for c in chunks)

    def test_content_hash_deterministic(self):
        from core.knowledge_store import chunk_markdown

        chunks1 = chunk_markdown("# Foo\nBar", "test.md")
        chunks2 = chunk_markdown("# Foo\nBar", "test.md")
        assert chunks1[0]["content_hash"] == chunks2[0]["content_hash"]

    def test_empty_file_returns_empty(self):
        from core.knowledge_store import chunk_markdown

        chunks = chunk_markdown("", "empty.md")
        assert chunks == []


# ── KnowledgeStore table management ──

class TestKnowledgeStore:
    """Test KnowledgeStore table creation and sync."""

    def test_ensure_tables_creates_all(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # Verify tables exist
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
        ).fetchall()}
        assert "knowledge_chunks" in tables
        assert "knowledge_fts" in tables

    def test_upsert_chunk(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        store.upsert_chunk(
            source_file="Notes/test.md",
            chunk_index=0,
            heading="## Test",
            content="Hello world",
            content_hash="abc123",
            metadata={"date": "2026-04-01"},
        )

        rows = conn.execute("SELECT * FROM knowledge_chunks").fetchall()
        assert len(rows) == 1

    def test_delta_sync_skips_unchanged(self):
        """Delta sync should skip chunks with same content_hash."""
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        content_hash = hashlib.sha256(b"Hello world").hexdigest()
        store.upsert_chunk("test.md", 0, "## T", "Hello world", content_hash)

        # Get existing hashes
        existing = store.get_existing_hashes("test.md")
        assert existing.get(0) == content_hash

    def test_remove_stale_chunks(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # Insert 3 chunks
        for i in range(3):
            store.upsert_chunk("test.md", i, f"## S{i}", f"content {i}", f"hash{i}")

        # Remove all but chunk 0
        store.remove_stale_chunks("test.md", keep_indexes={0})
        rows = conn.execute("SELECT chunk_index FROM knowledge_chunks WHERE source_file = ?", ("test.md",)).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 0

    def test_fts5_search(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        store.upsert_chunk("Notes/cred.md", 0, "## Credentials",
                          "Two credential chains coexist on this machine", "h1")
        store.upsert_chunk("Notes/other.md", 0, "## Weather",
                          "The weather is nice today", "h2")

        results = store.fts5_search("credential chains")
        assert len(results) >= 1
        assert results[0]["source_file"] == "Notes/cred.md"

    def test_fts5_search_no_results(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        results = store.fts5_search("nonexistent query xyz")
        assert results == []

    def test_remove_file_entries(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        store.upsert_chunk("delete_me.md", 0, "## D", "content", "h1")
        store.remove_file_entries("delete_me.md")
        rows = conn.execute("SELECT * FROM knowledge_chunks WHERE source_file = ?", ("delete_me.md",)).fetchall()
        assert len(rows) == 0


# ── sync_knowledge_index (integration-level) ──

class TestSyncKnowledgeIndex:
    """Test the top-level sync function with a real directory."""

    def test_sync_indexes_md_files(self, tmp_path):
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        # Create a mini Knowledge/ dir
        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        (notes_dir / "test-note.md").write_text("# Test Note\n\nSome content about testing.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        stats = sync_knowledge_index(store, knowledge_dir)
        assert stats["files_scanned"] >= 1
        assert stats["chunks_added"] >= 1

    def test_sync_indexes_archives_but_skips_jobresults(self, tmp_path):
        """DoD4 (pure-filesystem recall design §3.2/§5.8): Archives/*.md IS now
        indexed (long-term memory reachable by recall), BUT JobResults* flow-log
        subdirs nested under Archives are EXCLUDED (time-series noise)."""
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        archives = knowledge_dir / "Archives"
        archives.mkdir(parents=True)
        # A real archived-memory file → MUST be indexed
        (archives / "MEMORY-archive-2026-04.md").write_text(
            "# Archive\n\n## Old Decision\n\nzebrafish-marker unique archived memory."
        )
        # A job flow-log nested dir → MUST be skipped
        joblog = archives / "JobResults-2026Q1"
        joblog.mkdir()
        (joblog / "2026-01-01-scan.md").write_text(
            "# Job\n\n## Result\n\nzebrafish-marker but this is flow-log noise."
        )

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()
        sync_knowledge_index(store, knowledge_dir)

        # The archived memory is findable; the job log is not.
        indexed = {r["source_file"] for r in store.fts5_search("zebrafish-marker", limit=10)}
        assert any("MEMORY-archive-2026-04" in s for s in indexed), \
            "archived memory must be indexed (the real recall gap this fixes)"
        assert not any("JobResults" in s for s in indexed), \
            "JobResults flow-logs must be excluded (design §5.8 noise filter)"

    def test_sync_indexes_context_memory_archives_but_not_active_memory(self, tmp_path):
        """CYCLE 1' (privacy partition): MEMORY archives now live in the gitignored
        .context/ (a SIBLING of Knowledge/), not the git-tracked Knowledge/Archives/.
        Recall MUST reach them (else the 'recall can't see archived memory' gap
        reopens the moment archives move) — AND must NEVER index the ACTIVE private
        docs (MEMORY.md/USER.md/EVOLUTION.md), which are already full-injected."""
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        (knowledge_dir / "Notes").mkdir(parents=True)
        ctx = tmp_path / ".context"
        ctx.mkdir()
        # A memory archive in .context/ → MUST be indexed (recall coverage)
        (ctx / "MEMORY-archive-2026-08.md").write_text(
            "# Memory Archive\n\n### Archived Recent Context\n\n- quokka-sentinel archived-memory phrase."
        )
        # ACTIVE private docs → MUST NOT be indexed (they're full-injected already)
        (ctx / "MEMORY.md").write_text("# MEMORY\n\n- quokka-sentinel active memory (must NOT be recalled).")
        (ctx / "USER.md").write_text("# USER\n\n- quokka-sentinel user profile (must NOT be recalled).")
        (ctx / "EVOLUTION.md").write_text("# EVOLUTION\n\n- quokka-sentinel evolution (must NOT be recalled).")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()
        sync_knowledge_index(store, knowledge_dir)

        indexed = {r["source_file"] for r in store.fts5_search("quokka-sentinel", limit=10)}
        assert any("MEMORY-archive-2026-08" in s for s in indexed), \
            ".context/ memory archive must be recall-indexed (privacy-partition coverage)"
        assert not any(
            s.endswith("MEMORY.md") or s.endswith("USER.md") or s.endswith("EVOLUTION.md")
            for s in indexed
        ), "active private docs must NEVER be recall-indexed"
        # source_file keeps 'Archives' semantics (memory_chain_probe invariant)
        assert any("Archives" in s for s in indexed if "MEMORY-archive-2026-08" in s)

    def test_sync_indexes_all_context_archives_not_just_memory(self, tmp_path):
        """STEP2 (unified retrieval): the privacy-partition coverage must span ALL
        .context/ archives — EVOLUTION-archive.md included — not just MEMORY-archive*.
        A parallel session sediments EVOLUTION-archive.md; recall MUST reach it too.
        The '*-archive*.md' glob still fail-closed-excludes ACTIVE docs (MEMORY.md /
        EVOLUTION.md / USER.md have no '-archive' infix)."""
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        (knowledge_dir / "Notes").mkdir(parents=True)
        ctx = tmp_path / ".context"
        ctx.mkdir()
        # BOTH archive families in .context/ → MUST be indexed (recall coverage)
        (ctx / "MEMORY-archive-2026-08.md").write_text(
            "# Memory Archive\n\n### Archived\n\n- axolotl-memory archived phrase."
        )
        (ctx / "EVOLUTION-archive.md").write_text(
            "# Evolution Archive\n\n### Archived Corrections\n\n- axolotl-evolution archived correction phrase."
        )
        # ACTIVE private docs → MUST NOT be indexed (full-injected already)
        (ctx / "MEMORY.md").write_text("# MEMORY\n\n- axolotl-active must NOT be recalled.")
        (ctx / "EVOLUTION.md").write_text("# EVOLUTION\n\n- axolotl-active must NOT be recalled.")
        (ctx / "USER.md").write_text("# USER\n\n- axolotl-active must NOT be recalled.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()
        sync_knowledge_index(store, knowledge_dir)

        mem = {r["source_file"] for r in store.fts5_search("axolotl-memory", limit=10)}
        evo = {r["source_file"] for r in store.fts5_search("axolotl-evolution", limit=10)}
        active = {r["source_file"] for r in store.fts5_search("axolotl-active", limit=10)}
        assert any("MEMORY-archive-2026-08" in s for s in mem), \
            ".context/ MEMORY archive must be recall-indexed"
        assert any("EVOLUTION-archive" in s for s in evo), \
            ".context/ EVOLUTION archive must be recall-indexed (STEP2 unified coverage)"
        assert not active, "active private docs (MEMORY/EVOLUTION/USER.md) must NEVER be recall-indexed"

    def test_context_and_knowledge_same_month_archive_no_collision(self, tmp_path):
        """Gate-2 HIGH regression: a legacy Knowledge/Archives/MEMORY-archive-
        YYYY-MM.md and a new .context/MEMORY-archive-YYYY-MM.md of the SAME basename
        must BOTH be indexed — they must NOT collide on one current_files key (which
        would silently drop one from recall + thrash chunks). The private partition
        uses a distinct '.context/Archives/' rel_path prefix to keep them disjoint."""
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        legacy = knowledge_dir / "Archives"
        legacy.mkdir(parents=True)
        ctx = tmp_path / ".context"
        ctx.mkdir()
        # SAME basename in both dirs, distinct content
        (legacy / "MEMORY-archive-2026-08.md").write_text(
            "# Archive\n\n- legacy-narwhal phrase in the git-tracked archive."
        )
        (ctx / "MEMORY-archive-2026-08.md").write_text(
            "# Memory Archive\n\n- private-pangolin phrase in the .context archive."
        )

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()
        sync_knowledge_index(store, knowledge_dir)

        legacy_hits = {r["source_file"] for r in store.fts5_search("legacy-narwhal", limit=10)}
        private_hits = {r["source_file"] for r in store.fts5_search("private-pangolin", limit=10)}
        assert any("MEMORY-archive-2026-08" in s for s in legacy_hits), \
            "legacy Knowledge/Archives entry dropped (collision) — must survive"
        assert private_hits, ".context/ entry must be indexed"
        # The two must have DISTINCT source_file keys (no overwrite)
        assert legacy_hits != private_hits
        assert all("Archives" in s for s in (legacy_hits | private_hits)), \
            "both keep 'Archives' in source_file (memory_chain_probe invariant)"

    def test_sync_delta_skips_unchanged(self, tmp_path):
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        (notes_dir / "test.md").write_text("# Test\n\nContent.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # First sync
        stats1 = sync_knowledge_index(store, knowledge_dir)
        # Second sync (no changes)
        stats2 = sync_knowledge_index(store, knowledge_dir)
        assert stats2["chunks_skipped"] >= stats1["chunks_added"]
        assert stats2["chunks_added"] == 0

    def test_sync_removes_deleted_files(self, tmp_path):
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        test_file = notes_dir / "test.md"
        test_file.write_text("# Test\n\nContent.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        sync_knowledge_index(store, knowledge_dir)
        # Delete file
        test_file.unlink()
        stats = sync_knowledge_index(store, knowledge_dir)
        assert stats["files_removed"] >= 1

    def test_deadline_defers_remaining_files(self, tmp_path):
        """A past deadline stops the per-file loop before any work, deferring
        the rest to the next session (context_health 30s-timeout false-alarm
        fix)."""
        import time
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        for i in range(5):
            (notes_dir / f"n{i}.md").write_text(f"# Note {i}\n\nContent {i}.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # Deadline already in the past → loop breaks on the first file.
        stats = sync_knowledge_index(
            store, knowledge_dir,
            deadline=time.monotonic() - 1.0,
        )
        assert stats["files_scanned"] == 0
        assert stats["deferred"] == 5
        assert stats["chunks_added"] == 0

    def test_no_deadline_processes_all(self, tmp_path):
        """Regression: deadline=None (default) must process every file —
        the budget gate is opt-in, never changes the unbounded behavior."""
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        for i in range(4):
            (notes_dir / f"n{i}.md").write_text(f"# Note {i}\n\nContent {i}.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        stats = sync_knowledge_index(store, knowledge_dir)
        assert stats["files_scanned"] == 4
        assert stats["deferred"] == 0


# ── knowledge_fts corruption fix (run_1d198980) ──

class TestFtsCorruptionFix:
    """The external-content FTS5 index desynced because upsert_chunk's update
    branch bound the NEW content to the FTS5 'delete' command instead of the OLD
    stored values. FTS5 needs the OLD values to reverse posting lists; new!=old
    progressively corrupts the index → 'database disk image is malformed'."""

    def _query_fts(self, store, term):
        """Drive the real fts5_search rank path (the one that goes malformed)."""
        return store.fts5_search(term, limit=5)

    def test_double_content_update_keeps_fts_queryable(self):
        """AC1 (root-cause regression): updating a chunk's content TWICE must
        leave the FTS index queryable. Under the old code (delete bound NEW
        values) the posting lists desync; the query path eventually corrupts."""
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # Same (source_file, chunk_index) → exercises the UPDATE branch twice.
        for i, body in enumerate(["alpha beta gamma", "delta epsilon zeta",
                                   "eta theta iota kappa"]):
            store.upsert_chunk(source_file="Notes/x.md", chunk_index=0,
                               heading="## H", content=body,
                               content_hash=f"h{i}")

        # The index must reflect the LATEST content and stay queryable.
        hits = self._query_fts(store, "theta")      # latest body term → should hit
        assert any("theta" in h.get("content", "") for h in hits), \
            "latest content must be findable after repeated updates"
        # Stale terms from overwritten versions must NOT linger as phantom hits.
        stale = self._query_fts(store, "alpha")
        assert not any(h.get("content") == "alpha beta gamma" for h in stale), \
            "overwritten content must be removed from the index (no posting desync)"

    def test_repair_fts_index_restores_after_corruption(self):
        """AC2: repair_fts_index() rebuilds from the content table; data-loss-free."""
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()
        store.upsert_chunk(source_file="Notes/y.md", chunk_index=0,
                           heading="## H", content="quokka wombat",
                           content_hash="h0")
        before = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]

        store.repair_fts_index()  # must not raise; rebuilds from content

        after = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
        assert after == before, "repair must be data-loss-free"
        hits = store.fts5_search("quokka", limit=5)
        assert len(hits) >= 1, "content is findable after repair"

    def test_fts_health_probe_distinguishes_healthy(self):
        """AC5: a healthy index reports healthy (probe doesn't false-positive)."""
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()
        store.upsert_chunk(source_file="Notes/z.md", chunk_index=0,
                           heading="## H", content="narwhal", content_hash="h0")
        assert store._fts_is_healthy() is True

    def test_fts_health_probe_DETECTS_corruption(self):
        """AC5 (the probe's whole point): a desynced index must report UNhealthy.
        Regression for the weak-probe bug (run_1d198980): a no-match probe term
        short-circuits before touching posting lists → false 'healthy'. The probe
        must use a REAL stored term so it traverses the corrupt structure."""
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()
        store.upsert_chunk(source_file="n.md", chunk_index=0, heading="H",
                           content="alpha beta gamma", content_hash="h0")
        assert store._fts_is_healthy() is True

        # Desync the external-content index: 'delete' with values never indexed.
        conn.execute(
            "INSERT INTO knowledge_fts(knowledge_fts, rowid, content, heading, source_file) "
            "VALUES('delete', 1, 'zzz nonexistent qqq', '', 'n.md')"
        )
        assert store._fts_is_healthy() is False, \
            "probe must DETECT a desynced index (must query a real term, not a no-match)"

        # And repair recovers it.
        store.repair_fts_index()
        assert store._fts_is_healthy() is True
