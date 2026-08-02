"""Ownership plan A — mount indexing is gated by TECH.md-owned OR mount-registered.

The contamination guard `repo_root_is_owned` (run_1950e67e) rejects indexing any
directory a project doesn't declare in its TECH.md. Mounting an EXTERNAL dir
inverts that — so plan A adds a PARALLEL predicate `mount_path_is_registered`
and a composed oracle `is_mount_indexable = owned OR registered`.

Critical safety property (the reason this cycle is security-sensitive): the
composed oracle is used ONLY by the NEW mount-index path (Cycle 4). The 3 existing
project-loop sites (reindex job / startup watcher / context-health hook) stay
UNTOUCHED and strict — a project reindex must NEVER pick up a mount, that IS the
contamination bug. So this cycle proves:
  1. a registered mount path → indexable (opt-in works)
  2. an UNregistered external path → REJECTED (NEGATIVE — the invariant holds)
  3. registration is PER-SCOPE (a mount in scope A does not authorize scope B —
     never a global allowlist, which would reopen run_1950e67e)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.library_mounts import LibraryMounts, mount_path_is_registered, is_mount_indexable


@pytest.fixture
def store() -> LibraryMounts:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    s = LibraryMounts(conn)
    s.ensure_table()
    return s


def test_registered_path_is_registered(tmp_path: Path, store: LibraryMounts) -> None:
    store.add_mount(scope="SwarmAI", path=str(tmp_path), kind="code")
    assert mount_path_is_registered(store, "SwarmAI", str(tmp_path)) is True


def test_unregistered_external_path_is_rejected(tmp_path: Path, store: LibraryMounts) -> None:
    """NEGATIVE: a path never registered is NOT authorized (contamination invariant)."""
    external = tmp_path / "someone-elses-repo"
    external.mkdir()
    assert mount_path_is_registered(store, "SwarmAI", str(external)) is False


def test_registration_is_per_scope_not_global(tmp_path: Path, store: LibraryMounts) -> None:
    """NEGATIVE: a mount registered in scope A must NOT authorize scope B.
    A global allowlist would reopen the run_1950e67e cross-project contamination."""
    store.add_mount(scope="ProjectA", path=str(tmp_path), kind="code")
    assert mount_path_is_registered(store, "ProjectA", str(tmp_path)) is True
    assert mount_path_is_registered(store, "ProjectB", str(tmp_path)) is False


def test_disabled_mount_is_not_authorized(tmp_path: Path, store: LibraryMounts) -> None:
    """A disabled mount (toggle off) is not indexable — recall must skip it."""
    mid = store.add_mount(scope="SwarmAI", path=str(tmp_path), kind="code")
    store.set_enabled(mid, False)
    assert mount_path_is_registered(store, "SwarmAI", str(tmp_path)) is False


def test_is_mount_indexable_ors_owned_and_registered(tmp_path: Path, store: LibraryMounts) -> None:
    """The composed oracle: owned OR registered. A registered mount is indexable
    even though the project does NOT own it (owned=False for an external dir)."""
    external = tmp_path / "ext"
    external.mkdir()
    project_dir = tmp_path / "Projects" / "SwarmAI"
    project_dir.mkdir(parents=True)
    # Not owned (no TECH.md declaring this external dir) AND not registered → reject.
    assert is_mount_indexable(project_dir, str(external), "SwarmAI", store) is False
    # Register it → now indexable via the OR branch, WITHOUT changing ownership.
    store.add_mount(scope="SwarmAI", path=str(external), kind="code")
    assert is_mount_indexable(project_dir, str(external), "SwarmAI", store) is True


def test_path_normalization_trailing_slash(tmp_path: Path, store: LibraryMounts) -> None:
    """Registration matches regardless of a trailing-slash difference (else a legit
    mount silently fails the gate — the same expanduser/rstrip care as the owned oracle)."""
    store.add_mount(scope="SwarmAI", path=str(tmp_path), kind="code")
    assert mount_path_is_registered(store, "SwarmAI", str(tmp_path) + "/") is True
