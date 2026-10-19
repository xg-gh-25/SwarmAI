"""The display object every tool_use carries (chat turn spec v6 §7.1).

What is tested: ``core.tool_summarizer.tool_display`` and the tool_use dict the
streaming orchestrator builds.

Properties pinned:
- the tool is its own name (an MCP tool loses its ``mcp__<server>__`` prefix);
- the object is the command / path / pattern / url / query / skill / Agent task, not a
  prose summary, redacted by ``redact_display`` and at most 2000 characters;
- the merge key is equal for calls shown identically and differs otherwise; it is
  computed from the REDACTED object, so it can never confirm a guess of a masked secret;
- arguments of a tool with no known object field are redacted structurally: a
  secret-named value is masked whole at any depth (string, list, nested dict), JSON
  escaping cannot leak its tail, and any-scheme URL credentials and
  ``--password value`` flags are masked;
- it never raises on odd input;
- the orchestrator's tool_use dict carries ``display`` on both of its paths (with and
  without the summarizer), and the persisted copy is the same dict.
"""
from __future__ import annotations

import json

import pytest

from core.tool_summarizer import DISPLAY_MAX, tool_display


def test_bash_object_is_the_command():
    d = tool_display("Bash", {"command": "git status --short"})
    assert d["tool"] == "Bash"
    assert d["object"] == "git status --short"


def test_read_edit_write_object_is_the_path():
    for name in ("Read", "Edit", "Write"):
        assert tool_display(name, {"file_path": "/a/b/c.ts"})["object"] == "/a/b/c.ts"


def test_search_url_query_skill_agent():
    assert tool_display("Grep", {"pattern": "foo.*bar", "path": "src"})["object"] == "foo.*bar"
    assert tool_display("Glob", {"pattern": "**/*.ts"})["object"] == "**/*.ts"
    assert tool_display("WebFetch", {"url": "https://example.com/x"})["object"] == "https://example.com/x"
    assert tool_display("WebSearch", {"query": "sdk stop_task"})["object"] == "sdk stop_task"
    assert tool_display("ToolSearch", {"query": "select:Read,Bash"})["object"] == "select:Read,Bash"
    assert tool_display("Skill", {"skill": "s_autonomous-pipeline"})["object"] == "s_autonomous-pipeline"
    assert tool_display("Agent", {"description": "Gate 1 plan skeptic", "prompt": "long"})["object"] == "Gate 1 plan skeptic"


def test_mcp_prefix_dropped():
    d = tool_display("mcp__swarm_ui__ui_action", {"action": "open"})
    assert d["tool"] == "ui_action"


def test_unknown_tool_object_is_compact_args():
    d = tool_display("Frobnicate", {"b": 2, "a": "x"})
    assert d["tool"] == "Frobnicate"
    assert d["object"] == '{"a": "x", "b": 2}'


def test_object_is_redacted():
    d = tool_display("Bash", {"command": "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY && aws s3 ls"})
    assert "wJalrXUtnFEMI" not in d["object"]
    assert "aws s3 ls" in d["object"]


def test_object_is_capped():
    d = tool_display("Bash", {"command": "echo " + "x" * 5000})
    assert len(d["object"]) <= DISPLAY_MAX == 2000


def test_key_equal_for_calls_shown_identically():
    a = tool_display("Bash", {"command": "make test", "timeout": 60})
    b = tool_display("Bash", {"timeout": 61, "command": "make test"})
    c = tool_display("Bash", {"command": "make lint"})
    assert a["key"] == b["key"] != c["key"]
    assert len(a["key"]) == 16 and "make" not in a["key"]
    assert tool_display("Read", {"file_path": "/x"})["key"] != tool_display("Write", {"file_path": "/x"})["key"]


def test_key_cannot_confirm_a_masked_secret():
    # two inputs that differ only in the masked value get the same key: the key holds
    # nothing beyond what the redacted object already shows
    a = tool_display("Bash", {"command": "export DB_PASSWORD=482913 && ./deploy"})
    b = tool_display("Bash", {"command": "export DB_PASSWORD=111111 && ./deploy"})
    assert "482913" not in a["object"]
    assert a["object"] == b["object"] and a["key"] == b["key"]


