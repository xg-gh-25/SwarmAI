"""Tests for Code Intelligence JSON Exporter (backend/core/code_intel/json_exporter.py).

TDD: tests written first, implementation follows.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


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

    # get_module_map
    graph.get_module_map.return_value = {
        "backend/core": [
            {"id": "n1", "file_path": "backend/core/handler.py", "node_type": "function", "name": "handle_request"},
            {"id": "n2", "file_path": "backend/core/handler.py", "node_type": "class", "name": "RequestHandler"},
        ],
        "backend/routers": [
            {"id": "n3", "file_path": "backend/routers/api.py", "node_type": "function", "name": "get_items"},
        ],
    }

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


class TestJsonExporter:
    """Tests for export_code_intel_json."""

    def test_export_produces_valid_json(self, tmp_path):
        """Exported file is valid JSON and contains required top-level keys."""
        from core.code_intel.json_exporter import export_code_intel_json

        graph = _make_mock_graph()
        output = tmp_path / "code-intel.json"

        result_path = export_code_intel_json(graph, "test-project", output)
        assert result_path.exists()

        data = json.loads(result_path.read_text())
        # Required top-level keys
        assert data["version"] == "2.0"
        assert "generated_at" in data
        assert "repo" in data
        assert "modules" in data
        assert "routes" in data

    def test_export_includes_routes(self, tmp_path):
        """Routes section contains extracted HTTP routes."""
        from core.code_intel.json_exporter import export_code_intel_json

        graph = _make_mock_graph()
        output = tmp_path / "code-intel.json"

        export_code_intel_json(graph, "test-project", output)
        data = json.loads(output.read_text())

        routes = data["routes"]
        assert len(routes) == 2
        assert routes[0]["method"] == "GET"
        assert routes[0]["path"] == "/api/items"
        assert routes[1]["method"] == "POST"

    def test_export_includes_modules(self, tmp_path):
        """Modules section lists module prefixes with symbol counts."""
        from core.code_intel.json_exporter import export_code_intel_json

        graph = _make_mock_graph()
        output = tmp_path / "code-intel.json"

        export_code_intel_json(graph, "test-project", output)
        data = json.loads(output.read_text())

        modules = data["modules"]
        assert len(modules) >= 2
        # Check module structure
        core_mod = next((m for m in modules if m["name"] == "backend/core"), None)
        assert core_mod is not None
        assert "symbols" in core_mod or "symbol_count" in core_mod

    def test_export_schema_version(self, tmp_path):
        """JSON output declares v2 schema."""
        from core.code_intel.json_exporter import export_code_intel_json

        graph = _make_mock_graph()
        output = tmp_path / "code-intel.json"

        export_code_intel_json(graph, "test-project", output)
        data = json.loads(output.read_text())

        assert data["$schema"] == "https://ai-ready-repo.dev/schemas/code-intel.v2.json"
        assert data["version"] == "2.0"

    def test_export_repo_metadata(self, tmp_path):
        """Repo section includes name, languages, and counts."""
        from core.code_intel.json_exporter import export_code_intel_json

        graph = _make_mock_graph()
        output = tmp_path / "code-intel.json"

        export_code_intel_json(graph, "test-project", output)
        data = json.loads(output.read_text())

        repo = data["repo"]
        assert repo["name"] == "test-project"
        assert "languages" in repo
        assert repo["total_symbols"] == 160
        assert repo["total_edges"] == 350

    def test_export_dead_code_section(self, tmp_path):
        """Dead code section lists unreferenced symbols."""
        from core.code_intel.json_exporter import export_code_intel_json

        graph = _make_mock_graph()
        output = tmp_path / "code-intel.json"

        export_code_intel_json(graph, "test-project", output)
        data = json.loads(output.read_text())

        dead = data.get("dead_code", [])
        assert len(dead) >= 1
        assert dead[0]["name"] == "deprecated_fn"

    def test_export_size_cap(self, tmp_path):
        """If output exceeds 500KB, dead_code section is trimmed."""
        from core.code_intel.json_exporter import export_code_intel_json

        graph = _make_mock_graph()
        # Generate many dead code entries to push over 500KB
        graph.find_dead_code.return_value = [
            {"id": f"d{i}", "file_path": f"backend/big/module_{i}.py",
             "node_type": "function", "name": f"unused_function_with_long_name_{i}" * 10}
            for i in range(5000)
        ]
        # Also generate many modules
        big_modules = {}
        for i in range(200):
            big_modules[f"module_{i}/submod"] = [
                {"id": f"nm{i}_{j}", "file_path": f"module_{i}/submod/f{j}.py",
                 "node_type": "function", "name": f"func_{j}"}
                for j in range(50)
            ]
        graph.get_module_map.return_value = big_modules

        output = tmp_path / "code-intel.json"
        export_code_intel_json(graph, "test-project", output)

        # File must exist and be <= 500KB
        assert output.exists()
        size_kb = output.stat().st_size / 1024
        assert size_kb <= 512  # small tolerance for JSON formatting


class TestV3PreservationAndCoverage:
    """Run AB Cycle 3 — the ROOT fix (Gate-1 Check-2): a full reindex must NOT wipe
    the v3 business-semantic layer. The v2 exporter overwrote domains/flows/steps/
    unclassified + route ids on every reindex, so a backfilled accounted_ratio=1.0
    silently reverted to 4.8% on the next commit — a FALSE 100% (the banking red line).
    The exporter now PRESERVES the prior v3 layer + carries a coverage_ledger + writes
    atomically with a status stamp."""

    def _prior_v3_doc(self):
        return {
            "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
            "version": "3.0",
            "routes": [
                {"id": "route:get-items-abc", "method": "GET", "path": "/api/items",
                 "handler": "n3", "framework": "fastapi", "file_path": "backend/routers/api.py",
                 "line_number": 15, "middleware": None},
                {"id": "route:post-items-def", "method": "POST", "path": "/api/items",
                 "handler": "n3", "framework": "fastapi", "file_path": "backend/routers/api.py",
                 "line_number": 30, "middleware": "auth"},
            ],
            "domains": [{"id": "domain:items", "name": "Items"}],
            "flows": [{"id": "flow:0", "domain_id": "domain:items", "entry_ref": "route:get-items-abc"}],
            "steps": [{"id": "step:0", "flow_id": "flow:0"}],
            "unclassified": [{"id": "route:post-items-def",
                              "reason": "admin-only mutation, no user-facing business flow"}],
        }

    def test_v3_layer_preserved_across_reindex(self, tmp_path):
        """The killer test: a prior doc with domains/flows/steps/unclassified must
        NOT be wiped by a v2 reindex export."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        # seed a prior v3 doc on disk
        output.write_text(json.dumps(self._prior_v3_doc()))

        graph = _make_mock_graph()
        export_code_intel_json(graph, "test-project", output)

        data = json.loads(output.read_text())
        assert data.get("domains"), "domains[] WIPED by reindex — the exact Gate-1 Check-2 regression"
        assert data.get("flows"), "flows[] WIPED"
        assert data.get("steps"), "steps[] WIPED"
        assert data.get("unclassified"), "unclassified[] WIPED — accounted_ratio would revert to false-low"
        # version stays v3 when a v3 layer is preserved
        assert data["version"] == "3.0"

    def test_route_ids_preserved_across_reindex(self, tmp_path):
        """Route ids (the anchor menu) must survive — flow.entry_ref points at them.
        The v2 _build_routes emits NO id, so a naive overwrite orphans every flow."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        output.write_text(json.dumps(self._prior_v3_doc()))
        graph = _make_mock_graph()
        export_code_intel_json(graph, "test-project", output)
        data = json.loads(output.read_text())
        # the flow's entry_ref must still resolve to a route id in routes[]
        route_ids = {r.get("id") for r in data.get("routes", []) if r.get("id")}
        flow_refs = {f.get("entry_ref") for f in data.get("flows", [])}
        assert flow_refs and flow_refs <= route_ids, \
            f"flow entry_refs {flow_refs} no longer resolve to route ids {route_ids} — orphaned"

    def test_no_prior_file_exports_v2_cleanly(self, tmp_path):
        """First-ever export (no prior file) still produces a valid v2 doc — no crash,
        no phantom v3 layer."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        export_code_intel_json(graph := _make_mock_graph(), "test-project", output)
        data = json.loads(output.read_text())
        assert data["version"] == "2.0"  # no v3 layer to preserve
        assert "domains" not in data or data["domains"] == []

    def test_corrupt_prior_file_does_not_crash(self, tmp_path):
        """A corrupt/unparseable prior file must not break the export (fail-safe:
        treat as no-prior, export fresh)."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        output.write_text("{ this is not valid json ")
        export_code_intel_json(_make_mock_graph(), "test-project", output)
        data = json.loads(output.read_text())
        assert data["version"] == "2.0"

    def test_status_stamp_present(self, tmp_path):
        """F19: the doc carries an explicit status: complete|partial stamp."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        export_code_intel_json(_make_mock_graph(), "test-project", output)
        data = json.loads(output.read_text())
        assert data.get("status") in ("complete", "partial")

    def test_coverage_holes_carried_and_status_partial(self, tmp_path):
        """coverage_holes passed in are written to doc['coverage_ledger'] and force
        status=partial (coverage is not complete when holes exist)."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        holes = [{"ref": "legacy.cbl", "kind": "file",
                  "reason": "unsupported extension .cbl — no AST parser available"}]
        export_code_intel_json(_make_mock_graph(), "test-project", output, coverage_holes=holes)
        data = json.loads(output.read_text())
        assert data.get("coverage_ledger") == holes
        assert data["status"] == "partial"

    def test_f19_atomic_no_tmp_leftover(self, tmp_path):
        """F19: atomic write leaves no .tmp sibling and the final file is intact."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        export_code_intel_json(_make_mock_graph(), "test-project", output)
        leftovers = list(tmp_path.glob("*.tmp")) + list(tmp_path.glob(".*.tmp"))
        assert not leftovers, f"atomic write left temp files: {leftovers}"
        assert json.loads(output.read_text())  # final is valid

    def test_f19_write_failure_preserves_prior(self, tmp_path, monkeypatch):
        """F19: if the atomic swap fails mid-write, the PRIOR file must remain intact
        (never a half-written file)."""
        import core.code_intel.json_exporter as JE
        output = tmp_path / "code-intel.json"
        prior = self._prior_v3_doc()
        output.write_text(json.dumps(prior))
        # force os.replace to fail
        real_replace = JE.os.replace if hasattr(JE, "os") else __import__("os").replace
        import os as _os
        def boom(src, dst):
            raise OSError("simulated swap failure")
        monkeypatch.setattr(_os, "replace", boom)
        with pytest.raises(Exception):
            JE.export_code_intel_json(_make_mock_graph(), "test-project", output)
        # prior file must still be the intact v3 doc
        data = json.loads(output.read_text())
        assert data["version"] == "3.0" and data.get("domains"), "prior file corrupted by failed write"
