"""Live-vs-reload scenarios: the backend half of the "live == reload" property.

Generates seeded random chat scenarios and runs each through the REAL backend on
the REAL test database (conftest SQLite, real migrations):

- user turns: the real ``SessionRouter.run_conversation`` (user row, slot, pending
  conversion, ``_published_turn``, ``_persist_stream_event``) with only the CLI
  scripted (``SessionUnit.send``);
- SESSION_BUSY: ``send`` raises, run_conversation keeps the message pending; the
  real ``drain_pending`` later delivers every pending message in one drained turn;
- CLI-initiated turns: the real ``run_injected_turn`` on a real ``SessionUnit``
  (``background_finished`` from its real ``BackgroundAgents``);
- continuations: the real ``continue_with_answer`` / ``continue_with_cmd_permission``
  (the decision recorded as ``SessionUnit._deliver_permission_decision`` does);
- Stop: the real ``_persist_stop_receipt`` mid-turn (written, not published), the
  turn then ends without a result;
- compaction: a real ``insert_compaction`` row between turns;
- reload: the real ``routers.chat.get_session_messages``.

Output: ``desktop/src/__tests__/fixtures/liveReload/scenarios.json`` — per scenario
its session id, the ordered steps a tab sees (its own user bubbles and POST-only
replies, its decisions and answers, every published envelope, the turn-end
reconcile snapshots) and the history a reload returns. The frontend test
(``liveReload.property.test.ts``) replays them through the real lifecycle.

Deterministic (sequential uuids, a fixed clock, rows reduced to the fields the
history mapper reads), so ``test_fixture_is_current`` fails when backend behaviour
changed and the fixture was not regenerated (``LIVE_RELOAD_UPDATE=1``).
"""
from __future__ import annotations

import itertools
import json
import os
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Imported before anything patches database.db: both bind `db` at import time.
import core.session_manager  # noqa: F401
import routers.chat  # noqa: F401
from core import session_events
from core.exceptions import SessionBusyError
from core.session_router import SessionRouter
from core.session_unit import SessionState, SessionUnit

FIXTURE = (Path(__file__).resolve().parents[2] / "desktop" / "src" / "__tests__"
           / "fixtures" / "liveReload" / "scenarios.json")
SEED = 20261022
N_SCENARIOS = 40

WORDS = ("checked the gate", "found two issues", "the test is red", "patched the parser",
         "all green", "looking at the log", "reading session_router", "agent three is back")
ROW_FIELDS = ("id", "session_id", "role", "content", "model", "created_at", "metadata")


class _Ids:
    """Deterministic uuids, distinct per scenario: row ids are a primary key of the
    one test DB all scenarios share, so a counter restarting per scenario would
    overwrite earlier scenarios' rows."""

    def __init__(self, scenario: int = 0) -> None:
        self.n = itertools.count(1 + scenario * 1_000_000)

    def uuid4(self) -> uuid.UUID:
        return uuid.UUID(int=(next(self.n) * 0x9E3779B97F4A7C15F39CC0605CEDC835) % (1 << 128))


class _Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 22, 12, 0, 0)

    def now(self) -> datetime:
        self.t += timedelta(milliseconds=10)
        return self.t


# ── scenario model ───────────────────────────────────────────────────────────

