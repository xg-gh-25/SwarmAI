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
