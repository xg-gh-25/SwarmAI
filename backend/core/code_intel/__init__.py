"""
Code Intelligence Platform — project-scoped code graph for pipeline & agent.

Public API:
    load_project_graph(project_name) → GraphStore | None
    code_intel_dbs(project_dir) → list[CodeIntelDb]   (project db + bind-managed dbs)
    code_intel_db_for(project_dir) → Path | None      (the primary one)
    get_code_intel_db_path(project_name) → Path
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
_graph_cache: dict[str, GraphStore] = {}
# db path → (open graph, (st_dev, st_ino) of the file it was opened on)
_db_graph_cache: dict[str, tuple[GraphStore, tuple[int, int]]] = {}


@dataclass(frozen=True)
class CodeIntelDb:
    """One code-intel graph a project owns.

    ``repo`` is the binding's repo name, or None for the project's own
    ``Projects/<p>/code_intel.db``; ``repo_root`` is the bind-managed clone for a
    binding db (None for the project db, whose repo root lives in its graph_meta).
    """

    db_path: Path
    repo: str | None = None
    repo_root: Path | None = None


def code_intel_dbs(project_dir: str | Path) -> list[CodeIntelDb]:
    """Every code-intel db that exists for *project_dir*, in a fixed order.

    The ONE answer to "where is this project's code graph?". First the project's own
    ``code_intel.db`` (built by the reindex job / daemon for self-hosted repos), then,
    in ``bindings.yaml`` order, the db ``bind_repo`` builds beside each bind-managed
    clone (``ddd_bindings.binding_db_path``). Before this, every reader looked only
    at ``Projects/<p>/code_intel.db``, so a graph built by binding a repo — the New
    Brain path — was invisible to recall, Brain Hub, the watcher and the reindex job.

    Internal / Brazil bindings (never cloned here) and bindings whose worktree lies
    outside the bindings root (self-hosted checkouts) contribute no binding db.
    Never raises: an unreadable bindings.yaml yields just the project db, if any.
    """
    project_dir = Path(project_dir)
    out: list[CodeIntelDb] = []
    own = project_dir / "code_intel.db"
    if own.exists():
        out.append(CodeIntelDb(db_path=own))
    bindings_file = project_dir / "bindings.yaml"
    if not bindings_file.exists():
        return out
    try:
        from core.ddd_bindings import binding_db_path, binding_worktree, load_bindings
        doc = load_bindings(bindings_file)
    except Exception as exc:  # noqa: BLE001 — a bad bindings.yaml must not break readers
        logger.debug("code_intel_dbs: %s unreadable: %s", bindings_file, exc)
        return out
    seen = {own.resolve()} if own.exists() else set()
    for binding in doc.bindings:
        if binding.kind == "internal" or binding.delivery_contract.build_system == "brazil":
            continue
        try:
            db_path = binding_db_path(binding)
            worktree = binding_worktree(binding)
        except ValueError:
            continue  # self-hosted (outside the bindings root) or an invalid repo name
        if db_path.exists() and db_path.resolve() not in seen:
            seen.add(db_path.resolve())
            out.append(CodeIntelDb(db_path=db_path, repo=binding.repo, repo_root=worktree))
    return out


def code_intel_db_for(project_dir: str | Path) -> Path | None:
    """The project's primary code-intel db (first of ``code_intel_dbs``), or None.

    Readers that work with one graph per project use this. A project binding several
    repos is served by its first db; merging several graphs in one read is not done
    here.
    """
    dbs = code_intel_dbs(project_dir)
    return dbs[0].db_path if dbs else None


def get_code_intel_db_path(project_name: str) -> Path:
    """The project's primary code-intel db path (``code_intel_db_for``).

    Returns the project's own ``Projects/<p>/code_intel.db`` path when the project has
    no db at all, so callers that check ``.exists()`` keep working.
    """
    from jobs.paths import PROJECTS_DIR
    project_dir = PROJECTS_DIR / project_name
    return code_intel_db_for(project_dir) or project_dir / "code_intel.db"


def load_project_graph(project_name: str) -> GraphStore | None:
    """
    Load the graph for a project. Returns None if no code_intel.db exists.
    Cached at module level (same pattern as SessionRecall).
    """
    with _cache_lock:
        if project_name in _graph_cache:
            return _graph_cache[project_name]

    db_path = get_code_intel_db_path(project_name)
    if not db_path.exists():
        return None

    try:
        from .graph_store import GraphStore
        graph = GraphStore(db_path)
        with _cache_lock:
            # Double-check: another thread may have populated the cache.
            if project_name in _graph_cache:
                graph.close()
                return _graph_cache[project_name]
            _graph_cache[project_name] = graph
        return graph
    except Exception as e:
        logger.warning(f"Failed to load code_intel for {project_name}: {e}")
        return None


def load_project_graphs(project_name: str) -> list[tuple[CodeIntelDb, GraphStore]]:
    """Every code graph the project has (``code_intel_dbs`` order), opened once per db
    path and cached. The multi-repo counterpart of ``load_project_graph`` (which
    returns the first only): a project that binds several repos is searched in all
    of them. A db that cannot be opened is skipped."""
    from jobs.paths import PROJECTS_DIR
    out: list[tuple[CodeIntelDb, GraphStore]] = []
    for ref in code_intel_dbs(PROJECTS_DIR / project_name):
        key = str(ref.db_path)
        try:
            st = ref.db_path.stat()
            ident = (st.st_dev, st.st_ino)
        except OSError:
            continue
        with _cache_lock:
            cached = _db_graph_cache.get(key)
            if cached is not None and cached[1] != ident:
                # the file was deleted and re-created (a re-bind): the old handle
                # still reads the old graph
                _db_graph_cache.pop(key, None)
                try:
                    cached[0].close()
                except Exception as exc:  # noqa: BLE001 — a stale handle is dropped anyway
                    logger.debug("code_intel: closing a stale graph handle failed: %s", exc)
                cached = None
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


def invalidate_cache(project_name: str | None = None):
    """Invalidate caches. Called on project create/delete. Closes evicted GraphStores."""
    with _cache_lock:
        if project_name:
            old = _graph_cache.pop(project_name, None)
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            try:
                from jobs.paths import PROJECTS_DIR
                paths = [str(r.db_path) for r in code_intel_dbs(PROJECTS_DIR / project_name)]
            except Exception as exc:  # noqa: BLE001 — nothing to evict when the dbs cannot be listed
                logger.debug("code_intel: listing %s's graph dbs failed: %s", project_name, exc)
                paths = []
            for key in paths:
                entry = _db_graph_cache.pop(key, None)
                if entry:
                    try:
                        entry[0].close()
                    except Exception as exc:  # noqa: BLE001 — the entry is evicted anyway
                        logger.debug("code_intel: closing evicted graph %s failed: %s", key, exc)
        else:
            for g in list(_graph_cache.values()) + [e[0] for e in _db_graph_cache.values()]:
                try:
                    g.close()
                except Exception:
                    pass
            _graph_cache.clear()
            _db_graph_cache.clear()


# Entrypoint files scanned (in order) for FastAPI include_router() prefixes.
_PREFIX_ENTRYPOINT_CANDIDATES = (
    "backend/main.py", "main.py", "app.py", "src/main.py", "src/app.py",
    "server.py", "backend/app.py",
)


def resolve_router_prefixes(graph: GraphStore, repo_root: Path) -> int:
    """Apply FastAPI include_router prefix resolution to stored routes.

    ``build.routes_for`` stores routes as the decorator path only (e.g.
    ``/sessions``); the mount prefix (``/api/chat``) lives in the entrypoint's
    ``include_router()`` call. EVERY path that (re)stores routes — the reindex job
    AND the FS watcher — must call this afterwards, or the stored path stays bare
    and a route query for ``/api/chat/sessions`` resolves to the wrong handler
    (GS_COD010 flake). Idempotent: an already-prefixed path is skipped.

    Returns the number of routes updated.
    """
    from .route_parser import build_prefix_map, _make_route_id

    entrypoint_path = None
    entrypoint_content = None
    for candidate in _PREFIX_ENTRYPOINT_CANDIDATES:
        fp = repo_root / candidate
        if fp.exists():
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

    Two declarations count, in this order:
      1. the repo path its own TECH.md declares (``_tech_md_repo_root``, legacy);
      2. each bind-managed clone (``ddd_bindings.binding_worktree``) that exists —
         binding the repo is itself the declaration, no TECH.md path is needed. The
         clone is confined to one directory under the bindings root.
    A free path in bindings.yaml (``bindings[].worktree`` outside the bindings root,
    ``governed_assets[].source_workspace``) does NOT make a repo owned: bindings.yaml
    may be written from repo content, and ownership decides what gets indexed into
    this brain (the cross-project contamination run_1950e67e guards against).
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

    _add(_tech_md_repo_root(project_dir))
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


def resolve_owned_repo_root(project_dir) -> str | None:
    """The project's first owned repo path (``owned_repo_roots``), or None."""
    roots = owned_repo_roots(project_dir)
    return roots[0] if roots else None


