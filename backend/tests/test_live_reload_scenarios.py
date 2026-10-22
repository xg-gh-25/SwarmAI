"""Live-vs-reload scenarios: the backend half of the "live == reload" property.

Generates seeded random chat scenarios and runs each through the REAL backend
seams a turn goes through:

- persistence: ``SessionRouter._persist_stream_event`` / ``_persist_assistant_blocks``
  (rows captured by a recording ``db.messages``);
- publication: ``SessionRouter._published_turn`` on a real ``SessionEventHub``
  (the envelopes a subscribed tab receives: turn_start / events / turn_end);
- the CLI-initiated turn: the real ``run_injected_turn`` (background_finished
  block included);
- continuations: the real ``continue_with_answer`` / ``continue_with_cmd_permission``
  (the latter records the decision through ``core.permission_prompt``);
- reload: the real ``routers.chat.get_session_messages`` over the stored rows.

A chat turn's own entry point (``run_conversation``) needs a live CLI, so it is
mirrored: user row with the turn's client_id, events through ``_published_turn``
(start on session_start) and ``_persist_stream_event(client_id=<cid>-asst)``,
exactly the calls run_conversation makes.

Output: ``desktop/src/__tests__/fixtures/liveReload/scenarios.json`` — per scenario
the ordered steps a tab sees (its own user bubbles, its permission decisions and
answers, and every published envelope) plus the history JSON a reload returns.
The frontend test (``liveReload.property.test.ts``) replays the steps through the
real lifecycle and compares what it rendered with the reload.

Ids are made deterministic (sequential uuids, a fixed clock) so the fixture is
reproducible: ``test_fixture_is_current`` fails when backend behaviour changed and
the fixture was not regenerated (``LIVE_RELOAD_UPDATE=1``).
"""
from __future__ import annotations

import itertools
import json
import os
import random
import uuid
from contextlib import aclosing
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import session_events
from core.background_agents import BackgroundAgents
from core.session_router import SessionRouter
from core.session_unit import SessionState

FIXTURE = (Path(__file__).resolve().parents[2] / "desktop" / "src" / "__tests__"
           / "fixtures" / "liveReload" / "scenarios.json")
SEED = 20261022
N_SCENARIOS = 40
SID = "sess-lr"

WORDS = ("checked the gate", "found two issues", "the test is red", "patched the parser",
         "all green", "looking at the log", "reading session_router", "agent three is back")


# ── deterministic ids / clock ────────────────────────────────────────────────

class _Ids:
    def __init__(self) -> None:
        self.n = itertools.count(1)

    def uuid4(self) -> uuid.UUID:
        return uuid.UUID(int=(next(self.n) * 0x9E3779B97F4A7C15F39CC0605CEDC835) % (1 << 128))


class _Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 22, 12, 0, 0)

    def now(self) -> datetime:
        self.t += timedelta(milliseconds=10)
        return self.t


# ── recording db ─────────────────────────────────────────────────────────────

class _Messages:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def put(self, row: dict) -> None:
        self.rows.append(json.loads(json.dumps(row)))

    async def count_by_session(self, sid: str) -> int:
        return len(self.rows)

    async def list_by_session(self, sid: str) -> list[dict]:
        return json.loads(json.dumps(self.rows))

    async def update_permission_decision(self, sid: str, request_id: str, decision: str) -> bool:
        for row in self.rows:
            for b in row.get("content") or []:
                if b.get("type") == "cmd_permission_request" and b.get("requestId") == request_id \
                        and not b.get("decision"):
                    b["decision"] = decision
                    return True
        return False

    async def update_agent_result(self, *a, **k) -> bool:
        return False


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
        last = turns[-1] if turns else None
        kind = "chat" if i == 0 or rng.random() < 0.5 else "injected"
        turn = {"kind": kind, "steps": _items(rng, ids),
                "bg": kind == "injected" and rng.random() < 0.7, "pause": None}
        if rng.random() < 0.3:
            turn["pause"] = rng.choice(("question", "permission"))
        turns.append(turn)
        if turn["pause"] and i < n - 1:
            cont = {"kind": "answer" if turn["pause"] == "question" else "permission",
                    "steps": _items(rng, ids), "pause": None,
                    "decision": rng.choice(("approve", "deny"))}
            turns.append(cont)
        del last
    return turns


