"""Tests for the Code Intelligence graph visualization endpoint.

Verifies GET /api/code-intel/{project}/graph returns nodes + edges
in the format expected by the frontend force-directed graph component.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with the code_intel router."""
    from fastapi import FastAPI
    from routers.code_intel import router
    app = FastAPI()
    app.include_router(router, prefix="/api/code-intel")
    return TestClient(app)


@pytest.fixture
def mock_graph():
    """Mock GraphStore with sample nodes and edges."""
    graph = MagicMock()
    graph.get_graph_data.return_value = {
        "nodes": [
            {"id": "backend/core/foo.py::hello", "name": "hello", "type": "function", "module": "backend/core", "file_path": "backend/core/foo.py"},
            {"id": "backend/core/bar.py::Bar", "name": "Bar", "type": "class", "module": "backend/core", "file_path": "backend/core/bar.py"},
            {"id": "backend/routers/api.py::get_data", "name": "get_data", "type": "function", "module": "backend/routers", "file_path": "backend/routers/api.py"},
        ],
        "edges": [
            {"source": "backend/routers/api.py::get_data", "target": "backend/core/foo.py::hello", "type": "calls"},
            {"source": "backend/core/foo.py::hello", "target": "backend/core/bar.py::Bar", "type": "instantiates"},
        ],
    }
    return graph


class TestGraphEndpoint:
    """GET /api/code-intel/{project}/graph"""

    def test_returns_nodes_and_edges(self, client, mock_graph):
        """Happy path: returns structured graph data."""
        with patch("routers.code_intel.load_project_graph", return_value=mock_graph):
            resp = client.get("/api/code-intel/SwarmAI/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

    def test_node_shape(self, client, mock_graph):
        """Each node has required fields for the frontend."""
        with patch("routers.code_intel.load_project_graph", return_value=mock_graph):
            resp = client.get("/api/code-intel/SwarmAI/graph")
        node = resp.json()["nodes"][0]
        assert "id" in node
        assert "name" in node
        assert "type" in node
        assert "module" in node

    def test_edge_shape(self, client, mock_graph):
        """Each edge has source and target."""
        with patch("routers.code_intel.load_project_graph", return_value=mock_graph):
            resp = client.get("/api/code-intel/SwarmAI/graph")
        edge = resp.json()["edges"][0]
        assert "source" in edge
        assert "target" in edge

    def test_limit_parameter(self, client, mock_graph):
        """Respects ?limit= parameter (passed to get_graph_data)."""
        with patch("routers.code_intel.load_project_graph", return_value=mock_graph):
            resp = client.get("/api/code-intel/SwarmAI/graph?limit=2")
        # Verify get_graph_data was called with the limit
        mock_graph.get_graph_data.assert_called_once_with(2)
        assert resp.status_code == 200

    def test_404_when_no_db(self, client):
        """Returns 404 when project has no code_intel.db."""
        with patch("routers.code_intel.load_project_graph", return_value=None):
            resp = client.get("/api/code-intel/NoProject/graph")
        assert resp.status_code == 404

    def test_invalid_project_name(self, client):
        """Rejects path traversal in project name."""
        # FastAPI normalizes ../ so test with special chars instead
        resp = client.get("/api/code-intel/bad%20name!/graph")
        assert resp.status_code == 400
