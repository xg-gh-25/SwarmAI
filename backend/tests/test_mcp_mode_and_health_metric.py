"""Regression tests for the two P4 review defects (run_2f19c4e1).

Both defects came from run_a7b35b68's justification for deleting
``MCP_CONNECTION_NONBLOCKING=0``, not from its re-probe logic (which is sound and
covered by test_mcp_health_reprobe.py).

DEFECT 1 — the deletion was a provable NO-OP sold as a latency root-fix.
  Verified against the CLI bundle (claude-code 2.1.145) connect path:
      O = lK(env) ? false : (xH(env) || (opts.nonBlocking ?? false))
  with ``lK(undefined) === false`` and ``xH(undefined) === false``, so an UNSET
  env var falls through to ``opts.nonBlocking ?? false`` → BLOCKING. The Python
  SDK never sets ``nonBlocking`` (zero occurrences in claude_agent_sdk), so
  "unset" and "0" are behaviourally identical. Pinning "0" costs nothing and
  stops a future SDK/CLI that starts passing nonBlocking=true from silently
  flipping us to background init while the readiness question below is open.

DEFECT 2 — ``mcp_health_ok`` logged ``configured=%d ok=%d`` where ``ok`` was
  ``len(non_failed)``, counted over EVERY server in the CLI response including
  ones this session never configured. That produced the self-contradictory
  "configured=4 ok=5" seen in production, which was then cited as the evidence
  that background init was healthy. ``ok`` must be counted over the same set as
  ``configured``.

STILL OPEN (deliberately not "fixed" here — it is a product call): treating
"pending" as non-failed only means the health check does not FALSE-ALARM. It
does not establish that a first-turn tool call against a not-yet-connected MCP
succeeds. Blocking mode is what makes that moot today.
"""
from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.app_config_manager import AppConfigManager
from core.claude_environment import _configure_claude_environment
from core import session_unit as su

_ENV_VAR = "MCP_CONNECTION_NONBLOCKING"

# The CLI's own predicates, transcribed from the bundle. Used to prove the
# pinned value actually lands on the blocking branch rather than asserting a
# bare string equality that would survive a semantic change.
_CLI_FALSY = {"0", "false", "no", "off"}
_CLI_TRUTHY = {"1", "true", "yes", "on"}


def _cli_nonblocking(env_value: str | None, sdk_opt: bool | None = None) -> bool:
    """Reimplementation of the CLI's connect-mode decision (see module docstring).

    Returns True when the CLI would connect MCPs in BACKGROUND (non-blocking).
    """
    if env_value is not None and str(env_value).lower().strip() in _CLI_FALSY:
        return False
    if env_value is not None and str(env_value).lower().strip() in _CLI_TRUTHY:
        return True
    return bool(sdk_opt) if sdk_opt is not None else False


def _make_config() -> AppConfigManager:
    cfg = AppConfigManager.__new__(AppConfigManager)
    cfg._cache = {
        "use_bedrock": True,
        "aws_region": "us-east-1",
        "anthropic_base_url": None,
    }
    return cfg


@pytest.fixture(autouse=True)
def _restore_env():
    """Save/restore the one env var these tests mutate."""
    had = _ENV_VAR in os.environ
    prev = os.environ.get(_ENV_VAR)
    os.environ.pop(_ENV_VAR, None)
    yield
    os.environ.pop(_ENV_VAR, None)
    if had and prev is not None:
        os.environ[_ENV_VAR] = prev


# ── DEFECT 1: the mode must be PINNED to blocking ────────────────────────────

def test_unset_env_is_blocking_so_deletion_was_a_noop():
    """The premise the deletion rested on. If this ever flips, the CLI changed
    its default and the pin below becomes load-bearing rather than belt-and-braces."""
    assert _cli_nonblocking(None, sdk_opt=None) is False, (
        "UNSET must resolve to BLOCKING — deleting the env var cannot enable "
        "background init on its own"
    )
    assert _cli_nonblocking("0") is False
    assert _cli_nonblocking("1") is True, "only an explicit truthy value enables it"


def test_configure_pins_blocking_mcp_connect():
    """_configure_claude_environment must leave the CLI on the BLOCKING branch."""
    _configure_claude_environment(_make_config())
    assert os.environ.get(_ENV_VAR) == "0", "blocking mode must be pinned explicitly"
    assert _cli_nonblocking(os.environ[_ENV_VAR]) is False, (
        "the pinned value must land on the CLI's blocking branch"
    )


