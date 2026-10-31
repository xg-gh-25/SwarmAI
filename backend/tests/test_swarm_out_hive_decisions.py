"""A Hive pipeline run's decision reaches the Host and is answered from there.

Design: docs/design/2026-10-31-swarm-out-hive-decisions.md. What must hold:

- Hive: the decisions the runs wait on are one snapshot (``decisions.json``), the whole
  current list, rewritten when it changes. A decision is listed while its run waits
  (no card, a restart) and is gone once the run moves on. A pass that cannot read every
  run changes nothing. The run's own question card is not mirrored as a second question.
- An answer names one of the options and what the card showed (``shown_sha256``). With
  the card open it goes on the card with the option's exact label; with no card it is
  recorded on the run and the session gets a continue message in fixed words, taken
  once the router accepts it. A wrong option, another card's digest, a truncated
  decision or a refusal by the run is refused and records nothing; a passing failure is
  tried again. Each answered decision is closed once in ``closed/``: ``why: delivered``
  when the run took it, ``why: answer_refused`` with a reason code when not; a refused
  one the run stops waiting on is closed again plainly; a restart never writes over a
  delivered close.
- Host: it shows exactly the snapshot, a listed decision makes its run ``waiting``
  whatever the session reports, and an answer takes one of its option ids with the
  digest of what was shown.
- End to end on a real v1 run: both delivery paths land a ``human_answer``.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import pipeline_human
from core import swarm_out
from core.permission_manager import PermissionManager
from core.run_protocol import decision as decision_protocol
from core.swarm_out_bridge import Bridge
from core.swarm_out_decisions import (MAX_CONTINUE_TRIES, MAX_RECORD_TRIES, decision_digest,
                                      decision_key)

from tests.test_swarm_out_bridge import FakeRouter, FakeStore, Unit, _answer, _settle  # noqa: F401
from tests.test_swarm_out_bridge import live_questions  # noqa: F401 — autouse fixture
from tests.test_pipeline_v1_human_loop import OWNER, _decision, _git_repo

ACCEPTED = {"type": "accepted", "sessionId": "x"}


class Router(FakeRouter):
    """FakeRouter that also takes a new turn (the no-card continue message)."""

    def __init__(self, reply=None):
        super().__init__()
        self.reply = [ACCEPTED, {"type": "assistant"}] if reply is None else reply

    def run_conversation(self, agent_id, user_message=None, session_id=None, client_id=None, **_kw):
        self.calls.append(("turn", session_id, user_message, agent_id, client_id))

        async def gen():
            for ev in self.reply:
                yield ev
            self.read_to_end.append(("turn", session_id))
        return gen()


SID = "sess-1"
RUN = "run_dec1"
SNAP = "bridge/worker-1/decisions.json"


def _pending(request_id="q1", question="Which storage?"):
    value = _decision(request_id)
    value["question"] = question
    return {"request_id": value["request_id"], "kind": value["kind"], "question": question,
            "why_evidence_insufficient": value["why_evidence_insufficient"],
            "options": [{k: o[k] for k in ("id", "choice", "consequence", "reversible", "risk")}
                        for o in value["options"]],
            "ask_user_question": decision_protocol.ask_payload(value)}


def _item(pending=None, sid=SID, run_id=RUN, generation=0):
    return {"run_id": run_id, "project": "P", "session_id": sid, "owner_generation": generation,
            "asked_at": "2026-10-31T01:02:03.456789+00:00", "decision": pending or _pending()}


def _key(pending=None, sid=SID, run_id=RUN, generation=0):
    return decision_key(run_id, sid, generation, pending or _pending())


class Env:
    def __init__(self, tmp_path, decisions=None, router=None):
        self.pm = PermissionManager(persist_path=tmp_path / "pending.json")
        self.router = router or Router()
        self.store = FakeStore()
        self.decisions = list(decisions or [])
        self.fail = False
        self.recorded = []
        self.record_error = None      # an exception the run raises on record

        def provider():
            if self.fail:
                raise OSError("a run could not be read")
            return list(self.decisions)

        def record(run_id, request_id, option_id):
            if self.record_error is not None:
                raise self.record_error
            self.recorded.append((run_id, request_id, option_id))
        self.provider, self.record = provider, record
        self.bridge = self.new_bridge()

    def new_bridge(self):
        """A bridge as after a restart: same store and board, nothing in memory."""
        return Bridge("worker-1", self.store, pm=self.pm, router=self.router,
                      decisions=self.provider, record_decision=self.record)

    def card(self, pending, tid="toolu_card", sid=SID):
        """The run's own question card for *pending*, open on the board."""
        questions = pending["ask_user_question"]["questions"]
        self.pm._prompts.setdefault(sid, {})[tid] = {
            "kind": "ask_user_question", "sessionId": sid, "tool_use_id": tid, "questions": questions}
        self.router.units.append(Unit(sid, tid))
        return questions[0]["question"]

    def snapshot(self):
        raw = self.store.objects.get(SNAP)
        return json.loads(raw)["decisions"] if raw else None

    def entry(self, key):
        return next((e for e in self.snapshot() or [] if e["request_id"] == key), None)

    def shown(self, key):
        """What the Host's card shows for *key* (core.swarm_out's own cleaning)."""
        return swarm_out._clean_decision(self.entry(key), "worker-1")

    def answer(self, key, option, sid=SID, **over):
        body = {"request_id": key, "option_id": option, "shown_sha256": self.shown(key)["shown_sha256"],
                **over}
        _answer(self.store, sid, key, body)

    def asks(self):
        return [json.loads(v) for k, v in self.store.objects.items() if "/ask/" in k]

    def closed(self, key, sid=SID):
        """The close the Hive wrote for *key*, without its time, or None."""
        raw = self.store.objects.get(f"bridge/worker-1/closed/{sid}/{key}.json")
        if raw is None:
            return None
        body = json.loads(raw)
        assert body.pop("request_id") == key and isinstance(body.pop("closed_at"), float)
        return body


