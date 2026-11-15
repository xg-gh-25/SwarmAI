"""core/model_access_warmer: the CLI's model-access cache kept fresh off the spawn path.

The CLI and the file are faked at their edges (a client whose connect rewrites the
cache file the way the CLI's probe does); the schedule, the key learning, the locks,
the back-offs and the activity window are the real code.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import model_access_warmer as maw
from core.model_access_warmer import FRESH_S, AFTER_EXPIRY_S, RETRY_S, ModelAccessWarmer

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, now: float = T0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def write_cache(path: Path, entries: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "entries": {
        k: {"m-sonnet": {"s": "ok", "at": int(at * 1000)}} for k, at in entries.items()}}))


class FakeClient:
    """connect(): what the CLI's start does to the file (re-probe *rewrites* keys)."""

    def __init__(self, path: Path, clock: Clock, rewrites=(), hang=False, error=None):
        self.path, self.clock, self.rewrites = path, clock, list(rewrites)
        self.hang, self.error = hang, error
        self.connected = self.disconnected = 0
        self._transport = MagicMock()
        self._transport._process = MagicMock(returncode=0)
        self.on_connect = None

    async def connect(self):
        self.connected += 1
        if self.on_connect:
            self.on_connect()
        if self.error:
            raise self.error
        if self.hang:
            await asyncio.Event().wait()
        if self.rewrites:
            entries = maw.read_entries(self.path)
            for k in self.rewrites:
                entries[k] = self.clock()
            write_cache(self.path, entries)

    async def disconnect(self):
        self.disconnected += 1


async def _direct_io(fn, *args):
    return fn(*args)


def _warmer(tmp_path: Path, clock: Clock, client: FakeClient, budget=lambda: True):
    path = tmp_path / "cache" / "model-access.json"
    w = ModelAccessWarmer(clock=clock, path=lambda: path, client_factory=lambda opts: client,
                          budget=budget, run_io=_direct_io)
    opts = MagicMock(model="m", settings='{"a": 1}', setting_sources=["project"],
                     cwd="/ws", cli_path=None, env={"X": "1"})
    w._template = maw.WarmTemplate.from_options(opts)
    w._last_activity = clock()
    return w, path


class _StopLoop(BaseException):  # not an Exception: the loop swallows those
    pass


@pytest.mark.asyncio
async def test_warms_just_after_learned_key_expires(tmp_path):
    """With the chat key learned, the loop sleeps until that entry expires (+2 s), then
    runs one warm-up; the rewritten entry pushes the next one 15 minutes out."""
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["CHAT"])
    w, path = _warmer(tmp_path, clock, client)
    w._key = "CHAT"
    write_cache(path, {"CHAT": T0 - 100, "JOB": T0 - 10})
    sleeps: list[float] = []

    async def sleep(s):
        sleeps.append(s)
        if len(sleeps) > 30:
            raise _StopLoop()
        clock.now += s

    w._sleep = sleep
    connect_times: list[float] = []
    client.on_connect = lambda: connect_times.append(clock.now)
    with pytest.raises(_StopLoop):
        await w._run()
    expiry = T0 - 100 + FRESH_S
    assert connect_times, "no warm-up ran"
    assert expiry < connect_times[0] <= expiry + 5
    # one warm-up per expiry, not one per poll
    assert all(b - a >= FRESH_S for a, b in zip(connect_times, connect_times[1:]))


def test_next_due_rules(tmp_path):
    clock = Clock()
    w, _ = _warmer(tmp_path, clock, FakeClient(tmp_path, clock))
    assert w.next_due({}, T0) == T0                          # nothing fresh: now
    assert w.next_due({"A": T0 - 1000}, T0) == T0            # all stale: now
    assert w.next_due({"A": T0 - 100, "B": T0 - 50}, T0) == T0 - 100 + FRESH_S + AFTER_EXPIRY_S
    w._key = "B"
    assert w.next_due({"A": T0 - 100, "B": T0 - 50}, T0) == T0 - 50 + FRESH_S + AFTER_EXPIRY_S
    w._not_before = T0 + 5000
    assert w.next_due({"B": T0 - 50}, T0) == T0 + 5000