def _items(rng: random.Random, ids) -> list[list[dict]]:
    """1-4 steps; each step is the events the orchestrator yields for it."""
    out = []
    for _ in range(rng.randint(1, 4)):
        kind = rng.choice(("text", "text", "thinking", "tool"))
        if kind == "text":
            t = rng.choice(WORDS) + f" #{rng.randint(1, 99)}"
            k = rng.randint(1, len(t) - 1)
            out.append([{"type": "text_delta", "text": t[:k], "index": 0},
                        {"type": "text_delta", "text": t[k:], "index": 0},
                        {"type": "assistant", "content": [{"type": "text", "text": t}], "model": "m"}])
        elif kind == "thinking":
            t = "think: " + rng.choice(WORDS)
            out.append([{"type": "thinking_delta", "thinking": t, "index": 0},
                        {"type": "assistant", "content": [{"type": "thinking", "thinking": t}], "model": "m"}])
        else:
            tid = f"toolu_{next(ids)}"
            err = rng.random() < 0.2
            out.append([{"type": "assistant", "model": "m", "content": [
                            {"type": "tool_use", "id": tid, "name": "Bash", "summary": "Running: ls",
                             "category": "bash"}]},
                        {"type": "tool_result", "content": [
                            {"type": "tool_result", "tool_use_id": tid, "content": "out", "is_error": err,
                             "truncated": False, "outcome": "failed" if err else "ok"}]}])
    return out


def _scenario(rng: random.Random) -> list[dict]:
    ids = itertools.count(1)
    turns: list[dict] = []
    n = rng.randint(2, 5)
    for i in range(n):
        r = rng.random()
        kind = ("chat" if i == 0 or r < 0.35 else "injected" if r < 0.6
                else "busy" if r < 0.75 else "stop" if r < 0.88 else "chat")
        turn = {"kind": kind, "steps": _items(rng, ids), "pause": None,
                "bg": kind == "injected" and rng.random() < 0.7,
                "queued": rng.randint(1, 2) if kind == "busy" else 0}
        if kind in ("chat", "injected") and rng.random() < 0.3 and i < n - 1:
            turn["pause"] = rng.choice(("question", "permission"))
        turns.append(turn)
        if turn["pause"]:
            turns.append({"kind": "answer" if turn["pause"] == "question" else "permission",
                          "steps": _items(rng, ids), "pause": None,
                          "decision": rng.choice(("approve", "deny"))})
        if rng.random() < 0.15:
            turns.append({"kind": "compaction"})
    return turns


def _pause_event(turn: dict, n: int, sid: str) -> dict:
    if turn["pause"] == "question":
        return {"type": "ask_user_question", "toolUseId": f"toolu_q{n}", "sessionId": sid,
                "questions": [{"question": "Which one?", "header": "Pick", "multiSelect": False,
                               "options": [{"label": "A", "description": "a"},
                                           {"label": "B", "description": "b"}]}]}
    return {"type": "cmd_permission_request", "requestId": f"perm_{n}", "sessionId": sid,
            "toolName": "Bash", "toolInput": {"command": "make clean"}, "reason": "risky",
            "options": ["approve", "deny"], "toolUseId": f"toolu_p{n}"}


def _frames(turn: dict, n: int, sid: str, *, session_start: bool, upto: int | None = None) -> list[dict]:
    evs: list[dict] = []
    if session_start:
        evs.append({"type": "session_start", "sessionId": sid})
    for step in turn["steps"][:upto]:
        evs.extend(json.loads(json.dumps(step)))
    if upto is not None:
        return evs                              # a stopped turn: no pause, no result
    if turn["pause"]:
        evs.append(_pause_event(turn, n, sid))
    else:
        evs.append({"type": "result", "sessionId": sid, "durationMs": 1, "totalCostUsd": 0})
    return evs


# ── running one scenario through the real backend ────────────────────────────

def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


def _reduce(row: dict) -> dict:
    """The fields the history mapper (toMessageCamelCase) reads; volatile DB columns
    (updated_at, expires_at, pending bookkeeping) are not part of the fixture."""
    if row.get("role") == "system":
        return {k: v for k, v in row.items() if k not in ("updated_at", "expires_at")}
    return {k: row[k] for k in ROW_FIELDS if k in row}


async def _history(router: SessionRouter, sid: str) -> list[dict]:
    import database
    import routers.chat as chat_router
    req = MagicMock()
    req.headers = {}
    with patch.object(chat_router, "db", database.db), \
            patch.object(chat_router, "_get_router", lambda: router), \
            patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())):
        resp = await chat_router.get_session_messages(sid, req, limit=None, before_id=None, turns=None)
    return [_reduce(r) for r in json.loads(resp.body)]


