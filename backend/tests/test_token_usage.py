"""Tests for Token Usage Tracking feature.

Covers:
- DB table creation and recording (record_token_usage, get_token_usage_summary)
- API endpoint (GET /api/system/tokens/usage)
- Fire-and-forget recording doesn't break on failure

Acceptance criteria:
1. Every CLI result event persists token usage to SQLite token_usage table
2. Background boto3 calls also record token usage
3. GET /api/tokens/usage returns today_tokens_m and total_tokens_m in millions
4. TopBar displays real data (frontend — not tested here)
5. Write failure never breaks streaming
"""
import pytest
from unittest.mock import patch

import database as database_module


_N = iter(range(10**9))


async def _spend(db, *, session_id=None, source="cli", input_tokens=0, output_tokens=0,
                 cache_read_tokens=0, cache_create_tokens=0, cost_usd=None, model=None):
    """One usage-ledger spend row (core/usage_accounting), the record the summary reads;
    ``cost_usd`` is the row's own cost."""
    from core import usage_accounting as ua
    row = ua._row(surface="chat" if source == "cli" else "background", model=model,
                  tokens={"input": input_tokens, "output": output_tokens, "cache_read": cache_read_tokens,
                          "cache_write": cache_create_tokens},
                  cost=cost_usd, cost_source="cli", dedup_key=f"t:{next(_N)}", session_id=session_id)
    await db.insert_usage_rows([row])


# ---------------------------------------------------------------------------
# DB-level tests
# ---------------------------------------------------------------------------

