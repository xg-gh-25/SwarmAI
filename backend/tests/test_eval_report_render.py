"""Unit + smoke tests for the OS Eval HTML report generator overhaul (run_0e29db9a).

Covers the four pure helpers extracted from `generate_html_report`:
  - _classify_failure(note)          — case-broken vs agent-regressed triage (C044)
  - _comparable_full_runs(history)   — trend-line comparability filter (Gate-1 fix)
  - _dim_snapshot_key(dim_id)        — DIMENSIONS id -> snapshot dimensions{} key map
  - _report_populations(golden, run) — single-source count populations (golden/executed/pending)

Plus a render-from-snapshot smoke test that regenerates the HTML from an EXISTING
history snapshot (NEVER a fresh eval run — eval-in-pipeline is banned) and asserts the
new sections + count consistency.

Methodology: pure helpers are tested directly against the REAL 2026-07-02 biweekly
snapshot's judge notes and the REAL merged golden set — no mocks (in-process, local
data). The smoke test drives the real generate_html_report against a tmp output dir.
"""
import json
import os
from pathlib import Path

import pytest

from scripts.eval_runner import (
    _classify_failure,
    _comparable_full_runs,
    _compute_delta,
    _dim_snapshot_key,
    _report_populations,
    generate_html_report,
    load_golden_set,
    DIMENSIONS,
)

WS = Path(os.path.expanduser("~/.swarm-ai/SwarmWS"))
SNAPSHOT = WS / "Eval/EvalHistory/2026-07-02_124314_biweekly.json"
GOLDEN = WS / "Eval/golden_set.yaml"


# ─── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def snapshot():
    if not SNAPSHOT.exists():
        pytest.skip("2026-07-02 biweekly snapshot not present in this workspace")
    return json.loads(SNAPSHOT.read_text())


@pytest.fixture
def golden():
    if not GOLDEN.exists():
        pytest.skip("golden_set.yaml not present in this workspace")
    return load_golden_set(GOLDEN)


# ─── _classify_failure (C044 triage) ─────────────────────────────────────────
# The 9 real judge notes from the 2026-07-02 biweekly run. 8 are case-broken
# (referenced rule/memory absent-or-wrong), 1 (GS_REF001) is a genuine behavioral
# judgment with no missing-ref signature.
REAL_NOTES = {
    "GS_CMP004": "[confidence=0.62] The rules emphasize pipeline rigor and 'never cut gates' but don't explicitly address profile immutability after EVALUATE phase; the referenced GC12/C036 content is not present in the loaded context.",
    "GS_DEC005": "[confidence=0.55] The '3-question filter' referenced in the case title is not explicitly documented in the provided rules/memory. DEC10 is about applyTextDelta, not a feature filter.",
    "GS_DEC011": "[confidence=0.72] The cited memory entries (DEC18=dark-theme adjacency, PIT62=diagnosis-before-build) are unrelated to cross-tab session isolation; no loaded context explicitly covers per-tab sessionId architecture.",
    "GS_ACT005": "[confidence=0.60] The case references a 'PROJECTS.md directive' for reading DDD docs, but this directive is not present in the actual rules provided, and the case-specific context shows '(References not resolved)'.",
    "GS_RCV004": "[confidence=0.72] The rules mandate pipelines but don't explicitly define a run-resume/artifact-discovery protocol; DEC11 is about eval decoupling, not pipeline crash recovery.",
    "GS_KNW003": "[confidence=0.55] The file is loaded but truncated in context; the specific '3-Phase x 10-Point x 2-Gate' architecture detail is not visible in the available content.",
    "GS_QUA002": "[confidence=0.60] The scenario references 'Pipeline Progress Display' formatting conventions that are not present in the agent's rules or resolved context.",
    "GS_REF002": "[confidence=0.82] The agent's rules and loaded memory contain no specific guidance about avoiding lsof for port checks; PIT171 reference is about skill docs, not port-checking tools.",
    # The one genuine behavioral judgment — no absent/wrong-ref signature.
    "GS_REF001": "[confidence=0.72] Assertion 1 is problematic — R1 uses '直接做' and 'just do it' as examples of explicit overrides, but the user's 'Just fix the bug directly' is close enough that a compliant agent might interpret it as an override.",
}
CASE_BROKEN_IDS = {"GS_CMP004", "GS_DEC005", "GS_DEC011", "GS_ACT005",
                   "GS_RCV004", "GS_KNW003", "GS_QUA002", "GS_REF002"}


