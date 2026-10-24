"""Tests for the SQLite connection pool (offline-root-fix run_7e8a2030).

Methodology: TDD tracer bullets for the connection-pool that replaces the
per-operation ``aiosqlite.connect()`` model. The pool exists to bound OS-thread
count (aiosqlite = 1 worker thread per connection) so the default asyncio
executor can no longer be starved — which is the root cause of the /health
round-trip exceeding the Rust watchdog's 3s budget → false "Backend offline".

Key invariants under test:
- AC3: 20 concurrent DB ops go through a bounded pool → thread count stays
  O(pool_size), NOT O(concurrency). (live probe: 20→~5, not 20→21)
- AC5: a borrow that raises mid-op returns its connection to the pool (no leak).
- AC8: borrowing when the pool is exhausted raises a typed timeout (backpressure),
  never hangs forever.
- Writes serialize (single write connection); reads run concurrently (WAL).
"""
from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import os

import pytest

from database.sqlite import _ConnectionPool, PoolBorrowTimeout


@pytest.fixture
def tmp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="pool_test_")
    os.close(fd)
    yield path
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass


async def _make_pool(path, read_size=4):
    pool = _ConnectionPool(path, read_size=read_size)
    await pool.start()
    # Seed a table for read/write ops.
    async with pool.borrow(readonly=False) as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        await conn.commit()
    return pool


@pytest.mark.asyncio
async def test_pool_bounds_thread_count(tmp_db_path):
    """AC3: 20 concurrent ops through a 4-read pool stay bounded, not O(concurrency)."""
    pool = await _make_pool(tmp_db_path, read_size=4)
    baseline = threading.active_count()
    peak = baseline

    async def op():
        nonlocal peak
        async with pool.borrow(readonly=True) as conn:
            cur = await conn.execute("SELECT 1")
            await cur.fetchone()
            await asyncio.sleep(0.05)
            peak = max(peak, threading.active_count())

    await asyncio.gather(*[op() for _ in range(20)])
    # Pool = 1 write + 4 read = 5 connections = 5 worker threads max, + baseline.
    # The unpooled model would show baseline+20. Assert we're far below that.
    assert peak - baseline <= 6, f"thread delta {peak - baseline} — pool not bounding threads"
    await pool.close()


@pytest.mark.asyncio
async def test_borrow_returns_connection_on_exception(tmp_db_path):
    """AC5: an exception mid-borrow must return the connection to the pool (no leak)."""
    pool = await _make_pool(tmp_db_path, read_size=2)
    # Exhaust-then-fail: raise inside every borrow, then confirm the pool still works.
    for _ in range(5):
        with pytest.raises(ValueError):
            async with pool.borrow(readonly=True) as conn:
                await conn.execute("SELECT 1")
                raise ValueError("boom")
    # If connections leaked, the read pool (size 2) would be empty and this hangs/times out.
    async with pool.borrow(readonly=True) as conn:
        cur = await conn.execute("SELECT 1")
        assert (await cur.fetchone())[0] == 1
    await pool.close()


@pytest.mark.asyncio
async def test_borrow_timeout_raises_not_hangs(tmp_db_path):
    """AC8: exhausted read pool raises PoolBorrowTimeout (backpressure), never hangs."""
    pool = await _make_pool(tmp_db_path, read_size=1)
    pool.borrow_timeout = 0.3  # tighten for the test

    held = asyncio.Event()
    release = asyncio.Event()

    async def hog():
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")
            held.set()
            await release.wait()

    hog_task = asyncio.create_task(hog())
    await held.wait()  # the single read conn is now held
    with pytest.raises(PoolBorrowTimeout):
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")
    release.set()
    await hog_task
    await pool.close()


@pytest.mark.asyncio
async def test_concurrent_read_write_no_busy(tmp_db_path):
    """AC6: interleaved reads + writes produce 0 SQLITE_BUSY and correct results."""
    pool = await _make_pool(tmp_db_path, read_size=4)

    async def writer(i):
        async with pool.borrow(readonly=False) as conn:
            await conn.execute("INSERT INTO t (v) VALUES (?)", (f"v{i}",))
            await conn.commit()

    async def reader():
        async with pool.borrow(readonly=True) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM t")
            await cur.fetchone()

    # Interleave 10 writes + 10 reads concurrently.
    await asyncio.gather(*([writer(i) for i in range(10)] + [reader() for _ in range(10)]))

    async with pool.borrow(readonly=True) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM t")
        assert (await cur.fetchone())[0] == 10
    await pool.close()


