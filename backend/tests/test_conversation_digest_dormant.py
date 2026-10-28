"""Conversation digest remains dormant until channels are explicitly enabled.

The active-path tests cover per-channel extraction isolation. Stage B removed the
cultivation approval and approve-time retarget lifecycle; those assertions have no
replacement because CDF performs autonomous routing and disposition.
"""

import asyncio


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
