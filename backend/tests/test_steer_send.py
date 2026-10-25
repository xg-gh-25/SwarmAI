"""D1 / D2 / D8: a message sent to a busy tab goes into the running turn (a steer).

Design docs/design/2026-10-24-chat-steer.md D1, D2, D5, D8 (T5, T8, T9, T14
router half, T18, T23, T25, T29). The real ``run_conversation`` admission path
(conftest SQLite): another turn of the session holds the hub, so the message
would be queued; with the switch on and the D1 conditions met it is written
into the running turn instead. A real SessionUnit with a real CliStreamReader
over a fake SDK client: its ``query`` records what reaches stdin, the frames the
test feeds are what the CLI echoes.

Each test fails with its branch reverted.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
import pytest_asyncio

from core import session_events, session_pending, session_state
from core.session_router import SessionRouter
from core.session_unit import SessionState
from tests.test_cli_stream_reader import FakeClient, _settle
from tests.test_cli_stream_reader_steer import _echo, _init, _state, _tool_result, _tool_use

U1 = "00000000-0000-4000-8000-0000000000c1"


class _SDK(FakeClient):
    """The fake SDK client: the frames the test feeds + what was written."""

    def __init__(self, fail: bool = False) -> None:
        super().__init__()
        self.written: list[dict] = []
        self.fail = fail

    async def query(self, prompt: Any, session_id: str = "default") -> None:
        if self.fail:
            raise RuntimeError("stdin closed")
        async for m in prompt:
            self.written.append(m)


@pytest.fixture(autouse=True)
def _switch(monkeypatch):
    monkeypatch.setattr(SessionRouter, "_steer_enabled", classmethod(lambda cls: True))
    async def _skip(self, session_id):
        return None
    monkeypatch.setattr(SessionRouter, "drain_pending", _skip)


@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def _finish_tasks():
    yield
    pend = [t for t in asyncio.all_tasks() if not t.done() and t is not asyncio.current_task()
            and any(k in getattr(t.get_coro(), "__qualname__", "") for k in ("publish", "_confirm_steer", "_release_steer_row"))]
    if pend:
        await asyncio.wait(pend, timeout=2)


def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


class _Busy:
    """A session whose user turn runs: STREAMING, its claim open in the reader."""

    def __init__(self, sid: str, *, fail: bool = False):
        self.sid = sid
        self.cid = f"c-{sid}"   # the send ledger is one table for every test: unique ids
        self.router = _router()
        self.unit = self.router.get_or_create_unit(sid, "default")
        self.sdk = _SDK(fail=fail)
        self.unit._client = self.sdk
        self.unit._start_stream_reader(self.sdk)
        self.reader = self.unit._stream_reader
        self.unit._transition(SessionState.IDLE)
        self.unit._transition(SessionState.STREAMING)
        self.unit._turn_stream = self.reader.claim(U1, "human")
        self.unit._steer_accepting = True
        assert session_events.hub().try_claim_turn(sid, "running-turn")

    async def open_turn(self):
        self.sdk.feed([_state("running"), _init(), _echo(U1), _tool_use("t1")])
        await _settle(self.sdk)

    async def send(self, cid=None, text="skip the second one", **kw) -> list[dict]:
        cid = cid or self.cid
        out = []
        async for ev in self.router.run_conversation(agent_id="default", user_message=text,
                                                     session_id=self.sid, client_id=cid, **kw):
            out.append(ev)
        return out

    async def row(self, cid=None) -> dict:
        cid = cid or self.cid
        from database import db
        rows = await db.messages.list_by_session(self.sid, include_pending=True)
        (r,) = [r for r in rows if r["role"] == "user" and (r.get("metadata") or {}).get("client_id") == cid]
        return r

    def close(self):
        self.reader.close()


# ── written into the running turn ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_busy_send_is_written_into_the_running_turn():
    b = _Busy("s-steer-ok")
    await b.open_turn()
    stream_before = b.unit._turn_stream
    events = await b.send()
    (receipt,) = events
    assert receipt["type"] == "accepted" and receipt["steer"] is True and receipt["queued"] is False
    (msg,) = b.sdk.written
    assert msg["priority"] == "next" and msg["uuid"] and msg["message"]["role"] == "user"
    assert "skip the second one" in json.dumps(msg["message"]["content"])
    assert b.unit._turn_stream is stream_before                         # T9
    r = await b.row()
    assert r["sent"] == 0 and (r.get("metadata") or {}).get("steer") == "inflight"
    # the snapshot lists it as being steered, not queued (D5)
    snap = await session_state.session_state_entry(b.unit)
    assert [s["client_id"] for s in snap.get("steering", [])] == [b.cid] and snap["pending"] == []

    # the CLI reads it at the tool result: history now, labelled "during"
    b.sdk.feed([_tool_result("t1"), _echo(msg["uuid"])])
    await _settle(b.sdk)
    await asyncio.sleep(0.05)
    r = await b.row()
    assert r["sent"] == 1 and r["metadata"].get("steered") == "during" and "steer" not in r["metadata"]
    assert b.unit._steer_rows == {}
    b.close()


@pytest.mark.asyncio
async def test_switch_off_queues_exactly_as_before(monkeypatch):
    monkeypatch.setattr(SessionRouter, "_steer_enabled", classmethod(lambda cls: False))
    b = _Busy("s-steer-off")
    await b.open_turn()
    events = await b.send()
    assert events[0]["queued"] is True and "steer" not in events[0] and "queuedReason" not in events[0]
    assert events[1]["code"] == "SESSION_BUSY"
    assert b.sdk.written == []
    b.close()


@pytest.mark.parametrize("why,setup", [
    ("busy", lambda u: setattr(u, "_steer_accepting", False)),          # the turn reads no more steers
    ("busy", lambda u: setattr(u._turn_stream, "kind", "injected")),     # a CLI-started turn
    ("busy", lambda u: u._stream_reader.claim("00000000-0000-4000-8000-0000000000d9", "leak")),
    ("question", lambda u: (setattr(u, "_pending_tool_use_id", "tq"),
                            setattr(u, "_pending_question", {"tool_use_id": "tq"}))),
    ("permission", lambda u: (setattr(u, "_pending_tool_use_id", "rq"),
                              setattr(u, "_pending_question", {"tool_use_id": "rq", "request_id": "rq"}))),
    ("busy", lambda u: setattr(u, "_stopping", True)),
    ("busy", lambda u: setattr(u, "_retrying", True)),
    ("busy", lambda u: setattr(u, "_tool_hang_interrupt_at", 1.0)),
])
@pytest.mark.asyncio
async def test_t5_each_condition_alone_queues_it_with_a_reason(why, setup):
    b = _Busy(f"s-steer-t5-{why}-{id(setup)}")
    await b.open_turn()
    setup(b.unit)
    events = await b.send()
    assert events[0]["queued"] is True and events[0]["queuedReason"] == why
    assert b.sdk.written == []
    b.close()


@pytest.mark.asyncio
async def test_a_channel_message_is_queued():
    b = _Busy("s-steer-channel")
    await b.open_turn()
    events = await b.send(channel_context={"channel_id": "C1", "channel_type": "slack"})
    assert events[0]["queued"] is True and b.sdk.written == []
    b.close()


@pytest.mark.asyncio
async def test_the_recheck_after_the_row_write_queues_it_when_the_turn_moved_on():
    """H4: the turn decides not to read on during the row's write (an await)."""
    b = _Busy("s-steer-recheck")
    await b.open_turn()
    real = session_pending.mark_steer_inflight

    async def mark(sid, mid):
        out = await real(sid, mid)
        b.unit._steer_accepting = False
        return out
    with patch.object(session_pending, "mark_steer_inflight", mark):
        events = await b.send()
    assert events[0]["queued"] is True and b.sdk.written == []
    assert b.reader.next_steer() is None
    r = await b.row()
    assert r["sent"] == 0 and r["pending_seq"] is not None and "steer" not in (r.get("metadata") or {})
    b.close()


