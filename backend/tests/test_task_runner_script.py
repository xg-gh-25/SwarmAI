"""Tests for script task execution in TaskManager.

Verifies that TaskManager supports type='script' tasks: daemon-owned
subprocess execution that survives session death. Core ACs from run_e5ccef04.
"""
import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.task_manager import task_manager


@pytest.fixture(autouse=True)
async def _clean_task_state():
    """Reset task manager state between tests."""
    task_manager._running_tasks.clear()
    task_manager._event_buffers.clear()
    task_manager._subscribers.clear()
    task_manager._message_queues.clear()
    task_manager._script_pids.clear()
    yield
    # Cancel any remaining tasks safely
    for task_id in list(task_manager._running_tasks.keys()):
        try:
            await task_manager.cancel_task(task_id)
        except Exception:
            pass
    # Kill any orphan script PIDs
    for pid in list(task_manager._script_pids.values()):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    task_manager._running_tasks.clear()
    task_manager._script_pids.clear()


@pytest.fixture
def mock_db():
    """Mock database for task persistence."""
    tasks_store = {}

    async def put(task):
        tasks_store[task["id"]] = task

    async def get(task_id):
        return tasks_store.get(task_id)

    async def update(task_id, fields):
        if task_id in tasks_store:
            tasks_store[task_id].update(fields)

    async def list_all(**kwargs):
        return list(tasks_store.values())

    with patch("core.task_manager.db") as mock:
        mock.tasks.put = AsyncMock(side_effect=put)
        mock.tasks.get = AsyncMock(side_effect=get)
        mock.tasks.update = AsyncMock(side_effect=update)
        mock.tasks.list = AsyncMock(side_effect=list_all)
        mock.workspace_config.get_config = AsyncMock(return_value={"id": "ws_default"})
        yield mock, tasks_store


class TestScriptTaskCreation:
    """AC1: POST returns task, subprocess runs in daemon tree."""

    @pytest.mark.asyncio
    async def test_create_script_task_returns_task_record(self, mock_db):
        """Script task creation returns a valid task with type=script."""
        _, tasks_store = mock_db
        task = await task_manager.create_script_task(
            command="echo hello",
            description="Test echo",
        )
        assert task["id"].startswith("task_")
        assert task["type"] == "script"
        assert task["status"] == "draft"
        assert "log_path" in task

    @pytest.mark.asyncio
    async def test_script_task_transitions_to_wip(self, mock_db):
        """Script task transitions from draft to wip when subprocess starts."""
        _, tasks_store = mock_db
        task = await task_manager.create_script_task(
            command="echo hello",
        )
        # Give the background task time to start
        await asyncio.sleep(0.3)
        stored = tasks_store.get(task["id"])
        assert stored is not None
        assert stored["status"] in ("wip", "completed")  # fast command may complete

    @pytest.mark.asyncio
    async def test_script_task_has_pid_while_running(self, mock_db):
        """Running script task exposes subprocess PID."""
        _, tasks_store = mock_db
        task = await task_manager.create_script_task(
            command="sleep 5",
        )
        await asyncio.sleep(0.3)
        stored = tasks_store.get(task["id"])
        assert stored.get("pid") is not None
        assert stored["pid"] > 0
        # Cleanup
        await task_manager.cancel_task(task["id"])


class TestScriptTaskCompletion:
    """AC2: GET returns exit_code after completion."""

    @pytest.mark.asyncio
    async def test_successful_script_records_exit_code_zero(self, mock_db):
        """Successful script records exit_code=0."""
        _, tasks_store = mock_db
        task = await task_manager.create_script_task(
            command="echo done",
        )
        # Wait for completion
        for _ in range(20):
            await asyncio.sleep(0.1)
            stored = tasks_store.get(task["id"])
            if stored and stored.get("status") == "completed":
                break
        assert stored["exit_code"] == 0
        assert stored["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failed_script_records_nonzero_exit_code(self, mock_db):
        """Failed script records the actual exit code."""
        _, tasks_store = mock_db
        task = await task_manager.create_script_task(
            command="exit 42",
        )
        for _ in range(20):
            await asyncio.sleep(0.1)
            stored = tasks_store.get(task["id"])
            if stored and stored.get("status") in ("completed", "blocked"):
                break
        assert stored["exit_code"] == 42


class TestScriptTaskLogs:
    """AC3: Log file contains stdout."""

    @pytest.mark.asyncio
    async def test_script_output_written_to_log_file(self, mock_db):
        """Script stdout/stderr written to log_path."""
        _, tasks_store = mock_db
        task = await task_manager.create_script_task(
            command="echo test_output_123",
        )
        for _ in range(20):
            await asyncio.sleep(0.1)
            stored = tasks_store.get(task["id"])
            if stored and stored.get("status") == "completed":
                break
        log_path = stored.get("log_path")
        assert log_path is not None
        content = Path(log_path).read_text()
        assert "test_output_123" in content
        # Cleanup
        Path(log_path).unlink(missing_ok=True)


class TestScriptTaskCancel:
    """AC4: DELETE sends SIGTERM."""

    @pytest.mark.asyncio
    async def test_cancel_sends_sigterm(self, mock_db):
        """Cancelling a script task terminates the subprocess."""
        _, tasks_store = mock_db
        task = await task_manager.create_script_task(
            command="sleep 60",
        )
        await asyncio.sleep(0.3)
        stored = tasks_store.get(task["id"])
        pid = stored.get("pid")
        assert pid is not None

        result = await task_manager.cancel_task(task["id"])
        assert result is True
        await asyncio.sleep(0.3)

        # Process should be gone
        try:
            os.kill(pid, 0)
            assert False, "Process should be dead"
        except OSError:
            pass  # Expected — process terminated

        stored = tasks_store.get(task["id"])
        assert stored["status"] == "cancelled"


class TestScriptTaskConcurrency:
    """AC5: Max 2 concurrent script tasks."""

    @pytest.mark.asyncio
    async def test_third_concurrent_script_rejected(self, mock_db):
        """3rd concurrent script task raises ValueError (429 at API layer)."""
        _, tasks_store = mock_db
        # Create 2 long-running tasks
        await task_manager.create_script_task(command="sleep 30")
        await task_manager.create_script_task(command="sleep 30")
        await asyncio.sleep(0.3)  # Let them start

        # 3rd should be rejected
        with pytest.raises(ValueError, match="[Cc]oncurrent"):
            await task_manager.create_script_task(command="sleep 30")

        # Cleanup
        for task_id in list(task_manager._running_tasks.keys()):
            await task_manager.cancel_task(task_id)
