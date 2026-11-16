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
                          budget=budget, run_io=_direct_io,
                          state=lambda: tmp_path / "model_access_warm.json")
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
    assert w.running_count() == 0
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


# ── a restart: warm from a template built from the configuration ─────────────

BUILT = {"model": "m-built", "settings": '{"b": 1}', "setting_sources": ["project"],
         "cwd": "/ws", "cli_path": None, "env": {"G": "1"}}


def _booting(tmp_path: Path, clock: Clock, client, *, factory=None, budget=lambda: True,
             run_io=_direct_io):
    """The warmer of a freshly started daemon: no chat spawn yet."""
    cache = tmp_path / "cache" / "model-access.json"
    return ModelAccessWarmer(clock=clock, path=lambda: cache,
                             client_factory=factory or (lambda opts: client),
                             budget=budget, run_io=run_io,
                             state=lambda: tmp_path / "model_access_warm.json"), cache


async def _stop_at_first_sleep(s):
    raise _StopLoop()


@pytest.mark.asyncio
async def test_boot_warms_from_built_template_when_person_was_active(tmp_path):
    """The process before the restart saw the person 5 minutes ago and learned the chat
    key; the entry expired while the daemon was down. At start, with no chat spawn,
    the warmer runs one warm-up from the built template, so the first cold start reads
    a fresh entry instead of paying the probe."""
    clock = Clock()
    old, cache = _booting(tmp_path, Clock(T0 - 300), None)
    old.note_activity()
    old._key = "CHAT"
    await old._save_state()

    write_cache(cache, {"CHAT": T0 - FRESH_S - 60, "JOB": T0 - 100})
    client = FakeClient(cache, clock, rewrites=["CHAT"])
    seen = {}

    def factory(opts):
        seen["opts"] = opts
        return client

    w, _ = _booting(tmp_path, clock, client, factory=factory)
    w._sleep = _stop_at_first_sleep
    await w.start(None, lambda: dict(BUILT))
    assert w._task is not None, "no loop started at boot"
    with pytest.raises(_StopLoop):
        await asyncio.wait_for(w._task, timeout=5)
    assert client.connected == 1
    o = seen["opts"]
    assert (o.model, o.settings, o.setting_sources, str(o.cwd)) == ("m-built", '{"b": 1}', ["project"], "/ws")
    assert o.env["G"] == "1"
    assert clock() - maw.read_entries(cache)["CHAT"] < FRESH_S   # fresh for the first chat spawn


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["old", "missing", "garbage", "future", "infinite"])
async def test_boot_without_recent_activity_starts_nothing(tmp_path, state):
    """Last activity older than the window, no state file, one that is not JSON, or a
    time after now (a clock set back, a damaged file): nobody is known to be active,
    so start() starts no loop and no CLI."""
    clock = Clock()
    path = tmp_path / "model_access_warm.json"
    if state == "old":
        maw.write_state(path, T0 - maw.ACTIVE_WINDOW_S - 1, "CHAT")
    elif state == "garbage":
        path.write_text("{not json")
    elif state == "future":
        maw.write_state(path, T0 + 3600, "CHAT")
    elif state == "infinite":
        path.write_text('{"last_activity": Infinity, "key": "CHAT"}')
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["CHAT"])
    w, _ = _booting(tmp_path, clock, client)
    await w.start(None, lambda: dict(BUILT))
    assert w._task is None
    assert client.connected == 0


@pytest.mark.asyncio
async def test_chat_spawn_replaces_built_template(tmp_path):
    clock = Clock()
    captured = []
    w, _ = _booting(tmp_path, clock, None, factory=lambda opts: captured.append(opts) or
                    FakeClient(tmp_path / "cache" / "model-access.json", clock))
    w._template_factory = lambda: dict(BUILT)
    w._last_activity = clock()
    await w.warm_once({})
    w._ensure_task = lambda: None
    w.note_spawn(MagicMock(model="m-chat", settings='{"c": 3}', setting_sources=["project"],
                           cwd="/chat", cli_path=None, env={"H": "2"}))
    w._not_before = float("-inf")
    await w.warm_once({})
    assert [o.model for o in captured] == ["m-built", "m-chat"]
    assert captured[1].env["H"] == "2" and "G" not in captured[1].env


