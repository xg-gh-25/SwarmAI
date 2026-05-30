"""
JSON Exporter for Code Intelligence v2 format.

Exports the graph database to code-intel.json v2 format. Called after full
reindex completion to produce a portable, schema-conforming JSON snapshot.

Output schema: https://ai-ready-repo.dev/schemas/code-intel.v2.json
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# Maximum output size in bytes (500KB). If exceeded, trim dead_code section.
_MAX_SIZE_BYTES = 500 * 1024


def export_code_intel_json(
    graph_store: 'GraphStore',
    project_name: str,
    output_path: Path,
) -> Path:
    """Export the graph database to code-intel.json v2 format.

    Args:
        graph_store: The GraphStore instance to export from.
        project_name: Human-readable project name.
        output_path: Where to write the JSON file.

    Returns:
        Path to the written file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Gather data from graph store
    summary = graph_store.get_codebase_summary()
    module_map = graph_store.get_module_map()
    routes = graph_store.get_routes()
    dead_code = graph_store.find_dead_code()

    # Build modules list
    modules = _build_modules(module_map, summary.get("modules", {}))

    # Build entry points from nodes marked as entry points
    entry_points = _build_entry_points(module_map)

    # Build hot zones from top connected
    hot_zones = _build_hot_zones(summary.get("top_connected", []))

    # Build risk areas (high fan-in nodes)
    risk_areas = _build_risk_areas(summary.get("top_connected", []))

    # Build dependencies (language breakdown as proxy)
    dependencies = _build_dependencies(summary.get("languages", {}))

    # Assemble the v2 document
    doc = {
        "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
        "version": "2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": {
            "name": project_name,
            "languages": summary.get("languages", {}),
            "total_symbols": summary.get("total_nodes", 0),
            "total_edges": summary.get("total_edges", 0),
        },
        "modules": modules,
        "routes": _build_routes(routes),
        "entry_points": entry_points,
        "hot_zones": hot_zones,
        "risk_areas": risk_areas,
        "dead_code": _build_dead_code(dead_code),
        "dependencies": dependencies,
    }

    # Serialize and check size cap
    content = json.dumps(doc, indent=2, ensure_ascii=False)

    if len(content.encode("utf-8")) > _MAX_SIZE_BYTES:
        # Trim dead_code section first (least critical)
        doc["dead_code"] = []
        content = json.dumps(doc, indent=2, ensure_ascii=False)

        # If still over, trim modules to top-20 by symbol count
        if len(content.encode("utf-8")) > _MAX_SIZE_BYTES:
            doc["modules"] = sorted(
                doc["modules"],
                key=lambda m: m.get("symbol_count", 0),
                reverse=True,
            )[:20]
            content = json.dumps(doc, indent=2, ensure_ascii=False)

    output_path.write_text(content, encoding="utf-8")
    logger.info(
        f"Exported code-intel.json for {project_name} "
        f"({len(content)} bytes, {len(modules)} modules, {len(routes)} routes)"
    )
    return output_path


def _build_modules(
    module_map: dict[str, list[dict]],
    module_stats: dict[str, dict],
) -> list[dict]:
    """Convert module_map + stats into v2 modules list."""
    modules = []
    for name, nodes in module_map.items():
        stats = module_stats.get(name, {})
        files = sorted(set(n.get("file_path", "") for n in nodes))
        modules.append({
            "name": name,
            "symbol_count": len(nodes),
            "function_count": stats.get("function_count", sum(
                1 for n in nodes if n.get("node_type") in ("function", "method")
            )),
            "class_count": stats.get("class_count", sum(
                1 for n in nodes if n.get("node_type") == "class"
            )),
            "file_count": stats.get("file_count", len(files)),
            "files": files[:20],  # Cap file list per module
        })
    return modules


def _build_routes(routes: list[dict]) -> list[dict]:
    """Format routes for v2 output."""
    return [
        {
            "method": r.get("method", "GET"),
            "path": r.get("path", ""),
            "handler": r.get("handler_node_id", ""),
            "framework": r.get("framework", ""),
            "file_path": r.get("file_path", ""),
            "line_number": r.get("line_number"),
            "middleware": r.get("middleware"),
        }
        for r in routes
    ]


def _build_entry_points(module_map: dict[str, list[dict]]) -> list[dict]:
    """Extract entry points (is_entry_point nodes) from module map."""
    entry_points = []
    for nodes in module_map.values():
        for n in nodes:
            if n.get("is_entry_point"):
                entry_points.append({
                    "name": n.get("name", ""),
                    "file_path": n.get("file_path", ""),
                    "type": n.get("node_type", "function"),
                })
    return entry_points


def _build_hot_zones(top_connected: list[dict]) -> list[dict]:
    """Convert top-connected summary into hot_zones."""
    return [
        {
            "name": item.get("name", ""),
            "file_path": item.get("file_path", ""),
            "callers": item.get("callers", 0),
        }
        for item in top_connected
    ]


def _build_risk_areas(top_connected: list[dict]) -> list[dict]:
    """High fan-in nodes are risk areas (change propagation risk)."""
    return [
        {
            "name": item.get("name", ""),
            "file_path": item.get("file_path", ""),
            "risk_score": min(1.0, item.get("callers", 0) / 20.0),
            "reason": f"High fan-in: {item.get('callers', 0)} callers",
        }
        for item in top_connected
        if item.get("callers", 0) >= 5
    ]


def _build_dead_code(dead_code: list[dict]) -> list[dict]:
    """Format dead code entries."""
    return [
        {
            "name": d.get("name", ""),
            "file_path": d.get("file_path", ""),
            "type": d.get("node_type", "function"),
        }
        for d in dead_code[:100]  # Cap at 100 entries
    ]


def _build_dependencies(languages: dict[str, int]) -> dict:
    """Build dependencies section from available data."""
    return {
        "language_distribution": languages,
    }
