"""Tests for Radar ToDo noise reduction fixes.

AC1: Deduplicate gate — same title+source pending/in_discussion → skip creation
AC2: Evolution confidence gate — conf < 0.5 → no todo created
AC3: Auto-purge — handled/cancelled > 7 days excluded from list
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Seed workspace via sync sqlite to avoid event loop deadlock with async conftest."""
    import sqlite3
    ws_id = "swarmws"
    # conftest swaps database.db to a temp SQLiteDatabase; reuse its path.
    from tests.conftest import _test_db
    db_file = str(_test_db.db_path)
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO workspace_config (id, name, file_path, context) VALUES (?, ?, ?, ?)",
            (ws_id, "SwarmWS", "/tmp/test-swarm-workspaces/SwarmWS", ""),
        )
        conn.commit()
    finally:
        conn.close()
    return ws_id


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


class TestEvolutionProposalPersistence:
    """Evolution proposals persist to .evolution_proposals.json and NEVER write a
    todo (run_50db230a: the ToDo card is a pure user-planning surface; a proposal's
    home is the json file, reviewed by the human there)."""

    def test_proposal_persisted_no_todo_any_confidence(self):
        """A proposal is written to the json file regardless of confidence, and no
        todo is ever created (the todo-write path was removed)."""
        import json
        import tempfile
        from pathlib import Path
        from core.evolution_optimizer import _write_evolution_proposal

        for conf in (0.24, 0.65):
            with tempfile.TemporaryDirectory() as tmpdir:
                ctx_dir = Path(tmpdir)
                proposal = {
                    "skill_name": f"test-skill-{conf}",
                    "confidence": conf,
                    "score_before": 0.55,
                    "score_after": 0.70,
                    "changes": [{"reason": "test", "preview": "test"}],
                    "proposed_at": datetime.now(timezone.utc).isoformat(),
                }

                # If any code path still tried to create a todo, this patch would trip.
                with patch("core.todo_manager.ToDoManager.create", new_callable=AsyncMock) as mock_create:
                    _write_evolution_proposal(ctx_dir, proposal)

                    # Proposal file written (the signal's real home) — for BOTH confidences.
                    proposals_path = ctx_dir / ".evolution_proposals.json"
                    assert proposals_path.exists()
                    saved = json.loads(proposals_path.read_text())
                    assert len(saved) == 1
                    assert saved[0]["skill_name"] == f"test-skill-{conf}"

                    # No todo is EVER created now — regardless of confidence.
                    mock_create.assert_not_called()


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

        # Backdate via direct SQL through the app's internal DB connection.
        # The TestClient runs the ASGI app in a thread with its own event loop.
        # We use the app's lifespan-scoped DB by calling through an endpoint hack:
        # PUT the todo with a status field (no-op since already handled) to trigger
        # an updated_at change, then use raw SQL through the test transport.
        #
        # Correct approach: use httpx AsyncClient with ASGITransport in an async test.
        old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        _backdate_todo_via_app(client, todo["id"], old_date)

        resp = client.get("/api/todos")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()]
        assert todo["id"] not in ids

    def test_old_cancelled_excluded(self, client: TestClient, workspace_id: str):
        """Cancelled todos older than 7 days should be excluded from list."""
        todo = _create_todo(client, workspace_id, title="Old cancelled purge", source="test_cancel_c")
        client.post(f"/api/todos/{todo['id']}/mark-cancelled")

        old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        _backdate_todo_via_app(client, todo["id"], old_date)

        resp = client.get("/api/todos")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()]
        assert todo["id"] not in ids

    def test_old_pending_not_excluded(self, client: TestClient, workspace_id: str):
        """Old pending todos should NOT be excluded (only handled/cancelled)."""
        todo = _create_todo(client, workspace_id, title="Old pending keep", source="test_pending_p")

        old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        _backdate_todo_via_app(client, todo["id"], old_date)

        resp = client.get("/api/todos")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()]
        assert todo["id"] in ids


def _backdate_todo_via_app(client: TestClient, todo_id: str, iso_date: str) -> None:
    """Backdate a todo's updated_at by running raw SQL through the app's DB.

    The TestClient runs the ASGI app in a background thread with its own event
    loop. We use `client.app` to access the FastAPI app, then run an async
    helper inside that app's event loop via a temporary endpoint.
    """
    from starlette.responses import JSONResponse
    from fastapi import Request

    app = client.app

    # Add a temporary test-only endpoint that does the backdate
    @app.post(f"/_test/backdate/{todo_id}")
    async def _backdate_endpoint(request: Request):
        from database import db
        # Direct SQL update bypassing schema validation.
        # db.todos is a SQLiteTable which has _get_connection().
        async with db.todos._get_connection() as conn:
            await conn.execute(
                "UPDATE todos SET updated_at = ? WHERE id = ?",
                (iso_date, todo_id),
            )
            await conn.commit()
        return JSONResponse({"ok": True})

    resp = client.post(f"/_test/backdate/{todo_id}")
    assert resp.status_code == 200, f"Backdate failed: {resp.text}"

    # Clean up the temporary route
    app.routes[:] = [r for r in app.routes if not (hasattr(r, 'path') and r.path == f"/_test/backdate/{todo_id}")]
