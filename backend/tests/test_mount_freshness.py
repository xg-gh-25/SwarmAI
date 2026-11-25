"""Cycle 7 — mount freshness: source-mtime vs last-indexed → fresh / stale / missing.

check_health so far only did the source-EXISTS probe (fresh|missing). The
freshness leg adds 'stale': the source still exists but was edited AFTER the last
index (source max-mtime > last_synced). A stale mount's briefing/graph is an older
snapshot — recall still lands on the pointer (agent reads the LIVE source), so
stale only lowers HIT probability, never returns wrong content (design trade-off).

refresh_all_mounts() is the light periodic job body: re-probe every mount, persist
the health, return a summary. Never raises on a bad mount.

Properties:
  1. freshly-synced mount whose source is untouched → fresh
  2. NEGATIVE: source edited after last_synced → stale
  3. NEGATIVE: source deleted → missing (exists-probe wins over stale)
  4. never-synced mount (no last_synced) whose source exists → fresh (nothing to
     be stale against yet)
  5. refresh_all_mounts summarizes {fresh, stale, missing} and never crashes
  6. with reindex (the job), a mount whose files changed is re-indexed in a child,
     an unchanged, disabled or already-indexing one is not, and a mount whose kind
     judges differently now is switched and re-indexed under the new kind
  7. an index that found nothing is recorded: the unchanged source reads fresh
     (no re-index on every sweep), the next change reads stale again
  8. edits no indexer reads (.DS_Store, .git, node_modules) do not make a mount stale
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from core.library_mounts import LibraryMounts, refresh_all_mounts


@pytest.fixture
def store() -> LibraryMounts:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    s = LibraryMounts(conn)
    s.ensure_table()
    return s


def _touch_after(path: Path, seconds_ahead: int = 5) -> None:
    """Force path's mtime to the future so it's unambiguously > last_synced."""
    future = time.time() + seconds_ahead
    os.utime(path, (future, future))


def test_untouched_source_is_fresh(tmp_path: Path, store: LibraryMounts) -> None:
    d = tmp_path / "docs"; d.mkdir(); (d / "a.md").write_text("x")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    store.mark_synced(mid, index_ref="ref")
    assert store.check_health(mid) == "fresh"


def test_edited_source_goes_stale(tmp_path: Path, store: LibraryMounts) -> None:
    """NEGATIVE: an edit after last_synced flips the mount to stale."""
    d = tmp_path / "docs"; d.mkdir()
    f = d / "a.md"; f.write_text("x")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    store.mark_synced(mid, index_ref="ref")
    # Edit the source AFTER indexing (future mtime removes any clock-granularity flake).
    f.write_text("x edited")
    _touch_after(f, 5)
    assert store.check_health(mid) == "stale"
    assert store.get_mount(mid)["health"] == "stale"


def test_deleted_source_is_missing_not_stale(tmp_path: Path, store: LibraryMounts) -> None:
    """NEGATIVE: exists-probe wins — a deleted source is 'missing', never 'stale'."""
    d = tmp_path / "docs"; d.mkdir(); f = d / "a.md"; f.write_text("x")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    store.mark_synced(mid, index_ref="ref")
    f.unlink(); d.rmdir()
    assert store.check_health(mid) == "missing"


def test_never_synced_but_present_is_fresh(tmp_path: Path, store: LibraryMounts) -> None:
    """A mount added but not yet indexed (no last_synced) is fresh if the source
    exists — nothing to be stale against yet."""
    d = tmp_path / "docs"; d.mkdir()
    mid = store.add_mount(scope="S", path=str(d), kind="docs")  # no mark_synced
    assert store.get_mount(mid)["last_synced"] is None
    assert store.check_health(mid) == "fresh"


def test_refresh_all_mounts_summary(tmp_path: Path, store: LibraryMounts) -> None:
    fresh_d = tmp_path / "fresh"; fresh_d.mkdir()
    stale_d = tmp_path / "stale"; stale_d.mkdir()
    sf = stale_d / "a.md"; sf.write_text("x")
    gone_d = tmp_path / "gone"; gone_d.mkdir()

    mf = store.add_mount(scope="S", path=str(fresh_d), kind="docs"); store.mark_synced(mf)
    ms = store.add_mount(scope="S", path=str(stale_d), kind="docs"); store.mark_synced(ms)
    mg = store.add_mount(scope="S", path=str(gone_d), kind="docs"); store.mark_synced(mg)

    _touch_after(sf, 5)  # stale one edited after sync
    gone_d.rmdir()       # missing one

    summary = refresh_all_mounts(store)
    assert summary["fresh"] == 1
    assert summary["stale"] == 1
    assert summary["missing"] == 1
    assert summary["scanned"] == 3


# ── 6-8: the sweep keeps a mount's index current ─────────────────────────────

@pytest.fixture
def spawned(monkeypatch, tmp_path):
    import core.code_intel.build as build
    import core.library_mounts as lm
    calls: list = []
    monkeypatch.setattr(build, "spawn_child", lambda args: calls.append(args))
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    monkeypatch.setattr(lm, "_remove_mount_knowledge_chunks", lambda mid: calls.append(["rm", mid]) or 0)
    return calls


def _docs(tmp_path: Path, name: str = "notes") -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "a.md").write_text("# a\nsome text\n")
    return d


def _current(store, mid):
    """An index of today's docs version has landed."""
    import core.library_mounts as lm
    store.mark_synced(mid, index_version=lm.DOCS_INDEX_VERSION)


