"""The loop guard counts a repeated call whose result changes as progress, not a loop.

Design: run_f8f6abd5 (DESIGN_SPEC A1). A recorded call's identity is (tool, input hash)
until its result arrives, then (tool, input hash + normalized result fingerprint). Every
layer that compares calls (consecutive repeat, diversity stall, set-overlap, single-tool
repeat, the rolling baseline) compares that identity; thresholds are unchanged.

Questions this answers:
  - seven identical resumable commands whose results show new progress: no escalation
    (fails on the base: it reached KILL);
  - seven identical calls with one result: SOFT/HARD/KILL at today's counts;
  - durations, clock times, sizes and ids are noise, other numbers are progress;
  - a call whose result has not arrived (or never does) is compared on input only;
  - a result the guard did not record a call for (a sub-agent's) is ignored;
  - the orchestrator's real read loop hands the main agent's results to the guard.
"""
from __future__ import annotations

import pytest

from core.compaction_guard import CompactionGuard, EscalationLevel, GuardPhase

CMD = {"command": "python -m scripts.expert_bench run --cases cases.jsonl --out results.jsonl"}


def _round(g: CompactionGuard, i: int, tool: str, inp, result) -> EscalationLevel:
    """One main-agent call: record it, check, then deliver its result."""
    tid = f"t{i}"
    g.record_tool_call(tool, inp, tool_use_id=tid)
    level = g.check()
    if result is not None:
        g.record_tool_result(tid, result)
    return level


def test_changing_results_are_progress_not_a_loop():
    g = CompactionGuard()
    levels = [_round(g, i, "Bash", CMD, f"recorded {100 + 30 * i} of 2436 reviews") for i in range(12)]
    assert set(levels) == {EscalationLevel.MONITORING}


def test_identical_results_still_escalate_at_todays_counts():
    g = CompactionGuard()
    levels = [_round(g, i, "Bash", CMD, "FAILED tests/test_a.py::test_x - assert 1 == 2") for i in range(7)]
    assert levels[2] == EscalationLevel.SOFT_WARN
    # the grace window holds the level for 3 calls after an escalation, as today
    assert EscalationLevel.KILL not in levels[:6]
    g2 = CompactionGuard()
    for i in range(7):
        g2.record_tool_call("Bash", CMD, tool_use_id=f"t{i}")
        g2.record_tool_result(f"t{i}", "same output")
    assert g2._consec_count == 7 and g2._is_consecutive_repeat() == EscalationLevel.KILL
    g3 = CompactionGuard()
    for i in range(10):
        g3.record_tool_call("Read", {"file_path": "/x"}, tool_use_id=f"r{i}")
        g3.record_tool_result(f"r{i}", "same file text")
    assert g3._is_consecutive_repeat() == EscalationLevel.KILL


def test_timing_noise_is_not_progress():
    g = CompactionGuard()
    for i in range(7):
        g.record_tool_call("Bash", CMD, tool_use_id=f"t{i}")
        g.record_tool_result(
            f"t{i}",
            f"1 failed in {1.1 + i * 0.37:.2f}s at 2026-11-19T04:0{i}:12Z pid={4000 + i} "
            f"session {i:x}deadbeef{i} Peak RSS: {130 + i} MB took {200 + i}ms 10:4{i}:0{i}")
    assert g._consec_count == 7
    assert g._is_consecutive_repeat() == EscalationLevel.KILL
    # a count that is not a duration, time, size or id is progress: the run never grows
    # past the call in flight and the one before it (matched on input), below SOFT_WARN
    h = CompactionGuard()
    streaks = []
    for i in range(7):
        h.record_tool_call("Bash", CMD, tool_use_id=f"t{i}")
        streaks.append(h._consec_count)
        h.record_tool_result(f"t{i}", f"{i * 10} cases done")
    assert max(streaks) == 2


def test_pending_or_missing_result_compares_by_input():
    g = CompactionGuard()
    for i in range(7):   # results never arrive: today's behaviour
        g.record_tool_call("Bash", CMD, tool_use_id=f"t{i}")
    assert g._consec_count == 7 and g._is_consecutive_repeat() == EscalationLevel.KILL
    h = CompactionGuard()
    for i in range(6):
        h.record_tool_call("Bash", CMD, tool_use_id=f"t{i}")
        h.record_tool_result(f"t{i}", "same")
    h.record_tool_call("Bash", CMD, tool_use_id="t6")   # in flight: matched on input
    assert h._consec_count == 7


