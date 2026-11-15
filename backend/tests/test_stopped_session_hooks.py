"""A session whose turn was stopped still gets its post-session hooks.

A user Stop (``SessionUnit.interrupt``, autonomous=False; the compaction guard's
interrupt too) recycles the subprocess as the turn ends: STREAMING → IDLE → DEAD →
COLD inside one call. The lifecycle hook paths took only IDLE units
(``_fire_idle_hooks``, the TTL kill) or DEAD ones (``_cleanup_dead``), so no path
ever saw the stopped unit: its hooks (the CDF review request, DailyActivity, a
Hive's ``closed`` record) waited for the next message, the next daemon startup or
the shutdown of a still-alive unit.

These tests drive a real SessionUnit, SessionRouter and LifecycleManager. The only
fakes are at the subprocess boundary (an SDK client fed the CLI's frames, read by
the real stream reader) and the hook executor (records what is enqueued). Time is
advanced on the lifecycle manager's clock only.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from claude_agent_sdk._internal.message_parser import parse_message

import core.lifecycle_manager as lifecycle_module
from core.cli_stream_reader import CliStreamReader
from core.lifecycle_manager import LifecycleManager
from core.session_router import SessionRouter
from core.session_unit import SessionState, SessionUnit, awake_time

GRACE = LifecycleManager.IDLE_HOOK_GRACE
SDK_ID = "sdk-stop-1"


def _sys(state: str) -> dict:
    return {"type": "system", "subtype": "session_state_changed", "state": state,
            "session_id": SDK_ID}


def _init() -> dict:
    return {"type": "system", "subtype": "init", "session_id": SDK_ID, "cwd": "/",
            "tools": [], "mcp_servers": [], "model": "m", "permissionMode": "default"}


def _assistant(text: str) -> dict:
    return {"type": "assistant", "parent_tool_use_id": None, "session_id": SDK_ID,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "model": "m"}}


def _result(subtype: str = "success", is_error: bool = False) -> dict:
    return {"type": "result", "subtype": subtype, "is_error": is_error,
            "duration_ms": 10, "duration_api_ms": 5, "num_turns": 1,
            "session_id": SDK_ID, "total_cost_usd": 0, "result": "",
            "usage": {"input_tokens": 1, "output_tokens": 1}}


class FakeClient:
    """The SDK client at the subprocess boundary: receive_messages() over a queue
    of CLI frames. ``query`` opens a turn; ``complete`` says whether the CLI then
    finishes it (result + idle) or keeps streaming until ``interrupt``."""

    def __init__(self, *, complete: bool) -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self.complete = complete
        self.queries = 0

    async def receive_messages(self):
        while True:
            item = await self.q.get()
            if item is None:
                return
            msg = parse_message(item)
            if msg is not None:
                yield msg

    async def query(self, prompt, session_id="default"):
        if not isinstance(prompt, str):
            async for _ in prompt:
                pass
        self.queries += 1
        frames = [_sys("running"), _init(), _assistant("working on it")]
        if self.complete:
            frames += [_result(), _sys("idle")]
        for f in frames:
            self.q.put_nowait(f)

    async def interrupt(self):
        # the CLI ends the interrupted turn the way it does in production
        for f in (_result("error_during_execution", True), _sys("idle")):
            self.q.put_nowait(f)

    def receive_response(self):  # pragma: no cover - the stream reader owns the stream
        raise AssertionError("receive_response() read while a stream reader exists")


class Recorder:
    """The hook executor: what was enqueued, by session id."""

    def __init__(self) -> None:
        self.fired: list[str] = []

    def fire(self, ctx) -> None:
        self.fired.append(ctx.session_id)


async def _until(pred, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def _no_reader_outlives_the_test(monkeypatch):
    """Close every stream reader a test started (a reader left pending on the fake
    client's queue would hang the loop's teardown) and this loop's DB pools."""
    readers: list[CliStreamReader] = []
    orig_start = CliStreamReader.start

    def _start(self):
        readers.append(self)
        orig_start(self)

    monkeypatch.setattr(CliStreamReader, "start", _start)
    yield
    tasks = []
    for r in readers:
        r.close()
        if r._task is not None:
            tasks.append(r._task)
    current = asyncio.current_task()
    for t in asyncio.all_tasks():
        if t is not current and not t.done() and t not in tasks:
            t.cancel()
            tasks.append(t)
    if tasks:
        await asyncio.wait(tasks, timeout=2)
    from database.sqlite import close_loop_pools
    await close_loop_pools()


@pytest.fixture()
def clock(monkeypatch):
    """The lifecycle manager's clock, advanced by ``clock["skew"]`` seconds."""
    state = {"skew": 0.0}
    monkeypatch.setattr(lifecycle_module, "awake_time", lambda: awake_time() + state["skew"])
    return state


