"""run_owner_gate's session_worktree hand-off (design 2026-10-26-turn-cards-unified.md §3.2).

What is tested (the real gate factory, no IO):
- any Bash line of the main agent that names ``scripts.session_worktree`` gets
  ``export SWARM_APP_SESSION_ID=… SWARM_TURN_ID=…;`` in front of the WHOLE line via
  updatedInput, whatever its shape (``cd &&``, ``$(…)``, a heredoc, a wrapper);
- from a sub-agent (hook input with ``agent_id``): only the session id, never a turn id;
- not rewritten: a line that does not name the module, a line that sets either
  variable itself, a prewarmed session, an invalid session id, the approved phase;
- ``_turn_sw`` is set by a main-agent session_worktree line only; a new turn resets it;
- SessionUnit._reset_turn_state gives each admitted turn a fresh turn id, and a
  question / permission continuation keeps it;
- a denied pipeline segment denies the line; the pipeline rewrite (_with_session) is
  unchanged for its own command.
"""
from __future__ import annotations

import asyncio
import shlex

import pytest

from core.run_owner_gate import _with_session, create_run_owner_gate

SID = "11111111-2222-3333-4444-555555555555"
TURN = "0123456789abcdef0123456789abcdef"
LAND = '"$SWARM_BACKEND_BIN" py -m scripts.session_worktree land /w/x'
EXPORT = f"export SWARM_APP_SESSION_ID={shlex.quote(SID)} SWARM_TURN_ID={TURN}; "


def _run(command, *, ctx=None, agent_id=None, phase="check"):
    ctx = {"sdk_session_id": SID, "_turn_id": TURN} if ctx is None else ctx
    gate = create_run_owner_gate(ctx, phase)
    data = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_id:
        data["agent_id"] = agent_id
    return asyncio.run(gate(data, "tu1", None))


def _rewritten(out):
    return ((out.get("hookSpecificOutput") or {}).get("updatedInput") or {}).get("command")


@pytest.mark.parametrize("cmd", [
    LAND,
    '"$SWARM_BACKEND_BIN" py -m scripts.session_worktree create /r --name t',
    "python3 -m scripts.session_worktree land /w/x | tail -1",
    "cd /w/x && " + LAND,
    "W=$(ls -d /w/*); " + LAND + " $W",
    "cat > /tmp/a <<'EOF'\nx\nEOF\n" + LAND,
    "timeout 60 " + LAND,
    "bash -c '" + LAND + "'",
    "echo scripts.session_worktree",
])
def test_main_agent_line_gets_the_export_in_front(cmd):
    assert _rewritten(_run(cmd)) == EXPORT + cmd


def test_sub_agent_line_gets_no_turn_id():
    new = _rewritten(_run(LAND, agent_id="a123"))
    assert new == f"export SWARM_APP_SESSION_ID={shlex.quote(SID)}; {LAND}"


@pytest.mark.parametrize("cmd", [
    '"$SWARM_BACKEND_BIN" py -m scripts.other land /w/x',
    "SWARM_TURN_ID=" + TURN + " " + LAND,
    f"SWARM_APP_SESSION_ID={SID} " + LAND,
])
def test_not_rewritten(cmd):
    assert _rewritten(_run(cmd)) is None


def test_prewarmed_or_invalid_session_and_missing_turn_id():
    from core.session_router import PREWARM_SESSION_PREFIX
    assert _rewritten(_run(LAND, ctx={"sdk_session_id": PREWARM_SESSION_PREFIX + "x",
                                      "_turn_id": TURN})) is None
    assert _rewritten(_run(LAND, ctx={"sdk_session_id": "", "_turn_id": TURN})) is None
    new = _rewritten(_run(LAND, ctx={"sdk_session_id": SID}))
    assert new == f"export SWARM_APP_SESSION_ID={shlex.quote(SID)}; {LAND}"


def test_approved_phase_does_not_rewrite():
    assert _rewritten(_run(LAND, phase="approved")) is None


def test_turn_sw_is_set_by_a_main_agent_line_only():
    ctx = {"sdk_session_id": SID, "_turn_id": TURN, "_turn_sw": False}
    _run("ls /tmp", ctx=ctx)
    _run(LAND, ctx=ctx, agent_id="a1")
    assert ctx["_turn_sw"] is False
    _run("cd /x && " + LAND, ctx=ctx)
    assert ctx["_turn_sw"] is True


def test_pipeline_rewrite_is_unchanged():
    cmd = '"$SWARM_BACKEND_BIN" pipeline-v1 status --project P --run-id run_abcd1234'
    assert _with_session(cmd, SID) == f"SWARM_APP_SESSION_ID={shlex.quote(SID)} {cmd}"