@pytest.mark.parametrize("args, secret", [
    ({"client_secret": "zzzSECRETzzz"}, "zzzSECRETzzz"),
    ({"password": " leadingSECRET"}, "leadingSECRET"),
    ({"pwd": "pwdSECRET"}, "pwdSECRET"),
    ({"password": 'ab"cdefSECRETTAIL'}, "SECRETTAIL"),
    ({"token": ["listSECRET1", "listSECRET2"]}, "listSECRET"),
    ({"token": {"value": "nestedSECRET"}}, "nestedSECRET"),
    ({"headers": {"Authorization": "Basic dXNlcjpwYXNzd29yZA=="}}, "dXNlcjpwYXNzd29yZA"),
    ({"conn": "postgres://admin:S3cretPw@db/x"}, "S3cretPw"),
    ({"auth": {"user": "u", "pass": "deepSECRET"}}, "deepSECRET"),
    ({"credentials": "credSECRET"}, "credSECRET"),
    ({"cookie": "sid=cookieSECRET"}, "cookieSECRET"),
])
def test_mcp_args_redacted_structurally(args, secret):
    d = tool_display("mcp__db__run_query", {"sql": "select 1", **args})
    assert secret not in d["object"], d["object"]
    assert "select 1" in d["object"]


@pytest.mark.parametrize("cmd, secret", [
    ("DB_PASS=hunter2 ./run", "hunter2"),
    ("SECRET=topSECRETvalue ./run", "topSECRETvalue"),
    ("mysql --password hunter2pw -u root", "hunter2pw"),
    ("tool --api-key abcSECRET123 run", "abcSECRET123"),
    ("psql postgres://admin:S3cretPw@db:5432/x", "S3cretPw"),
])
def test_bash_secret_shapes_redacted(cmd, secret):
    d = tool_display("Bash", {"command": cmd})
    assert secret not in d["object"], d["object"]


def test_never_raises():
    for inp in (None, {}, {"command": None}, {"file_path": 3}, {"x": object()}):
        d = tool_display("Bash", inp)
        assert set(d) == {"tool", "object", "key"}
    assert tool_display("", None)["tool"] == ""
    assert set(tool_display(None, {"a": 1})) == {"tool", "object", "key"}  # type: ignore[arg-type]
    assert set(tool_display(7, {"a": 1})) == {"tool", "object", "key"}  # type: ignore[arg-type]
    deep: dict = {}
    cur = deep
    for _ in range(50):
        cur["n"] = {}
        cur = cur["n"]
    assert set(tool_display("Frob", deep)) == {"tool", "object", "key"}


async def _stream(msgs) -> list[dict]:
    """The real orchestrator loop over scripted SDK messages (test_subagent_stream style)."""
    import asyncio
    import time
    from unittest.mock import MagicMock, patch

    from core import permission_manager as _pm
    from core.session_unit import SessionState, SessionUnit
    from core.streaming_orchestrator import StreamingOrchestrator

    class _Script:
        def __init__(self, m):
            self._m = list(m)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._m:
                return self._m.pop(0)
            raise RuntimeError("script exhausted")

    unit = SessionUnit(session_id="tool-display", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._streaming_start_time = unit._last_event_time = time.time()
    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    unit._client.receive_response = lambda: _Script(msgs)
    events: list[dict] = []
    with patch.object(_pm.permission_manager, "get_session_queue", return_value=asyncio.Queue()):
        with pytest.raises(Exception):
            async for ev in orch._read_formatted_response():
                events.append(ev)
    return events


def _tool_uses(events):
    return [b for e in events if e["type"] == "assistant" for b in e["content"] if b.get("type") == "tool_use"]


@pytest.mark.asyncio
async def test_orchestrator_tool_use_carries_display():
    from claude_agent_sdk import AssistantMessage, SystemMessage, ToolUseBlock

    events = await _stream([
        SystemMessage(subtype="init", data={"session_id": "s"}),
        AssistantMessage(content=[ToolUseBlock(id="b1", name="Bash", input={"command": "git log -3"})], model="m"),
    ])
    tu = _tool_uses(events)
    assert tu and tu[0]["display"] == tool_display("Bash", {"command": "git log -3"})


@pytest.mark.asyncio
async def test_orchestrator_without_summarizer_still_sets_display():
    """The no-summarizer branch has nothing to redact with, so it shows no object."""
    import sys
    from unittest.mock import patch

    from claude_agent_sdk import AssistantMessage, SystemMessage, ToolUseBlock

    with patch.dict(sys.modules, {"core.tool_summarizer": None}):
        events = await _stream([
            SystemMessage(subtype="init", data={"session_id": "s"}),
            AssistantMessage(content=[ToolUseBlock(id="b1", name="mcp__srv__do_it", input={"x": "secret"})], model="m"),
        ])
    tu = _tool_uses(events)
    assert tu and tu[0]["display"] == {"tool": "do_it", "object": "", "key": ""}


def test_display_survives_persist_cleaning():
    from core.session_router import _clean_blocks_for_persist

    block = {"type": "tool_use", "id": "t1", "name": "Bash", "summary": "Running: ls",
             "category": "bash", "display": tool_display("Bash", {"command": "ls"})}
    out = _clean_blocks_for_persist([block])
    assert json.loads(json.dumps(out))[0]["display"] == block["display"]
