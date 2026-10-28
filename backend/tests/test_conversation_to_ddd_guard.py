"""Conversation-to-DDD extraction hands candidates to CDF without writing files.

The tests capture ``core.ingestion.api.enqueue`` and assert stable semantic slots,
complete cultivation payloads, conservative provenance, project snapshots, and the
conversation anti-flood cap. The source must never create proposal or DDD content.
"""

from pathlib import Path

from core.ddd_cultivation import CultivationProposal, _cultivate_proposals, cultivate_from_conversation
from core.ingestion.api import EnqueueResult


def _project_dir(tmp_path: Path) -> tuple[Path, bytes]:
    proj = tmp_path / "Projects" / "SwarmAI"
    proj.mkdir(parents=True)
    doc = proj / "IMPROVEMENT.md"
    doc.write_text("# IMPROVEMENT\n\n## What to Watch For\n\n")
    return proj, doc.read_bytes()


def _capture(monkeypatch):
    calls = []

    def enqueue(*args, **kwargs):
        calls.append((args, kwargs))
        return EnqueueResult(status="queued", candidate_id=f"cand-{len(calls)}")

    monkeypatch.setattr("core.ingestion.api.enqueue", enqueue)
    return calls


def test_conversation_proposal_enqueues_and_never_writes_ddd(tmp_path, monkeypatch):
    proj, before = _project_dir(tmp_path)
    calls = _capture(monkeypatch)
    result = _cultivate_proposals([CultivationProposal(
        target_doc="IMPROVEMENT.md", target_section="What to Watch For",
        content="Team decided to adopt approach X for the digest job.",
        source_run_id="conv_sess_1", confidence=0.3, source_stage="conversation",
    )], proj)

    assert result["queued"] == 1 and result["applied"] == result["escalated"] == 0
    args, kwargs = calls[0]
    assert args[0:2] == ("cultivation", "conv_sess_1")
    assert args[2].startswith("proposal:")
    assert args[3] == {"kind": "cultivation", "proposal": {
        "doc_hint": "IMPROVEMENT.md", "section_hint": "What to Watch For",
        "text": "Team decided to adopt approach X for the digest job.",
        "why": "conversation evidence for project SwarmAI; source confidence 0.30",
    }}
    assert kwargs["envelope"] == {"source_event_ids": [], "evidence": "inferred",
                                  "tainted": True, "writer": "cultivation"}
    assert kwargs["project_snapshot"]["project"] == "SwarmAI"
    assert (proj / "IMPROVEMENT.md").read_bytes() == before
    assert not (proj / ".artifacts" / "proposals").exists()


def test_non_conversation_source_uses_the_same_enqueue_boundary(tmp_path, monkeypatch):
    proj, before = _project_dir(tmp_path)
    calls = _capture(monkeypatch)
    result = _cultivate_proposals([CultivationProposal(
        target_doc="IMPROVEMENT.md", target_section="What to Watch For",
        content="A normal reflect lesson is still judged by CDF.",
        source_run_id="run_x", confidence=0.9, source_stage="reflect",
    )], proj)

    assert result["queued"] == 1
    assert calls[0][0][0] == "cultivation"
    assert (proj / "IMPROVEMENT.md").read_bytes() == before


def test_cultivate_from_conversation_enqueues_every_candidate(tmp_path, monkeypatch):
    proj, _ = _project_dir(tmp_path)
    calls = _capture(monkeypatch)
    candidates = [
        {"content": "Decision A", "target_doc": "IMPROVEMENT.md",
         "target_section": "What to Watch For", "evidence": "XG: let's do A"},
        {"content": "Decision B", "target_doc": "PROJECT.md",
         "target_section": "Recent Decisions", "evidence": "XG: and B"},
    ]

    result = cultivate_from_conversation(candidates, "conv_sess_2", "SwarmAI", proj)

    assert result["queued"] == 2
    assert [call[0][0] for call in calls] == ["cultivation", "cultivation"]
    assert all("conversation evidence" in call[0][3]["proposal"]["why"] for call in calls)


def test_conversation_candidates_keep_the_evidence_quote(tmp_path, monkeypatch):
    proj, _ = _project_dir(tmp_path)
    calls = _capture(monkeypatch)
    cultivate_from_conversation([
        {"content": "We will cap retries at 3.", "target_doc": "PROJECT.md",
         "target_section": "Recent Decisions", "evidence": "XG: cap it at 3"},
    ], "conv_sess_3", "SwarmAI", proj)

    assert "XG: cap it at 3" in calls[0][0][3]["proposal"]["text"]


def test_anti_flood_cap_limits_enqueued_candidates(tmp_path, monkeypatch):
    from core.ddd_cultivation import MAX_CONVERSATION_PROPOSALS_PER_RUN

    proj, _ = _project_dir(tmp_path)
    calls = _capture(monkeypatch)
    candidates = [
        {"content": f"Decision {i}", "target_doc": "PROJECT.md",
         "target_section": "Recent Decisions", "evidence": f"q{i}"}
        for i in range(10)
    ]

    result = cultivate_from_conversation(candidates, "conv_sess_4", "SwarmAI", proj)

    assert result["queued"] == MAX_CONVERSATION_PROPOSALS_PER_RUN
    assert len(calls) == MAX_CONVERSATION_PROPOSALS_PER_RUN


def test_semantic_slot_is_stable_for_replayed_candidate(tmp_path, monkeypatch):
    proj, _ = _project_dir(tmp_path)
    calls = _capture(monkeypatch)
    candidate = {"content": "Decision to surface", "target_doc": "PROJECT.md",
                 "target_section": "Recent Decisions", "evidence": "XG: yes"}

    cultivate_from_conversation([candidate], "conv_sess_5", "SwarmAI", proj)
    cultivate_from_conversation([candidate], "conv_sess_5", "SwarmAI", proj)

    assert calls[0][0][2] == calls[1][0][2]
    assert calls[0][0][3] == calls[1][0][3]
