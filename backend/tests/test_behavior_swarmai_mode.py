"""Behavior cases can run against SwarmAI's own system prompt and carry
deterministic checks (design 2026-10-26: cases must test the product agent,
not a bare CLI that happens to read our files)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import eval_runner as er  # noqa: E402
from scripts import scenario_runner as sr  # noqa: E402


def _stream(*messages):
    return "\n".join(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": m}})
                     for m in messages) + "\n" + json.dumps({"type": "result", "result": "done"})


def _text(t):
    return {"type": "text", "text": t}


def _tool(name="Read"):
    return {"type": "tool_use", "name": name, "input": {"file_path": "x.md"}}


def test_parse_pre_tool_text_counts_text_since_last_call():
    out = _stream([_text("I read it as a lookup."), _tool()], [_tool()], [_text("now"), _tool()], [_text("answer")])
    assert sr.parse_pre_tool_text(out) == [len("I read it as a lookup."), 0, 3]


@pytest.mark.parametrize("checks,text,pre,failed", [
    (["reply_cjk"], "端口是 18321。", [0], []),
    (["reply_cjk"], "The port is 18321.", [0], ["reply_cjk"]),
    (["reply_cjk"], "用 `retry_manager.py` 里的 RetryManager class 处理重试", [0], []),
    (["reply_cjk"], "在这里：`backend core session unit retry manager streaming orchestrator`", [0], []),  # code spans don't count
    (["no_pre_tool_text"], "x", [40, 0, 0], []),                 # opening restatement is allowed
    (["no_pre_tool_text"], "x", [40, 0, 12], ["no_pre_tool_text"]),
    (["no_pre_tool_text"], "x", [900], ["no_pre_tool_text"]),     # more than a restatement
    (["no_pre_tool_text"], "x", [], ["no_pre_tool_text"]),        # no call → cannot pass
    (["no_next_steps_block"], "18321.\n```next-steps\nx\n```", [0], ["no_next_steps_block"]),
    (["no_next_steps_block"], "18321.", [0], []),
    (["typo_check"], "x", [0], ["unknown check"]),               # fails closed
])
def test_checks(checks, text, pre, failed):
    got = er._behavior_check_failures(checks, text, pre)
    assert [g.split(" ")[0] for g in got] == [f.split(" ")[0] for f in failed], got


def _case(**kw):
    c = {"id": "GS_X", "evaluators": ["trajectory_capture"], "title": "x", "tier": "behavior",
         "scenario": {"prompt": "read x.md", "system": "swarmai"}, "expected_trajectory": ["Read x.md"],
         "trajectory_match": "any_order"}
    c.update(kw)
    return c


def test_capture_passes_system_and_applies_checks(monkeypatch):
    seen = {}

    def _raw(prompt, allowed_tools=None, timeout=240, system="bare", workspace=None):
        seen["system"] = system
        stdout = _stream([_tool()], [_text("let me also check"), _tool()], [_text("答案")])
        return sr.parse_trajectory(stdout), "答案", stdout
    monkeypatch.setattr("scripts.scenario_runner.run_scenario_raw", _raw)
    ok = er.eval_trajectory_capture(_case(checks=["reply_cjk"]))
    assert seen["system"] == "swarmai" and ok["status"] == "passed", ok
    bad = er.eval_trajectory_capture(_case(checks=["no_pre_tool_text"]))
    assert bad["status"] == "failed" and "no_pre_tool_text" in bad["notes"], bad


def test_swarmai_mode_spawns_with_our_prompt_and_model(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(sr, "_swarmai_system_prompt", lambda workspace=None: "SWARMAI SYSTEM PROMPT")
    monkeypatch.setattr("jobs.model_resolve.resolve_job_model", lambda config=None: "the-default-model")
    monkeypatch.setattr(sr, "_resolve_claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr(sr, "_cli_supports_bare", lambda p: True)
    monkeypatch.setattr(sr, "_build_cli_env", lambda x: {})

    class _P:
        returncode = 0
        stdout = _stream([_tool()], [_text("ok")])
        stderr = ""

    def _run(cmd, **kw):
        f = cmd[cmd.index("--system-prompt-file") + 1]
        seen.update(cmd=cmd, prompt=Path(f).read_text(), file=f)
        return _P()
    monkeypatch.setattr(sr.subprocess, "run", _run)
    traj, text, stdout = sr.run_scenario_raw("hi", ["Read"], 10, system="swarmai")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "the-default-model"
    assert seen["prompt"] == "SWARMAI SYSTEM PROMPT"
    assert not os.path.exists(seen["file"])                 # temp prompt file removed
    assert traj and "Read" in traj[0]


def test_bare_mode_is_unchanged_and_unknown_system_is_infra(monkeypatch):
    monkeypatch.setattr(sr, "_resolve_claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr(sr, "_cli_supports_bare", lambda p: True)
    monkeypatch.setattr(sr, "_build_cli_env", lambda x: {})
    seen = {}

    class _P:
        returncode = 0
        stdout = _stream([_tool()])
        stderr = ""
    monkeypatch.setattr(sr.subprocess, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd) and _P())
    sr.run_scenario_raw("hi", ["Read"], 10)
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "sonnet"
    assert seen["cmd"][seen["cmd"].index("--tools") + 1] == "Read"   # available set, not just auto-approve
    assert "--system-prompt-file" not in seen["cmd"]
    with pytest.raises(sr.ScenarioInfraError):
        sr.run_scenario_raw("hi", ["Read"], 10, system="other")
