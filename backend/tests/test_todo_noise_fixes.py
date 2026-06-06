"""Tests for Radar ToDo noise reduction fixes.

AC1: Deduplicate gate — same title+source pending/in_discussion → skip creation
AC2: Evolution confidence gate — conf < 0.5 → no todo created
AC3: Auto-purge — handled/cancelled > 7 days excluded from list
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Return the singleton workspace ID."""
    import asyncio
    from tests.helpers import ensure_default_workspace
    return asyncio.run(ensure_default_workspace())


def _create_todo(client: TestClient, workspace_id: str, **overrides) -> dict:
    """Helper to create a todo."""
    payload = {
        "workspace_id": workspace_id,
        "title": overrides.pop("title", "Test ToDo"),
        "source": overrides.pop("source", "test_source"),
        "source_type": overrides.pop("source_type", "ai_detected"),
        "priority": overrides.pop("priority", "medium"),
        **overrides,
    }
    resp = client.post("/api/todos", json=payload)
    return resp.json()


class TestDeduplicateGate:
    """AC1: Duplicate todos with same title+source should be skipped."""

    def test_duplicate_title_and_source_returns_existing(self, client: TestClient, workspace_id: str):
        """Creating a todo with same title+source as pending one returns existing."""
        # First creation succeeds
        todo1 = _create_todo(client, workspace_id, title="Evolution proposal: s_foo (conf 24%)", source="evolution_pipeline")
        assert "id" in todo1

        # Second creation with same title+source should return existing (not create new)
        resp = client.post("/api/todos", json={
            "workspace_id": workspace_id,
            "title": "Evolution proposal: s_foo (conf 24%)",
            "source": "evolution_pipeline",
            "source_type": "ai_detected",
            "priority": "medium",
        })
        assert resp.status_code == 201
        todo2 = resp.json()
        assert todo2["id"] == todo1["id"]  # Same todo returned

    def test_duplicate_blocked_when_in_discussion(self, client: TestClient, workspace_id: str):
        """Dedup also matches in_discussion status."""
        todo1 = _create_todo(client, workspace_id, title="Streaming fix2", source="pipeline:run_xyz")

        # Transition to in_discussion via bind-session simulation
        # (just use PUT to update status since bind isn't easy to test)
        client.put(f"/api/todos/{todo1['id']}", json={"status": "in_discussion"})

        # Re-create: should still dedup because it's in_discussion
        resp = client.post("/api/todos", json={
            "workspace_id": workspace_id,
            "title": "Streaming fix2",
            "source": "pipeline:run_xyz",
            "source_type": "ai_detected",
            "priority": "medium",
        })
        todo2 = resp.json()
        assert todo2["id"] == todo1["id"]

    def test_different_source_creates_new(self, client: TestClient, workspace_id: str):
        """Same title but different source creates a new todo."""
        todo1 = _create_todo(client, workspace_id, title="Fix bug X", source="source_a")

        resp = client.post("/api/todos", json={
            "workspace_id": workspace_id,
            "title": "Fix bug X",
            "source": "source_b",
            "source_type": "ai_detected",
            "priority": "medium",
        })
        assert resp.status_code == 201
        todo2 = resp.json()
        assert todo2["id"] != todo1["id"]  # New todo

    def test_handled_todo_allows_recreation(self, client: TestClient, workspace_id: str):
        """A handled/cancelled todo with same title+source allows new creation."""
        todo1 = _create_todo(client, workspace_id, title="Old task", source="evolution_pipeline")
        # Mark as handled
        client.post(f"/api/todos/{todo1['id']}/mark-handled")

        # Create again — should create new since old is handled
        resp = client.post("/api/todos", json={
            "workspace_id": workspace_id,
            "title": "Old task",
            "source": "evolution_pipeline",
            "source_type": "ai_detected",
            "priority": "medium",
        })
        assert resp.status_code == 201
        todo2 = resp.json()
        assert todo2["id"] != todo1["id"]