def _tech_md_repo_root(project_dir) -> str | None:
    """Return the absolute repo path the project's TECH.md declares, or None.

    The legacy declaration: whatever its own TECH.md declares (via
    ``extract_repo_path`` over ``ddd_path(project_dir, "TECH.md")``), resolved and
    confirmed to be a real local directory. Returns None when TECH.md declares
    no local repo — e.g. a data-agent / internal-repo DDD whose bindings are all
    ``worktree: null`` (code.amazon.com/* Brazil packages, never checked out here).

    This is the OWNERSHIP oracle used to validate a code_intel.db's stored
    ``repo_root`` before we index or watch it. It exists because nothing previously
    checked that a db's ``repo_root`` belonged to its project: IVTHub's db carried
    ``repo_root=<SwarmAI source>`` and the startup watcher happily watched the
    SwarmAI tree, re-indexing SwarmAI's files into IVTHub's brain on every save
    (self-perpetuating content contamination — a Principle-1 violation: IVTHub's
    brain described SwarmAI's code). A ``None`` result means "index NOTHING", never
    "trust whatever repo_root is already in the db" (run_1950e67e).
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


def graph_holds_repo(graph: GraphStore, repo_root: str | Path) -> bool:
    """True iff *graph* was built from *repo_root* (its stored ``repo_root``).

    A writer that feeds files by repo-relative path must check this first: two repos
    can both have ``src/main.py``, and an incremental re-store replaces whatever the
    graph holds at that path. Never raises; an unknown stored root is False.
    """
    try:
        import os
        stored = graph.get_meta("repo_root")
        if not stored:
            return False
        a = Path(stored).expanduser().resolve()
        b = Path(repo_root).expanduser().resolve()
        return a == b or (a.is_dir() and b.is_dir() and os.path.samefile(a, b))
    except Exception:  # noqa: BLE001
        return False


def repo_root_is_owned(project_dir, stored_repo_root: str | None) -> bool:
    """True iff ``stored_repo_root`` matches a repo the project OWNS.

    The gate the index trigger sites (startup watcher, reindex job, health hook) apply
    before trusting a db's ``repo_root``. False when the project owns no local repo
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
