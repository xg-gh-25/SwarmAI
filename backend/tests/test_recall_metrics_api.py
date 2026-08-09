"""Tests for the recall-metrics VISIBILITY layer (unified-recall Run 3, run_35f42b75).

Run 3 adds the READ side on top of Run 2's collecting substrate:
  - db.get_recall_metrics_summary(window_hours, context) — count/p50/p95 latency by
    (context, domain), percentiles computed READ-SIDE in Python from raw rows
    (SQLite has no percentile fn; design §3.3 forbids pre-aggregation);
  - db.prune_recall_metrics(days) — age-based retention DELETE (bounds unbounded growth);
  - recall_metrics.flush_once() now prunes AFTER inserting (retention folded into the
    existing 5-min loop, no new scheduler);
  - GET /api/recall/metrics — read-only endpoint over the TABLE (never the in-memory rings).

These pin the load-bearing behaviors Gate-1 flagged:
  - percentiles match the manual percentile of the inserted rows (correctness);
  - the endpoint reads the TABLE and does NOT drain the in-memory rings (no double-drain);
  - the prune cutoff is formatted LOCAL-NAIVE '%Y-%m-%dT%H:%M:%S' to MATCH the writer
    (bulk_insert_recall_metrics, sqlite.py) — NOT a UTC-suffixed form, or the lexicographic
    string compare would delete the wrong rows (Gate-1 SSA(b) latent bug).
"""
import pytest


