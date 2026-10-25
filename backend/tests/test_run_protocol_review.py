"""Tests Pipeline v1 event-driven REVIEW orchestration.

Methodology: temporary AuditStore and real strict hook bridge; no model calls.
Invariants: lightweight roster, exact handouts, canonical findings, minimal R2, and one
cluster/refutation assignment per open finding.
"""
from __future__ import annotations

import pytest

from core.run_protocol import events as ev
from core.run_protocol.hook_bridge import ReviewHookBridge
from core.run_protocol.review import (OUTPUT_CONTRACT, ReviewError, ReviewOrchestrator,
                                      lint_prompt, reviewers)
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"


def _rig(tmp_path):
    authority = ev.EventAuthority()
    store = AuditStore(tmp_path / "audit", "run_one", authority)
    return ReviewOrchestrator(store, tmp_path / "run"), ReviewHookBridge(store, "session_1")


def test_roster_is_lightweight_and_security_is_risk_triggered():
    plain = [{"path": "backend/core/a.py", "insertions": 80, "deletions": 0}]
    assert [row["id"] for row in reviewers(profile="full", changed=plain)] == ["correctness"]
    risky = [{"path": "backend/core/security_hooks.py", "insertions": 60, "deletions": 0}]
    assert [row["id"] for row in reviewers(profile="full", changed=risky)] == [
        "correctness", "security"]
    small_risk = [{"path": "backend/auth.py", "insertions": 15, "deletions": 0}]
    assert [row["id"] for row in reviewers(profile="direct", changed=small_risk)] == ["security"]
    assert reviewers(profile="full", changed=plain, small_change=True) == []
    assert [row["id"] for row in reviewers(profile="full", changed=[], required=["integration"])] == ["correctness"]


def test_handout_has_exact_contract_and_is_hash_bound(tmp_path):
    review, _ = _rig(tmp_path)
    handout = review.handout(expert_id="correctness", stage="REVIEW", round=1,
                             attempt=1, at=AT, expert_instructions="Check correctness",
                             review_input="diff --git a/x b/x")
    assert lint_prompt(handout["prompt"], expert_id="correctness", round=1) == []
    assert OUTPUT_CONTRACT in handout["prompt"]
    assert handout["event"]["payload"]["prompt_sha"] == handout["sha"]
    assert open(handout["path"], encoding="utf-8").read() == handout["prompt"]


def test_r1_finding_clusters_and_minimal_r2_flow(tmp_path):
    review, bridge = _rig(tmp_path)
    handout = review.handout(expert_id="correctness", stage="REVIEW", round=1,
                             attempt=1, at=AT, expert_instructions="Check",
                             review_input="diff")
    bound_r1 = bridge.track_tool_use("tool-r1", handout["prompt"])
    assert bound_r1
    bridge.record_answer(
        bound_r1,
        '{"severity":"HIGH","kind":"behavior","file":"a.py","line":10,"claim":"crash","fix":"guard"}\n'
        '{"severity":"MEDIUM","kind":"contract","file":"a.py","line":17,"claim":"reader stale","fix":"migrate"}',
        completed_at="2026-10-22T00:01:00+00:00")
    findings = review.findings()
    assert len(findings) == 2 and len(review.clusters()) == 1
    addressed = {row["canonical_id"] for row in findings}
    assert review.r2_roster(addressed) == {"correctness": sorted(addressed)}
    r2_input = review.r2_input("correctness", sorted(addressed),
                               hunks={value: "@@ fix" for value in addressed},
                               proofs={}, reclassifications={})
    r2 = review.handout(expert_id="correctness", stage="REVIEW", round=2,
                        attempt=2, at="2026-10-22T00:02:00+00:00",
                        expert_instructions="Recheck", review_input=r2_input,
                        finding_ids=sorted(addressed))
    assert "R2 ONLY" in r2["prompt"] and "whole diff" in r2["prompt"]
    bound_r2 = bridge.track_tool_use("tool-r2", r2["prompt"])
    assert bound_r2
    answer = "\n".join(
        f'{{"finding_id":"{value}","status":"OPEN"}}' for value in sorted(addressed))
    answer += "\nNO FINDINGS"
    bridge.record_answer(bound_r2, answer, completed_at="2026-10-22T00:03:00+00:00")
    assert all(row["status"] == "open" for row in review.findings())
    assert "## Findings" in review.render_review()