# ── running one scenario through the real backend ────────────────────────────

def _pause_event(turn: dict, n: int) -> dict:
    if turn["pause"] == "question":
        return {"type": "ask_user_question", "toolUseId": f"toolu_q{n}", "sessionId": SID,
                "questions": [{"question": "Which one?", "header": "Pick", "multiSelect": False,
                               "options": [{"label": "A", "description": "a"},
                                           {"label": "B", "description": "b"}]}]}
    return {"type": "cmd_permission_request", "requestId": f"perm_{n}", "sessionId": SID,
            "toolName": "Bash", "toolInput": {"command": "make clean"}, "reason": "risky",
            "options": ["approve", "deny"], "toolUseId": f"toolu_p{n}"}


def _frames(turn: dict, n: int, *, session_start: bool):
    evs: list[dict] = []
    if session_start:
        evs.append({"type": "session_start", "sessionId": SID})
    for step in turn["steps"]:
        evs.extend(json.loads(json.dumps(step)))
    if turn["pause"]:
        evs.append(_pause_event(turn, n))
    else:
        evs.append({"type": "result", "sessionId": SID, "durationMs": 1, "totalCostUsd": 0})
    return evs


async def _run(turns: list[dict], hub, messages: "_Messages") -> tuple[list[dict], set[str]]:
    router = SessionRouter(prompt_builder=MagicMock())
    unit = SimpleNamespace(session_id=SID, background_agents=BackgroundAgents(), _turn_changes=None,
                           _turn_files={}, state=SessionState.IDLE, _turn_client_id=None)
    router.get_unit = lambda sid: unit  # type: ignore[method-assign]
    steps: list[dict] = []
    seen = 0
    waiting: set[str] = set()
    pending_q: dict | None = None
    pending_p: dict | None = None

    def collect():
        nonlocal seen
        log = hub._logs.get(SID)
        buf = list(log.buf) if log else []
        for env in buf[seen:]:
            steps.append({"kind": "event", "env": json.loads(json.dumps(env))})
        seen = len(buf)

    for n, turn in enumerate(turns, 1):
        frames = _frames(turn, n, session_start=turn["kind"] == "chat")
        if turn["kind"] == "chat":
            cid = f"local-{n}"
            text = f"user message {n}"
            steps.append({"kind": "user", "clientId": cid, "text": text})
            await messages.put({"id": str(uuid.uuid4()), "session_id": SID, "role": "user",
                                "content": [{"type": "text", "text": text}],
                                "created_at": __import__("core.session_router", fromlist=["x"]).datetime.now().isoformat(),
                                "metadata": {"client_id": cid}})
            unit._turn_client_id = cid

            async def chat_turn(frames=frames, cid=cid):
                pending: list[dict] = []
                for ev in frames:
                    for extra in await router._persist_stream_event(
                            SID, ev, pending, client_id=f"{cid}-asst",
                            files=router._turn_files_of(unit), changes=None):
                        yield extra
                    yield ev
            async with aclosing(router._published_turn(
                    chat_turn(), session_id=SID, turn_client_id=f"{cid}-asst", source="chat",
                    start_on_session_start=True)) as t:
                async for _ in t:
                    pass
        elif turn["kind"] == "injected":
            if turn["bg"]:
                unit.background_agents.launch(f"toolu_agent{n}", f"audit part {n}")
                unit.background_agents.task_started(f"task{n}", f"toolu_agent{n}")
                unit.background_agents.task_finished(f"task{n}", "completed", tool_use_id=f"toolu_agent{n}")

            def begin(ts, client_id):
                unit._turn_client_id = client_id
            unit.begin_injected_turn = begin

            async def read(frames=frames):
                for ev in frames:
                    yield ev
            unit._read_formatted_response = read
            await router.run_injected_turn(unit, object())
        elif turn["kind"] == "answer":
            answers = {"Which one?": "A"}
            steps.append({"kind": "answer", "toolUseId": pending_q["toolUseId"], "answers": answers})

            async def cont(answer, tool_use_id=None, answered_by="person", frames=frames):
                for ev in frames:
                    yield ev
            unit.continue_with_answer = cont
            async for _ in router.continue_with_answer(SID, json.dumps(answers), tool_use_id=pending_q["toolUseId"]):
                pass
            pending_q = None
        else:  # permission continuation
            rid = pending_p["requestId"]
            steps.append({"kind": "decide", "requestId": rid, "decision": turn["decision"]})

            async def contp(request_id, allowed, frames=frames):
                # as SessionUnit._deliver_permission_decision does when the hook
                # waits on the prompt (aa491b97): the decision onto the stored block
                from core.permission_prompt import record_decision
                await record_decision(SID, request_id, "approve" if allowed else "deny")
                for ev in frames:
                    yield ev
            unit.continue_with_permission = contp
            async for _ in router.continue_with_cmd_permission(SID, rid, turn["decision"] == "approve"):
                pass
            waiting.discard(rid)
            pending_p = None
        collect()
        if turn["pause"] == "permission":
            waiting_now = {_pause_event(turn, n)["requestId"]}
        else:
            waiting_now = set(waiting)
        # the turn-end reconcile a tab runs: the DB as it is right after this turn
        steps.append({"kind": "reconcile", "history": await _history(messages, waiting_now)})
        if turn["pause"] == "question":
            pending_q = _pause_event(turn, n)
        elif turn["pause"] == "permission":
            pending_p = _pause_event(turn, n)
            waiting = {pending_p["requestId"]}
    return steps, waiting


