"""Gate 0 and Gate 2 rows in the chat stream (core.pipeline_stage_events).

Why this exists: a pipeline has 9 stages and 3 gates, but the chat showed only the
Gate 1 row. Gate 0 (the EVALUATE skeptic) and Gate 2 (the adversarial review inside
DELIVER) ran without a row of their own, so a reader could not see they happened.
Neither gate has a stage record; their verdicts live in stage ARTIFACTS:
  - Gate 0: the evaluation artifact's ``understanding.skeptic_verdict`` (older runs:
    gate keys on the evaluate record)
  - Gate 2: the deliver artifact's ``adversarial_review`` (goal profile: the
    goal_cycle artifact when deliver has none), shown as "<fixed>/<total> fixed".

Covered: the row appears right after evaluate's row / right before deliver's row,
NO row when the gate did not run (negative), no duplicate when a legacy
``adversarial`` stage record already gives the gate its own row, and an artifact
that is unreadable contributes nothing (fail-safe).
"""
from __future__ import annotations

import json
from pathlib import Path

from core import pipeline_stage_events as pse

AT = "2026-10-19T09:00:00+08:00"


def _run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run_g1"
    d.mkdir()
    return d


def _artifact(run_dir: Path, kind: str, art_id: str, data: dict) -> None:
    (run_dir / f"{kind}-20261019-{art_id}.json").write_text(json.dumps({"data": data}))


def _state(run_dir: Path | None, profile: str, **stages: dict) -> dict:
    return {"profile": profile, "stages": stages,
            "run_dir": str(run_dir) if run_dir else None}


def _stages(blocks: list[dict]) -> list[str]:
    return [b["stage"] for b in blocks]


class TestGate0:
    def test_gate0_row_follows_evaluate_with_the_skeptic_verdict(self, tmp_path):
        rd = _run_dir(tmp_path)
        _artifact(rd, "evaluation", "art_ev1", {"understanding": {"skeptic_verdict": "SUPPORTED"}})
        after = _state(rd, "full", evaluate={"stage": "evaluate", "status": "completed",
                                             "artifact_id": "art_ev1"})
        blocks = pse.new_stage_blocks("run_g1", _state(rd, "full"), after, AT)
        assert _stages(blocks) == ["evaluate", "gate0"]
        g = blocks[1]
        assert g["gate"] == {"name": "GATE 0", "verdict": "SUPPORTED"}
        assert g["status"] == "completed" and g["position"] is None and g["at"] == AT
        assert blocks[0]["next"] == "think" and g["next"] == "think"

    def test_legacy_gate0_key_on_the_record_counts(self, tmp_path):
        rd = _run_dir(tmp_path)
        after = _state(rd, "full", evaluate={"stage": "evaluate", "status": "completed",
                                             "gate0_verdict": "WARN"})
        blocks = pse.new_stage_blocks("run_g1", None, after, AT)
        assert _stages(blocks) == ["evaluate", "gate0"]
        assert blocks[1]["gate"]["verdict"] == "WARN"

    def test_no_gate0_row_when_the_gate_did_not_run(self, tmp_path):
        rd = _run_dir(tmp_path)
        _artifact(rd, "evaluation", "art_ev2", {"understanding": {"claim": "x"}})
        after = _state(rd, "trivial", evaluate={"stage": "evaluate", "status": "completed",
                                                "artifact_id": "art_ev2"})
        assert _stages(pse.new_stage_blocks("run_g1", None, after, AT)) == ["evaluate"]

    def test_gate0_row_does_not_repeat_when_evaluate_is_rerecorded(self, tmp_path):
        rd = _run_dir(tmp_path)
        _artifact(rd, "evaluation", "art_ev1", {"understanding": {"skeptic_verdict": "SUPPORTED"}})
        s = _state(rd, "full", evaluate={"stage": "evaluate", "status": "completed",
                                         "artifact_id": "art_ev1"})
        assert pse.new_stage_blocks("run_g1", s, s, AT) == []

    def test_unreadable_artifact_contributes_nothing(self, tmp_path):
        rd = _run_dir(tmp_path)
        (rd / "evaluation-20261019-art_bad.json").write_text("{not json")
        after = _state(rd, "full", evaluate={"stage": "evaluate", "status": "completed",
                                             "artifact_id": "art_bad"})
        assert _stages(pse.new_stage_blocks("run_g1", None, after, AT)) == ["evaluate"]


