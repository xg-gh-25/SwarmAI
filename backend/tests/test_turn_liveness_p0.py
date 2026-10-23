"""P0 acceptance tests for docs/design/chat-turn-liveness.md (v2) — backend half.

Each test states one invariant the spec requires. Today's code breaks them, so each
is ``xfail(strict=True)`` naming the phase that fixes it: strict, so the moment a
fix lands the test passes, the run goes red, and the marker must be removed. The
ones today's code already satisfies (T5b, T9) are plain regression tests.

Methodology: the real router / unit / hub with only the far leaf replaced (the CLI:
``SessionUnit.send`` is a scripted generator, as in test_transcript_real_path.py),
and asyncio.Event gates placed INSIDE the phase under test, so concurrency is
deterministic: a test opens a gate, observes, then releases it.

- T1  a cold-starting turn is visible: activity preparing/starting/waiting, never
      idle; the hub has an open turn from acceptance; activitySeq increases (P1).
- T3  two concurrent sends: one turn_start; the second is queued pending (P1).
- T4  Stop never passes through IDLE while the subprocess is being recycled, and
      a send arriving then is queued instead of starting on that unit (P1).
- T5  an injected turn whose process dies is retried and the reason persisted (P3).
- T5b an injected turn that ends with a zero-output success ends quietly, no retry
      (guards 1d060eee).
- T8  a queued message can be listed and cancelled before it is claimed (P1).
- T9  the PostToolUse reflect hook returns at once, off the event loop (#7).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import session_events
from core.session_router import SessionRouter
from core.session_unit import SessionState, SessionUnit
from core.turn_phase import turn_phase_event

P1 = "chat-turn-liveness P1 (S1: activity + try_open_turn)"
P3 = "chat-turn-liveness P3 (S2: one failure policy)"


def red(phase: str):
    """Expected to fail on its ASSERTION until `phase` lands. raises=AssertionError:
    a test that breaks for any other reason (harness, import) is reported as an
    error, never mistaken for the expected red."""
    return pytest.mark.xfail(strict=True, reason=phase, raises=AssertionError)


@pytest.fixture
def hub(monkeypatch):
    h = session_events.SessionEventHub(buffer_size=500, queue_size=500)
    monkeypatch.setattr(session_events, "_hub", h)
    return h


def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


def _gated_send(gates: dict[str, asyncio.Event], reached: dict[str, asyncio.Event], text="ok"):
    """unit.send as a cold-starting CLI: STARTING (spawn), WAITING (query sent), then
    the reply. Before each phase it marks `reached[phase]` and waits on `gates[phase]`."""
    async def send(**kwargs: Any):
        sid = kwargs.get("app_session_id")
        for phase in ("starting", "waiting"):
            reached[phase].set()
            await gates[phase].wait()
            yield turn_phase_event(phase)
        yield {"type": "session_start", "sessionId": sid}
        yield {"type": "assistant", "content": [{"type": "text", "text": text}], "model": "test"}
        yield {"type": "result", "sessionId": sid, "durationMs": 1}
    return send


def _drive(router, sid, text, cid, send):
    """Start run_conversation as a task; returns (task, events list)."""
    events: list[dict] = []

    async def run():
        with patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock,
                   return_value={"model": "test"}), \
                patch("core.session_unit.SessionUnit.send", side_effect=send, autospec=False):
            async for ev in router.run_conversation(agent_id="default", user_message=text,
                                                    session_id=sid, client_id=cid):
                events.append(ev)
    return asyncio.ensure_future(run()), events


async def _settle(*tasks) -> None:
    """Let a driven turn run to its end (gates already released) so no generator
    is left mid-iteration when the test's loop closes."""
    for t in tasks:
        try:
            await asyncio.wait_for(asyncio.shield(t), 5)
        except BaseException:  # noqa: BLE001 — the assertion under test already decided
            pass


async def _state(router, sid) -> dict:
    from core.session_state import session_state_entry
    return await session_state_entry(router.get_unit(sid))


def _open_turn(hub, sid) -> bool:
    log = hub._logs.get(sid)
    return bool(log and log.active)


