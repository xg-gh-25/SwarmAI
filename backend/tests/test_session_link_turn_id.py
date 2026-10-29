"""session_link_gate guards SWARM_TURN_ID like SWARM_APP_SESSION_ID (design
2026-10-29-pipeline-retro-fixes, the residual its review named).

run_owner_gate hands a landing command the turn id, which the landing writes into its
reflog record; a command that sets it attributes the landing to another turn's changes
card. Questions: is every removal / override / nested form of the session-id matrix
denied for the turn id too, with its own reason; are the same reads still allowed; does
the session id keep its own deny text; and does the variable switch back after a check?
"""
import asyncio

import pytest

from core import security_hooks as sh
from tests.test_session_link_gate import NESTED, OVERRIDES, READS_ALLOWED, REMOVALS, V

T = "SWARM_TURN_ID"


def _call(command):
    gate = sh.create_session_link_gate({"sdk_session_id": "app-session-1"})
    return asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": command},
                             "cwd": "/tmp"}, None, None))


def _reason(res):
    out = res.get("hookSpecificOutput", {})
    return out.get("permissionDecisionReason", "") if out.get("permissionDecision") == "deny" else None


def _as_turn(forms):
    return [c.replace(V, T) for c in forms if V in c]


@pytest.mark.parametrize("command", _as_turn(REMOVALS + OVERRIDES + NESTED) + [
    f"export {T}=0123456789abcdef0123456789abcdef; python -m scripts.session_worktree land /w",
    f'{T}=deadbeef "$SWARM_BACKEND_BIN" pipeline-v1 land --project P --run-id run_x',
])
def test_setting_or_removing_the_turn_id_is_denied(command):
    reason = _reason(_call(command))
    assert reason is not None, command
    # a form that is also a session-id unlink (`sudo VAR=x python` resets the env)
    # is denied with the session id's reason
    assert (T in reason and "changes card" in reason) or V in reason, reason


def test_a_reset_that_keeps_the_session_id_is_allowed():
    """Dropping the turn id only loses the attribution (turn=none), never forges it."""
    assert _call(f"sudo --preserve-env={V},PATH python a.py") == {"decision": "approve"}


@pytest.mark.parametrize("command", _as_turn(READS_ALLOWED) + [
    f"grep -rn {T} backend/core/run_owner_gate.py"])
def test_reading_the_turn_id_is_allowed(command):
    assert _call(command) == {"decision": "approve"}, command


def test_the_session_id_keeps_its_own_reason_and_the_variable_switches_back():
    reason = _reason(_call(f"unset {V}"))
    assert reason is not None and V in reason and "pipeline test gate" in reason
    # in the same context, as the hook chain runs one gate after another
    assert sh._command_link_var(f"unset {T}", T) is not None
    assert sh._link_var() == V
    assert sh._command_session_link(f"unset {V}") is not None
