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


# ── AC1: deployment_context detection (internal via ~/.ada|~/.midway) ──

class TestDeploymentContext:
    """auth-hint returns deployment_context so the wizard shows the right
    method cards. Internal = ~/.ada OR ~/.midway present (NOT ~/.toolbox —
    too generic, false-positives external users). Else external."""

    def _hint_with_home(self, client, present: set):
        """Call auth-hint with Path.home() joinpath faked so only *present*
        dot-dirs report is_dir()==True."""
        def joinpath_side_effect(p):
            m = MagicMock()
            m.is_dir.return_value = (p in present)
            m.glob.return_value = []
            return m
        with patch("routers.system.Path") as mock_path_cls:
            home = MagicMock()
            home.joinpath.side_effect = joinpath_side_effect
            mock_path_cls.home.return_value = home
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("ANTHROPIC_API_KEY", None)
                os.environ["SWARMAI_MODE"] = "daemon"
                return client.get("/api/system/auth-hint").json()

    def test_ada_present_is_internal(self, client):
        d = self._hint_with_home(client, {".ada"})
        assert d["deployment_context"] == "internal"

    def test_midway_present_is_internal(self, client):
        d = self._hint_with_home(client, {".midway"})
        assert d["deployment_context"] == "internal"

    def test_no_internal_signals_is_external(self, client):
        d = self._hint_with_home(client, set())
        assert d["deployment_context"] == "external"

    def test_toolbox_alone_is_NOT_internal(self, client):
        # ~/.toolbox is a generic name (non-Amazon tools use it) — must NOT
        # flip an external user to internal (Gate-1 FIX-E).
        d = self._hint_with_home(client, {".toolbox"})
        assert d["deployment_context"] == "external"


# ── AC5: verify-auth is stateless — accepts an optional override body ──

