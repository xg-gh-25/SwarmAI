"""Mock data generation script for development and testing.

Generates realistic mock data for the Daily Work Operating Loop:
- ToDos (Signals) with various statuses and priorities
- Tasks with various statuses (draft, wip, blocked, completed)
- PlanItems, Communications, Artifacts, Reflections
- A TestWS workspace with its own mock data

Usage:
    Called via POST /api/dev/generate-mock-data endpoint (DEBUG mode only)
    Or directly: python -m scripts.generate_mock_data

Requirements: 12.1-12.6
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
    """Get the SwarmWS workspace ID, or return a placeholder."""
    ws = await db.swarm_workspaces.get_default()
    if ws:
        return ws["id"]
    # Create a minimal SwarmWS for mock data
    ws_id = str(uuid4())
    await db.swarm_workspaces.put({
        "id": ws_id,
        "name": "SwarmWS",
        "file_path": "",
        "context": "Your Global Work Hub",
        "is_default": 1,
        "is_archived": 0,
        "created_at": _now(),
        "updated_at": _now(),
    })
    return ws_id


async def _create_test_workspace() -> str:
    """Create a TestWS workspace and return its ID."""
    ws_id = str(uuid4())
    await db.swarm_workspaces.put({
        "id": ws_id,
        "name": "TestWS",
        "file_path": "",
        "context": "Test workspace for development",
        "is_default": 0,
        "is_archived": 0,
        "created_at": _now(),
        "updated_at": _now(),
    })
    return ws_id


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


async def _generate_plan_items(workspace_id: str) -> list[str]:
    """Generate mock PlanItems for a workspace."""
    items_data = [
        {"title": "Complete API integration tests", "focus_type": "today", "priority": "high", "sort_order": 0},
        {"title": "Review pull requests", "focus_type": "today", "priority": "medium", "sort_order": 1},
        {"title": "Draft architecture proposal", "focus_type": "upcoming", "priority": "high", "sort_order": 0},
        {"title": "Update team wiki", "focus_type": "upcoming", "priority": "low", "sort_order": 1},
        {"title": "Resolve dependency conflict", "focus_type": "blocked", "priority": "high", "sort_order": 0},
    ]
    ids = []
    for data in items_data:
        item_id = str(uuid4())
        ids.append(item_id)
        await db.plan_items.put({
            "id": item_id,
            "workspace_id": workspace_id,
            "title": data["title"],
            "description": f"Plan item: {data['title']}",
            "status": "active",
            "priority": data["priority"],
            "focus_type": data["focus_type"],
            "sort_order": data["sort_order"],
            "created_at": _days_ago(2),
            "updated_at": _now(),
        })
    return ids


async def _generate_communications(workspace_id: str) -> list[str]:
    """Generate mock Communications for a workspace."""
    comms_data = [
        {"title": "Follow up on API contract changes", "recipient": "backend-team@example.com",
         "channel_type": "email", "status": "pending_reply", "priority": "high"},
        {"title": "Share sprint retrospective notes", "recipient": "#team-channel",
         "channel_type": "slack", "status": "ai_draft", "priority": "medium",
         "ai_draft_content": "Here are the key takeaways from our sprint retro..."},
        {"title": "Confirm deployment window with SRE", "recipient": "sre-oncall@example.com",
         "channel_type": "email", "status": "follow_up", "priority": "high"},
        {"title": "Send weekly status update", "recipient": "stakeholders@example.com",
         "channel_type": "email", "status": "sent", "priority": "medium"},
    ]
    ids = []
    for data in comms_data:
        comm_id = str(uuid4())
        ids.append(comm_id)
        record = {
            "id": comm_id,
            "workspace_id": workspace_id,
            "title": data["title"],
            "description": f"Communication: {data['title']}",
            "recipient": data["recipient"],
            "channel_type": data["channel_type"],
            "status": data["status"],
            "priority": data["priority"],
            "created_at": _days_ago(2),
            "updated_at": _now(),
        }
        if data.get("ai_draft_content"):
            record["ai_draft_content"] = data["ai_draft_content"]
        if data["status"] == "sent":
            record["sent_at"] = _days_ago(1)
        await db.communications.put(record)
    return ids


async def _generate_artifacts(workspace_id: str) -> list[str]:
    """Generate mock Artifacts for a workspace."""
    artifacts_data = [
        {"title": "Q3 Architecture Plan", "artifact_type": "plan", "file_path": "Artifacts/Plans/q3_arch_plan.md"},
        {"title": "Performance Benchmark Report", "artifact_type": "report", "file_path": "Artifacts/Reports/perf_benchmark.md"},
        {"title": "API Design Document", "artifact_type": "doc", "file_path": "Artifacts/Docs/api_design.md"},
        {"title": "ADR-001: Use SQLite for local storage", "artifact_type": "decision", "file_path": "Artifacts/Decisions/adr_001.md"},
    ]
    ids = []
    for data in artifacts_data:
        art_id = str(uuid4())
        ids.append(art_id)
        await db.artifacts.put({
            "id": art_id,
            "workspace_id": workspace_id,
            "artifact_type": data["artifact_type"],
            "title": data["title"],
            "file_path": data["file_path"],
            "version": 1,
            "created_by": "system",
            "created_at": _days_ago(7),
            "updated_at": _now(),
        })
    return ids


async def _generate_reflections(workspace_id: str) -> list[str]:
    """Generate mock Reflections for a workspace."""
    reflections_data = [
        {"title": "Daily Recap - Today", "reflection_type": "daily_recap",
         "period_start": _days_ago(1), "period_end": _now()},
        {"title": "Weekly Summary - This Week", "reflection_type": "weekly_summary",
         "period_start": _days_ago(7), "period_end": _now()},
        {"title": "Lessons: Async migration pitfalls", "reflection_type": "lessons_learned",
         "period_start": _days_ago(14), "period_end": _days_ago(7)},
    ]
    ids = []
    for data in reflections_data:
        ref_id = str(uuid4())
        ids.append(ref_id)
        await db.reflections.put({
            "id": ref_id,
            "workspace_id": workspace_id,
            "reflection_type": data["reflection_type"],
            "title": data["title"],
            "file_path": f"Artifacts/Reports/{data['reflection_type']}_{ref_id[:8]}.md",
            "period_start": data["period_start"],
            "period_end": data["period_end"],
            "generated_by": "system",
            "created_at": _days_ago(1),
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
    swarm_plan_ids = await _generate_plan_items(swarm_ws_id)
    swarm_comm_ids = await _generate_communications(swarm_ws_id)
    swarm_art_ids = await _generate_artifacts(swarm_ws_id)
    swarm_ref_ids = await _generate_reflections(swarm_ws_id)

    # Create TestWS and generate its data
    test_ws_id = await _create_test_workspace()
    test_todo_ids = await _generate_todos(test_ws_id, prefix="[TestWS] ")
    test_task_ids = await _generate_tasks(test_ws_id, agent_id, prefix="[TestWS] ")
    test_plan_ids = await _generate_plan_items(test_ws_id)

    summary = {
        "skipped": False,
        "swarm_ws_id": swarm_ws_id,
        "test_ws_id": test_ws_id,
        "counts": {
            "todos": len(swarm_todo_ids) + len(test_todo_ids),
            "tasks": len(swarm_task_ids) + len(test_task_ids),
            "plan_items": len(swarm_plan_ids) + len(test_plan_ids),
            "communications": len(swarm_comm_ids),
            "artifacts": len(swarm_art_ids),
            "reflections": len(swarm_ref_ids),
        },
    }

    logger.info(f"Mock data generation complete: {summary['counts']}")
    return summary
