"""Tests for GET /api/eval/context-health/lite — the thin first-paint endpoint.

Testing methodology: FastAPI TestClient (same pattern as test_hook_health_endpoint.py).
The lite endpoint exists so the C&M Global Brain overlay's first paint is instant: it
returns ONLY the calibrated token_block + a governance count, and MUST NOT run the heavy ops the full /context-health endpoint does
(_ch_ddd_staleness / _build_learning_dashboard). NOTE: the
refresh_log key still exists in the full payload (default []) but its read op was
removed with the auto_refresh module (run_781ffbd9); it remains in HEAVY_KEYS
because the lite endpoint must still never surface it.

Key properties verified:
  - lite returns exactly {token_block, governance_pending_count} (AC1)
  - NONE of the heavy full-endpoint keys are present (AC2)
  - token_block matches build_context_token_block (AC3)
  - governance_pending_count == get_pending_governance()["total"] (AC5)
  - fail-soft: no workspace / read error -> 200 with safe defaults, never 500 (AC6)
  - regression: the full /context-health endpoint is unchanged (AC7)
"""

import pytest
from fastapi.testclient import TestClient

LITE = "/api/eval/context-health/lite"
FULL = "/api/eval/context-health"

# The 5 heavy fields the lite endpoint must NEVER include.
HEAVY_KEYS = {"refresh_log", "staleness", "learning_dashboard", "weeks_available"}
LITE_KEYS = {"token_block", "governance_pending_count"}


@pytest.fixture
def client():
    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestContextHealthLiteShape:
    def test_lite_returns_200_with_exactly_the_two_keys(self, client):
        """AC1: lite returns {token_block, governance_pending_count}."""
        r = client.get(LITE)
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == LITE_KEYS, f"unexpected keys: {set(body.keys())}"

    def test_lite_omits_all_heavy_fields(self, client):
        """AC2: none of the 5 heavy full-endpoint fields appear in lite."""
        body = client.get(LITE).json()
        assert HEAVY_KEYS.isdisjoint(body.keys()), (
            f"lite leaked heavy keys: {HEAVY_KEYS & set(body.keys())}"
        )

    def test_governance_count_is_int(self, client):
        """AC5 (shape): governance_pending_count is an int, not a list/None."""
        body = client.get(LITE).json()
        assert isinstance(body["governance_pending_count"], int)

class TestContextHealthLiteMatchesSources:
    def test_governance_count_matches_service_total(self, client):
        """AC5: governance_pending_count == get_pending_governance()['total']."""
        from core.eval_service import get_eval_service
        expected = get_eval_service().get_pending_governance()["total"]
        body = client.get(LITE).json()
        assert body["governance_pending_count"] == expected

    def test_token_block_matches_builder_when_workspace_present(self, client, monkeypatch):
        """AC3: token_block equals build_context_token_block output (same total_tokens)."""
        from core.initialization_manager import initialization_manager
        ws = initialization_manager.get_cached_workspace_path()
        if not ws:
            pytest.skip("no workspace resolved in this environment")
        from pathlib import Path
        from core.context_brain import build_context_token_block
        expected = build_context_token_block(Path(ws) / ".context")
        body = client.get(LITE).json()
        assert body["token_block"] is not None
        assert body["token_block"]["total_tokens"] == expected["total_tokens"]
        assert len(body["token_block"]["per_file"]) == len(expected["per_file"])

class TestContextHealthLiteFailSoft:
    def test_no_workspace_returns_safe_defaults_not_500(self, client, monkeypatch):
        """AC6: when the workspace can't be resolved, lite returns 200 with nulls/zeros."""
        from core.initialization_manager import initialization_manager
        monkeypatch.setattr(
            initialization_manager, "get_cached_workspace_path", lambda: None
        )
        r = client.get(LITE)
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "token_block": None,
            "governance_pending_count": 0,
        }

    def test_builder_error_fails_soft_to_null_not_500(self, client, monkeypatch):
        """AC6: if build_context_token_block raises, token_block is null and status is 200."""
        import routers.eval as eval_mod
        def _boom(*a, **k):
            raise RuntimeError("simulated token block failure")
        monkeypatch.setattr(eval_mod, "build_context_token_block", _boom, raising=False)
        r = client.get(LITE)
        assert r.status_code == 200
        assert r.json()["token_block"] is None

    def test_governance_missing_total_key_fails_soft_to_zero(self, client, monkeypatch):
        """AC6 (Gate-2 Gap-3): a malformed governance dict (no 'total') → count 0, 200."""
        from core.eval_service import get_eval_service
        svc = get_eval_service()
        monkeypatch.setattr(svc, "get_pending_governance", lambda: {"proposals": []})
        r = client.get(LITE)
        assert r.status_code == 200
        assert r.json()["governance_pending_count"] == 0

class TestFullEndpointUnchanged:
    def test_full_endpoint_still_has_heavy_fields(self, client):
        """AC7 regression: /context-health still returns the full schema."""
        body = client.get(FULL).json()
        # These keys are the full endpoint's contract for EvalDashboard.
        for k in ("refresh_log", "staleness"):
            assert k in body, f"full endpoint lost key {k}"

    def test_neither_endpoint_carries_pending_proposals(self, client):
        """The proposal review queue is gone: no payload advertises it."""
        assert "pending_proposals" not in client.get(FULL).json()
        assert "pending_proposals" not in client.get(LITE).json()