def test_each_admitted_turn_gets_a_fresh_turn_id():
    from core.session_unit import SessionUnit
    unit = SessionUnit(session_id="s-turn", agent_id="default")
    unit._hook_session_context = {"sdk_session_id": "s-turn"}
    unit._reset_turn_state()
    first = unit._hook_session_context["_turn_id"]
    unit._reset_turn_state()
    second = unit._hook_session_context["_turn_id"]
    from core.app_session_id import valid_turn_id
    assert valid_turn_id(first) and valid_turn_id(second) and first != second
    unit._hook_session_context["_turn_sw"] = True
    unit._reset_turn_state()
    assert unit._hook_session_context["_turn_sw"] is False, "a new turn starts unmarked"


def test_continuations_do_not_reset_the_turn():
    import inspect
    from core.session_unit import SessionUnit
    for name in ("continue_with_answer", "continue_with_permission"):
        assert "_reset_turn_state" not in inspect.getsource(getattr(SessionUnit, name))


PIPE = '"$SWARM_BACKEND_BIN" pipeline-v1 status run_abc'


def test_a_land_and_an_allowed_pipeline_call_keep_the_export(monkeypatch):
    from core import run_owner_gate as rog
    seen = []
    monkeypatch.setattr(rog, "_decide", lambda sid, cmd, *a: seen.append(cmd) or None)
    cmd = f"{LAND} && {PIPE}"
    assert _rewritten(_run(cmd)) == EXPORT + cmd
    assert seen == [cmd], "the pipeline check ran"


def test_a_denied_pipeline_segment_denies_the_line(monkeypatch):
    from core import run_owner_gate as rog
    monkeypatch.setattr(rog, "_decide", lambda *a: rog._deny("not yours"))
    out = _run(f"{LAND} && {PIPE}")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── a pipeline landing gets the same hand-off (design 2026-10-29-pipeline-retro-fixes F1) ──

V1_LAND = ('"$SWARM_BACKEND_BIN" pipeline-v1 land --project P --run-id run_abcd1234 '
           '--message "m"')


def _allow_with_session(monkeypatch):
    from core import run_owner_gate as rog

    def decide(sid, cmd, *a):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "allow",
                                       "updatedInput": {"command": _with_session(cmd, sid)}}}
    monkeypatch.setattr(rog, "_decide", decide)


def test_a_pipeline_land_gets_the_export_and_marks_the_turn(monkeypatch):
    _allow_with_session(monkeypatch)
    ctx = {"sdk_session_id": SID, "_turn_id": TURN, "_turn_sw": False}
    assert _rewritten(_run(V1_LAND, ctx=ctx)) == EXPORT + _with_session(V1_LAND, SID)
    assert ctx["_turn_sw"] is True


def test_another_pipeline_command_gets_no_export(monkeypatch):
    _allow_with_session(monkeypatch)
    ctx = {"sdk_session_id": SID, "_turn_id": TURN, "_turn_sw": False}
    cmd = '"$SWARM_BACKEND_BIN" pipeline-v1 status --project P --run-id run_abcd1234'
    assert _rewritten(_run(cmd, ctx=ctx)) == _with_session(cmd, SID)
    assert ctx["_turn_sw"] is False


def test_a_sub_agent_pipeline_land_gets_no_turn_id(monkeypatch):
    _allow_with_session(monkeypatch)
    ctx = {"sdk_session_id": SID, "_turn_id": TURN, "_turn_sw": False}
    new = _rewritten(_run(V1_LAND, ctx=ctx, agent_id="a1"))
    assert new == f"export SWARM_APP_SESSION_ID={shlex.quote(SID)}; " + _with_session(V1_LAND, SID)
    assert ctx["_turn_sw"] is False


RUN_LAND = ('"$SWARM_BACKEND_BIN" pipeline-v1 run-land --project P --run-id run_abcd1234 '
            '--request req_1')


def test_an_unapproved_run_land_is_still_denied_in_the_approved_phase(monkeypatch):
    from core import security_hooks
    _allow_with_session(monkeypatch)
    monkeypatch.setattr(security_hooks, "consume_land_approval", lambda *a: False)
    out = _run(RUN_LAND, phase="approved")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "updatedInput" not in out["hookSpecificOutput"]


def test_an_approved_run_land_gets_the_same_rewrite_in_both_phases(monkeypatch):
    from core import security_hooks
    _allow_with_session(monkeypatch)
    monkeypatch.setattr(security_hooks, "consume_land_approval", lambda *a: True)
    approved = _rewritten(_run(RUN_LAND, phase="approved"))
    assert approved == _rewritten(_run(RUN_LAND)) == EXPORT + _with_session(RUN_LAND, SID)


def test_a_denied_pipeline_land_stays_denied(monkeypatch):
    from core import run_owner_gate as rog
    monkeypatch.setattr(rog, "_decide", lambda *a: rog._deny("not yours"))
    out = _run(V1_LAND)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "updatedInput" not in out["hookSpecificOutput"]
