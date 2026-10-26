#!/usr/bin/env python3
"""probe_knowledge — product eval probes for the knowledge library index.

Contract: scripts/_probe_kit.py. Subcommands:
  sync_safe  syncing while Knowledge/ is temporarily missing deletes nothing from the
             index, and mounted documents survive a sync of Knowledge/
  fts_heal   a desynchronised full-text index is detected and repaired with no data loss
  mount      a mounted docs folder is searchable, and unmounting removes it
"""
from __future__ import annotations

import hashlib
import sqlite3
from unittest import mock

from scripts._probe_kit import run, tempdir, verdict


def _store():
    from core.knowledge_store import KnowledgeStore

    store = KnowledgeStore(sqlite3.connect(":memory:"))
    store.ensure_tables()
    return store


def _put(store, source, text):
    store.upsert_chunk(source_file=source, chunk_index=0, heading="H", content=text,
                       content_hash=hashlib.sha256(text.encode()).hexdigest())


# ── sync_safe ─────────────────────────────────────────────────────────────────

def _sync_holds(knowledge_present: bool) -> tuple[bool, str]:
    from core.knowledge_store import sync_knowledge_index

    with tempdir() as d:
        store = _store()
        _put(store, "Notes/kept.md", "zircon note that was indexed earlier")
        _put(store, "mount:abc/x.md", "zircon mounted document")
        kdir = d / "Knowledge"
        if knowledge_present:  # broken input: an existing but empty Knowledge/ (prune is live)
            kdir.mkdir()
        sync_knowledge_index(store, kdir)
        left = {h["source_file"] for h in store.fts5_search("zircon", limit=50)}
    if "Notes/kept.md" not in left:
        return False, "the index lost entries while Knowledge/ was missing"
    if "mount:abc/x.md" not in left:
        return False, "a sync removed a mounted document"
    return True, ""


def sync_safe(negative: bool) -> int:
    return verdict("KNW_SYNC_SAFE", negative, lambda: _sync_holds(False),
                   lambda: _sync_holds(True))


# ── fts_heal ──────────────────────────────────────────────────────────────────

def _heal_holds(repair: bool) -> tuple[bool, str]:
    store = _store()
    _put(store, "n.md", "alpha beta gamma")
    conn = store._conn
    n_before = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
    # Desynchronise the external-content index: a 'delete' with values never indexed.
    conn.execute("INSERT INTO knowledge_fts(knowledge_fts, rowid, content_seg, heading_seg, "
                 "source_file_seg) VALUES('delete', 1, 'zzz nonexistent qqq', '', 'n.md')")
    if store._fts_is_healthy():
        return False, "a desynchronised index was reported healthy"
    if repair:
        store.repair_fts_index()
    if not store._fts_is_healthy():
        return False, "the index is still unhealthy after repair"
    n_after = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
    if n_after != n_before or not store.fts5_search("alpha", limit=5):
        return False, "repair lost data"
    return True, ""


def fts_heal(negative: bool) -> int:
    return verdict("KNW_FTS_HEAL", negative, lambda: _heal_holds(True),
                   lambda: _heal_holds(False))


# ── mount ─────────────────────────────────────────────────────────────────────

def _mount_holds(text_file: bool) -> tuple[bool, str]:
    from core.knowledge_store import KnowledgeStore
    from core.library_mounts import LibraryMounts, index_docs_mount
    from core.vec_db import open_vec_db
    from jobs.paths import DB_PATH

    marker = "MOUNTED_DOC_MARKER_7f3"
    with tempdir() as d:
        docs = d / "docs"
        docs.mkdir()
        if text_file:
            (docs / "a.md").write_text(f"# Doc\n\nThe {marker} lives here.\n", encoding="utf-8")
        else:
            (docs / "a.bin").write_bytes(b"\xff\xfe\x00\x81" * 64)
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        mounts = LibraryMounts(conn)
        mounts.ensure_table()
        mid = mounts.add_mount(scope="global", path=str(docs), kind="docs")
        index_docs_mount(mounts, mid)

        def hits():
            with open_vec_db() as c:
                ks = KnowledgeStore(c)
                ks.ensure_tables()
                return [h for h in ks.fts5_search(marker, limit=5)
                        if h.get("source_file", "").startswith(f"mount:{mid}/")]
        found = hits()
        mounts.delete_mount(mid)
        after = hits()
        conn.close()
    if not found:
        return False, "a mounted docs folder is not searchable"
    if after:
        return False, "unmounting left the documents searchable"
    return True, ""


def mount(negative: bool) -> int:
    return verdict("KNW_MOUNT", negative, lambda: _mount_holds(True),
                   lambda: _mount_holds(False))


if __name__ == "__main__":
    raise SystemExit(run({"sync_safe": sync_safe, "fts_heal": fts_heal, "mount": mount}))
