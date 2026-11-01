"""Tests for core/pipeline_expert_agent.py: the pipeline-expert sub-agent type and its gates.

- the agent definition: read-only tools, effort medium, maxTurns backstop, in the
  session's ClaudeAgentOptions
- expert_type_gate: an EXPERT-ID spawn not typed pipeline-expert is denied (main thread
  only); a correct spawn, a non-expert spawn, a spawn inside a sub-agent, FORCE pass
- expert_budget_gate: per agent_id, calls up to EXPERT_TOOL_CAP pass and every later one
  is denied; other agent types and the main thread are never counted; bounded state
- both gates are registered by build_hooks
"""
import asyncio

import pytest

from core import pipeline_expert_agent as pea

EXPERT_PROMPT = "EXPERT-ID: correctness\nAdversarially review this changeset: read /x/correctness.md first; follow it."


def _run(gate, data):
    return asyncio.run(gate(data, "tu", None))


def _spawn(subagent_type, prompt=EXPERT_PROMPT, **extra):
    return {"tool_name": "Agent", "tool_input": {"prompt": prompt, "subagent_type": subagent_type,
                                                  "description": "d"}, **extra}


def _denied(res):
    return (res.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


def test_definition_is_read_only_medium_effort_with_a_turn_backstop():
    d = pea.agent_definitions()[pea.PIPELINE_EXPERT]
    assert d.tools == ["Read", "Grep", "Glob", "Bash"]
    assert d.effort == "medium" and d.model == "inherit" and d.maxTurns == pea.EXPERT_MAX_TURNS
    assert str(pea.EXPERT_TOOL_CAP) in d.prompt and "UNCHECKED" in d.prompt


def test_type_gate_denies_an_expert_spawned_as_general_purpose():
    g = pea.create_expert_type_gate()
    res = _run(g, _spawn("general-purpose"))
    assert _denied(res)
    reason = res["hookSpecificOutput"]["permissionDecisionReason"]
    assert 'subagent_type: "pipeline-expert"' in reason and "correctness" in reason
    assert _denied(_run(g, _spawn(None)))           # no type at all = general-purpose


@pytest.mark.parametrize("data", [
    _spawn("pipeline-expert"),
    _spawn("Explore", prompt="Find where the config is loaded"),   # not an expert
    _spawn("general-purpose", agent_id="a1"),                      # inside a sub-agent
    {"tool_name": "Bash", "tool_input": {"command": "ls"}},
])
def test_type_gate_passes_everything_else(data):
    assert _run(pea.create_expert_type_gate(), data) == {}


def test_type_gate_force(monkeypatch):
    monkeypatch.setenv("SWARM_GATE_FORCE", "expert_gate")
    assert _run(pea.create_expert_type_gate(), _spawn("general-purpose")) == {}


def test_type_gate_is_off_when_the_agent_cannot_be_defined(monkeypatch):
    def boom():
        raise ImportError("no sdk")
    monkeypatch.setattr(pea, "agent_definitions", boom)
    assert _run(pea.create_expert_type_gate(), _spawn("general-purpose")) == {}


def _call(agent_id="a1", agent_type=pea.PIPELINE_EXPERT, tool="Read"):
    d = {"tool_name": tool, "tool_input": {}}
    if agent_id:
        d["agent_id"] = agent_id
    if agent_type:
        d["agent_type"] = agent_type
    return d


def test_budget_gate_denies_every_call_past_the_cap():
    g = pea.create_expert_budget_gate()
    for _ in range(pea.EXPERT_TOOL_CAP):
        assert _run(g, _call()) == {}
    res = _run(g, _call(tool="Bash"))
    assert _denied(res) and "write your report now" in res["hookSpecificOutput"]["permissionDecisionReason"]
    assert _denied(_run(g, _call()))
    # another expert has its own count
    assert _run(g, _call(agent_id="a2")) == {}


@pytest.mark.parametrize("data", [_call(agent_type="general-purpose"), _call(agent_type="Explore"),
                                  _call(agent_id=None, agent_type=None)])
def test_budget_gate_counts_only_pipeline_experts(data):
    g = pea.create_expert_budget_gate()
    for _ in range(pea.EXPERT_TOOL_CAP + 5):
        assert _run(g, data) == {}


def test_budget_gate_force_and_bounded_state(monkeypatch):
    g = pea.create_expert_budget_gate()
    for i in range(pea._MAX_TRACKED + 50):
        _run(g, _call(agent_id=f"x{i}"))
    from collections import OrderedDict
    counts = next(c.cell_contents for c in g.__closure__
                  if isinstance(c.cell_contents, OrderedDict))
    assert len(counts) == pea._MAX_TRACKED
    for _ in range(pea.EXPERT_TOOL_CAP):
        _run(g, _call(agent_id="f"))
    monkeypatch.setenv("SWARM_GATE_FORCE", "expert_gate")
    assert _run(g, _call(agent_id="f")) == {}


def test_build_hooks_registers_both_gates(monkeypatch):
    from core import hook_builder
    from core.permission_manager import permission_manager
    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        recorded.append((event, name, matcher))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="test-sess", session_context={"sdk_session_id": "test-sess"},
        permission_manager=permission_manager))
    assert ("PreToolUse", "expert_type_gate", "Agent|Task") in recorded
    assert ("PreToolUse", "expert_budget_gate", "Read|Grep|Glob|Bash") in recorded


