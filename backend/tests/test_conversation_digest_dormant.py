"""DoD3 (run_e346b8ed): dormant digest trigger + approve-time re-target.

Two structural guarantees:
  1. The conversation-digest job is DORMANT by default — with no opt-in config
     it returns `skipped` WITHOUT touching the DB, extractor, or LLM. This is
     how capability C ships inert (C037 §7).
  2. The cultivation approve endpoint honors an approve-time target override
     (§9-D1) — attribution is decided by XG at approve, not guessed at extract.
"""

import asyncio
import json
from pathlib import Path

import pytest

from jobs.handlers.conversation_digest import (
    run_conversation_digest,
    _load_enabled_channels,
)


# ── Dormant-by-default ────────────────────────────────────────────────────────

def test_no_config_is_dormant(tmp_path):
    """No config file → dormant (empty channel list)."""
    assert _load_enabled_channels(tmp_path) == []


def test_malformed_config_is_dormant(tmp_path):
    """A malformed / channel-less config → dormant, fail-closed."""
    cfg = tmp_path / "Services" / "swarm-jobs" / "conversation-digest.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("enabled_channels: not-a-list\n")
    assert _load_enabled_channels(tmp_path) == []


def test_non_dict_root_config_is_dormant(tmp_path):
    """LOW-1 (Gate-2): a yaml whose ROOT is a list/scalar (a natural mistake)
    must go dormant, NOT crash with AttributeError. Before the fix, .get() ran
    on a non-dict outside the try/except and propagated to the executor."""
    cfg = tmp_path / "Services" / "swarm-jobs" / "conversation-digest.yaml"
    cfg.parent.mkdir(parents=True)
    # root is a LIST, not a mapping
    cfg.write_text("- channel_session_id: cs_1\n  project: SwarmAI\n")
    assert _load_enabled_channels(tmp_path) == []
    # root is a scalar
    cfg.write_text("just a string\n")
    assert _load_enabled_channels(tmp_path) == []


def test_run_digest_dormant_touches_nothing(tmp_path):
    """With no opted-in channel the handler returns skipped and NEVER calls the
    DB, extractor, or cultivation sink."""
    calls = {"db": 0, "extract": 0, "cultivate": 0}

    class _DB:
        class channel_messages:
            @staticmethod
            async def list_by_session(sid):
                calls["db"] += 1
                return []

    def _extract(rows, project):
        calls["extract"] += 1
        return []

    def _cultivate(c, s, p, d):
        calls["cultivate"] += 1
        return {"escalated": 0}

    result = asyncio.run(run_conversation_digest(
        workspace=tmp_path, db=_DB(), extract_fn=_extract, cultivate_fn=_cultivate,
    ))
    assert result["status"] == "skipped"
    assert result["channels"] == 0
    assert calls == {"db": 0, "extract": 0, "cultivate": 0}, "dormant must touch nothing"


def test_run_digest_enabled_channel_flows_to_cultivation(tmp_path):
    """When a channel IS opted-in, rows → extractor → cultivation (escalated)."""
    cfg = tmp_path / "Services" / "swarm-jobs" / "conversation-digest.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "enabled_channels:\n"
        "  - channel_session_id: cs_1\n"
        "    project: SwarmAI\n"
    )

    class _DB:
        class channel_messages:
            @staticmethod
            async def list_by_session(sid):
                assert sid == "cs_1"
                return [{"direction": "inbound", "content": "XG: do X",
                         "metadata": {"sender_tier": "owner"}}]

    def _extract(rows, project):
        assert project == "SwarmAI"
        return [{"content": "Decision X", "evidence": "XG: do X"}]

    captured = {}

    def _cultivate(cands, sid, project, project_dir):
        captured["sid"] = sid
        captured["n"] = len(cands)
        return {"escalated": len(cands), "applied": 0}

    result = asyncio.run(run_conversation_digest(
        workspace=tmp_path, db=_DB(), extract_fn=_extract, cultivate_fn=_cultivate,
    ))
    assert result["status"] == "success"
    assert result["escalated"] == 1
    assert captured == {"sid": "cs_1", "n": 1}


