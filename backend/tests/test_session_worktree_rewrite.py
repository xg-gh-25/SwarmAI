"""run_owner_gate's session_worktree rewrite (design 2026-10-25-turn-result-card.md §3.2).

What is tested (the real gate factory, no IO):
- an exact first-of-line ``"$SWARM_BACKEND_BIN" py -m scripts.session_worktree
  create|land|remove`` (or ``python3 -m ...``) from the main agent gets
  ``SWARM_APP_SESSION_ID`` and ``SWARM_TURN_ID`` prefixed via updatedInput;
- from a sub-agent (hook input with ``agent_id``) only the session id, never a turn id;
- not rewritten: a later command (``cd x && ...``), ``echo``, a heredoc, a ``$(...)``,
  a backtick, a wrapper or an assignment in front, another module, an unknown verb;
- a prewarmed (not yet adopted) session and a context without a turn id;
- SessionUnit._reset_turn_state gives each admitted turn a fresh turn id, and a
  question / permission continuation keeps it;
- the pipeline rewrite (_with_session) is unchanged for its own command.
"""
from __future__ import annotations

import asyncio
import shlex

import pytest

from core.run_owner_gate import _with_session, create_run_owner_gate

SID = "11111111-2222-3333-4444-555555555555"
TURN = "0123456789abcdef0123456789abcdef"
LAND = '"$SWARM_BACKEND_BIN" py -m scripts.session_worktree land /w/x'


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
    '"$SWARM_BACKEND_BIN" py -m scripts.session_worktree remove /w/x',
    "python3 -m scripts.session_worktree land /w/x",
    "python -m scripts.session_worktree land /w/x | tail -1",
    "  " + LAND,
])
def test_main_agent_call_gets_session_and_turn(cmd):
    new = _rewritten(_run(cmd))
    prefix = f"SWARM_APP_SESSION_ID={shlex.quote(SID)} SWARM_TURN_ID={TURN} "
    assert new == cmd[:len(cmd) - len(cmd.lstrip())] + prefix + cmd.lstrip()


def test_sub_agent_call_gets_no_turn_id():
    new = _rewritten(_run(LAND, agent_id="a123"))
    assert new == f"SWARM_APP_SESSION_ID={shlex.quote(SID)} {LAND}"
    assert "SWARM_TURN_ID" not in new


@pytest.mark.parametrize("cmd", [
    "cd /w/x && " + LAND,
    "echo scripts.session_worktree land",
    "cat > /tmp/a <<'EOF'\nx\nEOF\n" + LAND,
    LAND + " $(date)",
    LAND + " `date`",
    "env " + LAND,
    "timeout 60 " + LAND,
    "FOO=1 " + LAND,
    '"$SWARM_BACKEND_BIN" py -m scripts.other land /w/x',
    '"$SWARM_BACKEND_BIN" py -m scripts.session_worktree frob /w/x',
    '"$SWARM_BACKEND_BIN" py scripts/session_worktree.py land /w/x',
])
def test_not_rewritten(cmd):
    assert _rewritten(_run(cmd)) is None


def test_prewarmed_session_and_missing_turn_id():
    from core.session_router import PREWARM_SESSION_PREFIX
    assert _rewritten(_run(LAND, ctx={"sdk_session_id": PREWARM_SESSION_PREFIX + "x",
                                      "_turn_id": TURN})) is None
    new = _rewritten(_run(LAND, ctx={"sdk_session_id": SID}))
    assert new == f"SWARM_APP_SESSION_ID={shlex.quote(SID)} {LAND}"


def test_approved_phase_does_not_rewrite():
    assert _rewritten(_run(LAND, phase="approved")) is None


def test_land_calls_handed_the_turn_id_are_counted():
    """TurnChanges reads landings only when this turn handed a land its id, and stops at
    that many (design 2026-10-25-turn-result-card.md §4)."""
    ctx = {"sdk_session_id": SID, "_turn_id": TURN}
    _run(LAND, ctx=ctx)
    _run(LAND, ctx=ctx)
    _run('"$SWARM_BACKEND_BIN" py -m scripts.session_worktree create /r', ctx=ctx)
    _run(LAND, ctx=ctx, agent_id="a1")                  # a sub-agent: no turn id, not counted
    _run("cd /x && " + LAND, ctx=ctx)                    # not rewritten, not counted
    assert ctx["_turn_lands"] == 2


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
    unit._hook_session_context["_turn_lands"] = 3
    unit._reset_turn_state()
    assert unit._hook_session_context["_turn_lands"] == 0, "a new turn counts its own lands"


def test_continuations_do_not_reset_the_turn():
    import inspect
    from core.session_unit import SessionUnit
    for name in ("continue_with_answer", "continue_with_permission"):
        assert "_reset_turn_state" not in inspect.getsource(getattr(SessionUnit, name))