def test_pin_does_not_clobber_an_explicit_operator_override():
    """setdefault, not assignment: an operator who deliberately exports "1" to
    trial background init keeps the escape hatch (and gets non-blocking)."""
    os.environ[_ENV_VAR] = "1"
    _configure_claude_environment(_make_config())
    assert os.environ[_ENV_VAR] == "1", "must not clobber an explicit override"
    assert _cli_nonblocking(os.environ[_ENV_VAR]) is True


# ── DEFECT 2: `ok` must be counted over the same set as `configured` ─────────

def _make_unit(configured):
    """Bare SessionUnit carrying only the fields _check_mcp_health reads."""
    u = su.SessionUnit.__new__(su.SessionUnit)
    u.session_id = "test-sess"
    u._mcp_health_checked = False
    u._mcp_pending_reprobes_left = 3
    u._configured_mcps = set(configured)

    class _UnlockedIO:
        def locked(self):
            return False

    u._client_io = _UnlockedIO()
    u._client = MagicMock()
    return u


def _status(*pairs):
    return {"mcpServers": [{"name": n, "status": s} for n, s in pairs]}


def _parse_ok_line(caplog):
    """Return (configured, ok) from the mcp_health_ok log record, or None."""
    for rec in caplog.records:
        msg = rec.getMessage()
        if "mcp_health_ok" in msg:
            cfg = int(msg.split("configured=")[1].split()[0])
            ok = int(msg.split("ok=")[1].split()[0])
            return cfg, ok
    return None


@pytest.mark.asyncio
async def test_ok_excludes_servers_this_session_did_not_configure(caplog):
    """THE production bug: the CLI also reports user/global-scope servers. Those
    must not inflate `ok` past `configured` (the "configured=4 ok=5" line)."""
    u = _make_unit({"slack", "email", "git", "sqlite"})
    # 4 configured + 1 the session never configured — exactly the live shape.
    u._client.get_mcp_status = AsyncMock(return_value=_status(
        ("slack", "connected"), ("email", "connected"),
        ("git", "connected"), ("sqlite", "connected"),
        ("some-user-scope-mcp", "connected"),
    ))
    with caplog.at_level(logging.INFO, logger="core.session_unit"):
        assert await u._check_mcp_health() is None, "all configured up → no warning"
    parsed = _parse_ok_line(caplog)
    assert parsed is not None, "healthy path must emit mcp_health_ok"
    configured, ok = parsed
    assert (configured, ok) == (4, 4), f"expected 4/4, got configured={configured} ok={ok}"


@pytest.mark.parametrize("extra", [
    [],
    [("ghost", "connected")],
    [("ghost", "connected"), ("ghost2", "pending")],
    [("ghost", "disabled")],
])
@pytest.mark.asyncio
async def test_ok_never_exceeds_configured_invariant(caplog, extra):
    """ok <= configured for any number of unconfigured servers in the response.
    This is the invariant that makes the log line readable as 'N of N up'."""
    configured = {"slack", "email"}
    u = _make_unit(configured)
    u._client.get_mcp_status = AsyncMock(return_value=_status(
        ("slack", "connected"), ("email", "pending"), *extra,
    ))
    # email is pending → spend the re-probe budget so we reach the finalizing
    # branch that emits mcp_health_ok (pending is non-failed, so no warning).
    u._mcp_pending_reprobes_left = 0
    with caplog.at_level(logging.INFO, logger="core.session_unit"):
        assert await u._check_mcp_health() is None
    parsed = _parse_ok_line(caplog)
    assert parsed is not None
    cfg, ok = parsed
    assert cfg == len(configured)
    assert ok <= cfg, f"ok={ok} must never exceed configured={cfg} (extra={extra})"
    assert ok == 2, "both configured servers are non-failed (connected + pending)"


@pytest.mark.asyncio
async def test_ok_metric_is_not_vacuous_a_missing_configured_mcp_still_warns(caplog):
    """Mutation guard: the intersection must not be so lenient that a genuinely
    absent configured MCP stops warning. Without this, changing `ok` could be
    'fixed' by silently widening non_failed."""
    u = _make_unit({"slack", "email"})
    u._client.get_mcp_status = AsyncMock(return_value=_status(
        ("slack", "connected"), ("unrelated", "connected"),
    ))  # 'email' absent entirely → definitively missing
    with caplog.at_level(logging.INFO, logger="core.session_unit"):
        r = await u._check_mcp_health()
    assert r is not None, "a configured MCP missing from the response must warn"
    assert "email" in r.get("message", "")
    assert _parse_ok_line(caplog) is None, "warning path must not log mcp_health_ok"
