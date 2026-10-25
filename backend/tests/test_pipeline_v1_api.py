"""Tests Pipeline v1 run projection compatibility with the dashboard API."""
from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.run_protocol.engine import RunEngine
from routers.pipelines import _to_response


def _git_repo(path):
    """A one-commit git repo: every code run names one (S6-5 removed the shared mode)."""
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "a.txt").write_text("a\n")
    for args in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i")):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    return path


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
        session_id="session-real", repos={"repo": str(_git_repo(tmp_path / "repo"))},
        run_id=run_id)
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


def test_a_run_without_a_v1_contract_is_not_found(tmp_path, monkeypatch):
    """No old-pipeline compatibility: a run.json with no contract is neither listed
    nor acted on, and is never mutated."""
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
            assert response.status_code == 404
        assert client.get("/api/pipelines/run_legacy1").status_code == 404
        assert client.get("/api/pipelines").json()["count"] == 0
        assert client.get("/api/pipelines/analytics").json()["overall"]["total_runs"] == 0
    assert run_file.read_text() == original
    assert not (run_file.parent / ".cancel_requested").exists()


def test_list_analytics_and_detail_read_the_v1_projection(tmp_path, monkeypatch):
    """R1b: the list, the analytics and the detail read only v1 runs, from their
    projection: finished_at gives the duration, measured metric events give the
    tokens, and an unmeasured run carries no token number."""
    from core.run_protocol.store import NEXT
    engine, client = _real_v1(tmp_path, monkeypatch, "run_readmodel1")
    legacy = engine.run_dir.parent / "run_legacy2" / "run.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"id": "run_legacy2", "status": "running", "stages": []}))
    try:
        listed = client.get("/api/pipelines").json()
        assert [r["id"] for r in listed["pipelines"]] == ["run_readmodel1"]
        assert listed["pipelines"][0]["tokens_consumed"] is None
        row = client.get("/api/pipelines/analytics").json()["by_project"][0]["runs"][0]
        assert row["cycle_time_min"] is None and row["owner_session_id"] == "session-real"
        for name, value in (("input_tokens", 1200), ("output_tokens", 300)):
            engine.store.append_new(writer=engine.authority.writer("cli"), kind="metric",
                                    at="2026-10-23T00:00:00+00:00", stage="reflect",
                                    attempt=1, ordinal=NEXT,
                                    payload={"name": name, "value": value})
        engine.abandon(reason="cancelled_by_user")
        engine.write_projection()
        from core import run_index
        run_index._clear_for_tests()
        data = client.get("/api/pipelines/analytics").json()
        assert data["overall"]["tokens_actual"] == 1500
        row = data["by_project"][0]["runs"][0]
        # the user cancelled it themselves: nothing for them to act on (R5 review)
        assert row["status"] == "abandoned" and row["needs_you"] is False
        assert isinstance(row["cycle_time_min"], float)
        assert client.get("/api/pipelines/run_readmodel1").json()["status"] == "abandoned"
    finally:
        client._v1_patcher.stop()  # type: ignore[attr-defined]


def test_the_listed_identity_is_the_run_directory_not_run_json(tmp_path, monkeypatch):
    """R1b review: an agent that rewrites run A's run.json id/project to run B's must
    not make the dashboard show or act on B; a malformed projection does not break the
    list, and an unknown status is not shown as running."""
    engine, client = _real_v1(tmp_path, monkeypatch, "run_identity_a")
    path = engine.run_dir / "run.json"
    value = json.loads(path.read_text())
    value.update({"id": "run_someone_else", "project": "Other", "requirement": 7,
                  "updated_at": 5, "status": "bogus"})
    path.write_text(json.dumps(value))
    from core import run_index
    run_index._clear_for_tests()
    try:
        rows = client.get("/api/pipelines").json()["pipelines"]
        assert [(r["id"], r["project"], r["status"]) for r in rows] == [
            ("run_identity_a", "P", "failed")]
        assert rows[0]["tokens_consumed"] is None
        overall = client.get("/api/pipelines/analytics").json()["overall"]
        assert overall["tokens_actual"] is None and "tokens_est" not in overall
    finally:
        client._v1_patcher.stop()  # type: ignore[attr-defined]


def test_a_run_the_user_cancelled_does_not_need_them(tmp_path, monkeypatch):
    """R5 review: Cancel is the user's own decision — no Need You item for it; an
    abandon for any other reason still needs them, with a state-specific title."""
    from core import attention_authority as aa
    from core import pipeline_metrics as pm
    assert pm.needs_you("abandoned", None, pm.USER_CANCEL_REASON) is False
    assert pm.needs_you("abandoned", None, "checks_exhausted") is True
    base = {"profile": "direct", "stages": {}, "created_at": "", "updated_at": "",
            "_project": "P", "session_id": "s-own"}
    items = aa._collect_paused_runs([
        {**base, "id": "run_c1", "status": "abandoned", "reason": pm.USER_CANCEL_REASON,
         "requirement": "a"},
        {**base, "id": "run_c2", "status": "abandoned", "reason": "checks_exhausted",
         "requirement": "b"},
        {**base, "id": "run_c3", "status": "awaiting_human", "requirement": "c"},
    ])
    by_id = {i.id: i for i in items}
    assert set(by_id) == {"paused_run:run_c2", "paused_run:run_c3"}
    assert by_id["paused_run:run_c3"].title == "Answer a question a pipeline run is waiting on"
    assert by_id["paused_run:run_c3"].summary == "c"
    assert by_id["paused_run:run_c3"].dispatch["context"]["owner_session_id"] == "s-own"
    assert "checks_exhausted" in by_id["paused_run:run_c2"].dispatch["message"]
