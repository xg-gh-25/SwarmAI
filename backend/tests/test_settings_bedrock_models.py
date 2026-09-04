"""Tests for GET /api/settings/bedrock/models — Bedrock model auto-discovery.

Verifies the read-only discovery endpoint that lists the account's callable
Claude inference profiles via the daemon's DEFAULT credential chain, so a newly
released Bedrock model appears without a human editing hardcoded tables.

Contract under test:
- Uses ``list_inference_profiles`` (returns directly-callable ids like
  ``us.anthropic.claude-opus-5``), NOT ``list_foundation_models`` (returns
  non-callable ``anthropic.claude-opus-5`` with INFERENCE_PROFILE type).
- Paginates (nextToken) so a newer model is never silently truncated.
- Filters to Claude + SYSTEM_DEFINED; dedups preferring ``us.`` over ``global.``.
- Strips ``us.anthropic.`` → short_name; is_new = not already in available_models.
- Fail-open: on any AWS error returns {available: false, error, models: []} with
  HTTP 200 (never a 5xx, never an empty list masquerading as "no models") so the
  frontend keeps the current list rather than blanking the picker.

# Feature: bedrock-model-autodiscovery
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.settings import router, set_config_manager
from core.app_config_manager import AppConfigManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_probes():
    with patch("routers.settings._probe_aws_credentials", return_value=True), \
         patch("routers.settings._probe_anthropic_api_key", return_value=False):
        yield


@pytest.fixture()
def _isolated_config(tmp_path):
    cfg_path = tmp_path / "config.json"
    mgr = AppConfigManager(config_path=cfg_path)
    mgr.load()
    # Seed available_models so is_new can be computed against a known baseline.
    mgr.update({"available_models": ["claude-opus-4-8"], "default_model": "claude-opus-4-8"})
    set_config_manager(mgr)
    yield mgr
    set_config_manager(None)


@pytest.fixture()
def client(_isolated_config) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/settings")
    with TestClient(app) as tc:
        yield tc


# ---------------------------------------------------------------------------
# Fake Bedrock paginator — two pages, to prove pagination is honored
# ---------------------------------------------------------------------------


def _fake_bedrock_client_two_pages():
    """A boto3 bedrock client stub whose paginator yields TWO pages.

    Page 1 has opus-4-8 (us + global dup) and a non-Claude model; page 2 has
    opus-5 and sonnet-5. If the endpoint does NOT paginate, opus-5/sonnet-5
    (page 2) would be silently dropped — the exact truncation this guards.
    """
    page1 = {"inferenceProfileSummaries": [
        {"inferenceProfileId": "us.anthropic.claude-opus-4-8", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "global.anthropic.claude-opus-4-8", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "us.amazon.nova-pro-v1:0", "type": "SYSTEM_DEFINED"},
    ]}
    page2 = {"inferenceProfileSummaries": [
        {"inferenceProfileId": "us.anthropic.claude-opus-5", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "global.anthropic.claude-sonnet-5", "type": "SYSTEM_DEFINED"},
    ]}
    paginator = MagicMock()
    paginator.paginate = MagicMock(return_value=iter([page1, page2]))
    client = MagicMock()
    client.get_paginator = MagicMock(return_value=paginator)
    return client


# ---------------------------------------------------------------------------
# AC1 — discovery returns callable Claude ids, paginated + deduped
# ---------------------------------------------------------------------------


class TestBedrockModelDiscovery:

    def test_returns_available_true_with_models(self, client: TestClient):
        fake = _fake_bedrock_client_two_pages()
        with patch("routers.settings._bedrock_client", return_value=fake):
            resp = client.get("/api/settings/bedrock/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        shorts = {m["short_name"] for m in data["models"]}
        # opus-5 + sonnet-5 come from PAGE 2 — proves pagination.
        assert "claude-opus-5" in shorts
        assert "claude-sonnet-5" in shorts
        assert "claude-opus-4-8" in shorts

    def test_filters_non_claude(self, client: TestClient):
        fake = _fake_bedrock_client_two_pages()
        with patch("routers.settings._bedrock_client", return_value=fake):
            resp = client.get("/api/settings/bedrock/models")
        shorts = {m["short_name"] for m in resp.json()["models"]}
        assert not any("nova" in s for s in shorts)

    def test_dedups_preferring_us_over_global(self, client: TestClient):
        fake = _fake_bedrock_client_two_pages()
        with patch("routers.settings._bedrock_client", return_value=fake):
            resp = client.get("/api/settings/bedrock/models")
        models = resp.json()["models"]
        opus48 = [m for m in models if m["short_name"] == "claude-opus-4-8"]
        assert len(opus48) == 1, "us.+global. duplicate not deduped"
        assert opus48[0]["bedrock_id"] == "us.anthropic.claude-opus-4-8"

    def test_bedrock_id_is_full_callable_profile_id(self, client: TestClient):
        fake = _fake_bedrock_client_two_pages()
        with patch("routers.settings._bedrock_client", return_value=fake):
            resp = client.get("/api/settings/bedrock/models")
        opus5 = next(m for m in resp.json()["models"] if m["short_name"] == "claude-opus-5")
        assert opus5["bedrock_id"] == "us.anthropic.claude-opus-5"

    def test_is_new_flag_against_available_models(self, client: TestClient):
        # available_models seeded with only claude-opus-4-8 → opus-5 is NEW, opus-4-8 is not.
        fake = _fake_bedrock_client_two_pages()
        with patch("routers.settings._bedrock_client", return_value=fake):
            resp = client.get("/api/settings/bedrock/models")
        by_short = {m["short_name"]: m for m in resp.json()["models"]}
        assert by_short["claude-opus-5"]["is_new"] is True
        assert by_short["claude-opus-4-8"]["is_new"] is False


# ---------------------------------------------------------------------------
# AC2 — fail-open on AWS error
# ---------------------------------------------------------------------------


class TestFrontendContractShape:
    """Layer-4 cross-boundary E2E: the endpoint response shape IS the frontend's
    getBedrockModels() contract. Drives the REAL router+endpoint through the ASGI
    stack (only the far-leaf boto client is mocked, never the seam), and asserts
    every field the frontend mapper reads (short_name/bedrock_id/is_new + the
    available/error envelope). A divergence here breaks the frontend silently.
    """

    def test_response_matches_frontend_mapper_contract(self, client: TestClient):
        fake = _fake_bedrock_client_two_pages()
        with patch("routers.settings._bedrock_client", return_value=fake):
            resp = client.get("/api/settings/bedrock/models")
        data = resp.json()
        # Envelope the frontend destructures (d.available / d.error / d.models):
        assert set(data.keys()) == {"available", "error", "models"}
        # Each model must carry EXACTLY the snake_case keys the mapper reads:
        for m in data["models"]:
            assert set(m.keys()) == {"short_name", "bedrock_id", "is_new"}
            assert isinstance(m["short_name"], str)
            assert isinstance(m["bedrock_id"], str)
            assert isinstance(m["is_new"], bool)


class TestBedrockModelFailOpen:

    def test_aws_error_returns_available_false_http_200(self, client: TestClient):
        def _boom(*a, **k):
            raise RuntimeError("ExpiredTokenException: token expired arn:aws:...:role/x")
        with patch("routers.settings._bedrock_client", side_effect=_boom):
            resp = client.get("/api/settings/bedrock/models")
        assert resp.status_code == 200          # NOT a 5xx
        data = resp.json()
        assert data["available"] is False
        assert data["models"] == []             # empty, but flagged unavailable
        assert "error" in data and data["error"]

    def test_error_does_not_leak_arn_or_secret(self, client: TestClient):
        """The client-facing error must NOT echo the boto exception body — boto
        puts the ARN/account-id in the first chars, so even a truncated prefix
        would leak. We return only the exception TYPE + a fixed hint."""
        long_arn = "arn:aws:iam::123456789012:role/" + ("x" * 400)
        def _boom(*a, **k):
            raise RuntimeError(f"AccessDenied for {long_arn} secret=SHOULDNOTLEAKFULLY")
        with patch("routers.settings._bedrock_client", side_effect=_boom):
            resp = client.get("/api/settings/bedrock/models")
        err = resp.json()["error"]
        assert "arn:aws" not in err, "error leaked an ARN"
        assert "123456789012" not in err, "error leaked an account id"
        assert "SHOULDNOTLEAKFULLY" not in err, "error leaked the exception body"
        assert "RuntimeError" in err, "error should name the exception type"
