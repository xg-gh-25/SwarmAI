"""Regression tests for the adversarial review of the prompt-board refactor.

1. A decision that comes back while the stream is still suspended at the prompt
   event's yield must find the unit WAITING_INPUT on that prompt, so the
   continuation reads on (else no reader remains: a wedge).
2. Delivering a decision and switching state happen with no await in between: a
   continuation cancelled while the decision is being recorded cannot leave a
   delivered decision with nobody reading.
3. `sh -c '...' ARGS` and `bash <<EOF` scripts are judged like the outer command.
4. Every `tee` operand is an output (an earlier one can be a store).
5. A permission decision while the unit waits on an unrelated QUESTION does not
   resume the unit or clear the question.
"""

import asyncio
from unittest.mock import patch

import pytest

from core.ask_question_manager import ask_question_manager as aqm
from core.permission_manager import permission_manager as pm
from core.security_hooks import _is_dangerous_rm, _targets_irreplaceable_store
from core.session_unit import SessionState, SessionUnit

STORE_DB = "~/.swarm-ai/SwarmWS/Projects/critical/data.db"


def _unit(sid: str) -> SessionUnit:
    u = SessionUnit(session_id=sid, agent_id="a")
    u._client = object()
    u._transition(SessionState.IDLE)
    u._transition(SessionState.STREAMING)
    return u


def test_prompt_state_is_set_before_the_event_leaves():
    """Source-level invariant of the two surface branches."""
    import inspect
    from core import streaming_orchestrator as so
    src = inspect.getsource(so.StreamingOrchestrator._read_formatted_response_body)
    for event in ('"type": "ask_user_question"', '"type": "cmd_permission_request"'):
        at = src.index(event)
        before = src[:at][-900:]
        assert "_transition(SessionState.WAITING_INPUT)" in before, event


@pytest.mark.asyncio
async def test_decision_switches_state_with_no_await_in_between():
    unit = _unit("rev-2")
    rid = "perm_rev2"
    unit._pending_question = {"request_id": rid, "tool_use_id": rid}
    unit._pending_tool_use_id = rid
    unit._transition(SessionState.WAITING_INPUT)
    waiter = asyncio.ensure_future(pm.request_decision(unit.session_id, {"requestId": rid}))
    await asyncio.sleep(0)
    never = asyncio.Event()

    async def _blocked_record(*_a):
        await never.wait()

    async def _reads():
        yield {"type": "assistant"}
        await never.wait()

    with patch("core.permission_prompt.record_decision", _blocked_record), \
         patch.object(SessionUnit, "_read_formatted_response", lambda self: _reads()):
        gen = unit.continue_with_permission(rid, True)
        first = await gen.__anext__()
        assert first == {"type": "assistant"}
        assert unit.state == SessionState.STREAMING and unit._pending_question is None
        await gen.aclose()
    assert await asyncio.wait_for(waiter, 2) == "approve"


@pytest.mark.asyncio
async def test_permission_while_waiting_on_a_question_does_not_resume():
    unit = _unit("rev-5")
    aqm.register_waiter("tu_A")
    unit._pending_tool_use_id = "tu_A"
    unit._pending_question = {"tool_use_id": "tu_A", "questions": []}
    unit._transition(SessionState.WAITING_INPUT)
    waiter = asyncio.ensure_future(pm.request_decision(unit.session_id, {"requestId": "perm_B"}))
    await asyncio.sleep(0)
    try:
        events = [e async for e in unit.continue_with_permission("perm_B", True)]
        assert events == []
        assert await asyncio.wait_for(waiter, 2) == "approve"
        assert unit.state == SessionState.WAITING_INPUT
        assert unit._pending_tool_use_id == "tu_A"
    finally:
        aqm.discard_waiter("tu_A")


def test_scripts_handed_to_another_shell_are_judged():
    for cmd in (
        f"sh -c 'rm \"$1\"' _ {STORE_DB}",
        f"bash -c 'cd /tmp && rm -f {STORE_DB}'",
        f"bash <<'EOF'\necho start\nrm {STORE_DB}\nEOF",
        f"sudo bash -s <<EOF\nmv {STORE_DB} /tmp/x\nEOF",
    ):
        assert _targets_irreplaceable_store(cmd) is True, cmd
    assert _is_dangerous_rm("sh -c 'rm -rf \"$1\"' _ ~/Documents") is True
    assert _is_dangerous_rm("bash <<'EOF'\nrm -rf ~/Documents\nEOF") is True


def test_heredoc_fed_to_a_non_shell_is_data():
    cmd = f"cat > /tmp/notes.md <<'EOF'\nrm {STORE_DB}\nEOF"
    assert _targets_irreplaceable_store(cmd) is False


def test_every_tee_output_is_checked(tmp_path):
    store = tmp_path / "Projects" / "critical"
    store.mkdir(parents=True)
    (store / "data.db").write_text("x")
    assert _targets_irreplaceable_store(f"tee {store}/data.db /tmp/copy < /dev/null") is True
    assert _targets_irreplaceable_store(f"tee -a {store}/data.db < /dev/null") is False
    assert _targets_irreplaceable_store(f"echo x | tee {store}/new.md") is False
