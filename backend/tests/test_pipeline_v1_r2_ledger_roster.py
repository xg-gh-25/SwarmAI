"""The R2 roster comes from the ledger (design 2026-10-26-pipeline-review-closure-and-
visibility §2.3 point 7, step 9).

Before: the R2 handout listed only the findings of the ``review-address`` call that wrote
it; addresses already in the log (an earlier call that failed half way, as in a run of
2026-10-26) never reached any R2, and the R2 silence rule then reopened them.

What is tested, through real runs (``tests/pipeline_v1_rig.py``); the old half-written log
is made with ``record_fix_handoff`` (the event-by-event writer):
- the next pass's R2 handout includes every addressed finding no R2 handout covered yet,
  each with the hunk of its own address event;
- with such an address uncovered, ``next`` names it, REVIEW cannot complete (RV-5), and
  ``review-address`` with no items dispatches its R2.
"""
from __future__ import annotations

import json

import pytest

from tests.pipeline_v1_rig import Rig
from tests.test_pipeline_v1_review_address_atomic import TEST_CMD, _at_review

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _half_written(r: Rig, finding_id: str) -> None:
    from core.run_protocol import review
    engine = r.engine()
    review.ReviewOrchestrator(engine.store, engine.run_dir).record_fix_handoff(
        {"pass": 1, "items": [{"finding_id": finding_id, "hunk": "@@ OLD-DOCSTRING-HUNK"}]},
        at="2026-10-26T15:56:19+00:00", attempt=1)


def _r2_handouts(r: Rig) -> list[dict]:
    return [e for e in r.events() if e["kind"] == "handout" and e.get("round") == 2]


def test_the_next_pass_covers_an_earlier_uncovered_address(rig):
    ids = _at_review(rig)
    _half_written(rig, ids["docs"])
    assert not _r2_handouts(rig)
    out = rig.v1("review-address", "--artifact", rig.artifact("b", {"pass": 1, "items": [
        {"finding_id": ids["behavior"], "hunk": "@@ return a + b",
         "proof": {"evidence_type": "reported_test", "command": TEST_CMD}}]}))
    assert out.get("ok") is True, out
    (handout,) = _r2_handouts(rig)
    assert sorted(handout["payload"]["finding_ids"]) == sorted(ids.values())
    full = __import__("pathlib").Path(out["handouts"][0]["path"]).read_text(encoding="utf-8")
    assert "OLD-DOCSTRING-HUNK" in full and "return a + b" in full


def test_an_uncovered_address_blocks_review_until_an_empty_pass_dispatches_it(rig):
    ids = _at_review(rig)
    _half_written(rig, ids["docs"])
    nxt = rig.v1("status")["next"]
    assert ids["docs"] in json.dumps(nxt) and "review-address" in nxt["command"], nxt
    done = rig.v1("stage-complete")
    assert done.get("passed") is not True, done
    assert any(c.get("id") == "RV-5" and not c.get("passed") for c in done.get("checks", [])), done
    out = rig.v1("review-address", "--artifact", rig.artifact("e", {"pass": 1, "items": []}))
    assert out.get("ok") is True, out
    (handout,) = _r2_handouts(rig)
    assert handout["payload"]["finding_ids"] == [ids["docs"]]
    from core.run_protocol import checks
    assert checks.evaluate("RV-5", rig.engine().check_context())[0] is True