def test_session_options_carry_the_agent():
    from core.prompt_builder import _pipeline_expert_agents
    assert list(_pipeline_expert_agents()) == [pea.PIPELINE_EXPERT]


# --- the cap grows with a long pipeline PROMPT-FILE (design 2026-11-01 friction D1) ---

def _blob(tmp_path, lines, width=15):
    d = tmp_path / "state" / "pipeline_agent_audit" / "runs" / "run_x" / "blobs"
    d.mkdir(parents=True)
    p = d / ("a" * 64)
    p.write_text(("x" * width + "\n") * lines)
    return str(p)


def _read(path, agent_id="a1"):
    return {"tool_name": "Read", "tool_input": {"file_path": path}, "agent_id": agent_id,
            "agent_type": pea.PIPELINE_EXPERT}


def test_expert_cap_adds_one_call_per_read_page(tmp_path):
    assert pea.expert_cap(_blob(tmp_path, 4000)) == pea.EXPERT_TOOL_CAP + 2


def test_expert_cap_counts_bytes_for_long_lines(tmp_path):
    # 1000 lines of 200 chars = 201 KB: one page by lines, three by Read's output limit
    assert pea.expert_cap(_blob(tmp_path, 1000, width=200)) == pea.EXPERT_TOOL_CAP + 3


def test_expert_cap_is_bounded(tmp_path):
    assert pea.expert_cap(_blob(tmp_path, 200_000)) == pea.EXPERT_TOOL_CAP_MAX


def test_expert_cap_ignores_files_that_are_not_prompt_blobs(tmp_path):
    other = tmp_path / "big.md"
    other.write_text("x\n" * 50_000)
    assert pea.expert_cap(str(other)) == pea.EXPERT_TOOL_CAP
    assert pea.expert_cap(str(tmp_path / "state/pipeline_agent_audit/runs/r/blobs/" / ("b" * 64))) \
        == pea.EXPERT_TOOL_CAP        # named like a blob, but missing


def test_budget_gate_raises_the_cap_on_the_first_prompt_blob_read(tmp_path):
    g = pea.create_expert_budget_gate()
    path = _blob(tmp_path, 4000)
    cap = pea.EXPERT_TOOL_CAP + 2
    assert _run(g, _read(path)) == {}
    for _ in range(cap - 1):
        assert _run(g, _call()) == {}
    res = _run(g, _call())
    assert _denied(res)
    reason = res["hookSpecificOutput"]["permissionDecisionReason"]
    assert f"({cap} calls)" in reason and "VERDICT" not in reason.upper()


def test_budget_gate_keeps_the_base_cap_without_a_prompt_blob(tmp_path):
    g = pea.create_expert_budget_gate()
    other = tmp_path / "x.py"
    other.write_text("x\n" * 50_000)
    assert _run(g, _read(str(other))) == {}
    for _ in range(pea.EXPERT_TOOL_CAP - 1):
        assert _run(g, _call()) == {}
    assert _denied(_run(g, _call()))


def test_budget_gate_sets_the_cap_only_once(tmp_path):
    g = pea.create_expert_budget_gate()
    small = str(tmp_path / "small")
    big = _blob(tmp_path, 100_000)
    small_dir = tmp_path / "s" / "pipeline_agent_audit" / "runs" / "r" / "blobs"
    small_dir.mkdir(parents=True)
    small = small_dir / ("c" * 64)
    small.write_text("x\n")
    assert _run(g, _read(str(small))) == {}       # cap = 25
    assert _run(g, _read(big)) == {}              # a later blob read does not raise it
    for _ in range(pea.EXPERT_TOOL_CAP + 1 - 2):
        assert _run(g, _call()) == {}
    assert _denied(_run(g, _call()))
