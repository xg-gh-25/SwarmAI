"""Every command a gate suggests in its deny message passes the whole Bash gate chain.

Why: a deny is only useful when the agent can run what it says. On 2026-10-21 the
pytest guard suggested `pytest … > file`, which its own second rule denied, so
agents were refused twice in a row (bash_misuse_report: 95 sessions with 3+ denials
by that gate in a week). This file checks the suggestion of each gate against the
REAL PreToolUse chain built by hook_builder.build_hooks, not one gate alone.

Methodology: build_hooks with a mock permission manager and a session context;
every PreToolUse matcher whose pattern matches "Bash" (or has none) is run on the
command, in registration order. A case triggers a deny, takes the command the
reason suggests (``Run it as: `…` `` or the line after ``instead:``), fills its
placeholders, and asserts no hook in the chain denies or asks for it. Run state is
real run.json files in a temp workspace; worktree mode is off.

Add a case here whenever a gate's deny text suggests a command.
"""
import asyncio
import json
import re
from unittest.mock import MagicMock

import pytest

from core import security_hooks as sh
from core.hook_builder import build_hooks

REPO = sh._SOURCE_REPO_ROOT
SID = "app-session-suggest"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE", str(root))
    monkeypatch.setenv("SWARM_WORKTREE_MODE", "off")
    monkeypatch.setattr(sh, "_ACTIVE_RUN_GLOB_ROOT", str(root))
    monkeypatch.delenv("SWARM_GATE_FORCE", raising=False)
    return root


def _seed_run(ws, run_id="run_sugg0001", project="SwarmAI"):
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps(
        {"id": run_id, "project": project, "status": "running", "session_id": SID}))
    return run_id


def _chain(command: str, cwd: str = REPO) -> list:
    """(hook matcher, reason) for every hook of the real chain that denies or asks."""
    async def go():
        ctx = {"sdk_session_id": SID, "app_session_id": SID}
        hooks, _, _ = await build_hooks({"id": "default"}, False, False, None, ctx, MagicMock())
        hits = []
        for hm in hooks.get("PreToolUse", []):
            m = getattr(hm, "matcher", None)
            if m and not re.fullmatch(m, "Bash"):
                continue
            for h in hm.hooks:
                r = await asyncio.wait_for(h({"tool_name": "Bash", "tool_input": {"command": command},
                                              "cwd": cwd, "hook_event_name": "PreToolUse"},
                                             "tu-1", {"signal": None}), 30)
                out = (r or {}).get("hookSpecificOutput", {})
                if out.get("permissionDecision") in ("deny", "ask"):
                    hits.append((m, out.get("permissionDecisionReason", "")))
        return hits
    return asyncio.run(go())


def _suggested(reason: str) -> list:
    """The commands a deny reason tells the agent to run."""
    cmds = re.findall(r"Run it as: `([^`]+)`", reason)
    cmds += [m.strip() for m in re.findall(r"instead[^:\n]*:\n([^\n]+)", reason)]
    cmds += [m.strip() for m in re.findall(r"close it first[^\n]*\n([^\n]+)", reason)]
    return cmds


def _fill(cmd: str) -> str:
    return cmd.replace("<tests>", "tests/test_a.py").replace("<why>", "done").replace("'<why>'", "done")


def _assert_suggestions_pass(denied_by: list, cwd: str = REPO):
    assert denied_by, "the case must trigger a deny"
    suggestions = [c for _, r in denied_by for c in _suggested(r)]
    assert suggestions, f"the deny suggests no runnable command: {denied_by}"
    for cmd in suggestions:
        cmd = _fill(cmd)
        assert _chain(cmd, cwd) == [], f"suggested command is itself refused: {cmd}"


def test_pytest_guard_suggestion_passes_the_chain(ws):
    hits = _chain("python -m pytest tests/test_a.py | tail -5")
    assert [m for m, _ in hits] == ["Bash"]
    _assert_suggestions_pass(hits)
    s = _fill(_suggested(hits[0][1])[0])
    assert _chain(s + " > /tmp/t.txt 2>&1") == []
    assert _chain(s + " | tail -5") == []


def test_pipeline_test_gate_suggestions_pass_the_chain(ws):
    _seed_run(ws)
    hits = _chain("perl -e 'alarm 100; exec @ARGV' python -m pytest backend/tests/test_a.py -q")
    assert any("pipeline test runner" in r for _, r in hits)
    _assert_suggestions_pass(hits)
