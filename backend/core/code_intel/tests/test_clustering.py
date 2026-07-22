"""Bounded-domain graph clustering tests (run_93e78bcd, language-agnostic).

Clustering acts on the shared graph (code_nodes + code_edges: calls/references/
extends/dynamic_sql_write), so it is language-agnostic — the same algorithm
clusters py/ts/js/java/go/sql. Tests cover: determinism, honest cohesion (0-edge
cluster != 1.0), kind classification (community/file_bucket/singleton), the
extraction_candidates actionability filter, weighted-vote (a hub is NOT excluded /
does NOT desert its community), cross-language separation, and edge cases.
"""
from core.code_intel.clustering import compute_graph_clusters


class _FakeGraph:
    """Minimal stand-in exposing get_full_graph() — clustering's only dependency."""
    def __init__(self, nodes, edges):
        self._nodes = nodes
        self._edges = edges

    def get_full_graph(self):
        return {"nodes": self._nodes, "edges": self._edges}


def _node(nid, lang="python", ntype="function", name=None):
    return {"id": nid, "file_path": nid.split("::")[0], "node_type": ntype,
            "name": name or nid.split("::")[-1], "language": lang,
            "is_export": 1, "is_entry_point": 0}


def _edge(s, t, etype="calls", conf=1.0):
    return {"source_id": s, "target_id": t, "edge_type": etype,
            "confidence": conf, "line_number": 1}


def _two_communities():
    """Two dense triangles A/B connected by a single weak bridge."""
    nodes = [_node(f"a.py::a{i}") for i in range(3)] + [_node(f"b.py::b{i}") for i in range(3)]
    edges = [
        _edge("a.py::a0", "a.py::a1"), _edge("a.py::a1", "a.py::a2"), _edge("a.py::a2", "a.py::a0"),
        _edge("b.py::b0", "b.py::b1"), _edge("b.py::b1", "b.py::b2"), _edge("b.py::b2", "b.py::b0"),
        _edge("a.py::a0", "b.py::b0"),  # single bridge
    ]
    return _FakeGraph(nodes, edges)


def test_clustering_is_deterministic():
    """AC1: same input → same output (no random)."""
    g = _two_communities()
    r1 = compute_graph_clusters(g)
    r2 = compute_graph_clusters(g)
    key = lambda r: sorted(tuple(sorted(c["member_ids"])) for c in r["clusters"])
    assert key(r1) == key(r2)


def test_two_communities_separate():
    """AC4: two triangles joined by one bridge → two communities, not one blob,
    and not fragmented (the hub/bridge does not desert its community)."""
    r = compute_graph_clusters(_two_communities())
    communities = [c for c in r["clusters"] if c["kind"] == "community"]
    assert len(communities) == 2, [c["member_ids"] for c in r["clusters"]]
    for c in communities:
        assert c["size"] == 3


def test_hub_does_not_desert_its_community():
    """AC4 (Gate-1 CRITICAL): a hub everyone calls (high degree) must STAY in its
    community, not be excluded into a singleton. Star: h called by 4 leaves that
    also call each other → one community of 5 containing h, not h-as-singleton."""
    nodes = [_node("m.py::h")] + [_node(f"m.py::l{i}") for i in range(4)]
    edges = [_edge(f"m.py::l{i}", "m.py::h") for i in range(4)]
    edges += [_edge("m.py::l0", "m.py::l1"), _edge("m.py::l1", "m.py::l2"),
              _edge("m.py::l2", "m.py::l3"), _edge("m.py::l3", "m.py::l0")]
    r = compute_graph_clusters(_FakeGraph(nodes, edges))
    hub_cluster = next(c for c in r["clusters"] if "m.py::h" in c["member_ids"])
    assert hub_cluster["kind"] == "community"
    assert hub_cluster["size"] >= 4, f"hub deserted its community: {hub_cluster}"


def test_zero_edge_cluster_cohesion_is_zero_not_one():
    """AC3 (Gate-1 CRITICAL): a bag of disconnected symbols sharing a file must
    have cohesion 0.0, NEVER 1.0. Two isolated nodes in the same file → file_bucket
    with cohesion 0.0."""
    nodes = [_node("solo.py::x"), _node("solo.py::y")]  # same file, NO edges
    r = compute_graph_clusters(_FakeGraph(nodes, []))
    for c in r["clusters"]:
        assert c["cohesion"] == 0.0, f"0-edge cluster claimed cohesion {c['cohesion']}"
        assert c["kind"] in ("file_bucket", "singleton")
        assert c["kind"] != "community"