def _run(coro):
    asyncio.run(coro)


async def _pass(env, bridge=None, n=1):
    for _ in range(n):
        await (bridge or env.bridge).tick()
        await _settle()


# ── Hive: the snapshot ───────────────────────────────────────────────────────────────

def test_a_waiting_decision_is_listed_while_its_run_waits_and_gone_after(tmp_path):
    env = Env(tmp_path, [_item()])
    key = _key()

    async def go():
        await _pass(env)
        e = env.entry(key)
        assert e["kind"] == "decision" and e["run_id"] == RUN and e["session_id"] == SID
        assert e["decision_request_id"] == "q1" and e["question"] == "Which storage?"
        assert e["truncated"] is False and "refused" not in e
        assert [o["id"] for o in e["options"]] == ["a", "b"]
        assert e["options"][1]["reversible"] is False and e["options"][1]["risk"] == "high"
        assert e["asked_at"] == "2026-10-31T01:02:03Z"         # when the run asked
        env.decisions.clear()                                   # the run moved on
        await _pass(env)
        assert env.snapshot() == []
    _run(go())
    assert env.asks() == []                                     # no ask/closed lifecycle


def test_the_snapshot_is_written_only_when_it_changes(tmp_path):
    env = Env(tmp_path, [_item()])
    puts = []
    real = env.store.put
    env.store.put = lambda k, b: (puts.append(k), real(k, b))
    _run(_pass(env, n=3))
    assert puts.count(SNAP) == 1


def test_a_pass_that_cannot_read_every_run_changes_nothing(tmp_path):
    pending = _pending()
    env = Env(tmp_path, [_item(pending)])
    env.card(pending)
    key = _key()

    async def go():
        await _pass(env)
        env.fail = True                    # one run unreadable: the pass reads nothing
        env.decisions.clear()
        await _pass(env, n=2)
        assert env.entry(key) is not None  # not taken for a run that moved on
        assert env.asks() == []            # its card stays hidden
    _run(go())


