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

import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

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
        # Gate-2 #4 (HIGH): close+evict any cached GraphStore handle for this mount,
        # else a long-lived daemon leaks the sqlite connection across mount churn.
        _invalidate_mount_graph(mount_id)
        return cur.rowcount > 0

    def set_enabled(self, mount_id: str, enabled: bool) -> bool:
        cur = self._conn.execute(
            "UPDATE library_mounts SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, mount_id),
        )
        self._conn.commit()
        # Gate-2 #4 (HIGH): disabling evicts the cached handle (freed now, re-loaded
        # fresh if re-enabled) — no lingering open connection, no stale-graph serve.
        if not enabled:
            _invalidate_mount_graph(mount_id)
        return cur.rowcount > 0

    def mark_synced(self, mount_id: str, index_ref: Optional[str] = None) -> bool:
        """Record a successful index: stamp last_synced (now) + index_ref, and
        set health fresh. Called by the indexer after a graph is built."""
        cur = self._conn.execute(
            "UPDATE library_mounts SET last_synced = datetime('now'), "
            "index_ref = COALESCE(?, index_ref), health = 'fresh' WHERE id = ?",
            (index_ref, mount_id),
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


# ── Code-dir mount: per-mount graph + additive recall pass (Cycle 4) ─────────
#
# A code mount is indexed into its OWN code_intel graph under the workspace
# (Knowledge/Library/mounts/<id>/code_intel.db) with repo_root pointed at the
# EXTERNAL source. This graph is SEPARATE from every project graph (no shared
# _graph_cache key → structurally impossible to contaminate a project's brain —
# the run_1950e67e concern). Its symbols surface via recall_mounts(), which the
# codeintel recall leg calls as an ADDITIVE pass (no signature change to
# _codeintel_recall / recall_all — Gate-1 rev 3).

# Per-mount GraphStore cache, keyed by mount id (NOT project name — never touches
# code_intel._graph_cache, so a mount can never collide with / evict a project).
_mount_graph_cache: dict[str, object] = {}
_mount_cache_lock = threading.Lock()


def _mounts_dir() -> Path:
    """Workspace dir that holds per-mount indexes: SwarmWS/Knowledge/Library/mounts/.
    Indirected through a function so tests can monkeypatch it to a tmp dir."""
    from jobs.paths import SWARMWS
    return SWARMWS / "Knowledge" / "Library" / "mounts"


def _mount_db_path(mount_id: str) -> Path:
    return _mounts_dir() / mount_id / "code_intel.db"


def _load_mount_graph(mount_id: str):
    """Load (cached) the per-mount GraphStore, or None if not indexed yet."""
    with _mount_cache_lock:
        if mount_id in _mount_graph_cache:
            return _mount_graph_cache[mount_id]
    db_path = _mount_db_path(mount_id)
    if not db_path.exists():
        return None
    try:
        from core.code_intel.graph_store import GraphStore
        graph = GraphStore(db_path)
        with _mount_cache_lock:
            if mount_id in _mount_graph_cache:  # double-check after acquiring
                graph.close()
                return _mount_graph_cache[mount_id]
            _mount_graph_cache[mount_id] = graph
        return graph
    except Exception as exc:  # noqa: BLE001
        logger.warning("mount %s: failed to load graph: %s", mount_id, exc)
        return None


def _invalidate_mount_graph(mount_id: str) -> None:
    with _mount_cache_lock:
        g = _mount_graph_cache.pop(mount_id, None)
    if g is not None:
        try:
            g.close()
        except Exception:  # noqa: BLE001
            pass


def index_code_mount(store: "LibraryMounts", mount_id: str) -> dict:
    """Index a code-kind mount into its own per-mount graph (repo_root=external).

    Uses code_intel's public parse API + GraphStore public API only — never
    touches the CRITICAL shared _graph_cache or the project reindex loop. Returns
    {status, symbols?} — status ∈ {indexed, skipped_non_code, unknown, source_missing}.
    """
    row = store.get_mount(mount_id)
    if row is None:
        return {"status": "unknown"}
    if row["kind"] != "code":
        return {"status": "skipped_non_code"}
    src = Path(row["path"]).expanduser()
    if not src.is_dir():
        store.check_health(mount_id)  # persists 'missing'
        return {"status": "source_missing"}

    from core.code_intel.parser import parse_repo_with_coverage
    from core.code_intel.graph_store import GraphStore

    parse_out = parse_repo_with_coverage(src)
    _invalidate_mount_graph(mount_id)  # drop any cached handle before rewrite
    db_path = _mount_db_path(mount_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    graph = GraphStore(db_path)
    total = 0
    if parse_out.results:
        graph.clear()
        graph.bulk_insert(parse_out.results, repo_root=str(src))
        graph.set_meta("repo_root", str(src))  # points at the EXTERNAL source
        graph.set_meta("mount_id", mount_id)
        total = sum(len(pr.nodes) for pr in parse_out.results)
    with _mount_cache_lock:
        _mount_graph_cache[mount_id] = graph
    store.mark_synced(mount_id, index_ref=str(db_path))
    return {"status": "indexed", "symbols": total}


def recall_mounts(query: str, *, scope: str, store: "LibraryMounts", limit: int = 8) -> list[dict]:
    """Search all ENABLED code mounts in `scope`, newest hits first.

    Each hit is a symbol dict stamped with `mount_id` + `mount_path` so the agent
    knows to Read the LIVE external source. Never raises — a missing/broken mount
    graph is skipped (a deleted-source mount just yields nothing). This is the
    additive pass the codeintel recall leg composes in.
    """
    out: list[dict] = []
    try:
        rows = store.list_mounts(scope=scope)
    except Exception:  # noqa: BLE001
        return out
    for row in rows:
        if not row["enabled"] or row["kind"] != "code":
            continue
        graph = _load_mount_graph(row["id"])
        if graph is None:
            continue
        try:
            hits = graph.search_symbols(query, limit=limit)
        except Exception as exc:  # noqa: BLE001 — one bad mount must not sink recall
            logger.debug("mount %s: search failed: %s", row["id"], exc)
            continue
        for h in hits:
            out.append({**h, "mount_id": row["id"], "mount_path": row["path"]})
    # Rank across mounts by the graph's own rank (more-negative BM25 = better).
    out.sort(key=lambda h: h.get("rank", 0.0))
    return out[:limit]
