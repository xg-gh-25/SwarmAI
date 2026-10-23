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
- reload: the real ``routers.chat.get_session_messages``;
- a hard crash (scenarios after the connection ones): a restart mid-turn whose
  cancelled turn writes nothing more — what a SIGKILL leaves is what the turn had
  written when it published its last event;
- connection loss (scenarios ``N_SCENARIOS`` on): the window's subscription drops
  mid-turn and reconnects mid-turn or after the turn (``gap`` / ``gap_late``), with
  the real ``SessionEventHub.subscribe`` answering the client's cursor (a replay
  from the buffer, or — scenarios with a short buffer — a ``resync``); and a daemon
  restart mid-turn or between turns (``restart`` / ``restart_idle``): the turn's
  task is cancelled as the server's shutdown cancels it, a new hub (new epoch) and
  a new router take over, and the client's stale cursor gets the hub's ``resync``.

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

import asyncio
import contextlib
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
#: further scenarios with connection loss / daemon restarts (drawn after the first
#: N_SCENARIOS, so those stay byte-identical)
N_NET_SCENARIOS = 30
#: then scenarios with a HARD crash (SIGKILL): like a restart mid-turn, but nothing
#: of the dying process's clean-up reaches the DB (no finally block writes)
N_CRASH_SCENARIOS = 10
NET_KINDS = ("gap", "gap_late", "restart", "restart_idle")

WORDS = ("checked the gate", "found two issues", "the test is red", "patched the parser",
         "all green", "looking at the log", "reading session_router", "agent three is back")
ROW_FIELDS = ("id", "session_id", "role", "content", "model", "created_at", "metadata", "row_revs")


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


def _crash(rng: random.Random, turns: list[dict]) -> int:
    """One turn ends in a hard crash, right after a tool result when it has one."""
    able = [i for i, t in enumerate(turns)
            if t["kind"] in ("chat", "injected", "busy", "answer", "permission")
            and not t.get("pause") and len(t["steps"]) >= 2]
    if able:
        i = rng.choice(able)
        n = len(turns[i]["steps"])
        after_tool = [k for k in range(1, n) if turns[i]["steps"][k - 1][-1]["type"] == "tool_result"]
        turns[i]["net"] = {"kind": "crash", "at": rng.choice(after_tool or list(range(1, n))), "back": None}
    return 10_000


def _net(rng: random.Random, turns: list[dict]) -> int:
    """Give 1-2 turns a connection event (``turn["net"]``); returns the hub's
    per-session buffer size: a short one makes a reconnect's cursor fall out of the
    buffer, so the hub answers with a ``resync`` instead of a replay.

    Only turns that do not pause qualify: after a restart nothing may still wait on
    the unit's pending question."""
    able = [i for i, t in enumerate(turns)
            if t["kind"] in ("chat", "injected", "busy", "answer", "permission")
            and not t.get("pause") and len(t["steps"]) >= 2]
    for i in sorted(rng.sample(able, min(len(able), rng.randint(1, 2)))):
        kind = rng.choice(NET_KINDS)
        n = len(turns[i]["steps"])
        at = rng.randint(1, n - 1)
        after_tool = [k for k in range(1, n) if turns[i]["steps"][k - 1][-1]["type"] == "tool_result"]
        if kind == "restart" and after_tool:
            # the process ending right after a step's result, before the model's next
            # message: the commonest place for it, and the one a stream flush misses
            at = rng.choice(after_tool)
        turns[i]["net"] = {"kind": kind, "at": at,
                           "back": rng.randint(at + 1, n) if kind == "gap" else None}
    return rng.choice((4, 10_000))


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


def _scripted(unit: SessionUnit, frames: list[dict], *, pause: dict | None, after=None,
              at: dict | None = None):
    """The CLI's events for one turn, with the state changes the orchestrator makes.
    ``at[i]`` runs before frame ``i`` is yielded (``at[len(frames)]`` after the last):
    the frames before it are published by then."""
    async def gen(*_a, **_k):
        if unit.state != SessionState.STREAMING:
            unit._transition(SessionState.STREAMING)
        for i, ev in enumerate(frames):
            if at and i in at:
                await at[i]()
            if pause is not None and ev is frames[-1]:
                key = "request_id" if ev["type"] == "cmd_permission_request" else "tool_use_id"
                unit._pending_question = {key: ev.get("requestId") or ev.get("toolUseId")}
                unit._transition(SessionState.WAITING_INPUT)
            yield ev
        if at and len(frames) in at:
            await at[len(frames)]()
        if after is not None:
            await after()
        if pause is None and unit.state == SessionState.STREAMING:
            unit._transition(SessionState.IDLE)
    return gen