def test_pending_decisions_is_all_or_nothing(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(pipeline_human, "_workspace", lambda: tmp_path / "ws")
    rows = [SimpleNamespace(path=tmp_path / name / "run.json",
                            data={"status": "awaiting_human", "decision_cap": 3})
            for name in ("run_bad", "run_good")]
    monkeypatch.setattr("core.run_index.runs", lambda _ws: rows)

    def load(run_id, workspace=None):
        if run_id == "run_bad":
            raise ValueError("torn event log")
        return None
    monkeypatch.setattr(pipeline_human, "load_engine", load)
    with pytest.raises(ValueError):
        pipeline_human.pending_decisions()


def test_the_runs_own_card_is_not_mirrored_as_a_second_question(tmp_path):
    pending = _pending()
    env = Env(tmp_path, [_item(pending)])
    env.card(pending)
    _run(_pass(env))
    assert env.asks() == [] and env.entry(_key()) is not None


def test_a_card_of_another_session_is_still_mirrored(tmp_path):
    pending = _pending()
    env = Env(tmp_path, [_item(pending)])
    env.card(pending, sid="sess-9")
    _run(_pass(env))
    assert [a["kind"] for a in env.asks()] == ["question"]


def test_a_decision_too_long_to_show_whole_is_listed_as_truncated_and_not_answered(tmp_path):
    pending = _pending(question="Which storage? " + "x" * 5000)
    env = Env(tmp_path, [_item(pending)])
    env.card(pending)
    key = _key(pending)

    async def go():
        await _pass(env)
        assert env.entry(key)["truncated"] is True and env.shown(key)["truncated"] is True
        env.answer(key, "b")                       # written past the Host's own refusal
        await _pass(env, n=2)
    _run(go())
    assert env.router.calls == [] and env.recorded == []
    assert env.closed(key) == {"why": "answer_refused", "reason": "too_long"}


def test_a_decision_with_an_overlong_option_id_is_not_listed(tmp_path):
    pending = _pending()
    pending["options"][0]["id"] = "a" * 201
    env = Env(tmp_path, [_item(pending)])
    _run(_pass(env))
    assert env.snapshot() == []


def test_each_takeover_makes_a_new_key_even_back_to_the_first_owner():
    assert _key() == _key(dict(_pending()))
    assert len({_key(), _key(sid="sess-2", generation=1), _key(generation=2),
                _key(_pending("q2")), _key(run_id="run_other")}) == 5


def test_after_a_takeover_the_answer_goes_to_the_new_owner(tmp_path):
    env = Env(tmp_path, [_item()])

    async def go():
        await _pass(env)
        env.decisions = [_item(sid="sess-2", generation=1)]
        await _pass(env)
        new = _key(sid="sess-2", generation=1)
        assert [e["request_id"] for e in env.snapshot()] == [new]
        env.answer(new, "a", sid="sess-2")
        await _pass(env)
    _run(go())
    assert env.recorded == [(RUN, "q1", "a")]
    assert [c[:2] for c in env.router.calls] == [("turn", "sess-2")]


# ── Hive: answering ──────────────────────────────────────────────────────────────────

def test_an_answer_with_the_card_open_goes_on_the_card_with_the_exact_label(tmp_path):
    pending = _pending()
    env = Env(tmp_path, [_item(pending)])
    asked = env.card(pending)
    key = _key(pending)

    async def go():
        await _pass(env)
        env.answer(key, "b")
        await _pass(env)
    _run(go())
    assert env.router.calls == [("answer", SID, {asked: "b — sqlite"}, "toolu_card", "person")]
    assert env.recorded == []                      # the ask gate records it, not the bridge
    assert decision_protocol.option_from_answer(_decision(), env.router.calls[0][2]) == "b"


def test_a_card_answer_the_run_did_not_take_is_recorded_directly(tmp_path):
    pending = _pending()
    env = Env(tmp_path, [_item(pending)])
    env.card(pending)
    key = _key(pending)

    async def go():
        await _pass(env)
        env.answer(key, "b")
        await _pass(env)                           # delivered on the card
        await _pass(env)                           # the run still waits: one pass
        assert env.recorded == []
        await _pass(env)                           # a second: recorded here
        assert env.asks() == []                    # the card stayed hidden meanwhile
    _run(go())
    assert env.recorded == [(RUN, "q1", "b")]
    assert [c[0] for c in env.router.calls] == ["answer", "turn"]


def test_an_answer_with_no_card_is_recorded_and_the_session_is_continued(tmp_path):
    env = Env(tmp_path, [_item()])
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "a")
        await _pass(env)
    _run(go())
    assert env.recorded == [(RUN, "q1", "a")]
    (kind, sid, message, agent, client_id), = env.router.calls
    assert (kind, sid, agent, client_id) == ("turn", SID, "default", f"swarm-out-{key}")
    assert f"Continue pipeline run {RUN}" in message and 'option id "a"' in message
    # fixed words: it does not speak as the person, and carries no run-written text
    assert "I answered" not in message and "files" not in message and "Which storage" not in message
    assert env.closed(key) == {"why": "delivered"}     # recorded on the run: taken


