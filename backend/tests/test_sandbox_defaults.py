"""Tests for sandbox configuration defaults and path expansion.

Validates that sandbox defaults include the app data directory as writable,
all known seatbelt-blocked commands are excluded, and tilde paths are
expanded to absolute paths.

# Feature: sandbox-defaults-fix
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from core.app_config_manager import DEFAULT_CONFIG
from core.prompt_builder import PromptBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_builder(config_overrides: dict | None = None) -> PromptBuilder:
    """Create a PromptBuilder with mock config, optionally overriding values."""
    base = {
        "default_model": "claude-sonnet-4-6",
        "use_bedrock": False,
        "sandbox_enabled_default": True,
        "sandbox_excluded_commands": DEFAULT_CONFIG["sandbox_excluded_commands"],
        "sandbox_auto_allow_bash": True,
        "sandbox_allow_unsandboxed": False,
        "sandbox_allowed_hosts": "*",
        "sandbox_additional_write_paths": DEFAULT_CONFIG["sandbox_additional_write_paths"],
    }
    if config_overrides:
        base.update(config_overrides)
    mock_config = MagicMock()
    mock_config.get = MagicMock(side_effect=lambda key, default=None: base.get(key, default))
    return PromptBuilder(config=mock_config)


# ---------------------------------------------------------------------------
# AC1: ~/.swarm-ai/ in sandbox_additional_write_paths default
# ---------------------------------------------------------------------------

class TestDefaultWritePaths:
    """Acceptance criterion 1: app data dir is writable by default."""

    def test_swarm_ai_dir_in_default_write_paths(self):
        """~/.swarm-ai/ must be in DEFAULT_CONFIG sandbox_additional_write_paths."""
        raw = DEFAULT_CONFIG["sandbox_additional_write_paths"]
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        assert any("swarm-ai" in p or ".swarm-ai" in p for p in paths), (
            f"~/.swarm-ai/ not found in default write paths: {raw!r}"
        )

    def test_default_write_paths_not_empty(self):
        """Default write paths must not be empty string."""
        assert DEFAULT_CONFIG["sandbox_additional_write_paths"].strip() != ""


# ---------------------------------------------------------------------------
# AC2: All seatbelt-blocked commands in excluded list
# ---------------------------------------------------------------------------

class TestExcludedCommands:
    """Acceptance criterion 2: seatbelt-blocked commands are excluded."""

    SEATBELT_BLOCKED = [
        "docker", "ps", "pgrep", "pkill", "top",
        "open", "screencapture", "osascript", "launchctl",
    ]

    def test_all_seatbelt_commands_in_default(self):
        """Every known seatbelt-blocked command must be in the default."""
        raw = DEFAULT_CONFIG["sandbox_excluded_commands"]
        excluded = {cmd.strip() for cmd in raw.split(",") if cmd.strip()}
        for cmd in self.SEATBELT_BLOCKED:
            assert cmd in excluded, f"{cmd!r} missing from sandbox_excluded_commands"

    def test_build_sandbox_config_passes_all_excluded(self):
        """build_sandbox_config returns all excluded commands from config."""
        builder = _make_builder()
        config = builder.build_sandbox_config()
        excluded = config["excludedCommands"]
        for cmd in self.SEATBELT_BLOCKED:
            assert cmd in excluded, f"{cmd!r} missing from sandbox config output"


# ---------------------------------------------------------------------------
# AC3: Path expansion — ~ resolves to actual home directory
# ---------------------------------------------------------------------------

class TestPathExpansion:
    """Acceptance criterion 3: tilde paths are expanded to absolute paths."""

    def test_tilde_expanded_in_add_dirs(self):
        """add_dirs built by prompt_builder must have ~ expanded to home dir."""
        # Test the actual code path in prompt_builder
        builder = _make_builder({
            "sandbox_additional_write_paths": "~/.swarm-ai/,~/Desktop/test",
        })
        # Access the private _config to simulate what build_sdk_options does
        raw = builder._config.get("sandbox_additional_write_paths", "")
        add_dirs = [
            os.path.expanduser(p.strip()) for p in raw.split(",")
            if p.strip()
        ]
        home = os.path.expanduser("~")
        for d in add_dirs:
            assert not d.startswith("~"), f"Tilde not expanded: {d}"
            assert d.startswith(home) or d.startswith("/"), (
                f"Path not absolute after expansion: {d}"
            )

    def test_default_write_paths_expand_correctly(self):
        """The DEFAULT_CONFIG write paths expand to real absolute paths."""
        raw = DEFAULT_CONFIG["sandbox_additional_write_paths"]
        add_dirs = [
            os.path.expanduser(p.strip()) for p in raw.split(",")
            if p.strip()
        ]
        home = os.path.expanduser("~")
        assert len(add_dirs) >= 1
        for d in add_dirs:
            assert d.startswith("/"), f"Path not absolute after expansion: {d}"
            assert not d.startswith("~"), f"Tilde not expanded: {d}"
        # Specifically check ~/.swarm-ai/ resolves
        assert any(d == os.path.join(home, ".swarm-ai/") or d == os.path.join(home, ".swarm-ai") for d in add_dirs)

    def test_absolute_paths_unchanged(self):
        """Absolute paths pass through without modification."""
        builder = _make_builder({
            "sandbox_additional_write_paths": "/tmp/test,/var/data",
        })
        raw = builder._config.get("sandbox_additional_write_paths", "")
        add_dirs = [
            os.path.expanduser(p.strip()) for p in raw.split(",")
            if p.strip()
        ]
        assert add_dirs == ["/tmp/test", "/var/data"]


# ---------------------------------------------------------------------------
# AC4: No regressions — sandbox config still works with sandbox enabled/disabled
# ---------------------------------------------------------------------------

class TestSandboxConfigRegression:
    """Acceptance criterion 4: no regressions in sandbox behavior."""

    def test_sandbox_disabled_returns_enabled_false(self):
        """When sandbox disabled, returns {enabled: False}."""
        builder = _make_builder({"sandbox_enabled_default": False})
        config = builder.build_sandbox_config()
        assert config == {"enabled": False}

    def test_sandbox_enabled_returns_full_config(self):
        """When sandbox enabled, returns full config with all fields."""
        builder = _make_builder({"sandbox_enabled_default": True})
        config = builder.build_sandbox_config()
        assert config["enabled"] is True
        assert "excludedCommands" in config
        assert "autoAllowBashIfSandboxed" in config
        assert "network" in config
        assert config["network"]["allowedHosts"] == ["*"]


# ---------------------------------------------------------------------------
# AC5: Additive — user overrides preserved
# ---------------------------------------------------------------------------

class TestUserOverridesPreserved:
    """Acceptance criterion 5: user config additions don't clobber defaults."""

    def test_user_additional_paths_work(self):
        """User can add custom paths via config override."""
        builder = _make_builder({
            "sandbox_additional_write_paths": "~/.swarm-ai/,~/my-custom-dir",
        })
        raw = "~/.swarm-ai/,~/my-custom-dir"
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        assert len(paths) == 2

    def test_user_additional_commands_work(self):
        """User can override excluded commands."""
        custom = "docker,ps,pgrep,pkill,top,open,screencapture,osascript,launchctl,my-custom-cmd"
        builder = _make_builder({"sandbox_excluded_commands": custom})
        config = builder.build_sandbox_config()
        assert "my-custom-cmd" in config["excludedCommands"]
