"""Property-based tests for blocked task preserves reason.

**Feature: workspace-refactor, Property 10: Blocked task preserves reason**

Uses Hypothesis to verify that when a task transitions from failed to blocked,
the blocked_reason field is preserved as non-empty, retaining the original
failure context.

**Validates: Requirements 5.5**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.task_manager import task_manager
from tests.helpers import ensure_default_workspace


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Strategy: non-empty failure reason strings (printable, trimmed)
blocked_reason_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda x: x.strip())

task_title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=80,
).filter(lambda x: x.strip())


async def _insert_failed_task_with_reason(
    title: str, blocked_reason: str, workspace_id: str
) -> str:
    """Insert a task with status='failed' and a given blocked_reason."""
    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task_{uuid4().hex[:12]}"
    await db.tasks.put({
        "id": task_id,
        "agent_id": "default",
        "session_id": None,
        "status": "failed",
        "title": title,
        "description": None,
        "priority": "none",
        "workspace_id": workspace_id,
        "source_todo_id": None,
        "blocked_reason": blocked_reason,
        "model": "test-model",
        "created_at": now,
        "started_at": now,
        "completed_at": now,
        "error": blocked_reason,
        "work_dir": None,
    })
    return task_id


class TestBlockedTaskPreservesReason:
    """Property 10: Blocked task preserves reason.

    *For any* task stored with status 'failed' and a non-empty blocked_reason,
    retrieving it via TaskManager (which maps failed→blocked) SHALL return
    a non-empty blocked_reason preserving the original failure context.

    **Validates: Requirements 5.5**
    """

    @given(reason=blocked_reason_strategy, title=task_title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_get_task_preserves_blocked_reason(self, reason: str, title: str):
        """get_task returns non-empty blocked_reason when failed maps to blocked.

        **Validates: Requirements 5.5**
        """
        ws_id = await ensure_default_workspace()
        task_id = await _insert_failed_task_with_reason(title, reason, ws_id)

        task = await task_manager.get_task(task_id)

        assert task is not None
        assert task["status"] == "blocked", (
            f"Failed task should map to 'blocked', got '{task['status']}'"
        )
        assert task.get("blocked_reason"), (
            "blocked_reason must be non-empty when task transitions to blocked from failure"
        )
        assert task["blocked_reason"] == reason, (
            f"blocked_reason should preserve original failure context '{reason}', "
            f"got '{task['blocked_reason']}'"
        )

    @given(reason=blocked_reason_strategy, title=task_title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_list_tasks_preserves_blocked_reason(self, reason: str, title: str):
        """list_tasks returns non-empty blocked_reason for failed→blocked tasks.

        **Validates: Requirements 5.5**
        """
        ws_id = await ensure_default_workspace()
        task_id = await _insert_failed_task_with_reason(title, reason, ws_id)

        tasks = await task_manager.list_tasks(workspace_id=ws_id)

        matching = [t for t in tasks if t["id"] == task_id]
        assert len(matching) == 1
        task = matching[0]
        assert task["status"] == "blocked", (
            f"Failed task should map to 'blocked' in list, got '{task['status']}'"
        )
        assert task.get("blocked_reason"), (
            "blocked_reason must be non-empty in list results for blocked tasks"
        )
        assert task["blocked_reason"] == reason, (
            f"blocked_reason should preserve '{reason}' in list, "
            f"got '{task['blocked_reason']}'"
        )
