"""Tests for AttentionAuthority — the unified Need You read-aggregation layer.

Covers the load-bearing logic (design 2026-08-08):
  - 5-source normalization into AttentionItem
  - tier assignment (BLOCKING vs REVIEW) per source + escalation level
  - brain attribution + governance=None (OS-level)
  - per-brain query EXCLUDES governance (the badge-lie fix)
  - job circuit-breaker threshold (>=3, not >0)
  - counts + BLOCKING-first ordering
  - fail-soft: a broken source does not blank the channel
"""
from __future__ import annotations

from pathlib import Path
import pytest

from core import attention_authority as aa
from core.attention_authority import (
    AttentionItem, collect, TIER_BLOCKING, TIER_REVIEW,
    _JOB_CIRCUIT_BREAKER_THRESHOLD,
)


def _item(source, tier, brain, iid="x"):
    return AttentionItem(id=f"{source}:{iid}", source=source, tier=tier, brain=brain, title="t")


@pytest.fixture
def patched_sources(monkeypatch):
    """Patch every collector so tests drive aggregation logic deterministically."""
    calls = {}

    def mk(name, ret):
        def _fn(*a, **k):
            calls[name] = True
            return list(ret)
        return _fn

    state = {
        "esc": [_item("escalation", TIER_BLOCKING, "SwarmAI", "e1"),
                _item("escalation", TIER_REVIEW, "CMHK", "e2")],
        "cult": [_item("cultivation", TIER_REVIEW, "SwarmAI", "c1")],
        "gov": [_item("governance", TIER_REVIEW, None, "g1")],
        "paused": [_item("paused_run", TIER_BLOCKING, "SwarmAI", "r1")],
        "jobs": [_item("job", TIER_BLOCKING, None, "j1")],
    }
    monkeypatch.setattr(aa, "_collect_escalations", mk("esc", state["esc"]))
    monkeypatch.setattr(aa, "_collect_cultivation", mk("cult", state["cult"]))
    monkeypatch.setattr(aa, "_collect_governance", mk("gov", state["gov"]))
    monkeypatch.setattr(aa, "_collect_paused_runs", mk("paused", state["paused"]))
    monkeypatch.setattr(aa, "_collect_jobs", mk("jobs", state["jobs"]))
    return state


def test_aggregates_all_five_sources(patched_sources):
    res = collect(Path("/tmp"))
    sources = {it.source for it in res.items}
    assert sources == {"escalation", "cultivation", "governance", "paused_run", "job"}
    assert len(res.items) == 6


def test_counts_split_by_tier(patched_sources):
    res = collect(Path("/tmp"))
    # BLOCKING: esc-e1, paused-r1, job-j1 = 3 ; REVIEW: esc-e2, cult-c1, gov-g1 = 3
    assert res.counts[TIER_BLOCKING] == 3
    assert res.counts[TIER_REVIEW] == 3


def test_blocking_items_sorted_first(patched_sources):
    res = collect(Path("/tmp"))
    tiers = [it.tier for it in res.items]
    # all BLOCKING must come before any REVIEW
    first_review = tiers.index(TIER_REVIEW)
    assert all(t == TIER_BLOCKING for t in tiers[:first_review])


def test_governance_has_no_brain(patched_sources):
    res = collect(Path("/tmp"))
    gov = [it for it in res.items if it.source == "governance"]
    assert gov and gov[0].brain is None


def test_per_brain_query_excludes_governance(patched_sources):
    """The badge-lie fix: a per-brain query returns that brain's items and does
    NOT include OS-level governance (brain=None)."""
    res = collect(Path("/tmp"), brain="SwarmAI")
    brains = {it.brain for it in res.items}
    assert brains == {"SwarmAI"}          # only SwarmAI, never None
    # SwarmAI items: escalation e1, cultivation c1, paused r1 = 3
    assert len(res.items) == 3
    assert not any(it.source == "governance" for it in res.items)


def test_per_brain_query_includes_escalation_not_just_cultivation(patched_sources):
    """Badge lie root cause was _pending_count only counting cultivation.
    A per-brain query MUST include escalation (+paused) too."""
    res = collect(Path("/tmp"), brain="SwarmAI")
    assert any(it.source == "escalation" for it in res.items)
    assert any(it.source == "paused_run" for it in res.items)


