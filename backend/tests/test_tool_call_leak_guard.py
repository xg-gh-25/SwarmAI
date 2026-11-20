"""Tests for tool-call XML leak detection + orchestrator guard.

What is tested
--------------
1. ``detect_tool_call_leak`` (pure fn) — the double-condition matcher that
   distinguishes a real leak (``call`` line + line-start ``‹invoke name=``) from
   prose that merely discusses the syntax inline. Table-driven, including the
   exact shapes observed in the messages DB on 2026-06-24.
2. The StreamingOrchestrator TextBlock guard (去容灾化 contract) — a leaked text
   block is STRIPPED (raw ``‹invoke>`` removed) and KEPT; the turn yields to its
   natural completion. kill()/respawn was removed — termination authority belongs
   to the model, not a disaster-recovery kill.

Key properties / invariants
----------------------------
- INV1: every DB-confirmed leak shape (call+invoke) → True
- INV2: inline-discussion + fenced-code-block mention → False (false-positive
  safety on the hottest path — every assistant text block)
- INV3: a detected leak does NOT kill or raise — strip+keep, one path for both
  pure-leak and leak-alongside-real-tool_use (the former fork was removed)
- INV4: the strip fires INSIDE the TextBlock branch, before persist — the raw
  leak never reaches a yielded ``assistant`` event (but the cleaned block does)
- INV5: nothing of a leak is shown — no XML, no placeholder, no opener word,
  and live text_delta forwarding stops at the leak onset
- INV6: a turn that ENDS on a leak continues in-process (same client, one
  corrective query, no kill, one final ``result``), bounded to
  ``_MAX_LEAK_CONTINUATIONS`` consecutive tries and skipped after a user Stop
"""

from __future__ import annotations

from tests._tool_markup import tc
import asyncio
import types

import pytest

import json
from pathlib import Path

from core.session_utils import (
    detect_tool_call_leak,
    strip_tool_call_leak,
)


# ── 1. Pure detector — table-driven ────────────────────────────────────────

# Real leak shapes — RAW ‹invoke> body in text (the durable harm). Detection
# keys on the BODY (‹invoke name="X"> + ‹parameter / ‹/invoke>), NOT the optional
# "call" prefix, so leaks without "call", with CRLF, or appearing late all match.
LEAK_CASES = [
    # Classic harness shape with a "call" line (DB rowid 204014/203549 shape)
    tc('some text\n\ncall\n‹invoke name="Bash">\n‹parameter name="command">cd /x‹/parameter>'),
    # Skill invocation variant
    tc('output:\n\ncall\n‹invoke name="Skill">\n‹parameter name="skill">foo‹/parameter>'),
    # Leak at the very start of the block
    tc('call\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>'),
    # NO "call" prefix at all (the 8th DB case + future variants) — MUST match
    tc('‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>'),
    # CRLF line endings — must still match
    tc('text\r\ncall\r\n‹invoke name="Bash">\r\n‹parameter name="command">ls‹/parameter>'),
    # Self-closing / empty invoke body
    tc('doing the thing now\n‹invoke name="Read">‹/invoke>'),
    # Leak AFTER 4000 chars (no scan-limit truncation) — MUST match
    "x" * 4100 + tc('\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>'),
]

