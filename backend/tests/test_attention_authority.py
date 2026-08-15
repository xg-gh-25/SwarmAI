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
    # R3 6th source: default-empty in the aggregation fixture so the existing
    # count assertions (6 items) stay valid; exercised in isolation below.
    monkeypatch.setattr(aa, "_collect_community_digests", mk("digests", []))
    # db_recovery: default-empty in the aggregation fixture so the existing count
    # assertions (6 items) stay valid; its own behavior is covered in isolation.
    monkeypatch.setattr(aa, "_collect_db_recovery", mk("db_recovery", []))
    return state


def test_aggregates_all_sources(patched_sources):
    res = collect(Path("/tmp"))
    sources = {it.source for it in res.items}
    # 6 collectors wired; community_digest is patched-empty in this fixture (its
    # own behavior is covered by the _collect_community_digests isolation tests),
    # so it contributes no source here — the other 5 all appear.
    assert sources == {"escalation", "cultivation", "governance", "paused_run", "job"}
    assert "community_digest" not in sources  # patched-empty in this fixture
    assert len(res.items) == 6  # 2 esc + 1 cult + 1 gov + 1 paused + 1 job


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


def test_db_recovery_collector_surfaces_pending_marker(tmp_path, monkeypatch):
    """run_a456640f (option B): a boot-time DB-recovery marker must surface as a
    BLOCKING attention item so the recover-vs-discard decision reaches the user
    in-band at the next session (STEERING #20 'reach the human')."""
    from core.data_safety import write_recovery_marker
    isolated = tmp_path / "data.db.corrupt-x"
    isolated.write_text("preserved data")  # pending: isolated file still present
    write_recovery_marker(tmp_path, isolated_path=isolated,
                          reason="Corrupt data.db isolated at boot; fresh store seeded")
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    items = aa._collect_db_recovery()
    assert len(items) == 1
    it = items[0]
    assert it.source == "db_recovery"
    assert it.tier == TIER_BLOCKING
    assert it.brain is None  # OS-level, not a single brain
    assert "recover" in it.dispatch["message"].lower()
    assert it.dispatch["context"]["kind"] == "db_recovery"


def test_db_recovery_collector_empty_when_no_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    assert aa._collect_db_recovery() == []


def test_db_recovery_surfaces_while_isolated_file_exists(tmp_path, monkeypatch):
    """Pending decision: isolated file present → surfaces + marker kept."""
    from core.data_safety import write_recovery_marker, read_recovery_marker
    isolated = tmp_path / "data.db.corrupt-x"
    isolated.write_text("preserved data")
    write_recovery_marker(tmp_path, isolated_path=isolated, reason="corrupt at boot")
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    assert len(aa._collect_db_recovery()) == 1
    assert read_recovery_marker(tmp_path) is not None, "marker kept while pending"


def test_db_recovery_self_clears_when_decision_resolved(tmp_path, monkeypatch):
    """Anti-nag (MEDIUM review finding): once the user resolves (isolated file gone
    — recovered back into place OR discarded), the collector auto-clears the marker
    and stops surfacing. Keyed on observable state, not agent discipline."""
    from core.data_safety import write_recovery_marker, read_recovery_marker
    isolated = tmp_path / "data.db.corrupt-x"  # NOTE: never created → "resolved"
    write_recovery_marker(tmp_path, isolated_path=isolated, reason="corrupt at boot")
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    assert aa._collect_db_recovery() == [], "resolved → no longer surfaces"
    assert read_recovery_marker(tmp_path) is None, "marker auto-cleared on resolution"


