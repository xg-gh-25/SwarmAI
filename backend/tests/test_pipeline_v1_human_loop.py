"""Pipeline v1 human loop (docs/design/pipeline-v1-human-loop.md).

What is tested, against real v1 runs (RunEngine.create in a tmp workspace):

- a decision is asked with a deterministic AskUserQuestion payload, and ``status``
  hands the same payload back for a later turn;
- only a PERSON's pick on that card records ``human_answer`` (daemon side); free
  text, another question, or the channel gateway's unattended pick record nothing;
- the agent has no answer command, and its Bash call to the dashboard's decision
  endpoint is denied;
- Stop / a closed tab pause an active run and leave a waiting run waiting; the
  chat stop endpoint records it;
- a takeover is a signed ``owner_transfer``: the projection and every later
  authorization follow the new owner;
- the dashboard classifies decision / stopped / idle, shows the question, answers
  it, and Resume reports the owner session to continue in.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core import pipeline_human
from core import run_owner_gate as gate
from core.ask_question_manager import AskQuestionManager
from core.run_protocol import decision as decision_protocol
from core.run_protocol.engine import RunEngine

OWNER = "aaaaaaaa-1111-2222-3333-444444444444"
OTHER = "bbbbbbbb-1111-2222-3333-444444444444"


def _decision(request_id: str = "q1") -> dict:
    return {
        "request_id": request_id, "kind": "design_tradeoff",
        "question": "Which storage?", "why_evidence_insufficient": "both fit",
        "refs": [], "options": [
            {"id": "a", "choice": "files", "consequence": "simple",
             "reversible": True, "risk": "low"},
            {"id": "b", "choice": "sqlite", "consequence": "migration",
             "reversible": False, "risk": "high"}]}


@pytest.fixture()
def run(tmp_path, monkeypatch):
    workspace, data = tmp_path / "workspace", tmp_path / "data"
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setattr(pipeline_human, "_workspace", lambda: workspace)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="trivial", session_id=OWNER, repos={},
                              mode="shared", run_id="run_human1")
    engine.write_projection()
    return engine


def _ask(engine: RunEngine) -> dict:
    value = _decision()
    value["refs"] = [engine.store.read_events()[0]["event_id"]]
    return engine.request_decision(value, document="DESIGN_SPEC")


def _card_answer(payload: dict, label: str) -> dict:
    return {payload["questions"][0]["question"]: label}


def _state(engine: RunEngine) -> dict:
    return json.loads((engine.run_dir / "run.json").read_text())


# ── the question ────────────────────────────────────────────────────────────

def test_decision_request_returns_the_card_and_status_hands_it_back(run):
    asked = _ask(run)
    payload = asked["ask_user_question"]
    question = payload["questions"][0]
    assert asked["state"]["status"] == "awaiting_human"
    assert question["question"].endswith("[q1]") and question["multiSelect"] is False
    assert [o["label"] for o in question["options"]] == ["a — files", "b — sqlite"]
    assert "irreversible" in question["options"][1]["description"]
    assert run.pending_decision()["ask_user_question"] == payload


def test_option_from_answer_ignores_free_text_and_other_questions():
    value = _decision()
    payload = decision_protocol.ask_payload(value)
    assert decision_protocol.option_from_answer(value, _card_answer(payload, "b — sqlite")) == "b"
    assert decision_protocol.option_from_answer(value, _card_answer(payload, "use sqlite")) is None
    assert decision_protocol.option_from_answer(value, {"Which storage?": "a — files"}) is None
    assert decision_protocol.option_from_answer(value, "a — files") is None


# ── the answer ──────────────────────────────────────────────────────────────

def test_a_person_card_pick_records_human_answer(run):
    payload = _ask(run)["ask_user_question"]
    recorded = pipeline_human.record_card_answer(OWNER, _card_answer(payload, "b — sqlite"))
    assert recorded == [{"run_id": "run_human1", "request_id": "q1", "option_id": "b"}]
    assert _state(run)["status"] == "active"
    answers = [e for e in run.store.read_events() if e["kind"] == "human_answer"]
    assert [a["payload"]["option_id"] for a in answers] == ["b"]


def test_free_text_or_another_session_records_nothing(run):
    payload = _ask(run)["ask_user_question"]
    assert pipeline_human.record_card_answer(OWNER, _card_answer(payload, "maybe")) == []
    assert pipeline_human.record_card_answer(OTHER, _card_answer(payload, "a — files")) == []
    assert _state(run)["status"] == "awaiting_human"


def _run_gate(questions: list, answer: dict, answered_by: str, monkeypatch) -> dict:
    from core import security_hooks
    mgr = AskQuestionManager()

    def _publish(*_args, **_kwargs):
        return None
    monkeypatch.setattr("core.permission_manager.permission_manager.publish_prompt",
                        _publish)
    hook = security_hooks.create_ask_question_gate(OWNER, {"sdk_session_id": OWNER}, mgr)

    async def _go():
        task = asyncio.ensure_future(hook({"tool_name": "AskUserQuestion",
                                           "tool_input": {"questions": questions}},
                                          "tu-1", None))
        await asyncio.sleep(0)
        mgr.set_answer("tu-1", answer, answered_by=answered_by)
        return await task
    return asyncio.run(_go())


def test_ask_gate_records_only_a_person(run, monkeypatch):
    payload = _ask(run)["ask_user_question"]
    answer = _card_answer(payload, "a — files")
    _run_gate(payload["questions"], answer, "auto", monkeypatch)
    assert _state(run)["status"] == "awaiting_human", "a channel auto-pick is not a human"
    result = _run_gate(payload["questions"], answer, "person", monkeypatch)
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert _state(run)["status"] == "active"


def test_the_agent_has_no_answer_command():
    import scripts.pipeline_v1 as cli
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["decision-answer", "--project", "P", "--run-id", "run_x",
                                 "--request-id", "q1", "--option-id", "a"])


def test_agent_bash_call_to_the_decision_endpoint_is_denied():
    command = ("curl -X POST http://127.0.0.1:18321/api/pipelines/run_human1/decision "
               "-d '{\"request_id\":\"q1\",\"option_id\":\"a\"}'")
    result = gate._decide(OWNER, command, {"command": command})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "answered by a person" in result["hookSpecificOutput"]["permissionDecisionReason"]


# ── stop and resume ─────────────────────────────────────────────────────────

def test_stop_pauses_active_and_resume_reactivates(run):
    assert pipeline_human.on_user_stop(OWNER) == ["run_human1"]
    assert _state(run)["status"] == "paused" and _state(run)["reason"] == "user_stop"
    assert pipeline_human.on_user_stop(OWNER) == [], "a paused run is not paused twice"
    assert run.resume()["state"]["status"] == "active"
    assert pipeline_human.on_session_lost(OWNER) == ["run_human1"]
    assert _state(run)["reason"] == "session_lost"


def test_stop_leaves_a_waiting_run_waiting(run):
    _ask(run)
    assert pipeline_human.on_user_stop(OWNER) == []
    assert pipeline_human.on_session_lost(OWNER) == []
    assert _state(run)["status"] == "awaiting_human"
    assert run.pending_decision()["request_id"] == "q1"


def test_chat_stop_endpoint_pauses_the_owned_run(run):
    from main import app

    class _Router:
        async def interrupt_session(self, _sid):
            return {"success": True, "message": "stopped"}
    with patch("routers.chat._get_router", return_value=_Router()):
        response = TestClient(app).post(f"/api/chat/stop/{OWNER}")
    assert response.status_code == 200 and response.json()["status"] == "stopped"
    assert _state(run)["status"] == "paused"


# ── ownership ───────────────────────────────────────────────────────────────

def test_owner_transfer_moves_projection_and_authorizations(run):
    assert run.transfer_owner(OTHER, reason="takeover") is True
    assert run.owner_session() == OTHER and _state(run)["session_id"] == OTHER
    assert run.transfer_owner(OTHER, reason="takeover") is False
    # the next owner-scoped write still projects the new owner, not the creator
    run.write_projection()
    assert _state(run)["session_id"] == OTHER
    assert pipeline_human.on_user_stop(OWNER) == [], "the old owner no longer drives it"
    assert pipeline_human.on_user_stop(OTHER) == ["run_human1"]


def test_gate_takeover_signs_owner_transfer(run, monkeypatch):
    from core import security_hooks
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root",
                        lambda: str(run.workspace))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: False)
    from core import hook_token          # the token issuer (B-class B-b): a stand-in
    monkeypatch.setattr(hook_token, "issue", lambda *_a, **_k: "40001." + "c" * 64)
    command = ("python backend/scripts/pipeline_v1.py resume --project P "
               "--run-id run_human1")
    result = gate._decide(OTHER, command, {"command": command})
    assert (result or {}).get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    kinds = [e["kind"] for e in run.store.read_events()]
    assert "owner_transfer" in kinds and run.owner_session() == OTHER


# ── dashboard ───────────────────────────────────────────────────────────────

@pytest.fixture()
def client(run):
    from main import app
    with patch("routers.pipelines._get_swarmws", return_value=run.workspace):
        yield TestClient(app)


def test_dashboard_shows_the_question_and_answers_it(run, client):
    _ask(run)
    detail = client.get("/api/pipelines/run_human1").json()
    assert detail["pause_kind"] == "decision" and detail["owner_session_id"] == OWNER
    assert detail["decision"]["question"] == "Which storage?"
    assert [o["id"] for o in detail["decision"]["options"]] == ["a", "b"]
    assert client.patch("/api/pipelines/run_human1/resume").status_code == 409
    answered = client.post("/api/pipelines/run_human1/decision",
                           json={"request_id": "q1", "option_id": "b"})
    assert answered.status_code == 200 and answered.json()["owner_session_id"] == OWNER
    assert _state(run)["status"] == "active"
    again = client.post("/api/pipelines/run_human1/decision",
                        json={"request_id": "q1", "option_id": "a"})
    assert again.status_code == 409


def test_dashboard_stopped_and_idle(run, client, monkeypatch):
    monkeypatch.setattr(pipeline_human, "owner_live", lambda _sid: False)
    assert client.get("/api/pipelines/run_human1").json()["pause_kind"] == "idle"
    monkeypatch.setattr(pipeline_human, "owner_live", lambda _sid: True)
    assert client.get("/api/pipelines/run_human1").json()["pause_kind"] is None
    pipeline_human.on_user_stop(OWNER)
    assert client.get("/api/pipelines/run_human1").json()["pause_kind"] == "stopped"
    resumed = client.patch("/api/pipelines/run_human1/resume").json()
    assert resumed["status"] == "running" and resumed["owner_session_id"] == OWNER
