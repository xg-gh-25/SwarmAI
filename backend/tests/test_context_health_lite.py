"""Tests for GET /api/eval/context-health/lite — the thin first-paint endpoint.

Testing methodology: FastAPI TestClient (same pattern as test_hook_health_endpoint.py).
The lite endpoint exists so the C&M Global Brain overlay's first paint is instant: it
returns ONLY the calibrated token_block + the pending_proposals list + a governance
count, and MUST NOT run the 5 heavy ops the full /context-health endpoint does
(read_refresh_log / _ch_ddd_staleness / get_semantic_drift / _build_learning_dashboard).

Key properties verified:
  - lite returns exactly {token_block, pending_proposals, governance_pending_count} (AC1)
  - NONE of the heavy full-endpoint keys are present (AC2)
  - token_block matches build_context_token_block (AC3)
  - pending_proposals uses the same mapping+cap as the full endpoint (AC4)
  - governance_pending_count == get_pending_governance()["total"] (AC5)
  - fail-soft: no workspace / read error -> 200 with safe defaults, never 500 (AC6)
  - regression: the full /context-health endpoint is unchanged (AC7)
"""

import pytest
from fastapi.testclient import TestClient

LITE = "/api/eval/context-health/lite"
FULL = "/api/eval/context-health"

# The 5 heavy fields the lite endpoint must NEVER include.
HEAVY_KEYS = {"refresh_log", "staleness", "semantic_drift", "learning_dashboard", "weeks_available"}
LITE_KEYS = {"token_block", "pending_proposals", "governance_pending_count"}


@pytest.fixture
def client():
    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestContextHealthLiteShape:
    def test_lite_returns_200_with_exactly_the_three_keys(self, client):
        """AC1: lite returns {token_block, pending_proposals, governance_pending_count}."""
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

    def test_pending_proposals_is_list(self, client):
        body = client.get(LITE).json()
        assert isinstance(body["pending_proposals"], list)


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

    def test_pending_proposals_shape_matches_full_endpoint(self, client):
        """AC4: lite's pending_proposals items have the same fields as the full endpoint."""
        lite_props = client.get(LITE).json()["pending_proposals"]
        full_props = client.get(FULL).json().get("pending_proposals", [])
        # Same mapping + cap → identical content for the same underlying data.
        assert lite_props == full_props


class TestFormatProposalsHelper:
    """Non-vacuous coverage of the shared mapping (Gate-2 HIGH-2: the endpoint
    parity test passes vacuously when 0 proposals are pending in the env, so drive
    the helper directly with synthetic proposals)."""

    def _make(self, n):
        from core.ddd_cultivation import CultivationProposal
        return [
            CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Failed",
                content="X" * 500,  # long → must be truncated to 200
                source_run_id="run_test",
                confidence=0.5,
                id=f"proposal_{i:02d}",
            )
            for i in range(n)
        ]

    def test_maps_all_six_fields(self):
        from routers.eval import _format_proposals_for_health
        out = _format_proposals_for_health(self._make(1))
        assert set(out[0].keys()) == {
            "id", "target_doc", "target_section", "content", "created_at", "confidence",
        }
        assert out[0]["id"] == "proposal_00"
        assert out[0]["target_doc"] == "IMPROVEMENT.md"

    def test_content_truncated_to_200(self):
        from routers.eval import _format_proposals_for_health
        out = _format_proposals_for_health(self._make(1))
        assert len(out[0]["content"]) == 200

    def test_caps_at_ten(self):
        from routers.eval import _format_proposals_for_health
        out = _format_proposals_for_health(self._make(25))
        assert len(out) == 10
        assert out[-1]["id"] == "proposal_09"  # first 10, order preserved

    def test_empty_input_empty_output(self):
        from routers.eval import _format_proposals_for_health
        assert _format_proposals_for_health([]) == []


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
            "pending_proposals": [],
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

    def test_proposals_read_error_fails_soft_to_empty(self, client, monkeypatch):
        """AC6 (Gate-2 Gap-2): read_pending_proposals raising → [] proposals, 200."""
        import core.ddd_cultivation as ddd_mod
        def _boom(*a, **k):
            raise RuntimeError("simulated proposals read failure")
        monkeypatch.setattr(ddd_mod, "read_pending_proposals", _boom, raising=False)
        r = client.get(LITE)
        assert r.status_code == 200
        assert r.json()["pending_proposals"] == []


class TestFullEndpointUnchanged:
    def test_full_endpoint_still_has_heavy_fields(self, client):
        """AC7 regression: /context-health still returns the full schema."""
        body = client.get(FULL).json()
        # These keys are the full endpoint's contract for EvalDashboard.
        for k in ("refresh_log", "staleness", "pending_proposals", "semantic_drift"):
            assert k in body, f"full endpoint lost key {k}"
