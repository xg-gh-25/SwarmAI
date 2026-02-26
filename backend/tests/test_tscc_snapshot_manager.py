"""Unit tests for TSCCSnapshotManager.

Tests the filesystem-based TSCC snapshot manager, covering:

- ``create_snapshot`` — JSON file creation with correct fields and colon-safe filenames
- ``list_snapshots`` — chronological ordering and corrupted-file resilience
- ``get_snapshot`` — retrieval by ID and None for missing IDs
- Deduplication within 30-second window
- Retention enforcement (max 50 snapshots per thread)
- Snapshot directory auto-creation on first write
- Constructor accepts ``state_manager`` parameter
- ``lifecycle_state`` field inclusion in snapshots
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.tscc_snapshot_manager import TSCCSnapshotManager, MAX_SNAPSHOTS_PER_THREAD
from core.tscc_state_manager import TSCCStateManager
from schemas.tscc import (
    TSCCActiveCapabilities,
    TSCCContext,
    TSCCLiveState,
    TSCCSource,
    TSCCState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(thread_id: str = "t1", project_id=None) -> TSCCState:
    """Build a minimal TSCCState for testing."""
    scope_type = "project" if project_id else "workspace"
    scope_label = f"Project: Test" if project_id else "Workspace: SwarmWS (General)"
    return TSCCState(
        thread_id=thread_id,
        project_id=project_id,
        scope_type=scope_type,
        last_updated_at=datetime.now(timezone.utc).isoformat(),
        lifecycle_state="active",
        live_state=TSCCLiveState(
            context=TSCCContext(scope_label=scope_label, thread_title="Test Thread"),
            active_agents=["ResearchAgent", "WriterAgent"],
            active_capabilities=TSCCActiveCapabilities(
                skills=["web-search"], mcps=["github"], tools=["bash"]
            ),
            what_ai_doing=["Analyzing docs", "Writing summary"],
            active_sources=[TSCCSource(path="src/main.py", origin="Project")],
            key_summary=["Found 3 issues", "Proposed fix"],
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def workspace_manager(tmp_workspace):
    """Mock SwarmWorkspaceManager that resolves to tmp_workspace."""
    mgr = MagicMock()
    mgr._resolve_workspace_path.return_value = str(tmp_workspace)
    return mgr


@pytest.fixture
def state_manager():
    """Fresh TSCCStateManager."""
    return TSCCStateManager(max_entries=200)


@pytest.fixture
def snapshot_manager(workspace_manager, state_manager):
    """TSCCSnapshotManager wired to temp workspace."""
    return TSCCSnapshotManager(workspace_manager, state_manager)


# ---------------------------------------------------------------------------
# create_snapshot
# ---------------------------------------------------------------------------

class TestCreateSnapshot:
    """Tests for create_snapshot."""

    def test_creates_json_file(self, snapshot_manager, tmp_workspace):
        state = _make_state()
        snap = snapshot_manager.create_snapshot("t1", state, "plan decomposition")
        assert snap is not None
        assert snap.snapshot_id
        assert snap.thread_id == "t1"
        assert snap.reason == "plan decomposition"
        # File should exist
        snap_dir = tmp_workspace / "chats" / "t1" / "snapshots"
        files = list(snap_dir.glob("snapshot_*.json"))
        assert len(files) == 1

    def test_colon_safe_filename(self, snapshot_manager, tmp_workspace):
        state = _make_state()
        snapshot_manager.create_snapshot("t1", state, "test")
        snap_dir = tmp_workspace / "chats" / "t1" / "snapshots"
        files = list(snap_dir.glob("snapshot_*.json"))
        filename = files[0].name
        # No colons in filename
        assert ":" not in filename
        # Matches pattern snapshot_YYYY-MM-DDTHH-MM-SSZ.json
        assert filename.startswith("snapshot_")
        assert filename.endswith(".json")

    def test_includes_lifecycle_state(self, snapshot_manager):
        state = _make_state()
        snap = snapshot_manager.create_snapshot("t1", state, "test")
        assert snap.lifecycle_state == "active"

    def test_includes_all_required_fields(self, snapshot_manager):
        state = _make_state()
        snap = snapshot_manager.create_snapshot("t1", state, "decision recorded")
        assert snap.active_agents == ["ResearchAgent", "WriterAgent"]
        assert snap.active_capabilities.skills == ["web-search"]
        assert snap.active_capabilities.mcps == ["github"]
        assert snap.active_capabilities.tools == ["bash"]
        assert snap.what_ai_doing == ["Analyzing docs", "Writing summary"]
        assert len(snap.active_sources) == 1
        assert snap.active_sources[0].path == "src/main.py"
        assert snap.key_summary == ["Found 3 issues", "Proposed fix"]

    def test_creates_directory_on_first_write(self, snapshot_manager, tmp_workspace):
        snap_dir = tmp_workspace / "chats" / "t1" / "snapshots"
        assert not snap_dir.exists()
        state = _make_state()
        snapshot_manager.create_snapshot("t1", state, "test")
        assert snap_dir.exists()

    def test_json_file_content_is_valid(self, snapshot_manager, tmp_workspace):
        state = _make_state()
        snap = snapshot_manager.create_snapshot("t1", state, "test")
        snap_dir = tmp_workspace / "chats" / "t1" / "snapshots"
        files = list(snap_dir.glob("snapshot_*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["snapshot_id"] == snap.snapshot_id
        assert data["thread_id"] == "t1"
        assert data["reason"] == "test"
        assert "lifecycle_state" in data
        assert "active_agents" in data
        assert "active_capabilities" in data
        assert "what_ai_doing" in data
        assert "active_sources" in data
        assert "key_summary" in data


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Tests for snapshot deduplication within 30-second window."""

    def test_same_reason_within_window_deduplicates(self, snapshot_manager):
        state = _make_state()
        snap1 = snapshot_manager.create_snapshot("t1", state, "same reason")
        snap2 = snapshot_manager.create_snapshot("t1", state, "same reason")
        assert snap1 is not None
        assert snap2 is None  # deduplicated

    def test_different_reason_not_deduplicated(self, snapshot_manager):
        state = _make_state()
        snap1 = snapshot_manager.create_snapshot("t1", state, "reason A")
        snap2 = snapshot_manager.create_snapshot("t1", state, "reason B")
        assert snap1 is not None
        assert snap2 is not None

    def test_same_reason_after_window_creates_second(
        self, snapshot_manager, tmp_workspace
    ):
        state = _make_state()
        snap1 = snapshot_manager.create_snapshot("t1", state, "old reason")
        assert snap1 is not None

        # Manually backdate the existing snapshot file by 31 seconds
        snap_dir = tmp_workspace / "chats" / "t1" / "snapshots"
        files = list(snap_dir.glob("snapshot_*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=31)
        data["timestamp"] = old_ts.isoformat()
        files[0].write_text(json.dumps(data), encoding="utf-8")

        snap2 = snapshot_manager.create_snapshot("t1", state, "old reason")
        assert snap2 is not None


# ---------------------------------------------------------------------------
# list_snapshots
# ---------------------------------------------------------------------------

class TestListSnapshots:
    """Tests for list_snapshots."""

    def test_returns_empty_for_no_snapshots(self, snapshot_manager):
        result = snapshot_manager.list_snapshots("nonexistent")
        assert result == []

    def test_returns_chronological_order(self, snapshot_manager):
        state = _make_state()
        snap1 = snapshot_manager.create_snapshot("t1", state, "first")
        # Use different reason to avoid dedup
        snap2 = snapshot_manager.create_snapshot("t1", state, "second")
        snap3 = snapshot_manager.create_snapshot("t1", state, "third")

        result = snapshot_manager.list_snapshots("t1")
        assert len(result) == 3
        assert result[0].reason == "first"
        assert result[1].reason == "second"
        assert result[2].reason == "third"

    def test_skips_corrupted_json(self, snapshot_manager, tmp_workspace):
        state = _make_state()
        snapshot_manager.create_snapshot("t1", state, "valid")

        # Write a corrupted file
        snap_dir = tmp_workspace / "chats" / "t1" / "snapshots"
        bad_file = snap_dir / "snapshot_9999-01-01T00-00-00Z.json"
        bad_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        result = snapshot_manager.list_snapshots("t1")
        # Should have the valid one, skip the corrupted one
        assert len(result) == 1
        assert result[0].reason == "valid"


# ---------------------------------------------------------------------------
# get_snapshot
# ---------------------------------------------------------------------------

class TestGetSnapshot:
    """Tests for get_snapshot."""

    def test_returns_correct_snapshot(self, snapshot_manager):
        state = _make_state()
        snap = snapshot_manager.create_snapshot("t1", state, "target")
        result = snapshot_manager.get_snapshot("t1", snap.snapshot_id)
        assert result is not None
        assert result.snapshot_id == snap.snapshot_id
        assert result.reason == "target"

    def test_returns_none_for_nonexistent_id(self, snapshot_manager):
        state = _make_state()
        snapshot_manager.create_snapshot("t1", state, "exists")
        result = snapshot_manager.get_snapshot("t1", "nonexistent-uuid")
        assert result is None

    def test_returns_none_for_nonexistent_thread(self, snapshot_manager):
        result = snapshot_manager.get_snapshot("no-thread", "no-snap")
        assert result is None


# ---------------------------------------------------------------------------
# Retention enforcement
# ---------------------------------------------------------------------------

class TestRetentionEnforcement:
    """Tests for retention cap (MAX_SNAPSHOTS_PER_THREAD = 50)."""

    def test_deletes_oldest_when_exceeding_cap(self, snapshot_manager, tmp_workspace):
        state = _make_state()
        snap_dir = tmp_workspace / "chats" / "t1" / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)

        # Pre-populate 50 snapshot files with backdated timestamps
        for i in range(50):
            ts = datetime(2025, 1, 1, 0, i, 0, tzinfo=timezone.utc)
            data = {
                "snapshot_id": f"old-{i}",
                "thread_id": "t1",
                "timestamp": ts.isoformat(),
                "reason": f"reason-{i}",
                "lifecycle_state": "active",
                "active_agents": [],
                "active_capabilities": {"skills": [], "mcps": [], "tools": []},
                "what_ai_doing": [],
                "active_sources": [],
                "key_summary": [],
            }
            safe = ts.strftime("%Y-%m-%dT%H-%M-%SZ")
            (snap_dir / f"snapshot_{safe}.json").write_text(
                json.dumps(data), encoding="utf-8"
            )

        assert len(list(snap_dir.glob("snapshot_*.json"))) == 50

        # Create one more — should trigger retention, deleting the oldest
        snapshot_manager.create_snapshot("t1", state, "new one")
        files = list(snap_dir.glob("snapshot_*.json"))
        assert len(files) == 50  # still capped at 50


# ---------------------------------------------------------------------------
# Constructor and state_manager integration
# ---------------------------------------------------------------------------

class TestConstructor:
    """Tests that constructor accepts state_manager parameter."""

    def test_accepts_state_manager(self, workspace_manager, state_manager):
        mgr = TSCCSnapshotManager(workspace_manager, state_manager)
        assert mgr._state_manager is state_manager
        assert mgr._workspace_manager is workspace_manager


# ---------------------------------------------------------------------------
# Project-scoped path resolution
# ---------------------------------------------------------------------------

class TestPathResolution:
    """Tests for _get_snapshot_dir path resolution."""

    def test_workspace_scoped_path(self, snapshot_manager, tmp_workspace):
        snap_dir = snapshot_manager._get_snapshot_dir("t1")
        expected = Path(str(tmp_workspace)) / "chats" / "t1" / "snapshots"
        assert snap_dir == expected

    def test_project_scoped_path(
        self, workspace_manager, state_manager, tmp_workspace
    ):
        # Put a state with project_id into the state manager
        from collections import OrderedDict
        state = _make_state(thread_id="t2", project_id="proj-123")
        state_manager._states["t2"] = state

        # Mock _find_project_dir to return a project directory
        project_dir = tmp_workspace / "Projects" / "MyProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        workspace_manager._find_project_dir.return_value = project_dir

        mgr = TSCCSnapshotManager(workspace_manager, state_manager)
        snap_dir = mgr._get_snapshot_dir("t2")
        expected = project_dir / "chats" / "t2" / "snapshots"
        assert snap_dir == expected
