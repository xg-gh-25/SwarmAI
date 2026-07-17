"""
Code Intelligence Platform — project-scoped code graph for pipeline & agent.

Public API:
    load_project_graph(project_name) → GraphStore | None
    detect_project_from_path(file_path) → str | None
    get_code_intel_db_path(project_name) → Path
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# Module-level caches (guarded by _cache_lock for thread safety)
_cache_lock = threading.Lock()
_graph_cache: dict[str, GraphStore] = {}
_project_path_cache: dict[str, str] = {}  # repo_path → project_name
_cache_initialized = False


def get_code_intel_db_path(project_name: str) -> Path:
    """Returns: ~/.swarm-ai/SwarmWS/Projects/{project}/code_intel.db"""
    from jobs.paths import PROJECTS_DIR
    return PROJECTS_DIR / project_name / "code_intel.db"


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


def detect_project_from_path(file_path: str) -> str | None:
    """
    Match a file path to a project by checking TECH.md repo_path fields.

    Performance: repo_path→project mapping built ONCE, cached at module level.
    Invalidated only on project create/delete.
    """
    global _cache_initialized
    with _cache_lock:
        if not _cache_initialized:
            _build_project_path_cache()
            _cache_initialized = True
        # Snapshot the cache under the lock for iteration safety.
        path_cache_snapshot = dict(_project_path_cache)

    resolved = str(Path(file_path).resolve())
    for repo_path, project_name in path_cache_snapshot.items():
        if resolved.startswith(repo_path):
            return project_name
    return None


def invalidate_cache(project_name: str | None = None):
    """Invalidate caches. Called on project create/delete. Closes evicted GraphStores."""
    global _cache_initialized
    with _cache_lock:
        if project_name:
            old = _graph_cache.pop(project_name, None)
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            to_remove = [k for k, v in _project_path_cache.items() if v == project_name]
            for k in to_remove:
                del _project_path_cache[k]
        else:
            for g in _graph_cache.values():
                try:
                    g.close()
                except Exception:
                    pass
            _graph_cache.clear()
            _project_path_cache.clear()
            _cache_initialized = False


def extract_and_store_routes(graph: GraphStore, file_path: str, content: str, language: str) -> int:
    """Extract routes from a file and store them in the graph database.

    Called after parse_file() produces nodes — extracts HTTP routes and
    persists them via graph_store.insert_routes().

    Args:
        graph: The GraphStore instance to store routes in.
        file_path: Relative file path.
        content: File content as string.
        language: Language identifier.

    Returns:
        Number of routes extracted and stored.
    """
    try:
        from .route_parser import extract_routes
        # Clear stale routes for this file before inserting fresh ones.
        # Without this, removed decorators would leave phantom routes.
        graph.delete_routes_for_file(file_path)
        routes = extract_routes(file_path, content, language)
        if routes:
            graph.insert_routes(routes)
            return len(routes)
    except Exception as e:
        logger.debug(f"Route extraction failed for {file_path}: {e}")
    return 0


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


def _build_project_path_cache():
    """Scan Projects/*/TECH.md for repo_path fields (uses `extract_repo_path`)."""
    from jobs.paths import PROJECTS_DIR
    projects_dir = PROJECTS_DIR
    if not projects_dir.is_dir():
        return

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        tech_md = project_dir / "TECH.md"
        if not tech_md.exists():
            continue
        try:
            content = tech_md.read_text(encoding="utf-8")
            candidate = extract_repo_path(content)
            if candidate:
                resolved = str(Path(candidate.rstrip("/")).resolve())
                if Path(resolved).is_dir():
                    _project_path_cache[resolved] = project_dir.name
        except Exception:
            continue
