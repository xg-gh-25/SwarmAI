"""Tests for _client_io subprocess-access serialization (run_4b74b764).

The bug (root cause, M3-skeptic-confirmed): a maintenance op calls `compact()`
which drains `self._client.receive_response()` for a long time. Meanwhile a NEW
user turn calls `send()`, whose IDLE-reuse path drives the SAME
`self._client.receive_response()`. Two concurrent `receive_response()` consumers
iterate the single anyio `_message_receive` channel; anyio delivers each message
to exactly ONE waiter → the two drives split the stream and co-starve → both hit
their timeouts → kill + --resume respawn. User sees a freeze.

NOTE (run_2b1957f8): the proactive soft-compact path that ORIGINALLY drove this
maintenance compact was REMOVED (it held _client_io for 300s at 60% context and
froze the next send). `compact()` itself lives on (proactive_restart RSS path +
manual /compact), so the _client_io serialization guarantee below still matters.

Fix (A+C+B, Gate-1-revised):
- (A) `self._client_io = asyncio.Lock()`, SEPARATE from `self._lock` (the
  recovery-transaction lock). `compact()` holds it across its query+drain; the
  foreground turn acquires a SHORT BARRIER at send()'s IDLE-entry (so it waits
  for an in-flight compact, then drives unlocked — NOT held across the body, or
  it would self-deadlock with the in-loop CompactionGuard interrupt + break Stop).
- (C) background maint ops (`_check_mcp_health`) probe `_client_io.locked()` and
  SKIP this round if a turn holds it.
- interrupt() / flush_subprocess_pipe stay LOCK-FREE (control-channel, not
  receive_response — acquiring would deadlock Stop against the turn it stops).

Methodology: forced-execution. We drive the REAL `compact()` (mocking ONLY the
SDK client boundary) and assert mutual exclusion / yield behavior — NOT a "lock
object exists" check.
"""

import asyncio
import time

import pytest

from core.session_unit import SessionUnit, SessionState


def _unit():
    """Minimal IDLE SessionUnit without __init__ wiring, with _client_io set.

    Mirrors test_session_unit_soft_compact._unit() but adds the _client_io lock
    + a _client so the serialization path is exercisable.
    """
    u = SessionUnit.__new__(SessionUnit)
    u.session_id = "test-client-io"
    u.state = SessionState.IDLE
    u._model_name = "claude-opus-4-8"
    u._last_known_context_tokens = 900_000
    u._sdk_session_id = "sdk-abc"
    # The primitive under test. If __init__ doesn't create it, prod code that
    # references self._client_io will AttributeError — that is a RED signal.
    u._client_io = asyncio.Lock()
    return u


class _SlowClient:
    """Stand-in for the SDK client whose receive_response drains slowly.

    Records the wall-clock [start, end] interval of each drain so a test can
    assert two drains did NOT overlap (serialization proof).
    """

    def __init__(self, drain_seconds=0.05):
        self.drain_seconds = drain_seconds
        self.intervals = []  # list[(start, end)] of receive_response drains
        self.query_calls = []

    async def query(self, prompt=None, session_id=None):
        self.query_calls.append(prompt)

    async def receive_response(self):
        start = time.monotonic()
        # Yield control so a concurrent drain CAN interleave if unserialized.
        await asyncio.sleep(self.drain_seconds)
        end = time.monotonic()
        self.intervals.append((start, end))
        if False:
            yield None  # make this an async generator
        return

    async def get_mcp_status(self):
        return {}


def _overlap(intervals):
    """True if any two [start,end] intervals overlap in wall-clock time."""
    s = sorted(intervals)
    for i in range(1, len(s)):
        if s[i][0] < s[i - 1][1]:  # next started before prev ended
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────
# AC2 — the serialization primitive exists and compact() holds it
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_init_creates_client_io_lock():
    """AC2: a real SessionUnit (via __init__) has a _client_io lock distinct
    from _lock. RED until __init__ creates it."""
    u = SessionUnit(session_id="x", agent_id="default")
    assert hasattr(u, "_client_io"), "_client_io lock must exist"
    assert isinstance(u._client_io, asyncio.Lock)
    assert u._client_io is not u._lock, "_client_io MUST be separate from _lock"


