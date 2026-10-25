"""The pipeline's review-expert sub-agent type and the two gates that keep it in scope.

Measured 2026-10-19 over 60 sub-agents: every expert ran as ``general-purpose`` on
opus with the session's ``thinking_effort: high``, and the prompts' SCOPE BUDGET
("at most 20 tool calls, under 600 words") was prose nothing enforced. The slow tail
(10-12 min) took 40-72 tool calls and wrote 50-74K output tokens for a 600-word
report; a parallel batch waits for its slowest member.

- ``PIPELINE_EXPERT`` / ``agent_definitions()`` — the dedicated ``pipeline-expert``
  agent: read-only tools (Read, Grep, Glob, Bash), ``effort`` medium, ``maxTurns``
  as a backstop. Registered on every session (prompt_builder ``agents=``).
- ``create_expert_type_gate`` — PreToolUse (Agent|Task) on the main thread: a spawn
  whose prompt starts with ``EXPERT-ID:`` must use ``subagent_type: pipeline-expert``;
  anything else is denied with the exact fix. Fail-open.
- ``create_expert_budget_gate`` — PreToolUse (Read|Grep|Glob|Bash) inside a
  ``pipeline-expert`` sub-agent: past ``EXPERT_TOOL_CAP`` tool calls every further
  call is denied and the expert is told to write its report now, marking what it
  did not reach UNCHECKED. Counts are per agent_id in the factory's closure (one per
  session), bounded. Fail-open.

Operator bypass: ``SWARM_GATE_FORCE=expert_gate`` (or one gate's name) in the backend env.

@exports PIPELINE_EXPERT, EXPERT_TOOL_CAP, EXPERT_TOOLS, agent_definitions,
         create_expert_type_gate, create_expert_budget_gate
"""
from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import Any

from core.gate_force import force_enabled

logger = logging.getLogger(__name__)

PIPELINE_EXPERT = "pipeline-expert"
EXPERT_TOOLS = ["Read", "Grep", "Glob", "Bash"]
# The largest SCOPE BUDGET a prompt hands out is 20 tool calls; 4 more for the
# report's last look. A backstop for the slow tail, not the budget itself.
EXPERT_TOOL_CAP = 24
EXPERT_EFFORT = "medium"
EXPERT_MAX_TURNS = 32
_MAX_TRACKED = 256

_SYSTEM_PROMPT = (
    "You are a SwarmAI review expert (a native review the agent asked for, or a pipeline "
    "run's review). Your first message names "
    "your prompt file on its PROMPT-FILE: line: read it from the top (page with offset "
    "if the output is cut short) and follow it exactly. You review only: never edit, write, "
    "move or delete files, never commit, never run a pipeline-v1 command. "
    "Keep to the SCOPE BUDGET in that file. After "
    f"{EXPERT_TOOL_CAP} tool calls your tools are refused: write your report then, with "
    "every item you did not reach marked UNCHECKED."
)


def agent_definitions() -> dict[str, Any]:
    """``{name: AgentDefinition}`` for ClaudeAgentOptions(agents=...)."""
    from claude_agent_sdk.types import AgentDefinition
    return {PIPELINE_EXPERT: AgentDefinition(
        description=("Pipeline v1 review expert (correctness, security; the "
                     "Gate-0 decision reviewer). Use for every spawn whose prompt starts "
                     "with EXPERT-ID."),
        prompt=_SYSTEM_PROMPT,
        tools=list(EXPERT_TOOLS),
        model="inherit",
        effort=EXPERT_EFFORT,
        maxTurns=EXPERT_MAX_TURNS,
    )}


def _forced(gate: str = "expert_gate", input_data: Any = None) -> bool:
    return force_enabled(gate, input_data)


def _deny(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def create_expert_type_gate():
    """PreToolUse (Agent|Task), main thread: an EXPERT-ID spawn must be a pipeline-expert."""
    from core.runtime_hooks import _extract_expert_id
    try:
        agent_definitions()  # the same call prompt_builder makes: no type, no gate
    except Exception:  # noqa: BLE001
        logger.warning("pipeline-expert agent unavailable: expert_type_gate off", exc_info=True)

        async def _off(input_data: dict[str, Any], tool_use_id: "str | None",
                       context: Any) -> dict[str, Any]:
            return {}
        return _off

    async def _gate(input_data: dict[str, Any], tool_use_id: "str | None",
                    context: Any) -> dict[str, Any]:
        try:
            if input_data.get("tool_name") not in ("Agent", "Task") or input_data.get("agent_id"):
                return {}
            ti = input_data.get("tool_input") or {}
            expert_id = _extract_expert_id(str(ti.get("prompt") or "")[:2000])
            if not expert_id or str(ti.get("subagent_type") or "") == PIPELINE_EXPERT:
                return {}
            if _forced("expert_type_gate", input_data):
                logger.warning("[expert-type-gate] FORCE: %s spawned as %r", expert_id,
                               ti.get("subagent_type"))
                return {}
            logger.warning("[BLOCKED] expert_type_gate: %s spawned as %r", expert_id,
                           ti.get("subagent_type"))
            return _deny(
                f"Spawn {expert_id} with subagent_type: \"{PIPELINE_EXPERT}\" — same prompt, "
                "same description, run_in_background false. That agent type is the review "
                f"expert: read-only tools, effort {EXPERT_EFFORT}, {EXPERT_TOOL_CAP}-call cap. "
                "Re-issue every expert of this batch that way, in one message.")
        except Exception:  # noqa: BLE001 — a gate never blocks on itself
            logger.warning("expert_type_gate failed — failing open", exc_info=True)
            return {}

    return _gate


def create_expert_budget_gate():
    """PreToolUse (Read|Grep|Glob|Bash) inside a pipeline-expert: the tool-call cap."""
    counts: "OrderedDict[str, int]" = OrderedDict()

    async def _gate(input_data: dict[str, Any], tool_use_id: "str | None",
                    context: Any) -> dict[str, Any]:
        try:
            agent_id = str(input_data.get("agent_id") or "")
            if not agent_id or input_data.get("agent_type") != PIPELINE_EXPERT:
                return {}
            n = counts.pop(agent_id, 0) + 1
            counts[agent_id] = n
            while len(counts) > _MAX_TRACKED:
                counts.popitem(last=False)
            if n <= EXPERT_TOOL_CAP or _forced("expert_budget_gate", input_data):
                return {}
            if n == EXPERT_TOOL_CAP + 1:
                logger.warning("[BLOCKED] expert_budget_gate: agent %s past %d tool calls",
                               agent_id, EXPERT_TOOL_CAP)
            return _deny(
                f"Tool budget used ({EXPERT_TOOL_CAP} calls). Make no more tool calls: write "
                "your report now from what you have read. Mark every checklist item you did "
                "not reach `UNCHECKED: <item> — budget exhausted` and your verdict PARTIAL.")
        except Exception:  # noqa: BLE001
            logger.warning("expert_budget_gate failed — failing open", exc_info=True)
            return {}

    return _gate
