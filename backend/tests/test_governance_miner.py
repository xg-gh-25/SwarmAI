"""Eval governance source tests for CDF stage B.

Eligible GC rows are read, submitted with stable identities, and never mutated.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.evolution import governance_miner as gm


CONTENT = """## Governance Candidates

| ID | Proposed Rule | Evidence | Count | Status | Checked |
|---|---|---|---|---|---|
| GC1 | \"Always verify generated artifacts\" | run-a, run-b | 3/5 | pending | yes |
| GC2 | \"Too early\" | run-c | 1/5 | pending | yes |
"""


def test_generation_remains_read_only(tmp_path):
    path = tmp_path / "EVOLUTION.md"
    path.write_text(CONTENT)
    proposals = gm.generate_governance_proposals(path)
    assert [proposal.gc_id for proposal in proposals] == ["GC1"]
    assert path.read_text() == CONTENT


def test_eligible_governance_candidates_form_one_s5_aggregate(monkeypatch, tmp_path):
    from core.ingestion.sources import s5_metrics

    path = tmp_path / "EVOLUTION.md"
    path.write_text(CONTENT)
    calls = []
    monkeypatch.setattr(
        s5_metrics, "extract_metrics",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [SimpleNamespace(status="queued")],
    )
    assert gm.enqueue_governance_proposals(path) == 1
    args, kwargs = calls[0]
    assert args[0] == "governance-candidates"
    assert args[1]["candidate_count"] == 1
    assert args[1]["maximum_occurrences"] == 3
    assert kwargs["run_count"] == 3
    assert path.read_text() == CONTENT


def test_legacy_human_verdict_marker_is_read_only(tmp_path):
    path = tmp_path / "EVOLUTION.md"
    path.write_text(CONTENT)
    assert gm.mark_gc_decision(path, "GC1", accepted=True) is False
    assert path.read_text() == CONTENT
