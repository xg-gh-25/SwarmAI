"""
Code Intelligence Platform — project-scoped code graph for pipeline & agent.

Public API:
    load_project_graphs(project_name) → [(CodeIntelDb, GraphStore)]  (every graph)
    project_graph(project_name, repo=None) → (CodeIntelDb, GraphStore) | None
    owned_graphs(project_dir) → list[CodeIntelDb]     (every owned repo's graph, built or not)
    code_intel_dbs(project_dir) → list[CodeIntelDb]   (the ones that exist)
    code_intel_db_for(project_dir) → Path | None      (the first that exists)
    owned_repo_roots(project_dir) → list[str]         (repo-ownership oracle)
    resolve_router_prefixes(graph, repo_root) → int  (call after any route re-store)
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.ddd_paths import ddd_path

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# Module-level caches (guarded by _cache_lock for thread safety)
_cache_lock = threading.Lock()
# db path → (open graph, (st_dev, st_ino) of the file it was opened on)
_db_graph_cache: dict[str, tuple[GraphStore, tuple[int, int]]] = {}


@dataclass(frozen=True)
class CodeIntelDb:
    """One code-intel graph a project owns: the graph of one owned repo.

    ``repo`` names it (a binding's ``repo``; a local root's directory name);
    ``repo_root`` is the repo it is built from.
    """

    db_path: Path
    repo: str | None = None
    repo_root: Path | None = None


def owned_graphs(project_dir: str | Path) -> list[CodeIntelDb]:
    """The graph of every repo *project_dir* owns, built or not, in a fixed order.

    The ONE answer to "where is this project's code graph?": first each owned
    local root (``local_roots.confirmed``; its db is keyed by the real path,
    ``local_roots.db_path``), then on a Hive each binding's task clone
    (``local_roots.hive_task_clones``), then, in ``bindings.yaml`` order, each bind-managed
    clone with the db ``bind_repo`` builds beside it (``ddd_bindings.binding_db_path``).
    Internal / Brazil bindings (never cloned here) and bindings whose worktree lies
    outside the bindings root contribute nothing. Never raises.
    """
    project_dir = Path(project_dir)
    out: list[CodeIntelDb] = []
    seen: set[str] = set()
    names: set[str] = set()

    def _add(db: Path, repo: str, root: Path) -> None:
        key = str(db.resolve())
        if key in seen:
            return
        seen.add(key)
        name = repo
        while name in names:
            name += "-local"
        names.add(name)
        out.append(CodeIntelDb(db_path=db, repo=name, repo_root=root))

    try:
        from .local_roots import confirmed, db_path as local_db_path
        for root in confirmed(project_dir.name):
            _add(local_db_path(root), Path(root).name, Path(root))
    except Exception as exc:  # noqa: BLE001 — a bad registry must not break readers
        logger.warning("code_intel: local roots of %s unreadable: %s", project_dir.name, exc)
    from .local_roots import hive_db_path, hive_task_clones
    for repo, clone in hive_task_clones(project_dir):   # a Hive: the task clone (F8)
        _add(hive_db_path(clone), repo, clone)
    bindings_file = project_dir / "bindings.yaml"
    if not bindings_file.exists():
        return out
    try:
        from core.ddd_bindings import binding_db_path, binding_worktree, load_bindings
        doc = load_bindings(bindings_file)
    except Exception as exc:  # noqa: BLE001 — a bad bindings.yaml must not break readers
        logger.debug("code_intel: %s unreadable: %s", bindings_file, exc)
        return out
    for binding in doc.bindings:
        if binding.kind == "internal" or binding.delivery_contract.build_system == "brazil":
            continue
        try:
            db = binding_db_path(binding)
            worktree = binding_worktree(binding)
        except ValueError:
            continue  # self-hosted (outside the bindings root) or an invalid repo name
        _add(db, binding.repo, worktree)
    return out


def code_intel_dbs(project_dir: str | Path) -> list[CodeIntelDb]:
    """The project's graphs that exist (``owned_graphs`` order). Never raises."""
    return [ref for ref in owned_graphs(project_dir) if ref.db_path.exists()]


def code_intel_db_for(project_dir: str | Path) -> Path | None:
    """The project's first code-intel db that exists, or None."""
    dbs = code_intel_dbs(project_dir)
    return dbs[0].db_path if dbs else None


