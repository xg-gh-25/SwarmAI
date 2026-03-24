"""Tests for the Escalation Protocol v2 (core/escalation.py).

Covers: data model, L0 INFORM, L1 CONSULT, L2 BLOCK, SSE event builder,
persistence (save/load/list), resolution flow, Radar todo integration,
timeout resolution, and REST API endpoints.

# Feature: escalation-protocol-v2
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.escalation import (
    Escalation,
    Level,
    Option,
    block,
    build_sse_event,
    consult,
    create_radar_todo,
    get_open_escalations,
    inform,
    load_escalation,
    resolve,
    resolve_expired,
    save_escalation,
    mark_todo_handled,
)


# ---------------------------------------------------------------------------
# L0 INFORM
# ---------------------------------------------------------------------------

class TestInform:
    def test_inform_creates_l0(self):
        esc = inform(
            title="Chose approach 2",
            situation="PRODUCT.md aligns with caching strategy.",
            trigger="CLEAR_EVALUATION",
            pipeline_stage="evaluate",
            project="TestProject",
        )
        assert esc.level == Level.INFORM
        assert esc.status == "resolved"
        assert esc.resolved_by == "swarm"
        assert esc.title == "Chose approach 2"
        assert esc.id.startswith("esc_")

    def test_inform_no_options(self):
        esc = inform(title="FYI", situation="All good.")
        assert esc.options == []
        assert esc.recommendation is None

    def test_inform_with_evidence(self):
        esc = inform(
            title="Test",
            situation="Context",
            evidence=["PRODUCT.md: caching is priority #1"],
        )
        assert len(esc.evidence) == 1


# ---------------------------------------------------------------------------
# L2 BLOCK
# ---------------------------------------------------------------------------

class TestBlock:
    def test_block_creates_l2_open(self):
        esc = block(
            title="Ambiguous scope: improve performance",
            situation="Cannot determine what to optimize.",
            options=[
                Option(label="API latency", description="Focus on backend response times"),
                Option(label="UI render", description="Focus on frontend paint time"),
                Option(label="Discuss", description="Let me explain more"),
            ],
            trigger="AMBIGUOUS_SCOPE",
            pipeline_stage="evaluate",
            project="ClientApp",
        )
        assert esc.level == Level.BLOCK
        assert esc.status == "open"
        assert len(esc.options) == 3
        assert esc.resolved_at is None

    def test_block_with_recommendation(self):
        esc = block(
            title="Architecture choice",
            situation="Monolith vs microservice.",
            options=[
                Option(label="Monolith", description="Keep it simple", is_recommendation=True),
                Option(label="Microservice", description="Scale later"),
            ],
            recommendation="Monolith — team of 1, not worth the overhead.",
        )
        assert esc.recommendation is not None
        assert esc.options[0].is_recommendation is True

    def test_block_without_project_is_ephemeral(self):
        esc = block(
            title="What to focus on?",
            situation="Multiple priorities.",
            options=[Option(label="A", description="Option A")],
        )
        assert esc.project is None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class TestResolve:
    def test_resolve_changes_status(self):
        esc = block(
            title="Test", situation="Test",
            options=[Option(label="Yes", description="Do it")],
        )
        resolved = resolve(esc, resolution="Yes", resolved_by="user")
        assert resolved.status == "resolved"
        assert resolved.resolved_by == "user"
        assert resolved.resolution == "Yes"
        assert resolved.resolved_at is not None
        # Original unchanged
        assert esc.status == "open"

    def test_resolve_preserves_context(self):
        esc = block(
            title="T", situation="S",
            options=[Option(label="A", description="B")],
            project="P", pipeline_stage="evaluate",
            evidence=["doc1"],
        )
        resolved = resolve(esc, "A")
        assert resolved.project == "P"
        assert resolved.pipeline_stage == "evaluate"
        assert resolved.evidence == ["doc1"]


# ---------------------------------------------------------------------------
# SSE Event Builder
# ---------------------------------------------------------------------------

class TestSSEEvent:
    def test_event_has_required_fields(self):
        esc = block(
            title="Test", situation="Context",
            options=[Option(label="A", description="B", risk="low")],
            trigger="AMBIGUOUS_SCOPE",
            pipeline_stage="evaluate",
            project="X",
        )
        event = build_sse_event(esc)
        assert event["type"] == "escalation"
        assert event["level"] == 2
        assert event["levelName"] == "BLOCK"
        assert event["trigger"] == "AMBIGUOUS_SCOPE"
        assert event["title"] == "Test"
        assert event["situation"] == "Context"
        assert event["pipelineStage"] == "evaluate"
        assert event["project"] == "X"
        assert event["status"] == "open"
        assert len(event["options"]) == 1
        assert event["options"][0]["label"] == "A"
        assert event["options"][0]["risk"] == "low"

    def test_event_is_json_serializable(self):
        esc = inform(title="T", situation="S", evidence=["e1"])
        event = build_sse_event(esc)
        # Must not raise
        serialized = json.dumps(event)
        assert '"type": "escalation"' in serialized

    def test_inform_event_has_level_0(self):
        esc = inform(title="T", situation="S")
        event = build_sse_event(esc)
        assert event["level"] == 0
        assert event["levelName"] == "INFORM"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    @pytest.fixture()
    def workspace(self, tmp_path):
        """Create a temp workspace with project structure."""
        project_dir = tmp_path / "Projects" / "TestProject"
        project_dir.mkdir(parents=True)
        return tmp_path

    def test_save_and_load(self, workspace):
        esc = block(
            title="Test", situation="Context",
            options=[Option(label="A", description="B")],
            project="TestProject",
        )
        save_escalation(workspace, esc)
        loaded = load_escalation(workspace, "TestProject", esc.id)
        assert loaded is not None
        assert loaded.id == esc.id
        assert loaded.title == "Test"
        assert loaded.level == Level.BLOCK
        assert len(loaded.options) == 1
        assert loaded.options[0].label == "A"

    def test_save_noop_without_project(self, workspace):
        esc = inform(title="T", situation="S")
        # Should not raise
        save_escalation(workspace, esc)
        # Nothing was written
        assert not (workspace / "Projects").exists() or \
            not list((workspace / "Projects").rglob("esc_*.json"))

    def test_get_open_escalations(self, workspace):
        esc1 = block(title="Open1", situation="S", options=[], project="TestProject")
        esc2 = block(title="Open2", situation="S", options=[], project="TestProject")
        esc3 = resolve(
            block(title="Resolved", situation="S", options=[], project="TestProject"),
            resolution="Done",
        )
        save_escalation(workspace, esc1)
        save_escalation(workspace, esc2)
        save_escalation(workspace, esc3)

        open_escs = get_open_escalations(workspace, "TestProject")
        assert len(open_escs) == 2
        titles = {e.title for e in open_escs}
        assert titles == {"Open1", "Open2"}

    def test_load_nonexistent_returns_none(self, workspace):
        result = load_escalation(workspace, "TestProject", "esc_nonexistent")
        assert result is None

    def test_get_open_empty_project(self, workspace):
        result = get_open_escalations(workspace, "TestProject")
        assert result == []


# ---------------------------------------------------------------------------
# Level enum
# ---------------------------------------------------------------------------

class TestLevel:
    def test_level_ordering(self):
        assert Level.INFORM < Level.CONSULT < Level.BLOCK

    def test_level_names(self):
        assert Level.INFORM.name == "INFORM"
        assert Level.CONSULT.name == "CONSULT"
        assert Level.BLOCK.name == "BLOCK"


# ---------------------------------------------------------------------------
# Helpers for v2 tests
# ---------------------------------------------------------------------------

def _sample_options() -> list[Option]:
    return [
        Option(label="Option A", description="Do A", risk="low", is_recommendation=True),
        Option(label="Option B", description="Do B", risk="medium"),
    ]


@pytest.fixture
def todo_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with the todos table."""
    db_path = tmp_path / "data.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE todos (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            source TEXT,
            source_type TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT NOT NULL DEFAULT 'none',
            due_date TEXT,
            linked_context TEXT,
            task_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# L1 CONSULT (v2)