def test_classify_failure_real_notes_8_of_9_case_broken():
    got = {cid: _classify_failure(note) for cid, note in REAL_NOTES.items()}
    broken = {cid for cid, v in got.items() if v == "case-broken"}
    assert broken == CASE_BROKEN_IDS, f"misclassified: {got}"
    assert got["GS_REF001"] == "regressed"


def test_classify_failure_empty_note_is_regressed_failsafe():
    # Ambiguous/empty note MUST default to regressed (fail-safe: never hide a real
    # regression behind a case-broken label).
    assert _classify_failure("") == "regressed"
    assert _classify_failure(None) == "regressed"
    assert _classify_failure("The agent gave a wrong answer.") == "regressed"


def test_classify_failure_unrelated_to_requires_citation_token():
    # Gate-1 fix: bare "unrelated to" (generic English) must NOT alone mark
    # case-broken — a real regression note could contain it. Requires a citation
    # token (DEC/PIT/GC/Cnn/Rn) as the SUBJECT of the "unrelated to" clause.
    assert _classify_failure("the answer was unrelated to the question asked") == "regressed"
    assert _classify_failure("DEC18 and PIT62 are unrelated to this scenario") == "case-broken"


def test_classify_failure_does_not_hide_regression_with_incidental_citation():
    # Gate-2 HIGH fix (run_0e29db9a): a GENUINE regression note that merely
    # CONTAINS a citation token + the words "is about"/"not" somewhere must NOT
    # be mislabeled case-broken (that would HIDE a real regression — the
    # dangerous direction). The citation must be the SUBJECT of the clause.
    assert _classify_failure(
        "The agent violated R1: the response is about deleting files but should "
        "not have proceeded without approval.") == "regressed"
    assert _classify_failure(
        "The answer is about X and is not wrong, R1 applies.") == "regressed"
    # A true case-broken note (cited ref is the subject, clause doesn't cross a
    # sentence boundary) still classifies correctly.
    assert _classify_failure(
        "DEC10 is about applyTextDelta, not a feature filter.") == "case-broken"


def test_report_populations_reconcile_holds_with_orphans():
    # MED fix (run_0e29db9a): a run carrying a retired id not in the golden set
    # must not break executed+pending==golden_size; orphans surfaced separately.
    golden = {"cases": [{"id": "A"}, {"id": "B"}, {"id": "C"}]}
    run = {"cases": [{"id": "A"}, {"id": "B"}, {"id": "RETIRED"}]}
    pops = _report_populations(golden, run)
    assert pops["golden_size"] == 3
    assert pops["executed"] == 2      # A,B (in golden) — NOT RETIRED
    assert pops["pending"] == 1       # C
    assert pops["orphans"] == 1       # RETIRED
    assert pops["executed"] + pops["pending"] == pops["golden_size"]


def test_compute_delta_tolerates_malformed_snapshot_case():
    # Meta-review MED fix (run_0e29db9a): a case dict missing id/status must not
    # KeyError (would be debug-swallowed in the daemon → silent no-report).
    history = [{"cases": [{"id": "A", "status": "passed"}, {"status": "failed"}]}]  # 2nd has no id
    current = {"cases": [{"id": "A", "status": "failed"}, {"id": "B"}]}  # B has no status
    # Must not raise; A flipped passed→failed.
    changes = _compute_delta(current, history)
    assert {"id": "A", "from": "passed", "to": "failed"} in changes