def test_a_card_answer_is_closed_delivered_once_the_run_stops_waiting(tmp_path):
    pending = _pending()
    env = Env(tmp_path, [_item(pending)])
    env.card(pending)
    key = _key(pending)

    async def go():
        await _pass(env)
        env.answer(key, "b")
        await _pass(env)                           # handed to the card
        assert env.closed(key) is None             # the run has not taken it yet
        env.decisions = []                         # the ask gate recorded it
        await _pass(env)
    _run(go())
    assert env.closed(key) == {"why": "delivered"}


def test_a_refused_decision_the_run_stops_waiting_on_is_closed_plainly(tmp_path):
    env = Env(tmp_path, [_item()])
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "c")                       # no such option
        await _pass(env)
        assert env.closed(key) == {"why": "answer_refused", "reason": "no_option"}
        env.decisions = []                         # answered on the Hive's own page
        await _pass(env)
    _run(go())
    assert env.closed(key) == {}


def test_a_card_answer_whose_turn_failed_is_delivered_once_the_run_took_it(tmp_path):
    pending = _pending()
    env = Env(tmp_path, [_item(pending)])
    env.card(pending)
    key = _key(pending)
    def failing(session_id, answer, tool_use_id=None, answered_by="person"):
        async def gen():
            yield {"type": "error", "code": "MODEL_ERROR"}
        return gen()
    env.router.continue_with_answer = failing

    async def go():
        await _pass(env)
        env.answer(key, "b")
        await _pass(env)                           # handed to the card; its turn errors
        env.decisions = []                         # the ask gate recorded it all the same
        await _pass(env)
    _run(go())
    assert env.closed(key) == {"why": "delivered"}


def test_a_close_that_failed_to_write_is_written_on_a_later_pass(tmp_path):
    env = Env(tmp_path, [_item()])
    key = _key()
    real_put = env.store.put
    failures = []

    def flaky(k, body):
        if "/closed/" in k and not failures:
            failures.append(k)
            raise OSError("s3 down")
        real_put(k, body)
    env.store.put = flaky

    async def go():
        await _pass(env)
        env.answer(key, "a")
        await _pass(env)                           # recorded; its close fails
        assert env.closed(key) is None and failures
        await _pass(env)
    _run(go())
    assert env.closed(key) == {"why": "delivered"}


def test_a_restart_never_writes_over_a_delivered_close(tmp_path):
    env = Env(tmp_path, [_item()])
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "a")
        await _pass(env)
        assert env.closed(key) == {"why": "delivered"}
        restarted = env.new_bridge()               # the run still lists it a moment longer
        env.record_error = __import__("core.run_protocol.engine", fromlist=["EngineError"]).EngineError("taken")
        await _pass(env, restarted)
    _run(go())
    assert env.closed(key) == {"why": "delivered"}


def test_a_rewritten_card_cannot_swap_what_an_option_stands_for(tmp_path):
    env = Env(tmp_path, [_item()])
    key = _key()

    async def go():
        await _pass(env)
        forged = dict(env.entry(key))
        a, b = (dict(o) for o in forged["options"])
        a["choice"], b["choice"] = b["choice"], a["choice"]   # "a" now reads "sqlite"
        forged["options"] = [a, b]
        shown = swarm_out._clean_decision(forged, "worker-1")["shown_sha256"]
        _answer(env.store, SID, key, {"request_id": key, "option_id": "a", "shown_sha256": shown})
        await _pass(env, n=2)
    _run(go())
    assert env.recorded == [] and env.router.calls == []
    assert env.closed(key) == {"why": "answer_refused", "reason": "changed"}


@pytest.mark.parametrize("over,reason", [({"option_id": "c"}, "no_option"), ({"option_id": None}, "no_option"),
                                         ({"request_id": "dc-other"}, "no_option"),
                                         ({"shown_sha256": None}, "changed"), ({"shown_sha256": "0" * 64}, "changed")])
def test_an_answer_that_names_no_option_or_another_card_is_refused(tmp_path, over, reason):
    env = Env(tmp_path, [_item()])
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "a", **over)
        await _pass(env, n=2)
    _run(go())
    assert env.recorded == [] and env.router.calls == []
    assert env.closed(key) == {"why": "answer_refused", "reason": reason}


