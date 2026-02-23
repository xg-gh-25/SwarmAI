"""Property-based tests for task status backward compatibility.

**Feature: workspace-refactor, Property 9: Task status backward compatibility**

Uses Hypothesis to verify that legacy task statuses (pending, running, failed)
are correctly mapped to new statuses (draft, wip, blocked) when tasks are
retrieved via TaskManager.

**Validates: Requirements 5.4**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.task_manager import task_manager, _LEGACY_STATUS_MAP, _VALID_STATUSES
from tests.helpers import ensure_default_workspace


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Strategies
legacy_status_strategy = st.sampled_from(list(_LEGACY_STATUS_MAP.keys()))
new_status_strategy = st.sampled_from(sorted(_VALID_STATUSES))
any_known_status_strategy = st.sampled_from(
    list(_LEGACY_STATUS_MAP.keys()) + sorted(_VALID_STATUSES)
)
task_title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=80,
).filter(lambda x: x.strip())


async def _insert_task_with_status(status: str, title: str, workspace_id: str) -> str:
    """Insert a task directly into the DB with the given raw status."""
    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task_{uuid4().hex[:12]}"
    await db.tasks.put({
        "id": task_id,
        "agent_id": "default",
        "session_id": None,
        "status": status,
        "title": title,
        "description": None,
        "priority": "none",
        "workspace_id": workspace_id,
        "source_todo_id": None,
        "blocked_reason": "test failure" if status == "failed" else None,
        "model": "test-model",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "work_dir": None,
    })
    return task_id


class TestLegacyStatusMapping:
    """Property 9: Task status backward compatibility.

    *For any* task stored with a legacy status (pending, running, failed),
    retrieving it via TaskManager SHALL return the mapped new status
    (draft, wip, blocked).

    **Validates: Requirements 5.4**
    """

    @given(legacy_status=legacy_status_strategy, title=task_title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_get_task_maps_legacy_status(self, legacy_status: str, title: str):
        """get_task returns the mapped new status for legacy statuses.

        **Validates: Requirements 5.4**
        """
        ws_id = await ensure_default_workspace()
        task_id = await _insert_task_with_status(legacy_status, title, ws_id)

        task = await task_manager.get_task(task_id)

        expected = _LEGACY_STATUS_MAP[legacy_status]
        assert task is not None
        assert task["status"] == expected, (
            f"Legacy '{legacy_status}' should map to '{expected}', got '{task['status']}'"
        )

    @given(legacy_status=legacy_status_strategy, title=task_title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_list_tasks_maps_legacy_status(self, legacy_status: str, title: str):
        """list_tasks returns mapped new statuses for legacy statuses.

        **Validates: Requirements 5.4**
        """
        ws_id = await ensure_default_workspace()
        task_id = await _insert_task_with_status(legacy_status, title, ws_id)

        tasks = await task_manager.list_tasks(workspace_id=ws_id)

        matching = [t for t in tasks if t["id"] == task_id]
        assert len(matching) == 1
        expected = _LEGACY_STATUS_MAP[legacy_status]
        assert matching[0]["status"] == expected, (
            f"Legacy '{legacy_status}' should map to '{expected}' in list, "
            f"got '{matching[0]['status']}'"
        )


class TestNewStatusPassthrough:
    """Property 9 (corollary): New statuses pass through unchanged.

    *For any* task stored with a valid new status, retrieving it via
    TaskManager SHALL return that same status unmodified.

    **Validates: Requirements 5.4**
    """

    @given(new_status=new_status_strategy, title=task_title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_get_task_preserves_new_status(self, new_status: str, title: str):
        """get_task returns new statuses unchanged.

        **Validates: Requirements 5.4**
        """
        ws_id = await ensure_default_workspace()
        task_id = await _insert_task_with_status(new_status, title, ws_id)

        task = await task_manager.get_task(task_id)

        assert task is not None
        assert task["status"] == new_status, (
            f"New status '{new_status}' should pass through unchanged, "
            f"got '{task['status']}'"
        )


class TestMappingCompleteness:
    """Property 9 (completeness): Every legacy status has a valid new mapping.

    The mapping must cover all three legacy statuses and each must map to
    a member of the valid new status set.

    **Validates: Requirements 5.4**
    """

    @given(legacy_status=legacy_status_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_legacy_maps_to_valid_new_status(self, legacy_status: str):
        """Every legacy status maps to a valid new status value.

        **Validates: Requirements 5.4**
        """
        mapped = task_manager._map_legacy_status(legacy_status)
        assert mapped in _VALID_STATUSES, (
            f"Legacy '{legacy_status}' mapped to '{mapped}' which is not in "
            f"valid statuses {_VALID_STATUSES}"
        )

    def test_all_legacy_statuses_covered(self):
        """The mapping covers pending, running, and failed.

        **Validates: Requirements 5.4**
        """
        required = {"pending", "running", "failed"}
        assert required == set(_LEGACY_STATUS_MAP.keys()), (
            f"Expected legacy statuses {required}, got {set(_LEGACY_STATUS_MAP.keys())}"
        )

    def test_specific_mappings(self):
        """Verify the exact mapping: pending→draft, running→wip, failed→blocked.

        **Validates: Requirements 5.4**
        """
        assert task_manager._map_legacy_status("pending") == "draft"
        assert task_manager._map_legacy_status("running") == "wip"
        assert task_manager._map_legacy_status("failed") == "blocked"
