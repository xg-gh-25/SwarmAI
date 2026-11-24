"""A dispatch card for every Swarm Out task a chat session sent, however the Bash
command that sent it was written (core/swarm_out_dispatch_index, core/swarm_out_card
``cards_of_claimed``, core/streaming_orchestrator ``_bash_card_events``).

Questions: (1) does a wrapper script's dispatch draw its card from the dispatch log row
(``dispatched_from`` = the session, sent while the call ran), one per dispatch it sent;
(2) does a dispatch never get a second card: an old id echoed later, parallel calls whose
windows overlap, a literal call next to the sweep, a "not sure" card; (3) do rows of
another session, from before the call, or of a Swarm Run's child draw no dispatch card
(a child draws its run's card once); (4) does a reload show the same cards; (5) is the
added work per Bash result one in-memory lookup, measured.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from core import executors, swarm_out, swarm_out_card, swarm_out_dispatch_index as index
from tests.test_swarm_out_card import CMD, ROW, _bash, _cards, _res

SID = "sess-test"            # tests.test_tool_call_leak_guard._make_orchestrator's session
WRAPPER = "/tmp/so/go.sh h1 'SwarmAI weekly report'"


def _at(dt: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + dt))


def _send(did: str, sid=SID, dt: float = 0.0, **kw) -> dict:
    """Write a dispatch log row through the log's own writer (as the daemon does)."""
    row = {**ROW, "dispatch_id": did, "sent_at": _at(dt), "dispatched_from": sid, **kw}
    swarm_out._append(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _drive(messages):
    from tests.test_tool_call_leak_guard import _drive as drive, _make_orchestrator, _make_result_message
    events, raised = asyncio.run(drive(_make_orchestrator(), [*messages, _make_result_message()]))
    assert raised is None
    return events


def _ids(events):
    return [b["dispatchId"] for b in _cards(events)]


def _run_cards(events):
    return [b for e in events if e.get("type") == "assistant"
            for b in e.get("content") or [] if isinstance(b, dict) and b.get("type") == "swarm_run"]


@pytest.fixture(autouse=True)
def _isolated_queue():
    from core.permission_manager import permission_manager
    permission_manager.clear_session(SID)
    yield
    permission_manager.clear_session(SID)


D1, D2, D3 = "so_" + "a" * 12, "so_" + "b" * 12, "so_" + "c" * 12


def test_the_index_is_fed_by_the_logs_writer(monkeypatch, caplog):
    """The log's writer indexes a row by the session that sent it; a row no session sent
    is not indexed; an index failure never fails the write."""
    assert not index.has(SID)
    _send(D1)
    _send(D2, sid=None)
    assert index.has(SID) and not index.has(None)
    assert index.claim(SID, time.time(), time.time()) == ([D1], [])
    assert [r["dispatch_id"] for r in swarm_out._read_log()] == [D1, D2]

    def boom(row):
        raise RuntimeError("index gone")
    monkeypatch.setattr(index, "record", boom)
    _send(D3)
    assert swarm_out._read_log()[-1]["dispatch_id"] == D3
    assert "not indexed" in caplog.text


@pytest.mark.parametrize("carrier", ["user", "assistant"])
def test_a_wrapper_script_dispatch_yields_its_card(carrier):
    """The command names no `scripts.swarm_out dispatch`: the card comes from the row,
    after the call's result, with what the row holds."""
    _send(D1)
    events = _drive([_bash("tu1", WRAPPER), _res("tu1", "dispatched\n", carrier=carrier)])
    (b,) = _cards(events)
    assert (b["ok"], b["dispatchId"], b["hive"], b["brain"]) == (True, D1, "explorer", "SwarmAI")
    at = next(i for i, e in enumerate(events) if _cards([e]))
    res = next(i for i, e in enumerate(events) if e.get("type") == "tool_result"
               or (e.get("type") == "assistant" and any(isinstance(c, dict) and c.get("type") == "tool_result"
                                                         for c in e.get("content") or [])))
    assert res < at < [e.get("type") for e in events].index("result")


def test_a_wrapper_that_dispatches_two_tasks_yields_two_cards():
    """Even when its result is an error (the wrapper failed after sending both)."""
    _send(D1, title="check A")
    _send(D2, title="check B")
    events = _drive([_bash("tu1", WRAPPER), _res("tu1", "Exit code 1\nboom", is_error=True)])
    assert [(b["dispatchId"], b["title"]) for b in _cards(events)] == [(D1, "check A"), (D2, "check B")]


def test_an_old_dispatch_id_echoed_later_yields_no_card():
    """A dispatch that has its card: a later command printing its id or its row (the
    literal path included) draws none. A row sent before the call started draws none
    either, though nothing drew it."""
    _send(D1)
    assert _ids(_drive([_bash("tu1", WRAPPER), _res("tu1", "")])) == [D1]
    later = _drive([_bash("tu2", f"echo {D1}"), _res("tu2", D1)])
    assert _cards(later) == []
    reprint = _drive([_bash("tu3", CMD), _res("tu3", json.dumps({**ROW, "dispatch_id": D1}, indent=2))])
    assert _cards(reprint) == []
    _send(D2, dt=-60)
    assert _cards(_drive([_bash("tu4", f"echo {D2}"), _res("tu4", D2)])) == []


def test_rows_of_another_session_or_before_the_call_draw_nothing():
    _send(D1, sid="sess-other")
    _send(D2, dt=-60)
    _send(D3, sid=None)
    assert _cards(_drive([_bash("tu1", WRAPPER), _res("tu1", "")])) == []


def test_a_literal_dispatch_still_yields_exactly_one_card():
    """Printed row, output to a file, or a loop with expanded titles: one card per
    dispatch, the literal path's; the sweep adds none."""
    row = _send(D1, title="an earlier task")    # the redirected call below names ROW's title
    events = _drive([_bash("tu1", CMD), _res("tu1", json.dumps(row, indent=2))])
    assert _ids(events) == [D1]
    _send(D2)
    events = _drive([_bash("tu2", CMD + " > /tmp/d.json 2>&1"), _res("tu2", "")])
    assert _ids(events) == [D2]
    _send(D3, title="check A")
    d4 = "so_" + "d" * 12
    _send(d4, title="check B")
    loop = ('for n in A B; do "$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --brain $n '
            '--title "check $n" --task x | tail -1; done')
    assert _ids(_drive([_bash("tu3", loop), _res("tu3", "ok\nok\n")])) == [D3, d4]


def test_a_not_sure_literal_dispatch_keeps_its_one_card():
    """A dispatch the Hive may have taken (``maybe_sent``) wrote a session-less row: the
    call keeps today's one "not sure" card and the row is not drawn as sent as well. A
    refusal stays one refusal card."""
    _send(D1, session_id=None)
    out = '{"ok": false, "maybe_sent": true, "detail": "no answer in time"}'
    (b,) = _cards(_drive([_bash("tu1", CMD), _res("tu1", "Exit code 1\n" + out, is_error=True)]))
    assert (b["ok"], b["maybeSent"], b["dispatchId"]) == (False, True, None)
    assert _cards(_drive([_bash("tu2", "ls"), _res("tu2", "")])) == []
    refused = '{"ok": false, "refused": true, "detail": "the spec did not pass"}'
    (r,) = _cards(_drive([_bash("tu3", CMD), _res("tu3", "Exit code 1\n" + refused, is_error=True)]))
    assert (r["ok"], r["refused"]) == (False, True)


def test_parallel_calls_draw_one_card_per_dispatch():
    """Two calls in one step whose windows both hold the row: whichever result comes
    first draws it, the other draws nothing; the literal call's own card is dropped when
    an overlapping call drew it."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    def step(a, b):
        return AssistantMessage(content=[ToolUseBlock(id=a[0], name="Bash", input={"command": a[1]}),
                                         ToolUseBlock(id=b[0], name="Bash", input={"command": b[1]})], model="m")
    _send(D1)
    events = _drive([step(("tu1", WRAPPER), ("tu2", "ls")), _res("tu2", ""), _res("tu1", "")])
    assert _ids(events) == [D1]
    row = _send(D2)
    events = _drive([step(("tu3", CMD), ("tu4", "ls")), _res("tu4", ""), _res("tu3", json.dumps(row, indent=2))])
    assert _ids(events) == [D2]


def test_a_failed_draw_is_drawn_at_the_next_result(monkeypatch):
    """The log could not be read when a call's rows were claimed: they stay claimed (no
    other call draws them) and the session's next Bash result draws them, whatever its
    window; a Swarm Run child's run card too, with its own call's first-children check."""
    _send(D1)
    _send(D2, parent_run="run_abcdef12", wp="W1", project="SwarmAI")

    def unreadable(*_a):
        raise OSError("log unreadable")
    real = swarm_out_card.cards_of_claimed
    monkeypatch.setattr(swarm_out_card, "cards_of_claimed", unreadable)
    events = _drive([_bash("tu1", WRAPPER), _res("tu1", "")])
    assert _cards(events) == [] and _run_cards(events) == []
    assert not index.mark(SID, D1)                   # still claimed: nothing else draws it
    monkeypatch.setattr(swarm_out_card, "cards_of_claimed", real)
    later = time.time() + 3600                        # a call long after: outside the row's window
    ids, runs = index.claim(SID, later, later)
    assert ids == [D1] and [r for r, _ in runs] == ["run_abcdef12"]
    index.undrawn(SID, ids, runs)
    events = _drive([_bash("tu2", "ls"), _res("tu2", "")])
    assert _ids(events) == [D1] and [b["runId"] for b in _run_cards(events)] == ["run_abcdef12"]
    assert _cards(_drive([_bash("tu3", "ls"), _res("tu3", "")])) == []


def test_a_swarm_run_child_dispatch_draws_its_run_card_once():
    """A Swarm Run's children sent through a wrapper draw the run's card once and no
    dispatch card per child; a later call sending more children draws none. A literal
    child dispatch keeps its run card, and the sweep adds nothing to it."""
    child = {"parent_run": "run_abcdef12", "wp": "W1", "project": "SwarmAI"}
    _send(D1, **child)
    _send(D2, **{**child, "wp": "W2"})
    events = _drive([_bash("tu1", "/tmp/run/children.sh"), _res("tu1", "")])
    assert _cards(events) == []
    assert [(b["runId"], b["project"]) for b in _run_cards(events)] == [("run_abcdef12", "SwarmAI")]
    _send(D3, **{**child, "wp": "W3"})
    events = _drive([_bash("tu2", "/tmp/run/children.sh W3"), _res("tu2", "")])
    assert _cards(events) == [] and _run_cards(events) == []

    other = {"parent_run": "run_0123abcd", "wp": "W1", "project": "SwarmAI"}
    _send("so_" + "e" * 12, **other)
    literal = '"$SWARM_BACKEND_BIN" pipeline-v1 child --project SwarmAI --run-id run_0123abcd dispatch W1'
    events = _drive([_bash("tu3", literal), _res("tu3", "{}")])
    assert _cards(events) == []
    assert [b["runId"] for b in _run_cards(events)] == ["run_0123abcd"]


@pytest.mark.asyncio
async def test_a_reload_shows_the_same_cards():
    """The cards a wrapper's result drew are persisted rows of the turn: the session's
    transcript (what a reload reads) holds each one, once."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.session_router import SessionRouter
    from core.session_unit import SessionState
    from database import db
    from tests.test_tool_call_leak_guard import _drive as drive, _make_orchestrator, _make_result_message

    sid = "s-dispatch-reload"
    _send(D1, sid="sess-test", title="check A")
    _send(D2, sid="sess-test", title="check B")
    events, _ = await drive(_make_orchestrator(), [_bash("tu1", WRAPPER), _res("tu1", ""), _make_result_message()])
    assert _ids(events) == [D1, D2]
    shown = [e for e in events if e.get("type") in ("assistant", "tool_result")]

    async def send(**kwargs):
        yield {"type": "session_start", "sessionId": kwargs.get("app_session_id")}
        for e in shown:
            yield e
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    router = SessionRouter(prompt_builder=pb, config=MagicMock())
    unit = router.get_or_create_unit(sid, "default")
    unit._client = object()
    unit._transition(SessionState.IDLE)
    with patch("core.session_unit.SessionUnit.send", side_effect=send):
        async for _ in router.run_conversation(agent_id="default", user_message="go",
                                               session_id=sid, client_id=f"local-{sid}"):
            pass
    rows = await db.messages.list_by_session(sid, include_pending=True)
    persisted = [b for r in rows for b in r["content"] if isinstance(b, dict) and b.get("type") == "swarm_out_dispatch"]
    assert [(b["dispatchId"], b["title"]) for b in persisted] == [(D1, "check A"), (D2, "check B")]


