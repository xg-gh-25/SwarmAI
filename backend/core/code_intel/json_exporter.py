"""
JSON Exporter for Code Intelligence v2 format.

Exports the graph database to code-intel.json v2 format. Called after full
reindex completion to produce a portable, schema-conforming JSON snapshot.

Output schema: https://ai-ready-repo.dev/schemas/code-intel.v2.json
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# Maximum output size in bytes (500KB). If exceeded, trim dead_code section.
_MAX_SIZE_BYTES = 500 * 1024

# The v3 business-semantic layer keys the exporter must PRESERVE across a reindex.
# The graph store only knows v2 structure (modules/routes/nodes); the v3 layer is
# authored by the s_ai-ready-repo skill (LLM classification + finalize_v3) and lives
# ONLY in the on-disk code-intel.json. A naive v2 overwrite wipes it → a backfilled
# accounted_ratio=1.0 silently reverts to 4.8% on the next commit (Gate-1 Check-2:
# the FALSE-100% banking red line). So we read the prior doc and re-attach these.
_V3_PRESERVED_KEYS = ("domains", "flows", "steps", "unclassified")


def export_code_intel_json(
    graph_store: 'GraphStore',
    project_name: str,
    output_path: Path,
    coverage_holes: list[dict] | None = None,
    parse_status: str = "complete",
) -> Path:
    """Export the graph database to code-intel.json (v2, preserving any v3 layer).

    Args:
        graph_store: The GraphStore instance to export from.
        project_name: Human-readable project name.
        output_path: Where to write the JSON file.
        coverage_holes: Optional file/repo-level coverage holes from
            parser.parse_repo_with_coverage — written to doc['coverage_ledger'] and,
            when non-empty, force status="partial" (coverage is NOT complete when the
            parser could not read part of the repo). Never a silent under-report.
        parse_status: "complete" | "partial" from the parse phase (oversized repo,
            missing files). Combined with coverage_holes into the doc's status stamp.

    Returns:
        Path to the written file.

    ROOT FIX (Run AB Cycle 3): this used to blindly overwrite with a v2-only doc,
    wiping the v3 domains/flows/steps/unclassified layer + route ids on every
    reindex. It now (1) PRESERVES the prior v3 layer, (2) re-attaches prior route
    ids so flow.entry_ref keeps resolving, (3) writes ATOMICALLY (tmp+os.replace,
    F19) so an interrupted write never corrupts the prior file, (4) stamps an
    explicit status: complete|partial.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Read the prior doc (for v3-layer + route-id preservation). Fail-safe:
    # a missing/corrupt prior file → treat as no-prior, export fresh (never crash). ──
    prior: dict = {}
    if output_path.exists():
        try:
            prior = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(prior, dict):
                prior = {}
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Prior code-intel.json unreadable ({e}); exporting fresh")
            prior = {}

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

    # Build routes, re-attaching prior route ids so flow.entry_ref keeps resolving
    # (the v2 graph does not persist the v3 anchor ids — they live only on disk).
    built_routes = _build_routes(routes)
    _reattach_route_ids(built_routes, prior.get("routes"))

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
        "routes": built_routes,
        "entry_points": entry_points,
        "hot_zones": hot_zones,
        "risk_areas": risk_areas,
        "dead_code": _build_dead_code(dead_code),
        "dependencies": dependencies,
    }

    # ── PRESERVE the v3 business-semantic layer from the prior doc (Gate-1 Check-2
    # ROOT fix). Without this a reindex silently reverts a backfilled coverage layer. ──
    v3_preserved = False
    for key in _V3_PRESERVED_KEYS:
        if prior.get(key):
            doc[key] = prior[key]
            v3_preserved = True
    if v3_preserved:
        # A doc carrying the v3 layer IS a v3 doc — bump the version so downstream
        # v3 validation (validate_code_intel_json) actually runs on it.
        doc["version"] = "3.0"
        doc["$schema"] = "https://ai-ready-repo.dev/schemas/code-intel.v3.json"

    # ── coverage_ledger + status stamp (F19: never a silent under-report) ──
    holes = coverage_holes or []
    if holes:
        doc["coverage_ledger"] = holes
    status = "partial" if (holes or parse_status == "partial") else "complete"
    doc["status"] = status

    # Serialize and check size cap
    content = json.dumps(doc, indent=2, ensure_ascii=False)

    if len(content.encode("utf-8")) > _MAX_SIZE_BYTES:
        # Trim dead_code section first (least critical). NEVER trim the v3 layer or
        # coverage_ledger — those are the coverage guarantee, not disposable padding.
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

    # ── F19: ATOMIC write (tmp + os.replace). An interrupted/failed write must never
    # leave a half-written file or corrupt the prior one. os.replace is atomic within
    # a filesystem; the .tmp sibling is cleaned up on failure. ──
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, output_path)
    except Exception:
        # Clean up the partial temp so no .tmp leftover; prior file stays intact
        # (os.replace either fully succeeded or never touched output_path).
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    logger.info(
        f"Exported code-intel.json for {project_name} "
        f"({len(content)} bytes, {len(modules)} modules, {len(routes)} routes, "
        f"status={status}, v3_layer={'preserved' if v3_preserved else 'none'}, "
        f"holes={len(holes)})"
    )
    return output_path


def _reattach_route_ids(built_routes: list[dict], prior_routes: list[dict] | None) -> None:
    """Re-attach v3 anchor ids from the prior doc onto freshly-built routes, matched
    by (method, path, file_path). The v2 graph does not persist route ids, so without
    this every flow.entry_ref (which points at a route id) is orphaned on reindex.

    Mutates built_routes in place. Routes with no prior match keep no id (they are
    NEW routes the next generation pass will id+classify — surfaced as coverage holes
    by check_anchor_accounting, never silently accepted)."""
    if not prior_routes:
        return
    prior_by_key: dict[tuple, str] = {}
    for r in prior_routes:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        key = (r.get("method"), r.get("path"), r.get("file_path"))
        prior_by_key[key] = r["id"]
    for r in built_routes:
        key = (r.get("method"), r.get("path"), r.get("file_path"))
        if key in prior_by_key and not r.get("id"):
            r["id"] = prior_by_key[key]


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
