"""MCP isolation invariant — a SwarmAI session must load ONLY SwarmAI-configured MCP.

Root cause (run_e12a3589, dive-deep): the Claude CLI unconditionally reads two
user-global config files that SwarmAI does not govern —
  1. ``~/.claude/settings.json`` ``enabledPlugins`` + ``extraKnownMarketplaces``
     → injects AIM plugin agents + the plugin's builder-mcp.
  2. ``~/.claude.json`` top-level ``mcpServers`` (8 global MCP: github, hs-kmine,
     loaf_mcp, pippin, sharepoint, phonetool, cloud-intelligence, spec-studio).

These are the SAME injection class as the CLI auto-loading a project CLAUDE.md:
config the agent cannot control leaking into the session. Two NON-REDUNDANT flags
close it, each treating one source:
  * ``--setting-sources project`` (already set) → blocks source #1 (plugin agents/MCP).
  * ``--strict-mcp-config``       (the fix)     → blocks source #2 — CLI help:
    "Only use MCP servers from --mcp-config, ignoring all other MCP configurations."

Two test layers, distinct responsibilities (do not conflate — a Gate-2 adversarial
review flagged the original docstring for overstating "mutation-proven"):

  * CONTRACT layer (test_isolation_flags_* / test_mutation_*): drives the real SDK
    ``SubprocessCLITransport._build_command`` with hand-built options to prove the SDK
    HONORS both flags (flag present when set, absent when unset). These verify SDK
    behavior — they would pass even if the production fix were absent, so they do NOT
    by themselves guard the fix.
  * PRODUCTION layer (test_production_build_options_sets_strict_mcp_config): couples to
    the actual fix by asserting ``prompt_builder.py`` sets ``strict_mcp_config=True``
    (mutation-proven: deleting the flag from prompt_builder turns THIS test RED — the
    contract-layer tests stay green). This is a source-text guard, deliberately chosen
    because ``build_options`` is async + dependency-heavy to unit-drive; a rename/
    reformat of the assignment could defeat it, so the canonical form must be kept.

Guarding the SDK-command layer (not build_options) for the contract tests is deliberate:
that layer is where isolation actually takes effect and is where the leak was observed.
"""

from pathlib import Path


def _cmd_for(*, strict: bool, setting_sources):
    """Drive the real SDK transport to produce the CLI command for given options."""
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.transport.subprocess_cli import (
        SubprocessCLITransport,
    )

    opts = ClaudeAgentOptions(
        mcp_servers={
            "coe-mcp": {"type": "stdio", "command": "coemcpproxy", "args": []}
        },
        setting_sources=setting_sources,
        strict_mcp_config=strict,
    )
    transport = SubprocessCLITransport(prompt="probe", options=opts)
    # Skip CLI path resolution — we only inspect the assembled command.
    transport._cli_path = "/bin/true"
    return transport._build_command()


def _has_strict(cmd) -> bool:
    return "--strict-mcp-config" in cmd


def _has_project_setting_source(cmd) -> bool:
    return any(
        c.startswith("--setting-sources") and "project" in c for c in cmd
    )


def test_isolation_flags_both_present_in_spawned_command():
    """The SwarmAI isolation contract: spawned CLI cmd carries BOTH flags."""
    cmd = _cmd_for(strict=True, setting_sources=["project"])
    assert _has_strict(cmd), (
        "--strict-mcp-config missing → global ~/.claude.json mcpServers leak into session"
    )
    assert _has_project_setting_source(cmd), (
        "--setting-sources=project missing → plugin agents/MCP from "
        "~/.claude/settings.json leak into session"
    )


def test_mutation_strict_false_drops_flag():
    """SDK-contract check: with strict_mcp_config=False the SDK omits the flag.

    Proves the flag is not spuriously emitted — this guards the SDK contract the
    fix relies on, NOT the production fix itself (that is the *_production_* test).
    """
    cmd = _cmd_for(strict=False, setting_sources=["project"])
    assert not _has_strict(cmd), (
        "Mutation guard: with strict_mcp_config=False the flag must be absent — "
        "if this fails the test can no longer detect the regression."
    )


def test_mutation_default_setting_sources_drops_project_only_guarantee():
    """SDK-contract check: user-layer setting-source is observable in the command.

    When setting_sources includes 'user', source #1 (plugin agents) is no longer
    blocked; this asserts that weakening is visible at the command layer. Guards the
    SDK contract, not the production fix (that is the *_production_* test).
    """
    cmd = _cmd_for(strict=True, setting_sources=["user", "project"])
    # 'user' present means the setting-sources flag no longer restricts to project-only.
    joined = ",".join(cmd)
    assert "user" in joined, (
        "Mutation guard: user-layer setting-source must be observable in the cmd — "
        "otherwise the project-only isolation invariant cannot be verified."
    )


def test_production_build_options_sets_strict_mcp_config():
    """Production guard: the ONE options constructor sets strict_mcp_config=True.

    This locks the actual fix site (prompt_builder.build_options) so a future edit
    that drops the flag is caught even though build_options itself is too
    dependency-heavy to unit-drive here.
    """
    src = (
        Path(__file__).resolve().parents[1] / "core" / "prompt_builder.py"
    ).read_text()
    assert "strict_mcp_config=True" in src, (
        "prompt_builder.py must set strict_mcp_config=True on the interactive-session "
        "ClaudeAgentOptions — global MCP isolation depends on it (run_e12a3589)."
    )
    assert 'setting_sources = ["project"]' in src, (
        "prompt_builder.py must keep setting_sources=['project'] — the other half "
        "of the two-flag isolation contract."
    )
