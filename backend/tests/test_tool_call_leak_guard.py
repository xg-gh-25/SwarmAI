"""Tests for tool-call XML leak detection + orchestrator guard.

What is tested
--------------
1. ``detect_tool_call_leak`` (pure fn) — the double-condition matcher that
   distinguishes a real leak (``call`` line + line-start ``<invoke name=``) from
   prose that merely discusses the syntax inline. Table-driven, including the
   exact shapes observed in the messages DB on 2026-06-24.
2. The StreamingOrchestrator TextBlock guard — a leaked text block is NOT
   appended/yielded as assistant content, ``kill()`` is awaited, and a
   RuntimeError matching a retriable pattern is raised (→ send() --resume retry).

Key properties / invariants
----------------------------
- INV1: every DB-confirmed leak shape (call+invoke) → True
- INV2: inline-discussion + fenced-code-block mention → False (false-positive
  safety on the hottest path — every assistant text block)
- INV3: a detected leak is retriable (``_is_retriable_error`` recognizes the
  raised message) so the existing retry loop resumes the turn
- INV4: the guard fires INSIDE the TextBlock branch, before persist — the
  leaked block never reaches a yielded ``assistant`` event
"""

from __future__ import annotations

import asyncio
import types

import pytest

from core.session_utils import detect_tool_call_leak, _is_retriable_error


# ── 1. Pure detector — table-driven ────────────────────────────────────────

# Real leak shapes (DB-confirmed 2026-06-24, rowids 203549/204014/204018 etc.)
LEAK_CASES = [
    # Classic harness shape with a "call" line then line-start <invoke
    'some text\n\ncall\n<invoke name="Bash">\n<parameter name="command">cd /x</parameter>',
    # Skill invocation variant
    'output:\n\ncall\n<invoke name="Skill">\n<parameter name="skill">foo</parameter>',
    # Leak at the very start of the block
    'call\n<invoke name="Bash">\n<parameter name="command">ls</parameter>',
    # Extra inter-line whitespace / trailing spaces on the call line
    'prefix\ncall  \n   <invoke name="Read">',
    # Tabs around the call token
    'x\n\tcall\t\n<invoke name="Edit">',
]

# Non-leak: prose discussing the syntax (must NOT trigger — false-positive safe)
NON_LEAK_CASES = [
    # Inline backtick mention (the rowid 204050 shape that must be rejected)
    '看两张截图,我的回复每次都在 `<invoke name="Bash">` 那一刻停了',
    # Inline mention mid-sentence, no leading bare "call" line
    'The harness emits <invoke name="Bash"> as the tool-call format.',
    # Fenced code block discussing the syntax — preceded by ``` not a call line
    'Example:\n```\n<invoke name="Bash">\n<parameter name="command">x</parameter>\n```',
    # The word "call" appears but not as its own line before a line-start tag
    'we call the <invoke name= matcher here inline',
    # A "call" line but the next line is NOT an <invoke tag
    'call\nthe function and see what happens',
    # Empty / trivial
    '',
    'just a normal assistant reply with no tool syntax at all',
]


@pytest.mark.parametrize("text", LEAK_CASES)
def test_detector_flags_real_leaks(text):
    """INV1: every DB-confirmed leak shape is detected."""
    assert detect_tool_call_leak(text) is True


@pytest.mark.parametrize("text", NON_LEAK_CASES)
def test_detector_ignores_discussion(text):
    """INV2: prose mentioning the syntax inline / in fences is NOT flagged."""
    assert detect_tool_call_leak(text) is False


def test_detector_scan_limit_does_not_crash_on_large_text():
    """A huge block with the leak near the front is still detected; a huge
    block with no leak returns False quickly (bounded scan)."""
    big_leak = "x" * 100 + "\ncall\n<invoke name=\"Bash\">" + "y" * 100_000
    assert detect_tool_call_leak(big_leak) is True
    big_clean = "z" * 500_000
    assert detect_tool_call_leak(big_clean) is False


def test_detected_leak_is_retriable():
    """INV3: the RuntimeError message the guard raises is recognized as
    retriable, so send() routes it to the --resume retry path."""
    assert _is_retriable_error("Tool-call XML leaked into text channel (session_id=x)") is True


# ── 2. Orchestrator guard (integration) ─────────────────────────────────────


