"""Code SwarmAI writes is signed by SwarmAI, in every repo.

The CLI tells the agent which line to end a commit (and a PR) with, from its
``attribution`` setting; left unset it asks for its own Claude co-author line. A
repo-local hook that rewrote that line existed in one checkout on one machine only,
so every other repo, machine and Hive got the Claude line. ``build_options`` now
sends the setting with every session (the same ``--settings`` the CLI reads
``autoCompactWindow`` from).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


async def _settings(channel_context=None) -> dict:
    from core.prompt_builder import PromptBuilder
    cfg = MagicMock()
    values = {"thinking_mode": "adaptive", "thinking_effort": "high"}
    cfg.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    cfg.get_model = MagicMock(return_value="claude-opus-5-5")
    opts = await PromptBuilder(cfg).build_options(
        agent_config={"model": "claude-opus-5-5"},
        enable_skills=False, enable_mcp=False, resume_session_id=None,
        session_context={"sdk_session_id": "sid"}, channel_context=channel_context,
        editor_context=None, terminal_context=None, cached_system_prompt="CACHED")
    return json.loads(opts.settings)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_context", [None, {"channel_id": "c1", "channel_type": "slack"}])
async def test_every_session_signs_commits_and_prs_as_swarm(channel_context):
    attribution = (await _settings(channel_context))["attribution"]
    assert attribution["commit"] == "Co-Authored-By: Swarm <swarm@swarmai.dev>"
    assert "SwarmAI" in attribution["pr"]
    assert "Claude" not in json.dumps(attribution) and "anthropic" not in json.dumps(attribution)