def test_sweep_reindexes_a_changed_docs_mount(tmp_path, store, spawned):
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); _current(store, mid)
    summary = refresh_all_mounts(store, reindex=True)
    assert spawned == [] and "reindexing" not in summary       # unchanged: nothing to do
    _touch_after(d / "a.md")
    summary = refresh_all_mounts(store, reindex=True)
    assert spawned == [["-m", "core.library_mounts", "index-docs", mid]]
    assert summary["reindexing"] == 1


def test_sweep_reindexes_a_changed_code_mount(tmp_path, store, spawned):
    d = tmp_path / "repo"; d.mkdir()
    (d / "pyproject.toml").write_text("[project]\nname='x'\n")
    (d / "a.py").write_text("def a():\n    return 1\n")
    mid = store.add_mount(scope="S", path=str(d), kind="code"); store.mark_synced(mid)
    _touch_after(d / "a.py")
    refresh_all_mounts(store, reindex=True)
    assert spawned == [["-m", "core.library_mounts", "index-code", mid]]


def test_sweep_without_reindex_only_probes(tmp_path, store, spawned):
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); store.mark_synced(mid)
    _touch_after(d / "a.md")
    assert refresh_all_mounts(store)["stale"] == 1
    assert spawned == []


def test_sweep_skips_a_disabled_mount(tmp_path, store, spawned):
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); store.mark_synced(mid)
    store.set_enabled(mid, False)
    _touch_after(d / "a.md")
    refresh_all_mounts(store, reindex=True)
    assert spawned == []