async def _history(messages: _Messages, live: set[str]) -> list[dict]:
    import routers.chat as chat_router
    fake_db = MagicMock()
    fake_db.messages = messages
    fake_db.compaction_signature = AsyncMock(return_value="0.0")
    fake_db.list_compactions = AsyncMock(return_value=[])
    req = MagicMock()
    req.headers = {}
    with patch.object(chat_router, "db", fake_db), \
            patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())), \
            patch("core.permission_prompt.live_request_ids", lambda unit, sid: set(live)):
        resp = await chat_router.get_session_messages(SID, req, limit=None, before_id=None, turns=None)
    return json.loads(resp.body)


async def _generate() -> dict:
    rng = random.Random(SEED)
    out = []
    for i in range(N_SCENARIOS):
        turns = _scenario(rng)
        ids, clock = _Ids(), _Clock()
        hub = session_events.SessionEventHub(buffer_size=10_000, queue_size=10_000)
        messages = _Messages()
        import database
        dbmod = MagicMock()
        dbmod.messages = messages
        turn_ids = itertools.count(1)
        with patch.object(session_events, "_hub", hub), \
                patch.object(database, "db", dbmod), \
                patch("core.session_router.uuid4", ids.uuid4), \
                patch("uuid.uuid4", ids.uuid4), \
                patch("core.session_router.datetime", SimpleNamespace(now=clock.now)), \
                patch("routers.chat.datetime", SimpleNamespace(now=clock.now), create=True), \
                patch("core.background_agents._now_ms", lambda: int(clock.t.timestamp() * 1000)):
            # turn ids from _published_turn are uuid4().hex[:12]: deterministic via ids
            del turn_ids
            steps, live = await _run(turns, hub, messages)
            history = await _history(messages, live)
        out.append({"name": f"s{i:02d}", "shape": [t["kind"] + (f"/{t['pause']}" if t.get("pause") else "")
                                                   for t in turns],
                    "steps": steps, "history": history})
    return {"seed": SEED, "scenarios": out}


@pytest.mark.asyncio
async def test_fixture_is_current():
    data = await _generate()
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    if os.environ.get("LIVE_RELOAD_UPDATE") == "1" or not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, (
        "backend behaviour changed: regenerate with LIVE_RELOAD_UPDATE=1 and rerun "
        "desktop liveReload.property.test.ts")
    assert len(data["scenarios"]) == N_SCENARIOS
    assert all(s["history"] for s in data["scenarios"])
