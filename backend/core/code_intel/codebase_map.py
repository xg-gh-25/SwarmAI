"""
Session start context — ~100 token codebase summary for proactive briefing.

Injected via proactive_intelligence.py at session start.
"""

from __future__ import annotations

import logging
import time

from . import load_project_graph

logger = logging.getLogger(__name__)


def generate_codebase_map(project_name: str) -> str | None:
    """
    ~100 tokens injected at session start.

    Format:
    📦 SwarmAI Codebase (Python 78%, TypeScript 22%)
    Modules: core(89fn) hooks(34fn) channels(28fn)
    Hot files (30d): session_unit.py(18), prompt_builder.py(12)
    Entry points: 14 API routes, 8 CLI commands
    Dead code: 23 unused exports
    Last indexed: 2min ago (incremental)
    """
    graph = load_project_graph(project_name)
    if not graph:
        return None

    try:
        summary = graph.get_codebase_summary()
        if not summary:
            return None
        return _format_briefing(project_name, summary)
    except Exception as e:
        logger.debug(f"Failed to generate codebase map for {project_name}: {e}")
        return None


def _format_briefing(project_name: str, summary: dict) -> str | None:
    """Format a codebase summary dict into a ~100 token briefing string."""
    lines = []

    # Header with language breakdown
    lang_breakdown = summary.get("languages", {})
    total_nodes = summary.get("total_nodes", 0)
    if not total_nodes:
        return None

    lang_parts = []
    for lang, count in sorted(lang_breakdown.items(), key=lambda x: -x[1]):
        pct = round(count / total_nodes * 100)
        if pct >= 5:
            lang_parts.append(f"{lang.title()} {pct}%")
    lang_str = ", ".join(lang_parts) if lang_parts else "mixed"
    lines.append(f"📦 {project_name} Codebase ({lang_str})")

    # Module summary
    modules = summary.get("modules", {})
    if modules:
        mod_parts = []
        for name, info in sorted(modules.items(), key=lambda x: -x[1].get("function_count", 0))[:6]:
            fn_count = info.get("function_count", 0)
            mod_parts.append(f"{name}({fn_count}fn)")
        lines.append(f"Modules: {' '.join(mod_parts)}")

    # Hot files (most connected)
    top_connected = summary.get("top_connected", [])
    if top_connected:
        hot_parts = []
        for item in top_connected[:5]:
            name = item.get("name", "?")
            callers = item.get("callers", 0)
            hot_parts.append(f"{name}({callers})")
        lines.append(f"Most connected: {', '.join(hot_parts)}")

    # Routes (top 10, ranked by info density — most navigable first)
    routes = summary.get("routes", [])
    if routes:
        ranked = _rank_routes(routes)
        route_parts = []
        for r in ranked[:10]:
            handler = r.get("handler_node_id", "").split("::")[-1] if "::" in r.get("handler_node_id", "") else r.get("handler_node_id", "?")
            route_parts.append(f"{r['method']} {r['path']} → {handler}")
        lines.append(f"Routes ({len(routes)} total): {', '.join(route_parts[:5])}")
        if len(route_parts) > 5:
            lines.append(f"  + {', '.join(route_parts[5:10])}")

    # Entry points and dead code
    entry_count = summary.get("entry_point_count", 0)
    dead_count = summary.get("dead_code_count", 0)
    total_edges = summary.get("total_edges", 0)

    stats = []
    if entry_count:
        stats.append(f"{entry_count} entry points")
    if dead_count:
        stats.append(f"{dead_count} unused exports")
    stats.append(f"{total_nodes} symbols, {total_edges} edges")
    lines.append(f"Stats: {', '.join(stats)}")

    # Last indexed
    last_indexed_str = summary.get("last_indexed")
    if last_indexed_str:
        try:
            last_ts = float(last_indexed_str)
            age_s = time.time() - last_ts
            if age_s < 120:
                age_str = f"{int(age_s)}s ago"
            elif age_s < 7200:
                age_str = f"{int(age_s / 60)}min ago"
            elif age_s < 172800:
                age_str = f"{int(age_s / 3600)}h ago"
            else:
                age_str = f"{int(age_s / 86400)}d ago"
            lines.append(f"Last indexed: {age_str}")
        except (ValueError, TypeError):
            pass

    return "\n".join(lines)


def _rank_routes(routes: list[dict]) -> list[dict]:
    """Rank routes by navigation value for the agent.

    Goal: show the routes an agent is MOST LIKELY TO NEED in its first actions.
    Strategy: diversity × specificity — cover different domains, avoid showing
    10 routes from the same file.

    Scoring:
    1. Path specificity (2-3 segments ideal — not too shallow, not too deep)
    2. Diversity bonus (first route per file_path gets +2)
    3. Penalize generic utility (/, /health) and overly deep CRUD variants
    """
    seen_files: set[str] = set()

    def _score(r: dict) -> float:
        path = r.get("path", "/")
        method = r.get("method", "GET")
        file_path = r.get("file_path", "")

        # Penalize generic utility routes
        generic_paths = {"/", "/health", "/status", "/ping", "/ready", "/metrics"}
        if path in generic_paths:
            return 0.1

        # Path specificity: 2-3 segments is the sweet spot
        segments = [s for s in path.split("/") if s and not s.startswith("{")]
        segment_score = min(len(segments), 3)  # Cap at 3 — deeper isn't more useful

        # Method variety: POST/PUT are actions, GET is discovery
        method_weights = {"POST": 1.3, "PUT": 1.2, "DELETE": 1.1, "PATCH": 1.1, "GET": 1.0}
        method_score = method_weights.get(method, 1.0)

        # Diversity: first route per source file gets a big bonus
        diversity_bonus = 2.0 if file_path not in seen_files else 0.0

        return segment_score * method_score + diversity_bonus

    # Score all, but track seen_files progressively (greedy diversity)
    scored = []
    for r in sorted(routes, key=lambda x: x.get("path", "")):
        score = _score(r)
        scored.append((score, r))
        seen_files.add(r.get("file_path", ""))

    # Re-sort by score descending
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored]