class TestGate2:
    def _done_through_test(self) -> dict:
        return {n: {"stage": n, "status": "completed"}
                for n in ("evaluate", "think", "plan", "build", "review", "test")}

    def test_gate2_row_comes_right_before_deliver(self, tmp_path):
        rd = _run_dir(tmp_path)
        _artifact(rd, "delivery", "art_dl1", {"adversarial_review": {
            "spawned": True, "findings_total": 11, "findings_fixed": 10}})
        st = self._done_through_test()
        before = _state(rd, "full", **st)
        after = _state(rd, "full", **st, deliver={"stage": "deliver", "status": "completed",
                                                  "artifact_id": "art_dl1"})
        blocks = pse.new_stage_blocks("run_g1", before, after, AT)
        assert _stages(blocks) == ["gate2", "deliver"]
        assert blocks[0]["gate"] == {"name": "GATE 2", "verdict": "10/11 fixed"}
        assert blocks[0]["next"] == "deliver"

    def test_zero_findings_reads_pass(self, tmp_path):
        rd = _run_dir(tmp_path)
        _artifact(rd, "delivery", "art_dl0", {"adversarial_review": {
            "spawned": True, "findings_total": 0, "findings_fixed": 0}})
        after = _state(rd, "full", deliver={"stage": "deliver", "status": "completed",
                                            "artifact_id": "art_dl0"})
        blocks = pse.new_stage_blocks("run_g1", None, after, AT)
        assert blocks[0]["gate"]["verdict"] == "PASS"

    def test_goal_profile_falls_back_to_the_goal_cycle_artifact(self, tmp_path):
        rd = _run_dir(tmp_path)
        _artifact(rd, "goal_cycle", "art_gc1", {"adversarial_review": {
            "findings_total": 3, "findings_fixed": 3}})
        _artifact(rd, "delivery", "art_dl2", {"summary": "no review block here"})
        gc = {"stage": "goal_cycle", "status": "completed", "artifact_id": "art_gc1"}
        before = _state(rd, "goal", goal_cycle=gc)
        after = _state(rd, "goal", goal_cycle=gc,
                       deliver={"stage": "deliver", "status": "completed", "artifact_id": "art_dl2"})
        blocks = pse.new_stage_blocks("run_g1", before, after, AT)
        assert _stages(blocks) == ["gate2", "deliver"]
        assert blocks[0]["gate"]["verdict"] == "3/3 fixed"

    def test_no_gate2_row_when_no_adversarial_review(self, tmp_path):
        rd = _run_dir(tmp_path)
        _artifact(rd, "delivery", "art_dl3", {"summary": "docs run"})
        after = _state(rd, "docs", deliver={"stage": "deliver", "status": "completed",
                                            "artifact_id": "art_dl3"})
        assert _stages(pse.new_stage_blocks("run_g1", None, after, AT)) == ["deliver"]

    def test_legacy_adversarial_stage_record_is_not_duplicated(self, tmp_path):
        rd = _run_dir(tmp_path)
        _artifact(rd, "delivery", "art_dl4", {"adversarial_review": {
            "findings_total": 2, "findings_fixed": 2}})
        adv = {"stage": "adversarial", "status": "completed"}
        before = _state(rd, "full", adversarial=adv)
        after = _state(rd, "full", adversarial=adv,
                       deliver={"stage": "deliver", "status": "completed", "artifact_id": "art_dl4"})
        assert _stages(pse.new_stage_blocks("run_g1", before, after, AT)) == ["deliver"]

    def test_no_run_dir_means_no_artifact_gate_rows(self):
        after = {"profile": "full", "stages": {
            "deliver": {"stage": "deliver", "status": "completed", "artifact_id": "art_dl1"}}}
        assert _stages(pse.new_stage_blocks("run_g1", None, after, AT)) == ["deliver"]


# ── AC2a: a stage row appears when the stage STARTS (stage_timing.started_at) ──

def _timed(profile: str, timing: dict, **stages: dict) -> dict:
    return {"profile": profile, "stages": stages, "timing": timing, "run_dir": None}


