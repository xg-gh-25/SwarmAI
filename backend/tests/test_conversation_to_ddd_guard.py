"""DoD1 (run_e346b8ed): the never-auto-apply guard for conversation→DDD.

宁缺毋滥 structural guarantee: a proposal with source_stage=="conversation" can
NEVER auto-apply — it is forced down the escalate branch in _cultivate_proposals
regardless of its target section, so it always requires XG's approve-time
confirmation. The "settled decision vs chatter" judgment is an LLM call upstream;
this guard is the structural backstop that a wrong judgment can't silently land.

NON-VACUOUS BY CONSTRUCTION: the test targets a SAFE-APPEND section
(IMPROVEMENT.md / "What to Watch For") — which a normal source WOULD auto-apply.
So the test only passes because the guard overrides auto-apply. Mutation:
remove the guard → the conversation proposal auto-applies → applied==1,
escalated==0 → test RED.
"""

import json
from pathlib import Path


from core.ddd_cultivation import (
    CultivationProposal,
    _cultivate_proposals,
    cultivate_from_conversation,
    SAFE_APPEND_SECTIONS,
)


def _project_dir(tmp_path: Path) -> Path:
    """A minimal project dir with the DDD docs + a proposals/ sink."""
    proj = tmp_path / "Projects" / "SwarmAI"
    (proj / ".artifacts" / "proposals").mkdir(parents=True, exist_ok=True)
    # Create the target doc with the safe-append section heading so a
    # non-guarded proposal COULD apply (makes the test non-vacuous).
    (proj / "IMPROVEMENT.md").write_text(
        "# IMPROVEMENT\n\n## What to Watch For\n\n"
    )
    (proj / "PROJECT.md").write_text("# PROJECT\n\n## Recent Decisions\n\n")
    return proj


def test_safe_append_target_is_a_real_safe_append():
    """Guard rails: confirm the target we use IS auto-appliable for a normal
    source — otherwise the guard test would pass vacuously."""
    assert "What to Watch For" in SAFE_APPEND_SECTIONS.get("IMPROVEMENT.md", set())


def test_conversation_proposal_to_safe_append_still_escalates(tmp_path):
    """The core guard: conversation source + safe-append target → ESCALATE, not apply."""
    proj = _project_dir(tmp_path)
    p = CultivationProposal(
        target_doc="IMPROVEMENT.md",
        target_section="What to Watch For",   # safe-append → would auto-apply
        content="Team decided to adopt approach X for the digest job.",
        source_run_id="conv_sess_1",
        confidence=0.3,
        source_stage="conversation",          # the load-bearing tag
    )
    result = _cultivate_proposals([p], proj)

    assert result["applied"] == 0, "conversation source must NEVER auto-apply"
    assert result["escalated"] == 1, "must be forced to the human-gate"
    # And the escalation actually wrote a proposal file for XG to approve/reject.
    proposals = list((proj / ".artifacts" / "proposals").glob("*.json"))
    assert len(proposals) == 1
    data = json.loads(proposals[0].read_text())
    assert data["source_stage"] == "conversation"


def test_non_conversation_source_to_same_target_auto_applies(tmp_path):
    """Control: the SAME safe-append target auto-applies for a normal source —
    proving the escalation above is caused by the source guard, not the target."""
    proj = _project_dir(tmp_path)
    p = CultivationProposal(
        target_doc="IMPROVEMENT.md",
        target_section="What to Watch For",
        content="A normal reflect lesson that should auto-apply.",
        source_run_id="run_x",
        confidence=0.9,
        source_stage="reflect",
    )
    result = _cultivate_proposals([p], proj)
    # Normal source + safe-append → applies (subject to auto-approval gate, which
    # is fail-open here). The point: escalated is NOT forced for non-conversation.
    assert result["escalated"] == 0 or result["applied"] >= 1


def test_cultivate_from_conversation_tags_every_proposal(tmp_path):
    """cultivate_from_conversation must set source_stage='conversation' on EVERY
    proposal (the guard fails open if a proposal defaults to 'reflect')."""
    proj = _project_dir(tmp_path)
    candidates = [
        {"content": "Decision A", "target_doc": "IMPROVEMENT.md",
         "target_section": "What to Watch For", "evidence": "XG: let's do A"},
        {"content": "Decision B", "target_doc": "PROJECT.md",
         "target_section": "Recent Decisions", "evidence": "XG: and B"},
    ]
    result = cultivate_from_conversation(candidates, "conv_sess_2", "SwarmAI", proj)

    assert result["applied"] == 0
    assert result["escalated"] == 2
    for f in (proj / ".artifacts" / "proposals").glob("*.json"):
        assert json.loads(f.read_text())["source_stage"] == "conversation"


def test_conversation_candidates_carry_evidence_quote(tmp_path):
    """Every escalated proposal must carry the source conversation quote so the
    human-gate reviewer can verify it (never extract without a traceable source)."""
    proj = _project_dir(tmp_path)
    candidates = [{"content": "We will cap retries at 3.",
                   "target_doc": "PROJECT.md", "target_section": "Recent Decisions",
                   "evidence": "XG: cap it at 3"}]
    cultivate_from_conversation(candidates, "conv_sess_3", "SwarmAI", proj)
    files = list((proj / ".artifacts" / "proposals").glob("*.json"))
    assert len(files) == 1
    content = json.loads(files[0].read_text())["content"]
    assert "XG: cap it at 3" in content, "evidence quote must be preserved for review"


def test_anti_flood_cap(tmp_path):
    """Conversation source is capped tighter than the general MAX (anti-flood)."""
    from core.ddd_cultivation import MAX_CONVERSATION_PROPOSALS_PER_RUN
    proj = _project_dir(tmp_path)
    candidates = [
        {"content": f"Decision {i}", "target_doc": "PROJECT.md",
         "target_section": "Recent Decisions", "evidence": f"q{i}"}
        for i in range(10)
    ]
    result = cultivate_from_conversation(candidates, "conv_sess_4", "SwarmAI", proj)
    assert result["escalated"] == MAX_CONVERSATION_PROPOSALS_PER_RUN


def test_escalated_conversation_proposal_surfaces_to_approval_ux(tmp_path):
    """LOW-3 (Gate-2, run_e346b8ed) — SHIP-BLOCKER regression: an escalated
    conversation proposal MUST be returned by read_pending_proposals (the shared
    consumer of both router GET /proposals and briefing L5). Before the fix,
    write_proposal persisted status='escalated' but read_pending_proposals
    filtered to status=='pending' only → the human-gate was a silent black hole
    (candidates escalated to disk, XG never saw them). This is THE test that
    proves the human-gate actually works."""
    from core.ddd_cultivation import read_pending_proposals
    proj = _project_dir(tmp_path)
    candidates = [{"content": "Decision to surface", "target_doc": "PROJECT.md",
                   "target_section": "Recent Decisions", "evidence": "XG: yes"}]
    result = cultivate_from_conversation(candidates, "conv_sess_5", "SwarmAI", proj)
    assert result["escalated"] == 1

    # workspace_dir is the tmp root (read_pending_proposals appends Projects/<p>/...)
    pending = read_pending_proposals(tmp_path, "SwarmAI")
    assert len(pending) == 1, "escalated conversation proposal must surface to the approval UX"
    assert pending[0].status == "escalated"
    assert "Decision to surface" in pending[0].content
