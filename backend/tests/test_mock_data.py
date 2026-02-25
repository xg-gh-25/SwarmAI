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
    """Test that mock data generation creates workspace data.

    In the single-workspace model, all mock data is created under the
    singleton SwarmWS workspace config.  The legacy TestWS multi-workspace
    concept is removed.
    """
    result = await generate_mock_data()
    swarm_ws_id = result["swarm_ws_id"]

    # Verify SwarmWS workspace config exists
    ws = await db.workspace_config.get_config()
    if ws:
        assert ws["name"] is not None

    # Verify SwarmWS has todos
    todos = await db.todos.list_by_workspace(swarm_ws_id)
    assert len(todos) > 0

    # Verify SwarmWS has tasks
    tasks = await db.tasks.list_all(workspace_id=swarm_ws_id)
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
