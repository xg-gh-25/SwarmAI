"""A transient model-service error mid-turn continues the turn on the same CLI.

What is tested (real StreamingOrchestrator._read_formatted_response, real SDK
message classes, the orchestrator harness of test_tool_call_leak_guard):
- the model already worked (a tool call) when the CLI reported "API Error: 503 ...":
  its synthetic error line is not shown, a `reconnecting` event says why, the
  continuation prompt goes to the SAME client after the backoff, and the turn ends
  with the follow-up's real reply — no error event
- the budget: after _MAX_API_ERROR_CONTINUATIONS the error is surfaced as before
- a 503 before any content is left to the respawn retry (the in-process path is not
  taken); a non-transient error ends the turn as before
- the detector: 5xx / 429 / overloaded / "unable to process" match; a 400 does not
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from core.streaming_orchestrator import StreamingOrchestrator, _is_transient_api_error_text
from tests.test_tool_call_leak_guard import _drive, _make_orchestrator, _make_result_message

ERR = "API Error: 503 Bedrock is unable to process your request. This is a server-side issue, usually temporary"


def _tool_round():
    return [AssistantMessage(content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})], model="m"),
            UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)])]


def _api_error_round():
    return [AssistantMessage(content=[TextBlock(text=ERR)], model="<synthetic>"),
            ResultMessage(subtype="success", duration_ms=10, duration_api_ms=10, is_error=True, num_turns=1,
                          session_id="sdk-test", result=ERR, usage={"input_tokens": 1, "output_tokens": 0})]


def _reply(text="Done after the retry."):
    return [AssistantMessage(content=[TextBlock(text=text)], model="m"), _make_result_message()]


def _texts(events):
    return [b.get("text") for e in events if e.get("type") == "assistant" for b in e.get("content", []) if b.get("type") == "text"]


@pytest.fixture(autouse=True)
def _no_wait():
    # no real backoff, and no fire-and-forget token-usage writes to a real DB
    # (their aiosqlite threads outlive the test loop)
    import database
    with patch("core.streaming_orchestrator._api_error_wait", AsyncMock()) as s, \
            patch.object(database.db, "record_token_usage", AsyncMock()):
        yield s


@pytest.mark.asyncio
async def test_mid_turn_503_continues_in_process(_no_wait):
    orch = _make_orchestrator()
    orch._parent._content_emitted = True  # the model was already working
    events, raised = await _drive(orch, [*_tool_round(), *_api_error_round()], followups=[_reply()])
    assert raised is None
    assert orch._parent._client.queries == [StreamingOrchestrator._API_ERROR_CONTINUATION_PROMPT]
    assert _no_wait.await_args_list[0].args == (StreamingOrchestrator._API_ERROR_BACKOFF_S[0],)
    assert [e for e in events if e.get("type") == "reconnecting"][0]["reason"] == "api_error"
    assert not any(e.get("type") == "error" for e in events)
    assert ERR not in " ".join(t or "" for t in _texts(events))
    assert "Done after the retry." in _texts(events)
    assert events[-1].get("type") != "error"


@pytest.mark.asyncio
async def test_the_budget_ends_with_the_error_as_before():
    orch = _make_orchestrator()
    orch._parent._content_emitted = True
    n = StreamingOrchestrator._MAX_API_ERROR_CONTINUATIONS
    events, raised = await _drive(orch, [*_tool_round(), *_api_error_round()],
                                  followups=[_api_error_round() for _ in range(n)])
    assert len(orch._parent._client.queries) == n
    assert raised is None
    assert any(e.get("type") == "error" for e in events)


@pytest.mark.asyncio
async def test_a_503_before_any_content_is_not_continued_in_process():
    orch = _make_orchestrator()
    orch._parent._content_emitted = False
    events, raised = await _drive(orch, _api_error_round(), followups=[_reply()])
    assert orch._parent._client.queries == []


@pytest.mark.asyncio
async def test_a_user_stop_during_the_wait_is_respected(_no_wait):
    orch = _make_orchestrator()
    orch._parent._content_emitted = True

    async def _stop(*_a, **_k):
        orch._parent._user_stopped_current_turn = True
    _no_wait.side_effect = _stop
    events, raised = await _drive(orch, [*_tool_round(), *_api_error_round()], followups=[_reply()])
    assert orch._parent._client.queries == []


@pytest.mark.parametrize("text,hit", [
    (ERR, True), ("API Error: 529 overloaded_error", True), ("API Error: 429 Too many requests", True),
    ("API Error: 500 internal", True), ("API Error: 400 The provided model identifier is invalid", False),
    ("", False), (None, False),
])
def test_detector(text, hit):
    assert _is_transient_api_error_text(text) is hit
