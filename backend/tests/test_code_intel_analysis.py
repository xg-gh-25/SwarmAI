"""Codebase analysis computed on demand from a project's code graph.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.9 (option B, user
2026-10-27): code-intel.json left the product, the analysis it carried became an
on-demand product capability (core/code_intel/analysis.py): a library call, an HTTP
endpoint and a CLI, computed from code_intel.db each time and never stored.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.code_intel.analysis import LAYERS, analyze_graph_store, analyze_project, main, resolve_layers
from core.code_intel.graph_store import GraphStore


def _make_mock_graph():
    """Create a mock GraphStore with realistic test data."""
    graph = MagicMock()

    # get_codebase_summary
    graph.get_codebase_summary.return_value = {
        "languages": {"python": 120, "typescript": 40},
        "total_nodes": 160,
        "total_edges": 350,
        "total_files": 45,
        "module_count": 8,
        "modules": {
            "backend/core": {"function_count": 45, "class_count": 12, "file_count": 15},
            "backend/routers": {"function_count": 30, "class_count": 5, "file_count": 10},
        },
        "top_connected": [
            {"name": "handle_request", "file_path": "backend/core/handler.py", "callers": 22},
        ],
        "entry_point_count": 14,
        "dead_code_count": 5,
        "last_indexed": "1717000000.0",
    }

    # get_module_map (carries is_entry_point — run_4344d341)
    graph.get_module_map.return_value = {
        "backend/core": [
            {"id": "n1", "file_path": "backend/core/handler.py", "node_type": "function", "name": "handle_request", "is_entry_point": 1},
            {"id": "n2", "file_path": "backend/core/handler.py", "node_type": "class", "name": "RequestHandler", "is_entry_point": 0},
        ],
        "backend/routers": [
            {"id": "n3", "file_path": "backend/routers/api.py", "node_type": "function", "name": "get_items", "is_entry_point": 0},
        ],
    }

    # get_module_edges (architectural skeleton — run_4344d341; confidence-enriched
    # run_2392a203: each edge carries confidence label + score, god-node-guarded).
    graph.get_module_edges.return_value = [
        {"from": "backend/routers", "to": "backend/core", "count": 3,
         "confidence": "EXTRACTED", "confidence_score": 1.0},
    ]

    # get_routes
    graph.get_routes.return_value = [
        {
            "id": "r1",
            "method": "GET",
            "path": "/api/items",
            "handler_node_id": "n3",
            "framework": "fastapi",
            "file_path": "backend/routers/api.py",
            "line_number": 15,
            "middleware": None,
        },
        {
            "id": "r2",
            "method": "POST",
            "path": "/api/items",
            "handler_node_id": "n3",
            "framework": "fastapi",
            "file_path": "backend/routers/api.py",
            "line_number": 30,
            "middleware": "auth",
        },
    ]

    # find_dead_code
    graph.find_dead_code.return_value = [
        {"id": "d1", "file_path": "backend/utils/old.py", "node_type": "function", "name": "deprecated_fn"},
    ]

    # get_meta (for repo_root, last indexed)
    graph.get_meta.side_effect = lambda key: {
        "repo_root": "/tmp/myrepo",
        "last_full_index": "1717000000.0",
        "last_indexed_commit": "abc1234",
    }.get(key)

    return graph




def _structure(graph=None):
    graph = graph or _make_mock_graph()
    with patch("core.code_intel.analysis._packages", return_value=None):
        return analyze_graph_store(graph, ["structure"])["structure"]


def _real_graph(tmp_path: Path) -> tuple[GraphStore, Path]:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (repo / "pkg" / "b.py").write_text(
        "from pkg.a import helper\n\ndef caller():\n    return helper()\n", encoding="utf-8")
    from core.code_intel.parser import parse_repo
    graph = GraphStore(tmp_path / "g.db")
    graph.bulk_insert(parse_repo(repo), repo_root=repo)
    return graph, repo


class TestLayers:
    def test_all_layers_by_default_in_computation_order(self):
        assert resolve_layers(None) == list(LAYERS)
        assert resolve_layers(["domain_rules", "structure"]) == ["structure", "domain_rules"]

    def test_unknown_layer_is_refused(self):
        with pytest.raises(ValueError, match="unknown analysis layer"):
            resolve_layers(["structure", "domains"])

    def test_a_failing_layer_is_reported_and_the_rest_returned(self, tmp_path):
        graph, _repo = _real_graph(tmp_path)
        try:
            with patch("core.code_intel.clustering.compute_graph_clusters",
                       side_effect=RuntimeError("boom")):
                out = analyze_graph_store(graph, ["structure", "graph_clusters"])
            assert "structure" in out and "graph_clusters" not in out
            assert "boom" in out["errors"]["graph_clusters"]
        finally:
            graph.close()


class TestStructureLayer:
    def test_routes(self):
        routes = _structure()["routes"]
        assert [(r["method"], r["path"]) for r in routes] == [("GET", "/api/items"), ("POST", "/api/items")]

    def test_modules(self):
        core = next(m for m in _structure()["modules"] if m["name"] == "backend/core")
        assert core["symbol_count"] == 2 and core["function_count"] == 45

    def test_entry_points_exclude_test_entries(self):
        graph = _make_mock_graph()
        graph.get_module_map.return_value = {
            "backend/core": [
                {"id": "e1", "file_path": "backend/core/main.py", "node_type": "function", "name": "main", "is_entry_point": 1},
                {"id": "a1", "file_path": "backend/core/attestation.py", "node_type": "function", "name": "cli", "is_entry_point": 1},
            ],
            "backend/tests": [
                {"id": "t1", "file_path": "backend/tests/test_x.py", "node_type": "function", "name": "test_foo", "is_entry_point": 1},
                {"id": "t2", "file_path": "backend/tests/conftest.py", "node_type": "function", "name": "fixture_db", "is_entry_point": 1},
            ],
        }
        names = {e["name"] for e in _structure(graph)["entry_points"]}
        assert names == {"main", "cli"}

    def test_module_edges_pass_through(self):
        me = _structure()["module_edges"]
        assert me == [{"from": "backend/routers", "to": "backend/core", "count": 3,
                       "confidence": "EXTRACTED", "confidence_score": 1.0}]

    def test_central_symbols_and_risk_areas(self):
        s = _structure()
        assert s["central_symbols"] == [{"name": "handle_request",
                                         "file_path": "backend/core/handler.py", "callers": 22}]
        assert s["risk_areas"][0]["risk_score"] == 1.0
        assert "hot_zones" not in s

    def test_dead_code(self):
        assert _structure()["dead_code"][0]["name"] == "deprecated_fn"

    def test_coverage_comes_from_the_last_full_parse(self):
        graph = _make_mock_graph()
        meta = {"repo_root": "/tmp/myrepo", "parse_status": "complete",
                "coverage_holes": json.dumps([{"ref": "x.cbl", "kind": "file", "reason": "unknown ext"}])}
        graph.get_meta.side_effect = meta.get
        cov = _structure(graph)["coverage"]
        assert cov["status"] == "partial" and cov["holes"][0]["ref"] == "x.cbl"

    def test_coverage_unknown_when_not_recorded(self):
        assert _structure()["coverage"] == {"status": "unknown", "holes": [], "as_of_commit": None}


class TestGraphAnalysisUsesStructuralClusters:
    def test_cross_community_edge_is_surprising(self, tmp_path):
        from core.code_intel.graph_analysis import analyze_graph
        graph, _repo = _real_graph(tmp_path)
        try:
            clusters = {"clusters": [
                {"cluster_id": "c1", "kind": "community", "member_ids": ["pkg/a.py::helper"]},
                {"cluster_id": "c2", "kind": "community", "member_ids": ["pkg/b.py::caller"]}]}
            sc = analyze_graph(graph, clusters=clusters)["surprising_connections"]
            assert [(e["from"], e["to"], e["from_cluster"], e["to_cluster"]) for e in sc["edges"]] == [
                ("pkg/b.py::caller", "pkg/a.py::helper", "c2", "c1")]
        finally:
            graph.close()


class TestContract:
    def test_schema_and_version(self, tmp_path):
        from core.code_intel.analysis import SCHEMA, VERSION
        graph, _repo = _real_graph(tmp_path)
        try:
            with patch("core.code_intel.analysis._packages", return_value=None):
                out = analyze_graph_store(graph, ["structure"])
            assert (out["schema"], out["version"]) == (SCHEMA, VERSION) == ("swarmai.code-intel.analysis", 1)
        finally:
            graph.close()

    @pytest.mark.parametrize("blank", [",", " ", " , "])
    def test_blank_layer_list_means_all(self, blank):
        assert resolve_layers(blank.split(",")) == list(LAYERS)

    def test_domain_rules_never_loads_an_oversized_graph(self, tmp_path):
        graph, _repo = _real_graph(tmp_path)
        try:
            with patch("core.code_intel.clustering._MAX_EDGES", 0), \
                 patch.object(graph, "get_full_graph", side_effect=AssertionError("loaded")):
                out = analyze_graph_store(graph, ["domain_rules"])
            assert "errors" not in out, out.get("errors")
            assert "skipped" in out["domain_rules"]
        finally:
            graph.close()

    def test_clusters_are_computed_once_per_request(self, tmp_path):
        import core.code_intel.clustering as cl
        graph, _repo = _real_graph(tmp_path)
        try:
            real = cl.compute_graph_clusters
            with patch("core.code_intel.clustering.compute_graph_clusters", side_effect=real) as spy, \
                 patch("core.code_intel.domain_rules.compute_graph_clusters", side_effect=real) as spy2, \
                 patch.object(graph, "get_full_graph", wraps=graph.get_full_graph) as loads:
                analyze_graph_store(graph, ["graph_clusters", "graph_analysis", "domain_rules"])
            assert spy.call_count + spy2.call_count == 1
            assert loads.call_count == 1
        finally:
            graph.close()

    def test_packages_come_from_the_repo(self, tmp_path):
        """Unpatched: the skill helper that detects package boundaries is found."""
        graph, _repo = _real_graph(tmp_path)
        try:
            structure = analyze_graph_store(graph, ["structure"])["structure"]
            assert structure["packages"] and structure["packages"][0]["root"] == "."
        finally:
            graph.close()


class TestOnDemand:
    def test_every_layer_on_a_real_graph_and_nothing_is_written(self, tmp_path):
        graph, repo = _real_graph(tmp_path)
        try:
            before = sorted(p.name for p in tmp_path.rglob("*"))
            out = analyze_graph_store(graph)
            assert out["layers"] == list(LAYERS) and "errors" not in out, out.get("errors")
            for name in LAYERS:
                assert name in out
            assert out["repo_root"] == str(repo.resolve()) or out["repo_root"] == str(repo)
            assert sorted(p.name for p in tmp_path.rglob("*")) == before
        finally:
            graph.close()

    def test_project_without_a_graph(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jobs.paths.PROJECTS_DIR", tmp_path)
        from core.code_intel import invalidate_cache
        invalidate_cache("Nope")
        assert analyze_project("Nope") is None

    def test_cli(self, tmp_path, monkeypatch, capsys):
        graph, _repo = _real_graph(tmp_path)
        graph.close()
        proj = tmp_path / "Projects" / "Demo"
        proj.mkdir(parents=True)
        (tmp_path / "g.db").rename(proj / "code_intel.db")
        monkeypatch.setattr("jobs.paths.PROJECTS_DIR", tmp_path / "Projects")
        from core.code_intel import invalidate_cache
        invalidate_cache("Demo")
        try:
            out_file = tmp_path / "a.json"
            assert main(["Demo", "--layers", "structure", "--out", str(out_file)]) == 0
            data = json.loads(out_file.read_text(encoding="utf-8"))
            assert data["project"] == "Demo" and data["layers"] == ["structure"]
            assert main(["Demo", "--layers", "bogus"]) == 2
            assert main(["Missing"]) == 1
            assert not (proj / "code-intel.json").exists()
        finally:
            invalidate_cache("Demo")


class TestEndpoint:
    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers.code_intel import router
        app = FastAPI()
        app.include_router(router, prefix="/api/code-intel")
        return TestClient(app)

    def test_ok(self, client):
        with patch("core.code_intel.analysis.analyze_project",
                   return_value={"project": "P", "layers": ["structure"]}) as fn:
            r = client.get("/api/code-intel/P/analysis?layers=structure")
        assert r.status_code == 200 and r.json()["project"] == "P"
        assert fn.call_args.args == ("P", ["structure"])

    def test_unknown_layer_400(self, client):
        assert client.get("/api/code-intel/P/analysis?layers=nope").status_code == 400

    def test_no_graph_404(self, client):
        with patch("core.code_intel.analysis.analyze_project", return_value=None):
            assert client.get("/api/code-intel/P/analysis").status_code == 404

    def test_bad_project_name_400(self, client):
        assert client.get("/api/code-intel/a..b/analysis").status_code == 400


class TestReindexJobStoresNothingButTheGraph:
    def test_full_reindex_writes_no_json_and_records_coverage(self, tmp_path, monkeypatch):
        from core.code_intel.tests.test_freshness import TestGradedIncrementalE2E as _Fixture
        handler, proj, _env = _Fixture()._init_repo(tmp_path, monkeypatch)
        monkeypatch.setattr("config.get_swarmws", lambda: proj.parent.parent)
        (proj / "notes.cbl").write_text("IDENTIFICATION DIVISION.\n", encoding="utf-8")
        handler.reindex_projects(full=True)
        assert not (proj / "code-intel.json").exists()
        graph = GraphStore(proj / "code_intel.db")
        try:
            with patch("core.code_intel.analysis._packages", return_value=None):
                cov = analyze_graph_store(graph, ["structure"])["structure"]["coverage"]
            assert cov["status"] == "partial"
            assert any("notes.cbl" in h.get("ref", "") for h in cov["holes"]), cov["holes"]
            assert cov["as_of_commit"]
        finally:
            graph.close()


def test_no_product_code_reads_or_writes_the_retired_files():
    """I11 (design 2026-10-27 §10): code-intel.json and spec-details left the product.
    No product code, comment or rule names them any more."""
    import subprocess
    repo = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        ["git", "-C", str(repo), "grep", "-l", "-E", r"code-intel\.json|spec-details", "--",
         "backend", "desktop/src", ":!**/tests/**", ":!**/test_*", ":!**/*.test.*", ":!*.md",
         ":!*.tmpl"],
        capture_output=True, text=True).stdout.split()
    assert out == []