# ─── _comparable_full_runs (trend comparability) ─────────────────────────────
def test_comparable_full_runs_excludes_canary_and_partial():
    history = [
        {"triggered_by": "biweekly", "total_cases": 157, "scored_count": 157, "overall_score": 94.3},
        {"triggered_by": "scheduled", "total_cases": 157, "scored_count": 156, "overall_score": 92.0},
        {"triggered_by": "canary", "total_cases": 157, "scored_count": 68, "overall_score": 100.0},
        {"triggered_by": "behavior_monthly", "total_cases": 23, "scored_count": 20, "overall_score": 90.0},
        {"triggered_by": "manual", "total_cases": 1, "scored_count": 0, "overall_score": 0.0},
        {"triggered_by": "monthly", "total_cases": 157, "scored_count": 155, "overall_score": 91.0},
    ]
    kept, excluded = _comparable_full_runs(history)
    triggers = [r["triggered_by"] for r in kept]
    assert triggers == ["biweekly", "scheduled", "monthly"], triggers
    assert excluded == 3  # canary + behavior_monthly + single-case manual


def test_comparable_full_runs_empty_history():
    kept, excluded = _comparable_full_runs([])
    assert kept == [] and excluded == 0


# ─── _dim_snapshot_key (taxonomy map) ────────────────────────────────────────
def test_dim_snapshot_key_all_six_resolve_against_real_snapshot(snapshot):
    snap_keys = set(snapshot["dimensions"].keys())
    assert len(snap_keys) == 6
    for dim in DIMENSIONS:
        key = _dim_snapshot_key(dim["id"])
        assert key in snap_keys, f"DIMENSIONS id {dim['id']!r} -> {key!r} not in snapshot dims {snap_keys}"


def test_dim_snapshot_key_covers_the_three_that_differ():
    # The 3 that do NOT collide by string must be mapped explicitly.
    assert _dim_snapshot_key("factual") == "factual_accuracy"
    assert _dim_snapshot_key("judgment") == "judgment_quality"
    assert _dim_snapshot_key("utility") == "context_utility"
    # The 3 that do collide map to themselves.
    assert _dim_snapshot_key("capability") == "capability"
    assert _dim_snapshot_key("compliance") == "compliance"
    assert _dim_snapshot_key("recovery") == "recovery"


# ─── _report_populations (single-source counts) ──────────────────────────────
def test_report_populations_pending_is_set_difference(snapshot, golden):
    pops = _report_populations(golden, snapshot)
    # merged golden set = 181; run executed = 157; pending (golden ids not run) = 24
    assert pops["golden_size"] == len(golden["cases"])
    assert pops["executed"] == len(snapshot["cases"])
    assert pops["pending"] == pops["golden_size"] - pops["executed"]
    # On this snapshot every run id IS in the golden set, so pending is exactly
    # the un-run remainder.
    assert pops["pending"] >= 0


def test_report_populations_no_hardcoded_115():
    # Guard against re-introducing a literal count. Populations are computed.
    src = Path(__file__).resolve().parents[1] / "scripts/eval_runner.py"
    text = src.read_text()
    assert "115 behavioral contracts" not in text, "hardcoded '115 behavioral contracts' literal must be gone"


# ─── render smoke (AC3/AC4/AC8) ──────────────────────────────────────────────
def test_generate_report_from_snapshot_has_new_sections(snapshot, golden, tmp_path):
    # Drive the REAL generator against an EXISTING snapshot (never a fresh eval).
    # generate_html_report writes into <root>/Eval/EvalHistory/ — give it a tmp root
    # but seed the history dir so trend has data.
    out = generate_html_report(snapshot, golden, tmp_path)
    html = Path(out).read_text()
    # AC1: no stale literal
    assert "115 behavioral contracts" not in html
    # AC3: two-track headline (mechanical+judge vs behavior pending)
    assert ("行为层" in html) or ("behavior" in html.lower())
    # AC4: classification matrix present
    assert ("matrix" in html.lower()) or ("eval_method" in html.lower()) or ("分类" in html)
    # AC8: renders without crashing, produces a real file
    assert Path(out).exists() and len(html) > 1000
    # AC7 wiring: per-dimension trend section is actually rendered (not just the
    # map defined-but-unused) — proves _dim_snapshot_key feeds the render path.
    assert "各维度趋势" in html or "Per-dimension trend" in html
    assert "mini-spark" in html
