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

# Real leak shapes — RAW <invoke> body in text (the durable harm). Detection
# keys on the BODY (<invoke name="X"> + <parameter / </invoke>), NOT the optional
# "call" prefix, so leaks without "call", with CRLF, or appearing late all match.
LEAK_CASES = [
    # Classic harness shape with a "call" line (DB rowid 204014/203549 shape)
    'some text\n\ncall\n<invoke name="Bash">\n<parameter name="command">cd /x</parameter>',
    # Skill invocation variant
    'output:\n\ncall\n<invoke name="Skill">\n<parameter name="skill">foo</parameter>',
    # Leak at the very start of the block
    'call\n<invoke name="Bash">\n<parameter name="command">ls</parameter>',
    # NO "call" prefix at all (the 8th DB case + future variants) — MUST match
    '<invoke name="Bash">\n<parameter name="command">ls</parameter>',
    # CRLF line endings — must still match
    'text\r\ncall\r\n<invoke name="Bash">\r\n<parameter name="command">ls</parameter>',
    # Self-closing / empty invoke body
    'doing the thing now\n<invoke name="Read"></invoke>',
    # Leak AFTER 4000 chars (no scan-limit truncation) — MUST match
    "x" * 4100 + '\n<invoke name="Bash">\n<parameter name="command">ls</parameter>',
]

# Non-leak: prose / documentation discussing the syntax (must NOT trigger).
NON_LEAK_CASES = [
    # Inline backtick mention (the rowid 204050 shape that must be rejected)
    '看两张截图,我的回复每次都在 `<invoke name="Bash">` 那一刻停了',
    # Inline mention mid-sentence, backtick-wrapped
    'The harness emits `<invoke name="Bash">` as the tool-call format.',
    # FENCED code block documenting the syntax — the critical false-positive
    # the first design missed. A meta-heavy turn explaining THIS bug.
    'Example of the leak:\n```\ncall\n<invoke name="Bash">\n<parameter name="command">x</parameter>\n```\nThat is what we guard against.',
    # Fenced block WITHOUT a language tag, full body inside
    '```\n<invoke name="Skill">\n<parameter name="skill">foo</parameter>\n</invoke>\n```',
    # Self-explanation referencing the shape but backtick-wrapped
    'My previous reply stopped at `<invoke name="Bash">` — that was a leak.',
    # The word "call" as its own line but NO <invoke body follows
    'call\nthe function and see what happens',
    # Mentions <invoke but never a real body (no <parameter, no close)
    'the <invoke tag is interesting to discuss in the abstract',
    # Different casing — harness emits lowercase only; this is prose, not a leak
    'Prose: <INVOKE name="Bash"><PARAMETER name="command">ls</PARAMETER>',
    # Empty / trivial
    '',
    'just a normal assistant reply with no tool syntax at all',
]


@pytest.mark.parametrize("text", LEAK_CASES)
def test_detector_flags_real_leaks(text):
    """INV1: every raw-body leak shape is detected (with/without call prefix,
    CRLF, late in block, case-variant)."""
    assert detect_tool_call_leak(text) is True


@pytest.mark.parametrize("text", NON_LEAK_CASES)
def test_detector_ignores_discussion(text):
    """INV2: prose mentioning the syntax inline / in fenced code is NOT flagged
    (false-positive safety on the hottest path)."""
    assert detect_tool_call_leak(text) is False


def test_detector_handles_large_text_without_truncation():
    """No scan-limit truncation: a leak anywhere in a large block is detected;
    a huge clean block returns False (and the fast-path '<invoke' check keeps
    it cheap)."""
    big_leak = "x" * 100_000 + '\n<invoke name="Bash">\n<parameter name="command">ls</parameter>'
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
    from core.compaction_guard import EscalationLevel

    parent._compaction_guard = types.SimpleNamespace(
        record_tool_call=lambda *a, **k: None,
        check=lambda: EscalationLevel.MONITORING,
    )
    parent._open_tool_uses = {}
    parent._active_agent_tools = {}
    parent._emit_post_stream_metadata = lambda *a, **k: iter(())

    async def _noop_async(*a, **k):
        return None

    parent._check_rss_and_proactive_restart = _noop_async
    # (_check_context_soft_compact stub removed — method deleted run_2b1957f8)

    # Health sensor stub
    parent._health_sensor = types.SimpleNamespace(
        record_activity=lambda: None,
        record_turn=lambda **k: None,
    )
    parent._last_event_time = 0.0
    parent._maybe_build_elapsed_heartbeat = lambda: None
    parent._compute_message_timeout = lambda: 300.0
    # orchestrator's first-message timeout now calls _compute_init_timeout
    # (run_4b74b764, Part B) — mock parent must provide it (fresh-session 180s).
    parent._compute_init_timeout = lambda: 180.0

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


def test_orchestrator_does_not_fire_when_real_tool_use_present():
    """Third signal: if the message ALSO contains a real ToolUseBlock, a sibling
    TextBlock containing <invoke> body text is the model TALKING about a tool
    call, not leaking one — the guard must NOT fire."""
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    # Text that WOULD match the body matcher on its own ...
    text_with_body = 'Here is what I ran:\n<invoke name="Bash">\n<parameter name="command">ls</parameter>'
    msg = AssistantMessage(
        content=[
            TextBlock(text=text_with_body),
            ToolUseBlock(id="toolu_1", name="Bash", input={"command": "ls"}),
        ],
        model="test-model",
    )

    events, raised = asyncio.run(_drive(orch, [msg, _make_result_message()]))

    # ... but because a REAL tool_use is present, the guard must not fire.
    assert orch._parent._kill_calls["n"] == 0
    assert raised is None
