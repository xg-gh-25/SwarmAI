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
