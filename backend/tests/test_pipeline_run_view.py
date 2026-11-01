"""A local run's big card (design docs/design/2026-11-01-brain-eval-and-runs.md §4.2).

Questions, one per test:
- does each state offer the commands it can take, in order, the first that asks the
  person primary (answer, resume, continue, confirm landed, cancel, open session)?
- does GET /{id}/view give the row, the retrospective, the stages, the acceptance and
  the commands in one read?
- can a person confirm from the dashboard that an abandoned run landed as a commit the
  engine could not match, recorded as the chat path records it (a rescue), and is it
  refused for a run that is not abandoned and for a command (run_owner_gate)?
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemas.pipeline_run import PipelineDecision, PipelineRunSummary
from tests.pipeline_v1_rig import Rig, _git, write

pytestmark = pytest.mark.timeout(300)


def _s(**o) -> PipelineRunSummary:
    base = dict(id="run_a", requirement="r", status="running", profile="direct", progress="1/3",
                cycle_time_min=None, tokens_actual=None, created_at="", updated_at="",
                pause_kind=None, needs_you=False, landed_as=None, checkpoint_reason=None,
                owner_session_id="s1", not_landed=False, report_path=None, audit_card=None)
    return PipelineRunSummary(**{**base, **o})


def test_each_state_offers_its_commands():
    from routers.pipelines import run_actions
    keys = lambda acts: [(a["key"], a["primary"]) for a in acts]
    decision = PipelineDecision(request_id="d1", options=[])
    assert keys(run_actions(_s(status="paused", pause_kind="decision", needs_you=True), decision)) == [
        ("answer", True), ("cancel", False), ("open_session", False)]
    assert keys(run_actions(_s(status="paused", pause_kind="stopped", needs_you=True), None)) == [
        ("resume", True), ("cancel", False), ("open_session", False)]
    assert keys(run_actions(_s(pause_kind="idle", needs_you=True), None)) == [
        ("continue", True), ("cancel", False), ("open_session", False)]
    assert keys(run_actions(_s(status="abandoned", needs_you=True), None)) == [
        ("confirm_landed", True), ("open_session", False)]
    # cancelled by the person (needs_you False) or landed as: nothing to confirm
    assert keys(run_actions(_s(status="abandoned", needs_you=False, owner_session_id=None), None)) == []
    assert keys(run_actions(_s(status="completed", owner_session_id=None), None)) == []
    # a question that could not be read: its chat card answers it
    assert keys(run_actions(_s(status="paused", pause_kind="decision", needs_you=True), None)) == [
        ("open_session", True), ("cancel", False)]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    monkeypatch.setenv("SWARM_DATA_DIR", str(r.data))
    monkeypatch.setattr("routers.pipelines._get_swarmws", lambda: r.ws)
    yield r
    r.close()


def _client() -> TestClient:
    from routers.pipelines import router
    app = FastAPI()
    app.include_router(router, prefix="/api/pipelines")
    return TestClient(app)


def _hand_landing(r: Rig) -> str:
    """Abandon the run, then land a commit that differs from the reviewed change."""
    r.create()
    r.drive(until="deliver")
    assert r.v1("abandon", "--reason", "landed by hand").get("ok") is True
    write(r.repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a  # by hand\n"})
    _git(r.repo, "add", "-A")
    _git(r.repo, "commit", "-q", "-m", "fix: swap, adjusted while landing")
    return _git(r.repo, "rev-parse", "HEAD")


def test_view_gives_the_card_in_one_read(rig):
    rig.create()
    rig.drive(until="deliver")
    got = _client().get(f"/api/pipelines/{rig.run_id}/view")
    assert got.status_code == 200, got.text
    v = got.json()
    assert v["summary"]["id"] == rig.run_id and v["detail"]["id"] == rig.run_id
    assert [s["name"] for s in v["stages"]] and all("attempts" in s for s in v["stages"])
    assert {"answers", "corrections", "rescues"} <= set(v["interventions"])
    assert isinstance(v["acceptance"], list)
    assert [a["key"] for a in v["actions"]][-1] == "open_session"
    assert _client().get("/api/pipelines/run_nosuch/view").status_code == 404


def test_a_person_confirms_a_hand_landing_from_the_dashboard(rig):
    from core import pipeline_metrics as pm
    from core import run_interventions
    landed = _hand_landing(rig)
    before = _client().get(f"/api/pipelines/{rig.run_id}/view").json()
    assert ("confirm_landed", True) in [(a["key"], a["primary"]) for a in before["actions"]]
    out = _client().post(f"/api/pipelines/{rig.run_id}/confirm-landed", json={"commits": {"repo": landed}})
    assert out.status_code == 200, out.text
    assert out.json()["landed_as"] == {"repo": landed}
    raw = json.loads((rig.engine().run_dir / "run.json").read_text(encoding="utf-8"))
    assert raw["landed_as"] == {"repo": landed}
    assert pm.needs_you(raw["status"], None, raw.get("reason"), raw.get("landed_as")) is False
    event = [e for e in rig.events() if e["kind"] == "landed_as"][-1]
    assert event["payload"]["request_id"]                       # bound to the person's request
    assert run_interventions.count(raw, corrections=0)["rescues"] == 1
    after = _client().get(f"/api/pipelines/{rig.run_id}/view").json()
    assert "confirm_landed" not in [a["key"] for a in after["actions"]]
    assert [x["kind"] for x in after["interventions"]["rescues"]] == ["confirm_landed"]
    again = _client().post(f"/api/pipelines/{rig.run_id}/confirm-landed", json={"commits": {"repo": landed}})
    assert again.status_code == 409


def test_confirm_landed_is_refused_for_a_live_run_and_a_bad_body(rig):
    rig.create()
    head = _git(rig.repo, "rev-parse", "HEAD")
    c = _client()
    assert c.post(f"/api/pipelines/{rig.run_id}/confirm-landed", json={"commits": {"repo": head}}).status_code == 409
    assert c.post(f"/api/pipelines/{rig.run_id}/confirm-landed", json={"commits": {}}).status_code == 422
    assert c.post(f"/api/pipelines/{rig.run_id}/confirm-landed", json={"commits": "x"}).status_code == 422


def test_a_command_cannot_confirm_a_landing():
    from core.run_owner_gate import _user_only_deny
    deny = _user_only_deny("curl -X POST http://localhost:18321/api/pipelines/run_ab12/confirm-landed -d '{}'")
    assert deny is not None and "person" in json.dumps(deny)
    assert _user_only_deny("grep -rn confirm-landed backend/routers") is None


def test_the_earlier_owners_survive_the_projection(rig):
    """run.json is rebuilt from the log on every write: the owner windows the person's
    corrections are read in come from the logged transfers, not a field a hook wrote."""
    from core import run_interventions
    rig.create()
    engine = rig.engine()
    first = engine.owner_session()
    assert engine.transfer_owner("0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d", reason="takeover")
    engine.write_projection()
    raw = json.loads((engine.run_dir / "run.json").read_text(encoding="utf-8"))
    assert [h["session_id"] for h in raw["session_history"]] == [first]
    assert [w[0] for w in run_interventions.windows(raw)] == [first, "0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d"]