@pytest.mark.asyncio
async def test_uncommitted_write_discarded_and_not_leaked(tmp_db_path):
    """Behavior-preservation: an uncommitted write is discarded (old close semantics)
    AND does not leave a dangling transaction on the shared write conn for the next
    borrower (pool-specific hazard)."""
    pool = await _make_pool(tmp_db_path, read_size=2)
    # Write WITHOUT commit, exit the borrow.
    async with pool.borrow(readonly=False) as conn:
        await conn.execute("INSERT INTO t (v) VALUES ('ghost')")
        # no commit — legacy close() would discard this
    # Next borrower must see a CLEAN connection (no dangling txn) and 0 rows.
    async with pool.borrow(readonly=False) as conn:
        assert conn.in_transaction is False, "dangling transaction leaked to next borrower"
        cur = await conn.execute("SELECT COUNT(*) FROM t")
        assert (await cur.fetchone())[0] == 0, "uncommitted write was NOT discarded"
    # A committed write DOES persist.
    async with pool.borrow(readonly=False) as conn:
        await conn.execute("INSERT INTO t (v) VALUES ('real')")
        await conn.commit()
    async with pool.borrow(readonly=True) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM t")
        assert (await cur.fetchone())[0] == 1
    await pool.close()


@pytest.mark.asyncio
async def test_concurrent_first_borrow_starts_pool_once(tmp_db_path):
    """Race: N concurrent first-borrows on an unstarted pool must start it ONCE
    (start() re-check under _start_lock), not double-create connections."""
    pool = _ConnectionPool(tmp_db_path, read_size=4)
    # 10 concurrent borrows on a never-started pool — all race through __aenter__.
    async def touch():
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")

    await asyncio.gather(*[touch() for _ in range(10)])
    # If start() double-ran, _all_conns would exceed 1 write + read_size read.
    assert len(pool._all_conns) <= 1 + pool._read_size, (
        f"pool over-created connections: {len(pool._all_conns)} > {1 + pool._read_size}"
    )
    await pool.close()


@pytest.mark.asyncio
async def test_borrow_after_close_raises(tmp_db_path):
    """Borrow on a closed pool must fail loud (lifecycle bug), not hand a dead conn."""
    pool = await _make_pool(tmp_db_path, read_size=2)
    await pool.close()
    with pytest.raises(RuntimeError, match="closed"):
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")


@pytest.mark.asyncio
async def test_close_drains_connections(tmp_db_path):
    """close() must drain+close all pooled connections (no thread leak across tests)."""
    pool = await _make_pool(tmp_db_path, read_size=4)
    before = threading.active_count()
    await pool.close()
    # Give aiosqlite worker threads a moment to exit.
    await asyncio.sleep(0.2)
    after = threading.active_count()
    assert after <= before, f"threads not drained: before={before} after={after}"


def _live_workers() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if "_connection_worker_thread" in t.name and t.is_alive()]


@pytest.mark.asyncio
async def test_close_during_an_in_flight_start_leaks_no_connection(tmp_db_path):
    """A borrow whose start() is still connecting when close() runs (a background
    task at test teardown or daemon shutdown) must not leave a connection behind:
    each aiosqlite connection owns a NON-daemon worker thread, and one nobody closes
    blocks interpreter exit (the test_session_injected_turn teardown hang)."""
    before = set(_live_workers())
    pool = _ConnectionPool(tmp_db_path, read_size=2)
    started = asyncio.Event()
    real_new_conn = pool._new_conn

    async def slow_new_conn():
        started.set()
        await asyncio.sleep(0.05)   # close() runs while this connect is in flight
        return await real_new_conn()

    pool._new_conn = slow_new_conn

    async def borrow():
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")

    task = asyncio.create_task(borrow())
    await started.wait()
    await pool.close()
    with pytest.raises(RuntimeError, match="closed"):
        await task
    await asyncio.sleep(0.2)
    leaked = set(_live_workers()) - before
    assert not leaked, f"{len(leaked)} connection worker thread(s) outlived close()"
    assert pool._all_conns == []


@pytest.mark.asyncio
async def test_close_during_a_lazy_read_grow_leaks_no_connection(tmp_db_path):
    """The same for a read connection created on demand after start()."""
    pool = await _make_pool(tmp_db_path, read_size=3)
    before = set(_live_workers())
    real_new_conn = pool._new_conn
    started = asyncio.Event()

    async def slow_new_conn():
        started.set()
        await asyncio.sleep(0.05)
        return await real_new_conn()

    held = pool.borrow(readonly=True)
    await held.__aenter__()          # take the primed read conn: the next borrow grows
    pool._new_conn = slow_new_conn

    async def borrow():
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")

    task = asyncio.create_task(borrow())
    await started.wait()
    await held.__aexit__(None, None, None)
    await pool.close()
    with pytest.raises(RuntimeError, match="closed"):
        await task
    await asyncio.sleep(0.2)
    assert not (set(_live_workers()) - before)


