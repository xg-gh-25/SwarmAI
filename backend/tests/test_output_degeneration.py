"""Tests for streamed-output degeneration detection and no-progress notices.

What is tested
--------------
1. ``core.output_degeneration`` (pure functions):
   - ``DegenerationDetector`` fires on the repetition units seen in real
     runaway blocks (``━``, ``\\n\\ncourse``, ``core\\n\\n``, ``काए\\n\\n``) within
     2250 chars of the run start, fed in random-sized chunks like real deltas;
   - it stays silent on legitimate long output: a pipeline completion box
     built from ``━`` separator lines, and this repo's own source/docs files
     (a public stand-in for the private transcript corpus, which is replayed by
     ``scripts/degeneration_dataset_eval.py`` locally);
   - ``find_degenerate_run`` / ``strip_degenerate_runs`` /
     ``strip_degenerate_in_value`` cut only the repeated run;
   - ``ProgressWatch`` raises each notice once per no-output episode and never
     while a tool is running.
2. ``leak_transcript.clean_transcript_leaks`` truncates degenerate runs in a
   CLI transcript (text blocks and tool_use inputs) and keeps it valid JSONL.
3. ``StreamingOrchestrator._read_formatted_response`` on a degenerate stream:
   interrupts through the user-Stop path, yields the legit prefix + notice as an
   ``assistant`` event, ends with ``result`` (subtype ``output_degenerated``),
   flags the transcript for cleaning, and never sends a leak continuation.
4. The REAL ``SessionUnit.interrupt`` runs on that path (R28 forced execution).

Key invariants
--------------
- INV1: detection threshold has >3x margin on both sides of measured data.
- INV2: after a degeneration interrupt nothing after the generator's return
  runs — no ``client.query`` (leak continuation), no retry.
- INV3: transcript cleaning never changes line count, uuids, or JSON validity.
"""

from __future__ import annotations

import asyncio
import json
import random
import types
from pathlib import Path

import pytest

from core.output_degeneration import (
    DEGENERATION_NOTICE,
    DEGENERATION_TRANSCRIPT_MARKER,
    RATIO_THRESHOLD,
    STEP,
    WINDOW,
    DegenerationDetector,
    ProgressWatch,
    compress_ratio,
    find_degenerate_run,
    strip_degenerate_in_value,
    strip_degenerate_runs,
)

REPO = Path(__file__).resolve().parents[2]

# Repetition units taken from the real runaway blocks (only the repeated unit —
# none of the surrounding private content).
UNITS = ["━", "\n\ncourse", "core\n\n", "काए\n\n"]

LEGIT_PREFIX = (
    "━" * 60 + "\n✅ Pipeline COMPLETE: Bedrock thinking 网关代理链路验证(research)\n"
    "结论:GO。实测 3 条链路均可用,延迟中位数 1.2s,签名透传无损。\n"
)


def _feed_chunks(det: DegenerationDetector, text: str, seed: int = 7):
    """Feed text in random 1-40 char chunks; return chars fed when it fired."""
    rng = random.Random(seed)
    i = 0
    while i < len(text):
        n = rng.randint(1, 40)
        if det.feed(text[i:i + n]):
            return i + n
        i += n
    return None


def _completion_box() -> str:
    sep = "━" * 60
    body = []
    for k in range(12):
        body.append(sep)
        body.append(f"## ✦ ⑤ BUILD 第 {k} 轮 → TDD: 3R→3G | 18 tests, 0 failed")
        body.append(f"  • 文件 backend/core/module_{k}.py 改动 +{k * 7} / -{k * 3},覆盖 AC{k % 7 + 1}")
        body.append("  • Gate-2: 2 findings (1 MED, 1 LOW) → 2 fixed, 0 remaining")
    body.append(sep)
    return "\n".join(body)


# ── 1. Pure detector ──────────────────────────────────────────────────────