# ── T1 ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@red(P1)
async def test_t1_cold_start_is_visible_through_every_phase(hub):
    r = _router()
    sid = "s-t1"
    u = r.get_or_create_unit(sid, "default")          # COLD: no subprocess yet
    assert u.state == SessionState.COLD
    gates = {p: asyncio.Event() for p in ("starting", "waiting")}
    reached = {p: asyncio.Event() for p in ("starting", "waiting")}
    task, _ = _drive(r, sid, "hello", "local-t1", _gated_send(gates, reached))
    seen: list[tuple[str, int]] = []
    try:
        for phase in ("starting", "waiting"):
            await asyncio.wait_for(reached[phase].wait(), 5)
            st = await _state(r, sid)
            assert _open_turn(hub, sid), f"no open hub turn while {phase}"
            assert st.get("activity") not in (None, "idle"), st
            seen.append((st["activity"], st["activitySeq"]))
            gates[phase].set()
        await asyncio.wait_for(task, 5)
    finally:
        for g in gates.values():
            g.set()
        await _settle(task)
    # at each gate the turn is in the phase BEFORE the one about to start
    assert [a for a, _ in seen] == ["preparing", "starting"]
    assert seen[0][1] < seen[1][1]


# ── T3 ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@red(P1)
async def test_t3_concurrent_sends_open_one_turn_and_queue_the_second(hub):
    from core import session_pending
    r = _router()
    sid = "s-t3"
    r.get_or_create_unit(sid, "default")
    gates = {p: asyncio.Event() for p in ("starting", "waiting")}
    reached = {p: asyncio.Event() for p in ("starting", "waiting")}
    first, _ = _drive(r, sid, "first", "local-t3a", _gated_send(gates, reached, "a"))
    second = None
    try:
        await asyncio.wait_for(reached["starting"].wait(), 5)   # first is cold-starting
        second, ev2 = _drive(r, sid, "second", "local-t3b",
                             _gated_send({p: asyncio.Event() for p in gates},
                                         {p: asyncio.Event() for p in reached}, "b"))
        done, _ = await asyncio.wait({second}, timeout=5)
        assert done, "the second send waited for the first turn instead of being queued"
        starts = [e for e in hub._logs[sid].buf if e.get("type") == "turn_start"]
        assert len(starts) == 1, f"{len(starts)} turn_start for one running turn"
        assert any(e.get("type") == "accepted" and e.get("queued") for e in ev2), ev2
        assert await session_pending.count_pending(sid) == 1
    finally:
        for g in gates.values():
            g.set()
        await _settle(first, *([second] if second else []))


# ── T4 ────────────────────────────────────────────────────────────────────────

def _streaming_unit(sid: str) -> SessionUnit:
    unit = SessionUnit(session_id=sid, agent_id="default")
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    unit._client = AsyncMock()
    unit._client.interrupt = AsyncMock()
    unit._wrapper = MagicMock(pid=99999)
    unit._sdk_session_id = "resume-t4"
    return unit


@pytest.mark.asyncio
@red(P1)
async def test_t4_stop_never_passes_through_idle_while_recycling(monkeypatch):
    unit = _streaming_unit("s-t4")
    states: list[SessionState] = []
    real = unit._transition

    def record(s, *a, **k):
        states.append(s)
        return real(s, *a, **k)
    monkeypatch.setattr(unit, "_transition", record)

    in_checkpoint, release = asyncio.Event(), asyncio.Event()

    async def gated_checkpoint(*a, **k):
        in_checkpoint.set()
        await release.wait()
    monkeypatch.setattr(unit, "_arm_recovery_checkpoint", gated_checkpoint)

    stop = asyncio.ensure_future(unit.interrupt(timeout=5.0, autonomous=False))
    await asyncio.wait_for(in_checkpoint.wait(), 5)
    # the window in which 10-22 19:20 accepted a new send onto a dying process
    observed = unit.state
    release.set()
    await asyncio.wait_for(stop, 5)
    assert unit.state == SessionState.COLD and unit._sdk_session_id == "resume-t4"
    assert observed != SessionState.IDLE, "unit reads IDLE while its process is about to be killed"
    assert SessionState.IDLE not in states, f"Stop passed through IDLE: {states}"


# ── T5 (T5b is test_injected_turn_empty.py, already green: it guards 1d060eee) ─

