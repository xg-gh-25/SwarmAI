"""P0 (docs/design/chat-transcript-single-source.md §4): the real send path, real DB.

Real ``SessionRouter.run_conversation`` against the conftest SQLite test database
(real migrations), with only the CLI replaced: ``SessionUnit.send`` is a scripted
async generator. The reload side is the real ``routers.chat.get_session_messages``.

Properties (each states the invariant; the ones today's code breaks are
``xfail(strict=True)`` naming the phase that fixes them — strict, so the test
turns red the moment the fix lands and the marker must be removed):

- one send, one user row; a normal turn's reload is its rows (baseline);
- the same client_id sent twice → still one user row (S2 / P1);
- a SESSION_BUSY reply keeps the message pending; resending the same client_id
  adds nothing (S2 / P1);
- the first send of a new tab (no session yet) retried with the same client_id
  → one session, one user row (S2 / P1);
- a channel turn retried with the same platform message → one user row (S2 / P1);
- history paged by turns, all pages joined == the whole history (S1 paging).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.exceptions import SessionBusyError
from core.session_router import SessionRouter
from core.session_unit import SessionState


def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


def _script(text: str):
    """unit.send as the CLI would answer one user message with one text."""
    async def send(**kwargs: Any):
        yield {"type": "session_start", "sessionId": kwargs.get("app_session_id")}
        yield {"type": "text_delta", "text": text, "index": 0}
        yield {"type": "assistant", "content": [{"type": "text", "text": text}], "model": "test"}
        yield {"type": "result", "sessionId": kwargs.get("app_session_id"), "durationMs": 1}
    return send


def _busy(**kwargs: Any):
    async def gen():
        raise SessionBusyError(detail="actively streaming")
        yield  # pragma: no cover
    return gen()


async def _send(router, sid, text, cid, *, send=None, channel=None):
    unit = router.get_or_create_unit(sid, "default") if sid else None
    if unit is not None and unit.state != SessionState.IDLE:
        unit._transition(SessionState.IDLE)
    events = []
    with patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock,
               return_value={"model": "test"}), \
            patch("core.session_unit.SessionUnit.send", side_effect=send or _script(f"reply to {text}"),
                  autospec=False):
        async for ev in router.run_conversation(agent_id="default", user_message=text, session_id=sid,
                                                client_id=cid, channel_context=channel):
            events.append(ev)
    return events


async def _user_rows(sid: str) -> list[dict]:
    from database import db
    rows = await db.messages.list_by_session(sid, include_pending=True)
    return [r for r in rows if r.get("role") == "user"]


async def _history(sid: str, **kw) -> list[dict]:
    import routers.chat as chat_router
    req = MagicMock()
    req.headers = {}
    import database
    with patch.object(chat_router, "db", database.db), \
            patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())):
        resp = await chat_router.get_session_messages(sid, req, limit=None,
                                                      before_id=kw.get("before_id"), turns=kw.get("turns"))
    return json.loads(resp.body)


# ── baseline ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_send_one_user_row_and_its_reply():
    r = _router()
    await _send(r, "s-one", "hello", "local-1")
    users = await _user_rows("s-one")
    assert [u["content"][0]["text"] for u in users] == ["hello"]
    hist = await _history("s-one")
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[1]["content"][-1]["text"] == "reply to hello"


# ── S2 / P1: the same client_id is one message ─────────────────────────────────

P1 = pytest.mark.xfail(strict=True, reason="RC2: send is not idempotent — fixed by S2 / P1 (send_requests)")


@P1
@pytest.mark.asyncio
async def test_same_client_id_twice_is_one_user_row():
    r = _router()
    await _send(r, "s-dup", "once", "local-dup")
    await _send(r, "s-dup", "once", "local-dup")
    assert len(await _user_rows("s-dup")) == 1


@P1
@pytest.mark.asyncio
async def test_resend_after_session_busy_adds_nothing():
    r = _router()
    ev = await _send(r, "s-busy", "wait for me", "local-b", send=_busy)
    assert any(e.get("code") == "SESSION_BUSY" for e in ev)
    assert len(await _user_rows("s-busy")) == 1          # kept, as pending
    await _send(r, "s-busy", "wait for me", "local-b", send=_busy)   # the retry
    assert len(await _user_rows("s-busy")) == 1


@P1
@pytest.mark.asyncio
async def test_first_send_retried_before_a_session_exists_is_one_session():
    from database import db
    r = _router()
    a = await _send(r, None, "brand new", "local-first")
    b = await _send(r, None, "brand new", "local-first")
    sids = {e.get("sessionId") for e in a + b if e.get("type") == "session_start"}
    assert len(sids) == 1
    rows = await db.messages.list_by_session(sids.pop(), include_pending=True)
    assert len([x for x in rows if x["role"] == "user"]) == 1


@P1
@pytest.mark.asyncio
async def test_channel_turn_retried_is_one_user_row():
    r = _router()
    ctx = {"channel_id": "C1", "channel_type": "slack", "external_message_id": "C1:1729600000.0001"}
    await _send(r, "s-chan", "from slack", None, channel=ctx)
    await _send(r, "s-chan", "from slack", None, channel=ctx)
    assert len(await _user_rows("s-chan")) == 1


# ── S1 paging ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pages_by_turns_join_to_the_whole_history():
    r = _router()
    for i in range(7):
        await _send(r, "s-page", f"q{i}", f"local-p{i}")
    whole = await _history("s-page")
    pages, before = [], None
    for _ in range(10):
        page = await _history("s-page", turns=2, before_id=before)
        if not page:
            break
        pages = page + pages
        before = page[0]["id"]
    strip = lambda ms: [(m["role"], [b.get("text") for b in m["content"]]) for m in ms]
    assert strip(pages) == strip(whole)


# ── R10 (fixed: core/turn_writer): a result shown live is kept when the turn ends right after it ──

def _tool_then_stop(**kwargs: Any):
    """The CLI ran a step and the turn ends there (Stop) — no result event."""
    async def gen():
        yield {"type": "session_start", "sessionId": kwargs.get("app_session_id")}
        yield {"type": "assistant", "model": "test", "content": [
            {"type": "tool_use", "id": "toolu_r10", "name": "Bash", "summary": "Running: ls"}]}
        yield {"type": "tool_result", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_r10", "content": "out", "is_error": False}]}
    return gen()


@pytest.mark.asyncio
async def test_a_result_shown_live_survives_a_turn_that_ends_after_it():
    r = _router()
    events = await _send(r, "s-r10", "list it", "local-r10", send=_tool_then_stop)
    assert any(e.get("type") == "tool_result" for e in events)        # shown live
    blocks = [b for m in await _history("s-r10") for b in m["content"]]
    assert any(b.get("type") == "tool_result" and b.get("tool_use_id") == "toolu_r10" for b in blocks)
