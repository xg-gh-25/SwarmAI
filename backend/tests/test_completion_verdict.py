"""completed ≠ verified: a run's delivery is verified iff DELIVER was push-ready.

What is tested:
- artifact_cli._completion_verdict: no completed deliver → None; last completed deliver
  push_ready True → verified; False → unverified with the push_ready_reason split into
  its causes; the LAST completed deliver decides (a re-entered DELIVER);
- run-update --status completed stamps verified / unverified_reasons on run.json (pinned
  in test_pipeline_run.TestRunUpdate.test_update_completed_sets_timestamp);
- the overlay roster (PipelineRunSummary) carries the field.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import artifact_cli as cli  # noqa: E402


def _deliver(push_ready, reason=None, status="completed"):
    rec = {"stage": "deliver", "status": status, "push_ready": push_ready}
    if reason:
        rec["push_ready_reason"] = reason
    return rec


def test_no_completed_deliver_is_not_applicable():
    assert cli._completion_verdict({"stages": []}) == (None, [])
    assert cli._completion_verdict({"stages": [_deliver(True, status="running")]}) == (None, [])


def test_push_ready_deliver_is_verified():
    assert cli._completion_verdict({"stages": [_deliver(True)]}) == (True, [])


def test_not_push_ready_is_unverified_with_each_cause():
    st = {"stages": [_deliver(False, "Gate-2 expert(s) have not reviewed the current change; "
                                     "tests: only tdd evidence")]}
    ok, why = cli._completion_verdict(st)
    assert ok is False and why == ["Gate-2 expert(s) have not reviewed the current change",
                                   "tests: only tdd evidence"]


def test_the_last_completed_deliver_decides():
    st = {"stages": [_deliver(False, "x"), _deliver(True)]}
    assert cli._completion_verdict(st)[0] is True
    st = {"stages": [_deliver(True), _deliver(False, "re-entered, review stale")]}
    assert cli._completion_verdict(st) == (False, ["re-entered, review stale"])


def test_the_roster_model_carries_verified():
    from schemas.pipeline_run import PipelineRunSummary
    row = PipelineRunSummary(id="r", requirement="q", status="completed", profile="full",
                             verified=False)
    assert row.model_dump()["verified"] is False
    assert PipelineRunSummary(id="r", requirement="q", status="running",
                              profile="full").verified is None