def test_sweep_does_not_start_a_second_index_behind_a_running_one(tmp_path, store, spawned):
    import core.library_mounts as lm
    from core.code_intel.build import write_lock
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); store.mark_synced(mid)
    _touch_after(d / "a.md")
    import subprocess, sys, textwrap
    lock = lm._index_lock_path(mid, "docs")
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import fcntl, sys, time
        fh = open({str(lock) + '.write.lock'!r}, 'a+'); fcntl.flock(fh, fcntl.LOCK_EX)
        print('held', flush=True); time.sleep(30)
    """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        refresh_all_mounts(store, reindex=True)
        assert spawned == []
    finally:
        holder.kill(); holder.wait()
    refresh_all_mounts(store, reindex=True)                    # lock free: it runs
    assert spawned == [["-m", "core.library_mounts", "index-docs", mid]]


def test_sweep_switches_a_mount_whose_kind_judges_differently(tmp_path, store, spawned):
    # a folder of documents with a stray script, registered as code by an older judge
    d = _docs(tmp_path, "decks")
    (d / "tool.py").write_text("print(1)\n")
    mid = store.add_mount(scope="S", path=str(d), kind="code"); store.mark_synced(mid)
    summary = refresh_all_mounts(store, reindex=True)
    assert store.get_mount(mid)["kind"] == "docs"
    assert summary["rejudged"] == 1
    assert spawned == [["-m", "core.library_mounts", "index-docs", mid]]
    # docs -> code: the docs chunks are cleared before the code index
    spawned.clear()
    (d / "pyproject.toml").write_text("[project]\nname='x'\n")
    refresh_all_mounts(store, reindex=True)
    assert store.get_mount(mid)["kind"] == "code"
    assert spawned == [["rm", mid], ["-m", "core.library_mounts", "index-code", mid]]


def test_an_empty_index_is_not_retried_until_the_source_changes(tmp_path, store, spawned):
    d = tmp_path / "bin"; d.mkdir()
    f = d / "x.bin"; f.write_bytes(b"\xff\xfe\x00")
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); _current(store, mid)
    _touch_after(f)
    refresh_all_mounts(store, reindex=True)
    assert len(spawned) == 1                                   # changed: re-indexed
    future = time.time() + 10
    store._conn.execute(  # the child found nothing, after the change
        "UPDATE library_mounts SET indexed_empty_at = datetime(?, 'unixepoch') WHERE id = ?",
        (int(future), mid))
    spawned.clear()
    assert refresh_all_mounts(store, reindex=True)["fresh"] == 1
    assert spawned == []                                       # not again and again
    os.utime(f, (future + 5, future + 5))
    assert refresh_all_mounts(store, reindex=True)["stale"] == 1
    assert len(spawned) == 1


def test_index_docs_mount_records_an_empty_result(tmp_path, store, monkeypatch):
    import contextlib
    import core.library_mounts as lm
    import core.vec_db as vec_db
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    vec = sqlite3.connect(":memory:")
    monkeypatch.setattr(vec_db, "open_vec_db", lambda *a, **k: contextlib.nullcontext(vec))
    d = tmp_path / "bin"; d.mkdir(); (d / "x.bin").write_bytes(b"\xff\xfe\x00")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    assert lm.index_docs_mount(store, mid)["status"] == "indexed_empty"
    row = store.get_mount(mid)
    assert row["indexed_empty_at"] and row["last_synced"] is None


def test_edits_no_indexer_reads_do_not_make_a_mount_stale(tmp_path, store):
    d = _docs(tmp_path)
    for rel in (".DS_Store", ".git/FETCH_HEAD", "node_modules/p/index.js"):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text("x")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    future = time.time() + 5
    for rel in (".DS_Store", ".git/FETCH_HEAD", "node_modules/p/index.js", ".git", "node_modules/p", "node_modules"):
        os.utime(d / rel, (future, future))
    store.mark_synced(mid)
    assert store.check_health(mid) == "fresh"
    os.utime(d / "a.md", (future, future))
    assert store.check_health(mid) == "stale"


def test_an_old_table_gets_the_empty_index_column(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE library_mounts (id TEXT PRIMARY KEY, scope TEXT NOT NULL, path TEXT NOT NULL,"
                 " kind TEXT NOT NULL, briefing TEXT DEFAULT '', index_ref TEXT, last_synced TEXT,"
                 " health TEXT NOT NULL DEFAULT 'fresh', enabled INTEGER NOT NULL DEFAULT 1,"
                 " created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute("INSERT INTO library_mounts (id, scope, path, kind) VALUES ('m', 'S', '/x', 'docs')")
    s = LibraryMounts(conn); s.ensure_table(); s.ensure_table()
    assert s.get_mount("m")["indexed_empty_at"] is None


def test_the_freshness_job_reindexes_a_changed_mount(tmp_path, spawned, monkeypatch):
    import jobs.paths as jp
    from jobs.handlers.library_freshness import run_library_freshness
    db = tmp_path / "data.db"
    conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
    s = LibraryMounts(conn); s.ensure_table()
    d = _docs(tmp_path)
    mid = s.add_mount(scope="S", path=str(d), kind="docs"); s.mark_synced(mid)
    conn.close()
    _touch_after(d / "a.md")
    monkeypatch.setattr(jp, "DB_PATH", db)
    result = run_library_freshness()
    assert result["status"] == "success" and result["reindexing"] == 1
    assert spawned == [["-m", "core.library_mounts", "index-docs", mid]]


# ── review fixes: a waiting child is seen, a switch retries, stamps are honest ──

import contextlib
import subprocess
import sys
import textwrap


@contextlib.contextmanager
def _held(lock_base: Path):
    """Another process holds ``write_lock(lock_base)`` while the block runs."""
    lock_base.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import fcntl, time
        fh = open({str(lock_base) + '.write.lock'!r}, 'a+'); fcntl.flock(fh, fcntl.LOCK_EX)
        print('held', flush=True); time.sleep(30)
    """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        yield
    finally:
        holder.kill(); holder.wait()


