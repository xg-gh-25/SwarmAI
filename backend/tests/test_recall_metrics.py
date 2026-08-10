"""Tests for the recall-metrics substrate (core/recall_metrics.py, run_40091f5c).

Unified-recall Run 2 (design Knowledge/Designs/2026-08-09-unified-recall-architecture.md
§3.3-3.4). The substrate records per-recall {context, domains, latency_ms, hit_count,
degraded_reason} into a per-context bounded RING OF SAMPLES (not a counter — p50/p95
can't come from a running sum), lock-guarded because recall is recorded from BOTH an
io-pool thread (session-prompt path) AND the event loop (overlay path), then drained
every 5 min to the recall_metrics table.

These pin the load-bearing behaviors Gate-0 corrected:
  - samples retained (not counted) so percentiles are computable downstream;
  - threading.Lock safety under concurrent record from a thread + the loop;
  - drain = atomic swap-and-clear (a record during drain is never silently lost twice);
  - fire-and-forget: a broken record MUST NOT raise into the recall path.
"""
import threading

import pytest


def _reset():
    from core import recall_metrics
    recall_metrics.reset_for_test()
    return recall_metrics


def test_record_retains_samples_not_counts():
    """A running COUNT can't yield p50/p95 — the substrate must retain individual
    latency SAMPLES per context (Gate-0 samples-vs-counters correction)."""
    rm = _reset()
    rm.record_recall_metric("session_prompt", ("ddd",), 100.0, hit_count=3)
    rm.record_recall_metric("session_prompt", ("ddd",), 300.0, hit_count=1)
    samples = rm.drain_samples()
    lat = sorted(s["latency_ms"] for s in samples if s["context"] == "session_prompt")
    assert lat == [100.0, 300.0], "both individual samples retained, not summed"


def test_each_sample_carries_its_own_record_time_timestamp():
    """Bug fix: the timestamp must be stamped at MEASUREMENT time (per sample), not
    at flush time. A flush-time timestamp collapsed a whole 5-min window onto one
    instant, so the table could not answer WHEN recall was slow. Each drained sample
    must carry a non-empty 'timestamp' in the expected shape."""
    import re
    rm = _reset()
    rm.record_recall_metric("session_prompt", ("ddd",), 100.0)
    rm.record_recall_metric("session_prompt", ("ddd",), 200.0)
    samples = rm.drain_samples()
    assert len(samples) == 2
    for s in samples:
        assert s.get("timestamp"), "each sample must carry its own record-time timestamp"
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", s["timestamp"]), \
            f"unexpected timestamp shape: {s['timestamp']!r}"


def test_drain_is_swap_and_clear():
    """drain returns the buffered samples AND empties the ring — a second drain
    with no new records returns nothing (no double-count, no residue)."""
    rm = _reset()
    rm.record_recall_metric("library_overlay", ("library", "codeintel"), 42.0)
    first = rm.drain_samples()
    assert len(first) == 1
    second = rm.drain_samples()
    assert second == [], "ring cleared after drain — samples not re-emitted"


def test_record_never_raises_fire_and_forget():
    """A metric failure must NEVER propagate into recall (fire-and-forget, mirrors
    _record_recall_degraded). Even a garbage arg is swallowed."""
    rm = _reset()
    # domains as a non-iterable-ish / weird value must not blow up the caller
    rm.record_recall_metric("session_prompt", None, float("nan"))  # must not raise
    # a truly broken internal state still must not raise
    rm.record_recall_metric("x", ("a",), 1.0)
    # no assertion on internals — the contract is "does not raise"


def test_concurrent_record_from_thread_and_main_loses_nothing():
    """Recorded from a background thread (io-pool analogue) AND the main thread
    (event-loop analogue) concurrently — the lock must let every sample land."""
    rm = _reset()
    N = 200

    def _worker(ctx):
        for i in range(N):
            rm.record_recall_metric(ctx, ("ddd",), float(i))

    t = threading.Thread(target=_worker, args=("session_prompt",))
    t.start()
    _worker("library_overlay")  # main thread
    t.join()

    samples = rm.drain_samples()
    n_session = sum(1 for s in samples if s["context"] == "session_prompt")
    n_library = sum(1 for s in samples if s["context"] == "library_overlay")
    # Ring may cap per-context (bounded), but with maxlen >= N both should be full;
    # the invariant under test is NO CORRUPTION / NO CRASH and both contexts present.
    assert n_library == N, f"main-thread samples all landed, got {n_library}"
    assert n_session == N, f"worker-thread samples all landed, got {n_session}"


def test_domains_normalized_to_stable_string():
    """domains is stored as a stable string (csv/json) so the table column is a
    simple TEXT — a tuple must serialize deterministically."""
    rm = _reset()
    rm.record_recall_metric("cli", ("library", "codeintel"), 10.0)
    s = rm.drain_samples()[0]
    assert isinstance(s["domains"], str), "domains serialized to TEXT for the table"
    assert "library" in s["domains"] and "codeintel" in s["domains"]


