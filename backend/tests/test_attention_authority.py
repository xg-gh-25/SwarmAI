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
    monkeypatch.setattr(aa, "_collect_job_digests", mk("digests", []))
    # db_recovery: default-empty in the aggregation fixture so the existing count
    # assertions (6 items) stay valid; its own behavior is covered in isolation.
    monkeypatch.setattr(aa, "_collect_db_recovery", mk("db_recovery", []))
    return state


def test_aggregates_all_sources(patched_sources):
    res = collect(Path("/tmp"))
    sources = {it.source for it in res.items}
    # 6 collectors wired; job_digest is patched-empty in this fixture (its
    # own behavior is covered by the _collect_job_digests isolation tests),
    # so it contributes no source here — the other 5 all appear.
    assert sources == {"escalation", "cultivation", "governance", "paused_run", "job"}
    assert "job_digest" not in sources  # patched-empty in this fixture
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


def test_db_recovery_stops_surfacing_when_resolved_and_never_writes(tmp_path, monkeypatch):
    """Anti-nag: once the user resolves (isolated file gone — recovered back into place
    OR discarded) the item stops surfacing, keyed on observable state. collect() is
    read-only (fixing spec G1): it leaves the marker as it is; write_recovery_marker
    prunes resolved entries on its next write."""
    from core.data_safety import RECOVERY_MARKER_NAME, write_recovery_marker
    isolated = tmp_path / "data.db.corrupt-x"  # NOTE: never created → "resolved"
    write_recovery_marker(tmp_path, isolated_path=isolated, reason="corrupt at boot")
    marker = tmp_path / RECOVERY_MARKER_NAME
    before = (marker.read_bytes(), marker.stat().st_mtime_ns)
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    assert aa._collect_db_recovery() == [], "resolved → no longer surfaces"
    assert marker.exists() and (marker.read_bytes(), marker.stat().st_mtime_ns) == before


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
    assert set(it.keys()) == {"id", "source", "tier", "brain", "title", "summary", "detail", "dispatch"}


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
    assert "ui_action" in names and "surface_run_outputs" not in names   # retired (R3)


# ── R3 闭环 dispatch: _collect_job_digests ──────────────────────────────────────
# A fresh, successful result of a job some DDD declares (bindings.yaml `kind: job`,
# indexed in .context/ddd_job_registry.json) surfaces as a REVIEW item owned by that
# DDD, telling the agent to learn from it and s_persist into that brain. Which jobs
# qualify comes only from the registry, never from a job-name pattern (P7).

import json
import textwrap
from datetime import datetime, timezone, timedelta
from core.attention_authority import _collect_job_digests, TIER_REVIEW


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


def _own(ws: Path, **jobs):
    (ws / ".context").mkdir(parents=True, exist_ok=True)
    (ws / ".context" / "ddd_job_registry.json").write_text(json.dumps({"version": 1, "jobs": [
        {"job_id": j, "owner_ddd": o} for j, o in jobs.items()]}))


def test_ddd_job_digest_surfaces_for_its_owner(tmp_path):
    jr = tmp_path / "Knowledge" / "JobResults"
    _own(tmp_path, **{"daily-scan": "Research"})
    fresh = datetime.now(timezone.utc) - timedelta(hours=2)
    _write_jobresult(jr, "2026-08-09-daily-scan.md", "daily-scan", "success", fresh)
    items = _collect_job_digests(tmp_path)
    assert len(items) == 1
    it = items[0]
    assert it.source == "job_digest"
    assert it.tier == TIER_REVIEW
    assert it.brain == "Research"
    msg = it.dispatch.get("message", "")
    assert "s_persist" in msg and "Research" in msg
    assert "daily-scan" in it.dispatch["context"]["path"]


def test_job_no_ddd_declares_is_not_a_digest(tmp_path):
    """No name pattern: a 'github-community-*' job no DDD declares does not surface."""
    jr = tmp_path / "Knowledge" / "JobResults"
    _own(tmp_path, **{"other": "Research"})
    fresh = datetime.now(timezone.utc) - timedelta(hours=1)
    _write_jobresult(jr, "a.md", "github-community-morning", "success", fresh)
    assert _collect_job_digests(tmp_path) == []
    (tmp_path / ".context" / "ddd_job_registry.json").unlink()
    assert _collect_job_digests(tmp_path) == []


def test_stale_digest_ages_out(tmp_path):
    jr = tmp_path / "Knowledge" / "JobResults"
    _own(tmp_path, **{"daily-scan": "Research"})
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    _write_jobresult(jr, "old.md", "daily-scan", "success", old)
    assert _collect_job_digests(tmp_path) == []


def test_failed_digest_not_surfaced(tmp_path):
    jr = tmp_path / "Knowledge" / "JobResults"
    _own(tmp_path, **{"daily-scan": "Research"})
    fresh = datetime.now(timezone.utc) - timedelta(hours=1)
    _write_jobresult(jr, "f.md", "daily-scan", "failed", fresh)
    assert _collect_job_digests(tmp_path) == []



def test_only_newest_digest_per_job(tmp_path):
    """Two fresh results of one job surface only the NEWEST (no stacking)."""
    jr = tmp_path / "Knowledge" / "JobResults"
    _own(tmp_path, **{"daily-scan": "Research"})
    now = datetime.now(timezone.utc)
    _write_jobresult(jr, "a.md", "daily-scan", "success", now - timedelta(hours=6))
    _write_jobresult(jr, "b.md", "daily-scan", "success", now - timedelta(hours=1))
    items = _collect_job_digests(tmp_path)
    assert len(items) == 1 and items[0].dispatch["context"]["path"].endswith("b.md")


