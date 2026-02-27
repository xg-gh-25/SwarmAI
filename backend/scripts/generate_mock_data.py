"""Mock data generation script for development and testing.

Generates realistic mock data for the preserved subsystems:

- ``_generate_todos``  — ToDos with various statuses and priorities
- ``_generate_tasks``  — Tasks with various statuses (draft, wip, blocked, completed)
- ``generate_mock_data`` — Orchestrator that populates SwarmWS with mock data
- ``has_mock_data``    — Guard to skip generation if data already exists

Usage:
    Called via POST /api/dev/generate-mock-data endpoint (DEBUG mode only)
    Or directly: python -m scripts.generate_mock_data

Requirements: 6.1-6.5
"""
import logging
from datetime import datetime, timedelta
from uuid import uuid4

from database import db

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat()


def _days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def _days_from_now(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).isoformat()


async def has_mock_data() -> bool:
    """Check if mock data already exists by counting todos."""
    todos = await db.todos.list()
    return len(todos) > 0


async def _get_or_create_swarmws() -> str:
    """Get the SwarmWS workspace ID (always 'swarmws' in singleton model)."""
    ws = await db.workspace_config.get_config()
    if ws:
        return ws["id"]
    # Create a minimal workspace_config row for mock data
    await db.workspace_config.put({
        "id": "swarmws",
        "name": "SwarmWS",
        "file_path": "",
        "icon": "🏠",
        "context": "Your Global Work Hub",
        "created_at": _now(),
        "updated_at": _now(),
    })
    return "swarmws"


async def _create_test_workspace() -> str:
    """Return the SwarmWS workspace ID for test data.

    In the single-workspace model there is no separate TestWS workspace.
    All mock data is scoped to the singleton SwarmWS ('swarmws').
    """
    return "swarmws"


async def _get_default_agent_id() -> str:
    """Get the default agent ID."""
    agents = await db.agents.list()
    for a in agents:
        if a.get("is_default"):
            return a["id"]
    return "default"


async def _generate_todos(workspace_id: str, prefix: str = "") -> list[str]:
    """Generate mock ToDos for a workspace. Returns list of created IDs."""
    todos_data = [
        {"title": f"{prefix}Review Q3 OKR progress report", "status": "pending", "priority": "high",
         "source_type": "manual", "due_date": _days_from_now(2)},
        {"title": f"{prefix}Respond to design review feedback", "status": "pending", "priority": "medium",
         "source_type": "slack", "due_date": _days_from_now(5)},
        {"title": f"{prefix}Update API documentation for v2 endpoints", "status": "in_discussion", "priority": "medium",
         "source_type": "meeting"},
        {"title": f"{prefix}Fix flaky integration test in CI pipeline", "status": "overdue", "priority": "high",
         "source_type": "integration", "due_date": _days_ago(1)},
        {"title": f"{prefix}Schedule 1:1 with new team member", "status": "handled", "priority": "low",
         "source_type": "email"},
        {"title": f"{prefix}Triage incoming bug reports from support", "status": "pending", "priority": "medium",
         "source_type": "integration", "due_date": _days_from_now(1)},
        {"title": f"{prefix}Prepare demo for stakeholder meeting", "status": "cancelled", "priority": "low",
         "source_type": "meeting"},
    ]
    ids = []
    for data in todos_data:
        todo_id = str(uuid4())
        ids.append(todo_id)
        await db.todos.put({
            "id": todo_id,
            "workspace_id": workspace_id,
            "title": data["title"],
            "description": f"Auto-generated mock data for {data['title']}",
            "source": data.get("source_type", "manual"),
            "source_type": data.get("source_type", "manual"),
            "status": data["status"],
            "priority": data["priority"],
            "due_date": data.get("due_date"),
            "created_at": _days_ago(3),
            "updated_at": _now(),
        })
    return ids


async def _generate_tasks(workspace_id: str, agent_id: str, prefix: str = "") -> list[str]:
    """Generate mock Tasks for a workspace. Returns list of created IDs."""
    tasks_data = [
        {"title": f"{prefix}Implement user authentication flow", "status": "wip", "priority": "high"},
        {"title": f"{prefix}Set up database migration scripts", "status": "completed", "priority": "medium"},
        {"title": f"{prefix}Design REST API schema", "status": "draft", "priority": "high"},
        {"title": f"{prefix}Write unit tests for payment module", "status": "blocked", "priority": "medium",
         "blocked_reason": "Waiting for payment gateway sandbox credentials"},
        {"title": f"{prefix}Refactor logging middleware", "status": "completed", "priority": "low"},
        {"title": f"{prefix}Deploy staging environment", "status": "draft", "priority": "medium"},
    ]
    ids = []
    for data in tasks_data:
        task_id = str(uuid4())
        ids.append(task_id)
        await db.tasks.put({
            "id": task_id,
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "title": data["title"],
            "description": f"Auto-generated mock task for {data['title']}",
            "status": data["status"],
            "priority": data.get("priority", "none"),
            "blocked_reason": data.get("blocked_reason"),
            "created_at": _days_ago(5),
            "updated_at": _now(),
        })
    return ids


async def generate_mock_data() -> dict:
    """Generate all mock data for SwarmWS and TestWS.

    Returns a summary dict with counts of created entities.
    Skips if mock data already exists.
    """
    if await has_mock_data():
        logger.info("Mock data already exists, skipping generation")
        return {"skipped": True, "reason": "Mock data already exists"}

    logger.info("Generating mock data...")

    # Get or create SwarmWS
    swarm_ws_id = await _get_or_create_swarmws()
    agent_id = await _get_default_agent_id()

    # Generate SwarmWS data
    swarm_todo_ids = await _generate_todos(swarm_ws_id)
    swarm_task_ids = await _generate_tasks(swarm_ws_id, agent_id)

    # Create TestWS and generate its data
    test_ws_id = await _create_test_workspace()
    test_todo_ids = await _generate_todos(test_ws_id, prefix="[TestWS] ")
    test_task_ids = await _generate_tasks(test_ws_id, agent_id, prefix="[TestWS] ")

    summary = {
        "skipped": False,
        "swarm_ws_id": swarm_ws_id,
        "test_ws_id": test_ws_id,
        "counts": {
            "todos": len(swarm_todo_ids) + len(test_todo_ids),
            "tasks": len(swarm_task_ids) + len(test_task_ids),
        },
    }

    logger.info(f"Mock data generation complete: {summary['counts']}")
    return summary
