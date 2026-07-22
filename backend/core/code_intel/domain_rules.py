"""Per-domain business-rule manifest — language-neutral, deterministic.

Turns each bounded structural domain (``graph_clusters`` extraction_candidate)
into machine-readable BUSINESS RULES anchored to source, so a monolith can be
understood/rewritten domain-by-domain. This is the structural spine under a
language-neutral per-domain spec (the LLM prose layer fills chapters from this
manifest; the manifest itself is 100% deterministic — graph-derived, no LLM).

It is the on-box/open analogue of AWS Transform's ``business-rules-extraction``.

LANGUAGE-AGNOSTIC by construction: it reads the shared graph (code_nodes +
code_edges). Today the rich signal is PL/SQL ``dynamic_sql_write`` edges + recon
dispositions, but any language whose edges land in the graph clusters the same.

Design (run_28a8f99d, Gate-1 hardened — all against real HLR source):
  • **Disposition join = TABLE-NAME EQUALITY, never proximity** (Gate-1 C1). A
    reconciliation call ``prov_recon_services[_cps]('schema','TABLE','DISPOSITION',
    ...)`` carries the remediation disposition (CREATE_ACTIVATE / DELETE) as its
    3rd literal arg and the target table as its 2nd. With 300+ calls and 600+
    writes interleaved in one file, proximity mis-pairs — so a write rule is joined
    to a disposition ONLY when the call's arg[2] casefold-equals the write target.
    The call literals are NOT in the graph (the parser blanks string literals to
    avoid false call edges), so we scan source ourselves — ONCE per file.
  • **engine_status is ADVISORY and NEVER suppresses a rule** (Gate-1 H1). A
    monolith often has a superseded engine (``*_OLD`` name, or a strict SUBSET of
    another symbol's write-target set). We FLAG it ``suspected_deprecated`` but
    still emit its rules — silently dropping a live banking rule is the exact
    false-100% failure the exporter's red-line guards forbid.
  • **Anchors are graph-derived** (the write edge's ``line_number``) — correct by
    construction, no hand-typed line (the C-probe's hand anchors were all wrong).
  • **Dynamic/unresolved targets stay honest**: a write whose table name is a
    runtime-concatenated variable (no literal token) is emitted with target
    ``<dynamic:unresolved>``, never a phantom table.

``verify_traceability`` mirrors AWS's ``verify-traceability.py`` §1-8-only scan:
every rule_id must be referenced in the spec's BEHAVIORAL chapters, not merely
listed in the §9 traceability matrix. Fail-closed (no matrix section → FAIL).
"""
from __future__ import annotations

import re

from .clustering import compute_graph_clusters

_SEP = "::"
_DYNAMIC_TARGET = "<dynamic:unresolved>"