@pytest.mark.asyncio
async def test_persisted_state_holds_no_environment(tmp_path):
    """The state file carries the last activity and the key only, written through the
    warmer's IO runner (off the event loop) by its loop, never by note_activity."""
    clock = Clock()
    io_calls = []

    async def run_io(fn, *args):
        io_calls.append(fn.__name__)
        return fn(*args)

    cache = tmp_path / "cache" / "model-access.json"
    write_cache(cache, {"CHAT": T0 - 10})
    client = FakeClient(cache, clock)
    w, _ = _booting(tmp_path, clock, client, run_io=run_io)
    w._template = maw.WarmTemplate(model="m", settings='{"awsCredentialExport": "x"}',
                                   setting_sources=["project"], cwd="/ws", cli_path=None,
                                   env={"PROVIDER_ONLY_VAR": "env-only-value"})
    w._key = "CHAT"
    w._ensure_task = lambda: None
    w.note_activity()
    assert not (tmp_path / "model_access_warm.json").exists()   # note_activity: no IO
    w._sleep = _stop_at_first_sleep
    with pytest.raises(_StopLoop):
        await w._run()
    text = (tmp_path / "model_access_warm.json").read_text()
    assert json.loads(text) == {"last_activity": T0, "key": "CHAT"}
    assert "env-only-value" not in text and "awsCredentialExport" not in text
    assert "write_state" in io_calls
    # unchanged state: not written again
    io_calls.clear()
    with pytest.raises(_StopLoop):
        await w._run()
    assert "write_state" not in io_calls


@pytest.mark.asyncio
async def test_built_template_failure_backs_off(tmp_path):
    """A template the configuration cannot give: the warm-up fails like any other
    (no CLI, back-off, not counted)."""
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock)
    w, _ = _booting(tmp_path, clock, client)

    def broken():
        raise RuntimeError("config unreadable")

    w._template_factory = broken
    await w.warm_once({})
    assert client.connected == 0
    assert w.running_count() == 0
    assert w._not_before == T0 + RETRY_S


@pytest.mark.asyncio
async def test_built_template_matches_desktop_build_options(monkeypatch):
    """The built template is what build_options gives a default desktop chat spawn with
    the same configuration: model, settings, setting sources, cwd and the gateway env."""
    from core import bedrock_thinking_gateway as gw
    from core.prompt_builder import PromptBuilder
    monkeypatch.setattr(gw, "_listen_port", 18321)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    cfg = MagicMock()
    values = {"thinking_mode": "adaptive", "thinking_effort": "high", "use_bedrock": True,
              "default_model": "claude-opus-5-5"}
    cfg.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    cfg.get_model = MagicMock(return_value="claude-opus-5-5")
    opts = await PromptBuilder(cfg).build_options(
        agent_config={}, enable_skills=False, enable_mcp=False, resume_session_id=None,
        session_context={"sdk_session_id": "sid"}, channel_context=None,
        editor_context=None, terminal_context=None, cached_system_prompt="CACHED")
    tpl = maw.WarmTemplate.from_fields(PromptBuilder(cfg).warm_up_template())
    chat = maw.WarmTemplate.from_options(opts)
    assert tpl.model == chat.model and tpl.model
    assert tpl.settings == chat.settings
    assert tpl.setting_sources == chat.setting_sources
    assert tpl.cwd == chat.cwd
    assert tpl.cli_path == chat.cli_path
    assert tpl.env == {"ANTHROPIC_BEDROCK_BASE_URL": "http://127.0.0.1:18321/bedrock-gw"}
    assert all(chat.env.get(k) == v for k, v in tpl.env.items())


# ── the warm-up's CLI counts in the spawn budget ─────────────────────────────

@pytest.mark.asyncio
async def test_running_warm_up_counts_as_staged_spawn(tmp_path, monkeypatch):
    """While the warm-up's CLI runs it is a staged spawn: the router's count (which the
    chat spawn's budget check adds to alive_count) and the warmer's own budget check
    both see it; once it is closed it is not counted."""
    from core import session_registry
    from core.resource_monitor import resource_monitor
    from core.session_router import SessionRouter
    clock = Clock()
    router = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
    monkeypatch.setattr(session_registry, "session_router", router)
    asked: list[int] = []
    monkeypatch.setattr(resource_monitor, "spawn_budget",
                        lambda alive_count=0: asked.append(alive_count) or MagicMock(can_spawn=True))
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["K"])
    seen = {}
    client.on_connect = lambda: seen.update(staged=router.staged_spawn_count(),
                                            budget_ok=maw._budget_allows())
    w, _ = _warmer(tmp_path, clock, client, budget=maw._budget_allows)
    monkeypatch.setattr(maw, "WARMER", w)
    assert router.staged_spawn_count() == 0
    await w.warm_once({})
    assert client.connected == 1
    assert seen["staged"] == 1
    assert asked == [0, 1]          # the warm-up's own check, then one made during it
    assert router.staged_spawn_count() == 0


@pytest.mark.asyncio
async def test_warm_up_count_released_on_failure_and_hang(tmp_path):
    clock = Clock()
    path = tmp_path / "cache" / "model-access.json"
    w, _ = _warmer(tmp_path, clock, FakeClient(path, clock, error=RuntimeError("exit 1")))
    await w.warm_once({})
    assert w.running_count() == 0
    w._client_factory = lambda opts: FakeClient(path, clock, hang=True)
    w._not_before = float("-inf")
    with patch.object(maw, "HANDSHAKE_CAP_S", 0.05):
        await asyncio.wait_for(w.warm_once({}), timeout=5)
    assert w.running_count() == 0
    # cancelled mid-handshake (shutdown's stop())
    w._not_before = float("-inf")
    task = asyncio.ensure_future(w.warm_once({}))
    for _ in range(50):
        await asyncio.sleep(0)
        if w.running_count():
            break
    assert w.running_count() == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert w.running_count() == 0


