"""No Swarm Out state leaves the agent that answers for a run without a way out, and no
wait goes unanswered.

Each test answers one question, and goes red when its fix is reverted:
- an Elastic round whose session ended early (idle, inside its lifetime): does stop
  succeed (``RunEnded`` is "already stopped"), and does a whole read after the end make
  the run discardable?
- a run waiting on an answer: is stop among its actions?
- a run held back only by returned files that never came: does ``settle_without_files``
  record the agent's decision, pull once first, and leave nothing holding the run? Is it
  refused for any other blocker, or from another session? Does a lost round's result
  count its files as never coming?
- a run whose owner session is gone: may a session of the same person adopt it, and is
  that refused while the owner lives, for another person, for a Swarm Run's child? Does
  the Swarm Out tab's New session hand that tab's open runs over?
- a wait with more questions than a chat card holds: does the agent answer it through
  the CLI, and does only a wait the Host cannot answer reach Needs You?
- do Swarm Out's Needs You items say since when they wait?
- a woken agent that did nothing: is it woken again, a bounded number of times, then one
  Needs You item?
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from datetime import datetime, timezone

import pytest

from core import (attention_authority as aa, hive_files, hive_records, swarm_out,
                  swarm_out_actor as act, swarm_out_follow, swarm_out_record as rc,
                  swarm_out_results, swarm_out_settle as st, swarm_out_wait_card as wc,
                  swarm_out_wake as wake, swarm_run)
from core import swarm_out_results as sor
from core.carriers import agentcore as ac
from core.carriers import registry
from tests.test_carriers_agentcore import DID as AC_DID, _open, _to_s3, world  # noqa: F401 — the fixture

DID = "so_aaaaaaaaaaaa"
SID, SID2 = "s-abc123", "s-def456"
ENDED = "2026-11-02T10:00:00Z"
OUT_FILE = f"Outputs/{DID}/report.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A Host with one run's dispatch row and kept result; sessions ``gone`` do not
    exist, every other one does and is the install's owner."""
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    rows: dict[str, dict] = {}
    kept = {"records": {"closed": True, "last_at": _now_iso()},
            "result": {"ended": "completed", "provisional": False, "ended_at": ENDED,
                       "work": {"repos": [], "files": []}}}

    def log_row(dispatch_id):
        if dispatch_id not in rows:
            raise swarm_out.SwarmOutError("no dispatch")
        return rows[dispatch_id]
    monkeypatch.setattr(swarm_out, "_log_row_of", log_row)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: kept)
    monkeypatch.setattr(aa, "_session_title", lambda sid: (sid != "gone", "Main"))
    monkeypatch.setattr(aa, "session_confirmed", lambda sid: sid != "gone")
    people = {"gone": (False, "")}
    monkeypatch.setattr(aa, "session_person", lambda sid: people.get(sid, (True, "")))
    monkeypatch.setattr(wc, "escalation", lambda d, r: None)
    monkeypatch.setattr(hive_files, "placed", lambda d: [])
    monkeypatch.setattr(hive_files, "not_sealed", lambda d: {})
    monkeypatch.setattr(swarm_out, "_refresh_soon", lambda: None)
    return rows, kept, people


def _row(**kw) -> dict:
    r = {"dispatch_id": DID, "hive_id": "h1", "hive": "demo", "session_id": "hs1", "title": "T",
         "dispatched_from": SID, "sent_at": _now_iso(), "project_agent": {"returns": "answer"},
         "state": "stopped", "state_reason": "idle",
         "result": {"ended": "completed", "provisional": False, "ended_at": ENDED, "lessons": None,
                    "work": {"repos": [], "files": []}}}
    r.update(kw)
    return r


def _annotated(r: dict, kept: dict) -> dict:
    """The row as /runs shows it."""
    out = dict(r)
    st.annotate([out])
    return out


# ── 1. an Elastic round that ended early ─────────────────────────────────────

class _Ended:
    """A carrier client that refuses at once: the round's session has ended."""

    async def __aenter__(self):
        raise ac.RunEnded("ended")

    async def __aexit__(self, *exc):
        return False