class TestTokenUsageDB:
    """Tests for record_token_usage and get_token_usage_summary in SQLiteDatabase."""

    @pytest.mark.asyncio
    async def test_record_token_usage_basic(self):
        """AC1: Record a CLI result event's token usage."""
        db = database_module.db
        await _spend(db, 
            session_id="sess-1",
            source="cli",
            input_tokens=50000,
            output_tokens=10000,
            cache_read_tokens=30000,
            cache_create_tokens=5000,
            cost_usd=0.42,
            model="claude-opus-4-6",
        )
        summary = await db.get_token_usage_summary()
        # the ledger's spend rows; every kind the model bills: input, output, cache reads, cache writes
        assert summary["total_tokens"] == 95000
        assert summary["total_breakdown"] == {"input": 50000, "output": 10000, "cache_read": 30000, "cache_write": 5000}

    @pytest.mark.asyncio
    async def test_record_token_usage_background_job(self):
        """AC2: Record a background boto3 call's token usage."""
        db = database_module.db
        await _spend(db, 
            session_id=None,
            source="background_job",
            input_tokens=1000,
            output_tokens=500,
        )
        summary = await db.get_token_usage_summary()
        assert summary["total_tokens"] == 1500

    @pytest.mark.asyncio
    async def test_get_summary_today_vs_total(self):
        """AC3: Summary distinguishes today's tokens from all-time total."""
        db = database_module.db
        # Insert today's usage
        await _spend(db, 
            session_id="sess-today",
            source="cli",
            input_tokens=1_000_000,
            output_tokens=200_000,
        )
        summary = await db.get_token_usage_summary()
        # Today and total should both include this record
        assert summary["today_tokens"] >= 1_200_000
        assert summary["total_tokens"] >= 1_200_000

    @pytest.mark.asyncio
    async def test_get_summary_returns_millions(self):
        """AC3: Summary provides values convertible to M (millions)."""
        db = database_module.db
        await _spend(db, 
            session_id="sess-x",
            source="cli",
            input_tokens=5_000_000,
            output_tokens=500_000,
        )
        summary = await db.get_token_usage_summary()
        # Raw tokens returned; API layer converts to M
        assert summary["total_tokens"] == 5_500_000
        assert summary["today_tokens"] == 5_500_000

    @pytest.mark.asyncio
    async def test_get_summary_includes_costs(self):
        """Summary includes cost aggregation."""
        db = database_module.db
        await _spend(db, 
            session_id="s1", source="cli",
            input_tokens=100, output_tokens=50, cost_usd=0.10,
        )
        await _spend(db, 
            session_id="s2", source="cli",
            input_tokens=200, output_tokens=100, cost_usd=0.20,
        )
        summary = await db.get_token_usage_summary()
        assert summary["total_tokens"] == 450
        assert abs(summary["total_cost_usd"] - 0.30) < 0.001

    @pytest.mark.asyncio
    async def test_today_is_this_machines_local_day(self):
        """A row from 23:00 local yesterday is in the total, not in today."""
        from datetime import datetime, timedelta, timezone
        from core import usage_accounting as ua
        db = database_module.db
        local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        late = (local_midnight - timedelta(hours=1)).astimezone(timezone.utc).isoformat(timespec="seconds")
        early = (local_midnight + timedelta(minutes=1)).astimezone(timezone.utc).isoformat(timespec="seconds")
        rows = [ua._row(surface="chat", model="m", tokens={"output": n}, cost=c, cost_source="cli",
                        dedup_key=f"day:{n}", at_utc=at) for n, c, at in ((100, 1.0, late), (7, 0.25, early))]
        await db.insert_usage_rows(rows)
        summary = await db.get_token_usage_summary()
        assert (summary["total_tokens"], summary["today_tokens"]) == (107, 7)
        assert (summary["total_cost_usd"], summary["today_cost_usd"]) == (1.25, 0.25)

    @pytest.mark.asyncio
    async def test_get_summary_empty_db(self):
        """Summary returns zeros when no data recorded yet."""
        db = database_module.db
        summary = await db.get_token_usage_summary()
        assert summary["today_tokens"] == 0
        assert summary["total_tokens"] == 0
        assert summary["today_cost_usd"] == 0.0
        assert summary["total_cost_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_record_with_minimal_fields(self):
        """Record works with only required fields (input_tokens, output_tokens)."""
        db = database_module.db
        await _spend(db, 
            session_id=None,
            source="cli",
            input_tokens=100,
            output_tokens=50,
        )
        summary = await db.get_token_usage_summary()
        assert summary["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_multiple_records_accumulate(self):
        """Multiple records sum correctly."""
        db = database_module.db
        for i in range(5):
            await _spend(db, 
                session_id=f"sess-{i}",
                source="cli",
                input_tokens=1000,
                output_tokens=500,
            )
        summary = await db.get_token_usage_summary()
        assert summary["total_tokens"] == 7500  # 5 * (1000 + 500)


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestTokenUsageEndpoint:
    """Tests for GET /api/system/tokens/usage endpoint."""

    def test_endpoint_returns_200(self, client):
        """AC3: Endpoint exists and returns 200."""
        response = client.get("/api/system/tokens/usage")
        assert response.status_code == 200

    def test_response_shape(self, client):
        """AC3: Response has expected fields in millions."""
        response = client.get("/api/system/tokens/usage")
        data = response.json()
        assert "today_tokens_m" in data
        assert "total_tokens_m" in data
        assert "today_cost_usd" in data
        assert "total_cost_usd" in data
        for k in ("today", "total"):
            assert set(data[k]) == {"input", "output", "cache_read", "cache_write", "cache_hit_rate"}

    def test_cache_hit_rate_is_cache_reads_over_input_side_tokens(self, client):
        import asyncio
        asyncio.run(_spend(database_module.db, 
            session_id="hit", source="cli", input_tokens=10, output_tokens=500,
            cache_read_tokens=900, cache_create_tokens=90, cost_usd=0.5))
        data = client.get("/api/system/tokens/usage").json()
        assert data["total"]["cache_hit_rate"] == 0.9   # 900 / (10 + 900 + 90), output not counted

    def test_empty_returns_zeros(self, client):
        """Returns 0.0 when no usage recorded."""
        response = client.get("/api/system/tokens/usage")
        data = response.json()
        assert data["today_tokens_m"] == 0.0
        assert data["total_tokens_m"] == 0.0

    @pytest.mark.asyncio
    async def test_with_recorded_data(self, async_client):
        """Returns correct M values after recording."""
        db = database_module.db
        await _spend(db, 
            session_id="test-sess",
            source="cli",
            input_tokens=2_500_000,
            output_tokens=500_000,
        )
        response = await async_client.get("/api/system/tokens/usage")
        assert response.status_code == 200
        data = response.json()
        assert data["total_tokens_m"] == 3.0  # 3M tokens
        assert data["today_tokens_m"] == 3.0


# ---------------------------------------------------------------------------
# Fire-and-forget safety tests
# ---------------------------------------------------------------------------

class TestTokenUsageFailureSafety:
    """AC5: Write failure never breaks streaming pipeline."""

    @pytest.mark.asyncio
    async def test_record_failure_is_silent(self):
        """If DB write fails, record_token_usage catches and logs, never raises."""
        db = database_module.db
        # Simulate DB error by patching aiosqlite.connect to raise
        with patch("aiosqlite.connect", side_effect=Exception("DB locked")):
            # Should NOT raise — fire-and-forget safety
            await db.record_token_usage(
                session_id="s1", source="cli",
                input_tokens=100, output_tokens=50,
            )