def load_project_graphs(project_name: str) -> list[tuple[CodeIntelDb, GraphStore]]:
    """Every code graph the project has (``code_intel_dbs`` order), opened once per db
    path and cached: the one way in for every reader and writer, so a project that
    binds several repos is searched, watched and re-indexed in all of them. A db that
    cannot be opened is skipped.

    A db file deleted and re-created gets a new store, and the old one is closed:
    ``GraphStore.close`` waits for a query in flight on it, and a later use fails
    loudly instead of writing into a deleted file (an unclosed old connection kept
    the deleted file and its WAL alive and could remove the new WAL by path when it
    was finally collected). The watcher re-resolves its store by db path each batch.
    """
    from jobs.paths import PROJECTS_DIR
    out: list[tuple[CodeIntelDb, GraphStore]] = []
    for ref in code_intel_dbs(PROJECTS_DIR / project_name):
        key = str(ref.db_path)
        stale = None
        with _cache_lock:
            # stat under the lock: a stat taken before another thread cached the
            # re-created file would read as a mismatch and evict that fresh store
            try:
                st = ref.db_path.stat()
            except OSError:
                continue
            ident = (st.st_dev, st.st_ino)
            cached = _db_graph_cache.get(key)
            if cached is not None and cached[1] != ident:
                stale = _db_graph_cache.pop(key)[0]
                cached = None
        if stale is not None:
            # outside the cache lock: close() waits for a query in flight on it
            try:
                stale.close()
            except Exception:  # noqa: BLE001
                logger.debug("closing the store of a re-created %s failed", key, exc_info=True)
        if cached is not None:
            graph = cached[0]
        else:
            try:
                from .graph_store import GraphStore
                graph = GraphStore(ref.db_path)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to open code graph %s: %s", ref.db_path, e)
                continue
            with _cache_lock:
                if key in _db_graph_cache:
                    graph.close()
                    graph = _db_graph_cache[key][0]
                else:
                    _db_graph_cache[key] = (graph, ident)
        out.append((ref, graph))
    return out


def project_graph(project_name: str, repo: str | None = None
                  ) -> tuple[CodeIntelDb, GraphStore] | None:
    """One of the project's graphs, for a reader that shows one at a time: the graph
    of the binding named *repo*, or the first (``code_intel_dbs`` order) when *repo*
    is None. None when there is no such graph."""
    graphs = load_project_graphs(project_name)
    if repo is None:
        return graphs[0] if graphs else None
    return next(((ref, g) for ref, g in graphs if ref.repo == repo), None)


def invalidate_cache(project_name: str | None = None):
    """Drop cached stores (one project's, or all) and close them.

    A reset for tests and tools, never called by the running app: closing a store
    another thread is querying breaks that query, and a re-bind is already handled by
    ``load_project_graphs`` (a re-created db file gets a new store)."""
    with _cache_lock:
        if project_name:
            try:
                from jobs.paths import PROJECTS_DIR
                keys = [str(r.db_path) for r in code_intel_dbs(PROJECTS_DIR / project_name)]
            except Exception as exc:  # noqa: BLE001 — nothing to evict when the dbs cannot be listed
                logger.debug("code_intel: listing %s's graph dbs failed: %s", project_name, exc)
                keys = []
            entries = [e for e in (_db_graph_cache.pop(k, None) for k in keys) if e]
        else:
            entries = list(_db_graph_cache.values())
            _db_graph_cache.clear()
    for graph, _ident in entries:
        try:
            graph.close()
        except Exception as exc:  # noqa: BLE001 — the entry is evicted anyway
            logger.debug("code_intel: closing evicted graph %s failed: %s", graph._db_path, exc)


# Entrypoint files scanned (in order) for FastAPI include_router() prefixes.
_PREFIX_ENTRYPOINT_CANDIDATES = (
    "backend/main.py", "main.py", "app.py", "src/main.py", "src/app.py",
    "server.py", "backend/app.py",
)


