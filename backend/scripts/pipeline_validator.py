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
- ``validate_stage_state`` — validate() against an in-memory candidate run state
- ``validate_stage_record`` / ``STAGE_RECORD_SCHEMAS`` / ``STAGE_RECORD_TEMPLATES``
  — the stage-RECORD contract used by ``artifact_cli.py stage-complete``
- ``validate_exit_criteria`` / ``judge_exit_criteria`` / ``default_exit_criteria``
  — the G1 exit contract (structure, judging against runner evidence, defaults)
- ``push_ready_upper_bound`` — DELIVER push_ready computed from gate results
- ``evidence_freshness`` / ``evidence_regression_modes`` — is runner evidence newer
  than the run's last edits (tree_digest / changed_files digests), and which
  latest-*.json reports carry a regression-classified failure
- ``load_test_evidence`` / ``unreadable_evidence`` — every latest-*.json report,
  newest first; one that exists but cannot be read is an ``unreadable``
  placeholder (never green, never "absent")
- ``validate_exit_criteria_replan`` — a replan after a later stage completed may
  only ADD exit criteria (each with a reason), never drop or change one
- ``structure_errors``     — legacy message strings → {code, path, message, got?, expected?}
- ``STAGE_ENUMS``          — allowed literals for every enum-typed template field
"""

import argparse
import contextvars
import copy
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

# Add parent directory for core imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ddd_paths import ddd_path
from core.pipeline_profiles import get_profile_stages, normalize_profile, is_relaxed_profile
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


# ── Gate-2 Red Team trigger (HIGH half) ──
# deliver's rule: "spawn the Red Team when the source changeset > 200 lines OR any
# specialist found a HIGH". stage-start applies the size half (it lists `red-team`
# in `experts`); it cannot see findings, so the HIGH half used to be prose-only.
# This enforces it on the artifact: each finding names its producer in `specialist`
# (the field the gate2-adversarial prompt already makes every reviewer emit), and
# `adversarial_review.red_team_dispatched` records that the Red Team ran.
_RED_TEAM_TOKEN = "redteam"   # "red-team" / "Red Team" / "red_team" normalized
_SPECIALIST_SPLIT_RE = re.compile(r"\s*(?:[+,;/&]|\band\b)\s*", re.IGNORECASE)
# Grandfathering cutover. Runs CREATED before it predate the per-finding
# `specialist` field (every old HIGH is unattributed, so all would trigger): for
# them the rule is a WARNING (same text), never a block. Keyed on run.json
# `created_at` (written by run-create), NEVER on whether the new fields are
# present — omitting `specialist` must not be a way to dodge the gate.
RED_TEAM_CUTOVER = "2026-10-18T06:00:00Z"
_RED_TEAM_GRANDFATHER_NOTE = (f" [grandfathered: run created before {RED_TEAM_CUTOVER} "
                              "— warning only]")
# Producers that merge HIGHs into findings[] but are NOT Gate-2 specialists
# (normalized-token PREFIXES): meta-review, gate0/gate1 skeptic, fresh-user and
# user-value probes, convergence, doc-sync, completion audit.
_NON_SPECIALIST_PREFIXES = ("metareview", "meta", "gate0", "gate1", "skeptic",
                            "freshuser", "uservalue", "convergence", "docsync",
                            "completionaudit")
# Gate-2 specialist ids used if backend/experts/code/ cannot be listed. Unioned
# with the live registry, so an unreadable registry never shrinks the set.
_GATE2_SPECIALIST_FALLBACK = frozenset({
    "apicontract", "codequality", "concurrency", "correctness", "integration",
    "operational", "performance", "security", "speccompliance", "statemachine",
    "uxtest",
})
_EXPERTS_CODE_DIR = Path(__file__).resolve().parent.parent / "experts" / "code"
_gate2_specialist_cache: "frozenset[str] | None" = None
# An unattributed finding whose text starts with a red-team label ("Red Team: …",
# "[red-team round-1] …") is the Red Team's own.
_RED_TEAM_TEXT_RE = re.compile(r"^\W*red[\s_-]*team\b", re.IGNORECASE)
# Severity words that mean HIGH for this check. Gate-2 prompts and experts emit
# HIGH|MED|LOW; artifacts also carry CRITICAL, spec-compliance's BLOCK, P0/P1.
_HIGH_SEVERITY_WORDS = frozenset({"HIGH", "CRITICAL", "CRIT", "BLOCKER", "BLOCK",
                                  "BLOCKING", "P0", "P1"})
_SEVERITY_NUM_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(?:/\s*10)?\s*")
_FINDING_TEXT_KEYS = ("finding", "summary", "title", "issue", "description")


def _norm_producer(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _gate2_specialist_ids() -> "frozenset[str]":
    """Normalized Gate-2 specialist ids: the expert registry (backend/experts/code/
    *.md stems, i.e. the EXPERT-IDs) minus red-team, plus the shipped fallback."""
    global _gate2_specialist_cache
    if _gate2_specialist_cache is None:
        ids = set(_GATE2_SPECIALIST_FALLBACK)
        try:
            ids.update(_norm_producer(p.stem) for p in _EXPERTS_CODE_DIR.glob("*.md"))
        except OSError as e:
            print(f"[pipeline_validator] expert registry unreadable ({e}); "
                  "using the shipped specialist ids", file=sys.stderr)
        ids.discard(_RED_TEAM_TOKEN)
        ids.discard("")
        _gate2_specialist_cache = frozenset(ids)
    return _gate2_specialist_cache


def _specialist_tokens(value: object) -> list[str]:
    """Normalize a finding's `specialist` (str or list of str) into alnum-only
    lowercase tokens. A merged MULTI-SPECIALIST finding ("correctness + red-team")
    yields one token per producer. Absent/blank → [] (unattributed)."""
    parts = value if isinstance(value, list) else [value]
    tokens: list[str] = []
    for p in parts:
        if not isinstance(p, str):
            continue
        for piece in _SPECIALIST_SPLIT_RE.split(p):
            tok = re.sub(r"[^a-z0-9]", "", piece.lower())
            if tok:
                tokens.append(tok)
    return tokens


def _producer_kind(token: str) -> str:
    """Classify one normalized producer token: "redteam" (STARTS WITH redteam —
    "red-team (opus)", "Red-Team Specialist"), "other" (a known non-specialist
    producer, or a name that is no Gate-2 registry id), or "specialist" (names a
    registry id: "correctness", "Security Specialist", "gate2-api-contract")."""
    if token.startswith(_RED_TEAM_TOKEN):
        return "redteam"
    if token.startswith(_NON_SPECIALIST_PREFIXES):
        return "other"
    if any(sid in token for sid in _gate2_specialist_ids()):
        return "specialist"
    return "other"


def _finding_text(f: dict) -> str:
    return next((str(f[k]) for k in _FINDING_TEXT_KEYS if f.get(k)), "")


def _finding_kinds(f: dict) -> "set[str]":
    """Producer kinds of a finding. Unattributed (no/blank `specialist`) is
    {"specialist"} — fail-closed — unless its text starts with a red-team label."""
    tokens = _specialist_tokens(f.get("specialist"))
    if not tokens:
        return {"redteam" if _RED_TEAM_TEXT_RE.match(_finding_text(f)) else "specialist"}
    return {_producer_kind(t) for t in tokens}


def _finding_is_high(f: dict) -> bool:
    """HIGH for this check. Reads `severity`, else `sev` / `level` / `priority`.
    A label is HIGH when a word before any "(" is HIGH/CRITICAL/BLOCK(ER)/P0/P1
    ("HIGH (conf 9)", "HIGH-EQUIV", "critical"); a number is HIGH at >= 8 (a
    10-point scale, "8" or "9/10")."""
    for key in ("severity", "sev", "level", "priority"):
        v = f.get(key)
        if v is None or isinstance(v, bool) or (isinstance(v, str) and not v.strip()):
            continue
        if isinstance(v, (int, float)):
            return v >= 8
        label = str(v).upper().split("(", 1)[0]
        num = _SEVERITY_NUM_RE.fullmatch(label)
        if num:
            return float(num.group(1)) >= 8
        return bool(_HIGH_SEVERITY_WORDS & set(re.findall(r"[A-Z0-9]+", label)))
    return False


def _all_review_findings(ar: dict) -> "list[dict]":
    """Every finding dict in adversarial_review: `findings[]`,
    `specialists[].findings` and `iterations[].findings`. A finding nested under a
    specialists[] entry that has no `specialist` of its own inherits the entry's
    name (`specialist` / `expert` / `expert_id` / `name`)."""
    out: "list[dict]" = []

    def add(fs: object, parent: "str | None" = None) -> None:
        for f in fs if isinstance(fs, list) else []:
            if isinstance(f, dict):
                if parent and not _specialist_tokens(f.get("specialist")):
                    f = {**f, "specialist": parent}
                out.append(f)

    add(ar.get("findings"))
    for key in ("specialists", "iterations"):
        entries = ar.get(key)
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict):
                continue
            parent = None
            if key == "specialists":
                parent = next((e[k] for k in ("specialist", "expert", "expert_id", "name")
                               if isinstance(e.get(k), str) and e[k].strip()), None)
            add(e.get("findings"), parent)
    return out


def _red_team_dispatched(ar: dict) -> bool:
    """True when the artifact CLAIMS the Red Team ran: `red_team_dispatched` is
    true / a count >= 1, or some finding is the Red Team's own. A claim only —
    stage-complete additionally requires the red-team EXPERT-ID marker."""
    flag = ar.get("red_team_dispatched")
    if flag is True or (isinstance(flag, str) and flag.strip().lower() == "true"):
        return True
    if isinstance(flag, (int, float)) and not isinstance(flag, bool) and flag >= 1:
        return True
    return any("redteam" in _finding_kinds(f) for f in _all_review_findings(ar))


def _specialist_high_findings(findings: object) -> list[dict]:
    """HIGH findings (resolved OR not — the rule is "a specialist FOUND a HIGH",
    fixing it does not un-trigger the Red Team) produced by a Gate-2 specialist.
    Accepts a findings list or a whole adversarial_review dict (then every
    finding source counts). Unattributed counts (fail-closed); the Red Team's own
    and known non-specialist producers (meta-review, skeptic, …) do not."""
    if isinstance(findings, dict):
        findings = _all_review_findings(findings)
    if not isinstance(findings, list):
        return []
    return [f for f in findings
            if isinstance(f, dict) and _finding_is_high(f) and "specialist" in _finding_kinds(f)]


def _red_team_triggered(ar: object, profile: str) -> "list[dict]":
    """The trigger alone, independent of any claim that the Red Team ran: the
    specialist HIGHs on a strict profile ([] for relaxed profiles / no review)."""
    if not isinstance(ar, dict) or is_relaxed_profile(normalize_profile(profile)):
        return []
    return _specialist_high_findings(ar)


def _red_team_enforced(run_created_at: "str | None") -> bool:
    """True when the Red Team HIGH rule BLOCKS for a run whose run.json
    `created_at` is given. None = no run known (a publish without --run-id) → not
    enforced. A present but unparseable value is enforced (fail-closed)."""
    if run_created_at is None:
        return False
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(str(run_created_at).strip().replace("Z", "+00:00"))
        cut = datetime.fromisoformat(RED_TEAM_CUTOVER.replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cut


def _red_team_gate(ar: object, profile: str, *, run_created_at: "str | None",
                   warnings: "list[str] | None" = None, prefix: str = "") -> "list[str]":
    """The Red Team HIGH rule as a gate: the `_red_team_trigger_errors` message is
    an ERROR for runs created at/after RED_TEAM_CUTOVER, and a WARNING (same text
    plus a grandfather note, appended to `warnings`) for older runs or no run."""
    msgs = _red_team_trigger_errors(ar, profile, prefix)
    if not msgs or _red_team_enforced(run_created_at):
        return msgs
    if warnings is not None:
        warnings.extend(m + _RED_TEAM_GRANDFATHER_NOTE for m in msgs
                        if m + _RED_TEAM_GRANDFATHER_NOTE not in warnings)
    return []


def _red_team_trigger_errors(ar: object, profile: str, prefix: str = "") -> list[str]:
    """Gate-2 HIGH half of the Red Team rule, shared by the publish/stage-complete
    gate (validate_artifact_data) and the completion gate (_check_depth).

    Strict profiles only (relaxed trivial/docs/research exempt). Returns the
    message when any specialist HIGH finding exists (`_red_team_triggered`) and
    `_red_team_dispatched(ar)` is False. The message names the triggering
    findings and the remedy. Whether it blocks or warns is `_red_team_gate`'s
    call (the grandfather cutover)."""
    highs = _red_team_triggered(ar, profile)
    if not highs or _red_team_dispatched(ar):
        return []
    named = []
    for f in highs[:3]:
        text = _finding_text(f) or "?"
        who = f.get("specialist") or "unattributed"
        sev = next((f[k] for k in ("severity", "sev", "level", "priority") if f.get(k)), "?")
        named.append(f"[{str(sev).upper()} by {who}] {text[:100]}")
    more = f" (+{len(highs) - 3} more)" if len(highs) > 3 else ""
    return [
        f"{prefix}Red Team required: {len(highs)} specialist HIGH/CRITICAL finding(s) "
        f"but adversarial_review shows no Red Team run — " + "; ".join(named) + more
        + ". Gate-2 rule: Red Team runs when source > 200 lines OR any specialist "
        "found HIGH. Remedy: spawn the red-team expert (EXPERT-ID: red-team) with the "
        "merged specialist findings — prompt = evidence/prompts/red-team.md if "
        "stage-start listed it, else `spawn_prompt.py --gate gate2-adversarial` with "
        "<EXPERT_ID>=red-team and backend/experts/code/red-team.md "
        "(ref/deliver/gate2-dispatch.md); merge its findings with "
        "\"specialist\": \"red-team\" and set adversarial_review.red_team_dispatched: "
        "true. stage-complete also requires the red-team EXPERT-ID marker for this "
        "run. Every finding records its producer's EXPERT-ID in \"specialist\"; an "
        "unattributed HIGH counts as a specialist's."
    ]


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


# ---------------------------------------------------------------------------
# Structured Requirement Spec (SRS) + User Story Gate — Phase 1
# Design SSOT: Knowledge/Designs/2026-10-12-pipeline-requirement-spec-and-scope-depth-design.md
#
# ⚠️ ALL requirements checks below are WARN-ONLY. They are wired into validate()'s
# advisory warnings[] (the _check_semantic_depth / Check-11 precedent), NEVER into
# validate_artifact_data (publish-time, errors-only, caller sys.exit(1)s on any
# entry). Gate-1 of this feature's own pipeline run BLOCKed an earlier design that
# appended into validate_artifact_data — that path has no WARN channel. (§8, O1.)
# ---------------------------------------------------------------------------
REQ_SCHEMA_VERSION = 1
# User-value classification (per-FR, O2). user-story/enabler-story are the only
# work_classes the INVEST gate applies to; spike/bug/task are exempt (anti-ceremony).
REQ_WORK_CLASSES = (
    "user-story", "enabler-story", "spike", "bug", "task",
)
_INVEST_WORK_CLASSES = ("user-story", "enabler-story")
REQ_SOURCE_KINDS = ("user-stated", "ddd-derived", "assumption")
REQ_STATUSES = ("confirmed", "assumed", "needs-clarification")
# Scopes with no TEST/REVIEW stage — a user-perceivable (user-story) requirement
# under these is a hard-contradiction (O7): docs/research can't deliver one.
_NO_STORY_SCOPES = ("docs-only", "docs", "research-only", "research")


STAGE_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "evaluate": {
        "required": ["recommendation", "scope"],
        # requirements/schema_version are deliberately NOT recommended here: a strict-profile
        # SRS block is produced only on strict evaluates, but _check_schema's "missing
        # recommended field" nudge is NOT profile-gated — listing them would emit advisory
        # noise on every trivial/bugfix/docs evaluate that legitimately produces no SRS block.
        # The publish-command example (evaluate.md Artifact Publish) teaches the fields; the
        # validator lights up from their PRESENCE (validate_requirements_block), not a schema
        # nudge. (finding #1 fix — fix(a) only; cosmetic fix(b) dropped as net-negative.)
        "recommended": ["acceptance_criteria", "scores", "clarifications"],
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
    # goal_cycle (D4, run_57929039): the goal-profile inner loop (replaces
    # BUILD+REVIEW+TEST). Before D4 it had NO schema entry, so validate_artifact_data
    # returned [] and the goal run's inner quality fields (DoD, per-cycle adversarial)
    # were never validated. Required fields mirror the essentials goal_cycle.md records:
    # dod_met (did the Definition-of-Done pass) + adversarial_review (the final
    # cross-path adversarial on the total changeset — its findings[] feed the same
    # _blocked_findings gate as a fresh deliver adversarial). STAGE_DEPTH enforces the
    # adversarial_review shape (profile-aware). NOTE: goal_cycle ∈ goal profile ONLY —
    # _check_profile_respected still rejects it under full/trivial/etc (contract pinned
    # by test_completion_backstop_skips_off_profile_stage_after_upgrade).
    "goal_cycle": {
        "required": ["dod_met", "adversarial_review"],
        "recommended": ["cycles_run", "progress_path", "review_cadence"],
    },
    # reflect has no artifact — skip schema check
}

# Stage schema templates — show expected JSON shape for publish-time errors
STAGE_TEMPLATES: dict[str, dict] = {
    "evaluate": {
        "recommendation": "GO",
        "scope": "standard",
        "acceptance_criteria": ["AC1: ...", "AC2: ..."],
        "scores": {"strategic": 0, "priority": 0, "feasibility": 0},
        # Understanding gate (strict profiles): a present-tense, observation-backed,
        # refuted claim about the CURRENT state — before THINK proposes a fix.
        # Bug-class evals may instead use the observation_evidence alias.
        "understanding": {
            "work_type": "existing-feature",
            "claim": "A falsifiable statement about the CURRENT state (present-tense, NOT a plan)",
            "evidence": "An OBSERVATION: code-trace file:line / ps / log counts / repro / characterization",
            "evidence_kind": "code-trace",
            "skeptic_verdict": "SUPPORTED",
            "alternative_considered": "The simplest competing framing, and why it loses",
        },
        # Self-Socratic ambiguity scan (strict profiles): re-scan the filled
        # clarification output for residual ambiguity; every hit must be resolved.
        "ambiguity_scan": {
            "scanned_fields": ["who", "what", "why", "when", "acceptance_criteria"],
            "terms_checked": list(_AMBIGUITY_TERMS),
            "hits": [{"term": "standard", "where": "what",
                      "resolution": "self-answer: read file:line — exact meaning, not vague.",
                      "kind": "self-answer"}],
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
        "alternatives": [{"constraint": "SIMPLICITY", "what": "...", "effort": "S"}],
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
        # G1 exit contract: written BEFORE build, judged by the tool against runner
        # evidence at build/test/deliver stage-complete. REQUIRED at plan
        # stage-complete for strict profiles; never required on the legacy publish path.
        "exit_criteria": [
            {"id": "X1", "kind": "test", "ref": "backend/tests/test_x.py::test_red_team_case_1",
             "expect": "pass"},
            {"id": "X2", "kind": "test", "ref": "backend/tests/test_x.py", "expect": "pass"},
            {"id": "X3", "kind": "probe", "ref": "bash-fuzz", "expect": "no_new_findings"},
        ],
    },
    "build": {
        "files_changed": ["path/to/file.py"],
        "tdd": {"green_pass": True, "smoke_tests": 3, "red_count": 5, "green_count": 5,
                "tests_generated": 5},
        "ac_coverage": [
            {"ac": "AC1: User can login with email", "impl": "auth.py::login()",
             "test": "test_auth.py::test_login_email", "verified": True},
        ],
        "commits": ["abc1234"],
    },
    "review": {
        "approved": True,
        "litmus_gate": {
            "verdict": "PASS",
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
            "patterns": [{"pattern": "RP7 error message vs real cause", "status": "pass", "detail": ">10 chars describing what was checked"}],
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
            "spawned": True, "profile_tier": "full",
            "findings_total": 3, "findings_fixed": 3, "findings_remaining": 0,
            # `confidence` (1-10) gates MEDIUM findings at delivery: an unresolved
            # MEDIUM with confidence >= CONFIDENCE_GATE_THRESHOLD (7) BLOCKS; absent
            # confidence on an unresolved MEDIUM is fail-closed (also blocks). HIGH
            # blocks regardless. See _blocked_findings().
            # `specialist` = the producer's EXPERT-ID (carried over from its JSON
            # line; "red-team" for Red Team findings, "meta-review" etc. for
            # non-specialists). A HIGH from a Gate-2 registry specialist (or an
            # unattributed one) requires red_team_dispatched + the red-team marker,
            # for runs created from RED_TEAM_CUTOVER — see _red_team_gate().
            "findings": [{"severity": "MEDIUM", "confidence": 8, "resolved": True,
                          "specialist": "correctness",
                          "finding": "path/to/file.py function_name() line N: what is wrong. Fixed: how."}],
            # Did the Red Team run (size rule or a specialist HIGH)? Strict profiles
            # BLOCK a specialist HIGH/CRITICAL without it.
            "red_team_dispatched": False,
            # Rule 23: HOW the reviewers were spawned (required when spawned=true on a
            # strict profile — blocks a self-review declared as spawned).
            "evidence": "Agent tool: N specialist sub-agents (EXPERT-ID: correctness, "
                        "security, ...) spawned in one parallel batch",
        },
        # gaps / unfixable_gaps are the counts deliver.md's Completion Audit records and
        # the push-ready gate reads (gaps>0 blocks; unfixable_gaps>0 is a warning the
        # REPORT must surface) — listed so the template matches what writers write.
        "completion_audit": {"all_green": True, "requirements_met": 5, "requirements_total": 5,
                             "gaps": 0, "unfixable_gaps": 0},
        # The review summary's "解决了什么问题 / 怎么做的" (core.run_delivery_summary.
        # review_summary). Optional — absent renders 未记录 — but when present it must
        # have this shape (_outcome_shape_errors). The text is an INSTRUCTION, so a
        # template copied verbatim shows as visibly unfilled, never as a real result.
        "outcome": {
            "problem_before": "What was wrong before this run, in plain words",
            "result_now": "What is true now that this run is done",
            "approach": ["The key move, one line each"],
        },
        # The fields below are REQUIRED for strict profiles (full/bugfix/goal) — the
        # template must carry everything the validator demands, or every run finds
        # out by rejection (pinned by test_artifact_cli_schema
        # TestTemplatePassesItsOwnValidator).
        "ac_verification": {"status": "verified", "detail": "AC1 → test_x; AC2 → test_y"},
        "meta_review": {"verdict": "CLEAR", "risks": []},
        "convergence": {"iterations": 1, "all_pass": True, "final_status": "push-ready"},
    },
    # goal_cycle (D4): the goal-profile inner loop artifact. Mirrors STAGE_SCHEMAS
    # required (dod_met + adversarial_review dict carrying findings[]).
    "goal_cycle": {
        "dod_met": True,
        "cycles_run": 2,
        "progress_path": ".artifacts/runs/<RUN_ID>/PROGRESS.md",
        "review_cadence": "every cycle",
        "adversarial_review": {
            "spawned": True, "profile_tier": "full",
            "findings": [{"severity": "LOW", "confidence": 5, "resolved": True,
                          "finding": "path/to/file.py function_name() line N: what is wrong. Fixed: how."}],
            "evidence": "Agent tool: correctness + security specialists spawned in one batch",
        },
    },
}

# Allowed literal values for every enum-typed template field, keyed by stage then
# dotted path ("[]" marks a list element). The templates above carry ONE valid
# literal each (they must pass their own validator); this table carries the full
# choice set so `schema --stage X` / stage-start can show both.
STAGE_ENUMS: dict[str, dict[str, list]] = {
    "evaluate": {
        "recommendation": ["GO", "DEFER", "REJECT"],
        "scope": ["trivial", "standard", "complex", "bugfix"],
        "understanding.work_type": ["bugfix", "existing-feature", "greenfield",
                                    "refactor", "research", "docs"],
        "understanding.evidence_kind": ["observation", "code-trace", "repro",
                                        "premortem", "characterization"],
        "understanding.skeptic_verdict": ["SUPPORTED", "UNSUPPORTED",
                                          "ALREADY-SATISFIED", "WRONG-FRAME"],
        "ambiguity_scan.hits[].kind": ["self-answer", "escalation"],
    },
    "think": {
        "alternatives[].constraint": ["SPEED", "QUALITY", "SIMPLICITY"],
        "alternatives[].effort": ["S", "M", "L"],
    },
    "plan": {
        "exit_criteria[].kind": ["test", "probe", "mutation", "manual"],
        "exit_criteria[].expect": ["pass", "no_new_findings", "all_killed", "done"],
    },
    "review": {
        "litmus_gate.verdict": ["PASS", "BORDERLINE", "FAIL"],
        "runtime_patterns.patterns[].status": ["pass", "N/A", "fail"],
    },
    "deliver": {
        "adversarial_review.profile_tier": ["full", "lite", "skipped"],
        "adversarial_review.findings[].severity": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        "ac_verification.status": ["verified", "partial", "unverified"],
        "meta_review.verdict": ["CLEAR", "RISKS"],
    },
    "goal_cycle": {
        "adversarial_review.profile_tier": ["full", "lite", "skipped"],
        "adversarial_review.findings[].severity": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
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
    # D4: goal_cycle's final cross-path adversarial must be a DICT carrying findings[]
    # (same shape the deliver adversarial records). This enforces SHAPE + presence at
    # the goal_cycle stage (a bare `adversarial_review: true` fails — see the non-dict
    # rejection in validate_artifact_data). NOTE: the _blocked_findings gate (which
    # rejects an UNRESOLVED finding) runs at the DELIVER stage, NOT here — goal_cycle
    # carries its findings forward into the DELIVER adversarial_review.findings[], where
    # they block. So this entry gates the artifact's shape; DELIVER gates the content.
    "goal_cycle": {
        "adversarial_review": ["findings"],
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


# ---------------------------------------------------------------------------
# SRS helpers — all WARN-only, routed through validate() warnings[] (NOT the
# publish-time validate_artifact_data). Pure functions: zero gate wiring except
# validate_requirements_block, which validate() calls into warnings[].
# ---------------------------------------------------------------------------
def _safe_dict(value) -> dict:
    """Defensive coercion: return value only if it is a dict, else {}. A non-empty
    non-dict (e.g. a string) is truthy, so `value or {}` would NOT substitute and a
    later `.get` would raise AttributeError — these functions run inside a _CheckGuard
    that would then silently swallow the ENTIRE WARN layer for that artifact. Totality
    matters: malformed input must degrade to a WARN, never crash the check off."""
    return value if isinstance(value, dict) else {}


def _safe_list(value) -> list:
    """Defensive coercion: a dict/str/None where a list is expected becomes []. A bare
    string is iterable char-by-char — iterating `depends_on="FR-9"` would emit one bogus
    dangling-ref WARN per character."""
    return value if isinstance(value, list) else []


# Canonical AC/FR/NFR id grammar, anchored with a BOUNDARY (#2). Without the trailing
# (?![\w-]) a prefix-collision token (`FR-1-placeholder`, `AC3garbage`, `NFR-2unrelated`)
# matches as the real id (`FR-1`/`AC3`/`NFR-2`), letting BUILD ac_coverage mark an
# uncovered PLAN AC "covered". The boundary rejects any trailing word-char OR hyphen —
# so `AC1`, `AC1:`, `AC1 — x`, `FR-1: stmt` match, but `AC1garbage`/`FR-1-foo` do not.
# (Scope note: the AC-coverage path uses digit-only ids — no `AC5b`/`FR-1a` sub-id form
# exists there, verified Gate-0 — so excluding a trailing hyphen-word is safe here.)
_AC_ID_RE = re.compile(r"^(AC\d+|FR-\d+|NFR-\d+)(?![\w-])")


def _extract_ac_id(text) -> str | None:
    """Extract a canonical AC/FR/NFR id from the START of a coverage string, or None.
    Boundary-anchored (#2) — a prefix-collision token returns None, not a false id."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    m = _AC_ID_RE.match(text.strip())
    return m.group(1) if m else None


def _hashable_id(value) -> str:
    """Coerce a requirement id to a hashable str for use as a set member / dict key (#7).
    A list/dict-typed id would raise `TypeError: unhashable type` inside a set/dict
    comprehension; that crash is caught by the SRS _CheckGuard and swallowed as a generic
    ERRORED, LOSING every other SRS/clarification check for the artifact. Coercing to a
    stable str keeps the id usable as a key while the malformed-id itself is WARNed
    separately by _check_requirements_shape. A str passes through unchanged, int/bool via
    str(); other types get a repr so two distinct malformed ids don't collide."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, bool)):
        return str(value)
    return repr(value)


def _acceptance_is_present(acceptance) -> bool:
    """AC4: an acceptance list is 'present' if it has >=1 entry that is EITHER a
    non-empty free-text string (legacy) OR a COMPLETE GWT object — given AND when
    AND then all non-empty (the measurable outcome needs its trigger+context, else
    it is a partial GWT that reads as covered but specifies nothing testable: the
    §4.5 false-green trap). Empty / None / partial-GWT do NOT count."""
    if not acceptance or not isinstance(acceptance, (list, tuple)):
        return False
    for item in acceptance:
        if isinstance(item, str):
            if item.strip():
                return True
        elif isinstance(item, dict):
            # GWT: a complete triplet. A `then` with no `given`/`when` is a partial
            # spec — it names an outcome with no condition, which is not testable.
            if (str(item.get("then", "")).strip()
                    and str(item.get("given", "")).strip()
                    and str(item.get("when", "")).strip()):
                return True
    return False


def _coerce_acceptance(acceptance):
    """#5: normalize a requirement's `acceptance` to a LIST so the list-only consumers
    (_acceptance_is_present / _render_acceptance / INVEST / the audit neutralizer) read
    it uniformly. The doc teaching block once showed a single GWT OBJECT, which every
    consumer silently read as absent (false `_(none)_` / false INVEST WARN — the glass
    box lied). A single GWT dict becomes a 1-item list; a non-empty free-text str a
    1-item list; a list/tuple passes through (as a list); anything else -> []. The
    presence/partial-GWT judgment stays in _acceptance_is_present (§4.5 false-green
    guard preserved) — this only fixes the SHAPE, never the present-vs-absent verdict."""
    if isinstance(acceptance, (list, tuple)):
        return list(acceptance)
    if isinstance(acceptance, dict):
        return [acceptance]
    if isinstance(acceptance, str) and acceptance.strip():
        return [acceptance]
    return []


def _iter_requirements(data: dict) -> list[dict]:
    reqs = data.get("requirements", []) if isinstance(data, dict) else []
    # #5: return shallow copies with acceptance coerced to a list at this single
    # boundary — all 5 consumers route through here, so one coercion covers them all.
    # The copy exists so coercing a single-dict acceptance does NOT rewrite the caller's
    # data["requirements"] shape (the same `data` is shared by validate + audit-render);
    # it is NOT an anti-mutation measure (no consumer writes back — Gate-1 F1).
    return [
        {**r, "acceptance": _coerce_acceptance(r.get("acceptance"))}
        for r in reqs
        if isinstance(r, dict)
    ]


def _check_source_provenance(requirements: list[dict]) -> list[str]:
    """AC2: WARN for any requirement lacking a non-empty source.kind (SOUL P1 at
    the requirement layer — 'no requirement without a source'). WARN-only."""
    warns: list[str] = []
    for r in requirements:
        rid = r.get("id", "<no-id>")
        kind = str(_safe_dict(r.get("source")).get("kind", "")).strip()
        if not kind:
            warns.append(
                f"SRS source: requirement {rid} has no source.kind — "
                f"every requirement should cite user-stated | ddd-derived | assumption."
            )
        elif kind not in REQ_SOURCE_KINDS:
            warns.append(
                f"SRS source: requirement {rid} source.kind='{kind}' is not one of "
                f"{REQ_SOURCE_KINDS}."
            )
    return warns


_CLARIFICATION_ANSWERERS = ("user", "self")


def _check_clarifications(data: dict) -> list[str]:
    """Run 2 (Phase 2) AC2: WARN-only validation of the clarifications[] array.

    Each clarification records one ambiguity and how it was resolved — whether the
    agent self-answered it (`answered_by: self`) or the user answered an MCQ
    (`answered_by: user`). Both are recorded identically and distinguished ONLY by
    the answerer field (design §4.4) so the §4.6 audit trail can show "who decided
    what". This check enforces that each entry carries the fields that make it
    auditable, plus the source.kind<->status consistency invariant below.

    Canonical clarification shape (design SSOT §4.1 + the §4.6.1 audit render —
    which displays 问题/选项/回答/答者): `{qid, question, options?, answer,
    answered_by}`. `answer` is the AUDIT ANCHOR (what was actually picked) and is
    required for a user MCQ. `resolution` is an OPTIONAL self-answer rationale
    (the evidence a `self` answer rests on) — not the audit anchor, so it is only
    expected when `answered_by == self`.

    Checks (all WARN-only, never BLOCK — routed into validate()'s warnings[]):
    - each clarification is a dict with non-empty qid / question
    - answered_by is one of {user, self}
    - `answer` is non-empty (the recorded pick — the §4.6.1 audit anchor)
    - a `self` answer should record its `resolution` (the self-answer evidence)
    - qids are unique within the array (duplicate = corrupted audit trail,
      esp. on resume-and-re-append)
    - CONSISTENCY (O1 clause d): a requirement whose source.kind == 'user-stated'
      (upgraded because the user answered its MCQ) must be status == 'confirmed'.
      A user-stated requirement left un-confirmed — including status MISSING/empty,
      the most likely half-upgrade error — is a recording gap worth a WARN.

    Defensive totality: a non-dict `data`, a non-list `clarifications`, a non-dict
    entry, or an explicit JSON null in any field never throws — it is skipped or
    WARNed, never raised. (`(c.get(k) or "")` treats null as missing, not 'None'.)"""
    if not isinstance(data, dict):
        return []
    warns: list[str] = []
    clarifications = _safe_list(data.get("clarifications"))
    seen_qids: set[str] = set()
    for i, c in enumerate(clarifications):
        if not isinstance(c, dict):
            warns.append(
                f"SRS clarification: entry #{i} is not a dict ({type(c).__name__}) — "
                f"each clarification must be an object with qid/question/answer/answered_by."
            )
            continue
        qid = str(c.get("qid") or "").strip()
        cid = qid or f"#{i}"
        if not qid:
            warns.append(f"SRS clarification: entry {cid} has no qid.")
        elif qid in seen_qids:
            warns.append(f"SRS clarification: duplicate qid '{qid}' — qid must be "
                         f"unique within clarifications[] (a repeated id corrupts the "
                         f"audit trail; check for a double-append on resume).")
        else:
            seen_qids.add(qid)
        if not str(c.get("question") or "").strip():
            warns.append(f"SRS clarification: entry {cid} has no question.")
        if not str(c.get("answer") or "").strip():
            warns.append(f"SRS clarification: entry {cid} has no answer — record the "
                         f"pick (the §4.6.1 audit anchor: what was actually decided).")
        answerer = str(c.get("answered_by") or "").strip()
        if not answerer:
            warns.append(f"SRS clarification: entry {cid} has no answered_by — "
                         f"must be one of {_CLARIFICATION_ANSWERERS}.")
        elif answerer not in _CLARIFICATION_ANSWERERS:
            warns.append(f"SRS clarification: entry {cid} answered_by='{answerer}' is not "
                         f"one of {_CLARIFICATION_ANSWERERS}.")
        elif answerer == "self" and not str(c.get("resolution") or "").strip():
            warns.append(f"SRS clarification: entry {cid} answered_by='self' but no "
                         f"resolution — record the evidence the self-answer rests on.")
    # CONSISTENCY: user-stated requirement should be confirmed (O1 clause d).
    # A MISSING/empty status is the likeliest half-upgrade (kind set, status
    # forgotten) — treat it as inconsistent too, NOT a silent pass (RT1).
    for r in _iter_requirements(data):
        rid = r.get("id", "<no-id>")
        kind = str(_safe_dict(r.get("source")).get("kind") or "").strip()
        status = str(r.get("status") or "").strip()
        if kind == "user-stated" and status != "confirmed":
            shown = status or "<missing>"
            warns.append(
                f"SRS clarification: requirement {rid} source.kind='user-stated' but "
                f"status='{shown}' — a user-answered requirement should be status='confirmed' "
                f"(O1 clause d: the MCQ answer upgraded its source, status should follow)."
            )
    # RECONCILE (a) — Run 3a 块2 (RT3, whole-block, EXISTING fields only; NO per-FR
    # `resolves` linkage — that + direction (b) are deferred to a producer-touching
    # follow-up run). The audit trail answers "who decided what": if the USER answered
    # at least one clarification but NO requirement is source.kind='user-stated', the
    # user's decision was not recorded as a requirement → the link is broken. This is
    # ADVISORY wording (not an assertion): a user may legitimately answer a non-
    # requirement-shaping MCQ (priority / tie-break), so it is a soft WARN, never BLOCK
    # (Gate-1 re-gate NEW-ISSUE: tolerate the false-positive, flag for tuning). Keys on
    # answered_by=user ONLY — self-answers never demand a user-stated requirement
    # (autonomy-first). schema_version-gated upstream by validate_requirements_block.
    reqs = _iter_requirements(data)
    any_user_answer = any(
        isinstance(c, dict) and str(c.get("answered_by") or "").strip() == "user"
        for c in clarifications
    )
    any_user_stated = any(
        str(_safe_dict(r.get("source")).get("kind") or "").strip() == "user-stated"
        for r in reqs
    )
    if any_user_answer and not any_user_stated:
        warns.append(
            "SRS clarification: a user answered ≥1 clarification but NO requirement is "
            "source.kind='user-stated' — the audit trail cannot show which requirement "
            "the user's decision shaped (advisory: a non-requirement-shaping MCQ answer "
            "may legitimately leave no user-stated requirement; verify the user's answer "
            "was recorded on the requirement it decided)."
        )
    return warns


def _check_requirements_shape(requirements: list[dict]) -> list[str]:
    """#6: structural conformance of the declared SRS schema — WARN-only. The enum
    sets REQ_WORK_CLASSES / REQ_STATUSES were defined but never enforced, so typos
    (`user-stroy`, `confirmd`), missing/duplicate ids, and missing statements all
    passed silently. Checks (all advisory, never errors — strangler/autonomy-first):
      - work_class ∈ REQ_WORK_CLASSES
      - status ∈ REQ_STATUSES
      - id present + unique (crash-safe via _hashable_id — a list/dict id is itself a
        malformed-id WARN here AND must not crash the dup detection, #6↔#7 coupling)
      - statement present (non-empty)
    Wired FIRST in validate_requirements_block so a malformed id is WARNed before any
    downstream set/dict comprehension could trip on it."""
    warns: list[str] = []
    # dup detection counts ONLY present, hashable-coerced ids — a missing/empty id is
    # reported by the missing-id WARN, never fed into the dup map (so a literal string
    # id "None" can't collide with a None sentinel: Gate-2 LOW false-negative closed).
    seen: dict[str, int] = {}
    for r in requirements:
        rid_raw = r.get("id")
        rid = rid_raw if isinstance(rid_raw, str) and rid_raw.strip() else None
        rid_label = rid if rid else "<no-id>"
        if not rid:
            warns.append(
                f"SRS shape[{rid_label}]: id is missing or not a string "
                f"(got {type(rid_raw).__name__})."
            )
        else:
            key = _hashable_id(rid_raw)
            seen[key] = seen.get(key, 0) + 1
        wc = r.get("work_class")
        if wc not in REQ_WORK_CLASSES:
            warns.append(
                f"SRS shape[{rid_label}]: work_class={wc!r} is not one of "
                f"{list(REQ_WORK_CLASSES)}."
            )
        st = r.get("status")
        if st not in REQ_STATUSES:
            warns.append(
                f"SRS shape[{rid_label}]: status={st!r} is not one of "
                f"{list(REQ_STATUSES)}."
            )
        stmt = str(r.get("statement", "")).strip()
        if not stmt:
            warns.append(f"SRS shape[{rid_label}]: statement is missing or empty.")
    for key, n in seen.items():
        if n > 1:
            warns.append(
                f"SRS shape: duplicate id {key} appears {n}× "
                f"(ids must be unique within a run)."
            )
    return warns


def _check_invest(requirements: list[dict], profile: str) -> list[str]:
    """AC3/AC10: INVEST self-check — WARN-only, and ONLY for work_class in
    {user-story, enabler-story}. spike/bug/task are exempt (anti-ceremony). The
    `goal` profile is carved out entirely (O5: goal has no discrete FR/TEST model).

    Checks: Independent (depends_on has no cycle and no dangling ref), Valuable
    (source.ref — the Why — non-empty), Testable (acceptance present). Estimable/
    Small/Negotiable are left to human review (not mechanically checkable here)."""
    if normalize_profile(profile) == "goal":
        return []  # AC10 goal carve-out
    warns: list[str] = []
    # #7: coerce ids to a hashable key — a list/dict id would raise TypeError here and
    # the _CheckGuard would swallow the ENTIRE SRS layer. dangling-ref comparison below
    # uses the same coercion so a dep referencing a (coerced) id still resolves.
    all_ids = {_hashable_id(r.get("id")) for r in requirements}
    # Cycle detection is a GRAPH-level property — compute ONCE, not per-requirement
    # (meta-review LOW: per-requirement call was O(n*(V+E)) + emitted N duplicate WARNs).
    if _has_dependency_cycle(requirements):
        warns.append(
            "SRS INVEST: Independent — the depends_on graph has a cycle "
            "(requirements are not independently deliverable)."
        )
    for r in requirements:
        if r.get("work_class") not in _INVEST_WORK_CLASSES:
            continue  # AC3: only story-class requirements get INVEST
        rid = r.get("id", "<no-id>")
        # Independent — dangling ref detection (per-requirement).
        # _safe_list: a string depends_on would iterate char-by-char (bogus WARN/char).
        deps = _safe_list(r.get("depends_on"))
        for d in deps:
            if _hashable_id(d) not in all_ids:
                warns.append(
                    f"SRS INVEST[{rid}]: Independent — depends_on references '{d}' "
                    f"which is not a requirement in this run (dangling)."
                )
        # Valuable — the Why (source.ref) must be non-empty
        ref = str(_safe_dict(r.get("source")).get("ref", "")).strip()
        if not ref:
            warns.append(
                f"SRS INVEST[{rid}]: Valuable — source.ref (the Why) is empty."
            )
        # Testable — acceptance present (AC exists; NOT a coverage claim)
        if not _acceptance_is_present(r.get("acceptance")):
            warns.append(
                f"SRS INVEST[{rid}]: Testable — no acceptance criterion present."
            )
    return warns


def _has_dependency_cycle(requirements: list[dict]) -> bool:
    """DFS cycle detection over the depends_on graph (ids only)."""
    # #7: coerce ids/deps to hashable keys — a list/dict id would crash this dict-comp.
    graph = {
        _hashable_id(r.get("id")): [_hashable_id(d) for d in _safe_list(r.get("depends_on"))]
        for r in requirements
    }
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}

    def visit(node: str) -> bool:
        if color.get(node, WHITE) == GRAY:
            return True
        if color.get(node, WHITE) == BLACK:
            return False
        color[node] = GRAY
        for nxt in graph.get(node, []):
            if nxt in graph and visit(nxt):
                return True
        color[node] = BLACK
        return False

    return any(visit(n) for n in list(graph))


def _check_requirements_axis(requirements: list[dict], scope: str) -> list[str]:
    """AC8/O7: three-axis hard-contradiction — WARN when scope has no delivery
    stage for user-perceivable value (docs/research) yet a requirement is tagged
    with a user-perceivable work_class. BOTH user-story AND enabler-story are
    user-perceivable (they are the INVEST-gated set — an enabler-story unlocks a
    feature a user can perceive), so both are the contradiction under docs/research.
    Most axis combinations are legal; only this one WARNs."""
    warns: list[str] = []
    if (scope or "").strip() in _NO_STORY_SCOPES:
        for r in requirements:
            if r.get("work_class") in _INVEST_WORK_CLASSES:
                rid = r.get("id", "<no-id>")
                warns.append(
                    f"SRS axis: scope='{scope}' has no delivery stage for a "
                    f"user-perceivable requirement, but {rid} is "
                    f"work_class={r.get('work_class')} "
                    f"— likely a misclassification (re-check scope or work_class)."
                )
    return warns


def validate_requirements_block(data: dict, profile: str) -> list[str]:
    """Orchestrate the WARN-only SRS checks. GATED on schema_version>=1 present in
    the requirements block (AC5/R28): absent -> return [] (skip, never block an
    old-shape artifact). Returns a list of WARN strings (routed by validate() into
    warnings[], never errors[])."""
    if not isinstance(data, dict):
        return []
    if "requirements" not in data:
        return []
    # AC5: schema_version gate — a block without it is a legacy/old-shape artifact.
    version = data.get("schema_version")
    if not isinstance(version, int) or version < REQ_SCHEMA_VERSION:
        return []
    requirements = _iter_requirements(data)
    if not requirements:
        return []
    warns: list[str] = []
    # #6 wired FIRST (before _check_invest) so a malformed/unhashable id is WARNed
    # before any downstream set/dict comprehension could trip on it (#6↔#7 coupling).
    warns.extend(_check_requirements_shape(requirements))
    warns.extend(_check_source_provenance(requirements))
    warns.extend(_check_invest(requirements, profile))
    warns.extend(_check_requirements_axis(requirements, str(data.get("scope", ""))))
    warns.extend(_check_clarifications(data))
    return warns


def render_requirements_md(data: dict) -> str:
    """AC7: render the requirements block as human-readable markdown for the
    Pipeline card / Canvas. Per-FR: id, work_class, source, status, GWT AC.

    ⚠️ Testable is shown with a NEUTRAL 'AC-exists' glyph (○ absent / ● present),
    NEVER a green check — a checkmark would read as 'coverage verified', which this
    does not prove (§4.5.2/§4.5.4 false-green guard). PURE function, no gate wiring."""
    reqs = _iter_requirements(data)
    lines: list[str] = ["## Requirements", ""]
    if not reqs:
        lines.append("_(no structured requirements)_")
        return "\n".join(lines)
    lines.append("| ID | work_class | source | status | AC present | Acceptance (GWT) |")
    lines.append("|----|-----------|--------|--------|:---------:|------------------|")
    for r in reqs:
        rid = r.get("id", "")
        wc = r.get("work_class", "")
        src = _safe_dict(r.get("source"))
        src_s = f"{src.get('kind', '')}: {src.get('ref', '')}".strip(": ")
        status = r.get("status", "")
        # neutral AC-exists glyph — ● present / ○ absent, NOT a checkmark
        ac_glyph = "●" if _acceptance_is_present(r.get("acceptance")) else "○"
        ac_text = _render_acceptance(r.get("acceptance"))
        lines.append(f"| {rid} | {wc} | {src_s} | {status} | {ac_glyph} | {ac_text} |")
    return "\n".join(lines)


_ANSWERER_GLYPH = {"user": "👤 user", "self": "🧠 self"}


def _neutralize_requirements_for_audit(reqs: list) -> dict:
    """Build a defensively-escaped `{"requirements": [...]}` copy for the audit
    derivation table (Gate-2 Red-Team HIGH). render_requirements_md interpolates
    id / work_class / source.kind|ref / status / acceptance RAW; we map each through
    _md_cell on a COPY so a newline/pipe in any LLM-authored field cannot inject
    structure into the glass-box audit block — WITHOUT mutating the shared renderer
    (which has other consumers + own output-contract tests). Non-dict entries are
    dropped (matches _iter_requirements). acceptance items (str or GWT dict) are
    escaped element-wise so _render_acceptance can no longer carry a raw newline."""
    out: list[dict] = []
    for r in reqs:
        if not isinstance(r, dict):
            continue
        src = _safe_dict(r.get("source"))
        safe_src = dict(src)
        if "kind" in src:
            safe_src["kind"] = _md_cell(src.get("kind"))
        if "ref" in src:
            safe_src["ref"] = _md_cell(src.get("ref"))
        safe_acc = []
        for item in (r.get("acceptance") if isinstance(r.get("acceptance"), (list, tuple)) else []):
            if isinstance(item, str):
                safe_acc.append(_md_cell(item))
            elif isinstance(item, dict):
                safe_acc.append({k: _md_cell(v) for k, v in item.items()})
            else:
                safe_acc.append(item)
        safe = dict(r)
        safe["id"] = _md_cell(r.get("id", ""))
        safe["work_class"] = _md_cell(r.get("work_class", ""))
        safe["status"] = _md_cell(r.get("status", ""))
        safe["source"] = safe_src
        safe["acceptance"] = safe_acc
        out.append(safe)
    return {"requirements": out}


def _md_cell(value) -> str:
    """Neutralize LLM-authored artifact text before it is interpolated into the
    audit markdown (Gate-2 MED): fold newlines to a visible marker (so a crafted
    `statement`/`answer`/`resolution` cannot terminate a list item and inject a
    fake `## heading` / bullet into the report) and escape `|` (table-column
    injection). The audit block is the 'glass box' — its own integrity must not be
    breakable by the very data it renders. ONE rule for EVERY interpolated field, so
    the per-field escaping drift the Q&A table had cannot recur.

    Whitespace/newline-ONLY input returns "" (not a visible ⏎): an empty-in-substance
    value must stay empty-in-substance, else a blank acceptance would fold to a
    non-empty ' ⏎ ' and flip a downstream presence check (○→● false-green). Content
    with real text keeps its folded newline markers."""
    raw = str(value if value is not None else "")
    if not raw.strip():
        return ""
    return raw.replace("\\", "\\\\").replace("\n", " ⏎ ").replace("\r", "").replace("|", "\\|")


def render_audit_trail_md(requirement: str, data: dict) -> str:
    """Run 3a 块1 (design §4.6.1-4.6.3): render the requirement-provenance AUDIT
    block for the run-end REPORT — the "glass box" view of what the AI understood
    and where it decided for the user. Four parts, ALL mechanically rendered from
    the run's own evaluation artifact (same source as the real-time inline emit, so
    preview and archive cannot drift):

      1. Original intent — the run's requirement text, verbatim (audit anchor:
         "what was originally asked").
      2. Requirements derivation table — reuses render_requirements_md (id /
         work_class / source / status / GWT).
      3. Clarification Q&A — each clarification with a VISUALLY DISTINCT answerer
         marker (👤 user vs 🧠 self), so an auditor sees who decided what.
      4. ⚠️ Leftover / unclosed — requirements the AI "decided for you" or left
         open: source.kind='assumption', status∈{assumed,needs-clarification}, and
         self-answered clarifications. Triggers key on REAL schema fields only;
         `assumption_weight='load-bearing'` is an OPTIONAL extra mark (if present),
         never a trigger. Empty → explicit "✅ none", never a blank.

    PURE function — no gate wiring, no I/O. Graceful degrade: a non-dict/empty data
    or an absent/empty requirements[] (old-shape, non-structured run) returns an
    explicit note, never a crash and never a blank block (AC2)."""
    if not isinstance(data, dict):
        data = {}
    reqs = _iter_requirements(data)
    clarifications = _safe_list(data.get("clarifications"))
    lines: list[str] = ["## 需求溯源审计 (Requirement Provenance Audit)", ""]

    # 1. Original intent — the requirement text is LLM/user-authored, so it is
    # neutralized like every other audit field (Gate-2 Red-Team: a newline in the
    # intent injected a fake heading into the glass-box block). `_md_cell` preserves
    # content (folds newline to ⏎, escapes |) — the intent is still readable verbatim.
    lines.append("### 原始意图 (Original Intent)")
    lines.append(f"> {_md_cell(requirement).strip() or '_(no requirement text)_'}")
    lines.append("")

    if not reqs:
        # AC2: graceful degrade — explicit, never blank.
        lines.append(
            "_(无结构化需求数据 / no structured requirements[] — this run predates the "
            "SRS schema or produced only free-text acceptance_criteria; provenance audit "
            "is unavailable.)_"
        )
        return "\n".join(lines)

    # 2. Requirements derivation table — reuse the existing renderer, but feed it a
    # DEFENSIVELY-NEUTRALIZED copy. render_requirements_md interpolates LLM-authored
    # fields (id / work_class / source.kind|ref / status / acceptance) raw; under the
    # glass-box banner a newline in any of them injects a fake heading (Gate-2 Red-Team
    # HIGH). We neutralize at the CALLER — mapping each field through _md_cell on a copy
    # — rather than mutating render_requirements_md (shared, has other consumers + own
    # tests whose output contract we must not change here; the shared-renderer escaping
    # fix is the separate deferred item). This keeps the audit block injection-proof
    # (its own AC) without a cross-consumer contract migration.
    lines.append("### 需求推导 (Derivation)")
    lines.append(render_requirements_md(_neutralize_requirements_for_audit(reqs)))
    lines.append("")

    # 3. Clarification Q&A — answerer-distinguished.
    lines.append("### 澄清问答 (Clarifications — who decided what)")
    if clarifications:
        lines.append("| Q | 问题 (question) | 回答 (answer) | 答者 (answered_by) |")
        lines.append("|---|------------------|---------------|---------------------|")
        for c in clarifications:
            if not isinstance(c, dict):
                continue
            qid = _md_cell(c.get("qid") or "?")
            q = _md_cell(c.get("question") or "")
            a = _md_cell(c.get("answer") or "")
            answerer = str(c.get("answered_by") or "").strip()
            # Known answerer → trusted glyph constant (safe); any off-vocab value is
            # cell-escaped like every other field (Gate-2 LOW: glyph fallback was raw).
            glyph = _ANSWERER_GLYPH.get(answerer) or (_md_cell(answerer) if answerer else "_(unspecified)_")
            lines.append(f"| {qid} | {q} | {a} | {glyph} |")
    else:
        lines.append("_(无澄清记录 / no clarifications)_")
    lines.append("")

    # 4. ⚠️ Leftover / unclosed — what the AI decided for you or left open.
    lines.append("### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)")
    leftover: list[str] = []
    for r in reqs:
        rid = _md_cell(r.get("id", "?"))
        src = _safe_dict(r.get("source"))
        kind = str(src.get("kind") or "").strip()
        status = str(r.get("status") or "").strip()
        mark = " ← 建议现在确认" if str(src.get("assumption_weight") or "").strip() == "load-bearing" else ""
        stmt = _md_cell(r.get("statement") or "")
        if kind == "assumption":
            leftover.append(f"- [ASSUMPTION] {rid}: {stmt}{mark}")
        elif status in ("assumed", "needs-clarification"):
            leftover.append(f"- [{status.upper()}] {rid}: {stmt}{mark}")
    for c in clarifications:
        if isinstance(c, dict) and str(c.get("answered_by") or "").strip() == "self":
            qid = _md_cell(c.get("qid") or "?")
            leftover.append(f"- [SELF-ANSWERED] {qid}: {_md_cell(c.get('answer') or '')} "
                            f"(🧠 AI 替你拍的板 — resolution: {_md_cell(c.get('resolution') or '')})")
    if leftover:
        lines.extend(leftover)
    else:
        lines.append("✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)")
    return "\n".join(lines)


def _render_acceptance(acceptance) -> str:
    if not acceptance or not isinstance(acceptance, (list, tuple)):
        return "_(none)_"
    parts: list[str] = []
    for item in acceptance:
        if isinstance(item, str):
            parts.append(item.replace("|", "\\|"))
        elif isinstance(item, dict):
            g, w, t = item.get("given", ""), item.get("when", ""), item.get("then", "")
            parts.append(f"Given {g}, when {w}, then {t}".replace("|", "\\|"))
    return "<br>".join(parts) if parts else "_(none)_"


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
# Gate-3 M1 (false-positive fix): the old table matched bare substrings — `"single "`
# fired on "add a single retry", `"funnel"` on "funnel chart", `"consolidate"` on
# "consolidate two CSS files", `"one gate"` on a literal parking-gate UI. A class
# migration is not any sentence containing one of those words; it is moving a COLLECTION
# (all callers / every path / a class of siblings) onto one target. So each pattern is a
# WORD-BOUNDED regex that requires the migrate-a-collection SHAPE, not a lone noun.
# Dropped as too broad (real false positives from the E2E probe): bare "single ",
# "funnel", "one gate", "consolidate <two things>", standalone "unified".
_MIGRATION_PATTERNS = tuple(re.compile(p) for p in (
    r"\bmigrat(?:e|es|ing|ion)\b",                    # migrate/migration (the core verb)
    r"\b(?:unify|unifies|unifying|consolidat\w+|de-?dup\w*)\b\s+(?:all|every|the\s+\w+\s+(?:callers|paths|sites))",
    r"\ball\s+(?:callers|call\s?sites|paths|writers|write\s+paths|sink\s+callers)\b",
    r"\bevery\s+(?:caller|call\s?site|path|writer|write\s+path)\b",
    r"\b(?:route|gate|funnel|channel)\s+(?:all|every)\b",          # "route/gate all …"
    r"\bthrough\s+(?:one|a\s+single)\s+\w+",                        # "through one gate"
    r"\bsingle\s+(?:gate|entry\s?point|chokepoint|ingestion|admission)\b\s+for\s+(?:all|every)",
    r"\bchokepoint\b",
))


def _check_migration_class_declared(data: dict, profile: str) -> list[str]:
    """AC11: if the evaluation's requirement/understanding text is migration-shaped, the
    artifact MUST carry a non-empty `migration_class` block. Strict profiles only (a docs/
    research run that merely mentions 'migrate' isn't shipping code). Fail-CLOSED on the
    keyword hit — the whole point is to remove the opt-out for the runs that need it.
    Matching is via word-bounded shape patterns (Gate-3 M1), NOT bare substrings, so a
    benign "single retry" / "funnel chart" / "consolidate two files" does NOT false-fire."""
    if profile in _RELAXED_UNDERSTANDING_PROFILES:
        return []
    und = data.get("understanding") if isinstance(data.get("understanding"), dict) else {}
    haystack = " ".join(str(x).lower() for x in (
        und.get("claim", ""), und.get("evidence", ""), data.get("summary", ""),
        data.get("requirement", ""),
    ))
    if not any(p.search(haystack) for p in _MIGRATION_PATTERNS):
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


def _check_security_boundaries(
    project: str, run_id: str, plan_data: dict, stages_list: list, repo_root: str | None = None,
) -> tuple[list[str], list[str]]:
    """Design-time security gate (completion-time, cross-stage).

    When EVALUATE classified the change `cross_boundary.value == true`, the PLAN
    design_doc must carry a `security_boundaries` block whose every entry either
    cites a REAL enforcement locus (`path:line` whose source file exists on disk)
    or is explicitly `escalate: true` (handed to the human). This is the pipeline's
    ONLY design-level security touchpoint — every other security review is
    code-level (REVIEW specialist / Gate-2 adversarial), i.e. AFTER code is written.

    It is deliberately assumption-VERIFICATION, not threat-ENUMERATION: enumerating
    threats against an unwritten design hallucinates; naming a trust assumption and
    citing the code that enforces it is bounded and catchable. The validator verifies
    the locus is REAL (fabricated/stale locus → BLOCK); the human architect + the
    DELIVER security specialist verify it is CORRECT (semantics are beyond a validator).

    Lives here (completion-time `validate()`) and NOT in publish-time
    `validate_artifact_data`, because only `validate()` has (project, run_id) to
    read the cross-stage evaluation artifact via `_load_artifact_data`.

    THREE-WAY fail policy (Gate-1 #6 — never a silent escape, never a false-block):
      • evaluation artifact unreadable OR `cross_boundary` field absent → WARN
        (surface the un-verifiable case; do NOT block a run whose eval read hiccups)
      • `cross_boundary.value != true` → no-op (zero ceremony on non-security runs)
      • `cross_boundary.value == true` → require a well-formed security_boundaries
        block; otherwise BLOCK.

    Returns (errors, warnings). Errors BLOCK; warnings surface.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Locate the evaluation stage's artifact and read cross_boundary.
    eval_stage = next(
        (s for s in stages_list if s.get("stage", "?") == "evaluate"), None
    )
    eval_art_id = eval_stage.get("artifact_id") if eval_stage else None
    eval_data = _load_artifact_data(project, run_id, eval_art_id) if eval_art_id else None

    if not eval_data or "cross_boundary" not in eval_data:
        # Un-verifiable — WARN, do NOT block (fail-visible, not fail-closed, not silent).
        warnings.append(
            "Security boundaries: could not read the evaluation artifact's "
            "cross_boundary classification — cannot verify whether a design-level "
            "security review is required. (If this change opens an exposure surface, "
            "add a security_boundaries block to the design_doc.)"
        )
        return errors, warnings

    cb = eval_data.get("cross_boundary") or {}
    if not (isinstance(cb, dict) and cb.get("value") is True):
        return errors, warnings  # false / malformed-value → no-op by design

    # cross_boundary is TRUE → a security_boundaries block is required.
    sb = plan_data.get("security_boundaries")
    if not isinstance(sb, list) or not sb:
        errors.append(
            "Security boundaries: this change is cross_boundary=true "
            f"(kinds={cb.get('kinds')}), so the design_doc MUST carry a non-empty "
            "`security_boundaries` block. Each entry names a trust boundary the design "
            "introduces, the trust ASSUMPTION at it, and the enforcing code locus "
            "(`path:line`) — or `escalate: true` if it needs human review. This is the "
            "design-level security check; a code-review-only pass ships a design-level "
            "gap (e.g. an unauthenticated endpoint) undetected until runtime."
        )
        return errors, warnings

    src_root = Path(repo_root) if repo_root else None
    for i, entry in enumerate(sb):
        if not isinstance(entry, dict):
            errors.append(f"Security boundaries: entry[{i}] is not an object.")
            continue
        # `is True` (not truthiness) on purpose: escalate is a deliberate boolean
        # opt-out; a stray truthy value (1/"true") should NOT silently bypass the
        # locus check — it falls through and is validated like any other entry.
        if entry.get("escalate") is True:
            continue  # explicitly handed to the human — accepted
        locus = str(entry.get("enforcement") or "").strip()
        if not locus:
            errors.append(
                f"Security boundaries: entry[{i}] ('{entry.get('boundary','?')}') has "
                "no enforcement locus and is not escalate:true — a prose assumption with "
                "no code boundary is theater. Cite the enforcing `path:line`, or set "
                "escalate:true."
            )
            continue
        # Verify the locus FILE exists (verifies the locus is REAL, not that it is
        # semantically correct — that is the human's + security specialist's job).
        # Parse 'path:line' → path. FAIL-OPEN on uncertainty (mirrors deliver.md
        # disk_check): a locus is BLOCKED as fabricated ONLY when we can DEFINITIVELY
        # resolve a base root and the file is absent there. When no source root is
        # known (repo_root=None, the common completion-time case) a RELATIVE locus is
        # un-verifiable — WARN, never BLOCK — because the design_doc's paths are
        # relative to the SOURCE repo, which is neither cwd nor the tmp/workspace root.
        # rsplit(":", 1) drops a trailing ':NN' line number; a Windows drive prefix
        # (C:\x) has no trailing digit-only segment we depend on — the path branch
        # below handles it as absolute.
        locus_path = locus.rsplit(":", 1)[0] if ":" in locus else locus
        p = Path(locus_path)
        if p.is_absolute():
            # Absolute path is definitively checkable.
            if not p.exists():
                errors.append(
                    f"Security boundaries: entry[{i}] ('{entry.get('boundary','?')}') cites "
                    f"enforcement locus '{locus}' but that file does not exist — a "
                    "fabricated or stale locus. Cite the real enforcing code, or escalate:true."
                )
        elif src_root:
            # A known source root makes a relative locus definitively checkable.
            if not (src_root / locus_path).exists():
                errors.append(
                    f"Security boundaries: entry[{i}] ('{entry.get('boundary','?')}') cites "
                    f"enforcement locus '{locus}' but that file does not exist under the "
                    "source root — a fabricated or stale locus. Cite the real enforcing "
                    "code, or escalate:true."
                )
        else:
            # No source root — relative locus is un-verifiable. WARN, never false-block.
            warnings.append(
                f"Security boundaries: entry[{i}] locus '{locus}' could not be verified "
                "(no source root available at completion-time). Confirm the enforcing "
                "code exists at that path (fail-open, not blocking)."
            )

    return errors, warnings


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
        # Additive (A4): allowed literals for the template's enum fields, and the
        # stage-RECORD contract (judgment fields stage-complete accepts).
        "enums": STAGE_ENUMS.get(stage, {}),
        "record_schema": STAGE_RECORD_SCHEMAS.get(stage, {}),
        "record_template": STAGE_RECORD_TEMPLATES.get(stage, {}),
    }


def _outcome_shape_errors(outcome) -> list[str]:
    """Errors for a PRESENT deliver ``outcome`` that review_summary cannot read.

    review_summary reads ``{problem_before: str, result_now: str, approach: [str]}``
    through ``_d()``, so any other shape renders 未记录 with no error — the author
    believes it was recorded. Absence is NOT checked here (it is the documented,
    visible 未记录 path); extra keys are allowed; blank strings are left to the
    renderer, which already shows them as 未记录."""
    shape = "{problem_before: str, result_now: str, approach: [str]}"
    if not isinstance(outcome, dict):
        return [f"outcome must be an object {shape}, got {type(outcome).__name__} — "
                f"the completion summary cannot read it and would show 未记录"]
    errs = []
    for key in ("problem_before", "result_now"):
        if not isinstance(outcome.get(key), str):
            errs.append(f"outcome.{key} must be a string ({shape}), "
                        f"got {type(outcome.get(key)).__name__}")
    approach = outcome.get("approach")
    if not isinstance(approach, list) or not all(isinstance(a, str) for a in approach):
        errs.append(f"outcome.approach must be a list of strings ({shape})")
    return errs


def validate_artifact_data(
    stage: str, data: dict, profile: str = "full", repo_root: str | None = None,
    *, run_created_at: str | None = None, warnings: list[str] | None = None,
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
        run_created_at: run.json `created_at` of the run this artifact belongs to.
                 Runs created before RED_TEAM_CUTOVER, and None (no run known),
                 get the Red Team HIGH rule as a warning instead of an error.
        warnings: optional list that receives those grandfathered warnings.
    """
    errors: list[str] = []

    # C3: canonicalize the profile ONCE at entry so every strict/relaxed decision
    # below compares against the SSOT value — a variant ("Full") or alias ("standard")
    # can no longer slip past the adversarial-enforcement gates (which used raw
    # `profile in ("full","bugfix","")` literals, fail-OPEN on any unknown value).
    profile = normalize_profile(profile)

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
            # (C3: strict = fail-closed via SSOT — unknown/variant profile is strict)
            if not is_relaxed_profile(profile):
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
        elif child_fields:
            # Present, NOT a dict, AND has REQUIRED CHILD FIELDS (e.g. a bare
            # `adversarial_review: true`). Without this branch the child-field
            # requirements are silently skipped for any non-dict parent — a hollow
            # artifact passes depth validation (D4 adversarial finding, run_57929039:
            # goal_cycle's STAGE_DEPTH `adversarial_review: [findings]` was defanged by
            # a boolean parent). A present depth-parent that declares children MUST be
            # a dict carrying them.
            #
            # ⚠️ GUARDED ON `child_fields` (Gate-2 CRITICAL, run_57929039): a
            # PRESENCE-ONLY depth entry has an EMPTY child list — notably
            # STAGE_DEPTH["build"]["ac_coverage"] = [], where `ac_coverage` is
            # legitimately a LIST, NOT a dict. An unguarded `else` rejected every valid
            # BUILD publish. Empty child_fields ⇒ presence-only ⇒ the parent's type is
            # not constrained by THIS branch. `ac_coverage`'s list-shape + non-empty
            # requirement is instead enforced by Check 8f in validate() (the completion/
            # advance gate, a DIFFERENT function) — a malformed ac_coverage cannot
            # advance to REVIEW even though it is not rejected here at publish time.
            errors.append(
                f"'{parent_field}' must be a dict carrying {child_fields} for depth "
                f"validation, got {type(parent_val).__name__} — a bare scalar cannot "
                f"carry the required sub-fields (record the object, not a boolean)."
            )

    # Stage-specific invariants (subset of _check_depth for fast feedback)
    if stage == "deliver":
        if "outcome" in data:
            errors.extend(_outcome_shape_errors(data["outcome"]))
        ar = data.get("adversarial_review")
        if isinstance(ar, dict):
            tier = ar.get("profile_tier", "")
            # Tier=skipped on full/bugfix → BLOCK (C026 fix)
            # Relaxed profiles (trivial/research/docs) can skip adversarial
            # (C3: strict = fail-closed via SSOT — a variant/unknown profile is
            # STRICT, so it can no longer skip adversarial enforcement.)
            _strict = not is_relaxed_profile(profile)
            if tier in ("skipped", "lite") and _strict:
                errors.append(
                    f"adversarial_review.profile_tier='{tier}' but profile='{profile}' "
                    f"requires full adversarial review. Only trivial/research/docs exempt."
                )
            # spawned=true enforcement: adversarial sub-agent must actually run (not just tier declared)
            spawned = ar.get("spawned")
            if _strict:
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
            # Gate-2 Red Team trigger (HIGH half): a specialist HIGH/CRITICAL
            # requires the Red Team to have run. Strict profiles only; shared
            # helper with the completion-time gate in _check_depth (R27).
            # Grandfathered (warning only) for runs before RED_TEAM_CUTOVER.
            errors.extend(_red_team_gate(ar, profile, run_created_at=run_created_at,
                                         warnings=warnings))

        # --- Three-Layer Governance: Full deliver ceremony enforcement ---
        # For full/bugfix profiles, ALL deliver sub-steps must have evidence.
        # This prevents the C028 pattern: running adversarial but skipping
        # completion audit, meta-review, and convergence loop.
        # Profile-aware relaxation: trivial/research/docs skip meta_review + convergence.
        # (C3: SSOT predicate — strict is the fail-closed default for unknown/variant.)
        if is_relaxed_profile(profile):
            # Relaxed validation: only need completion_audit (basic AC check)
            ca = data.get("completion_audit")
            if not isinstance(ca, dict):
                errors.append(
                    "completion_audit missing from deliver artifact. "
                    "Even trivial profiles need basic AC verification."
                )
            # meta_review, convergence: NOT required for relaxed profiles
        else:
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
        # (C3: SSOT predicate — strict is fail-closed for unknown/variant.)
        _is_bug = (
            (data.get("scope") == "bugfix") or (data.get("bug_class") is True)
        ) and not is_relaxed_profile(profile)
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
    # goal_cycle (D4): replaces BUILD+REVIEW+TEST in the goal profile — consumes the
    # design_doc, produces the changeset the goal cycles accumulate. deliver then
    # consumes that changeset (goal profile skips the standalone build/review/test).
    "goal_cycle": {
        "consumes": ["design_doc"],
        "produces": ["changeset"],
        "optional_produces": ["review", "test_report"],
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

# In-memory candidate-state override (A1, stage-complete). When set, _load_run and
# _load_artifact_data answer from it instead of disk, so validate() — the ONE
# implementation of the per-stage checks — runs against the state stage-complete is
# ABOUT to write. A ContextVar (not a module global) so concurrent validations in
# one process cannot see each other's candidate. Shape:
#   {"project": str, "run_id": str, "run": dict, "artifacts": {artifact_id: dict}}
_STATE_OVERRIDE: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "pipeline_validator_state_override", default=None,
)


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
    """Load a pipeline run from .artifacts/runs/<run_id>/run.json.

    Consults the in-memory candidate-state override first (see
    ``validate_stage_state``): while a stage-complete pre-write validation is in
    flight, the CANDIDATE run state is returned for that exact (project, run_id), so
    validate() judges what WOULD be written without anything touching disk.
    """
    _ov = _STATE_OVERRIDE.get()
    if _ov is not None and _ov.get("project") == project and _ov.get("run_id") == run_id:
        return _ov.get("run")
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

    A candidate (not-yet-published) artifact registered by ``validate_stage_state``
    resolves from the in-memory override before any manifest read.
    """
    _ov = _STATE_OVERRIDE.get()
    if _ov is not None and _ov.get("project") == project:
        _cand = (_ov.get("artifacts") or {}).get(artifact_id)
        if _cand is not None:
            return _cand
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
                 run_id: str = "", *, run_created_at: str | None = None,
                 warnings: list[str] | None = None) -> list[str]:
    """Layer 2: validate field values, not just existence.

    Catches hollow artifacts where fields exist but content indicates
    the quality gate was not actually executed.
    """
    errors: list[str] = []

    # C3: canonicalize once at entry (SSOT) so the adversarial-enforcement gates
    # below can't be skipped by a variant/alias profile value.
    profile = normalize_profile(profile)

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
            # (C3: fail-closed via SSOT — strict for any non-relaxed profile.)
            if not is_relaxed_profile(profile):
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
            elif tier in ("skipped", "lite") and not is_relaxed_profile(profile):
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

            # Gate-2 Red Team trigger (HIGH half) at COMPLETION time — same helper
            # as validate_artifact_data (R27), so a red_team_dispatched patched away
            # after publish still blocks `run-update --status completed`. Runs
            # created before RED_TEAM_CUTOVER (or no run) get a warning instead.
            errors.extend(_red_team_gate(ar, profile, run_created_at=run_created_at,
                                         warnings=warnings, prefix="Depth: "))

            # Two-field (spawned + evidence) enforcement at COMPLETION time.
            # Mirrors validate_artifact_data:423-445 (publish-time), closing the
            # fail-open hole found by adversarial review of run_45ab67c7: the
            # completion gate (validate()->_check_depth) previously checked only
            # profile_tier, so a spawned=false self-review artifact passed
            # completion. This makes the gate_spawn_blocked guarantee structural
            # on EVERY path to status:completed, not just `publish --stage deliver`.
            # (C037/CLASS-A fail-open-at-last-gate pattern.)
            if isinstance(ar, dict) and not is_relaxed_profile(profile):
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

            # D1 (run_57929039 deferred → run_1ce2ca39 REJECTED). Do NOT re-open:
            # the completion-time honor-system `spawned`/`evidence` fields here are
            # BY DESIGN, not a hole — the real adversarial teeth live at the COMMIT
            # layer and are STRONGER than the run-scoped completion check D1 proposed.
            # The live commit gate `create_adversarial_commit_gate` (security_hooks.py,
            # wired PreToolUse Bash in hook_builder.py) → `_session_adversarial_coverage`
            # DENYs `git commit` unless an adversarial `_adv_` marker's reviewed_paths
            # COVERS every path being committed (DIFF-BOUND, not merely run-scoped). So
            # un-reviewed code cannot be committed regardless of what this stage's
            # adversarial_review fields say. Adding run-scoped teeth HERE would be
            # redundant AND reintroduce a false-block hazard: the SubagentStop hook has
            # only `sdk_session_id` (`_active_pipeline_run_id` is read but SET nowhere —
            # dead), and run.json records no session_id, so a run-id-scoped marker check
            # would false-block EVERY real full/bugfix completion (Gate-2, run_57929039),
            # while a ts>run_start session-window fallback (Check 9b) lets a CONCURRENT
            # sibling run's marker satisfy this run (Gate-1). Check 9b stays WARN-level
            # for the same reason. Net: commit-gate = teeth; this field = audit record.

        # completion_audit: MUST exist for full/bugfix profiles
        ca = artifact_data.get("completion_audit")
        if ca is None and not is_relaxed_profile(profile):
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

        # ac_verification: MUST exist with a status for full/bugfix (D9, run_57929039).
        # STAGE_DEPTH["deliver"] requires ac_verification.status at PUBLISH time
        # (validate_artifact_data), but the COMPLETION gate runs _check_depth (this
        # function), NOT validate_artifact_data, and the completion backstop loop skips
        # the deliver stage — so ac_verification could be PATCHED AWAY after publish and
        # still pass `run-update --status completed`. Mirror the completion_audit check
        # here so the field is enforced on EVERY path to status:completed (R27: one
        # invariant, both gates — no publish-only enforcement hole).
        av = artifact_data.get("ac_verification")
        if av is None and not is_relaxed_profile(profile):
            errors.append(
                "Depth: ac_verification field MISSING from deliver artifact — "
                "the AC verification step (each acceptance criterion → evidence) was "
                "not recorded. This is MANDATORY for full/bugfix. Run it before completing."
            )
        elif isinstance(av, dict):
            if not str(av.get("status", "")).strip():
                errors.append(
                    "Depth: ac_verification.status missing or empty — record the "
                    "verification outcome (e.g. 'verified') before delivery."
                )
        elif av is not None:
            errors.append(
                f"Depth: ac_verification must be a dict carrying 'status', "
                f"got {type(av).__name__}."
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


_RP_ID_RE = re.compile(r"\bRP\d+\b")
_SOURCE_REPO = Path(__file__).resolve().parents[2]


def _unverdicted_review_patterns(patterns, files_touched) -> "list[str]":
    """RP ids that --patterns-for selects for ``files_touched`` (by path, as REVIEW
    lists them) with no entry in ``patterns``. [] when anything is unreadable: this
    is a warning, never a reason to fail validation."""
    if not isinstance(files_touched, list) or not files_touched:
        return []
    try:
        import importlib.util
        sp_file = (_SOURCE_REPO / "backend" / "skills" / "s_autonomous-pipeline"
                   / "scripts" / "spawn_prompt.py")
        spec = importlib.util.spec_from_file_location("_spawn_prompt_rp", sp_file)
        sp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sp)
        paths = []
        for f in files_touched:
            p = Path(str(f))
            try:
                paths.append(str(p.resolve().relative_to(_SOURCE_REPO)) if p.is_absolute()
                             else str(p))
            except ValueError:
                paths.append(str(p))
        _h, _rows, hits = sp._select(sp.REVIEW_PATTERNS_PATH.read_text(encoding="utf-8"),
                                     paths, "")
    except Exception:  # noqa: BLE001 — advisory check
        return []
    seen: set[str] = set()
    for p in patterns if isinstance(patterns, list) else []:
        if isinstance(p, dict):
            seen.update(_RP_ID_RE.findall(" ".join(str(p.get(k) or "")
                                                   for k in ("pattern", "id", "rp"))))
    return [r["id"] for r in hits if r["id"] not in seen]


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
                missing = _unverdicted_review_patterns(patterns, run.get("files_touched"))
                if missing:
                    warnings.append(
                        "Semantic: runtime_patterns gives no verdict for "
                        f"{len(missing)} pattern(s) that spawn_prompt.py --patterns-for "
                        f"selects for this run's changed files: {', '.join(missing)}. "
                        "Record each listed row as pass / fail / N/A.")

    # --- Check 3: Confidence penalty consistency (deliver stage) ---
    # Skip if confidence_score isn't a dict — _check_depth already BLOCKs on bare numbers (PE-5)
    if stage == "deliver" and isinstance(artifact_data.get("confidence_score"), dict):
        cs = artifact_data["confidence_score"]
        penalties = cs.get("penalties", [])

        # Check if review had findings
        review_findings = 0
        test_failures = 0
        for s in run.get("stages", []):
            s_name = s.get("stage", "")
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
                if s.get("stage", "?") == stage:
                    break
                s_routing = STAGE_ROUTING.get(s.get("stage", ""), {})
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

    # C3: canonicalize the run's profile ONCE (SSOT) — every strict/relaxed check
    # in validate() and the helpers it calls compares against this normalized value,
    # so a variant/alias stored in run.json ("Full"/"standard") gates identically.
    profile = normalize_profile(run.get("profile"))
    stages_list = run.get("stages", [])

    # Find the stage record for the stage being validated
    stage_record = None
    for s in stages_list:
        if s.get("stage", "?") == stage:
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
                        (s for s in stages_list if s.get("stage", "?") == "plan"),
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
                                    _aid = _extract_ac_id(entry_ac)
                                    if _aid:
                                        covered_ac_ids.add(_aid)
                                covered_texts = {entry.get("ac", "").strip() for entry in ac_coverage}

                                # Detect duplicate AC IDs in PLAN (AC ID match unsafe if dupes)
                                plan_id_counts: dict[str, int] = {}
                                for p in plan_acs:
                                    p_str = p.strip() if isinstance(p, str) else str(p).strip()
                                    _aid = _extract_ac_id(p_str)
                                    if _aid:
                                        plan_id_counts[_aid] = plan_id_counts.get(_aid, 0) + 1
                                # AC IDs that appear exactly once in PLAN (safe for ID-based match)
                                unique_plan_ids = {k for k, v in plan_id_counts.items() if v == 1}

                                for i, pac in enumerate(plan_acs):
                                    pac_str = pac.strip() if isinstance(pac, str) else str(pac).strip()

                                    # Strategy 1: identifier match (AC1, AC2, etc.)
                                    # Extract "ACN:" prefix from plan AC (boundary-anchored, #2)
                                    ac_id = _extract_ac_id(pac_str)

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
                                    _aid = _extract_ac_id(p_str)
                                    if _aid:
                                        plan_ac_ids.add(_aid)

                                for entry in ac_coverage:
                                    entry_ac = entry.get("ac", "").strip()
                                    entry_ref = entry.get("plan_ac_ref", "").strip()
                                    reverse_matched = False
                                    # Match by plan_ac_ref identifier
                                    if entry_ref and entry_ref in plan_ac_ids:
                                        reverse_matched = True
                                    # Match by extracted AC ID from BUILD entry text (boundary, #2)
                                    if not reverse_matched:
                                        _eid = _extract_ac_id(entry_ac)
                                        if _eid and _eid in plan_ac_ids:
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
                (s for s in stages_list if s.get("stage", "?") == "build"),
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
            # C3: relaxed skip via SSOT (fail-closed — unknown/variant is strict, checked).
            # _block_profiles stays an explicit tier: full/bugfix BLOCK, goal (also strict) WARNs.
            _block_profiles = ("full", "bugfix")  # F11: BLOCK not WARN for quality profiles
            if artifact_data and not is_relaxed_profile(profile):
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
                            (s for s in stages_list if s.get("stage", "?") == "build"),
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
        elif stage == "plan":
            # Check 8-plan: design-level security gate. When EVALUATE classified
            # cross_boundary=true, the design_doc must carry a security_boundaries
            # block whose entries each cite a real enforcement locus (or escalate).
            # Three-way fail policy inside the helper (false=no-op / true+incomplete=
            # BLOCK / eval-unreadable=WARN). Cross-stage: reads the evaluation artifact.
            checks_total += 1
            sec_errs, sec_warns = _check_security_boundaries(
                project, run_id, artifact_data or {}, stages_list, repo_root=None
            )
            errors.extend(sec_errs)
            warnings.extend(sec_warns)
            if not sec_errs:
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
            depth_errors = _check_depth(stage, artifact_data, profile, run_id=run_id,
                                        run_created_at=run.get("created_at") or "",
                                        warnings=warnings)
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
        # direct (G1) runs the same full-tier adversarial review at DELIVER.
        if stage == "deliver" and profile in ("full", "bugfix", "direct"):
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
    if _g.errored_nonblocking and stage == "deliver" and profile in ("full", "bugfix", "direct"):
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

    # --- Check 11b: Structured Requirement Spec (SRS) WARN layer (evaluate) ---
    # WARN-ONLY by construction: routed into warnings[] exactly like Check 11, NEVER
    # into validate_artifact_data (publish-time, errors-only). schema_version-gated:
    # an old-shape evaluation (no requirements block / no schema_version) is skipped.
    # (Design §4.0/§8; this run's own Gate-1 BLOCK fixed the earlier mis-routing.)
    with _CheckGuard("srs_requirements", SEVERITY_ADVISORY, errors, warnings, check_results) as _g:
        _g.passed()
        if artifact_data and stage == "evaluate":
            srs_data = dict(artifact_data)
            srs_data.setdefault("scope", artifact_data.get("scope", ""))
            srs_warnings = validate_requirements_block(srs_data, profile)
            warnings.extend(srs_warnings)

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
        if s.get("stage", "?") == stage_name:
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
# Stage-boundary contract (pipeline-speed-systematic-fix §4-A / F2 / G1)
#   STAGE_RECORD_SCHEMAS + validate_stage_record  — the stage-RECORD contract
#   validate_exit_criteria / judge_exit_criteria  — the G1 exit contract
#   push_ready_upper_bound                         — deliver push_ready from gates
#   structure_errors                               — A3 structured error shape
#   validate_stage_state                           — validate() on a candidate state
# ---------------------------------------------------------------------------

DECISION_CLASSIFICATIONS = ("mechanical", "taste", "judgment")
EXIT_CRITERIA_KINDS = ("test", "probe", "mutation", "manual")
_LESSON_MIN_CHARS = 20  # each reflect lesson must be > 20 chars (completion-gate parity)
_SAFE_REF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_FAILING_OUTCOMES = frozenset({"failed", "error", "timeout"})

# Field → type spec. Specs: "str", "bool", "number", "dict", "list",
# "list[decision]", "list[lesson]", "dict[str,str]". Unknown record keys are allowed
# (forward compat) — derived keys are overwritten by stage-complete anyway.
_COMMON_RECORD_FIELDS: dict[str, str] = {
    "decisions": "list[decision]",
    "skip_reason": "str",
    "skip_justification": "dict",
    "notes": "str",
    "summary": "str",
    "exit_evidence": "dict[str,str]",
    "token_cost": "number",
}
_RECORD_STAGE_EXTRAS: dict[str, dict[str, str]] = {
    "deliver": {"push_ready": "bool", "push_ready_reason": "str",
                "outputs_surfaced": "bool", "report_path": "str",
                "attention_flags": "list"},
    "reflect": {"lessons": "list[lesson]", "next_steps": "list"},
}
_RECORD_STAGES = ("evaluate", "think", "plan", "build", "review", "test",
                  "deliver", "goal_cycle", "reflect")

# Per-stage stage-RECORD contract (the INSTRUCTIONS.md "flat fields" table as
# code). `required` applies to a COMPLETED record; `required_when_skipped` to a
# SKIPPED one. Missing `decisions` stays a validate() WARNING (Check 4) — not
# tightened here.
STAGE_RECORD_SCHEMAS: dict[str, dict] = {
    _st: {
        "fields": {**_COMMON_RECORD_FIELDS, **_RECORD_STAGE_EXTRAS.get(_st, {})},
        "required": ["lessons"] if _st == "reflect" else [],
        "required_when_skipped": ["skip_reason"],
        "enums": {"decisions[].classification": list(DECISION_CLASSIFICATIONS),
                  "status": ["completed", "skipped"]},
    }
    for _st in _RECORD_STAGES
}

_DECISION_EXAMPLE = {"description": "Chose the shared helper over a copy",
                     "classification": "judgment",
                     "reasoning": "one implementation of the checks cannot drift"}
STAGE_RECORD_TEMPLATES: dict[str, dict] = {
    _st: {"decisions": [dict(_DECISION_EXAMPLE)]} for _st in _RECORD_STAGES
}
# TEST's exit_evidence keys (stages/test.md): every value is ONE string. A list is
# accepted on input and joined by normalize_exit_evidence (run_a87bfe3f).
STAGE_RECORD_TEMPLATES["test"] = {
    "decisions": [dict(_DECISION_EXAMPLE)],
    "exit_evidence": {
        "wtf": "score 0 (0 fixes, 4 files)",
        "fixes": "none",
        "unfixed": "tests/test_x.py::test_y: fails on HEAD too, outside this change",
        "expectations": "no test expectation modified",
    },
}


def normalize_exit_evidence(record: dict) -> dict:
    """Return ``record`` with each list-of-strings ``exit_evidence`` value joined
    into one string (blank items dropped first, joined with ``'; '``).

    stages/test.md describes ``fixes``/``unfixed`` per item, so agents write lists,
    while the record schema is ``dict[str,str]``; 4+ runs were rejected on exactly
    this shape (run_a87bfe3f). An empty or all-blank list becomes ``''``, never a
    placeholder: judge_exit_criteria counts a manual criterion as met only for a
    non-blank string, so ``{"X6": []}`` must stay unmet. Any other value (a list
    holding a non-string, a dict, a number) is left as is for the schema to reject.
    """
    if not isinstance(record, dict):
        return record
    ev = record.get("exit_evidence")
    if not isinstance(ev, dict):
        return record
    out = {}
    for key, val in ev.items():
        if isinstance(val, list) and all(isinstance(x, str) for x in val):
            out[key] = "; ".join(x.strip() for x in val if x.strip())
        else:
            out[key] = val
    return {**record, "exit_evidence": out}


STAGE_RECORD_TEMPLATES["deliver"] = {
    "decisions": [dict(_DECISION_EXAMPLE)],
    "exit_evidence": {"X9": "manual check: opened the page and the badge renders"},
    "notes": "push_ready is computed by the tool; send push_ready:false + "
             "push_ready_reason only to LOWER it",
}
STAGE_RECORD_TEMPLATES["reflect"] = {
    "lessons": ["SMOKE tests caught 2 runtime crashes that unit tests missed",
                "Writing the exit contract first ended verification after one probe round"],
}

def _record_type_error(path: str, spec: str, value) -> str | None:
    """Return a 'Record schema:' error for `value` not matching `spec`, else None."""
    def _err(msg: str) -> str:
        return f"Record schema: {path}: {msg}"
    if spec == "str":
        return None if isinstance(value, str) else _err(f"must be a string, got {type(value).__name__}")
    if spec == "bool":
        return None if isinstance(value, bool) else _err(f"must be a boolean, got {type(value).__name__}")
    if spec == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok and not math.isfinite(value):
            return _err(f"must be a finite number, got {value!r}")
        return None if ok else _err(f"must be a number, got {type(value).__name__}")
    if spec in ("dict", "dict[str,str]"):
        if not isinstance(value, dict):
            return _err(f"must be an object, got {type(value).__name__}")
        if spec == "dict[str,str]":
            bad = [k for k, v in value.items() if not isinstance(v, str)]
            if bad:
                return _err(f"values must be strings (bad keys: {bad[:5]})")
        return None
    if spec.startswith("list"):
        return None if isinstance(value, list) else _err(f"must be a list, got {type(value).__name__}")
    return None


def _non_finite_numbers(node, path: str = "", depth: int = 0):
    """Yield (dotted path, value) for every inf / nan float nested in ``node``."""
    if depth > 20:
        return
    if isinstance(node, float) and not math.isfinite(node):
        yield path or "record", node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _non_finite_numbers(v, f"{path}.{k}" if path else str(k), depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _non_finite_numbers(v, f"{path}[{i}]", depth + 1)


def validate_stage_record(stage: str, record: dict) -> tuple[list[str], list[str]]:
    """Validate the judgment fields of a stage RECORD against STAGE_RECORD_SCHEMAS.

    Returns (errors, warnings). Every error starts with ``Record schema: <path>:``
    so structure_errors() maps it to E_RECORD_SCHEMA with a ``record.<path>`` path.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return [f"Record schema: record: must be an object, got {type(record).__name__}"], []
    schema = STAGE_RECORD_SCHEMAS.get(stage)
    if schema is None:
        return [f"Record schema: stage: unknown stage '{stage}'"], []
    status = record.get("status", "completed")
    if status not in ("completed", "skipped"):
        errors.append(f"Record schema: status: must be 'completed' or 'skipped', got {status!r}")
    for field, spec in schema["fields"].items():
        if field in record and record[field] is not None:
            err = _record_type_error(field, spec, record[field])
            if err:
                errors.append(err)
    # Any OTHER numeric value (nested or an unschema'd key) must be finite too:
    # json.loads accepts 1e999 / NaN / Infinity, and json.dumps would write them
    # back as non-standard ``Infinity`` into run.json.
    for path, val in _non_finite_numbers(record):
        if schema["fields"].get(path) != "number":
            errors.append(f"Record schema: {path}: must be a finite number, got {val!r}")
    decisions = record.get("decisions")
    if isinstance(decisions, list):
        for i, d in enumerate(decisions):
            if not isinstance(d, dict):
                errors.append(f"Record schema: decisions[{i}]: must be an object")
                continue
            desc = d.get("description")
            if not isinstance(desc, str) or not desc.strip():
                errors.append(f"Record schema: decisions[{i}].description: required non-empty string")
            cls = d.get("classification")
            if cls not in DECISION_CLASSIFICATIONS:
                errors.append(
                    f"Record schema: decisions[{i}].classification: must be one of "
                    f"{list(DECISION_CLASSIFICATIONS)}, got {cls!r}"
                )
    if status == "skipped":
        for field in schema["required_when_skipped"]:
            val = record.get(field)
            if (not isinstance(val, str) or not val.strip()) and not record.get("skip_justification"):
                errors.append(f"Record schema: {field}: required when status is 'skipped'")
        return errors, warnings
    for field in schema["required"]:
        if field not in record:
            errors.append(f"Record schema: {field}: required for a completed '{stage}' record")
    if stage == "reflect" and isinstance(record.get("lessons"), list):
        lessons = record["lessons"]
        if not lessons:
            errors.append("Record schema: lessons: must contain at least one lesson")
        for i, lesson in enumerate(lessons):
            if not isinstance(lesson, str) or len(lesson.strip()) <= _LESSON_MIN_CHARS:
                errors.append(
                    f"Record schema: lessons[{i}]: each lesson must be a string of more "
                    f"than {_LESSON_MIN_CHARS} chars and actionable"
                )
    if record.get("push_ready") is False:
        reason = record.get("push_ready_reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("Record schema: push_ready_reason: required when lowering push_ready to false")
    return errors, warnings


def _is_mode_ref(ref: str) -> bool:
    """True when a ``kind=test`` ref names a runner mode (``mode:<m>``).

    The ONE predicate both the PLAN check (``_test_ref_shape_error``) and the judge
    (``_judge_test_criterion``) use, on the raw ref: `` mode:x`` (leading space) is
    a path to both, so it cannot pass PLAN as a mode and then be judged as a path."""
    return ref.startswith("mode:")


_TEST_REF_GLOB_CHARS = ("*", "?")  # not "[": bracketed route folders (app/[id]/) are real paths

# Pseudo-files of the runner's synthetic nodeids (pipeline_test._res): ``desktop::tsc``,
# ``desktop::vitest``, ``import::<module>``. The judge matches them by nodeid
# equality, so ``<namespace>::<name>`` is a satisfiable ref even though the part
# before ``::`` has no extension (and ``desktop/`` is a real directory).
_RUNNER_NODEID_NAMESPACES = ("desktop", "import")


def _exit_criteria_roots() -> list[str]:
    """Default roots for the PLAN directory check — the same places
    ``artifact_cli._evidence_roots`` resolves a relative path against: the
    runner's repo override, this repo, the workspace."""
    roots: list[str] = []
    env = os.environ.get("SWARM_TEST_REPO_ROOT")
    if env:
        roots.append(env)
    roots.append(str(Path(__file__).resolve().parents[2]))
    try:
        roots.append(str(_get_workspace()))
    except Exception:  # noqa: BLE001 — a root we cannot resolve is just skipped
        pass
    return roots


def _test_ref_shape_error(ref: str, roots) -> str | None:
    """Why a ``kind=test`` ref can never be satisfied, or None.

    The judge (``_evidence_matches``) compares a ref against ONE result file or
    nodeid, so a directory or a glob never matches anything and the criterion
    stays unmet until a forced commit. Only the path part (before ``::``) is
    checked: brackets and wildcards of a parametrized nodeid are legitimate."""
    if _is_mode_ref(ref):
        return None
    if ref.strip().startswith("mode:"):
        # The judge reads this as a PATH (``_is_mode_ref`` is False), which no
        # result file can equal — whatever the mode name looks like.
        return f"{ref!r} has whitespace before 'mode:'; write it as {ref.strip()!r}"
    head, sep, name = ref.partition("::")
    path = _norm_test_path(head)
    if sep and name.strip() and path in _RUNNER_NODEID_NAMESPACES:
        return None
    fix = ("name each test file (or file::test) as its own criterion, or use "
           "mode:regression for whole-scope coverage")
    if any(ch in path for ch in _TEST_REF_GLOB_CHARS):
        return f"{ref!r} is a glob; a test ref must match one result file — {fix}"
    is_dir = path.endswith("/") or not Path(path).suffix
    if not is_dir:
        for root in roots or ():
            try:
                if (Path(root) / path).is_dir():
                    is_dir = True
                    break
            except OSError:
                continue
    if is_dir:
        return f"{ref!r} names a directory, not a test file — {fix}"
    return None


def validate_exit_criteria(criteria, required: bool, *, roots=None) -> list[str]:
    """Structural check of a PLAN ``exit_criteria`` list (G1).

    ``required`` = strict profile at plan stage-complete. The legacy publish path
    never calls this, so in-flight runs without the field are not broken.
    A ``kind=test`` ref that names a directory or a glob is rejected on EVERY
    profile (it is unsatisfiable on all of them); ``roots`` are where a relative
    ref may live for the directory check (None → ``_exit_criteria_roots()``).
    Errors start with ``Exit criteria: <path>:`` (→ E_EXIT_CRITERIA).
    """
    if criteria is None:
        if required:
            return ["Exit criteria: exit_criteria: missing — a strict profile must declare "
                    "the exit contract at PLAN ([{id, kind, ref, expect}])"]
        return []
    if not isinstance(criteria, list):
        return [f"Exit criteria: exit_criteria: must be a list, got {type(criteria).__name__}"]
    if not criteria and required:
        return ["Exit criteria: exit_criteria: empty — declare at least one checkable criterion"]
    errors: list[str] = []
    seen: set[str] = set()
    for i, c in enumerate(criteria):
        p = f"exit_criteria[{i}]"
        if not isinstance(c, dict):
            errors.append(f"Exit criteria: {p}: must be an object")
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or not cid.strip():
            errors.append(f"Exit criteria: {p}.id: required non-empty string")
        elif cid in seen:
            errors.append(f"Exit criteria: {p}.id: duplicate id {cid!r}")
        else:
            seen.add(cid)
        kind = c.get("kind")
        if kind not in EXIT_CRITERIA_KINDS:
            errors.append(f"Exit criteria: {p}.kind: must be one of {list(EXIT_CRITERIA_KINDS)}, got {kind!r}")
        ref = c.get("ref")
        if kind != "manual" or ref is not None:
            if not isinstance(ref, str) or not ref.strip():
                errors.append(f"Exit criteria: {p}.ref: required non-empty string")
            elif "<" in ref and ">" in ref:
                errors.append(f"Exit criteria: {p}.ref: still a template placeholder ({ref!r}) — name the real target")
            elif kind == "probe" and not _SAFE_REF_NAME_RE.fullmatch(ref):
                errors.append(f"Exit criteria: {p}.ref: probe name must match {_SAFE_REF_NAME_RE.pattern}")
            elif kind == "test":
                if roots is None:
                    roots = _exit_criteria_roots()
                shape = _test_ref_shape_error(ref, roots)
                if shape:
                    errors.append(f"Exit criteria: {p}.ref: {shape}")
        expect = c.get("expect")
        if not isinstance(expect, str) or not expect.strip():
            errors.append(f"Exit criteria: {p}.expect: required non-empty string")
    return errors


def validate_exit_criteria_replan(old_criteria, new_criteria, later_stage: str) -> list[str]:
    """A PLAN re-completed after ``later_stage`` already completed may not weaken
    the exit contract (MED-3): every old criterion id must survive UNCHANGED
    (same kind / ref / expect), and every added criterion must carry a non-empty
    ``reason``. Errors start with ``Exit criteria:`` (→ E_EXIT_CRITERIA)."""
    old = [c for c in (old_criteria if isinstance(old_criteria, list) else [])
           if isinstance(c, dict) and c.get("id")]
    new = [c for c in (new_criteria if isinstance(new_criteria, list) else [])
           if isinstance(c, dict) and c.get("id")]
    new_by_id = {str(c["id"]): c for c in new}
    old_ids = {str(c["id"]) for c in old}
    errors: list[str] = []
    for c in old:
        cid = str(c["id"])
        n = new_by_id.get(cid)
        if n is None:
            errors.append(f"Exit criteria: exit_criteria: replan after '{later_stage}' completed "
                          f"drops criterion {cid!r} — a replan may only ADD criteria")
        elif any(n.get(k) != c.get(k) for k in ("kind", "ref", "expect")):
            errors.append(f"Exit criteria: exit_criteria: replan after '{later_stage}' completed "
                          f"changes criterion {cid!r} — keep it as declared and add a new id instead")
    for i, c in enumerate(new_criteria if isinstance(new_criteria, list) else []):
        if not isinstance(c, dict) or not c.get("id") or str(c["id"]) in old_ids:
            continue
        reason = c.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"Exit criteria: exit_criteria[{i}].reason: criterion {c['id']!r} added "
                          f"after '{later_stage}' completed needs a reason")
    return errors


def default_exit_criteria(work_type: str | None, acceptance_criteria=None) -> list[dict]:
    """Default exit-contract TEMPLATE for a work type (G1). Refs are placeholders the
    model must replace (validate_exit_criteria rejects ``<...>`` refs)."""
    wt = (work_type or "").strip().lower()
    if wt == "bugfix":
        return [
            {"id": "X1", "kind": "test", "ref": "<test_file>::<regression test per counter-example>", "expect": "pass"},
            {"id": "X2", "kind": "test", "ref": "<each touched test file>", "expect": "pass"},
            {"id": "X3", "kind": "probe", "ref": "<probe-name>", "expect": "no_new_findings"},
        ]
    if wt == "refactor":
        return [
            {"id": "X1", "kind": "test", "ref": "<each touched test file>", "expect": "pass"},
            {"id": "X2", "kind": "test", "ref": "mode:verify", "expect": "pass"},
            {"id": "X3", "kind": "mutation", "ref": "<mutation spec>", "expect": "all_killed"},
        ]
    if wt in ("docs", "research"):
        return [{"id": "X1", "kind": "manual", "expect": "done",
                 "ref": "<what a reader checks to confirm the deliverable>"}]
    acs = [a for a in (acceptance_criteria or []) if a] or ["AC1"]
    out = [{"id": f"X{i + 1}", "kind": "test", "expect": "pass",
            "ref": f"<test proving {_extract_ac_id(str(a)) or f'AC{i + 1}'}>"}
           for i, a in enumerate(acs[:10])]
    out.append({"id": f"X{len(out) + 1}", "kind": "test", "ref": "mode:verify", "expect": "pass"})
    return out


# Evidence reports are written by our own runner (pipeline_test.py) and grow with
# the suite: a full verify / regression report already runs to several MB. This
# cap only bounds a pathological file, so it sits far above any real report. (The
# 2 MB _DISK_CHECK_MAX_BYTES is sized for SOURCE files and must not be reused here:
# it made every full-suite report unreadable.)
_EVIDENCE_MAX_BYTES = 128 * 1024 * 1024


def _load_evidence_json(path: Path) -> tuple[dict | None, str | None]:
    """Read one evidence JSON file → ``(data, problem)``.

    ``(dict, None)`` readable · ``(None, None)`` absent · ``(None, reason)`` the file
    EXISTS but cannot be used (too large, unreadable, not JSON, not an object).
    Callers must not treat the third case as "no evidence": a report that exists
    but cannot be read is a failure to verify, never a pass by omission.
    """
    try:
        if not path.exists():
            # A dangling symlink is a report that is there but cannot be read.
            return (None, "dangling symlink") if path.is_symlink() else (None, None)
        if not path.is_file():
            return None, "not a regular file"
        size = path.stat().st_size
        if size > _EVIDENCE_MAX_BYTES:
            return None, f"{size} bytes exceeds the {_EVIDENCE_MAX_BYTES}-byte evidence cap"
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"unreadable: {exc.__class__.__name__}"
    except (ValueError, RecursionError):
        return None, "not valid JSON"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def _read_evidence_json(path: Path) -> dict | None:
    """Read one evidence JSON file (C2/C3 shape, schema 1). None when absent or
    unusable — use ``_load_evidence_json`` where the two must be told apart."""
    return _load_evidence_json(path)[0]


def _iso_utc(ts: float) -> str:
    """``finished_at``-compatible stamp (runner format: ``YYYY-MM-DDTHH:MM:SSZ``)."""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_test_evidence(run_dir: Path) -> list[dict]:
    """All C2 ``evidence/tests/latest-<mode>.json`` reports, NEWEST first
    (by ``finished_at``, falling back to file mtime).

    A report that exists but cannot be read is NOT dropped: it is returned as a
    placeholder ``{"mode", "unreadable": reason, "counts": {}, ...}`` stamped with the
    file's mtime, so every consumer sees a report it cannot verify (never green,
    never "absent") instead of silently judging an older report in its place.
    """
    tests_dir = Path(run_dir) / "evidence" / "tests"
    found: list[tuple[str, float, dict]] = []
    if tests_dir.is_dir():
        for p in tests_dir.glob("latest-*.json"):
            mode = p.stem[len("latest-"):]
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            data, problem = _load_evidence_json(p)
            if data is None:
                if problem is None:
                    continue  # vanished between glob and read — genuinely absent
                data = {"mode": mode, "unreadable": problem, "counts": {},
                        "results": [], "failures": [], "finished_at": _iso_utc(mtime)}
            data.setdefault("mode", mode)
            found.append((str(data.get("finished_at") or ""), mtime, data))
    found.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [d for _, _, d in found]


def unreadable_evidence(evidence: list | None) -> list[str]:
    """``latest-<mode>.json: reason`` for every placeholder in ``evidence``."""
    return [f"latest-{ev.get('mode')}.json: {ev['unreadable']}" for ev in (evidence or [])
            if isinstance(ev, dict) and ev.get("unreadable")]


def _norm_test_path(p: str) -> str:
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _path_eq(a: str, b: str) -> bool:
    a, b = _norm_test_path(a), _norm_test_path(b)
    return bool(a and b) and (a == b or a.endswith("/" + b) or b.endswith("/" + a))


def _evidence_matches(ref: str, result: dict) -> bool:
    nodeid = str(result.get("nodeid") or "")
    if "::" in ref:
        return _path_eq(ref, nodeid)
    rfile = str(result.get("file") or nodeid.split("::")[0])
    return _path_eq(ref, rfile)


_ACCEPTED_FAILURE_CLASSES = ("pre-existing", "flaky")


def _evidence_failure_ok(f: object) -> bool:
    """One failure the runner accepts as NON-blocking — the single SSOT mirror of
    ``pipeline_test._exit_code``: classified pre-existing (fails at the HEAD baseline
    too) or flaky, OR a GLOBAL wall-clock deadline timeout (classification ``timeout``
    with ``deadline`` set — a load/capacity signal, re-run and never cached). A per-test
    hang (``outcome == "timeout"`` with ``deadline`` falsey) is a real defect and blocks,
    as does every other class. Fails closed on a malformed / deadline-less timeout."""
    if not isinstance(f, dict):
        return False
    if f.get("classification") == "timeout":
        return f.get("outcome") == "timeout" and bool(f.get("deadline"))
    return f.get("classification") in _ACCEPTED_FAILURE_CLASSES and f.get("outcome") != "timeout"


def _evidence_green(ev: dict) -> bool:
    """A ``mode:<m>`` report is green when something passed and every failure is one the
    runner itself accepts (``_evidence_failure_ok`` — the mirror of
    ``pipeline_test._exit_code``). A runner error, a per-test hang, or more
    failed/errored/timed-out results than failure records is not."""
    counts = ev.get("counts") if isinstance(ev.get("counts"), dict) else {}
    if int(counts.get("passed") or 0) <= 0:
        return False
    if ev.get("runner_errors"):
        return False
    failures = list(ev.get("failures") or [])
    # Every failing/errored/timed-out result needs a failure record to judge; a short
    # list hides a verdict. timeouts are counted here now, because a deadline timeout is
    # an acceptable record rather than an automatic reject.
    if len(failures) < int(counts.get("failed") or 0) + int(counts.get("errors") or 0) \
            + int(counts.get("timeouts") or 0):
        return False
    return all(_evidence_failure_ok(f) for f in failures)


_EVIDENCE_DIGEST_LIMIT = 8 * 1024 * 1024  # mirrors pipeline_test._file_digest


def _current_digest(path: Path) -> str:
    """The digest pipeline_test records for a file (sha256 / big:<size>:<mtime> / missing)."""
    try:
        st = path.stat()
        if st.st_size > _EVIDENCE_DIGEST_LIMIT:
            return f"big:{st.st_size}:{st.st_mtime_ns}"
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def evidence_digests(ev) -> dict | None:
    """{repo-rel path: digest} a C2 report recorded (``tree_digest``, else
    ``changed_files``), or None for an older report that carries neither."""
    if not isinstance(ev, dict):
        return None
    for key in ("tree_digest", "changed_files"):
        d = ev.get(key)
        if isinstance(d, dict) and d:
            return {str(k): str(v) for k, v in d.items()}
    return None


def evidence_freshness(ev, files_touched, roots=()) -> dict:
    """Is ``ev`` newer than the current content of the run's files_touched?

    Each files_touched entry that the report digested is re-hashed now (absolute
    entries in place; relative ones under the first of ``roots`` where the file
    exists). A changed digest means the tests ran BEFORE a later edit.
    status: ``stale`` (≥1 mismatch) | ``fresh`` (≥1 checked, none changed) |
    ``unknown`` (nothing comparable) | ``undigested`` (older report format) |
    ``unreadable`` (``ev`` is a load_test_evidence placeholder for a report that
    exists but cannot be read).
    """
    if isinstance(ev, dict) and ev.get("unreadable"):
        return {"status": "unreadable", "stale_files": [], "checked": 0}
    digests = evidence_digests(ev)
    if digests is None:
        return {"status": "undigested", "stale_files": [], "checked": 0}
    stale: list[str] = []
    checked = 0
    for f in files_touched if isinstance(files_touched, list) else []:
        if not isinstance(f, str) or not f.strip():
            continue
        key = next((k for k in digests if _path_eq(k, f)), None)
        if key is None:
            continue
        p = Path(f.strip())
        if p.is_absolute():
            located = p
        else:
            located = next((Path(r) / key for r in roots if r and (Path(r) / key).exists()), None)
            if located is None:
                if digests[key] == "missing":
                    checked += 1  # still absent — consistent with the report
                continue  # cannot locate the file → cannot judge it
        checked += 1
        if _current_digest(located) != digests[key]:
            stale.append(key)
    status = "stale" if stale else ("fresh" if checked else "unknown")
    return {"status": status, "stale_files": stale, "checked": checked}


def evidence_regression_modes(evidence: list | None) -> list[str]:
    """Modes of every report carrying a regression-classified failure."""
    return [str(ev.get("mode")) for ev in (evidence or [])
            if isinstance(ev, dict) and (evidence_regressions(ev) or 0) > 0]


def _judge_test_criterion(ref: str, evidence: list[dict], stale_of=None) -> tuple[bool, str]:
    """``stale_of(ev) -> bool``: optional freshness probe; a pass judged on a
    report that predates later edits does not count."""
    def _stale(ev) -> bool:
        return bool(stale_of and stale_of(ev))
    if _is_mode_ref(ref):
        mode = ref[len("mode:"):].strip()
        idx = next((i for i, e in enumerate(evidence) if e.get("mode") == mode), None)
        if idx is None:
            return False, f"no latest-{mode}.json evidence"
        ev = evidence[idx]
        if ev.get("unreadable"):
            return False, f"latest-{mode}.json exists but is unreadable ({ev['unreadable']})"
        newer_bad = unreadable_evidence(evidence[:idx])
        if newer_bad:
            # A newer report that cannot be read may carry a regression.
            return False, f"newer runner evidence is unreadable {newer_bad}"
        newer_reg = evidence_regression_modes(evidence[:idx])
        if newer_reg:
            return False, f"newer runner evidence ({newer_reg}) reports a regression"
        if _stale(ev):
            return False, f"latest-{mode}.json: test evidence predates later edits"
        return (_evidence_green(ev),
                f"latest-{mode}.json counts={ev.get('counts')}")
    for ev in evidence:  # newest first — the newest report that mentions ref decides
        if ev.get("unreadable"):
            # It may mention ref (and fail it); an older report cannot speak for it.
            return False, (f"latest-{ev.get('mode')}.json is newer and unreadable "
                           f"({ev['unreadable']}) — cannot judge {ref}")
        hits = [r for r in (ev.get("results") or []) if isinstance(r, dict) and _evidence_matches(ref, r)]
        if not hits:
            continue
        failing = [r for r in hits if r.get("outcome") in _FAILING_OUTCOMES]
        passed = [r for r in hits if r.get("outcome") == "passed"]
        ok = bool(passed) and not failing
        if ok and _stale(ev):
            return False, f"{ev.get('mode')} evidence for {ref}: test evidence predates later edits"
        return ok, (f"{ev.get('mode')} evidence: {len(passed)} passed, "
                    f"{len(failing)} failing for {ref}")
    return False, f"no test evidence mentions {ref}"


def judge_exit_criteria(criteria, run_dir: Path, record: dict | None = None, *,
                        files_touched=None, roots=()) -> dict:
    """Judge each G1 exit criterion against runner evidence under ``run_dir``.

    test     → newest C2 report mentioning the nodeid/file: ≥1 passed, none failed
               (``mode:<m>`` refs: latest-<m>.json green by the runner's own exit
               rule, every failure pre-existing/flaky, AND no newer report
               carries a regression-classified failure). With ``files_touched``,
               a pass from a report whose digests no longer match the files'
               current content is UNMET ("test evidence predates later edits");
               a report without digests is judged as before plus a ``warnings`` entry.
               A report that exists but cannot be read (load_test_evidence
               placeholder) never passes: as the judged report, or as any report
               newer than the one that would decide.
    probe    → evidence/probes/<ref>.json ``converged`` is true
    mutation → evidence/mutations/latest.json ``all_killed`` is true
    manual   → record.exit_evidence[id] is a non-empty string
    Returns {"met": bool|None, "unmet": [ids], "detail": {id: why}, "count": n};
    met is None when no criteria are declared (legacy run — nothing to judge).
    """
    if not isinstance(criteria, list) or not criteria:
        return {"met": None, "unmet": [], "detail": {}, "count": 0}
    run_dir = Path(run_dir)
    evidence = load_test_evidence(run_dir)
    manual = (record or {}).get("exit_evidence") if isinstance(record, dict) else None
    manual = manual if isinstance(manual, dict) else {}
    unmet: list[str] = []
    detail: dict[str, str] = {}
    warns: list[str] = []
    undigested: set[str] = set()

    def _stale_of(ev) -> bool:
        if files_touched is None:
            return False
        fr = evidence_freshness(ev, files_touched, roots)
        if fr["status"] == "undigested":
            undigested.add(str(ev.get("mode")))
        return fr["status"] == "stale"

    for i, c in enumerate(criteria):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or f"#{i}")
        kind = c.get("kind")
        ref = str(c.get("ref") or "")
        if kind == "test":
            ok, why = _judge_test_criterion(ref, evidence, _stale_of)
        elif kind == "probe":
            if not _SAFE_REF_NAME_RE.fullmatch(ref):
                ok, why = False, "unsafe probe ref"
            else:
                pr, bad = _load_evidence_json(run_dir / "evidence" / "probes" / f"{ref}.json")
                ok = bool(pr and pr.get("converged") is True)
                why = ("probe converged" if ok else "probe not converged" if pr
                       else f"probe evidence unreadable ({bad})" if bad else "no probe evidence")
        elif kind == "mutation":
            mu, bad = _load_evidence_json(run_dir / "evidence" / "mutations" / "latest.json")
            ok = bool(mu and mu.get("all_killed") is True and mu.get("mutations"))
            why = ("all mutations killed" if ok else "surviving mutation(s)" if mu
                   else f"mutation evidence unreadable ({bad})" if bad else "no mutation evidence")
        elif kind == "manual":
            val = manual.get(cid)
            ok = isinstance(val, str) and bool(val.strip())
            why = "manual evidence recorded" if ok else f"record.exit_evidence['{cid}'] missing"
        else:
            ok, why = False, f"unknown kind {kind!r}"
        detail[cid] = why
        if not ok:
            unmet.append(cid)
    out = {"met": not unmet, "unmet": unmet, "detail": detail, "count": len(criteria)}
    if undigested:
        warns.append(f"test evidence {sorted(undigested)} carries no tree_digest/changed_files "
                     "digests — could not verify it postdates the last edit")
    if warns:
        out["warnings"] = warns
    return out


def evidence_regressions(ev: dict | None) -> int | None:
    """Count of C2 failures classified ``regression`` (None when no evidence)."""
    if not isinstance(ev, dict):
        return None
    return sum(1 for f in (ev.get("failures") or [])
               if isinstance(f, dict) and f.get("classification") == "regression")


def push_ready_upper_bound(
    artifact: dict, profile: str, *, test_evidence: dict | None = None,
    test_artifact: dict | None = None, exit_result: dict | None = None,
    all_test_evidence: list | None = None, evidence_fresh: dict | None = None,
) -> tuple[bool, list[str]]:
    """Compute the DELIVER push_ready UPPER BOUND from gate results (F2).

    True only when every gate holds: tests pass (runner evidence wins, else the
    test/deliver artifact; evidence needs counts.passed > 0, and an explicit
    ``quality.tests_pass: false`` always counts), no regression-classified
    failure in ANY latest-*.json report (``all_test_evidence``), the evidence is
    not older than the last edit (``evidence_fresh`` from evidence_freshness),
    no blocking finding (_blocked_findings — the same SSOT as the publish +
    completion gates), completion_audit.all_green, zero regressions, meta_review
    without a HIGH, no report that exists but cannot be read (load_test_evidence
    placeholder), and (strict profiles) every declared exit criterion met.
    Returns (bound, reasons-it-is-false).
    """
    artifact = artifact if isinstance(artifact, dict) else {}
    reasons: list[str] = []
    quality = _safe_dict(artifact.get("quality"))
    if isinstance(test_evidence, dict):
        bad = [f for f in (test_evidence.get("failures") or [])
               if not _evidence_failure_ok(f)]
        counts = _safe_dict(test_evidence.get("counts"))
        if bad:
            reasons.append(f"tests: {len(bad)} blocking failure(s) in runner evidence")
        elif int(counts.get("passed") or 0) <= 0:
            reasons.append("tests: runner evidence records no passing test (counts.passed == 0)")
        if quality.get("tests_pass") is False:
            reasons.append("tests: quality.tests_pass is false")
    else:
        tests_pass = quality.get("tests_pass")
        if tests_pass is None and isinstance(test_artifact, dict):
            tests_pass = test_artifact.get("passed")
        if tests_pass is not True:
            reasons.append("tests: no evidence that tests pass (quality.tests_pass / test.passed)")
    ar = _safe_dict(artifact.get("adversarial_review"))
    blocked = _blocked_findings(ar.get("findings", []))
    if blocked:
        reasons.append(f"findings: {len(blocked)} unresolved HIGH / MEDIUM(conf>={CONFIDENCE_GATE_THRESHOLD})")
    if _safe_dict(artifact.get("completion_audit")).get("all_green") is not True:
        reasons.append("completion_audit.all_green is not true")
    regs = [quality.get("regressions"), evidence_regressions(test_evidence)]
    if isinstance(test_artifact, dict):
        regs.append(test_artifact.get("regressions"))
    if any(isinstance(r, (int, float)) and r > 0 for r in regs):
        reasons.append(f"regressions > 0 ({[r for r in regs if r]})")
    unreadable = unreadable_evidence(list(all_test_evidence or [])
                                     + ([test_evidence] if isinstance(test_evidence, dict) else []))
    if unreadable:
        reasons.append(f"tests: runner evidence exists but is unreadable {sorted(set(unreadable))}")
    reg_modes = evidence_regression_modes(all_test_evidence)
    if reg_modes:
        reasons.append(f"regression-classified failure in runner evidence {reg_modes}")
    if isinstance(evidence_fresh, dict) and evidence_fresh.get("status") == "stale":
        reasons.append("test evidence predates later edits "
                       f"({evidence_fresh.get('stale_files')})")
    mr = artifact.get("meta_review")
    if isinstance(mr, dict):
        risks = [r for r in _safe_list(mr.get("risks")) if isinstance(r, dict)
                 and str(r.get("severity", "")).upper() in ("HIGH", "CRITICAL")
                 and not r.get("resolved")]
        if risks or str(mr.get("verdict", "")).upper() in ("HIGH", "BLOCK", "FAIL"):
            reasons.append("meta_review carries a HIGH")
    if not is_relaxed_profile(profile) and isinstance(exit_result, dict) \
            and exit_result.get("met") is False:
        reasons.append(f"exit criteria unmet: {exit_result.get('unmet')}")
    return (not reasons), reasons


# (code, regex, path) — first match wins, so specific shapes precede general ones.
# `path` is a literal, or "{name}" formatted from the regex's named groups.
_ERROR_SHAPES: list[tuple[str, "re.Pattern[str]", str]] = [
    ("E_INPUT", re.compile(r"^Input:"), ""),
    ("E_STAGE_NOT_OPEN", re.compile(r"^Stage not open:"), "stage_timing"),
    ("E_RECORD_SCHEMA", re.compile(r"^Record schema: (?P<p>[^:]+):"), "record.{p}"),
    ("E_EXIT_CRITERIA", re.compile(r"^Exit criteria: (?P<p>[^:]+):"), "{p}"),
    ("E_DEPTH_MISSING", re.compile(r"^Missing required field '(?P<p>[^']+)' — depth validation"), "{p}"),
    ("E_MISSING_FIELD", re.compile(r"^Missing required field '(?P<p>[^']+)' for stage"), "{p}"),
    ("E_SCHEMA", re.compile(r"^Schema violation: '[^']+' artifact missing required field '(?P<p>[^']+)'"), "{p}"),
    ("E_DEPTH_MISSING", re.compile(r"^Missing '(?P<p>[^']+)' — required for depth"), "{p}"),
    ("E_EMPTY_PATTERNS", re.compile(r"^'(?P<p>[^']+)' is empty — must contain"), "{p}"),
    ("E_DEPTH_TYPE", re.compile(r"^'(?P<p>[^']+)' must be a dict carrying"), "{p}"),
    ("E_LITMUS", re.compile(r"^Litmus gate missing"), "litmus_gate"),
    ("E_LITMUS", re.compile(r"^Litmus (?:gate )?evidence"), "litmus_gate.evidence"),
    ("E_LITMUS", re.compile(r"^Litmus gate hf_checked"), "litmus_gate.hf_checked"),
    ("E_LITMUS", re.compile(r"^Litmus BORDERLINE"), "litmus_gate.weak_areas"),
    ("E_LITMUS", re.compile(r"^Litmus"), "litmus_gate.verdict"),
    ("E_STAGE_ORDER", re.compile(r"^Stage order violation"), "stage"),
    ("E_PROFILE", re.compile(r"^Profile violation|is not in the '[^']+' profile"), "stage"),
    ("E_ARTIFACT_MISSING", re.compile(r"^No artifact published for|could not be loaded"), "artifact"),
    ("E_ADVERSARIAL_TIER", re.compile(r"^adversarial_review\.profile_tier="), "adversarial_review.profile_tier"),
    ("E_ADVERSARIAL_SPAWN", re.compile(r"^adversarial_review\.spawned"), "adversarial_review.spawned"),
    ("E_RED_TEAM_REQUIRED", re.compile(r"Red Team required:"), "adversarial_review.red_team_dispatched"),
    ("E_BLOCKING_FINDINGS", re.compile(r"unresolved blocking finding"), "adversarial_review.findings"),
    ("E_COMPLETION_AUDIT", re.compile(r"^completion_audit"), "completion_audit"),
    ("E_META_REVIEW", re.compile(r"^meta_review missing"), "meta_review"),
    ("E_CONVERGENCE", re.compile(r"^convergence"), "convergence"),
    ("E_PUSH_READY", re.compile(r"^Push-ready gate|^Confidence gate"), "quality.push_ready"),
    ("E_TEST_LAYERS", re.compile(r"^TEST "), "layers"),
    ("E_AC_COVERAGE", re.compile(r"ac_coverage|^AC not covered in BUILD"), "ac_coverage"),
    ("E_ROUTING", re.compile(r"must consume '[^']+' artifact"), "record.consumed_artifacts"),
    ("E_SKIP", re.compile(r"skip_reason|skip_justification|Anti-rationalization"), "record.skip_reason"),
    ("E_INTEGRATION_TRACE", re.compile(r"^Integration trace"), "integration_trace.checked"),
    ("E_SMOKE", re.compile(r"smoke_tests=0|^SMOKE step"), "tdd.smoke_tests"),
    ("E_REPORT_MISSING", re.compile(r"REPORT\.md not found"), "REPORT.md"),
    ("E_VALIDATOR_ERRORED", re.compile(r"ERRORED|crashed", re.I), ""),
]
_STAGE_PREFIX_RE = re.compile(r"^\[(?P<stage>[a-z_]+)\]\s*")


def _template_at(stage: str, path: str):
    """The template snippet at a dotted ``path`` (``[i]`` → first element), or None.
    ``record.*`` paths resolve against STAGE_RECORD_TEMPLATES."""
    if not path:
        return None
    if path.startswith("record."):
        node, parts = STAGE_RECORD_TEMPLATES.get(stage, {}), path[len("record."):]
    else:
        node, parts = STAGE_TEMPLATES.get(stage, {}), path
    if not node and parts.startswith("exit_criteria"):
        node = STAGE_TEMPLATES.get("plan", {})
    for raw in parts.split("."):
        key = raw.split("[")[0]
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return None
        if "[" in raw and isinstance(node, list):
            node = node[0] if node else None
    return copy.deepcopy(node)


def _value_at(data, path: str):
    """Best-effort value at a dotted path in ``data`` (``_MISSING`` if absent)."""
    node = data
    for raw in path.split("."):
        key = raw.split("[")[0]
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
        m = re.search(r"\[(\d+)\]", raw)
        if m and isinstance(node, list):
            idx = int(m.group(1))
            if idx >= len(node):
                return _MISSING
            node = node[idx]
    return node


_MISSING = object()


def structure_errors(stage: str | None, errors: list[str], data: dict | None = None,
                     record: dict | None = None) -> list[dict]:
    """A3: map legacy validator message strings to structured errors.

    Each → ``{code, path, message, got?, expected?}``. ``expected`` is the stage
    template's snippet at ``path`` so the error itself shows the fix; ``got`` is the
    submitted value at that path when ``data``/``record`` is given. A leading
    ``[stage] `` prefix (run-update completion errors) selects that stage's
    template. Unmatched shapes fall back to code ``E_VALIDATION``.
    """
    out: list[dict] = []
    for msg in errors or []:
        text = str(msg)
        st = stage or ""
        m = _STAGE_PREFIX_RE.match(text)
        if m:
            st, text = m.group("stage"), text[m.end():]
        code, path = "E_VALIDATION", ""
        for c, rx, ptmpl in _ERROR_SHAPES:
            mm = rx.search(text)
            if mm:
                code = c
                path = ptmpl.format(**mm.groupdict()) if "{" in ptmpl else ptmpl
                break
        item: dict = {"code": code, "path": path, "message": str(msg)}
        expected = _template_at(st, path)
        if expected is not None:
            item["expected"] = expected
        src, sub = (record, path[len("record."):]) if path.startswith("record.") else (data, path)
        if path and isinstance(src, dict):
            got = _value_at(src, sub)
            if got is not _MISSING:
                item["got"] = got
        out.append(item)
    return out


def validate_stage_state(
    project: str, run_id: str, run_state: dict, stage: str,
    artifact_data: dict | None, artifact_id: str | None = None,
) -> dict[str, Any]:
    """Run validate() — the single implementation of the per-stage checks —
    against an IN-MEMORY candidate ``run_state`` (+ candidate artifact) instead of
    disk. Nothing is read from or written to run.json for (project, run_id) while
    the override is active; upstream artifacts still resolve from the manifest.
    The caller's candidate stage record must carry ``artifact_id`` when an
    artifact is supplied.
    """
    artifacts = {}
    if artifact_id and isinstance(artifact_data, dict):
        artifacts[artifact_id] = copy.deepcopy(artifact_data)
    token = _STATE_OVERRIDE.set({
        "project": project, "run_id": run_id,
        "run": copy.deepcopy(run_state), "artifacts": artifacts,
    })
    try:
        return validate(project, run_id, stage)
    finally:
        _STATE_OVERRIDE.reset(token)


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
            stage_name = stage_rec.get("stage", "?")
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
