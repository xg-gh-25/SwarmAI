"""Gate-2 Red Team trigger (HIGH half) — classification, severity and cutover.

What is tested (pure pipeline_validator helpers, no filesystem):
- Grandfathering: runs created before RED_TEAM_CUTOVER (and no run at all) get the
  rule as a WARNING carrying the same text; runs at/after it are BLOCKED. Enforcement
  keys on run.json created_at, never on the presence of `specialist` /
  `red_team_dispatched` (omitting them must not dodge the gate).
- Producers: only Gate-2 registry ids (backend/experts/code/*.md minus red-team) and
  unattributed findings count as specialists; meta-review / skeptic / probes /
  convergence / doc-sync are exempt.
- Red Team labels ("Red-Team Specialist", "red-team (opus)", unattributed
  "Red Team: …") never self-trigger.
- Severity normalization (severity|sev|level|priority, labels, numeric >= 8) and the
  extra finding sources (specialists[].findings, iterations[].findings).
"""
from __future__ import annotations

import pytest

from scripts import pipeline_validator as pv

POST = "2026-10-19T00:00:00+00:00"
PRE = "2026-10-13T01:38:37.770517+00:00"


def _high(who="security", sev="HIGH", **kw):
    f = {"severity": sev, "confidence": 9, "resolved": True,
         "finding": "backend/a.py f() line 3: injection. Fixed: param query."}
    if who is not None:
        f["specialist"] = who
    f.update(kw)
    return f


def _ar(findings, **extra):
    return {"profile_tier": "full", "spawned": True, "findings": findings, **extra}


def _blocks(ar, created=POST):
    return pv._red_team_gate(ar, "full", run_created_at=created)


# ── HIGH 1: grandfather by run.json created_at ──────────────────────────────
@pytest.mark.parametrize("created,enforced", [
    (POST, True), (pv.RED_TEAM_CUTOVER, True), ("2026-10-18T06:00:00+00:00", True),
    ("2026-10-18T05:59:59+00:00", False), (PRE, False), (None, False),
    ("", True), ("not-a-date", True),          # a run with a bad stamp: fail-closed
    ("2026-10-19T00:00:00", True),             # naive → read as UTC
])
def test_cutover_decides_block_vs_warning(created, enforced):
    assert pv._red_team_enforced(created) is enforced
    warnings: list[str] = []
    errs = pv._red_team_gate(_ar([_high()]), "full", run_created_at=created,
                             warnings=warnings)
    msg = pv._red_team_trigger_errors(_ar([_high()]), "full")[0]
    if enforced:
        assert errs == [msg] and warnings == []
    else:
        assert errs == [] and len(warnings) == 1 and warnings[0].startswith(msg)
        assert "grandfathered" in warnings[0]


def test_omitting_new_fields_does_not_dodge_after_cutover():
    bare = {"severity": "HIGH", "resolved": True, "finding": "a.py x: y. Fixed: z."}
    assert _blocks(_ar([bare]))                      # no specialist, no red_team_dispatched
    assert not _blocks(_ar([bare]), created=PRE)


def test_publish_and_completion_gates_grandfather_the_same_way():
    import copy
    art = copy.deepcopy(pv.STAGE_TEMPLATES["deliver"])
    art["adversarial_review"].update(findings=[_high()], red_team_dispatched=False)
    for created in (PRE, None):
        w: list[str] = []
        assert not [e for e in pv.validate_artifact_data(
            "deliver", art, profile="full", run_created_at=created, warnings=w)
            if "Red Team required" in e]
        assert any("Red Team required" in x for x in w)
        w2: list[str] = []
        assert not [e for e in pv._check_depth("deliver", art, "full", run_created_at=created,
                                                warnings=w2) if "Red Team required" in e]
        assert any(x.startswith("Depth: Red Team required") for x in w2)


# ── MED 2: only Gate-2 registry ids (or unattributed) are specialists ───────
def test_registry_ids_come_from_the_expert_files():
    from pathlib import Path
    stems = {p.stem.replace("-", "") for p in
             (Path(pv.__file__).resolve().parent.parent / "experts" / "code").glob("*.md")}
    assert "correctness" in stems and "redteam" in stems
    ids = pv._gate2_specialist_ids()
    assert stems - {"redteam"} <= ids and "redteam" not in ids


@pytest.mark.parametrize("who", [
    "correctness", "Security", "api-contract", "API Contract", "Security Specialist",
    "Correctness Auditor (sonnet)", "state-machine", "gate2-concurrency", "ux-test",
    "spec-compliance", "code-quality", "正确性评审官", "", None,     # unattributed → counts
    ["performance", "meta-review"], "meta-review + integration",
])
def test_specialist_producers_trigger(who):
    assert _blocks(_ar([_high(who)]))


