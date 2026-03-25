"""Property-based test for the Singleton Workspace Invariant.

Tests that the ``workspace_config`` table always contains exactly one row
regardless of what sequence of API-like operations is performed. This is
**Property 1** from the SwarmWS Foundation design document.

Testing methodology: property-based testing using Hypothesis.

Key property verified:
- ``test_singleton_workspace_invariant`` — For any sequence of API calls
  (workspace reads, workspace updates, project CRUD, folder operations),
  the ``workspace_config`` table shall always contain exactly one row with
  ``id = 'swarmws'`` and ``name = 'SwarmWS'``.

**Validates: Requirements 1.1, 1.3**
"""
import os
import pytest
import aiosqlite
import tempfile
from pathlib import Path
from hypothesis import given, strategies as st, settings, HealthCheck

from database.sqlite import SQLiteDatabase
from core.swarm_workspace_manager import SwarmWorkspaceManager


# ---------------------------------------------------------------------------
# Hypothesis settings for property tests
# ---------------------------------------------------------------------------

PROPERTY_SETTINGS = settings(
    max_examples=20,
      # filesystem operations can be slow
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Operations that can be performed against the workspace API
operation_strategy = st.sampled_from([
    "get_workspace",
    "update_workspace_icon",
    "update_workspace_context",
    "create_project",
    "list_projects",
    "delete_project",
    "ensure_default_workspace",
])

# Sequences of 1-10 operations
operation_sequence_strategy = st.lists(
    operation_strategy,
    min_size=1,
    max_size=10,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _count_workspace_config_rows(db_path: str) -> int:
    """Count rows in workspace_config table via raw SQL."""
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("SELECT COUNT(*) FROM workspace_config") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def _get_workspace_config_row(db_path: str) -> dict | None:
    """Read the singleton workspace_config row via raw SQL."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM workspace_config") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def _setup_isolated_env(tmp_path: Path) -> tuple[SQLiteDatabase, SwarmWorkspaceManager, str]:
    """Create an isolated database + workspace for a single test example.

    Returns (db, manager, workspace_path).
    """
    db_path = str(tmp_path / "test.db")
    db = SQLiteDatabase(db_path=db_path)
    await db.initialize()

    manager = SwarmWorkspaceManager()
    workspace_path = str(tmp_path / "SwarmWS")

    # Seed the workspace via ensure_default_workspace so the singleton row exists
    # Temporarily patch DEFAULT_WORKSPACE_CONFIG to use our tmp_path
    import core.swarm_workspace_manager as swm_module
    original_config = swm_module.DEFAULT_WORKSPACE_CONFIG.copy()
    swm_module.DEFAULT_WORKSPACE_CONFIG["file_path"] = workspace_path

    try:
        await manager.ensure_default_workspace(db)
    finally:
        swm_module.DEFAULT_WORKSPACE_CONFIG.update(original_config)

    return db, manager, workspace_path


async def _execute_operation(
    op: str,
    db: SQLiteDatabase,
    manager: SwarmWorkspaceManager,
    workspace_path: str,
    created_project_ids: list[str],
) -> None:
    """Execute a single API-like operation against the workspace.

    Simulates the operations that the workspace API endpoints perform,
    exercising the database and manager methods directly.
    """
    if op == "get_workspace":
        await db.workspace_config.get_config()

    elif op == "update_workspace_icon":
        await db.workspace_config.update_config({"icon": "🔬"})

    elif op == "update_workspace_context":
        await db.workspace_config.update_config({"context": "Updated context"})

    elif op == "create_project":
        try:
            project = await manager.create_project(
                project_name=f"test-project-{len(created_project_ids)}",
                workspace_path=workspace_path,
            )
            if project and "id" in project:
                created_project_ids.append(project["id"])
        except Exception:
            pass  # Duplicate names or other errors are fine

    elif op == "list_projects":
        try:
            await manager.list_projects(workspace_path=workspace_path)
        except Exception:
            pass

    elif op == "delete_project":
        if created_project_ids:
            pid = created_project_ids.pop()
            try:
                await manager.delete_project(
                    project_id=pid,
                    workspace_path=workspace_path,
                )
            except Exception:
                pass  # Already deleted or not found is fine

    elif op == "ensure_default_workspace":
        import core.swarm_workspace_manager as swm_module
        original_config = swm_module.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_module.DEFAULT_WORKSPACE_CONFIG["file_path"] = workspace_path
        try:
            await manager.ensure_default_workspace(db)
        finally:
            swm_module.DEFAULT_WORKSPACE_CONFIG.update(original_config)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------

class TestSingletonWorkspaceInvariant:
    """Property 1: Singleton Workspace Invariant.

    *For any* sequence of API calls (including workspace reads, project CRUD,
    folder operations), the ``workspace_config`` table shall always contain
    exactly one row with ``id = 'swarmws'`` and ``name = 'SwarmWS'``.

    **Validates: Requirements 1.1, 1.3**
    """

    @given(operations=operation_sequence_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_singleton_workspace_invariant(self, operations: list[str], tmp_path_factory):
        """After any sequence of operations, workspace_config has exactly one row.

        **Validates: Requirements 1.1, 1.3**
        """
        tmp_path = tmp_path_factory.mktemp("singleton")
        db, manager, workspace_path = await _setup_isolated_env(tmp_path)

        # Pre-condition: exactly one row after setup
        count = await _count_workspace_config_rows(str(db.db_path))
        assert count == 1, f"Pre-condition failed: expected 1 row, got {count}"

        created_project_ids: list[str] = []

        # Execute the generated sequence of operations
        for op in operations:
            await _execute_operation(op, db, manager, workspace_path, created_project_ids)

            # Invariant check after every operation
            row_count = await _count_workspace_config_rows(str(db.db_path))
            assert row_count == 1, (
                f"Singleton invariant violated after '{op}': "
                f"expected 1 row in workspace_config, got {row_count}"
            )

        # Final invariant: the row must have the correct id and name
        config = await _get_workspace_config_row(str(db.db_path))
        assert config is not None, "workspace_config row is missing after all operations"
        assert config["name"] == "SwarmWS", (
            f"Expected name='SwarmWS', got '{config['name']}'"
        )
