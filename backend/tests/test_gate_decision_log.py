"""Tests for core.gate_decision_log and its wiring in HookRegistry.

- A PreToolUse hook that denies or asks is logged once, with the full command.
- Allow / warn / empty results are not logged; the hook's result is unchanged.
- PostToolUse hooks are not wrapped.
- A write failure never raises into the tool call.
"""

import asyncio
import json

from core import gate_decision_log as gdl
from core.hook_builder import HookRegistry


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _deny(reason="nope"):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "deny", "permissionDecisionReason": reason}}


def _run(registry, event="PreToolUse", data=None):
    fn = registry.build_sdk_hooks()[event][0].hooks[0]
    return asyncio.run(fn(data or {}, "tu1", {}))


def test_deny_is_logged_with_full_command(tmp_path, monkeypatch):
    log = tmp_path / "g.jsonl"
    monkeypatch.setenv("SWARM_GATE_LOG", str(log))
    long_cmd = "echo " + "x" * 500

    async def gate(_i, _t, _c):
        return _deny("because")

    reg = HookRegistry()
    reg.register("PreToolUse", gate, "my_gate", matcher="Bash")
    out = _run(reg, data={"tool_name": "Bash", "tool_input": {"command": long_cmd},
                          "session_id": "s1"})
    assert out == _deny("because")
    (rec,) = _lines(log)
    assert rec["gate"] == "my_gate" and rec["decision"] == "deny"
    assert rec["input"] == long_cmd and rec["reason"] == "because"
    assert rec["tool"] == "Bash" and rec["session_id"] == "s1"


def test_chained_deny_logged_once_by_the_denying_gate(tmp_path, monkeypatch):
    log = tmp_path / "g.jsonl"
    monkeypatch.setenv("SWARM_GATE_LOG", str(log))

    async def ok(_i, _t, _c):
        return {}

    async def gate(_i, _t, _c):
        return _deny()

    reg = HookRegistry()
    reg.register("PreToolUse", ok, "first", matcher="Bash")
    reg.register("PreToolUse", gate, "second", matcher="Bash")
    _run(reg, data={"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert [r["gate"] for r in _lines(log)] == ["second"]


def test_allow_and_warn_not_logged(tmp_path, monkeypatch):
    log = tmp_path / "g.jsonl"
    monkeypatch.setenv("SWARM_GATE_LOG", str(log))

    async def warn(_i, _t, _c):
        return {"additionalContext": "careful"}

    reg = HookRegistry()
    reg.register("PreToolUse", warn, "w", matcher="Write")
    assert _run(reg) == {"additionalContext": "careful"}
    assert _lines(log) == []


def test_post_tool_use_not_wrapped(tmp_path, monkeypatch):
    log = tmp_path / "g.jsonl"
    monkeypatch.setenv("SWARM_GATE_LOG", str(log))

    async def post(_i, _t, _c):
        return {"decision": "block", "reason": "post"}

    reg = HookRegistry()
    reg.register("PostToolUse", post, "p")
    _run(reg, event="PostToolUse")
    assert _lines(log) == []


def test_write_failure_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    monkeypatch.setenv("SWARM_GATE_LOG", str(blocker / "under_a_file.jsonl"))

    async def gate(_i, _t, _c):
        return _deny()

    reg = HookRegistry()
    reg.register("PreToolUse", gate, "g", matcher="Bash")
    assert _run(reg) == _deny()


def test_force_bypass_record(tmp_path, monkeypatch):
    log = tmp_path / "g.jsonl"
    monkeypatch.setenv("SWARM_GATE_LOG", str(log))
    gdl.record_force_bypass("pipeline_test_gate",
                            {"tool_name": "Bash", "tool_input": {"command": "pytest"}}, "op")
    (rec,) = _lines(log)
    assert rec["decision"] == "force_bypass" and rec["input"] == "pytest"
