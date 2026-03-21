"""Unit tests for _configure_claude_environment() after AppConfigManager migration.

Tests the updated function that reads from AppConfigManager in-memory cache
instead of the database. Validates:

- Bedrock toggle sets/removes CLAUDE_CODE_USE_BEDROCK
- AWS_REGION and AWS_DEFAULT_REGION set from config when Bedrock enabled
- ANTHROPIC_BASE_URL set/removed based on config
- AWS credential env vars are NEVER set by this function
- AuthenticationNotConfiguredError raised when no API key and Bedrock disabled

**Validates: Requirements 2.3, 13.1, 13.2, 13.3, 13.5, 13.6, 13.7**
"""
import os
import pytest

from core.claude_environment import (
    _configure_claude_environment,
    AuthenticationNotConfiguredError,
)
from core.app_config_manager import AppConfigManager


def _make_config(overrides: dict | None = None) -> AppConfigManager:
    """Create an AppConfigManager with a pre-populated in-memory cache."""
    cfg = AppConfigManager.__new__(AppConfigManager)
    cfg._cache = {
        "use_bedrock": True,
        "aws_region": "us-east-1",
        "anthropic_base_url": None,
    }
    if overrides:
        cfg._cache.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# Env-var save/restore fixture
# ---------------------------------------------------------------------------
MANAGED_VARS = [
    "CLAUDE_CODE_USE_BEDROCK",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    # Credential vars that must NEVER be set
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
]


@pytest.fixture(autouse=True)
def _clean_env():
    """Save and restore env vars around every test."""
    saved = {k: os.environ.get(k) for k in MANAGED_VARS}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


# ---------------------------------------------------------------------------
# Bedrock toggle (Req 13.1, 13.2)
# ---------------------------------------------------------------------------
class TestBedrockToggle:
    def test_bedrock_enabled_sets_env_vars(self):
        """Req 13.1: Bedrock enabled → set CLAUDE_CODE_USE_BEDROCK, AWS_REGION, AWS_DEFAULT_REGION."""
        cfg = _make_config({"use_bedrock": True, "aws_region": "eu-west-1"})
        _configure_claude_environment(cfg)

        assert os.environ["CLAUDE_CODE_USE_BEDROCK"] == "true"
        assert os.environ["AWS_REGION"] == "eu-west-1"
        assert os.environ["AWS_DEFAULT_REGION"] == "eu-west-1"

    def test_bedrock_disabled_removes_env_var(self):
        """Req 13.2: Bedrock disabled → remove CLAUDE_CODE_USE_BEDROCK."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "true"
        cfg = _make_config({"use_bedrock": False})
        _configure_claude_environment(cfg)

        assert "CLAUDE_CODE_USE_BEDROCK" not in os.environ

    def test_bedrock_default_region_us_east_1(self):
        """Default region is us-east-1 when not specified."""
        cfg = _make_config({"use_bedrock": True, "aws_region": None})
        _configure_claude_environment(cfg)

        assert os.environ["AWS_REGION"] == "us-east-1"
        assert os.environ["AWS_DEFAULT_REGION"] == "us-east-1"


# ---------------------------------------------------------------------------
# Base URL (Req 13.3)
# ---------------------------------------------------------------------------
class TestBaseUrl:
    def test_base_url_set_when_configured(self):
        """Req 13.3: anthropic_base_url set → ANTHROPIC_BASE_URL in env."""
        cfg = _make_config({"anthropic_base_url": "https://custom.example.com"})
        _configure_claude_environment(cfg)

        assert os.environ["ANTHROPIC_BASE_URL"] == "https://custom.example.com"

    def test_base_url_removed_when_none(self):
        """Req 13.3: anthropic_base_url None → ANTHROPIC_BASE_URL removed."""
        os.environ["ANTHROPIC_BASE_URL"] = "https://old.example.com"
        cfg = _make_config({"anthropic_base_url": None})
        _configure_claude_environment(cfg)

        assert "ANTHROPIC_BASE_URL" not in os.environ


# ---------------------------------------------------------------------------
# Credential env vars NEVER set (Req 2.3, 13.5)
# ---------------------------------------------------------------------------
class TestNoCredentialEnvVars:
    def test_credential_vars_never_set_bedrock_enabled(self):
        """Req 13.5: Function never sets AWS credential env vars."""
        # Pre-clear to ensure they don't exist
        for var in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                     "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"]:
            os.environ.pop(var, None)

        cfg = _make_config({"use_bedrock": True})
        _configure_claude_environment(cfg)

        for var in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                     "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"]:
            assert var not in os.environ, f"{var} should not be set"

    def test_credential_vars_never_set_bedrock_disabled(self):
        """Req 13.5: Even with Bedrock off, no credential vars set."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        for var in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                     "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"]:
            os.environ.pop(var, None)

        cfg = _make_config({"use_bedrock": False})
        _configure_claude_environment(cfg)

        for var in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                     "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"]:
            assert var not in os.environ, f"{var} should not be set"


# ---------------------------------------------------------------------------
# Auth validation (Req 13.6)
# ---------------------------------------------------------------------------
class TestAuthValidation:
    def test_raises_when_no_api_key_and_bedrock_disabled(self):
        """Req 13.6: No ANTHROPIC_API_KEY + Bedrock off → raise."""
        os.environ.pop("ANTHROPIC_API_KEY", None)
        cfg = _make_config({"use_bedrock": False})

        with pytest.raises(AuthenticationNotConfiguredError):
            _configure_claude_environment(cfg)

    def test_no_raise_when_api_key_set(self):
        """Req 13.6: ANTHROPIC_API_KEY present → no raise even without Bedrock."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        cfg = _make_config({"use_bedrock": False})

        _configure_claude_environment(cfg)  # should not raise

    def test_no_raise_when_bedrock_enabled_no_api_key(self):
        """Req 13.6: Bedrock enabled → no raise even without API key."""
        os.environ.pop("ANTHROPIC_API_KEY", None)
        cfg = _make_config({"use_bedrock": True})

        _configure_claude_environment(cfg)  # should not raise


# ---------------------------------------------------------------------------
# Zero IO / synchronous (Req 13.7)
# ---------------------------------------------------------------------------
class TestZeroIO:
    def test_function_is_synchronous(self):
        """Req 13.7: Function is not async — reads from in-memory cache only."""
        import inspect
        assert not inspect.iscoroutinefunction(_configure_claude_environment), (
            "_configure_claude_environment should be synchronous (zero IO)"
        )
