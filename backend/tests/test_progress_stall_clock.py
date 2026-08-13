"""Progress-clock stall detection — streaming_stall_seconds must measure REAL
content progress, not any-SDK-message liveness.

Root cause (the "前端卡死 30 分钟 / 后端假 streaming / slot 饱和" class):
``streaming_stall_seconds`` read ``_last_event_time``, which
``streaming_orchestrator`` refreshes on EVERY SDK message — including framing,
SystemMessage(init), and sub-agent internal noise. So a turn that produces ZERO
real content to the client but whose subprocess keeps the SDK pipe warm with
non-content messages kept the stall clock fresh forever → the lifecycle
watchdog never fired → STREAMING enum never released → spinner pinned AND the
daemon-wide ``_streaming_count`` slot stayed occupied (admission saturated).

Fix: a SEPARATE ``_last_progress_time`` clock, advanced ONLY on real-progress
events (text_delta / thinking_delta / AssistantMessage / sub-agent tool_result).
``streaming_stall_seconds`` reads THIS clock. ``_last_event_time`` is left
untouched (it feeds the PID watchdog + HealthSensor + dumb-spawn discriminator,
whose "any event = subprocess alive" semantics are correct and must stay wide).

Testing methodology: construct a real SessionUnit via __new__ (mirrors
test_output_liveness_watchdog._make_unit), drive the clocks directly, and assert
streaming_stall_seconds tracks progress-silence, not message-silence.

Validates: progress-clock root-cause fix (run_58e84e58).
"""
from __future__ import annotations

import asyncio
import time
import types

from core.session_unit import SessionState, SessionUnit


def _make_streaming_unit(session_id: str = "prog-test") -> SessionUnit:
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = SessionState.STREAMING
    unit._sdk_session_id = None
    unit._client = None
    unit._wrapper = None
    unit._hooks_enqueued = False
    unit._streaming_start_time = time.time()
    unit._last_event_time = time.time()
    unit._last_progress_time = time.time()
    unit._open_tool_uses = {}
    unit.last_used = time.time()
    return unit


def _make_thinking_orchestrator():
    """Build a StreamingOrchestrator with a minimal stubbed parent SessionUnit,
    sufficient to drive ``_read_formatted_response`` through a thinking_delta.

    Mirrors tests/test_tool_call_leak_guard.py::_make_orchestrator — only the
    SDK boundary is faked; the orchestrator's real logic (incl. :605) runs."""
    from core.streaming_orchestrator import StreamingOrchestrator

    parent = types.SimpleNamespace()
    parent.session_id = "prog-think-test"
    parent._client = None
    parent._sdk_session_id = "sdk-test"  # is_resume=True → no init required
    parent.state = SessionState.STREAMING
    parent.last_used = 0.0

    def _transition(new_state):
        parent.state = new_state

    parent._transition = _transition
    parent._content_emitted = False
    parent._streaming_start_time = time.time()
    parent._last_event_time = time.time()
    parent._last_progress_time = time.time()
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
    parent._emit_post_stream_metadata = lambda *a, **k: iter(())
    parent._maybe_build_elapsed_heartbeat = lambda: None
    parent._compute_message_timeout = lambda: 300.0
    # orchestrator first-message timeout calls _compute_init_timeout (run_4b74b764)
    parent._compute_init_timeout = lambda: 180.0
    parent._health_sensor = types.SimpleNamespace(
        record_activity=lambda: None,
        record_turn=lambda **k: None,
    )

    async def _noop_async(*a, **k):
        return None

    parent._check_rss_and_proactive_restart = _noop_async
    # (_check_context_soft_compact stub removed — method deleted run_2b1957f8)

    async def _kill():
        return None

    parent.kill = _kill

    return StreamingOrchestrator(parent)


def _drive_thinking(orch) -> tuple[list[dict], float]:
    """Feed a single real SDK StreamEvent(thinking_delta) through the real
    ``_read_formatted_response`` generator. Returns (yielded_events,
    progress_clock_snapshot_at_thinking_delta).

    The snapshot of ``_last_progress_time`` is captured the instant the
    thinking_delta is yielded — BEFORE any later content could advance the
    clock — so this isolates orchestrator:605. A single-event stream ends with
    no content_emitted, so the orchestrator's end-of-stream zombie detector
    raises RuntimeError; we tolerate it (the thinking_delta is yielded first),
    exactly as tests/test_tool_call_leak_guard.py::_drive tolerates it.

    Uses the real ``StreamEvent`` type so the orchestrator's
    ``isinstance(message, StreamEvent)`` check matches (a duck-typed fake would
    be silently skipped and the test would pass for the wrong reason)."""
    from claude_agent_sdk.types import StreamEvent

    thinking_evt = StreamEvent(
        uuid="evt-think-1",
        session_id="sdk-test",
        event={
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "weighing three approaches..."},
        },
    )

    async def _stream():
        yield thinking_evt

    class _Client:
        def receive_response(self):
            return _stream()

    orch._parent._client = _Client()

    async def _run():
        events = []
        snapshot = {"progress": None}
        try:
            async for ev in orch._read_formatted_response():
                events.append(ev)
                if ev.get("type") == "thinking_delta" and snapshot["progress"] is None:
                    # Capture the clock the instant thinking_delta is yielded,
                    # before any subsequent event could touch it.
                    snapshot["progress"] = orch._parent._last_progress_time
        except RuntimeError:
            # End-of-stream zombie detector (no content_emitted) — expected for a
            # thinking-only stream. The thinking_delta was already yielded.
            pass
        return events, snapshot["progress"]

    return asyncio.run(_run())


