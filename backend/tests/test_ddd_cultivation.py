"""CDF cultivation source tests.

The retired direct DDD writer and human proposal lifecycle are intentionally not
covered. These tests require stable enqueue identity and no corpus mutation.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.ddd_cultivation import (
    CultivationProposal,
    _cultivate_proposals,
    apply_to_ddd,
    write_proposal,
)


def _proposal() -> CultivationProposal:
    return CultivationProposal(
        target_doc="TECH.md",
        target_section="Conventions",
        content="Use one deterministic source of truth for routing decisions.",
        source_run_id="run-7",
        confidence=0.9,
    )


def test_cultivation_enqueues_stable_payload_without_writing(monkeypatch, tmp_path):
    from core.ingestion import api

    project = tmp_path / "Project"
    project.mkdir()
    calls = []
    monkeypatch.setattr(
        api, "enqueue",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(status="queued", candidate_id="candidate-1"),
    )
    before = list(project.rglob("*"))
    result = _cultivate_proposals([_proposal()], project)
    assert result["queued"] == 1 and result["applied"] == 0
    assert list(project.rglob("*")) == before
    args, kwargs = calls[0]
    assert args[:2] == ("cultivation", "run-7")
    assert args[2].startswith("proposal:")
    assert args[3]["kind"] == "cultivation"
    assert args[3]["proposal"]["doc_hint"] == "TECH.md"
    assert kwargs["envelope"]["tainted"] is True


def test_legacy_apply_compatibility_only_enqueues(monkeypatch, tmp_path):
    from core.ingestion import api

    monkeypatch.setattr(
        api, "enqueue",
        lambda *args, **kwargs: SimpleNamespace(status="queued", candidate_id="c"),
    )
    assert apply_to_ddd(_proposal(), tmp_path) == "queued"
    receipt = write_proposal(_proposal(), tmp_path)
    assert receipt.suffix == ".cdf"
    assert not receipt.exists()


def test_semantically_identical_proposals_use_same_slot(monkeypatch, tmp_path):
    from core.ingestion import api

    slots = []
    monkeypatch.setattr(
        api, "enqueue",
        lambda source, ref, slot, payload, **kwargs: slots.append(slot)
        or SimpleNamespace(status="duplicate", candidate_id="c"),
    )
    _cultivate_proposals([_proposal(), _proposal()], tmp_path)
    assert slots[0] == slots[1]
