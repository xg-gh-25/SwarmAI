"""A Hive's wait asked in the chat that dispatched the run; the person's pick forwarded.

Design docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md §2.5 (V6): only the
person's answer, on the card the Host built, in the session that dispatched the run, is
forwarded, and only to an option of the wait.
"""
from __future__ import annotations

import asyncio
import copy

import pytest

from core import swarm_out, swarm_out_follow, swarm_out_wait_card as wc
from core.ask_question_manager import AskQuestionManager

DID, SESSION = "so_aaaaaaaaaaaa", "sess-a"
ROW = {"dispatch_id": DID, "title": "fix the tests", "hive": "demo", "hive_id": "hv1",
       "session_id": "hs-1", "dispatched_from": SESSION, "state": "waiting"}
DECISION = {"kind": "decision", "session_id": "hs-1", "request_id": "dc-1", "phase": "waiting",
            "question": "Where should the fix go?", "why": "both are valid", "truncated": False,
            "shown_sha256": "h" * 64,
            "options": [{"id": "a", "choice": "the fixture", "consequence": "one file", "risk": "low",
                         "reversible": True},
                        {"id": "b", "choice": "conftest", "consequence": "every test", "risk": "high",
                         "reversible": False}]}
APPROVAL = {"kind": "approval", "session_id": "hs-1", "request_id": "ap-1", "phase": "waiting",
            "tool_name": "Bash", "command": "rm -rf build", "reason": "dangerous", "truncated": False,
            "shown_sha256": "s" * 64}
QUESTION = {"kind": "question", "session_id": "hs-1", "request_id": "q-1", "phase": "waiting",
            "questions": [{"question": "How long?", "header": "Length", "multiSelect": False,
                           "options": [{"label": "A", "description": "3 lines"},
                                       {"label": "B", "description": "a paragraph"}]}]}


