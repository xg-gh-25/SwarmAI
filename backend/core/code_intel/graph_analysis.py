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

- **surprising_connections** — symbol edges whose two endpoint FILES belong to
  DIFFERENT business domains (a cross-cutting concern or a leak). The file→domain
  map is DERIVED from the doc (business_rules[].anchor + issues[].file +
  steps[].file_path→flows[].domain_id) — there is no first-class module→domain
  field. Coverage is honestly small (many endpoints are unmapped); the result
  carries a ``coverage`` stat so a tiny edge list reads as "low coverage", not
  "no coupling". Gate-1: module-level mapping was ~2% (dead); file-level is the
  honest scope.

- **suggested_questions** — DOMAIN-INDEPENDENT (Gate-1: domains are often absent).
  Derived from low-confidence (AMBIGUOUS/INFERRED) edges ("is this dependency
  real?") + risk_areas ("this hub has huge fan-in — is it a refactor risk?").

The module is a pure consumer: ``analyze_graph(doc, graph_store)`` reads the
exported doc + the symbol graph and returns an additive dict. json_exporter wires
it under ``doc["graph_analysis"]``.
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


def _file_of(node_id: str) -> str:
    """Bare file path of a ``file::symbol`` id (drops the ::symbol AND any :line)."""
    return node_id.split("::", 1)[0].split(":", 1)[0]


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


_SOURCE_EXT_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go",
                        ".java", ".rb", ".c", ".cpp", ".h", ".cs", ".php")


def _is_file_hub(name: str, node_type: str) -> bool:
    """True if this node is a file-level hub (not a real symbol abstraction)."""
    if node_type == "file":
        return True
    return any(name.endswith(sfx) for sfx in _SOURCE_EXT_SUFFIXES)


def _build_file_domain_map(doc: dict) -> dict[str, str]:
    """Derive file→domain from the doc's nested domain evidence.

    Sources (all carry a file path tied to a domain), in precedence order:
    1. ``domains[].business_rules[].anchor`` (may be ``file`` or ``file:line``)
    2. ``domains[].issues[].file`` (may be ``file:line``)
    3. ``steps[].file_path`` mapped through ``steps[].flow_id → flows[].domain_id``

    Coverage is intentionally partial — only files a domain explicitly references
    are mapped. Returns ``{bare_file_path: domain_id}``.
    """
    fld: dict[str, str] = {}

    def _bare(p: str) -> str:
        return p.split(":", 1)[0] if p else ""

    # setdefault (NOT plain assignment): keep the FIRST/highest-precedence writer
    # so the documented order holds (business_rules > issues > steps). Plain
    # last-writer-wins would let a step silently override a business_rule anchor
    # for a file cited by two domains (Gate-2 finding).
    for dom in doc.get("domains") or []:
        did = dom.get("id")
        if not did:
            continue
        for br in dom.get("business_rules") or []:
            anchor = br.get("anchor")
            if anchor:
                fld.setdefault(_bare(anchor), did)
        for iss in dom.get("issues") or []:
            f = iss.get("file")
            if f:
                fld.setdefault(_bare(f), did)

    flow_domain = {f.get("id"): f.get("domain_id") for f in (doc.get("flows") or [])}
    for step in doc.get("steps") or []:
        fp = step.get("file_path")
        did = flow_domain.get(step.get("flow_id"))
        if fp and did:
            fld.setdefault(_bare(fp), did)

    return fld


def _surprising_connections(
    edges: list[tuple], file_domain: dict[str, str], top_n: int = _DEFAULT_TOP_N
) -> dict:
    """Symbol edges crossing two business domains, with an honest coverage stat.

    ``edges``: ``(source_id, target_id, confidence)``. An edge is "surprising"
    iff BOTH endpoint files map to domains AND those domains differ. The coverage
    stat (mapped/total endpoints) makes a small result read as "low coverage",
    not "no coupling" (Gate-1: file→domain mapping is genuinely sparse).
    """
    surprising: list[dict] = []
    endpoints: set[str] = set()
    mapped: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()

    for src, tgt, conf in edges:
        sf, tf = _file_of(src), _file_of(tgt)
        endpoints.add(sf)
        endpoints.add(tf)
        sd = file_domain.get(sf)
        td = file_domain.get(tf)
        if sd:
            mapped.add(sf)
        if td:
            mapped.add(tf)
        if sd and td and sd != td:
            key = (src, tgt)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            surprising.append({
                "from": src, "to": tgt,
                "from_domain": sd, "to_domain": td,
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


def analyze_graph(doc: dict, graph_store: "GraphStore", top_n: int = _DEFAULT_TOP_N) -> dict:
    """Compute the topology-analysis layer for a code-intel doc.

    Reads the symbol graph (``graph_store``) for degree + edges and the exported
    ``doc`` for the derived file→domain map + risk_areas. Pure read — no writes.
    Returns an additive dict for ``doc["graph_analysis"]``.
    """
    degree_rows = _degree_rows(graph_store, top_n)
    edges = graph_store._conn.execute(
        "SELECT source_id, target_id, confidence FROM code_edges"
    ).fetchall()

    file_domain = _build_file_domain_map(doc)
    risk_areas = doc.get("risk_areas") or []

    return {
        "god_nodes": _god_nodes(degree_rows, top_n),
        "surprising_connections": _surprising_connections(edges, file_domain, top_n),
        "suggested_questions": _suggested_questions(edges, risk_areas, top_n),
    }
