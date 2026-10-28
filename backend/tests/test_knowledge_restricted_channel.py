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


@pytest.mark.parametrize("cmd", ["ls -la", "grep -rn knowledge notes.txt", 'echo "addressing the issue"'])
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


def test_channel_session_gets_the_env_flag_and_the_handler(monkeypatch, tmp_path):
    """build_options for a non-owner channel: the subprocess env carries RESTRICTED_ENV
    and the handler denies the command; an owner channel gets neither."""
    import inspect
    from core import prompt_builder
    src = inspect.getsource(prompt_builder.PromptBuilder.build_options)
    assert "deny_knowledge=True" in src and "RESTRICTED_ENV: \"1\"" in src
    flag_at = src.index('RESTRICTED_ENV: "1"')
    guard = src.rfind("if _channel_sender_dir:", 0, flag_at)
    assert guard != -1 and flag_at - guard < 400
