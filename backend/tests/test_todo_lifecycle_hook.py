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
from unittest.mock import AsyncMock, patch

import pytest

from hooks.todo_lifecycle_hook import (
    TodoLifecycleHook,
    _files_overlap,
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

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook.todo_manager") as mock_tm, \
         patch("hooks.todo_lifecycle_hook._get_session_commit_count", return_value=3):
        mock_db.sessions.get = AsyncMock(return_value=mock_session)
        mock_tm.transition_status = AsyncMock(return_value=True)

        await hook.execute(ctx)

        # Should mark as handled via todo_manager
        mock_tm.transition_status.assert_called_once_with(
            "todo-abc", "handled", source="hook_explicit",
        )


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

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook.todo_manager") as mock_tm, \
         patch("hooks.todo_lifecycle_hook._get_session_commit_count", return_value=0):
        mock_db.sessions.get = AsyncMock(return_value=mock_session)
        mock_tm.transition_status = AsyncMock(return_value=True)

        await hook.execute(ctx)

        mock_tm.transition_status.assert_called_once_with(
            "todo-xyz", "in_discussion", source="hook_explicit",
        )


@pytest.mark.asyncio
async def test_already_handled_todo_skipped():
    """Already-handled todos should not be re-transitioned (manager returns False)."""
    hook = TodoLifecycleHook()
    ctx = _make_context()

    mock_session = {
        "id": "test-session-1",
        "metadata": json.dumps({"todo_id": "todo-done"}),
        "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook.todo_manager") as mock_tm, \
         patch("hooks.todo_lifecycle_hook._get_session_commit_count", return_value=5):
        mock_db.sessions.get = AsyncMock(return_value=mock_session)
        # transition_status returns False for already-terminal todos
        mock_tm.transition_status = AsyncMock(return_value=False)

        await hook.execute(ctx)

        # Still called — but the manager handles the terminal-state guard
        mock_tm.transition_status.assert_called_once()


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
         patch("hooks.todo_lifecycle_hook.todo_manager") as mock_tm, \
         patch("hooks.todo_lifecycle_hook._get_session_changed_files",
               return_value=["backend/core/session_unit.py", "backend/main.py"]):
        mock_db.sessions.get = AsyncMock(return_value=mock_session)
        mock_db.todos.list_by_workspace = AsyncMock(return_value=mock_pending_todos)
        mock_tm.transition_status = AsyncMock(return_value=True)

        await hook.execute(ctx)

        # Should transition only the matching todo
        mock_tm.transition_status.assert_called_once_with(
            "todo-file-match", "in_discussion", source="hook_implicit",
        )


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


# ---------------------------------------------------------------------------
# The code repo comes from the session's own change record (usable-by-anyone P7)
# ---------------------------------------------------------------------------

def _git_repo_with_a_commit(path):
    import subprocess
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    (path / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "a.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "c"], check=True, env=env)
    return path


@pytest.mark.asyncio
async def test_commits_are_counted_in_the_repo_the_session_changed(tmp_path):
    """Any user's bound repo: the hook counts commits in the repo the session's
    "files changed" record names, and in no guessed checkout location."""
    import hooks.todo_lifecycle_hook as mod
    repo = _git_repo_with_a_commit(tmp_path / "their-repo")
    hook = TodoLifecycleHook()
    ctx = _make_context(start_time=(datetime.now() - timedelta(hours=1)).isoformat())
    session = {"id": "test-session-1", "metadata": json.dumps({"todo_id": "todo-abc"}),
               "created_at": ctx.session_start_time}

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook.todo_manager") as mock_tm, \
         patch("config.get_app_data_dir", return_value=tmp_path / "appdata"), \
         patch("core.session_changes.working_repo",
               return_value={"path": str(repo), "name": repo.name}) as wr, \
         patch("hooks.todo_lifecycle_hook._get_session_commit_count",
               wraps=mod._get_session_commit_count) as count, \
         patch.object(TodoLifecycleHook, "_write_outcome_notes", AsyncMock()):
        mock_db.sessions.get = AsyncMock(return_value=session)
        mock_tm.transition_status = AsyncMock(return_value=True)
        await hook.execute(ctx)

    assert wr.call_args.args[0] == "test-session-1"
    counted = {str(c.args[0]) for c in count.call_args_list}
    assert counted == {str(tmp_path / "appdata" / "SwarmWS"), str(repo)}
    mock_tm.transition_status.assert_called_once_with("todo-abc", "handled", source="hook_explicit")


@pytest.mark.asyncio
async def test_unreadable_change_record_counts_only_the_workspace(tmp_path):
    import sqlite3
    hook = TodoLifecycleHook()
    ctx = _make_context()
    session = {"id": "test-session-1", "metadata": json.dumps({"todo_id": "todo-abc"}),
               "created_at": ctx.session_start_time}

    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch("hooks.todo_lifecycle_hook.todo_manager") as mock_tm, \
         patch("config.get_app_data_dir", return_value=tmp_path / "appdata"), \
         patch("core.session_changes.working_repo", side_effect=sqlite3.OperationalError("locked")), \
         patch("hooks.todo_lifecycle_hook._get_session_commit_count", return_value=0) as count:
        mock_db.sessions.get = AsyncMock(return_value=session)
        mock_tm.transition_status = AsyncMock(return_value=True)
        await hook.execute(ctx)

    assert [str(c.args[0]) for c in count.call_args_list] == [str(tmp_path / "appdata" / "SwarmWS")]
    mock_tm.transition_status.assert_called_once_with("todo-abc", "in_discussion", source="hook_explicit")
