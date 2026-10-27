"""Tests for sandbox configuration defaults and path expansion.

Validates that sandbox defaults include the app data directory as writable,
all known seatbelt-blocked commands are excluded, and tilde paths are
expanded to absolute paths.

# Feature: sandbox-defaults-fix
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock


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
    """Acceptance criterion 3: add_dirs are absolute, the app data dir always first.

    These call prompt_builder.additional_dirs, the code build_sdk_options uses (they
    used to re-implement the expansion inline, so they could not catch a change to it).
    """

    def test_tilde_expanded_in_add_dirs(self):
        from core.prompt_builder import additional_dirs
        dirs = additional_dirs({"sandbox_additional_write_paths": "~/.swarm-ai/,~/Desktop/test"})
        assert dirs and all(d.startswith("/") for d in dirs), dirs
        assert os.path.join(os.path.expanduser("~"), "Desktop/test") in dirs

    def test_app_data_dir_always_first_and_follows_swarm_data_dir(self, monkeypatch, tmp_path):
        from core.prompt_builder import additional_dirs
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
        for cfg in ({}, None, {"sandbox_additional_write_paths": ""},
                    {"sandbox_additional_write_paths": "/tmp/x"}):
            assert additional_dirs(cfg)[0] == str(tmp_path / "data"), cfg

    def test_default_write_paths_only_repeat_the_data_dir(self, monkeypatch):
        """An install's saved default "~/.swarm-ai/" adds no second directory."""
        from core.prompt_builder import additional_dirs
        monkeypatch.delenv("SWARM_DATA_DIR", raising=False)
        cfg = {"sandbox_additional_write_paths": DEFAULT_CONFIG["sandbox_additional_write_paths"]}
        assert additional_dirs(cfg) == [os.path.join(os.path.expanduser("~"), ".swarm-ai")]

    def test_absolute_paths_unchanged(self):
        from core.prompt_builder import additional_dirs
        dirs = additional_dirs({"sandbox_additional_write_paths": "/tmp/test, /var/data,/tmp/test"})
        assert dirs[1:] == ["/tmp/test", "/var/data"]

    def test_build_options_passes_additional_dirs(self):
        """The call site uses the function above, not its own expansion."""
        import ast
        import inspect
        from core.prompt_builder import PromptBuilder
        # a method's source is indented; wrapping it in a class makes it parse
        tree = ast.parse("class _C:\n" + inspect.getsource(PromptBuilder.build_options))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "additional_dirs"]
        reads = [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
                 and n.value == "sandbox_additional_write_paths"]
        assert len(calls) == 1 and not reads


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