# ---------------------------------------------------------------------------

class TestConsult:
    def test_creates_open_with_timeout(self):
        esc = consult(
            "Architecture choice", "Two approaches possible",
            _sample_options(), recommendation="Option A", timeout_hours=24,
        )
        assert esc.level == Level.CONSULT
        assert esc.status == "open"
        assert esc.timeout_at is not None
        assert esc.recommendation == "Option A"
        timeout = datetime.fromisoformat(esc.timeout_at)
        delta = timeout - datetime.now(timezone.utc)
        assert 23 < delta.total_seconds() / 3600 < 25

    def test_zero_timeout_disables_auto_accept(self):
        esc = consult("Q", "S", _sample_options(), timeout_hours=0)
        assert esc.timeout_at is None

    def test_custom_timeout(self):
        esc = consult("Q", "S", _sample_options(), timeout_hours=4)
        timeout = datetime.fromisoformat(esc.timeout_at)
        delta = timeout - datetime.now(timezone.utc)
        assert 3 < delta.total_seconds() / 3600 < 5

    def test_consult_sse_event(self):
        esc = consult("Q", "S", _sample_options(), recommendation="A")
        evt = build_sse_event(esc)
        assert evt["level"] == 1
        assert evt["levelName"] == "CONSULT"
        assert evt["recommendation"] == "A"

    def test_resolve_consult_with_override(self):
        esc = consult("Q", "S", _sample_options(), recommendation="A")
        resolved = resolve(esc, resolution="Override: B", resolved_by="user")
        assert resolved.status == "resolved"
        assert resolved.resolution == "Override: B"

    def test_save_and_load_consult(self, tmp_path):
        ws = tmp_path
        (ws / "Projects" / "P" / ".artifacts" / "escalations").mkdir(parents=True)
        esc = consult(
            "Q", "S", _sample_options(),
            project="P", recommendation="A", timeout_hours=12,
        )
        save_escalation(ws, esc)
        loaded = load_escalation(ws, "P", esc.id)
        assert loaded.level == Level.CONSULT
        assert loaded.timeout_at is not None
        assert loaded.recommendation == "A"


