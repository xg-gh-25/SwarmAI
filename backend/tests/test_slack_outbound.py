"""Slack bot outbound messages: the Web API with the bot token is the only path.

When the Web API is unreachable (a corp proxy) or the bot token is refused, the message
is not sent: the calls return ``None`` (an update is dropped) and a refused token is
reported to the gateway. The bot never falls back to the person's own Slack MCP, which
would post as the person.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def _adapter():
    from channels.adapters.slack import SlackChannelAdapter

    adapter = SlackChannelAdapter(channel_id="test-channel",
                                  config={"bot_token": "xoxb-test", "app_token": "xapp-test"},  # pragma: allowlist secret
                                  on_message=AsyncMock())
    adapter._slack_client = MagicMock()
    adapter._loop = asyncio.get_running_loop()
    adapter._report_auth_failure = AsyncMock()
    return adapter


def _message(text="Hello"):
    from channels.base import OutboundMessage

    return OutboundMessage(channel_id="test-channel", external_chat_id="C123", external_thread_id=None, text=text)


class _AuthError(Exception):
    pass


def test_the_bot_adapter_has_no_path_to_an_mcp_server():
    tree = ast.parse((BACKEND / "channels" / "adapters" / "slack.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "mcp" not in (node.module or ""), f"the bot adapter imports {node.module}"
        if isinstance(node, ast.Import):
            assert not any("mcp" in a.name for a in node.names)


@pytest.mark.asyncio
async def test_web_api_is_the_primary_path():
    adapter = _adapter()
    adapter._slack_client.chat_postMessage.return_value = {"ts": "primary.ts"}
    assert await adapter.send_message(_message()) == "primary.ts"
    assert await adapter.send_typing_indicator("C123") == "primary.ts"
    await adapter.update_message("C123", "ts", "text", is_final=True)
    adapter._slack_client.chat_update.assert_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionError("403 blocked-by-allowlist"), OSError("[Errno 61] refused")])
async def test_an_unreachable_web_api_sends_nothing(error):
    adapter = _adapter()
    adapter._slack_client.chat_postMessage.side_effect = error
    adapter._slack_client.chat_update.side_effect = error
    assert await adapter.send_message(_message()) is None
    assert await adapter.send_typing_indicator("C123") is None
    await adapter.update_message("C123", "ts", "text", is_final=True)
    adapter._report_auth_failure.assert_not_called()


@pytest.mark.asyncio
async def test_a_refused_token_is_reported_and_sends_nothing(monkeypatch):
    import channels.adapters.slack as slack_mod

    monkeypatch.setattr(slack_mod, "_is_auth_error", lambda exc: isinstance(exc, _AuthError))
    adapter = _adapter()
    adapter._slack_client.chat_postMessage.side_effect = _AuthError("invalid_auth")
    adapter._slack_client.chat_update.side_effect = _AuthError("invalid_auth")
    assert await adapter.send_message(_message()) is None
    assert await adapter.send_typing_indicator("C123") is None
    await adapter.update_message("C123", "ts", "text", is_final=True)
    assert adapter._report_auth_failure.await_count == 3