def _make_assistant_message(text, model="test-model"):
    """Build a REAL claude_agent_sdk.AssistantMessage so the orchestrator's
    isinstance(block, TextBlock) / isinstance(message, AssistantMessage) checks
    match — a fake duck-typed object would be silently ignored and the test
    would pass for the wrong reason (the zombie detector, not the leak guard)."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    return AssistantMessage(content=[TextBlock(text=text)], model=model)


def _make_result_message():
    """A clean end-of-turn ResultMessage so the normal (non-zombie) result path
    runs after a clean text block."""
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=10,
        is_error=False,
        num_turns=1,
        session_id="sdk-test",
        stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
        total_cost_usd=0.0,
    )


async def _drive(orchestrator, messages):
    """Run _read_formatted_response against a stream of messages and collect
    yielded events. Returns (events, raised_exc)."""

    async def _stream():
        for m in messages:
            yield m

    class _Client:
        def receive_response(self):
            return _stream()

    orchestrator._parent._client = _Client()

    events = []
    raised = None
    try:
        async for ev in orchestrator._read_formatted_response():
            events.append(ev)
    except RuntimeError as e:
        raised = e
    return events, raised


def _make_orchestrator():
    """Build a StreamingOrchestrator with a minimal stubbed parent SessionUnit."""
    from core.streaming_orchestrator import StreamingOrchestrator

    from core.session_unit import SessionState

    parent = types.SimpleNamespace()
    parent.session_id = "sess-test"
    parent._client = None
    parent._sdk_session_id = "sdk-test"  # is_resume=True path
    # State machine stubs — the no-ResultMessage stream-end path reads .state
    # and calls _transition()/last_used (clean-text test exercises this).
    parent.state = SessionState.STREAMING
    parent.last_used = 0.0

    def _transition(new_state):
        parent.state = new_state

    parent._transition = _transition
    parent._content_emitted = False
    parent._streaming_start_time = None
    parent._interrupted = False
    parent._active_agent_tools = {}
    parent._open_tool_uses = {}
    parent._pending_file_changes = {}
    parent._tool_hang_interrupted = False
    parent._tool_hang_interrupt_at = None
    parent._tool_hang_episodes = 0
    parent._model_name = "test-model"
    parent._configured_mcps = []
    parent._mcp_health_checked = True
    parent._lifecycle_response_count = 0
    parent._retry_count = 0
    parent.pid = None
    parent._peak_tree_rss_bytes = 0
    parent._interrupted = False
    # Normal result-path stubs (clean-text test reaches ResultMessage handling)
    parent._compaction_guard = types.SimpleNamespace(
        record_tool_call=lambda *a, **k: None,
    )
    parent._emit_post_stream_metadata = lambda *a, **k: iter(())

    async def _noop_async(*a, **k):
        return None

    parent._check_rss_and_proactive_restart = _noop_async
    parent._check_context_soft_compact = _noop_async

    # Health sensor stub
    parent._health_sensor = types.SimpleNamespace(
        record_activity=lambda: None,
        record_turn=lambda **k: None,
    )
    parent._last_event_time = 0.0
    parent._maybe_build_elapsed_heartbeat = lambda: None
    parent._compute_message_timeout = lambda: 300.0

    # kill() must be awaitable + record invocation
    kill_calls = {"n": 0}

    async def _kill():
        kill_calls["n"] += 1

    parent.kill = _kill
    parent._kill_calls = kill_calls

    orch = StreamingOrchestrator(parent)
    return orch


def test_orchestrator_blocks_leaked_block_and_kills():
    """INV4: a leaked TextBlock is not yielded as assistant content; kill() is
    awaited and a retriable RuntimeError is raised."""
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    leak = 'analysis text\n\ncall\n<invoke name="Bash">\n<parameter name="command">cd /x</parameter>'
    msg = _make_assistant_message(leak)

    events, raised = asyncio.run(_drive(orch, [msg]))

    # kill was invoked
    assert orch._parent._kill_calls["n"] == 1
    # a retriable RuntimeError was raised — and specifically the LEAK guard's
    # error, not the unrelated zombie detector (proves the right path fired)
    assert raised is not None
    assert "Tool-call XML leaked into text channel" in str(raised)
    assert _is_retriable_error(str(raised)) is True
    # the leaked XML never reached a yielded assistant content event
    for ev in events:
        if ev.get("type") == "assistant":
            for block in ev.get("content", []):
                assert "<invoke name=" not in block.get("text", "")


def test_orchestrator_passes_clean_text_block():
    """A normal text block (even one mentioning <invoke name= inline) is
    yielded as assistant content and does NOT kill the session."""
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    clean = "The harness emits <invoke name=\"Bash\"> as the tool-call format inline."
    msg = _make_assistant_message(clean)

    # Include a trailing ResultMessage so the turn ends via the normal result
    # path (not the zombie detector, which is unrelated to the leak guard).
    events, raised = asyncio.run(_drive(orch, [msg, _make_result_message()]))

    assert orch._parent._kill_calls["n"] == 0
    assert raised is None
    # the clean text was yielded as assistant content
    assert any(
        ev.get("type") == "assistant"
        and any(b.get("text") == clean for b in ev.get("content", []))
        for ev in events
    )
