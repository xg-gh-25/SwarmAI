"""Tests for TodoLifecycleHook — post-session todo auto-completion.

Verifies (design docs/design/2026-10-25-landings-consumers.md D3: the session's own
records, never a git time window; the hook never sets ``handled``):
- explicit binding: a recorded outcome (card rows, outputs, landings) sends an active
  ToDo to review (review_state='completed', status unchanged); none -> in_discussion;
  a finished ToDo is not changed; each write is conditional (db.todos.update_if);
- another session's commits in the same hour count for nothing (no git runs at all);
- notes: "N recorded files", "at least" when a part is capped or unreadable, and the
  landings' subjects; one file as a card row and a landing file is one entry;
- implicit matching: from outputs and from the working repo's rows / landing files,
  not from another bound repo;
- a context with deleting=True changes nothing; trivial sessions skip implicit
  matching; errors never propagate;
- db.todos.update_if against a real table.
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


def _session(todo_id=None):
    meta = json.dumps({"todo_id": todo_id}) if todo_id else "{}"
    return {"id": "test-session-1", "metadata": meta, "created_at": "2026-10-25T10:00:00"}


def _row(path, root="/r/proj", **kw):
    return {"path": path, "absolutePath": f"{root}/{path}", "gitRoot": root, **kw}


def _landing(*paths, root="/r/proj", subjects=("fix: one",)):
    return {"repo": "proj", "gitRoot": root, "dest": "main", "branch": "session/k-x",
            "count": len(subjects), "subjects": [{"sha": "abc1234", "subject": s} for s in subjects],
            "files": [_row(p, root) for p in paths]}


def _records(cards=(), outputs=(), landings=(), *, working="/r/proj", cards_partial=False,
             outputs_partial=False, landings_partial=False):
    """Patch the hook's three record readers (each runs on its own pool)."""
    import hooks.todo_lifecycle_hook as mod
    return [
        patch.object(mod, "_card_part", lambda sid, db_path: {
            "rows": list(cards), "working": working, "partial": cards_partial}),
        patch.object(mod, "_output_part", lambda sid: {
            "rows": list(outputs), "partial": outputs_partial}),
        patch.object(mod, "_landing_part", lambda sid: {
            "landings": list(landings), "partial": landings_partial}),
    ]


async def _run(ctx, session, *records, update_if=True):
    from contextlib import ExitStack
    hook = TodoLifecycleHook()
    with ExitStack() as st:
        for r in records:
            st.enter_context(r)
        mock_db = st.enter_context(patch("hooks.todo_lifecycle_hook.db"))
        st.enter_context(patch("core.todo_manager.db", mock_db))
        mock_tm = st.enter_context(patch("hooks.todo_lifecycle_hook.todo_manager"))
        from core.todo_manager import ToDoManager
        mock_tm.to_review = ToDoManager().to_review      # the real write, on the mocked db
        run = st.enter_context(patch("subprocess.run", side_effect=AssertionError("no git")))
        mock_db.sessions.get = AsyncMock(return_value=session)
        mock_db.todos.get = AsyncMock(return_value={"id": "x", "updated_at": "u0",
                                                    "linked_context": "{}"})
        mock_db.todos.update_if = AsyncMock(return_value=update_if)
        mock_db.todos.list_by_workspace = AsyncMock(return_value=[])
        mock_tm.transition_status = AsyncMock(return_value=True)
        await hook.execute(ctx)
        assert not run.called
        return mock_db, mock_tm, lambda: _note_of(mock_db)


def _note_of(mock_db):
    """The notes the hook's (last) conditional write carried."""
    return json.loads(mock_db.todos.update_if.call_args.args[1]["linked_context"])["notes"]


@pytest.mark.asyncio
async def test_a_recorded_outcome_sends_the_todo_to_review_never_handled():
    db_, tm, notes = await _run(_make_context(), _session("todo-abc"),
                                *_records(cards=[_row("a.py")]))
    db_.todos.update_if.assert_called_once()
    args, kw = db_.todos.update_if.call_args
    assert args[0] == "todo-abc" and args[1]["review_state"] == "completed"
    assert "status" not in args[1] and args[1]["completed_at"]
    assert kw == {"status_in": ("pending", "overdue", "in_discussion"), "review_state_null": True,
                  "where_eq": {"updated_at": "u0"}}
    tm.transition_status.assert_not_called()
    assert "1 recorded file" in notes()