def test_a_pick_the_run_refuses_is_closed_as_refused(tmp_path):
    from core.run_protocol.engine import EngineError
    env = Env(tmp_path, [_item()])
    env.record_error = EngineError("decision answer is not for the current pending decision")
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "a")
        await _pass(env, n=2)
    _run(go())
    assert env.router.calls == []                  # no continue message for an unrecorded pick
    assert env.closed(key) == {"why": "answer_refused", "reason": "run_refused"}


@pytest.mark.parametrize("error", [OSError("lock"), ValueError("torn json"), LookupError("no engine yet")])
def test_a_passing_failure_to_record_is_tried_again(tmp_path, error):
    env = Env(tmp_path, [_item()])
    env.record_error = error
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "a")
        await _pass(env)
        assert env.recorded == []
        env.record_error = None
        await _pass(env)
    _run(go())
    assert env.recorded == [(RUN, "q1", "a")] and env.closed(key) == {"why": "delivered"}


def test_a_record_that_keeps_failing_is_refused_after_its_tries(tmp_path):
    env = Env(tmp_path, [_item()])
    env.record_error = OSError("disk")
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "a")
        await _pass(env, n=MAX_RECORD_TRIES + 1)
    _run(go())
    assert env.closed(key) == {"why": "answer_refused", "reason": "not_recorded"}


@pytest.mark.parametrize("reply", [
    [ACCEPTED, {"type": "error", "code": "QUEUE_TIMEOUT", "pendingSeq": 3}],   # queued
    [ACCEPTED, {"type": "error", "code": "SESSION_BUSY", "pendingSeq": 4}],    # queued
    [ACCEPTED, {"type": "error", "code": "SPAWN_FAILED"}],                     # the turn failed after
])
def test_a_continue_the_router_accepted_is_never_sent_again(tmp_path, reply):
    env = Env(tmp_path, [_item()], router=Router(reply))
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "a")
        await _pass(env)
        env.decisions.clear()
        await _pass(env, n=3)
    _run(go())
    assert [c[0] for c in env.router.calls] == ["turn"]


@pytest.mark.parametrize("reply", [[{"type": "error", "code": "SESSION_BUSY"}],     # no receipt
                                   [{"type": "error", "code": "SESSION_NOT_FOUND"}]])
def test_a_continue_not_accepted_is_sent_again_under_the_same_id_then_given_up(tmp_path, reply):
    env = Env(tmp_path, [_item()], router=Router(reply))
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "a")
        await _pass(env)
        env.decisions.clear()                      # recorded: the run moved on
        await _pass(env, n=MAX_CONTINUE_TRIES + 2)
    _run(go())
    assert [c[0] for c in env.router.calls] == ["turn"] * MAX_CONTINUE_TRIES
    assert {c[4] for c in env.router.calls} == {f"swarm-out-{key}"}
    assert env.recorded == [(RUN, "q1", "a")]


def test_a_restart_keeps_a_decision_and_its_answer(tmp_path):
    env = Env(tmp_path, [_item()])
    key = _key()

    async def go():
        await _pass(env)
        env.answer(key, "b")
        fresh = env.new_bridge()                   # restarted before the answer was read
        await fresh.reconcile()
        await _pass(env, fresh)
    _run(go())
    assert env.recorded == [(RUN, "q1", "b")]


def test_a_failed_snapshot_write_does_not_stop_the_prompts(tmp_path):
    env = Env(tmp_path, [_item()])
    real = env.store.put
    env.store.put = lambda k, b: (_ for _ in ()).throw(OSError("S3 down")) if k == SNAP else real(k, b)
    env.pm._prompts.setdefault("s9", {})["toolu_9"] = {
        "kind": "ask_user_question", "sessionId": "s9", "tool_use_id": "toolu_9",
        "questions": [{"question": "Which DB?", "options": []}]}
    env.router.units.append(Unit("s9", "toolu_9"))

    async def go():
        await _pass(env)
        env.store.put = real
        await _pass(env)
    _run(go())
    assert [a["kind"] for a in env.asks()] == ["question"] and env.entry(_key()) is not None


# ── Host ─────────────────────────────────────────────────────────────────────────────

def _entry(**over):
    rec = {"kind": "decision", "session_id": SID, "request_id": "dc-abc", "run_id": RUN,
           "asked_at": "2020-01-01T00:00:00Z", "decision_request_id": "q1",
           "question": "Which storage?", "why": "both fit", "truncated": False,
           "options": [{"id": "a", "choice": "files", "consequence": "simple",
                        "reversible": True, "risk": "low"},
                       {"id": "b", "choice": "sqlite", "consequence": "migration",
                        "reversible": False, "risk": "high"}], "hive": "evil"}
    rec.update(over)
    return rec


