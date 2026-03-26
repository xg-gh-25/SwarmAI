"""Tests for onboarding bootstrap endpoints.

Validates:
- POST /api/system/verify-auth — real auth verification with mocked providers
- GET /api/system/auth-hint — credential environment detection
- PUT /api/system/onboarding-complete — set flag
- DELETE /api/system/onboarding-complete — reset flag
- GET /api/system/status — includes onboarding_complete field
"""
import os
from unittest.mock import patch, MagicMock

import pytest


# ── AC1: verify-auth returns success/error with model, latency, error taxonomy ──

class TestVerifyAuthBedrock:
    """Test POST /api/system/verify-auth with Bedrock path."""

    def test_verify_auth_bedrock_success(self, client):
        """Happy path: mock boto3 invoke_model returns valid response."""
        mock_response = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "body": MagicMock(read=lambda: b'{"content":[{"text":"hi"}]}'),
        }
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = mock_response

        with patch("routers.system.boto3") as mock_boto3, \
             patch("routers.system._get_auth_config", return_value={
                 "use_bedrock": True, "aws_region": "us-east-1",
                 "default_model": "claude-opus-4-6", "bedrock_model_map": None,
             }):
            mock_boto3.client.return_value = mock_client
            resp = client.post("/api/system/verify-auth")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["model"] == "claude-opus-4-6"
        assert "latency_ms" in data
        assert isinstance(data["latency_ms"], int)

    def test_verify_auth_bedrock_expired(self, client):
        """ExpiredTokenException → error_type: expired_credentials."""
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = Exception("ExpiredTokenException: token has expired")

        with patch("routers.system.boto3") as mock_boto3, \
             patch("routers.system._get_auth_config", return_value={
                 "use_bedrock": True, "aws_region": "us-east-1",
                 "default_model": "claude-opus-4-6", "bedrock_model_map": None,
             }):
            mock_boto3.client.return_value = mock_client
            resp = client.post("/api/system/verify-auth")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_type"] == "expired_credentials"
        assert "fix_hint" in data

    def test_verify_auth_bedrock_access_denied(self, client):
        """Model not enabled → error_type: access_denied."""
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = Exception("AccessDenied: not authorized to access model")

        with patch("routers.system.boto3") as mock_boto3, \
             patch("routers.system._get_auth_config", return_value={
                 "use_bedrock": True, "aws_region": "us-east-1",
                 "default_model": "claude-opus-4-6", "bedrock_model_map": None,
             }):
            mock_boto3.client.return_value = mock_client
            resp = client.post("/api/system/verify-auth")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_type"] == "access_denied"


class TestVerifyAuthAnthropicAPI:
    """Test POST /api/system/verify-auth with Anthropic API key path."""

    def test_verify_auth_apikey_success(self, client):
        """Happy path: mock _verify_anthropic_api returns success."""
        import asyncio

        async def mock_verify(config):
            return {"success": True, "model": "claude-opus-4-6", "latency_ms": 100}

        with patch("routers.system._get_auth_config", return_value={
                 "use_bedrock": False, "default_model": "claude-opus-4-6",
                 "anthropic_base_url": None,
             }), \
             patch("routers.system._verify_anthropic_api", side_effect=mock_verify):
            resp = client.post("/api/system/verify-auth")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["model"] == "claude-opus-4-6"

    def test_verify_auth_apikey_missing(self, client):
        """No API key set → error_type: missing_key."""
        with patch("routers.system._get_auth_config", return_value={
                 "use_bedrock": False, "default_model": "claude-opus-4-6",
                 "anthropic_base_url": None,
             }), \
             patch.dict(os.environ, {}, clear=True):
            # Ensure ANTHROPIC_API_KEY is not set
            os.environ.pop("ANTHROPIC_API_KEY", None)
            resp = client.post("/api/system/verify-auth")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_type"] == "missing_key"

    def test_verify_auth_apikey_invalid(self, client):
        """401 from API → error_type: invalid_key."""
        async def mock_verify(config):
            return {
                "success": False, "error": "Invalid API key",
                "error_type": "invalid_key",
                "fix_hint": "API key is invalid. Check the key at console.anthropic.com.",
            }

        with patch("routers.system._get_auth_config", return_value={
                 "use_bedrock": False, "default_model": "claude-opus-4-6",
                 "anthropic_base_url": None,
             }), \
             patch("routers.system._verify_anthropic_api", side_effect=mock_verify):
            resp = client.post("/api/system/verify-auth")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_type"] == "invalid_key"


# ── AC2: auth-hint detects ADA dir, SSO cache, API key ──

class TestAuthHint:
    """Test GET /api/system/auth-hint."""

    def test_auth_hint_ada_detected(self, client):
        """~/.ada/ exists → suggested_method: ada."""
        with patch("routers.system.Path") as mock_path_cls:
            home_mock = MagicMock()
            home_mock.joinpath.return_value.is_dir.return_value = True  # ~/.ada/ exists
            home_mock.joinpath.return_value.glob.return_value = []  # no SSO cache
            mock_path_cls.home.return_value = home_mock

            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("ANTHROPIC_API_KEY", None)
                resp = client.get("/api/system/auth-hint")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_ada_dir"] is True
        assert data["suggested_method"] == "ada"

    def test_auth_hint_sso_detected(self, client):
        """SSO cache exists, no ADA → suggested_method: sso."""
        with patch("routers.system.Path") as mock_path_cls:
            home_mock = MagicMock()

            def joinpath_side_effect(path):
                m = MagicMock()
                if path == ".ada":
                    m.is_dir.return_value = False
                elif path == ".aws/sso/cache":
                    m.glob.return_value = ["token.json"]
                return m

            home_mock.joinpath.side_effect = joinpath_side_effect
            mock_path_cls.home.return_value = home_mock

            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("ANTHROPIC_API_KEY", None)
                resp = client.get("/api/system/auth-hint")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_sso_cache"] is True
        assert data["suggested_method"] == "sso"

    def test_auth_hint_apikey_detected(self, client):
        """ANTHROPIC_API_KEY set → suggested_method: apikey."""
        with patch("routers.system.Path") as mock_path_cls:
            home_mock = MagicMock()
            home_mock.joinpath.return_value.is_dir.return_value = False
            home_mock.joinpath.return_value.glob.return_value = []
            mock_path_cls.home.return_value = home_mock

            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
                resp = client.get("/api/system/auth-hint")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_api_key"] is True
        assert data["suggested_method"] == "apikey"


# ── AC3: onboarding_complete flag lifecycle ──

class TestOnboardingComplete:
    """Test PUT/DELETE /api/system/onboarding-complete + GET /api/system/status."""

    def test_onboarding_complete_default_false(self, client):
        """Fresh install: onboarding_complete should be false."""
        resp = client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "onboarding_complete" in data
        assert data["onboarding_complete"] is False

    def test_set_onboarding_complete(self, client):
        """PUT sets onboarding_complete to true."""
        resp = client.put("/api/system/onboarding-complete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify it's now true in status
        status_resp = client.get("/api/system/status")
        assert status_resp.json()["onboarding_complete"] is True

    def test_reset_onboarding(self, client):
        """DELETE resets onboarding_complete to false."""
        # First set it
        client.put("/api/system/onboarding-complete")
        # Then reset
        resp = client.delete("/api/system/onboarding-complete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify it's false again
        status_resp = client.get("/api/system/status")
        assert status_resp.json()["onboarding_complete"] is False

    def test_system_status_includes_onboarding(self, client):
        """GET /api/system/status always includes onboarding_complete."""
        resp = client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "onboarding_complete" in data
        assert isinstance(data["onboarding_complete"], bool)
