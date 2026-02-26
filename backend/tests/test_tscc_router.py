"""Unit tests for the TSCC FastAPI router endpoints.

Tests the four TSCC API endpoints defined in ``routers/tscc.py``:

- ``GET  /api/chat_threads/{thread_id}/tscc``                    — get state
- ``POST /api/chat_threads/{thread_id}/snapshots``               — create snapshot
- ``GET  /api/chat_threads/{thread_id}/snapshots``               — list snapshots
- ``GET  /api/chat_threads/{thread_id}/snapshots/{snapshot_id}`` — get snapshot

Testing methodology: unit tests with mocked TSCCStateManager and
TSCCSnapshotManager injected via ``register_tscc_dependencies()``.

Key invariants verified:
- 200 responses for valid resources
- 404 responses for missing threads/snapshots
- 409 response for duplicate snapshots
- All response field names are snake_case
"""

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from schemas.tscc import (
    TSCCActiveCapabilities,
    TSCCContext,
    TSCCLiveState,
    TSCCSnapshot,
    TSCCSource,
    TSCCState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


def _all_keys_snake_case(obj) -> bool:
    """Recursively check that every key in a JSON-like object is snake_case."""
    if isinstance(obj, dict):
        for key in obj:
            if not SNAKE_CASE_RE.match(key):
                return False
            if not _all_keys_snake_case(obj[key]):
                return False
    elif isinstance(obj, list):
        for item in obj:
            if not _all_keys_snake_case(item):
                return False
    return True


def _make_tscc_state(thread_id: str = "thread-1") -> TSCCState:
    """Build a minimal valid TSCCState for testing."""
    return TSCCState(
        thread_id=thread_id,
        project_id=None,
        scope_type="workspace",
        last_updated_at=datetime.now(timezone.utc).isoformat(),
        lifecycle_state="active",
        live_state=TSCCLiveState(
            context=TSCCContext(
                scope_label="Workspace: SwarmWS (General)",
                thread_title="Test Thread",
                mode=None,
            ),
            active_agents=["SwarmAgent"],
            active_capabilities=TSCCActiveCapabilities(
                skills=["code-review"],
                mcps=["filesystem"],
                tools=["read_file"],
            ),
            what_ai_doing=["Analyzing code structure"],
            active_sources=[
                TSCCSource(path="src/main.py", origin="Project"),
            ],
            key_summary=["Initial analysis complete"],
        ),
    )


def _make_snapshot(
    thread_id: str = "thread-1",
    snapshot_id: str = "snap-001",
    reason: str = "plan decomposition",
) -> TSCCSnapshot:
    """Build a minimal valid TSCCSnapshot for testing."""
    return TSCCSnapshot(
        snapshot_id=snapshot_id,
        thread_id=thread_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        reason=reason,
        lifecycle_state="active",
        active_agents=["SwarmAgent"],
        active_capabilities=TSCCActiveCapabilities(
            skills=["code-review"], mcps=[], tools=[]
        ),
        what_ai_doing=["Reviewing changes"],
        active_sources=[TSCCSource(path="src/app.py", origin="Project")],
        key_summary=["Changes look good"],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_managers():
    """Create mock state and snapshot managers and register them."""
    state_mgr = AsyncMock()
    snap_mgr = MagicMock()

    import routers.tscc as tscc_mod
    tscc_mod._state_manager = state_mgr
    tscc_mod._snapshot_manager = snap_mgr

    yield state_mgr, snap_mgr

    # Reset to None after test
    tscc_mod._state_manager = None
    tscc_mod._snapshot_manager = None


# ---------------------------------------------------------------------------
# GET /api/chat_threads/{thread_id}/tscc
# ---------------------------------------------------------------------------

class TestGetTSCCState:
    """Tests for the GET tscc state endpoint."""

    def test_returns_200_with_valid_state(self, client: TestClient, mock_managers):
        state_mgr, _ = mock_managers
        state = _make_tscc_state("thread-abc")
        state_mgr.get_state.return_value = state

        resp = client.get("/api/chat_threads/thread-abc/tscc")

        assert resp.status_code == 200
        body = resp.json()
        assert body["thread_id"] == "thread-abc"
        assert body["lifecycle_state"] == "active"
        assert body["live_state"]["active_agents"] == ["SwarmAgent"]

    def test_returns_404_for_missing_thread(self, client: TestClient, mock_managers):
        state_mgr, _ = mock_managers
        state_mgr.get_state.return_value = None

        resp = client.get("/api/chat_threads/no-such-thread/tscc")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_response_fields_are_snake_case(self, client: TestClient, mock_managers):
        state_mgr, _ = mock_managers
        state_mgr.get_state.return_value = _make_tscc_state()

        resp = client.get("/api/chat_threads/thread-1/tscc")

        assert resp.status_code == 200
        assert _all_keys_snake_case(resp.json())


# ---------------------------------------------------------------------------
# POST /api/chat_threads/{thread_id}/snapshots
# ---------------------------------------------------------------------------

class TestCreateSnapshot:
    """Tests for the POST create snapshot endpoint."""

    def test_creates_snapshot_returns_200(self, client: TestClient, mock_managers):
        state_mgr, snap_mgr = mock_managers
        state = _make_tscc_state("thread-1")
        state_mgr.get_state.return_value = state
        snap_mgr.create_snapshot.return_value = _make_snapshot("thread-1")

        resp = client.post(
            "/api/chat_threads/thread-1/snapshots",
            json={"reason": "plan decomposition"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["thread_id"] == "thread-1"
        assert body["reason"] == "plan decomposition"
        snap_mgr.create_snapshot.assert_called_once()

    def test_returns_404_when_thread_missing(self, client: TestClient, mock_managers):
        state_mgr, _ = mock_managers
        state_mgr.get_state.return_value = None

        resp = client.post(
            "/api/chat_threads/missing/snapshots",
            json={"reason": "test"},
        )

        assert resp.status_code == 404

    def test_returns_409_on_duplicate(self, client: TestClient, mock_managers):
        state_mgr, snap_mgr = mock_managers
        state_mgr.get_state.return_value = _make_tscc_state()
        snap_mgr.create_snapshot.return_value = None  # dedup triggered

        resp = client.post(
            "/api/chat_threads/thread-1/snapshots",
            json={"reason": "same reason"},
        )

        assert resp.status_code == 409
        assert "duplicate" in resp.json()["detail"].lower()

    def test_response_fields_are_snake_case(self, client: TestClient, mock_managers):
        state_mgr, snap_mgr = mock_managers
        state_mgr.get_state.return_value = _make_tscc_state()
        snap_mgr.create_snapshot.return_value = _make_snapshot()

        resp = client.post(
            "/api/chat_threads/thread-1/snapshots",
            json={"reason": "test"},
        )

        assert resp.status_code == 200
        assert _all_keys_snake_case(resp.json())


# ---------------------------------------------------------------------------
# GET /api/chat_threads/{thread_id}/snapshots
# ---------------------------------------------------------------------------

class TestListSnapshots:
    """Tests for the GET list snapshots endpoint."""

    def test_returns_list_in_chronological_order(
        self, client: TestClient, mock_managers
    ):
        _, snap_mgr = mock_managers
        snap_a = _make_snapshot(snapshot_id="snap-a", reason="first")
        snap_b = _make_snapshot(snapshot_id="snap-b", reason="second")
        snap_mgr.list_snapshots.return_value = [snap_a, snap_b]

        resp = client.get("/api/chat_threads/thread-1/snapshots")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["snapshot_id"] == "snap-a"
        assert body[1]["snapshot_id"] == "snap-b"

    def test_returns_empty_list_when_no_snapshots(
        self, client: TestClient, mock_managers
    ):
        _, snap_mgr = mock_managers
        snap_mgr.list_snapshots.return_value = []

        resp = client.get("/api/chat_threads/thread-1/snapshots")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_response_fields_are_snake_case(self, client: TestClient, mock_managers):
        _, snap_mgr = mock_managers
        snap_mgr.list_snapshots.return_value = [_make_snapshot()]

        resp = client.get("/api/chat_threads/thread-1/snapshots")

        assert resp.status_code == 200
        for item in resp.json():
            assert _all_keys_snake_case(item)


# ---------------------------------------------------------------------------
# GET /api/chat_threads/{thread_id}/snapshots/{snapshot_id}
# ---------------------------------------------------------------------------

class TestGetSnapshot:
    """Tests for the GET single snapshot endpoint."""

    def test_returns_200_for_existing_snapshot(
        self, client: TestClient, mock_managers
    ):
        _, snap_mgr = mock_managers
        snap = _make_snapshot(snapshot_id="snap-xyz")
        snap_mgr.get_snapshot.return_value = snap

        resp = client.get("/api/chat_threads/thread-1/snapshots/snap-xyz")

        assert resp.status_code == 200
        assert resp.json()["snapshot_id"] == "snap-xyz"

    def test_returns_404_for_missing_snapshot(
        self, client: TestClient, mock_managers
    ):
        _, snap_mgr = mock_managers
        snap_mgr.get_snapshot.return_value = None

        resp = client.get("/api/chat_threads/thread-1/snapshots/no-such")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_response_fields_are_snake_case(self, client: TestClient, mock_managers):
        _, snap_mgr = mock_managers
        snap_mgr.get_snapshot.return_value = _make_snapshot()

        resp = client.get("/api/chat_threads/thread-1/snapshots/snap-001")

        assert resp.status_code == 200
        assert _all_keys_snake_case(resp.json())