def test_db_recovery_collector_failsoft(monkeypatch):
    """A broken app-data-dir resolver must not blank the channel — returns []."""
    monkeypatch.setattr(aa, "get_app_data_dir",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert aa._collect_db_recovery() == []


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


# ── R3 闭环 dispatch: _collect_community_digests ──────────────────────────────
# A fresh community daily-digest / weekly-report surfaces as a REVIEW item whose
# dispatch.message tells the agent to LEARN + s_persist from it — closing the loop
# (job → 🔔 → click → digest in chat → sediment). Time-windowed (read-only, no
# marker file): a digest ages out of the window naturally, so no perpetual noise.

import textwrap
from datetime import datetime, timezone, timedelta
from core.attention_authority import _collect_community_digests, TIER_REVIEW


def _write_jobresult(dir_: Path, name: str, job_id: str, status: str, run_at: datetime):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(textwrap.dedent(f"""\
        ---
        job_id: {job_id}
        job_name: {job_id}
        run_at: {run_at.isoformat()}
        status: {status}
        ---

        ## Digest body
        Published 4 comments today.
    """))


def test_community_digest_surfaces_as_review(tmp_path):
    """A recent SUCCESSFUL community digest → one REVIEW AttentionItem with a
    learn+persist dispatch.message."""
    jr = tmp_path / "Knowledge" / "JobResults"
    fresh = datetime.now(timezone.utc) - timedelta(hours=2)
    _write_jobresult(jr, "2026-08-09-github-community-morning.md",
                     "github-community-morning", "success", fresh)
    items = _collect_community_digests(tmp_path)
    assert len(items) == 1
    it = items[0]
    assert it.source == "community_digest"
    assert it.tier == TIER_REVIEW  # a digest is review-worthy, never blocking
    msg = it.dispatch.get("message", "").lower()
    assert "learn" in msg or "persist" in msg or "沉淀" in msg
    assert "github-community-morning" in it.dispatch.get("context", {}).get("path", "") \
        or "github-community-morning" in it.dispatch.get("message", "")


def test_stale_digest_ages_out(tmp_path):
    """A digest older than the window does NOT surface (no perpetual noise —
    the window IS the state, no read-marker needed)."""
    jr = tmp_path / "Knowledge" / "JobResults"
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    _write_jobresult(jr, "2026-08-07-github-community-morning.md",
                     "github-community-morning", "success", old)
    assert _collect_community_digests(tmp_path) == []


def test_failed_digest_not_surfaced(tmp_path):
    """A FAILED community job is NOT a digest-to-review (that's _collect_jobs'
    circuit-breaker concern) — only status:success digests surface here."""
    jr = tmp_path / "Knowledge" / "JobResults"
    fresh = datetime.now(timezone.utc) - timedelta(hours=1)
    _write_jobresult(jr, "2026-08-09-github-community-evening.md",
                     "github-community-evening", "failed", fresh)
    assert _collect_community_digests(tmp_path) == []


def test_non_community_jobresult_ignored(tmp_path):
    """Only community digests/reports surface — an unrelated job result is ignored."""
    jr = tmp_path / "Knowledge" / "JobResults"
    fresh = datetime.now(timezone.utc) - timedelta(hours=1)
    _write_jobresult(jr, "2026-08-09-memory-health.md", "memory-health", "success", fresh)
    assert _collect_community_digests(tmp_path) == []


def test_only_newest_digest_per_job(tmp_path):
    """If a job produced 2 fresh digests, surface only the NEWEST (avoid stacking
    the same job's review items)."""
    jr = tmp_path / "Knowledge" / "JobResults"
    now = datetime.now(timezone.utc)
    _write_jobresult(jr, "2026-08-09-github-community-morning.md",
                     "github-community-morning", "success", now - timedelta(hours=6))
    _write_jobresult(jr, "2026-08-09b-github-community-morning.md",
                     "github-community-morning", "success", now - timedelta(hours=1))
    items = _collect_community_digests(tmp_path)
    assert len(items) == 1  # newest only


def test_missing_jobresults_dir_returns_empty(tmp_path):
    """No JobResults dir → [] (fail-soft, never crash the channel)."""
    assert _collect_community_digests(tmp_path) == []


# --- governance card readability (the "governance rule" empty-card fix) ---

def _patch_governance(monkeypatch, proposals):
    """Drive _collect_governance with a fake eval service returning `proposals`."""
    class _FakeEval:
        def get_pending_governance(self):
            return {"proposals": proposals}

    import core.eval_service as es
    monkeypatch.setattr(es, "get_eval_service", lambda: _FakeEval())


def test_governance_title_built_from_proposed_rule(monkeypatch):
    """No explicit title → the card title is built from the actual rule text,
    NOT the useless bare type 'governance rule'."""
    _patch_governance(monkeypatch, [{
        "id": "g1", "proposal_kind": "rule",
        "proposed_rule": "always verify a file exists before editing",
        "source_class": "CLASS_B", "occurrence_count": 3, "confidence": 0.82,
    }])
    items = aa._collect_governance()
    assert len(items) == 1
    it = items[0]
    assert it.title == "New rule: always verify a file exists before editing"
    assert it.title != "governance rule"          # the old empty-card bug
    # detail explains WHY: human failure-pattern + recurrence + confidence
    assert "inferred without verifying" in it.detail
    assert "recurred 3×" in it.detail
    assert "82% confidence" in it.detail


def test_governance_explicit_title_wins(monkeypatch):
    """An explicit title/summary is respected as-is."""
    _patch_governance(monkeypatch, [{
        "id": "g2", "proposal_kind": "gate",
        "title": "Block edits without a prior Read",
        "proposed_rule": "…", "source_class": "CLASS_A",
    }])
    items = aa._collect_governance()
    assert items[0].title == "Block edits without a prior Read"


def test_governance_no_rule_text_is_still_labeled(monkeypatch):
    """Even with no rule text, never fall back to a bare type — say so explicitly."""
    _patch_governance(monkeypatch, [{"id": "g3", "proposal_kind": "rule"}])
    it = aa._collect_governance()[0]
    assert it.title == "New governance rule (no rule text)"
    assert it.detail  # still carries the class phrase ("recurring pattern")


def test_governance_malformed_fields_do_not_leak(monkeypatch):
    """Adversarial hardening: producer fields are untrusted. bool occurrence_count
    must NOT render 'recurred True×'; out-of-range confidence must be clamped; a
    non-string proposed_rule must NOT leak a Python repr into the title."""
    # bool occurrence_count (bool is an int subclass — the HIGH finding)
    _patch_governance(monkeypatch, [{
        "id": "b", "proposal_kind": "rule", "proposed_rule": "x",
        "source_class": "CLASS_A", "occurrence_count": True, "confidence": True,
    }])
    d = aa._collect_governance()[0].detail
    assert "True×" not in d and "recurred True" not in d
    assert "100% confidence" not in d  # confidence=True must not become 100%

    # out-of-range confidence is clamped to [0,1]
    _patch_governance(monkeypatch, [{
        "id": "c", "proposal_kind": "rule", "proposed_rule": "x",
        "source_class": "CLASS_A", "confidence": 1.5,
    }])
    assert "150%" not in aa._collect_governance()[0].detail

    # float occurrence_count is honored (symmetric with confidence), rendered int
    _patch_governance(monkeypatch, [{
        "id": "f", "proposal_kind": "rule", "proposed_rule": "x",
        "source_class": "CLASS_A", "occurrence_count": 3.0,
    }])
    assert "recurred 3×" in aa._collect_governance()[0].detail

    # non-string proposed_rule must not leak a repr — falls back to the safe label
    _patch_governance(monkeypatch, [{
        "id": "l", "proposal_kind": "rule", "proposed_rule": ["a", "b"],
    }])
    t = aa._collect_governance()[0].title
    assert "[" not in t and "'" not in t
    assert t == "New governance rule (no rule text)"


def test_governance_nonfinite_occurrence_does_not_abort_loop(monkeypatch):
    """2nd-round adversarial: int(inf)/int(nan) would throw and, inside the single
    try/except over the proposals loop, silently drop EVERY remaining card. A
    poisoned proposal must not take out its siblings."""
    _patch_governance(monkeypatch, [
        {"id": "bad", "proposal_kind": "rule", "proposed_rule": "x",
         "source_class": "CLASS_A", "occurrence_count": float("inf")},
        {"id": "nan", "proposal_kind": "rule", "proposed_rule": "y",
         "source_class": "CLASS_A", "occurrence_count": float("nan")},
        {"id": "good", "proposal_kind": "rule", "proposed_rule": "z",
         "source_class": "CLASS_A", "occurrence_count": 5},
    ])
    items = aa._collect_governance()
    # all three survive (no exception), and inf/nan just omit the recurred clause
    ids = {i.id for i in items}
    assert ids == {"governance:bad", "governance:nan", "governance:good"}
    bad = next(i for i in items if i.id == "governance:bad")
    assert "recurred" not in bad.detail  # non-finite → clause dropped, no crash
    good = next(i for i in items if i.id == "governance:good")
    assert "recurred 5×" in good.detail
