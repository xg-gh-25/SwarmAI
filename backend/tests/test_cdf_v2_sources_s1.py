"""CDF v2 shared extractor and S1 source tests.

The tests inject the LLM and monkeypatch every api_v2 side effect. They assert
source arguments, one attempt per extraction, current evidence, and no truncation.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.ingestion import api_v2
from core.ingestion.sources import extract_llm, s1_user


def _unit(what: str = "Use one authoritative writer for governed cognition.") -> dict:
    return {"kind": "cognition", "type": "guideline", "scope": "world",
            "title": "One authoritative writer", "what": what,
            "how": "Route every autonomous write through CDF.",
            "applicability": "When persistent cognition is written.",
            "evidence": ["event:old"]}


class FakeExtractor:
    def __init__(self, values):
        self.values = list(values)
        self.prompts = []

    def extract(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(value=self.values.pop(0), raw="raw")


def test_extractor_prompt_has_full_contract_and_counterexamples():
    prompt = extract_llm.build_prompt("S2", "data", ("event:e1",),
                                      ("cognition", "event", "reference"))
    assert all(word in prompt for word in (
        "FIRST PRINCIPLE", "operational trivia", "unverified guesses",
        "restatement of injected memory", "task-local state", "event:e1"))
    assert all(name in prompt for name in ("principle", "correction", "decision",
                                           "model", "guideline", "pitfall", "process"))


def test_invalid_unit_is_recorded_not_fixed():
    bad = _unit(); bad.pop("what")
    result = extract_llm.extract_units("S2", "data", ("event:e1",),
        allowed_kinds=("cognition",), llm=FakeExtractor([[bad]]))
    assert result.units == [] and result.invalid_units
    assert result.invalid_units[0]["raw"] == bad


def test_oversize_gets_one_split_request(monkeypatch):
    monkeypatch.setattr(extract_llm, "MAX_SEMANTIC_BYTES", 64)
    split = _unit("complete")
    llm = FakeExtractor([[_unit("x" * 65)], [split]])
    result = extract_llm.extract_units("S2", "data", ("event:e1",),
        allowed_kinds=("cognition",), llm=llm)
    assert result.splits == 1 and len(llm.prompts) == 2
    assert "Split it into" in llm.prompts[1]


def test_s1_enqueues_current_evidence_and_records_one_attempt(monkeypatch):
    enqueued, attempts = [], []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        enqueued.append((a, k)) or SimpleNamespace(status="queued", detail="echo"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    outcomes = s1_user.submit_units([_unit()], session_id="session-1",
                                    evidence="input:latest", turn_kind="user")
    assert len(outcomes) == 1 and len(attempts) == 1
    args, kwargs = enqueued[0]
    assert args[0] == "S1" and args[2].evidence == ["event:input:latest"]
    assert kwargs["session_id"] == "session-1"
    assert kwargs["extraction_pass"] == "s1" and kwargs["read_root_ids"] == ()


def test_s1_caps_units_and_refuses_missing_evidence(monkeypatch):
    enqueued, attempts = [], []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        enqueued.append((a, k)) or SimpleNamespace(status="queued"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    s1_user.submit_units([_unit(str(i)) for i in range(4)],
                         session_id="session-1", evidence="event:e1", turn_kind="user")
    assert len(enqueued) == 3
    assert attempts[0][1]["invalid_units"][-1]["reason"] == "s1_unit_cap"
    with pytest.raises(ValueError, match="evidence"):
        s1_user.submit_units([_unit()], session_id="session-1", evidence="",
                             turn_kind="user")
    with pytest.raises(ValueError, match="user-triggered"):
        s1_user.submit_units([_unit()], session_id="session-1", evidence="event:e1",
                             turn_kind="cron")


def test_session_env_exports_latest_user_event(monkeypatch):
    from core.prompt_builder import with_app_session_id
    monkeypatch.setattr("core.session_inputs.latest_user_event_id", lambda sid: "input:new")
    assert with_app_session_id({}, {"sdk_session_id": "session-1"}) == {
        "SWARM_APP_SESSION_ID": "session-1", "SWARM_LAST_USER_EVENT_ID": "input:new"}


# ── a refused unit says why (the CLI used to exit 1 and print nothing) ────────

def _quiet_api(monkeypatch):
    enqueued = []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        enqueued.append(a[2]) or SimpleNamespace(status="queued", candidate_id="cand-1", detail=""))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k: "a")
    return enqueued


def test_every_given_unit_gets_an_answer_in_order_with_the_reason(monkeypatch):
    enqueued = _quiet_api(monkeypatch)
    extra = {**_unit(), "project": "SwarmAI"}                      # no such field
    bad_scope = {**_unit(), "scope": "project"}                    # needs project:<name>
    out = s1_user.submit_units([extra, _unit(), bad_scope], session_id="session-1",
                               evidence="event:e1", turn_kind="user")
    assert [r.status for r in out] == ["rejected", "queued", "rejected"]
    assert len(enqueued) == 1
    assert "project: not a field" in out[0].detail
    assert "scope" in out[2].detail and "project:<project name>" in out[2].detail


def test_a_unit_over_the_cap_is_answered_not_dropped(monkeypatch):
    _quiet_api(monkeypatch)
    out = s1_user.submit_units([_unit(str(i)) for i in range(4)], session_id="session-1",
                               evidence="event:e1", turn_kind="user")
    assert [r.status for r in out] == ["queued"] * 3 + ["rejected"]
    assert out[3].detail.startswith("s1_unit_cap")


def test_the_cli_prints_the_reason_and_exits_2(monkeypatch, capsys):
    import json
    import desktop_main
    _quiet_api(monkeypatch)
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "session-1")
    monkeypatch.setenv("SWARM_LAST_USER_EVENT_ID", "input:e1")
    payload = [{**_unit(), "project": "SwarmAI"}, _unit()]
    rc = desktop_main.run_ingest(["submit", "--source", "S1", "--evidence", "input:e1",
                                  "--payload-json", json.dumps(payload)])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert rc == 2
    assert lines[0] == {"status": "rejected", "unit": 1, "title": "One authoritative writer",
                        "detail": lines[0]["detail"]} and "project" in lines[0]["detail"]
    assert lines[1]["status"] == "queued"


def test_with_wait_a_refused_unit_still_exits_2(monkeypatch, capsys, tmp_path):
    import json
    import desktop_main
    _quiet_api(monkeypatch)
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))            # no store: the wait runs out
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "session-1")
    monkeypatch.setenv("SWARM_LAST_USER_EVENT_ID", "input:e1")
    payload = [{**_unit(), "scope": "nowhere"}, _unit()]
    rc = desktop_main.run_ingest(["submit", "--source", "S1", "--evidence", "input:e1", "--wait", "0.4",
                                  "--payload-json", json.dumps(payload)])
    assert rc == 2 and '"status": "rejected"' in capsys.readouterr().out
