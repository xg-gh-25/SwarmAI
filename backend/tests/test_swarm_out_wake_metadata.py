"""The wake's row says it is a wake (design "the runs this session dispatched, visible in
the dispatching tab" r2 §3 ③, T4).

Each test answers one question:
- does a wake send its persisted row's metadata: ``origin: wake`` and, for each run it is
  about, the dispatch id, title and stage from this Host's snapshot;
- is that metadata merged into the user row only for a system turn, never over the
  client id, and never for the person's, a channel's or the Host's message;
- does the persisted user row of a system turn carry it (session_router's write);
- does the agent's Canvas line name the Runs tab.
"""
from __future__ import annotations

import asyncio

import pytest

from core import swarm_out, swarm_out_wake as wake
from core.prompt_builder import _render_ui_context_section
from core.session_router import user_row_metadata

SID = "s-wake-meta"
DID = "so_bbbbbbbbbbbb"


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


@pytest.fixture
def idle(monkeypatch, tmp_path):
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    from core import session_pending

    async def none(sid):
        return 0
    monkeypatch.setattr(session_pending, "count_pending", none)


def test_a_wake_sends_its_row_metadata(idle, monkeypatch):
    from core import swarm_out_follow, swarm_out_record
    run = {"dispatch_id": DID, "dispatched_from": SID, "stage": "awaiting", "state": "idle",
           "session_id": "hs1", "title": "H7 Fix login", "settle": {"why_not": ["verdict"]}}
    monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": [run], "asks": [], "answered": []})
    monkeypatch.setattr(swarm_out_record, "actionable", lambda r, asks: True)
    monkeypatch.setattr(swarm_out_record, "owner_alive", lambda r: True)
    monkeypatch.setattr(swarm_out_follow, "digest", lambda s: ("", {DID: "fp1"}))
    router = _Router()
    assert asyncio.run(wake.wake_sessions(router)) == [SID]
    sent = router.sent[-1]
    assert sent["input_origin"] == "system" and sent["user_message"].startswith(wake.MARKER)
    assert sent["row_metadata"] == {"origin": "wake", "wake": {
        "kind": "runs", "dispatches": [{"id": DID, "title": "H7 Fix login", "stage": "awaiting"}]}}


def test_wake_metadata_names_a_run_the_snapshot_lost_by_id():
    meta = wake.wake_metadata({"runs": []}, ["so_x"], "swarm_run")
    assert meta == {"origin": "wake", "wake": {"kind": "swarm_run",
                                               "dispatches": [{"id": "so_x", "title": None, "stage": None}]}}
    many = wake.wake_metadata({"runs": []}, [f"so_{i}" for i in range(50)], "runs")
    assert len(many["wake"]["dispatches"]) == wake.MAX_WAKE_DISPATCHES


def test_row_metadata_only_for_system_turns():
    meta = {"origin": "wake", "wake": {"kind": "runs", "dispatches": []}, "client_id": "forged"}
    assert user_row_metadata("c1", "system", meta) == {"client_id": "c1", "origin": "wake",
                                                       "wake": {"kind": "runs", "dispatches": []}}
    assert user_row_metadata("c1", "person", meta) == {"client_id": "c1"}
    assert user_row_metadata("c1", "host", meta) == {"client_id": "c1", "origin": "host"}
    assert user_row_metadata(None, "channel_peer", meta) is None
    assert user_row_metadata("c1", "system", None) == {"client_id": "c1"}
    assert user_row_metadata("c1", "system", "not a dict") == {"client_id": "c1"}
    # only the wake's own keys: no other origin, no other key
    assert user_row_metadata("c1", "system", {"origin": "host", "wake": {}}) == {"client_id": "c1"}
    assert user_row_metadata("c1", "system", {"origin": "wake", "steered": True}) == {"client_id": "c1", "origin": "wake"}


def test_the_canvas_line_names_the_runs_tab():
    canvas = {"open": True, "visible": "open", "tab": "runs", "list_folded": False, "pinned": False,
              "output_count": 0, "outputs": [], "file": None, "pending_open": None, "commands": []}
    out = _render_ui_context_section({"file_path": "", "file_name": "", "canvas": canvas})
    assert "- Canvas (output panel): open, Runs tab (the runs this session dispatched)" in out
    canvas["tab"] = "anything else"
    assert "open, Artifacts tab" in _render_ui_context_section({"file_path": "", "file_name": "", "canvas": canvas})


# ── the real write: the persisted (and published) user row carries it ──────────

def _real_router():
    from unittest.mock import AsyncMock, MagicMock
    import core.session_manager  # noqa: F401
    import routers.chat  # noqa: F401
    from core.session_router import SessionRouter
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


async def _one_turn(r, sid, cid, **kw):
    from unittest.mock import patch
    from core.session_unit import SessionState

    def send(**kwargs):
        async def g():
            yield {"type": "session_start", "sessionId": kwargs.get("app_session_id")}
            yield {"type": "assistant", "content": [{"type": "text", "text": "ok"}], "model": "m"}
            yield {"type": "result", "sessionId": kwargs.get("app_session_id")}
        return g()
    unit = r.get_or_create_unit(sid, "default")
    unit._client = object()
    if unit.state != SessionState.IDLE:
        unit._transition(SessionState.IDLE)
    with patch("core.session_unit.SessionUnit.send", side_effect=send):
        async for _ in r.run_conversation(agent_id="default", user_message=wake.WAKE_TEXT, session_id=sid,
                                          client_id=cid, **kw):
            pass


@pytest.mark.asyncio
async def test_the_persisted_user_row_carries_the_wake():
    from unittest.mock import patch
    from core import session_events
    from database import db
    hub = session_events.SessionEventHub()
    meta = wake.wake_metadata({"runs": [{"dispatch_id": DID, "title": "T", "stage": "awaiting"}]}, [DID], "runs")
    with patch.object(session_events, "_hub", hub):
        await _one_turn(_real_router(), "s-wake-row", "swarm-out-wake-1", input_origin="system", row_metadata=meta)
        await _one_turn(_real_router(), "s-wake-person", "local-p1", input_origin="person", row_metadata=meta)
    rows = [m for m in await db.messages.list_by_session("s-wake-row") if m.get("role") == "user"]
    assert rows and rows[0]["metadata"] == {"client_id": "swarm-out-wake-1", **meta}
    published = [e for e in hub._logs["s-wake-row"].buf if e["type"] == "transcript_row"]
    assert published[0]["row"]["metadata"]["origin"] == "wake"
    person = [m for m in await db.messages.list_by_session("s-wake-person") if m.get("role") == "user"]
    assert person and person[0]["metadata"] == {"client_id": "local-p1"}
