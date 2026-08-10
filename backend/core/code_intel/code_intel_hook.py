"""
PreToolUse hook — injects dependency context when agent reads code files.

Triggers on: Read, Grep (when path is inside a project's repo)
Injects:
  - Read: symbols, callers, routes, risk score, blast radius preview
  - Grep: if pattern matches a URL path, returns handler file:line directly

Cache: graph_store loaded once per session, lazy on first tool call.
Latency target: <50ms per injection (SQLite indexed query).
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from . import detect_project_from_path, load_project_graph

logger = logging.getLogger(__name__)

# URL path pattern: starts with /, has at least one segment
_URL_PATH_RE = re.compile(r"^/[a-zA-Z0-9_\-/{}\.:]+$")


def create_code_intel_hook():
    """
    Create a PreToolUse hook that injects dependency context.

    Returns a callable hook function compatible with Claude Agent SDK hooks.

    Deduplication (2026-06-01): Injects context for a given file only ONCE
    per session. Repeated Read/Grep on the same file returns approve-only
    (no additionalContext). This prevents ~200 tokens × N repeated accesses
    from eating into the task_budget and triggering premature autocompact.
    Evidence: session 59b18ce8 had the same file's context injected 25 times
    (5000 tokens wasted). With 128K task_budget, that's 4% burned on noise.
    """
    _cache: dict[str, Any] = {}  # project_name → GraphStore
    _seen_files: set[str] = set()  # files already annotated this session

    def hook(tool_name: str, tool_input: dict) -> dict:
        """
        Hook signature: (tool_name, tool_input) → dict.
        Returns {"decision": "approve"} always, with optional additionalContext
        via hookSpecificOutput for Read/Grep on indexed projects.

        Per-session dedup: each file gets context injected only on FIRST access.
        """
        if tool_name not in ("Read", "Grep"):
            return {"decision": "approve"}

        file_path = tool_input.get("file_path") or tool_input.get("path", "")

        # ── Grep: route query shortcut ──────────────────────────────────
        if tool_name == "Grep":
            pattern = tool_input.get("pattern", "")
            # If grep pattern looks like a URL path, try route lookup
            if pattern and _URL_PATH_RE.match(pattern):
                context = _route_query(pattern, _cache)
                if context:
                    return {
                        "decision": "approve",
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "additionalContext": context,
                        }
                    }
            # For Grep, also try file-based context if path is provided
            if not file_path:
                return {"decision": "approve"}

        if not file_path:
            return {"decision": "approve"}

        # ── Dedup: skip if already injected for this file ──────────────
        # Normalize to avoid path variants (./ prefix, trailing /, etc.)
        norm_path = str(Path(file_path).resolve())
        if norm_path in _seen_files:
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
                # Mark as seen AFTER successful build — failed builds can retry
                _seen_files.add(norm_path)
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


def _route_query(url_path: str, cache: dict) -> str | None:
    """If url_path matches a known route, return handler location directly.

    This is the O(1) "give me a path, get the handler" shortcut.
    Agent greps for '/api/chat/send' → gets 'routers/chat.py::send_message (line 42)'.
    """
    # Try all cached projects
    for project_name, graph in cache.items():
        result = _search_routes_in_graph(graph, url_path)
        if result:
            return result

    # Try all projects (may not be cached yet)
    from . import _project_path_cache, _build_project_path_cache, _cache_initialized
    if not _cache_initialized:
        _build_project_path_cache()

    for project_name in set(_project_path_cache.values()):
        if project_name in cache:
            continue  # Already tried
        graph = _get_or_load_graph(project_name, cache)
        if not graph:
            continue
        result = _search_routes_in_graph(graph, url_path)
        if result:
            return result

    return None


def _search_routes_in_graph(graph, url_path: str) -> str | None:
    """Search for a URL path in a graph's routes. Supports exact and suffix match.

    Skips test files (routes from test mocks pollute results).
    """
    try:
        routes = graph.get_routes()
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE. None reads as "that URL is not a known route", which is a
        # legitimate answer — so a broken graph is indistinguishable from a genuine miss.
        logger.debug("route lookup unavailable for %r: %s", url_path, exc)
        return None

    # Filter out test file routes
    real_routes = [r for r in routes if "test" not in r.get("file_path", "").lower()]

    # Exact match first
    for r in real_routes:
        if r.get("path") == url_path:
            return _format_route_match(r, url_path)

    # Suffix match: /api/system/health → match /health (router mounts add prefix)
    path_suffix = "/" + url_path.rstrip("/").split("/")[-1] if "/" in url_path else url_path
    for r in real_routes:
        if r.get("path", "").endswith(path_suffix) or url_path.endswith(r.get("path", "")):
            return _format_route_match(r, url_path)

    # Substring match: query is contained in route path (not the other way —
    # avoids "/" matching everything)
    for r in real_routes:
        route_path = r.get("path", "")
        # Only match if the route path is specific enough (>= half the query length)
        if len(route_path) >= len(url_path) // 2 and url_path in route_path:
            return _format_route_match(r, url_path)

    return None


def _format_route_match(r: dict, query: str) -> str:
    """Format a route match result."""
    handler = r.get("handler_node_id", "")
    line = r.get("line_number", "?")
    file_path = r.get("file_path", "")
    method = r.get("method", "?")
    path = r.get("path", query)
    return (
        f"🎯 Route Match: {method} {path}\n"
        f"  Handler: {handler} (line {line})\n"
        f"  File: {file_path}"
    )


def _build_context(graph, file_path: str, project: str) -> str | None:
    """Build context injection string for a file.

    Includes: symbols, routes, risk assessment, blast radius preview.
    """
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

    # ── Risk assessment ─────────────────────────────────────────────────
    if total_callers >= 5:
        risk = _compute_file_risk(graph, rel_path, nodes, total_callers)
        if risk:
            lines.append(f"  ⚠️ Risk: {risk['level']} ({risk['reason']})")

    # ── Blast radius preview (top callers) ──────────────────────────────
    if total_callers >= 5:
        top_callers = _get_top_callers(graph, rel_path, limit=3)
        if top_callers:
            caller_strs = []
            for c in top_callers:
                tested = "✓" if c["has_test"] else "✗ untested"
                caller_strs.append(f"{c['name']} ({tested})")
            lines.append(f"  Blast radius (top callers): {', '.join(caller_strs)}")

    # ── Route context ───────────────────────────────────────────────────
    try:
        file_routes = graph.get_routes(file_path=rel_path)
        if file_routes:
            route_strs = []
            for r in file_routes[:5]:
                handler_name = r.get("handler_node_id", "").split("::")[-1] if "::" in r.get("handler_node_id", "") else r.get("handler_node_id", "")
                route_strs.append(f"{r['method']} {r['path']} → {handler_name}()")
            route_line = "  Routes: " + ", ".join(route_strs)
            if len(file_routes) > 5:
                route_line += f" ... and {len(file_routes) - 5} more"
            lines.append(route_line)
    except Exception:
        pass  # Routes table may not exist in older DBs

    return "\n".join(lines)


def _compute_file_risk(graph, rel_path: str, nodes: list, total_callers: int) -> dict | None:
    """Compute a simple risk score for a file.

    Dimensions: caller_count × test_gap × churn (via git commits if available).
    Returns: {"level": "HIGH", "reason": "28 callers, 3 untested symbols"}
    """
    # Check how many symbols have test callers
    tested_count = 0
    untested_count = 0
    for n in nodes:
        node_id = n.get("id", "") if isinstance(n, dict) else ""
        if not node_id:
            continue
        # A symbol is "tested" if any of its callers is in a test file
        try:
            callers = graph._conn.execute(
                "SELECT source_id FROM code_edges WHERE target_id = ? AND edge_type = 'calls' LIMIT 20",
                (node_id,)
            ).fetchall()
            has_test_caller = any("test" in c[0].lower() for c in callers)
            if has_test_caller:
                tested_count += 1
            elif n.get("is_export", True):
                untested_count += 1
        except Exception:
            continue

    # Risk level
    if total_callers >= 20 and untested_count >= 5:
        level = "CRITICAL"
        reason = f"{total_callers} callers, {untested_count} untested exports"
    elif total_callers >= 10 and untested_count >= 3:
        level = "HIGH"
        reason = f"{total_callers} callers, {untested_count} untested exports"
    elif total_callers >= 5 and untested_count >= 2:
        level = "MEDIUM"
        reason = f"{total_callers} callers, {untested_count} untested exports"
    elif total_callers >= 5:
        level = "LOW"
        reason = f"{total_callers} callers, well tested"
    else:
        return None

    return {"level": level, "reason": reason}


def _get_top_callers(graph, rel_path: str, limit: int = 3) -> list[dict]:
    """Get top N callers of symbols in this file, with test coverage flag.

    Returns: [{"name": "session_router.py::route_message", "has_test": True}, ...]
    """
    try:
        # Get all node IDs in this file
        node_ids = [
            row[0] for row in graph._conn.execute(
                "SELECT id FROM code_nodes WHERE file_path = ?", (rel_path,)
            ).fetchall()
        ]
        if not node_ids:
            return []

        # Find callers across all nodes in this file, deduplicated by source file
        placeholders = ",".join("?" * len(node_ids))
        caller_rows = graph._conn.execute(
            f"SELECT DISTINCT e.source_id, n.file_path, n.name "
            f"FROM code_edges e "
            f"JOIN code_nodes n ON n.id = e.source_id "
            f"WHERE e.target_id IN ({placeholders}) "
            f"AND e.edge_type = 'calls' "
            f"AND n.file_path != ? "
            f"ORDER BY n.file_path "
            f"LIMIT ?",
            node_ids + [rel_path, limit * 3],  # fetch extra, then deduplicate
        ).fetchall()

        # Deduplicate by file (show one caller per file)
        seen_files: set[str] = set()
        results = []
        for source_id, caller_file, caller_name in caller_rows:
            if caller_file in seen_files:
                continue
            seen_files.add(caller_file)
            has_test = "test" in caller_file.lower()
            # For non-test callers, check if THEY have test callers
            if not has_test:
                test_check = graph._conn.execute(
                    "SELECT 1 FROM code_edges e "
                    "JOIN code_nodes n ON n.id = e.source_id "
                    "WHERE e.target_id = ? AND n.file_path LIKE '%test%' LIMIT 1",
                    (source_id,)
                ).fetchone()
                has_test = test_check is not None
            results.append({
                "name": f"{caller_file.split('/')[-1]}::{caller_name}",
                "has_test": has_test,
            })
            if len(results) >= limit:
                break

        return results
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE. [] reads as "nothing calls this file" — the single most
        # misleading answer a caller-graph query can give, since it invites the reader to
        # treat the file as dead code.
        logger.debug("top-callers lookup failed for %s: %s", rel_path, exc)
        return []
