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
# authored by the s_repo-to-ddd skill (LLM classification + finalize_v3) and lives
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
        # module_edges = the architectural skeleton (which module calls which),
        # aggregated from code_edges to 2-level module pairs (run_4344d341). NOT a
        # raw per-symbol edge dump — that would bloat the readable JSON ~10x. The
        # per-symbol graph lives in code_intel.db for callers that need it.
        "module_edges": graph_store.get_module_edges(),
        "hot_zones": hot_zones,
        "risk_areas": risk_areas,
        "dead_code": _build_dead_code(dead_code),
        "dependencies": dependencies,
    }

    # ── packages[] partition (multi-package / monorepo navigation, run_a9fe5ad3) ──
    # Additive key: a monorepo yields one entry per detected package boundary; a
    # single-package repo yields exactly [{name, root: "."}]. Derived from the SAME
    # skill helper the skill/GENERATE producer uses (build_packages_partition), via
    # the identical lazy fail-open sys.path import the v3 validation below relies on
    # (core→skill is the ONE legal direction; skill→core stays forbidden — C046).
    # Fail-open: a detection failure must NEVER corrupt the export — packages[] is
    # navigation metadata, not a coverage guarantee, so absence degrades gracefully.
    _pkg_repo_root = graph_store.get_meta("repo_root") if hasattr(graph_store, "get_meta") else None
    if _pkg_repo_root:
        try:
            from importlib import import_module
            import sys as _sys
            _skill_scripts = str(Path(__file__).resolve().parents[2]
                                 / "skills" / "s_repo-to-ddd" / "scripts")
            if _skill_scripts not in _sys.path:
                _sys.path.insert(0, _skill_scripts)
            _arh_pkg = import_module("ai_ready_helpers")
            doc["packages"] = _arh_pkg.build_packages_partition(_pkg_repo_root)
        except Exception as e:  # noqa: BLE001 — fail-open by design (nav metadata)
            logger.debug("packages[] partition skipped: %s: %s", type(e).__name__, e)

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

    # ── prune stale v3 refs (Gate-2 F5) BEFORE validation so a deleted route's
    # lingering unclassified id doesn't trip the anti-fabrication guard ──
    if v3_preserved:
        _prune_stale_v3_refs(doc)

    # ── stamp each domain's spec_hash (run_fe26ed6c, §8 loop-liveness) ──
    # The SINGLE source of the spec-details staleness hash lives in the skill
    # (ai_ready_helpers._spec_content_hash). We compute it HERE — the one place
    # domains+flows+steps are all in hand — and stamp it onto each domain, so
    # freshness.detect_spec_details_staleness can decide staleness by CONTENT
    # (not mtime) without importing the skill (C046) or recomputing the hash
    # (Gate-1 F1b no-two-writer-drift). Same sys.path import the v3 validation
    # below already relies on; fail-open (a stamping failure must never corrupt
    # the export — the detector just treats an unstamped domain as unjudgeable).
    if v3_preserved and doc.get("domains"):
        _stamp_spec_hashes(doc)

    # ── coverage_ledger + status stamp (F19: never a silent under-report) ──
    holes = list(coverage_holes or [])
    status = "partial" if (holes or parse_status == "partial") else "complete"

    # ── Gate-2 F2 (KEYSTONE, CRITICAL): the reindex→export path is the ONLY writer
    # of code-intel.json on the automated on:git_commit job, and it previously NEVER
    # re-ran the v3 gates — so a preserved-but-now-inconsistent v3 layer (e.g. a
    # moved route orphaning a flow.entry_ref, an id-less route) would ship stamped
    # "complete" with NOTHING enforcing the coverage invariant until a human re-ran
    # the skill. A "complete" stamp must never outrun validation. If the doc carries
    # a v3 layer, validate it here and DOWNGRADE to partial + record the failure as a
    # coverage hole when it doesn't hold. ──
    if v3_preserved:
        try:
            from importlib import import_module
            import sys as _sys
            _skill_scripts = str(Path(__file__).resolve().parents[2]
                                 / "skills" / "s_repo-to-ddd" / "scripts")
            if _skill_scripts not in _sys.path:
                _sys.path.insert(0, _skill_scripts)
            _arh = import_module("ai_ready_helpers")
            repo_root = graph_store.get_meta("repo_root") if hasattr(graph_store, "get_meta") else None
            v3_errors = _arh.validate_code_intel_json(doc, repo_root=repo_root)
        except Exception as e:
            # Fail-SAFE: if validation itself can't run, that is NOT a clean bill of
            # health — mark partial + a hole, never silently "complete".
            v3_errors = [f"v3 validation could not run at export: {type(e).__name__}: {e}"]
        if v3_errors:
            status = "partial"
            holes.append({
                "ref": "code-intel.json", "kind": "repo",
                "reason": ("v3 coverage layer failed validation at export time — "
                           "accounted_ratio is NOT trustworthy until re-generated: "
                           + "; ".join(v3_errors[:3]))[:500],
            })

    if holes:
        doc["coverage_ledger"] = holes

    # ── graph_analysis: topology "understanding" layer (run_dd13fb03, §24.2) ──
    # Additive key. Computed AFTER the v3 domains/risk_areas are in `doc` (surprising
    # connections derive a file→domain map from them). Fail-open: analysis is a
    # convenience layer, never a reason to sink the whole export (O030 spirit).
    try:
        from .graph_analysis import analyze_graph
        doc["graph_analysis"] = analyze_graph(doc, graph_store)
    except Exception as e:  # noqa: BLE001 — one analysis error must not lose the doc
        logger.warning("graph_analysis failed (non-fatal, doc still exported): %s", e)

    # ── graph_clusters + domain_rules: structural domain decomposition + rules ──
    # Additive keys, parallel to graph_analysis. graph_clusters detects bounded
    # structural domains (run_93e78bcd); domain_rules turns each into anchored,
    # machine-readable business rules (run_28a8f99d) — the on-box analogue of AWS
    # Transform business-rules-extraction. Both are GRAPH-DERIVED and recomputed on
    # every export (NOT in _V3_PRESERVED_KEYS) so they can never go stale — a
    # coverage/rule number stored in a regenerated artifact is a lie (run_afa86bd9).
    # One get_full_graph() is materialized here and SHARED by both (Gate-1 M1: no
    # double load). Fail-open — a convenience layer, never a reason to sink export.
    try:
        from .clustering import compute_graph_clusters
        _shared_graph = graph_store.get_full_graph()
        doc["graph_clusters"] = compute_graph_clusters(graph_store, _graph=_shared_graph)
        try:
            from .domain_rules import compute_domain_rules
            _dr_root = graph_store.get_meta("repo_root") if hasattr(graph_store, "get_meta") else None
            doc["domain_rules"] = compute_domain_rules(
                graph_store, repo_root=_dr_root, _graph=_shared_graph)
        except Exception as e:  # noqa: BLE001
            logger.warning("domain_rules failed (non-fatal, doc still exported): %s", e)
    except Exception as e:  # noqa: BLE001
        logger.warning("graph_clusters failed (non-fatal, doc still exported): %s", e)

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

        # If STILL over, drop the per-cluster member_ids from graph_clusters (the
        # bulk of its size — full node id lists for every cluster). Keep the
        # summary fields (cluster_id/kind/size/cohesion/entry_points) + the
        # extraction_candidates, which is what a consumer actually acts on. Without
        # this graph_clusters is uncapped and can be several× _MAX_SIZE_BYTES alone
        # on a large repo (Gate-2 #7, run_93e78bcd).
        if len(content.encode("utf-8")) > _MAX_SIZE_BYTES:
            gc = doc.get("graph_clusters")
            if isinstance(gc, dict):
                for c in gc.get("clusters", []):
                    c.pop("member_ids", None)
                    c["members_trimmed"] = True
                content = json.dumps(doc, indent=2, ensure_ascii=False)

        # If STILL over, slim domain_rules.rules to their IDENTITY fields (rule_id,
        # domain_id, source_symbol, operation, target_data_object, disposition) and
        # drop the verbose anchor object. rules can be thousands of entries on a
        # large monolith and otherwise has no trim (Gate-2 MED, run_28a8f99d). The
        # domains[] summary + rule identity survive; anchors are recoverable by
        # re-running compute_domain_rules (graph-derived). Flag rules_trimmed.
        if len(content.encode("utf-8")) > _MAX_SIZE_BYTES:
            dr = doc.get("domain_rules")
            if isinstance(dr, dict) and dr.get("rules"):
                for r in dr["rules"]:
                    r.pop("anchor", None)
                dr["rules_trimmed"] = True
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
    """Re-attach v3 anchor ids onto freshly-built routes so flow.entry_ref keeps
    resolving across a reindex (the v2 graph does not persist route ids).

    Matching: (method, path, file_path) against the prior doc. Mutates built_routes
    in place. EVERY built route ends with an id:
    - a prior match → reuse the prior id (keeps flow.entry_ref stable);
    - NO prior match (a NEW or moved/renamed route) → mint a FRESH deterministic id
      via derive_route_id, so the route is NEVER id-less.

    Gate-2 F1 (run AB adversarial, CRITICAL): a route left id-less is silently
    excluded from the coverage denominator (extract_entry_anchors skips id-less
    entries) → a moved route would VANISH and accounted_ratio would falsely read 1.0.
    Minting an id for unmatched routes keeps them IN the denominator; if they carry
    no flow/unclassified entry they surface as a real coverage hole via
    check_anchor_accounting — visible, never silently accepted."""
    # derive_route_id lives in the s_repo-to-ddd skill (not importable from core —
    # C046 core-must-not-import-skill). Mint a fresh id inline that is BYTE-IDENTICAL
    # to ai_ready_helpers.derive_route_id so a later skill run (backfill_route_ids)
    # produces the SAME id for the same route → a moved route re-matches by id and its
    # flow.entry_ref keeps resolving. MUST stay in lockstep with derive_route_id:
    # slug = re.sub([^a-z0-9]+,-, "{method} {path}".lower()).strip(-);
    # h = sha1("{method}|{path}|{file_path}")[:8] (32-bit non-crypto id, NO [:60] cap).
    import hashlib as _hashlib
    import re as _re

    def _mint_id(method: str, path: str, file_path: str) -> str:
        m, p, fp = method or "", path or "", file_path or ""
        slug = _re.sub(r"[^a-z0-9]+", "-", f"{m} {p}".lower()).strip("-")
        key = f"{m}|{p}|{fp}"
        h = _hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        return f"route:{slug}-{h}"

    prior_by_key: dict[tuple, str] = {}
    for r in (prior_routes or []):
        if not isinstance(r, dict) or not r.get("id"):
            continue
        key = (r.get("method"), r.get("path"), r.get("file_path"))
        prior_by_key.setdefault(key, r["id"])  # first-wins on dup key (stable)
    for r in built_routes:
        if r.get("id"):
            continue
        key = (r.get("method"), r.get("path"), r.get("file_path"))
        r["id"] = prior_by_key.get(key) or _mint_id(*key)


