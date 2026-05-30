"""Tests for graph_store.py route-related methods."""

import tempfile
from pathlib import Path

import pytest

from core.code_intel.graph_store import GraphStore


@pytest.fixture
def graph_store():
    """Create a temporary GraphStore for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_code_intel.db"
        store = GraphStore(db_path)
        yield store
        store.close()


# ── test_create_routes_table ────────────────────────────────────────────

def test_create_routes_table(graph_store):
    """Verify code_routes table exists after init."""
    cursor = graph_store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='code_routes'"
    )
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "code_routes"


# ── test_insert_and_query_routes ────────────────────────────────────────

def test_insert_and_query_routes(graph_store):
    """Round-trip insert and get_routes works."""
    routes = [
        {
            "id": "route_1",
            "method": "GET",
            "path": "/api/users",
            "handler_node_id": "api/users.py::list_users",
            "framework": "fastapi",
            "file_path": "api/users.py",
            "line_number": 10,
            "middleware": None,
        },
        {
            "id": "route_2",
            "method": "POST",
            "path": "/api/users",
            "handler_node_id": "api/users.py::create_user",
            "framework": "fastapi",
            "file_path": "api/users.py",
            "line_number": 15,
            "middleware": "auth,logging",
        },
    ]
    graph_store.insert_routes(routes)

    all_routes = graph_store.get_routes()
    assert len(all_routes) == 2

    # Check fields round-trip correctly
    get_route = next(r for r in all_routes if r["method"] == "GET")
    assert get_route["path"] == "/api/users"
    assert get_route["handler_node_id"] == "api/users.py::list_users"
    assert get_route["framework"] == "fastapi"
    assert get_route["line_number"] == 10

    post_route = next(r for r in all_routes if r["method"] == "POST")
    assert post_route["middleware"] == "auth,logging"


# ── test_get_routes_by_file ─────────────────────────────────────────────

def test_get_routes_by_file(graph_store):
    """Filter by file_path works."""
    routes = [
        {
            "id": "route_a",
            "method": "GET",
            "path": "/api/health",
            "handler_node_id": "main.py::health",
            "framework": "fastapi",
            "file_path": "main.py",
            "line_number": 5,
            "middleware": None,
        },
        {
            "id": "route_b",
            "method": "GET",
            "path": "/api/users",
            "handler_node_id": "users.py::list_users",
            "framework": "fastapi",
            "file_path": "users.py",
            "line_number": 10,
            "middleware": None,
        },
    ]
    graph_store.insert_routes(routes)

    # Filter by file
    main_routes = graph_store.get_routes(file_path="main.py")
    assert len(main_routes) == 1
    assert main_routes[0]["path"] == "/api/health"

    users_routes = graph_store.get_routes(file_path="users.py")
    assert len(users_routes) == 1
    assert users_routes[0]["path"] == "/api/users"


# ── test_get_routes_for_handler ─────────────────────────────────────────

def test_get_routes_for_handler(graph_store):
    """FK lookup by handler_node_id works."""
    routes = [
        {
            "id": "route_x",
            "method": "GET",
            "path": "/items",
            "handler_node_id": "items.py::get_items",
            "framework": "express",
            "file_path": "items.py",
            "line_number": 1,
            "middleware": None,
        },
        {
            "id": "route_y",
            "method": "POST",
            "path": "/items",
            "handler_node_id": "items.py::create_item",
            "framework": "express",
            "file_path": "items.py",
            "line_number": 10,
            "middleware": None,
        },
    ]
    graph_store.insert_routes(routes)

    result = graph_store.get_routes_for_handler("items.py::get_items")
    assert len(result) == 1
    assert result[0]["method"] == "GET"
    assert result[0]["path"] == "/items"

    result2 = graph_store.get_routes_for_handler("items.py::create_item")
    assert len(result2) == 1
    assert result2[0]["method"] == "POST"

    # Non-existent handler returns empty
    result3 = graph_store.get_routes_for_handler("nonexistent::handler")
    assert result3 == []
