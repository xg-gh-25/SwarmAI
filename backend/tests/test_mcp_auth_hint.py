"""The MCP auth-failure hint fits how the person signs in (R5.3).

``mwinit -s`` (Midway) is suggested only when the SwarmAI AWS account signs in with
ADA / Midway; anyone else is told to sign this MCP server in again.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core import aws_account
from core import session_unit as su


def _unit():
    u = su.SessionUnit.__new__(su.SessionUnit)
    u.session_id = "s"
    u._mcp_health_checked = False
    u._mcp_pending_reprobes_left = 3
    u._configured_mcps = {"tracker"}

    class _IO:
        def locked(self):
            return False

    u._client_io = _IO()
    u._client = MagicMock()
    u._client.get_mcp_status = AsyncMock(return_value={"mcpServers": [
        {"name": "tracker", "status": "needs-auth", "error": "auth required (401)"}]})
    return u


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["sso_app", "bedrock_api_key", "profile", None])
async def test_generic_hint_without_ada(monkeypatch, method):
    monkeypatch.setattr(aws_account, "remediation_method", lambda config=None: method)
    event = await _unit()._check_mcp_health()
    assert "mwinit" not in event["message"]
    assert "re-authenticate this MCP server" in event["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ada_account", "ada"])
async def test_mwinit_with_ada(monkeypatch, method):
    monkeypatch.setattr(aws_account, "remediation_method", lambda config=None: method)
    event = await _unit()._check_mcp_health()
    assert "mwinit -s" in event["message"]


@pytest.mark.asyncio
async def test_unknown_sign_in_is_generic(monkeypatch):
    def broken(config=None):
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(aws_account, "remediation_method", broken)
    event = await _unit()._check_mcp_health()
    assert "mwinit" not in event["message"]
