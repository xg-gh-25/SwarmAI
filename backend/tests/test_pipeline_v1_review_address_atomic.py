"""``review-address`` writes all of a pass or nothing (design 2026-10-26-pipeline-review-
closure-and-visibility §2.3 point 1, step 2).

Before: the address events were appended one by one, then the proofs, then the R2
handouts; a proof the ledger refused, or an R2 prompt that failed its lint, left the
earlier events in the log, and the retry wrote a second set.

What is tested, through real runs (``tests/pipeline_v1_rig.py``):
- a pass with several invalid items is refused with every invalid item named (finding,
  kind, state, what is allowed), and the event log is unchanged;
- a failure while building the R2 handouts (after every item checked) leaves the log
  unchanged;
- a valid pass writes its address, proof and R2 handout events together.
"""
from __future__ import annotations

import json

import pytest

from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)

TEST_CMD = "python -m pytest backend/tests/test_x.py"
BEHAVIOR = {"severity": "HIGH", "kind": "behavior", "file": "backend/core/mod.py", "line": 2,
            "claim": "operands swapped", "fix": "restore order"}
DOCS = {"severity": "LOW", "kind": "docs", "file": "backend/core/mod.py", "line": 40,
        "claim": "docstring stale", "fix": "rewrite"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _at_review(r: Rig) -> dict[str, str]:
    r.CHANGE = {"backend/core/mod.py": "def add(a, b):\n    return b + a\n" + "".join(
        f"\n\ndef f{i}():\n    return {i}\n" for i in range(15))}
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    answer = "\n".join(json.dumps(f) for f in (BEHAVIOR, DOCS))
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], answer if i == 0 else "NO FINDINGS", f"r1-{i}")
    from core.run_protocol import review
    engine = r.engine()
    rows = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()
    assert len(rows) == 2, rows
    by_kind = {row["kind"]: row["canonical_id"] for row in rows}
    r.edit({"backend/core/mod.py": r.CHANGE["backend/core/mod.py"].replace("b + a", "a + b")})
    r.v1("report-tests", "--artifact", r.artifact("fix", r.report()))
    return by_kind


def _snapshot(r: Rig) -> list[str]:
    return [e["event_id"] for e in r.events()]


def test_invalid_items_are_all_named_and_nothing_is_written(rig):
    r = rig
    ids = _at_review(r)
    before = _snapshot(r)
    out = r.v1("review-address", "--artifact", r.artifact("bad", {"pass": 1, "items": [
        {"finding_id": ids["behavior"], "hunk": "@@ return a + b",
         "proof": {"evidence_type": "reported_test", "command": TEST_CMD}},
        {"finding_id": ids["docs"], "hunk": "@@ docstring",
         "proof": {"evidence_type": "reported_test", "command": TEST_CMD}},
        {"finding_id": "CF-deadbeef", "hunk": "@@ x"}]}))
    assert out.get("ok") is False, out
    error = out.get("error", "")
    assert ids["docs"] in error and "diff_assertion" in error, error
    assert "CF-deadbeef" in error, error
    assert ids["behavior"] not in error, error       # the valid item is not listed
    assert _snapshot(r) == before


def test_a_failure_building_the_r2_handouts_writes_nothing(rig, monkeypatch):
    r = rig
    ids = _at_review(r)
    before = _snapshot(r)
    from core.run_protocol import review
    monkeypatch.setattr(review, "lint_prompt", lambda *a, **k: ["injected drift"])
    engine = r.engine()
    with pytest.raises(Exception, match="injected drift"):
        engine.review_address({"pass": 1, "items": [
            {"finding_id": ids["behavior"], "hunk": "@@ return a + b",
             "proof": {"evidence_type": "reported_test", "command": TEST_CMD}}]})
    assert _snapshot(r) == before


def test_a_valid_pass_writes_address_proof_and_handout_together(rig):
    r = rig
    ids = _at_review(r)
    before = len(r.events())
    out = r.v1("review-address", "--artifact", r.artifact("ok", {"pass": 1, "items": [
        {"finding_id": ids["behavior"], "hunk": "@@ return a + b",
         "proof": {"evidence_type": "reported_test", "command": TEST_CMD}},
        {"finding_id": ids["docs"], "hunk": "@@ docstring"}]}))
    assert out.get("ok") is True, out
    new = r.events()[before:]
    kinds = [e["kind"] for e in new]
    assert kinds.count("address") == 2 and kinds.count("proof") == 1, kinds
    assert kinds.count("handout") == 1, kinds
    (handout,) = [e for e in new if e["kind"] == "handout"]
    assert sorted(handout["payload"]["finding_ids"]) == sorted(ids.values())
    assert out["handouts"] and json.dumps(out["handouts"][0]["prompt"])


def test_a_refusal_at_write_time_of_the_last_event_writes_nothing(rig):
    """The store refuses the R2 handout (the last event of the pass) only when it is
    written: the address and proof events before it are not on disk either."""
    r = rig
    ids = _at_review(r)
    before = _snapshot(r)
    engine = r.engine()
    from core.run_protocol.store import StoreError
    original = engine.store.validator

    def refuse_handouts(chain):
        if chain and chain[-1].get("kind") == "handout" and chain[-1].get("round") == 2:
            raise StoreError("injected refusal of the R2 handout")
        return original(chain) if original is not None else None
    engine.store.validator = refuse_handouts
    with pytest.raises(Exception, match="injected refusal"):
        engine.review_address({"pass": 1, "items": [
            {"finding_id": ids["behavior"], "hunk": "@@ return a + b",
             "proof": {"evidence_type": "reported_test", "command": TEST_CMD}}]})
    engine.store.validator = original
    assert _snapshot(r) == before