@pytest.mark.asyncio
async def test_no_record_means_in_discussion_conditionally():
    db_, tm, notes = await _run(_make_context(), _session("todo-xyz"), *_records())
    args, kw = db_.todos.update_if.call_args
    assert args == ("todo-xyz", {"status": "in_discussion"})
    assert kw["review_state_null"] is True and "handled" not in kw["status_in"]


@pytest.mark.asyncio
async def test_a_refused_conditional_write_writes_no_notes():
    """A Confirm / Reject (or a finished ToDo) wins: no notes are appended."""
    db_, _tm, _notes = await _run(_make_context(), _session("todo-done"),
                                  *_records(cards=[_row("a.py")]), update_if=False)
    import core.todo_manager as tm_mod
    assert db_.todos.update_if.call_count == tm_mod._CAS_TRIES   # re-read, re-tried, then left


@pytest.mark.asyncio
async def test_outputs_and_landings_are_an_outcome_and_one_file_counts_once():
    land = _landing("a.py", subjects=("fix: the thing", "feat: more"))
    _db, _tm, notes = await _run(
        _make_context(), _session("todo-abc"),
        *_records(cards=[_row("a.py")], landings=[land],
                  outputs=[{"path": "Knowledge/n.md", "absolutePath": "/ws/Knowledge/n.md"}]))
    text = notes()
    assert "2 recorded files" in text and "at least" not in text
    assert "session/k-x → main" in text and "fix: the thing" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["cards_partial", "outputs_partial", "landings_partial"])
async def test_a_capped_or_unreadable_part_says_at_least(which):
    _db, _tm, notes = await _run(_make_context(), _session("todo-abc"),
                                 *_records(cards=[_row("a.py")], **{which: True}))
    assert "at least 1 recorded file" in notes()


@pytest.mark.asyncio
async def test_an_unreadable_record_is_partial_and_the_rest_still_counts():
    import hooks.todo_lifecycle_hook as mod
    recs = _records(outputs=[{"path": "n.md", "absolutePath": "/ws/n.md"}])
    recs[0] = patch.object(mod, "_card_part", side_effect=RuntimeError("locked"))
    _db, _tm, notes = await _run(_make_context(), _session("todo-abc"), *recs)
    assert "at least 1 recorded file" in notes()


@pytest.mark.asyncio
async def test_a_deleted_session_changes_no_todo():
    import dataclasses
    ctx = dataclasses.replace(_make_context(), deleting=True)
    db_, tm, _notes = await _run(ctx, _session("todo-abc"), *_records(cards=[_row("a.py")]))
    db_.sessions.get.assert_not_called()
    db_.todos.update_if.assert_not_called()
    tm.transition_status.assert_not_called()


@pytest.mark.asyncio
async def test_implicit_matching_uses_outputs_and_the_working_repo_only():
    import hooks.todo_lifecycle_hook as mod
    todos = [{"id": "t-out", "status": "pending", "linked_context": json.dumps({"files": ["n.md"]})},
             {"id": "t-work", "status": "pending", "linked_context": json.dumps({"files": ["core/a.py"]})},
             {"id": "t-other", "status": "pending", "linked_context": json.dumps({"files": ["config.ts"]})}]
    recs = _records(cards=[_row("src/core/a.py")],
                    landings=[_landing("src/config.ts", root="/r/other")],
                    outputs=[{"path": "Knowledge/n.md", "absolutePath": "/ws/Knowledge/n.md"}])
    from contextlib import ExitStack
    with ExitStack() as st:
        for r in recs:
            st.enter_context(r)
        mock_db = st.enter_context(patch("hooks.todo_lifecycle_hook.db"))
        mock_tm = st.enter_context(patch("hooks.todo_lifecycle_hook.todo_manager"))
        mock_db.sessions.get = AsyncMock(return_value=_session())
        mock_db.todos.list_by_workspace = AsyncMock(return_value=todos)
        mock_tm.transition_status = AsyncMock(return_value=True)
        await TodoLifecycleHook().execute(_make_context(message_count=5))
    moved = [c.args[0] for c in mock_tm.transition_status.call_args_list]
    assert moved == ["t-out", "t-work"], "config.ts changed only in another bound repo"
    assert mod is not None


