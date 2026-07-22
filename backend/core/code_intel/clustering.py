"""Bounded-domain graph clustering — language-agnostic structural decomposition.

Clusters the code-intel graph (code_nodes + code_edges) into structural
communities so a monolith can be understood/decomposed domain-by-domain. This is
the structural sibling of ``graph_analysis`` (topology metrics) and is entirely
LANGUAGE-AGNOSTIC: it operates on the shared edge set (calls/references/extends/
dynamic_sql_write), so py/ts/js/java/go/sql all cluster with the same algorithm.

⚠️ This is NOT the LLM ``domains[]`` concept in code-intel.json (spec-details /
flows[].domain_id — business-semantic domains). These are STRUCTURAL communities
read from graph topology. The output key is ``graph_clusters`` (distinct).

Algorithm (run_93e78bcd, Gate-1 hardened):
  • WEIGHTED, async label-propagation — no networkx, no random. DETERMINISTIC
    PER-INPUT (same graph → byte-identical partition, verified across hash seeds)
    but NOT stable across node-id changes: async LP order = sorted(node_ids), so a
    file rename shifts positions and can re-partition. cluster_ids are therefore
    EPHEMERAL — do not diff them across reindexes as if a changed id means a
    changed domain (Gate-2 #2, run_93e78bcd).
    A neighbor's vote is weighted 1/sqrt(deg(neighbor)) so a hub (god-node) is
    NOT excluded and does NOT desert its community; its vote is merely diluted.
    (Gate-1 CRITICAL: excluding god-node edges made the domain-anchor a singleton
    — backwards. The god-node filter in graph_analysis is a *ranking* concern, not
    a graph-surgery rule.)
  • HYBRID: a size-1 community is folded to a per-file bucket, but tagged
    kind="file_bucket" (or "singleton" if alone), NEVER conflated with a real
    community — a bag of symbols sharing a filename is not a bounded domain.
  • cohesion = intra/(intra+inter); a 0-internal-edge cluster is cohesion=0.0,
    NEVER 1.0 (Gate-1: 1.0 for a disconnected bag is a lie).
  • extraction_candidates = the ACTIONABLE subset (real communities with a
    cuttable seam), so the consumer gets decomposition candidates, not raw buckets.
  • Includes low-confidence edges (dynamic_sql_write @0.4) — clustering wants the
    structural signal; it does NOT apply get_module_edges' 0.5 floor.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

# Above this edge count, skip clustering (record a skip reason) rather than spend
# unbounded time inside the fail-open export block.
_MAX_EDGES = 50_000
_MAX_ROUNDS = 10
# extraction-candidate thresholds (a cluster is "decomposition-ready" iff all hold)
_MIN_CANDIDATE_SIZE = 3
_MIN_CANDIDATE_COHESION = 0.5

_SEP = "::"


def _file_of(node_id: str) -> str:
    return node_id.split(_SEP)[0]


def compute_graph_clusters(graph_store) -> dict:
    """Cluster the full graph into structural communities.

    Returns ``{converged, rounds_used, clusters: [...], extraction_candidates: [...],
    skipped?: reason}``. Each cluster: ``{cluster_id, kind, size, member_ids,
    entry_points, intra_edges, inter_edges, cohesion, languages, files}``.
    """
    # Edge-count pre-check BEFORE materializing the whole graph — on a pathological
    # repo get_full_graph() would OOM before an after-the-fact guard could fire.
    try:
        edge_count = graph_store.count_edges()
        if edge_count > _MAX_EDGES:
            return {"converged": False, "rounds_used": 0, "clusters": [],
                    "extraction_candidates": [],
                    "skipped": f"edge count {edge_count} exceeds {_MAX_EDGES}"}
    except Exception:  # noqa: BLE001 — no count API → fall through to post-materialize guard
        pass

    graph = graph_store.get_full_graph()
    nodes = graph["nodes"]
    edges = graph["edges"]

    node_ids = [n["id"] for n in nodes]
    node_set = set(node_ids)
    lang_of = {n["id"]: n.get("language", "unknown") for n in nodes}

    if not node_ids:
        return {"converged": True, "rounds_used": 0, "clusters": [],
                "extraction_candidates": []}
    if len(edges) > _MAX_EDGES:
        return {"converged": False, "rounds_used": 0, "clusters": [],
                "extraction_candidates": [],
                "skipped": f"edge count {len(edges)} exceeds {_MAX_EDGES}"}

    # Undirected adjacency over IN-GRAPH endpoints only (an edge to an external/
    # unresolved node — e.g. a stdlib call — has no node to cluster). A set drops
    # self-loops and duplicate edges. Includes ALL edge types + confidences.
    adj: dict[str, set[str]] = defaultdict(set)
    in_degree: Counter = Counter()
    for e in edges:
        s, t = e["source_id"], e["target_id"]
        if s in node_set and t in node_set and s != t:
            adj[s].add(t)
            adj[t].add(s)
            in_degree[t] += 1  # directional in-degree → entry-point ranking

    # Precompute vote weight per node = 1/sqrt(deg): dilutes a hub's influence
    # without removing it (Gate-1 weighted-vote fix).
    weight = {nid: 1.0 / math.sqrt(len(adj[nid])) if adj[nid] else 1.0
              for nid in node_ids}

    # Deterministic async label-propagation. order = sorted ids (no random);
    # ties broken by smallest label id → reproducible.
    label = {nid: nid for nid in node_ids}
    order = sorted(node_ids)
    converged = False
    rounds_used = 0
    for rnd in range(1, _MAX_ROUNDS + 1):
        rounds_used = rnd
        changed = 0
        for nid in order:
            neigh = adj[nid]
            if not neigh:
                continue
            scores: dict[str, float] = defaultdict(float)
            for m in neigh:
                scores[label[m]] += weight[m]
            # Deterministic winner: highest weighted vote; on a tie, the
            # lexicographically SMALLEST label id (min over the tied top score).
            top = max(scores.values())
            best = min(lab for lab, w in scores.items() if w == top)
            if label[nid] != best:
                label[nid] = best
                changed += 1
        if changed == 0:
            converged = True
            break

    # Group by final label.
    groups: dict[str, list[str]] = defaultdict(list)
    for nid, lab in label.items():
        groups[lab].append(nid)

    # HYBRID fold: a size-1 group is re-keyed to its file bucket.
    folded: dict[str, list[str]] = defaultdict(list)
    for lab, members in groups.items():
        if len(members) == 1:
            folded["FILE:" + _file_of(members[0])].append(members[0])
        else:
            folded[lab].extend(members)

    clusters = []
    for cid in sorted(folded):
        members = sorted(folded[cid])
        mset = set(members)
        intra = 0
        inter = 0
        for nid in members:
            for m in adj[nid]:
                if m in mset:
                    intra += 1
                else:
                    inter += 1
        intra //= 2  # each internal edge counted from both endpoints
        total = intra + inter
        cohesion = (intra / total) if total > 0 else 0.0  # 0-edge bag != 1.0
        # kind: a real community has internal edges; a file-fold with >1 member but
        # 0 internal edges is a file_bucket; a lone member is a singleton.
        if intra > 0:
            kind = "community"
        elif len(members) == 1:
            kind = "singleton"
        else:
            kind = "file_bucket"
        entry_points = sorted(members, key=lambda n: (-in_degree[n], n))[:3]
        languages = sorted({lang_of.get(n, "unknown") for n in members})
        files = sorted({_file_of(n) for n in members})
        clusters.append({
            "cluster_id": cid,
            "kind": kind,
            "size": len(members),
            "member_ids": members,
            "entry_points": entry_points,
            "intra_edges": intra,
            "inter_edges": inter,
            "cohesion": round(cohesion, 4),
            "languages": languages,
            "files": files,
        })

    # Actionable subset: real communities, big enough, cohesive, with a cuttable
    # seam (inter_edges finite — always true here; the real gates are kind/size/
    # cohesion). This is the decomposition-ready output vs raw buckets.
    extraction_candidates = [
        c["cluster_id"] for c in clusters
        if c["kind"] == "community"
        and c["size"] >= _MIN_CANDIDATE_SIZE
        and c["cohesion"] >= _MIN_CANDIDATE_COHESION
    ]

    return {
        "converged": converged,
        "rounds_used": rounds_used,
        "clusters": clusters,
        "extraction_candidates": extraction_candidates,
    }
