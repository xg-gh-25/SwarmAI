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
