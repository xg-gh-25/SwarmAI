"""Tests Pipeline v1 run projection compatibility with the dashboard API."""
from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.run_protocol.engine import RunEngine
from routers.pipelines import _to_response


def test_active_v1_projection_maps_to_running_and_counts_stage_dict():
    response = _to_response({
        "id": "run_x", "project": "P", "requirement": "x", "status": "active",
        "profile": "direct", "stages": {"build": "degraded", "review": "completed",
                                           "test": "open",
                                           "deliver": "pending", "reflect": "pending"},
        "created_at": "now", "updated_at": "now", "audit_card": None,
    })
    assert response.status.value == "running"
    assert response.stages_completed == 2 and response.progress == "2/5"


def test_awaiting_human_maps_to_paused_and_completed_card_survives():
    waiting = _to_response({
        "id": "run_w", "project": "P", "requirement": "x", "status": "awaiting_human",
        "profile": "direct", "stages": {}, "created_at": "now", "updated_at": "now"})
    assert waiting.status.value == "paused" and waiting.pause_kind == "decision"
    done = _to_response({
        "id": "run_d", "project": "P", "requirement": "x", "status": "completed",
        "profile": "direct", "stages": {}, "created_at": "now", "updated_at": "now",
        "audit_card": "Run Audit · run_d"})
    assert done.audit_card == "Run Audit · run_d"


def _real_v1(tmp_path, monkeypatch, run_id: str) -> tuple[RunEngine, TestClient]:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("SWARM_DATA_DIR", str(data_dir))
    engine = RunEngine.create(
        data_dir=data_dir, workspace=workspace, project="P", profile="direct", requirement="test change",
        session_id="session-real", repos={}, mode="shared", run_id=run_id)
    from main import app
    patcher = patch("routers.pipelines._get_swarmws", return_value=workspace)
    patcher.start()
    client = TestClient(app)
    client._v1_patcher = patcher  # type: ignore[attr-defined]
    return engine, client


def _pause(engine: RunEngine) -> None:
    """Pause through the real producer: the daemon's Stop handler records user_stop."""
    assert engine.record_stop("user_stop", reason="user requested pause")
    engine.write_projection()


def test_real_v1_cancel_appends_abandon_event(tmp_path, monkeypatch):
    engine, client = _real_v1(tmp_path, monkeypatch, "run_cancelv1")
    try:
        response = client.patch("/api/pipelines/run_cancelv1/cancel")
        assert response.status_code == 200
        state = json.loads((engine.run_dir / "run.json").read_text())
        assert state["status"] == "abandoned"
        assert [event["kind"] for event in engine.store.read_events()][-2:] == [
            "abandon", "transition"]
        assert engine.store.read_events()[-2]["payload"]["reason"] == "cancelled_by_user"
    finally:
        client._v1_patcher.stop()  # type: ignore[attr-defined]


def test_real_v1_resume_appends_resume_event(tmp_path, monkeypatch):
    engine, client = _real_v1(tmp_path, monkeypatch, "run_resumev1")
    _pause(engine)
    try:
        response = client.patch("/api/pipelines/run_resumev1/resume")
        assert response.status_code == 200
        assert response.json()["status"] == "running"
        assert json.loads((engine.run_dir / "run.json").read_text())["status"] == "active"
        assert [event["kind"] for event in engine.store.read_events()][-2:] == [
            "resume", "transition"]
    finally:
        client._v1_patcher.stop()  # type: ignore[attr-defined]


def test_real_v1_detail_reads_stage_dict_and_reflect_artifact(tmp_path, monkeypatch):
    engine, client = _real_v1(tmp_path, monkeypatch, "run_detailv1")
    reflect = engine.run_dir / "artifacts" / "reflect.json"
    reflect.parent.mkdir(parents=True, exist_ok=True)
    reflect.write_text(json.dumps({"lessons": [
        {"lesson": "Keep event authority in the protocol"}, "Plain lesson"]}))
    try:
        response = client.get("/api/pipelines/run_detailv1")
        assert response.status_code == 200
        detail = response.json()
        assert detail["status"] == "running"
        assert detail["reflect_lessons"] == [
            "Keep event authority in the protocol", "Plain lesson"]
    finally:
        client._v1_patcher.stop()  # type: ignore[attr-defined]


def test_historical_run_cancel_and_resume_are_read_only(tmp_path, monkeypatch):
    """One-shot cutover: a pre-v1 run.json is never mutated by the dashboard."""
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "workspace"
    run_file = workspace / "Projects" / "P" / ".artifacts" / "runs" / "run_legacy1" / "run.json"
    run_file.parent.mkdir(parents=True)
    original = json.dumps({"id": "run_legacy1", "status": "paused", "checkpoint": {}})
    run_file.write_text(original)
    from main import app
    with patch("routers.pipelines._get_swarmws", return_value=workspace):
        client = TestClient(app)
        for action in ("cancel", "resume"):
            response = client.patch(f"/api/pipelines/run_legacy1/{action}")
            assert response.status_code == 410
            assert response.json()["status"] == "read_only"
        assert client.patch("/api/pipelines/run_absent1/cancel").status_code == 404
    assert run_file.read_text() == original
    assert not (run_file.parent / ".cancel_requested").exists()
