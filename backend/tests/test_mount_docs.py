"""Cycle 5 — docs-dir mount: file-level briefing cards on the library FTS5 leg.

A docs-dir mount does NOT go through code_intel (.md/.txt/.pdf are non-source).
Instead the agent (chat-native) walks the dir, judges worthwhile files, and calls
write_docs_cards() to persist file-level briefing cards to
Knowledge/Library/mounts/<id>/ + a directory-overview card. Those .md cards are
AUTO-indexed by the existing sync_knowledge_index rglob('*.md') scan → surface on
the library FTS5 recall leg with ZERO new recall code (Gate-1 verified).

Each card carries a back-pointer to the LIVE external source path (recall lands on
the card → the agent Reads the live source = progressive load, index-not-warehouse).

Properties proven:
  1. cards land under the mount's own dir (Knowledge/Library/mounts/<id>/)
  2. each file-card names its source path (progressive-load pointer)
  3. a directory-overview card is written
  4. re-writing is idempotent (delta by content, no dup explosion)
  5. NEGATIVE: a non-docs (code) mount is skipped here
  6. cards are plain .md (so sync_knowledge_index picks them up unchanged) —
     never a copy of the source bytes (index-not-warehouse)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.library_mounts import LibraryMounts, write_docs_cards


@pytest.fixture
def store() -> LibraryMounts:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    s = LibraryMounts(conn)
    s.ensure_table()
    return s


def _make_docs_dir(root: Path) -> Path:
    d = root / "all-hands"
    d.mkdir()
    (d / "q3-plan.md").write_text("# Q3 Plan\n\nShip the widget. Hire two engineers.\n")
    (d / "notes.txt").write_text("standup: blocked on auth\n")
    return d


def test_write_docs_cards_lands_under_mount_dir(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    src = _make_docs_dir(tmp_path)
    mounts_root = tmp_path / "mounts"
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: mounts_root)
    mid = store.add_mount(scope="SwarmAI", path=str(src), kind="docs")

    # Cards for the two files (the agent supplies the per-file briefing text).
    briefings = {
        "q3-plan.md": "Q3 roadmap: ship widget, +2 hires.",
        "notes.txt": "Standup log; current blocker is auth.",
    }
    result = write_docs_cards(store, mid, briefings=briefings, overview="All-hands docs: Q3 plan + standup notes.")
    assert result["status"] == "written"
    assert result["cards"] == 2

    card_dir = mounts_root / mid
    cards = sorted(p.name for p in card_dir.glob("*.md"))
    # 2 file-cards + 1 overview card
    assert len(cards) == 3
    assert "_overview.md" in cards


def test_file_card_carries_source_pointer(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    src = _make_docs_dir(tmp_path)
    mounts_root = tmp_path / "mounts"
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: mounts_root)
    mid = store.add_mount(scope="SwarmAI", path=str(src), kind="docs")
    write_docs_cards(store, mid, briefings={"q3-plan.md": "Q3 roadmap brief."}, overview="ov")

    card = next((mounts_root / mid).glob("*q3-plan*.md"))
    text = card.read_text()
    # The card is a POINTER: it names the LIVE source path for progressive load,
    # and carries the briefing — NOT a copy of the source bytes.
    assert str(src / "q3-plan.md") in text
    assert "Q3 roadmap brief." in text
    assert "Ship the widget" not in text  # source body is NOT copied in


def test_overview_card_written(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    src = _make_docs_dir(tmp_path)
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: tmp_path / "mounts")
    mid = store.add_mount(scope="SwarmAI", path=str(src), kind="docs")
    write_docs_cards(store, mid, briefings={"q3-plan.md": "b"}, overview="All-hands corpus overview.")
    ov = (tmp_path / "mounts" / mid / "_overview.md").read_text()
    assert "All-hands corpus overview." in ov
    assert str(src) in ov  # overview points at the mounted dir


def test_rewrite_is_idempotent(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    src = _make_docs_dir(tmp_path)
    mounts_root = tmp_path / "mounts"
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: mounts_root)
    mid = store.add_mount(scope="SwarmAI", path=str(src), kind="docs")
    write_docs_cards(store, mid, briefings={"q3-plan.md": "b"}, overview="ov")
    write_docs_cards(store, mid, briefings={"q3-plan.md": "b2"}, overview="ov2")
    cards = list((mounts_root / mid).glob("*q3-plan*.md"))
    assert len(cards) == 1  # rewritten in place, not duplicated
    assert "b2" in cards[0].read_text()


def test_docs_cards_skips_code_mount(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    """NEGATIVE: a code-kind mount is not a docs mount — skipped here."""
    d = tmp_path / "repo"; d.mkdir()
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: tmp_path / "mounts")
    mid = store.add_mount(scope="SwarmAI", path=str(d), kind="code")
    result = write_docs_cards(store, mid, briefings={"x": "y"}, overview="o")
    assert result["status"] == "skipped_non_docs"


def test_docs_mount_marks_synced(tmp_path: Path, store: LibraryMounts, monkeypatch) -> None:
    src = _make_docs_dir(tmp_path)
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: tmp_path / "mounts")
    mid = store.add_mount(scope="SwarmAI", path=str(src), kind="docs")
    write_docs_cards(store, mid, briefings={"q3-plan.md": "b"}, overview="ov")
    row = store.get_mount(mid)
    assert row["last_synced"] is not None
    assert row["health"] == "fresh"
