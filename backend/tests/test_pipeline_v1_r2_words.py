"""R2 status words (design 2026-10-26-pipeline-review-closure-and-visibility §2.3 points
2-3, step 3).

Before: an R2 line whose word the finding's state does not take (RESOLVED for a
``closed_proven`` finding) was dropped; the finding then counted as unanswered, the R2
silence rule turned a test-proven HIGH back to open, and RV-1a read the answer as
incomplete.

What is tested, through real runs (``tests/pipeline_v1_rig.py``) and the ledger:
- the R2 handout stores each finding's ``allowed_status``; the expert's input lists it;
- for a HIGH ``closed_proven``: no line (silence) → open; ``OPEN`` → read as
  TEST_INADEQUATE → ``invalid_test``; ``RESOLVED`` → ``UNANSWERED``, the state stays
  ``closed_proven`` and the finding still blocks RV-2; ``ADEQUATE`` → closed;
- an UNANSWERED line still makes the answer complete (RV-1a's count);
- ``next`` asks for one re-dispatch (``review-address`` with no items); the new R2 handout
  holds only those findings and says which word was not allowed; an ADEQUATE then closes
  the finding; the same wrong word again stays UNANSWERED, writes no duplicate event, and
  no second re-dispatch is offered;
- an answer recorded before this change (line dropped, integrity note only) still folds.
"""
from __future__ import annotations

import json

import pytest

from core.run_protocol import findings as fd
from tests.pipeline_v1_rig import Rig
from tests.test_run_protocol_findings import Stream

pytestmark = pytest.mark.timeout(300)

TEST_CMD = "python -m pytest backend/tests/test_x.py"
FINDING = ('{"severity":"HIGH","kind":"behavior","file":"backend/core/mod.py","line":2,'
           '"claim":"operands swapped","fix":"restore order"}')


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _row(r: Rig, finding_id: str) -> dict:
    from core.run_protocol import review
    engine = r.engine()
    rows = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()
    return next(row for row in rows if row["canonical_id"] == finding_id)


def _proven_awaiting_r2(r: Rig) -> tuple[str, dict]:
    r.CHANGE = {"backend/core/mod.py": "def add(a, b):\n    return b + a\n" + "".join(
        f"\n\ndef f{i}():\n    return {i}\n" for i in range(15))}
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], FINDING if i == 0 else "NO FINDINGS", f"r1-{i}")
    from core.run_protocol import review
    engine = r.engine()
    finding_id = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()[0]["canonical_id"]
    r.edit({"backend/core/mod.py": r.CHANGE["backend/core/mod.py"].replace("b + a", "a + b")})
    r.v1("report-tests", "--artifact", r.artifact("fix", r.report()))
    out = r.v1("review-address", "--artifact", r.artifact("address", {"pass": 1, "items": [
        {"finding_id": finding_id, "hunk": "@@ -2 +2 @@ return a + b",
         "proof": {"evidence_type": "reported_test", "command": TEST_CMD}}]}))
    assert out.get("ok") is True, out
    (handout,) = out["handouts"]
    return finding_id, handout


def _answer(r: Rig, handout: dict, word: str | None, tool: str) -> None:
    text = "NO FINDINGS" if word is None else json.dumps(
        {"finding_id": handout["finding_id"], "status": word, "note": "n"}) + "\nNO FINDINGS"
    r.spawn_reviewer(handout["prompt"], text, tool)


def test_the_r2_handout_stores_and_shows_the_allowed_words(rig):
    finding_id, handout = _proven_awaiting_r2(rig)
    event = next(e for e in rig.events() if e["kind"] == "handout" and e.get("round") == 2)
    assert event["payload"]["allowed_status"] == {finding_id: ["ADEQUATE", "TEST_INADEQUATE"]}
    full = __import__("pathlib").Path(handout["path"]).read_text(encoding="utf-8")
    assert '"allowed_status"' in full and "ADEQUATE" in full


