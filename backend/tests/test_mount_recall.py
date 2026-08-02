"""Cycle 4 — code-dir mount indexing + additive recall pass.

A code-dir mount indexes an EXTERNAL directory into a per-mount code_intel graph
that lives UNDER the workspace (Knowledge/Library/mounts/<id>/code_intel.db) with
its repo_root pointed at the external source. Its symbols surface through the
existing `recall_all` codeintel bucket via an ADDITIVE pass — NO change to
_codeintel_recall's or recall_all's signature (Gate-1 rev 3: consult the registry
internally).

Safety properties proven here:
  1. an enabled code mount's symbols surface in recall_all
  2. the mount graph is SEPARATE from any project graph (no contamination) — a
     project's own code_intel graph is never touched by indexing a mount
  3. a disabled mount does NOT surface (toggle honored)
  4. NEGATIVE: a mount whose source was deleted flips health→missing and does NOT
     crash recall
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.library_mounts import LibraryMounts, index_code_mount, recall_mounts


@pytest.fixture
def store() -> LibraryMounts:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    s = LibraryMounts(conn)
    s.ensure_table()
    return s


def _make_code_dir(root: Path) -> Path:
    d = root / "ext-repo"
    d.mkdir()
    (d / "widget.py").write_text(
        "def compute_widget_score(x):\n    return x * 2\n\n"
        "class WidgetEngine:\n    def render_widget(self):\n        pass\n"
    )
    return d


def test_index_code_mount_builds_separate_graph(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    ext = _make_code_dir(tmp_path)
    mounts_root = tmp_path / "mounts"
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: mounts_root)

    mid = store.add_mount(scope="SwarmAI", path=str(ext), kind="code")
    result = index_code_mount(store, mid)
    assert result["status"] == "indexed"
    assert result["symbols"] > 0
    # The per-mount graph db lives UNDER the workspace mounts dir, NOT in Projects/.
    db = mounts_root / mid / "code_intel.db"
    assert db.exists()


def test_mount_symbols_surface_in_recall(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    ext = _make_code_dir(tmp_path)
    mounts_root = tmp_path / "mounts"
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: mounts_root)

    mid = store.add_mount(scope="SwarmAI", path=str(ext), kind="code")
    index_code_mount(store, mid)

    hits = recall_mounts("widget", scope="SwarmAI", store=store)
    names = {h["name"] for h in hits}
    assert any("widget" in n.lower() for n in names), f"expected a widget symbol, got {names}"
    # Every hit is stamped with its origin mount so the agent reads the LIVE source.
    assert all(h.get("mount_id") == mid for h in hits)


def test_disabled_mount_does_not_surface(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    ext = _make_code_dir(tmp_path)
    mounts_root = tmp_path / "mounts"
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: mounts_root)

    mid = store.add_mount(scope="SwarmAI", path=str(ext), kind="code")
    index_code_mount(store, mid)
    store.set_enabled(mid, False)

    hits = recall_mounts("widget", scope="SwarmAI", store=store)
    assert hits == []


def test_index_only_touches_code_kind(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    """A docs mount is NOT code-indexed here (that's the docs-mount cycle)."""
    d = tmp_path / "docsdir"; d.mkdir()
    (d / "note.md").write_text("# hi\n")
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: tmp_path / "mounts")
    mid = store.add_mount(scope="SwarmAI", path=str(d), kind="docs")
    result = index_code_mount(store, mid)
    assert result["status"] == "skipped_non_code"


def test_recall_survives_deleted_source(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    """NEGATIVE: a mount whose external source is deleted must not crash recall.
    The graph persists (indexed snapshot) but health flips missing on re-probe."""
    ext = _make_code_dir(tmp_path)
    mounts_root = tmp_path / "mounts"
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: mounts_root)

    mid = store.add_mount(scope="SwarmAI", path=str(ext), kind="code")
    index_code_mount(store, mid)
    # Delete the external source out from under the mount.
    for p in sorted(ext.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    ext.rmdir()

    # Recall must not raise; health re-probe reports missing.
    hits = recall_mounts("widget", scope="SwarmAI", store=store)
    assert isinstance(hits, list)  # no crash
    assert store.check_health(mid) == "missing"


def test_recall_mounts_empty_when_no_mounts(store: LibraryMounts) -> None:
    assert recall_mounts("anything", scope="SwarmAI", store=store) == []


def test_delete_mount_evicts_graph_cache(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    """Gate-2 #4: deleting a mount closes+evicts its cached GraphStore handle
    (no leaked sqlite connection in a long-lived daemon)."""
    import core.library_mounts as lm
    ext = _make_code_dir(tmp_path)
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: tmp_path / "mounts")
    mid = store.add_mount(scope="SwarmAI", path=str(ext), kind="code")
    index_code_mount(store, mid)
    # Load into cache via a recall.
    recall_mounts("widget", scope="SwarmAI", store=store)
    assert mid in lm._mount_graph_cache
    store.delete_mount(mid)
    assert mid not in lm._mount_graph_cache  # evicted (handle closed)


def test_disable_mount_evicts_graph_cache(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    """Gate-2 #4: disabling a mount evicts its cached handle too."""
    import core.library_mounts as lm
    ext = _make_code_dir(tmp_path)
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: tmp_path / "mounts")
    mid = store.add_mount(scope="SwarmAI", path=str(ext), kind="code")
    index_code_mount(store, mid)
    recall_mounts("widget", scope="SwarmAI", store=store)
    assert mid in lm._mount_graph_cache
    store.set_enabled(mid, False)
    assert mid not in lm._mount_graph_cache