class TestProgressStallClock:
    def test_stall_measures_progress_not_message_liveness(self):
        """The crux: _last_event_time fresh (framing keeps pipe warm) but NO
        real progress for 400s → stall must report ~400s, NOT ~0s. Pre-fix this
        read _last_event_time and reported ~0 → watchdog never fired."""
        unit = _make_streaming_unit()
        now = time.time()
        # Framing/noise kept _last_event_time fresh ...
        unit._last_event_time = now
        # ... but real content stopped 400s ago.
        unit._last_progress_time = now - 400.0
        unit._streaming_start_time = now - 500.0

        stall = unit.streaming_stall_seconds
        assert stall is not None
        assert stall >= 395.0, (
            f"stall must track progress-silence (~400s), got {stall:.0f}s — "
            "regression: reading _last_event_time (message-liveness) again"
        )

    def test_active_progress_keeps_stall_low(self):
        """Real content flowing (progress clock fresh) → stall near 0, even if
        _last_event_time is old. A healthy fast turn must never be flagged."""
        unit = _make_streaming_unit()
        now = time.time()
        unit._last_event_time = now - 300.0  # old, irrelevant
        unit._last_progress_time = now       # content just flowed
        stall = unit.streaming_stall_seconds
        assert stall is not None and stall < 5.0

    def test_no_progress_yet_measures_from_stream_start(self):
        """Before the first progress event, fall back to _streaming_start_time
        (preserves the dumb-spawn 'no token since spawn' detection)."""
        unit = _make_streaming_unit()
        now = time.time()
        unit._last_progress_time = None
        unit._streaming_start_time = now - 120.0
        stall = unit.streaming_stall_seconds
        assert stall is not None and stall >= 115.0

    def test_not_streaming_returns_none(self):
        unit = _make_streaming_unit()
        unit.state = SessionState.IDLE
        assert unit.streaming_stall_seconds is None

    def test_thinking_delta_advances_progress_clock_via_real_orchestrator(self):
        """REGRESSION LOCK (Gate-2 #1, Kiro) — NON-VACUOUS: drives the REAL
        ``_read_formatted_response`` generator with a real SDK ``StreamEvent``
        carrying a ``thinking_delta``, and asserts the orchestrator advanced
        ``_last_progress_time``. This locks orchestrator:605 — neutralize that
        line (``pass``) and this test goes RED (mutation-verified, GUI07/PIT13).

        Why this matters: a pure extended-thinking phase (thinking_delta
        flowing, NO text yet, NO open tool) must keep the progress clock fresh.
        Opus 4.8 on a heavy prompt can think >300s before the first token; if
        the progress clock only tracked text/assistant (the ``_content_emitted``
        set), content-stall would fire at 300s and temper a HEALTHY long
        thinking turn to IDLE. Evidence this is real: a heavy cold-resume
        thought 90s+ before any output; only thinking_progress arrived.

        Methodology (GUI16): drive the function-under-change, mock only the SDK
        boundary — NOT the reader. The earlier version of this test hardcoded
        ``unit._last_progress_time = now - 0.5`` and asserted on
        ``streaming_stall_seconds`` (the READER), so mutating :605 (the WRITER)
        could not fail it — test-theater. This version closes that gap."""
        orch = _make_thinking_orchestrator()
        parent = orch._parent
        # Stale the progress clock so a real advance is unambiguous.
        stale = time.time() - 280.0
        parent._last_progress_time = stale
        parent._last_event_time = stale

        events, progress_at_thinking = _drive_thinking(orch)

        # The thinking_delta must have been yielded (the branch ran) ...
        assert any(e.get("type") == "thinking_delta" for e in events), (
            f"orchestrator did not yield a thinking_delta; got {[e.get('type') for e in events]}"
        )
        # ... AND :605 must have advanced the progress clock AT that moment.
        # Snapshot taken the instant thinking_delta was yielded — isolates :605,
        # so no later event can mask a mutation of that line.
        assert progress_at_thinking is not None and progress_at_thinking > stale + 100.0, (
            "thinking_delta did NOT advance _last_progress_time — regression: "
            f"thinking dropped from the real-progress set (orchestrator:605); "
            f"clock at thinking_delta = {progress_at_thinking}"
        )

    def test_thinking_phase_reads_low_stall(self):
        """Reader-side companion: with the progress clock fresh (as the real
        orchestrator leaves it after a thinking_delta) and _last_event_time old,
        streaming_stall_seconds must read ~0 — a live thinking turn is not a
        stall. (Locks the READER; the writer is locked by the test above.)"""
        unit = _make_streaming_unit()
        now = time.time()
        unit._open_tool_uses = {}            # no tool → open-tool guard cannot save it
        unit._streaming_start_time = now - 280.0
        unit._last_event_time = now - 280.0
        unit._last_progress_time = now - 0.5  # orchestrator advanced it on thinking_delta
        stall = unit.streaming_stall_seconds
        assert stall is not None and stall < 5.0, (
            f"a live thinking phase must read as ~0 stall, got {stall:.0f}s"
        )