def test_review_document_is_generated_from_events(tmp_path):
    review, bridge = _rig(tmp_path)
    handout = review.handout(expert_id="correctness", stage="REVIEW", round=1,
                             attempt=1, at=AT, expert_instructions="Check", review_input="diff")
    bound = bridge.track_tool_use("tool", handout["prompt"])
    assert bound
    bridge.record_answer(bound, "NO FINDINGS",
                         completed_at="2026-10-22T00:01:00+00:00")
    written = review.write_documents()
    assert written == [str(tmp_path / "run" / "REVIEW.md")]
    assert "tool" not in (tmp_path / "run" / "REVIEW.md").read_text()
    assert "parsed" in (tmp_path / "run" / "REVIEW.md").read_text()




def test_triggered_review_patterns_dispatch_correctness_even_for_a_small_change():
    small = [{"path": "backend/core/a.py", "insertions": 2, "deletions": 0}]
    got = reviewers(profile="direct", changed=small, small_change=True, patterns=["RP1", "RP13"])
    assert [r["id"] for r in got] == ["correctness"] and "RP1, RP13" in got[0]["reason"]
    assert reviewers(profile="direct", changed=small, small_change=True, patterns=[]) == []


def test_only_specific_evidence_triggers_a_pattern():
    from core.run_protocol.review import review_patterns
    # a bare *.py glob fires on every Python file: not evidence about this change
    assert review_patterns(["a.py"], "-value = 1\n+value = 2")["triggered"] == []
    hit = review_patterns(["backend/core/a.py"], "+    subprocess.Popen(['git'])")
    assert "RP1" in hit["triggered"] and hit["error"] is None
    # every applying row is still listed for the reviewer, incl. the *.py ones
    assert "| RP1 |" in hit["compact"]


def test_a_broken_pattern_table_still_reviews(monkeypatch):
    from core.run_protocol import review
    monkeypatch.setattr(review, "_spawn_prompt", lambda: (_ for _ in ()).throw(OSError("gone")))
    got = review.review_patterns(["backend/core/a.py"], "+x")
    assert got["triggered"] == [] and "gone" in got["error"]
    assert "could not select rows" in review.patterns_block(got)


# ── F-8: a comment-only change to source is not executable ──────────────────

@pytest.mark.parametrize("file,diff,expected", [
    ("a.py", "@@ -3 +3 @@\n-# old note\n+# new note\n", True),
    ("a.py", "@@ -3 +3 @@\n-x = 1\n+x = 2\n", False),
    # L4: a .py change is judged on its AST: only comments and layout are not code
    ("a.py", "@@ -3 +3 @@\n-x = 1  # n\n+x = 1  # m\n", True),        # trailing comment
    ("a.py", '@@ -3 +3 @@\n-    """old"""\n+    """new"""\n', False),  # a docstring is data
    ("a.py", '@@ -3 +3 @@\n-x = "old"\n+x = "new"\n', False),         # a value
    ("a.ts", "@@ -3,2 +3,2 @@\n- * old\n-// a\n+ * new\n+// b\n", True),
    ("a.go", "@@ -3 +3 @@\n-// a\n+// b\n", False),                 # unknown syntax
    ("run.sh", "@@ -1 +1 @@\n-#!/bin/bash\n+#!/bin/sh\n", False),      # shebang is code
    ("a.js", "@@ -3 +3 @@\n-  *gen() {}\n+  *gen2() {}\n", False),    # generator, not comment
    ("README.md", "@@ -1 +1 @@\n-a\n+b\n", True),
])
def test_comment_only_diffs_are_docs(file, diff, expected):
    from core.run_protocol.engine import _comment_only_diff
    assert _comment_only_diff(file, diff) is expected