def test_stop_turn_treats_run_ended_as_stopped(env, monkeypatch):
    rows, _kept, _p = env
    rows[DID] = _row(state="running")

    class H:
        session = "chat-1"
    monkeypatch.setattr(registry, "handle_of", lambda row: H())
    monkeypatch.setattr(registry, "client_for", lambda handle, *a, **k: _Ended())
    out = asyncio.run(swarm_out.stop_turn(DID))
    assert out == {"stopped": True, "ended": "ended"}


@pytest.fixture
def elastic(world, monkeypatch, tmp_path):                 # noqa: F811 — the imported fixture
    """A round on the fake Elastic runtime opened an hour ago (far inside its lifetime)
    whose Outputs file came back and whose turn record never did."""
    monkeypatch.setitem(registry._made, "agentcore", world.carrier)
    monkeypatch.setattr(hive_files, "_place_one", lambda ws, row, src, path: path)
    from core import project_registry
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: tmp_path / "ws")
    rows: dict = {}
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: rows[d])

    async def make():
        h = await _open(world)
        h = dataclasses.replace(h, opened_at=time.time() - 3600)
        ws = world.tmp / "hive-ws"
        out = f"Outputs/{AC_DID}/report.md"
        hive_records.append(ws, h.run_ref, "file", data=b"# report\n", path=out, listed=out)
        _to_s3(world, ws, h.run_ref)
        row = {"dispatch_id": AC_DID, "hive_id": "ac1", "hive": "elastic", "session_id": "chat-0000000001",
               "rounds": [h.to_row()], "project_agent": {"returns": "answer"},
               "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600)),
               "dispatched_from": "sess-agent", "state": "stopped", "state_reason": "idle"}
        rows[AC_DID] = row
        return row, h
    return make


async def test_an_idle_ended_elastic_round_read_whole_is_discardable(elastic, world):  # noqa: F811
    row, h = await elastic()
    # the session ended itself (idle): its state.json says so, and this host sees it
    world.s3.put(f"bridge/{h.run_ref}/state.json", {"state": "ended"})
    assert await world.carrier.state(h) == ("stopped", "idle")
    end = ac.writes_end_at(h)
    assert end is not None and end < h.opened_at + ac.MAX_RUN_S     # not the 8 h bound
    kept = await sor.fetch(row)
    assert kept["result"] is None
    assert kept["round_end"]["round"] == 1
    r = {**row, "result": kept.get("result"), "result_status": sor._status(row, "running", kept),
         "result_error": kept.get("fetch_error")}
    st.annotate([r])
    assert (r["stage"], r["why"]) == ("stopped", "no_result")
    assert "stop" not in [a["key"] for a in r["actions"]]
    assert act.discard_blocker(r) is None


# ── 2. a run waiting on an answer ────────────────────────────────────────────

@pytest.mark.parametrize("reason", [None, "answer_refused", "waiting_input"])
def test_needs_answer_offers_stop(reason):
    keys = [a["key"] for a in st._actions("needs_answer", True, None, reason)]
    assert "stop" in keys and keys[-1] == "discard"


def test_the_owner_agent_may_stop_a_waiting_run(env):
    rows, kept, _p = env
    rows[DID] = _row(state="waiting", state_reason="waiting_input")
    r = _annotated(rows[DID], kept)
    assert r["stage"] == "needs_answer"
    assert act.row_blocker(act.actor("agent", SID, "nobody will answer it"), "stop", r) is None


# ── 3. a run held back only by files that never came ─────────────────────────

def _files_only(env):
    """An accepted answer run whose one listed Outputs file never came back."""
    rows, kept, _p = env
    kept["result"]["work"]["files"] = [{"path": OUT_FILE}]
    rows[DID] = _row(result={**kept["result"], "lessons": None}, state_reason="hive_stopped")
    st._update(DID, lambda rec: rec.update(verdict={"value": "pass", "ended_at": ENDED, "at": ENDED}))
    return rows[DID]


