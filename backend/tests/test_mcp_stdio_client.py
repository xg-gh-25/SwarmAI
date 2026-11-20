"""core.mcp_stdio_client: server resolution by exact id, timeout, close, result parsing.

The server under test is a small Python script speaking MCP over stdio (written to
tmp_path and started with this interpreter), so the real subprocess transport runs.
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

from core.mcp_stdio_client import (
    McpStdioClient,
    McpTimeout,
    McpToolError,
    McpToolRefused,
    McpUnavailable,
    parse_tool_result,
    resolve_server,
    result_text,
    unwrap_untrusted,
)

FAKE_SERVER = textwrap.dedent(
    r'''
    import json, sys, time

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue  # notifications/initialized
        mid, method = msg["id"], msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2024-11-05", "capabilities": {}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": "echo"}, {"name": "hang"}]}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"]["arguments"]
            if name == "echo":
                send({"jsonrpc": "2.0", "id": mid, "result": {"structuredContent": {"args": args}, "content": []}})
            elif name == "hang":
                time.sleep(60)
            elif name == "big":
                text = "x" * 200000
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}})
            elif name == "noisy":
                sys.stdout.write("plain log line on stdout\n")
                send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
                send({"jsonrpc": "2.0", "id": mid + 1000, "result": {"content": [{"type": "text", "text": "wrong"}]}})
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "right"}]}})
            elif name == "fail":
                send({"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": "token expired"}]}})
            elif name == "hang_ignoring_term":
                import signal
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                time.sleep(60)
            elif name == "pid":
                import os
                send({"jsonrpc": "2.0", "id": mid, "result": {"structuredContent": {"pid": os.getpid()}}})
    '''
)


@pytest.fixture
def server_script(tmp_path):
    path = tmp_path / "fake_mcp.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


@pytest.fixture
def make_client(server_script):
    def make(**kwargs):
        return McpStdioClient(sys.executable, [str(server_script)], name="fake", call_timeout=10.0, **kwargs)
    return make


def _write_layers(ws, catalog, dev):
    mcps = ws / ".claude" / "mcps"
    mcps.mkdir(parents=True)
    (mcps / "mcp-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (mcps / "mcp-dev.json").write_text(json.dumps(dev), encoding="utf-8")


def _entry(eid, command, **extra):
    return {"id": eid, "name": eid, "connection_type": "stdio", "config": {"command": command, **extra.pop("config", {})}, **extra}


def test_resolves_server_by_exact_id_not_substring(tmp_path, monkeypatch):
    monkeypatch.setenv("INBOX_TEST_TOKEN_DIR", "/opt/tokens")
    _write_layers(
        tmp_path,
        catalog=[_entry("slack-mcp", "/catalog/slack", enabled=True), _entry("off-server", "/x", enabled=False)],
        dev=[
            _entry("my-slack-helper", "/dev/helper"),
            _entry("slack-mcp", "/dev/slack-mcp", config={"args": ["--dir", "$INBOX_TEST_TOKEN_DIR"], "env": {"A": "1"}},
                   rejected_tools=["post_message"]),
        ],
    )
    spec = resolve_server("slack-mcp", tmp_path)
    assert spec.command == "/dev/slack-mcp"  # dev overrides catalog by id
    assert spec.args == ("--dir", "/opt/tokens")  # $VAR expanded by mcp_config_loader
    assert spec.env == {"A": "1"}
    assert spec.rejected_tools == frozenset({"post_message"})
    for missing in ("slack", "mcp", "helper", "off-server"):
        with pytest.raises(McpUnavailable):
            resolve_server(missing, tmp_path)


def test_parse_structured_content_and_untrusted_text():
    assert parse_tool_result({"structuredContent": {"a": 1}, "content": [{"type": "text", "text": "ignored"}]}) == {"a": 1}
    wrapped = 'Data follows.\n<untrusted_content source="slack">\n{"messages": [{"ts": "1.2"}]}\n</untrusted_content>'
    assert parse_tool_result({"content": [{"type": "text", "text": wrapped}]}) == {"messages": [{"ts": "1.2"}]}
    assert unwrap_untrusted('<untrusted_content id="x">only open') == "only open"
    assert parse_tool_result({"content": [{"type": "text", "text": "plain words"}]}) == "plain words"
    two = {"content": [{"type": "text", "text": "[1]"}, {"type": "text", "text": "[2]"}]}
    assert parse_tool_result(two) == [[1], [2]]
    assert result_text({"content": [{"type": "text", "text": "<untrusted_content>hi</untrusted_content>"}]}) == "hi"
    assert parse_tool_result({"content": []}) is None


async def test_call_returns_structured_content_and_lists_tools(make_client):
    async with make_client() as client:
        result = await client.call_tool("echo", {"q": "hello"})
        assert parse_tool_result(result) == {"args": {"q": "hello"}}
        assert [t["name"] for t in await client.list_tools()] == ["echo", "hang"]


async def test_call_timeout_raises_and_kills_process(make_client):
    async with make_client() as client:
        first = parse_tool_result(await client.call_tool("pid", {}))["pid"]
        proc = client._process
        with pytest.raises(McpTimeout):
            await client.call_tool("hang", {}, timeout=0.5)
        assert not client.running
        assert proc.returncode is not None  # the hung server is gone, not left running
        second = parse_tool_result(await client.call_tool("pid", {}))["pid"]
        assert second != first  # the next call started a fresh server


async def test_close_terminates_and_client_restarts(make_client):
    async with make_client() as client:
        await client.call_tool("echo", {})
        proc = client._process
        await client.close()
        assert proc.returncode is not None
        assert not client.running
        assert parse_tool_result(await client.call_tool("echo", {"again": True})) == {"args": {"again": True}}


async def test_unrelated_lines_and_other_ids_are_not_taken_as_the_answer(make_client):
    async with make_client() as client:
        assert parse_tool_result(await client.call_tool("noisy", {})) == "right"


async def test_large_response_line_over_64k(make_client):
    async with make_client() as client:
        assert len(parse_tool_result(await client.call_tool("big", {}))) == 200000


async def test_tool_error_and_rejected_tool(server_script):
    c = McpStdioClient(sys.executable, [str(server_script)], rejected_tools=frozenset({"echo"}))
    try:
        with pytest.raises(McpToolError, match="token expired"):
            await c.call_tool("fail", {})
        raw = await c.call_tool_raw("fail", {})
        assert raw["isError"] is True
        with pytest.raises(McpToolRefused):
            await c.call_tool("echo", {})
    finally:
        await c.close()


async def test_missing_command_is_unavailable(tmp_path):
    c = McpStdioClient(str(tmp_path / "no-such-binary"))
    with pytest.raises(McpUnavailable):
        await c.call_tool("echo", {})


async def test_slack_bridge_uses_the_client_and_keeps_its_none_contract(server_script, monkeypatch):
    import channels.adapters.slack as slack_mod

    monkeypatch.setattr(
        slack_mod, "_find_slack_mcp_config",
        lambda: {"command": sys.executable, "args": [str(server_script)], "env": {}},
    )
    bridge = slack_mod.SlackMcpBridge()
    try:
        assert isinstance(bridge, McpStdioClient)
        assert (await bridge.call_tool("echo", {"x": 1}))["structuredContent"] == {"args": {"x": 1}}
        assert (await bridge.call_tool("fail", {}))["isError"] is True  # result passed through as before
        assert await bridge.call_tool("hang", {}, timeout=0.3) is None  # failures read as None
    finally:
        await bridge.close()


async def test_a_timed_out_call_stops_only_its_own_server(make_client):
    """A server that ignores SIGTERM keeps the timed-out call's stop busy for seconds;
    calls made meanwhile start a new server, and stopping the old one must not take the
    new one's reader with it."""
    async with make_client() as client:
        hung = asyncio.create_task(client.call_tool("hang_ignoring_term", {}, timeout=0.3))
        await asyncio.sleep(0.8)  # the timeout fired; its stop now waits out the SIGTERM grace
        during = parse_tool_result(await client.call_tool("pid", {}, timeout=5))["pid"]
        with pytest.raises(McpTimeout):
            await hung
        after = parse_tool_result(await client.call_tool("pid", {}, timeout=3))["pid"]
        assert after == during  # the new server is still read and reused