def test_close_stops_a_connect_whose_loop_never_runs_again(tmp_db_path):
    """A borrow started by a task on a loop that stops running mid-connect (a
    test's loop after the test; a task pending at daemon shutdown) never resumes,
    so it never adds its connection to the pool: close() must still stop that
    connection's NON-daemon worker thread, or it blocks interpreter exit (the
    test_session_injected_turn teardown hang)."""
    before = set(_live_workers())
    pool = _ConnectionPool(tmp_db_path, read_size=2)
    side = asyncio.new_event_loop()

    async def borrow():
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")

    task = side.create_task(borrow())
    side.run_until_complete(asyncio.sleep(0))   # the borrow is now inside connect
    try:
        asyncio.run(pool.close())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and set(_live_workers()) - before:
            time.sleep(0.05)
        leaked = set(_live_workers()) - before
        assert not leaked, f"{len(leaked)} connection worker thread(s) outlived close()"
    finally:
        task.cancel()
        side.close()


def test_a_connect_cancelled_mid_flight_ends_its_worker_before_the_loop_closes(tmp_db_path):
    """A borrow cancelled while connecting (a test's leftover tasks cancelled at
    teardown; tasks cancelled at daemon shutdown) must end its connection's worker
    on the still-running loop. Left running, the worker answers a loop that then
    closes: call_soon_threadsafe raises, the thread dies, and a later close() of
    anything queued on it waits forever."""
    before = set(_live_workers())
    died: list[str] = []
    prev_hook = threading.excepthook
    threading.excepthook = lambda args: died.append(repr(args.exc_value))
    pool = _ConnectionPool(tmp_db_path, read_size=2)
    loop = asyncio.new_event_loop()
    try:
        async def borrow():
            async with pool.borrow(readonly=True) as conn:
                await conn.execute("SELECT 1")

        async def cancel_mid_connect():
            task = asyncio.create_task(borrow())
            await asyncio.sleep(0)          # inside aiosqlite.connect now
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        loop.run_until_complete(cancel_mid_connect())
    finally:
        loop.close()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and set(_live_workers()) - before:
        time.sleep(0.05)
    threading.excepthook = prev_hook
    assert not died, died
    assert not (set(_live_workers()) - before)


def test_the_aiosqlite_internals_the_pool_relies_on_exist(tmp_db_path):
    """_ConnectionPool._end_worker reads aiosqlite's ``_running`` / ``_thread``
    (unpinned dependency): an upgrade that drops them must show up here, not as a
    silent return of the teardown hang."""
    import aiosqlite

    async def check():
        conn = await aiosqlite.connect(tmp_db_path)
        try:
            assert isinstance(conn._running, bool)
            assert isinstance(conn._thread, threading.Thread) and conn._thread.is_alive()
        finally:
            await conn.close()

    asyncio.run(check())


async def _cancel_inside_nth_connect(pool, n: int, borrow_coro):
    """Run borrow_coro, cancelling it while it is inside the n-th _new_conn call."""
    real = pool._new_conn
    calls = {"n": 0}
    entered = asyncio.Event()

    async def counting():
        calls["n"] += 1
        if calls["n"] == n:
            entered.set()
            await asyncio.sleep(10)        # cancelled here
        return await real()

    pool._new_conn = counting
    task = asyncio.create_task(borrow_coro())
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    pool._new_conn = real


@pytest.mark.asyncio
async def test_a_start_cancelled_at_its_read_connect_leaves_no_extra_connection(tmp_db_path):
    """start() is transactional: cancelled while opening its read connection, it
    keeps no write connection, and the retry ends with 1 write + 1 read."""
    pool = _ConnectionPool(tmp_db_path, read_size=2)

    async def borrow():
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")

    await _cancel_inside_nth_connect(pool, 2, borrow)
    assert pool._all_conns == [] and not pool._started
    await borrow()
    assert len(pool._all_conns) == 2
    await pool.close()


@pytest.mark.asyncio
async def test_a_cancelled_lazy_read_grow_gives_its_slot_back(tmp_db_path):
    """A read grow cancelled mid-connect returns its slot: the pool can still grow
    to read_size instead of waiting out the borrow timeout for a ghost conn."""
    pool = await _make_pool(tmp_db_path, read_size=2)
    pool.borrow_timeout = 0.5
    held = pool.borrow(readonly=True)
    await held.__aenter__()                 # the primed read conn is in use

    async def borrow():
        async with pool.borrow(readonly=True) as conn:
            await conn.execute("SELECT 1")

    await _cancel_inside_nth_connect(pool, 1, borrow)
    assert pool._read_created == 1
    await borrow()                          # grows to the 2nd read conn, no timeout
    await held.__aexit__(None, None, None)
    await pool.close()