# A reconciliation-style remediation call:
#   PKG.prov_recon_services[_cps]('schema','TABLE_NAME','DISPOSITION', ...)
# arg[1] = schema, arg[2] = target table, arg[3] = disposition. We capture the
# table (group 2) + disposition (group 3). Case-insensitive; tolerant of the
# optional package qualifier and whitespace. NOT anchored to a specific package
# name so it generalizes beyond RECONCILIATION_INTERFACES.
_RECON_CALL = re.compile(
    r"""prov_recon_services(?:_cps)?\s*\(\s*
        '[^']*'\s*,\s*      # arg1: schema
        '([^']*)'\s*,\s*    # arg2: target table   -> group(1)
        '([^']*)'           # arg3: disposition    -> group(2)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _file_of(node_id: str) -> str:
    return node_id.split(_SEP)[0]


def _table_name_of(node_id: str) -> str:
    """Bare table name from a ``file::table:NAME`` data_object id (casefold key
    is applied by callers). Falls back to the last id segment."""
    tail = node_id.split(_SEP)[-1]
    return tail[len("table:"):] if tail.startswith("table:") else tail


def _scan_dispositions(source: str) -> dict[str, str]:
    """Map casefold(table_name) -> disposition, scanned from recon call sites.

    Table-name equality is the join key (Gate-1 C1). If the same table appears in
    multiple recon calls with different dispositions, the FIRST wins (deterministic
    by source order) — real HLR pairs each report table with one disposition.
    """
    out: dict[str, str] = {}
    for m in _RECON_CALL.finditer(source):
        tbl, disp = m.group(1).strip(), m.group(2).strip()
        key = tbl.casefold()
        if key and key not in out:
            out[key] = disp
    return out


def _detect_deprecated(symbol_writes: dict[str, set[str]]) -> set[str]:
    """Return the set of symbols that are SUSPECTED deprecated (advisory only).

    Two signals (Gate-1 H1 — advisory, never used to suppress):
      • name ends in ``_OLD`` (case-insensitive), AND another symbol shares ≥1
        write target (i.e. it's a superseded twin, not a lone _OLD utility); OR
      • its write-target set is a strict SUBSET of another symbol's set AND they
        overlap (a narrower re-implementation of the same domain).
    Disjoint write-sets (e.g. two live HLR partitions) are NEVER flagged.
    """
    deprecated: set[str] = set()
    symbols = list(symbol_writes)
    for s in symbols:
        sw = symbol_writes[s]
        if not sw:
            continue
        for other in symbols:
            if other == s:
                continue
            ow = symbol_writes[other]
            if not (sw & ow):
                continue  # disjoint → unrelated, never flag (two live partitions)
            name_old = s.upper().endswith("_OLD")
            strict_subset = sw < ow  # proper subset
            if name_old or strict_subset:
                deprecated.add(s)
                break
    return deprecated


def compute_domain_rules(graph_store, source_reader=None, repo_root=None, _graph=None) -> dict:
    """Extract per-domain business rules from the shared graph.

    Args:
      graph_store: exposes ``get_full_graph()`` (+ ``count_edges()``).
      source_reader: ``file_path -> source_text`` (for scanning recon-call
        dispositions). Injected in tests. If omitted, a default reader resolves
        the node's file_path (which is REPO-RELATIVE, e.g. ``body.sql``) against
        ``repo_root``; without ``repo_root`` the relative path would fail to open
        and every disposition would be silently null (dog-food bug, run_28a8f99d).
      repo_root: base dir the relative node file_paths resolve against (the
        exporter passes it; the graph stores paths relative to it).
      _graph: optional pre-materialized ``get_full_graph()`` result, so the
        exporter can share ONE materialization with ``compute_graph_clusters``
        (Gate-1 M1 — avoid a second full-graph load + no per-rule disk I/O).

    Returns ``{domains: [...], rules: [...]}``. Each rule:
      ``{rule_id, domain_id, source_symbol, operation, target_data_object,
         disposition|None, anchor:{file,line}, engine_status: current|
         suspected_deprecated}``.
    """
    graph = _graph if _graph is not None else graph_store.get_full_graph()
    clusters = compute_graph_clusters(graph_store, _graph=graph) \
        if _accepts_graph_kw(compute_graph_clusters) else compute_graph_clusters(graph_store)
    candidate_ids = set(clusters.get("extraction_candidates", []))
    by_id = {c["cluster_id"]: c for c in clusters.get("clusters", [])}

    edges = graph["edges"]
    # Index dynamic_sql_write edges by source symbol.
    writes_by_symbol: dict[str, list[dict]] = {}
    symbol_targets: dict[str, set[str]] = {}
    for e in edges:
        if not str(e.get("edge_type", "")).startswith("dynamic_sql_write"):
            continue
        s = e["source_id"]
        writes_by_symbol.setdefault(s, []).append(e)
        tbl = _table_name_of(e["target_id"])
        symbol_targets.setdefault(s, set()).add(tbl.casefold())

    # Deprecation is detected GLOBALLY across ALL write-performing symbols, not
    # per-cluster: label-propagation clustering routinely splits a current engine
    # and its superseded twin into DIFFERENT communities (they connect only through
    # shared table nodes, which LP assigns to one side). Scoping deprecation to a
    # single cluster would therefore miss every cross-cluster superseded engine.
    deprecated = _detect_deprecated(symbol_targets)

    if source_reader is None:
        import os

        def source_reader(fp):  # noqa: ANN001
            # Node file_paths are repo-RELATIVE; resolve against repo_root so the
            # disposition scan can actually open them (else silent all-null).
            path = os.path.join(repo_root, fp) if repo_root else fp
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            except OSError:
                return ""

    # Cache disposition scans per file (Gate-1 M1: once per file, not per rule).
    _disp_cache: dict[str, dict[str, str]] = {}

    def _dispositions_for(file_path: str) -> dict[str, str]:
        if file_path not in _disp_cache:
            _disp_cache[file_path] = _scan_dispositions(source_reader(file_path))
        return _disp_cache[file_path]

    domains = []
    rules = []
    for cid in sorted(candidate_ids):
        cluster = by_id.get(cid)
        if not cluster:
            continue
        members = set(cluster["member_ids"])
        # Symbols in THIS domain that perform dynamic writes.
        domain_symbols = {s for s in writes_by_symbol if s in members}
        if not domain_symbols:
            continue
        domain_rule_ids = []
        for s in sorted(domain_symbols):
            sym_name = s.split(_SEP)[-1]
            status = "suspected_deprecated" if s in deprecated else "current"
            for e in sorted(writes_by_symbol[s], key=lambda x: x.get("line_number", 0)):
                op = str(e["edge_type"]).split(":", 1)[1] if ":" in str(e["edge_type"]) else "WRITE"
                tbl_raw = _table_name_of(e["target_id"])
                target = _DYNAMIC_TARGET if not tbl_raw or tbl_raw == _DYNAMIC_TARGET else tbl_raw
                disp = None
                if target != _DYNAMIC_TARGET:
                    disp = _dispositions_for(_file_of(s)).get(target.casefold())
                rid = f"BR-{cid}-{len(rules) + 1}"
                rules.append({
                    "rule_id": rid,
                    "domain_id": cid,
                    "source_symbol": sym_name,
                    "operation": op,
                    "target_data_object": target,
                    "disposition": disp,
                    "anchor": {"file": _file_of(s), "line": e.get("line_number", 0)},
                    "engine_status": status,
                })
                domain_rule_ids.append(rid)
        domains.append({
            "domain_id": cid,
            "size": cluster["size"],
            "cohesion": cluster["cohesion"],
            "languages": cluster.get("languages", []),
            "symbols": sorted(sym.split(_SEP)[-1] for sym in domain_symbols),
            "rule_count": len(domain_rule_ids),
        })
    return {"domains": domains, "rules": rules}


def _accepts_graph_kw(fn) -> bool:
    """True if fn accepts a ``_graph`` kwarg (so we can share materialization)."""
    try:
        import inspect
        return "_graph" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# ── Traceability coverage gate ────────────────────────────────────────────────

# The heading that opens the traceability matrix / appendix. Everything AT or
# AFTER this heading is EXCLUDED from the behavioral-coverage scan (Gate-1 H2:
# tolerant of level + numbering + a named heading).
_MATRIX_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s*(?:9\b|traceability\s+matrix)",
    re.IGNORECASE | re.MULTILINE,
)


def verify_traceability(spec_markdown: str, manifest: dict) -> dict:
    """Every rule_id must be referenced in the BEHAVIORAL chapters (before the §9
    traceability matrix), not merely listed in the matrix. Fail-closed.

    Mirrors AWS Transform verify-traceability.py: a rule that appears ONLY in the
    matrix that tracks it is NOT covered. Returns
    ``{ok, covered:[...], uncovered_rule_ids:[...], reason?}``.
    """
    rule_ids = [r["rule_id"] for r in manifest.get("rules", []) if r.get("rule_id")]
    if not rule_ids:
        return {"ok": True, "covered": [], "uncovered_rule_ids": []}

    m = _MATRIX_HEADING.search(spec_markdown)
    if not m:
        # No matrix section at all → cannot prove separation → fail CLOSED
        # (Gate-1 H2: a missing matrix must not read as "everything covered").
        return {"ok": False, "covered": [], "uncovered_rule_ids": list(rule_ids),
                "reason": "no §9 / traceability-matrix heading found — cannot verify "
                          "behavioral coverage; fail-closed"}

    behavioral = spec_markdown[:m.start()]
    covered, uncovered = [], []
    for rid in rule_ids:
        # Word-boundary match so BR-1 is not 'covered' by BR-12 (Gate-1 H2).
        if re.search(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])", behavioral):
            covered.append(rid)
        else:
            uncovered.append(rid)
    return {"ok": not uncovered, "covered": covered, "uncovered_rule_ids": uncovered}
