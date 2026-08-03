"""Tests for tool-call leak RECOVERY (run_37008f2d).

Distinct from test_tool_call_leak_guard.py (which tests DETECTION). This file
tests what happens AFTER a leak is detected — the bounded corrective-resume
recovery that replaces the old bare --resume infinite loop.

The bug (root cause, dual-skeptic-confirmed): a detected leak raised a retriable
RuntimeError → send() routed it to _retry_with_resume → a BARE --resume that
replayed the SDK's poisoned transcript verbatim → the model re-leaked from the
same priming. Log proof: resume id e9d7c08d leaked at 19:12:51 then AGAIN at
19:14:41 (same id, two consecutive force_kills) — a self-reinforcing loop that
also fed the OT01 recycle storm (each kill bumped the frontend gen).

The fix (Gate-1-revised, bounded loop NOT "prevent"):
- 1st leak → `_handle_tool_call_leak` injects a DESCRIPTIVE correction prefix into
  query_content, then --resume. The prefix REDUCES re-leak probability (it cannot
  PREVENT it — --resume restores the poisoned assistant turn and the prefix is a
  NEW user message appended after it; the poison is the restored transcript, not
  the flag). This buys the model one self-correction chance.
- 2nd consecutive leak (recovery flag already True) → do NOT resume again. Route to
  a leak-specific CLEAN TERMINAL: clear_identity=True (drop the poisoned --resume
  target so the NEXT user turn starts fresh, not re-poisoned) + a leak-specific
  user-visible event. This BOUNDS the loop (Gate-1 check #4).
- Leak recovery does NOT increment _retry_count (strategy correction, mirrors
  _handle_buffer_overflow), so it never starves the genuine-transient retry budget.

Methodology: forced-execution. Drive the REAL `_handle_tool_call_leak` and the
REAL send()-intercept decision, mocking ONLY the SDK boundary (_spawn,
_crash_to_cold_async, the streaming orchestrator). No re-derivation of prod logic
in the test (the Part-B test-theater lesson, run_4b74b764).
"""

from __future__ import annotations


import pytest

from core.session_unit import SessionUnit, SessionState
from core.session_utils import detect_tool_call_leak, _is_retriable_error


LEAK_ERROR = (
    "Tool-call XML leaked into text channel "
    "(session_id=test) — retrying with --resume for a proper tool_use"
)


def _unit():
    """A real SessionUnit (via __init__, so all flags/locks are wired)."""
    return SessionUnit(session_id="test-leak-recovery", agent_id="default")


def _opts():
    """A real ClaudeAgentOptions — _build_retry_options calls vars() on it, so a
    bare object() fails. Use the real type the production path receives."""
    from claude_agent_sdk import ClaudeAgentOptions
    return ClaudeAgentOptions()


# ─────────────────────────────────────────────────────────────────────────
# AC1 — 1st leak routes to _handle_tool_call_leak and injects a correction
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_leak_injects_correction_into_query():
    """AC1/AC3: _handle_tool_call_leak prepends a correction to query_content
    before the resume stream. RED until the handler exists + injects."""
    u = _unit()
    u._sdk_session_id = "sdk-poisoned"

    captured = {"query": None}

    async def _fake_crash(*a, **k):
        u.state = SessionState.COLD

    async def _fake_spawn(*a, **k):
        u.state = SessionState.STREAMING

    async def _fake_stream(query):
        captured["query"] = query
        return
        yield  # make async-gen

    u._crash_to_cold_async = _fake_crash
    u._spawn = _fake_spawn
    u._streaming_orchestrator.stream_query = _fake_stream

    original = "do the thing"
    async for _ in u._handle_tool_call_leak(original, _opts(), None, LEAK_ERROR):
        pass

    assert captured["query"] is not None, "recovery stream never ran"
    injected = (
        captured["query"]
        if isinstance(captured["query"], str)
        else " ".join(
            b.get("text", "") for b in captured["query"] if isinstance(b, dict)
        )
    )
    assert original in injected, "original query must be preserved"
    assert injected != original, "AC3: query MUST be changed (correction injected), not bare resume"


@pytest.mark.asyncio
async def test_correction_prefix_contains_no_leak_syntax():
    """AC2: the injected correction must NOT itself contain tool-call leak
    syntax — otherwise it re-pollutes the very context that caused the leak."""
    u = _unit()
    u._sdk_session_id = "sdk-x"
    captured = {"query": None}

    async def _fake_crash(*a, **k):
        u.state = SessionState.COLD

    async def _fake_spawn(*a, **k):
        u.state = SessionState.STREAMING

    async def _fake_stream(query):
        captured["query"] = query
        return
        yield

    u._crash_to_cold_async = _fake_crash
    u._spawn = _fake_spawn
    u._streaming_orchestrator.stream_query = _fake_stream

    async for _ in u._handle_tool_call_leak("q", _opts(), None, LEAK_ERROR):
        pass

    injected = (
        captured["query"]
        if isinstance(captured["query"], str)
        else " ".join(
            b.get("text", "") for b in captured["query"] if isinstance(b, dict)
        )
    )
    # The detector is the ground truth for "is this leak syntax". The correction
    # text must not trip it.
    assert not detect_tool_call_leak(injected), (
        "correction prefix contains leak syntax — re-pollutes context (the bug)"
    )


