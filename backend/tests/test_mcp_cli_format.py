"""Tests for mcp_config_loader.load_mcp_config_for_cli().

Verifies that the shared loader produces CLI-format MCP config
({"mcpServers": {...}}) suitable for --mcp-config flag,
replacing the duplicated logic formerly in executor._load_mcp_config().
"""

import json
import pytest
from pathlib import Path


def test_load_mcp_config_for_cli_returns_cli_format(tmp_path: Path):
    """CLI format wraps servers in {"mcpServers": {...}}."""
    from core.mcp_config_loader import load_mcp_config_for_cli

    mcps_dir = tmp_path / ".claude" / "mcps"
    mcps_dir.mkdir(parents=True)

    dev = [
        {
            "id": "test-mcp",
            "name": "test-mcp",
            "enabled": True,
            "connection_type": "stdio",
            "config": {"command": "node", "args": ["server.js"]},
        }
    ]
    (mcps_dir / "mcp-dev.json").write_text(json.dumps(dev))

    result = load_mcp_config_for_cli(tmp_path)
    assert "mcpServers" in result
    assert "test-mcp" in result["mcpServers"]
    server = result["mcpServers"]["test-mcp"]
    assert server["type"] == "stdio"
    assert server["command"] == "node"


def test_load_mcp_config_for_cli_empty_when_no_files(tmp_path: Path):
    """Returns empty dict when no MCP config files exist."""
    from core.mcp_config_loader import load_mcp_config_for_cli

    result = load_mcp_config_for_cli(tmp_path)
    assert result == {}


def test_load_mcp_config_for_cli_merges_catalog_and_dev(tmp_path: Path):
    """Dev entries override catalog entries by id."""
    from core.mcp_config_loader import load_mcp_config_for_cli

    mcps_dir = tmp_path / ".claude" / "mcps"
    mcps_dir.mkdir(parents=True)

    catalog = [
        {
            "id": "shared-mcp",
            "name": "shared-mcp",
            "enabled": True,
            "connection_type": "stdio",
            "config": {"command": "old-cmd", "args": []},
        }
    ]
    dev = [
        {
            "id": "shared-mcp",
            "name": "shared-mcp",
            "enabled": True,
            "connection_type": "stdio",
            "config": {"command": "new-cmd", "args": ["--flag"]},
        }
    ]
    (mcps_dir / "mcp-catalog.json").write_text(json.dumps(catalog))
    (mcps_dir / "mcp-dev.json").write_text(json.dumps(dev))

    result = load_mcp_config_for_cli(tmp_path)
    assert result["mcpServers"]["shared-mcp"]["command"] == "new-cmd"


def test_load_mcp_config_for_cli_filters_disabled(tmp_path: Path):
    """Disabled entries are excluded."""
    from core.mcp_config_loader import load_mcp_config_for_cli

    mcps_dir = tmp_path / ".claude" / "mcps"
    mcps_dir.mkdir(parents=True)

    dev = [
        {
            "id": "disabled-mcp",
            "name": "disabled-mcp",
            "enabled": False,
            "connection_type": "stdio",
            "config": {"command": "node", "args": []},
        }
    ]
    (mcps_dir / "mcp-dev.json").write_text(json.dumps(dev))

    result = load_mcp_config_for_cli(tmp_path)
    assert result == {}


def test_load_mcp_config_for_cli_handles_sse_type(tmp_path: Path):
    """SSE connection type is converted correctly."""
    from core.mcp_config_loader import load_mcp_config_for_cli

    mcps_dir = tmp_path / ".claude" / "mcps"
    mcps_dir.mkdir(parents=True)

    dev = [
        {
            "id": "sse-mcp",
            "name": "sse-mcp",
            "enabled": True,
            "connection_type": "sse",
            "config": {"url": "http://localhost:3000"},
        }
    ]
    (mcps_dir / "mcp-dev.json").write_text(json.dumps(dev))

    result = load_mcp_config_for_cli(tmp_path)
    assert result["mcpServers"]["sse-mcp"]["type"] == "sse"
    assert result["mcpServers"]["sse-mcp"]["url"] == "http://localhost:3000"