def _snapshot_env(monkeypatch, entries):
    from tests.test_swarm_out_host import _answer_env
    return _answer_env(monkeypatch, {SNAP: json.dumps({"decisions": entries}).encode()})


def test_host_and_hive_compute_the_same_digest_over_what_is_shown(tmp_path):
    env = Env(tmp_path, [_item()])
    _run(_pass(env))
    assert env.shown(_key())["shown_sha256"] == decision_digest(env.entry(_key()))


def test_host_keeps_a_decision_from_known_fields():
    clean = swarm_out._clean_decision(_entry(extra="x"), "worker-1")
    assert clean["kind"] == "decision" and clean["hive"] == "worker-1" and "extra" not in clean
    assert [o["id"] for o in clean["options"]] == ["a", "b"] and clean["truncated"] is False


@pytest.mark.parametrize("over", [
    {"options": [{"id": "a", "choice": "x"}]},                          # one option
    {"options": [{"id": "a"}, {"id": "a"}]},                            # duplicate ids
    {"options": [{"id": "a"}, {"id": 7}]},                              # not a string
    {"options": [{"id": "a"}, {"id": "b" * 201}]},                      # too long to send back
    {"options": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]},  # more than DC-1 allows
    {"run_id": "../x"}, {"request_id": "toolu_1"}, {"kind": "question"},
])
def test_host_refuses_a_malformed_decision(over):
    assert swarm_out._clean_decision(_entry(**over), "worker-1") is None


@pytest.mark.parametrize("over", [{"truncated": True}, {"question": "q" * 5000},
                                  {"options": [{"id": "a", "choice": "c" * 501}, {"id": "b"}]}])
def test_host_marks_a_decision_it_cannot_show_whole(over):
    assert swarm_out._clean_decision(_entry(**over), "worker-1")["truncated"] is True


def test_a_decision_written_to_ask_is_not_shown():
    assert swarm_out._clean_ask(_entry(), "worker-1") is None


def test_host_lists_exactly_the_snapshot(monkeypatch):
    s3 = _snapshot_env(monkeypatch, [_entry(), _entry(request_id="dc-bad", options=[])])
    listed = swarm_out._read_decisions(s3, "b", "worker-1")
    assert [d["request_id"] for d in listed] == ["dc-abc"]
    del s3.objects[SNAP]
    assert swarm_out._read_decisions(s3, "b", "worker-1") == []      # an older Hive


def test_an_open_decision_makes_its_run_waiting_whatever_the_session_says():
    row = {"session_id": SID}
    idle = {SID: {"streaming": False, "waiting_input": False, "activity": "idle"}}
    assert swarm_out.run_state(row, "running", idle, True, True) == ("waiting", "decision")
    assert swarm_out.run_state(row, "running", {}, True, True) == ("waiting", "decision")
    assert swarm_out.run_state(row, "running", idle, False, False) == ("stopped", "idle")
    assert swarm_out.run_state(row, "gone", idle, True, True) == ("stopped", "hive_gone")


def test_host_writes_the_option_and_what_it_showed(monkeypatch):
    s3 = _snapshot_env(monkeypatch, [_entry()])
    shown = swarm_out._clean_decision(_entry(), "worker-1")["shown_sha256"]
    for kw, match in (({"option_id": "b", "shown_sha256": "0" * 64}, "changed"),
                      ({"option_id": "c", "shown_sha256": shown}, "options")):
        with pytest.raises(swarm_out.SwarmOutError, match=match):
            asyncio.run(swarm_out.answer("h1", SID, "dc-abc", **kw))
    asyncio.run(swarm_out.answer("h1", SID, "dc-abc", option_id="b", shown_sha256=shown))
    body = json.loads(s3.objects[f"bridge/worker-1/answer/{SID}/dc-abc.json"])
    assert (body["request_id"], body["option_id"], body["shown_sha256"]) == ("dc-abc", "b", shown)
    with pytest.raises(swarm_out.SwarmOutError, match="already answered"):
        asyncio.run(swarm_out.answer("h1", SID, "dc-abc", option_id="a", shown_sha256=shown))


@pytest.mark.parametrize("entries,closed", [([], False), ([_entry()], True), ([_entry(truncated=True)], False),
                                            ([_entry(session_id="sess-2")], False)])
