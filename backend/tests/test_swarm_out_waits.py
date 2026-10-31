"""A Swarm Out wait's phase on the Host, read from the bridge's own files.

Design: docs/design/2026-10-31-swarm-out-single-loop-fixes.md §2.1. What must hold:

- Each wait (question, approval, decision) has one phase, from the board (``ask/``
  not closed / listed in ``decisions.json``), ``answer/``, ``closed/`` and the close's
  ``why``: waiting (listed, no answer), sent (listed, answered, no close), taken
  (``why: delivered``), refused (``why: answer_refused``, with its reason code), gone
  (answered, then closed otherwise or no longer listed).
- Only ``why: delivered`` is shown as taken: a wait that vanished, or one closed for
  another reason, never reads as received (P1).
- What the person chose comes from the Host's own answer file.
- ``asks`` keeps what needs the person (waiting, refused) and ``answered`` the rest; a
  run's state follows the design's order.
- An unreadable close or answer is never shown as taken (F3).
"""
from __future__ import annotations

import json

import pytest

from core import swarm_out
from tests.test_swarm_out_binding import B, ListingS3, NOW, _ask

SID = "s1"


def _question(rid="r1"):
    return json.dumps({"kind": "question", "session_id": SID, "request_id": rid, "asked_at": NOW,
                       "questions": [{"question": "Which db?", "options": [{"label": "sqlite"}]}]}).encode()


def _decision(rid="dc-abc"):
    return {"kind": "decision", "hive": "worker-1", "session_id": SID, "request_id": rid,
            "asked_at": NOW, "run_id": "run_x", "question": "Which storage?", "why": "both fit",
            "options": [{"id": "a", "choice": "files"}, {"id": "b", "choice": "sqlite"}],
            "truncated": False, "shown_sha256": "0" * 64}


def _read(objects, decisions=None):
    answered: list[dict] = []
    asks = swarm_out._open_asks(ListingS3(objects), "bucket", "worker-1", answered, decisions)
    return asks, answered


def _close(why=None, reason=None):
    body = {"request_id": "r1", "closed_at": 1.0}
    if why:
        body["why"] = why
    if reason:
        body["reason"] = reason
    return json.dumps(body).encode()


ANSWER_Q = json.dumps({"request_id": "r1", "answers": {"Which db?": "sqlite"},
                       "answered_at": "2026-10-31T08:21:51Z"}).encode()


@pytest.fixture(autouse=True)
def _no_cache():
    swarm_out._BODIES.clear()
    yield
    swarm_out._BODIES.clear()


def test_a_question_with_no_answer_is_waiting():
    asks, answered = _read({B + "ask/s1/r1.json": _question()})
    assert [(a["request_id"], a["phase"]) for a in asks] == [("r1", "waiting")] and answered == []


def test_an_answered_question_the_hive_has_not_closed_is_sent_with_what_was_chosen():
    asks, answered = _read({B + "ask/s1/r1.json": _question(), B + "answer/s1/r1.json": ANSWER_Q})
    assert asks == []
    (a,) = answered
    assert (a["phase"], a["chosen"], a["question_text"], a["answered_at"]) == \
        ("sent", "sqlite", "Which db?", "2026-10-31T08:21:51Z")


def test_only_a_delivered_close_is_taken():
    base = {B + "ask/s1/r1.json": _question(), B + "answer/s1/r1.json": ANSWER_Q}
    _, answered = _read({**base, B + "closed/s1/r1.json": _close("delivered")})
    assert [a["phase"] for a in answered] == ["taken"]
    for close in (_close(), _close("restart"), b"{torn"):
        swarm_out._BODIES.clear()
        _, answered = _read({**base, B + "closed/s1/r1.json": close})
        assert [a["phase"] for a in answered] == ["gone"], close


def test_a_refused_answer_needs_the_person_with_its_reason_and_choice():
    asks, answered = _read({B + "ask/s1/r1.json": _ask("s1", "r1"),
                            B + "answer/s1/r1.json": json.dumps({"request_id": "r1", "decision": "approve"}).encode(),
                            B + "closed/s1/r1.json": _close("answer_refused", "changed")})
    (a,) = asks
    assert (a["phase"], a["refused"], a["refused_reason"], a["chosen"]) == ("refused", True, "changed", "approve")
    assert answered == []


def test_an_unknown_reason_code_is_other():
    asks, _ = _read({B + "ask/s1/r1.json": _ask("s1", "r1"), B + "answer/s1/r1.json": b"{}",
                     B + "closed/s1/r1.json": _close("answer_refused", "<b>rm -rf</b>")})
    assert asks[0]["refused_reason"] == "other"


