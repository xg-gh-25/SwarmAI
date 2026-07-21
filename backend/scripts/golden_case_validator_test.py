"""Tests for golden_case_validator — the 4-gate quality check + privacy scan."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.golden_case_validator import (  # noqa: E402
    gate_schema, gate_duplicate, gate_non_vacuous, gate_redline, privacy_scan, validate_case,
)


def _ok_case(cid="GS_NEW", **over):
    c = {
        "id": cid, "category": "compliance", "dimension": "compliance",
        "eval_method": "programmatic", "affected_by": ["backend/core/x.py"],
        "evaluators": ["file_contains"],
        # gate-eligible (programmatic + file_contains) → gate_teeth requires a
        # negative_command (added when run_5edf2cc0 wired gate_teeth into
        # validate_case). Tests that call gate_* helpers directly override
        # verification and bypass teeth; the validate_case-level tests use this
        # default and need the field present.
        "verification": {"file": "backend/core/x.py", "grep": "class Foo",
                         "negative_command": "grep -q 'class Foo' /dev/null"},
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


# ── gate_redline (severity marker validity — run_21490939) ──
def test_redline_absent_is_ok():
    ok, errs = gate_redline(_ok_case())
    assert ok and errs == []

def test_redline_true_with_runnable_evaluator_ok():
    ok, errs = gate_redline(_ok_case(redline=True, evaluators=["file_contains"]))
    assert ok, errs

def test_redline_true_llm_evaluator_ok():
    # An llm-judged red-line (refusal/tone) is the WHOLE point — must be allowed.
    ok, errs = gate_redline(_ok_case(redline=True, eval_method="llm", evaluators=["goal_success"]))
    assert ok, errs

def test_redline_non_bool_rejected():
    ok, errs = gate_redline(_ok_case(redline="true"))  # string, not bool
    assert not ok and "bool" in " ".join(errs).lower()

def test_redline_true_without_runnable_evaluator_rejected():
    # Gate-1 F3 evasion: mark redline + give it an unknown evaluator -> always
    # 'skipped' -> never gates -> always-passes. gate_redline must refuse it.
    ok, errs = gate_redline(_ok_case(redline=True, evaluators=["nonexistent_evaluator"]))
    assert not ok and "runnable" in " ".join(errs).lower()

def test_redline_true_empty_evaluators_rejected():
    ok, errs = gate_redline(_ok_case(redline=True, evaluators=[]))
    assert not ok

def test_redline_false_not_validated_as_redline():
    # redline: false with a junk evaluator is fine — it's not a red-line.
    ok, errs = gate_redline(_ok_case(redline=False, evaluators=["nonexistent_evaluator"]))
    assert ok, errs

def test_validate_case_wires_gate_redline():
    """A redline:true case with an unrunnable evaluator must FAIL validate_case."""
    c = _ok_case(redline=True, evaluators=["nonexistent_evaluator"],
                 verification={"file": "x.py", "grep": "class Foo"})
    ok, report = validate_case(c, existing=[], for_public=False)
    assert not ok
    assert "redline" in report and report["redline"][0] is False


# ── mirror-drift guard (adversarial L1, run_21490939) ──
# _RUNNABLE_EVALUATORS / _GATE_ELIGIBLE_EVALUATORS are hand-copied in this module
# and MUST equal eval_runner's canonical sets. Nothing enforces the mirror at
# runtime (a module-level derive would risk the circular import — eval_runner
# imports compute_case_stamp from HERE). This test IS the enforcement: if
# eval_runner adds/removes an evaluator, this goes RED so the validator's copy is
# updated in lockstep. Without it, gate_redline silently drifts — a redline case
# using a new evaluator gets false-rejected (or, worse, an unrunnable one accepted).
def test_runnable_evaluators_mirror_eval_runner_dispatch():
    from scripts import eval_runner
    from scripts.golden_case_validator import _RUNNABLE_EVALUATORS
    canonical = (eval_runner.PROGRAMMATIC_EVALUATORS
                 | eval_runner.LLM_EVALUATORS
                 | eval_runner.BEHAVIOR_EVALUATORS)
    assert _RUNNABLE_EVALUATORS == canonical, (
        f"drift: validator _RUNNABLE_EVALUATORS != eval_runner dispatch union. "
        f"missing={canonical - _RUNNABLE_EVALUATORS}, extra={_RUNNABLE_EVALUATORS - canonical}")


def test_gate_eligible_evaluators_mirror_eval_runner():
    from scripts import eval_runner
    from scripts.golden_case_validator import _GATE_ELIGIBLE_EVALUATORS
    assert _GATE_ELIGIBLE_EVALUATORS == eval_runner._GATE_ELIGIBLE_EVALUATORS, (
        "drift: validator _GATE_ELIGIBLE_EVALUATORS != eval_runner's — keep in sync")


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
    """ADD to private does NOT run privacy gate — instance cases are allowed there.
    Uses AGENT.R1 (an instance ref that RESOLVES) so the new gate_refs passes; the
    point under test is that the privacy gate is skipped, not ref-drift. (STEERING.R1
    was the old fixture but resolves EMPTY post-2026-06-27 reorg → gate_refs rejects.)"""
    ok, report = validate_case(_ok_case(affected_by=["AGENT.R1"]), existing=[], for_public=False)
    assert ok, report

def test_validate_promote_blocks_instance_case():
    """PROMOTE to public RUNS privacy gate — instance case blocked. Uses AGENT.R1
    (resolves, so gate_refs passes) to prove the block comes from PRIVACY, not the
    new refs gate — otherwise the `not ok` assertion could pass for the wrong reason."""
    ok, report = validate_case(_ok_case(affected_by=["AGENT.R1"]), existing=[], for_public=True)
    assert not ok
    assert report["privacy"][0] is False, "block must come from privacy gate, not refs"
    assert report["refs"][0] is True, "AGENT.R1 should resolve (refs gate clean)"


def test_clean_pass_report_carries_stamp_not_a_gate_tuple():
    """run_674f32ef regression: on a clean pass validate_case adds
    report['stamp'] = <str>, NOT a (ok, errs) tuple. The CLI summary loop
    must NOT blind-unpack every report value or it crashes (exit 1) on every
    successful validation. Asserts the shape the CLI loop relies on."""
    ok, report = validate_case(_ok_case(), existing=[], for_public=False)
    assert ok, report
    assert "stamp" in report and isinstance(report["stamp"], str)
    # every NON-stamp entry is a (bool, list) gate tuple — the CLI skips 'stamp'
    for gate, result in report.items():
        if gate == "stamp":
            continue
        assert isinstance(result, tuple) and len(result) == 2, gate


def test_cli_main_exits_zero_on_clean_public_pass(tmp_path):
    """The actual bug: `python golden_case_validator.py --for-public` crashed
    (exit 1, ValueError unpacking report['stamp']) AFTER printing all gates ✓.
    Drive main() end-to-end and assert exit 0 + PASS on a code-only case."""
    import json
    import subprocess
    case = {
        "id": "GS_CLI_SMOKE", "category": "recall", "dimension": "capability",
        "eval_method": "programmatic",
        "affected_by": ["backend/core/context_injector.py"],
        "evaluators": ["canary_pass"],
        "verification": {
            "command": "python backend/scripts/recall_chain_probe.py resume_fill",
            "expected_contains": "RESUME_FILL_OK",
            "negative_command": "python backend/scripts/recall_chain_probe.py resume_fill negative",
        },
    }
    cf = tmp_path / "case.json"
    cf.write_text(json.dumps(case))
    backend = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, "scripts/golden_case_validator.py",
         "--case-file", str(cf), "--for-public"],
        cwd=str(backend), capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert "PASS" in r.stdout
    assert "Traceback" not in r.stderr


# ── validate-corpus sweep mode (run_51d897f6 WS-B) ──────────────────────────
# Closes the load/run hole: gate_refs fires only on the ADD/UPDATE write path
# (eval_service), never on the resting corpus. A drifted ref (STEERING.R5 →
# resolves EMPTY post-reorg) sits green forever. The sweep runs gate_refs over
# every case in BOTH golden sets and surfaces the stale inventory.
def _write_corpus(tmp_path, cases, private_cases=None):
    """Build a minimal SwarmWS-shaped workspace: Eval/ + .context/ with a
    STEERING.md that has NO 'R5' rule (so STEERING.R5 resolves EMPTY) but DOES
    have AGENT.R1 (so that ref resolves). The golden set carries `version: 2`
    because validate_corpus reuses eval_runner.load_golden_set, which asserts it."""
    import yaml
    (tmp_path / "Eval").mkdir()
    (tmp_path / ".context").mkdir()
    (tmp_path / "Eval" / "golden_set.yaml").write_text(
        yaml.safe_dump({"version": 2, "cases": cases}))
    if private_cases is not None:
        (tmp_path / "Eval" / "golden_set.private.yaml").write_text(
            yaml.safe_dump({"version": 2, "cases": private_cases}))
    (tmp_path / ".context" / "STEERING.md").write_text("### 1. Prevention\nsome text\n")
    (tmp_path / ".context" / "AGENT.md").write_text("R1. **Pipeline mandatory**\nfull text\n\n")
    (tmp_path / ".context" / "MEMORY.md").write_text("- [DEC38] real entry\n")
    return tmp_path


def test_validate_corpus_flags_drifted_ref_and_exits_nonzero(tmp_path):
    """RED until validate-corpus exists: a case with a STEERING.R5 ref that
    resolves EMPTY must be reported and drive exit 1."""
    import subprocess
    drifted = {
        "id": "GS_DRIFT", "category": "compliance", "dimension": "capability",
        "eval_method": "llm", "affected_by": ["STEERING.R5"],
        "evaluators": ["goal_success"],
        "assertions": ["agent does the right thing"],
        "scenario": {"turns": [{"input": "x"}]},
    }
    clean = {
        "id": "GS_CLEAN", "category": "compliance", "dimension": "capability",
        "eval_method": "llm", "affected_by": ["AGENT.R1"],
        "evaluators": ["goal_success"],
        "assertions": ["agent runs the pipeline"],
        "scenario": {"turns": [{"input": "y"}]},
    }
    ws = _write_corpus(tmp_path, [drifted, clean])
    backend = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, "scripts/golden_case_validator.py",
         "--validate-corpus", "--root", str(ws), "--exit-nonzero"],
        cwd=str(backend), capture_output=True, text=True, timeout=60,
    )
    assert "GS_DRIFT" in r.stdout, f"drifted case not reported:\n{r.stdout}\n{r.stderr}"
    assert "STEERING.R5" in r.stdout
    # GS_CLEAN (the AGENT.R1 case) must NOT appear in the stale inventory.
    # Parenthesized so the assert binds the whole conditional, not just the lhs.
    assert ("GS_CLEAN" not in r.stdout.split("STALE")[-1]) if "STALE" in r.stdout else True
    assert r.returncode == 1, f"exit should be 1 with --exit-nonzero; got {r.returncode}"
    assert "Traceback" not in r.stderr


def test_validate_corpus_report_only_exits_zero(tmp_path):
    """Without --exit-nonzero, the sweep REPORTS drift but exits 0 (report-only
    default — so adding it to CI doesn't red 27 pre-existing cases on day 1)."""
    import subprocess
    drifted = {
        "id": "GS_DRIFT2", "category": "compliance", "dimension": "capability",
        "eval_method": "llm", "affected_by": ["STEERING.R5"],
        "evaluators": ["goal_success"], "assertions": ["x"],
        "scenario": {"turns": [{"input": "x"}]},
    }
    ws = _write_corpus(tmp_path, [drifted])
    backend = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, "scripts/golden_case_validator.py",
         "--validate-corpus", "--root", str(ws)],
        cwd=str(backend), capture_output=True, text=True, timeout=60,
    )
    assert "GS_DRIFT2" in r.stdout
    assert r.returncode == 0, f"report-only must exit 0; got {r.returncode}"


