"""Codebase analysis — computed on demand from a project's code graph.

What a repo's graph says about its shape, for any project and any language the parser
reads: the structure (modules, routes, entry points, module coupling, central symbols,
risk areas, dead code, packages), the topology analysis (god nodes, couplings across
structural clusters, questions the graph cannot settle), the structural domain
decomposition (``graph_clusters``) and the per-domain business-rule manifest
(``domain_rules``, the on-box analogue of AWS Transform's business-rules extraction).

It is computed from ``code_intel.db`` every time it is asked for and never stored, so
it cannot go stale (a stored copy drifts from the graph — design
2026-10-27-new-brain-initial-ddd.md §5.9).

Entry points (one function behind each):
    analyze_graph_store(graph, layers=None) → dict      the library call
    analyze_project(project, layers=None) → dict | None the project's graph (resolver)
    GET /api/code-intel/{project}/analysis?layers=…     routers/code_intel.py
    python -m core.code_intel.analysis <project> [--layers a,b] [--repo R] [--out FILE]

Pure read: nothing here writes to the graph or to disk (the CLI writes only the
``--out`` file the caller names).

Contract: the result carries ``schema`` = ``SCHEMA`` and ``version`` = ``VERSION``.
Adding a field keeps the version; renaming or removing one bumps it. Top-level keys:
``schema, version, generated_at, project, repo? (the bound repo analyzed), repo_root
(local path), indexed_commit, layers, <one key per layer>, errors?``. A layer too large to compute safely reports
``{"skipped": reason}`` content instead of loading the whole graph.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

SCHEMA = "swarmai.code-intel.analysis"
VERSION = 1

# Every layer, in the order they are computed (graph_analysis reads graph_clusters).
LAYERS: tuple[str, ...] = ("structure", "graph_clusters", "graph_analysis", "domain_rules")

_DEAD_CODE_CAP = 100      # a readable list, not a dump
_MODULE_FILES_CAP = 20


def resolve_layers(layers: Iterable[str] | None) -> list[str]:
    """The requested layers in computation order; None, empty or only blanks = all.
    Raises ValueError naming an unknown layer."""
    wanted = {name.strip() for name in (layers or []) if name and name.strip()}
    if not wanted:
        return list(LAYERS)
    unknown = sorted(wanted - set(LAYERS))
    if unknown:
        raise ValueError(f"unknown analysis layer(s): {', '.join(unknown)} "
                         f"(known: {', '.join(LAYERS)})")
    return [name for name in LAYERS if name in wanted]


def analyze_graph_store(graph: "GraphStore", layers: Iterable[str] | None = None,
                        top_n: int = 10) -> dict:
    """Compute the requested analysis layers from *graph*. Never writes.

    Each layer is computed independently: one that fails is reported under
    ``errors`` (layer → message) and the others are still returned — a partial
    answer that says what is missing, never a silent one.
    """
    names = resolve_layers(layers)
    repo_root = graph.get_meta("repo_root")
    out: dict = {
        "schema": SCHEMA,
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": repo_root,
        "indexed_commit": graph.get_meta("last_indexed_commit"),
        "layers": names,
    }
    errors: dict[str, str] = {}
    shared: dict = {}  # one get_full_graph() shared by clusters + rules

    def _full_graph():
        """The materialized graph, or None above clustering's size guard — then each
        layer applies its own guard (clusters report ``skipped``)."""
        if "g" not in shared:
            from .clustering import _MAX_EDGES
            shared["g"] = None if graph.count_edges() > _MAX_EDGES else graph.get_full_graph()
        return shared["g"]

    for name in names:
        try:
            if name == "structure":
                out["structure"] = _structure(graph, repo_root)
            elif name == "graph_clusters":
                from .clustering import compute_graph_clusters
                out["graph_clusters"] = compute_graph_clusters(graph, _graph=_full_graph())
            elif name == "graph_analysis":
                from .graph_analysis import analyze_graph
                clusters = out.get("graph_clusters")
                if clusters is None:
                    from .clustering import compute_graph_clusters
                    clusters = compute_graph_clusters(graph, _graph=_full_graph())
                risk = (out.get("structure") or {}).get("risk_areas")
                if risk is None:
                    risk = _risk_areas(graph.get_codebase_summary().get("top_connected", []))
                out["graph_analysis"] = analyze_graph(
                    graph, clusters=clusters, risk_areas=risk, top_n=top_n)
            elif name == "domain_rules":
                full = _full_graph()
                if full is None:
                    # compute_domain_rules has no size guard of its own: never let a
                    # request load a graph clustering refuses to load.
                    from .clustering import _MAX_EDGES
                    out["domain_rules"] = {"domains": [], "rules": [], "skipped":
                                           f"edge count exceeds {_MAX_EDGES}"}
                else:
                    from .domain_rules import compute_domain_rules
                    out["domain_rules"] = compute_domain_rules(
                        graph, repo_root=repo_root, _graph=full,
                        clusters=out.get("graph_clusters"))
        except Exception as exc:  # noqa: BLE001 — one layer must not lose the others
            logger.warning("code-intel analysis: layer %s failed: %s", name, exc)
            errors[name] = f"{type(exc).__name__}: {str(exc)[:200]}"
    if errors:
        out["errors"] = errors
    return out


def analyze_project(project: str, layers: Iterable[str] | None = None,
                    repo: str | None = None) -> dict | None:
    """Analyze one of *project*'s code graphs (``project_graph``: the bound repo
    *repo*, else the first); None when there is no such graph."""
    names = resolve_layers(layers)  # validate before opening anything
    from . import project_graph
    found = project_graph(project, repo)
    if found is None:
        return None
    ref, graph = found
    result = analyze_graph_store(graph, names)
    result["project"] = project
    if ref.repo:
        result["repo"] = ref.repo
    if "structure" in result:
        result["structure"]["repo"]["name"] = ref.repo or project
    return result


# ── structure layer ──────────────────────────────────────────────────────────

def _structure(graph: "GraphStore", repo_root: str | None) -> dict:
    summary = graph.get_codebase_summary()
    module_map = graph.get_module_map()
    top = summary.get("top_connected", [])
    out = {
        "repo": {
            "languages": summary.get("languages", {}),
            "total_symbols": summary.get("total_nodes", 0),
            "total_edges": summary.get("total_edges", 0),
        },
        "modules": _modules(module_map, summary.get("modules", {})),
        "routes": _routes(graph.get_routes()),
        "entry_points": _entry_points(module_map),
        # Which 2-level module calls which (aggregated from the symbol edges).
        "module_edges": graph.get_module_edges(),
        # Symbols most called from elsewhere. Not "hot zones": fan-in says nothing
        # about how often the code changes (design 2026-10-27 §5.8 G5).
        "central_symbols": [
            {"name": t.get("name", ""), "file_path": t.get("file_path", ""),
             "callers": t.get("callers", 0)} for t in top],
        "risk_areas": _risk_areas(top),
        "dead_code": [
            {"name": d.get("name", ""), "file_path": d.get("file_path", ""),
             "type": d.get("node_type", "function")}
            for d in graph.find_dead_code()[:_DEAD_CODE_CAP]],
    }
    packages = _packages(repo_root)
    if packages is not None:
        out["packages"] = packages
    out["coverage"] = _coverage(graph)
    return out


def _coverage(graph: "GraphStore") -> dict:
    """What the last FULL parse could not read (recorded by the reindex job).

    As of that parse (``as_of_commit``): incremental updates do not re-measure it.
    ``status`` is "complete", "partial" or "unknown" (the graph was built by a path
    that does not record coverage, e.g. a bind); ``holes`` lists the files skipped.
    """
    status = graph.get_meta("parse_status") or "unknown"
    holes: list = []
    raw = graph.get_meta("coverage_holes")
    if raw:
        try:
            holes = json.loads(raw)
        except ValueError:
            status = "unknown"
    if holes and status == "complete":
        status = "partial"
    return {"status": status, "holes": holes, "as_of_commit": graph.get_meta("coverage_commit")}


def _modules(module_map: dict[str, list[dict]], module_stats: dict[str, dict]) -> list[dict]:
    modules = []
    for name, nodes in module_map.items():
        stats = module_stats.get(name, {})
        files = sorted(set(n.get("file_path", "") for n in nodes))
        modules.append({
            "name": name,
            "symbol_count": len(nodes),
            "function_count": stats.get("function_count", sum(
                1 for n in nodes if n.get("node_type") in ("function", "method"))),
            "class_count": stats.get("class_count", sum(
                1 for n in nodes if n.get("node_type") == "class")),
            "file_count": stats.get("file_count", len(files)),
            "files": files[:_MODULE_FILES_CAP],
        })
    return modules


def _routes(routes: list[dict]) -> list[dict]:
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


def is_test_path(file_path: str, name: str) -> bool:
    """A test entry point (test_*, *.test.*, conftest, TestX/Benchmark), not an
    architectural one. The parser marks these is_entry_point=1 for its own purpose;
    in a structure report they bury the real entries (main/cli/app)."""
    fp = file_path.lower()
    fname = fp.rsplit("/", 1)[-1]
    # Path SEGMENTS / filename patterns — not a bare "test" substring, which would
    # drop real entries in attestation.py / latest_run.py / contest/engine.py.
    segments = fp.split("/")
    if "tests" in segments or "test" in segments or "__tests__" in segments:
        return True
    if (fname.startswith("test_") or fname.endswith("_test.py")
            or fname == "conftest.py" or ".test." in fname or ".spec." in fname):
        return True
    if name.startswith("test_") or name.startswith("Test") or name.startswith("Benchmark"):
        return True
    return False


def _entry_points(module_map: dict[str, list[dict]]) -> list[dict]:
    entry_points = []
    for nodes in module_map.values():
        for n in nodes:
            if not n.get("is_entry_point"):
                continue
            if is_test_path(n.get("file_path", ""), n.get("name", "")):
                continue
            entry_points.append({
                "name": n.get("name", ""),
                "file_path": n.get("file_path", ""),
                "type": n.get("node_type", "function"),
            })
    return entry_points


def _risk_areas(top_connected: list[dict]) -> list[dict]:
    """High fan-in symbols: a change there propagates widely."""
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


def _packages(repo_root: str | None) -> list[dict] | None:
    """Package boundaries of a monorepo ([{name, root: "."}] for a single package),
    from ``core.brain_bootstrap.packages``. None when it cannot run."""
    if not repo_root:
        return None
    try:
        from core.brain_bootstrap.packages import detect_packages
        return detect_packages(repo_root)
    except Exception as exc:  # noqa: BLE001 — navigation metadata, never fatal
        logger.debug("packages partition skipped: %s: %s", type(exc).__name__, exc)
        return None


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.code_intel.analysis",
        description="Analyze a project's code graph (computed now, never stored).")
    ap.add_argument("project", help="project name (Projects/<name>)")
    ap.add_argument("--layers", default="",
                    help=f"comma-separated subset of: {', '.join(LAYERS)} (default: all)")
    ap.add_argument("--repo", default=None,
                    help="a bound repo's graph (its binding name); default: the first")
    ap.add_argument("--out", default=None, help="write the JSON here instead of stdout")
    args = ap.parse_args(argv)
    try:
        names = resolve_layers(args.layers.split(",") if args.layers else None)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = analyze_project(args.project, names, repo=args.repo)
    if result is None:
        what = f"repo {args.repo!r}" if args.repo else "code graph"
        print(f"{args.project}: no {what} (bind a repo, or index the project's own)",
              file=sys.stderr)
        return 1
    if args.repo is None:
        from . import load_project_graphs
        others = [ref.repo for ref, _g in load_project_graphs(args.project)[1:] if ref.repo]
        if others:
            print(f"{args.project} also has the graphs of: {', '.join(others)} "
                  "(analyze one with --repo)", file=sys.stderr)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