def test_a_decision_moves_from_waiting_to_sent_to_taken_and_gone():
    rel = f"{SID}/dc-abc.json"
    answer = json.dumps({"request_id": "dc-abc", "option_id": "a", "shown_label": "a — files",
                         "shown_question": "Which storage?"}).encode()
    listed = [_decision()]
    asks, answered = _read({}, listed)
    assert listed[0]["phase"] == "waiting" and answered == []
    listed = [_decision()]
    _, answered = _read({B + "answer/" + rel: answer}, listed)
    assert listed == [] and [(a["phase"], a["chosen"]) for a in answered] == [("sent", "a — files")]
    listed = [_decision()]
    _, answered = _read({B + "answer/" + rel: answer, B + "closed/" + rel: _close("delivered")}, listed)
    assert [a["phase"] for a in answered] == ["taken"]
    # no longer listed: its card text comes from the Host's own answer file
    _, answered = _read({B + "answer/" + rel: answer, B + "closed/" + rel: _close("delivered")}, [])
    assert [(a["phase"], a["question_text"], a["chosen"]) for a in answered] == [("taken", "Which storage?", "a — files")]
    swarm_out._BODIES.clear()
    _, answered = _read({B + "answer/" + rel: answer, B + f"closed/{SID}/dc-old.json": _close()}, [])
    assert [a["phase"] for a in answered] == ["gone"]                 # vanished: never taken


def test_a_refused_decision_still_listed_needs_the_person():
    rel = f"{SID}/dc-abc.json"
    listed = [_decision()]
    asks, answered = _read({B + "answer/" + rel: json.dumps({"option_id": "b"}).encode(),
                            B + "closed/" + rel: _close("answer_refused", "run_refused")}, listed)
    assert listed == [] and answered == []
    assert [(a["phase"], a["refused_reason"], a["chosen"]) for a in asks] == [("refused", "run_refused", "b")]


def test_an_answer_older_than_the_longest_wait_is_not_listed(monkeypatch):
    monkeypatch.setattr(swarm_out, "_age_ok", lambda lm, now: False)
    asks, answered = _read({B + "ask/s1/r1.json": _question(), B + "answer/s1/r1.json": ANSWER_Q,
                            B + "closed/s1/r1.json": _close("delivered")})
    assert asks == [] and answered == []


# ── a run's state ───────────────────────────────────────────────────────────────

ROW = {"session_id": SID}
IDLE = {SID: {"streaming": False, "waiting_input": False, "activity": "idle"}}
WAITING = {SID: {"streaming": False, "waiting_input": True, "activity": "waiting_input"}}


def test_the_state_order_of_a_runs_waits():
    st = swarm_out.run_state
    assert st(ROW, "running", IDLE, True, True, True, True) == ("waiting", "decision")
    assert st(ROW, "running", IDLE, False, False, True, True) == ("waiting", "answer_refused")
    assert st(ROW, "running", WAITING, False, False, False, True) == ("running", "answer_sent")
    # an answer sent while another question of the session waits: the session says
    assert st(ROW, "running", WAITING, True, False, False, True) == ("waiting", "waiting_input")
    assert st(ROW, "running", IDLE, False, False, False, False) == ("stopped", "idle")
    assert st(ROW, "gone", IDLE, False, False, True, True) == ("stopped", "hive_gone")


def test_list_runs_splits_what_needs_the_person_from_what_was_answered(monkeypatch):
    import asyncio
    from tests.test_swarm_out_binding import INST
    rows = [{"dispatch_id": "d1", "hive_id": "h1", "hive": "worker-1", "session_id": SID, "title": "T"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})

    async def no_states(inst):
        return None

    async def statuses():
        return {"h1": "running"}
    monkeypatch.setattr(swarm_out, "_hive_statuses", statuses)
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_session_states", no_states)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, *_a: ListingS3({
        B + "ask/s1/r1.json": _question(), B + "answer/s1/r1.json": ANSWER_Q}))
    data = asyncio.run(swarm_out.list_runs())
    assert data["asks"] == [], data["errors"]
    assert data["answered"], data["errors"]
    assert [a["phase"] for a in data["answered"]] == ["sent"]
    run = data["runs"][0]
    assert (run["state"], run["state_reason"], run["waiting"]) == ("running", "answer_sent", False)
    assert data["answered"][0]["dispatch_title"] == "T"


def test_a_decision_list_not_read_never_concludes_an_answer_gone():
    rel = f"{SID}/dc-abc.json"
    asks, answered = _read({B + "answer/" + rel: json.dumps({"option_id": "a", "shown_label": "a — files"}).encode()}, None)
    assert asks == [] and answered == []          # unknown this pass: not shown, never "not used"


def test_an_older_hives_snapshot_refusal_still_reads_as_refused():
    rel = f"{SID}/dc-abc.json"
    listed = [dict(_decision(), snapshot_refused=True)]
    asks, answered = _read({B + "answer/" + rel: json.dumps({"option_id": "b"}).encode()}, listed)
    assert [(a["phase"], a["refused_reason"]) for a in asks] == [("refused", "other")] and answered == []
    assert "snapshot_refused" not in asks[0]


def test_an_older_hive_that_never_closes_decisions_does_not_show_a_taken_one_as_not_used():
    rel = f"{SID}/dc-abc.json"
    answer = json.dumps({"option_id": "a", "shown_label": "a — files"}).encode()
    _, answered = _read({B + "answer/" + rel: answer}, [])
    assert answered == []                          # it may have been taken: not "not used"
    _, answered = _read({B + "answer/" + rel: answer, B + f"closed/{SID}/dc-other.json": _close()}, [])
    assert [a["phase"] for a in answered] == ["gone"]   # this Hive closes decisions: unclosed and gone
