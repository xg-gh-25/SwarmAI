"""Topology analysis over the code-intel graph — god-nodes, surprising
connections, suggested questions.

Ported from graphify's ``analyze.py`` (§24.2 of the AI-Ready-Repo design;
run_dd13fb03). graphify computes these purely from graph topology with zero LLM;
we do the same, adapted to our data model. Three analyses, each a bounded top-N
(never a raw dump — the point is a readable "understanding" layer, not volume):

- **god_nodes** — degree-centrality ranking of the most-connected SYMBOLS, with
  file-level hub nodes excluded so they don't displace real abstractions
  (graphify `god_nodes`, analyze.py:100). Gate-1 (run_dd13fb03): this MUST be
  symbol-level (from ``code_edges`` degree), NOT ``module_edges`` — module-pair
  degree is god-*modules*, a category error.

- **surprising_connections** — symbol edges whose two endpoints sit in DIFFERENT
  structural communities (``graph_clusters``, kind=community) — a cross-cutting
  concern or a leak — so it exists for every repo the parser reads. Symbols outside
  every community are unmapped, and the result carries a ``coverage`` stat so a
  tiny edge list reads as "low coverage", not "no coupling".

- **suggested_questions** — DOMAIN-INDEPENDENT (Gate-1: domains are often absent).
  Derived from low-confidence (AMBIGUOUS/INFERRED) edges ("is this dependency
  real?") + risk_areas ("this hub has huge fan-in — is it a refactor risk?").

The module is a pure consumer: ``analyze_graph(graph_store, clusters=…,
risk_areas=…)`` reads the symbol graph and returns a dict; ``analysis`` (the
on-demand codebase analysis) wires it in as the ``graph_analysis`` layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

# Confidence <= this is "low" (bare/ambiguous/regex-inferred) — the seed for a
# "is this real?" question. Mirrors the semantics in graph_store/parser
# (1.0 = exact/qualified, 0.6 = regex-inferred, 0.5 = bare/ambiguous).
_LOW_CONFIDENCE = 0.6

_DEFAULT_TOP_N = 10


def _module_of(node_id: str) -> str:
    """2-level dir prefix of a ``file::symbol`` id (mirrors graph_store._mod_of)."""
    fpath = node_id.split("::", 1)[0]
    parts = fpath.split("/")
    if len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0] if parts else ""


def _god_nodes(degree_rows: list[tuple], top_n: int = _DEFAULT_TOP_N) -> list[dict]:
    """Rank symbols by degree, excluding file-level hub nodes.

    ``degree_rows``: ``(id, name, node_type, file_path, degree)`` already sorted
    by degree desc (the caller's SQL does the ORDER BY). A file-level hub is a
    node whose label IS a filename (node_type 'file', or name endswith a source
    ext) — those accumulate import/contains edges and would drown real
    abstractions (graphify analyze.py:100).
    """
    out: list[dict] = []
    for row in degree_rows:
        node_id, name, node_type, file_path, degree = row[0], row[1], row[2], row[3], row[4]
        if _is_file_hub(name, node_type):
            continue
        out.append({"id": node_id, "label": name, "degree": degree, "file": file_path})
        if len(out) >= top_n:
            break
    return out


def _source_ext_suffixes() -> tuple[str, ...]:
    from .parser import LANGUAGE_MAP
    return tuple(LANGUAGE_MAP)


def _is_file_hub(name: str, node_type: str) -> bool:
    """True if this node is a file-level hub (not a real symbol abstraction)."""
    if node_type == "file":
        return True
    return name.endswith(_source_ext_suffixes())


def _symbol_cluster_map(clusters: dict | None) -> dict[str, str]:
    """``{symbol_id: cluster_id}`` for the COMMUNITY clusters of ``compute_graph_clusters``.

    Symbol-level, because clusters are: a file whose symbols sit in two communities
    has no single domain. Singleton and file-bucket clusters are not communities, so
    their symbols stay unmapped (an edge out of one is not a cross-domain coupling).
    """
    out: dict[str, str] = {}
    for c in (clusters or {}).get("clusters") or []:
        cid = c.get("cluster_id")
        if not cid or c.get("kind") != "community":
            continue
        for member in c.get("member_ids") or []:
            out[member] = cid
    return out


def _surprising_connections(
    edges: list[tuple], symbol_cluster: dict[str, str], top_n: int = _DEFAULT_TOP_N
) -> dict:
    """Symbol edges crossing two structural communities, with an honest coverage stat.

    ``edges``: ``(source_id, target_id, confidence)``. An edge is "surprising"
    iff BOTH endpoint symbols are in communities AND those communities differ. The
    coverage stat (mapped/total endpoint symbols) makes a small result read as
    "low coverage", not "no coupling".
    """
    surprising: list[dict] = []
    endpoints: set[str] = set()
    mapped: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()

    for src, tgt, conf in edges:
        endpoints.add(src)
        endpoints.add(tgt)
        sd = symbol_cluster.get(src)
        td = symbol_cluster.get(tgt)
        if sd:
            mapped.add(src)
        if td:
            mapped.add(tgt)
        if sd and td and sd != td:
            key = (src, tgt)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            surprising.append({
                "from": src, "to": tgt,
                "from_cluster": sd, "to_cluster": td,
                "confidence": conf,
            })

    # Rank: lower-confidence cross-domain edges are MORE surprising (an inferred
    # link jumping domains is the higher-signal anomaly). Coalesce a NULL
    # confidence to 1.0 — code_edges.confidence has no NOT NULL constraint, and an
    # un-guarded sort would TypeError on None-vs-float (Gate-2; matches the None
    # guard in _suggested_questions).
    surprising.sort(key=lambda e: e["confidence"] if e["confidence"] is not None else 1.0)
    return {
        "coverage": {
            "mapped_endpoints": len(mapped),
            "total_endpoints": len(endpoints),
        },
        "edges": surprising[:top_n],
    }


def _suggested_questions(
    edges: list[tuple], risk_areas: list[dict], top_n: int = _DEFAULT_TOP_N
) -> list[dict]:
    """Domain-independent questions the graph is positioned to answer.

    Two sources (no domain dependency — Gate-1):
    - low-confidence edges (confidence <= _LOW_CONFIDENCE): "is this dependency
      real?" — the AMBIGUOUS/INFERRED edges are exactly where the graph is unsure.
    - risk_areas: "this hub has huge fan-in — is a change here safe?"
    """
    questions: list[dict] = []

    for src, tgt, conf in edges:
        if conf is not None and conf <= _LOW_CONFIDENCE:
            questions.append({
                "type": "ambiguous_edge",
                "question": f"Is the dependency `{src}` → `{tgt}` real, or an unresolved/ambiguous call?",
                "why": f"edge confidence {conf} (≤{_LOW_CONFIDENCE}) — the graph inferred it but could not resolve it exactly",
            })
        if len(questions) >= top_n:
            break

    for ra in risk_areas or []:
        name = ra.get("name") or ra.get("file_path") or "?"
        questions.append({
            "type": "risk_area",
            "question": f"Is a change to `{name}` safe given its fan-in?",
            "why": ra.get("reason") or "flagged as a risk area (high change-propagation)",
        })
        if len(questions) >= top_n * 2:
            break

    return questions[: top_n * 2]


def _degree_rows(graph_store: "GraphStore", limit: int) -> list[tuple]:
    """Symbol-level degree (in+out), highest first. Own query — the CTE inside
    get_graph_data is not a reusable API (Gate-1)."""
    return graph_store._conn.execute(
        "WITH degree AS ("
        "  SELECT id, "
        "    (SELECT COUNT(*) FROM code_edges WHERE source_id = code_nodes.id) + "
        "    (SELECT COUNT(*) FROM code_edges WHERE target_id = code_nodes.id) AS deg "
        "  FROM code_nodes"
        ") "
        "SELECT n.id, n.name, n.node_type, n.file_path, d.deg "
        "FROM code_nodes n JOIN degree d ON d.id = n.id "
        "ORDER BY d.deg DESC LIMIT ?",
        (max(limit * 4, 40),),  # over-fetch so file-hub exclusion still yields top_n
    ).fetchall()


def analyze_graph(graph_store: "GraphStore", *, clusters: dict | None = None,
                  risk_areas: list[dict] | None = None, top_n: int = _DEFAULT_TOP_N) -> dict:
    """Compute the topology-analysis layer.

    Reads the symbol graph (``graph_store``) for degree + edges; ``clusters`` (the
    ``graph_clusters`` layer) gives the symbol→community map for surprising connections
    and ``risk_areas`` (the structure layer's) seeds the risk questions. Pure read.
    """
    degree_rows = _degree_rows(graph_store, top_n)
    edges = graph_store._conn.execute(
        "SELECT source_id, target_id, confidence FROM code_edges"
    ).fetchall()

    return {
        "god_nodes": _god_nodes(degree_rows, top_n),
        "surprising_connections": _surprising_connections(
            edges, _symbol_cluster_map(clusters), top_n),
        "suggested_questions": _suggested_questions(edges, risk_areas or [], top_n),
    }
