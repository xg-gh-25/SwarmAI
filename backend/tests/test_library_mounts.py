"""Tests for the library_mounts registry (Cycle 2 of the Library goal run).

The mount registry is a lightweight row store {id, scope, path, kind, briefing,
index_ref, last_synced, health, enabled, created_at} — a POINTER, never a copy
(index-not-warehouse). Health is a source-exists check: 'missing' when the path
is gone, else 'fresh'/'stale'. CRUD + health only; indexing lands in later cycles.

Methodology: in-memory sqlite3 (mirrors test_knowledge_store) — the store
self-owns its schema via ensure_table, so it never touches the CRITICAL 263-caller
database/sqlite.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.library_mounts import LibraryMounts


@pytest.fixture
def store() -> LibraryMounts:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    s = LibraryMounts(conn)
    s.ensure_table()
    return s


def test_ensure_table_idempotent(store: LibraryMounts) -> None:
    # Calling twice must not raise (CREATE IF NOT EXISTS).
    store.ensure_table()
    assert store.list_mounts() == []


def test_add_and_get_mount(tmp_path: Path, store: LibraryMounts) -> None:
    mid = store.add_mount(scope="SwarmAI", path=str(tmp_path), kind="docs", briefing="notes dir")
    assert mid
    row = store.get_mount(mid)
    assert row is not None
    assert row["path"] == str(tmp_path)
    assert row["kind"] == "docs"
    assert row["scope"] == "SwarmAI"
    assert row["enabled"] == 1  # enabled by default
    assert row["briefing"] == "notes dir"


def test_list_mounts_returns_all(tmp_path: Path, store: LibraryMounts) -> None:
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    store.add_mount(scope="SwarmAI", path=str(a), kind="docs")
    store.add_mount(scope="SwarmAI", path=str(b), kind="code")
    rows = store.list_mounts()
    assert len(rows) == 2
    assert {r["kind"] for r in rows} == {"docs", "code"}


def test_delete_mount(tmp_path: Path, store: LibraryMounts) -> None:
    mid = store.add_mount(scope="SwarmAI", path=str(tmp_path), kind="docs")
    assert store.delete_mount(mid) is True
    assert store.get_mount(mid) is None
    # Deleting a non-existent id is a no-op False, not a crash.
    assert store.delete_mount("nope") is False


def test_set_enabled_toggle(tmp_path: Path, store: LibraryMounts) -> None:
    mid = store.add_mount(scope="SwarmAI", path=str(tmp_path), kind="docs")
    store.set_enabled(mid, False)
    assert store.get_mount(mid)["enabled"] == 0
    store.set_enabled(mid, True)
    assert store.get_mount(mid)["enabled"] == 1


def test_health_fresh_when_source_exists(tmp_path: Path, store: LibraryMounts) -> None:
    mid = store.add_mount(scope="SwarmAI", path=str(tmp_path), kind="docs")
    assert store.check_health(mid) == "fresh"


def test_health_missing_when_source_deleted(tmp_path: Path, store: LibraryMounts) -> None:
    """NEGATIVE: a deleted source flips health to 'missing' — not a crash."""
    d = tmp_path / "gone"
    d.mkdir()
    mid = store.add_mount(scope="SwarmAI", path=str(d), kind="docs")
    assert store.check_health(mid) == "fresh"
    # Delete the source directory out from under the mount.
    d.rmdir()
    assert store.check_health(mid) == "missing"
    # The stored health column is updated as a side effect (observable).
    assert store.get_mount(mid)["health"] == "missing"


def test_check_health_unknown_id_returns_none(store: LibraryMounts) -> None:
    """A health check on a non-existent mount is None, not a crash."""
    assert store.check_health("does-not-exist") is None


def test_add_mount_rejects_bad_kind(tmp_path: Path, store: LibraryMounts) -> None:
    """NEGATIVE: an unknown kind is rejected (CHECK constraint / guard)."""
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        store.add_mount(scope="SwarmAI", path=str(tmp_path), kind="banana")


# ── judge_mount_kind: repo-marker judgement (run_139d7652) ───────────────────
# The kind judge decides code vs docs by REPO-NESS (a repo marker present), NOT by
# "contains any parseable source file". These lock the fix for the AI-Native mislabel:
# a docs-majority folder with a stray code file must NOT be judged code.

from core.library_mounts import judge_mount_kind  # noqa: E402


def test_judge_kind_scattered_code_no_marker_is_docs(tmp_path: Path) -> None:
    """THE FIX (run_139d7652): a folder of scattered code FILES with no repo marker
    is docs, not code. This is the AI-Native case — a stray .py must not force code."""
    (tmp_path / "notes.md").write_text("# notes", encoding="utf-8")
    (tmp_path / "snippet.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "example.ts").write_text("const x = 1\n", encoding="utf-8")
    assert judge_mount_kind(str(tmp_path)) == "docs"


def test_judge_kind_git_repo_is_code(tmp_path: Path) -> None:
    """A .git tree is the strongest repo signal -> code."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    assert judge_mount_kind(str(tmp_path)) == "code"


def test_judge_kind_package_json_is_code(tmp_path: Path) -> None:
    """A manifest (package.json) marks a real repo -> code."""
    (tmp_path / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    assert judge_mount_kind(str(tmp_path)) == "code"


def test_judge_kind_pyproject_is_code(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert judge_mount_kind(str(tmp_path)) == "code"


def test_judge_kind_marker_in_shallow_subdir_is_code(tmp_path: Path) -> None:
    """A repo mounted one level up (marker in an immediate subdir) is still code."""
    sub = tmp_path / "myrepo"
    sub.mkdir()
    (sub / "go.mod").write_text("module x\n", encoding="utf-8")
    assert judge_mount_kind(str(tmp_path)) == "code"


def test_judge_kind_vendored_marker_ignored(tmp_path: Path) -> None:
    """A marker inside node_modules belongs to a dependency, not this dir -> docs."""
    nm = tmp_path / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text('{"name":"dep"}', encoding="utf-8")
    (tmp_path / "guide.md").write_text("# guide", encoding="utf-8")
    assert judge_mount_kind(str(tmp_path)) == "docs"


def test_judge_kind_empty_dir_is_docs(tmp_path: Path) -> None:
    assert judge_mount_kind(str(tmp_path)) == "docs"


def test_judge_kind_nonexistent_is_docs(tmp_path: Path) -> None:
    assert judge_mount_kind(str(tmp_path / "nope")) == "docs"


def test_judge_kind_git_only_in_subdir_is_code(tmp_path: Path) -> None:
    """Gate-2 LOW (run_139d7652): a repo mounted one level up whose ONLY marker is a
    shallow `.git` dir must judge code — `.git` is both a skip-dir (don't descend) AND
    a marker, so the prune must look at ANCESTORS only, letting the `.git` dir itself
    still count."""
    sub = tmp_path / "myrepo"
    sub.mkdir()
    (sub / ".git").mkdir()
    (sub / "main.py").write_text("x = 1\n", encoding="utf-8")
    assert judge_mount_kind(str(tmp_path)) == "code"