def _scripted(unit: SessionUnit, frames: list[dict], *, pause: dict | None, after=None):
    """The CLI's events for one turn, with the state changes the orchestrator makes."""
    async def gen(*_a, **_k):
        if unit.state != SessionState.STREAMING:
            unit._transition(SessionState.STREAMING)
        for ev in frames:
            if pause is not None and ev is frames[-1]:
                key = "request_id" if ev["type"] == "cmd_permission_request" else "tool_use_id"
                unit._pending_question = {key: ev.get("requestId") or ev.get("toolUseId")}
                unit._transition(SessionState.WAITING_INPUT)
            yield ev
        if after is not None:
            await after()
        if pause is None and unit.state == SessionState.STREAMING:
            unit._transition(SessionState.IDLE)
    return gen


def _busy_send(**_k):
    async def gen():
        raise SessionBusyError(detail="actively streaming")
        yield  # pragma: no cover
    return gen()


async def _run(turns: list[dict], sid: str, hub) -> tuple[list[dict], SessionRouter]:
    from database import db
    router = _router()
    unit = router.get_or_create_unit(sid, "default")
    unit._client = object()
    unit._transition(SessionState.IDLE)
    steps: list[dict] = []
    seen = 0
    pending_q = pending_p = None

    def collect():
        nonlocal seen
        log = hub._logs.get(sid)
        buf = list(log.buf) if log else []
        for env in buf[seen:]:
            steps.append({"kind": "event", "env": json.loads(json.dumps(env))})
        seen = len(buf)

    async def chat(cid: str, text: str, gen) -> list[dict]:
        out = []
        with patch.object(SessionUnit, "send", side_effect=gen):
            async for ev in router.run_conversation(agent_id="default", user_message=text,
                                                    session_id=sid, client_id=cid):
                out.append(ev)
        return out

    for n, turn in enumerate(turns, 1):
        kind = turn["kind"]
        pause = _pause_event(turn, n, sid) if turn.get("pause") else None
        if kind == "chat":
            cid, text = f"local-{n}", f"user message {n}"
            steps.append({"kind": "user", "clientId": cid, "text": text})
            await chat(cid, text, _scripted(unit, _frames(turn, n, sid, session_start=True), pause=pause))
        elif kind == "stop":
            cid, text = f"local-{n}", f"user message {n}"
            steps.append({"kind": "user", "clientId": cid, "text": text})
            receipt: list[dict] = []

            async def press_stop():
                # /chat/stop: the receipt is persisted (not published) and returned
                receipt.extend(await router._persist_stop_receipt(sid, unit))
            upto = max(1, len(turn["steps"]) - 1)
            await chat(cid, text, _scripted(unit, _frames(turn, n, sid, session_start=True, upto=upto),
                                            pause=None, after=press_stop))
            collect()
            steps.append({"kind": "stop", "receipt": json.loads(json.dumps(receipt))})
        elif kind == "busy":
            for k in range(turn["queued"]):
                cid, text = f"local-{n}-{k}", f"queued message {n}.{k}"
                post = await chat(cid, text, _busy_send)
                steps.append({"kind": "user", "clientId": cid, "text": text,
                              "post": json.loads(json.dumps(post))})
            frames = _frames(turn, n, sid, session_start=True)
            with patch.object(SessionUnit, "send", side_effect=_scripted(unit, frames, pause=None)), \
                    patch.object(SessionUnit, "cli_quiescent", property(lambda _s: True)):
                await router.drain_pending(sid)
        elif kind == "injected":
            if turn["bg"]:
                unit.background_agents.launch(f"toolu_agent{n}", f"audit part {n}")
                unit.background_agents.task_started(f"task{n}", f"toolu_agent{n}")
                unit.background_agents.task_finished(f"task{n}", "completed", tool_use_id=f"toolu_agent{n}")
            unit._read_formatted_response = _scripted(unit, _frames(turn, n, sid, session_start=False),
                                                      pause=pause)
            await router.run_injected_turn(unit, object())
        elif kind == "answer":
            answers = {"Which one?": "A"}
            steps.append({"kind": "answer", "toolUseId": pending_q["toolUseId"], "answers": answers})
            unit._pending_question = None
            unit.continue_with_answer = _scripted(unit, _frames(turn, n, sid, session_start=False), pause=None)
            async for _ in router.continue_with_answer(sid, json.dumps(answers),
                                                       tool_use_id=pending_q["toolUseId"]):
                pass
            pending_q = None
        elif kind == "permission":
            rid = pending_p["requestId"]
            steps.append({"kind": "decide", "requestId": rid, "decision": turn["decision"]})
            inner = _scripted(unit, _frames(turn, n, sid, session_start=False), pause=None)

            def contp(request_id, allowed, inner=inner):
                async def gen():
                    # as SessionUnit._deliver_permission_decision (note now, record after)
                    from core.permission_prompt import note_decision, record_decision
                    d = "approve" if allowed else "deny"
                    note_decision(request_id, d)
                    unit._pending_question = None
                    await record_decision(sid, request_id, d)
                    async for ev in inner():
                        yield ev
                return gen()
            unit.continue_with_permission = contp
            async for _ in router.continue_with_cmd_permission(sid, rid, turn["decision"] == "approve"):
                pass
            pending_p = None
        elif kind == "compaction":
            from core.compact_summary import build_record
            await db.insert_compaction(sid, build_record({"trigger": "auto"}, f"summary after {n - 1} turns"))
        if kind != "stop":
            collect()
        if pause is not None:
            if turn["pause"] == "question":
                pending_q = pause
            else:
                pending_p = pause
        # the turn-end reconcile a tab runs: the DB as it is right after this step
        steps.append({"kind": "reconcile", "history": await _history(router, sid)})
    return steps, router


