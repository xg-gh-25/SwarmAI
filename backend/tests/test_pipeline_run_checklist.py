"""The run's checklist in the chat (core.pipeline_stage_events.checklist / run_block /
new_stage_blocks; the stage lists come from lifecycle.PROFILE_STAGES).

Why this exists: a pipeline turn shows ONE block whose header is the run's plan as a
checklist (done / running / pending). Pending gate lines need to know which gates a
profile runs and where they sit; that knowledge lives in the backend, so the backend
writes the checklist into the ``pipeline_run`` block and the frontend only renders it.
A run is created before EVALUATE sets its profile, so the first ``pipeline_run``
block of a turn has no plan: when the profile is first set (or changes) the backend
emits a refreshed block.

Covered:
  - checklist order for full / trivial / direct / research (goal is removed), and none without
    a profile (negative).
  - run_block carries the checklist.
  - new_stage_blocks emits a refreshed run block when the profile is set or changes,
    and none when it is unchanged or the run has no requirement (negatives).
  - plan's ``next`` names the profile's next stage.
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


class TestChecklist:
    def test_full(self):
        assert _items(pse.checklist("full")) == [
            ("evaluate", False), ("think", False), ("plan", False),
            ("build", False), ("review", False), ("test", False),
            ("deliver", False), ("reflect", False)]

    def test_removed_goal_has_no_checklist(self):
        assert pse.checklist("goal") == []

    def test_trivial_has_only_gate2(self):
        assert _items(pse.checklist("trivial")) == [
            ("evaluate", False), ("plan", False), ("build", False), ("review", False),
            ("test", False), ("deliver", False), ("reflect", False)]

    def test_direct(self):
        assert _items(pse.checklist("direct")) == [
            ("build", False), ("review", False), ("test", False),
            ("deliver", False), ("reflect", False)]       # no PLAN (execution plan v2 N1)

    def test_research_has_no_gates(self):
        assert _items(pse.checklist("research")) == [
            ("evaluate", False), ("think", False), ("deliver", False), ("reflect", False)]

    def test_no_profile_no_checklist(self):
        assert pse.checklist(None) == []
        assert pse.checklist("") == []


class TestRunBlock:
    def test_run_block_carries_the_checklist(self):
        rb = pse.run_block("run_c1", _state("bugfix"), AT)
        assert rb["checklist"] == pse.checklist("bugfix")
        assert rb["stages"] == pp.PIPELINE_PROFILES["bugfix"]

    def test_run_block_before_evaluate_has_an_empty_checklist(self):
        rb = pse.run_block("run_c1", _state(None), AT)
        assert rb["checklist"] == [] and rb["stages"] == []


class TestRefreshedRunBlock:


    def test_unchanged_profile_emits_no_run_block(self):
        blocks = pse.new_stage_blocks("run_c1", _state("bugfix"), _state("bugfix"), AT)
        assert not [b for b in blocks if b["type"] == pse.RUN_BLOCK_TYPE]

    def test_no_requirement_no_refresh(self):
        blocks = pse.new_stage_blocks("run_c1", _state(None, requirement=None),
                                      _state("bugfix", requirement=None), AT)
        assert not [b for b in blocks if b["type"] == pse.RUN_BLOCK_TYPE]


class TestPlanNextGate1:
    @pytest.mark.parametrize("profile,nxt", [("full", "build"), ("trivial", "build")])
    def test_plan_next_names_gate1_when_the_profile_lists_it(self, profile, nxt):
        before = _state(profile, plan={"stage": "plan", "status": "in_progress"})
        after = _state(profile, plan={"stage": "plan", "status": "completed"})
        rows = [b for b in pse.new_stage_blocks("run_c1", before, after, AT) if b.get("stage") == "plan"]
        assert rows and rows[0]["next"] == nxt