@pytest.mark.asyncio
async def test_t8_a_failed_write_queues_it_and_withdraws_the_claim():
    b = _Busy("s-steer-t8", fail=True)
    await b.open_turn()
    events = await b.send()
    assert events[0]["queued"] is True and events[1]["code"] == "SESSION_BUSY"
    assert b.reader.next_steer() is None and b.unit._steer_rows == {}
    r = await b.row()
    assert r["sent"] == 0 and r["pending_seq"] is not None
    b.close()


# ── never read: back to the queue (D8, T25) ─────────────────────────────────

@pytest.mark.asyncio
async def test_t25_a_steer_the_process_never_read_goes_back_to_the_queue_and_kicks_the_drain():
    b = _Busy("s-steer-t25")
    await b.open_turn()
    await b.send()
    b.unit._last_ui_block = "## Current UI State\nsomething"
    kicked = []
    b.router.queued_while_busy = lambda sid: kicked.append(sid)
    b.reader.close()                        # the Stop's recycle / the process died
    await asyncio.sleep(0.05)
    r = await b.row()
    assert r["sent"] == 0 and r["pending_seq"] is not None and "steer" not in (r.get("metadata") or {})
    assert kicked == [b.sid] and b.unit._last_ui_block is None
    assert [p["client_id"] for p in await session_pending.list_pending(b.sid)] == [b.cid]


# ── read while the turn is being stopped (T29) ───────────────────────────────

@pytest.mark.asyncio
async def test_t29_a_steer_read_during_a_stop_is_placed_before_the_receipt():
    from database import db
    b = _Busy("s-steer-t29")
    await b.open_turn()
    await b.send()
    (msg,) = b.sdk.written
    receipt_rows = await b.router._persist_stop_receipt(b.sid, b.unit)
    assert receipt_rows
    b.sdk.feed([_tool_result("t1"), _echo(msg["uuid"])])    # the CLI read it during interrupt()
    await _settle(b.sdk)
    await asyncio.sleep(0.05)
    rows = await db.messages.list_by_session(b.sid, include_pending=True)
    kinds = [("steer" if r["role"] == "user" and (r.get("metadata") or {}).get("client_id") == b.cid
              else "receipt" if any(x.get("type") == "turn_stop" for x in r["content"]) else r["role"])
             for r in rows]
    assert kinds.index("steer") < kinds.index("receipt")
    b.close()


# ── what the CLI gets (T18) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_t18_a_steer_carries_the_terminal_and_ui_prefixes_and_no_recall():
    b = _Busy("s-steer-t18")
    await b.open_turn()
    b.unit._recall_query_block = "[RECALLED] something from the first message"
    events = await b.send(text="look at this error",
                          terminal_context={"buffer_tail": "Traceback: boom", "cwd": "/tmp"},
                          editor_context={"canvas": {"open": True, "tab": "Changes"}})
    assert events[0]["steer"] is True
    (msg,) = b.sdk.written
    text = json.dumps(msg["message"]["content"])
    assert "Traceback: boom" in text and "UNTRUSTED" in text.upper()
    assert "[RECALLED]" not in text
    assert text.index("Traceback") < text.index("look at this error")
    (prefix,) = b.unit._steer_query_prefixes
    assert "Traceback: boom" in prefix
    assert "Traceback: boom" in (b.unit.all_query_prefixes() or "")
    b.close()
