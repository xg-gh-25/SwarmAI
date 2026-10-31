"""The knowledge command is the owner's: a non-owner channel session cannot use it
(knowledge addressing §2.4, §6.3 test 11).

It reads every project's DDD and the Library, and names no file, so the Bash path
check alone never saw it. Two guards: the CLI refuses when the daemon put
``RESTRICTED_ENV`` in the session's environment, and the daemon's Bash handler denies
the command. Both are usability guards; Bash in such a session is not a
confidentiality boundary (see knowledge_command_denial's docstring).
"""
import asyncio

import pytest

from core import knowledge_cli
from core.security_hooks import create_file_access_permission_handler, knowledge_command_denial


@pytest.mark.parametrize("cmd", [
    '"$SWARM_BACKEND_BIN" knowledge find --project Shared --query "quokka"',
    '"$SWARM_BACKEND_BIN" knowledge map --projects',
    '"$SWARM_BACKEND_BIN" "knowledge" find --project P --query x',
    '"$SWARM_BACKEND_BIN" knowledge "get" --project Secret abcd1234',
    '"$SWARM_BACKEND_BIN" knowle\'\'dge find --project P --query x',
    '"$SWARM_BACKEND_BIN" knowledge \\\nfind --project P',
    '"$SWARM_BACKEND_BIN" knowledge find --project Shared --proj Private --query x',
    '"$SWARM_BACKEND_BIN" knowledge find --in "library" --query x',
    'K=find; "$SWARM_BACKEND_BIN" knowledge $K --project Secret --query x',
    '/Users/x/.swarm-ai/runtime/2.3.0/python-backend knowledge map --project P',
    'python3 backend/desktop_main.py knowledge map --project P',
    '"$SWARM_BACKEND_BIN" py -m core.knowledge_cli find --project P --query x',
    '"$SWARM_BACKEND_BIN" py -c "from core import addressing; print(addressing.ddd_map(\'P\'))"',
    '"$SWARM_BACKEND_BIN" py -c "import core.addressing as a"',
])
def test_denied_in_any_spelling(cmd):
    assert knowledge_command_denial(cmd)


@pytest.mark.parametrize("cmd", ["ls -la", "grep -rn knowledge notes.txt", 'echo "addressing the issue"',
                                 "grep addressing notes.py", "cat knowledge.py"])
def test_other_commands_unaffected(cmd):
    assert knowledge_command_denial(cmd) is None


def test_handler_denies_in_a_channel_and_allows_for_the_owner(tmp_path):
    cmd = {"command": '"$SWARM_BACKEND_BIN" knowledge find --project P --query x'}
    channel = create_file_access_permission_handler([str(tmp_path)], [], deny_knowledge=True)
    res = asyncio.run(channel("Bash", cmd, {}))
    assert res["behavior"] == "deny" and "not available" in res["message"]
    owner = create_file_access_permission_handler([str(tmp_path)])
    assert asyncio.run(owner("Bash", cmd, {}))["behavior"] == "allow"


def test_cli_refuses_when_the_session_is_restricted(monkeypatch, capsys):
    monkeypatch.setenv(knowledge_cli.RESTRICTED_ENV, "1")
    assert knowledge_cli.main(["map", "--projects"]) == 3
    assert "not available in this session" in capsys.readouterr().out


def test_cli_rejects_abbreviated_options(monkeypatch):
    monkeypatch.delenv(knowledge_cli.RESTRICTED_ENV, raising=False)
    with pytest.raises(SystemExit):
        knowledge_cli.main(["find", "--proj", "P", "--query", "x"])


async def _options(channel_context, tmp_path, monkeypatch, *, enable_mcp=False, agent_config=None):
    from unittest.mock import MagicMock
    from core import initialization_manager as im
    from core.prompt_builder import PromptBuilder
    monkeypatch.setattr(im.initialization_manager, "get_cached_workspace_path",
                        lambda: str(tmp_path), raising=False)
    cfg = MagicMock()
    values = {"thinking_mode": "adaptive", "thinking_effort": "high"}
    cfg.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    cfg.get_model = MagicMock(return_value="claude-opus-5-5")
    agent_config = agent_config if agent_config is not None else {}
    agent_config.setdefault("model", "claude-opus-5-5")
    return await PromptBuilder(cfg).build_options(
        agent_config=agent_config, enable_skills=False, enable_mcp=enable_mcp,
        resume_session_id=None, session_context={"sdk_session_id": "sid"},
        channel_context=channel_context, editor_context=None, terminal_context=None,
        cached_system_prompt="CACHED")


@pytest.mark.asyncio
@pytest.mark.parametrize("tier,restricted", [("public", True), ("owner", False)])
async def test_build_options_restricts_only_a_non_owner_channel(tier, restricted, tmp_path, monkeypatch):
    ctx = {"channel_id": "c1", "channel_type": "slack",
           "sender_identity": {"permission_tier": tier, "external_id": "u1"}}
    opts = await _options(ctx, tmp_path, monkeypatch)
    env = opts.env or {}
    assert (env.get(knowledge_cli.RESTRICTED_ENV) == "1") is restricted
    cmd = {"command": '"$SWARM_BACKEND_BIN" knowledge map --projects'}
    if restricted:
        assert (await opts.can_use_tool("Bash", cmd, {}))["behavior"] == "deny"
    elif opts.can_use_tool is not None:
        assert (await opts.can_use_tool("Bash", cmd, {}))["behavior"] == "allow"
