"""What the agent sees of the review (design 2026-10-26-pipeline-review-closure-and-
visibility §3.2, step 7).

Before: finding ids and states were only in events.jsonl (``events`` gives no payload,
``status`` no findings), and refusals did not say the rule ("statement is volatile or
invalid" for a 401-character statement).

What is tested:
- ``findings`` lists every finding with id, status, closed and close_with; REVIEW's
  ``next`` lists the open ones, the same lines;
- close_with follows the finding (open, awaiting R2, after RESOLVED), it is not one text;
- closure refusals name the finding, its state and what is allowed; reflect refusals
  give the limit or the word that hit.
"""
from __future__ import annotations

import pytest

from core.run_protocol import findings as fd
from core.run_protocol import reflect
from tests.pipeline_v1_rig import Rig
from tests.test_pipeline_v1_review_address_atomic import _at_review

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def test_findings_and_next_give_the_same_lines(rig):
    ids = _at_review(rig)
    out = rig.v1("findings")
    assert out.get("ok") is True, out
    rows = {row["id"]: row for row in out["findings"]}
    assert set(rows) == set(ids.values())
    for row in rows.values():
        assert {"id", "expert", "severity", "kind", "file", "line", "status", "r2_status",
                "closed", "close_with"} <= set(row)
        assert row["closed"] is False and row["close_with"]
    nxt = rig.v1("status")["next"]
    assert nxt.get("findings") == out["findings"], nxt
    assert "reported_test" in rows[ids["behavior"]]["close_with"]
    assert "diff_assertion" in rows[ids["docs"]]["close_with"]

    rig.v1("review-address", "--artifact", rig.artifact("a", {"pass": 1, "items": [
        {"finding_id": ids["docs"], "hunk": "@@ docstring"}]}))
    waiting = {row["id"]: row for row in rig.v1("findings")["findings"]}[ids["docs"]]
    assert "awaiting R2" in waiting["close_with"], waiting


def test_closure_refusals_name_the_finding_and_what_is_allowed():
    row = {"canonical_id": "CF-1", "status": "closed_proven", "r2_status": None,
           "kind": "behavior", "reclassification": None}
    with pytest.raises(fd.FindingError, match=r"CF-1.*allowed: ADEQUATE, TEST_INADEQUATE"):
        fd._status(row, {"payload": {"status": "REFUTED"}})
    proof = {"producer": "cli", "event_id": "e", "payload": {
        "evidence_type": "diff_assertion", "result": "asserted", "code_tree": "a" * 40}}
    with pytest.raises(fd.FindingError, match=r"CF-1 is closed_proven.*open / invalid_test"):
        fd._proof(row, proof, {})


@pytest.mark.parametrize("statement,needle", [
    ("x" * 401, "at most 400"), ("see run_abc123 for it", "run_abc123"),
    ("two\nlines", "line break"), ("   ", "empty")])
def test_reflect_refusals_give_the_rule(statement, needle):
    lesson = {"id": "L1", "type": "pitfall", "scope": "project", "statement": statement,
              "tell": "t", "evidence": ["e1"], "relation": {"kind": "new", "entry": None}}
    with pytest.raises(reflect.ReflectError, match=needle):
        reflect.prepare([lesson], event_ids={"e1"}, candidates={})


def test_reflect_refusals_for_fields_tell_and_evidence():
    base = {"id": "L1", "type": "pitfall", "scope": "project", "statement": "s",
            "tell": "t", "evidence": ["e1"], "relation": {"kind": "new", "entry": None}}
    with pytest.raises(reflect.ReflectError, match=r"missing \['tell'\]"):
        reflect.prepare([{k: v for k, v in base.items() if k != "tell"}], event_ids={"e1"},
                        candidates={})
    with pytest.raises(reflect.ReflectError, match="at most 400"):
        reflect.prepare([{**base, "tell": "t" * 401}], event_ids={"e1"}, candidates={})
    with pytest.raises(reflect.ReflectError, match=r"not in this run: \['zz'\]"):
        reflect.prepare([{**base, "evidence": ["zz"]}], event_ids={"e1"}, candidates={})