class TestEvolutionConfidenceGate:
    """AC2: Evolution proposals with confidence < 0.5 should not create todos."""

    def test_low_confidence_no_todo(self):
        """Proposals with confidence < 0.5 write to file but skip todo creation."""
        import json
        import tempfile
        from pathlib import Path
        from core.evolution_optimizer import _write_evolution_proposal

        with tempfile.TemporaryDirectory() as tmpdir:
            ctx_dir = Path(tmpdir)
            proposal = {
                "skill_name": "test-skill",
                "confidence": 0.24,
                "score_before": 0.55,
                "score_after": 0.57,
                "changes": [{"reason": "test", "preview": "test"}],
                "proposed_at": datetime.now(timezone.utc).isoformat(),
            }

            # Patch asyncio to intercept any todo creation attempt
            with patch("core.evolution_optimizer.asyncio") as mock_asyncio:
                _write_evolution_proposal(ctx_dir, proposal)

                # Proposals file should be written
                proposals_path = ctx_dir / ".evolution_proposals.json"
                assert proposals_path.exists()
                saved = json.loads(proposals_path.read_text())
                assert len(saved) == 1
                assert saved[0]["skill_name"] == "test-skill"

                # asyncio should NOT have been imported/called (early return)
                mock_asyncio.get_running_loop.assert_not_called()
                mock_asyncio.run.assert_not_called()

    def test_high_confidence_creates_todo(self):
        """Proposals with confidence >= 0.5 should attempt to create a todo."""
        import json
        import tempfile
        from pathlib import Path
        from core.evolution_optimizer import _write_evolution_proposal

        with tempfile.TemporaryDirectory() as tmpdir:
            ctx_dir = Path(tmpdir)
            proposal = {
                "skill_name": "test-skill-high",
                "confidence": 0.65,
                "score_before": 0.55,
                "score_after": 0.70,
                "changes": [{"reason": "test", "preview": "test"}],
                "proposed_at": datetime.now(timezone.utc).isoformat(),
            }

            # For high confidence, it SHOULD try to use asyncio (todo creation)
            with patch("core.evolution_optimizer.asyncio") as mock_asyncio:
                mock_asyncio.get_running_loop.side_effect = RuntimeError("no loop")
                mock_asyncio.run.return_value = "fake_title"
                _write_evolution_proposal(ctx_dir, proposal)

                # asyncio.run SHOULD have been called (todo creation attempted)
                mock_asyncio.run.assert_called_once()


class TestAutoPurge:
    """AC3: handled/cancelled todos > 7 days should be excluded from list."""

    def test_recent_handled_still_visible(self, client: TestClient, workspace_id: str):
        """Handled todos less than 7 days old should still appear in list."""
        todo = _create_todo(client, workspace_id, title="Recent handled", source="test")
        client.post(f"/api/todos/{todo['id']}/mark-handled")

        resp = client.get("/api/todos")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()]
        assert todo["id"] in ids

    def test_old_handled_excluded(self, client: TestClient, workspace_id: str):
        """Handled todos older than 7 days should be excluded from list."""
        todo = _create_todo(client, workspace_id, title="Old handled purge", source="test_old_h")
        client.post(f"/api/todos/{todo['id']}/mark-handled")

        # Backdate via direct DB call through the app's event loop
        from core.todo_manager import todo_manager
        old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        # Use the PUT endpoint with a mock field that triggers updated_at indirectly
        # Actually, we need to directly access DB. The TestClient's app has its own event loop.
        import httpx
        # Use a helper that runs inside the test client's ASGI app event loop
        from database import db
        import anyio

        async def _backdate():
            await db.todos.update(todo["id"], {"updated_at": old_date})

        # Run via the test client's transport
        with client:
            pass  # TestClient context is already entered

        # The simplest approach: use starlette's event loop
        from starlette.testclient import TestClient as _TC
        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_backdate())
        loop.close()

        resp = client.get("/api/todos")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()]
        assert todo["id"] not in ids

    def test_old_cancelled_excluded(self, client: TestClient, workspace_id: str):
        """Cancelled todos older than 7 days should be excluded from list."""
        todo = _create_todo(client, workspace_id, title="Old cancelled purge", source="test_cancel_c")
        client.post(f"/api/todos/{todo['id']}/mark-cancelled")

        # Backdate
        from database import db
        import asyncio
        old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(db.todos.update(todo["id"], {"updated_at": old_date}))
        loop.close()

        resp = client.get("/api/todos")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()]
        assert todo["id"] not in ids

    def test_old_pending_not_excluded(self, client: TestClient, workspace_id: str):
        """Old pending todos should NOT be excluded (only handled/cancelled)."""
        todo = _create_todo(client, workspace_id, title="Old pending keep", source="test_pending_p")

        # Backdate
        from database import db
        import asyncio
        old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(db.todos.update(todo["id"], {"updated_at": old_date}))
        loop.close()

        resp = client.get("/api/todos")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()]
        assert todo["id"] in ids
