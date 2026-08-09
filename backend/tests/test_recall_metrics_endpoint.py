"""Tests for the recall-metrics visibility ENDPOINTS (unified-recall Run 3).

  - GET /api/recall/metrics — read-only aggregation over the recall_metrics TABLE;
  - GET /api/ddd/brains/{name}/recall — Brain Hub overlay recall (empty-ok contract).

The endpoint tests pin:
  - the response shape {generated_at, contexts:[{context,domain,count,p50_ms,p95_ms}]};
  - READ-ONLY: hitting /api/recall/metrics does NOT drain core.recall_metrics' in-memory
    rings (no double-drain of the flush loop's samples — the endpoint reads the TABLE);
  - BrainHub recall degrades to empty (never 500) and empty q short-circuits.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def metrics_client(tmp_path, monkeypatch):
    """A TestClient for /api/recall with database.db pointed at a fresh temp DB."""
    import asyncio
    from database.sqlite import SQLiteDatabase
    import database
    from routers.recall_metrics_api import router

    db = SQLiteDatabase(str(tmp_path / "m.db"))
    asyncio.get_event_loop().run_until_complete(db.initialize())
    monkeypatch.setattr(database, "db", db, raising=False)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app), db


class TestMetricsEndpoint:
    def test_returns_shape_and_percentiles(self, metrics_client):
        import asyncio
        client, db = metrics_client
        asyncio.get_event_loop().run_until_complete(db.bulk_insert_recall_metrics([
            {"context": "library_overlay", "domains": "library,codeintel",
             "latency_ms": v, "hit_count": 1} for v in (10.0, 20.0, 30.0, 40.0, 50.0)
        ]))
        resp = client.get("/api/recall/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "generated_at" in body and "contexts" in body
        grp = [c for c in body["contexts"] if c["context"] == "library_overlay"]
        assert len(grp) == 1
        assert grp[0]["count"] == 5
        assert grp[0]["p50_ms"] == 30.0
        assert grp[0]["p95_ms"] == 50.0

    def test_context_filter(self, metrics_client):
        import asyncio
        client, db = metrics_client
        asyncio.get_event_loop().run_until_complete(db.bulk_insert_recall_metrics([
            {"context": "session_prompt", "domains": "ddd", "latency_ms": 100.0, "hit_count": 1},
            {"context": "library_overlay", "domains": "library", "latency_ms": 50.0, "hit_count": 1},
        ]))
        resp = client.get("/api/recall/metrics?context=session_prompt")
        assert resp.status_code == 200
        ctxs = resp.json()["contexts"]
        assert all(c["context"] == "session_prompt" for c in ctxs)
        assert len(ctxs) == 1

    def test_empty_returns_empty_contexts(self, metrics_client):
        client, _ = metrics_client
        resp = client.get("/api/recall/metrics")
        assert resp.status_code == 200
        assert resp.json()["contexts"] == []

    def test_endpoint_does_not_drain_rings(self, metrics_client):
        """READ-ONLY contract: the endpoint reads the TABLE, so the in-memory rings
        (which the flush loop drains) must be UNTOUCHED after a GET — else the endpoint
        would steal the flush loop's un-persisted samples (double-drain)."""
        from core import recall_metrics
        recall_metrics.reset_for_test()
        client, _ = metrics_client
        # Record samples into the in-memory rings (NOT yet flushed to the table).
        recall_metrics.record_recall_metric("session_prompt", ("ddd",), 77.0, hit_count=1)
        recall_metrics.record_recall_metric("library_overlay", ("library",), 88.0, hit_count=1)
        # Hit the endpoint — it must NOT drain the rings.
        client.get("/api/recall/metrics")
        remaining = recall_metrics.drain_samples()
        assert len(remaining) == 2, "endpoint must not drain the in-memory rings"


class TestBrainRecallRoute:
    @pytest.fixture
    def brain_client(self):
        from routers.ddd_brain import router
        app = FastAPI()
        app.include_router(router, prefix="/api/ddd")
        return TestClient(app)

    def test_empty_query_short_circuits(self, brain_client):
        # SwarmAI is a real project in the workspace tree.
        resp = brain_client.get("/api/ddd/brains/SwarmAI/recall?q=")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0 and body["hits"] == []

    def test_unknown_brain_404(self, brain_client):
        resp = brain_client.get("/api/ddd/brains/NoSuchBrainXYZ/recall?q=pipeline")
        assert resp.status_code == 404

    def test_recall_returns_hits_shape_and_records_metric(self, brain_client):
        """A real recall over SwarmAI returns the overlay hit shape AND records a
        brainhub_overlay metric sample (visible to the flush loop)."""
        from core import recall_metrics
        recall_metrics.reset_for_test()
        resp = brain_client.get("/api/ddd/brains/SwarmAI/recall?q=pipeline")
        assert resp.status_code == 200
        body = resp.json()
        assert "hits" in body and "count" in body
        for h in body["hits"]:
            assert set(h.keys()) >= {"domain", "title", "source", "content"}
        # The route recorded exactly one brainhub_overlay sample (empty-ok: even 0 hits
        # records a sample, because a non-empty q ran a real recall).
        samples = recall_metrics.drain_samples()
        bh = [s for s in samples if s["context"] == "brainhub_overlay"]
        assert len(bh) == 1, "a non-empty brain recall records one brainhub_overlay metric"

    def test_no_match_query_degrades_to_empty_not_500(self, brain_client):
        resp = brain_client.get("/api/ddd/brains/SwarmAI/recall?q=zzzznomatchqueryxyz")
        assert resp.status_code == 200
        assert resp.json()["hits"] == []