@pytest.mark.asyncio
async def test_learns_key_from_single_rewritten_entry(tmp_path):
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["CHAT"])
    w, path = _warmer(tmp_path, clock, client)
    write_cache(path, {"JOB": T0 - 2000, "CHAT": T0 - 1000})
    await w.warm_once(maw.read_entries(path))
    assert w._key == "CHAT"


@pytest.mark.asyncio
async def test_two_rewritten_entries_teach_nothing(tmp_path):
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["A", "B"])
    w, path = _warmer(tmp_path, clock, client)
    write_cache(path, {"A": T0 - 2000, "B": T0 - 1000})
    await w.warm_once(maw.read_entries(path))
    assert w._key is None


@pytest.mark.asyncio
async def test_handshake_holds_neither_spawn_nor_env_lock(tmp_path):
    """The warm-up's handshake runs with _spawn_lock and _env_lock free; it even
    completes while a chat spawn holds _spawn_lock."""
    from core import session_unit
    from core.claude_environment import _env_lock
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["K"])
    seen: dict = {}
    client.on_connect = lambda: seen.update(env=_env_lock.locked())
    w, path = _warmer(tmp_path, clock, client)
    async with session_unit._spawn_lock:          # a chat spawn in progress
        await asyncio.wait_for(w.warm_once({}), timeout=5)
    assert client.connected == 1
    assert seen == {"env": False}


def test_note_activity_does_no_io(tmp_path):
    """note_activity / note_spawn: no file IO, no exception, outside a running loop
    too (the chat path calls them synchronously)."""
    calls = []
    w = ModelAccessWarmer(path=lambda: calls.append(1) or tmp_path / "x")
    w.note_activity()
    w.note_spawn(MagicMock(model="m", settings=None, setting_sources=None, cwd=None,
                           cli_path=None, env=None))
    assert calls == []
    assert w._template is not None and w._task is None   # no loop outside an event loop
    maw.note_activity()                                   # the module helpers never raise
    maw.note_spawn(object())


@pytest.mark.asyncio
async def test_no_warm_without_recent_activity(tmp_path):
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["K"])
    w, path = _warmer(tmp_path, clock, client)
    w._last_activity = T0 - maw.ACTIVE_WINDOW_S - 1
    await asyncio.wait_for(w._run(), timeout=5)           # returns at once
    assert client.connected == 0


@pytest.mark.asyncio
async def test_no_warm_when_spawn_budget_refuses(tmp_path):
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["K"])
    w, _ = _warmer(tmp_path, clock, client, budget=lambda: False)
    await w.warm_once({})
    assert client.connected == 0
    assert w._not_before == T0 + RETRY_S


@pytest.mark.asyncio
async def test_failed_handshake_backs_off_15_minutes(tmp_path):
    """Forced execution of the failure path: connect raises → closed, logged, next
    warm-up at least RETRY_S later."""
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock,
                        error=RuntimeError("CLI exited"))
    w, path = _warmer(tmp_path, clock, client)
    await w.warm_once({})
    assert client.disconnected == 1
    assert w._not_before == T0 + RETRY_S
    assert w.next_due({}, T0) >= T0 + RETRY_S


@pytest.mark.asyncio
async def test_hung_handshake_is_closed_at_cap(tmp_path):
    """Forced execution of the hang path: connect never returns → cut at the cap, the
    client closed, a CLI the close left running SIGKILLed, then the back-off."""
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, hang=True)
    proc = MagicMock(returncode=None, pid=99999)
    client._transport._process = proc
    w, _ = _warmer(tmp_path, clock, client)
    with patch.object(maw, "HANDSHAKE_CAP_S", 0.05):
        await asyncio.wait_for(w.warm_once({}), timeout=5)
    assert client.disconnected == 1
    proc.kill.assert_called_once()
    assert w._not_before == T0 + RETRY_S