class TestVerifyAuthOverrideBody:
    """POST /api/system/verify-auth accepts an optional JSON body (region,
    use_bedrock, ...) merged over stored config, so a caller can verify a
    NOT-YET-PERSISTED config. Empty/no body falls back to stored config."""

    def test_verify_auth_override_region_used(self, client):
        """Body {region: eu-west-1} → boto3 client built with eu-west-1.

        NOTE: intentionally does NOT mock _get_auth_config (the function under
        change) — it drives the REAL merge path. The fallback base gives
        region us-east-1; the override must win → eu-west-1."""
        mock_response = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "body": MagicMock(read=lambda: b'{"content":[{"text":"hi"}]}'),
        }
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = mock_response

        with patch("routers.system.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_client
            resp = client.post("/api/system/verify-auth",
                               json={"use_bedrock": True, "aws_region": "eu-west-1"})

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # boto3 client MUST be built with the OVERRIDE region, not the stored/fallback.
        _, kwargs = mock_boto3.client.call_args
        assert kwargs.get("region_name") == "eu-west-1"

    def test_verify_auth_empty_body_falls_back_to_stored(self, client):
        """No body (Settings-tab caller) → does NOT crash; uses stored config."""
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
            resp = client.post("/api/system/verify-auth")  # NO body

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        _, kwargs = mock_boto3.client.call_args
        assert kwargs.get("region_name") == "us-east-1"

    def test_verify_auth_non_dict_body_does_not_crash(self, client):
        """A non-dict JSON body (array/string) must NOT 500 — falls back to
        stored config via the isinstance(parsed, dict) guard."""
        mock_response = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "body": MagicMock(read=lambda: b'{"content":[{"text":"hi"}]}'),
        }
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = mock_response
        with patch("routers.system.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_client
            resp = client.post("/api/system/verify-auth", json=["garbage", "array"])
        assert resp.status_code == 200
        # fell back to stored/fallback region, no crash
        _, kwargs = mock_boto3.client.call_args
        assert kwargs.get("region_name") == "us-east-1"

    def test_verify_auth_override_use_bedrock_false_routes_to_apikey(self, client):
        """Body {use_bedrock: false} overrides stored use_bedrock=true →
        routes to the Anthropic API path, not Bedrock."""
        async def mock_verify(config):
            assert config.get("use_bedrock") is False
            return {"success": True, "model": "claude-opus-4-6", "latency_ms": 50}

        # Do NOT mock _get_auth_config — drive the real merge; override flips
        # use_bedrock false so the endpoint routes to the API-key path.
        with patch("routers.system._verify_anthropic_api", side_effect=mock_verify):
            resp = client.post("/api/system/verify-auth", json={"use_bedrock": False})

        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ── AC3: Anthropic-direct key works in-app (env injection + verify reads secret) ──

class TestAnthropicDirectPath:
    """External Anthropic-direct users enter a key in-app; it must (1) be
    usable by the agent at spawn WITHOUT an env var or relaunch, and (2) let
    verify-auth succeed reading the persisted key (not just os.environ)."""

    def test_configure_env_injects_persisted_key_when_not_bedrock(self, tmp_path):
        from core.app_config_manager import AppConfigManager
        from core import claude_environment
        cfg = AppConfigManager(config_path=tmp_path / "config.json")
        cfg.load()
        cfg.update({"use_bedrock": False})
        cfg.set_secret("anthropic_api_key", "sk-ant-injected")
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            claude_environment._configure_claude_environment(cfg)
            # The persisted key must land in os.environ so the SDK subprocess uses it.
            assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-injected"

    def test_configure_env_does_NOT_inject_when_bedrock(self, tmp_path):
        from core.app_config_manager import AppConfigManager
        from core import claude_environment
        cfg = AppConfigManager(config_path=tmp_path / "config.json")
        cfg.load()
        cfg.update({"use_bedrock": True})
        cfg.set_secret("anthropic_api_key", "sk-ant-shouldnotleak")
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            claude_environment._configure_claude_environment(cfg)
            # Bedrock mode must not set the Anthropic key.
            assert os.environ.get("ANTHROPIC_API_KEY") is None

    def test_verify_anthropic_reads_persisted_key(self, client):
        """verify-auth (apikey mode) must succeed with a persisted key even when
        ANTHROPIC_API_KEY is NOT in the environment."""
        async def mock_verify_ok(config):
            # config must carry the key from the secret store, not env
            assert config.get("anthropic_api_key") == "sk-ant-persisted"
            return {"success": True, "model": "claude-opus-4-6", "latency_ms": 40}
        from core.app_config_manager import AppConfigManager
        with patch("routers.system._verify_anthropic_api", side_effect=mock_verify_ok), \
             patch("routers.system._get_auth_config", return_value={
                 "use_bedrock": False, "default_model": "claude-opus-4-6",
                 "anthropic_base_url": None, "anthropic_api_key": "sk-ant-persisted",
             }):
            resp = client.post("/api/system/verify-auth", json={"use_bedrock": False})
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ── AC6: Bedrock verify-auth is bounded (no unbounded hang) ──

class TestVerifyBedrockTimeout:
    def test_verify_bedrock_client_built_with_timeout_config(self, client):
        """_verify_bedrock must construct the boto3 client with an explicit
        botocore Config (connect/read timeout + max_attempts=1) so an
        unreachable AWS returns fast instead of hanging."""
        captured = {}
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "body": MagicMock(read=lambda: b'{"content":[{"text":"hi"}]}'),
        }
        def fake_client(service, **kwargs):
            captured.update(kwargs)
            return mock_client
        with patch("routers.system.boto3") as mock_boto3, \
             patch("routers.system._get_auth_config", return_value={
                 "use_bedrock": True, "aws_region": "us-east-1",
                 "default_model": "claude-opus-4-6", "bedrock_model_map": None,
             }):
            mock_boto3.client.side_effect = fake_client
            resp = client.post("/api/system/verify-auth")
        assert resp.status_code == 200
        # A botocore Config with bounded timeouts must be passed.
        cfg = captured.get("config")
        assert cfg is not None, "boto3 client built without a botocore Config (unbounded hang risk)"
        assert cfg.connect_timeout is not None and cfg.connect_timeout <= 15
        assert cfg.read_timeout is not None and cfg.read_timeout <= 30


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