def test_settle_without_files_records_decision_and_unblocks(env):
    row = _files_only(env)
    r = _annotated(row, env[1])
    assert r["stage"] == "accepted" and r["settle"]["why_not"] == ["files"]
    assert rc.actionable(r, [])                      # its owner is woken for it
    assert act.files_blocker(r) is None
    # the agent that answers for it, with its reason (the owner check of every command)
    assert act.row_blocker(act.actor("agent", SID, "its Hive is gone"), "settle_without_files", r) is None
    out = st.settle_without_files(DID, "its Hive was stopped for good", "agent", SID, False)
    assert out["skipped"] == [OUT_FILE]
    d = st.derive(row, env[1])
    assert d["why_not"] == [] and d["settles"]
    e = [c for c in rc.entries(st.load(DID)) if c["decision"] == "settle_without_files"]
    assert len(e) == 1 and e[0]["why"] == "its Hive was stopped for good" and e[0]["session"] == SID
    assert e[0]["observed"]["files"] == [OUT_FILE] and e[0]["observed"]["pulled"] is False
    # nothing holds it any more: a second call writes nothing
    assert st.settle_without_files(DID, "again", "agent", SID)["skipped"] == []


def test_settle_without_files_refused_for_other_blockers_or_sessions(env):
    rows, kept, _p = env
    row = _files_only(env)
    r = _annotated(row, kept)
    assert "another session" in act.row_blocker(act.actor("agent", SID2, "w"), "settle_without_files", r)
    # not decided yet: the verdict holds it too
    st._update(DID, lambda rec: rec.pop("verdict"))
    r = _annotated(row, kept)
    assert set(r["settle"]["why_not"]) == {"verdict", "files"}
    assert "waits on: verdict, files" in act.files_blocker(r)
    assert act.row_blocker(act.actor("agent", SID, "w"), "settle_without_files", r) == act.files_blocker(r)


def test_the_route_pulls_once_before_settling_without_files(env, monkeypatch):
    from routers import swarm_out as route
    row = _files_only(env)
    r = _annotated(row, env[1])
    pulls = []

    async def run_row(d):
        return r

    async def refresh(d):
        pulls.append(d)
        return {"result_error": "hive_not_running"}
    monkeypatch.setattr(swarm_out, "run_row", run_row)
    monkeypatch.setattr(swarm_out, "refresh_result", refresh)
    out = asyncio.run(route.run_settle_without_files(
        DID, route.ActorBody(by="agent", session=SID, why="its Hive is gone")))
    assert pulls == [DID] and out == {"skipped": [OUT_FILE], "pulled": False}


def test_a_lost_rounds_files_count_as_never_coming(env, monkeypatch):
    rows, kept, _p = env
    kept["result"] = {"ended": "running", "provisional": True, "work": {"files": [{"path": OUT_FILE}]}}
    row = _row(result=dict(kept["result"]))
    monkeypatch.setattr(st, "result_lost", lambda row, kept: {"why": "idle", "checked_at": ENDED})
    assert "files" not in st.derive(row, kept)["why_not"]


# ── 4. a run whose owner session is gone ─────────────────────────────────────

def _gone_owner_row(env, **kw):
    rows, kept, _p = env
    rows[DID] = _row(dispatched_from="gone", **kw)
    return _annotated(rows[DID], kept)


def test_adopt_when_the_owner_session_is_gone(env):
    r = _gone_owner_row(env)
    a = act.actor("agent", SID2, "its session was closed")
    assert act.row_blocker(a, "adopt", r) is None
    act.adopt(DID, SID2, swarm_run.owner_of(r), "agent", a.why)
    assert swarm_run.owner_of(r) == SID2 and swarm_run.owner_of(env[0][DID]) == SID2
    e = [c for c in rc.entries(st.load(DID)) if c["decision"] == "adopt"]
    assert len(e) == 1 and e[0]["session"] == SID2 and e[0]["observed"]["from_session"] == "gone"
    # every command now answers to the new owner, and no longer to anyone else
    assert act.row_blocker(a, "discard", _annotated(env[0][DID], env[1])) is None
    assert "another session" in act.row_blocker(act.actor("agent", SID, "w"), "discard",
                                                _annotated(env[0][DID], env[1]))