def test_a_fix_pass_addresses_a_whole_cluster_or_nothing(tmp_path):
    """F-9: two findings 7 lines apart are one cluster; addressing one alone is refused."""
    review, bridge = _rig(tmp_path)
    handout = review.handout(expert_id="correctness", stage="review", round=1,
                             attempt=1, at=AT, expert_instructions="Check",
                             review_input="diff")
    bound = bridge.track_tool_use("tool-c", handout["prompt"])
    bridge.record_answer(
        bound,
        '{"severity":"HIGH","kind":"behavior","file":"a.py","line":10,"claim":"crash","fix":"guard"}\n'
        '{"severity":"MEDIUM","kind":"contract","file":"a.py","line":17,"claim":"stale","fix":"migrate"}',
        completed_at=AT)
    first, second = [row["canonical_id"] for row in review.findings()]
    with pytest.raises(ReviewError, match="fixed as one"):
        review.record_fix_handoff({"pass": 1, "items": [{"finding_id": first, "hunk": "@@"}]},
                                  at=AT, attempt=1)
    assert not any(e["kind"] == "address" for e in review.store.read_events())
    done = review.record_fix_handoff(
        {"pass": 1, "items": [{"finding_id": first, "hunk": "@@"},
                              {"finding_id": second, "hunk": "@@"}]}, at=AT, attempt=1)
    assert done["addressed"] == sorted([first, second])


def test_fixing_spec_is_rendered_from_the_pass_events(tmp_path):
    """F-10: FIXING_SPEC.md exists once a fix pass is recorded and is built from events."""
    review, bridge = _rig(tmp_path)
    handout = review.handout(expert_id="correctness", stage="review", round=1,
                             attempt=1, at=AT, expert_instructions="Check",
                             review_input="diff")
    bridge.record_answer(bridge.track_tool_use("tool-f", handout["prompt"]),
                         '{"severity":"HIGH","kind":"behavior","file":"a.py","line":3,'
                         '"claim":"crash","fix":"guard"}', completed_at=AT)
    cid = review.findings()[0]["canonical_id"]
    assert review.render_fixing_passes() is None
    review.record_fix_handoff({"pass": 1, "items": [
        {"finding_id": cid, "hunk": "@@ -3 +3 @@\n-x\n+y", "cause": "no guard"}]},
        at=AT, attempt=1)
    review.write_documents()
    text = (tmp_path / "run" / "FIXING_SPEC.md").read_text()
    assert "## Pass 1" in text and cid in text and "no guard" in text and "a.py:3" in text


def test_a_bad_item_records_no_part_of_the_pass(tmp_path):
    review, bridge = _rig(tmp_path)
    handout = review.handout(expert_id="correctness", stage="review", round=1,
                             attempt=1, at=AT, expert_instructions="Check",
                             review_input="diff")
    bridge.record_answer(bridge.track_tool_use("tool-p", handout["prompt"]),
        '{"severity":"HIGH","kind":"behavior","file":"a.py","line":10,"claim":"a","fix":"b"}\n'
        '{"severity":"HIGH","kind":"behavior","file":"a.py","line":12,"claim":"c","fix":"d"}',
        completed_at=AT)
    first, second = [row["canonical_id"] for row in review.findings()]
    with pytest.raises(ReviewError):
        review.record_fix_handoff({"pass": 1, "items": [
            {"finding_id": first, "hunk": "@@", "reclassify": {"from": "behavior",
                                                              "to": "docs", "reason": "r"}},
            {"finding_id": second, "hunk": ""}]}, at=AT, attempt=1)
    assert not any(e["kind"] in ("address", "reclassify") for e in review.store.read_events())


def test_the_handout_prompt_is_kept_whole_in_the_audit_area(tmp_path):
    """F-4: the full prompt is an audit blob, not only a file in the run directory."""
    review, _ = _rig(tmp_path)
    handout = review.handout(expert_id="correctness", stage="review", round=1, attempt=1,
                             at=AT, expert_instructions="Check", review_input="diff --git a b")
    blob = handout["event"]["payload"]["prompt"]["blob"]
    assert (review.store.paths.blobs / blob).read_text(encoding="utf-8") == handout["prompt"]