def test_missing_jobresults_dir_returns_empty(tmp_path):
    _own(tmp_path, **{"daily-scan": "Research"})
    assert _collect_job_digests(tmp_path) == []


# --- governance card readability (the "governance rule" empty-card fix) ---

def _patch_governance(monkeypatch, proposals):
    """Drive _collect_governance with a fake eval service returning `proposals`."""
    class _FakeEval:
        def get_pending_governance(self):
            return {"proposals": proposals}

    import core.eval_service as es
    monkeypatch.setattr(es, "get_eval_service", lambda: _FakeEval())


def test_governance_title_names_the_mistake_in_plain_words(monkeypatch):
    """No explicit title → the title names the MISTAKE a person would recognise
    (from the class), not the producer's rule text or a bare type; the rule text
    and the examples go into the prompt; the summary says how often."""
    _patch_governance(monkeypatch, [{
        "id": "g1", "proposal_kind": "rule",
        "proposed_rule": "always verify a file exists before editing",
        "source_class": "CLASS_B", "occurrence_count": 3, "confidence": 0.82,
        "evidence": ["Edited a file without reading it first", "Guessed a path"],
    }])
    items = aa._collect_governance()
    assert len(items) == 1
    it = items[0]
    assert it.title == "The same mistake keeps happening: claims made without checking"
    assert it.summary == "Seen 3 times · SwarmAI suggests a new rule"
    msg = it.dispatch["message"]
    assert "always verify a file exists before editing" in msg
    assert "- Edited a file without reading it first" in msg
    assert "CLASS_B" not in it.title and "CLASS_B" not in msg.split("References:")[0]
    assert msg.rstrip().endswith("References: proposal g1 (82% confidence)")
    # detail explains WHY: human failure-pattern + recurrence + confidence
    assert "inferred without verifying" in it.detail
    assert "recurred 3×" in it.detail
    assert "82% confidence" in it.detail


def test_governance_gate_title_says_the_rule_is_not_working(monkeypatch):
    _patch_governance(monkeypatch, [{
        "id": "CLASS_C:gate", "proposal_kind": "gate", "source_class": "CLASS_C",
        "occurrence_count": 18, "proposed_rule": "CLASS_C: rule RULE_CLASS_C failed — escalate to a CODE GATE.",
    }])
    it = aa._collect_governance()[0]
    assert it.title == "A rule isn't working: fixes made in the wrong place still happen"
    assert it.summary == "Still seen 18 times after its rule · SwarmAI suggests an automatic check"


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
    """Even with no rule text or class, never fall back to a bare type."""
    _patch_governance(monkeypatch, [{"id": "g3", "proposal_kind": "rule"}])
    it = aa._collect_governance()[0]
    assert it.title == "SwarmAI suggests a new rule for a recurring mistake"
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
    assert t == "SwarmAI suggests a new rule for a recurring mistake"
    assert "['a'" not in aa._collect_governance()[0].dispatch["message"]


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


def test_recovery_item_for_a_non_db_store_is_store_specific(tmp_path, monkeypatch):
    """An isolated config.json/secrets.json must not be described as data.db
    (daemon connection pool, -wal/-shm) — the recovery steps are different."""
    from core.data_safety import write_recovery_marker
    isolated = tmp_path / "config.json.corrupt-x"
    isolated.write_text("{bad", encoding="utf-8")
    write_recovery_marker(tmp_path, isolated_path=isolated, reason="invalid JSON")
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    items = aa._collect_db_recovery()
    assert len(items) == 1
    it = items[0]
    assert it.tier == TIER_BLOCKING
    assert "config.json" in it.title
    assert "data.db" not in it.title and "data.db" not in it.dispatch["message"]
    assert str(isolated) in it.dispatch["message"]


def test_recovery_item_for_data_db_keeps_db_text(tmp_path, monkeypatch):
    from core.data_safety import write_recovery_marker
    isolated = tmp_path / "data.db.corrupt-x"
    isolated.write_text("preserved", encoding="utf-8")
    write_recovery_marker(tmp_path, isolated_path=isolated, reason="corrupt")
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    it = aa._collect_db_recovery()[0]
    assert it.title.startswith("Database recovery pending")
    assert "data.db" in it.dispatch["message"]


def test_could_not_isolate_entry_surfaces_until_file_parses(tmp_path, monkeypatch):
    """When a store could not be moved aside, the marker points at the original
    file; it must surface (the user has to act) and clear once the file parses."""
    from core.data_safety import write_recovery_marker
    original = tmp_path / "config.json"
    original.write_text("{bad", encoding="utf-8")
    write_recovery_marker(tmp_path, isolated_path=original, reason="could NOT be moved aside")
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    items = aa._collect_db_recovery()
    assert len(items) == 1 and "could not be preserved" in items[0].title
    original.write_text('{"aws_region": "x"}', encoding="utf-8")  # user fixes it
    assert aa._collect_db_recovery() == []


def test_repeated_entries_for_same_store_surface_once(tmp_path, monkeypatch):
    from core.data_safety import write_recovery_marker
    original = tmp_path / "config.json"
    original.write_text("{bad", encoding="utf-8")
    for _ in range(3):
        write_recovery_marker(tmp_path, isolated_path=original, reason="could NOT be moved aside")
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: tmp_path)
    assert len(aa._collect_db_recovery()) == 1