def test_adopt_refused_when_owner_alive_other_person_or_child(env, monkeypatch):
    rows, kept, people = env
    a = act.actor("agent", SID2, "w")
    # the owner session still exists
    rows[DID] = _row()
    assert "still exists" in act.row_blocker(a, "adopt", _annotated(rows[DID], kept))
    # another person's run
    people[SID2] = (True, "someone-else")
    assert "another person's" in act.row_blocker(a, "adopt", _gone_owner_row(env))
    people.pop(SID2)
    # a Swarm Run's child answers to its lead run's owner
    monkeypatch.setattr(swarm_run, "lead", lambda run_id: None)
    child = _gone_owner_row(env, parent_run="run_0123456789ab")
    assert "child of Swarm Run" in act.adopt_blocker(a, child)
    assert swarm_run.owner_of(rows[DID]) is None
    # the person adopts through New session, never this command
    assert "New session" in act.adopt_blocker(act.actor("person", None, ""), _gone_owner_row(env))


def test_new_session_hands_the_tabs_open_runs_over(env):
    rows, _kept, people = env
    mine = _row()
    other = _row(dispatch_id="so_bbbbbbbbbbbb", dispatched_from="s-zzz999")
    settled = _row(dispatch_id="so_cccccccccccc")
    st._update("so_cccccccccccc", lambda rec: rec.update(settled={"outcome": "passed"}))
    moved = act.hand_over(SID, SID2, [mine, other, settled])
    assert moved == [DID]
    assert swarm_run.owner_of(mine) == SID2 and swarm_run.owner_of(other) == "s-zzz999"
    assert swarm_run.owner_of(settled) == SID
    e = [c for c in rc.entries(st.load(DID)) if c["decision"] == "adopt"]
    assert e[-1]["by"] == "person" and e[-1]["why"] == act.HAND_OVER_WHY
    # never across people
    people["s-ppp111"] = (True, "someone-else")
    with pytest.raises(act.ActorError):
        act.hand_over(SID2, "s-ppp111", [mine])
    assert swarm_run.owner_of(mine) == SID2


# ── 5. a wait bigger than a chat card ────────────────────────────────────────

