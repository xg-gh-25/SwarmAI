"""Tests for TodoLifecycleHook — post-session todo auto-completion.

Verifies:
- Explicit binding: session with metadata.todo_id auto-marks handled on commits
- Explicit binding: session with no commits transitions to in_discussion
- Implicit file matching: session file changes match todo linked_context.files
- Already-resolved todos are not re-transitioned
- Trivial sessions (< 2 messages) skip implicit matching
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hooks.todo_lifecycle_hook import (
    TodoLifecycleHook,
    _files_overlap,
    _get_session_changed_files,
    _get_session_commit_count,
)
from core.session_hooks import HookContext


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------

class TestFilesOverlap:
    """Test the file matching logic."""

    def test_exact_match(self):
        assert _files_overlap(["backend/core/session_unit.py"], ["backend/core/session_unit.py"])

    def test_basename_match(self):
        assert _files_overlap(["session_unit.py"], ["backend/core/session_unit.py"])

    def test_suffix_match(self):
        assert _files_overlap(["core/session_unit.py"], ["backend/core/session_unit.py"])

    def test_no_match(self):
        assert not _files_overlap(["foo.py"], ["backend/core/session_unit.py"])

    def test_empty_todo_files(self):
        assert not _files_overlap([], ["backend/core/session_unit.py"])

    def test_empty_changed_files(self):
        assert not _files_overlap(["foo.py"], [])

    def test_both_empty(self):
        assert not _files_overlap([], [])


# ---------------------------------------------------------------------------
# Integration tests for the hook
# ---------------------------------------------------------------------------

def _make_context(
    session_id: str = "test-session-1",
    message_count: int = 5,
    start_time: str | None = None,
) -> HookContext:
    return HookContext(
        session_id=session_id,
        agent_id="default",
        message_count=message_count,
        session_start_time=start_time or (datetime.now() - timedelta(hours=1)).isoformat(),
        session_title="Test session",
    )


@pytest.mark.asyncio
async def test_explicit_binding_with_commits():
    """Session bound to todo + commits → todo becomes handled."""
    hook = TodoLifecycleHook()
    ctx = _make_context()

    mock_session = {
        "id": "test-session-1",
        "metadata": json.dumps({"todo_id": "todo-abc"}),
        "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }
    mock_todo = {
        "id": "todo-abc",
        "status": "pending",
        "linked_context": "{}",
    }

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook._get_session_commit_count", return_value=3):
        mock_db.sessions.get = AsyncMock(return_value=mock_session)
        mock_db.todos.get = AsyncMock(return_value=mock_todo)
        mock_db.todos.update = AsyncMock()

        await hook.execute(ctx)

        # Should mark as handled
        mock_db.todos.update.assert_called_once()
        call_args = mock_db.todos.update.call_args
        assert call_args[0][0] == "todo-abc"
        assert call_args[0][1]["status"] == "handled"


@pytest.mark.asyncio
async def test_explicit_binding_no_commits():
    """Session bound to todo but no commits → todo becomes in_discussion."""
    hook = TodoLifecycleHook()
    ctx = _make_context()

    mock_session = {
        "id": "test-session-1",
        "metadata": json.dumps({"todo_id": "todo-xyz"}),
        "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }
    mock_todo = {
        "id": "todo-xyz",
        "status": "pending",
        "linked_context": "{}",
    }

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook._get_session_commit_count", return_value=0):
        mock_db.sessions.get = AsyncMock(return_value=mock_session)
        mock_db.todos.get = AsyncMock(return_value=mock_todo)
        mock_db.todos.update = AsyncMock()

        await hook.execute(ctx)

        mock_db.todos.update.assert_called_once()
        call_args = mock_db.todos.update.call_args
        assert call_args[0][0] == "todo-xyz"
        assert call_args[0][1]["status"] == "in_discussion"


@pytest.mark.asyncio
async def test_already_handled_todo_skipped():
    """Already-handled todos should not be re-transitioned."""
    hook = TodoLifecycleHook()
    ctx = _make_context()

    mock_session = {
        "id": "test-session-1",
        "metadata": json.dumps({"todo_id": "todo-done"}),
        "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }
    mock_todo = {
        "id": "todo-done",
        "status": "handled",
        "linked_context": "{}",
    }

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook._get_session_commit_count", return_value=5):
        mock_db.sessions.get = AsyncMock(return_value=mock_session)
        mock_db.todos.get = AsyncMock(return_value=mock_todo)
        mock_db.todos.update = AsyncMock()

        await hook.execute(ctx)

        # Should NOT update — already resolved
        mock_db.todos.update.assert_not_called()


@pytest.mark.asyncio
async def test_implicit_file_matching():
    """Session changes files matching a pending todo → in_discussion."""
    hook = TodoLifecycleHook()
    ctx = _make_context(message_count=5)

    mock_session = {
        "id": "test-session-1",
        "metadata": "{}",
        "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }
    mock_pending_todos = [
        {
            "id": "todo-file-match",
            "status": "pending",
            "linked_context": json.dumps({"files": ["session_unit.py"]}),
        },
        {
            "id": "todo-no-match",
            "status": "pending",
            "linked_context": json.dumps({"files": ["unrelated.py"]}),
        },
    ]

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook._get_session_changed_files",
               return_value=["backend/core/session_unit.py", "backend/main.py"]):
        mock_db.sessions.get = AsyncMock(return_value=mock_session)
        mock_db.todos.list_by_workspace = AsyncMock(return_value=mock_pending_todos)
        mock_db.todos.update = AsyncMock()

        await hook.execute(ctx)

        # Should update only the matching todo
        assert mock_db.todos.update.call_count == 1
        call_args = mock_db.todos.update.call_args
        assert call_args[0][0] == "todo-file-match"
        assert call_args[0][1]["status"] == "in_discussion"


@pytest.mark.asyncio
async def test_trivial_session_skips_implicit():
    """Sessions with < 2 messages skip implicit matching."""
    hook = TodoLifecycleHook()
    ctx = _make_context(message_count=1)

    mock_session = {
        "id": "test-session-1",
        "metadata": "{}",
        "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook._get_session_changed_files") as mock_files:
        mock_db.sessions.get = AsyncMock(return_value=mock_session)

        await hook.execute(ctx)

        # Should not even check files
        mock_files.assert_not_called()


@pytest.mark.asyncio
async def test_hook_error_isolation():
    """Hook errors are caught and logged — never propagate."""
    hook = TodoLifecycleHook()
    ctx = _make_context()

    with patch("hooks.todo_lifecycle_hook.db") as mock_db:
        mock_db.sessions.get = AsyncMock(side_effect=RuntimeError("DB down"))

        # Should NOT raise
        await hook.execute(ctx)