@pytest.fixture
def hive(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    asks = [DECISION, APPROVAL, QUESTION]
    sent: list = []

    async def run_row(dispatch_id):
        return ROW if dispatch_id == DID else None

    async def list_runs(max_age_s=0):
        return {"runs": [ROW], "asks": asks, "answered": []}

    async def answer(hive_id, session_id, request_id, **kw):
        sent.append((hive_id, session_id, request_id, kw))
        return {"ok": True}
    monkeypatch.setattr(swarm_out, "run_row", run_row)
    monkeypatch.setattr(swarm_out, "list_runs", list_runs)
    monkeypatch.setattr(swarm_out, "answer", answer)
    return sent


def _card(request_id):
    return asyncio.run(wc.card(DID, request_id))["questions"]


def _forward(questions, answers, session=SESSION):
    return asyncio.run(wc.forward(session, questions, answers))


# ── the card ────────────────────────────────────────────────────────────────

def test_a_decision_card_names_the_wait_and_its_options(hive):
    qs = _card("dc-1")
    assert len(qs) == 1 and qs[0]["question"].startswith(f"[Hive · {DID} · dc-1]")
    assert "Where should the fix go?" in qs[0]["question"] and "both are valid" in qs[0]["question"]
    assert [o["label"] for o in qs[0]["options"]] == ["a — the fixture", "b — conftest"]
    assert qs[0]["options"][1]["description"].startswith("[high risk, cannot be undone]")


def test_an_approval_card_shows_the_command(hive):
    qs = _card("ap-1")
    assert "rm -rf build" in qs[0]["question"] and [o["label"] for o in qs[0]["options"]] == ["Approve", "Deny"]


@pytest.mark.parametrize("change,word", [
    (lambda a: a[1].update(truncated=True), "too long"),
    (lambda a: a[0].update(truncated=True), "too long"),
    (lambda a: a[2]["questions"][0]["options"].extend([{"label": str(i)} for i in range(3)]), "does not fit"),
    (lambda a: a[2]["questions"].extend([copy.deepcopy(QUESTION["questions"][0])] * 4), "does not fit"),
])
def test_a_wait_a_chat_card_cannot_show_whole_is_refused(hive, monkeypatch, change, word):
    asks = copy.deepcopy([DECISION, APPROVAL, QUESTION])
    change(asks)

    async def list_runs(max_age_s=0):
        return {"runs": [ROW], "asks": asks, "answered": []}
    monkeypatch.setattr(swarm_out, "list_runs", list_runs)
    for rid in ("dc-1", "ap-1", "q-1"):
        try:
            _card(rid)
        except wc.CardError as e:
            assert word in str(e)
            return
    pytest.fail("no card was refused")


def test_no_card_for_a_wait_that_is_not_open(hive):
    with pytest.raises(wc.CardError, match="not open"):
        _card("nope")


# ── forwarding ──────────────────────────────────────────────────────────────

def test_the_persons_decision_pick_is_forwarded_as_its_option(hive):
    qs = _card("dc-1")
    out = _forward(qs, {qs[0]["question"]: "b — conftest"})
    assert out["status"] == "forwarded"
    assert hive == [("hv1", "hs-1", "dc-1", {"via": "chat", "option_id": "b", "shown_sha256": "h" * 64})]
    assert wc.status(DID, "dc-1")["status"] == "forwarded"


def test_an_approval_is_forwarded_as_approve_or_deny(hive):
    qs = _card("ap-1")
    _forward(qs, {qs[0]["question"]: "Deny"})
    assert hive[-1][3] == {"via": "chat", "decision": "deny", "shown_sha256": "s" * 64}


def test_a_questions_answer_is_forwarded_under_the_hives_own_question(hive):
    qs = _card("q-1")
    _forward(qs, {qs[0]["question"]: "an answer of my own"})     # free text is fine for a question
    assert hive[-1][3] == {"via": "chat", "answers": {"How long?": "an answer of my own"}}


@pytest.mark.parametrize("how", ["other session", "edited", "free text", "two picks"])
def test_what_is_not_the_persons_pick_on_the_built_card_is_not_forwarded(hive, how):
    qs = _card("dc-1")
    session, answers = SESSION, {qs[0]["question"]: "a — the fixture"}
    if how == "other session":
        session = "sess-b"
    elif how == "edited":
        qs = copy.deepcopy(qs)
        qs[0]["options"][0]["description"] = "the agent's own words"
    elif how == "free text":
        answers = {qs[0]["question"]: "do whatever"}
    else:
        answers = {qs[0]["question"]: "a — the fixture", "x": "b — conftest"}
    out = _forward(qs, answers, session)
    assert out["status"] == "not_forwarded" and hive == []
    if how == "other session":
        assert out["reason"] == "the card was answered in a session that does not answer for this run"
    assert wc.status(DID, "dc-1")["status"] == "not_forwarded"


def test_no_card_no_forward_and_a_second_answer_is_not_sent(hive):
    assert _forward([{"question": "plain question", "options": []}], {"plain question": "x"}) is None
    forged = [{"question": f"[Hive · {DID} · dc-1] made up", "options": []}]
    assert _forward(forged, {forged[0]["question"]: "a"})["reason"].startswith("no card was built")
    qs = _card("dc-1")
    _forward(qs, {qs[0]["question"]: "a — the fixture"})
    assert _forward(qs, {qs[0]["question"]: "a — the fixture"})["reason"] == "this wait was already forwarded"
    assert len(hive) == 1


def test_the_hives_refusal_is_kept_and_forwarding_never_raises(hive, monkeypatch):
    qs = _card("dc-1")

    async def refused(*a, **k):
        raise swarm_out.SwarmOutError("the decision changed on the Hive since it was shown; reload")
    monkeypatch.setattr(swarm_out, "answer", refused)
    assert "changed on the Hive" in _forward(qs, {qs[0]["question"]: "a — the fixture"})["reason"]

    async def broken(*a, **k):
        raise RuntimeError("network")
    monkeypatch.setattr(swarm_out, "answer", broken)
    assert _forward(qs, {qs[0]["question"]: "a — the fixture"})["status"] == "not_forwarded"


def test_a_forward_that_happened_is_never_overwritten_as_not_forwarded(hive):
    qs = _card("dc-1")
    _forward(qs, {qs[0]["question"]: "a — the fixture"})
    rec = wc._load(DID, "dc-1")
    rec["forward"] = None                                        # a second answer read it before
    wc._not(rec, "this question was already answered")
    assert wc.status(DID, "dc-1")["status"] == "forwarded"


def test_a_forwarded_wait_gets_no_new_card(hive):
    qs = _card("dc-1")
    _forward(qs, {qs[0]["question"]: "a — the fixture"})
    with pytest.raises(wc.CardError, match="already forwarded"):
        _card("dc-1")
    assert wc.status(DID, "dc-1")["status"] == "forwarded"
    assert wc._LOCKS == {}                                        # the lock map does not grow


def test_a_built_card_is_not_news_but_why_it_was_not_forwarded_is(hive):
    data = {"runs": [ROW], "asks": [DECISION], "answered": []}
    before = swarm_out_follow.fingerprint(ROW, data)
    qs = _card("dc-1")
    assert swarm_out_follow.fingerprint(ROW, data) == before     # only asked
    _forward(qs, {qs[0]["question"]: "free text"})
    first = swarm_out_follow.fingerprint(ROW, data)
    _card("dc-1")
    _forward(qs, {qs[0]["question"]: "a — the fixture"}, session="sess-b")
    assert swarm_out_follow.fingerprint(ROW, data) != first      # not forwarded again, for another reason


def test_a_hive_card_is_never_recorded_as_a_local_pipeline_decision(hive, monkeypatch):
    from core import pipeline_human
    recorded: list = []
    monkeypatch.setattr(pipeline_human, "record_card_answer", lambda *a: recorded.append(a) or [])
    qs = _card("dc-1")
    qs[0]["question"] += " [dc-local]"                           # Hive text ending in a local marker
    _gate(qs, {qs[0]["question"]: "a — the fixture"}, "person", monkeypatch)
    assert recorded == []
    plain = [{"question": "Pick one [dc-local]", "options": [{"label": "a"}, {"label": "b"}]}]
    _gate(plain, {plain[0]["question"]: "a"}, "person", monkeypatch)
    assert len(recorded) == 1


def test_a_card_not_forwarded_is_news_to_the_agent_following(hive):
    data = {"runs": [ROW], "asks": [DECISION], "answered": []}
    before = swarm_out_follow.fingerprint(ROW, data)
    qs = _card("dc-1")
    _forward(qs, {qs[0]["question"]: "free text"})
    after = swarm_out_follow.fingerprint(ROW, data)
    assert before != after
    assert swarm_out_follow.summary(ROW, data)["waits"][0]["card"]["status"] == "not_forwarded"


# ── the daemon's answer path ────────────────────────────────────────────────

def _gate(questions, answer, answered_by, monkeypatch):
    from core import security_hooks
    mgr = AskQuestionManager()
    monkeypatch.setattr("core.permission_manager.permission_manager.publish_prompt", lambda *a, **k: None)
    hook = security_hooks.create_ask_question_gate(SESSION, {"sdk_session_id": SESSION}, mgr)

    async def go():
        task = asyncio.ensure_future(hook({"tool_name": "AskUserQuestion",
                                           "tool_input": {"questions": questions}}, "tu-1", None))
        await asyncio.sleep(0)
        mgr.set_answer("tu-1", answer, answered_by=answered_by)
        return await task
    return asyncio.run(go())


def test_the_ask_gate_forwards_only_a_persons_pick(hive, monkeypatch):
    qs = _card("dc-1")
    pick = {qs[0]["question"]: "a — the fixture"}
    _gate(qs, pick, "auto", monkeypatch)
    assert hive == [], "an unattended channel pick is not the person's"
    out = _gate(qs, pick, "person", monkeypatch)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert out["hookSpecificOutput"]["updatedInput"]["answers"] == pick
    assert [h[2] for h in hive] == ["dc-1"]