def test_one_channel_failure_does_not_abort_others(tmp_path):
    """LOW-2 (Gate-2): a failure in extract/cultivate for one channel must not
    abort the remaining channels — the whole per-channel chain is isolated."""
    cfg = tmp_path / "Services" / "swarm-jobs" / "conversation-digest.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "enabled_channels:\n"
        "  - channel_session_id: cs_bad\n    project: SwarmAI\n"
        "  - channel_session_id: cs_good\n    project: SwarmAI\n"
    )

    class _DB:
        class channel_messages:
            @staticmethod
            async def list_by_session(sid):
                return [{"direction": "inbound", "content": "XG: x",
                         "metadata": {"sender_tier": "owner"}}]

    def _extract(rows, project):
        return [{"content": "c", "evidence": "e"}]

    seen = []

    def _cultivate(cands, sid, project, project_dir):
        seen.append(sid)
        if sid == "cs_bad":
            raise RuntimeError("write blew up on this channel")
        return {"escalated": 1, "applied": 0}

    result = asyncio.run(run_conversation_digest(
        workspace=tmp_path, db=_DB(), extract_fn=_extract, cultivate_fn=_cultivate,
    ))
    # cs_bad raised, but cs_good must still have been processed.
    assert "cs_good" in seen, "a failing channel must not abort the rest"
    assert result["status"] == "success"
    assert result["escalated"] == 1  # only cs_good's proposal


# ── Approve-time re-target (§9-D1) ────────────────────────────────────────────

def test_approve_retarget_overrides_proposal_target(tmp_path, monkeypatch):
    """approve_proposal applies to the OVERRIDDEN target when supplied."""
    from core.ddd_cultivation import CultivationProposal, write_proposal
    import routers.cultivation as cult

    proj = tmp_path / "Projects" / "SwarmAI"
    (proj / ".artifacts" / "proposals").mkdir(parents=True)
    (proj / "IMPROVEMENT.md").write_text("# IMPROVEMENT\n\n## What to Watch For\n\n")
    (proj / "PROJECT.md").write_text("# PROJECT\n\n## Recent Decisions\n\n")

    # A conversation proposal with a SUGGESTED target of PROJECT.md/Recent Decisions
    # Content is realistic (>= MIN_LESSON_LENGTH): apply_to_ddd enforces the value
    # floor on EVERY write path (run_e9cb7e2a chokepoint), incl. human-approved
    # proposals — a real approved proposal is a full sentence, not a 3-word stub.
    p = CultivationProposal(
        target_doc="PROJECT.md", target_section="Recent Decisions",
        content="Adopt the daily conversation digest job so decisions surface without manual review.",
        source_run_id="cs_1",
        confidence=0.3, source_stage="conversation",
    )
    write_proposal(p, proj)

    # Point the router's workspace resolution at tmp
    monkeypatch.setattr(
        cult.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
    )

    # Approve with an override target → should land in IMPROVEMENT.md / What to Watch For
    resp = asyncio.run(cult.approve_proposal(
        p.id, project="SwarmAI",
        target_doc="IMPROVEMENT.md", target_section="What to Watch For",
    ))
    assert resp["status"] == "applied"
    assert resp["target"] == "IMPROVEMENT.md#What to Watch For"
    assert "daily conversation digest" in (proj / "IMPROVEMENT.md").read_text()


def test_approve_without_override_keeps_suggested_target(tmp_path, monkeypatch):
    """A bare approve (no override) applies to the proposal's suggested target
    when that target is itself safe-appliable (IMPROVEMENT.md/What to Watch For).

    Note: apply_to_ddd re-checks is_safe_append (defense-in-depth), so a
    proposal suggesting a NON-safe doc (e.g. PROJECT.md) cannot be bare-approved
    — XG must re-target it to a safe section. That is the intended §9-D1 flow
    (XG decides the real destination), covered by the re-target test above."""
    from core.ddd_cultivation import CultivationProposal, write_proposal
    import routers.cultivation as cult

    proj = tmp_path / "Projects" / "SwarmAI"
    (proj / ".artifacts" / "proposals").mkdir(parents=True)
    (proj / "IMPROVEMENT.md").write_text("# IMPROVEMENT\n\n## What to Watch For\n\n")

    # Realistic content (>= MIN_LESSON_LENGTH) — see note in the retarget test above.
    p = CultivationProposal(
        target_doc="IMPROVEMENT.md", target_section="What to Watch For",
        content="Keep the digest proposal's suggested target when the human approves without an override.",
        source_run_id="cs_2",
        confidence=0.3, source_stage="conversation",
    )
    write_proposal(p, proj)
    monkeypatch.setattr(
        cult.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
    )

    # Pass None explicitly — FastAPI injects None at runtime; a direct call
    # would otherwise receive the Query(...) sentinel objects.
    resp = asyncio.run(cult.approve_proposal(
        p.id, project="SwarmAI", target_doc=None, target_section=None,
    ))
    assert resp["target"] == "IMPROVEMENT.md#What to Watch For"
    assert "suggested target when the human approves" in (proj / "IMPROVEMENT.md").read_text()