def _pair() -> tuple[SessionRouter, LifecycleManager, Recorder]:
    router = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
    router.enqueue_drain = MagicMock()  # the pending-message drain is not under test
    rec = Recorder()
    mgr = LifecycleManager(router=router, hook_executor=rec)
    mgr._release_session_state = MagicMock()
    return router, mgr, rec


def _live_unit(router: SessionRouter, sid: str) -> tuple[SessionUnit, FakeClient]:
    """A router-owned unit with a live (fake) CLI, as ``_spawn`` leaves it."""
    unit = router.get_or_create_unit(sid, "default")
    client = FakeClient(complete=False)
    unit._client = client
    unit._last_turn_clean = True
    unit._transition(SessionState.IDLE)
    unit._start_stream_reader(client)
    return unit, client


async def _stream_then_stop(unit: SessionUnit, client: FakeClient) -> float:
    """A turn streams; the user presses Stop mid-turn. Returns the stop's time."""
    async def consume():
        async for _ in unit.send("do the thing", MagicMock(), app_session_id="app-1"):
            pass

    turn = asyncio.create_task(consume())
    await _until(lambda: client.queries == 1 and unit.state == SessionState.STREAMING)
    await asyncio.sleep(0.05)  # let the open turn's frames reach the orchestrator
    assert await unit.interrupt() is True
    await asyncio.wait_for(turn, timeout=5)
    assert unit.state == SessionState.COLD, "a user Stop recycles the unit to COLD"
    assert unit._sdk_session_id == SDK_ID
    return awake_time()


async def _sweep_everything(mgr: LifecycleManager) -> None:
    """The maintenance loop's hook-firing passes, in its order."""
    await mgr._fire_idle_hooks()
    await mgr._check_ttl()
    await mgr._cleanup_dead()


class _Spawn:
    """``_ClaudeClientWrapper`` at the subprocess boundary: each spawn gets a new
    fake CLI that completes its turns."""

    def __init__(self) -> None:
        self.clients: list[FakeClient] = []

    def __call__(self, options=None):
        client = FakeClient(complete=True)
        self.clients.append(client)
        wrapper = MagicMock()
        wrapper.pid = None  # no real process: nothing to signal
        wrapper.__aenter__ = AsyncMock(return_value=client)
        wrapper.__aexit__ = AsyncMock(return_value=False)
        return wrapper


def _real_options():
    from claude_agent_sdk import ClaudeAgentOptions
    opts = ClaudeAgentOptions()
    opts.system_prompt = "test"
    return opts


# ── the stopped turn ─────────────────────────────────────────────────────────


async def test_a_stopped_turn_gets_its_hooks_once_after_the_grace(clock):
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-stop")
    await _stream_then_stop(unit, client)

    await _sweep_everything(mgr)
    assert rec.fired == [], "within the grace the user may still send: no hooks yet"

    clock["skew"] = GRACE + 1
    await _sweep_everything(mgr)
    assert rec.fired == ["sess-stop"]

    # every later pass, including the TTL and the DEAD sweep, leaves it at one
    clock["skew"] = GRACE * 10
    await _sweep_everything(mgr)
    await _sweep_everything(mgr)
    assert rec.fired == ["sess-stop"]


async def test_a_new_message_within_the_grace_delays_the_hooks(clock):
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-again")
    await _stream_then_stop(unit, client)

    clock["skew"] = GRACE / 2
    await mgr._fire_idle_hooks()
    assert rec.fired == []

    # the user sends again right away: COLD → spawn (with --resume) → a full turn
    spawn = _Spawn()
    with patch("core.claude_environment._ClaudeClientWrapper", side_effect=spawn), \
         patch("core.resource_monitor.resource_monitor.spawn_budget",
               return_value=MagicMock(can_spawn=True)):
        events = [e async for e in unit.send("and now this", _real_options(),
                                             app_session_id="app-1")]
    assert any(e.get("type") == "result" for e in events), [e.get("type") for e in events]
    assert len(spawn.clients) == 1 and spawn.clients[0].queries == 1
    assert unit.state == SessionState.IDLE
    turn_ended = awake_time()

    # past the stop's grace, but not the new turn's: still nothing
    clock["skew"] = (turn_ended - awake_time()) + GRACE / 2
    await _sweep_everything(mgr)
    assert rec.fired == []

    clock["skew"] = (turn_ended - awake_time()) + GRACE + 1
    await _sweep_everything(mgr)
    await _sweep_everything(mgr)
    assert rec.fired == ["sess-again"]


