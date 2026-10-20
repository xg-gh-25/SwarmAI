"""Backend-emitted pipeline stage rows (core.pipeline_stage_events + orchestrator wiring).

Why this exists: pipeline stage progress used to reach the chat ONLY as text the
model chose to write (`## ✦ ⑤ BUILD`). Two full runs in one afternoon wrote none,
so the user saw a wall of folded tool calls and no stage. The fix observes the
chokepoint the agent always crosses — an ``artifact_cli.py`` Bash call carrying a
run id — reads run.json (the authority) when that call returns, and emits one
persisted ``pipeline_stage`` content block per stage that newly reached a terminal
status.

Covered here:
  - pure module: command parsing (literal run id only), run.json reading against a
    real tmp workspace, the stage diff → block shape (ordinals, position, next stage,
    Gate-1 verdict, capped plain summary)
  - orchestrator wiring: driven through the REAL ``_read_formatted_response`` with a
    scripted SDK stream; only the run.json reader leaf is scripted
  - context_injector drops a row made only of stage blocks
  - the frontend fixture is kept in lockstep with real block output

Regenerate the frontend fixture after an intentional block-shape change:
    SWARMAI_REGEN_PIPELINE_STAGE_FIXTURE=1 pytest tests/test_pipeline_stage_events.py -k fixture
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from core import pipeline_stage_events as pse


# ── command parsing ──────────────────────────────────────────────────────────

class TestParseWatchedRunId:
    def test_run_update_with_literal_run_id(self):
        cmd = ("cd /x/swarmai && source backend/.venv/bin/activate && "
               "python backend/scripts/artifact_cli.py run-update --project SwarmAI "
               "--run-id run_d9c6d3c0 --stage-json '{\"stage\":\"think\"}'")
        assert pse.parse_watched_run_id(cmd) == "run_d9c6d3c0"

    def test_any_subcommand_counts(self):
        for sub in ("publish", "advance", "stage-complete", "stage-start", "run-get"):
            cmd = f"python backend/scripts/artifact_cli.py {sub} --project P --run-id run_ab12"
            assert pse.parse_watched_run_id(cmd) == "run_ab12", sub

    def test_id_followed_directly_by_a_shell_operator(self):
        assert pse.parse_watched_run_id(
            "python artifact_cli.py run-get --run-id run_ab; echo exit=$?") == "run_ab"
        assert pse.parse_watched_run_id(
            "python artifact_cli.py run-get --run-id run_ab&& echo ok") == "run_ab"

    def test_the_last_artifact_cli_call_owns_the_run_id(self):
        # an id quoted earlier (heredoc / echo) is not the call that runs
        cmd = ("cat <<EOF\nold: --run-id run_old\nEOF\n"
               "python backend/scripts/artifact_cli.py stage-complete --run-id run_new")
        assert pse.parse_watched_run_id(cmd) == "run_new"

    def test_the_last_assignment_before_the_reference_wins(self):
        assert pse.parse_watched_run_id(
            "R=run_a; echo x; R=run_b; python artifact_cli.py advance --run-id $R") == "run_b"
        # an assignment AFTER the call cannot be its value
        assert pse.parse_watched_run_id(
            "python artifact_cli.py advance --run-id $R; R=run_late") is None

    def test_equals_form(self):
        assert pse.parse_watched_run_id(
            "python artifact_cli.py advance --run-id=run_x9 --state build") == "run_x9"

    def test_shell_variable_run_id_is_not_guessed(self):
        assert pse.parse_watched_run_id(
            'python backend/scripts/artifact_cli.py run-update --run-id "$RID" --status running') is None
        assert pse.parse_watched_run_id(
            "python backend/scripts/artifact_cli.py run-update --run-id $RID") is None

    def test_variable_assigned_in_the_same_command_is_resolved(self):
        # Each Bash call is a fresh shell, so a $VAR run id can only come from an
        # assignment in the SAME command — parsing it is exact, not a guess.
        # (Real shapes: data.db replay, 168 of 170 such calls.)
        assert pse.parse_watched_run_id(
            "source backend/.venv/bin/activate\nRID=run_da39669c\n"
            "python backend/scripts/artifact_cli.py advance --run-id $RID --state review") == "run_da39669c"
        assert pse.parse_watched_run_id(
            'export RUN="run_1b2655df" && python artifact_cli.py run-update --run-id "${RUN}" --status x'
        ) == "run_1b2655df"
        # a flag bundle in a variable still carries the literal id
        assert pse.parse_watched_run_id(
            'P="--project SwarmAI --run-id run_bfd8b9d3"; python artifact_cli.py run-update $P --profile full'
        ) == "run_bfd8b9d3"
        # an assigned value that is not a valid id is still rejected
        assert pse.parse_watched_run_id(
            "RID=../../x; python artifact_cli.py advance --run-id $RID") is None
        # a different variable's assignment does not count
        assert pse.parse_watched_run_id(
            "OTHER=run_aa; python artifact_cli.py advance --run-id $RID") is None

    def test_not_artifact_cli(self):
        assert pse.parse_watched_run_id("python other.py --run-id run_x1") is None
        assert pse.parse_watched_run_id("grep -n run-id backend/scripts/artifact_cli.py") is None

    def test_traversal_shaped_id_rejected(self):
        assert pse.parse_watched_run_id(
            "python artifact_cli.py run-update --run-id ../../etc") is None

    def test_non_string(self):
        assert pse.parse_watched_run_id(None) is None
        assert pse.parse_watched_run_id(["artifact_cli.py", "--run-id", "run_x"]) is None


# ── run.json reading (real files) ────────────────────────────────────────────

def _write_run(ws: Path, run_id: str, data: dict, project: str = "SwarmAI") -> Path:
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "run.json"
    p.write_text(json.dumps(data))
    return p


class TestReadStageState:
    def test_reads_profile_and_last_record_per_stage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        _write_run(tmp_path, "run_r1", {
            "profile": "full",
            "stages": [
                {"stage": "evaluate", "status": "completed"},
                {"stage": "build", "status": "running"},
                {"stage": "build", "status": "completed", "gate1_verdict": "WARN"},
            ],
        })
        state = pse.read_stage_state("run_r1")
        assert state["profile"] == "full"
        assert state["stages"]["evaluate"]["status"] == "completed"
        # the LAST record for a stage is the one that counts
        assert state["stages"]["build"]["status"] == "completed"
        assert state["stages"]["build"]["gate1_verdict"] == "WARN"

    def test_missing_corrupt_or_bad_id_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        assert pse.read_stage_state("run_none") is None
        p = _write_run(tmp_path, "run_bad", {})
        p.write_text("{not json")
        assert pse.read_stage_state("run_bad") is None
        assert pse.read_stage_state("../x") is None
        assert pse.read_stage_state(None) is None

    def test_garbage_stage_entries_are_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        _write_run(tmp_path, "run_g", {"profile": "full", "stages": [
            "nope", {"no_stage": 1}, {"stage": 5}, {"stage": "think", "status": "completed"}]})
        assert list(pse.read_stage_state("run_g")["stages"]) == ["think"]


# ── diff → blocks ────────────────────────────────────────────────────────────

def _state(profile: str, **stages: str | dict) -> dict:
    out: dict = {}
    for name, rec in stages.items():
        out[name] = rec if isinstance(rec, dict) else {"stage": name, "status": rec}
    return {"profile": profile, "stages": out}


class TestNewStageBlocks:
    AT = "2026-10-17T22:00:00+08:00"

    def test_one_new_completion_is_one_block_with_full_shape(self):
        before = _state("full", evaluate="completed")
        after = _state("full", evaluate="completed", think="completed")
        blocks = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert blocks == [{
            "type": "pipeline_stage", "runId": "run_t1", "stage": "think",
            "status": "completed", "ordinal": 2, "position": 2, "total": 8,
            "profile": "full", "next": "plan", "nextOrdinal": 3,
            "gate": None, "summary": None, "at": self.AT,
        }]

    def test_unchanged_or_rerecorded_stage_emits_nothing(self):
        s = _state("full", evaluate="completed", think="completed")
        assert pse.new_stage_blocks("run_t1", s, s, self.AT) == []

    def test_running_status_is_not_terminal(self):
        before = _state("full", evaluate="completed")
        after = _state("full", evaluate="completed", think="running")
        assert pse.new_stage_blocks("run_t1", before, after, self.AT) == []

    def test_skipped_is_terminal(self):
        before = _state("docs", evaluate="completed", think="completed")
        after = _state("docs", evaluate="completed", think="completed", plan="skipped")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (b["stage"], b["status"], b["position"], b["total"], b["next"]) == (
            "plan", "skipped", 3, 5, "deliver")
        assert b["nextOrdinal"] == 9

    def test_next_skips_stages_already_finished(self):
        # resumed run: test was recorded before review finished
        before = _state("full", evaluate="completed", think="completed", plan="completed",
                        build="completed", test="completed")
        after = _state("full", evaluate="completed", think="completed", plan="completed",
                       build="completed", test="completed", review="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (b["stage"], b["next"], b["nextOrdinal"]) == ("review", "deliver", 9)

    def test_several_new_stages_come_out_in_profile_order(self):
        before = _state("full", evaluate="completed")
        after = {"profile": "full", "stages": {
            "plan": {"stage": "plan", "status": "completed"},
            "evaluate": {"stage": "evaluate", "status": "completed"},
            "think": {"stage": "think", "status": "completed"},
        }}
        assert [b["stage"] for b in pse.new_stage_blocks("run_t1", before, after, self.AT)] == [
            "think", "plan"]

    def test_gate1_and_build_finishing_together_give_two_rows(self):
        before = _state("full", evaluate="completed", think="completed", plan="completed")
        after = _state("full", evaluate="completed", think="completed", plan="completed",
                       build={"stage": "build", "status": "completed", "gate1_verdict": "WARN"})
        gate, b = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (gate["stage"], gate["ordinal"], gate["gate"]) == (
            "gate1", 4, {"name": "GATE 1", "verdict": "WARN"})
        assert gate["next"] == "review"  # build is already done
        assert b["ordinal"] == 5 and b["nextOrdinal"] == 6 and b["next"] == "review"
        assert b["gate"] is None  # the verdict lives on the ④ row only

    def test_plan_hands_over_to_gate1_on_strict_profiles(self):
        before = _state("full", evaluate="completed", think="completed")
        after = _state("full", evaluate="completed", think="completed", plan="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (b["next"], b["nextOrdinal"]) == ("gate1", 4)
        gb = _state("goal", evaluate="completed", think="completed")
        ga = _state("goal", evaluate="completed", think="completed", plan="completed")
        (g,) = pse.new_stage_blocks("run_t1", gb, ga, self.AT)
        assert g["next"] == "gate1"

    def test_gate1_verdict_before_build_finishes_is_its_own_row(self):
        before = _state("full", evaluate="completed", think="completed", plan="completed")
        after = _state("full", evaluate="completed", think="completed", plan="completed",
                       build={"stage": "build", "gate1_verdict": "PASS"})  # no status yet
        (g,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (g["stage"], g["next"], g["nextOrdinal"], g["gate"]["verdict"]) == (
            "gate1", "build", 5, "PASS")
        # recording the same verdict again later is not a new row
        later = _state("full", evaluate="completed", think="completed", plan="completed",
                       build={"stage": "build", "status": "completed", "gate1_verdict": "PASS"})
        (b,) = pse.new_stage_blocks("run_t1", after, later, self.AT)
        assert b["stage"] == "build"

    def test_last_stage_has_no_next(self):
        before = _state("research", evaluate="completed", think="completed")
        after = _state("research", evaluate="completed", think="completed", reflect="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (b["ordinal"], b["position"], b["total"], b["next"], b["nextOrdinal"]) == (
            10, 3, 3, None, None)

    def test_goal_cycle_maps_to_build_slot(self):
        before = _state("goal", evaluate="completed", think="completed", plan="completed")
        after = _state("goal", evaluate="completed", think="completed", plan="completed",
                       goal_cycle="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (b["ordinal"], b["next"], b["nextOrdinal"]) == (5, "deliver", 9)

    def test_legacy_complete_status_counts_as_completed(self):
        # 13 real run.json records say "complete" (data replay); show them as done
        after = _state("full", evaluate="complete")
        (b,) = pse.new_stage_blocks("run_t1", _state("full"), after, self.AT)
        assert b["status"] == "completed"
        # and a later "completed" for the same stage is not a new transition
        assert pse.new_stage_blocks("run_t1", after, _state("full", evaluate="completed"), self.AT) == []

    def test_gate_named_stage_records_get_gate_ordinals(self):
        # real stage names seen in run.json: adversarial (14), adversarial_review, gate1
        for name, ordinal in (("adversarial", 8), ("adversarial_review", 8), ("gate1", 4)):
            (b,) = pse.new_stage_blocks("run_t1", _state("full"), _state("full", **{name: "completed"}), self.AT)
            assert b["ordinal"] == ordinal, name

    def test_stage_outside_profile_still_emits_without_position(self):
        before = _state("research", evaluate="completed")
        after = _state("research", evaluate="completed", build="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert b["position"] is None and b["next"] is None and b["ordinal"] == 5

    def test_unknown_stage_name_has_no_ordinal(self):
        before = _state("full")
        after = _state("full", mystery="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert b["ordinal"] is None and b["stage"] == "mystery"

    def test_summary_is_plain_capped_and_non_string_dropped(self):
        long = "x" * 500
        after = _state("full", evaluate={"stage": "evaluate", "status": "completed", "summary": long})
        (b,) = pse.new_stage_blocks("run_t1", _state("full"), after, self.AT)
        assert len(b["summary"]) == pse.SUMMARY_MAX and b["summary"].endswith("…")
        after2 = _state("full", evaluate={"stage": "evaluate", "status": "completed", "summary": {"a": 1}})
        (b2,) = pse.new_stage_blocks("run_t1", _state("full"), after2, self.AT)
        assert b2["summary"] is None
        after3 = _state("full", evaluate={"stage": "evaluate", "status": "completed",
                                          "summary": "  line one\n\nline two  "})
        (b3,) = pse.new_stage_blocks("run_t1", _state("full"), after3, self.AT)
        assert b3["summary"] == "line one line two"

    def test_no_before_means_everything_terminal_is_new(self):
        after = _state("full", evaluate="completed", think="completed")
        assert [b["stage"] for b in pse.new_stage_blocks("run_t1", None, after, self.AT)] == [
            "evaluate", "think"]

    def test_no_after_is_empty(self):
        assert pse.new_stage_blocks("run_t1", _state("full"), None, self.AT) == []


# ── orchestrator wiring (real _read_formatted_response, scripted SDK stream) ──

@pytest.fixture
def _isolated_queue():
    """Each test drives the orchestrator under its own event loop; the permission
    manager caches a per-session queue bound to the loop that created it (see
    test_orchestrator_surface_wiring.py). Clear it around every orchestrator test."""
    from core.permission_manager import permission_manager
    permission_manager.remove_session_queue("sess-test")
    yield
    permission_manager.remove_session_queue("sess-test")


CLI = ("cd /x/swarmai && source backend/.venv/bin/activate && python "
       "backend/scripts/artifact_cli.py run-update --project SwarmAI --run-id {rid} "
       "--stage-json '{{\"stage\":\"think\",\"status\":\"completed\"}}'")


def _bash_use(tool_use_id: str, command: str):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name="Bash", input={"command": command})],
        model="test-model",
    )


def _bash_uses(*pairs):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    return AssistantMessage(
        content=[ToolUseBlock(id=i, name="Bash", input={"command": c}) for i, c in pairs],
        model="test-model",
    )


def _result(*ids, is_error: bool = False):
    from claude_agent_sdk import ToolResultBlock, UserMessage
    return UserMessage(content=[
        ToolResultBlock(tool_use_id=i, content="ok", is_error=is_error) for i in ids])


class _ScriptedReader:
    """Stands in for the run.json LEAF reader only: returns scripted states in call
    order (the last one repeats). Everything above it is the real orchestrator."""

    def __init__(self, *states):
        self.states = list(states)
        self.calls: list[str] = []

    def __call__(self, run_id):
        self.calls.append(run_id)
        if isinstance(self.states[0], Exception):
            raise self.states[0]
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


def _stage_events(events):
    return [e for e in events if e.get("type") == "assistant"
            and any(b.get("type") == "pipeline_stage" for b in e.get("content") or [])]


def _tool_result_at(events, tool_use_id):
    """Index of the first event carrying the tool_result for ``tool_use_id`` (a
    standalone tool_result event or one inside an assistant content list)."""
    for i, e in enumerate(events):
        if e.get("type") == "tool_result" and e.get("tool_use_id") in (tool_use_id, None):
            return i
        for b in e.get("content") or [] if isinstance(e.get("content"), list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == tool_use_id:
                return i
    raise AssertionError(f"no tool_result event for {tool_use_id}: {[e.get('type') for e in events]}")


def _drive_stream(orch, messages):
    from tests.test_tool_call_leak_guard import _drive
    return asyncio.run(_drive(orch, messages))


BEFORE = {"profile": "full", "stages": {"evaluate": {"stage": "evaluate", "status": "completed"}}}
AFTER = {"profile": "full", "stages": {
    "evaluate": {"stage": "evaluate", "status": "completed"},
    "think": {"stage": "think", "status": "completed"}}}


@pytest.mark.usefixtures("_isolated_queue")
class TestOrchestratorWiring:
    def _orch(self):
        from tests.test_tool_call_leak_guard import _make_orchestrator
        return _make_orchestrator()

    def test_transition_yields_one_persistable_stage_event_before_result(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        reader = _ScriptedReader(BEFORE, AFTER)
        monkeypatch.setattr(pse, "read_stage_state", reader)
        events, raised = _drive_stream(self._orch(), [
            _bash_use("tu1", CLI.format(rid="run_t1")), _result("tu1"), _make_result_message()])
        assert raised is None
        stage = _stage_events(events)
        assert len(stage) == 1
        (block,) = stage[0]["content"]
        assert (block["type"], block["runId"], block["stage"], block["ordinal"],
                block["next"]) == ("pipeline_stage", "run_t1", "think", 2, "plan")
        stage_at = events.index(stage[0])
        assert stage_at < [e["type"] for e in events].index("result")
        assert stage_at > _tool_result_at(events, "tu1"), "stage row must follow its tool_result"
        assert reader.calls == ["run_t1", "run_t1"]  # one pre-read, one post-read

    # DELIVER's start row (status in_progress) comes before Gate 2 and push_ready
    # exist: a "needs you" card built then would be partial and stay in history.
    _TEST_DONE = {"stage": "test", "status": "completed"}
    _STARTED = {"profile": "full", "stages": {"test": _TEST_DONE},
                "timing": {"deliver": {"started_at": "2026-10-19T02:00:00Z"}}}

    def _spy_attention(self, monkeypatch) -> list:
        built: list = []
        monkeypatch.setattr(pse, "attention_block", lambda run_id, state, at: built.append(run_id) or {
            "type": "pipeline_attention", "runId": run_id, "items": [{"kind": "gate"}], "at": at})
        return built

    def test_no_attention_card_when_deliver_starts(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        built = self._spy_attention(monkeypatch)
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(
            {"profile": "full", "stages": {"test": self._TEST_DONE}, "timing": {}}, self._STARTED))
        events, raised = _drive_stream(self._orch(), [
            _bash_use("tu1", CLI.format(rid="run_t1")), _result("tu1"), _make_result_message()])
        assert raised is None
        blocks = [b for e in _stage_events(events) for b in e["content"]]
        assert [(b["stage"], b["status"]) for b in blocks if b["type"] == "pipeline_stage"] == [("deliver", "in_progress")]
        assert built == []

    def test_attention_card_when_deliver_ends(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        built = self._spy_attention(monkeypatch)
        ended = {"profile": "full", "stages": {"test": self._TEST_DONE, "deliver": {"stage": "deliver", "status": "completed"}},
                 "timing": {"deliver": {"started_at": "2026-10-19T02:00:00Z", "completed_at": "2026-10-19T02:30:00Z"}}}
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(self._STARTED, ended))
        _, raised = _drive_stream(self._orch(), [
            _bash_use("tu2", CLI.format(rid="run_t1")), _result("tu2"), _make_result_message()])
        assert raised is None
        assert built == ["run_t1"]

    def test_unchanged_state_yields_nothing(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(AFTER, AFTER))
        events, _ = _drive_stream(self._orch(), [
            _bash_use("tu1", CLI.format(rid="run_t1")), _result("tu1"), _make_result_message()])
        assert _stage_events(events) == []

    def test_failed_tool_result_yields_nothing_and_skips_post_read(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        reader = _ScriptedReader(BEFORE, AFTER)
        monkeypatch.setattr(pse, "read_stage_state", reader)
        events, _ = _drive_stream(self._orch(), [
            _bash_use("tu1", CLI.format(rid="run_t1")), _result("tu1", is_error=True),
            _make_result_message()])
        assert _stage_events(events) == []
        assert reader.calls == ["run_t1"]

    def test_non_artifact_cli_command_never_reads(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        reader = _ScriptedReader(BEFORE, AFTER)
        monkeypatch.setattr(pse, "read_stage_state", reader)
        events, _ = _drive_stream(self._orch(), [
            _bash_use("tu1", "git status --short"), _result("tu1"), _make_result_message()])
        assert _stage_events(events) == [] and reader.calls == []

    def test_parallel_calls_on_one_run_emit_the_transition_once(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        # pre-read once (the 2nd call reuses the session snapshot), then two post-reads
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(BEFORE, AFTER, AFTER))
        events, _ = _drive_stream(self._orch(), [
            _bash_uses(("tu1", CLI.format(rid="run_t1")), ("tu2", CLI.format(rid="run_t1"))),
            _result("tu1", "tu2"), _make_result_message()])
        blocks = [b for e in _stage_events(events) for b in e["content"]]
        assert [b["stage"] for b in blocks] == ["think"]

    def test_snapshot_survives_into_the_next_turn(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        orch = self._orch()
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(BEFORE, AFTER))
        _drive_stream(orch, [_bash_use("tu1", CLI.format(rid="run_t1")), _result("tu1"),
                             _make_result_message()])
        later = {"profile": "full", "stages": {**AFTER["stages"],
                 "plan": {"stage": "plan", "status": "completed"}}}
        reader2 = _ScriptedReader(later)
        monkeypatch.setattr(pse, "read_stage_state", reader2)
        # second turn = second event loop: drop the queue bound to the first one
        from core.permission_manager import permission_manager
        permission_manager.remove_session_queue("sess-test")
        events, _ = _drive_stream(orch, [_bash_use("tu9", CLI.format(rid="run_t1")),
                                         _result("tu9"), _make_result_message()])
        blocks = [b for e in _stage_events(events) for b in e["content"]]
        assert [b["stage"] for b in blocks] == ["plan"]  # think is NOT repeated
        assert reader2.calls == ["run_t1"]  # no pre-read: the snapshot is the baseline

    def test_reader_exception_never_breaks_the_turn(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(RuntimeError("disk")))
        events, raised = _drive_stream(self._orch(), [
            _bash_use("tu1", CLI.format(rid="run_t1")), _result("tu1"), _make_result_message()])
        assert raised is None
        assert _stage_events(events) == []
        assert any(e["type"] == "result" for e in events)

    def test_tool_result_inside_assistant_message_branch_also_emits(self, monkeypatch):
        from claude_agent_sdk import AssistantMessage, ToolResultBlock
        from tests.test_tool_call_leak_guard import _make_result_message
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(BEFORE, AFTER))
        am_result = AssistantMessage(
            content=[ToolResultBlock(tool_use_id="tu1", content="ok", is_error=False)],
            model="test-model")
        events, raised = _drive_stream(self._orch(), [
            _bash_use("tu1", CLI.format(rid="run_t1")), am_result, _make_result_message()])
        assert raised is None
        stage = _stage_events(events)
        blocks = [b for e in stage for b in e["content"]]
        assert [b["stage"] for b in blocks] == ["think"]
        assert events.index(stage[0]) > _tool_result_at(events, "tu1")

    def test_missing_baseline_seeds_instead_of_replaying_history(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        # pre-read finds nothing (None); the post-read sees a run deep into its stages
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(None, AFTER))
        events, _ = _drive_stream(self._orch(), [
            _bash_use("tu1", CLI.format(rid="run_t1")), _result("tu1"), _make_result_message()])
        assert _stage_events(events) == []


# ── stage rows never re-enter a model prompt as assistant speech ─────────────

class TestContextInjectorSkipsStageRows:
    def test_stage_only_row_is_dropped_but_mixed_row_kept(self):
        from core.context_injector import _filter_tool_only_messages
        stage_only = {"role": "assistant", "content": [
            {"type": "pipeline_stage", "runId": "run_t1", "stage": "think"}]}
        mixed = {"role": "assistant", "content": [
            {"type": "pipeline_stage", "runId": "run_t1", "stage": "think"},
            {"type": "text", "text": "real answer"}]}
        assert _filter_tool_only_messages([stage_only, mixed]) == [mixed]


# ── frontend fixture lockstep (desktop/src/pages/chat/pipelineStage.contract.test.ts) ──

FIXTURE = (Path(__file__).resolve().parents[2] / "desktop" / "src" / "pages" / "chat"
           / "__fixtures__" / "pipeline-stage-blocks.json")


def _fixture_blocks() -> list[dict]:
    """Real new_stage_blocks output for the shapes the frontend must render."""
    at = "2026-10-17T22:00:00+08:00"
    full0 = _state("full", evaluate="completed", think="completed", plan="completed")
    cases = [
        ("run_fx1", _state("full", evaluate="completed"),
         _state("full", evaluate="completed", think={
             "stage": "think", "status": "completed", "summary": "3 alternatives, recommend A"})),
        ("run_fx1", full0, _state("full", evaluate="completed", think="completed",
                                  plan="completed", build={"stage": "build", "status": "completed",
                                                           "gate1_verdict": "WARN"})),
        ("run_fx2", _state("docs", evaluate="completed", think="completed"),
         _state("docs", evaluate="completed", think="completed", plan="skipped")),
        ("run_fx3", _state("research", evaluate="completed", think="completed"),
         _state("research", evaluate="completed", think="completed", reflect="completed")),
    ]
    out: list[dict] = []
    for rid, before, after in cases:
        out += pse.new_stage_blocks(rid, before, after, at)
    return out


def test_frontend_fixture_matches_real_block_output():
    blocks = _fixture_blocks()
    if os.environ.get("SWARMAI_REGEN_PIPELINE_STAGE_FIXTURE") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(blocks, ensure_ascii=False, indent=2) + "\n")
    assert FIXTURE.exists(), "regenerate: SWARMAI_REGEN_PIPELINE_STAGE_FIXTURE=1"
    assert json.loads(FIXTURE.read_text()) == blocks, (
        "pipeline_stage block shape changed — regenerate the fixture and re-run the "
        "frontend contract test (pipelineStage.contract.test.ts)")
    assert len(blocks) == 5  # case 2 yields the ④ Gate-1 row and the ⑤ build row


# ── end to end: real run.json changed WHILE the call runs, real reader ───────

@pytest.mark.usefixtures("_isolated_queue")
def test_real_run_json_rewritten_during_the_call_yields_the_new_stage(tmp_path, monkeypatch):
    """No leaf is scripted: the run.json file is rewritten between the tool_use and
    its tool_result (as artifact_cli does), and the persist-side cleaner keeps the
    block byte-for-byte, so what is streamed is what data.db stores."""
    from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
    from core.session_router import _clean_blocks_for_persist
    from tests.test_tool_call_leak_guard import _make_orchestrator, _make_result_message

    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    path = _write_run(tmp_path, "run_e2e1", {"profile": "full", "stages": [
        {"stage": "evaluate", "status": "completed"}]})
    cmd = CLI.format(rid="run_e2e1")

    class _Client:
        queries: list = []

        async def query(self, prompt):
            self.queries.append(prompt)

        def receive_response(self):
            async def _stream():
                yield AssistantMessage(content=[ToolUseBlock(
                    id="tu1", name="Bash", input={"command": cmd})], model="m")
                # the CLI call runs here: it records THINK as completed
                path.write_text(json.dumps({"profile": "full", "stages": [
                    {"stage": "evaluate", "status": "completed"},
                    {"stage": "think", "status": "completed", "summary": "2 options, chose A"}]}))
                yield UserMessage(content=[ToolResultBlock(tool_use_id="tu1", content="ok", is_error=False)])
                yield _make_result_message()
            return _stream()

    orch = _make_orchestrator()
    orch._parent._client = _Client()

    async def _run():
        return [ev async for ev in orch._read_formatted_response()]

    events = asyncio.run(_run())
    blocks = [b for e in _stage_events(events) for b in e["content"]]
    assert [(b["stage"], b["ordinal"], b["summary"]) for b in blocks] == [
        ("think", 2, "2 options, chose A")]
    assert _clean_blocks_for_persist(blocks) == blocks


def test_every_ended_run_gets_a_summary_direct_and_abandoned_included():
    """XG 2026-10-20: the result lists the run's documents for every run that ends —
    a direct run, and one that ended abandoned or failed. A run still going has none."""
    for status in ("completed", "abandoned", "failed", "cancelled", "complete"):
        state = {"status": status, "profile": "direct", "stages": {}, "run_dir": None}
        sb = pse.summary_block("run_x", state, "now")
        assert sb and sb["status"] == ("completed" if status == "complete" else status)
        assert [d["name"] for d in sb["docs"]] == [
            "REQUIREMENTS.md", "DESIGN_SPEC.md", "REVIEW.md", "FIXING_SPEC.md", "REPORT.md"]
    for status in ("running", "paused"):
        state = {"status": status, "profile": "direct", "stages": {}, "run_dir": None}
        assert pse.summary_block("run_x", state, "now") is None
    full = dict(state, profile="full", status="completed")
    sb = pse.summary_block("run_x", full, "now")
    assert sb is not None and sb["type"] == "pipeline_summary"
