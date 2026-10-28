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