async def test_no_hooks_while_the_session_is_busy(clock):
    """A COLD unit with a Stop still recycling it, a turn claimed on it or a retry
    between attempts is busy: the sweep leaves it for a later pass."""
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-busy")
    await _stream_then_stop(unit, client)
    clock["skew"] = GRACE + 1

    unit._stopping = True  # what interrupt() holds until its recycle has ended
    await mgr._fire_idle_hooks()
    assert rec.fired == []

    unit._stopping = False
    unit._retrying = True  # a retry of the turn waits between attempts, COLD
    await mgr._fire_idle_hooks()
    assert rec.fired == []

    unit._retrying = False
    await mgr._fire_idle_hooks()
    assert rec.fired == ["sess-busy"]


async def test_a_stop_without_a_client_stamps_last_used(clock):
    """The no-client branch of the Stop also ends the turn: the grace runs from it."""
    router, mgr, rec = _pair()
    unit = router.get_or_create_unit("sess-noclient", "default")
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    unit.last_used = awake_time() - 10 * GRACE  # the turn started long ago
    assert await unit.interrupt() is False
    assert unit.state == SessionState.COLD

    await mgr._fire_idle_hooks()
    assert rec.fired == [], "the grace is measured from the stop, not the turn's start"
    clock["skew"] = GRACE + 1
    await mgr._fire_idle_hooks()
    assert rec.fired == ["sess-noclient"]


# ── what must stay as it was ─────────────────────────────────────────────────


async def test_a_unit_restored_cold_at_startup_gets_no_hooks(clock):
    """A tab restored at startup is a COLD unit with its --resume id and no turn in
    this daemon: the startup sweep owns its earlier session, the idle sweep does not
    start firing for it."""
    router, mgr, rec = _pair()
    router._persisted_sdk_ids = {"sess-restored": "sdk-restored"}
    unit = router.get_or_create_unit("sess-restored", "default")
    assert unit.state == SessionState.COLD and unit._sdk_session_id == "sdk-restored"
    unit.last_used = awake_time() - 24 * 3600

    clock["skew"] = GRACE * 100
    await _sweep_everything(mgr)
    assert rec.fired == []


async def test_a_unit_whose_hooks_already_ran_gets_none_when_cold(clock):
    """Hooks fired while IDLE; then the unit is recycled to COLD with no new turn
    (here: the user stops its background agents). Nothing fires again."""
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-done")
    client.complete = True
    async for _ in unit.send("finish", MagicMock(), app_session_id="app-1"):
        pass
    assert unit.state == SessionState.IDLE

    clock["skew"] = GRACE + 1
    await mgr._fire_idle_hooks()
    assert rec.fired == ["sess-done"]

    unit.background_agents.running = MagicMock(return_value=[{"toolUseId": "t1"}])
    assert await unit.stop_background() is True
    assert unit.state == SessionState.COLD
    clock["skew"] = GRACE * 10
    await _sweep_everything(mgr)
    assert rec.fired == ["sess-done"]


async def test_a_dead_unit_gets_its_hooks_once_from_the_dead_sweep(clock):
    """The DEAD sweep fires and moves the unit to COLD; the COLD sweep then sees
    the hooks as enqueued."""
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-dead")
    client.complete = True
    async for _ in unit.send("finish", MagicMock(), app_session_id="app-1"):
        pass
    unit._transition(SessionState.DEAD)  # the CLI died after the turn
    await mgr._cleanup_dead()
    assert unit.state == SessionState.COLD
    assert rec.fired == ["sess-dead"]

    clock["skew"] = GRACE * 10
    await _sweep_everything(mgr)
    assert rec.fired == ["sess-dead"]


async def test_enqueue_hooks_for_unit_marks_the_unit(clock):
    """The TTL kill fires through enqueue_hooks_for_unit and kills the unit to COLD:
    the COLD sweep must not fire a second time."""
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-ttl")
    client.complete = True
    async for _ in unit.send("finish", MagicMock(), app_session_id="app-1"):
        pass
    assert unit._hooks_enqueued is False

    await mgr.enqueue_hooks_for_unit(unit)
    assert unit._hooks_enqueued is True
    await unit.kill()
    assert unit.state == SessionState.COLD

    clock["skew"] = GRACE * 10
    await _sweep_everything(mgr)
    assert rec.fired == ["sess-ttl"]


async def test_an_idle_unit_still_gets_its_hooks_once(clock):
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-idle")
    client.complete = True
    async for _ in unit.send("finish", MagicMock(), app_session_id="app-1"):
        pass
    assert unit.state == SessionState.IDLE

    await mgr._fire_idle_hooks()
    assert rec.fired == []
    clock["skew"] = GRACE + 1
    await mgr._fire_idle_hooks()
    await mgr._fire_idle_hooks()
    assert rec.fired == ["sess-idle"]


