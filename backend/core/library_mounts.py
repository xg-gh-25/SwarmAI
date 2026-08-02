"""Library mount registry — pointers to external knowledge, never copies.

The mount registry backs the Library overlay's Mounted section. A mount is a
lightweight row {id, scope, path, kind, briefing, index_ref, last_synced, health,
enabled, created_at} — a POINTER into the user's disk, indexed IN PLACE by the
engine best suited to its kind (code → code_intel graph; docs → briefing cards on
the FTS5 library leg). This module owns ONLY the registry (CRUD + source-exists
health); indexing + ownership-plan-A live in later cycles.

Design: Knowledge/Designs/2026-08-02-library-mount-points-design.md (Cycle 2).

Deliberate architecture (Gate-1 revision + R25): this store SELF-OWNS its schema
via ensure_table() — the KnowledgeStore(conn) pattern — so it never touches the
CRITICAL 263-caller database/sqlite.py. It takes a sqlite3.Connection, making it
trivially testable with an in-memory DB and decoupled from the app DB lifecycle.

Index-not-warehouse invariant: this store holds a `path` string + a `briefing`
snapshot, never the external content. Health only reports whether the pointer is
still valid; recall reads the LIVE source on a hit.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Optional

# Open-ended by design (the paradigm grows by adding a kind, never a type).
# Enforced at the write boundary so a bad kind fails loud (fail-closed), not
# silently stored — the registry is a contract, not a scratchpad.
VALID_KINDS = ("code", "docs", "url")

# Health is a source-exists signal, NOT a content-freshness claim: 'fresh' = the
# pointer resolves to a live dir/file; 'missing' = the source is gone (dangling
# reference). 'stale' (source mtime > index) is set by the freshness job cycle.
VALID_HEALTH = ("fresh", "stale", "missing")


class LibraryMounts:
    """CRUD + health for the library mount registry over a sqlite3 connection."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        # Return dict-like rows so callers (tests, the API) read by column name.
        if conn.row_factory is None:
            conn.row_factory = sqlite3.Row

    def ensure_table(self) -> None:
        """Create the mount registry table if absent (idempotent)."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS library_mounts (
                id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                path TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('code', 'docs', 'url')),
                briefing TEXT DEFAULT '',
                index_ref TEXT,
                last_synced TEXT,
                health TEXT NOT NULL DEFAULT 'fresh' CHECK (health IN ('fresh', 'stale', 'missing')),
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # A scope+path pair is unique — mounting the same dir twice in one scope
        # is a no-op update, not a duplicate row.
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_library_mounts_scope_path "
            "ON library_mounts(scope, path)"
        )
        self._conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add_mount(
        self,
        *,
        scope: str,
        path: str,
        kind: str,
        briefing: str = "",
        index_ref: Optional[str] = None,
    ) -> str:
        """Register a mount. Returns its id. Rejects an unknown kind (fail-closed).

        Health is computed at insert from the live source (a mount whose path
        already doesn't exist lands as 'missing', not a false 'fresh').
        """
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid mount kind {kind!r}; must be one of {VALID_KINDS}")
        mid = uuid.uuid4().hex
        health = self._probe_health(path)
        self._conn.execute(
            "INSERT INTO library_mounts (id, scope, path, kind, briefing, index_ref, health) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mid, scope, path, kind, briefing, index_ref, health),
        )
        self._conn.commit()
        return mid

    def get_mount(self, mount_id: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM library_mounts WHERE id = ?", (mount_id,))
        return cur.fetchone()

    def list_mounts(self, scope: Optional[str] = None) -> list[sqlite3.Row]:
        if scope is None:
            cur = self._conn.execute("SELECT * FROM library_mounts ORDER BY created_at")
        else:
            cur = self._conn.execute(
                "SELECT * FROM library_mounts WHERE scope = ? ORDER BY created_at", (scope,)
            )
        return cur.fetchall()

    def delete_mount(self, mount_id: str) -> bool:
        """Unmount (registry row only — never touches the external source). Returns
        True if a row was removed, False if the id was unknown (no-op, not a crash)."""
        cur = self._conn.execute("DELETE FROM library_mounts WHERE id = ?", (mount_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def set_enabled(self, mount_id: str, enabled: bool) -> bool:
        cur = self._conn.execute(
            "UPDATE library_mounts SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, mount_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── Health ────────────────────────────────────────────────────────────

    def check_health(self, mount_id: str) -> Optional[str]:
        """Re-probe the source and persist+return the health. None if unknown id.

        A dangling mount (source deleted/moved) flips to 'missing' — the agent
        then reports "source no longer at <path>" rather than failing silently.
        """
        row = self.get_mount(mount_id)
        if row is None:
            return None
        health = self._probe_health(row["path"])
        self._conn.execute(
            "UPDATE library_mounts SET health = ? WHERE id = ?", (health, mount_id)
        )
        self._conn.commit()
        return health

    @staticmethod
    def _probe_health(path: str) -> str:
        """Source-exists probe: 'fresh' if the pointer resolves, else 'missing'.
        ('stale' is a freshness-job concern — mtime vs index — not source-exists.)"""
        try:
            return "fresh" if Path(path).expanduser().exists() else "missing"
        except OSError:
            return "missing"

    def is_registered(self, scope: str, path: str) -> bool:
        """True iff `path` is an ENABLED mount registered under `scope`.

        Per-scope + enabled-only by design: a disabled mount (toggle off) or a
        mount registered under a DIFFERENT scope does NOT authorize indexing —
        anything looser is a global allowlist, which reopens run_1950e67e.
        Path match is normalization-tolerant (expanduser + rstrip + resolve) so a
        trailing-slash / ~ difference doesn't silently fail a legit mount, but it
        never widens to a prefix/substring match (that would authorize siblings).
        """
        want = _norm_path(path)
        for row in self.list_mounts(scope=scope):
            if not row["enabled"]:
                continue
            if _norm_path(row["path"]) == want:
                return True
        return False


def _norm_path(path: str) -> str:
    """Canonical path form for registry equality (never a prefix match)."""
    try:
        return str(Path(path.rstrip("/")).expanduser().resolve())
    except OSError:
        return path.rstrip("/")


# ── Ownership plan A — parallel predicate + composed oracle ──────────────────
#
# The contamination guard `repo_root_is_owned` (run_1950e67e) stays UNTOUCHED and
# strict at its 3 project-loop sites. Mount indexing (Cycle 4) uses the composed
# oracle below instead — owned OR explicitly-registered — so an external dir the
# user opted in via the registry is indexable WITHOUT loosening the project guard.


def mount_path_is_registered(store: "LibraryMounts", scope: str, path: str) -> bool:
    """Free-function form of LibraryMounts.is_registered (the parallel predicate).

    Kept as a module function (not only a method) so the mount-index call site can
    compose it with repo_root_is_owned without holding a store method reference."""
    return store.is_registered(scope, path)


def is_mount_indexable(
    project_dir,
    path: str,
    scope: str,
    store: "LibraryMounts",
) -> bool:
    """The composed ownership oracle for the MOUNT-index path: owned OR registered.

    `owned` = the project's TECH.md declares `path` as its repo (the existing
    guard); `registered` = `path` is an enabled mount in THIS scope. Either branch
    authorizes indexing; neither → reject (the invariant that keeps a random
    external path out). This is ONLY for the new mount-index path — the 3
    project-loop sites keep calling repo_root_is_owned directly (a project reindex
    must never pick up a mount).
    """
    try:
        from core.code_intel import repo_root_is_owned
        if repo_root_is_owned(project_dir, path):
            return True
    except Exception:  # noqa: BLE001 — ownership check must never raise into the gate
        pass
    return mount_path_is_registered(store, scope, path)