@pytest.mark.asyncio
async def test_compact_holds_client_io_during_drain():
    """AC2/AC1: compact() must hold _client_io while it drains the subprocess,
    so a concurrent turn cannot drive the same client. RED until compact()
    acquires the lock."""
    u = _unit()
    u._client = _SlowClient(drain_seconds=0.05)
    u._compaction_guard = _StubGuard()

    locked_during_drain = asyncio.Event()

    async def observer():
        # Poll until compact has begun holding the lock.
        for _ in range(200):
            if u._client_io.locked():
                locked_during_drain.set()
                return
            await asyncio.sleep(0.001)

    obs = asyncio.create_task(observer())
    await u.compact()
    await obs
    assert locked_during_drain.is_set(), (
        "compact() did not hold _client_io during its drain — a concurrent "
        "send() could split the SDK stream"
    )


@pytest.mark.asyncio
async def test_concurrent_drives_are_serialized_not_interleaved():
    """AC1: two concurrent compact-style drives on the same unit must NOT
    overlap — the lock serializes them. This is the co-starvation repro:
    without the lock the two receive_response drains interleave."""
    u = _unit()
    client = _SlowClient(drain_seconds=0.05)
    u._client = client
    u._compaction_guard = _StubGuard()

    # Fire two compacts concurrently — both must serialize on _client_io.
    async def drive():
        await u.compact()

    await asyncio.gather(drive(), drive())
    assert len(client.intervals) == 2
    assert not _overlap(client.intervals), (
        "two drains overlapped — _client_io did not serialize them "
        "(co-starvation race still reachable)"
    )


# ─────────────────────────────────────────────────────────────────────────
# AC3 — background maintenance YIELDS (skips) when a turn holds the client
#
# NOTE (run_2b1957f8): the proactive soft-compact path (_check_context_soft_compact)
# was REMOVED — its two _client_io-yield tests are gone with it. The _client_io
# serialization guarantee itself is still exercised by the compact()/interrupt()/
# _check_mcp_health tests above and below (compact() lives on for proactive_restart
# + manual /compact).
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_health_skips_when_client_io_held():
    """AC3: _check_mcp_health must also yield when a turn holds _client_io
    (it calls get_mcp_status on the same subprocess)."""
    u = _unit()
    u._mcp_health_checked = False
    u._configured_mcps = ["a", "b"]
    called = {"v": False}

    class _C:
        async def get_mcp_status(self):
            called["v"] = True
            return {}

    u._client = _C()

    await u._client_io.acquire()
    try:
        await u._check_mcp_health()
    finally:
        u._client_io.release()

    assert not called["v"], "_check_mcp_health drove the client while a turn held it"


# ─────────────────────────────────────────────────────────────────────────
# interrupt() / flush must stay LOCK-FREE (Gate-1 BLOCKER 5)
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_interrupt_does_not_acquire_client_io():
    """Gate-1 BLOCKER 5: interrupt() is a control-channel call; it must NOT
    acquire _client_io, or a user Stop deadlocks against the turn it stops
    (the turn holds the lock; Stop would wait forever)."""
    u = _unit()
    interrupted = {"v": False}

    class _C:
        async def interrupt(self):
            # If interrupt tried to acquire _client_io while a turn held it,
            # this would never run. Assert it runs even WITH the lock held.
            interrupted["v"] = True

    u._client = _C()
    u._send_generation = 0
    u._pipe_flush_task = None
    u._stop_event = asyncio.Event()
    u._interrupted = False
    u._user_stopped_current_turn = False
    u._active_agent_tools = {}
    u._open_tool_uses = {}
    u._lock = asyncio.Lock()
    u._pid_watchdog_task = None
    u._wrapper = None
    # The post-interrupt recycle + state transitions are OUT OF SCOPE for this
    # test — we only assert interrupt() reaches client.interrupt() lock-free
    # (the lock-free property is proven the instant client.interrupt() runs
    # while this test holds _client_io). Stub the recycle + transition bookkeeping
    # so the minimal fixture needn't provide the full kill/state machinery.
    from unittest.mock import AsyncMock
    u._crash_to_cold_async = AsyncMock(return_value=None)
    u._transition = lambda *a, **k: None
    # interrupt() early-returns unless STREAMING/WAITING_INPUT — put it in a
    # state where it WILL reach client.interrupt().
    u.state = SessionState.STREAMING

    await u._client_io.acquire()  # a turn holds the client
    try:
        # interrupt must complete WITHOUT waiting on _client_io. If interrupt
        # tried to acquire _client_io (held here), this would time out.
        await asyncio.wait_for(u.interrupt(timeout=0.5), timeout=2.0)
    finally:
        u._client_io.release()

    assert interrupted["v"], "interrupt() did not reach client.interrupt() — it blocked on _client_io"


class _StubGuard:
    """Minimal CompactionGuard stand-in for compact()'s work_summary/activate."""

    def work_summary(self):
        return ""

    def activate(self):
        pass
