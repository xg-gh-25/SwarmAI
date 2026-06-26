"""Tests for golden_case_validator — the 4-gate quality check + privacy scan."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.golden_case_validator import (  # noqa: E402
    gate_schema, gate_duplicate, gate_non_vacuous, privacy_scan, validate_case,
)


def _ok_case(cid="GS_NEW", **over):
    c = {
        "id": cid, "category": "compliance", "dimension": "compliance",
        "eval_method": "programmatic", "affected_by": ["backend/core/x.py"],
        "evaluators": ["file_contains"],
        "verification": {"file": "backend/core/x.py", "grep": "class Foo"},
    }
    c.update(over)
    return c


# ── G1 schema ──
def test_schema_passes_complete_case():
    ok, errs = gate_schema(_ok_case())
    assert ok, errs

def test_schema_fails_missing_required():
    ok, errs = gate_schema({"id": "X"})
    assert not ok and any("affected_by" in e or "evaluators" in e for e in errs)


# ── G2 duplicate (structural) ──
def test_duplicate_detects_same_verification():
    existing = [_ok_case("GS_OLD")]
    ok, errs = gate_duplicate(_ok_case("GS_NEW"), existing)  # same verification.grep+file
    assert not ok and "duplicate" in " ".join(errs).lower()

def test_duplicate_passes_distinct():
    existing = [_ok_case("GS_OLD", verification={"file": "a.py", "grep": "AAA"})]
    ok, errs = gate_duplicate(_ok_case("GS_NEW", verification={"file": "b.py", "grep": "BBB"}), existing)
    assert ok, errs


# ── G3 non-vacuous (G4 in design = vacuous-assert guard) ──
def test_non_vacuous_fails_trivially_true():
    # grep matches anything / echo OK echoing its own literal
    ok, errs = gate_non_vacuous(_ok_case(verification={"command": "echo OK", "expected_contains": "OK"}))
    assert not ok and "vacuous" in " ".join(errs).lower()

def test_non_vacuous_passes_real_assertion():
    ok, errs = gate_non_vacuous(_ok_case(verification={"file": "x.py", "grep": "class MessageStore"}))
    assert ok, errs

def test_non_vacuous_present_empty_grep_still_vacuous():
    # An ACTUAL grep field that matches anything is still vacuous.
    ok, errs = gate_non_vacuous(_ok_case(verification={"file": "x.py", "grep": ""}))
    assert not ok and "vacuous" in " ".join(errs).lower()

def test_non_vacuous_canary_without_grep_is_not_vacuous():
    # run_b2d62f47 fix: a canary_pass case asserts via command/expected_contains
    # and has NO grep field. The missing field must NOT be treated as
    # "grep matches anything" (the old bug false-killed every GS_RCHAIN_* probe).
    ok, errs = gate_non_vacuous(_ok_case(verification={
        "command": "cd backend && .venv/bin/python scripts/recall_chain_probe.py knowledge_live",
        "expected_contains": "KNOWLEDGE_LIVE_OK"}))
    assert ok, errs


# ── privacy scan (the ship-boundary gate for PROMOTE) ──
def test_privacy_rejects_sensitive_word():
    ok, errs = privacy_scan(_ok_case(title="report for gawan@amazon.com"))
    assert not ok and "privacy" in " ".join(errs).lower()

def test_privacy_rejects_instance_path():
    # references .context/ — instance structure leak even without a sensitive word
    ok, errs = privacy_scan(_ok_case(affected_by=[".context/MEMORY.md"]))
    assert not ok

def test_privacy_rejects_ddd_ref():
    ok, errs = privacy_scan(_ok_case(affected_by=["STEERING.R1"]))
    assert not ok

def test_privacy_passes_code_only():
    ok, errs = privacy_scan(_ok_case(affected_by=["backend/core/x.py"]))
    assert ok, errs


# ── validate_case orchestrates all gates for ADD (private, no privacy gate) ──
def test_validate_add_allows_instance_case():
    """ADD to private does NOT run privacy gate — instance cases are allowed there."""
    ok, report = validate_case(_ok_case(affected_by=["STEERING.R1"]), existing=[], for_public=False)
    assert ok, report

def test_validate_promote_blocks_instance_case():
    """PROMOTE to public RUNS privacy gate — instance case blocked."""
    ok, report = validate_case(_ok_case(affected_by=["STEERING.R1"]), existing=[], for_public=True)
    assert not ok
