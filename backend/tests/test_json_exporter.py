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

    def test_export_populates_entry_points(self, tmp_path):
        """run_4344d341: entry_points was always [] because get_module_map dropped
        is_entry_point. With it carried, is_entry_point=1 nodes must export."""
        from core.code_intel.json_exporter import export_code_intel_json
        graph = _make_mock_graph()
        output = tmp_path / "code-intel.json"
        export_code_intel_json(graph, "test-project", output)
        data = json.loads(output.read_text())
        eps = data["entry_points"]
        assert len(eps) == 1, "the single is_entry_point=1 node must be exported"
        assert eps[0]["name"] == "handle_request"

    def test_entry_points_exclude_test_entries(self, tmp_path):
        """run_4344d341: test_* / conftest entries are TEST entries, not
        architectural ones — they must NOT bloat exported entry_points."""
        from core.code_intel.json_exporter import export_code_intel_json
        graph = _make_mock_graph()
        graph.get_module_map.return_value = {
            "backend/core": [
                {"id": "e1", "file_path": "backend/core/main.py", "node_type": "function", "name": "main", "is_entry_point": 1},
            ],
            "backend/tests": [
                {"id": "t1", "file_path": "backend/tests/test_x.py", "node_type": "function", "name": "test_foo", "is_entry_point": 1},
                {"id": "t2", "file_path": "backend/tests/conftest.py", "node_type": "function", "name": "fixture_db", "is_entry_point": 1},
            ],
        }
        output = tmp_path / "code-intel.json"
        export_code_intel_json(graph, "test-project", output)
        eps = json.loads(output.read_text())["entry_points"]
        names = {e["name"] for e in eps}
        assert "main" in names, "architectural entry (main) must be kept"
        assert "test_foo" not in names, "test_ entry must be excluded"
        assert "fixture_db" not in names, "conftest entry must be excluded"

    def test_entry_points_no_false_exclusion_on_test_substring(self, tmp_path):
        """run_4344d341 Gate-2 MEDIUM: a bare 'test' substring false-excludes real
        files (attestation.py, latest_run.py, contest/engine.py). Segment-anchored
        check must KEEP a real main in such a file."""
        from core.code_intel.json_exporter import export_code_intel_json
        graph = _make_mock_graph()
        graph.get_module_map.return_value = {
            "backend/core": [
                {"id": "a1", "file_path": "backend/core/attestation.py", "node_type": "function", "name": "main", "is_entry_point": 1},
                {"id": "a2", "file_path": "backend/core/latest_run.py", "node_type": "function", "name": "cli", "is_entry_point": 1},
            ],
            "backend/tests": [
                {"id": "t1", "file_path": "backend/tests/test_x.py", "node_type": "function", "name": "test_foo", "is_entry_point": 1},
            ],
        }
        output = tmp_path / "code-intel.json"
        export_code_intel_json(graph, "test-project", output)
        names = {e["name"] for e in json.loads(output.read_text())["entry_points"]}
        assert "main" in names and "cli" in names, "real entries in test-substring paths must be kept"
        assert "test_foo" not in names, "genuine test entry still excluded"

    def test_export_includes_module_edges(self, tmp_path):
        """run_4344d341: edges were never assembled into the doc. module_edges[]
        (architectural skeleton) must be present with from/to/count shape."""
        from core.code_intel.json_exporter import export_code_intel_json
        graph = _make_mock_graph()
        output = tmp_path / "code-intel.json"
        export_code_intel_json(graph, "test-project", output)
        data = json.loads(output.read_text())
        assert "module_edges" in data, "doc must carry module_edges (was missing → edges=0)"
        me = data["module_edges"]
        assert me and me[0]["from"] == "backend/routers" and me[0]["to"] == "backend/core"
        assert me[0]["count"] == 3
        # run_2392a203: confidence enrichment must pass through the exporter unchanged
        assert me[0]["confidence"] == "EXTRACTED", "confidence label preserved through export"
        assert me[0]["confidence_score"] == 1.0, "confidence_score preserved through export"

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