def test_validate_corpus_wrong_root_fails_loud_not_clean(tmp_path):
    """Gate-2 F5: a typo'd --root (no Eval/golden_set.yaml) must NOT false-green
    to 'CORPUS CLEAN'. load_golden_set raises FileNotFoundError → exit 2 +
    'UNVERIFIABLE', never a silent clean report."""
    import subprocess
    bad = tmp_path / "definitely_nonexistent"
    backend = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, "scripts/golden_case_validator.py",
         "--validate-corpus", "--root", str(bad)],
        cwd=str(backend), capture_output=True, text=True, timeout=60,
    )
    assert "CORPUS CLEAN" not in r.stdout, f"wrong root false-greened:\n{r.stdout}"
    assert "UNVERIFIABLE" in r.stdout
    assert r.returncode == 2, f"wrong root must exit 2; got {r.returncode}"
    assert "Traceback" not in r.stderr


def test_validate_corpus_dup_id_fails_loud(tmp_path):
    """Gate-2 F2: an id present in BOTH public + private is a migration error.
    Reusing load_golden_set makes the sweep fail LOUD (exit 2) instead of
    silently double-counting (the bug in the first self-rolled loader)."""
    import subprocess
    case = {
        "id": "GS_DUP", "category": "compliance", "dimension": "capability",
        "eval_method": "llm", "affected_by": ["AGENT.R1"],
        "evaluators": ["goal_success"], "assertions": ["x"],
        "scenario": {"turns": [{"input": "x"}]},
    }
    ws = _write_corpus(tmp_path, [case], private_cases=[dict(case)])
    backend = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, "scripts/golden_case_validator.py",
         "--validate-corpus", "--root", str(ws), "--exit-nonzero"],
        cwd=str(backend), capture_output=True, text=True, timeout=60,
    )
    assert "UNVERIFIABLE" in r.stdout, f"dup-id not caught:\n{r.stdout}\n{r.stderr}"
    assert "GS_DUP" in r.stdout
    assert r.returncode == 2, f"dup-id must exit 2; got {r.returncode}"
    assert "Traceback" not in r.stderr
