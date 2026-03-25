"""Shared test helpers for backend tests.

Provides common workspace and entity creation helpers used across 20+ test
files. These are module-level async functions (not fixtures) because
Hypothesis tests don't support pytest fixtures well.

Also provides shared Hypothesis settings constants so all PBT files
inherit max_examples from the active profile (default=30 dev, ci=100 CI)
instead of hardcoding per-file values.

Import them directly::

    from helpers import PROPERTY_SETTINGS, PROPERTY_SETTINGS_MINIMAL
    from helpers import (
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

from hypothesis import settings, HealthCheck

from database import db


# ---------------------------------------------------------------------------
# Shared Hypothesis settings — profile-aware, single source of truth
# ---------------------------------------------------------------------------
# max_examples is NOT set here — it inherits from the active Hypothesis
# profile registered in conftest.py:
#   - "default" (local dev): 30 examples per property
#   - "ci" (CI pipeline):   100 examples per property
#
# This means local dev runs ~70% fewer PBT examples than CI, which is the
# right tradeoff: fast iteration locally, thorough coverage in CI.

PROPERTY_SETTINGS = settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
"""Standard PBT settings for tests using function-scoped fixtures (most of ours).

Inherits max_examples from active profile. Use for all PBT tests unless
you have a specific reason to override.
"""

PROPERTY_SETTINGS_MINIMAL = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
"""Minimal PBT settings for structural-verification-only tests.

Use when the property verifies a type/enum invariant where 2 examples
are sufficient (e.g., round-trip serialization, enum exhaustiveness).
Always runs 2 examples regardless of profile.
"""


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
    """Create the default SwarmWS workspace config (idempotent) and return its ID.

    Checks for an existing workspace config first to avoid duplicate
    key errors across Hypothesis examples that share the same test-run
    database.
    """
    existing = await db.workspace_config.get_config()
    if existing:
        return existing["id"]

    now = now_iso()
    await db.workspace_config.put({
        "id": "swarmws",
        "name": "SwarmWS",
        "file_path": "/tmp/test-swarm-workspaces/SwarmWS",
        "icon": "🏠",
        "context": "Default SwarmAI workspace for general tasks and projects.",
        "created_at": now,
        "updated_at": now,
    })
    return "swarmws"


async def create_default_workspace() -> str:
    """Create or replace the default SwarmWS workspace config and return its ID.

    Unlike ``ensure_default_workspace``, this always upserts the row.
    Use in tests where each example needs a fresh default workspace.
    """
    now = now_iso()
    await db.workspace_config.put({
        "id": "swarmws",
        "name": "SwarmWS",
        "file_path": f"/tmp/test-swarm-workspaces/SwarmWS-{uuid4().hex[:8]}",
        "icon": "🏠",
        "context": "Default workspace",
        "created_at": now,
        "updated_at": now,
    })
    return "swarmws"


async def create_custom_workspace(
    name: str | None = None,
    index: int | None = None,
    is_archived: bool = False,
) -> str:
    """Create a workspace config entry and return its ID.

    In the single-workspace model, this creates/updates the singleton
    workspace_config row. The name parameter is accepted for backward
    compatibility but the ID is always 'swarmws'.
    """
    now = now_iso()
    ws_id = "swarmws"
    if name is None:
        name = f"TestWS-{uuid4().hex[:8]}" if index is None else f"CustomWS-{index}"
    await db.workspace_config.put({
        "id": ws_id,
        "name": name,
        "file_path": f"/tmp/test-swarm-workspaces/{name}-{uuid4().hex[:8]}",
        "icon": "📁",
        "context": f"Custom workspace: {name}",
        "created_at": now,
        "updated_at": now,
    })
    return ws_id


async def create_workspace(
    name: str = "TestWS",
    is_default: bool = False,
    is_archived: bool = False,
) -> dict:
    """Create a workspace config entry and return the full dict.

    In the single-workspace model, always uses 'swarmws' as the ID.
    """
    now = now_iso()
    ws = {
        "id": "swarmws",
        "name": name,
        "file_path": f"/tmp/test/{name}-{uuid4().hex[:6]}",
        "icon": "",
        "context": f"Context for {name}",
        "created_at": now,
        "updated_at": now,
    }
    await db.workspace_config.put(ws)
    return ws


async def create_workspace_with_path(
    tmp_path: Path,
    name: str = "TestWS",
    is_default: bool = False,
) -> dict:
    """Create a workspace config backed by a real filesystem directory.

    Used by artifact, reflection, and context tests that need to read/write
    actual files.
    """
    ws_id = "swarmws"
    ws_path = str(tmp_path / ws_id)
    os.makedirs(ws_path, exist_ok=True)

    now = now_iso()
    ws = {
        "id": ws_id,
        "name": name,
        "file_path": ws_path,
        "icon": "📁",
        "context": f"Test workspace for {name}",
        "created_at": now,
        "updated_at": now,
    }
    await db.workspace_config.put(ws)
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