def test_host_refuses_an_answer_to_a_decision_it_cannot_answer(monkeypatch, entries, closed):
    s3 = _snapshot_env(monkeypatch, entries)
    if closed:                                     # the Hive refused or closed it already
        s3.objects[f"bridge/worker-1/closed/{SID}/dc-abc.json"] = b'{"why": "answer_refused"}'
    shown = swarm_out._clean_decision(entries[0], "worker-1")["shown_sha256"] if entries else None
    with pytest.raises(swarm_out.SwarmOutError):
        asyncio.run(swarm_out.answer("h1", SID, "dc-abc", option_id="b", shown_sha256=shown))
    assert not [k for k in s3.objects if "/answer/" in k]


# ── End to end on a real v1 run ──────────────────────────────────────────────────────

@pytest.fixture()
def real_run(tmp_path, monkeypatch):
    from core.run_protocol.engine import RunEngine
    workspace, data = tmp_path / "workspace", tmp_path / "data"
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setattr(pipeline_human, "_workspace", lambda: workspace)
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="trivial", session_id=OWNER,
                              repos={"repo": str(_git_repo(tmp_path / "repo"))},
                              run_id="run_e2e1")
    engine.write_projection()
    value = _decision()
    value["refs"] = [engine.store.read_events()[0]["event_id"]]
    asked = engine.request_decision(value, document="DESIGN_SPEC")
    return engine, asked


def _human_answers(engine):
    return [e["payload"] for e in engine.store.read_events() if e.get("kind") == "human_answer"]


def test_e2e_no_card_the_host_pick_lands_on_the_run(real_run, tmp_path):
    engine, _ = real_run
    found = pipeline_human.pending_decisions()
    assert [(d["run_id"], d["session_id"], d["owner_generation"], d["decision"]["request_id"])
            for d in found] == [("run_e2e1", OWNER, 0, "q1")]
    router = Router()
    store = FakeStore()
    bridge = Bridge("worker-1", store, pm=PermissionManager(persist_path=tmp_path / "p.json"),
                    router=router)                       # the real readers
    key = decision_key("run_e2e1", OWNER, 0, found[0]["decision"])

    async def go():
        await bridge.tick()
        entry = next(e for e in json.loads(store.objects[SNAP])["decisions"] if e["request_id"] == key)
        shown = swarm_out._clean_decision(entry, "worker-1")["shown_sha256"]
        store.put(f"bridge/worker-1/answer/{OWNER}/{key}.json",
                  json.dumps({"request_id": key, "option_id": "b", "shown_sha256": shown}).encode())
        await bridge.tick()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if router.calls:
                break
        await bridge.tick()
    _run(go())
    assert _human_answers(engine) == [{"request_id": "q1", "option_id": "b"}]
    assert pipeline_human.pending_decisions() == []
    assert router.calls and router.calls[0][0] == "turn"
    assert json.loads(store.objects[SNAP])["decisions"] == []


def test_e2e_card_open_the_delivered_pick_is_recorded_by_the_card_path(real_run):
    engine, asked = real_run
    (pending,) = pipeline_human.pending_decisions()
    question = asked["ask_user_question"]["questions"][0]["question"]
    option = next(o for o in pending["decision"]["options"] if o["id"] == "a")
    delivered = {question: decision_protocol._label(option)}   # what the bridge sends the card
    # the ask gate passes a person's card answer to record_card_answer (security_hooks)
    recorded = pipeline_human.record_card_answer(OWNER, delivered)
    assert recorded == [{"run_id": "run_e2e1", "request_id": "q1", "option_id": "a"}]
    assert _human_answers(engine) == [{"request_id": "q1", "option_id": "a"}]


def test_an_unreadable_decision_list_keeps_the_questions_and_says_so(monkeypatch):
    from tests.test_swarm_out_host import _answer_env, INST
    _answer_env(monkeypatch, {SNAP: b"{torn"})
    monkeypatch.setattr(swarm_out, "_open_asks", lambda s3, b, h, *_a: [
        {"kind": "approval", "session_id": "s1", "request_id": "r1"}])

    async def no_states(inst):
        return None
    monkeypatch.setattr(swarm_out, "_session_states", no_states)
    asks, _sessions, errors = asyncio.run(swarm_out._read_hive("h1"))
    assert [a["request_id"] for a in asks] == ["r1"]
    assert errors == [f"could not read the decisions of Hive {INST['name']}"]