def test_stall_window_counts_new_results_as_distinct():
    calls = [("Read", {"file_path": "/a"}), ("Read", {"file_path": "/b"}), ("Bash", CMD)]
    progressing, stuck = CompactionGuard(), CompactionGuard()
    for i in range(40):
        tool, inp = calls[i % 3]
        progressing.record_tool_call(tool, inp, tool_use_id=f"p{i}")
        progressing.record_tool_result(f"p{i}", f"step {i}")
        stuck.record_tool_call(tool, inp, tool_use_id=f"s{i}")
        stuck.record_tool_result(f"s{i}", "unchanged")
    assert progressing._is_stalled()[0] is False and progressing._is_hard_stalled() is False
    assert stuck._is_stalled()[0] is True and stuck._is_hard_stalled() is True


def test_set_overlap_and_single_repeat_use_results():
    def guard(before: str, after) -> CompactionGuard:
        g = CompactionGuard()
        for i in range(6):
            g.record_tool_call("Bash", CMD, tool_use_id=f"a{i}")
            g.record_tool_result(f"a{i}", before)
        g.activate()
        for i in range(6):
            g.record_tool_call("Bash", CMD, tool_use_id=f"b{i}")
            g.record_tool_result(f"b{i}", after(i))
        return g
    moving = guard("before", lambda i: f"after {i}")
    assert moving._phase == GuardPhase.ACTIVE and moving._detect_loop() is False
    same = guard("before", lambda i: "before")
    assert same._detect_loop() is True


def test_sub_agent_results_are_ignored():
    g = CompactionGuard()
    for i in range(6):
        g.record_tool_call("Bash", CMD, tool_use_id=f"t{i}")
        g.record_tool_result(f"t{i}", "same")
    g.record_tool_result("sub-agent-call", "a different result")   # never recorded by the parent
    g.record_tool_result(None, "no id")
    assert g._consec_count == 6
    assert all(":" not in h or h.endswith(g._recent_calls[0][1].split(":")[1])
               for _t, h in g._recent_calls)


def test_result_recording_errors_never_raise(monkeypatch):
    g = CompactionGuard()
    g.record_tool_call("Bash", CMD, tool_use_id="t0")
    monkeypatch.setattr(CompactionGuard, "_fingerprint", staticmethod(lambda *a: 1 / 0))
    g.record_tool_result("t0", "x")          # logged, not raised
    assert g._recent_calls[-1] == ("Bash", CompactionGuard._hash_input(CMD))
    assert g.check() == EscalationLevel.MONITORING


def test_heuristic_compaction_detection_is_unchanged():
    from unittest.mock import patch
    g = CompactionGuard()
    g.record_tool_call("Bash", CMD, tool_use_id="t0")
    g.record_tool_result("t0", "out")
    with patch("core.prompt_builder.PromptBuilder.get_model_context_window", return_value=200_000):
        g.update_context_usage(160_000, "model")
        assert g._phase == GuardPhase.PASSIVE
        g.update_context_usage(40_000, "model")      # a 60-point drop, as today
    assert g._phase == GuardPhase.ACTIVE
    assert ("Bash", CompactionGuard._hash_input(CMD) + ":" + CompactionGuard._fingerprint("out", False)) \
        in g._pre_compaction_set


def test_reset_all_forgets_pending_results():
    g = CompactionGuard()
    g.record_tool_call("Bash", CMD, tool_use_id="t0")
    g.reset_all()
    g.record_tool_result("t0", "late")
    assert g._recent_calls == [] and g._consec_count == 0 and g._last_pair is None


@pytest.mark.asyncio
async def test_orchestrator_hands_main_agent_results_to_the_guard():
    from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage

    from tests.test_tool_call_leak_guard import _drive, _make_orchestrator, _make_result_message

    orch = _make_orchestrator()
    guard = CompactionGuard()
    orch._parent._compaction_guard = guard
    messages = []
    for i in range(8):
        messages += [AssistantMessage(content=[ToolUseBlock(id=f"t{i}", name="Bash", input=CMD)], model="m"),
                     UserMessage(content=[ToolResultBlock(tool_use_id=f"t{i}",
                                                          content=f"recorded {i * 40} reviews",
                                                          is_error=False)])]
    messages.append(_make_result_message())
    events, raised = await _drive(orch, messages)
    assert raised is None
    assert not any(e.get("type") == "compaction_guard" and e.get("subtype") in ("hard_warn", "kill")
                   for e in events)
    assert guard._escalation == EscalationLevel.MONITORING
    assert all(":" in h for _t, h in guard._recent_calls)    # every result reached the guard


