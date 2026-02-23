"""Tests for mock data generation.

Tests:
- Data generation creates expected records for all entity types
- Duplicate prevention (second call is a no-op)

Requirements: 12.1-12.6
"""
import pytest

from database import db
from scripts.generate_mock_data import generate_mock_data, has_mock_data


@pytest.mark.asyncio
async def test_generate_mock_data_creates_records():
    """Test that generate_mock_data creates records for all entity types."""
    result = await generate_mock_data()

    assert result["skipped"] is False
    assert result["swarm_ws_id"] is not None
    assert result["test_ws_id"] is not None

    counts = result["counts"]
    assert counts["todos"] > 0
    assert counts["tasks"] > 0
    assert counts["plan_items"] > 0
    assert counts["communications"] > 0
    assert counts["artifacts"] > 0
    assert counts["reflections"] > 0


@pytest.mark.asyncio
async def test_generate_mock_data_creates_todos_with_various_statuses():
    """Test that generated ToDos have various statuses and priorities."""
    result = await generate_mock_data()
    swarm_ws_id = result["swarm_ws_id"]

    todos = await db.todos.list_by_workspace(swarm_ws_id)
    statuses = {t["status"] for t in todos}
    priorities = {t["priority"] for t in todos}

    # Should have multiple statuses
    assert len(statuses) >= 3
    assert "pending" in statuses
    assert "overdue" in statuses

    # Should have multiple priorities
    assert len(priorities) >= 2
    assert "high" in priorities


@pytest.mark.asyncio
async def test_generate_mock_data_creates_tasks_with_various_statuses():
    """Test that generated Tasks have various statuses."""
    result = await generate_mock_data()
    swarm_ws_id = result["swarm_ws_id"]

    tasks = await db.tasks.list_all(workspace_id=swarm_ws_id)
    statuses = {t["status"] for t in tasks}

    assert "draft" in statuses
    assert "wip" in statuses
    assert "blocked" in statuses
    assert "completed" in statuses


@pytest.mark.asyncio
async def test_generate_mock_data_creates_test_workspace():
    """Test that a TestWS workspace is created with its own data."""
    result = await generate_mock_data()
    test_ws_id = result["test_ws_id"]

    # Verify TestWS exists
    ws = await db.swarm_workspaces.get(test_ws_id)
    assert ws is not None
    assert ws["name"] == "TestWS"
    assert ws["is_default"] == 0

    # Verify TestWS has its own todos
    todos = await db.todos.list_by_workspace(test_ws_id)
    assert len(todos) > 0

    # Verify TestWS has its own tasks
    tasks = await db.tasks.list_all(workspace_id=test_ws_id)
    assert len(tasks) > 0


@pytest.mark.asyncio
async def test_duplicate_prevention():
    """Test that second call to generate_mock_data is a no-op."""
    # First call generates data
    result1 = await generate_mock_data()
    assert result1["skipped"] is False

    # Second call should skip
    result2 = await generate_mock_data()
    assert result2["skipped"] is True
    assert result2["reason"] == "Mock data already exists"


@pytest.mark.asyncio
async def test_has_mock_data_false_initially():
    """Test that has_mock_data returns False on clean database."""
    assert await has_mock_data() is False


@pytest.mark.asyncio
async def test_has_mock_data_true_after_generation():
    """Test that has_mock_data returns True after generation."""
    await generate_mock_data()
    assert await has_mock_data() is True
