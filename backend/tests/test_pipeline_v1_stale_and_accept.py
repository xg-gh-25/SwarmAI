"""Closures when the tree moves, and accept-finding's state hash (design 2026-10-26-
pipeline-review-closure-and-visibility §2.3 points 5-6, step 6).

Before: a refutation or diff assertion went stale whenever the run's tree id changed,
even when a clean rebase left the judged files alone (and a refuted HIGH cannot be
proven again, so it blocked RV-2 for good); design / security_policy closures
(``closed_unproven``) were never checked at all. An accept-finding request hashed the
state after the R2 silence rule while the ledger checked it before, so a finding in an
unanswered R2 handout could not be accepted.

What is tested:
- ``stale_closures``: same tree → fresh; other tree, judged file the same blob → fresh;
  judged file changed or unreadable → stale; refutations by their refs' blobs;
  ``closed_unproven`` checked; ``closed_proven`` never (TEST + LG-1 re-establish it);
- a finding waiting for its R2 answer is accepted, and the log still folds.
"""
from __future__ import annotations

import pytest

from core.run_protocol import facts

TREE, NOW = "a" * 40, "b" * 40


def _row(cid: str, status: str, file: str = "a.md") -> dict:
    return {"canonical_id": cid, "status": status, "file": file, "evidence_event_id": "p-" + cid}


def _proof(cid: str, etype: str, refs=None) -> dict:
    payload = {"canonical_id": cid, "evidence_type": etype, "code_tree": TREE, "repo": "r"}
    if refs is not None:
        payload["refs"] = refs
    return {"event_id": "p-" + cid, "kind": "proof", "payload": payload}


def _blobs(table):
    return lambda repo, tree, path: table.get((tree, path))


def test_same_tree_is_fresh_and_no_reader_means_any_move_is_stale():
    rows, events = [_row("X", "closed_asserted")], [_proof("X", "diff_assertion")]
    assert facts.stale_closures(rows, events, {"r": TREE}) == set()
    assert facts.stale_closures(rows, events, {"r": NOW}) == {"X"}


@pytest.mark.parametrize("now_blob,stale", [("b1", False), ("b2", True), (None, True)])
def test_a_diff_assertion_follows_its_file(now_blob, stale):
    rows = [_row("X", "closed_asserted"), _row("Y", "closed_unproven", "p.md")]
    events = [_proof("X", "diff_assertion"), _proof("Y", "diff_assertion")]
    table = {(TREE, "a.md"): "b1", (NOW, "a.md"): now_blob, (TREE, "p.md"): "c", (NOW, "p.md"): "c"}
    out = facts.stale_closures(rows, events, {"r": NOW}, blob_of=_blobs(table))
    assert ("X" in out) is stale and "Y" not in out


def test_closed_unproven_goes_stale_and_closed_proven_never():
    rows = [_row("Y", "closed_unproven"), _row("Z", "closed_proven")]
    events = [_proof("Y", "diff_assertion"), _proof("Z", "reported_test")]
    table = {(TREE, "a.md"): "b1", (NOW, "a.md"): "b2"}
    assert facts.stale_closures(rows, events, {"r": NOW}, blob_of=_blobs(table)) == {"Y"}


def test_a_refutation_follows_its_refs():
    refs = [{"kind": "path", "path": "m.py", "lines": [1, 2], "blob": "m1"},
            {"kind": "event", "event_id": "e"}]
    rows, events = [_row("R", "refuted", "x.py")], [_proof("R", "refuted", refs)]
    fresh = {(NOW, "m.py"): "m1"}
    assert facts.stale_closures(rows, events, {"r": NOW}, blob_of=_blobs(fresh)) == set()
    moved = {(NOW, "m.py"): "m2"}
    assert facts.stale_closures(rows, events, {"r": NOW}, blob_of=_blobs(moved)) == {"R"}


def test_a_finding_waiting_for_r2_can_be_accepted(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import Rig, SESSION
    from tests.test_pipeline_v1_review_address_atomic import _at_review
    rig = Rig(tmp_path / "case", monkeypatch, profile="direct")
    ids = _at_review(rig)
    out = rig.v1("review-address", "--artifact", rig.artifact("a", {"pass": 1, "items": [
        {"finding_id": ids["docs"], "hunk": "@@ docstring"}]}))
    assert out.get("ok") is True, out                   # its R2 handout is not answered
    engine = rig.engine()
    engine.prepare_authorized_action(action="accept_finding", target=ids["docs"],
                                     request_id="auth_wait")
    from core.run_protocol import findings, landing, request_ctx
    landing.approve_from_event(engine.store, "auth_wait", now=__import__("time").time(),
                               at="2026-10-27T00:00:00Z")
    with request_ctx.active(request_ctx.Request(session_id=SESSION)):
        engine.execute_authorized_action("auth_wait")
    rows = {row["canonical_id"]: row for row in findings.fold(rig.events(), engine.authority)}
    assert rows[ids["docs"]]["status"] == "accepted"
    assert rig.v1("status").get("ok") is True