@pytest.mark.parametrize("who", [
    "meta-review", "Meta-Review", "meta agent", "gate1-skeptic", "Gate-0 Skeptic",
    "skeptic", "fresh-user", "fresh user probe", "user-value probe", "convergence",
    "convergence loop", "doc-sync", "completion-audit", "reviewer-x",
])
def test_non_specialist_producers_are_exempt(who):
    assert not _blocks(_ar([_high(who)]))


# ── MED 3: the Red Team's own HIGH never self-triggers ──────────────────────
@pytest.mark.parametrize("finding", [
    _high("red-team"), _high("Red Team"), _high("red_team"), _high(["red-team"]),
    _high("Red-Team Specialist"), _high("red-team (opus)"), _high("RedTeam round 2"),
    _high(None, finding="Red Team: bypass via symlink. Fixed: realpath."),
    _high(None, finding="[red-team round-1] TOCTOU on lock. Fixed."),
    _high("", finding="", summary="red team — replayed marker accepted. Fixed."),
])
def test_red_team_labels_do_not_self_trigger(finding):
    ar = _ar([finding])
    assert pv._specialist_high_findings(ar) == []
    assert pv._red_team_dispatched(ar)            # its own finding is the claim
    assert not _blocks(ar)


def test_red_team_text_label_needs_to_lead_and_needs_no_specialist():
    late = _high(None, finding="Fixed after the red team asked: a.py x.")
    assert _blocks(_ar([late]))                   # unattributed, label not leading
    labelled = _high("security", finding="Red Team: a.py x.")
    assert _blocks(_ar([labelled]))               # attribution wins over the text


def test_red_team_finding_satisfies_the_claim_for_other_highs():
    ar = _ar([_high("correctness"), _high("red-team (opus)", sev="LOW")])
    assert pv._red_team_triggered(ar, "full") and not _blocks(ar)


# ── LOW 5: severity normalization and every finding source ──────────────────
@pytest.mark.parametrize("field,value,high", [
    ("severity", "HIGH", True), ("severity", "high", True), ("severity", "CRITICAL", True),
    ("severity", "HIGH (conf 9)", True), ("severity", "HIGH-EQUIV (META-REVIEW CRITICAL)", True),
    ("severity", "BLOCKER", True), ("severity", "BLOCK", True), ("severity", "P0", True),
    ("severity", "P1", True), ("severity", 8, True), ("severity", 9.5, True),
    ("severity", "9/10", True), ("severity", " 8 ", True), ("sev", "HIGH", True),
    ("level", "critical", True), ("priority", "P1", True),
    ("severity", "MED", False), ("severity", "MEDIUM", False), ("severity", "LOW", False),
    ("severity", "LOW (was HIGH)", False), ("severity", "P2", False), ("severity", 7, False),
    ("severity", "7/10", False), ("severity", True, False), ("severity", "", False),
    ("severity", None, False), ("sev", "NIT", False),
])
def test_severity_normalization(field, value, high):
    f = {field: value, "finding": "x", "specialist": "security"}
    assert pv._finding_is_high(f) is high
    assert bool(_blocks(_ar([f]))) is high


def test_severity_prefers_severity_over_sev():
    assert not pv._finding_is_high({"severity": "LOW", "sev": "HIGH"})
    assert pv._finding_is_high({"severity": "", "sev": "HIGH"})


@pytest.mark.parametrize("ar,blocked", [
    ({"findings": [], "specialists": [{"name": "security", "findings": [
        {"severity": "HIGH", "finding": "x"}]}]}, True),            # inherits "security"
    ({"findings": [], "specialists": [{"expert_id": "meta-review", "findings": [
        {"severity": "HIGH", "finding": "x"}]}]}, False),           # inherits a non-specialist
    ({"findings": [], "specialists": [{"name": "meta-review", "findings": [
        {"severity": "HIGH", "finding": "x", "specialist": "correctness"}]}]}, True),
    ({"findings": [], "iterations": [{"round": 1, "findings": [
        {"severity": "CRITICAL", "finding": "x"}]}]}, True),        # unattributed → counts
    ({"findings": [], "iterations": [{"findings": [
        {"sev": "HIGH", "finding": "x", "specialist": "integration"}]}],
      "specialists": [{"name": "red-team", "findings": [
          {"severity": "LOW", "finding": "y"}]}]}, False),          # red team ran (nested)
    ({"findings": "not a list", "specialists": "x", "iterations": [None, 3]}, False),
])
def test_nested_finding_sources(ar, blocked):
    assert bool(_blocks(ar)) is blocked


@pytest.mark.parametrize("profile", ["trivial", "research"])
def test_relaxed_profiles_never_trigger(profile):
    assert pv._red_team_triggered(_ar([_high()]), profile) == []
    assert pv._red_team_gate(_ar([_high()]), profile, run_created_at=POST) == []