# ---------------------------------------------------------------------------
# Radar Todo Integration (v2)
# ---------------------------------------------------------------------------

class TestRadarTodo:
    def test_l0_skipped(self, todo_db: Path):
        esc = inform("FYI", "Info")
        assert create_radar_todo(esc, db_path=todo_db) is None

    def test_l2_creates_high_priority_todo(self, todo_db: Path):
        esc = block(
            "Need decision", "Ambiguous scope",
            _sample_options(), project="SwarmAI", pipeline_stage="evaluate",
        )
        todo_id = create_radar_todo(esc, db_path=todo_db)
        assert todo_id is not None

        conn = sqlite3.connect(str(todo_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        conn.close()

        assert row["priority"] == "high"
        assert row["source"] == f"escalation:{esc.id}"
        assert row["source_type"] == "ai_detected"
        assert "[BLOCK]" in row["title"]
        assert row["status"] == "pending"

        ctx = json.loads(row["linked_context"])
        assert ctx["escalation_id"] == esc.id
        assert ctx["escalation_level"] == "BLOCK"
        assert len(ctx["options"]) == 2

    def test_l1_creates_medium_priority_todo(self, todo_db: Path):
        esc = consult(
            "Arch choice", "Two approaches", _sample_options(),
            project="SwarmAI", recommendation="Option A", timeout_hours=24,
        )
        todo_id = create_radar_todo(esc, db_path=todo_db)
        assert todo_id is not None

        conn = sqlite3.connect(str(todo_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        conn.close()

        assert row["priority"] == "medium"
        assert "[CONSULT]" in row["title"]
        assert row["due_date"] == esc.timeout_at

    def test_missing_db_returns_none(self, tmp_path: Path):
        esc = block("Q", "S", _sample_options())
        assert create_radar_todo(esc, db_path=tmp_path / "nope.db") is None

    def test_mark_todo_handled(self, todo_db: Path):
        esc = block("Q", "S", _sample_options(), project="X")
        todo_id = create_radar_todo(esc, db_path=todo_db)
        assert todo_id is not None

        mark_todo_handled(esc.id, db_path=todo_db)

        conn = sqlite3.connect(str(todo_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM todos WHERE id = ?", (todo_id,)).fetchone()
        conn.close()
        assert row["status"] == "handled"

    def test_mark_todo_handled_noop_missing_db(self, tmp_path: Path):
        # Should not raise
        mark_todo_handled("esc_nope", db_path=tmp_path / "nope.db")

    def test_todo_description_includes_options(self, todo_db: Path):
        opts = [
            Option(label="Fast path", description="Quick fix", is_recommendation=True),
            Option(label="Proper fix", description="Full refactor"),
        ]
        esc = block("Design Q", "Need to choose", opts, project="P")
        todo_id = create_radar_todo(esc, db_path=todo_db)

        conn = sqlite3.connect(str(todo_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT description FROM todos WHERE id = ?", (todo_id,)).fetchone()
        conn.close()

        assert "Fast path" in row["description"]
        assert "(recommended)" in row["description"]
        assert "Proper fix" in row["description"]


# ---------------------------------------------------------------------------
# Timeout Resolution (v2)
# ---------------------------------------------------------------------------

class TestTimeoutResolution:
    @pytest.fixture()
    def workspace(self, tmp_path):
        (tmp_path / "Projects" / "P" / ".artifacts" / "escalations").mkdir(parents=True)
        return tmp_path

    def _make_expired_consult(self, **kwargs) -> Escalation:
        """Create an L1 CONSULT with timeout in the past."""
        esc = consult(
            kwargs.get("title", "Expired Q"),
            kwargs.get("situation", "Sit"),
            kwargs.get("options", _sample_options()),
            project=kwargs.get("project", "P"),
            recommendation=kwargs.get("recommendation", "Option A"),
            timeout_hours=1,
        )
        # Overwrite timeout to the past
        return Escalation(**{
            **esc.__dict__,
            "timeout_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        })

    def test_resolves_expired_l1(self, workspace):
        esc = self._make_expired_consult()
        save_escalation(workspace, esc)

        resolved = resolve_expired(workspace, "P")
        assert len(resolved) == 1
        assert resolved[0].status == "resolved"
        assert resolved[0].resolved_by == "timeout"
        assert resolved[0].resolution == "Option A"

        loaded = load_escalation(workspace, "P", esc.id)
        assert loaded.status == "resolved"

    def test_skips_non_expired(self, workspace):
        esc = consult("Future", "S", _sample_options(), project="P", timeout_hours=24)
        save_escalation(workspace, esc)
        assert resolve_expired(workspace, "P") == []

    def test_skips_l2_block(self, workspace):
        esc = block("Blocked", "Need input", _sample_options(), project="P")
        save_escalation(workspace, esc)
        assert resolve_expired(workspace, "P") == []

    def test_uses_deferred_when_no_recommendation(self, workspace):
        esc = self._make_expired_consult(recommendation=None)
        save_escalation(workspace, esc)

        resolved = resolve_expired(workspace, "P")
        assert len(resolved) == 1
        assert resolved[0].resolution == "deferred (timeout)"

    def test_idempotent(self, workspace):
        esc = self._make_expired_consult()
        save_escalation(workspace, esc)

        assert len(resolve_expired(workspace, "P")) == 1
        assert len(resolve_expired(workspace, "P")) == 0  # second call is no-op

    def test_timeout_marks_radar_todo_handled(self, workspace, todo_db):
        esc = self._make_expired_consult()
        save_escalation(workspace, esc)
        todo_id = create_radar_todo(esc, db_path=todo_db)
        assert todo_id is not None

        import core.escalation as esc_mod
        original_db = esc_mod._DB_PATH
        esc_mod._DB_PATH = todo_db
        try:
            resolve_expired(workspace, "P")
        finally:
            esc_mod._DB_PATH = original_db

        conn = sqlite3.connect(str(todo_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM todos WHERE id = ?", (todo_id,)).fetchone()
        conn.close()
        assert row["status"] == "handled"

    def test_empty_project_returns_empty(self, workspace):
        assert resolve_expired(workspace, "P") == []


# ---------------------------------------------------------------------------
# REST API (escalations router, v2)
# ---------------------------------------------------------------------------

class TestEscalationAPI:
    @pytest.fixture()
    def workspace(self, tmp_path):
        (tmp_path / "Projects" / "P" / ".artifacts" / "escalations").mkdir(parents=True)
        return tmp_path

    @pytest.fixture
    def client(self, workspace, monkeypatch):
        monkeypatch.setattr("routers.escalations._WORKSPACE_ROOT", workspace)
        from routers.escalations import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_list_empty(self, client):
        resp = client.get("/api/escalations/P")
        assert resp.status_code == 200
        data = resp.json()
        assert data["open"] == []
        assert data["auto_resolved"] == []

    def test_list_with_open(self, client, workspace):
        esc = block("Q", "S", _sample_options(), project="P")
        save_escalation(workspace, esc)
        resp = client.get("/api/escalations/P")
        assert len(resp.json()["open"]) == 1

    def test_get_single(self, client, workspace):
        esc = block("Q", "S", _sample_options(), project="P")
        save_escalation(workspace, esc)
        resp = client.get(f"/api/escalations/P/{esc.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == esc.id

    def test_get_not_found(self, client):
        assert client.get("/api/escalations/P/esc_nope").status_code == 404

    def test_resolve_endpoint(self, client, workspace):
        esc = block("Q", "S", _sample_options(), project="P")
        save_escalation(workspace, esc)

        resp = client.post(
            f"/api/escalations/P/{esc.id}/resolve",
            json={"resolution": "Go with A"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"

        loaded = load_escalation(workspace, "P", esc.id)
        assert loaded.status == "resolved"

    def test_resolve_already_resolved_409(self, client, workspace):
        esc = resolve(block("Q", "S", _sample_options(), project="P"), "Done")
        save_escalation(workspace, esc)
        resp = client.post(
            f"/api/escalations/P/{esc.id}/resolve",
            json={"resolution": "Again"},
        )
        assert resp.status_code == 409

    def test_resolve_not_found_404(self, client):
        resp = client.post(
            "/api/escalations/P/esc_nope/resolve",
            json={"resolution": "X"},
        )
        assert resp.status_code == 404

    def test_list_auto_resolves_expired_l1(self, client, workspace):
        esc = consult("Expired", "S", _sample_options(), project="P", recommendation="A")
        esc = Escalation(**{
            **esc.__dict__,
            "timeout_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        })
        save_escalation(workspace, esc)

        resp = client.get("/api/escalations/P")
        data = resp.json()
        assert len(data["auto_resolved"]) == 1
        assert data["auto_resolved"][0]["id"] == esc.id
        assert len(data["open"]) == 0