def resolve_router_prefixes(graph: GraphStore, repo_root: Path) -> int:
    """Apply FastAPI include_router prefix resolution to stored routes.

    ``build.routes_for`` stores routes as the decorator path only (e.g.
    ``/sessions``); the mount prefix (``/api/chat``) lives in the entrypoint's
    ``include_router()`` call. EVERY path that (re)stores routes (``build.rebuild``,
    ``build.routes_for``) must call this afterwards, or the stored path stays bare
    and a route query for ``/api/chat/sessions`` resolves to the wrong handler
    (GS_COD010 flake). Idempotent: an already-prefixed path is skipped.

    Returns the number of routes updated.
    """
    from .parser import resolves_inside
    from .route_parser import build_prefix_map, _make_route_id

    entrypoint_path = None
    entrypoint_content = None
    for candidate in _PREFIX_ENTRYPOINT_CANDIDATES:
        fp = repo_root / candidate
        if fp.exists() and resolves_inside(repo_root, fp):
            entrypoint_path = candidate
            entrypoint_content = fp.read_text(encoding="utf-8", errors="replace")
            break

    if not entrypoint_content:
        return 0

    prefix_map = build_prefix_map(entrypoint_content, entrypoint_path)
    if not prefix_map:
        return 0

    # Go through the store's _LockingConnection (proxy) — NOT a raw cursor — so
    # this read-modify-write is serialized against any concurrent reader/writer on
    # the shared connection (R1 race fix, run_071e54c8). Hold the store's RLock
    # across the whole SELECT→UPDATE*→commit sequence so it is atomic. RLock is
    # reentrant, so the proxy's own per-op acquires nest safely under this hold.
    import sqlite3

    updated = 0
    with graph._conn_lock:
        try:
            rows = graph._conn.execute(
                "SELECT id, method, path, file_path FROM code_routes"
            ).fetchall()

            for old_id, method, path, file_path in rows:
                prefix = prefix_map.get(file_path, "")
                if not prefix or path.startswith(prefix):
                    continue  # Already resolved or no prefix applies
                # Guard: if route path already contains the prefix as a substring,
                # it was likely applied inline via APIRouter(prefix=...) — skip
                if prefix.lstrip("/") in path:
                    continue

                new_path = prefix.rstrip("/") + "/" + path.lstrip("/") if path != "/" else prefix
                new_id = _make_route_id(file_path, method, new_path)

                try:
                    graph._conn.execute(
                        "UPDATE code_routes SET path = ?, id = ? WHERE id = ?",
                        (new_path, new_id, old_id)
                    )
                except sqlite3.IntegrityError:
                    # The prefixed row already exists (e.g. a watcher re-store
                    # raced a reindex): this bare row is its stale duplicate.
                    graph._conn.execute("DELETE FROM code_routes WHERE id = ?", (old_id,))
                updated += 1

            graph._conn.commit()
        except BaseException:
            # Never leave a half-applied transaction open on the shared connection.
            try:
                graph._conn.rollback()
            except Exception:  # noqa: BLE001 — the original error is what matters
                logger.debug("resolve_router_prefixes: rollback failed", exc_info=True)
            raise
    if updated:
        logger.info(f"Prefix resolution: updated {updated} routes")
    return updated


# Repo-path markers found across real TECH.md files, in priority order. Order is
# load-bearing: the labeled pattern is tried FIRST so a bold label always wins over
# an earlier bare-backtick path elsewhere in the doc; the bare-backtick fallback
# (re.MULTILINE, end-of-line anchored) catches the '## Codebase Location' + bare
# path-on-its-own-line format. Do NOT reorder or switch to findall — the first-match
# semantics are what keep a 400K TECH.md from resolving to a wrong inline path.
_REPO_PATH_PATTERNS = [
    re.compile(r"\*\*(?:Repo Path|Local|Codebase).*?:\*\*\s*`([^`]+)`"),
    re.compile(r"`(/[^`]+)`\s*$", re.MULTILINE),  # bare backtick path on a line
]


def extract_repo_path(content: str) -> str | None:
    """Extract a repo path from TECH.md *content*. PURE — no filesystem I/O.

    Tries the markers in `_REPO_PATH_PATTERNS` order, returning the FIRST matching
    pattern's captured path (raw, un-normalized — trailing slash preserved) or None.

    Supported formats (found in the wild):
    - ``**Repo Path:** `/path/to/repo` `` (legacy label)
    - ``**Local:** `/path/to/repo` `` (SwarmAI)
    - ``## Codebase Location`` heading + a bare `` `/path` `` line (ai_ready_repo)

    Callers own dir-validation (rstrip/resolve/is_dir) — keeping this pure lets it
    be unit-tested with plain strings and shared between the reindex endpoint and
    the project-path cache without coupling to any multi-project filtering.
    """
    for pattern in _REPO_PATH_PATTERNS:
        match = pattern.search(content)
        if match:
            return match.group(1)
    return None


