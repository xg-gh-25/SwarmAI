"""
PreToolUse hook — injects dependency context when agent reads code files.

Triggers on: Read, Grep (when path is inside a project's repo)
Injects: ~100 tokens of code intelligence context
Cache: graph_store loaded once per session, lazy on first tool call.
Latency target: <50ms per injection (SQLite indexed query).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from . import detect_project_from_path, load_project_graph

logger = logging.getLogger(__name__)


def create_code_intel_hook():
    """
    Create a PreToolUse hook that injects dependency context.

    Returns a callable hook function compatible with Claude Agent SDK hooks.
    """
    _cache: dict[str, Any] = {}  # project_name → GraphStore

    def hook(tool_name: str, tool_input: dict) -> dict:
        """
        Hook signature: (tool_name, tool_input) → dict.
        Returns {"decision": "approve"} always, with optional additionalContext
        via hookSpecificOutput for Read/Grep on indexed projects.
        """
        if tool_name not in ("Read", "Grep"):
            return {"decision": "approve"}

        file_path = tool_input.get("file_path") or tool_input.get("path", "")
        if not file_path:
            return {"decision": "approve"}

        # Detect project from file path
        project = detect_project_from_path(file_path)
        if not project:
            return {"decision": "approve"}

        try:
            # Load graph (cached per session)
            graph = _get_or_load_graph(project, _cache)
            if not graph:
                return {"decision": "approve"}

            start_time = time.monotonic()

            context = _build_context(graph, file_path, project)
            elapsed_ms = (time.monotonic() - start_time) * 1000

            if elapsed_ms > 50:
                logger.warning(f"code_intel hook took {elapsed_ms:.0f}ms for {file_path}")

            if context:
                return {
                    "decision": "approve",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": context,
                    }
                }
        except Exception as e:
            logger.debug(f"code_intel hook error for {file_path}: {e}")

        return {"decision": "approve"}

    return hook


def _get_or_load_graph(project: str, cache: dict) -> Any:
    """Load graph store, cached per project."""
    if project in cache:
        return cache[project]

    graph = load_project_graph(project)
    if graph:
        cache[project] = graph
    return graph


def _build_context(graph, file_path: str, project: str) -> str | None:
    """Build context injection string for a file."""
    repo_root = graph.get_meta("repo_root")
    if not repo_root:
        return None

    # Convert to relative path
    try:
        rel_path = str(Path(file_path).resolve().relative_to(Path(repo_root).resolve()))
    except (ValueError, OSError):
        return None

    # Find nodes in this file
    nodes = graph.get_nodes_by_file(rel_path)
    if not nodes:
        return None

    # Build context — nodes are dicts with "node_type", "name", etc.
    node_types: dict[str, int] = {}
    for n in nodes:
        ntype = n.get("node_type", "?") if isinstance(n, dict) else "?"
        node_types[ntype] = node_types.get(ntype, 0) + 1

    type_summary = ", ".join(f"{count} {t}" for t, count in sorted(node_types.items()))

    # Count callers — returns {node_id: caller_count}
    caller_counts = graph.count_callers_by_file(rel_path)
    total_callers = sum(caller_counts.values()) if isinstance(caller_counts, dict) else 0
    nodes_with_callers = sum(1 for v in caller_counts.values() if v > 0) if isinstance(caller_counts, dict) else 0

    # Get module
    parts = rel_path.split("/")
    module = parts[1] if len(parts) > 2 else parts[0] if parts else "root"

    lines = [
        f"📊 Code Intel: {rel_path}",
        f"  Symbols: {len(nodes)} ({type_summary})",
        f"  Incoming edges: {total_callers} callers on {nodes_with_callers}/{len(nodes)} symbols",
        f"  Module: {module}",
    ]

    return "\n".join(lines)