class _DeadUnit:
    """The far leaf of an injected turn: the CLI starts answering, then its process
    dies (exit -9 as on 10-22). Every later read answers normally, so a retry
    recovers the turn. Counts reads so the test sees whether anyone retried."""

    def __init__(self, sid: str) -> None:
        self.session_id = sid
        self.state = SessionState.IDLE
        self._turn_changes = None
        self.reads = 0
        self.crashed = 0
        from core.background_agents import BackgroundAgents
        self.background_agents = BackgroundAgents()
        self.background_agents.launch("ag1", "t5 agent")
        self.background_agents.task_finished("task-1", "completed", tool_use_id="ag1",
                                             summary="done", usage={"duration_ms": 1})

    def begin_injected_turn(self, ts, client_id):
        self.state = SessionState.STREAMING

    def _transition(self, s):
        self.state = s

    async def _crash_to_cold_async(self, clear_identity=False):
        self.crashed += 1
        self.state = SessionState.COLD

    async def _read_formatted_response(self):
        self.reads += 1
        yield {"type": "session_start", "sessionId": self.session_id}
        if self.reads == 1:
            yield {"type": "assistant", "content": [{"type": "text", "text": "partial"}], "model": "t"}
            raise RuntimeError("Command failed with exit code -9 (exit code: -9)")
        yield {"type": "assistant", "content": [{"type": "text", "text": "the answer"}], "model": "t"}
        yield {"type": "result", "session_id": self.session_id}


@pytest.mark.asyncio
@red(P3)
async def test_t5_an_injected_turn_whose_process_dies_is_retried_with_a_reason(hub, monkeypatch):
    r = _router()
    monkeypatch.setattr(r, "_turn_files_of", lambda u: None)
    written: list[dict] = []

    async def persist(*a, **k):
        return []
    monkeypatch.setattr(r, "_persist_stream_event", persist)
    from core import turn_writer

    async def write(self, event, **k):
        written.append(event)
        return []
    monkeypatch.setattr(turn_writer.TurnWriter, "write", write)
    unit = _DeadUnit("s-t5")
    await r.run_injected_turn(unit, object())

    blocks = [b for e in written if e.get("type") == "assistant" for b in e.get("content", [])]
    notices = [b for b in blocks if b.get("type") == "turn_notice"]
    assert unit.reads >= 2, "the injected turn was not retried"
    assert [n["reason"] for n in notices][:1] == ["restarted"], notices
    assert sum(b.get("type") == "background_finished" for b in blocks) == 1
    assert any(b.get("text") == "the answer" for b in blocks)


# ── T8 ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@red(P1)
async def test_t8_a_queued_message_is_listed_and_cancellable_until_claimed():
    from core import session_pending
    sid = "s-t8"
    assert hasattr(session_pending, "list_unclaimed"), "no queue list (spec S3)"
    assert hasattr(session_pending, "cancel_unclaimed"), "no queue cancel (spec S3)"
    await session_pending.persist_pending(sid, user_message="later", content=None,
                                          agent_id="default", client_id="local-t8a")
    await session_pending.persist_pending(sid, user_message="much later", content=None,
                                          agent_id="default", client_id="local-t8b")
    listed = await session_pending.list_unclaimed(sid)
    assert [p["client_id"] for p in listed] == ["local-t8a", "local-t8b"]
    assert await session_pending.cancel_unclaimed(sid, "local-t8a") == "cancelled"
    await session_pending.claim_pending_batch(sid)
    assert await session_pending.cancel_unclaimed(sid, "local-t8b") == "claimed"


# ── T9 (#7, fixed in 9570fb9ce — regression) ─────────────────────────────────

@pytest.mark.asyncio
async def test_t9_the_reflect_hook_returns_without_scanning_on_the_loop(tmp_path, monkeypatch):
    from core.run_protocol import reflect_job
    projects = tmp_path / "Projects" / "P" / ".artifacts" / "runs"
    for i in range(10_000):
        (projects / f"run_{i:05d}").mkdir(parents=True)
    monkeypatch.setattr("core.pipeline_human._workspace", lambda: tmp_path)
    submitted = []
    monkeypatch.setattr(reflect_job, "_pool", lambda: MagicMock(submit=lambda *a: submitted.append(a)))
    hook = reflect_job.create_reflect_trigger({"sdk_session_id": "s-t9"})
    t0 = time.perf_counter()
    await hook({"tool_input": {"command": "python pipeline_v1.py land"}}, "tu", None)
    assert (time.perf_counter() - t0) < 0.005
    assert submitted, "the lookup must be handed to the reflect worker"
