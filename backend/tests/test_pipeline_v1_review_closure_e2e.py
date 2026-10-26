"""The review-closure scenario end to end (design 2026-10-26-pipeline-review-closure-and-
visibility §2.3 last paragraph, step 4).

A run of 2026-10-26 fixed every finding and still audited "0/N findings closed": an R2
word its finding did not take, an address that never reached R2, a docs fix away from the
reported line. Here one R1 answer reports a behavior HIGH, an absence, a docs and a
security_policy finding; the agent fixes them, follows ``next`` (one R2 line uses a word
not allowed and is re-dispatched), closes the docs / policy findings by diff_assertion and
lands. The final Run Audit counts every finding closed, none unclosed HIGH.
"""
from __future__ import annotations

import json

import pytest

from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(600)

TEST_CMD = "python -m pytest backend/tests/test_x.py"
FILLER = "".join(f"\n\ndef f{i}():\n    return {i}\n" for i in range(15))
MOD = "# the old note\ndef add(a, b):\n    return b + a\n" + FILLER
FIXED = MOD.replace("b + a", "a + b").replace("the old note", "the new note")
FINDINGS = [
    {"severity": "HIGH", "kind": "behavior", "file": "backend/core/mod.py", "line": 3,
     "claim": "operands swapped", "fix": "restore order"},
    {"severity": "MEDIUM", "kind": "absence", "file": "backend/tests/test_x.py", "line": 1,
     "claim": "no test of the order", "fix": "add one"},
    {"severity": "LOW", "kind": "docs", "file": "backend/core/mod.py", "line": 30,
     "claim": "the note is stale", "fix": "rewrite it"},
    {"severity": "MEDIUM", "kind": "security_policy", "file": "docs/guide.md", "line": 3,
     "claim": "the guide asks for a token in the URL", "fix": "drop it"},
]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _say(r: Rig, handout: dict, words: dict[str, str], tool: str) -> None:
    lines = [json.dumps({"finding_id": cid, "status": word, "note": "n"})
             for cid, word in words.items()]
    r.spawn_reviewer(handout["prompt"], "\n".join(lines + ["NO FINDINGS"]), tool)


def test_every_fixed_finding_audits_closed(rig):
    r = rig
    r.CHANGE = {"backend/core/mod.py": MOD, "docs/guide.md": "# Guide\n\nPut the token in the URL.\n"}
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], "\n".join(json.dumps(f) for f in FINDINGS)
                         if i == 0 else "NO FINDINGS", f"r1-{i}")
    from core.run_protocol import review
    engine = r.engine()
    ids = {row["kind"]: row["canonical_id"]
           for row in review.ReviewOrchestrator(engine.store, engine.run_dir).findings()}
    assert len(ids) == 4, ids

    # the fix, and the test report that proves the behavior and the absence
    r.edit({"backend/core/mod.py": FIXED, "docs/guide.md": "# Guide\n\nUse a header.\n",
            "backend/tests/test_x.py": (
                "from core.mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\n"
                "def test_order():\n    assert add('a', 'b') == 'ab'\n")})
    assert r.v1("report-tests", "--artifact", r.artifact("fix", r.report())).get("ok") is True
    proof = {"evidence_type": "reported_test", "command": TEST_CMD}
    out = r.v1("review-address", "--artifact", r.artifact("pass1", {"pass": 1, "items": [
        {"finding_id": ids["behavior"], "hunk": "@@ return a + b", "proof": proof},
        {"finding_id": ids["absence"], "hunk": "@@ def test_order", "proof": proof},
        {"finding_id": ids["docs"], "hunk": "@@ the new note"},
        {"finding_id": ids["security_policy"], "hunk": "@@ Use a header"}]}))
    assert out.get("ok") is True, out
    (handout,) = out["handouts"]
    # RESOLVED is not a word a proven finding takes: that line becomes UNANSWERED
    _say(r, handout, {ids["behavior"]: "RESOLVED", ids["absence"]: "ADEQUATE",
                      ids["docs"]: "RESOLVED", ids["security_policy"]: "RESOLVED"}, "r2-0")

    nxt = r.v1("status")["next"]
    assert "review-address" in nxt["command"] and ids["behavior"] in json.dumps(nxt), nxt
    out = r.v1("review-address", "--artifact", r.artifact("redo", {"pass": 1, "items": []}))
    assert out.get("ok") is True, out
    (again,) = out["handouts"]
    _say(r, again, {ids["behavior"]: "ADEQUATE"}, "r2-1")

    out = r.v1("review-proof", "--artifact", r.artifact("proofs", {"pass": 1, "proofs": [
        {"finding_id": ids["docs"], "evidence_type": "diff_assertion"},
        {"finding_id": ids["security_policy"], "evidence_type": "diff_assertion"}]}))
    assert out.get("ok") is True, out
    done = r.v1("stage-complete")
    assert done.get("ok") is True and r.state()["stage"] == "test", done

    state = r.drive()
    assert state["status"] == "completed", state
    audit = json.loads((r.engine().run_dir / "run_audit.json").read_text())
    quality = audit["summary"]["quality"]
    assert (quality["findings_closed"], quality["findings_total"]) == (4, 4), quality
    assert quality["findings_confirmed"] == 2, quality     # the two test-proven ones
    assert audit["details"]["unclosed_high"] == [], audit["details"]