def _stamp_spec_hashes(doc: dict) -> None:
    """Stamp each domain with `spec_hash` = the content-hash of its rendered spec
    skeleton (domain + its flows + steps). The hash function is the SKILL's single
    source (ai_ready_helpers._spec_content_hash) — imported via the same sys.path
    the v3 validation uses. Mutates doc in place. Fail-open: any import/compute
    error leaves domains unstamped (detector treats an unstamped domain as
    unjudgeable → never a false-positive), never corrupts the export."""
    try:
        import sys as _sys
        _skill_scripts = str(Path(__file__).resolve().parents[2]
                             / "skills" / "s_repo-to-ddd" / "scripts")
        if _skill_scripts not in _sys.path:
            _sys.path.insert(0, _skill_scripts)
        from importlib import import_module
        _arh = import_module("ai_ready_helpers")
        spec_hash = _arh._spec_content_hash
    except Exception as e:  # noqa: BLE001 — fail-open by design (documented above)
        logger.warning("spec_hash stamping skipped (import failed): %s", e)
        return
    flows = doc.get("flows") or []
    steps = doc.get("steps") or []
    for dom in doc.get("domains") or []:
        if not isinstance(dom, dict):
            continue
        try:
            dom["spec_hash"] = spec_hash(dom, flows, steps)
        except Exception as e:  # noqa: BLE001 — one bad domain must not sink the rest
            logger.warning("spec_hash compute failed for %s: %s", dom.get("id"), e)


