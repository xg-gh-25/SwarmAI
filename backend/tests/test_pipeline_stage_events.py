"""Backend-emitted pipeline stage rows (core.pipeline_stage_events + orchestrator wiring).

Why this exists: pipeline stage progress used to reach the chat ONLY as text the
model chose to write (`## ✦ ⑤ BUILD`). Two full runs in one afternoon wrote none,
so the user saw a wall of folded tool calls and no stage. The fix observes the
chokepoint the agent always crosses — an ``pipeline_v1.py`` Bash call carrying a
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
               "python backend/scripts/pipeline_v1.py run-update --project SwarmAI "
               "--run-id run_d9c6d3c0 --stage-json '{\"stage\":\"think\"}'")
        assert pse.parse_watched_run_id(cmd) == "run_d9c6d3c0"

    def test_any_subcommand_counts(self):
        for sub in ("publish", "advance", "stage-complete", "stage-start", "run-get"):
            cmd = f"python backend/scripts/pipeline_v1.py {sub} --project P --run-id run_ab12"
            assert pse.parse_watched_run_id(cmd) == "run_ab12", sub

    def test_id_followed_directly_by_a_shell_operator(self):
        assert pse.parse_watched_run_id(
            "python pipeline_v1.py run-get --run-id run_ab; echo exit=$?") == "run_ab"
        assert pse.parse_watched_run_id(
            "python pipeline_v1.py run-get --run-id run_ab&& echo ok") == "run_ab"

    def test_the_last_artifact_cli_call_owns_the_run_id(self):
        # an id quoted earlier (heredoc / echo) is not the call that runs
        cmd = ("cat <<EOF\nold: --run-id run_old\nEOF\n"
               "python backend/scripts/pipeline_v1.py stage-complete --run-id run_new")
        assert pse.parse_watched_run_id(cmd) == "run_new"

    def test_the_last_assignment_before_the_reference_wins(self):
        assert pse.parse_watched_run_id(
            "R=run_a; echo x; R=run_b; python pipeline_v1.py advance --run-id $R") == "run_b"
        # an assignment AFTER the call cannot be its value
        assert pse.parse_watched_run_id(
            "python pipeline_v1.py advance --run-id $R; R=run_late") is None

    def test_equals_form(self):
        assert pse.parse_watched_run_id(
            "python pipeline_v1.py advance --run-id=run_x9 --state build") == "run_x9"

    def test_shell_variable_run_id_is_not_guessed(self):
        assert pse.parse_watched_run_id(
            'python backend/scripts/pipeline_v1.py run-update --run-id "$RID" --status running') is None
        assert pse.parse_watched_run_id(
            "python backend/scripts/pipeline_v1.py run-update --run-id $RID") is None

    def test_variable_assigned_in_the_same_command_is_resolved(self):
        # Each Bash call is a fresh shell, so a $VAR run id can only come from an
        # assignment in the SAME command — parsing it is exact, not a guess.
        # (Real shapes: data.db replay, 168 of 170 such calls.)
        assert pse.parse_watched_run_id(
            "source backend/.venv/bin/activate\nRID=run_da39669c\n"
            "python backend/scripts/pipeline_v1.py advance --run-id $RID --state review") == "run_da39669c"
        assert pse.parse_watched_run_id(
            'export RUN="run_1b2655df" && python pipeline_v1.py run-update --run-id "${RUN}" --status x'
        ) == "run_1b2655df"
        # a flag bundle in a variable still carries the literal id
        assert pse.parse_watched_run_id(
            'P="--project SwarmAI --run-id run_bfd8b9d3"; python pipeline_v1.py run-update $P --profile full'
        ) == "run_bfd8b9d3"
        # an assigned value that is not a valid id is still rejected
        assert pse.parse_watched_run_id(
            "RID=../../x; python pipeline_v1.py advance --run-id $RID") is None
        # a different variable's assignment does not count
        assert pse.parse_watched_run_id(
            "OTHER=run_aa; python pipeline_v1.py advance --run-id $RID") is None

    def test_not_artifact_cli(self):
        assert pse.parse_watched_run_id("python other.py --run-id run_x1") is None
        assert pse.parse_watched_run_id("grep -n run-id backend/scripts/pipeline_v1.py") is None

    def test_traversal_shaped_id_rejected(self):
        assert pse.parse_watched_run_id(
            "python pipeline_v1.py run-update --run-id ../../etc") is None

    def test_non_string(self):
        assert pse.parse_watched_run_id(None) is None
        assert pse.parse_watched_run_id(["pipeline_v1.py", "--run-id", "run_x"]) is None


def test_pipeline_v1_commands_are_watched():
    assert pse.is_run_create("python backend/scripts/pipeline_v1.py create --project P")
    assert not pse.is_run_create(
        "python backend/scripts/pipeline_v1.py status --project P --run-id run_v1")
    for command in ("status", "gate0-start", "stage-complete", "review-start",
                    "push-status", "land", "review-address", "reflect-prepare",
                    "events"):
        cmd = f"python backend/scripts/pipeline_v1.py {command} --project P --run-id run_v1"
        assert pse.parse_watched_run_id(cmd) == "run_v1", command


# ── run.json reading (real files) ────────────────────────────────────────────

def _write_run(ws: Path, run_id: str, data: dict, project: str = "SwarmAI",
               profile: str = "full", requirement: str = "") -> Path:
    """A v1 run's projection beside its protected contract (read_stage_state reads
    only a run that has one; the test's data dir is the conftest one)."""
    from config import get_app_data_dir
    from core.run_protocol.contract import contract_path
    cp = contract_path(get_app_data_dir(), run_id)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({"run_id": run_id, "protocol_version": 1, "project": project,
                              "profile": profile, "mode": "shared",
                              "requirement": requirement}))
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "run.json"
    p.write_text(json.dumps(data))
    return p


