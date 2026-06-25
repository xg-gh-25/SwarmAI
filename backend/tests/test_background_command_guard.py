"""Forcing tests for the background-command PreToolUse guard.

Tests that the guard ENTERS its decision path (not just that it exists):
- backgrounding (run_in_background flag OR shell &/nohup/setsid) is DENIED
- foreground commands and false-positive ampersands (&&, 2>&1) are APPROVED
- the long-lived-service allowlist (dev servers, --watch, tail -f) may background
- non-Bash tools are ignored

Closes the runaway hole the foreground 120s timeout cannot bound
(anthropics/claude-code#61568): a backgrounded find/pytest escapes the timeout
and can hang indefinitely.
"""
from __future__ import annotations

import pytest

from core.security_hooks import background_command_guard, _is_backgrounded


def _bash(command: str, *, run_in_background: bool | None = None) -> dict:
    tool_input: dict = {"command": command}
    if run_in_background is not None:
        tool_input["run_in_background"] = run_in_background
    return {"tool_name": "Bash", "tool_input": tool_input}


def _denied(result: dict) -> bool:
    return (
        result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


# ── _is_backgrounded detection ────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "find desktop -maxdepth 2 -name 'vitest.config*' &",
    "python -m pytest tests/ &",
    "nohup ./long.sh",
    "setsid ./worker.sh",
    "sleep 100 & echo started",
    "./build.sh & disown",
])
def test_detects_shell_backgrounding(command):
    assert _is_backgrounded(command, {"command": command}) is True


def test_detects_run_in_background_flag():
    assert _is_backgrounded("find . -name x", {"run_in_background": True}) is True


@pytest.mark.parametrize("command", [
    "find desktop -name 'vite.config*'",        # plain foreground
    "make build && npm test",                    # && is logical-AND, not bg
    "python script.py 2>&1 | tee log",          # fd dup, not bg
    "grep -r foo src &> /dev/null",             # &> redirect, not bg
    "ls -la",
])
def test_foreground_and_false_positive_amps_not_flagged(command):
    assert _is_backgrounded(command, {"command": command}) is False


# ── the guard decision (the path that was never exercised) ──────────────────

@pytest.mark.asyncio
async def test_backgrounded_find_is_denied():
    """The exact bug: a backgrounded find that hung ~10min — now blocked."""
    result = await background_command_guard(
        _bash("find desktop -maxdepth 2 -name 'vitest.config*' &"), None, None,
    )
    assert _denied(result)
    assert "foreground" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


@pytest.mark.asyncio
async def test_backgrounded_pytest_is_denied():
    result = await background_command_guard(
        _bash("python -m pytest tests/", run_in_background=True), None, None,
    )
    assert _denied(result)


@pytest.mark.asyncio
async def test_foreground_command_approved():
    result = await background_command_guard(
        _bash("find desktop -name 'vite.config*'"), None, None,
    )
    assert result == {"decision": "approve"}


@pytest.mark.asyncio
async def test_logical_and_not_treated_as_background():
    result = await background_command_guard(
        _bash("brazil-build && npm test"), None, None,
    )
    assert result == {"decision": "approve"}


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    "npm run dev &",
    "vite &",
    "tail -f ~/.swarm-ai/logs/backend-daemon.log &",
    "tsc --watch &",
    "./dev.sh &",
])
async def test_longlived_services_may_background(command):
    """Genuine long-lived services are allowlisted to background."""
    result = await background_command_guard(_bash(command), None, None)
    assert result == {"decision": "approve"}


@pytest.mark.asyncio
async def test_non_bash_tool_ignored():
    result = await background_command_guard(
        {"tool_name": "Read", "tool_input": {"file_path": "x"}}, None, None,
    )
    assert result == {"decision": "approve"}