@pytest.mark.asyncio
async def test_first_leak_sets_recovery_flag_before_stream():
    """AC4 ordering (Gate-1 check #1/#3): the recovery flag MUST be set BEFORE
    the resume stream runs, so a re-leak DURING recovery is recognized as the
    2nd leak. RED until the handler sets the flag first."""
    u = _unit()
    u._sdk_session_id = "sdk-x"
    flag_at_stream_time = {"v": None}

    async def _fake_crash(*a, **k):
        u.state = SessionState.COLD

    async def _fake_spawn(*a, **k):
        u.state = SessionState.STREAMING

    async def _fake_stream(query):
        flag_at_stream_time["v"] = u._tool_call_leak_recovery
        return
        yield

    u._crash_to_cold_async = _fake_crash
    u._spawn = _fake_spawn
    u._streaming_orchestrator.stream_query = _fake_stream

    async for _ in u._handle_tool_call_leak("q", _opts(), None, LEAK_ERROR):
        pass

    assert flag_at_stream_time["v"] is True, (
        "flag must be True by the time the recovery stream runs (else a re-leak "
        "during recovery is mis-read as a 1st leak → infinite loop)"
    )


@pytest.mark.asyncio
async def test_leak_recovery_does_not_increment_retry_count():
    """AC6: leak recovery is a strategy correction, NOT a transient retry —
    it must not consume the _retry_count budget (mirrors buffer_overflow)."""
    u = _unit()
    u._sdk_session_id = "sdk-x"
    before = u._retry_count

    async def _fake_crash(*a, **k):
        u.state = SessionState.COLD

    async def _fake_spawn(*a, **k):
        u.state = SessionState.STREAMING

    async def _fake_stream(query):
        return
        yield

    u._crash_to_cold_async = _fake_crash
    u._spawn = _fake_spawn
    u._streaming_orchestrator.stream_query = _fake_stream

    async for _ in u._handle_tool_call_leak("q", _opts(), None, LEAK_ERROR):
        pass

    assert u._retry_count == before, "leak recovery must not increment _retry_count"


# ─────────────────────────────────────────────────────────────────────────
# AC1 flag — initial state
# ─────────────────────────────────────────────────────────────────────────


def test_recovery_flag_initializes_false():
    """The per-message recovery flag starts False (mirror _buffer_overflow_recovery)."""
    u = _unit()
    assert u._tool_call_leak_recovery is False


# ─────────────────────────────────────────────────────────────────────────
# AC4 — 2nd consecutive leak → clean terminal (bounded loop), NOT another resume
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_leak_is_clean_terminal_not_resume():
    """AC4: when the recovery flag is ALREADY True (a re-leak during/after the
    1st corrective resume), send()'s leak intercept must route to a clean
    terminal — NOT call _handle_tool_call_leak again, NOT fall through to bare
    _retry_with_resume. The terminal must clear_identity (drop the poisoned
    --resume target). RED until the 2nd-leak branch exists."""
    u = _unit()
    u._sdk_session_id = "sdk-poisoned"
    u._tool_call_leak_recovery = True  # simulate: 1st recovery already happened

    cleared = {"v": False}
    handler_called = {"v": False}

    async def _fake_crash(*a, clear_identity=False, **k):
        if clear_identity:
            cleared["v"] = True
        u.state = SessionState.COLD

    async def _fake_handler(*a, **k):
        handler_called["v"] = True
        return
        yield

    u._crash_to_cold_async = _fake_crash
    u._handle_tool_call_leak = _fake_handler

    events = []
    async for ev in u._dispatch_leak_recovery(LEAK_ERROR, "q", _opts(), None):
        events.append(ev)

    assert handler_called["v"] is False, "2nd leak must NOT re-run the corrective resume"
    assert cleared["v"] is True, "2nd-leak terminal MUST clear_identity (drop poisoned --resume target)"
    # A leak-specific terminal event must be surfaced (not a generic crash).
    assert any(
        "leak" in str(ev.get("code", "")).lower()
        or "leak" in str(ev.get("message", "")).lower()
        for ev in events
    ), "2nd leak must surface a leak-specific terminal event, not a generic error"


@pytest.mark.asyncio
async def test_first_leak_dispatch_calls_handler_not_terminal():
    """AC1/AC4 control: when the recovery flag is False, the dispatcher routes
    to _handle_tool_call_leak (the corrective resume), NOT the terminal."""
    u = _unit()
    u._sdk_session_id = "sdk-x"
    u._tool_call_leak_recovery = False

    handler_called = {"v": False}
    cleared = {"v": False}

    async def _fake_handler(*a, **k):
        handler_called["v"] = True
        yield {"_recovered": True}

    async def _fake_crash(*a, clear_identity=False, **k):
        if clear_identity:
            cleared["v"] = True

    u._handle_tool_call_leak = _fake_handler
    u._crash_to_cold_async = _fake_crash

    async for _ in u._dispatch_leak_recovery(LEAK_ERROR, "q", _opts(), None):
        pass

    assert handler_called["v"] is True, "1st leak must route to the corrective handler"
    assert cleared["v"] is False, "1st leak must NOT clear identity (keeps sdk_session for resume)"