def _prune_stale_v3_refs(doc: dict) -> None:
    """Drop unclassified[] / flow.entry_ref entries whose route id no longer exists
    in the current routes[] (Gate-2 F5): a deleted/moved route leaves a stale
    unclassified id that check_anchor_accounting would flag as 'fabricated anchor' —
    the wrong signal (it's a removed route, not a hallucination). Prune so the doc
    stays internally consistent across reindexes. Mutates doc in place."""
    current_ids = {r.get("id") for r in (doc.get("routes") or [])
                   if isinstance(r, dict) and r.get("id")}
    if not current_ids:
        return
    uncls = doc.get("unclassified")
    if isinstance(uncls, list):
        doc["unclassified"] = [u for u in uncls
                               if isinstance(u, dict) and u.get("id") in current_ids]


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


def _is_test_path(file_path: str, name: str) -> bool:
    """A test entry point (test_*, *.test.*, conftest, TestX/Benchmark) — a TEST
    entry, not an ARCHITECTURAL one. run_4344d341: the parser marks these
    is_entry_point=1 (correctly, for its own purpose), but they dominate the count
    (~11.2K of 11.3K) and are noise in the exported architectural entry_points."""
    fp = file_path.lower()
    fname = fp.rsplit("/", 1)[-1]
    # Anchor to path SEGMENTS / filename patterns — NOT a bare "test" substring,
    # which false-excludes real entries like attestation.py / latest_run.py /
    # contest/engine.py (Gate-2 MEDIUM, run_4344d341: "any-repo" skill must not
    # silently drop a real main in such a file).
    segments = fp.split("/")
    if "tests" in segments or "test" in segments or "__tests__" in segments:
        return True
    if (fname.startswith("test_") or fname.endswith("_test.py")
            or fname == "conftest.py" or ".test." in fname or ".spec." in fname):
        return True
    if name.startswith("test_") or name.startswith("Test") or name.startswith("Benchmark"):
        return True
    return False


def _build_entry_points(module_map: dict[str, list[dict]]) -> list[dict]:
    """Extract ARCHITECTURAL entry points (is_entry_point nodes) from the module
    map, EXCLUDING test entries. run_4344d341: is_entry_point=1 covers ~11.3K nodes
    (mostly test_* functions the parser flags as test entries); dumping all of them
    bloated code-intel.json ~7.5x (244K→1.8MB) and buried the ~117 real entries
    (main/cli/app) that a reader actually wants. Test entries stay in the DB (the
    parser's is_entry_point is unchanged); this export just surfaces the
    architectural ones."""
    entry_points = []
    for nodes in module_map.values():
        for n in nodes:
            if not n.get("is_entry_point"):
                continue
            if _is_test_path(n.get("file_path", ""), n.get("name", "")):
                continue
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
