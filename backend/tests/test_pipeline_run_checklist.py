"""The run's checklist in the chat (core.pipeline_profiles.PROFILE_GATES +
core.pipeline_stage_events.checklist / run_block / new_stage_blocks).

Why this exists: a pipeline turn shows ONE block whose header is the run's plan as a
checklist (done / running / pending). Pending gate lines need to know which gates a
profile runs and where they sit; that knowledge lives in the backend, so the backend
writes the checklist into the ``pipeline_run`` block and the frontend only renders it.
A run is created before EVALUATE sets its profile, so the first ``pipeline_run``
block of a turn has no plan: when the profile is first set (or changes) the backend
emits a refreshed block.

Covered:
  - PROFILE_GATES per profile, and three pins that stop it drifting from the rules
    enforced elsewhere: every profile ``artifact_cli.GATE1_PROFILES`` requires to
    run Gate 1 lists gate1; gate0 exactly where a strict profile runs EVALUATE;
    gate2 exactly where a profile has DELIVER.
  - checklist order for full / goal / trivial / direct / research, and none without
    a profile (negative).
  - run_block carries the checklist.
  - new_stage_blocks emits a refreshed run block when the profile is set or changes,
    and none when it is unchanged or the run has no requirement (negatives).
  - plan's ``next`` names Gate 1 exactly when the profile lists gate1.
"""
from __future__ import annotations

import pytest

from core import pipeline_profiles as pp
from core import pipeline_stage_events as pse

AT = "2026-10-19T13:00:00+08:00"


def _state(profile, requirement="Do the thing", **stages):
    return {"profile": profile, "stages": stages, "timing": {}, "status": "running",
            "requirement": requirement, "run_dir": None}


def _items(cl):
    return [(c["stage"], c["gate"]) for c in cl]


class TestProfileGates:
    def test_each_profile_lists_its_gates(self):
        assert pp.PROFILE_GATES["full"] == ("gate0", "gate1", "gate2")
        assert pp.PROFILE_GATES["bugfix"] == ("gate0", "gate1", "gate2")
        assert pp.PROFILE_GATES["goal"] == ("gate0", "gate1", "gate2")
        assert pp.PROFILE_GATES["direct"] == ("gate2",)
        assert pp.PROFILE_GATES["trivial"] == ("gate2",)
        assert "docs" not in pp.PROFILE_GATES   # removed profile
        assert pp.PROFILE_GATES["research"] == ()

    def test_every_profile_has_an_entry(self):
        assert set(pp.PROFILE_GATES) == set(pp.PIPELINE_PROFILES)

    def test_profiles_that_must_run_gate1_list_it(self):
        from scripts.artifact_cli import GATE1_PROFILES
        listed = {p for p, gates in pp.PROFILE_GATES.items() if "gate1" in gates}
        assert set(GATE1_PROFILES) <= listed

    @pytest.mark.parametrize("profile", sorted(pp.PIPELINE_PROFILES))
    def test_gate0_where_a_strict_profile_runs_evaluate(self, profile):
        expect = not pp.is_relaxed_profile(profile) and "evaluate" in pp.get_profile_stages(profile)
        assert ("gate0" in pp.PROFILE_GATES[profile]) is expect

    @pytest.mark.parametrize("profile", sorted(pp.PIPELINE_PROFILES))
    def test_gate2_where_the_profile_delivers(self, profile):
        assert ("gate2" in pp.PROFILE_GATES[profile]) is ("deliver" in pp.get_profile_stages(profile))

    def test_profile_gates_reads_normalized_profiles(self):
        assert pp.profile_gates("Full") == pp.PROFILE_GATES["full"]
        assert pp.profile_gates("standard") == pp.PROFILE_GATES["full"]
        # unknown -> strict, full's gates (fail-closed, like is_relaxed_profile)
        assert pp.profile_gates("mystery") == pp.PROFILE_GATES["full"]


class TestChecklist:
    def test_full(self):
        assert _items(pse.checklist("full")) == [
            ("evaluate", False), ("gate0", True), ("think", False), ("plan", False),
            ("gate1", True), ("build", False), ("review", False), ("test", False),
            ("gate2", True), ("deliver", False), ("reflect", False)]

    def test_goal(self):
        assert _items(pse.checklist("goal")) == [
            ("evaluate", False), ("gate0", True), ("think", False), ("plan", False),
            ("gate1", True), ("goal_cycle", False), ("gate2", True), ("deliver", False),
            ("reflect", False)]

    def test_trivial_has_only_gate2(self):
        assert _items(pse.checklist("trivial")) == [
            ("evaluate", False), ("think", False), ("build", False), ("review", False),
            ("test", False), ("gate2", True), ("deliver", False), ("reflect", False)]

    def test_direct(self):
        assert _items(pse.checklist("direct")) == [
            ("plan", False), ("build", False), ("gate2", True), ("deliver", False)]

    def test_research_has_no_gates(self):
        assert _items(pse.checklist("research")) == [
            ("evaluate", False), ("think", False), ("reflect", False)]

    def test_no_profile_no_checklist(self):
        assert pse.checklist(None) == []
        assert pse.checklist("") == []


class TestRunBlock:
    def test_run_block_carries_the_checklist(self):
        rb = pse.run_block("run_c1", _state("goal"), AT)
        assert rb["checklist"] == pse.checklist("goal")
        assert rb["stages"] == pp.get_profile_stages("goal")

    def test_run_block_before_evaluate_has_an_empty_checklist(self):
        rb = pse.run_block("run_c1", _state(None), AT)
        assert rb["checklist"] == [] and rb["stages"] == []


class TestRefreshedRunBlock:
    def test_profile_set_emits_a_refreshed_run_block(self):
        before = _state(None)
        after = _state("goal")
        blocks = pse.new_stage_blocks("run_c1", before, after, AT)
        runs = [b for b in blocks if b["type"] == pse.RUN_BLOCK_TYPE]
        assert len(runs) == 1
        assert runs[0]["checklist"] == pse.checklist("goal") and runs[0]["runId"] == "run_c1"

    def test_profile_change_emits_a_refreshed_run_block(self):
        blocks = pse.new_stage_blocks("run_c1", _state("full"), _state("goal"), AT)
        assert [b["profile"] for b in blocks if b["type"] == pse.RUN_BLOCK_TYPE] == ["goal"]

    def test_unchanged_profile_emits_no_run_block(self):
        blocks = pse.new_stage_blocks("run_c1", _state("goal"), _state("goal"), AT)
        assert not [b for b in blocks if b["type"] == pse.RUN_BLOCK_TYPE]

    def test_no_requirement_no_refresh(self):
        blocks = pse.new_stage_blocks("run_c1", _state(None, requirement=None),
                                      _state("goal", requirement=None), AT)
        assert not [b for b in blocks if b["type"] == pse.RUN_BLOCK_TYPE]


class TestPlanNextGate1:
    @pytest.mark.parametrize("profile,nxt", [("full", "gate1"), ("goal", "gate1"), ("direct", "build")])
    def test_plan_next_names_gate1_when_the_profile_lists_it(self, profile, nxt):
        before = _state(profile, plan={"stage": "plan", "status": "in_progress"})
        after = _state(profile, plan={"stage": "plan", "status": "completed"})
        rows = [b for b in pse.new_stage_blocks("run_c1", before, after, AT) if b.get("stage") == "plan"]
        assert rows and rows[0]["next"] == nxt