async def test_a_turn_started_while_the_hooks_are_enqueued_keeps_its_own(clock):
    """The user sends again just as the grace runs out: the next turn starts while
    the sweep awaits the hook context. That turn's quiet period still gets hooks
    (the sweep's mark must not land after the turn has cleared it)."""
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-race")
    await _stream_then_stop(unit, client)
    clock["skew"] = GRACE + 1

    real_build = mgr._build_hook_context

    async def build_while_a_turn_starts(u):
        ctx = await real_build(u)
        u._transition(SessionState.IDLE)       # the send respawned the CLI
        u._transition(SessionState.STREAMING)  # and its turn started
        return ctx

    mgr._build_hook_context = build_while_a_turn_starts
    await mgr._fire_idle_hooks()
    assert rec.fired == ["sess-race"]          # the stopped turn's hooks
    assert unit._hooks_enqueued is False, "the new turn's quiet period is still owed"

    mgr._build_hook_context = real_build
    unit._transition(SessionState.IDLE)        # the new turn ends
    unit.last_used = awake_time()
    clock["skew"] = GRACE + 1
    await mgr._fire_idle_hooks()
    assert rec.fired == ["sess-race", "sess-race"]


async def test_enqueue_hooks_for_unit_keeps_a_turn_started_during_it(clock):
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-race-ttl")
    client.complete = True
    async for _ in unit.send("finish", MagicMock(), app_session_id="app-1"):
        pass
    real_build = mgr._build_hook_context

    async def build_while_a_turn_starts(u):
        ctx = await real_build(u)
        u._transition(SessionState.STREAMING)
        return ctx

    mgr._build_hook_context = build_while_a_turn_starts
    await mgr.enqueue_hooks_for_unit(unit)
    assert rec.fired == ["sess-race-ttl"]
    assert unit._hooks_enqueued is False


async def test_a_failed_hook_context_is_tried_again(clock):
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-db-error")
    await _stream_then_stop(unit, client)
    clock["skew"] = GRACE + 1
    real_build = mgr._build_hook_context
    mgr._build_hook_context = AsyncMock(return_value=None)  # the DB read failed
    await mgr._fire_idle_hooks()
    assert rec.fired == [] and unit._hooks_enqueued is False

    mgr._build_hook_context = real_build
    await mgr._fire_idle_hooks()
    assert rec.fired == ["sess-db-error"]


async def test_shutdown_leaves_a_failed_hook_build_to_the_next_pass(clock):
    """The router's callers leave the mark to enqueue_hooks_for_unit: a shutdown
    whose hook context could not be built does not mark the unit as done."""
    router, mgr, rec = _pair()
    router._lifecycle_manager = mgr
    unit, client = _live_unit(router, "sess-shutdown")
    client.complete = True
    async for _ in unit.send("finish", MagicMock(), app_session_id="app-1"):
        pass
    mgr._build_hook_context = AsyncMock(return_value=None)  # the DB read failed
    await router.disconnect_all()
    assert rec.fired == [] and unit._hooks_enqueued is False


async def test_a_send_recovering_a_dead_unit_during_its_hooks_keeps_its_own(clock):
    """The DEAD sweep awaits the hook context while a send() recovers the same unit
    (DEAD → COLD → IDLE → STREAMING): the new turn's quiet period is still owed."""
    router, mgr, rec = _pair()
    unit, client = _live_unit(router, "sess-dead-race")
    client.complete = True
    async for _ in unit.send("finish", MagicMock(), app_session_id="app-1"):
        pass
    unit._transition(SessionState.DEAD)
    real_build = mgr._build_hook_context

    async def build_while_send_recovers(u):
        ctx = await real_build(u)
        for state in (SessionState.COLD, SessionState.IDLE, SessionState.STREAMING):
            u._transition(state)
        return ctx

    mgr._build_hook_context = build_while_send_recovers
    await mgr._cleanup_dead()
    assert rec.fired == ["sess-dead-race"]
    assert unit.state == SessionState.STREAMING, "the recovered unit is left to send()"
    assert unit._hooks_enqueued is False


async def test_eviction_leaves_a_failed_hook_build_to_the_next_pass(clock):
    """The router's slot eviction, like shutdown, leaves the mark to
    enqueue_hooks_for_unit: a failed build is tried again by the COLD sweep."""
    router, mgr, rec = _pair()
    router._lifecycle_manager = mgr
    unit, client = _live_unit(router, "sess-evict")
    client.complete = True
    async for _ in unit.send("finish", MagicMock(), app_session_id="app-1"):
        pass
    unit.last_used = awake_time() - 10 * GRACE  # an idle orphan, past every grace
    mgr._build_hook_context = AsyncMock(return_value=None)  # the DB read failed
    with patch("routers.settings.owned_session_ids", return_value=set()):
        evicted = await router._evict_idle(
            exclude=SessionUnit(session_id="x", agent_id="default"), force=True)
    assert evicted is True and unit.state == SessionState.COLD
    assert rec.fired == [] and unit._hooks_enqueued is False

    del mgr._build_hook_context  # the real build again
    await mgr._fire_idle_hooks()
    assert rec.fired == ["sess-evict"]