def test_a_failing_pytest_rerun_still_reaches_kill_through_its_noise():
    # the dead-loop the guard exists for, with what a real failure prints differently
    # on every run: an object address, pytest's numbered tmp_path, a tempfile name, an
    # epoch, a port, a duration and the peak memory
    def output(i: int) -> str:
        return (f"E   AssertionError: <Foo object at 0x7f8a1c2b{i:04x}> != 3\n"
                f"tmp_path = /tmp/pytest-of-u/pytest-{120 + i}/test_x0\n"
                f"wrote /var/folders/nv/h2tw/T/tmpk3j_x{i}q/out.json ts={1732087200 + i}\n"
                f"server on 127.0.0.1:{51234 + i}\n"
                f"FAILED tests/test_a.py::test_x - AssertionError\n"
                f"1 failed in {2.1 + i / 10:.2f}s Peak RSS: {139 + i} MB")
    g = CompactionGuard()
    levels = []
    for i in range(7):
        g.record_tool_call("Bash", {"command": "pytest tests/test_a.py"}, tool_use_id=f"t{i}")
        levels.append(g.check())
        g.record_tool_result(f"t{i}", output(i))
    assert g._consec_count == 7 and g._is_consecutive_repeat() == EscalationLevel.KILL
    assert levels[2] == EscalationLevel.SOFT_WARN


def test_single_tool_repeat_fires_on_the_fifth_identical_call_as_before():
    g = CompactionGuard()
    g.activate()
    for i in range(4):
        g.record_tool_call("Read", {"file_path": "/x"}, tool_use_id=f"r{i}")
        g.record_tool_result(f"r{i}", "same text")
    assert g._detect_loop() is False
    g.record_tool_call("Read", {"file_path": "/x"}, tool_use_id="r4")   # 5th, in flight
    assert g._detect_loop() is True


def test_a_large_progress_count_is_still_progress():
    # a 9+ digit count that is not a timestamp (rows, bytes, tokens) is progress
    g = CompactionGuard()
    streaks = []
    for i in range(7):
        g.record_tool_call("Bash", CMD, tool_use_id=f"t{i}")
        streaks.append(g._consec_count)
        g.record_tool_result(f"t{i}", f"processed {123456789 + i * 1000} rows")
    assert max(streaks) == 2


def test_ports_and_ipv6_addresses_are_noise():
    fp = CompactionGuard._fingerprint
    assert fp("FAILED; server [::1]:51234 listening on port 51234") == \
        fp("FAILED; server [::1]:51299 listening on port 51299")
    assert fp("FAILED at ts=1732087200123") == fp("FAILED at ts=1732087299000")


def test_set_overlap_counts_a_call_in_flight_that_repeats_a_pre_compaction_call():
    g = CompactionGuard()
    for i in range(5):                      # before compaction: read f0..f4
        g.record_tool_call("Read", {"file_path": f"/f{i}"}, tool_use_id=f"a{i}")
        g.record_tool_result(f"a{i}", f"text {i}")
    g.activate()
    for i in range(3):                      # after: re-read f0..f2, same text
        g.record_tool_call("Read", {"file_path": f"/f{i}"}, tool_use_id=f"b{i}")
        g.record_tool_result(f"b{i}", f"text {i}")
    g.record_tool_call("Read", {"file_path": "/new"}, tool_use_id="b3")
    g.record_tool_result("b3", "new text")
    g.record_tool_call("Read", {"file_path": "/f3"}, tool_use_id="b4")   # in flight
    assert g._detect_loop() is True          # 4 of 5 overlap, as on the base


def test_noise_rules_keep_counts_and_drop_seeds():
    fp = CompactionGuard._fingerprint
    # a random seed printed at the top of every run is noise
    assert fp("Using --randomly-seed=2847562934\nFAILED 1") == fp("Using --randomly-seed=3912345678\nFAILED 1")
    # counts next to a time label, a file extension or an index are progress
    assert fp("epoch: 3 of 10") != fp("epoch: 4 of 10")
    assert fp("seed 3 of 10 done") != fp("seed 4 of 10 done")
    assert fp("seed: 3 of 10") != fp("seed: 4 of 10")
    assert fp("error at src/a.ts:12") != fp("error at src/a.ts:40")
    assert fp("arr[1]:5 done") != fp("arr[1]:6 done")
    # a labelled timestamp is still noise
    assert fp("FAILED time=1732087200.5") == fp("FAILED time=2032087299.7")


def test_set_overlap_matches_a_pre_compaction_call_that_was_in_flight():
    g = CompactionGuard()
    for i in range(5):                      # in flight when compaction came
        g.record_tool_call("Read", {"file_path": f"/f{i}"}, tool_use_id=f"a{i}")
    g.activate()
    for i in range(5):                      # the same reads after, with results
        g.record_tool_call("Read", {"file_path": f"/f{i}"}, tool_use_id=f"b{i}")
        g.record_tool_result(f"b{i}", f"text {i}")
    assert g._detect_loop() is True          # 5 of 5 overlap, as on the base
