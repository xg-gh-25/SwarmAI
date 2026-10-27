"""Tests for graph_analysis.py — topology analysis over the code-intel graph.

DoD-A (run_dd13fb03, §24.2 graphify analyze.py steal-list). Three analyses,
each tested as a PURE function over primitives (no DB needed) plus one
integration test wiring a real GraphStore:

  - god_nodes: symbol-level degree centrality, file-level hubs excluded
    (Gate-1: MUST be symbol-level, NOT module_edges — that was a category error).
  - surprising_connections: symbol edges whose two endpoint FILES map to
    DIFFERENT structural domains (graph_clusters), with an HONEST coverage stat.
  - suggested_questions: DOMAIN-INDEPENDENT — from low-confidence (AMBIGUOUS/
    INFERRED) edges + risk_areas (Gate-1: must not depend on domains, which are
    often absent).
"""

from core.code_intel.graph_analysis import (
    _god_nodes,
    _surprising_connections,
    _suggested_questions,
    _symbol_cluster_map,
    analyze_graph,
)


class TestGodNodes:
    def test_ranks_by_degree_excludes_file_hubs(self):
        # degree rows: (id, name, node_type, file_path, degree)
        rows = [
            ("f.py::logger", "logger", "function", "backend/core/f.py", 120),
            ("g.py", "g.py", "file", "backend/core/g.py", 300),   # file-level hub → excluded
            ("h.py::helper", "helper", "function", "backend/core/h.py", 40),
        ]
        got = _god_nodes(rows, top_n=10)
        names = [g["label"] for g in got]
        assert "g.py" not in names, "file-level hub must be excluded (graphify L100)"
        assert names[0] == "logger", "highest-degree real symbol ranks first"
        assert got[0]["degree"] == 120
        assert "helper" in names

    def test_bounded_top_n(self):
        rows = [(f"m.py::s{i}", f"s{i}", "function", "m.py", 100 - i) for i in range(50)]
        got = _god_nodes(rows, top_n=10)
        assert len(got) == 10, "god-nodes must be bounded top-N, not a full dump"


class TestSymbolClusterMap:
    def test_maps_community_members_only(self):
        clusters = {"clusters": [
            {"cluster_id": "c:chat", "kind": "community",
             "member_ids": ["backend/core/session.py::f", "backend/core/healing.py::g"]},
            {"cluster_id": "c:eval", "kind": "community", "member_ids": ["backend/core/eval.py::h"]},
            {"cluster_id": "s:1", "kind": "singleton", "member_ids": ["x.py::lonely"]},
            {"cluster_id": "FILE:y.py", "kind": "file_bucket", "member_ids": ["y.py::a"]}]}
        assert _symbol_cluster_map(clusters) == {
            "backend/core/session.py::f": "c:chat",
            "backend/core/healing.py::g": "c:chat",
            "backend/core/eval.py::h": "c:eval"}

    def test_one_file_in_two_communities_is_judged_per_symbol(self):
        """An edge between two symbols of the same community is not surprising, even
        when their file also holds symbols of another community."""
        clusters = {"clusters": [
            {"cluster_id": "c:A", "kind": "community", "member_ids": ["shared.py::a", "o.py::x"]},
            {"cluster_id": "c:B", "kind": "community", "member_ids": ["shared.py::b"]}]}
        out = _surprising_connections([("o.py::x", "shared.py::a", 1.0),
                                       ("o.py::x", "shared.py::b", 1.0)],
                                      _symbol_cluster_map(clusters))
        assert [(e["from"], e["to"]) for e in out["edges"]] == [("o.py::x", "shared.py::b")]

    def test_sort_tolerates_none_confidence(self):
        """Gate-2: code_edges.confidence has no NOT NULL constraint — a NULL among
        floats must not crash the surprising-edge sort."""
        edges = [
            ("a.py::f", "b.py::g", None),   # cross-domain, NULL confidence
            ("a.py::h", "b.py::i", 0.6),
        ]
        fld = {"a.py::f": "c:A", "a.py::h": "c:A", "b.py::g": "c:B", "b.py::i": "c:B"}
        result = _surprising_connections(edges, fld, top_n=10)  # must not raise
        assert len(result["edges"]) == 2