@pytest.mark.asyncio
async def test_activity_started_loop_lets_the_turn_probe_first(tmp_path):
    """A loop that a chat turn (or a tab change) starts does not warm beside that turn's
    own cold spawn: its first warm-up waits AFTER_ACTIVITY_S; start() at boot does not."""
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["K"])
    w, _ = _booting(tmp_path, clock, client)
    w._template_factory = lambda: dict(BUILT)
    w.note_activity()
    assert w._task is not None
    w.stop()
    assert w.next_due({}, T0) == T0 + maw.AFTER_ACTIVITY_S

    booted, _ = _booting(tmp_path, clock, client)
    maw.write_state(tmp_path / "model_access_warm.json", T0 - 60, None)
    await booted.start(None, lambda: dict(BUILT))
    booted.stop()
    assert booted.next_due({}, T0) == T0



# ── a chat spawn goes before a warm-up ───────────────────────────────────────

def _budget_full_beside_one_cli(monkeypatch, asked):
    """A machine at its budget: one more CLI fits only when nothing else runs."""
    from core.resource_monitor import resource_monitor

    def spawn_budget(alive_count=0):
        asked.append(alive_count)
        return MagicMock(can_spawn=alive_count == 0, reason="memory", available_mb=1,
                         estimated_cost_mb=2, headroom_mb=0)

    monkeypatch.setattr(resource_monitor, "spawn_budget", spawn_budget)


@pytest.mark.asyncio
async def test_chat_spawn_refused_beside_a_warm_up_ends_it_first(tmp_path, monkeypatch):
    """The budget refuses the chat spawn only because a warm-up's CLI runs: the
    warm-up is ended (closed, uncounted) and the chat spawn admitted."""
    from core import session_registry
    from core.session_router import SessionRouter
    from core.session_unit import SessionUnit
    clock = Clock()
    router = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
    monkeypatch.setattr(session_registry, "session_router", router)
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, hang=True)
    w, _ = _warmer(tmp_path, clock, client)
    monkeypatch.setattr(maw, "WARMER", w)
    w._task = asyncio.ensure_future(w.warm_once({}))
    for _ in range(50):
        await asyncio.sleep(0)
        if w.running_count():
            break
    assert w.running_count() == 1
    asked: list[int] = []
    _budget_full_beside_one_cli(monkeypatch, asked)
    unit = SessionUnit(session_id="chat-first", agent_id="default")
    await asyncio.wait_for(unit._check_spawn_budget(), timeout=5)   # admitted, no raise
    assert asked == [1, 0]
    assert w.running_count() == 0 and w._task.done()
    assert client.disconnected == 1


@pytest.mark.asyncio
async def test_chat_spawn_refused_without_a_warm_up_still_raises(monkeypatch):
    from core import session_registry
    from core.exceptions import ResourceExhaustedException
    from core.session_unit import SessionUnit
    monkeypatch.setattr(session_registry, "session_router", None)
    asked: list[int] = []
    from core.resource_monitor import resource_monitor
    monkeypatch.setattr(resource_monitor, "spawn_budget",
                        lambda alive_count=0: asked.append(alive_count) or MagicMock(
                            can_spawn=False, reason="memory", available_mb=1,
                            estimated_cost_mb=2, headroom_mb=0))
    unit = SessionUnit(session_id="chat-refused", agent_id="default")
    with pytest.raises(ResourceExhaustedException):
        await unit._check_spawn_budget()
    assert asked == [0]



@pytest.mark.asyncio
async def test_cancel_during_the_close_still_kills_the_warm_up_cli(tmp_path):
    """yield_to_chat's cancel lands while the warm-up is closing its CLI (the close
    hangs): the CLI is still SIGKILLed and the warm-up is no longer counted."""
    clock = Clock()
    client = FakeClient(tmp_path / "cache" / "model-access.json", clock, rewrites=["K"])
    proc = MagicMock(returncode=None, pid=99998)
    client._transport._process = proc
    closing = asyncio.Event()

    async def hanging_disconnect():
        client.disconnected += 1
        closing.set()
        await asyncio.Event().wait()

    client.disconnect = hanging_disconnect
    w, _ = _warmer(tmp_path, clock, client)
    w._task = asyncio.ensure_future(w.warm_once({}))
    await asyncio.wait_for(closing.wait(), timeout=5)
    assert w.running_count() == 1
    assert await asyncio.wait_for(w.yield_to_chat(), timeout=5) is True
    proc.kill.assert_called_once()
    assert w.running_count() == 0
