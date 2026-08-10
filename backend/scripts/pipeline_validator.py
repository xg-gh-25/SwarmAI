#!/usr/bin/env python3
"""Pipeline stage validator — structural enforcement for the pipeline orchestrator.

Enforces 8 invariants after each pipeline stage to prevent behavioral drift
in the prompt-driven orchestrator. Called via bash after every stage:

    python pipeline_validator.py check \\
        --project SwarmAI --run-id run_xxx --stage evaluate

Returns JSON:
    {"valid": true, "stage": "evaluate", "errors": [], "warnings": [],
     "checks_passed": 8, "checks_total": 8}

Errors (BLOCK) prevent stage advancement. Warnings are informational —
they surface in the delivery report but don't block progress.

The 13 invariant checks:
    1. Stage order     — current stage follows the last completed stage per profile
    2. Artifact exists — stage published an artifact (except reflect)
    3. Artifact schema — required fields present in artifact JSON
    4. Decision logged — at least 1 decision classified in StageRecord
    5. Budget recorded — token_cost > 0 in stage record
    6. Profile respected — stage is in the selected profile
    7. DDD consistency — cross-document checks: non-goals vs approach,
                          failed patterns vs plan, staleness detection
                          (WARN only, evaluate stage)
    8. Quality gate    — stage-specific structural enforcement:
                          8a. BUILD: smoke_tests > 0 when files_changed > 1
                          8b. REVIEW: integration_trace.checked > 0
                          8c. REVIEW: ux_review when frontend files in changeset
                          8d. REVIEW: findings_count required for large changesets
    9. Depth (L2)     — field values indicate real work (not just structure)
   10. Push-ready (L3)— binary verdict gate for deliver stage
   11. Semantic (L2.5)— content quality heuristics (WARN)
   12. Anti-rational.  — skips require structured justification; counter-arguments
                          block the skip (earned truths from EVOLUTION.md)
   13. Output routing  — stages must consume declared upstream artifacts;
                          freshness check warns on stale consumed artifacts

Public symbols:
- ``main``              — CLI entry point
- ``validate``          — Core validation logic (testable without CLI)
- ``check_ddd_consistency`` — Standalone DDD cross-doc check (testable without pipeline)
- ``check_ddd_staleness``  — Detect runs whose DDD docs changed since evaluation
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Add parent directory for core imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ddd_paths import ddd_path
from core.pipeline_profiles import get_profile_stages
from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth

# ---------------------------------------------------------------------------
# Check outcome taxonomy (run_55710438)
# A check has THREE possible outcomes, not two:
#   - PASSED:  the check ran and the content satisfied it
#   - FAILED:  the check ran and the content did NOT satisfy it (content fault)
#   - ERRORED: the check itself could not run (exception, missing tool, bad
#              input) — this is a CHECK fault, NOT a content fault
#
# Severity is derived MECHANICALLY from which accumulator a check writes to,
# never hand-assigned: a check that appends to errors[] is HARD (its failure
# blocks); a check that appends only to warnings[] is ADVISORY (its failure
# warns). This is the EXISTING contract — we are surfacing it, not changing it.
#
# Crash handling preserves the existing valid=len(errors)==0 semantics EXACTLY:
#   - HARD check crash  → synthetic string appended to errors[]   → still BLOCKS
#   - ADVISORY check crash → synthetic string appended to warnings[] → does NOT block
# The FAILED-vs-ERRORED distinction is purely ADDITIVE: it lives in
# check_results[] and errored[], leaving errors[]/warnings[]/valid untouched.
#
# CRITICAL (C037 / CLASS A): a HARD check that errors STILL fails closed. A
# crash never silently opens a hard gate. Only advisory gates fail open, and
# even then the ERRORED outcome is recorded in the audit trail.
# ---------------------------------------------------------------------------

CHECK_PASSED = "passed"
CHECK_FAILED = "failed"
CHECK_ERRORED = "errored"

SEVERITY_HARD = "hard"          # failure/crash appends to errors[] → blocks
SEVERITY_ADVISORY = "advisory"  # failure/crash appends to warnings[] → warns

# ── Finding-level confidence gate (AutoSDE port, run_7583af5f) ──
# The "confidence >= 7" rule that lived ONLY as prose in deliver.md:507 is now a
# CODE CONSTANT — prose gates get bypassed (12× CLASS A), hard constants hold (P7).
# Mirrors AutoSDE's threshold-as-constant form (their value is 8; we keep 7 to
# match the established deliver.md prose, avoiding gratuitous drift).
CONFIDENCE_GATE_THRESHOLD = 7
# Specialists emit "MED" (deliver.md:396) while the final schema says "MEDIUM"
# (STAGE_TEMPLATES :397) — a real in-repo severity-string collision (MOD04). The
# gate MUST accept both, case-insensitively, or it is dead for specialist findings.
_MED_SEVERITIES = frozenset({"MEDIUM", "MED"})


def _blocked_findings(
    findings: object, threshold: int = CONFIDENCE_GATE_THRESHOLD,
) -> list[dict]:
    """Return the UNRESOLVED findings that must block delivery/completion.

    Blocking rule (the single source of truth used by BOTH the publish-time
    gate `validate_artifact_data` and the completion-time gate `_check_depth`,
    replacing the two previously-duplicated inline HIGH-only filters):

    - severity HIGH or CRITICAL → always blocks (confidence-independent).
      (CRITICAL is a live severity in the review-agent schemas + confidence_score.py
      :133 which treats critical==high; omitting it would fail-OPEN on the single
      MOST severe finding class — the inverse of intent. Gate-2 finding, run_7583af5f.)
    - severity MEDIUM/MED → blocks when confidence >= `threshold`, OR when
      confidence is MISSING (fail-closed, P7: a gate must not be dodgeable by
      omitting a field).  confidence < threshold → note-only (not blocked),
      matching the deliver.md:507 prose semantics.
    - severity LOW (or anything else) → never blocks.
    - resolved findings never block regardless of severity/confidence.

    Severity is normalized case-insensitively across {HIGH, CRITICAL, MEDIUM, MED}.
    """
    if not isinstance(findings, list):
        return []
    blocked: list[dict] = []
    for f in findings:
        if not isinstance(f, dict) or f.get("resolved"):
            continue
        sev = str(f.get("severity", "")).strip().upper()
        if sev in ("HIGH", "CRITICAL"):
            blocked.append(f)
        elif sev in _MED_SEVERITIES:
            conf = f.get("confidence")
            if conf is None:            # fail-closed on missing confidence
                blocked.append(f)
            else:
                try:
                    if float(conf) >= threshold:
                        blocked.append(f)
                except (TypeError, ValueError):
                    blocked.append(f)   # unparseable confidence → fail-closed
    return blocked


# Max bytes read when verifying a disk_check locus. A findings file is source
# code; 2 MB is far beyond any real source file and bounds a pathological read.
_DISK_CHECK_MAX_BYTES = 2 * 1024 * 1024


def _verify_findings_on_disk(
    findings: object, allowed_root: str | None = None,
) -> tuple[list[str], list[str]]:
    """L4 verify-against-disk (Run B) — code-enforce INSTRUCTIONS.md:581.

    The COMPLEMENT of `_blocked_findings` (which filters UNRESOLVED findings):
    this pass inspects **resolved:true** findings that carry a structured
    ``disk_check`` and confirms the durable change is actually on disk. It is a
    SEPARATE, disjoint pass — never fold it into `_blocked_findings` (opposite
    resolved-polarity).

    A ``disk_check`` is::

        {"file": "<ABSOLUTE path>", "must_contain": "<str>"}     # fix ADDED code
        {"file": "<ABSOLUTE path>", "must_not_contain": "<str>"} # fix REMOVED code

    Returns ``(errors, invalid_warnings)``:
      - ``errors`` (BLOCK): file was READABLE and the durable change is gone —
        ``must_contain`` absent, or ``must_not_contain`` still present. This is
        the run_b5592983 "record said done, disk said otherwise" (C011) catch:
        an honest fix silently reverted by an external event (git-stash-pop,
        parallel-session commit, linter undo).
      - ``invalid_warnings`` (WARN, never BLOCK): the check could not be
        performed — file missing (for must_contain), unreadable, binary,
        oversized, a relative/empty path, or outside ``allowed_root``.
        **Fail-open on uncertainty** — a locus we cannot verify is "can't
        check", NOT "fix reverted"; blocking on it would false-block correct
        deliveries run from CI or another machine (Gate-1 Attack-5/6).

    ``disk_check.file`` is ABSOLUTE by contract (Gate-1 Attack-3): findings
    reference SOURCE-repo files, but the validator's workspace root is
    ``~/.swarm-ai/SwarmWS`` (the C040 source-vs-workspace split). Joining a
    relative path against the workspace root would grep the WRONG tree and
    false-block 100% of code-fix deliveries. Absolute paths eliminate the join.
    ``allowed_root``, when set, confines reads to that subtree (defence in depth
    against a traversal-crafted absolute path); when None, any absolute path is
    read (the finding author is the pipeline itself, not untrusted input).
    """
    errors: list[str] = []
    invalid: list[str] = []
    if not isinstance(findings, list):
        return errors, invalid

    for f in findings:
        if not isinstance(f, dict) or not f.get("resolved"):
            continue
        dc = f.get("disk_check")
        if not isinstance(dc, dict):
            continue  # no structured locus → caller decides whether to WARN

        raw = dc.get("file")
        must_contain = dc.get("must_contain")
        must_not_contain = dc.get("must_not_contain")
        label = str(f.get("finding", ""))[:80]

        # --- path validity (fail-open to WARN, never BLOCK) ---
        if not raw or not isinstance(raw, str) or not os.path.isabs(raw):
            invalid.append(
                f"disk_check invalid (non-absolute/empty file path) for finding "
                f"'{label}': disk_check.file must be an absolute source path — cannot verify."
            )
            continue
        try:
            rp = Path(raw).resolve()
        except (OSError, ValueError):
            invalid.append(f"disk_check invalid (unresolvable path '{raw}') for '{label}'.")
            continue
        if allowed_root:
            try:
                root = Path(allowed_root).resolve()
                if not (rp == root or root in rp.parents):
                    invalid.append(
                        f"disk_check invalid (path '{rp}' escapes allowed_root) for '{label}'."
                    )
                    continue
            except (OSError, ValueError):
                invalid.append(f"disk_check invalid (bad allowed_root) for '{label}'.")
                continue

        if must_contain is None and must_not_contain is None:
            invalid.append(
                f"disk_check invalid (neither must_contain nor must_not_contain) for '{label}'."
            )
            continue

        # --- file read (fail-open to WARN on any read problem) ---
        exists = rp.is_file()
        text = None
        if exists:
            try:
                if rp.stat().st_size > _DISK_CHECK_MAX_BYTES:
                    invalid.append(f"disk_check skipped (file >2MB) for '{label}'.")
                    continue
                text = rp.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                invalid.append(
                    f"disk_check skipped (unreadable/binary file '{rp}') for '{label}'."
                )
                continue

        # --- the actual gate ---
        if must_contain is not None:
            if not exists:
                # can't confirm the fix is present → WARN, don't BLOCK.
                # (A revert keeps the file and changes content; a MISSING file is
                #  "wrong machine / not built here", i.e. uncertainty, not proof.)
                invalid.append(
                    f"disk_check skipped (file not found '{rp}', cannot confirm "
                    f"must_contain) for '{label}'."
                )
            elif str(must_contain) not in text:
                errors.append(
                    f"L4 disk-check FAILED: resolved finding claims a fix but its "
                    f"must_contain marker is ABSENT from {rp} — the fix is not on "
                    f"disk (reverted?). Finding: '{label}'. Re-apply or un-resolve."
                )
        if must_not_contain is not None:
            if not exists:
                # File absent → the thing to remove is definitionally gone, so
                # this is NOT a block. But absent-because-wrong-checkout is also
                # "can't verify the removal actually happened here" → emit a WARN
                # for signal parity with the must_contain missing-file branch
                # (Gate-2 finding: the two branches were asymmetric — one warned,
                # one was fully silent).
                invalid.append(
                    f"disk_check skipped (file not found '{rp}', must_not_contain "
                    f"passes vacuously — cannot confirm removal on this checkout) "
                    f"for '{label}'."
                )
            elif str(must_not_contain) in text:
                errors.append(
                    f"L4 disk-check FAILED: resolved finding claims a removal/refactor "
                    f"but its must_not_contain marker is STILL PRESENT in {rp} — the fix "
                    f"is not on disk (reverted?). Finding: '{label}'. Re-apply or un-resolve."
                )

    return errors, invalid


class _CheckGuard:
    """Context manager that isolates one check so a crash becomes an ERRORED
    outcome instead of aborting the whole validate() run.

    Usage::

        with _CheckGuard("stage_order", SEVERITY_HARD, errors, warnings,
                         check_results) as g:
            if _check_stage_order(...):
                g.passed()
            else:
                g.failed()
                errors.append("...")

    On a clean exit the caller declares passed()/failed(). If the block raises,
    __exit__ swallows the exception (returns True), records an ERRORED
    CheckResult, and appends a synthetic message to the severity-appropriate
    accumulator (errors[] for hard, warnings[] for advisory).
    """

    def __init__(self, name: str, severity: str,
                 errors: list[str], warnings: list[str],
                 check_results: list[dict]):
        self.name = name
        self.severity = severity
        self._errors = errors
        self._warnings = warnings
        self._check_results = check_results
        self._recorded = False

    # True after __exit__ converts a crash into an ERRORED outcome. The caller
    # reads this to credit checks_passed for a non-blocking advisory ERROR so
    # the checks_passed==checks_total invariant holds on a valid run.
    errored_nonblocking = False

    def _record(self, status: str, detail: str = "", force: bool = False) -> None:
        # `force` lets an ERRORED outcome OVERWRITE a prior PASSED/FAILED record.
        # C037 safety guarantee: a hard check that crashes AFTER declaring an
        # outcome must STILL be recorded as ERRORED (and block) — never silently
        # fall through on a stale PASSED record (Correctness review MED-8).
        if self._recorded and not force:
            return
        if force:
            self._check_results[:] = [
                c for c in self._check_results if c.get("name") != self.name
            ]
        self._recorded = True
        self._check_results.append({
            "name": self.name,
            "severity": self.severity,
            "status": status,
            "detail": detail,
        })

    def passed(self, detail: str = "") -> None:
        self._record(CHECK_PASSED, detail)

    def failed(self, detail: str = "") -> None:
        self._record(CHECK_FAILED, detail)

    def __enter__(self) -> "_CheckGuard":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            # Caller may not have declared an outcome (some blocks only act on
            # one branch). Default to PASSED — a clean run with no failure
            # recorded means the check did not object.
            if not self._recorded:
                self._record(CHECK_PASSED)
            return False
        # NEVER swallow control-flow / process-exit signals — only ordinary
        # check faults become ERRORED (Correctness review MED-9). KeyboardInterrupt,
        # SystemExit, GeneratorExit must propagate so Ctrl-C and sys.exit() work.
        if not issubclass(exc_type, Exception):
            return False
        # The check itself crashed → ERRORED, classified by severity. force=True
        # so a crash after a prior passed()/failed() still records ERRORED and
        # (for hard) still blocks — the hard gate can never silently fail open.
        msg = f"{type(exc).__name__}: {exc}"
        self._record(CHECK_ERRORED, msg, force=True)
        if self.severity == SEVERITY_HARD:
            # Fail-closed: a hard check that cannot run BLOCKS, with a clear
            # diagnostic that distinguishes "check crashed" from "content bad".
            self._errors.append(
                f"Check '{self.name}' ERRORED (could not run): {msg}. "
                f"Hard gate fails closed — fix the validator or input before advancing."
            )
        else:
            # Fail-open for advisory checks only, but never silently — the
            # ERRORED outcome is in check_results[] and surfaced as a warning.
            self._warnings.append(
                f"Check '{self.name}' ERRORED (could not run): {msg}. "
                f"Advisory gate — not blocking, but the check did not execute."
            )
            # The advisory check did not block, so it counts as "passed" for the
            # checks_passed/checks_total metric (it would have incremented had it
            # not crashed). Signal the caller to credit it (Correctness review LOW-8).
            self.errored_nonblocking = True
        return True  # swallow: one check's crash must not abort the others


# ---------------------------------------------------------------------------
# Gate 2 Agent Tool Audit — marker file directory
# Written by SubagentStop hook, verified by depth check on DELIVER.
# ---------------------------------------------------------------------------
try:
    from jobs.paths import STATE_DIR as _VALIDATOR_STATE_DIR
    AGENT_AUDIT_DIR = _VALIDATOR_STATE_DIR / "pipeline_agent_audit"
except Exception:
    AGENT_AUDIT_DIR = Path.home() / ".swarm-ai" / "state" / "pipeline_agent_audit"


# ---------------------------------------------------------------------------
# Stage artifact schemas — required fields produce BLOCK, recommended produce WARN
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Self-Socratic ambiguity scan constants — defined BEFORE STAGE_TEMPLATES so the
# template can reference _AMBIGUITY_TERMS at module load. The gate helper
# (_check_ambiguity_scan) lives further down with the other gate logic; full
# rationale is in the comment above that function.
# ---------------------------------------------------------------------------

# Min non-blank chars for a hit's resolution — anti-laziness floor (a hit MUST be
# resolved by a real self-answer or escalation, not "ok").
_AMBIGUITY_RESOLUTION_MIN_CHARS = 12

# Ambiguity trigger terms (EN + CJK) — the vocabulary the stage scans its OWN
# output against. Sourced from awslabs/aidlc-workflows overconfidence-prevention.md
# plus CJK equivalents. Single source of truth so stage docs + validator never
# drift (PIT21: derive from one table).
_AMBIGUITY_TERMS = (
    "depends", "maybe", "not sure", "mix of", "somewhere between",
    "standard", "typical",
    "看情况", "可能", "大概", "差不多", "视情况", "标准做法", "一般",
)

# Working-Backwards lens (greenfield-only) — defined above STAGE_SCHEMAS for the
# template reference. Helper _check_working_backwards lives with the other gate
# logic below; full rationale is in the comment above that function.
# The ECONOMIC/value fields that are GENUINELY NOT covered by the greenfield
# Understanding row or the Pre-mortem Gate. Adversarial (run_b5b26ebe) flagged that
# `target_customer` overlaps the Understanding "who has it" — so it is NOT enforced
# here (it stays in the doc/template as framing context, captured by understanding).
# The 3 ENFORCED fields are the value-economics the other gates do NOT capture:
# current_workaround (incumbent), why_better (differentiation), must_be_true
# (adoption assumption). "who" → understanding; "top-3 failures" → pre_mortem (reused).
_WB_ECONOMIC_FIELDS = ("current_workaround", "why_better", "must_be_true")
# Min non-blank chars per economic field — anti-laziness floor (a one-word
# "faster" is not a value proposition). Shares the spirit of the ambiguity floor.
_WB_FIELD_MIN_CHARS = 12


STAGE_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "evaluate": {
        "required": ["recommendation", "scope"],
        "recommended": ["acceptance_criteria", "scores"],
    },
    "think": {
        "required": ["key_findings"],
        "recommended": ["alternatives", "sources"],
    },
    "plan": {
        "required": ["acceptance_criteria"],
        "recommended": ["approach", "data_model", "boundaries", "success_criteria"],
    },
    "build": {
        "required": ["files_changed", "tdd", "ac_coverage"],  # V1.17.0: ac_coverage required
        "recommended": ["commits", "diff_summary"],
        # tdd.smoke_tests must be > 0 when files_changed > 1 (Check 8)
        # ac_coverage must map every PLAN AC to impl+test (Check 8f)
    },
    "review": {
        "required": [
            "approved",
            "litmus_gate",        # V1.17.0: pre-gate verdict before adversarial
            "integration_trace",
            "runtime_patterns",   # V1.10.0: RP1-RP29 checklist evidence
            "findings_count",     # V1.10.0: explicit count (even if 0)
        ],
        "recommended": ["findings", "security_findings", "ux_review",
                         "operational_patterns", "wire_test"],
    },
    "test": {
        "required": ["passed", "layers"],  # V1.17.0: layers required (3-layer test strategy)
        "recommended": ["failed", "fixed", "coverage", "regressions"],
    },
    "deliver": {
        "required": [
            "title",
            "quality",             # V2: binary push_ready gate (replaces confidence_score)
            # adversarial_review + completion_audit enforced by STAGE_DEPTH (profile-aware).
            # DEPTH blocks full/bugfix if absent, exempts trivial/research/docs.
        ],
        "recommended": ["decisions", "attention_flags", "report_path",
                         "meta_review"],
    },
    # reflect has no artifact — skip schema check
}

# Stage schema templates — show expected JSON shape for publish-time errors
STAGE_TEMPLATES: dict[str, dict] = {
    "evaluate": {
        "recommendation": "GO|DEFER|REJECT",
        "scope": "trivial|standard|complex",
        "acceptance_criteria": ["AC1: ...", "AC2: ..."],
        "scores": {"strategic": 0, "priority": 0, "feasibility": 0},
        # Understanding gate (strict profiles): a present-tense, observation-backed,
        # refuted claim about the CURRENT state — before THINK proposes a fix.
        # Bug-class evals may instead use the observation_evidence alias.
        "understanding": {
            "work_type": "bugfix|existing-feature|greenfield|refactor|research|docs",
            "claim": "A falsifiable statement about the CURRENT state (present-tense, NOT a plan)",
            "evidence": "An OBSERVATION: code-trace file:line / ps / log counts / repro / characterization",
            "evidence_kind": "observation|code-trace|repro|premortem|characterization",
            "skeptic_verdict": "SUPPORTED|UNSUPPORTED|ALREADY-SATISFIED|WRONG-FRAME",
            "alternative_considered": "The simplest competing framing, and why it loses",
        },
        # Self-Socratic ambiguity scan (strict profiles): re-scan the filled
        # clarification output for residual ambiguity; every hit must be resolved.
        "ambiguity_scan": {
            "scanned_fields": ["who", "what", "why", "when", "acceptance_criteria"],
            "terms_checked": list(_AMBIGUITY_TERMS),
            "hits": [{"term": "standard", "where": "what",
                      "resolution": "self-answer: read file:line — exact meaning, not vague.",
                      "kind": "self-answer|escalation"}],
            "hit_count": 1,
            "all_resolved": True,
        },
        # Working-Backwards lens (GREENFIELD-only, strict): customer/value framing.
        # Only required when understanding.work_type=='greenfield'. The 3 ENFORCED
        # economic fields are the novel slice; target_customer is context only (the
        # "who" is captured by understanding); pre_mortem (top-level, below) is the
        # reused top-3-failures and is ALSO required for greenfield.
        "working_backwards": {
            "target_customer": "(context only — the 'who' is captured by understanding) the specific segment",
            "current_workaround": "how they solve / work around it today",
            "why_better": "why this is faster / cheaper / better than the alternative",
            "must_be_true": "the adoption assumption that must hold for success",
        },
        # Reused as the greenfield top-3 failure reasons (Pre-mortem Gate). The
        # Working-Backwards gate requires this non-empty when work_type=='greenfield'.
        "pre_mortem": ["reason this fails 1", "reason 2", "reason 3"],
    },
    "think": {
        "key_findings": ["finding 1", "finding 2"],
        "alternatives": [{"constraint": "SPEED|QUALITY|SIMPLICITY", "what": "...", "effort": "S|M|L"}],
        "recommendation": "Approach X because...",
        "ambiguity_scan": {
            "scanned_fields": ["recommendation", "risk_probe.verification"],
            "terms_checked": list(_AMBIGUITY_TERMS),
            "hits": [],
            "hit_count": 0,
            "all_resolved": True,
        },
    },
    "plan": {
        "acceptance_criteria": ["AC1: testable statement"],
        "approach": "description",
        "boundaries": {"always": [], "ask_first": [], "never": []},
        "success_criteria": ["verifiable condition"],
    },
    "build": {
        "files_changed": ["path/to/file.py"],
        "tdd": {"green_pass": True, "smoke_tests": 3, "red_count": 5, "green_count": 5},
        "ac_coverage": [
            {"ac": "AC1: User can login with email", "impl": "auth.py::login()",
             "test": "test_auth.py::test_login_email", "verified": True},
        ],
        "commits": ["abc1234"],
    },
    "review": {
        "approved": True,
        "litmus_gate": {
            "verdict": "PASS|BORDERLINE|FAIL",
            "hf_checked": [True, True, True, True],  # exactly 4 bools, one per HF criterion
            "soft_signal_count": 0,  # BORDERLINE when >= 3
            "weak_areas": [],  # required non-empty when verdict=BORDERLINE
            "evidence": "HF1: domain logic present (3 conditionals, 2 error handlers). "
                        "HF2: 5/5 ACs identifiable (auth_middleware→AC1, rate_limiter→AC2...). "
                        "HF3: no contradictions found. HF4: all 2 HTTP calls + 1 DB query wrapped.",
        },
        "findings_count": 2,
        "integration_trace": {"checked": 5, "clean": True, "details": "symbol A → caller B verified"},
        "runtime_patterns": {
            "checked": 3, "violations": 0,
            "patterns": [{"pattern": "name", "status": "pass|N/A", "detail": ">10 chars describing what was checked"}],
        },
    },
    "test": {
        "passed": True,
        "tests_new": 10, "tests_total": 50, "regressions": 0,
        "layers": {
            "ac_driven": {"run": True, "pass": 5},
            "dependency_scoped": {"run": True, "tests": 12, "pass": 12},
            "import_smoke": {"run": True, "modules": 3, "pass": 3},
        },
    },
    "deliver": {
        "title": "Feature Name",
        "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
        "adversarial_review": {
            "spawned": True, "profile_tier": "full|lite|skipped",
            "findings_total": 3, "findings_fixed": 3, "findings_remaining": 0,
            # `confidence` (1-10) gates MEDIUM findings at delivery: an unresolved
            # MEDIUM with confidence >= CONFIDENCE_GATE_THRESHOLD (7) BLOCKS; absent
            # confidence on an unresolved MEDIUM is fail-closed (also blocks). HIGH
            # blocks regardless. See _blocked_findings().
            "findings": [{"severity": "HIGH|MEDIUM|LOW", "confidence": 8, "resolved": True,
                          "finding": "path/to/file.py function_name() line N: what is wrong. Fixed: how."}],
        },
        "completion_audit": {"all_green": True, "requirements_met": 5, "requirements_total": 5},
    },
}

# Depth requirements: nested fields that must exist for depth validation to pass
STAGE_DEPTH: dict[str, dict[str, list[str]]] = {
    "build": {"tdd": ["green_pass", "smoke_tests"], "ac_coverage": []},  # ac_coverage: list presence check (depth validates non-empty via Check 8f)
    "test": {"layers": ["ac_driven"]},  # V1.17.0: layers.ac_driven must exist
    "review": {"runtime_patterns": ["checked", "patterns"], "integration_trace": ["checked"],
               "litmus_gate": ["verdict", "hf_checked", "evidence"]},
    "deliver": {
        "adversarial_review": ["profile_tier", "findings"],
        "completion_audit": ["all_green"],
        "ac_verification": ["status"],  # F8: enforce AC verification step was recorded
    },
}


# ---------------------------------------------------------------------------
# Understanding gate — universal diagnosis/framing-before-build (run_862fa4e0)
# Design: Knowledge/Designs/2026-06-26-understanding-gate-design.md
#
# Generalizes the bug-only REPRO gate into a work-type-shaped understanding gate
# at the EVALUATE→THINK boundary. Three mechanical checks, none rely on agent
# discipline (SOUL "I am the OS, not the model"):
#   M1 — solution-language scan: the claim must describe the PRESENT state, not
#        the plan ("I will / the fix is / add a …"). THINK is where fixes live.
#   M2 — hedge-word scan: an unresolved hedge (似乎/probably/should be …) BLOCKS
#        unless the evidence field is a concrete observation that resolves it.
#   M3 — skeptic sub-agent: BEHAVIORAL (evaluate.md), NOT enforced here. The
#        validator can only check the artifact field exists; the human-spawned
#        skeptic produces the verdict — same split as Diagnostic-Challenge Gate
#        vs observation_evidence.
#
# Profiles: strict (full/bugfix/goal) REQUIRE the understanding block; relaxed
# (trivial/docs/research) do NOT mandate it (anti-ceremony, Gap 3) but M1/M2
# still scan it when present (cheap). Bug-class back-compat: observation_evidence
# is a recognized ALIAS for understanding.evidence, and the bug-class REPRO marker
# is preserved (see the REPRO block in validate_artifact_data).
# ---------------------------------------------------------------------------

# Min non-blank chars for an evidence string — anti-LAZINESS floor, NOT
# anti-fabrication (a 20-char garbage string passes by design; M3 skeptic is the
# fabrication backstop). Shared with the REPRO gate so both use one threshold.
_EVIDENCE_MIN_CHARS = 20

# Relaxed profiles are the ONLY exemption list — strict is the fail-closed
# DEFAULT (any profile not explicitly relaxed is strict). This closes the
# "standard" alias (rank-4, == full) AND any unknown/future profile in one move,
# matching the design's "all work types" intent (adversarial HIGH, run_862fa4e0).
_RELAXED_UNDERSTANDING_PROFILES = ("trivial", "docs", "research")

# ---------------------------------------------------------------------------
# Self-Socratic ambiguity scan — interrogate the SPEC/framing, not the user
# (design: plan art_ea9701a1, run_932c0991). The EVALUATE Requirement
# Clarification Check and THINK Design Risk Probe re-scan their OWN filled output
# for ambiguity/hedge wording and force ONE self-answer round; the validator
# enforces the loop RAN (an `ambiguity_scan` block, every hit RESOLVED).
#
# This is the Understanding Gate's "refute your claim" discipline shifted LEFT to
# the requirement layer — SAME family, DISTINCT field + DISTINCT error tag
# ('Ambiguity scan:' vs 'Understanding gate'):
#   Understanding Gate → diagnosis-hedge in understanding.claim/evidence (INPUT
#       epistemics: observe before asserting what IS).
#   Ambiguity Scan     → spec-ambiguity in the stage's own clarification output /
#       probe assumptions (OUTPUT completeness: is what you're about to build
#       under-specified?). Verified non-overlapping by the skeptic (run_932c0991).
#
# Profile policy mirrors the Understanding Gate EXACTLY: strict (any profile NOT
# in _RELAXED_UNDERSTANDING_PROFILES) REQUIRES the block; relaxed profiles are
# exempt when it is absent but a present block is still scanned (cheap). The gate
# fires ONLY on the evaluate + think stages.
#
# Anti-ceremony (the failure modes this guards against): a stage-doc-only
# instruction with no validator marker is the grill-protocol failure (think.md:96
# "almost always skipped") and the aidlc #366 ceremonial gate. Enforcing
# resolution-on-every-hit is what proves the loop RAN, not just that a block was
# pasted in. NOT in scope: an interactive user-ask gate (rejected) or PR/FAQ
# Working-Backwards (greenfield-only follow-up).
# (Constants _AMBIGUITY_RESOLUTION_MIN_CHARS / _AMBIGUITY_TERMS are defined above
# STAGE_SCHEMAS for module-load ordering.)
# ---------------------------------------------------------------------------


def _check_ambiguity_scan(data: dict, profile: str) -> list[str]:
    """Self-Socratic ambiguity gate (presence + per-hit resolution). Returns error
    strings tagged 'Ambiguity scan:'. Distinct from the Understanding Gate — see
    the module comment above. Profile policy mirrors _check_understanding_gate.

    Logic:
      - strict profile (not in _RELAXED_UNDERSTANDING_PROFILES) → the block MUST
        be present and a dict; absence/wrong-type BLOCKS.
      - relaxed profile → exempt when absent, but a PRESENT block is still scanned.
      - for ANY present block: every entry in `hits` must carry a non-empty
        resolution (>= _AMBIGUITY_RESOLUTION_MIN_CHARS, not a bare bool). An
        unresolved hit BLOCKS — that is what proves the self-answer loop RAN.
    """
    errs: list[str] = []
    scan = data.get("ambiguity_scan")
    is_strict = profile not in _RELAXED_UNDERSTANDING_PROFILES
    has_block = isinstance(scan, dict)

    if is_strict and not has_block:
        errs.append(
            "Ambiguity scan: strict profile requires an 'ambiguity_scan' block. "
            "After filling the clarification output (EVALUATE: WHO/WHAT/WHY/WHEN) or "
            "the risk-probe assumptions + recommendation (THINK), re-scan THAT output "
            "for ambiguity/hedge terms (depends/maybe/not sure/mix of/somewhere "
            "between/standard/typical + CJK 看情况/可能/大概/差不多/视情况/标准做法/一般) "
            "and record {scanned_fields, terms_checked, hits, hit_count, all_resolved}. "
            "Each hit needs a resolution (self-answer via code/DDD, or escalation). "
            "Self-Socratic: interrogate the spec, not the user (run_932c0991)."
        )
        return errs

    if not has_block:
        return errs  # relaxed + absent → exempt

    # Wrong-type block (e.g. a bare string "ran it") — can't carry hits.
    if not isinstance(scan, dict):  # defensive; has_block already gates this
        errs.append("Ambiguity scan: 'ambiguity_scan' must be a dict.")
        return errs

    hits = scan.get("hits", [])
    if not isinstance(hits, list):
        errs.append(
            "Ambiguity scan: 'hits' must be a list (use [] when no ambiguity term "
            "was found in the scanned output)."
        )
        return errs

    unresolved = 0
    for idx, hit in enumerate(hits):
        if not isinstance(hit, dict):
            errs.append(f"Ambiguity scan: hit[{idx}] must be a dict.")
            unresolved += 1
            continue
        resolution = hit.get("resolution")
        # A string resolution must clear the anti-laziness floor. (A bare bool is
        # already excluded — bool is not a str subclass — so the str check alone
        # rejects True/False; no separate bool guard needed.)
        resolved = (
            isinstance(resolution, str)
            and len(resolution.strip()) >= _AMBIGUITY_RESOLUTION_MIN_CHARS
        )
        if not resolved:
            unresolved += 1
            term = hit.get("term", "?")
            errs.append(
                f"Ambiguity scan: hit[{idx}] (term={term!r}) has no real 'resolution' "
                f"(needs a >={_AMBIGUITY_RESOLUTION_MIN_CHARS}-char self-answer or escalation "
                f"string). An unresolved hit means the self-answer loop did NOT run — "
                f"either self-resolve via code/DDD, or record why it must escalate."
            )

    # Self-report consistency (adversarial LOW, run_932c0991): the agent-supplied
    # summary fields must AGREE with the hits list, so a "hits:[…unresolved…]" can't
    # be masked by a hand-set all_resolved:true / hit_count:0. This is the only
    # cross-check that gives the summary fields teeth — without it they are
    # decorative. (We do NOT independently regex the requirement text here; that is
    # the agent's scanning job — the resolution-floor + this consistency check are
    # the validator's teeth.)
    declared_count = scan.get("hit_count")
    if isinstance(declared_count, int) and declared_count != len(hits):
        errs.append(
            f"Ambiguity scan: hit_count={declared_count} disagrees with len(hits)="
            f"{len(hits)}. The summary must match the recorded hits."
        )
    declared_all_resolved = scan.get("all_resolved")
    if declared_all_resolved is True and unresolved > 0:
        errs.append(
            f"Ambiguity scan: all_resolved=true but {unresolved} hit(s) lack a valid "
            f"resolution. Do not self-report resolved while unresolved hits remain."
        )

    return errs


def _check_working_backwards(data: dict, profile: str) -> list[str]:
    """Greenfield-only Working-Backwards lens (presence + economic-field + pre_mortem
    reuse). Returns error strings tagged 'Working-Backwards:'. Plan B follow-up to
    the ambiguity scan (run_b5b26ebe).

    THE KEY DIFFERENCE FROM THE SIBLING GATES: this fires ONLY when
    understanding.work_type == 'greenfield' AND the profile is strict — it is the
    FIRST gate keyed on work_type (previously a cosmetic, never-read field). Where
    the Understanding Gate (diagnosis-hedge) and Ambiguity Scan (spec-ambiguity)
    apply to ALL strict profiles, customer/value framing is only meaningful for
    NET-NEW features, so non-greenfield work is structurally untouched.

    Scope (EVALUATE-skeptic + adversarial sharpened, run_b5b26ebe): the NOVEL slice
    is the 3 ECONOMIC questions (_WB_ECONOMIC_FIELDS: current_workaround / why_better
    / must_be_true) that the greenfield Understanding row (evaluate.md:140) and the
    Pre-mortem Gate do NOT capture. `target_customer` ("who") was DROPPED from
    enforcement — adversarial flagged it overlaps the Understanding "who has it"; it
    stays in the template as context. "failure reasons" are REUSED — the gate also
    requires a non-empty pre_mortem (its FIRST enforcement; evaluate.md:493 mandates
    it doc-side but the validator never checked it).

    Fail-open by design: a missing/typo'd work_type simply means no WB requirement.
    This is a framing-QUALITY lens, not a safety gate — consistent with the
    relaxed-profile exemption philosophy. Intelligent-Default (self-answer each
    question, human confirms at REVIEW as a taste decision) is a STAGE-DOC behavior;
    the validator only checks structure (block present + fields non-empty), never
    content-truth.
    """
    errs: list[str] = []
    und = data.get("understanding")
    work_type = und.get("work_type") if isinstance(und, dict) else None
    is_strict = profile not in _RELAXED_UNDERSTANDING_PROFILES

    # Gate fires ONLY for greenfield + strict. Everything else: structurally exempt.
    if work_type != "greenfield" or not is_strict:
        return errs

    wb = data.get("working_backwards")
    if not isinstance(wb, dict):
        errs.append(
            "Working-Backwards: greenfield (work_type=='greenfield') in a strict "
            "profile requires a 'working_backwards' block — the customer/value framing "
            "for a net-new feature. Record {target_customer, current_workaround, "
            "why_better, must_be_true} (each a real sentence) PLUS a non-empty "
            "pre_mortem (top-3 failure reasons — reused from the Pre-mortem Gate). "
            "Use Intelligent-Default: self-answer each from PRODUCT.md/DDD first, the "
            "human confirms at REVIEW. (run_b5b26ebe)"
        )
        return errs

    # The 4 ECONOMIC fields — each must be a real, non-empty sentence (the novel,
    # non-redundant slice). A bare bool / sub-floor string carries no value framing.
    for field in _WB_ECONOMIC_FIELDS:
        val = wb.get(field)
        ok = isinstance(val, str) and len(val.strip()) >= _WB_FIELD_MIN_CHARS
        if not ok:
            errs.append(
                f"Working-Backwards: '{field}' is missing or too thin (needs a "
                f">={_WB_FIELD_MIN_CHARS}-char answer). This is the value-framing the "
                f"Understanding/Pre-mortem gates do NOT capture — answer it concretely."
            )

    pre_mortem = data.get("pre_mortem")
    if not isinstance(pre_mortem, list) or len([p for p in pre_mortem if p]) == 0:
        errs.append(
            "Working-Backwards: greenfield requires a non-empty 'pre_mortem' list "
            "(top-3 reasons this fails) — reused from the mandatory Pre-mortem Gate "
            "(evaluate.md:493). This gate is its first code-enforcement for greenfield."
        )

    return errs


# ── Migration-class Gate (AC11, run_1d3df9e6) — CODE-ENFORCED, closes the opt-in escape ──
# A migration-shaped requirement MUST declare a migration_class, else the goal-run
# class-completeness gate (goal_cycle Final Quality Gate step 2.5) no-ops and a class
# sibling ships ungated (the run_0d60e04e decisions-path miss). Opt-in was the C036
# escape; this makes it mandatory-on-keyword. Enforced here so it is NOT prose-only.
_MIGRATION_KEYWORDS = (
    "migrate", "migration", "unify", "unified", "consolidate", "de-dup", "dedup",
    "route all", "route every", "gate all", "gate every", "every path", "all callers",
    "all paths", "single ", "one gate", "funnel", "chokepoint", "through one",
)


def _check_migration_class_declared(data: dict, profile: str) -> list[str]:
    """AC11: if the evaluation's requirement/understanding text is migration-shaped, the
    artifact MUST carry a non-empty `migration_class` block. Strict profiles only (a docs/
    research run that merely mentions 'migrate' isn't shipping code). Fail-CLOSED on the
    keyword hit — the whole point is to remove the opt-out for the runs that need it."""
    if profile in _RELAXED_UNDERSTANDING_PROFILES:
        return []
    und = data.get("understanding") if isinstance(data.get("understanding"), dict) else {}
    haystack = " ".join(str(x).lower() for x in (
        und.get("claim", ""), und.get("evidence", ""), data.get("summary", ""),
        data.get("requirement", ""),
    ))
    if not any(kw in haystack for kw in _MIGRATION_KEYWORDS):
        return []
    mc = data.get("migration_class")
    if isinstance(mc, dict) and mc.get("enumeration_cmd") and mc.get("members"):
        return []
    return [
        "Migration-class Gate (AC11): the requirement is migration-shaped (a class of "
        "callers/siblings migrated across cycles) but no non-empty 'migration_class' block "
        "is declared. Without it, the goal-run class-completeness gate no-ops and a class "
        "sibling no cycle touches ships ungated (the run_0d60e04e decisions miss). Declare "
        "migration_class{description, enumeration_cmd (a physical-sink grep across the tree "
        "root — NOT a hand list), members[{id,disposition,locator,evidence}]}. If this is "
        "NOT a class migration, rephrase the requirement to drop the migration keyword."
    ]


# M1 — solution-INTENT phrases (not bare verbs: "the state change is not
# persisted" must NOT false-block — 'change' there is a noun). These match a
# proposal to act, which belongs in THINK, not an understanding of the present.
import re as _re

# Action verbs that, with an object/target, signal a PLAN (belongs in THINK), not
# a present-state description. "make/switch/use/move/persist/set" added after the
# adversarial showed the flagship [DONE] case ("make X authoritative") slipped
# through. Gerund forms ("adding a field fixes it") and "the fix:"/"fix:" too.
_SOLUTION_VERBS = (
    r"add|change|refactor|rewrite|replace|introduce|implement|create|"
    r"make|switch|use|move|persist|set|remove|wrap|inject|extract"
)
_SOLUTION_GERUNDS = (
    r"adding|changing|refactoring|rewriting|replacing|introducing|implementing|"
    r"creating|making|switching|using|moving|persisting|setting|removing|wrapping"
)
# Negation lead-ins: a verb+object preceded by these is a present-state claim of
# what the code does NOT do ("does not implement the retry") — must NOT false-block.
_NEG_LEAD = r"(?:not|n't|fails?\s+to|never|does\s+not|did\s+not|doesn't|didn't|without)\s+"

_SOLUTION_LANGUAGE_PATTERNS = [
    r"\bi\s+will\b",
    r"\bi'll\b",  # mandatory apostrophe: contraction "I'll" — NOT the adjective "ill" in ill-suited/ill-defined (run_b9452eb9 same-class)
    r"\bwe\s+(?:will|should|need\s+to|must)\b",  # NOT can/could — "we can see/observe" is present-state observation, not a plan (run_b9452eb9)
    r"\blet's\b",  # mandatory apostrophe: the suggestion "let's" — NOT the verb "lets" (run_b9452eb9)
    r"\bthe\s+fix\b",          # "the fix is" / "the fix:" / "the fix should"
    r"\bthe\s+solution\b",
    r"\bto\s+fix\b",
    r"\bshould\s+(?:add|change|use|make|be\s+changed)\b",
    # action-verb + object/target (a plan). Negated forms are stripped BEFORE
    # this scan (see _strip_negated_verbs) so "does not implement the retry" is
    # a present-state claim and does NOT match.
    rf"\b(?:{_SOLUTION_VERBS})\s+"
    rf"(?:a|an|the|to|it|this|that|per-|new\b|[A-Za-z_]+\s+(?:to|authoritative|instead)\b)",
    # imperative claim ("Make the sentinel…", "Make [DONE]…", "Use a circuit breaker…").
    # The object must be a real imperative target — a determiner/pronoun/bracket/quote —
    # NOT a preposition or noun (so sentence-initial noun homographs "Use of the lock…",
    # "Set operations dominate…" are present-state, not plans). \S was too loose; do NOT
    # use [A-Z] here — the regex is IGNORECASE so it would match any letter (run_b9452eb9).
    rf"^\s*(?:{_SOLUTION_VERBS})\s+(?:a|an|the|to|it|this|that|per-|new\b|[\[\"'])",
    # gerund plan ("Adding a field fixes it"). KNOWN LIMITATION (run_b9452eb9): a
    # gerund-as-SUBJECT present-state claim ("Adding the header is handled by
    # middleware today") is structurally identical to a gerund-plan and cannot be
    # disambiguated by regex without losing the L108 block-case. Author workaround:
    # phrase present-state behavior without a sentence-initial gerund+determiner.
    rf"\b(?:{_SOLUTION_GERUNDS})\s+(?:a|an|the|it|this|that)\b",
]
_SOLUTION_LANGUAGE_RE = _re.compile("|".join(_SOLUTION_LANGUAGE_PATTERNS), _re.IGNORECASE)
# Negated verb-phrase ("does not implement", "fails to add") — a present-state
# claim of what the code does NOT do. Stripped before the M1 scan so it can't
# false-trigger the verb+object pattern. (Python `re` forbids variable-width
# lookbehind, so we excise rather than look-behind.)
_NEG_VERB_PHRASE_RE = _re.compile(
    rf"{_NEG_LEAD}(?:{_SOLUTION_VERBS})\b", _re.IGNORECASE
)


def _strip_negated_verbs(text: str) -> str:
    """Remove 'does not implement' / 'fails to add' style negated verb phrases so
    M1 sees only genuine plan-language, not present-state negations."""
    return _NEG_VERB_PHRASE_RE.sub(" ", text)


# Quoted spans — `backtick`, "double", 'single' — are CITATIONS of code/patterns,
# not plan intent. A claim describing the `let's` pattern or quoting "add a guard"
# is present-state, not a proposal. Strip them before M1 (run_7cf9da85 C3 — the
# author hit this live: an evaluate claim describing the very pattern under fix
# self-blocked M1). The single-quote span is boundary-gated (see the regex's inline
# note) so contraction apostrophes never open/close a span — the detail lives there.
_QUOTED_SPAN_RE = _re.compile(
    r"`[^`]*`"                          # backtick code span
    r"|\"[^\"]*\""                      # double-quoted span
    # single-quoted span: the opening ' must NOT be preceded by a letter and the
    # closing ' must NOT be followed by a letter — so a contraction apostrophe
    # ("that's", "it's") can never open/close a span and swallow plan-language
    # between two contractions (adversarial HIGH, run_7cf9da85). Bounded length
    # avoids a greedy run across the whole claim.
    r"|(?<![A-Za-z])'[^'\n]{0,80}'(?![A-Za-z])",
)


def _strip_quoted_spans(text: str) -> str:
    """Remove backtick/double/single-quoted spans so M1 scans only UNQUOTED prose.
    Quoted text is a citation (code, a pattern, a quoted phrase), not plan intent."""
    return _QUOTED_SPAN_RE.sub(" ", text)

# M2 — hedge words (EN + CJK). An unresolved hedge = inference, not observation.
_HEDGE_PATTERNS = [
    r"\bprobably\b", r"\blikely\b", r"\bmaybe\b", r"\bperhaps\b",
    r"\bshould\s+be\b", r"\bi\s+think\b", r"\bi\s+guess\b",
    r"\bseems?\s+(?:to|like)\b", r"\bmight\s+be\b", r"\bcould\s+be\b",
    r"似乎", r"可能", r"应该是", r"大概", r"也许", r"估计",
]
_HEDGE_RE = _re.compile("|".join(_HEDGE_PATTERNS), _re.IGNORECASE)


def _resolve_understanding_evidence(data: dict) -> object:
    """Return the evidence value, preferring understanding.evidence, falling back
    to the legacy observation_evidence alias (bug-class back-compat)."""
    und = data.get("understanding")
    if isinstance(und, dict) and "evidence" in und:
        return und.get("evidence")
    return data.get("observation_evidence")


def _has_real_evidence(obs: object) -> bool:
    """True iff obs is a non-empty observation: a >=20-char string, or a non-empty
    list/dict. A bare bool carries zero information → False (REPRO + UG share this)."""
    return (
        not isinstance(obs, bool)
        and bool(obs)
        and (not isinstance(obs, str) or len(obs.strip()) >= _EVIDENCE_MIN_CHARS)
    )


def _check_understanding_gate(data: dict, profile: str) -> list[str]:
    """Universal understanding gate (M1 + M2 + presence). Returns error strings
    tagged 'Understanding gate:'. M3 (skeptic) is behavioral, enforced in
    evaluate.md, not here."""
    errs: list[str] = []
    und = data.get("understanding")
    # Fail-closed: strict unless explicitly relaxed. "standard"/unknown → strict.
    is_strict = profile not in _RELAXED_UNDERSTANDING_PROFILES
    has_block = isinstance(und, dict)

    # Presence: strict profiles MUST carry the block with real evidence. The
    # observation_evidence alias satisfies this for bug-class evals that haven't
    # migrated to the block (back-compat — no double-blocking with REPRO).
    if is_strict:
        evidence = _resolve_understanding_evidence(data)
        if not _has_real_evidence(evidence):
            errs.append(
                "Understanding gate: strict profile requires an 'understanding' block "
                "with a non-empty 'evidence' field — an OBSERVATION of the CURRENT state "
                "(code-trace file:line / ps / log counts / repro / characterization), not "
                "an inference. THINK (the fix) cannot load until EVALUATE states what IS. "
                "Add understanding.{work_type,claim,evidence,evidence_kind,skeptic_verdict}, "
                "or use observation_evidence for a bug-class eval. "
                "(design 2026-06-26-understanding-gate)."
            )
            # No block / no evidence at all → presence error is the actionable one;
            # M1/M2 have nothing to scan. Return early.
            if not has_block:
                return errs

    # M1 + M2 scan the claim when a block is present (strict AND relaxed — cheap,
    # catches a solution-language / hedged claim even on a docs profile).
    if has_block:
        claim = und.get("claim")
        claim_str = claim.strip() if isinstance(claim, str) else ""
        evidence = _resolve_understanding_evidence(data)

        # M1 — solution language in the claim = a plan, not present-state.
        # Strip quoted spans (citations of code/patterns) AND negated verb phrases
        # first, so M1 scans only genuine UNQUOTED plan-language ("does not implement
        # X" is present-state; "`let's`" is a citation, not a suggestion).
        if claim_str and _SOLUTION_LANGUAGE_RE.search(
            _strip_negated_verbs(_strip_quoted_spans(claim_str))
        ):
            errs.append(
                "Understanding gate (M1 solution-language): understanding.claim contains "
                "solution/plan language (e.g. 'I will / the fix is / add … / refactor …'). "
                "The claim must describe the CURRENT state of the world (present-tense), not "
                "the proposed change — the fix belongs in THINK, on the other side of the wall."
            )

        # M2 — a hedge (in the claim OR the evidence) is inference, resolved ONLY
        # by a concrete, NON-hedged observation in evidence. A hedged evidence
        # string ("probably the same thing, not sure") does NOT resolve a hedged
        # claim — it is the same inference restated. (design §3.4: "paired with an
        # explicit OBSERVATION that resolves it").
        evidence_str = evidence.strip() if isinstance(evidence, str) else ""
        claim_hedged = bool(claim_str) and bool(_HEDGE_RE.search(claim_str))
        evidence_hedged = bool(evidence_str) and bool(_HEDGE_RE.search(evidence_str))
        evidence_is_observation = _has_real_evidence(evidence) and not evidence_hedged
        if (claim_hedged or evidence_hedged) and not evidence_is_observation:
            errs.append(
                "Understanding gate (M2 hedge-scan): the claim or evidence contains an "
                "unresolved hedge (似乎/可能/probably/should be/I think…) with no concrete, "
                "non-hedged observation in 'evidence' to resolve it. Either OBSERVE "
                "(ps/log counts/gauge/code-trace) and cite it, or remove the hedge. "
                "R16b mechanized — observe before asserting."
            )

    return errs


def get_stage_schema(stage: str) -> dict:
    """Public API: return schema + template for a stage.

    Used by artifact_cli publish --stage for pre-publish validation.
    Single source of truth — no duplicate definitions elsewhere.
    """
    schema = STAGE_SCHEMAS.get(stage, {})
    template = STAGE_TEMPLATES.get(stage, {})
    depth = STAGE_DEPTH.get(stage, {})
    return {
        "required": schema.get("required", []),
        "recommended": schema.get("recommended", []),
        "depth": depth,
        "template": template,
    }


def validate_artifact_data(
    stage: str, data: dict, profile: str = "full", repo_root: str | None = None,
) -> list[str]:
    """Public API: validate artifact data against stage schema.

    Returns list of error strings. Empty list = valid.
    Checks: required fields, depth nested fields, stage-specific invariants.
    Used by artifact_cli at publish time for fail-fast validation.

    Args:
        stage: Pipeline stage name (evaluate, think, plan, build, review, test, deliver)
        data: Artifact data dict to validate
        profile: Pipeline profile (full, bugfix, trivial, research, docs). Defaults to
                 "full" which is the strictest — trivial/research/docs exempt from
                 adversarial review requirements.
        repo_root: Optional source-repo root used ONLY to confine the L4
                 verify-against-disk reads (deliver stage). None (the default,
                 preserving all existing callers) = no confinement; disk_check
                 loci are absolute paths so no join is performed regardless.
    """
    errors: list[str] = []

    # Check 6 at PUBLISH time (Run A, run_7627f63c): reject an off-profile stage
    # BEFORE any schema work. This MUST precede the `if not schema: return []`
    # early-return below — else an artifactless off-profile stage (goal_cycle /
    # reflect, which have no STAGE_SCHEMAS entry) would slip through un-checked.
    # Reuses the SAME helper as the completion-time validate() Check 6 (:2288) — one
    # source of truth, no forked profile logic (R27). Previously this check ran only
    # at completion, so `publish --stage build` in a docs run was accepted and only a
    # downstream build-specific invariant fired as a confusing symptom (run_6589c62b).
    if not _check_profile_respected(stage, profile):
        return [
            f"'{stage}' is not in the '{profile}' profile "
            f"(stages: {get_profile_stages(profile)}). "
            f"Publish this stage under a profile whose stage-list includes it, or "
            f"re-run run-create with the profile that matches your work "
            f"(e.g. a markdown+commit change is 'trivial', not 'docs')."
        ]

    schema = STAGE_SCHEMAS.get(stage)
    if not schema:
        return []

    # Required fields
    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"Missing required field '{field}' for stage '{stage}'")

    # Depth: nested required fields
    # Absent parent field = depth requirement FAILED (not silently skipped)
    for parent_field, child_fields in STAGE_DEPTH.get(stage, {}).items():
        parent_val = data.get(parent_field)
        if parent_val is None:
            # Parent field entirely absent — this IS the violation
            # Exception: trivial/research/docs profiles can skip adversarial
            _skip_profiles = ("trivial", "research", "docs")
            if profile not in _skip_profiles:
                errors.append(
                    f"Missing required field '{parent_field}' — "
                    f"depth validation requires this for {stage} stage "
                    f"(profile={profile}). Was the step actually executed?"
                )
        elif isinstance(parent_val, dict):
            for child in child_fields:
                if child not in parent_val:
                    errors.append(
                        f"Missing '{parent_field}.{child}' — required for depth validation"
                    )
                elif (
                    isinstance(parent_val[child], list)
                    and len(parent_val[child]) == 0
                    and child == "patterns"  # PE-4: Only patterns must be non-empty
                ):
                    # patterns list must have entries (even N/A results).
                    # findings list can legitimately be empty (clean review).
                    errors.append(
                        f"'{parent_field}.{child}' is empty — must contain at least one entry"
                    )

    # Stage-specific invariants (subset of _check_depth for fast feedback)
    if stage == "deliver":
        ar = data.get("adversarial_review")
        if isinstance(ar, dict):
            tier = ar.get("profile_tier", "")
            # Tier=skipped on full/bugfix → BLOCK (C026 fix)
            # Relaxed profiles (trivial/research/docs) can skip adversarial
            _strict_profiles = ("full", "bugfix", "")
            if tier in ("skipped", "lite") and profile in _strict_profiles:
                errors.append(
                    f"adversarial_review.profile_tier='{tier}' but profile='{profile}' "
                    f"requires full adversarial review. Only trivial/research/docs exempt."
                )
            # spawned=true enforcement: adversarial sub-agent must actually run (not just tier declared)
            spawned = ar.get("spawned")
            if profile in _strict_profiles:
                if spawned is True or spawned == "true" or spawned == 1:
                    # Two-field enforcement (Rule 23): spawned=true alone is insufficient.
                    # Agent must also provide 'evidence' field describing HOW it was spawned.
                    # This blocks the CLASS A pattern of declaring spawned=true without
                    # actually invoking the Agent tool. Combined with GS021 golden set
                    # trajectory case, creates two-layer enforcement.
                    evidence = ar.get("evidence", "")
                    if not evidence or not str(evidence).strip():
                        errors.append(
                            "adversarial_review.spawned=true but 'evidence' field is missing or empty. "
                            "Rule 23 requires describing HOW the sub-agent was spawned "
                            "(e.g., 'Agent tool invocation for adversarial review'). "
                            "This prevents self-review disguised as adversarial."
                        )
                else:
                    errors.append(
                        f"adversarial_review.spawned={spawned} but profile='{profile}' "
                        f"requires sub-agent to be actually spawned (spawned=true). "
                        f"Cannot skip adversarial execution."
                    )
            # Finding-level confidence gate — INDEPENDENT of the spawned/tier
            # branches above (a genuinely-spawned review can still carry unresolved
            # blocking findings). Shared helper = single source of truth with the
            # completion-time gate in _check_depth (R27: no divergence).
            if tier and tier != "skipped":
                blocked = _blocked_findings(ar.get("findings", []))
                if blocked:
                    errors.append(
                        f"{len(blocked)} unresolved blocking finding(s) — HIGH "
                        f"(any confidence) or MEDIUM with confidence >= "
                        f"{CONFIDENCE_GATE_THRESHOLD} (missing confidence = "
                        f"fail-closed). Fix each and set 'resolved': true, or "
                        f"lower a MEDIUM's confidence below {CONFIDENCE_GATE_THRESHOLD} "
                        f"if it is genuinely note-only."
                    )
                # L4 verify-against-disk (Run B): the COMPLEMENT pass over
                # RESOLVED findings — confirm each disk_check locus is actually
                # on disk (INSTRUCTIONS.md:581, previously prose-only). Shared
                # helper with the completion-time gate in _check_depth (R27).
                # Only BLOCK-level disk errors surface here (this function returns
                # errors only); the WARN-level signals (missing disk_check on
                # HIGH/CRIT, invalid loci) surface at the completion validate()
                # path, which owns a warnings list — see _check_depth below.
                _disk_errs, _ = _verify_findings_on_disk(ar.get("findings", []), repo_root)
                errors.extend(_disk_errs)

        # --- Three-Layer Governance: Full deliver ceremony enforcement ---
        # For full/bugfix profiles, ALL deliver sub-steps must have evidence.
        # This prevents the C028 pattern: running adversarial but skipping
        # completion audit, meta-review, and convergence loop.
        # Profile-aware relaxation: trivial/research/docs skip meta_review + convergence.
        _relaxed_profiles = ("trivial", "research", "docs")
        if profile in _relaxed_profiles:
            # Relaxed validation: only need completion_audit (basic AC check)
            ca = data.get("completion_audit")
            if not isinstance(ca, dict):
                errors.append(
                    "completion_audit missing from deliver artifact. "
                    "Even trivial profiles need basic AC verification."
                )
            # meta_review, convergence: NOT required for relaxed profiles
        elif profile in ("full", "bugfix", ""):
            # Check: completion_audit must exist and be all_green
            ca = data.get("completion_audit")
            if not isinstance(ca, dict):
                errors.append(
                    "completion_audit missing from deliver artifact. "
                    "Run Completion Audit (AC → evidence verification) before declaring done."
                )
            elif not ca.get("all_green"):
                gaps = ca.get("gaps", "unknown")
                errors.append(
                    f"completion_audit.all_green=false (gaps={gaps}). "
                    f"Fix gaps before declaring push-ready."
                )

            # Check: meta_review must exist (verdict CLEAR or risks addressed)
            mr = data.get("meta_review")
            if not mr and not data.get("meta_review_verdict"):
                errors.append(
                    "meta_review missing from deliver artifact. "
                    "Run Meta-Review (pipeline blind spot analysis) before declaring done."
                )

            # Check: convergence evidence (iterations + all_pass)
            conv = data.get("convergence")
            if not isinstance(conv, dict):
                errors.append(
                    "convergence missing from deliver artifact. "
                    "Run Quality Convergence Loop (6-layer gate) before declaring done."
                )
            elif not conv.get("all_pass") and conv.get("final_status") != "push-ready":
                errors.append(
                    f"convergence.all_pass=false — Quality Convergence Loop did not pass. "
                    f"Iterations: {conv.get('iterations', '?')}"
                )

    if stage == "build":
        tdd = data.get("tdd")
        if isinstance(tdd, dict):
            files = data.get("files_changed", [])
            smoke = tdd.get("smoke_tests", 0)
            if isinstance(files, list) and len(files) > 1 and smoke == 0:
                errors.append(
                    f"smoke_tests=0 but {len(files)} files changed — "
                    f"run smoke tests with real objects"
                )

    # ── REPRO gate (bug-class only) — diagnosis before build ──────────────
    # A bug-fix evaluation must carry OBSERVATION evidence, not inference. This
    # session twice shipped a confident-but-wrong root cause to BUILD (kernel-OOM
    # narrative; "frontend ignores [DONE]") — both framing errors that a
    # diagnosis-evidence requirement would have caught at EVALUATE. The fix that
    # actually worked came from OBSERVING (ps / log-signal counting / live
    # gauges), never from reading code and inferring. So: a bug-class evaluation
    # is BLOCKED until it records what was OBSERVED. Scope-gated so feature/goal/
    # research work is structurally untouched (DoD3b: no false-block on non-bug).
    if stage == "evaluate":
        # Relaxed profiles (trivial/research/docs) skip REPRO — matches the
        # depth-check relaxation pattern above; a doc-typo "bugfix" shouldn't
        # demand ps/log forensics. Adversarial LOW (run_688b6487).
        _repro_skip = ("trivial", "research", "docs")
        _is_bug = (
            (data.get("scope") == "bugfix") or (data.get("bug_class") is True)
        ) and profile not in _repro_skip
        if _is_bug:
            # Resolve via the shared alias resolver: a bug-class eval may carry its
            # observation either in the legacy observation_evidence field OR in the
            # new understanding.evidence block. Either satisfies REPRO (back-compat).
            obs = _resolve_understanding_evidence(data)
            # _has_real_evidence: a bare bool carries ZERO info (adversarial MED);
            # strings need >=20 non-blank chars (anti-laziness floor — NOT anti-
            # fabrication; the diagnostic-challenge sub-agent is the fabrication
            # backstop). Non-empty list/dict accepted; empty collections fail.
            if not _has_real_evidence(obs):
                errors.append(
                    "REPRO gate: bug-class evaluation (scope=bugfix) requires a non-empty "
                    "'observation_evidence' field — what did you OBSERVE that proves this "
                    "root cause (ps / log-signal counts / live gauge / repro), not what you "
                    "inferred from reading code. Diagnosis-before-build (run_688b6487). "
                    "Add observation_evidence, or set scope to a non-bug class if this is "
                    "not a bug fix."
                )

        # ── Universal Understanding gate (ALL work types) ─────────────────
        # Generalizes REPRO beyond bug-class: strict profiles require an
        # observation-backed, present-tense (M1), non-hedged (M2) understanding
        # block before THINK. Relaxed profiles aren't forced to carry it, but a
        # present block is still scanned. The bug-class REPRO marker above and
        # this share the alias resolver, so a single understanding.evidence
        # satisfies both without double-blocking.
        errors.extend(_check_understanding_gate(data, profile))

        # ── Self-Socratic ambiguity scan (evaluate) ───────────────────────
        # Re-scan the requirement-clarification output for residual ambiguity.
        # Distinct field + tag from the Understanding Gate (run_932c0991).
        errors.extend(_check_ambiguity_scan(data, profile))

        # ── Working-Backwards lens (greenfield-only) ──────────────────────
        # Customer/value framing for NET-NEW features only. Fires ONLY when
        # understanding.work_type=='greenfield' AND strict — the first gate keyed
        # on work_type. Distinct 'Working-Backwards:' tag (run_b5b26ebe).
        errors.extend(_check_working_backwards(data, profile))

        # ── Migration-class Gate (AC11, run_1d3df9e6) ─────────────────────
        # A migration-shaped requirement MUST declare migration_class, else the
        # goal-run class-completeness gate no-ops → a class sibling ships ungated.
        # Code-enforced here (not prose) to close the C036 opt-in escape.
        errors.extend(_check_migration_class_declared(data, profile))

    # ── Self-Socratic ambiguity scan (think) ──────────────────────────────
    # THINK re-scans its risk-probe assumptions + recommendation. THINK has no
    # understanding block, so this is its only self-Socratic gate.
    if stage == "think":
        errors.extend(_check_ambiguity_scan(data, profile))

    return errors


# ---------------------------------------------------------------------------
# Stage routing — explicit I/O contracts per stage (Check 12/13)
# ---------------------------------------------------------------------------

STAGE_ROUTING: dict[str, dict[str, list[str]]] = {
    "evaluate": {
        "consumes": [],
        "produces": ["evaluation"],
        "optional_produces": [],
    },
    "think": {
        "consumes": ["evaluation"],
        "produces": ["research"],
        "optional_produces": ["alternatives"],
    },
    "plan": {
        "consumes": ["evaluation", "research"],
        "produces": ["design_doc"],
        "optional_produces": [],
    },
    "build": {
        "consumes": ["design_doc"],
        "produces": ["changeset"],
        "optional_produces": [],
    },
    "review": {
        "consumes": ["changeset"],
        "produces": ["review"],
        "optional_produces": ["security_review"],
    },
    "test": {
        # F2: design_doc removed — test stage runs tests against changeset,
        # informed by review findings. design_doc is for build (implementation).
        "consumes": ["changeset", "review"],
        "produces": ["test_report"],
        "optional_produces": [],
    },
    "deliver": {
        "consumes": ["changeset", "review", "test_report"],
        "produces": ["delivery"],
        "optional_produces": ["report"],
    },
    "reflect": {
        "consumes": ["test_report", "delivery"],
        "produces": [],
        "optional_produces": [],
    },
}

# Default TTL for artifact freshness (hours) — 7 days
_DEFAULT_TTL_HOURS = 168


# Stages that never require an artifact (regardless of profile)
NO_ARTIFACT_STAGES = {"reflect", "think"}

# Stages where artifact is optional for bugfix/trivial profiles
# (reduces ceremony — validator warns instead of blocking)
ARTIFACT_OPTIONAL_FOR_PROFILES: dict[str, set[str]] = {
    "bugfix": {"plan", "build", "review", "test", "deliver"},
    "trivial": {"build", "review", "test", "deliver"},
}

# Stages where decisions are optional (informational stages)
DECISION_OPTIONAL_STAGES = {"reflect", "deliver"}

# Code file extensions for changeset analysis
_CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".sh"}

# Evidence file extensions for semantic depth checks (includes docs + config)
_EVIDENCE_FILE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".md", ".json", ".yaml", ".sh")


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def _get_workspace() -> Path:
    import os
    from config import get_app_data_dir
    ws = os.environ.get("SWARM_WORKSPACE", str(get_app_data_dir() / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def _get_artifacts_dir(project: str) -> Path | None:
    """Get the .artifacts/ directory for a project."""
    ws = _get_workspace()
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    return artifacts_dir if artifacts_dir.is_dir() else None


def _load_run(project: str, run_id: str) -> dict[str, Any] | None:
    """Load a pipeline run from .artifacts/runs/<run_id>/run.json."""
    ws = _get_workspace()
    run_file = ws / "Projects" / project / ".artifacts" / "runs" / run_id / "run.json"
    if not run_file.exists():
        return None
    return json.loads(run_file.read_text())


def _load_artifact_data(project: str, run_id: str, artifact_id: str) -> dict[str, Any] | None:
    """Load artifact data file by artifact_id via manifest lookup.

    Note: run_id is accepted for call-site convenience but not used —
    manifest lookup is by artifact_id only (covers both run-scoped and
    top-level artifacts).
    """
    ws = _get_workspace()
    artifacts_dir = ws / "Projects" / project / ".artifacts"

    # Lookup via manifest (covers both run-scoped and top-level artifacts)
    manifest_file = artifacts_dir / "manifest.json"
    if not manifest_file.exists():
        return None

    try:
        manifest = json.loads(manifest_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    for entry in manifest.get("artifacts", []):
        if entry.get("id") == artifact_id:
            data_file = artifacts_dir / entry.get("file", "")
            if data_file.exists():
                try:
                    return json.loads(data_file.read_text())
                except (json.JSONDecodeError, OSError):
                    return None
    return None


# ---------------------------------------------------------------------------
# DDD cross-document consistency
# ---------------------------------------------------------------------------

def _parse_non_goals(product_text: str) -> list[str]:
    """Extract non-goal keywords from PRODUCT.md's Non-Goals section.

    Returns lowercase keyword phrases (e.g. ["cloud saas", "general chatbot"]).
    """
    non_goals: list[str] = []
    in_section = False
    for line in product_text.splitlines():
        stripped = line.strip()
        # Detect ## Non-Goals header
        if re.match(r"^##\s+Non[- ]?Goals", stripped, re.IGNORECASE):
            in_section = True
            continue
        # Exit on next ## header
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            # Extract the bold part or the whole line
            bold = re.findall(r"\*\*([^*]+)\*\*", stripped)
            if bold:
                non_goals.extend(b.strip().lower() for b in bold)
            else:
                # Use the line content after "- "
                non_goals.append(stripped[2:].strip().lower())
    return non_goals


def _parse_failed_patterns(improvement_text: str) -> list[str]:
    """Extract failed pattern descriptions from IMPROVEMENT.md's What Failed section.

    Returns lowercase summary phrases from each bullet.
    """
    patterns: list[str] = []
    in_section = False
    for line in improvement_text.splitlines():
        stripped = line.strip()
        if re.match(r"^##\s+What Failed", stripped, re.IGNORECASE):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            # Extract bold summary or first sentence
            bold = re.findall(r"\*\*([^*]+)\*\*", stripped)
            if bold:
                for b in bold:
                    b_clean = b.strip().lower()
                    # Skip date-only entries (auto-writeback noise)
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", b_clean):
                        continue
                    # Skip very short entries (< 10 chars, likely noise)
                    if len(b_clean) < 10:
                        continue
                    patterns.append(b_clean)
            else:
                text = stripped[2:].strip()
                # Take first sentence or up to 120 chars
                first_sentence = re.split(r"[.!?]", text)[0].strip()
                if first_sentence and len(first_sentence) >= 10:
                    patterns.append(first_sentence.lower()[:120])
    return patterns


def _compute_doc_checksum(text: str) -> str:
    """Compute a stable checksum for DDD document content (ignores whitespace variance)."""
    normalized = re.sub(r"\s+", " ", text.strip())
    return hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest()[:12]


def check_ddd_consistency(project: str, context_text: str | None = None) -> dict[str, Any]:
    """Cross-validate DDD documents for a project. Works standalone or within pipeline.

    Checks:
      1. Non-goals (PRODUCT.md) vs architecture description (TECH.md)
         — flags if non-goal keywords appear in TECH.md architecture section
      2. Failed patterns (IMPROVEMENT.md) existence check
         — warns if no failed patterns recorded (empty learning)
      3. Document staleness — computes checksums for change detection

    Args:
        project: Project name (directory under Projects/)
        context_text: Optional text to check against non-goals (e.g. evaluation summary).
                      If provided, also checks this text against non-goals.

    Returns:
        {"warnings": [...], "checksums": {"PRODUCT.md": "abc...", ...},
         "non_goals": [...], "failed_patterns": [...]}
    """
    ws = _get_workspace()
    project_dir = ws / "Projects" / project
    warnings: list[str] = []
    checksums: dict[str, str] = {}
    non_goals: list[str] = []
    failed_patterns: list[str] = []

    # Load DDD docs
    ddd_docs: dict[str, str] = {}
    for doc_name in DDD_CANONICAL_DOCS:
        doc_path = ddd_path(project_dir, doc_name)
        if doc_path.exists():
            try:
                content = doc_path.read_text()
                ddd_docs[doc_name] = content
                checksums[doc_name] = _compute_doc_checksum(content)
            except OSError:
                warnings.append(f"DDD: Could not read {doc_name} for project '{project}'")

    if not ddd_docs:
        return {
            "warnings": [f"DDD: No DDD documents found for project '{project}' — skipping consistency check"],
            "checksums": {},
            "non_goals": [],
            "failed_patterns": [],
        }

    # Check 1: Non-goals vs TECH.md architecture
    if "PRODUCT.md" in ddd_docs:
        non_goals = _parse_non_goals(ddd_docs["PRODUCT.md"])

    if non_goals and "TECH.md" in ddd_docs:
        tech_text = ddd_docs["TECH.md"].lower()
        # Only check the Architecture section of TECH.md (not the whole doc)
        arch_section = _extract_section(tech_text, "architecture")
        check_text = arch_section if arch_section else tech_text[:2000]

        for ng in non_goals:
            # Extract meaningful keywords (skip short/common words)
            keywords = [w for w in ng.split() if len(w) > 3 and w not in
                        {"not", "just", "only", "that", "this", "with", "from",
                         "have", "been", "does", "about", "into", "more", "than"}]
            for kw in keywords:
                if kw in check_text:
                    warnings.append(
                        f"DDD conflict: Non-goal '{ng}' keyword '{kw}' "
                        f"appears in TECH.md architecture — verify alignment"
                    )

    # Check 1b: Non-goals vs context_text (e.g. evaluation approach)
    if non_goals and context_text:
        ctx_lower = context_text.lower()
        for ng in non_goals:
            keywords = [w for w in ng.split() if len(w) > 3 and w not in
                        {"not", "just", "only", "that", "this", "with", "from",
                         "have", "been", "does", "about", "into", "more", "than"}]
            for kw in keywords:
                if kw in ctx_lower:
                    warnings.append(
                        f"DDD conflict: Non-goal '{ng}' keyword '{kw}' "
                        f"found in pipeline context — verify this isn't violating a non-goal"
                    )

    # Check 2: Failed patterns existence
    if "IMPROVEMENT.md" in ddd_docs:
        failed_patterns = _parse_failed_patterns(ddd_docs["IMPROVEMENT.md"])
        if not failed_patterns:
            warnings.append(
                "DDD note: IMPROVEMENT.md has no 'What Failed' entries — "
                "consider recording lessons from past work"
            )

    # Check 3: Missing DDD docs (not blocking, just informational)
    missing = [d for d in DDD_CANONICAL_DOCS
               if d not in ddd_docs]
    if missing:
        warnings.append(
            f"DDD incomplete: Missing {', '.join(missing)} for project '{project}' — "
            f"pipeline runs at reduced intelligence (L0/L1 instead of L2)"
        )

    return {
        "warnings": warnings,
        "checksums": checksums,
        "non_goals": non_goals,
        "failed_patterns": failed_patterns,
    }


def check_ddd_staleness(project: str) -> dict[str, Any]:
    """Check if any completed pipeline runs are stale (DDD docs changed since evaluation).

    Scans all completed runs in Projects/<project>/.artifacts/runs/, reads their
    stored ``ddd_checksums`` field, and compares against current DDD doc checksums.

    Returns:
        {
            "current_checksums": {"PRODUCT.md": "abc...", ...},
            "stale_runs": [
                {"run_id": "run_xxx", "stale_docs": ["PRODUCT.md"],
                 "run_checksums": {...}, "status": "completed"}
            ],
            "fresh_runs": ["run_yyy"],
            "untracked_runs": ["run_zzz"]  # runs without stored checksums
        }
    """
    ws = _get_workspace()
    runs_dir = ws / "Projects" / project / ".artifacts" / "runs"

    # Get current checksums
    current = check_ddd_consistency(project)
    current_checksums = current["checksums"]

    result: dict[str, Any] = {
        "current_checksums": current_checksums,
        "stale_runs": [],
        "fresh_runs": [],
        "untracked_runs": [],
    }

    if not runs_dir.exists():
        return result

    for run_dir in sorted(runs_dir.iterdir()):
        run_file = run_dir / "run.json"
        if not run_file.exists():
            continue

        try:
            run = json.loads(run_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        run_id = run.get("id", run_dir.name)
        run_status = run.get("status", "unknown")

        # Only check completed or delivered runs (active runs will re-evaluate anyway)
        if run_status not in ("completed", "delivered"):
            continue

        stored_checksums = run.get("ddd_checksums")
        if not stored_checksums:
            result["untracked_runs"].append(run_id)
            continue

        # Compare each doc
        stale_docs = []
        for doc_name, current_hash in current_checksums.items():
            stored_hash = stored_checksums.get(doc_name)
            if stored_hash and stored_hash != current_hash:
                stale_docs.append(doc_name)
            elif not stored_hash and current_hash:
                # Doc was added after the run
                stale_docs.append(doc_name)

        if stale_docs:
            result["stale_runs"].append({
                "run_id": run_id,
                "stale_docs": stale_docs,
                "run_checksums": stored_checksums,
                "status": run_status,
            })
        else:
            result["fresh_runs"].append(run_id)

    return result


def _extract_section(text: str, heading: str) -> str:
    """Extract a markdown section by heading (case-insensitive). Returns empty string if not found."""
    lines = text.splitlines()
    capturing = False
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(rf"^##\s+{re.escape(heading)}", stripped, re.IGNORECASE):
            capturing = True
            continue
        if capturing and stripped.startswith("## "):
            break
        if capturing:
            result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Depth validation (L2) — field values indicate real work
# ---------------------------------------------------------------------------

def _check_depth(stage: str, artifact_data: dict, profile: str,
                 run_id: str = "") -> list[str]:
    """Layer 2: validate field values, not just existence.

    Catches hollow artifacts where fields exist but content indicates
    the quality gate was not actually executed.
    """
    errors: list[str] = []

    if stage == "review":
        # runtime_patterns: must be dict with checked > 0 and patterns list
        rp = artifact_data.get("runtime_patterns")
        if rp is not None and not isinstance(rp, dict):
            errors.append(
                f"Depth: runtime_patterns must be a dict, got {type(rp).__name__}"
            )
        elif isinstance(rp, dict):
            checked = rp.get("checked", 0)
            if checked == 0:
                errors.append(
                    "Depth: runtime_patterns.checked == 0 — "
                    "RP1-RP29 checklist was not executed"
                )
            elif not rp.get("patterns"):
                errors.append(
                    f"Depth: runtime_patterns.checked={checked} but patterns list is empty — "
                    f"include per-pattern results (even 'N/A' is valid)"
                )

    if stage == "deliver":
        # adversarial_review: MUST exist as dict with profile_tier for full/bugfix
        ar = artifact_data.get("adversarial_review")
        if ar is None:
            # Absent field = adversarial review was never run
            if profile in ("full", "bugfix", ""):
                errors.append(
                    "Depth: adversarial_review field MISSING from deliver artifact — "
                    "adversarial sub-agent was never spawned. This is MANDATORY for "
                    "full/bugfix profiles. Spawn specialist sub-agents before publishing."
                )
        elif not isinstance(ar, dict):
            errors.append(
                f"Depth: adversarial_review must be a dict, got {type(ar).__name__}"
            )
        elif isinstance(ar, dict):
            tier = ar.get("profile_tier")
            if not tier:
                errors.append(
                    "Depth: adversarial_review.profile_tier missing — "
                    "was the sub-agent actually spawned?"
                )
            elif tier in ("skipped", "lite") and profile in ("full", "bugfix"):
                errors.append(
                    f"Depth: adversarial_review.profile_tier='{tier}' but profile='{profile}' "
                    f"requires full adversarial review (independent sub-agent). "
                    f"Only docs/trivial/research profiles allow lite/skipped."
                )
            elif tier != "skipped" and "findings" not in ar:
                errors.append(
                    "Depth: adversarial_review ran but has no 'findings' field"
                )
            elif tier != "skipped":
                # Blocking findings: HIGH (any confidence) + MEDIUM with
                # confidence >= threshold, via the shared _blocked_findings
                # helper (single source of truth with the publish-time gate —
                # previously these two sites had duplicated HIGH-only filters
                # that could diverge, R27).
                blocked = _blocked_findings(ar.get("findings", []))
                if blocked:
                    errors.append(
                        f"Depth: adversarial_review has {len(blocked)} unresolved "
                        f"blocking finding(s) — HIGH (any confidence) or MEDIUM "
                        f"with confidence >= {CONFIDENCE_GATE_THRESHOLD} (missing "
                        f"confidence = fail-closed). Fix before delivery."
                    )
                # L4 verify-against-disk (Run B): COMPLEMENT pass over RESOLVED
                # findings — the stronger completion-time gate (this path gates
                # status:completed). Shared helper with the publish-time gate in
                # validate_artifact_data (R27: one source of truth, no fork).
                # disk_check loci are absolute; repo_root is not threaded here
                # (None = no confinement) — the completion path runs from the
                # validator CLI without a registry, and absolute paths need no
                # join anyway (Gate-1 Attack-3).
                _disk_errs, _ = _verify_findings_on_disk(ar.get("findings", []))
                errors.extend(_disk_errs)

            # Two-field (spawned + evidence) enforcement at COMPLETION time.
            # Mirrors validate_artifact_data:423-445 (publish-time), closing the
            # fail-open hole found by adversarial review of run_45ab67c7: the
            # completion gate (validate()->_check_depth) previously checked only
            # profile_tier, so a spawned=false self-review artifact passed
            # completion. This makes the gate_spawn_blocked guarantee structural
            # on EVERY path to status:completed, not just `publish --stage deliver`.
            # (C037/CLASS-A fail-open-at-last-gate pattern.)
            if isinstance(ar, dict) and profile in ("full", "bugfix", ""):
                spawned = ar.get("spawned")
                if spawned is True or spawned == "true" or spawned == 1:
                    evidence = ar.get("evidence", "")
                    if not evidence or not str(evidence).strip():
                        errors.append(
                            "Depth: adversarial_review.spawned=true but 'evidence' field is "
                            "missing or empty. Rule 23 requires describing HOW the sub-agent "
                            "was spawned. This prevents self-review disguised as adversarial."
                        )
                else:
                    errors.append(
                        f"Depth: adversarial_review.spawned={spawned} but profile='{profile}' "
                        f"requires the sub-agent to be actually spawned (spawned=true). "
                        f"Self-review after a rejected spawn is the CLASS A bypass — "
                        f"CHECKPOINT reason=gate_spawn_blocked instead."
                    )

        # completion_audit: MUST exist for full/bugfix profiles
        ca = artifact_data.get("completion_audit")
        if ca is None and profile in ("full", "bugfix", ""):
            errors.append(
                "Depth: completion_audit field MISSING from deliver artifact — "
                "was the Completion Audit (AC → evidence verification) actually run? "
                "This is MANDATORY. Run the audit before publishing."
            )
        elif isinstance(ca, dict):
            if "all_green" not in ca:
                errors.append(
                    "Depth: completion_audit.all_green missing — "
                    "was the audit actually run?"
                )
            elif not ca["all_green"]:
                fixable = ca.get("gaps", 0) - ca.get("unfixable_gaps", 0)
                if fixable > 0:
                    errors.append(
                        f"Depth: completion_audit has {fixable} fixable gap(s) — "
                        f"fix them or mark as unfixable_gaps before delivery"
                    )
            # Rule 16: unfixable_gaps must have justification
            unfixable = ca.get("unfixable_gaps", 0)
            if unfixable > 0 and not ca.get("unfixable_justification"):
                errors.append(
                    f"Depth: completion_audit has {unfixable} unfixable_gaps "
                    f"but no 'unfixable_justification' — explain why each gap "
                    f"cannot be fixed (Rule 17: no premature completion)"
                )

        # confidence_score: must be dict from script, not hand-written number
        cs = artifact_data.get("confidence_score")
        if isinstance(cs, (int, float)):
            errors.append(
                "Depth: confidence_score is a bare number — "
                "must run confidence_score.py which returns "
                "{score, breakdown, penalties}. Hand-written scores are not accepted."
            )
        elif isinstance(cs, dict):
            if "breakdown" not in cs or "penalties" not in cs:
                errors.append(
                    "Depth: confidence_score missing 'breakdown' or 'penalties' — "
                    "run confidence_score.py to generate a real score"
                )

        # --- Rule 18: Adversarial findings must be specific ---
        # Reuses `ar` from this deliver block (F1 fix: no duplicate fetch)
        if isinstance(ar, dict) and ar.get("profile_tier") not in ("skipped", None):
            findings = ar.get("findings", [])
            if isinstance(findings, list) and len(findings) > 0:
                vague_count = 0
                for f in findings:
                    if not isinstance(f, dict):
                        continue
                    # A specific finding should reference a file path or function.
                    # Check multiple common field names for the description text.
                    desc = str(
                        f.get("finding", "")
                        or f.get("issue", "")
                        or f.get("desc", "")
                        or f.get("description", "")
                    )
                    has_file_ref = ("." in desc and ("/" in desc or ".py" in desc or ".ts" in desc or ".js" in desc))
                    has_func_ref = ("()" in desc or "def " in desc or "function " in desc or "line" in desc.lower())
                    # Not vague if: has file reference OR function reference OR is detailed (>= 50 chars)
                    if not has_file_ref and not has_func_ref and len(desc) < 50:
                        vague_count += 1
                if vague_count > 0 and vague_count >= len(findings) * 0.5:
                    errors.append(
                        f"Depth: {vague_count}/{len(findings)} adversarial findings are vague "
                        f"(no file path, function name, or line reference). "
                        f"Rule 18: findings must include file, what's wrong, and concrete fix."
                    )

    if stage == "build":
        # tdd: must include green_pass
        tdd = artifact_data.get("tdd")
        if isinstance(tdd, dict) and "green_pass" not in tdd:
            errors.append(
                "Depth: tdd.green_pass missing — was the RED→GREEN cycle completed?"
            )

    return errors


def _check_semantic_depth(
    stage: str, artifact_data: dict, run: dict, project: str, run_id: str,
) -> list[str]:
    """Layer 2.5: semantic heuristic checks — WARN level.

    Catches artifacts that are structurally valid but content-lazy.
    Returns a list of warning strings (never errors/blocks).

    Three checks:
      1. Completion audit evidence quality (deliver) — >=70% cite file/test
      2. RP patterns evidence quality (review) — >=50% non-N/A have >10 char evidence
      3. Confidence penalty consistency (deliver) — empty penalties when issues exist
    """
    warnings: list[str] = []

    # --- Check 1: Completion audit evidence quality (deliver stage) ---
    if stage == "deliver":
        ca = artifact_data.get("completion_audit", {})
        checklist = ca.get("checklist", []) if isinstance(ca, dict) else []
        if isinstance(checklist, list) and len(checklist) >= 2:
            substantive = 0
            for entry in checklist:
                if not isinstance(entry, dict):
                    continue
                ev = str(entry.get("evidence", ""))
                has_file = any(ext in ev for ext in _EVIDENCE_FILE_EXTS)
                has_test = "test_" in ev or "test(" in ev or "::test" in ev
                has_line = "line " in ev.lower() or "line:" in ev.lower()
                if has_file or has_test or has_line:
                    substantive += 1
            ratio = substantive / len(checklist)  # PE-8: len >= 2 guaranteed
            if ratio < 0.7:
                warnings.append(
                    f"Semantic: completion_audit evidence quality — "
                    f"{substantive}/{len(checklist)} entries ({ratio:.0%}) cite a file path "
                    f"or test name. Rule 16 requires verifiable evidence, not assertions "
                    f"like 'implemented' or 'verified'. Aim for >=70%."
                )

    # --- Check 2: RP patterns evidence quality (review stage) ---
    if stage == "review":
        rp = artifact_data.get("runtime_patterns", {})
        if isinstance(rp, dict):
            patterns = rp.get("patterns", [])
            if isinstance(patterns, list):
                non_na = [
                    p for p in patterns
                    if isinstance(p, dict) and str(p.get("result", "")).upper() != "N/A"
                ]
                if len(non_na) >= 2:
                    with_evidence = sum(
                        1 for p in non_na
                        if isinstance(p.get("evidence"), str) and len(p["evidence"]) > 10
                    )
                    ratio = with_evidence / len(non_na)
                    if ratio < 0.5:
                        warnings.append(
                            f"Semantic: runtime_patterns evidence quality — "
                            f"{with_evidence}/{len(non_na)} non-N/A patterns have "
                            f"evidence text >10 chars. 'PASS' without context is not "
                            f"verifiable. Add what was checked (e.g., 'No subprocess in "
                            f"changeset' or 'Error constant matches line 85'). Aim for >=50%."
                        )

    # --- Check 3: Confidence penalty consistency (deliver stage) ---
    # Skip if confidence_score isn't a dict — _check_depth already BLOCKs on bare numbers (PE-5)
    if stage == "deliver" and isinstance(artifact_data.get("confidence_score"), dict):
        cs = artifact_data["confidence_score"]
        penalties = cs.get("penalties", [])

        # Check if review had findings
        review_findings = 0
        test_failures = 0
        for s in run.get("stages", []):
            s_name = s.get("stage", s.get("name", ""))
            s_art_id = s.get("artifact_id")
            if not s_art_id:
                continue
            if s_name == "review":
                rev_data = _load_artifact_data(project, run_id, s_art_id)
                if rev_data:
                    review_findings = rev_data.get("findings_count", 0)
                    if not isinstance(review_findings, int):
                        review_findings = 0
            elif s_name == "test":
                test_data = _load_artifact_data(project, run_id, s_art_id)
                if test_data:
                    test_failures = test_data.get("failed", 0)
                    if not isinstance(test_failures, int):
                        test_failures = 0

        has_issues = review_findings > 0 or test_failures > 0
        if has_issues and isinstance(penalties, list) and len(penalties) == 0:
            warnings.append(
                f"Semantic: confidence_score penalties empty but issues exist — "
                f"review had {review_findings} finding(s), tests had {test_failures} "
                f"failure(s). A score with zero penalties despite known issues suggests "
                f"the scoring didn't account for them. Even if all were fixed, record "
                f"the penalty + resolution."
            )

    return warnings


# ---------------------------------------------------------------------------
# Check 12: Anti-rationalization gate — skips require structured justification
# ---------------------------------------------------------------------------

def _check_skip_justification(stage_record: dict) -> list[str]:
    """Check 12: Anti-rationalization — skips require structured justification.

    When a stage is skipped, the agent must fill a 4-field justification:
      - step_skipped: what is being skipped
      - reason: why the agent thinks it's safe
      - evidence_skip_safe: concrete evidence supporting the skip
      - counter_argument_check: must be null/empty to proceed

    THE KEY RULE: if counter_argument_check is non-empty, the skip is BLOCKED.
    This prevents the agent from rationalizing past its own corrections.

    Returns list of BLOCK-level error strings.
    """
    errors: list[str] = []
    status = stage_record.get("status")
    if status != "skipped":
        return errors

    # Allow skip_reason alone (legacy/simple skips like "profile does not include")
    skip_reason = stage_record.get("skip_reason", "")
    justification = stage_record.get("skip_justification")

    # If no justification object but has skip_reason, that's the legacy path — allow
    # F7: minimum 15 chars to prevent trivial bypass (".", "ok", "skip")
    if not justification:
        if not skip_reason or not skip_reason.strip():
            errors.append(
                "BLOCK: Stage skipped without skip_reason or skip_justification. "
                "Provide at minimum a skip_reason, or a full skip_justification "
                "with step_skipped, reason, evidence_skip_safe, counter_argument_check."
            )
        elif len(skip_reason.strip()) < 15:
            errors.append(
                "BLOCK: skip_reason too short (minimum 15 characters). "
                "Provide meaningful justification for skipping this stage."
            )
        return errors

    # Full justification provided — validate structure
    if not isinstance(justification, dict):
        errors.append("BLOCK: skip_justification must be a dict with 4 fields")
        return errors

    required_fields = ["step_skipped", "reason", "evidence_skip_safe"]
    for field in required_fields:
        val = justification.get(field)
        if not val or (isinstance(val, str) and not val.strip()):
            errors.append(f"BLOCK: skip_justification.{field} is empty or missing")

    # THE KEY RULE: if counter-argument exists, skip is blocked
    # F1 fix: coerce to string to prevent bypass via list/dict/bool types
    counter = justification.get("counter_argument_check")
    if counter is not None:
        if not isinstance(counter, str):
            # Non-string truthy value (list, dict, int, True) = bypass attempt
            errors.append(
                "BLOCK: skip_justification.counter_argument_check must be a string "
                f"or null, got {type(counter).__name__}. Non-string types are rejected."
            )
        elif counter.strip():
            errors.append(
                f"BLOCK: Anti-rationalization triggered — counter-argument exists: "
                f"'{counter[:120]}'. Cannot skip when a valid counter-argument is present. "
                f"Either address the counter-argument or execute the stage."
            )

    return errors


# ---------------------------------------------------------------------------
# Check 13: Output routing — stages must consume declared inputs
# ---------------------------------------------------------------------------

def _check_output_routing(
    stage: str, stage_record: dict, run: dict, project: str
) -> tuple[list[str], list[str]]:
    """Check 13: Explicit output routing enforcement.

    Verifies that a stage:
      - Consumed all declared upstream artifacts (BLOCK if not)
      - Produced the declared output artifact type (WARN — Check 2 handles existence)

    The consumed_artifacts field in stage records tracks what was actually read.
    Format: [{"type": "evaluation", "id": "art_xxx"}, ...]

    Returns (errors, warnings) tuple.
    """
    errors: list[str] = []
    warnings: list[str] = []

    routing = STAGE_ROUTING.get(stage)
    if not routing:
        return errors, warnings

    # Skip routing check for skipped stages
    if stage_record.get("status") == "skipped":
        return errors, warnings

    # --- Consume check ---
    consumed = stage_record.get("consumed_artifacts")
    has_consumed_field = consumed is not None
    if not isinstance(consumed, list):
        consumed = []

    consumed_types = {
        c.get("type") for c in consumed if isinstance(c, dict) and c.get("type")
    }

    for required_type in routing.get("consumes", []):
        if required_type not in consumed_types:
            # Check if a prior stage even produced this type
            stages_list = run.get("stages", [])
            prior_produced = False
            for s in stages_list:
                if s.get("stage", s.get("name")) == stage:
                    break
                s_routing = STAGE_ROUTING.get(s.get("stage", s.get("name", "")), {})
                if required_type in s_routing.get("produces", []):
                    if s.get("status") in ("completed", "done"):
                        prior_produced = True
                        break

            if prior_produced and has_consumed_field:
                # C4 narrowed (adversarial HIGH, run_7cf9da85): the field is PRESENT
                # but omits a type a completed producer made → positive evidence the
                # agent recorded consumption yet skipped a KNOWN input. Keep the
                # BLOCK — auto-resolving here would defang Check 13 entirely (it is
                # SEVERITY_HARD; without this branch it could never block).
                errors.append(
                    f"BLOCK: Stage '{stage}' must consume '{required_type}' artifact "
                    f"but didn't reference it in consumed_artifacts. "
                    f"Run `discover --types {required_type} --full` and record consumption."
                )
            elif prior_produced:
                # C4 auto-resolve (run_7cf9da85): the consumed_artifacts field is
                # ABSENT entirely AND a prior COMPLETED stage produced this type, so
                # the artifact demonstrably exists and is consumable. Auto-resolve
                # instead of demanding hand-recording — that ceremony was pure
                # friction (the author hit it: deliver BLOCKED until review/test_report
                # ids were manually filled, though both stages had completed). This
                # is the missing-field path ONLY; a present-but-incomplete field still
                # BLOCKs above (the real protection). Record it observably.
                warnings.append(
                    f"AUTO-RESOLVED: Stage '{stage}' consumes '{required_type}' from a "
                    f"completed upstream producer (consumed_artifacts field absent)."
                )
            else:
                # Upstream didn't produce it — warn (might be skipped stage)
                warnings.append(
                    f"WARN: Stage '{stage}' should consume '{required_type}' "
                    f"but no prior stage produced it (may have been skipped)."
                )

    return errors, warnings


# ---------------------------------------------------------------------------
# Artifact freshness — content-hash staleness detection
# ---------------------------------------------------------------------------

def check_artifact_freshness(
    artifact_meta: dict, project: str
) -> dict[str, Any]:
    """Evaluate whether an artifact's conclusions are still valid.

    Checks:
      1. DDD dependency drift — checksums at creation vs current
      2. TTL advisory — age exceeds configured threshold

    Args:
        artifact_meta: Artifact metadata dict (from manifest or stage record)
                       Must include 'created_at'. Optional: 'freshness' with
                       'depends_on' and 'ttl_advisory_hours'.
        project: Project name for DDD doc lookup.

    Returns:
        {"fresh": bool, "stale_reason": str | None, "age_hours": float}
    """
    from datetime import datetime, timezone

    created_str = artifact_meta.get("created_at", "")
    if not created_str:
        return {"fresh": True, "stale_reason": None, "age_hours": 0}

    try:
        # Handle both Z suffix and +00:00 formats
        created_str_clean = created_str.replace("Z", "+00:00")
        created = datetime.fromisoformat(created_str_clean)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_hours = (now - created).total_seconds() / 3600
    except (ValueError, TypeError):
        return {"fresh": True, "stale_reason": None, "age_hours": 0}

    freshness = artifact_meta.get("freshness", {})
    if not isinstance(freshness, dict):
        freshness = {}

    depends_on = freshness.get("depends_on", {})
    if not isinstance(depends_on, dict):
        depends_on = {}

    # Check 1: DDD dependency drift
    ddd_checksums = depends_on.get("ddd_checksums", {})
    if ddd_checksums:
        ws = _get_workspace()
        project_dir = ws / "Projects" / project
        for doc_name, expected_hash in ddd_checksums.items():
            doc_path = ddd_path(project_dir, doc_name)
            if doc_path.exists():
                try:
                    content = doc_path.read_text()
                    current_hash = _compute_doc_checksum(content)
                    if current_hash != expected_hash:
                        return {
                            "fresh": False,
                            "stale_reason": (
                                f"DDD doc {doc_name} changed since artifact was created"
                            ),
                            "age_hours": age_hours,
                        }
                except OSError:
                    pass
            else:
                # F6: Document was deleted — stronger staleness signal
                return {
                    "fresh": False,
                    "stale_reason": (
                        f"DDD doc {doc_name} was deleted since artifact was created"
                    ),
                    "age_hours": age_hours,
                }

    # Check 2: TTL advisory (soft)
    ttl = freshness.get("ttl_advisory_hours", _DEFAULT_TTL_HOURS)
    if not isinstance(ttl, (int, float)):
        ttl = _DEFAULT_TTL_HOURS
    if age_hours > ttl:
        return {
            "fresh": False,
            "stale_reason": f"Age {age_hours:.0f}h exceeds advisory TTL {ttl}h",
            "age_hours": age_hours,
        }

    return {"fresh": True, "stale_reason": None, "age_hours": age_hours}


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------

def validate(project: str, run_id: str, stage: str) -> dict[str, Any]:
    """Validate a pipeline stage against structural + semantic invariants.

    Checks 1-8: structural invariants (BLOCK on violations)
    Check 9: depth validation L2 (BLOCK on hollow artifacts)
    Check 10: confidence/push-ready gate L3 (BLOCK on not-push-ready)
    Check 11: semantic depth L2.5 (WARN on content-lazy artifacts)
    Check 12: anti-rationalization gate (BLOCK on skips with counter-arguments)
    Check 13: output routing (BLOCK on missing consumed_artifacts) + freshness (WARN)

    Returns a result dict with:
        valid: bool — False if any BLOCK errors
        stage: str — the validated stage
        errors: list[str] — BLOCK-level violations
        warnings: list[str] — informational issues + semantic heuristics
        checks_passed: int
        checks_total: int
    """
    errors: list[str] = []
    warnings: list[str] = []
    check_results: list[dict] = []
    checks_total = 8
    checks_passed = 0

    # Load run state
    run = _load_run(project, run_id)
    if run is None:
        return {
            "valid": False,
            "stage": stage,
            "errors": [f"Pipeline run {run_id} not found for project {project}"],
            "warnings": [],
            "errored": [],
            "check_results": [],
            "checks_passed": 0,
            "checks_total": checks_total,
        }

    profile = run.get("profile") or "full"
    stages_list = run.get("stages", [])

    # Find the stage record for the stage being validated
    stage_record = None
    for s in stages_list:
        if s.get("stage", s.get("name")) == stage:
            stage_record = s

    if stage_record is None:
        return {
            "valid": False,
            "stage": stage,
            "errors": [f"No stage record found for '{stage}' in run {run_id}"],
            "warnings": [],
            "errored": [],
            "check_results": [],
            "checks_passed": 0,
            "checks_total": checks_total,
        }

    # Pre-load artifact data for depth/gate checks (L2/L3)
    _art_id = stage_record.get("artifact_id")
    artifact_data: dict[str, Any] | None = None
    if _art_id and stage not in NO_ARTIFACT_STAGES:
        artifact_data = _load_artifact_data(project, run_id, _art_id)
        # For deliver stage: artifact MUST be loadable for adversarial review validation.
        # A deliver artifact_id without loadable data means publish happened but manifest
        # is missing/corrupt — this is a BLOCK because we can't verify quality gates.
        if artifact_data is None and stage == "deliver":
            errors.append(
                f"Artifact {_art_id} for 'deliver' could not be loaded — "
                f"file missing or corrupt"
            )

    # --- Check 1: Stage order ---
    with _CheckGuard("stage_order", SEVERITY_HARD, errors, warnings, check_results) as _g:
        if _check_stage_order(stage, profile, stages_list):
            checks_passed += 1
            _g.passed()
        else:
            _g.failed()
            profile_stages = get_profile_stages(profile)
            expected_idx = profile_stages.index(stage) if stage in profile_stages else -1
            if expected_idx > 0:
                expected_prev = profile_stages[expected_idx - 1]
                errors.append(
                    f"Stage order violation: '{stage}' requires '{expected_prev}' "
                    f"to be completed first (profile: {profile})"
                )
            else:
                errors.append(f"Stage order violation: '{stage}' position invalid in profile '{profile}'")

    # --- Check 2: Artifact exists ---
    with _CheckGuard("artifact_exists", SEVERITY_HARD, errors, warnings, check_results) as _g:
        if _check_artifact_exists(stage, stage_record):
            checks_passed += 1
            _g.passed()
        else:
            if stage in NO_ARTIFACT_STAGES:
                checks_passed += 1  # Never required
                _g.passed()
            elif stage in ARTIFACT_OPTIONAL_FOR_PROFILES.get(profile or "", set()):
                # Optional for this profile — warn, don't block
                warnings.append(
                    f"No artifact for '{stage}' (optional in {profile} profile)"
                )
                checks_passed += 1
                _g.passed()
            else:
                _g.failed()
                errors.append(
                    f"No artifact published for '{stage}' — "
                    f"artifact_id is missing or empty in stage record"
                )

    # --- Check 3: Artifact schema ---
    with _CheckGuard("artifact_schema", SEVERITY_HARD, errors, warnings, check_results) as _g:
        schema_result = _check_artifact_schema(stage, stage_record, project, run_id)
        if schema_result["passed"]:
            checks_passed += 1
            _g.passed()
        else:
            _g.failed()
        errors.extend(schema_result.get("errors", []))
        warnings.extend(schema_result.get("warnings", []))

    # --- Check 4: Decision logged (WARN only — doesn't block) ---
    with _CheckGuard("decision_logged", SEVERITY_ADVISORY, errors, warnings, check_results) as _g:
        if _check_decision_logged(stage, stage_record):
            checks_passed += 1
            _g.passed()
        else:
            checks_passed += 1  # Warnings don't reduce checks_passed
            _g.passed()
            if stage not in DECISION_OPTIONAL_STAGES:
                warnings.append(
                    f"No decisions classified for '{stage}' — "
                    f"classify at least one decision (mechanical/taste/judgment)"
                )
    if _g.errored_nonblocking:
        checks_passed += 1

    # --- Check 5: Budget recorded (WARN only — doesn't block) ---
    with _CheckGuard("budget_recorded", SEVERITY_ADVISORY, errors, warnings, check_results) as _g:
        if _check_budget_recorded(stage_record):
            checks_passed += 1
            _g.passed()
        else:
            checks_passed += 1  # Warnings don't reduce checks_passed
            _g.passed()
            warnings.append(
                f"token_cost is 0 for '{stage}' — "
                f"estimate the token cost for budget calibration"
            )
    if _g.errored_nonblocking:
        checks_passed += 1

    # --- Check 6: Profile respected ---
    with _CheckGuard("profile_respected", SEVERITY_HARD, errors, warnings, check_results) as _g:
        if _check_profile_respected(stage, profile):
            checks_passed += 1
            _g.passed()
        else:
            _g.failed()
            errors.append(
                f"Profile violation: '{stage}' is not in the '{profile}' profile. "
                f"Expected stages: {get_profile_stages(profile)}"
            )

    # --- Check 7: DDD cross-document consistency (WARN only) ---
    # Runs on evaluate stage — that's when DDD docs are first consulted.
    # On other stages, auto-pass (DDD was already validated at evaluate).
    with _CheckGuard("ddd_consistency", SEVERITY_ADVISORY, errors, warnings, check_results) as _g:
        if stage == "evaluate":
            # Build context text from the evaluation artifact for cross-check
            # Use pre-loaded artifact_data (don't re-load — F1/F2 fix)
            context_text = None
            if artifact_data:
                parts = [
                    str(artifact_data.get("recommendation", "")),
                    str(artifact_data.get("scope", "")),
                    str(artifact_data.get("summary", "")),
                    str(artifact_data.get("approach", "")),
                ]
                context_text = " ".join(parts)

            ddd_result = check_ddd_consistency(project, context_text)
            warnings.extend(ddd_result["warnings"])

            # Staleness check: warn if DDD docs changed since last completed run
            staleness = check_ddd_staleness(project)
            if staleness["stale_runs"]:
                latest_stale = staleness["stale_runs"][-1]  # most recent
                changed_docs = ", ".join(latest_stale["stale_docs"])
                warnings.append(
                    f"DDD staleness: {changed_docs} changed since last pipeline run "
                    f"({latest_stale['run_id']}). Prior evaluations may need review."
                )

            checks_passed += 1  # WARN only — never blocks
            _g.passed()
        else:
            checks_passed += 1  # Auto-pass for non-evaluate stages
            _g.passed()
    if _g.errored_nonblocking:
        # Advisory check crashed but did not block — credit it so
        # checks_passed == checks_total holds on a valid run (Correctness LOW-8).
        checks_passed += 1

    # --- Check 8: Quality gate (stage-specific: build smoke/ac_coverage,
    #     review integration/ux/findings/litmus, test layers) — HARD ---
    # Wrapped so a crash in any stage-branch becomes ERRORED+blocks (was the
    # largest unwrapped region; run_61413085 closes the asymmetry).
    _qg_errors_before = len(errors)
    with _CheckGuard("quality_gate", SEVERITY_HARD, errors, warnings, check_results) as _g8:
        # Outcome recorded at the END of the block (below) by comparing errors[]
        # length — so check_results reflects content FAILED vs PASSED accurately,
        # not a blanket PASSED. A crash still overrides to ERRORED via __exit__.
        if stage == "build":
            smoke_ok = True
            # Use pre-loaded artifact_data (F1/F2 fix — don't re-load)
            if artifact_data:
                tdd = artifact_data.get("tdd", {})
                files_changed = artifact_data.get("files_changed", [])
                code_files = [f for f in files_changed
                              if any(f.endswith(ext) for ext in _CODE_EXTS)]
                smoke_count = tdd.get("smoke_tests", 0) if isinstance(tdd, dict) else 0
                if len(code_files) > 1 and smoke_count == 0:
                    smoke_ok = False
                    errors.append(
                        f"SMOKE step skipped: build touched {len(code_files)} code files "
                        f"but smoke_tests=0 — runtime crashes (AttributeError, NameError) "
                        f"may be hidden by mocks. Run smoke tests with real objects "
                        f"before advancing to REVIEW."
                    )
            if smoke_ok:
                checks_passed += 1

            # Check 8f: AC Coverage Matrix — every PLAN AC must have impl+test in BUILD
            checks_total += 1
            ac_ok = True
            if artifact_data:
                ac_coverage = artifact_data.get("ac_coverage", [])
                files_changed = artifact_data.get("files_changed", [])

                # Structural: ac_coverage must be a non-empty list
                if not isinstance(ac_coverage, list) or len(ac_coverage) == 0:
                    ac_ok = False
                    errors.append(
                        "BUILD ac_coverage missing or empty: must map every PLAN acceptance "
                        "criterion to its implementation file and test file. Publish ac_coverage "
                        "as [{ac, impl, test, verified}] before advancing to REVIEW."
                    )
                else:
                    # Validate each entry has required fields
                    for i, entry in enumerate(ac_coverage):
                        if not isinstance(entry, dict):
                            ac_ok = False
                            errors.append(f"ac_coverage[{i}] must be a dict, got {type(entry).__name__}")
                            continue
                        ac_name = entry.get("ac", "")
                        impl = entry.get("impl", "")
                        test = entry.get("test", "")
                        verified = entry.get("verified")

                        if not ac_name:
                            ac_ok = False
                            errors.append(f"ac_coverage[{i}] missing 'ac' field (criterion text)")
                        if not impl:
                            ac_ok = False
                            errors.append(
                                f"ac_coverage[{i}] ('{ac_name[:50]}') missing 'impl' — "
                                f"must reference implementation file::function"
                            )
                        if not test:
                            ac_ok = False
                            errors.append(
                                f"ac_coverage[{i}] ('{ac_name[:50]}') missing 'test' — "
                                f"must reference test file::test_function"
                            )
                        if verified is not True:
                            ac_ok = False
                            errors.append(
                                f"ac_coverage[{i}] ('{ac_name[:50]}') not verified — "
                                f"'verified' must be boolean true (test must pass before publish)"
                            )

                        # Cross-check impl file against files_changed (anti-fabrication)
                        if impl and files_changed:
                            impl_file = impl.split("::")[0].strip()
                            if impl_file and not any(
                                impl_file in fc or fc.endswith(impl_file)
                                for fc in files_changed
                            ):
                                warnings.append(
                                    f"ac_coverage[{i}] impl '{impl_file}' not found in "
                                    f"files_changed — verify the reference is correct."
                                )

                    # Cross-reference: load PLAN artifact ACs and check coverage
                    plan_stage = next(
                        (s for s in stages_list if s.get("stage", s.get("name")) == "plan"),
                        None,
                    )
                    if plan_stage and plan_stage.get("artifact_id"):
                        plan_data = _load_artifact_data(project, run_id, plan_stage["artifact_id"])
                        if plan_data:
                            plan_acs = plan_data.get("acceptance_criteria", [])
                            if plan_acs:
                                # Build lookup: support plan_ac_ref (explicit), AC ID
                                # (extracted prefix if unique), and text matching (substring fallback)
                                covered_refs = {
                                    entry.get("plan_ac_ref", "").strip()
                                    for entry in ac_coverage if entry.get("plan_ac_ref")
                                }
                                # Extract AC IDs from BUILD ac_coverage "ac" field text
                                covered_ac_ids = set()
                                for entry in ac_coverage:
                                    entry_ac = entry.get("ac", "").strip()
                                    m = re.match(r"^(AC\d+)", entry_ac)
                                    if m:
                                        covered_ac_ids.add(m.group(1))
                                covered_texts = {entry.get("ac", "").strip() for entry in ac_coverage}

                                # Detect duplicate AC IDs in PLAN (AC ID match unsafe if dupes)
                                plan_id_counts: dict[str, int] = {}
                                for p in plan_acs:
                                    p_str = p.strip() if isinstance(p, str) else str(p).strip()
                                    m = re.match(r"^(AC\d+)", p_str)
                                    if m:
                                        plan_id_counts[m.group(1)] = plan_id_counts.get(m.group(1), 0) + 1
                                # AC IDs that appear exactly once in PLAN (safe for ID-based match)
                                unique_plan_ids = {k for k, v in plan_id_counts.items() if v == 1}

                                for i, pac in enumerate(plan_acs):
                                    pac_str = pac.strip() if isinstance(pac, str) else str(pac).strip()

                                    # Strategy 1: identifier match (AC1, AC2, etc.)
                                    # Extract "ACN:" prefix from plan AC
                                    ac_id_match = re.match(r"^(AC\d+)", pac_str)
                                    ac_id = ac_id_match.group(1) if ac_id_match else None

                                    matched = False
                                    # Check by plan_ac_ref first (preferred, unambiguous)
                                    if ac_id and ac_id in covered_refs:
                                        matched = True
                                    # Check by extracted AC ID — only if ID is unique in PLAN
                                    # (duplicate IDs like "AC1: X" and "AC1: Y" require text match)
                                    if not matched and ac_id and ac_id in unique_plan_ids and ac_id in covered_ac_ids:
                                        matched = True
                                    # Fallback: text matching (exact, or bidirectional substring)
                                    if not matched:
                                        matched = any(
                                            pac_str == cac or pac_str in cac or cac in pac_str
                                            for cac in covered_texts
                                        )

                                    if not matched:
                                        ac_ok = False
                                        errors.append(
                                            f"AC not covered in BUILD: '{pac_str[:80]}' — "
                                            f"appears in PLAN but has no ac_coverage entry. "
                                            f"Every PLAN AC must be implemented and tested. "
                                            f"Tip: add 'plan_ac_ref': '{ac_id or f'AC{i+1}'}' "
                                            f"to the coverage entry for unambiguous matching."
                                        )
                                # F7: Reverse check — warn on scope creep
                                # Pre-extract plan AC identifiers for efficient lookup
                                plan_ac_ids = set()
                                plan_ac_texts = set()
                                for p in plan_acs:
                                    p_str = p.strip() if isinstance(p, str) else str(p).strip()
                                    plan_ac_texts.add(p_str)
                                    m = re.match(r"^(AC\d+)", p_str)
                                    if m:
                                        plan_ac_ids.add(m.group(1))

                                for entry in ac_coverage:
                                    entry_ac = entry.get("ac", "").strip()
                                    entry_ref = entry.get("plan_ac_ref", "").strip()
                                    reverse_matched = False
                                    # Match by plan_ac_ref identifier
                                    if entry_ref and entry_ref in plan_ac_ids:
                                        reverse_matched = True
                                    # Match by extracted AC ID from BUILD entry text
                                    if not reverse_matched:
                                        entry_id_match = re.match(r"^(AC\d+)", entry_ac)
                                        if entry_id_match and entry_id_match.group(1) in plan_ac_ids:
                                            reverse_matched = True
                                    # Fallback: bidirectional text matching
                                    if not reverse_matched:
                                        reverse_matched = any(
                                            p in entry_ac or entry_ac in p
                                            for p in plan_ac_texts
                                        )
                                    if not reverse_matched and entry_ac:
                                        warnings.append(
                                            f"Scope creep: ac_coverage entry '{entry_ac[:60]}' "
                                            f"has no matching PLAN AC. BUILD may have added "
                                            f"scope beyond what was planned."
                                        )
                        else:
                            # Plan artifact exists but couldn't be loaded
                            warnings.append(
                                "Could not load PLAN artifact for AC cross-reference — "
                                "structural checks passed but completeness not verified."
                            )
            else:
                # artifact_data is None but artifact_id exists — corrupt/missing file
                if _art_id:
                    ac_ok = False
                    errors.append(
                        "BUILD artifact data could not be loaded — ac_coverage check failed. "
                        "Verify the artifact file exists and contains valid JSON."
                    )
            if ac_ok:
                checks_passed += 1
        elif stage == "review":
            # Check 8b: Integration trace must be present in review artifact
            # Use pre-loaded artifact_data (F1/F2 fix)
            trace_ok = True
            if artifact_data:
                trace = artifact_data.get("integration_trace", {})
                checked = trace.get("checked", 0) if isinstance(trace, dict) else 0
                if checked == 0:
                    trace_ok = False
                    errors.append(
                        "Integration trace missing: review must include "
                        "'integration_trace' with checked > 0. Verify every new "
                        "public symbol has a production caller, and every removed "
                        "call site doesn't orphan old code."
                    )
            if trace_ok:
                checks_passed += 1

            # Check 8c: UX review when frontend files are in the changeset (WARN only)
            _FRONTEND_EXTS = (".tsx", ".jsx", ".css", ".html", ".svelte", ".vue")
            has_frontend = False
            build_data = None  # PE-1 fix: initialize before conditional assignment
            # Look for frontend files in the build stage's changeset artifact
            build_stage = next(
                (s for s in stages_list if s.get("stage", s.get("name")) == "build"),
                None,
            )
            if build_stage and build_stage.get("artifact_id"):
                build_data = _load_artifact_data(project, run_id, build_stage["artifact_id"])
                if build_data:
                    has_frontend = any(
                        any(f.endswith(ext) for ext in _FRONTEND_EXTS)
                        for f in build_data.get("files_changed", [])
                    )
            if has_frontend and artifact_data:
                ux = artifact_data.get("ux_review", {})
                triggered = ux.get("triggered", False) if isinstance(ux, dict) else False
                if not triggered:
                    warnings.append(
                        "UX review not triggered: changeset includes frontend files "
                        "but review artifact has no 'ux_review' section. Run the 5-point "
                        "UX checklist (discoverability, feedback, behavioral contracts, "
                        "escape/click-outside, scroll tracking)."
                    )
            # Check 8d: Review completeness — large changesets with zero findings are suspicious
            # Reuse build_data from 8c (F2 fix — don't re-load)
            if build_data:
                    tdd = build_data.get("tdd", {})
                    tests_gen = tdd.get("tests_generated", 0) if isinstance(tdd, dict) else 0
                    code_files = [
                        f for f in build_data.get("files_changed", [])
                        if any(f.endswith(ext) for ext in _CODE_EXTS)
                    ]
                    is_large_changeset = len(code_files) > 3 or tests_gen > 10

                    if is_large_changeset and artifact_data:
                            findings_count = artifact_data.get("findings_count", -1)
                            if findings_count == -1:
                                # No findings_count field at all — review artifact is incomplete
                                errors.append(
                                    f"Review completeness: build touched {len(code_files)} code files "
                                    f"with {tests_gen} tests, but review artifact has no "
                                    f"'findings_count' field. A real review must report findings "
                                    f"(even if 0) with justification."
                                )
                            elif findings_count == 0:
                                # 0 findings on a large changeset — suspicious but not blocking
                                warnings.append(
                                    f"Review reported 0 findings on {len(code_files)} code files / "
                                    f"{tests_gen} tests. Verify this is genuine — large changesets "
                                    f"with zero findings often indicate a skipped review."
                                )
                    elif is_large_changeset and not _art_id:  # PE-2 fix: was `artifact_id` (NameError)
                        errors.append(
                            f"Review completeness: build touched {len(code_files)} code files "
                            f"but REVIEW stage has no artifact_id. The review was skipped entirely."
                        )

            # Check 8e: Litmus gate must be present, structurally valid, and semantically consistent
            checks_total += 1
            litmus_ok = True
            if artifact_data:
                litmus = artifact_data.get("litmus_gate", {})
                if not isinstance(litmus, dict):
                    litmus_ok = False
                    errors.append(
                        "Litmus gate missing: review artifact must include 'litmus_gate' with "
                        "verdict (PASS/BORDERLINE/FAIL), hf_checked (4 booleans), and evidence."
                    )
                else:
                    verdict = litmus.get("verdict", "")
                    hf_checked = litmus.get("hf_checked", [])
                    evidence = litmus.get("evidence", "")
                    weak_areas = litmus.get("weak_areas", [])

                    # --- Structural checks ---
                    if verdict not in ("PASS", "BORDERLINE", "FAIL"):
                        litmus_ok = False
                        errors.append(
                            f"Litmus gate invalid verdict: '{verdict}'. Must be PASS, BORDERLINE, or FAIL."
                        )
                    if not isinstance(hf_checked, list) or len(hf_checked) != 4:
                        litmus_ok = False
                        errors.append(
                            f"Litmus gate hf_checked must be a list of exactly 4 booleans "
                            f"(HF1-HF4). Got: {hf_checked!r}"
                        )
                    elif not all(isinstance(x, bool) for x in hf_checked):
                        litmus_ok = False
                        errors.append(
                            f"Litmus gate hf_checked elements must all be booleans. "
                            f"Got: {hf_checked!r}"
                        )
                    if not evidence or len(str(evidence)) < 20:
                        litmus_ok = False
                        errors.append(
                            "Litmus gate evidence too short: must provide per-criterion "
                            "reasoning (>20 chars). Empty verdicts are not auditable."
                        )

                    # --- Semantic consistency checks ---
                    # FAIL verdict must not coexist with approved=true
                    if verdict == "FAIL" and artifact_data.get("approved", False):
                        litmus_ok = False
                        errors.append(
                            "Litmus FAIL verdict contradicts approved=true. Cannot approve "
                            "review when litmus gate failed — must return to BUILD for rework."
                        )
                    # FAIL verdict must have at least one hf_checked=False
                    if verdict == "FAIL" and isinstance(hf_checked, list) and len(hf_checked) == 4:
                        if all(hf_checked):
                            litmus_ok = False
                            errors.append(
                                "Litmus FAIL verdict but all hf_checked are True — contradiction. "
                                "A FAIL must identify which hard-failure criterion triggered it."
                            )
                    # BORDERLINE verdict must have non-empty weak_areas
                    if verdict == "BORDERLINE":
                        if not isinstance(weak_areas, list) or len(weak_areas) == 0:
                            litmus_ok = False
                            errors.append(
                                "Litmus BORDERLINE verdict requires non-empty 'weak_areas' list "
                                "for adversarial focus injection. Cannot declare BORDERLINE without "
                                "identifying which soft signals triggered it."
                            )
                    # Evidence must reference at least 2 of HF1-HF4 by name (anti-generic)
                    if evidence and verdict in ("PASS", "BORDERLINE", "FAIL"):
                        hf_refs = sum(1 for tag in ("HF1", "HF2", "HF3", "HF4") if tag in str(evidence))
                        if hf_refs < 2:
                            litmus_ok = False
                            errors.append(
                                f"Litmus evidence must reference at least 2 of HF1/HF2/HF3/HF4 "
                                f"by name to demonstrate per-criterion analysis. Found {hf_refs} "
                                f"references. Generic evidence is not auditable."
                            )
            if litmus_ok:
                checks_passed += 1
        elif stage == "test":
            # Check 8g: TEST layers must have ac_driven.run=true for non-trivial profiles
            checks_total += 1
            test_layers_ok = True
            _skip_profiles = ("trivial", "research", "docs")
            _block_profiles = ("full", "bugfix")  # F11: BLOCK not WARN for quality profiles
            if artifact_data and profile not in _skip_profiles:
                layers = artifact_data.get("layers", {})
                if not isinstance(layers, dict) or not layers:
                    test_layers_ok = False
                    msg = ("TEST artifact missing 'layers' — 3-layer test strategy "
                           "(ac_driven, dependency_scoped, import_smoke) not evidenced. "
                           "Run AC-driven tests from BUILD ac_coverage before advancing.")
                    if profile in _block_profiles:
                        errors.append(msg)
                    else:
                        warnings.append(msg)
                else:
                    ac_driven = layers.get("ac_driven", {})
                    if not isinstance(ac_driven, dict) or not ac_driven.get("run"):
                        test_layers_ok = False
                        msg = ("TEST layers.ac_driven.run is not true — AC-driven verification "
                               "did not execute. BUILD's ac_coverage claims are unverified.")
                        if profile in _block_profiles:
                            errors.append(msg)
                        else:
                            warnings.append(msg)

                    # F5: Cross-verify ac_driven.pass count against BUILD ac_coverage count
                    if ac_driven.get("run"):
                        ac_pass_count = ac_driven.get("pass", 0)
                        # Load BUILD artifact to get ac_coverage count
                        build_stage = next(
                            (s for s in stages_list if s.get("stage", s.get("name")) == "build"),
                            None,
                        )
                        if build_stage and build_stage.get("artifact_id"):
                            build_data = _load_artifact_data(project, run_id, build_stage["artifact_id"])
                            if build_data:
                                ac_count = len(build_data.get("ac_coverage", []))
                                if ac_count > 0 and ac_pass_count < ac_count:
                                    test_layers_ok = False
                                    errors.append(
                                        f"TEST ac_driven.pass ({ac_pass_count}) < BUILD ac_coverage "
                                        f"count ({ac_count}). Not all AC tests passed — "
                                        f"BUILD's coverage claims are not fully verified."
                                    )
            if test_layers_ok:
                checks_passed += 1
        else:
            checks_passed += 1  # Auto-pass for other stages
        # Record content outcome: FAILED if this gate added any blocking error,
        # else PASSED. (A crash inside still overrides to ERRORED via __exit__.)
        if len(errors) > _qg_errors_before:
            _g8.failed()
        else:
            _g8.passed()

    # --- Check 9: Depth validation (L2) — field values indicate real work ---
    # Only runs when artifact data is available (L0/L1 catch missing data)
    with _CheckGuard("depth", SEVERITY_HARD, errors, warnings, check_results) as _g:
        if artifact_data and stage in ("review", "deliver", "build", "test"):
            depth_errors = _check_depth(stage, artifact_data, profile, run_id=run_id)
            errors.extend(depth_errors)
            if not depth_errors:
                checks_passed += 1
                _g.passed()
            else:
                _g.failed()
            checks_total += 1
            # --- L4 disk-check WARN surfacing (Run B) — inside the depth guard
            # so a crash here is crash-isolated like every other check. The
            # BLOCK-level disk errors are already emitted by _check_depth via the
            # shared _verify_findings_on_disk helper (R27). Here — where the
            # `warnings` list lives — we surface the advisory (non-blocking) signals:
            #   (1) resolved HIGH/CRITICAL findings with NO disk_check locus
            #       (scoped to HIGH/CRIT to avoid a LOW-severity WARN-storm — Gate-1)
            #   (2) invalid/unverifiable loci (relative/missing/binary) which fail
            #       OPEN (WARN, never BLOCK — never false-block CI/other-machine runs)
            if stage == "deliver":
                _ar = artifact_data.get("adversarial_review")
                if isinstance(_ar, dict):
                    _findings = _ar.get("findings", [])
                    _, _disk_invalid = _verify_findings_on_disk(_findings)
                    _hi_no_dc = [
                        f for f in _findings
                        if isinstance(f, dict) and f.get("resolved")
                        and str(f.get("severity", "")).strip().upper() in ("HIGH", "CRITICAL")
                        and not isinstance(f.get("disk_check"), dict)
                    ]
                    if _hi_no_dc:
                        warnings.append(
                            f"L4: {len(_hi_no_dc)} resolved HIGH/CRITICAL finding(s) carry "
                            f"no disk_check locus — cannot confirm the fix is on disk. Add "
                            f"disk_check:{{file(abs),must_contain|must_not_contain}} to "
                            f"enable verify-against-disk (INSTRUCTIONS.md:581)."
                        )
                    for _inv in _disk_invalid:
                        warnings.append(f"L4 disk-check WARN: {_inv}")

    # --- Check 9b: Gate 2 Agent Tool Audit (marker file verification) ---
    # Written by SubagentStop hook when Agent tool completes during a pipeline run.
    # Primary: <run_id>.marker (exact match). Fallback: session_*_<ts>.marker
    # with timestamp within the run's execution window.
    # If no marker found and profile requires adversarial → WARN (future: BLOCK).
    # Advisory: emits WARN only (never errors.append) → a crash must not block.
    with _CheckGuard("agent_tool_audit", SEVERITY_ADVISORY, errors, warnings, check_results) as _g:
        _g.passed()
        if stage == "deliver" and profile in ("full", "bugfix"):
            checks_total += 1
            marker_found = False
            # Primary: exact run_id marker
            marker_file = AGENT_AUDIT_DIR / f"{run_id}.marker"
            if marker_file.exists():
                marker_found = True
            elif AGENT_AUDIT_DIR.exists():
                # Fallback: any session marker written after run started
                run_created = run.get("created_at", "")
                run_start_ts = 0.0
                if run_created:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(run_created.replace("Z", "+00:00"))
                        run_start_ts = dt.timestamp()
                    except (ValueError, TypeError):
                        pass
                for f in AGENT_AUDIT_DIR.iterdir():
                    if f.name.startswith("session_") and f.suffix == ".marker":
                        try:
                            data = json.loads(f.read_text(encoding="utf-8"))
                            if data.get("ts", 0) > run_start_ts:
                                marker_found = True
                                break
                        except (json.JSONDecodeError, OSError):
                            continue
            if marker_found:
                checks_passed += 1
            else:
                warnings.append(
                    "Agent tool audit: no SubagentStop marker file found for this run. "
                    "This suggests the Agent tool was never invoked for adversarial review. "
                    "Ensure the runtime hook is active and the Agent tool was actually spawned."
                )

            # Opportunistic cleanup: remove markers older than 7 days
            try:
                if AGENT_AUDIT_DIR.exists():
                    import time as _time
                    cutoff = _time.time() - 7 * 86400
                    for old_f in AGENT_AUDIT_DIR.iterdir():
                        if old_f.suffix == ".marker" and old_f.stat().st_mtime < cutoff:
                            old_f.unlink(missing_ok=True)
            except Exception:
                # Best-effort cleanup AFTER checks_passed was already credited.
                # Broadened from OSError so a non-OSError here cannot escape into
                # __exit__ and trigger the errored_nonblocking credit a SECOND time
                # (would over-count checks_passed). Cleanup failure is never fatal.
                pass
    if _g.errored_nonblocking and stage == "deliver" and profile in ("full", "bugfix"):
        # 9b crashed after its checks_total += 1 but before crediting checks_passed.
        # Credit it so checks_passed == checks_total holds on a valid run (advisory
        # crash does not block). Guarded by the same condition that ran checks_total += 1.
        checks_passed += 1

    # --- Check 10: Push-Ready gate (L3) — binary verdict ---
    # V2: reads `quality.push_ready` (boolean) or infers from quality fields.
    # V1 compat: reads `confidence_score.score < 7`.
    with _CheckGuard("push_ready", SEVERITY_HARD, errors, warnings, check_results) as _g:
        _g.passed()  # default; demoted to failed below if the gate error fires
        if stage == "deliver" and artifact_data:
            quality = artifact_data.get("quality", {})
            conf = artifact_data.get("confidence_score", {})
            has_override = run.get("human_override", False)

            # F12: human_override must have audit trail
            if has_override:
                override_reason = run.get("override_reason", "")
                if not override_reason or len(str(override_reason)) < 20:
                    warnings.append(
                        "human_override is set but 'override_reason' is missing or too short "
                        "(< 20 chars). Override should include justification for audit trail."
                    )

            if isinstance(quality, dict) and "push_ready" in quality:
                # V2 path: explicit binary gate
                if not quality["push_ready"]:
                    blockers = quality.get("blockers", [])
                    if not has_override:
                        errors.append(
                            f"Push-ready gate: NOT-PUSH-READY. "
                            f"Blockers: {blockers}"
                        )
                    else:
                        warnings.append(
                            f"Push-ready gate: NOT-PUSH-READY — "
                            f"OVERRIDDEN by human_override flag."
                        )
            elif isinstance(quality, dict) and quality.get("tests_pass"):
                # V2 inferred: quality has tests_pass=true + regressions=0 → push-ready
                # This handles artifacts that provide quality evidence without explicit push_ready
                regressions = quality.get("regressions", 0)
                if regressions > 0 and not has_override:
                    errors.append(
                        f"Push-ready gate: quality.regressions={regressions} > 0."
                    )
                # Otherwise: inferred push-ready (tests pass, no regressions)
            elif isinstance(conf, dict) and conf.get("score") is not None:
                # V1 compat: numeric confidence score
                score = conf.get("score", 0)
                if score < 7 and not has_override:
                    errors.append(
                        f"Confidence gate: score={score}/12 (< 7). "
                        f"Fix penalties or add human_override to run.json."
                    )
                elif score < 7 and has_override:
                    warnings.append(
                        f"Confidence gate: score={score}/12 (< 7) — "
                        f"OVERRIDDEN by human_override flag."
                    )
            # No quality and no confidence_score → skip gate (depth checks are sufficient)

            checks_total += 1
            if not any("push-ready gate" in e.lower() or "confidence gate" in e.lower() for e in errors):
                checks_passed += 1
            else:
                _g.failed()

    # --- Check 11: Semantic depth (L2.5) — content quality heuristics (WARN) ---
    with _CheckGuard("semantic", SEVERITY_ADVISORY, errors, warnings, check_results) as _g:
        _g.passed()
        if artifact_data and stage in ("review", "deliver"):
            sem_warnings = _check_semantic_depth(stage, artifact_data, run, project, run_id)
            warnings.extend(sem_warnings)
    # Check 11 has no checks_passed increment (pure WARN, not counted) — so on a
    # crash there is no missed credit; errored_nonblocking would over-count. Skip.

    # --- Check 12: Anti-rationalization gate (skipped stages) ---
    with _CheckGuard("anti_rationalization", SEVERITY_HARD, errors, warnings, check_results) as _g:
        if stage_record.get("status") == "skipped":
            checks_total += 1
            skip_errors = _check_skip_justification(stage_record)
            errors.extend(skip_errors)
            if not skip_errors:
                checks_passed += 1
                _g.passed()
            else:
                _g.failed()
        else:
            _g.passed()

    # --- Check 13: Output routing — consume/produce enforcement ---
    # HARD on the routing errors; the freshness sub-check inside emits WARN only.
    with _CheckGuard("output_routing", SEVERITY_HARD, errors, warnings, check_results) as _g:
        if stage in STAGE_ROUTING and stage_record.get("status") != "skipped":
            checks_total += 1
            routing_errors, routing_warnings = _check_output_routing(
                stage, stage_record, run, project
            )
            errors.extend(routing_errors)
            warnings.extend(routing_warnings)
            if not routing_errors:
                checks_passed += 1
                _g.passed()
            else:
                _g.failed()

            # Freshness sub-check: warn on stale consumed artifacts
            # F3 fix: look up full artifact metadata from manifest (consumed_artifacts
            # entries typically only have type+id, not created_at/freshness).
            consumed = stage_record.get("consumed_artifacts", [])
            if isinstance(consumed, list):
                for c in consumed:
                    if not isinstance(c, dict):
                        continue
                    art_id = c.get("id")
                    # Try to get full metadata from manifest for richer freshness check
                    art_meta = c  # fallback to inline entry
                    if art_id:
                        full_meta = _load_artifact_meta_from_manifest(project, art_id)
                        if full_meta:
                            art_meta = full_meta
                    freshness_result = check_artifact_freshness(art_meta, project)
                    if not freshness_result["fresh"]:
                        warnings.append(
                            f"STALE: Stage '{stage}' consuming stale artifact "
                            f"'{c.get('id', '?')}' (type: {c.get('type', '?')}, "
                            f"age: {freshness_result['age_hours']:.0f}h, "
                            f"reason: {freshness_result['stale_reason']}). "
                            f"Consider re-running the producing stage."
                        )
        else:
            _g.passed()

    return {
        "valid": len(errors) == 0,
        "stage": stage,
        "errors": errors,
        "warnings": warnings,
        # Additive: FAILED-vs-ERRORED distinction (run_55710438). errored[] names
        # the checks that could NOT run (crashed), distinct from content failures
        # in errors[]. check_results[] carries the per-check {name,severity,status}.
        "errored": [c["name"] for c in check_results if c["status"] == CHECK_ERRORED],
        "check_results": check_results,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
    }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_stage_order(stage: str, profile: str, stages_list: list[dict]) -> bool:
    """Check 1: Current stage follows the last completed stage per profile."""
    profile_stages = get_profile_stages(profile)

    if stage not in profile_stages:
        return False  # Not in profile at all — caught by check 6 too

    stage_idx = profile_stages.index(stage)

    if stage_idx == 0:
        return True  # First stage always valid

    # All prior stages in profile must be completed or skipped
    for i in range(stage_idx):
        prior_stage_name = profile_stages[i]
        prior_record = _find_stage_record(prior_stage_name, stages_list)
        if prior_record is None:
            return False  # Prior stage not even recorded
        if prior_record.get("status") not in ("completed", "done", "skipped"):
            return False  # Prior stage not done

    return True


def _check_artifact_exists(stage: str, stage_record: dict) -> bool:
    """Check 2: Stage published an artifact (reflect is exempt)."""
    if stage in NO_ARTIFACT_STAGES:
        return True  # No artifact required

    artifact_id = stage_record.get("artifact_id")
    return bool(artifact_id and artifact_id.strip())


def _check_artifact_schema(
    stage: str, stage_record: dict, project: str, run_id: str
) -> dict[str, Any]:
    """Check 3: Required/recommended fields present in artifact data.

    Returns {"passed": bool, "errors": [...], "warnings": [...]}.
    """
    result: dict[str, Any] = {"passed": True, "errors": [], "warnings": []}

    if stage not in STAGE_SCHEMAS:
        return result  # No schema defined (e.g., reflect)

    artifact_id = stage_record.get("artifact_id")
    if not artifact_id:
        # No artifact — this is caught by check 2, skip schema check
        return result

    schema = STAGE_SCHEMAS[stage]
    artifact_data = _load_artifact_data(project, run_id, artifact_id)

    if artifact_data is None:
        result["passed"] = False
        result["errors"].append(
            f"Artifact {artifact_id} for '{stage}' could not be loaded — "
            f"file missing or corrupt"
        )
        return result

    # Check required fields (BLOCK)
    for field in schema.get("required", []):
        if field not in artifact_data:
            result["passed"] = False
            result["errors"].append(
                f"Schema violation: '{stage}' artifact missing required field '{field}'"
            )

    # Check recommended fields (WARN)
    for field in schema.get("recommended", []):
        if field not in artifact_data:
            result["warnings"].append(
                f"Schema note: '{stage}' artifact missing recommended field '{field}'"
            )

    return result


def _check_decision_logged(stage: str, stage_record: dict) -> bool:
    """Check 4: At least 1 decision classified in the stage record."""
    if stage in DECISION_OPTIONAL_STAGES:
        return True  # reflect and deliver don't require decisions

    decisions = stage_record.get("decisions", [])
    return len(decisions) > 0


def _check_budget_recorded(stage_record: dict) -> bool:
    """Check 5: token_cost > 0 in the stage record."""
    token_cost = stage_record.get("token_cost", 0)
    return token_cost > 0


def _check_profile_respected(stage: str, profile: str) -> bool:
    """Check 6: Stage is in the selected pipeline profile."""
    profile_stages = get_profile_stages(profile)
    return stage in profile_stages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_stage_record(stage_name: str, stages_list: list[dict]) -> dict | None:
    """Find the most recent record for a given stage name."""
    for s in reversed(stages_list):
        if s.get("stage", s.get("name")) == stage_name:
            return s
    return None


def _load_artifact_meta_from_manifest(project: str, artifact_id: str) -> dict | None:
    """Load artifact metadata (not data) from manifest by ID.

    Returns the manifest entry dict which includes created_at, freshness, etc.
    Returns None if not found.
    """
    ws = _get_workspace()
    manifest_file = ws / "Projects" / project / ".artifacts" / "manifest.json"
    if not manifest_file.exists():
        return None
    try:
        manifest = json.loads(manifest_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    for entry in manifest.get("artifacts", []):
        if entry.get("id") == artifact_id:
            return entry
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline stage validator — structural enforcement"
    )
    sub = parser.add_subparsers(dest="command")

    # check command
    check_p = sub.add_parser("check", help="Validate a pipeline stage")
    check_p.add_argument("--project", required=True, help="Project name")
    check_p.add_argument("--run-id", required=True, help="Pipeline run ID")
    check_p.add_argument("--stage", required=True, help="Stage to validate")

    # summary command — validate all completed stages in a run
    summary_p = sub.add_parser("summary", help="Validate all stages in a pipeline run")
    summary_p.add_argument("--project", required=True, help="Project name")
    summary_p.add_argument("--run-id", required=True, help="Pipeline run ID")

    # ddd-check command — standalone DDD cross-document consistency check
    ddd_p = sub.add_parser("ddd-check", help="Check DDD document consistency for a project")
    ddd_p.add_argument("--project", required=True, help="Project name")
    ddd_p.add_argument("--context", default=None, help="Optional text to check against non-goals")

    # ddd-staleness command — check if pipeline runs are stale
    stale_p = sub.add_parser("ddd-staleness", help="Check which pipeline runs are stale (DDD docs changed)")
    stale_p.add_argument("--project", required=True, help="Project name")

    args = parser.parse_args()

    if args.command == "check":
        result = validate(args.project, args.run_id, args.stage)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["valid"] else 1)

    elif args.command == "summary":
        run = _load_run(args.project, args.run_id)
        if run is None:
            print(json.dumps({"error": f"Run {args.run_id} not found"}))
            sys.exit(1)

        all_results = []
        total_errors = 0
        total_warnings = 0

        for stage_rec in run.get("stages", []):
            stage_name = stage_rec.get("stage", stage_rec.get("name"))
            if stage_rec.get("status") in ("completed", "done", "running"):
                result = validate(args.project, args.run_id, stage_name)
                all_results.append(result)
                total_errors += len(result["errors"])
                total_warnings += len(result["warnings"])

        # Adversarial meta-monitoring — check review health across runs
        adversarial_health = {}
        try:
            from core.adversarial_meta import check_adversarial_health

            artifacts_dir = _get_artifacts_dir(args.project)
            if artifacts_dir:
                adversarial_health = check_adversarial_health(artifacts_dir)
                if adversarial_health.get("degradation_warning"):
                    total_warnings += 1
        except Exception:
            pass  # Non-blocking — meta-monitoring is advisory

        summary = {
            "run_id": args.run_id,
            "project": args.project,
            "valid": total_errors == 0,
            "stages_checked": len(all_results),
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "results": all_results,
            "adversarial_health": adversarial_health,
        }
        print(json.dumps(summary, indent=2))
        sys.exit(0 if total_errors == 0 else 1)

    elif args.command == "ddd-check":
        result = check_ddd_consistency(args.project, args.context)
        print(json.dumps(result, indent=2))
        sys.exit(0)  # Always exit 0 — warnings only

    elif args.command == "ddd-staleness":
        result = check_ddd_staleness(args.project)
        print(json.dumps(result, indent=2))
        # Exit 1 if stale runs found (useful for CI/scripting)
        sys.exit(1 if result["stale_runs"] else 0)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