def test_extraction_candidates_exclude_file_buckets():
    """AC5: extraction_candidates is the ACTIONABLE subset — real communities only,
    never file_buckets/singletons. The two-community graph yields candidates; a
    disconnected file bag yields none."""
    r = compute_graph_clusters(_two_communities())
    cand_ids = set(r["extraction_candidates"])
    for c in r["clusters"]:
        if c["cluster_id"] in cand_ids:
            assert c["kind"] == "community"
            assert c["size"] >= 3
            assert c["cohesion"] >= 0.5
    # a pure file-bucket graph → zero candidates
    r2 = compute_graph_clusters(_FakeGraph([_node("f.py::a"), _node("f.py::b")], []))
    assert r2["extraction_candidates"] == []


def test_cross_language_not_force_merged():
    """AC6: py and sql nodes with NO edge between them stay in separate clusters;
    languages[] is labelled correctly. dynamic_sql_write edges (confidence 0.4) ARE
    included so the sql proc→table clusters (not dropped by a 0.5 floor)."""
    # The sql proc and its table live in DIFFERENT files, so if the 0.4 edge were
    # dropped they'd fold into SEPARATE FILE buckets — the test can only pass by
    # the edge genuinely clustering them into ONE community (not a same-file fold).
    nodes = [
        _node("app.py::p0"), _node("app.py::p1"),
        _node("proc.sql::run_recon", lang="sql", ntype="function"),
        _node("schema.sql::table:staging", lang="sql", ntype="data_object", name="staging"),
    ]
    edges = [
        _edge("app.py::p0", "app.py::p1"),                       # py community
        _edge("proc.sql::run_recon", "schema.sql::table:staging",
              etype="dynamic_sql_write:CREATE", conf=0.4),        # sql, low-conf, cross-file
    ]
    r = compute_graph_clusters(_FakeGraph(nodes, edges))
    sql_cluster = next((c for c in r["clusters"]
                        if "proc.sql::run_recon" in c["member_ids"]), None)
    assert sql_cluster is not None
    # kind==community (NOT file_bucket) is the ONLY signal that the 0.4 edge was
    # included — a dropped edge would leave two separate file buckets.
    assert sql_cluster["kind"] == "community", \
        f"0.4 dynamic_sql edge dropped — sql nodes did not form a community: {sql_cluster}"
    assert "schema.sql::table:staging" in sql_cluster["member_ids"], \
        "0.4 dynamic_sql edge was dropped — sql proc did not cluster with its table"
    assert sql_cluster["languages"] == ["sql"]
    assert sql_cluster["intra_edges"] >= 1
    # py nodes are in a DIFFERENT cluster (no cross-language edge)
    py_cluster = next(c for c in r["clusters"] if "app.py::p0" in c["member_ids"])
    assert "recon.sql::run_recon" not in py_cluster["member_ids"]
    assert py_cluster["languages"] == ["python"]


def test_empty_and_single_node():
    """AC7: empty graph → [], single node → one singleton, no crash."""
    assert compute_graph_clusters(_FakeGraph([], []))["clusters"] == []
    r = compute_graph_clusters(_FakeGraph([_node("a.py::x")], []))
    assert len(r["clusters"]) == 1
    assert r["clusters"][0]["kind"] == "singleton"
    assert r["converged"] is True


def test_convergence_fields_present():
    """AC7: converged/rounds_used are honest fields."""
    r = compute_graph_clusters(_two_communities())
    assert isinstance(r["converged"], bool)
    assert isinstance(r["rounds_used"], int) and r["rounds_used"] >= 1


def test_does_not_emit_domains_key():
    """AC2/AC8: clustering output must NOT collide with the LLM spec-details
    domains[] concept — its top key is graph_clusters-shaped, never 'domains'."""
    r = compute_graph_clusters(_two_communities())
    assert "domains" not in r
    assert "clusters" in r and "extraction_candidates" in r