def test_a_child_waiting_for_its_turn_is_seen_and_not_doubled(tmp_path, store, spawned):
    import core.library_mounts as lm
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); store.mark_synced(mid)
    _touch_after(d / "a.md")
    with _held(lm._queue_lock_path(mid)):
        refresh_all_mounts(store, reindex=True)
        assert spawned == []


def test_a_second_child_of_one_mount_exits_at_once(tmp_path, monkeypatch, capsys):
    import core.library_mounts as lm
    import jobs.paths as jp
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    monkeypatch.setattr(jp, "DB_PATH", tmp_path / "data.db")
    called = []
    monkeypatch.setattr(lm, "index_docs_mount", lambda store, mid: called.append(mid) or {"status": "indexed"})
    with _held(lm._queue_lock_path("m1")):
        assert lm.main(["index-docs", "m1"]) == 0
    assert called == [] and '"already_queued"' in capsys.readouterr().out
    assert lm.main(["index-docs", "m1"]) == 0
    assert called == ["m1"]


def test_a_mount_never_indexed_is_indexed_by_the_sweep(tmp_path, store, spawned):
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs")   # its first index failed
    refresh_all_mounts(store, reindex=True)
    assert spawned == [["-m", "core.library_mounts", "index-docs", mid]]


def test_a_kind_switch_is_retried_until_its_index_lands(tmp_path, store, spawned):
    d = _docs(tmp_path, "decks"); (d / "tool.py").write_text("print(1)\n")
    mid = store.add_mount(scope="S", path=str(d), kind="code"); store.mark_synced(mid)
    refresh_all_mounts(store, reindex=True)
    row = store.get_mount(mid)
    assert row["kind"] == "docs" and row["last_synced"] is None    # no docs index yet
    spawned.clear()
    refresh_all_mounts(store, reindex=True)                        # the child did not land
    assert spawned == [["-m", "core.library_mounts", "index-docs", mid]]


def test_no_switch_under_a_running_indexer_of_the_old_kind(tmp_path, store, spawned):
    import core.library_mounts as lm
    d = _docs(tmp_path, "decks"); (d / "tool.py").write_text("print(1)\n")
    mid = store.add_mount(scope="S", path=str(d), kind="code"); store.mark_synced(mid)
    with _held(lm._index_lock_path(mid, "code")):
        refresh_all_mounts(store, reindex=True)
    assert store.get_mount(mid)["kind"] == "code" and spawned == []


def test_an_unsure_judge_never_switches_a_mount(tmp_path, store, spawned, monkeypatch):
    import core.library_mounts as lm
    d = _docs(tmp_path, "decks")
    mid = store.add_mount(scope="S", path=str(d), kind="code"); store.mark_synced(mid)
    monkeypatch.setattr(lm, "_JUDGE_SCAN_CAP", 0)                   # the scan cannot finish
    assert lm._judge_kind(str(d)) is None and lm.judge_mount_kind(str(d)) == "docs"
    refresh_all_mounts(store, reindex=True)
    assert store.get_mount(mid)["kind"] == "code" and spawned == []


def _vec(monkeypatch):
    import core.vec_db as vec_db
    vec = sqlite3.connect(":memory:")
    monkeypatch.setattr(vec_db, "open_vec_db", lambda *a, **k: contextlib.nullcontext(vec))
    return vec


def test_a_docs_mount_that_lost_its_content_reads_not_indexed(tmp_path, store, monkeypatch):
    import core.library_mounts as lm
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    _vec(monkeypatch)
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    assert lm.index_docs_mount(store, mid)["status"] == "indexed"
    (d / "a.md").unlink(); (d / "x.bin").write_bytes(b"\xff\xfe\x00")
    assert lm.index_docs_mount(store, mid)["status"] == "indexed_empty"
    assert store.get_mount(mid)["last_synced"] is None


def test_an_edit_made_while_indexing_is_taken_next_sweep(tmp_path, store, monkeypatch):
    import core.knowledge_store as ks
    import core.library_mounts as lm
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    _vec(monkeypatch)
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    real = ks.index_text_items

    def index_then_edit(kstore, prefix, items):
        out = real(kstore, prefix, items)
        (d / "b.md").write_text("written while the index ran\n")
        return out
    monkeypatch.setattr(ks, "index_text_items", index_then_edit)
    assert lm.index_docs_mount(store, mid)["status"] == "indexed"
    assert store.check_health(mid) == "stale"


