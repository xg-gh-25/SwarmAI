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


def _build_project_path_cache():
    """Scan Projects/*/TECH.md for repo_path fields."""
    from jobs.paths import PROJECTS_DIR
    projects_dir = PROJECTS_DIR
    if not projects_dir.is_dir():
        return

    repo_path_pattern = re.compile(r"\*\*Repo Path:\*\*\s*`([^`]+)`")

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        tech_md = project_dir / "TECH.md"
        if not tech_md.exists():
            continue
        try:
            content = tech_md.read_text(encoding="utf-8")
            match = repo_path_pattern.search(content)
            if match:
                repo_path = str(Path(match.group(1)).resolve())
                _project_path_cache[repo_path] = project_dir.name
        except Exception:
            continue