class TestRecallMetricsTable:
    """v7 migration + bulk_insert_recall_metrics writer (row-per-recall)."""

    @pytest.mark.asyncio
    async def test_migration_creates_recall_metrics_at_v7(self, tmp_path):
        from database.sqlite import SQLiteDatabase, CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION >= 7, "schema bumped to >=7 for recall_metrics"
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        from database.sqlite import _get_pool
        async with _get_pool(str(db.db_path)).borrow(readonly=True) as conn:
            cur = await conn.execute("PRAGMA user_version")
            ver = (await cur.fetchone())[0]
            assert ver >= 7
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='recall_metrics'")
            assert await cur.fetchone() is not None, "recall_metrics table exists"

    @pytest.mark.asyncio
    async def test_bulk_insert_writes_one_row_per_sample(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        samples = [
            {"context": "session_prompt", "domains": "ddd", "latency_ms": 120.0, "hit_count": 2},
            {"context": "library_overlay", "domains": "library,codeintel", "latency_ms": 55.5,
             "hit_count": 0, "degraded_reason": "empty_with_keywords"},
        ]
        n = await db.bulk_insert_recall_metrics(samples)
        assert n == 2, "one row per sample (raw, not aggregated)"
        from database.sqlite import _get_pool
        async with _get_pool(str(db.db_path)).borrow(readonly=True) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM recall_metrics")
            assert (await cur.fetchone())[0] == 2

    @pytest.mark.asyncio
    async def test_bulk_insert_empty_is_noop(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        assert await db.bulk_insert_recall_metrics([]) == 0


class TestFlush:
    """flush_once: drain the rings → batch-write to recall_metrics, rings emptied."""

    @pytest.mark.asyncio
    async def test_flush_once_drains_to_table_and_empties_ring(self, tmp_path, monkeypatch):
        from database.sqlite import SQLiteDatabase, _get_pool
        from core import recall_metrics
        recall_metrics.reset_for_test()
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        # Point the module-level singleton the flush uses at our tmp db.
        import database
        monkeypatch.setattr(database, "db", db, raising=False)

        recall_metrics.record_recall_metric("session_prompt", ("ddd",), 90.0, hit_count=1)
        recall_metrics.record_recall_metric("library_overlay", ("library",), 40.0, hit_count=3)
        n = await recall_metrics.flush_once()
        assert n == 2, "both samples flushed to the table"
        # ring emptied by the drain inside flush_once
        assert recall_metrics.drain_samples() == [], "rings cleared after flush"
        async with _get_pool(str(db.db_path)).borrow(readonly=True) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM recall_metrics")
            assert (await cur.fetchone())[0] == 2

    @pytest.mark.asyncio
    async def test_flush_once_empty_is_noop(self, monkeypatch):
        from core import recall_metrics
        recall_metrics.reset_for_test()
        assert await recall_metrics.flush_once() == 0

    @pytest.mark.asyncio
    async def test_failed_write_requeues_window_not_lost(self, monkeypatch):
        """A DB write that fails must NOT lose the drained window: drain empties the
        ring, so the samples are re-queued for the next flush instead of vanishing."""
        from core import recall_metrics

        class _FailingDB:
            async def bulk_insert_recall_metrics(self, samples):
                raise RuntimeError("db down")

            async def prune_recall_metrics(self, days):
                return 0

        recall_metrics.reset_for_test()
        import database
        monkeypatch.setattr(database, "db", _FailingDB(), raising=False)
        recall_metrics.record_recall_metric("session_prompt", ("ddd",), 90.0, hit_count=1)
        n = await recall_metrics.flush_once()
        assert n == 0, "failed write reports 0 written"
        # The window survived: it's back in the ring for the next flush, not dropped.
        remaining = recall_metrics.drain_samples()
        assert len(remaining) == 1 and remaining[0]["latency_ms"] == 90.0

    @pytest.mark.asyncio
    async def test_write_returning_zero_on_nonempty_requeues(self, monkeypatch):
        """bulk_insert swallows DB errors and returns 0. A 0 for a NON-empty window
        means the write dropped it → requeue (not the same as 'nothing to write')."""
        from core import recall_metrics

        class _SilentDropDB:
            async def bulk_insert_recall_metrics(self, samples):
                return 0  # internal error swallowed → 0 despite real samples

            async def prune_recall_metrics(self, days):
                return 0

        recall_metrics.reset_for_test()
        import database
        monkeypatch.setattr(database, "db", _SilentDropDB(), raising=False)
        recall_metrics.record_recall_metric("session_prompt", ("ddd",), 12.0)
        await recall_metrics.flush_once()
        remaining = recall_metrics.drain_samples()
        assert len(remaining) == 1, "a 0-write of a non-empty window must requeue"
