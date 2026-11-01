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
  ``review-address`` with no items dispatches its R2;
- a second fixing pass waits for the first pass's R2 (one pass per visit, design
  2026-10-31); an UNANSWERED line stays re-askable meanwhile; accepted / refuted
  findings are never sent to R2 again;
- a pass prepared on a log that moved, or refused by the ledger, writes nothing.
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


def _answer(r: Rig, handout: dict, finding_id: str, word: str, tool: str) -> None:
    r.spawn_reviewer(handout["prompt"], json.dumps(
        {"finding_id": finding_id, "status": word, "note": "n"}) + "\nNO FINDINGS", tool)


def test_a_second_pass_waits_for_the_first_pass_s_r2(rig):
    """One fixing pass per visit (design 2026-10-31 §3 change 1): while its R2 is out, a
    second pass is refused and writes nothing; the first R2 stays the one to spawn."""
    ids = _at_review(rig)
    first = rig.v1("review-address", "--artifact", rig.artifact("a", {"pass": 1, "items": [
        {"finding_id": ids["behavior"], "hunk": "@@ return a + b",
         "proof": {"evidence_type": "reported_test", "command": TEST_CMD}}]}))
    assert first.get("ok") is True, first
    before = len(rig.events())
    second = rig.v1("review-address", "--artifact", rig.artifact("b", {"pass": 2, "items": [
        {"finding_id": ids["docs"], "hunk": "@@ docstring"}]}))
    assert second.get("ok") is False and "has not answered yet" in second["error"], second
    assert len(rig.events()) == before
    assert _r2_handouts(rig)[-1]["payload"]["finding_ids"] == [ids["behavior"]]
    from core.run_protocol import review
    assert review.uncovered_addresses(rig.events()) == []


def test_an_unanswered_line_stays_re_askable_while_a_second_pass_is_refused(rig):
    from core.run_protocol import review
    ids = _at_review(rig)
    out = rig.v1("review-address", "--artifact", rig.artifact("a", {"pass": 1, "items": [
        {"finding_id": ids["behavior"], "hunk": "@@ return a + b",
         "proof": {"evidence_type": "reported_test", "command": TEST_CMD}}]}))
    _answer(rig, out["handouts"][0], ids["behavior"], "RESOLVED", "r2-0")   # not allowed
    refused = rig.v1("review-address", "--artifact", rig.artifact("b", {"pass": 2, "items": [
        {"finding_id": ids["docs"], "hunk": "@@ docstring"}]}))
    assert refused.get("ok") is False and "has not answered yet" in refused["error"], refused
    targets = review.r2_redispatch_targets(rig.events())
    assert [set(wrong) for _answer_id, wrong in targets.values()] == [{ids["behavior"]}], targets
    redo = rig.v1("review-address", "--artifact", rig.artifact("c", {"pass": 2, "items": []}))
    assert redo.get("ok") is True and redo["handouts"], redo


def test_accepted_or_refuted_findings_are_never_sent_to_r2_again():
    from core.run_protocol import review
    base = [{"event_id": "a1", "kind": "address", "payload": {"canonical_id": "CF-1"}},
            {"event_id": "a2", "kind": "address", "payload": {"canonical_id": "CF-2"}},
            {"event_id": "x1", "kind": "auth_consumed",
             "payload": {"action": "accept_finding", "canonical_id": "CF-1"}},
            {"event_id": "x2", "kind": "proof",
             "payload": {"canonical_id": "CF-2", "evidence_type": "refuted"}}]
    assert review.uncovered_addresses(base) == []
    assert review.uncovered_addresses(base[:2]) == ["CF-1", "CF-2"]


def test_a_pass_prepared_on_a_moved_log_writes_nothing(rig, monkeypatch):
    from core.run_protocol import engine as engine_module
    ids = _at_review(rig)
    engine = rig.engine()
    real = engine.store.read_events

    def stale():
        # what review-address prepared on misses the last event; the locked write sees all
        import sys
        events = real()
        return events if sys._getframe(1).f_code.co_name == "append_batch" else events[:-1]

    monkeypatch.setattr(engine.store, "read_events", stale)
    before = [e["event_id"] for e in real()]
    with pytest.raises(engine_module.EngineError, match="log changed"):
        engine.review_address({"pass": 1, "items": [
            {"finding_id": ids["docs"], "hunk": "@@ docstring"}]})
    assert [e["event_id"] for e in real()] == before


def test_a_ledger_refusal_is_reported_as_wrote_nothing(rig, monkeypatch):
    from core.run_protocol import engine as engine_module, findings
    ids = _at_review(rig)
    engine = rig.engine()

    def refuse(**_kwargs):
        raise findings.FindingError("proof cannot replace a terminal finding state")

    monkeypatch.setattr(engine.store, "append_batch", refuse)
    with pytest.raises(engine_module.EngineError, match="wrote nothing"):
        engine.review_address({"pass": 1, "items": [
            {"finding_id": ids["docs"], "hunk": "@@ docstring"}]})


def test_an_unanswered_handout_an_expert_has_superseded_counts_as_uncovered():
    """An old log: H1 (X) never answered, then H2 (Y) of the same expert answered. No
    answer to H1 will come through next, so X is uncovered again (not waiting forever)."""
    from core.run_protocol import review
    handout = lambda eid, ids: {"event_id": eid, "kind": "handout", "stage": "review",
                                "round": 2, "payload": {"expert_id": "e", "finding_ids": ids}}
    events = [{"event_id": "a1", "kind": "address", "payload": {"canonical_id": "X"}},
              handout("h1", ["X"]),
              {"event_id": "a2", "kind": "address", "payload": {"canonical_id": "Y"}},
              handout("h2", ["Y"]),
              {"event_id": "r2", "kind": "answer", "payload": {"handout_event_id": "h2"}}]
    assert review.uncovered_addresses(events) == ["X"]
    assert review.pending_r2(events) == {}
    assert review.pending_r2(events[:4]) == {"e": ["Y"]}
