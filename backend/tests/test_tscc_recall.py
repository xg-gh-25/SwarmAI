"""Unit tests for the TSCC recall endpoint.

Tests ``GET /api/chat/{session_id}/recall`` in ``routers/tscc.py``:

- snapshot present → ran=True, body/tokens/keywords echoed back
- no snapshot      → neutral default (ran=False), no exception, never 404
- lifecycle pop    → _release_session_state clears recall_snapshot too

The endpoint is READ-ONLY: it reads ``session_registry.recall_snapshot`` and
never triggers recall. These tests seed that dict directly (mirroring the
security-scan test's approach) and clean up in a ``finally`` block.
"""

from fastapi.testclient import TestClient


def _seed(session_id: str, snap: dict):
    from core import session_registry

    session_registry.recall_snapshot[session_id] = snap


def _cleanup(session_id: str):
    from core import session_registry

    session_registry.recall_snapshot.pop(session_id, None)


class TestStructuredRecall:
    """The recall snapshot carries STRUCTURED hits (source/score/domain) — the real
    hits that fed the injected block, powering the mockup's per-source cards."""

    def test_flatten_extracts_source_and_score(self):
        """_flatten_recall_hits turns a BucketedRecall into [{domain,source,score,...}]
        — proving we can serve the mockup's card scores from REAL recall data, not a
        re-run."""
        from core.session_router import _flatten_recall_hits
        from core.recall_multi import BucketedRecall

        result = BucketedRecall(
            query="tscc context",
            buckets={
                "library": [
                    {"source_file": "pure-fs-migration.md", "hybrid_score": 0.63, "text": "two estimators diverge"},
                    {"source_file": "context-arch.md", "fts_score": 0.38, "content": "SOUL/AGENT behavior"},
                ],
                "ddd": [
                    {"heading": "TECH.md § Context", "score": 0.67, "text": "canonical loader"},
                ],
            },
            hit_layers={"library": "fts", "ddd": "keyword"},
        )
        hits = _flatten_recall_hits(result)
        assert len(hits) == 3
        lib = [h for h in hits if h["domain"] == "library"]
        assert lib[0]["source"] == "pure-fs-migration.md"
        assert lib[0]["score"] == 0.63
        assert lib[0]["has_score"] is True
        assert lib[0]["method"] == "fts"
        # heading-based hit (context-arch.md via fts_score)
        assert lib[1]["source"] == "context-arch.md" and lib[1]["score"] == 0.38
        ddd = [h for h in hits if h["domain"] == "ddd"]
        assert ddd[0]["score"] == 0.67 and ddd[0]["source"] == "TECH.md § Context"

    def test_flatten_domains_without_score_marked_has_score_false(self):
        """context_files/session have NO comparable score; codeintel `rank` is a
        raw negative FTS5 value — none should surface a fake [0,1] score."""
        from core.session_router import _flatten_recall_hits
        from core.recall_multi import BucketedRecall

        result = BucketedRecall(
            query="x",
            buckets={
                "context_files": [{"section": "Guidelines", "content": "some memory"}],
                "session": [{"text": "a past chat blob"}],
                "codeintel": [{"name": "foo", "file_path": "a.py", "rank": -8.42}],
            },
            hit_layers={"context_files": "keyword", "session": "fts", "codeintel": "graph"},
        )
        hits = _flatten_recall_hits(result)
        for h in hits:
            assert h["has_score"] is False, f"{h['domain']} must not claim a [0,1] score"
        # sources synthesized/real, never blank
        cf = next(h for h in hits if h["domain"] == "context_files")
        assert cf["source"] == "Guidelines"
        ci = next(h for h in hits if h["domain"] == "codeintel")
        assert ci["source"] == "foo"  # name preferred over synth label

    def test_flatten_never_raises_on_bad_shape(self):
        """A shape surprise must yield [] (or partial), never raise into the recall leg.

        NOTE: both cases here take the HAPPY path — isinstance(h, dict) filters the
        junk hits and a None result short-circuits on getattr. The exception
        handler is covered by the next test, written after the review pointed out
        that neither of these reaches it."""
        from core.session_router import _flatten_recall_hits

        class Weird:
            buckets = {"x": ["not-a-dict", 42, None]}
            hit_layers = {}
        assert _flatten_recall_hits(Weird()) == []
        assert _flatten_recall_hits(None) == []

    def test_flatten_structural_failure_logs_and_returns_partial(self, caplog):
        """ENTERS the exception handler: ``buckets`` is a list, so .items() raises.

        The handler returns what it has, logs, and counts the degradation. This was
        the module's only silent degradation leg, so a structural change in
        BucketedRecall would have shortened the panel's hit list indefinitely with
        nothing in the log to explain it (review run_abab234c, LOW #10)."""
        import logging
        from core.session_router import _flatten_recall_hits

        class BadBuckets:
            buckets = [("library", [{"source": "a.md", "score": 0.5}])]  # a LIST
            hit_layers = {}

        with caplog.at_level(logging.WARNING, logger="core.session_router"):
            hits = _flatten_recall_hits(BadBuckets())

        assert hits == [], "nothing was collected before the structural failure"
        assert any("flattening failed" in r.getMessage() for r in caplog.records), (
            "structural failure must be logged, got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_flatten_takes_no_project_argument(self):
        """``project`` was never read. Dropped rather than left as a dead knob a
        caller could reasonably expect to change the output (LOW #11)."""
        import inspect
        from core.session_router import _flatten_recall_hits

        params = list(inspect.signature(_flatten_recall_hits).parameters)
        assert params == ["result"], f"unexpected signature: {params}"

    def test_structured_hits_roundtrip_through_endpoint(self, client: TestClient):
        """Structured hits stored in the snapshot are returned by GET /recall,
        coerced into RecallHit models (domain/source/score)."""
        _seed("rec-struct", {
            "ran": True,
            "hits": [
                {"domain": "ddd", "source": "TECH.md § Context", "score": 0.67, "has_score": True, "method": "keyword", "text": "loader"},
                {"domain": "library", "source": "arch.md", "score": 0.38, "has_score": True, "method": "fts", "text": "soul/agent"},
            ],
            "body": "",
            "tokens": 88,
            "latency_ms": 210.0,
            "keywords": ["tscc"],
        })
        try:
            resp = client.get("/api/chat/rec-struct/recall")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ran"] is True
            assert len(body["hits"]) == 2
            assert body["hits"][0]["source"] == "TECH.md § Context"
            assert body["hits"][0]["score"] == 0.67
            assert body["hits"][0]["domain"] == "ddd"
        finally:
            _cleanup("rec-struct")


class TestRecallEndpoint:
    """Tests for the GET recall endpoint."""

    def test_snapshot_present_is_echoed(self, client: TestClient):
        _seed(
            "rec-hit",
            {
                "ran": True,
                "body": "> **[RECALLED]** prior context\n### Memory (MEMORY.md)\n- something",
                "tokens": 42,
                "latency_ms": 123.4,
                "keywords": ["tscc", "context", "recall"],
            },
        )
        try:
            resp = client.get("/api/chat/rec-hit/recall")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ran"] is True
            assert body["tokens"] == 42
            assert body["latency_ms"] == 123.4
            assert body["keywords"] == ["tscc", "context", "recall"]
            assert "[RECALLED]" in body["body"]
        finally:
            _cleanup("rec-hit")

    def test_missing_snapshot_returns_neutral_default(self, client: TestClient):
        resp = client.get("/api/chat/no-such-session/recall")
        assert resp.status_code == 200
        body = resp.json()
        # ran=False is a valid state ("no recall this session"), not an error.
        assert body["ran"] is False
        assert body["body"] == ""
        assert body["tokens"] == 0
        assert body["keywords"] == []

    def test_release_session_state_clears_snapshot(self):
        """The lifecycle cleanup must pop recall_snapshot (bounded growth)."""
        from core import session_registry
        from core.lifecycle_manager import LifecycleManager

        session_registry.recall_snapshot["rec-life"] = {"ran": True, "body": "x"}
        # _release_session_state is a staticmethod on LifecycleManager.
        LifecycleManager._release_session_state("rec-life")
        assert "rec-life" not in session_registry.recall_snapshot