class TestReadStageState:
    def test_reads_the_v1_projection_and_live_identity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        _write_run(tmp_path, "run_v1", {
            "status": "active", "stage": "build", "updated_at": "2026-10-22T01:02:03+00:00",
            "stages": {"evaluate": "completed", "build": "open", "review": "pending",
                       "bad": 5}}, profile="full", requirement="do x")
        state = pse.read_stage_state("run_v1")
        assert state["status"] == "running" and state["profile"] == "full"
        assert state["requirement"] == "do x" and state["current_stage"] == "build"
        assert state["stages"]["evaluate"] == {"stage": "evaluate", "status": "completed"}
        assert "bad" not in state["stages"] and pse.current_stage(state) == "build"

    def test_a_run_without_a_v1_contract_is_not_read(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        d = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "runs" / "run_old1"
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"profile": "full", "stages": []}))
        assert pse.read_stage_state("run_old1") is None

    def test_the_profile_and_project_are_the_contracts_not_run_json(self, tmp_path,
                                                                   monkeypatch):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        _write_run(tmp_path, "run_v2", {"profile": "research", "project": "Other",
                                        "stages": {}}, profile="direct")
        assert pse.read_stage_state("run_v2")["profile"] == "direct"

    def test_isolation_and_landing_come_from_contract_and_projection(self, tmp_path,
                                                                     monkeypatch):
        from config import get_app_data_dir
        from core.run_protocol.contract import contract_path
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        _write_run(tmp_path, "run_iso", {"stages": {}, "landing": "landed",
                                         "commits": [{"repo": "r", "sha": "a" * 40,
                                                      "landed": True}]})
        cp = contract_path(get_app_data_dir(), "run_iso")
        cp.write_text(json.dumps({**json.loads(cp.read_text()), "mode": "isolated"}))
        state = pse.read_stage_state("run_iso")
        assert state["isolated"] is True and state["landed"] is True
        assert state["landed_shas"] == ["a" * 40]

    def test_missing_corrupt_or_bad_id_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        assert pse.read_stage_state("run_none") is None
        p = _write_run(tmp_path, "run_bad", {})
        p.write_text("{not json")
        assert pse.read_stage_state("run_bad") is None
        assert pse.read_stage_state("../x") is None
        assert pse.read_stage_state(None) is None


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


    def test_plan_hands_over_to_build_on_v13_profiles(self):
        before = _state("full", evaluate="completed", think="completed")
        after = _state("full", evaluate="completed", think="completed", plan="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (b["next"], b["nextOrdinal"]) == ("build", 5)
        gb = _state("goal", evaluate="completed", think="completed")
        ga = _state("goal", evaluate="completed", think="completed", plan="completed")
        (g,) = pse.new_stage_blocks("run_t1", gb, ga, self.AT)
        assert g["next"] == "goal_cycle"


    def test_last_stage_has_no_next(self):
        before = _state("research", evaluate="completed", think="completed")
        after = _state("research", evaluate="completed", think="completed", reflect="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (b["ordinal"], b["position"], b["total"], b["next"], b["nextOrdinal"]) == (
            10, 4, 4, None, None)

    def test_goal_cycle_maps_to_build_slot(self):
        before = _state("goal", evaluate="completed", think="completed", plan="completed")
        after = _state("goal", evaluate="completed", think="completed", plan="completed",
                       goal_cycle="completed")
        (b,) = pse.new_stage_blocks("run_t1", before, after, self.AT)
        assert (b["ordinal"], b["next"], b["nextOrdinal"]) == (5, "review", 6)


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
    permission_manager.clear_session("sess-test")
    yield
    permission_manager.clear_session("sess-test")


CLI = ("cd /x/swarmai && source backend/.venv/bin/activate && python "
       "backend/scripts/pipeline_v1.py run-update --project SwarmAI --run-id {rid} "
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
    _STARTED = {"profile": "full", "stages": {"test": _TEST_DONE,
                                              "deliver": {"stage": "deliver", "status": "open"}},
                "current_stage": "deliver", "updated_at": "2026-10-19T02:00:00Z"}

    def _spy_attention(self, monkeypatch) -> list:
        built: list = []
        monkeypatch.setattr(pse, "attention_block", lambda run_id, state, at: built.append(run_id) or {
            "type": "pipeline_attention", "runId": run_id, "items": [{"kind": "gate"}], "at": at})
        return built

    def test_no_attention_card_when_deliver_starts(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        built = self._spy_attention(monkeypatch)
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(
            {"profile": "full", "stages": {"test": self._TEST_DONE}}, self._STARTED))
        events, raised = _drive_stream(self._orch(), [
            _bash_use("tu1", CLI.format(rid="run_t1")), _result("tu1"), _make_result_message()])
        assert raised is None
        blocks = [b for e in _stage_events(events) for b in e["content"]]
        assert [(b["stage"], b["status"]) for b in blocks if b["type"] == "pipeline_stage"] == [("deliver", "in_progress")]
        assert built == []

    def test_attention_card_when_deliver_ends(self, monkeypatch):
        from tests.test_tool_call_leak_guard import _make_result_message
        built = self._spy_attention(monkeypatch)
        ended = {"profile": "full", "stages": {"test": self._TEST_DONE, "deliver": {"stage": "deliver", "status": "completed"}}}
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
        permission_manager.clear_session("sess-test")
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
    cases = [
        ("run_fx1", _state("full", evaluate="completed"),
         _state("full", evaluate="completed", think="completed")),
        ("run_fx1", _state("full", evaluate="completed", think="completed", plan="completed"),
         _state("full", evaluate="completed", think="completed", plan="completed",
                build="degraded")),
        ("run_fx2", _state("trivial", build="completed"),
         _state("trivial", build="completed", review="completed")),
        ("run_fx3", _state("research", evaluate="completed", think="completed"),
         _state("research", evaluate="completed", think="completed", deliver="completed")),
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
    assert len(blocks) == 4


# ── end to end: real run.json changed WHILE the call runs, real reader ───────


def test_every_ended_run_gets_a_summary_direct_and_abandoned_included():
    """XG 2026-10-20: the result lists the run's documents for every run that ends —
    a direct run, and one that ended abandoned or failed. A run still going has none."""
    for status in ("completed", "abandoned", "failed"):
        state = {"status": status, "profile": "direct", "stages": {}, "run_dir": None}
        sb = pse.summary_block("run_x", state, "now")
        assert sb and sb["status"] == status
        # a direct run has no EVALUATE / PLAN, so no REQUIREMENTS / DESIGN_SPEC
        assert [d["name"] for d in sb["docs"]] == ["REVIEW.md", "FIXING_SPEC.md", "REPORT.md"]
    for status in ("running", "paused"):
        state = {"status": status, "profile": "direct", "stages": {}, "run_dir": None}
        assert pse.summary_block("run_x", state, "now") is None
    full = dict(state, profile="full", status="completed")
    sb = pse.summary_block("run_x", full, "now")
    assert sb is not None and sb["type"] == "pipeline_summary"
    assert [d["name"] for d in sb["docs"]] == [
        "REQUIREMENTS.md", "DESIGN_SPEC.md", "REVIEW.md", "FIXING_SPEC.md", "REPORT.md"]


def test_v1_stage_dict_transition_emits_completion_and_next_live_stage():
    before = {
        "profile": "full", "stages": {
            "plan": {"stage": "plan", "status": "open"},
            "build": {"stage": "build", "status": "pending"},
        }, "current_stage": "plan", "updated_at": "2026-10-22T01:00:00+00:00",
    }
    after = {
        "profile": "full", "stages": {
            "plan": {"stage": "plan", "status": "completed"},
            "build": {"stage": "build", "status": "open"},
        }, "current_stage": "build", "updated_at": "2026-10-22T01:01:00+00:00",
    }
    blocks = pse.new_stage_blocks("run_v1", before, after, "fallback")
    assert [(row["stage"], row["status"]) for row in blocks] == [
        ("plan", "completed"), ("build", "in_progress")]
    assert blocks[1]["at"] == "2026-10-22T01:01:00+00:00"


@pytest.mark.usefixtures("_isolated_queue")
def test_real_projection_rewritten_during_the_call_yields_the_new_stage(tmp_path, monkeypatch):
    """No leaf is scripted: the v1 run's projection is rewritten between the tool_use
    and its tool_result (as the CLI does), and the persist-side cleaner keeps the
    block byte-for-byte, so what is streamed is what data.db stores."""
    from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
    from core.session_router import _clean_blocks_for_persist
    from tests.test_tool_call_leak_guard import _make_orchestrator, _make_result_message

    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    path = _write_run(tmp_path, "run_e2e1", {"stages": {"evaluate": "completed",
                                                        "think": "open"}})
    cmd = CLI.format(rid="run_e2e1")

    class _Client:
        queries: list = []

        async def query(self, prompt):
            self.queries.append(prompt)

        def receive_response(self):
            async def _stream():
                yield AssistantMessage(content=[ToolUseBlock(
                    id="tu1", name="Bash", input={"command": cmd})], model="m")
                # the CLI call runs here: it completes THINK
                path.write_text(json.dumps({"stages": {"evaluate": "completed",
                                                       "think": "completed"}}))
                yield UserMessage(content=[ToolResultBlock(tool_use_id="tu1", content="ok",
                                                           is_error=False)])
                yield _make_result_message()
            return _stream()

    orch = _make_orchestrator()
    orch._parent._client = _Client()

    async def _run():
        return [ev async for ev in orch._read_formatted_response()]

    events = asyncio.run(_run())
    blocks = [b for e in _stage_events(events) for b in e["content"]]
    assert [(b["stage"], b["ordinal"], b["status"]) for b in blocks] == [
        ("think", 2, "completed")]
    assert _clean_blocks_for_persist(blocks) == blocks


def test_a_run_document_that_is_a_symlink_or_escapes_is_never_read(tmp_path, monkeypatch):
    """R3 review: the run directory is agent-writable; a REPORT.md symlinked to a
    host file must not be excerpted into the chat or named by path."""
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    path = _write_run(tmp_path, "run_doc1", {"stages": {}})
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("## S\nTOP SECRET\n")
    (path.parent / "REPORT.md").symlink_to(secret)
    (path.parent / "REQUIREMENTS.md").write_text("# R\n## Goal\nreal text\n")
    assert pse._doc_ref(str(path.parent), "REPORT.md") is None
    ref = pse._doc_ref(str(path.parent), "REQUIREMENTS.md")
    assert ref and ref["excerpt"].startswith("## Goal") and not ref["path"].startswith("/")


def test_the_requirement_and_run_id_rules_are_the_protocols(tmp_path, monkeypatch):
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    _write_run(tmp_path, "run_req1", {"stages": {}, "requirement": "forged words"})
    assert pse.read_stage_state("run_req1")["requirement"] in ("", None)
    long_id = "run_" + "a" * 80
    assert pse.parse_watched_run_id(f"python pipeline_v1.py status --run-id {long_id}") == long_id


def test_a_run_document_swapped_for_a_symlink_mid_check_is_never_read(tmp_path, monkeypatch):
    """Re-audit 536bb62b HIGH (TOCTOU): REPORT.md is a regular file when checked, then
    replaced by a symlink to a host file before it is read. The bytes read must be
    the checked file's or nothing, never the link target's."""
    import pathlib
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    path = _write_run(tmp_path, "run_doc2", {"stages": {}})
    secret = tmp_path.parent / "secret2.txt"
    secret.write_text("## S\nTOP SECRET\n")
    doc = path.parent / "REPORT.md"
    doc.write_text("# R\n## Goal\nreal text\n")
    real_resolve = pathlib.Path.resolve

    def swap_after_check(self, *a, **k):
        out = real_resolve(self, *a, **k)
        if self.name == "REPORT.md" and not doc.is_symlink():
            doc.unlink()                    # the checks passed on the regular file
            doc.symlink_to(secret)
        return out
    monkeypatch.setattr(pathlib.Path, "resolve", swap_after_check)
    ref = pse._doc_ref(str(path.parent), "REPORT.md")
    assert ref is None or "TOP SECRET" not in (ref.get("excerpt") or "")


def test_a_run_directory_swapped_for_a_symlink_mid_check_is_never_read(tmp_path, monkeypatch):
    """Re-audit 84b72ec1 HIGH: the run DIRECTORY (not just the document) is replaced
    by a symlink to a host directory after the checks. The read must refuse it."""
    import pathlib
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    path = _write_run(tmp_path, "run_doc3", {"stages": {}})
    run_dir = path.parent
    (run_dir / "REPORT.md").write_text("# R\n## Goal\nreal text\n")
    host = tmp_path.parent / "hostdir3"
    host.mkdir()
    (host / "REPORT.md").write_text("## S\nTOP SECRET\n")
    real_resolve = pathlib.Path.resolve

    def swap_after_check(self, *a, **k):
        out = real_resolve(self, *a, **k)
        if self.name == "REPORT.md" and not run_dir.is_symlink():
            moved = run_dir.with_name("run_doc3.moved")
            run_dir.rename(moved)          # the checks passed on the real directory
            run_dir.symlink_to(host)
        return out
    monkeypatch.setattr(pathlib.Path, "resolve", swap_after_check)
    ref = pse._doc_ref(str(run_dir), "REPORT.md")
    assert ref is None or "TOP SECRET" not in (ref.get("excerpt") or "")