def owned_repo_roots(project_dir) -> list[str]:
    """Every absolute local repo path project *project_dir* OWNS, deduplicated.

    Three declarations count, in this order:
      1. each owned local root (``local_roots.confirmed``): the user's own checkout,
         declared on this machine with ``bind --local``;
      1b. on a Hive, each binding's task clone (``local_roots.hive_task_clones``);
      2. each bind-managed clone (``ddd_bindings.binding_worktree``) that exists —
         binding the repo is itself the declaration. The clone is confined to one
         directory under the bindings root.
    Nothing a document says grants ownership: a TECH.md path (written by cultivation,
    carried by Brain sync) counted once, in the install-time migration
    (``local_roots.migrate_once``), and a free path in bindings.yaml
    (``bindings[].worktree`` outside the bindings root,
    ``governed_assets[].source_workspace``) counts for nothing: ownership decides what
    gets parsed into this brain (the cross-project contamination run_1950e67e guards
    against).
    Internal / Brazil bindings are never checked out here and own nothing local.
    Never raises; an empty list means "index nothing" (run_1950e67e).
    """
    out: list[str] = []

    def _add(p: str | Path | None) -> None:
        if not p:
            return
        try:
            resolved = str(Path(p).expanduser().resolve())
        except OSError:
            return
        if resolved not in out and Path(resolved).is_dir():
            out.append(resolved)

    from .local_roots import confirmed, hive_task_clones
    for root in confirmed(Path(project_dir).name):
        _add(root)
    for _repo, clone in hive_task_clones(project_dir):
        _add(clone)
    bindings_file = Path(project_dir) / "bindings.yaml"
    if bindings_file.exists():
        try:
            from core.ddd_bindings import binding_worktree, load_bindings
            for binding in load_bindings(bindings_file).bindings:
                if binding.kind == "internal" or binding.delivery_contract.build_system == "brazil":
                    continue
                try:
                    _add(binding_worktree(binding))
                except ValueError:
                    continue
        except Exception as exc:  # noqa: BLE001 — ownership must never raise into a caller
            logger.warning("owned-repo resolution from bindings failed for %s: %s", project_dir, exc)
    return out


def _tech_md_repo_root(project_dir) -> str | None:
    """Return the absolute repo path the project's TECH.md declares, or None.

    No longer an ownership declaration: the install-time migration
    (``local_roots.migrate_once``) reads it once, for a Brain with no local root. Reads
    ``extract_repo_path`` over ``ddd_path(project_dir, "TECH.md")``, resolved and
    confirmed to be a real local directory; None when TECH.md declares no local repo
    (e.g. a Brain whose bindings are all Brazil packages, never checked out here).

    Why it stopped deciding ownership: a TECH.md line is written by cultivation and
    travels with Brain sync, and a project whose TECH.md named another project's
    checkout had that checkout parsed into its graph (run_1950e67e).
    """
    try:
        tech_md = ddd_path(project_dir, "TECH.md")
        if not tech_md.exists():
            return None
        candidate = extract_repo_path(tech_md.read_text(encoding="utf-8"))
        if not candidate:
            return None
        # expanduser so a `~/repos/x` declaration resolves (else it becomes
        # <cwd>/~/repos/x, is_dir()=False → a legit project silently un-indexed).
        resolved = Path(candidate.rstrip("/")).expanduser().resolve()
        return str(resolved) if resolved.is_dir() else None
    except Exception as exc:  # noqa: BLE001 — ownership check must never raise into a caller
        # Degrade-OBSERVABLE. None means "this project owns no repo", i.e. exactly the
        # "a legit project silently un-indexed" outcome the expanduser comment above
        # guards against — reached here by a different route and, until now, with no
        # trace at all. Keep returning None (never raise into a caller), but say why.
        logger.warning("owned-repo resolution failed for %s: %s", project_dir, exc)
        return None


def repo_root_is_owned(project_dir, stored_repo_root: str | None) -> bool:
    """True iff ``stored_repo_root`` matches a repo the project OWNS.

    The gate a caller applies before trusting a stored ``repo_root`` (a library
    mount, the DDD completeness check). False when the project owns no local repo
    (``owned_repo_roots`` is empty) OR the stored value is none of them — either case
    means the db's ``repo_root`` is foreign and must NOT be indexed or watched
    (run_1950e67e).
    """
    owned = owned_repo_roots(project_dir)
    if not owned or not stored_repo_root:
        return False
    try:
        import os
        stored = Path(stored_repo_root.rstrip("/")).expanduser().resolve()
        for candidate in owned:
            owned_p = Path(candidate).resolve()
            if stored == owned_p:
                return True
            # Case-insensitive FS (macOS APFS/HFS+) or symlink: string-!= but SAME dir.
            # samefile compares inode identity — the ground truth "is it the same repo".
            if stored.is_dir() and owned_p.is_dir() and os.path.samefile(stored, owned_p):
                return True
        return False
    except Exception as exc:  # noqa: BLE001
        # Fail CLOSED on purpose — an ownership check that cannot decide must deny. But
        # the denial was invisible, so "why is my repo not indexed?" had no answer.
        logger.warning("ownership check failed for %s vs stored %r, denying: %s",
                       project_dir, stored_repo_root, exc)
        return False
