"""Tests for CLI-bundled skill overrides passed to every SwarmAI session.

What is tested
--------------
- ``CLI_SKILL_OVERRIDES`` turns the CLI-bundled ``claude-api`` skill off (one call
  of it injected ~290K tokens of SDK docs into a session).
- The real SDK transport turns ``settings=json.dumps({"skillOverrides": ...})`` into
  a ``--settings`` flag that still carries the overrides when sandbox settings are
  merged in (the SDK rewrites the JSON in that case).
- The production options constructor passes the overrides (source check, like
  test_mcp_isolation: build_options is too dependency-heavy to drive here).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.prompt_builder import CLI_SKILL_OVERRIDES


def _settings_flag(**opts):
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.transport.subprocess_cli import (
        SubprocessCLITransport,
    )

    transport = SubprocessCLITransport(
        prompt="probe", options=ClaudeAgentOptions(**opts)
    )
    transport._cli_path = "/bin/true"  # only inspect the assembled command
    cmd = transport._build_command()
    return json.loads(cmd[cmd.index("--settings") + 1])


def test_claude_api_bundled_skill_is_off():
    assert CLI_SKILL_OVERRIDES.get("claude-api") == "off"


@pytest.mark.parametrize("sandbox", [None, {"enabled": True}])
def test_overrides_reach_the_cli_settings_flag(sandbox):
    settings = _settings_flag(
        settings=json.dumps({"skillOverrides": CLI_SKILL_OVERRIDES}), sandbox=sandbox
    )
    assert settings["skillOverrides"] == CLI_SKILL_OVERRIDES
    if sandbox:
        assert settings["sandbox"] == sandbox


def test_production_options_pass_the_overrides():
    # build_options assembles the flag-level CLI settings in one dict and passes it
    # as `settings=json.dumps(cli_settings)` (it also carries autoCompactWindow).
    src = (Path(__file__).resolve().parents[1] / "core" / "prompt_builder.py").read_text()
    assert '"skillOverrides": CLI_SKILL_OVERRIDES' in src
    assert "settings=json.dumps(cli_settings)" in src
