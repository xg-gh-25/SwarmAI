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

The 8 invariant checks:
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
                              (BLOCK — catches AttributeError hidden by mocks)
                          8b. REVIEW: integration_trace.checked > 0
                              (BLOCK — ensures wiring verification was done)
                          8c. REVIEW: ux_review when frontend files in changeset
                              (WARN — ensures UX checklist on UI changes)
                          8d. REVIEW: findings_count required for large changesets
                              (BLOCK — prevents skipped/empty reviews on >3 code
                              files or >10 tests. Review must report findings.)

Public symbols:
- ``main``              — CLI entry point
- ``validate``          — Core validation logic (testable without CLI)
- ``check_ddd_consistency`` — Standalone DDD cross-doc check (testable without pipeline)
- ``check_ddd_staleness``  — Detect runs whose DDD docs changed since evaluation
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# Add parent directory for core imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipeline_profiles import get_profile_stages


# ---------------------------------------------------------------------------
# Stage artifact schemas — required fields produce BLOCK, recommended produce WARN
# ---------------------------------------------------------------------------

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
        "required": ["files_changed"],
        "recommended": ["commits", "diff_summary", "tdd"],
        # tdd.smoke_tests must be > 0 when files_changed > 1 (Check 8)
    },
    "review": {
        "required": ["approved", "integration_trace"],
        "recommended": ["findings", "security_findings", "ux_review"],
    },
    "test": {
        "required": ["passed"],
        "recommended": ["failed", "fixed", "coverage"],
    },
    "deliver": {
        "required": ["title", "status"],
        "recommended": ["confidence_score", "decisions", "attention_flags", "report_path"],
    },
    # reflect has no artifact — skip schema check
}

# Stages that don't require an artifact
NO_ARTIFACT_STAGES = {"reflect"}

# Stages where decisions are optional (informational stages)
DECISION_OPTIONAL_STAGES = {"reflect", "deliver"}