async def _generate() -> dict:
    rng = random.Random(SEED)
    out = []
    for i in range(N_SCENARIOS):
        turns = _scenario(rng)
        sid = f"sess-lr-{i:02d}"
        ids, clock = _Ids(i), _Clock()
        hub = session_events.SessionEventHub(buffer_size=10_000, queue_size=10_000)
        stamp = SimpleNamespace(now=clock.now, fromisoformat=datetime.fromisoformat)
        with patch.object(session_events, "_hub", hub), \
                patch("core.session_router.uuid4", ids.uuid4), \
                patch("uuid.uuid4", ids.uuid4), \
                patch("core.session_router.datetime", stamp), \
                patch("core.compact_summary.datetime", stamp), \
                patch("core.turn_stop.datetime", SimpleNamespace(now=lambda tz=None: clock.now())), \
                patch("core.background_agents._now_ms", lambda: int(clock.t.timestamp() * 1000)):
            steps, router = await _run(turns, sid, hub)
            history = await _history(router, sid)
        out.append({"name": f"s{i:02d}", "sid": sid,
                    "shape": [t["kind"] + (f"/{t['pause']}" if t.get("pause") else "")
                              + (f"x{t['queued']}" if t.get("queued") else "") for t in turns],
                    "steps": steps, "history": history})
    return {"seed": SEED, "scenarios": out}


@pytest.mark.asyncio
async def test_fixture_is_current():
    data = await _generate()
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    if os.environ.get("LIVE_RELOAD_UPDATE") == "1" or not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text, encoding="utf-8")
    current = FIXTURE.read_text(encoding="utf-8")
    # compare by hash: a failing assert on two 1 MB strings would diff for minutes
    assert hash(current) == hash(text), (
        "backend behaviour changed: regenerate with LIVE_RELOAD_UPDATE=1 and rerun "
        "desktop liveReload.property.test.ts")
    assert len(data["scenarios"]) == N_SCENARIOS
    assert all(s["history"] for s in data["scenarios"])
