"""Pipeline v1 real end-to-end tests: invariant: whatever the command order, the event log folds.

Split from test_pipeline_v1_real_e2e.py (design 2026-10-27-pipeline-hygiene §2);
shared fixture and helpers: tests/pipeline_v1_e2e_common.py. Invariant checked
after every case: the run's event log folds (``status`` succeeds).
"""
from __future__ import annotations

import pytest

from tests.pipeline_v1_rig import Rig
from tests.pipeline_v1_e2e_common import (  # noqa: F401  (rig is a fixture)
    CODE_PROFILES, _decision, _landed_clean, _main, _not_landed_tests_red, _prepare,
    _reason, _request, rig)

pytestmark = pytest.mark.timeout(300)


# ── invariant: whatever the command order, the log folds ────────────────────

_STEPS = ("events", "complete-current", "stage-complete-empty", "report", "report-fail",
          "review-start", "land", "decision", "answer", "stop", "resume", "status")


def _step(r: Rig, step: str) -> None:
    if step == "complete-current":
        if r.state()["status"] == "active":
            r.complete_stage()
    elif step == "stage-complete-empty":
        r.v1("stage-complete")
    elif step in {"report", "report-fail"}:
        if r.worktree is not None:
            r.report_and_complete("fail" if step == "report-fail" else "pass")
    elif step == "land":
        r.v1("land", "--message", "fix: swap\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    elif step == "decision":
        r.v1("decision-request", "--artifact", _decision(r, f"q{len(r.events())}"),
             "--document", "DESIGN_SPEC")
    elif step == "answer":
        if r.v1("status").get("decision"):
            r.answer_card("a — swap")
    elif step == "stop":
        r.stop_session()
    else:
        r.v1(step)


def test_any_command_sequence_leaves_a_foldable_log(tmp_path, monkeypatch):
    import random
    rng = random.Random(20261022)
    failures = []
    for case in range(6):
        r = Rig(tmp_path / f"seq{case}", monkeypatch, profile="direct")
        try:
            r.create()
            sequence = [rng.choice(_STEPS) for _ in range(9)]
            for step in sequence:
                _step(r, step)
                if r.v1("status").get("ok") is not True:
                    failures.append((sequence, step))
                    break
        finally:
            r.close()
    assert not failures, failures
