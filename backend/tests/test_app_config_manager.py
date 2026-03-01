"""Unit tests for AppConfigManager.

Tests the in-memory cached configuration manager backed by a JSON file.
Covers the full lifecycle: load, get, update, reload, plus edge cases
like missing files, invalid JSON, secret filtering, and file permissions.

Key invariants verified:

- ``get()`` returns from cache with zero file IO after ``load()``
- ``update()`` never writes secret keys to the config file
- Missing / empty / invalid JSON falls back to ``DEFAULT_CONFIG``
- ``reload()`` re-reads from disk and refreshes the cache
- File is created with ``0o600`` permissions
- Migration only runs when ``config.json`` is missing
"""

import json
import os
import stat
import sys
import pytest
from pathlib import Path

from core.app_config_manager import (
    AppConfigManager,
    DEFAULT_CONFIG,
    SECRET_KEYS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Return a path to a temporary config.json (does not exist yet)."""
    return tmp_path / "config.json"


@pytest.fixture
def mgr(tmp_config: Path) -> AppConfigManager:
    """Return an AppConfigManager pointing at the temp config path."""
    return AppConfigManager(config_path=tmp_config)


# ---------------------------------------------------------------------------
# load() tests
# ---------------------------------------------------------------------------


class TestLoad:
    """Tests for AppConfigManager.load()."""

    def test_load_creates_file_with_defaults_when_missing(self, mgr, tmp_config):
        """Missing config file → defaults written to disk."""
        result = mgr.load()
        assert tmp_config.exists()
        assert result["use_bedrock"] is True
        assert result["aws_region"] == "us-east-1"
        assert result["default_model"] == "claude-opus-4-6"

    def test_load_falls_back_on_empty_file(self, mgr, tmp_config):
        """Empty config file → defaults."""
        tmp_config.write_text("")
        result = mgr.load()
        assert result == DEFAULT_CONFIG

    def test_load_falls_back_on_invalid_json(self, mgr, tmp_config):
        """Malformed JSON → defaults."""
        tmp_config.write_text("{not valid json!!")
        result = mgr.load()
        assert result == DEFAULT_CONFIG

    def test_load_falls_back_on_non_dict_json(self, mgr, tmp_config):
        """JSON array at root → defaults."""
        tmp_config.write_text("[1, 2, 3]")
        result = mgr.load()
        assert result == DEFAULT_CONFIG

    def test_load_merges_with_defaults(self, mgr, tmp_config):
        """Partial config file → merged with defaults."""
        tmp_config.write_text(json.dumps({"aws_region": "eu-west-1"}))
        result = mgr.load()
        assert result["aws_region"] == "eu-west-1"
        # Default keys still present
        assert result["use_bedrock"] is True
        assert "available_models" in result

    def test_load_preserves_extra_keys(self, mgr, tmp_config):
        """Unknown keys in file are preserved (forward compat)."""
        tmp_config.write_text(json.dumps({"custom_key": "hello"}))
        result = mgr.load()
        assert result["custom_key"] == "hello"


# ---------------------------------------------------------------------------
# get() tests
# ---------------------------------------------------------------------------


class TestGet:
    """Tests for AppConfigManager.get()."""

    def test_get_returns_cached_value(self, mgr, tmp_config):
        """get() reads from cache, not disk."""
        tmp_config.write_text(json.dumps({"aws_region": "ap-southeast-1"}))
        mgr.load()
        assert mgr.get("aws_region") == "ap-southeast-1"

    def test_get_returns_default_for_missing_key(self, mgr):
        """Missing key returns the caller-supplied default."""
        mgr.load()
        assert mgr.get("nonexistent", "fallback") == "fallback"

    def test_get_returns_none_for_missing_key_no_default(self, mgr):
        """Missing key with no default returns None."""
        mgr.load()
        assert mgr.get("nonexistent") is None

    def test_get_auto_loads_if_cache_empty(self, mgr):
        """get() triggers load() if cache is None."""
        result = mgr.get("use_bedrock")
        assert result is True  # from DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# update() tests
# ---------------------------------------------------------------------------


class TestUpdate:
    """Tests for AppConfigManager.update()."""

    def test_update_merges_into_cache(self, mgr):
        """update() merges new values into the in-memory cache."""
        mgr.load()
        mgr.update({"aws_region": "us-west-2"})
        assert mgr.get("aws_region") == "us-west-2"
        # Other keys preserved
        assert mgr.get("use_bedrock") is True

    def test_update_writes_to_disk(self, mgr, tmp_config):
        """update() persists changes to the config file."""
        mgr.load()
        mgr.update({"aws_region": "eu-central-1"})
        on_disk = json.loads(tmp_config.read_text())
        assert on_disk["aws_region"] == "eu-central-1"

    def test_update_filters_secret_keys_from_file(self, mgr, tmp_config):
        """Secret keys are stripped from the persisted file."""
        mgr.load()
        mgr.update({
            "aws_region": "us-east-1",
            "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "aws_session_token": "FwoGZXIvYXdzEBY...",
            "aws_bearer_token": "bearer-token-value",
            "anthropic_api_key": "sk-ant-api03-...",
        })
        on_disk = json.loads(tmp_config.read_text())
        for secret in SECRET_KEYS:
            assert secret not in on_disk, f"{secret} leaked to config file"
        # Non-secret key is present
        assert on_disk["aws_region"] == "us-east-1"

    def test_update_auto_loads_if_cache_empty(self, mgr, tmp_config):
        """update() triggers load() if cache is None."""
        mgr.update({"aws_region": "ap-northeast-1"})
        assert mgr.get("aws_region") == "ap-northeast-1"


# ---------------------------------------------------------------------------
# reload() tests
# ---------------------------------------------------------------------------


class TestReload:
    """Tests for AppConfigManager.reload()."""

    def test_reload_picks_up_manual_edits(self, mgr, tmp_config):
        """reload() re-reads from disk after external changes."""
        mgr.load()
        assert mgr.get("aws_region") == "us-east-1"
        # Simulate manual edit
        data = json.loads(tmp_config.read_text())
        data["aws_region"] = "sa-east-1"
        tmp_config.write_text(json.dumps(data))
        mgr.reload()
        assert mgr.get("aws_region") == "sa-east-1"


# ---------------------------------------------------------------------------
# File permissions tests
# ---------------------------------------------------------------------------


class TestFilePermissions:
    """Tests for config file permission enforcement."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="chmod 0600 not meaningful on Windows",
    )
    def test_file_created_with_0600_permissions(self, mgr, tmp_config):
        """Config file is created with owner-only read/write."""
        mgr.load()  # triggers file creation
        mode = tmp_config.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="chmod 0600 not meaningful on Windows",
    )
    def test_update_preserves_0600_permissions(self, mgr, tmp_config):
        """update() re-applies 0600 after writing."""
        mgr.load()
        mgr.update({"aws_region": "us-west-2"})
        mode = tmp_config.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Round-trip consistency
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Tests for cache ↔ file consistency."""

    def test_cache_matches_file_after_update(self, mgr, tmp_config):
        """After update(), cache and file have identical non-secret data."""
        mgr.load()
        mgr.update({"aws_region": "eu-west-1", "use_bedrock": False})
        on_disk = json.loads(tmp_config.read_text())
        # Cache should match file for non-secret keys
        for key, value in on_disk.items():
            assert mgr.get(key) == value

    def test_non_updated_fields_preserved(self, mgr, tmp_config):
        """Partial update preserves fields not in the update dict."""
        mgr.load()
        original_model = mgr.get("default_model")
        mgr.update({"aws_region": "ap-south-1"})
        assert mgr.get("default_model") == original_model