# Non-leak: prose / documentation discussing the syntax (must NOT trigger).
NON_LEAK_CASES = [
    # Inline backtick mention (the rowid 204050 shape that must be rejected)
    tc('看两张截图,我的回复每次都在 `‹invoke name="Bash">` 那一刻停了'),
    # Inline mention mid-sentence, backtick-wrapped
    tc('The harness emits `‹invoke name="Bash">` as the tool-call format.'),
    # FENCED code block documenting the syntax — the critical false-positive
    # the first design missed. A meta-heavy turn explaining THIS bug.
    tc('Example of the leak:\n```\ncall\n‹invoke name="Bash">\n‹parameter name="command">x‹/parameter>\n```\nThat is what we guard against.'),
    # Fenced block WITHOUT a language tag, full body inside
    tc('```\n‹invoke name="Skill">\n‹parameter name="skill">foo‹/parameter>\n‹/invoke>\n```'),
    # Self-explanation referencing the shape but backtick-wrapped
    tc('My previous reply stopped at `‹invoke name="Bash">` — that was a leak.'),
    # The word "call" as its own line but NO ‹invoke body follows
    'call\nthe function and see what happens',
    # Mentions ‹invoke but never a real body (no ‹parameter, no close)
    tc('the ‹invoke tag is interesting to discuss in the abstract'),
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
    a huge clean block returns False (and the fast-path '‹invoke' check keeps
    it cheap)."""
    big_leak = "x" * 100_000 + tc('\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>')
    assert detect_tool_call_leak(big_leak) is True
    big_clean = "z" * 500_000
    assert detect_tool_call_leak(big_clean) is False


# ── 1b. strip_tool_call_leak — the WRITE-side cleaner ───────────────────────
#
# Difference from the detector: it must remove the TRUNCATED residue a mid-stream
# kill persists (‹invoke name="Bash"> with NO following ‹parameter/‹/invoke>),
# AND it must preserve legitimate fenced/inline references byte-for-byte
# (per-MATCH, not per-line — the IMPROVEMENT.md 2026-08-31 false-negative class).

# Raw leaks the cleaner MUST remove (incl. the truncated persisted shape).
STRIP_LEAK_CASES = [
    # Truncated persisted shape — the dominant data.db form (kill mid-stream)
    tc('court\n‹invoke name="Bash">'),
    tc('ToDo bar 都是红色、一致。\n\ncourt\n‹invoke name="Bash">'),
    tc('court\n‹invoke name="Edit">\n\ncourt替换:'),
    # Complete body — removed whole
    tc('analysis\ncall\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>'),
    # Self-closing
    tc('doing the thing\n‹invoke name="Read">‹/invoke>'),
    # Leak at the very start
    tc('‹invoke name="Bash">\n‹parameter name="command">cd /x‹/parameter>'),
]

# Legitimate references the cleaner MUST preserve byte-for-byte (all in code spans).
STRIP_PRESERVE_CASES = [
    tc('看两张截图,我的回复每次都在 `‹invoke name="Bash">` 那一刻停了'),
    tc('The harness emits `‹invoke name="Bash">` as the tool-call format.'),
    tc('Example:\n```\ncall\n‹invoke name="Bash">\n‹parameter name="command">x‹/parameter>\n```\nguarded.'),
    tc('```\n‹invoke name="Skill">\n‹parameter name="skill">foo‹/parameter>\n‹/invoke>\n```'),
    tc('My reply stopped at `‹invoke name="Bash">` — that was a leak.'),
    'just a normal assistant reply with no tool syntax at all',
    '',
]


@pytest.mark.parametrize("text", STRIP_LEAK_CASES)
def test_strip_removes_raw_leak(text):
    """A raw leak fragment (incl. the truncated persisted shape) is removed, and
    the result no longer contains a ‹invoke name= body outside code spans."""
    cleaned = strip_tool_call_leak(text)
    assert tc("‹invoke name=") not in cleaned
    assert cleaned != text  # something was actually stripped


@pytest.mark.parametrize("text", STRIP_PRESERVE_CASES)
def test_strip_preserves_legit_references(text):
    """A fenced/inline reference to the syntax is preserved BYTE-FOR-BYTE — 0
    false-strip (the RP42 orthogonal signal + per-MATCH, not per-line)."""
    assert strip_tool_call_leak(text) == text


def test_strip_per_match_not_per_line():
    """The decisive per-MATCH case: a text block carrying BOTH a legitimate inline
    reference AND a raw leak keeps the reference and strips ONLY the raw leak. A
    per-line grep -v exclusion would drop the whole line and lose the reference
    (IMPROVEMENT.md 2026-08-31 run_de417612)."""
    text = tc('As I said `‹invoke name="Bash">` is the format.\n\ncourt\n‹invoke name="Edit">\n\ncourt done')
    cleaned = strip_tool_call_leak(text)
    # legit inline reference preserved
    assert tc('`‹invoke name="Bash">`') in cleaned
    # raw leak removed
    assert tc('‹invoke name="Edit">') not in cleaned


def test_strip_is_idempotent():
    """Cleaning already-clean text (or already-cleaned text) is a no-op."""
    once = strip_tool_call_leak(tc('court\n‹invoke name="Bash">\n\ntext'))
    assert strip_tool_call_leak(once) == once


def test_strip_against_real_corpus():
    """Ground-truth double-direction: against the real data.db corpus, every
    robust-RAW sample has its ‹invoke body removed, every robust-FENCED sample is
    preserved byte-for-byte (0 false-strip, 0 miss). The corpus 'label' field is
    NOT trusted — it is re-derived here with the robust predicate (restore \\n
    escaping, mask code spans, then a residual bare ‹invoke => RAW)."""
    corpus_path = Path(
        "/Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/"
        "leak-feedback-corpus/corpus.json"
    )
    if not corpus_path.exists():
        pytest.skip("leak-feedback corpus not present (workspace-local artifact)")
    rows = json.loads(corpus_path.read_text())

    import re as _re
    _fence = _re.compile(r"```.*?```", _re.DOTALL)
    _inline = _re.compile(r"`[^`]*`")

    def robust_is_raw(text: str) -> bool:
        if not text or tc("‹invoke") not in text:
            return False
        norm = text.replace("\\n", "\n").replace("\\t", "\t")
        residual = _inline.sub("", _fence.sub("", norm))
        return tc("‹invoke") in residual

    raw_total = raw_cleaned = fenced_total = fenced_preserved = 0
    for row in rows:
        text = row.get("text", "")
        if tc("‹invoke") not in text:
            continue
        if robust_is_raw(text):
            raw_total += 1
            if tc("‹invoke name=") not in strip_tool_call_leak(text):
                raw_cleaned += 1
        else:
            fenced_total += 1
            if strip_tool_call_leak(text) == text:
                fenced_preserved += 1

    assert raw_total > 0, "corpus has no RAW samples — predicate or data drift"
    assert raw_cleaned == raw_total, f"RAW not fully cleaned: {raw_cleaned}/{raw_total}"
    assert fenced_preserved == fenced_total, (
        f"FENCED false-stripped: {fenced_preserved}/{fenced_total} preserved"
    )


# ── 1b. Lossless masking (bug 2a) + persist==yield (bug 2d) ──────────────────
#
# These guard the run_719886ee refactor (root ①): the persist-side strip is the
# SINGLE write chokepoint (Gate-1 confirmed memory_extractor/summarization inherit
# it), so it is KEPT — but it must be LOSSLESS and must not fork UI vs DB.


def test_strip_lossless_on_backtick_fence_and_leak():
    """Bug 2a: a lone backtick must NOT pair across a fence sentinel and destroy
    a code block or leave a NUL. Input mixes a lone backtick, a fenced code
    block, a raw leak, and another lone backtick. The code block must survive
    byte-for-byte and NO \\x00 may remain in the output.

    Mutation proof: revert _mask_doc_contexts to the two-pass fence-then-inline
    form → this test goes RED (code block dropped + \\x00 residue)."""
    code_body = "important_code = 1"
    text = (
        tc("Don`t panic here is a raw leak ‹invoke name=\"Bash\"> and a block\n"
        "```python\n") + code_body + "\n```\n"
        "then that`s it"
    )
    out = strip_tool_call_leak(text)
    # The fenced code block must be preserved verbatim.
    assert code_body in out, f"code block lost: {out!r}"
    assert "```python" in out, f"fence lost: {out!r}"
    # No NUL sentinel may leak into the output.
    assert "\x00" not in out, f"NUL residue in output: {out!r}"
    # The raw leak IS removed (chokepoint still cleans).
    assert tc('‹invoke name="Bash">') not in out, f"leak not stripped: {out!r}"


def test_strip_lossless_preserves_many_inline_and_fence_spans():
    """Reinforce 2a: multiple inline-code spans with odd backtick counts around
    a fence must all round-trip. Mutation proof: two-pass mask → RED."""
    text = (
        tc("Use `a` and `b` and a stray ` tick, then\n"
        "```js\nconst x = `template`;\n```\n"
        "and `c` after. Leak: ‹invoke name=\"X\">")
    )
    out = strip_tool_call_leak(text)
    assert "\x00" not in out
    assert "const x = `template`;" in out, f"fenced template lost: {out!r}"
    assert "`a`" in out and "`b`" in out and "`c`" in out
    assert tc('‹invoke name="X">') not in out


def test_mask_doc_contexts_round_trips_lone_backtick_with_fence():
    """Directly exercise the mask/restore round-trip (the 2a locus). A lone
    backtick + a fence must restore the fence span verbatim and leave no NUL.
    Mutation proof: two-pass mask → the fence span is corrupted → RED."""
    from core.session_utils import _mask_doc_contexts, _restore_doc_contexts

    text = "a ` lone tick\n```py\nZZZ=9\n```\n tail `x`"
    masked, spans = _mask_doc_contexts(text)
    restored = _restore_doc_contexts(masked, spans)
    assert "\x00" not in restored, f"NUL in restored: {restored!r}"
    assert "ZZZ=9" in restored, f"fence body lost in round-trip: {restored!r}"
    assert "```py" in restored


def test_persist_strip_returns_cleaned_blocks_no_fork():
    """Bug 2d (contract half): the persist-side clean must EXPOSE the cleaned
    blocks so the caller can write them back into event['content'] — persist and
    yield must carry the SAME bytes (no UI≠DB fork).

    This asserts the mechanism Gate-1 required: _clean_blocks_for_persist is the
    single clean that returns the cleaned list. Mutation proof: if the helper
    returns the original blocks unchanged (fork reintroduced), the leak remains
    in the 'persisted' copy → RED."""
    from core.session_router import _clean_blocks_for_persist

    blocks = [
        {"type": "text", "text": tc("hi ‹invoke name=\"Bash\"> leak")},
        {"type": "text", "text": "clean line"},
    ]
    cleaned = _clean_blocks_for_persist(blocks)
    # cleaned is a NEW list with the leak removed from the first block.
    assert tc('‹invoke name="Bash">') not in cleaned[0]["text"], cleaned
    assert cleaned[1]["text"] == "clean line"
    # The SAME cleaned list is what both persist and yield must use — the caller
    # writes it back to event['content']. Verify it is a usable blocks list.
    assert all(isinstance(b, dict) and "text" in b for b in cleaned)


def test_persist_strip_preserves_non_text_and_clean_blocks():
    """2d contract: non-text blocks and already-clean text pass through
    unchanged (clean must not corrupt clean content)."""
    from core.session_router import _clean_blocks_for_persist

    blocks = [
        {"type": "text", "text": "just discussing it, no leak"},
        {"type": "image", "source": {"x": 1}},
        {"type": "text", "text": "code: ```py\nk=1\n``` done"},
    ]
    cleaned = _clean_blocks_for_persist(blocks)
    assert cleaned[0]["text"] == "just discussing it, no leak"
    assert cleaned[1] == {"type": "image", "source": {"x": 1}}
    assert "k=1" in cleaned[2]["text"] and "\x00" not in cleaned[2]["text"]


def test_persist_bytes_equal_yield_bytes_e2e():
    """Layer-4 cross-boundary E2E (bug 2d seam = persist-writer bytes ==
    stream-yield bytes). Drives the REAL SessionRouter._persist_assistant_blocks
    (the actual write path — only the LEAF db.messages.put is mocked to capture
    what hit the DB; the strip + return-contract are NOT mocked).

    This test replicates the EXACT call-site pattern the 3 stream loops use:
        event = {..., "content": <original>}
        event["content"] = await self._persist_assistant_blocks(..., event["content"], ...)
        yield event                      # the UI sees event["content"]
    and asserts the YIELDED event["content"] (what the UI gets) == the content the
    DB received. This is the real 2d seam — NOT a tautology on the method's own
    return value.

    Two mutation proofs this catches (both reproduced in the DELIVER adversarial
    pass → RED):
      (a) revert the contract to `return True` (bool) → the write-back sets
          event["content"]=True while the DB got the cleaned list → yielded != DB.
      (b) delete the `event["content"] = ` assignment at the call site (keep the
          original) → event["content"] still holds the raw leak while the DB got
          the cleaned list → yielded != DB. (The prior version of this test asserted
          only the method's return and MISSED (b) — red-team finding, now fixed.)
    """
    import asyncio
    from unittest.mock import patch
    from core.session_router import SessionRouter

    captured = {}

    async def _fake_put(row):
        # Deep-ish copy so a later in-place mutation of the blocks can't retro-
        # change what we recorded as "what the DB received".
        captured["content"] = [dict(b) for b in row["content"]]

    fake_db = types.SimpleNamespace(messages=types.SimpleNamespace(put=_fake_put))

    router = SessionRouter.__new__(SessionRouter)  # no __init__ — pure method call

    # The event object exactly as the stream loop holds it, with a raw leak.
    event = {
        "type": "assistant",
        "content": [
            {"type": "text", "text": tc("answer ‹invoke name=\"Bash\"> leaked here")},
            {"type": "text", "text": "and a clean trailing line"},
        ],
    }

    async def _run():
        with patch("database.db", fake_db):
            # REPLICATE the real call-site write-back (session_router.py:3544/3790/3821).
            event["content"] = await router._persist_assistant_blocks(
                "sess-e2e", event["content"], "test-model", client_id="cid",
            )
            # `yield event` happens here in production — the UI reads event["content"].

    asyncio.run(_run())

    # DB actually received cleaned content (leak gone).
    assert captured.get("content") is not None, "db.put was never called"
    assert tc('‹invoke name="Bash">') not in captured["content"][0]["text"]
    # THE SEAM: what the UI gets (yielded event["content"]) == what the DB got.
    # Catches both (a) bool-return reversion and (b) a deleted write-back.
    assert event["content"] == captured["content"], (
        "yielded event['content'] != persisted DB content — the 2d fork is back"
    )
    assert isinstance(event["content"], list), "event['content'] must stay a blocks list"
    assert tc('‹invoke name="Bash">') not in event["content"][0]["text"]


# ── 2. Orchestrator guard (integration) ─────────────────────────────────────


def _make_assistant_message(text, model="test-model"):
    """Build a REAL claude_agent_sdk.AssistantMessage so the orchestrator's
    isinstance(block, TextBlock) / isinstance(message, AssistantMessage) checks
    match — a fake duck-typed object would be silently ignored and the test
    would pass for the wrong reason (the zombie detector, not the leak guard)."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    return AssistantMessage(content=[TextBlock(text=text)], model=model)


def _make_tool_use_message(tool_id="toolu_retry", model="test-model"):
    """An AssistantMessage carrying a REAL tool_use (the recovered action)."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    return AssistantMessage(
        content=[ToolUseBlock(id=tool_id, name="Bash", input={"command": "ls"})],
        model=model,
    )


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


async def _drive(orchestrator, messages, followups=()):
    """Run _read_formatted_response against a stream of messages and collect
    yielded events. Returns (events, raised_exc).

    ``followups`` are the responses to successive in-process ``client.query()``
    calls (the leak continuation); the prompts sent are recorded on
    ``orchestrator._parent._client.queries``."""
    rounds = [list(messages), *[list(r) for r in followups]]

    class _Client:
        def __init__(self):
            self.queries = []
            self._round = 0

        async def query(self, prompt):
            self.queries.append(prompt)
            self._round += 1

        def receive_response(self):
            msgs = rounds[self._round] if self._round < len(rounds) else []

            async def _stream():
                for m in msgs:
                    yield m

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
        record_tool_result=lambda *a, **k: None,
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


def test_orchestrator_strips_pure_leak_and_keeps_turn():
    """New contract (去容灾化): a PURE-LEAK TextBlock (leak text, NO real
    tool_use) is NOT killed and does NOT raise — the leak text is STRIPPED
    (best-effort pollution reduction) and the turn yields to natural completion.
    Termination authority belongs to the model, not a kill+respawn.

    This is the unification of the old PURE-LEAK and MIXED branches into a single
    strip-and-keep path. Replaces the old test_orchestrator_blocks_leaked_block_
    and_kills (which asserted the removed kill()+raise disaster-recovery)."""
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    leak = tc('analysis text\n\ncall\n‹invoke name="Bash">\n‹parameter name="command">cd /x‹/parameter>')
    msg = _make_assistant_message(leak)

    # A trailing ResultMessage so the turn ends via the normal result path —
    # the turn is no longer aborted mid-stream, it completes naturally. The
    # follow-up round is the model's reply to the in-process continuation.
    events, raised = asyncio.run(_drive(
        orch, [msg, _make_result_message()],
        followups=[[_make_tool_use_message(), _make_result_message()]],
    ))

    # Termination authority stays with the model: NO kill, NO raise.
    assert orch._parent._kill_calls["n"] == 0, "pure-leak must NOT kill (去容灾化)"
    assert raised is None, "pure-leak must NOT raise — turn completes naturally"
    # The leak text was STRIPPED but the turn's prose was KEPT.
    assistant_evs = [ev for ev in events if ev.get("type") == "assistant"]
    assert assistant_evs, "the turn's assistant content must still be yielded (kept)"
    texts = [
        block.get("text", "")
        for ev in assistant_evs
        for block in ev.get("content", [])
        if block.get("type") == "text"
    ]
    joined = "\n".join(texts)
    assert "analysis text" in joined
    # No residue of the failed call reaches the user: no XML, no placeholder,
    # no dangling opener word.
    assert tc("‹invoke name=") not in joined
    assert "[tool-call omitted]" not in joined
    assert "call" not in joined.replace("analysis text", "")


def test_orchestrator_passes_clean_text_block():
    """A normal text block (even one mentioning ‹invoke name= inline) is
    yielded as assistant content and does NOT kill the session."""
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    clean = tc("The harness emits ‹invoke name=\"Bash\"> as the tool-call format inline.")
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
    """Third signal: if the message ALSO contains a real ToolUseBlock, the turn
    is legitimate (the model DID invoke a tool) — the guard must NOT kill. But
    the leak-feedback fix (run_da39669c) additionally requires the raw ‹invoke>
    text be STRIPPED from the persisted content, so it cannot re-seed the loop on
    resume (the mixed-message gap, data.db session a0f04b00)."""
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    # Text that WOULD match the body matcher on its own ...
    text_with_body = tc('Here is what I ran:\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>')
    msg = AssistantMessage(
        content=[
            TextBlock(text=text_with_body),
            ToolUseBlock(id="toolu_1", name="Bash", input={"command": "ls"}),
        ],
        model="test-model",
    )

    events, raised = asyncio.run(_drive(orch, [msg, _make_result_message()]))

    # ... because a REAL tool_use is present, the guard must NOT kill (killing
    # would abort legitimate in-flight tool execution).
    assert orch._parent._kill_calls["n"] == 0
    assert raised is None
    # ... AND the raw leak text must be stripped from every yielded assistant
    # content block (the mixed-message persist gap this fix closes).
    for ev in events:
        if ev.get("type") == "assistant":
            for block in ev.get("content", []):
                assert tc("‹invoke name=") not in block.get("text", "")


def test_orchestrator_strips_leak_but_keeps_prose_in_mixed_message():
    """The mixed-message strip is per-MATCH: legitimate text around the leak is
    preserved while only the raw ‹invoke> body is removed — the turn's real
    content survives, the leak does not."""
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    mixed = tc('Rendering now.\n‹invoke name="Edit">\n‹parameter name="x">y‹/parameter>')
    msg = AssistantMessage(
        content=[
            TextBlock(text=mixed),
            ToolUseBlock(id="toolu_2", name="Edit", input={"x": "y"}),
        ],
        model="test-model",
    )

    events, raised = asyncio.run(_drive(orch, [msg, _make_result_message()]))

    assert raised is None
    assert orch._parent._kill_calls["n"] == 0
    # The real prose survived; the leak body did not.
    asst_texts = [
        b.get("text", "")
        for ev in events
        if ev.get("type") == "assistant"
        for b in ev.get("content", [])
    ]
    joined = "\n".join(asst_texts)
    assert "Rendering now." in joined
    assert tc("‹invoke name=") not in joined


# ── 3b. leak dead-end → in-process continuation ────────────────────────────
# A turn that ENDS on a leaked tool call (stop_reason=end_turn, the shape the CLI
# does not self-retry) must not dead-end: the orchestrator sends ONE corrective
# message on the SAME client (no kill, no respawn) and keeps reading; the user
# sees exactly one `result`, after the recovered action.

_DEADEND_LEAK = (
    tc("跑测试(GREEN 验证)。\n\ncount\n‹invoke name=\"Bash\">\n"
    "‹parameter name=\"command\">ls‹/parameter>\n‹/invoke>")
)


def _results(events):
    return [ev for ev in events if ev.get("type") == "result"]


def test_leak_deadend_continues_in_process_once():
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    events, raised = asyncio.run(_drive(
        orch,
        [_make_assistant_message(_DEADEND_LEAK), _make_result_message()],
        followups=[[_make_tool_use_message(), _make_result_message()]],
    ))
    client = orch._parent._client
    assert raised is None
    assert orch._parent._kill_calls["n"] == 0, "continuation must not kill"
    assert client.queries == [orch._LEAK_CONTINUATION_PROMPT]
    assert tc("‹invoke") not in orch._LEAK_CONTINUATION_PROMPT  # no priming markup
    # The dead-end round's `result` is swallowed; only the final one reaches the UI.
    assert len(_results(events)) == 1
    assert any(ev.get("type") == "tool_use" for ev in events) or any(
        b.get("type") == "tool_use"
        for ev in events if ev.get("type") == "assistant"
        for b in ev.get("content", [])
    ), "the recovered real tool_use must be streamed"


def test_leak_continuation_is_bounded():
    """A model that keeps leaking gets _MAX_LEAK_CONTINUATIONS retries, then
    the turn ends normally (loop guard)."""
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    leak_round = [_make_assistant_message(_DEADEND_LEAK), _make_result_message()]
    events, raised = asyncio.run(_drive(
        orch, leak_round, followups=[leak_round] * 5,
    ))
    assert raised is None
    assert len(orch._parent._client.queries) == orch._MAX_LEAK_CONTINUATIONS
    assert len(_results(events)) == 1


def test_leak_continuation_counter_resets_on_real_tool_use():
    """The bound counts CONSECUTIVE unproductive tries: a real tool_use in
    between resets it, so separate dead-ends in a long turn each recover."""
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    leak_round = [_make_assistant_message(_DEADEND_LEAK), _make_result_message()]
    recover_then_leak = [
        _make_tool_use_message("toolu_a"),
        _make_assistant_message(_DEADEND_LEAK),
        _make_result_message(),
    ]
    events, raised = asyncio.run(_drive(
        orch, leak_round,
        followups=[leak_round, recover_then_leak, leak_round,
                   [_make_tool_use_message("toolu_b"), _make_result_message()]],
    ))
    assert raised is None
    assert len(orch._parent._client.queries) == 4
    assert len(_results(events)) == 1


def test_leak_no_continuation_after_user_stop():
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    orch._parent._user_stopped_current_turn = True
    events, raised = asyncio.run(_drive(
        orch, [_make_assistant_message(_DEADEND_LEAK), _make_result_message()],
    ))
    assert raised is None
    assert orch._parent._client.queries == []
    assert len(_results(events)) == 1


def test_leak_followed_by_real_tool_use_does_not_continue():
    """CLI already recovered (leak, then a real tool_use): no extra query."""
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    events, raised = asyncio.run(_drive(orch, [
        _make_assistant_message(_DEADEND_LEAK),
        _make_tool_use_message(),
        _make_assistant_message("Tests are green."),
        _make_result_message(),
    ]))
    assert raised is None
    assert orch._parent._client.queries == []
    assert len(_results(events)) == 1


def test_clean_turn_never_continues():
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    events, raised = asyncio.run(_drive(orch, [
        _make_assistant_message("All done."), _make_result_message(),
    ]))
    assert raised is None
    assert orch._parent._client.queries == []


def _text_delta(text, index=0):
    from claude_agent_sdk.types import StreamEvent

    return StreamEvent(
        uuid="u", session_id="sdk-test", parent_tool_use_id=None,
        event={"type": "content_block_delta", "index": index,
               "delta": {"type": "text_delta", "text": text}},
    )


def test_live_deltas_hide_leak_from_onset():
    """Streamed tokens stop at the leak onset — the raw XML is never forwarded
    to the UI while it streams (split mid-tag to exercise the buffer)."""
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    chunks = ["跑测试。\n\ncount\n<inv", 'oke name="Bash">\n<param',
              tc('eter name="command">ls‹/parameter>')]
    msgs = [_text_delta(c) for c in chunks]
    msgs += [_make_assistant_message("跑测试。\n\n" + "count\n" + chunks[0][-4:]
                                     + "".join(chunks[1:])),
             _make_result_message()]
    events, raised = asyncio.run(_drive(
        orch, msgs, followups=[[_make_tool_use_message(), _make_result_message()]],
    ))
    assert raised is None
    streamed = "".join(ev["text"] for ev in events if ev.get("type") == "text_delta")
    assert tc("‹invoke") not in streamed and "<param" not in streamed
    assert streamed.startswith("跑测试。")


def test_live_deltas_keep_fenced_examples():
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    text = tc('Example:\n```\n‹invoke name="Bash">\n‹parameter name="command">x‹/parameter>\n```\n')
    events, raised = asyncio.run(_drive(orch, [
        _text_delta(text[:20]), _text_delta(text[20:]),
        _make_assistant_message(text), _make_result_message(),
    ]))
    streamed = "".join(ev["text"] for ev in events if ev.get("type") == "text_delta")
    assert streamed == text
    assert orch._parent._client.queries == []


@pytest.mark.parametrize("buf,expected", [
    (tc("hello\n\ncount\n‹invoke name=\"Ba"), 13),
    (tc("ab\n‹invoke name=\""), 3),
    (tc("```\n‹invoke name=\"X\""), -1),          # inside an open fence
    (tc("```\nx\n```\n‹invoke name=\"X\""), 10),  # after a closed fence
    (tc("x `‹invoke name=\"a\"` y"), -1),          # inline code
    ("no tags at all", -1),
])
def test_find_stream_leak_onset(buf, expected):
    from core.session_utils import find_stream_leak_onset

    assert find_stream_leak_onset(buf) == expected


def test_strip_removes_opener_word_and_leaves_no_placeholder():
    cleaned = strip_tool_call_leak(_DEADEND_LEAK)
    assert cleaned == "跑测试(GREEN 验证)。\n\n"


# ── 4. user-side persist strip (run_67250d95) ──────────────────────────────
# The ONLY path a RAW ‹invoke> body persists to data.db is a USER message:
# assistant-persist funnels through _clean_blocks_for_persist, but user-persist
# did not. Two user-content write doors (R27 + Gate-1): session_router's
# db.messages.put (production) and session_pending._payload_json (the raw-INSERT
# second door). Both must strip. Verified harm: data.db had 2 raw rows, both
# role=user — a user re-fed a prior turn's on-screen leak remnant.

def _decode_payload_text(payload: str) -> str:
    """Reconstruct the human-readable text from a _payload_json result.

    MUST decode (not substring-match the raw JSON): dumps_json escapes `"` →
    `\\"`, so a raw `‹invoke name="Bash">` in the text serializes to
    `‹invoke name=\\"Bash\\">` and a naive `'‹invoke name="Bash">' in payload`
    is ALWAYS False — a vacuous assertion that passes with OR without the strip
    (RP47 theater, caught at BUILD). Decoding back to the real text restores the
    bytes detect_tool_call_leak actually judges."""
    import json
    blocks = json.loads(payload)
    return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))


def test_payload_json_strips_user_message_leak():
    """session_pending._payload_json (the 2nd write door, raw INSERT) must strip
    a raw ‹invoke> body from a user_message. Mutation proof: without the strip,
    the DECODED payload text contains the raw ‹invoke> → detect fires → RED."""
    from core.session_pending import _payload_json
    leak = tc('why does this keep happening \'core\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>')
    text = _decode_payload_text(_payload_json(leak, None))
    assert tc('‹invoke name="Bash">') not in text, text
    assert detect_tool_call_leak(text) is False, text


def test_payload_json_strips_user_content_leak():
    """Same door, content (multimodal) path: a raw ‹invoke> body in a content
    block must be stripped (judged on the DECODED text, not the escaped JSON)."""
    from core.session_pending import _payload_json
    content = [{"type": "text", "text": tc('look:\n‹invoke name="Bash">\n‹parameter name="command">rm‹/parameter>')}]
    text = _decode_payload_text(_payload_json(None, content))
    assert tc('‹invoke name="Bash">') not in text, text
    assert detect_tool_call_leak(text) is False, text


def test_payload_json_preserves_clean_and_fenced():
    """No over-reach: clean text + a fenced ‹invoke discussion survive intact."""
    from core.session_pending import _payload_json
    # clean
    assert "hello world" in _decode_payload_text(_payload_json("hello world", None))
    # fenced discussion must be preserved (strip only removes RAW leaks)
    fenced = tc('the leak looks like ```\n‹invoke name="Bash">\n```')
    text = _decode_payload_text(_payload_json(fenced, None))
    assert tc("‹invoke") in text, "fenced discussion wrongly stripped: " + text


def test_session_router_user_persist_calls_clean():
    """session_router's user-persist path must pass user_content through
    _clean_blocks_for_persist before db.messages.put. Asserts the call-site wiring
    by source inspection (the production db.messages.put at the user-persist guard
    is fed a cleaned list). Mutation proof: remove the _clean_blocks_for_persist
    call from the user-persist block → this assertion RED."""
    import inspect
    from core import session_router
    src = inspect.getsource(session_router)
    # The user-persist block (role:"user") must clean user_content. Narrow to the
    # region around the user put and assert the clean call is present there.
    idx = src.find('"role": "user"')
    assert idx != -1, "user-persist put not found"
    window = src[max(0, idx - 800):idx]
    assert "_clean_blocks_for_persist(user_content" in window, (
        "user_content is persisted WITHOUT _clean_blocks_for_persist — the raw-leak "
        "user-persist gap (run_67250d95) is open"
    )
    # A user may type "count 一下": the glitch-prefix strip is assistant-only.
    assert "_clean_blocks_for_persist(user_content, assistant=False)" in window


# ── ReDoS: strip must be linear-time ───────────────────────────────────────
# The old single-regex strip rescanned to end-of-text for every ‹invoke whose
# ‹parameter> never closed: 240KB of crafted text took 5.1s synchronously on
# the event loop (every session frozen). Reachable from any user message
# (session_pending strips user text) and from model output.

@pytest.mark.parametrize("crafted", [
    tc('‹invoke name="A">‹parameter name="x">a ') * 25_000,   # ~1MB, unclosed bodies
    tc('‹invoke name="') * 60_000,                             # unclosed quotes
    'count\n' * 100_000,                                   # opener-like lines
    tc('‹invoke name="A">') + ' ' * 500_000,                   # long whitespace run
])
def test_strip_is_linear_on_crafted_input(crafted):
    import time

    t0 = time.perf_counter()
    strip_tool_call_leak(crafted)
    assert time.perf_counter() - t0 < 1.0, "strip must stay linear (ReDoS)"


@pytest.mark.parametrize("text,expected", [
    # A leak's trailing whitespace runs into the next line's opener word:
    # scanning resumes at the previous leak's end, like re.subn.
    (tc('x中文‹invoke name="X">\n  text \n‹invoke name="Bash">'), 'x中文text \n'),
    (tc('count\n‹invoke name="Bash">    \r\n  count\n‹invoke name="Bash">‹/invoke>‹/invoke>  '),
     tc('count\n‹/invoke>  ')),
    # complete body, then an unclosed one: first removed whole, second head-only
    (tc('a\n‹invoke name="B">\n‹parameter name="c">ls‹/parameter>\n‹/invoke>\n'
     'b\n‹invoke name="C">\n‹parameter name="d">never closed'),
     tc('a\n\nb\n‹parameter name="d">never closed')),
])
def test_strip_matches_previous_regex_semantics(text, expected):
    assert strip_tool_call_leak(text) == expected