def test_broken_source_is_isolated_inside_the_collector(monkeypatch):
    """Fail-soft is enforced INSIDE each collector (its own try/except), so a
    genuinely broken source returns [] instead of propagating. Verify the REAL
    escalation collector swallows a broken dependency and returns []."""
    # Force the escalation source's inner dependency to raise; the collector's
    # own try/except must catch it and return [] (not propagate).
    import core.escalation as esc_mod
    monkeypatch.setattr(esc_mod, "get_open_escalations",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got = aa._collect_escalations(Path("/tmp"))
    assert got == []  # swallowed, channel not blanked


def test_paused_run_carries_real_run_id(monkeypatch):
    """Adversarial HIGH#2 regression: a paused-decision run must carry the REAL
    run id (from PipelineRunResponse.id / raw['id']), NOT an empty string. An
    empty id collides all paused items as 'paused_run:' and blanks the resume
    message. Drive the REAL _collect_paused_runs with a paused-decision raw run."""
    from schemas.pipeline_run import PipelineRunStatus

    class _Resp:
        status = PipelineRunStatus.PAUSED
        pause_kind = "decision"
        id = "run_realid123"
        requirement = "do the thing"

    import routers.pipelines as pl
    monkeypatch.setattr(pl, "_to_response", lambda raw: _Resp())
    raw = {"_project": "SwarmAI", "id": "run_realid123",
           "checkpoint": {"reason": "Gate-1 BLOCK: decide X?"}}
    items = aa._collect_paused_runs(pipeline_runs=[raw])
    assert len(items) == 1
    it = items[0]
    assert it.id == "paused_run:run_realid123"        # not "paused_run:"
    assert it.brain == "SwarmAI"
    assert it.tier == TIER_BLOCKING
    assert it.dispatch["context"]["run_id"] == "run_realid123"
    assert "run_realid123" in it.dispatch["message"]


def test_item_to_dict_shape(patched_sources):
    res = collect(Path("/tmp"))
    d = res.to_dict()
    assert "items" in d and "counts" in d
    it = d["items"][0]
    assert set(it.keys()) == {"id", "source", "tier", "brain", "title", "detail", "dispatch"}


def test_circuit_breaker_threshold_is_three():
    assert _JOB_CIRCUIT_BREAKER_THRESHOLD == 3


# ── FLOW 4: agent SENSE — format_attention_for_agent (the read-the-queue tool) ──


def _patch_collect(monkeypatch, result):
    """Patch attention_authority.collect (which format_attention_for_agent calls)."""
    import core.ui_actions as ui
    from core.attention_authority import AttentionResult
    monkeypatch.setattr(
        "core.attention_authority.collect",
        lambda ws, brain=None: result,
    )
    # also ensure SWARMWS import inside the helper doesn't explode
    return ui


def test_sense_format_empty_queue(monkeypatch):
    from core.attention_authority import AttentionResult
    _patch_collect(monkeypatch, AttentionResult(items=[], counts={"blocking": 0, "review": 0}))
    from core.ui_actions import format_attention_for_agent
    out = format_attention_for_agent()
    assert "nothing needs you" in out.lower()
    assert "0 items" in out


def test_sense_format_groups_by_tier_then_brain(monkeypatch):
    from core.attention_authority import AttentionResult
    items = [
        _item("escalation", TIER_BLOCKING, "SwarmAI", "e1"),
        _item("job", TIER_BLOCKING, None, "j1"),
        _item("cultivation", TIER_REVIEW, "SwarmAI", "c1"),
        _item("governance", TIER_REVIEW, None, "g1"),
    ]
    # give the dispatch message so the "to handle" line renders
    for it in items:
        it.dispatch = {"message": f"handle {it.id}"}
    _patch_collect(monkeypatch, AttentionResult(
        items=items, counts={"blocking": 2, "review": 2}))
    from core.ui_actions import format_attention_for_agent
    out = format_attention_for_agent()
    # tier headers present, BLOCKING before REVIEW
    assert out.index("BLOCKING") < out.index("REVIEW")
    # brain grouping: SwarmAI + OS-level both appear
    assert "SwarmAI" in out and "OS-level" in out
    # governance (brain=None) rendered under OS-level, not a real brain
    assert "handle governance:g1" in out
    # counts in header
    assert "2 blocking" in out and "2 review" in out


def test_sense_format_per_brain_scope_label(monkeypatch):
    from core.attention_authority import AttentionResult
    _patch_collect(monkeypatch, AttentionResult(
        items=[_item("escalation", TIER_BLOCKING, "SwarmAI", "e1")],
        counts={"blocking": 1, "review": 0}))
    from core.ui_actions import format_attention_for_agent
    out = format_attention_for_agent(brain="SwarmAI")
    assert "SwarmAI" in out


def test_sense_tool_registered_in_ui_server():
    """The sense_attention tool must be part of the swarm_ui MCP server (FLOW 4
    dead-seam fix — the ACT tool existed, the SENSE tool did not). Assert BOTH the
    name constants AND that the tool is actually wired into the built server — so a
    regression that drops it from the tools=[...] list goes RED (adversarial note)."""
    from core.ui_actions import (
        SENSE_ATTENTION_TOOL_NAME, SENSE_ATTENTION_FULL_TOOL_NAME, get_ui_mcp_server,
    )
    assert SENSE_ATTENTION_TOOL_NAME == "sense_attention"
    assert SENSE_ATTENTION_FULL_TOOL_NAME == "mcp__swarm_ui__sense_attention"
    server = get_ui_mcp_server()
    assert server is not None, "swarm_ui MCP server must build"
    # Assert the tool is actually wired into the built server (not just that the
    # name constant exists) — so a regression dropping it from tools=[...] goes RED.
    # The mcp lowlevel Server populates its _tool_cache lazily on the first
    # list_tools request; drive that request through the registered ListTools
    # handler (the public, version-stable path) and read the tool names.
    import asyncio
    from mcp.types import ListToolsRequest
    inst = server["instance"] if isinstance(server, dict) else getattr(server, "instance")
    handler = inst.request_handlers[ListToolsRequest]
    result = asyncio.new_event_loop().run_until_complete(handler(ListToolsRequest(method="tools/list")))
    names = {t.name for t in result.root.tools}
    assert "sense_attention" in names, (
        f"sense_attention must be registered in the swarm_ui server — got {names}"
    )
    # sibling tools still present (didn't clobber the list)
    assert {"ui_action", "surface_run_outputs"} <= names