# Code file extensions for changeset analysis
_CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".sh"}


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def _get_workspace() -> Path:
    import os
    ws = os.environ.get("SWARM_WORKSPACE", str(Path.home() / ".swarm-ai" / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def _load_run(project: str, run_id: str) -> dict[str, Any] | None:
    """Load a pipeline run from .artifacts/runs/<run_id>/run.json."""
    ws = _get_workspace()
    run_file = ws / "Projects" / project / ".artifacts" / "runs" / run_id / "run.json"
    if not run_file.exists():
        return None
    return json.loads(run_file.read_text())


def _load_artifact_data(project: str, run_id: str, artifact_id: str) -> dict[str, Any] | None:
    """Load artifact data file from the run directory or top-level .artifacts/."""
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
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


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
    for doc_name in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
        doc_path = project_dir / doc_name
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
    missing = [d for d in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")
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
# Core validation
# ---------------------------------------------------------------------------

def validate(project: str, run_id: str, stage: str) -> dict[str, Any]:
    """Validate a pipeline stage against 7 structural invariants.

    Returns a result dict with:
        valid: bool — False if any BLOCK errors
        stage: str — the validated stage
        errors: list[str] — BLOCK-level violations
        warnings: list[str] — informational issues
        checks_passed: int
        checks_total: int
    """
    errors: list[str] = []
    warnings: list[str] = []
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
            "checks_passed": 0,
            "checks_total": checks_total,
        }

    # --- Check 1: Stage order ---
    if _check_stage_order(stage, profile, stages_list):
        checks_passed += 1
    else:
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
    if _check_artifact_exists(stage, stage_record):
        checks_passed += 1
    else:
        if stage not in NO_ARTIFACT_STAGES:
            errors.append(
                f"No artifact published for '{stage}' — "
                f"artifact_id is missing or empty in stage record"
            )
        else:
            # Shouldn't reach here, but be safe
            checks_passed += 1

    # --- Check 3: Artifact schema ---
    schema_result = _check_artifact_schema(stage, stage_record, project, run_id)
    if schema_result["passed"]:
        checks_passed += 1
    errors.extend(schema_result.get("errors", []))
    warnings.extend(schema_result.get("warnings", []))

    # --- Check 4: Decision logged (WARN only — doesn't block) ---
    if _check_decision_logged(stage, stage_record):
        checks_passed += 1
    else:
        checks_passed += 1  # Warnings don't reduce checks_passed
        if stage not in DECISION_OPTIONAL_STAGES:
            warnings.append(
                f"No decisions classified for '{stage}' — "
                f"classify at least one decision (mechanical/taste/judgment)"
            )

    # --- Check 5: Budget recorded (WARN only — doesn't block) ---
    if _check_budget_recorded(stage_record):
        checks_passed += 1
    else:
        checks_passed += 1  # Warnings don't reduce checks_passed
        warnings.append(
            f"token_cost is 0 for '{stage}' — "
            f"estimate the token cost for budget calibration"
        )

    # --- Check 6: Profile respected ---
    if _check_profile_respected(stage, profile):
        checks_passed += 1
    else:
        errors.append(
            f"Profile violation: '{stage}' is not in the '{profile}' profile. "
            f"Expected stages: {get_profile_stages(profile)}"
        )

    # --- Check 7: DDD cross-document consistency (WARN only) ---
    # Runs on evaluate stage — that's when DDD docs are first consulted.
    # On other stages, auto-pass (DDD was already validated at evaluate).
    if stage == "evaluate":
        # Build context text from the evaluation artifact for cross-check
        artifact_id = stage_record.get("artifact_id")
        context_text = None
        if artifact_id:
            artifact_data = _load_artifact_data(project, run_id, artifact_id)
            if artifact_data:
                # Combine key fields for non-goal cross-reference
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
    else:
        checks_passed += 1  # Auto-pass for non-evaluate stages

    # --- Check 8: Smoke tests executed (WARN, build stage only) ---
    # When build touches >1 file, smoke tests must exercise new code paths
    # with real objects (not mocks) to catch AttributeError/NameError.
    # This check prevented 2 HIGH findings in the RecallEngine activation
    # where MagicMock masked a missing attribute on SessionUnit.
    if stage == "build":
        smoke_ok = True
        artifact_id = stage_record.get("artifact_id")
        if artifact_id:
            artifact_data = _load_artifact_data(project, run_id, artifact_id)
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
    elif stage == "review":
        # Check 8b: Integration trace must be present in review artifact
        trace_ok = True
        artifact_id = stage_record.get("artifact_id")
        if artifact_id:
            artifact_data = _load_artifact_data(project, run_id, artifact_id)
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
        if has_frontend and artifact_id and artifact_data:
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
        # A 100+ line diff with 0 review findings means the review was likely skipped.
        # This is a BLOCK: the orchestrator must produce at least a review artifact
        # with findings_count (even if 0) AND explain why 0 is correct.
        if build_stage and build_stage.get("artifact_id"):
            build_art = _load_artifact_data(project, run_id, build_stage["artifact_id"])
            if build_art:
                tdd = build_art.get("tdd", {})
                # Estimate diff size from test count + file count as proxy
                tests_gen = tdd.get("tests_generated", 0) if isinstance(tdd, dict) else 0
                code_files = [
                    f for f in build_art.get("files_changed", [])
                    if any(f.endswith(ext) for ext in _CODE_EXTS)
                ]
                is_large_changeset = len(code_files) > 3 or tests_gen > 10

                if is_large_changeset and artifact_id:
                    review_art = _load_artifact_data(project, run_id, artifact_id)
                    if review_art:
                        findings_count = review_art.get("findings_count", -1)
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
                elif is_large_changeset and not artifact_id:
                    errors.append(
                        f"Review completeness: build touched {len(code_files)} code files "
                        f"but REVIEW stage has no artifact_id. The review was skipped entirely."
                    )
    else:
        checks_passed += 1  # Auto-pass for other stages

    return {
        "valid": len(errors) == 0,
        "stage": stage,
        "errors": errors,
        "warnings": warnings,
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

        summary = {
            "run_id": args.run_id,
            "project": args.project,
            "valid": total_errors == 0,
            "stages_checked": len(all_results),
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "results": all_results,
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
