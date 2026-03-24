"""Tests for the /api/pipelines endpoint.

Tests cover:
- Empty dashboard (no pipeline runs)
- Dashboard with active runs from filesystem
- Active-only filter
- Multi-project aggregation
- Corrupt/invalid JSON files are skipped gracefully
- Summary counts are accurate
"""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Patch the SwarmWS path before importing the router
_test_workspace = None


def _patched_swarmws():
    return _test_workspace


@pytest.fixture
def workspace(tmp_path):
    """Create a test workspace with Projects/ directory."""
    global _test_workspace
    _test_workspace = tmp_path
    return tmp_path


@pytest.fixture
def client(workspace):
    """Create a test client with patched workspace path."""
    with patch("routers.pipelines._get_swarmws", return_value=workspace):
        from main import app
        yield TestClient(app)


def _create_run(workspace: Path, project: str, run_id: str, **overrides) -> Path:
    """Helper: create a pipeline run file on disk."""
    artifacts_dir = workspace / "Projects" / project / ".artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "id": run_id,
        "project": project,
        "requirement": overrides.get("requirement", "Test requirement"),
        "profile": overrides.get("profile", "full"),
        "status": overrides.get("status", "running"),
        "stages": overrides.get("stages", []),
        "taste_decisions": overrides.get("taste_decisions", []),
        "budget": {},
        "checkpoint": overrides.get("checkpoint", None),
        "created_at": "2026-03-24T10:00:00+00:00",
        "updated_at": overrides.get("updated_at", "2026-03-24T10:00:00+00:00"),
        "completed_at": overrides.get("completed_at", None),
    }

    run_file = artifacts_dir / f"pipeline-run-{run_id}.json"
    run_file.write_text(json.dumps(state), encoding="utf-8")
    return run_file


class TestPipelinesEndpoint:
    def test_empty_dashboard(self, client, workspace):
        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["pipelines"] == []
        assert data["summary"]["running"] == 0

    def test_returns_active_run(self, client, workspace):
        _create_run(workspace, "TestProject", "run_abc123",
                     requirement="Add payment retry",
                     stages=[
                         {"stage": "evaluate", "status": "completed", "token_cost": 9000},
                         {"stage": "build", "status": "completed", "token_cost": 45000},
                     ])

        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

        pipeline = data["pipelines"][0]
        assert pipeline["id"] == "run_abc123"
        assert pipeline["project"] == "TestProject"
        assert pipeline["requirement"] == "Add payment retry"
        assert pipeline["status"] == "running"
        assert pipeline["stages_completed"] == 2
        assert pipeline["stages_total"] == 8
        assert pipeline["tokens_consumed"] == 54000
        assert pipeline["progress"] == "2/8"

    def test_active_only_filter(self, client, workspace):
        _create_run(workspace, "Proj", "run_active", status="running",
                     updated_at="2026-03-24T10:01:00+00:00")
        _create_run(workspace, "Proj", "run_done", status="completed",
                     updated_at="2026-03-24T10:00:00+00:00")

        # Without filter: both
        resp = client.get("/api/pipelines")
        assert resp.json()["count"] == 2

        # With filter: only active
        resp = client.get("/api/pipelines?active=true")
        data = resp.json()
        assert data["count"] == 1
        assert data["pipelines"][0]["id"] == "run_active"

    def test_multi_project(self, client, workspace):
        _create_run(workspace, "ProjectA", "run_a1")
        _create_run(workspace, "ProjectB", "run_b1")

        resp = client.get("/api/pipelines")
        data = resp.json()
        assert data["count"] == 2
        projects = {p["project"] for p in data["pipelines"]}
        assert projects == {"ProjectA", "ProjectB"}

    def test_paused_run_with_checkpoint(self, client, workspace):
        _create_run(workspace, "Proj", "run_paused",
                     status="paused",
                     checkpoint={
                         "reason": "L2 BLOCK: ambiguous scope",
                         "stage": "plan",
                         "checkpointed_at": "2026-03-24T10:05:00+00:00",
                         "completed_stages": ["evaluate", "think"],
                     })

        resp = client.get("/api/pipelines")
        pipeline = resp.json()["pipelines"][0]
        assert pipeline["status"] == "paused"
        assert pipeline["checkpoint"]["reason"] == "L2 BLOCK: ambiguous scope"
        assert pipeline["checkpoint"]["stage"] == "plan"

    def test_summary_counts(self, client, workspace):
        _create_run(workspace, "P", "run_1", status="running",
                     stages=[{"stage": "evaluate", "status": "completed", "token_cost": 10000}])
        _create_run(workspace, "P", "run_2", status="paused",
                     stages=[{"stage": "evaluate", "status": "completed", "token_cost": 8000}])
        _create_run(workspace, "P", "run_3", status="completed",
                     stages=[{"stage": "evaluate", "status": "completed", "token_cost": 5000}])

        resp = client.get("/api/pipelines")
        summary = resp.json()["summary"]
        assert summary["running"] == 1
        assert summary["paused"] == 1
        assert summary["completed"] == 1
        assert summary["total_tokens"] == 23000

    def test_corrupt_json_skipped(self, client, workspace):
        # Create a valid run
        _create_run(workspace, "Proj", "run_valid")

        # Create a corrupt file
        artifacts_dir = workspace / "Projects" / "Proj" / ".artifacts"
        (artifacts_dir / "pipeline-run-run_corrupt.json").write_text("not json{{{")

        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1  # corrupt skipped

    def test_invalid_status_falls_back(self, client, workspace):
        _create_run(workspace, "Proj", "run_bad_status", status="invalid_status")

        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        # Should fall back to "running" instead of crashing
        assert resp.json()["pipelines"][0]["status"] == "running"

    def test_trivial_profile_stage_count(self, client, workspace):
        _create_run(workspace, "Proj", "run_trivial", profile="trivial")

        resp = client.get("/api/pipelines")
        assert resp.json()["pipelines"][0]["stages_total"] == 6  # trivial has 6 stages

    def test_taste_decisions_counted(self, client, workspace):
        _create_run(workspace, "Proj", "run_taste",
                     taste_decisions=[
                         {"stage": "think", "description": "d1", "classification": "taste"},
                         {"stage": "build", "description": "d2", "classification": "taste"},
                     ])

        resp = client.get("/api/pipelines")
        assert resp.json()["pipelines"][0]["taste_decisions"] == 2