@pytest.mark.parametrize("unit", UNITS)
def test_detector_fires_on_real_units_soon_after_run_start(unit):
    junk = unit * (6000 // len(unit))
    fired_at = _feed_chunks(DegenerationDetector(), LEGIT_PREFIX + junk)
    assert fired_at is not None, f"degenerate {unit!r} run not detected"
    assert fired_at - len(LEGIT_PREFIX) <= 2250, (
        f"{unit!r}: detected {fired_at - len(LEGIT_PREFIX)} chars after run start"
    )


def test_detector_fires_on_run_with_prefix_longer_than_window():
    prefix = "这是一段正常的中文分析文字,包含不同的词汇与数字 %d。\n"
    legit = "".join(prefix % k for k in range(120))  # > WINDOW chars, low ratio
    assert len(legit) > WINDOW
    fired_at = _feed_chunks(DegenerationDetector(), legit + "━" * 6000)
    assert fired_at is not None
    assert fired_at - len(legit) <= 2250


def test_detector_silent_on_completion_box_with_separator_lines():
    box = _completion_box()
    assert len(box) > WINDOW
    assert _feed_chunks(DegenerationDetector(), box) is None


def test_detector_needs_full_window():
    # 1500 chars of junk never trigger — too short to judge (a long separator
    # line in a table must not be mistaken for a loop).
    assert _feed_chunks(DegenerationDetector(), "━" * 1500) is None


def _repo_corpus() -> list[Path]:
    files = sorted((REPO / "backend" / "core").rglob("*.py"))
    files += sorted((REPO / "docs").glob("*.md"))
    return [p for p in files if p.stat().st_size >= WINDOW * 2]


def test_detector_silent_on_repo_source_and_docs():
    corpus = _repo_corpus()
    assert len(corpus) > 50, "public legit corpus unexpectedly small"
    fired = []
    for p in corpus:
        text = p.read_text(encoding="utf-8", errors="replace")
        if _feed_chunks(DegenerationDetector(), text) is not None:
            fired.append(str(p.relative_to(REPO)))
    assert not fired, f"false positives on legit files: {fired}"


def test_threshold_margin_documented_values():
    # The constants the measurements were taken with — changing them requires
    # re-running scripts/degeneration_dataset_eval.py.
    assert (WINDOW, STEP, RATIO_THRESHOLD) == (2000, 250, 15.0)
    assert compress_ratio("━" * WINDOW) > 40
    # A deliberately templated box (rows differ only by digits) is the worst
    # legit shape we could construct — it still stays well under the line.
    assert compress_ratio(_completion_box()[:WINDOW]) < RATIO_THRESHOLD / 1.4


def test_templated_markdown_table_stays_below_threshold():
    """Near-identical rows (a model listing files/results) are the closest
    legit shape to a loop; they must not trigger."""
    rows = ["| # | file | + | - | AC |", "|---|---|---|---|---|"]
    rows += [f"| {k} | backend/core/mod_{k}.py | +{k * 3} | -{k} | AC{k % 7 + 1} |" for k in range(120)]
    table = "\n".join(rows)
    assert len(table) > WINDOW
    assert _feed_chunks(DegenerationDetector(), table) is None


# ── 1b. Run location + stripping ──────────────────────────────────────────


@pytest.mark.parametrize("unit", UNITS)
def test_find_run_starts_at_end_of_legit_prefix(unit):
    text = LEGIT_PREFIX + unit * (5000 // len(unit))
    run = find_degenerate_run(text)
    assert run is not None
    start, end = run
    assert end == len(text)
    # The cut lands within one unit of the boundary: a trailing char of the
    # prefix that equals the unit's tail (a newline) is indistinguishable from
    # the run and may be cut with it.
    assert len(LEGIT_PREFIX) - len(unit) <= start <= len(LEGIT_PREFIX) + len(unit)
    assert find_degenerate_run(text[:start]) is None


def test_find_run_none_on_legit():
    assert find_degenerate_run(_completion_box()) is None
    assert find_degenerate_run("") is None


def test_strip_runs_keeps_text_after_the_run():
    tail = "\n\n后面的正常内容:总结与下一步。"
    text = LEGIT_PREFIX + "काए\n\n" * 1500 + tail
    out = strip_degenerate_runs(text, "[cut]")
    assert out.startswith(LEGIT_PREFIX[:-1])  # trailing "\n" may go with the run
    assert "[cut]" in out
    assert out.endswith(tail)
    assert "काए\n\nकाए" not in out


def test_strip_runs_handles_approximate_repetition():
    rng = random.Random(3)
    junk = "".join(rng.choice(["काए\n\n", "काए\n\n", "काए\n\n", "का\n\n"]) for _ in range(1500))
    text = LEGIT_PREFIX + junk
    out = strip_degenerate_runs(text, "[cut]")
    assert out.startswith(LEGIT_PREFIX[: len(LEGIT_PREFIX) - 1])
    assert len(out) < len(LEGIT_PREFIX) + STEP + 10
    assert find_degenerate_run(out) is None


def test_strip_runs_handles_many_runs_and_does_not_rescan_cut_text():
    """Several separate loops are each replaced once; legit text between them
    survives; an earlier marker is never re-judged."""
    seg = "\n\n正常段落:结论 %d,下一步继续核对。\n\n"
    text = "".join(seg % k + ("━" if k % 2 else "core\n\n") * 2500 for k in range(6))
    out = strip_degenerate_runs(text, "[cut]")
    assert out.count("[cut]") == 6
    for k in range(6):
        assert (seg % k).strip() in out
    assert find_degenerate_run(out) is None


def test_detector_check_cost_does_not_grow_with_block_length():
    """A check joins only the trailing window, not the whole block."""
    det = DegenerationDetector()
    body = "".join(f"line {k}: value={k * 7 % 13} ok\n" for k in range(40000))
    for i in range(0, len(body), 30):
        det.feed(body[i:i + 30])
    assert len(det._parts) > 1000  # parts are not re-joined on every check
    assert det.fired is False


def test_strip_runs_identity_on_legit():
    box = _completion_box()
    assert strip_degenerate_runs(box, "[cut]") is box


def test_strip_in_value_recurses_and_preserves_identity():
    clean = {"file_path": "/tmp/a.txt", "content": "short"}
    assert strip_degenerate_in_value(clean, "[cut]") is clean
    dirty = {"file_path": "/tmp/a.txt", "edits": [{"new_string": "ok\n" + "━" * 4000}]}
    out = strip_degenerate_in_value(dirty, "[cut]")
    assert out["file_path"] == "/tmp/a.txt"
    assert out["edits"][0]["new_string"] == "ok\n[cut]"
    assert dirty["edits"][0]["new_string"].count("━") == 4000  # input not mutated


# ── 1c. ProgressWatch ─────────────────────────────────────────────────────


def test_progress_watch_thinking_notice_once_per_episode():
    w = ProgressWatch(now=0.0)
    for t in range(1, 299):
        w.on_event(float(t))
        w.on_thinking_tokens(float(t), delta=40)
        assert w.check(float(t), tools_open=False) is None
    w.on_event(301.0)
    w.on_thinking_tokens(301.0, delta=40)
    note = w.check(301.0, tools_open=False)
    assert note is not None and "思考" in note
    assert w.check(330.0, tools_open=False) is None  # once per episode
    w.on_visible(400.0)  # model produced output → re-armed
    w.on_thinking_tokens(705.0, delta=10)
    w.on_event(705.0)
    assert w.check(705.0, tools_open=False) is not None


def test_progress_watch_silent_notice_and_tool_suppression():
    w = ProgressWatch(now=0.0)
    assert w.check(400.0, tools_open=True) is None  # a running tool is not a stall
    note = w.check(400.0, tools_open=False)
    assert note is not None and "事件" in note
    assert w.check(500.0, tools_open=False) is None


def test_progress_watch_thinking_then_silent_gives_each_notice_once():
    from core.output_degeneration import SILENT_STALL_S

    w = ProgressWatch(now=0.0)
    w.on_thinking_tokens(10.0, delta=100)
    w.on_event(10.0)
    assert w.check(10.0 + SILENT_STALL_S - 1, tools_open=False) is None
    first = w.check(10.0 + SILENT_STALL_S + 1, tools_open=False)
    assert first is not None and "事件" in first  # thinking stopped long ago → silence
    assert w.check(900.0, tools_open=False) is None


def test_silent_notice_precedes_message_timeout_floor():
    """The silence notice must land before the orchestrator's hang guard (the
    message timeout, floor 300s) can end the wait."""
    from core.output_degeneration import SILENT_STALL_S
    from core.session_unit import SessionUnit

    unit = SessionUnit(session_id="test-degen-timeout-floor", agent_id="default")
    floor = unit._compute_message_timeout()  # fresh unit = smallest context
    assert SILENT_STALL_S + 30.0 <= floor  # + one heartbeat tick of slack


def test_silent_note_does_not_stick_once_events_resume():
    from core.output_degeneration import SILENT_STALL_S

    w = ProgressWatch(now=0.0)
    assert w.check(SILENT_STALL_S + 1, tools_open=False) is not None
    assert "事件" in w.stall_note(SILENT_STALL_S + 2)
    w.on_event(SILENT_STALL_S + 5)  # thinking frames arrive again
    w.on_thinking_tokens(SILENT_STALL_S + 5, delta=50)
    note = w.stall_note(SILENT_STALL_S + 6)
    assert note is None or "事件" not in note


# ── 2. Transcript cleaning ────────────────────────────────────────────────


def test_clean_transcript_truncates_degenerate_text_and_tool_input(tmp_path):
    from core.leak_transcript import clean_transcript_leaks

    recs = [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "写总结"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": LEGIT_PREFIX + "━" * 9000}]}},
        {"type": "assistant", "uuid": "a2", "parentUuid": "a1",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Write",
              "input": {"file_path": "/tmp/x", "content": "head\n" + "core\n\n" * 1200}}]}},
        {"type": "assistant", "uuid": "a3", "parentUuid": "a2",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": _completion_box()}]}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")

    assert clean_transcript_leaks(p) == 2
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(recs)
    out = [json.loads(x) for x in lines]
    assert [r["uuid"] for r in out] == ["u1", "a1", "a2", "a3"]
    assert [r["parentUuid"] for r in out] == [None, "u1", "a1", "a2"]
    text = out[1]["message"]["content"][0]["text"]
    assert text.startswith(LEGIT_PREFIX.rstrip("\n"))
    assert DEGENERATION_TRANSCRIPT_MARKER in text and "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" not in text[len(LEGIT_PREFIX):]
    content = out[2]["message"]["content"][0]["input"]["content"]
    assert content.startswith("head") and DEGENERATION_TRANSCRIPT_MARKER in content
    assert out[3] == recs[3]  # legit block untouched
    assert clean_transcript_leaks(p) == 0  # idempotent