class TestPercentileSummary:
    """get_recall_metrics_summary computes count/p50/p95 read-side from raw rows."""

    @pytest.mark.asyncio
    async def test_percentiles_match_manual(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        # 5 known latencies for one (context, domain): [10,20,30,40,50]
        samples = [
            {"context": "library_overlay", "domains": "library,codeintel",
             "latency_ms": v, "hit_count": 1}
            for v in (10.0, 20.0, 30.0, 40.0, 50.0)
        ]
        await db.bulk_insert_recall_metrics(samples)
        rows = await db.get_recall_metrics_summary()
        # exactly one group (library_overlay, "library,codeintel")
        grp = [r for r in rows if r["context"] == "library_overlay"]
        assert len(grp) == 1, "one group per (context, domain)"
        g = grp[0]
        assert g["count"] == 5
        # nearest-rank percentile: p50 of [10..50] = 30, p95 = 50
        assert g["p50_ms"] == 30.0, f"p50 wrong: {g['p50_ms']}"
        assert g["p95_ms"] == 50.0, f"p95 wrong: {g['p95_ms']}"

    @pytest.mark.asyncio
    async def test_groups_by_context_and_domain(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        await db.bulk_insert_recall_metrics([
            {"context": "session_prompt", "domains": "ddd", "latency_ms": 100.0, "hit_count": 1},
            {"context": "session_prompt", "domains": "ddd", "latency_ms": 200.0, "hit_count": 1},
            {"context": "library_overlay", "domains": "library,codeintel", "latency_ms": 50.0, "hit_count": 1},
        ])
        rows = await db.get_recall_metrics_summary()
        keys = {(r["context"], r["domain"]) for r in rows}
        assert ("session_prompt", "ddd") in keys
        assert ("library_overlay", "library,codeintel") in keys
        sp = next(r for r in rows if r["context"] == "session_prompt")
        assert sp["count"] == 2

    @pytest.mark.asyncio
    async def test_context_filter(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        await db.bulk_insert_recall_metrics([
            {"context": "session_prompt", "domains": "ddd", "latency_ms": 100.0, "hit_count": 1},
            {"context": "library_overlay", "domains": "library", "latency_ms": 50.0, "hit_count": 1},
        ])
        rows = await db.get_recall_metrics_summary(context="library_overlay")
        assert all(r["context"] == "library_overlay" for r in rows)
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_empty_table_returns_empty(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        assert await db.get_recall_metrics_summary() == []


class TestRetentionPrune:
    """prune_recall_metrics deletes rows older than N days — cutoff must match the
    writer's LOCAL-NAIVE timestamp format (Gate-1 SSA(b))."""

    @pytest.mark.asyncio
    async def test_prune_deletes_old_keeps_new(self, tmp_path):
        from database.sqlite import SQLiteDatabase, _get_pool
        from datetime import datetime, timedelta
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        # Insert directly with controlled timestamps in the WRITER's local-naive format.
        old_ts = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%S")
        new_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        async with _get_pool(str(db.db_path)).borrow(readonly=False) as conn:
            await conn.executemany(
                "INSERT INTO recall_metrics (timestamp, context, domains, latency_ms, hit_count) "
                "VALUES (?,?,?,?,?)",
                [(old_ts, "library_overlay", "library", 10.0, 1),
                 (new_ts, "library_overlay", "library", 20.0, 1)],
            )
            await conn.commit()
        pruned = await db.prune_recall_metrics(days=30)
        assert pruned == 1, "exactly the 40-day-old row pruned"
        async with _get_pool(str(db.db_path)).borrow(readonly=True) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM recall_metrics")
            assert (await cur.fetchone())[0] == 1, "the recent row survives"
            cur = await conn.execute("SELECT latency_ms FROM recall_metrics")
            assert (await cur.fetchone())[0] == 20.0, "the surviving row is the NEW one"

    @pytest.mark.asyncio
    async def test_ddl_default_timestamp_is_prune_compatible(self, tmp_path):
        """The recall_metrics DDL default (fallback when an insert omits timestamp)
        must be LOCAL-NAIVE, matching the writer — a UTC-'Z' default would sort after
        every written row and escape the prune (Run-3 review LOW). Insert omitting
        timestamp → the default fires → prune(30) must NOT delete it (it's 'now')."""
        from database.sqlite import SQLiteDatabase, _get_pool
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        async with _get_pool(str(db.db_path)).borrow(readonly=False) as conn:
            # Omit timestamp → DDL DEFAULT supplies it.
            await conn.execute(
                "INSERT INTO recall_metrics (context, domains, latency_ms, hit_count) "
                "VALUES (?,?,?,?)", ("session_prompt", "ddd", 5.0, 1))
            await conn.commit()
            cur = await conn.execute("SELECT timestamp FROM recall_metrics")
            ts = (await cur.fetchone())[0]
        assert "Z" not in ts, f"default must be local-naive (no Z), got {ts!r}"
        assert await db.prune_recall_metrics(days=30) == 0, "default-timestamped row is 'now', not pruned"

    @pytest.mark.asyncio
    async def test_prune_cutoff_matches_writer_local_format(self, tmp_path):
        """Regression for Gate-1 SSA(b): a row written by the REAL writer
        (bulk_insert_recall_metrics, local-naive '%Y-%m-%dT%H:%M:%S') that is <30d old
        must NOT be pruned. A UTC-suffixed cutoff ('...+00:00'/'...Z') would string-compare
        WRONG against the writer's suffix-less rows and could delete a fresh row."""
        from database.sqlite import SQLiteDatabase, _get_pool
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        # Written by the real writer → real local-naive format, timestamp = now.
        await db.bulk_insert_recall_metrics([
            {"context": "session_prompt", "domains": "ddd", "latency_ms": 90.0, "hit_count": 1},
        ])
        pruned = await db.prune_recall_metrics(days=30)
        assert pruned == 0, "a freshly-written row must not be pruned (cutoff format matches writer)"
        async with _get_pool(str(db.db_path)).borrow(readonly=True) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM recall_metrics")
            assert (await cur.fetchone())[0] == 1, "fresh row survives prune"


class TestFlushPrunes:
    """flush_once prunes after inserting — retention folded into the existing loop."""

    @pytest.mark.asyncio
    async def test_flush_once_prunes_old_rows(self, tmp_path, monkeypatch):
        from database.sqlite import SQLiteDatabase, _get_pool
        from datetime import datetime, timedelta
        from core import recall_metrics
        recall_metrics.reset_for_test()
        db = SQLiteDatabase(str(tmp_path / "d.db"))
        await db.initialize()
        import database
        monkeypatch.setattr(database, "db", db, raising=False)
        # Plant an ancient row directly.
        old_ts = (datetime.now() - timedelta(days=99)).strftime("%Y-%m-%dT%H:%M:%S")
        async with _get_pool(str(db.db_path)).borrow(readonly=False) as conn:
            await conn.execute(
                "INSERT INTO recall_metrics (timestamp, context, domains, latency_ms, hit_count) "
                "VALUES (?,?,?,?,?)", (old_ts, "session_prompt", "ddd", 1.0, 0))
            await conn.commit()
        # A fresh recall → flush inserts it AND prunes the ancient row.
        recall_metrics.record_recall_metric("library_overlay", ("library",), 40.0, hit_count=1)
        await recall_metrics.flush_once()
        async with _get_pool(str(db.db_path)).borrow(readonly=True) as conn:
            cur = await conn.execute("SELECT context FROM recall_metrics")
            ctxs = [r[0] for r in await cur.fetchall()]
        assert "session_prompt" not in ctxs, "ancient (99d) row pruned by flush"
        assert "library_overlay" in ctxs, "freshly-flushed row retained"
