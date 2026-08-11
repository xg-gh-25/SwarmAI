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