# ── 3. Orchestrator wiring ────────────────────────────────────────────────


def _text_stream(text, index=0, chunk=37, parent=None):
    from claude_agent_sdk.types import StreamEvent

    evs = [StreamEvent(uuid="u", session_id="s", parent_tool_use_id=parent, event={
        "type": "content_block_start", "index": index,
        "content_block": {"type": "text", "text": ""}})]
    for i in range(0, len(text), chunk):
        evs.append(StreamEvent(uuid="u", session_id="s", parent_tool_use_id=parent, event={
            "type": "content_block_delta", "index": index,
            "delta": {"type": "text_delta", "text": text[i:i + chunk]}}))
    return evs


def _tool_stream(name, partial_json, index=1, chunk=41):
    from claude_agent_sdk.types import StreamEvent

    evs = [StreamEvent(uuid="u", session_id="s", event={
        "type": "content_block_start", "index": index,
        "content_block": {"type": "tool_use", "id": "toolu_x", "name": name, "input": {}}})]
    for i in range(0, len(partial_json), chunk):
        evs.append(StreamEvent(uuid="u", session_id="s", event={
            "type": "content_block_delta", "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json[i:i + chunk]}}))
    return evs


def _result():
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=10, is_error=False,
        num_turns=1, session_id="sdk-test", stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5}, total_cost_usd=0.0,
    )


def _make_orchestrator():
    from tests.test_tool_call_leak_guard import _make_orchestrator as base

    orch = base()
    parent = orch._parent
    # permission_manager keeps one module-level asyncio.Queue per session id,
    # bound to the first event loop that waits on it; each test here runs its
    # own asyncio.run(). A per-test id keeps tests from sharing a stale-loop
    # queue (with a shared id the orchestrator's race loop spins forever).
    import uuid

    parent.session_id = f"sess-degen-{uuid.uuid4().hex[:8]}"
    parent._transcript_poisoned = False
    parent._user_stopped_current_turn = False
    parent._streaming_start_time = 0.0
    calls = {"n": 0}

    async def _interrupt(timeout: float = 5.0, autonomous: bool = False):
        calls["n"] += 1
        parent._user_stopped_current_turn = not autonomous
        return True

    parent.interrupt = _interrupt
    parent._interrupt_calls = calls
    return orch


def _drive(orch, messages):
    from tests.test_tool_call_leak_guard import _drive as base

    return asyncio.run(base(orch, messages, followups=[[_result()]]))


def _assistant_texts(events):
    return [
        b.get("text", "")
        for ev in events if ev.get("type") == "assistant"
        for b in ev.get("content", []) if b.get("type") == "text"
    ]


def test_orchestrator_interrupts_degenerate_text_and_ends_turn():
    orch = _make_orchestrator()
    msgs = _text_stream(LEGIT_PREFIX + "━" * 20000) + [_result()]
    events, raised = _drive(orch, msgs)

    assert raised is None
    assert orch._parent._interrupt_calls["n"] == 1
    assert orch._parent._user_stopped_current_turn is True
    assert orch._parent._transcript_poisoned is True
    texts = _assistant_texts(events)
    assert texts, "the legit prefix + notice must be yielded as assistant content"
    final = texts[-1]
    assert final.startswith(LEGIT_PREFIX.rstrip("\n"))
    assert final.endswith(DEGENERATION_NOTICE)
    assert events[-1]["type"] == "result"
    assert events[-1]["subtype"] == "output_degenerated"
    # Forwarding stopped at detection: far less junk streamed than produced.
    streamed = "".join(e.get("text", "") for e in events if e.get("type") == "text_delta")
    assert len(streamed) < len(LEGIT_PREFIX) + 2600
    assert orch._parent._client.queries == []  # no continuation / retry


def test_orchestrator_interrupts_degenerate_tool_input():
    orch = _make_orchestrator()
    partial = json.dumps({"file_path": "/tmp/o.txt", "content": "alpha\n" * 3000})
    events, raised = _drive(orch, _tool_stream("Write", partial) + [_result()])
    assert raised is None
    assert orch._parent._interrupt_calls["n"] == 1
    final = _assistant_texts(events)[-1]
    assert DEGENERATION_NOTICE in final and "Write" in final
    assert events[-1]["subtype"] == "output_degenerated"


def test_orchestrator_no_leak_continuation_after_degeneration():
    """A leaked tool call earlier in the response arms the leak continuation;
    the degeneration interrupt must still end the turn with no client.query."""
    from claude_agent_sdk import AssistantMessage, TextBlock
    from tests._tool_markup import tc

    orch = _make_orchestrator()
    leak = tc('look\n\ncall\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>')
    msgs = [AssistantMessage(content=[TextBlock(text=leak)], model="m")]
    msgs += _text_stream("core\n\n" * 3000, index=1) + [_result()]
    events, raised = _drive(orch, msgs)
    assert raised is None
    assert orch._parent._client.queries == []
    assert events[-1]["subtype"] == "output_degenerated"


def test_orchestrator_passes_long_legit_stream():
    orch = _make_orchestrator()
    box = _completion_box()
    events, raised = _drive(orch, _text_stream(box) + [_result()])
    assert raised is None
    assert orch._parent._interrupt_calls["n"] == 0
    assert orch._parent._transcript_poisoned is False
    assert events[-1]["type"] == "result" and events[-1]["subtype"] == "success"


def test_orchestrator_sidechain_and_main_blocks_do_not_mix():
    """Same content-block index from a sub-agent and the main thread are
    tracked separately — interleaved legit text must not combine into a
    false window, and junk in one must not hide behind the other."""
    orch = _make_orchestrator()
    main = _text_stream(_completion_box(), index=0)
    side = _text_stream(_completion_box()[::-1], index=0, parent="toolu_agent")
    mixed = [main[0], side[0]]
    for a, b in zip(main[1:], side[1:]):
        mixed += [a, b]
    events, raised = _drive(orch, mixed + [_result()])
    assert orch._parent._interrupt_calls["n"] == 0


def test_orchestrator_thinking_stall_notice(monkeypatch):
    from claude_agent_sdk import SystemMessage
    import core.streaming_orchestrator as so

    clock = {"t": 1000.0}
    monkeypatch.setattr(so.time, "time", lambda: clock["t"])
    orch = _make_orchestrator()

    frames = [
        SystemMessage(subtype="thinking_tokens",
                      data={"estimated_tokens": 400 * (k + 1), "estimated_tokens_delta": 400})
        for k in range(12)
    ]

    class _Client:
        queries: list = []

        async def query(self, prompt):
            self.queries.append(prompt)

        def receive_response(self):
            async def _s():
                for f in frames:
                    clock["t"] += 30.0  # 30s of invisible thinking per frame
                    yield f
                yield _result()
            return _s()

    orch._parent._client = _Client()

    async def _run():
        return [ev async for ev in orch._read_formatted_response()]

    events = asyncio.run(_run())
    stalls = [e for e in events if e.get("type") == "still_working" and "思考" in e.get("message", "")]
    assert len(stalls) == 1, stalls
    assert stalls[0]["stall"] is True
    # Never conversation content: nothing persisted as if the model said it.
    assert not [t for t in _assistant_texts(events) if "思考" in t]
    assert orch._parent._interrupt_calls["n"] == 0  # notice only, never a stop
    assert events[-1]["type"] == "result" and events[-1]["subtype"] == "success"


def test_heartbeat_keeps_showing_a_reported_stall(monkeypatch):
    """The generic still_working heartbeat shares the toast slot; while a stall
    is reported it must carry the stall text, not overwrite it."""
    from claude_agent_sdk import SystemMessage
    import core.streaming_orchestrator as so

    clock = {"t": 1000.0}
    monkeypatch.setattr(so.time, "time", lambda: clock["t"])
    orch = _make_orchestrator()
    orch._parent._maybe_build_elapsed_heartbeat = lambda: {
        "type": "still_working", "elapsedSeconds": 1, "message": "Still working — 6m elapsed on this step."}
    frames = [SystemMessage(subtype="thinking_tokens", data={"estimated_tokens_delta": 100}) for _ in range(14)]

    class _Client:
        queries: list = []

        async def query(self, prompt):
            self.queries.append(prompt)

        def receive_response(self):
            async def _s():
                for f in frames:
                    clock["t"] += 30.0
                    yield f
                yield _result()
            return _s()

    orch._parent._client = _Client()

    async def _run():
        return [ev async for ev in orch._read_formatted_response()]

    events = asyncio.run(_run())
    sw = [e for e in events if e.get("type") == "still_working"]
    first_stall = next(i for i, e in enumerate(sw) if e.get("stall"))
    assert first_stall < len(sw) - 1
    assert all("思考" in e["message"] for e in sw[first_stall:])  # never reverts to generic text


def test_abort_prefix_strips_hidden_leak_markup():
    """The live stream hides a leaked tool call from the UI; the abort prefix
    must not put it back into the persisted notice."""
    from tests._tool_markup import tc

    orch = _make_orchestrator()
    leak = tc('分析如下\ncall\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>\n')
    events, raised = _drive(orch, _text_stream(leak + "core\n\n" * 3000) + [_result()])
    final = _assistant_texts(events)[-1]
    assert DEGENERATION_NOTICE in final
    assert tc("‹invoke") not in final and tc("‹parameter") not in final
    assert "分析如下" in final


def test_drift_edge_does_not_eat_legit_text_next_to_run():
    text = "Line one.\n" + "━" * 40 + "\n" + "━" * 3000 + "\nSummary of the result."
    out = strip_degenerate_runs(text, "[M]")
    assert out.startswith("Line one.")
    assert out.endswith("\nSummary of the result.")


def test_detector_reset_on_any_block_start_and_no_json_prefix():
    """An input_json_delta for a non-tool_use block at a reused index must not
    land in the previous text block's detector, and raw JSON is never shown."""
    from claude_agent_sdk.types import StreamEvent

    orch = _make_orchestrator()
    msgs = _text_stream("正常的一段话。" * 20, index=0)
    msgs.append(StreamEvent(uuid="u", session_id="s", event={
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "server_tool_use", "id": "srv", "name": "web_search", "input": {}}}))
    partial = json.dumps({"query": "x" * 9000})
    for i in range(0, len(partial), 41):
        msgs.append(StreamEvent(uuid="u", session_id="s", event={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": partial[i:i + 41]}}))
    events, raised = _drive(orch, msgs + [_result()])
    final = _assistant_texts(events)[-1]
    assert final == DEGENERATION_NOTICE  # no prefix from the earlier text block, no JSON
    assert '"query"' not in final


def test_abort_touches_no_parent_turn_state_after_interrupt():
    """Once interrupt() reaches IDLE the drain worker may start the next
    send() on this unit; the aborted generator must not write its fields."""
    orch = _make_orchestrator()
    parent = orch._parent
    parent._streaming_start_time = 500.0

    async def _interrupt(timeout: float = 5.0, autonomous: bool = False):
        parent._interrupt_calls["n"] += 1
        parent._user_stopped_current_turn = True
        # A new send() started meanwhile:
        parent._content_emitted = False
        parent._streaming_start_time = 10**9
        return True

    parent.interrupt = _interrupt
    events, _ = _drive(orch, _text_stream("━" * 20000) + [_result()])
    assert parent._content_emitted is False  # the new turn's flag untouched
    res = events[-1]
    assert res["subtype"] == "output_degenerated"
    assert res["duration_ms"] > 0  # computed from the aborted turn's own start


def test_interrupt_runs_while_stream_keeps_being_drained():
    """The SDK buffers at most 100 messages; the abort must keep reading the
    stream while interrupt() is in flight, like a real user Stop."""
    from claude_agent_sdk.types import StreamEvent

    orch = _make_orchestrator()
    parent = orch._parent
    drained = {"n": 0}

    def _gen_events():
        yield from _text_stream("━" * 3000)
        while True:  # the model keeps looping until stopped
            yield StreamEvent(uuid="u", session_id="s", event={
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "━" * 30}})

    class _Client:
        queries: list = []

        async def query(self, prompt):
            self.queries.append(prompt)

        def receive_response(self):
            async def _s():
                for m in _gen_events():
                    if parent._interrupt_calls["n"]:
                        drained["n"] += 1
                    yield m
                    await asyncio.sleep(0)
            return _s()

    async def _interrupt(timeout: float = 5.0, autonomous: bool = False):
        parent._interrupt_calls["n"] += 1
        while drained["n"] < 150:  # completes only if someone keeps reading
            await asyncio.sleep(0)
        return True

    parent.interrupt = _interrupt
    parent._client = _Client()

    async def _run():
        return [ev async for ev in orch._read_formatted_response()]

    events = asyncio.run(asyncio.wait_for(_run(), timeout=20))
    assert drained["n"] >= 150
    assert events[-1]["subtype"] == "output_degenerated"


def test_aborted_turn_usage_is_still_recorded():
    """The real ResultMessage read while draining carries the spent tokens."""
    from claude_agent_sdk.types import StreamEvent

    orch = _make_orchestrator()
    parent = orch._parent
    recorded = []
    orch._record_token_usage_bg = lambda m, u: recorded.append(u)
    gate = {"open": False}

    class _Client:
        queries: list = []

        async def query(self, prompt):
            self.queries.append(prompt)

        def receive_response(self):
            async def _s():
                for m in _text_stream("━" * 3000):
                    yield m
                while not gate["open"]:
                    await asyncio.sleep(0)
                gate["result_sent"] = True
                yield _result()  # the CLI's own result after the interrupt
            return _s()

    async def _interrupt(timeout: float = 5.0, autonomous: bool = False):
        # The CLI answers the interrupt and emits its ResultMessage while the
        # control call is in flight — model that ordering deterministically.
        parent._interrupt_calls["n"] += 1
        gate["open"] = True
        while not gate.get("result_sent"):
            await asyncio.sleep(0)
        for _ in range(20):
            await asyncio.sleep(0)
        return True

    parent.interrupt = _interrupt
    parent._client = _Client()

    async def _run():
        return [ev async for ev in orch._read_formatted_response()]

    events = asyncio.run(asyncio.wait_for(_run(), timeout=20))
    assert events[-1]["subtype"] == "output_degenerated"
    assert recorded and recorded[0]["output_tokens"] == 5


# ── 4. Real interrupt path (R28 forced execution) ─────────────────────────


def test_real_session_unit_interrupt_runs_on_degeneration():
    from core.session_unit import SessionState, SessionUnit

    unit = SessionUnit(session_id="test-degen-real-interrupt", agent_id="default")
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    unit._sdk_session_id = "sdk-degen"
    unit._streaming_start_time = 0.0
    msgs = _text_stream(LEGIT_PREFIX + "━" * 20000) + [_result()]

    class _Client:
        def __init__(self):
            self.interrupts = 0
            self.queries = []

        async def interrupt(self):
            self.interrupts += 1

        async def query(self, prompt):
            self.queries.append(prompt)

        def receive_response(self):
            async def _s():
                for m in msgs:
                    yield m
            return _s()

    client = _Client()
    unit._client = client

    async def _run():
        out = []
        async for ev in unit._streaming_orchestrator._read_formatted_response():
            out.append(ev)
        return out

    events = asyncio.run(_run())
    assert client.interrupts == 1
    assert client.queries == []
    assert unit._user_stopped_current_turn is True
    assert unit._transcript_poisoned is True
    assert unit.state == SessionState.COLD
    assert unit._sdk_session_id  # --resume identity kept (not cleared)
    assert events[-1]["subtype"] == "output_degenerated"