# ─────────────────────────────────────────────────────────────────────────
# AC5 — INV3 preserved: leak string STAYS retriable (we did NOT remove it)
# ─────────────────────────────────────────────────────────────────────────


def test_leak_string_still_retriable_inv3_preserved():
    """AC5: the fix must NOT remove the leak pattern from _is_retriable_error —
    that would break INV3 (test_tool_call_leak_guard.py:108/:257) and create a
    crash-to-DEAD gap. The dedicated intercept handles routing; the retriable
    classification is preserved as a backstop."""
    assert _is_retriable_error(LEAK_ERROR) is True, (
        "INV3 must hold: leak error stays retriable (do NOT remove the pattern)"
    )


# ─────────────────────────────────────────────────────────────────────────
# Gate-2 must-fix #2 — the send() INTERCEPT control-flow (the for/else footgun).
# These drive the REAL send() except block (not the handler/dispatcher in
# isolation), mocking ONLY the SDK boundary. The for/else bug Gate-2 caught
# lived HERE and had zero coverage: an `async for ... else: return` with no
# `break` fired `else` on EVERY completion, silently swallowing a non-leak
# transient that surfaced during the corrective resume (bypassing the OOM
# cooldown / backoff in _retry_with_resume). The fix is a recovered/leak_handled
# boolean. These tests assert the exact contract the bug violated.
# ─────────────────────────────────────────────────────────────────────────


def _idle_unit_ready_to_send():
    """A SessionUnit parked in IDLE with a live client, so send() skips spawn
    and reaches the try/except streaming block. Mocks ONLY the boundary."""
    u = _unit()
    u.state = SessionState.IDLE
    u._client = object()  # non-None → send() does NOT respawn

    async def _noop_slot():
        return

    u._await_streaming_slot = _noop_slot
    return u


@pytest.mark.asyncio
async def test_send_intercept_nonleak_fallthrough_reaches_retry():
    """Gate-2 #2 / the for/else regression: when the leak corrective-resume
    surfaces a NON-leak transient (OOM/rate-limit/network), send() MUST fall
    through to _retry_with_resume — NOT silently swallow it. This is the exact
    behavior the for/else footgun broke. Mutation: revert to for/else+return →
    _retry_with_resume is never called → this test goes RED."""
    u = _idle_unit_ready_to_send()

    async def _leak_then_raise(query):
        # First (and only) stream attempt: emit the leak RuntimeError.
        raise RuntimeError(LEAK_ERROR)
        yield  # pragma: no cover — make async-gen

    # Dispatcher yields a NON-leak fallthrough sentinel (e.g. an OOM surfaced
    # during the corrective resume). send() must NOT treat this as handled.
    async def _dispatch_nonleak_fallthrough(*a, **k):
        # A genuinely RETRIABLE non-leak transient (Bedrock overloaded) — so the
        # fall-through actually reaches _retry_with_resume. If swallowed by the
        # for/else footgun, retry is never called.
        yield {"_fallthrough_error": "Bedrock service overloaded, please retry"}

    retry_called = {"v": False}

    async def _fake_retry(query, options, config, error_str, tb_str):
        retry_called["v"] = True
        retry_called["error"] = error_str
        return
        yield  # async-gen

    u._streaming_orchestrator.stream_query = _leak_then_raise
    u._dispatch_leak_recovery = _dispatch_nonleak_fallthrough
    u._retry_with_resume = _fake_retry

    async for _ in u.send("q", _opts(), config=None):
        pass

    assert retry_called["v"] is True, (
        "non-leak error during leak recovery MUST fall through to _retry_with_resume "
        "(for/else footgun swallowed it)"
    )
    assert "overloaded" in retry_called.get("error", ""), (
        "the fallthrough error context must be the NON-leak transient, not the leak"
    )


@pytest.mark.asyncio
async def test_send_intercept_recovered_returns_without_retry():
    """Control: when the corrective resume SUCCEEDS (_recovered), send() returns
    and does NOT fall through to the generic retry path. Guards against the
    inverse over-correction (treating a recovered leak as a retriable error)."""
    u = _idle_unit_ready_to_send()

    async def _leak_then_raise(query):
        raise RuntimeError(LEAK_ERROR)
        yield  # pragma: no cover

    async def _dispatch_recovered(*a, **k):
        yield {"_recovered": True}

    retry_called = {"v": False}

    async def _fake_retry(*a, **k):
        retry_called["v"] = True
        return
        yield

    u._streaming_orchestrator.stream_query = _leak_then_raise
    u._dispatch_leak_recovery = _dispatch_recovered
    u._retry_with_resume = _fake_retry

    async for _ in u.send("q", _opts(), config=None):
        pass

    assert retry_called["v"] is False, (
        "a recovered leak must end the turn (return), NOT fall through to retry"
    )