async def _resubscribe(hub, cursors: dict, epoch: str | None) -> list[dict]:
    """What the real ``SessionEventHub.subscribe`` sends a reconnecting client before
    its live envelopes: the buffered replay after its cursor, or a ``resync`` and the
    active turn's replay. ``events_hello`` stays in the client (it carries a random
    epoch), as ``sessionEvents`` keeps it from its listeners."""
    out: list[dict] = []
    agen = hub.subscribe(cursors, epoch)
    try:
        while True:
            try:
                env = await asyncio.wait_for(agen.__anext__(), 0.01)
            except (asyncio.TimeoutError, TimeoutError, StopAsyncIteration):
                break
            if env.get("type") != "events_hello":
                out.append(json.loads(json.dumps(env)))
    finally:
        await agen.aclose()
    return out


def _busy_send(**_k):
    async def gen():
        raise SessionBusyError(detail="actively streaming")
        yield  # pragma: no cover
    return gen()


async def _run(turns: list[dict], sid: str, hub) -> tuple[list[dict], SessionRouter]:
    from database import db

    def new_backend():
        r = _router()
        u = r.get_or_create_unit(sid, "default")
        u._client = object()
        u._transition(SessionState.IDLE)
        return r, u

    def record(h) -> list[dict]:
        """Every envelope ``h`` publishes, in order (the buffer may be short)."""
        sink: list[dict] = []
        orig = h.publish

        def publish(*a, **k):
            env = orig(*a, **k)
            sink.append(json.loads(json.dumps(env)))
            return env
        h.publish = publish
        return sink

    router, unit = new_backend()
    published = record(hub)
    steps: list[dict] = []
    seen = 0
    gap_open = False
    # connection steps, each placed after the envelope with seq <= its key
    marks: list[tuple[int, dict]] = []
    pending_q = pending_p = None

    def flush(limit: float) -> None:
        nonlocal gap_open
        while marks and marks[0][0] < limit:
            _, m = marks.pop(0)
            gap_open = m["kind"] == "disconnect"
            steps.append(m)

    def collect(upto: float = float("inf")) -> None:
        """The envelopes published since the last call, in seq order, with the
        connection steps between them; one published after ``upto`` reached no one."""
        nonlocal seen
        for env in published[seen:]:
            if env["eventSeq"] > upto:
                continue
            flush(env["eventSeq"])
            steps.append({"kind": "event", "env": env, **({"sub": False} if gap_open else {})})
        seen = len(published)
        flush(upto + 1 if upto != float("inf") else upto)

    def disconnect() -> int:
        head = hub.current_seq(sid)
        marks.append((head, {"kind": "disconnect"}))
        return head

    async def reconnect(cursor: int) -> None:
        """The window's subscription back, with the cursor of the last envelope it got;
        ``history`` is what its resync's DB read returns at this moment."""
        head = hub.current_seq(sid)
        envs = await _resubscribe(hub, {sid: cursor}, hub.epoch)
        marks.append((head, {"kind": "reconnect", "envs": envs,
                             "history": await _history(router, sid)}))

    async def chat(cid: str, text: str, gen) -> None:
        """One send: its user step, with the replies only its POST gets (``post``:
        the unstamped ones — the acceptance receipt, SESSION_BUSY …); ``ended``
        when the POST ended without a turn of its own."""
        step = {"kind": "user", "clientId": cid, "text": text, "post": []}
        steps.append(step)
        turned = False
        with patch.object(SessionUnit, "send", side_effect=gen):
            async for ev in router.run_conversation(agent_id="default", user_message=text,
                                                    session_id=sid, client_id=cid):
                if "turnId" in ev:
                    turned = True
                elif not turned:
                    step["post"].append(json.loads(json.dumps(ev)))
        if not turned:
            step["ended"] = True

    async def drive(coro, killed: asyncio.Event | None, hard: bool = False) -> None:
        """Run one turn; when ``killed`` fires the process ends: the turn's task is
        cancelled as the server's shutdown cancels its requests. ``hard`` (a
        SIGKILL): the cancelled turn's clean-up writes nothing — every DB write and
        publish made after the kill is dropped."""
        task = asyncio.ensure_future(coro)
        if killed is None:
            await task
            return
        waiter = asyncio.ensure_future(killed.wait())
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if not task.done():
            task.cancel()
        frozen = contextlib.ExitStack()
        if hard:
            from database import db as _db
            async def nothing(*_a, **_k):
                return None
            for target, name in ((_db.messages, "put"), (_db.messages, "update"),
                                 (_db.messages, "delete"), (_db, "update_send_request")):
                frozen.enter_context(patch.object(target, name, nothing))
            frozen.enter_context(patch.object(hub, "publish", lambda *a, **k: {}))
        with frozen:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def restart(cursor: int, upto: float) -> None:
        """A new daemon: new hub (new epoch), new router and unit; the window
        reconnects with its old cursor and epoch."""
        nonlocal router, unit, hub, published, seen, gap_open
        collect(upto)
        gap_open = False          # the restart step is the window's reconnect
        old_epoch = hub.epoch
        hub = session_events.SessionEventHub(buffer_size=hub._buffer_size, queue_size=10_000)
        session_events._hub = hub
        published, seen = record(hub), 0
        router, unit = new_backend()
        steps.append({"kind": "restart",
                      "envs": await _resubscribe(hub, {sid: cursor}, old_epoch),
                      "history": await _history(router, sid)})

    # client_ids are unique across scenarios: they share one test DB, and the send
    # ledger (core/send_requests) answers a client_id it has seen as a duplicate
    for n, turn in enumerate(turns, 1):
        kind = turn["kind"]
        pause = _pause_event(turn, n, sid) if turn.get("pause") else None
        net = turn.get("net")
        # frame index of step `net["at"]` (and `back`) in this turn's frames
        lead = 1 if kind in ("chat", "busy", "stop") else 0

        def frame(k: int) -> int:
            return lead + sum(len(st) for st in turn["steps"][:k])
        at: dict = {}
        killed: asyncio.Event | None = None
        cut = {"seq": 0, "kill": float("inf")}
        if net and net["kind"] in ("gap", "gap_late", "restart", "crash"):
            async def drop():
                cut["seq"] = disconnect()
            at[frame(net["at"])] = drop
        if net and net["kind"] == "gap":
            async def back():
                await reconnect(cut["seq"])
            at[frame(net["back"])] = back
        if net and net["kind"] in ("restart", "crash"):
            killed = asyncio.Event()

            async def die(drop=at[frame(net["at"])]):
                await drop()
                cut["kill"] = hub.current_seq(sid)
                killed.set()
                await asyncio.Event().wait()   # the process is gone
            at[frame(net["at"])] = die

        def scripted(frames, p=pause):
            return _scripted(unit, frames, pause=p, at=at or None)

        if kind == "chat":
            cid, text = f"local-{sid[-2:]}.{n}", f"user message {n}"
            await drive(chat(cid, text, scripted(_frames(turn, n, sid, session_start=True))), killed, hard=bool(net and net["kind"] == "crash"))
        elif kind == "stop":
            cid, text = f"local-{sid[-2:]}.{n}", f"user message {n}"
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
                cid, text = f"local-{sid[-2:]}.{n}-{k}", f"queued message {n}.{k}"
                await chat(cid, text, _busy_send)
            frames = _frames(turn, n, sid, session_start=True)

            async def drained(frames=frames):
                with patch.object(SessionUnit, "send", side_effect=scripted(frames, None)), \
                        patch.object(SessionUnit, "cli_quiescent", property(lambda _s: True)):
                    await router.drain_pending(sid)
            await drive(drained(), killed, hard=bool(net and net["kind"] == "crash"))
        elif kind == "injected":
            if turn["bg"]:
                unit.background_agents.launch(f"toolu_agent{n}", f"audit part {n}")
                unit.background_agents.task_started(f"task{n}", f"toolu_agent{n}")
                unit.background_agents.task_finished(f"task{n}", "completed", tool_use_id=f"toolu_agent{n}")
            unit._read_formatted_response = scripted(_frames(turn, n, sid, session_start=False))
            await drive(router.run_injected_turn(unit, object()), killed, hard=bool(net and net["kind"] == "crash"))
        elif kind == "answer":
            answers = {"Which one?": "A"}
            steps.append({"kind": "answer", "toolUseId": pending_q["toolUseId"], "answers": answers})
            unit._pending_question = None
            unit.continue_with_answer = scripted(_frames(turn, n, sid, session_start=False), None)

            async def answered(tid=pending_q["toolUseId"]):
                async for _ in router.continue_with_answer(sid, json.dumps(answers), tool_use_id=tid):
                    pass
            await drive(answered(), killed, hard=bool(net and net["kind"] == "crash"))
            pending_q = None
        elif kind == "permission":
            rid = pending_p["requestId"]
            steps.append({"kind": "decide", "requestId": rid, "decision": turn["decision"]})
            inner = scripted(_frames(turn, n, sid, session_start=False), None)

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

            async def decided(rid=rid, ok=turn["decision"] == "approve"):
                async for _ in router.continue_with_cmd_permission(sid, rid, ok):
                    pass
            await drive(decided(), killed, hard=bool(net and net["kind"] == "crash"))
            pending_p = None
        elif kind == "compaction":
            from core.compact_summary import build_record
            await db.insert_compaction(sid, build_record({"trigger": "auto"}, f"summary after {n - 1} turns"))
        if net and net["kind"] in ("restart", "crash"):
            await restart(cut["seq"], cut["kill"])
        elif kind != "stop":
            if net and net["kind"] == "gap_late":
                await reconnect(cut["seq"])          # back after the turn ended
            collect()
        if net and net["kind"] == "restart_idle":
            await restart(hub.current_seq(sid), float("inf"))
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
    for i in range(N_SCENARIOS + N_NET_SCENARIOS + N_CRASH_SCENARIOS):
        turns = _scenario(rng)
        buffer = (_crash(rng, turns) if i >= N_SCENARIOS + N_NET_SCENARIOS
                  else _net(rng, turns) if i >= N_SCENARIOS else 10_000)
        sid = f"sess-lr-{i:02d}"
        ids, clock = _Ids(i), _Clock()
        hub = session_events.SessionEventHub(buffer_size=buffer, queue_size=10_000)
        stamp = SimpleNamespace(now=clock.now, fromisoformat=datetime.fromisoformat)
        with patch.object(session_events, "_hub", hub), \
                patch("core.session_router.uuid4", ids.uuid4), \
                patch("uuid.uuid4", ids.uuid4), \
                patch("core.session_router.datetime", stamp), \
                patch("core.compact_summary.datetime", stamp), \
                patch("core.turn_stop.datetime", SimpleNamespace(now=lambda tz=None: clock.now())), \
                patch("core.background_agents._now_ms", lambda: int(clock.t.timestamp() * 1000)), \
                patch("core.session_state.publish", lambda *_a, **_k: None):
            # (session_state pushes are tested on their own, test_transcript_feed:
            # they are scheduled tasks, and the transcript is what this compares)
            steps, router = await _run(turns, sid, hub)
            history = await _history(router, sid)
        out.append({"name": f"s{i:02d}", "sid": sid,
                    "shape": [t["kind"] + (f"/{t['pause']}" if t.get("pause") else "")
                              + (f"x{t['queued']}" if t.get("queued") else "")
                              + (f"!{t['net']['kind']}" if t.get("net") else "") for t in turns],
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
    assert len(data["scenarios"]) == N_SCENARIOS + N_NET_SCENARIOS + N_CRASH_SCENARIOS
    assert all(s["history"] for s in data["scenarios"])
    # every connection case is in the fixture (a seed change must not drop one)
    cases = set()
    for s in data["scenarios"]:
        steps = s["steps"]
        for i, st in enumerate(steps):
            if st["kind"] == "reconnect":
                # the state snapshot every reconnect ends with is not part of the case
                types = [e["type"] for e in st["envs"] if not e.get("snapshot")]
                cases.add("resync+replay" if types[:2] == ["resync", "turn_start"]
                          else "resync" if types == ["resync"] else "replay")
            if st["kind"] == "restart":
                prev = [x for x in steps[:i] if x["kind"] == "event"]
                mid = bool(prev) and prev[-1]["env"]["type"] != "turn_end"
                cases.add("restart mid-turn" if mid else "restart idle")
                if mid and prev[-1]["env"]["type"] == "tool_result":
                    cases.add("restart after a tool result")
    assert cases >= {"replay", "resync", "resync+replay", "restart mid-turn", "restart idle",
                     "restart after a tool result"}, cases
