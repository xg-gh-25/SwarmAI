"""Regression tests for _check_mcp_health re-probe under background MCP init.

Background (run_a7b35b68): deleting MCP_CONNECTION_NONBLOCKING=0 moved MCP
connection to the SDK's background mode, so a configured MCP can still be
"pending" (not yet terminal: connected/failed) at the one-shot post-first-response
health check. Two masking bugs this guards against:

  1. PENDING-MASK: consuming the one-shot on a "pending" MCP would permanently
     mask an MCP that ultimately FAILS (adversarial finding, run_a7b35b68).
  2. TRANSIENT-MASK: setting the one-shot BEFORE get_mcp_status() meant a transient
     status-query exception / empty response consumed the check as "healthy"
     (second adversarial finding, same run).

Both are fixed by a bounded re-probe (_mcp_pending_reprobes_left, init 3, reset
per spawn): a non-terminal / indeterminate result does NOT consume the one-shot
until the budget is exhausted (which then finalizes — no unbounded probe,
STEERING #2). These tests mutation-lock that contract.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core import session_unit as su


def _make_unit(configured=None):
    """A bare SessionUnit with only the fields _check_mcp_health reads."""
    u = su.SessionUnit.__new__(su.SessionUnit)
    u.session_id = "test-sess"
    u._mcp_health_checked = False
    u._mcp_pending_reprobes_left = 3
    u._configured_mcps = set(configured or {"slack"})

    class _UnlockedIO:
        def locked(self):
            return False

    u._client_io = _UnlockedIO()
    u._client = MagicMock()
    return u


def _status(*servers):
    return {"mcpServers": [dict(name=n, status=s, **(dict(error=e) if e else {}))
                           for (n, s, e) in servers]}


@pytest.mark.asyncio
async def test_pending_reprobes_then_terminal_failure_warns():
    """A slow MCP seen as pending must NOT consume the one-shot; when it later
    turns 'failed', the warning must fire (the pending-mask bug)."""
    u = _make_unit({"slack"})
    u._client.get_mcp_status = AsyncMock(return_value=_status(("slack", "pending", None)))
    r = await u._check_mcp_health()
    assert r is None, "pending → no verdict yet"
    assert u._mcp_health_checked is False, "pending must NOT consume the one-shot"
    assert u._mcp_pending_reprobes_left == 2, "re-probe budget must decrement"

    # Next turn: now terminal failure → must warn (was permanently masked pre-fix)
    u._client.get_mcp_status = AsyncMock(return_value=_status(("slack", "failed", "timeout")))
    r = await u._check_mcp_health()
    assert r is not None and "slack" in r.get("message", ""), "terminal failure must warn"
    assert u._mcp_health_checked is True, "one-shot consumed on a terminal verdict"


@pytest.mark.asyncio
async def test_pending_budget_exhaustion_finalizes_without_false_alarm():
    """When the re-probe budget runs out with an MCP still pending, the check
    finalizes (no infinite re-probe) and does NOT false-alarm (pending=non-failed)."""
    u = _make_unit({"slack"})
    u._client.get_mcp_status = AsyncMock(return_value=_status(("slack", "pending", None)))
    for _ in range(3):
        assert await u._check_mcp_health() is None
        u._mcp_health_checked = False  # simulate the next turn re-entering
    assert u._mcp_pending_reprobes_left == 0, "budget exhausted after 3 re-probes"
    r = await u._check_mcp_health()
    assert r is None, "still-pending at exhaustion → non-failed, NO false alarm"
    assert u._mcp_health_checked is True, "finalized — no unbounded re-probe (STEERING #2)"


@pytest.mark.asyncio
async def test_transient_exception_does_not_consume_until_budget_out():
    """A transient get_mcp_status() exception is 'indeterminate', not 'healthy' —
    it must re-probe (bounded), never silently finalize as healthy on the first hit."""
    u = _make_unit({"slack"})
    u._client.get_mcp_status = AsyncMock(side_effect=RuntimeError("boom"))
    for n in range(3):
        assert await u._check_mcp_health() is None
        assert u._mcp_health_checked is False, f"transient retry {n + 1}: must not consume"
    assert u._mcp_pending_reprobes_left == 0
    # budget exhausted → finalize (bounded — no infinite retry)
    await u._check_mcp_health()
    assert u._mcp_health_checked is True, "exhausted transient budget must finalize"


@pytest.mark.asyncio
async def test_empty_status_response_reprobes_not_consumed():
    """An empty mcpServers response (old CLI / not-ready) is indeterminate —
    must re-probe, not consume the one-shot as healthy."""
    u = _make_unit({"slack"})
    u._client.get_mcp_status = AsyncMock(return_value={"mcpServers": []})
    assert await u._check_mcp_health() is None
    assert u._mcp_health_checked is False, "empty response must not consume the one-shot"
    assert u._mcp_pending_reprobes_left == 2


@pytest.mark.asyncio
async def test_healthy_connected_consumes_oneshot_immediately():
    """The happy path: all configured MCPs connected → consume the one-shot on the
    first check, no warning, budget untouched (no needless re-probe)."""
    u = _make_unit({"slack"})
    u._client.get_mcp_status = AsyncMock(return_value=_status(("slack", "connected", None)))
    r = await u._check_mcp_health()
    assert r is None, "all connected → no warning"
    assert u._mcp_health_checked is True, "terminal-healthy consumes the one-shot"
    assert u._mcp_pending_reprobes_left == 3, "healthy path must not spend re-probe budget"