def test_index_docs_reads_the_kind_under_its_lock(tmp_path, store, monkeypatch):
    import core.library_mounts as lm
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    _vec(monkeypatch)
    mid = store.add_mount(scope="S", path=str(_docs(tmp_path)), kind="code")
    assert lm.index_docs_mount(store, mid)["status"] == "skipped_non_docs"


def test_an_unavailable_store_records_nothing(tmp_path, store, monkeypatch):
    import core.library_mounts as lm
    import core.vec_db as vec_db
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    monkeypatch.setattr(vec_db, "open_vec_db", lambda *a, **k: contextlib.nullcontext(None))
    mid = store.add_mount(scope="S", path=str(_docs(tmp_path)), kind="docs")
    assert lm.index_docs_mount(store, mid)["status"] == "store_unavailable"
    row = store.get_mount(mid)
    assert row["indexed_empty_at"] is None and row["last_synced"] is None


def test_a_future_mtime_does_not_keep_a_mount_stale(tmp_path, store):
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); store.mark_synced(mid)
    far = time.time() + 86400 * 365
    os.utime(d / "a.md", (far, far))
    assert store.check_health(mid) == "fresh"


def test_the_column_added_twice_does_not_raise():
    import core.library_mounts as lm
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE library_mounts (id TEXT)")
    lm._add_column(conn, "indexed_empty_at", "TEXT")
    lm._add_column(conn, "indexed_empty_at", "TEXT")          # another process won the race



# ── backup copies and lock files are not content; an older docs index is redone ──

@pytest.mark.parametrize("name", [
    ".DS_Store", "~$deck.pptx", "notes.md~", "deck.html.bak", "deck.bak2",
    "deck.html.bak-1788432226", "deck.html.bak-title", "x.BAK"])
def test_names_no_index_reads(name):
    from core.library_mounts import _not_content
    assert _not_content(name)


@pytest.mark.parametrize("name", ["deck.html", "bakery.md", "notes.backup.md", "x.bake", "a~b.md"])
def test_names_that_are_content(name):
    from core.library_mounts import _not_content
    assert not _not_content(name)


def test_index_docs_skips_backups_and_lock_files(tmp_path, store, monkeypatch):
    import core.library_mounts as lm
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    vec = _vec(monkeypatch)
    d = _docs(tmp_path)
    (d / "deck.html").write_text("<p>current deck</p>\n")
    (d / "deck.html.bak-1788432226").write_text("<p>old deck</p>\n")
    (d / "~$deck.pptx").write_text("lock owner\n")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    assert lm.index_docs_mount(store, mid)["status"] == "indexed"
    files = {r[0] for r in vec.execute("SELECT DISTINCT source_file FROM knowledge_chunks")}
    assert files == {f"mount:{mid}/a.md", f"mount:{mid}/deck.html"}
    assert store.get_mount(mid)["index_version"] == lm.DOCS_INDEX_VERSION


def test_a_docs_index_of_an_older_version_is_redone(tmp_path, store, spawned):
    d = _docs(tmp_path)
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); store.mark_synced(mid)  # no version
    refresh_all_mounts(store, reindex=True)
    assert spawned == [["-m", "core.library_mounts", "index-docs", mid]]
    spawned.clear()
    _current(store, mid)
    refresh_all_mounts(store, reindex=True)
    assert spawned == []


def test_an_empty_docs_index_records_its_version(tmp_path, store, monkeypatch):
    import core.library_mounts as lm
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    _vec(monkeypatch)
    d = tmp_path / "bin"; d.mkdir(); (d / "x.bin").write_bytes(b"\xff\xfe\x00")
    mid = store.add_mount(scope="S", path=str(d), kind="docs")
    assert lm.index_docs_mount(store, mid)["status"] == "indexed_empty"
    assert store.get_mount(mid)["index_version"] == lm.DOCS_INDEX_VERSION


def test_rewriting_a_backup_does_not_make_a_mount_stale(tmp_path, store):
    d = _docs(tmp_path)
    (d / "deck.html.bak-1").write_text("x")
    mid = store.add_mount(scope="S", path=str(d), kind="docs"); store.mark_synced(mid)
    future = time.time() + 5
    os.utime(d / "deck.html.bak-1", (future, future))
    assert store.check_health(mid) == "fresh"