@pytest.mark.asyncio
async def test_no_change_and_no_fresh_entry_backs_off(tmp_path):
    """A provider without this cache (no file, nothing rewritten): the next warm-up waits
    RETRY_S, so the loop never spins."""
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock)
    w, path = _warmer(tmp_path, clock, client)
    await w.warm_once({})
    assert w._not_before == T0 + RETRY_S
    assert w.next_due({}, T0) == T0 + RETRY_S


@pytest.mark.asyncio
async def test_warm_options_copy_the_chat_spawn(tmp_path):
    """The handshake starts the CLI like the chat spawn: same settings, setting sources,
    cwd, model, and the chat env over the configured process env."""
    clock = Clock()
    captured = {}

    def factory(opts):
        captured["opts"] = opts
        return FakeClient(tmp_path / "cache" / "model-access.json", clock)

    w, _ = _warmer(tmp_path, clock, FakeClient(tmp_path, clock))
    w._client_factory = factory
    await w.warm_once({})
    o = captured["opts"]
    assert (o.model, o.settings, o.setting_sources, str(o.cwd)) == ("m", '{"a": 1}', ["project"], "/ws")
    assert o.env["X"] == "1" and "PATH" in o.env


@pytest.mark.asyncio
async def test_spawn_records_warm_template():
    """An adopted chat spawn hands its options to the warmer."""
    from claude_agent_sdk import ClaudeAgentOptions
    from core.session_unit import SessionUnit, _SpawnedClient
    unit = SessionUnit(session_id="warm-tpl", agent_id="default")
    opts = ClaudeAgentOptions(model="mx", settings='{"s": 2}', setting_sources=["project"],
                              cwd="/w")
    spawned = _SpawnedClient(wrapper=MagicMock(pid=1), client=MagicMock(), lock_wait_ms=0,
                             configure_ms=0, aenter_ms=1, total_ms=1)
    with patch.object(SessionUnit, "_start_stream_reader"):
        await unit._adopt_client(spawned, opts)
    tpl = maw.WARMER._template
    assert (tpl.model, tpl.settings, tpl.setting_sources, tpl.cwd) == ("mx", '{"s": 2}', ["project"], "/w")


@pytest.mark.asyncio
async def test_open_tabs_put_notes_activity(tmp_path, monkeypatch):
    from routers import settings as settings_router
    monkeypatch.setattr(settings_router, "_get_open_tabs_path", lambda: tmp_path / "open_tabs.json")
    before = maw.WARMER._last_activity
    await settings_router.save_open_tabs({"tabs": []})
    assert maw.WARMER._last_activity > before


@pytest.mark.asyncio
async def test_chat_turn_notes_activity():
    """Every turn through the real run_conversation marks the person active."""
    import uuid
    from claude_agent_sdk import ClaudeAgentOptions
    from core.session_router import SessionRouter
    from core.session_unit import SessionUnit

    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=ClaudeAgentOptions(system_prompt="S"))
    router = SessionRouter(prompt_builder=pb, config=MagicMock())
    sid = f"warm-{uuid.uuid4()}"

    async def send(query_content, options, *a, **k):
        yield {"type": "result", "sessionId": sid, "durationMs": 1, "totalCostUsd": 0}

    before = maw.WARMER._last_activity
    with patch("core.session_router._maybe_inject_recall", AsyncMock(return_value=None)), \
         patch.object(SessionUnit, "send", side_effect=send):
        async for _ in router.run_conversation(agent_id="default", user_message="hi",
                                               session_id=sid, client_id=f"c-{uuid.uuid4()}"):
            pass
    assert maw.WARMER._last_activity > before


@pytest.mark.asyncio
async def test_stop_prevents_a_new_loop():
    """After stop() (shutdown), later activity starts no loop: no warm-up CLI while
    the sessions are torn down."""
    w = ModelAccessWarmer()
    w.note_spawn(MagicMock(model="m", settings=None, setting_sources=None, cwd=None,
                           cli_path=None, env=None))
    assert w._task is not None
    w.stop()
    w.note_activity()
    assert w._task is None


def test_unreadable_budget_means_no_warm_up():
    from core import session_registry
    with patch.object(session_registry, "session_router", object()):   # no alive_count
        assert maw._budget_allows() is False
