"""Shared test helpers for backend tests.

Provides common workspace and entity creation helpers used across 20+ test
files. These are module-level async functions (not fixtures) because
Hypothesis tests don't support pytest fixtures well.

Import them directly::

    from tests.helpers import (
        now_iso,
        ensure_default_workspace,
        create_default_workspace,
        create_custom_workspace,
        create_workspace,
        create_workspace_with_path,
        seed_todo,
    )

Naming conventions:
- ``ensure_*`` — idempotent, checks for existing record first
- ``create_*`` — always creates a new record
- ``seed_*`` — inserts raw DB rows (bypasses manager classes)
"""
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from database import db


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------

async def ensure_default_workspace() -> str:
    """Create the default SwarmWS workspace (idempotent) and return its ID.

    Checks for an existing default workspace first to avoid duplicate
    key errors across Hypothesis examples that share the same test-run
    database.
    """
    existing = await db.swarm_workspaces.get_default()
    if existing:
        return existing["id"]

    now = now_iso()
    ws_id = str(uuid4())
    await db.swarm_workspaces.put({
        "id": ws_id,
        "name": "SwarmWS",
        "file_path": "/tmp/test-swarm-workspaces/SwarmWS",
        "context": "Default SwarmAI workspace for general tasks and projects.",
        "icon": "🏠",
        "is_default": True,
        "is_archived": 0,
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
    })
    return ws_id


async def create_default_workspace() -> str:
    """Create a new default SwarmWS workspace and return its ID.

    Unlike ``ensure_default_workspace``, this always inserts a new row.
    Use in tests where each example needs a fresh default workspace.
    """
    now = now_iso()
    ws_id = str(uuid4())
    await db.swarm_workspaces.put({
        "id": ws_id,
        "name": "SwarmWS",
        "file_path": f"/tmp/test-swarm-workspaces/SwarmWS-{ws_id[:8]}",
        "context": "Default workspace",
        "icon": "🏠",
        "is_default": True,
        "is_archived": 0,
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
    })
    return ws_id


async def create_custom_workspace(
    name: str | None = None,
    index: int | None = None,
    is_archived: bool = False,
) -> str:
    """Create a custom (non-default) workspace and return its ID.

    Parameters
    ----------
    name:
        Workspace name. Defaults to ``TestWS-<uuid[:8]>``.
    index:
        Optional numeric index appended to the name (used by property tests
        that create N workspaces in a loop).
    is_archived:
        Whether the workspace should be archived.
    """
    now = now_iso()
    ws_id = str(uuid4())
    if name is None:
        name = f"TestWS-{ws_id[:8]}" if index is None else f"CustomWS-{index}-{ws_id[:8]}"
    await db.swarm_workspaces.put({
        "id": ws_id,
        "name": name,
        "file_path": f"/tmp/test-swarm-workspaces/{name}-{ws_id[:8]}",
        "context": f"Custom workspace: {name}",
        "icon": "📁",
        "is_default": False,
        "is_archived": 1 if is_archived else 0,
        "archived_at": now if is_archived else None,
        "created_at": now,
        "updated_at": now,
    })
    return ws_id


async def create_workspace(
    name: str = "TestWS",
    is_default: bool = False,
    is_archived: bool = False,
) -> dict:
    """Create a workspace and return the full dict (including ``id``).

    This is the most flexible variant — used by tests that need the full
    workspace record rather than just the ID.
    """
    now = now_iso()
    ws = {
        "id": str(uuid4()),
        "name": name,
        "file_path": f"/tmp/test/{name}-{uuid4().hex[:6]}",
        "context": f"Context for {name}",
        "icon": "",
        "is_default": 1 if is_default else 0,
        "is_archived": 1 if is_archived else 0,
        "archived_at": now if is_archived else None,
        "created_at": now,
        "updated_at": now,
    }
    await db.swarm_workspaces.put(ws)
    return ws


async def create_workspace_with_path(
    tmp_path: Path,
    name: str = "TestWS",
    is_default: bool = False,
) -> dict:
    """Create a workspace backed by a real filesystem directory.

    Used by artifact, reflection, and context tests that need to read/write
    actual files.
    """
    ws_id = str(uuid4())
    ws_path = str(tmp_path / ws_id)
    os.makedirs(ws_path, exist_ok=True)

    now = now_iso()
    ws = {
        "id": ws_id,
        "name": name,
        "file_path": ws_path,
        "context": f"Test workspace for {name}",
        "icon": "📁",
        "is_default": 1 if is_default else 0,
        "is_archived": 0,
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.swarm_workspaces.put(ws)
    return ws


# ---------------------------------------------------------------------------
# Entity seed helpers
# ---------------------------------------------------------------------------

async def seed_todo(workspace_id: str, title: str = "Test ToDo", **kwargs) -> str:
    """Insert a ToDo directly into the DB and return its ID."""
    now = now_iso()
    todo_id = str(uuid4())
    await db.todos.put({
        "id": todo_id,
        "workspace_id": workspace_id,
        "title": title,
        "description": kwargs.get("description", f"Description for {title}"),
        "source": kwargs.get("source"),
        "source_type": kwargs.get("source_type", "manual"),
        "status": kwargs.get("status", "pending"),
        "priority": kwargs.get("priority", "none"),
        "due_date": kwargs.get("due_date"),
        "task_id": kwargs.get("task_id"),
        "created_at": now,
        "updated_at": now,
    })
    return todo_id