class TestStageStart:
    T0 = "2026-10-19T01:00:00+00:00"

    def test_a_new_started_at_emits_an_in_progress_row_at_that_time(self):
        before = _timed("full", {})
        after = _timed("full", {"evaluate": {"started_at": self.T0}})
        blocks = pse.new_stage_blocks("run_s1", before, after, AT)
        assert [(b["stage"], b["status"], b["at"]) for b in blocks] == [("evaluate", "in_progress", self.T0)]
        b = blocks[0]
        assert b["position"] == 1 and b["total"] == 8 and b["gate"] is None and b["summary"] is None

    def test_no_row_when_the_start_was_already_known(self):
        s = _timed("full", {"evaluate": {"started_at": self.T0}})
        assert pse.new_stage_blocks("run_s1", s, s, AT) == []

    def test_started_and_finished_in_one_call_gives_only_the_completed_row(self):
        before = _timed("full", {})
        after = _timed("full", {"think": {"started_at": self.T0, "completed_at": AT}},
                       think={"stage": "think", "status": "completed"})
        assert [(b["stage"], b["status"]) for b in pse.new_stage_blocks("run_s1", before, after, AT)] == [
            ("think", "completed")]

    def test_completion_then_next_start_in_one_call_keep_that_order(self):
        before = _timed("full", {"plan": {"started_at": self.T0}})
        after = _timed("full", {"plan": {"started_at": self.T0, "completed_at": AT},
                                "build": {"started_at": AT}},
                       plan={"stage": "plan", "status": "completed"})
        got = [(b["stage"], b["status"]) for b in pse.new_stage_blocks("run_s1", before, after, AT)]
        assert got == [("plan", "completed"), ("build", "in_progress")]

    def test_read_stage_state_carries_timing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        d = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_s2"
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"profile": "goal", "stages": [],
            "stage_timing": {"goal_cycle": {"started_at": self.T0}, "bad": "x"}}))
        st = pse.read_stage_state("run_s2")
        assert st["timing"] == {"goal_cycle": {"started_at": self.T0}}


# ── AC2c: the run block names the stage now in progress ──────────────────────

class TestRunBlockCurrent:
    def test_current_is_the_latest_started_unfinished_stage(self):
        st = {"profile": "goal", "requirement": "fix it", "stages": {},
              "timing": {"evaluate": {"started_at": "2026-10-18T17:11:54+00:00", "completed_at": "2026-10-18T17:14:52+00:00"},
                         "plan": {"started_at": "2026-10-18T17:16:04+00:00", "completed_at": "2026-10-18T17:17:20+00:00"},
                         "goal_cycle": {"started_at": "2026-10-18T17:17:26+00:00"}}}
        assert pse.run_block("run_c1", st, AT)["current"] == "goal_cycle"

    def test_no_stage_in_progress_means_no_current(self):
        st = {"profile": "full", "requirement": "x", "stages": {},
              "timing": {"evaluate": {"started_at": "a", "completed_at": "b"}}}
        assert pse.run_block("run_c1", st, AT)["current"] is None


# ── A re-opened stage (stage-start on a completed stage) ─────────────────────

class TestReopenedStage:
    DONE = {"stage": "plan", "status": "completed"}

    def test_reopening_a_completed_stage_emits_a_start_row(self):
        before = _timed("full", {"plan": {"started_at": "2026-10-19T01:00:00Z", "completed_at": "2026-10-19T01:05:00Z"}}, plan=self.DONE)
        after = _timed("full", {"plan": {"started_at": "2026-10-19T02:00:00Z", "first_started_at": "2026-10-19T01:00:00Z"}}, plan=self.DONE)
        assert [(b["stage"], b["status"]) for b in pse.new_stage_blocks("run_r1", before, after, AT)] == [("plan", "in_progress")]
        assert pse.current_stage(after) == "plan"

    def test_closing_it_again_emits_the_completed_row_once(self):
        before = _timed("full", {"plan": {"started_at": "2026-10-19T02:00:00Z", "first_started_at": "2026-10-19T01:00:00Z"}}, plan=self.DONE)
        after = _timed("full", {"plan": {"started_at": "2026-10-19T02:00:00Z", "first_started_at": "2026-10-19T01:00:00Z",
                                         "completed_at": "2026-10-19T02:09:00Z"}}, plan=self.DONE)
        assert [(b["stage"], b["status"]) for b in pse.new_stage_blocks("run_r1", before, after, AT)] == [("plan", "completed")]
        assert pse.new_stage_blocks("run_r1", after, after, AT) == []
        assert pse.current_stage(after) is None

    def test_an_old_terminal_record_without_an_end_time_is_not_current(self):
        st = _timed("full", {"plan": {"started_at": "2026-10-19T01:00:00Z"}}, plan=self.DONE)
        assert pse.current_stage(st) is None