@pytest.mark.parametrize("word,status,r2", [
    (None, "open", "OPEN"),                       # silence: the protection stays
    ("OPEN", "invalid_test", "TEST_INADEQUATE"),  # stricter, never a release
    ("RESOLVED", "closed_proven", "UNANSWERED"),  # the wrong word changes nothing
    ("ADEQUATE", "closed_proven", "ADEQUATE"),
])
def test_r2_words_for_a_proven_high(rig, word, status, r2):
    finding_id, handout = _proven_awaiting_r2(rig)
    _answer(rig, {**handout, "finding_id": finding_id}, word, "r2-0")
    row = _row(rig, finding_id)
    assert (row["status"], row["r2_status"]) == (status, r2)
    assert fd.push_effect(row) is (r2 != "ADEQUATE")
    if word == "RESOLVED":
        from core.run_protocol import checks
        engine = rig.engine()
        answers = checks._answers(engine.check_context())
        assert answers[-1]["payload"]["round"] == 2, "the R2 answer counts as complete"


def test_one_redispatch_for_a_wrong_word(rig):
    finding_id, handout = _proven_awaiting_r2(rig)
    _answer(rig, {**handout, "finding_id": finding_id}, "RESOLVED", "r2-0")
    nxt = rig.v1("status")["next"]
    assert "review-address" in json.dumps(nxt) and "re-dispatch" in json.dumps(nxt), nxt
    out = rig.v1("review-address", "--artifact", rig.artifact("redo", {"pass": 1, "items": []}))
    assert out.get("ok") is True, out
    (again,) = out["handouts"]
    event = [e for e in rig.events() if e["kind"] == "handout" and e.get("round") == 2][-1]
    assert event["payload"]["finding_ids"] == [finding_id]
    assert event["payload"].get("redispatch_of")
    full = __import__("pathlib").Path(again["path"]).read_text(encoding="utf-8")
    assert "RESOLVED" in full and "not allowed" in full
    _answer(rig, {**again, "finding_id": finding_id}, "RESOLVED", "r2-1")   # wrong again
    r2_answers = [e for e in rig.events() if e["kind"] == "answer" and e["payload"]["round"] == 2]
    assert len(r2_answers) == 2, "the second answer is recorded (its note is its own event)"
    row = _row(rig, finding_id)
    assert (row["status"], row["r2_status"]) == ("closed_proven", "UNANSWERED")
    assert "re-dispatch" not in json.dumps(rig.v1("status")["next"])
    out = rig.v1("review-address", "--artifact", rig.artifact("redo2", {"pass": 1, "items": []}))
    assert not out.get("handouts"), out


def test_a_redispatched_adequate_closes(rig):
    finding_id, handout = _proven_awaiting_r2(rig)
    _answer(rig, {**handout, "finding_id": finding_id}, "RESOLVED", "r2-0")
    out = rig.v1("review-address", "--artifact", rig.artifact("redo", {"pass": 1, "items": []}))
    (again,) = out["handouts"]
    _answer(rig, {**again, "finding_id": finding_id}, "ADEQUATE", "r2-1")
    row = _row(rig, finding_id)
    assert (row["status"], row["r2_status"]) == ("closed_proven", "ADEQUATE")
    assert fd.is_closed(row) and not fd.push_effect(row)


def test_unanswered_keeps_the_state_and_counts_as_answered_in_the_ledger():
    stream = Stream()
    event = stream.finding(kind="behavior")
    cid = "CF-" + event["event_id"][:8]
    answer = stream.answer("correctness", 2)
    stream.add("finding_status", {"answer_event_id": answer["event_id"],
                                  "canonical_id": cid, "status": "UNANSWERED"}, round=2)
    row = stream.projected()[0]
    assert (row["status"], row["r2_status"]) == ("open", "UNANSWERED")


def test_an_answer_recorded_before_this_change_still_folds():
    """Old recorder: the wrong line was dropped, only an integrity note written."""
    stream = Stream()
    event = stream.finding(kind="behavior")
    stream.answer("correctness", 2)
    stream.add("integrity", {"conflict_id": "c" * 64, "conflict_type": "invalid_r2_status",
                             "first_hash": "0" * 64, "new_hash": "c" * 64})
    row = stream.projected()[0]
    assert row["canonical_id"] == "CF-" + event["event_id"][:8]
    assert (row["status"], row["r2_status"]) == ("open", "OPEN")