class TestSurprisingConnections:
    def test_cross_domain_edge_flagged_with_coverage(self):
        # edges: (source_id, target_id, confidence) — ids are file::symbol
        edges = [
            ("backend/core/session.py::f", "backend/core/eval.py::g", 1.0),   # chat→eval = surprising
            ("backend/core/session.py::f", "backend/core/healing.py::h", 1.0),  # chat→chat = not
            ("backend/x/unmapped.py::a", "backend/y/other.py::b", 1.0),        # neither mapped
        ]
        fld = {
            "backend/core/session.py::f": "c:chat",
            "backend/core/healing.py::h": "c:chat",
            "backend/core/eval.py::g": "c:eval",
        }
        result = _surprising_connections(edges, fld, top_n=10)
        # distinct endpoint SYMBOLS: f (repeats), g, h, a, b = 5
        assert result["coverage"]["total_endpoints"] == 5
        assert result["coverage"]["mapped_endpoints"] == 3  # f, g, h
        # the surprising edge list holds only the cross-domain one
        surprising = result["edges"]
        assert len(surprising) == 1
        s = surprising[0]
        assert {s["from_cluster"], s["to_cluster"]} == {"c:chat", "c:eval"}

    def test_no_domains_yields_empty_but_honest(self):
        edges = [("a.py::x", "b.py::y", 1.0)]
        result = _surprising_connections(edges, {}, top_n=10)
        assert result["edges"] == []
        assert result["coverage"]["mapped_endpoints"] == 0, "honest zero, not a crash"


class TestSuggestedQuestions:
    def test_derives_from_low_confidence_edges_and_risk_areas(self):
        edges = [
            ("a.py::caller", "unresolved_target", 0.5),   # AMBIGUOUS → a question
            ("c.py::x", "d.py::y", 1.0),                   # solid → no question
        ]
        risk_areas = [{"name": "MessageStore.append", "file_path": "store.ts", "reason": "6347 callers"}]
        qs = _suggested_questions(edges, risk_areas, top_n=10)
        assert len(qs) >= 2, "one from the AMBIGUOUS edge, one from the risk area"
        kinds = {q["type"] for q in qs}
        assert "ambiguous_edge" in kinds and "risk_area" in kinds

    def test_domain_independent(self):
        # NO domains passed anywhere — questions still generated (Gate-1 requirement)
        qs = _suggested_questions([("a::b", "bare", 0.5)], [], top_n=10)
        assert len(qs) >= 1, "questions must work with zero domain data"


class TestAnalyzeGraphIntegration:
    def test_end_to_end_on_real_graph_store(self, tmp_path):
        from core.code_intel.graph_store import GraphStore
        gs = GraphStore(tmp_path / "ci.db")
        gs.upsert_nodes([
            {"id": "backend/core/session.py::f", "file_path": "backend/core/session.py",
             "node_type": "function", "name": "f", "line_start": 1, "line_end": 2,
             "language": "python", "is_export": 1, "is_entry_point": 0, "file_hash": "h"},
            {"id": "backend/core/eval.py::g", "file_path": "backend/core/eval.py",
             "node_type": "function", "name": "g", "line_start": 1, "line_end": 2,
             "language": "python", "is_export": 1, "is_entry_point": 0, "file_hash": "h"},
        ])
        gs.upsert_edges([
            {"source_id": "backend/core/session.py::f", "target_id": "backend/core/eval.py::g",
             "edge_type": "calls", "confidence": 1.0, "line_number": 1},
            {"source_id": "backend/core/session.py::f", "target_id": "bare_unresolved",
             "edge_type": "calls", "confidence": 0.5, "line_number": 2},
        ])
        clusters = {"clusters": [
            {"cluster_id": "c:chat", "kind": "community", "member_ids": ["backend/core/session.py::f"]},
            {"cluster_id": "c:eval", "kind": "community", "member_ids": ["backend/core/eval.py::g"]}]}
        out = analyze_graph(gs, clusters=clusters, risk_areas=[])
        assert "god_nodes" in out and "surprising_connections" in out and "suggested_questions" in out
        # f calls both → highest degree god-node
        assert out["god_nodes"], "non-empty god-nodes on a real store"
        # session→eval is cross-domain
        assert out["surprising_connections"]["edges"], "cross-domain edge surfaced"
        # the 0.5 bare edge → a question
        assert out["suggested_questions"], "low-confidence edge → question"
