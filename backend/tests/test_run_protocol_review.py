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
                                      lint_prompt, review_cache_key, reviewers,
                                      validate_fixing_spec)
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"


def _rig(tmp_path):
    authority = ev.EventAuthority(b"v" * 32)
    store = AuditStore(tmp_path / "audit", "run_one", authority)
    return ReviewOrchestrator(store, tmp_path / "run"), ReviewHookBridge(store, "session_1")


def test_roster_is_lightweight_and_security_is_risk_triggered():
    plain = [{"path": "backend/core/a.py", "insertions": 80, "deletions": 0}]
    assert [row["id"] for row in reviewers(profile="full", changed=plain)] == ["correctness"]
    risky = [{"path": "backend/core/security_hooks.py", "insertions": 60, "deletions": 0}]
    assert [row["id"] for row in reviewers(profile="full", changed=risky)] == [
        "correctness", "security", "red-team"]
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


def test_fixing_spec_covers_each_open_finding_exactly_once():
    findings = [{"canonical_id": "CF-aaaaaaaa", "status": "open"},
                {"canonical_id": "CF-bbbbbbbb", "status": "refuted"}]
    spec = {"pass": 1, "clusters": [{"id": "K1", "cause": "missing guard",
             "findings": ["CF-aaaaaaaa"], "sites": ["a.py:1"], "solution": "validate",
             "evidence_plan": [{"finding": "CF-aaaaaaaa", "type": "test", "ref": "t"}]}],
            "refuted": [], "reclassify": []}
    assert validate_fixing_spec(spec, findings) == spec
    broken = {**spec, "refuted": [{"finding": "CF-aaaaaaaa", "evidence": ["a.py:1"]}]}
    with pytest.raises(ReviewError, match="exactly once"):
        validate_fixing_spec(broken, findings)


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


def test_review_cache_key_binds_every_reuse_identity():
    args = dict(repo="r", code_tree="1" * 40, prompt_sha="2" * 64,
                expert_sha="3" * 64, model_id="m", pipeline_code_hash="4" * 64,
                protocol_version=1, rules_version="v1")
    base = review_cache_key(**args)
    for field in args:
        changed = dict(args); changed[field] = str(changed[field]) + "x"
        assert review_cache_key(**changed) != base


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