def test_the_per_result_cost_is_one_indexed_lookup(monkeypatch, capsys):
    """What every Bash result adds: for a session with no rows, one dict lookup; with
    1000 rows of earlier dispatches and nothing new, one indexed lookup and a window
    filter, under 50 microseconds, with no log read and no thread-pool hop."""
    for i in range(1000):
        index.record({"dispatch_id": f"so_{i:012x}", "dispatched_from": "sess-bench",
                      "sent_at": _at(-3600 + i), "title": "t"})
    n = 20000
    t0 = time.perf_counter()
    for _ in range(n):
        index.has("sess-none")
    skip_us = (time.perf_counter() - t0) / n * 1e6
    now = time.time()
    t0 = time.perf_counter()
    for _ in range(n):
        if index.has("sess-bench"):
            index.claim("sess-bench", now, now)
    lookup_us = (time.perf_counter() - t0) / n * 1e6
    print(f"\nper Bash result: no rows {skip_us:.3f} us; 1000 rows, nothing new {lookup_us:.3f} us")
    assert lookup_us < 50

    # through the orchestrator: no log read, no pool hop, for either session
    hops: list = []
    real = executors.run_in

    async def counted(pool, fn, *a, **k):
        hops.append(getattr(fn, "__name__", fn))
        return await real(pool, fn, *a, **k)

    def no_read():
        raise AssertionError("the dispatch log was read")
    monkeypatch.setattr(executors, "run_in", counted)
    monkeypatch.setattr(swarm_out, "_read_log", no_read)
    for i in range(3):
        index.record({"dispatch_id": f"so_{i + 5000:012x}", "dispatched_from": SID,
                      "sent_at": _at(-600), "title": "t"})
    events = _drive([_bash("tu1", "git status"), _res("tu1", "clean")])
    assert _cards(events) == [] and hops == []