FIVE = {"kind": "question", "session_id": "hs1", "request_id": "q-5", "phase": "waiting",
        "approval_class": "question", "asked_at": "2026-11-20T10:00:00Z",
        "questions": [{"question": f"Question {i}?", "header": "Q", "multiSelect": False,
                       "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}
                      for i in range(5)]}


@pytest.fixture
def hive(env, monkeypatch):
    rows, _kept, _p = env
    rows[DID] = _row(state="waiting")
    asks = [FIVE]
    sent: list = []

    async def run_row(dispatch_id):
        return rows.get(dispatch_id)

    async def list_runs(max_age_s=0):
        return {"runs": list(rows.values()), "asks": asks, "answered": []}

    async def answer(hive_id, session_id, request_id, **kw):
        sent.append((hive_id, session_id, request_id, kw))
        return {"ok": True}
    monkeypatch.setattr(swarm_out, "run_row", run_row)
    monkeypatch.setattr(swarm_out, "list_runs", list_runs)
    monkeypatch.setattr(swarm_out, "answer", answer)
    return sent, asks


def test_a_five_question_wait_is_answered_by_the_agent_through_the_cli(hive, monkeypatch, tmp_path):
    sent, _asks = hive
    with pytest.raises(wc.CardError):                    # no chat card holds it
        asyncio.run(wc.card(DID, "q-5"))
    shown = asyncio.run(wc.view(DID, "q-5"))
    assert shown["who"] == "you" and shown["why_not"] is None
    assert len(shown["wait"]["questions"]) == 5
    # the CLI sends one label per question, in order
    from scripts import swarm_out as cli
    from core.knowledge_cli import RESTRICTED_ENV
    monkeypatch.delenv(RESTRICTED_ENV, raising=False)
    monkeypatch.setenv("SWARM_APP_SESSION_ID", SID)
    calls = []
    monkeypatch.setattr(cli, "_call", lambda method, path, body=None, **k: calls.append((method, path, body)) or 0)
    f = tmp_path / "answers.json"
    f.write_text('["A", "B", "A", "B", "A"]')
    assert cli.main(["answer", DID, "q-5", "--answers-file", str(f), "--why", "each is the smaller change"]) == 0
    method, path, body = calls[-1]
    assert path == f"/runs/{DID}/waits/q-5/answer-host" and body["picks"] == ["A", "B", "A", "B", "A"]
    # the route's answer: every question answered, as the host's
    out = asyncio.run(wc.answer_as_host(body["session"], DID, "q-5", body["picks"], body["why"]))
    assert out["status"] == "forwarded" and out["by"] == "host"
    (_h, _s, rid, kw), = sent
    assert rid == "q-5" and kw["answered_by"] == "host"
    assert kw["answers"] == {f"Question {i}?": "AB"[i % 2] for i in range(5)}


def test_a_host_answer_is_one_of_the_options_never_free_text(hive):
    asyncio.run(wc.view(DID, "q-5"))
    with pytest.raises(wc.CardError):
        asyncio.run(wc.answer_as_host(SID, DID, "q-5", ["A", "B", "A", "B", "write it yourself"], "w"))
    with pytest.raises(wc.CardError):
        asyncio.run(wc.answer_as_host(SID2, DID, "q-5", ["A"] * 5, "w"))      # not its run
    assert hive[0] == []


def test_needs_you_has_only_the_waits_the_host_cannot_answer(env, monkeypatch):
    rows, _kept, _p = env
    truncated = {"kind": "decision", "session_id": "hs1", "request_id": "dc-1", "phase": "waiting",
                 "approval_class": "decision", "truncated": True, "asked_at": "2026-11-21T10:00:00Z",
                 "options": [{"id": "a"}, {"id": "b"}]}
    runs = [{**_row(state="waiting"), "stage": "needs_answer"}]
    monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": runs, "asks": [FIVE, truncated]})
    items = {i.id: i for i in aa._collect_swarm_out()}
    assert set(items) == {f"swarm_out:wait:{DID}:dc-1"}                       # the 5 questions are the agent's
    assert "cannot be answered from this host" in items[f"swarm_out:wait:{DID}:dc-1"].detail
    # the wake and the record agree: the agent has its wait to answer, the person theirs
    assert rc.actionable(runs[0], [FIVE]) and not rc.actionable(runs[0], [truncated])
    assert rc.needs_person(runs[0], [truncated]) is True and rc.needs_person(runs[0], [FIVE]) is False


# ── 6. Needs You's Swarm Out items say since when ────────────────────────────

def test_swarm_out_items_fill_since(env, monkeypatch):
    runs = [{**_row(), "stage": "needs_answer"},
            {**_row(dispatch_id="so_bbbbbbbbbbbb", session_id="hs2"), "stage": "awaiting",
             "funnel": {"ask": {"state": "open", "at": "2026-11-22T08:00:00Z", "decide": "keep?",
                                "judgment": "keep", "why": "taste", "landed": {}}}}]
    off = {"kind": "approval", "session_id": "hs1", "request_id": "ap-1", "approval_class": "off_machine",
           "asked_at": "2026-11-21T09:00:00Z"}
    monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": runs, "asks": [off]})
    items = {i.id: i for i in aa._collect_swarm_out()}
    assert items[f"swarm_out:wait:{DID}:ap-1"].since.startswith("2026-11-21T09:00:00")
    assert items["swarm_out:ask:so_bbbbbbbbbbbb"].since.startswith("2026-11-22T08:00:00")


# ── 7. a woken agent that did nothing ────────────────────────────────────────

class _Router:
    def __init__(self):
        self.sent = []

    def is_busy(self, sid):
        return False

    def run_conversation(self, **kw):
        self.sent.append(kw)

        async def gen():
            yield {"type": "accepted", "clientId": kw["client_id"]}
        return gen()


def test_rewake_is_bounded_then_one_needs_you_item(env, monkeypatch):
    run = {"dispatch_id": DID, "dispatched_from": SID, "stage": "awaiting", "state": "stopped",
           "session_id": "hs1", "title": "T", "settle": {"why_not": ["verdict"]}}
    data = {"runs": [run], "asks": [], "answered": []}
    monkeypatch.setattr(swarm_out, "snapshot", lambda: data)
    from core import session_pending

    async def none(sid):
        return 0
    monkeypatch.setattr(session_pending, "count_pending", none)
    clock = {"t": 1_000_000.0}

    class _Time:
        @staticmethod
        def time():
            return clock["t"]
    monkeypatch.setattr(wake, "time", _Time)
    router = _Router()
    # the change wakes it once; its turn's digest marks it seen and it does nothing
    assert asyncio.run(wake.wake_sessions(router)) == [SID]
    swarm_out_follow.mark_seen(SID, swarm_out_follow.digest(SID)[1])
    assert asyncio.run(wake.wake_sessions(router)) == []          # seen: the count starts now
    clock["t"] += wake.REWAKE_AFTER_S - 60
    assert asyncio.run(wake.wake_sessions(router)) == []          # not long enough yet
    for n in range(1, wake.MAX_REWAKES + 1):
        clock["t"] += wake.REWAKE_AFTER_S
        assert asyncio.run(wake.wake_sessions(router)) == [SID], n
        text = router.sent[-1]["user_message"]
        assert text.startswith(wake.MARKER) and DID in text and wake.is_wake_text(text)
        assert asyncio.run(wake.wake_sessions(router)) == []      # once per interval
    assert not [i for i in aa._collect_swarm_out() if i.id.startswith("swarm_out:stuck")]
    clock["t"] += wake.REWAKE_AFTER_S
    n = len(router.sent)
    assert asyncio.run(wake.wake_sessions(router)) == [] and len(router.sent) == n   # bounded
    stuck = [i for i in aa._collect_swarm_out() if i.id == f"swarm_out:stuck:{DID}"]
    assert len(stuck) == 1 and stuck[0].since
    # a new state of the run starts the count over: no item, woken again on its change
    run["stage"], run["settle"] = "stopped", {"why_not": ["verdict"]}
    assert not wake.exhausted(data, clock["t"])


# ── what the review found ────────────────────────────────────────────────────

def test_the_adopt_route_checks_whoever_the_body_says_it_is(env, monkeypatch):
    from fastapi import HTTPException
    from routers import swarm_out as route
    rows, kept, _p = env
    r = _gone_owner_row(env)

    async def run_row(d):
        return r
    monkeypatch.setattr(swarm_out, "run_row", run_row)
    for body in (route.ActorBody(by="person", session=SID2, why="mine now"),
                 route.ActorBody(by="agent", session=None, why="mine now")):
        with pytest.raises(HTTPException) as e:
            asyncio.run(route.run_adopt(DID, body))
        assert e.value.status_code == 403
        assert swarm_run.adopted(DID) is None
    out = asyncio.run(route.run_adopt(DID, route.ActorBody(by="agent", session=SID2, why="its session was closed")))
    assert out == {"adopted": True, "from_session": "gone"} and swarm_run.adopted(DID) == SID2


def test_two_adoptions_at_once_the_second_is_refused(env):
    swarm_run.set_owner(DID, SID2, "gone", "agent", ENDED)
    with pytest.raises(swarm_run.OwnerChanged):
        swarm_run.set_owner(DID, "s-zzz999", "gone", "agent", ENDED)    # checked before the first wrote
    assert swarm_run.adopted(DID) == SID2


def test_a_run_whose_person_was_not_known_is_adopted_by_nobody(env, monkeypatch):
    rows, kept, people = env
    people["s-unread1"] = (False, "")
    assert swarm_out._person_of("s-unread1") == act.UNKNOWN_PERSON
    r = _gone_owner_row(env, dispatched_person=act.UNKNOWN_PERSON)
    assert "another person's" in act.adopt_blocker(act.actor("agent", SID2, "w"), r)


def test_the_agent_never_answers_a_wait_the_host_cannot_answer(hive):
    sent, asks = hive
    asks.append({**FIVE, "request_id": "q-cut", "omitted": {"questions": 3, "options": 0}})
    shown = asyncio.run(wc.view(DID, "q-cut"))
    assert shown["who"] == "person" and shown["why_not"]
    with pytest.raises(wc.CardError) as e:
        asyncio.run(wc.answer_as_host(SID, DID, "q-cut", ["A"] * 5, "w"))
    assert e.value.status == 403 and sent == []


def test_settling_without_files_is_bound_to_the_result_judged(env):
    rows, kept, _p = env
    _files_only(env)
    with pytest.raises(st.SettleError):
        st.settle_without_files(DID, "w", "agent", SID, True, "2026-11-01T00:00:00Z")   # an older result
    assert st.settle_without_files(DID, "w", "agent", SID, True, ENDED)["skipped"] == [OUT_FILE]
    # a newer turn lists the same file: the skip of the older result does not cover it
    kept["result"] = {**kept["result"], "ended_at": "2026-11-03T10:00:00Z"}
    row = _row(result=dict(kept["result"]))
    assert "files" in st.derive(row, kept)["why_not"]


def test_a_command_shown_cut_short_is_only_denied_by_the_agent(hive):
    sent, asks = hive
    asks.append({"kind": "approval", "session_id": "hs1", "request_id": "ap-cut", "phase": "waiting",
                 "approval_class": "hive_local", "tool_name": "Bash", "command": "rm -rf build/" + "x" * 50,
                 "truncated": True, "shown_sha256": "s" * 64})
    shown = asyncio.run(wc.view(DID, "ap-cut"))
    assert shown["who"] == "you"                         # it can always be denied
    with pytest.raises(wc.CardError) as e:
        asyncio.run(wc.answer_as_host(SID, DID, "ap-cut", None, "w", pick="Approve"))
    assert e.value.status == 403 and sent == [] and "swarm_out escalate" in str(e.value)
    asyncio.run(wc.answer_as_host(SID, DID, "ap-cut", None, "it was cut short", pick="Deny"))
    assert sent[-1][3]["decision"] == "deny"


def test_a_truncated_decision_is_never_answered_by_the_agent(hive):
    sent, asks = hive
    asks.append({"kind": "decision", "session_id": "hs1", "request_id": "dc-cut", "phase": "waiting",
                 "approval_class": "decision", "truncated": True, "question": "Which?", "shown_sha256": "d" * 64,
                 "options": [{"id": "a", "choice": "A"}, {"id": "b", "choice": "B"}]})
    assert asyncio.run(wc.view(DID, "dc-cut"))["who"] == "person"
    with pytest.raises(wc.CardError) as e:
        asyncio.run(wc.answer_as_host(SID, DID, "dc-cut", None, "w", pick="a"))
    assert e.value.status == 403 and sent == []


def test_the_route_refuses_a_result_the_pull_replaced(env, monkeypatch):
    from fastapi import HTTPException
    from routers import swarm_out as route
    rows, kept, _p = env
    r = _annotated(_files_only(env), kept)

    async def run_row(d):
        return r

    async def refresh(d):                                # the pull brought a newer turn
        kept["result"] = {**kept["result"], "ended_at": "2026-11-03T10:00:00Z"}
        return {"result_error": None}
    monkeypatch.setattr(swarm_out, "run_row", run_row)
    monkeypatch.setattr(swarm_out, "refresh_result", refresh)
    with pytest.raises(HTTPException) as e:
        asyncio.run(route.run_settle_without_files(DID, route.ActorBody(by="agent", session=SID, why="w")))
    assert e.value.status_code == 400 and "files_skipped" not in st.load(DID)