class TestGate2F1F2F5ExporterFixes:
    """Run AB Gate-2 fixes at the exporter: F1 (mint id for unmatched route),
    F2 (validate before stamping complete), F5 (prune stale unclassified)."""

    def _prior_v3(self):
        return {
            "version": "3.0",
            "routes": [{"id": "route:a", "method": "GET", "path": "/api/items",
                        "handler": "n3", "framework": "fastapi",
                        "file_path": "backend/routers/api.py", "line_number": 15, "middleware": None}],
            "domains": [{"id": "domain:i", "name": "Items"}],
            "flows": [{"id": "flow:0", "domain_id": "domain:i", "entry_ref": "route:a"}],
            "unclassified": [],
        }

    def test_f1_unmatched_route_gets_minted_id(self, tmp_path):
        """A freshly-built route with no prior match must get a MINTED id (never
        id-less → never silently dropped from the denominator)."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        output.write_text(json.dumps(self._prior_v3()))
        graph = _make_mock_graph()  # emits routes with id="" — none match the prior key
        export_code_intel_json(graph, "test", output)
        data = json.loads(output.read_text())
        assert all(r.get("id") for r in data["routes"]), \
            f"every route must have an id after export, got {[r.get('id') for r in data['routes']]}"

    def test_f2_status_downgraded_when_v3_invalid(self, tmp_path):
        """F2 KEYSTONE: if the preserved v3 layer is inconsistent (flow points at a
        ghost route), export must NOT stamp complete — downgrade to partial + hole."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        bad = self._prior_v3()
        bad["flows"] = [{"id": "flow:0", "domain_id": "domain:i", "entry_ref": "route:GHOST"}]
        output.write_text(json.dumps(bad))
        export_code_intel_json(_make_mock_graph(), "test", output)
        data = json.loads(output.read_text())
        assert data["status"] == "partial", "invalid v3 layer must downgrade status to partial"
        assert any("validation" in h.get("reason", "").lower() for h in data.get("coverage_ledger", [])), \
            "a v3-validation failure must be recorded as a coverage hole"

    def test_f5_stale_unclassified_pruned(self, tmp_path):
        """F5: an unclassified id for a route no longer present is pruned (not left as
        a 'fabricated anchor' landmine)."""
        from core.code_intel.json_exporter import export_code_intel_json
        output = tmp_path / "code-intel.json"
        prior = self._prior_v3()
        prior["unclassified"] = [{"id": "route:DELETED", "reason": "was a real route, now removed from the codebase"}]
        output.write_text(json.dumps(prior))
        export_code_intel_json(_make_mock_graph(), "test", output)
        data = json.loads(output.read_text())
        uncls_ids = {u["id"] for u in data.get("unclassified", [])}
        assert "route:DELETED" not in uncls_ids, "stale unclassified id must be pruned"


class TestMintIdMirrorsDeriveRouteId:
    """Run AB Gate-2 re-verify: _mint_id (exporter, core-side) MUST be byte-identical
    to ai_ready_helpers.derive_route_id (skill-side) so a moved route re-matches by id
    on the next skill run. A lockstep contract test — if either drifts, this goes RED
    (the R7 lying-comment class: the comment claimed 'in sync' while it wasn't)."""

    def test_mint_id_byte_identical_to_derive_route_id(self, tmp_path):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                        "skills", "s_repo-to-ddd", "scripts"))
        from ai_ready_helpers import derive_route_id
        # reach _mint_id via a real export path is awkward; re-derive its logic by
        # calling through _reattach_route_ids on an unmatched route and comparing.
        from core.code_intel.json_exporter import _reattach_route_ids
        cases = [
            ("GET", "/api/items", "backend/routers/api.py"),
            ("POST", "/users/{id}/posts", "svc/users.py"),
            ("DELETE", "/a-b", "x.py"),
            ("GET", "/a/b", "x.py"),  # must NOT collide with /a-b
        ]
        for method, path, fp in cases:
            built = [{"method": method, "path": path, "file_path": fp}]  # no id
            _reattach_route_ids(built, prior_routes=None)  # forces mint
            minted = built[0]["id"]
            expected = derive_route_id(method, path, fp)
            assert minted == expected, (
                f"_mint_id drifted from derive_route_id for {method} {path}: "
                f"minted={minted} expected={expected}")
        # collision guard: /a-b and /a/b must differ
        b1 = [{"method": "GET", "path": "/a-b", "file_path": "x.py"}]
        b2 = [{"method": "GET", "path": "/a/b", "file_path": "x.py"}]
        _reattach_route_ids(b1, None); _reattach_route_ids(b2, None)
        assert b1[0]["id"] != b2[0]["id"], "slug-collapsing routes must get distinct ids"