@pytest.mark.asyncio
async def test_trivial_session_skips_implicit():
    import hooks.todo_lifecycle_hook as mod
    with patch("hooks.todo_lifecycle_hook.db") as mock_db, \
         patch.object(mod, "_card_part") as cards:
        mock_db.sessions.get = AsyncMock(return_value=_session())
        await TodoLifecycleHook().execute(_make_context(message_count=1))
        cards.assert_not_called()


@pytest.mark.asyncio
async def test_hook_error_isolation():
    with patch("hooks.todo_lifecycle_hook.db") as mock_db:
        mock_db.sessions.get = AsyncMock(side_effect=RuntimeError("DB down"))
        await TodoLifecycleHook().execute(_make_context())       # never raises


@pytest.mark.asyncio
async def test_update_if_is_one_conditional_write(tmp_path):
    from database.sqlite import SQLiteDatabase
    d = SQLiteDatabase(db_path=str(tmp_path / "t.db"))
    await d.initialize()
    now = "2026-10-25T00:00:00"
    base = {"workspace_id": "swarmws", "title": "t", "source_type": "manual", "priority": "none",
            "created_at": now, "updated_at": now}
    for tid, status, review in (("a", "pending", None), ("b", "handled", None),
                                ("c", "pending", "completed")):
        await d.todos.put({"id": tid, "status": status, "review_state": review, **base})
    act = ("pending", "overdue", "in_discussion")
    upd = {"review_state": "completed", "completed_at": now}
    assert await d.todos.update_if("a", dict(upd), status_in=act, review_state_null=True) is True
    assert await d.todos.update_if("b", dict(upd), status_in=act, review_state_null=True) is False
    assert await d.todos.update_if("c", {"status": "in_discussion"}, status_in=act,
                                   review_state_null=True) is False
    assert (await d.todos.get("a"))["review_state"] == "completed"
    assert (await d.todos.get("b"))["review_state"] is None
    assert (await d.todos.get("c"))["status"] == "pending"


@pytest.mark.asyncio
async def test_notes_and_review_state_are_one_write_that_loses_to_a_user_edit(tmp_path, monkeypatch):
    """The notes go in the same conditional write as the review state, guarded by the
    updated_at the hook read: a person's edit in between wins, nothing is overwritten."""
    from database.sqlite import SQLiteDatabase
    import hooks.todo_lifecycle_hook as mod
    d = SQLiteDatabase(db_path=str(tmp_path / "t.db"))
    await d.initialize()
    now = "2026-10-25T00:00:00"
    await d.todos.put({"id": "a", "status": "pending", "review_state": None, "workspace_id": "swarmws",
                       "title": "t", "source_type": "manual", "priority": "none", "created_at": now,
                       "updated_at": now, "linked_context": json.dumps({"notes": "mine"})})
    out = {"entries": {("repo", "/r", "a.py")}, "landings": [], "partial": False, "match": []}
    import core.todo_manager as tm_mod
    monkeypatch.setattr(mod, "db", d)
    monkeypatch.setattr(tm_mod, "db", d)
    await TodoLifecycleHook()._handle_explicit_binding("a", out)
    row = await d.todos.get("a")
    assert row["review_state"] == "completed"
    notes = json.loads(row["linked_context"])["notes"]
    assert notes.startswith("mine\n") and "1 recorded file" in notes

    await d.todos.put({"id": "b", "status": "pending", "review_state": None, "workspace_id": "swarmws",
                       "title": "t", "source_type": "manual", "priority": "none", "created_at": now,
                       "updated_at": now, "linked_context": "{}"})
    real_get = d.todos.get

    async def get_then_user_edits(tid):
        row = await real_get(tid)
        import sqlite3                      # a person's edit right after the hook read
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        conn.execute("UPDATE todos SET linked_context = ?, updated_at = ? WHERE id = ?",
                     (json.dumps({"notes": "user"}), "2026-10-25T00:00:01", tid))
        conn.commit()
        conn.close()
        return row
    monkeypatch.setattr(d.todos, "get", get_then_user_edits)
    monkeypatch.setattr(tm_mod, "_CAS_TRIES", 1)
    await TodoLifecycleHook()._handle_explicit_binding("b", out)
    row = await real_get("b")
    assert json.loads(row["linked_context"]) == {"notes": "user"} and row["review_state"] is None
