#!/usr/bin/env python3
"""CLI for artifact registry and pipeline run operations.

Called by the agent via bash to discover/publish artifacts and manage
pipeline runs.  Follows the same pattern as ``locked_write.py`` —
a standalone script with no FastAPI dependency.

Usage — Artifact Registry:
    python artifact_cli.py discover --project SwarmAI --types research,alternatives [--full]
    python artifact_cli.py publish --project SwarmAI --type evaluation \\
        --producer s_evaluate --summary "GO" --data '{"roi": 3.2}' [--run-id run_xxx]
    python artifact_cli.py state --project SwarmAI
    python artifact_cli.py advance --project SwarmAI --state think
    python artifact_cli.py learn --project SwarmAI --evaluation-id art_xxx --outcome success
    python artifact_cli.py projects

Usage — Pipeline Runs (stored in .artifacts/runs/<run_id>/):
    python artifact_cli.py run-create --project SwarmAI --requirement "Add feature" [--profile full]
    python artifact_cli.py run-update --project SwarmAI --run-id run_xxx [--stage-json '...'] [--status completed]
    python artifact_cli.py run-get --project SwarmAI [--run-id run_xxx]
    python artifact_cli.py run-budget --project SwarmAI --run-id run_xxx
    python artifact_cli.py run-checkpoint --project SwarmAI --run-id run_xxx --stage build --reason "L2 BLOCK"
    python artifact_cli.py run-history --project SwarmAI [--limit 10]
    python artifact_cli.py run-status [--active-only]
    python artifact_cli.py run-resume --project SwarmAI --run-id run_xxx

Usage — Pipeline Metrics:
    python artifact_cli.py run-metrics --project SwarmAI --run-id run_xxx
    python artifact_cli.py run-analytics --project SwarmAI [--limit 50]

Storage layout:
    Projects/<project>/.artifacts/
        manifest.json                   # global artifact index
        <type>-<date>-<topic>.json      # standalone artifacts (no pipeline)
        runs/
            <run_id>/
                run.json                # pipeline run state
                METRICS.json            # extracted metrics (auto on completion)
                <type>-<date>.json      # artifacts scoped to this run

Public symbols:
- ``main``  — CLI entry point with subcommand dispatch.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path so we can import core modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.artifact_registry import ArtifactRegistry
from core.pipeline_profiles import get_profile_stages

# Fields that `publish --stage` auto-records into a stage stub (see cmd_publish)
# and that a subsequent `run-update --stage-json` finalize must NOT lose to the
# full-record replace. ONLY artifact_id: it is the sole publish-set field that a
# gate reads (the completion/advance artifact-link check). `status` is excluded
# on purpose (finalize upgrades recorded→completed); `auto_recorded` is provenance
# no gate reads. Widen this ONLY when a new publish-set, gate-read field is proven.
_CARRY_FORWARD_FIELDS = frozenset({"artifact_id", "adversarial_review"})


def _get_workspace() -> Path:
    """Resolve workspace root from environment or default."""
    import os
    from config import get_app_data_dir
    ws = os.environ.get("SWARM_WORKSPACE", str(get_app_data_dir() / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def cmd_discover(args, reg: ArtifactRegistry) -> None:
    """Discover active artifacts of given types."""
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    artifacts = reg.discover(args.project, *types)

    if not artifacts:
        print(json.dumps({"artifacts": [], "count": 0}))
        return

    result = []
    for a in artifacts:
        entry = {
            "id": a.id,
            "type": a.type,
            "producer": a.producer,
            "summary": a.summary,
            "file": a.file,
        }
        # Optionally load full data
        if args.full:
            artifact_dir = (
                _get_workspace() / "Projects" / args.project / ".artifacts"
            )
            data_file = artifact_dir / a.file
            if data_file.exists():
                try:
                    entry["data"] = json.loads(
                        data_file.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    pass
        result.append(entry)

    print(json.dumps({"artifacts": result, "count": len(result)}, indent=2))


# NOTE (run_f3975b8b): the `_find_active_run` / `_find_active_runs` helpers were
# DELETED. They existed only to pick "the newest active run" when a caller omitted
# --run-id — i.e. to GUESS which run a publish/validate targets. That guess is the
# root of all cross-run contamination (run_3caef1d3 / run_b9ecb07a): even a single
# active run may belong to a sibling session. The contamination is now eradicated
# structurally — every write/validate path REQUIRES an explicit --run-id and fails
# closed (publish) or skips (advance) when it is absent — so there is no longer any
# code path that needs to enumerate "active runs" to guess a target. Do not
# reintroduce a newest-active selector for a mutate/validate path.


def _as_list(v) -> list:
    """Coerce an agent-supplied stage-json field into a list safe to slice/iterate.

    cmd_run_report reads several fields (alternatives, key_findings, eval_criteria,
    success_criteria) straight from raw stage-json — which agents populate freely
    and which bypasses validate_artifact_data's type checks. A field recorded as a
    truthy scalar (e.g. ``alternatives: 3``) crashed report generation at
    ``alternatives[:4]`` with ``TypeError: 'int' object is not subscriptable``
    (run_932c0991). This guards at the consumer — the layer where the crash occurs.

    - list → returned unchanged
    - non-empty str → ``[str]`` (a single render item; matches the prior
      success_criteria str-guard)
    - anything else (int, dict, None, empty str, tuple, …) → ``[]`` (skip the
      section rather than crash; a scalar in a list-field is malformed by
      definition and its section is purely descriptive)
    """
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v:
        return [v]
    return []


def _append_stage_to_run(
    project: str, run_id: str, stage_record: dict, reg: ArtifactRegistry
) -> None:
    """Append a stage record to run.json (same as run-update --stage-json)."""
    run_file = (
        Path(reg.workspace_root) / "Projects" / project
        / ".artifacts" / "runs" / run_id / "run.json"
    )
    if not run_file.exists():
        return
    data = json.loads(run_file.read_text())
    stages = data.get("stages", [])
    # If the stage already has a record (e.g. Gate-1 wrote a `build` record with
    # gate1_verdict BEFORE publish ran), don't drop the artifact link on the floor
    # — back-fill artifact_id into the existing record. Publish is the ONLY writer
    # of artifact_id; skipping entirely (the old behavior) left the stage
    # permanently unlinked whenever any prior run-update created the record first
    # (run_b7620c6e — surfaced by dogfooding: gate-1-writes-then-publish is the
    # real order, not the publish-first order the unit fixture assumed). Only
    # back-fill when the existing record LACKS it (never clobber an explicit one).
    _existing = next((s for s in stages if s.get("stage") == stage_record["stage"]), None)
    if _existing is not None:
        _incoming_aid = stage_record.get("artifact_id")
        if _incoming_aid and not _existing.get("artifact_id"):
            _existing["artifact_id"] = _incoming_aid
            data["stages"] = stages
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            run_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return
    stages.append(stage_record)
    data["stages"] = stages
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Match the formatting of every other run.json writer (cmd_run_update,
    # cmd_run_create, etc.): indent=2, default ensure_ascii, utf-8. Avoids the
    # same file flipping between raw-UTF8 and \uXXXX escapes depending on which
    # command wrote last (noisy diffs in .artifacts/runs/*/run.json).
    run_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def cmd_publish(args, reg: ArtifactRegistry) -> None:
    """Publish a new artifact. Validates schema when --stage is provided.

    With --stage: runs validate_artifact_data() from pipeline_validator
    (single source of truth). On failure, returns errors + expected template.
    """
    # --data accepts a raw JSON string OR "@PATH" (read a file) OR "@-" (stdin).
    # A JSON value never starts with '@' (object/array/string/number/bool/null),
    # so the '@' prefix is an unambiguous discriminator. Lets large artifact
    # payloads avoid shell-string quoting gymnastics (run_b7620c6e #1).
    _raw = args.data
    if isinstance(_raw, str) and _raw.startswith("@"):
        try:
            if _raw == "@-":
                _raw = sys.stdin.read()
            else:
                _raw = Path(_raw[1:]).expanduser().read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            print(json.dumps({"error": f"Could not read --data source {args.data!r}: {e}"}),
                  file=sys.stderr)
            sys.exit(1)
    try:
        data = json.loads(_raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON data: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Pre-publish validation: check schema + depth BEFORE writing to disk
    # Uses pipeline_validator as single source of truth (no duplicate schemas).
    stage = getattr(args, "stage", None)
    if stage:
        from pipeline_validator import validate_artifact_data, get_stage_schema
        # Determine the profile to validate against. Use the EXPLICIT --run-id's
        # profile if given; otherwise default to "full" (the strictest tier — also
        # the existing failure default). We NEVER guess the run by "newest active"
        # (run_f3975b8b): doing so could validate this artifact against a SIBLING
        # run's profile. A strict-here pass is harmless — validation re-runs at
        # completion against the true run's profile.
        _pub_profile = "full"
        _explicit_run_id = getattr(args, "run_id", None)
        try:
            if _explicit_run_id:
                # The caller NAMED the run — read ITS profile directly from disk
                # regardless of status (running/paused/completed/abandoned). A run
                # that completed between publish calls (resume path) must still
                # validate against its OWN profile, not a default — else a trivial
                # run gets spuriously BLOCKed by full-tier depth checks (Gate-2 #2).
                # Build the path from reg.workspace_root (same root the rest of this
                # function uses) — NOT _resolve_run_file, which keys off the global
                # _get_workspace() and sys.exit(1)s on a miss (would kill the publish).
                _own_file = (
                    Path(reg.workspace_root) / "Projects" / args.project
                    / ".artifacts" / "runs" / _explicit_run_id / "run.json"
                )
                try:
                    _own = json.loads(_own_file.read_text(encoding="utf-8"))
                    _pub_profile = _own.get("profile", "full") or "full"
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    pass  # unknown/typo'd run id → "full" (strict, safe)
            # No explicit --run-id → default to "full" (strictest), NEVER read a
            # run's profile by guessing the active one (run_f3975b8b). Reading even a
            # single active run's profile picks a sibling's by newest, which is the
            # same guess-the-target footgun we eradicated in auto-record. "full" is
            # the safe strict default; the explicit --run-id path above reads the
            # named run's real profile.
        except Exception:
            pass  # Default to "full" if can't determine profile
        errors = validate_artifact_data(stage, data, profile=_pub_profile)
        if errors:
            if getattr(args, "quiet", False):
                # Quiet mode: a SHORT single-line failure (no verbose schema
                # dump). Orchestrators parse this without choking on a multi-KB
                # indented template; re-run without --quiet to see the schema.
                print(json.dumps({
                    "validation_failed": True,
                    "stage": stage,
                    "errors": errors,
                    "hint": f"re-run without --quiet, or `artifact_cli.py schema --stage {stage}`, for the expected schema template",
                }), file=sys.stderr)
            else:
                schema_info = get_stage_schema(stage)
                print(json.dumps({
                    "validation_failed": True,
                    "stage": stage,
                    "errors": errors,
                    "expected_schema": schema_info.get("template", {}),
                }, indent=2), file=sys.stderr)
            sys.exit(1)

    # ── Pollinate delivery gate: mechanical validation ────────────────────
    # When producer is s_pollinate/s_autonomous-pipeline AND stage is deliver,
    # and data contains a content_dir field, run the structural validator.
    # This is the mechanical enforcement that prevents skipping the validator.
    producer = getattr(args, "producer", "") or ""
    if stage == "deliver" and "pollinate" in producer:
        # Auto-discover content_dir: explicit in data > most recent content/*/ dir
        content_dir = data.get("content_dir")
        if not content_dir:
            _pollinate_root = Path(reg.workspace_root) / "Knowledge" / "Pollinate"
            if _pollinate_root.is_dir():
                # Find most recently modified content directory
                _candidates = [d for d in _pollinate_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
                if _candidates:
                    content_dir = str(max(_candidates, key=lambda d: d.stat().st_mtime))
        if content_dir:
            try:
                import importlib.util
                _validator_path = Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts" / "pollinate_validator.py"
                _spec = importlib.util.spec_from_file_location("pollinate_validator", _validator_path)
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                validate_delivery = _mod.validate_delivery
                vresult = validate_delivery(content_dir)
                if not vresult.get("valid", True):
                    _pollinate_fail = {
                        "validation_failed": True,
                        "stage": "deliver",
                        "errors": [f"Pollinate validator: {e}" for e in vresult.get("errors", [])],
                        "hint": "Run: python pollinate_validator.py <content_dir> --json",
                    }
                    # --quiet ⇒ always single-line (no indent) so an orchestrator's
                    # line-based JSON parse never chokes on this failure path either
                    # (adversarial HIGH, run_688b6487 — the schema-fail path was
                    # already quiet-aware; this sibling path was the hole).
                    if getattr(args, "quiet", False):
                        print(json.dumps(_pollinate_fail), file=sys.stderr)
                    else:
                        print(json.dumps(_pollinate_fail, indent=2), file=sys.stderr)
                    sys.exit(1)
            except (ImportError, FileNotFoundError):
                pass  # Validator not available — skip (non-blocking)
            except Exception:
                pass  # Pollinate validator error — non-blocking, skip silently

    run_id = getattr(args, "run_id", None)
    try:
        artifact_id = reg.publish(
            project=args.project,
            artifact_type=args.type,
            data=data,
            producer=args.producer,
            summary=args.summary,
            topic=args.topic or "",
            run_id=run_id,
        )
        result = {"artifact_id": artifact_id, "project": args.project}
        if run_id:
            result["run_id"] = run_id

        # ── Auto-record stage to run.json (eliminates separate run-update call) ──
        # If --stage is provided, append stage record to the target run.
        # Priority: explicit --run-id > auto-discovered active run.
        if stage:
            try:
                target_run_id = run_id  # Explicit --run-id from caller
                if not target_run_id:
                    # NEVER GUESS (run_f3975b8b, XG directive). Earlier this code
                    # tried to "fall back to the newest active run" when --run-id
                    # was omitted, refusing only when 2+ runs were active. That was
                    # still unsafe: even a SINGLE active run may belong to a sibling
                    # session, so auto-recording into it contaminates an unrelated
                    # pipeline (run_3caef1d3 / run_b9ecb07a). The ability to guess a
                    # target run is the root of all cross-run contamination, so we
                    # eradicate it: a --stage publish with NO --run-id ALWAYS fails
                    # closed, regardless of how many runs are active (0, 1, or N).
                    #
                    # The artifact is already published (above) and is NOT lost — the
                    # agent re-runs the publish with an explicit --run-id and the stub
                    # records correctly. All pipeline stage docs already thread
                    # --run-id (run_3caef1d3), so real flows never hit this; only a
                    # forgotten/manual no-run-id publish does — exactly what must be
                    # blocked.
                    #
                    # Surface on STDERR (matching the validation_failed path), exit 3.
                    # The documented orchestrator guard reads STDERR on failure and
                    # feeds STDOUT to json.load(...)['artifact_id'] — printing the
                    # error to STDOUT would hide it AND crash that json.load.
                    refused = {
                        "error": (
                            f"auto-record REFUSED: publish --stage with NO --run-id "
                            f"cannot safely pick a target run in '{args.project}' — "
                            f"any active run may belong to a sibling session. "
                            f"Re-run this publish with an explicit --run-id."
                        ),
                        "artifact_id": artifact_id,
                        "stage": stage,
                    }
                    print(json.dumps(refused), file=sys.stderr)
                    sys.exit(3)
                if target_run_id:
                    # Auto-record is a SAFETY NET, not a substitute for run-update.
                    # It captures the artifact_id so a forgotten run-update never
                    # loses the stage→artifact link. But it deliberately marks the
                    # stage "recorded" (NOT "completed") and omits stage_doc_consumed:
                    #   - The completion gate (run-update --status completed) only
                    #     accepts "completed"/"done", so a stub alone can't close
                    #     the pipeline — the agent must still run-update to mark the
                    #     stage properly complete with stage_doc_consumed + token_cost.
                    #   - This preserves the stage_doc_consumed mechanical gate: the
                    #     stub can't bypass it, because "recorded" != "completed".
                    # run-update REPLACES (not skips) an existing record, so the
                    # later enrichment correctly upgrades the stub.
                    stage_record = {
                        "stage": stage,
                        "status": "recorded",
                        "artifact_id": artifact_id,
                        "auto_recorded": True,
                    }
                    _append_stage_to_run(args.project, target_run_id, stage_record, reg)
                    result["auto_recorded"] = True
                    result["run_id"] = target_run_id
            except Exception as e:
                # Best-effort — don't fail the publish itself. But NEVER fail
                # silently: a swallowed auto-record leaves run.json stages empty
                # and the completion gate blocks with no explanation (the exact
                # failure this fix addresses). Surface it on stderr so the
                # orchestrator sees the signal and can fall back to run-update.
                result["auto_recorded"] = False
                print(json.dumps({
                    "warning": "auto-record stage failed; run.json not updated. "
                               "Record manually via run-update --stage-json.",
                    "stage": stage,
                    "detail": str(e),
                }), file=sys.stderr)

        if getattr(args, "quiet", False):
            # Parse-proof success: ONLY the artifact_id, single line. The
            # auto-record into run.json still happened above (side effect); the
            # orchestrator just doesn't need project/run_id/auto_recorded echoed.
            print(json.dumps({"artifact_id": artifact_id}))
        else:
            print(json.dumps(result))
    except (ValueError, FileNotFoundError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def cmd_schema(args, reg: "ArtifactRegistry | None") -> None:
    """Print a stage's expected artifact schema + template as single-line JSON.

    Read-only. Lets a caller see the required/recommended/depth/template for a
    stage WITHOUT triggering a failed publish (the only way to see it before).
    Reuses pipeline_validator.get_stage_schema as the single source of truth —
    no duplicate schema definitions. Output is single-line (parse-proof, mirrors
    publish --quiet) so orchestrators can json.load it directly. (run_88b9f986)
    """
    from pipeline_validator import get_stage_schema
    info = get_stage_schema(args.stage)
    print(json.dumps({"stage": args.stage, **info}))


def cmd_state(args, reg: ArtifactRegistry) -> None:
    """Get pipeline state for a project."""
    state = reg.get_pipeline_state(args.project)
    print(json.dumps({"project": args.project, "pipeline_state": state}))


def cmd_advance(args, reg: ArtifactRegistry) -> None:
    """Advance pipeline state. Auto-validates if a run is active.

    If a pipeline run exists and the current stage has a record,
    runs the pipeline validator. Refuses to advance on BLOCK errors.
    Warnings are printed but don't block advancement.
    """
    # Auto-validate before advancing (structural enforcement)
    # FAIL-CLOSED: if validator crashes, block advancement.
    # Prior behavior (fail-open) allowed advancement when validator errored,
    # which is the most dangerous failure mode — gate opens on crash.
    try:
        _auto_validate_before_advance(
            args.project, args.state, getattr(args, "run_id", None)
        )
    except SystemExit:
        raise  # Re-raise if validation blocks
    except Exception as e:
        # FAIL-CLOSED: validator crash → block advancement
        print(json.dumps({
            "validation_blocked": True,
            "error": f"Validator crashed: {e}. Fix the validator before advancing.",
        }, indent=2), file=sys.stderr)
        sys.exit(1)

    try:
        reg.advance_pipeline(args.project, args.state)
        print(json.dumps({"project": args.project, "pipeline_state": args.state}))
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def _auto_validate_before_advance(
    project: str, next_state: str, run_id: str | None = None
) -> None:
    """Run pipeline validator on the current stage of the NAMED run before advancing.

    Validates ONLY the run explicitly named by --run-id. If no --run-id is given,
    SKIP the auto-validate entirely (run_f3975b8b): the validator is a best-effort
    pre-advance check, and guessing "the newest active run" project-wide could
    validate/block a SIBLING session's run (the same root contamination this run
    eradicates on the publish path). Skipping a best-effort check is safe; acting
    on the WRONG run is not — and the explicit run-update/publish path with
    --run-id still validates correctly at the completion gate.

    Blocks on errors, warns on warnings. No-op when run-id absent or run not found.
    """
    import subprocess

    # NEVER guess the target run. No --run-id → nothing to validate (the named-run
    # publish path validates at its own gate). This deliberately replaces the old
    # "newest active project-wide" selection (a guess-the-target footgun).
    if not run_id:
        return

    run_file = (
        _get_workspace() / "Projects" / project / ".artifacts" / "runs"
        / run_id / "run.json"
    )
    if not run_file.exists():
        # Typo'd / unknown run-id → nothing to validate. Blocks YOUR advance loudly
        # only if you also can't advance the state machine; never touches another run.
        return
    try:
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    stages = run_data.get("stages", [])

    if not stages:
        return

    # Determine current stage (last completed)
    current_stage = None
    current_record = None
    for s in reversed(stages):
        status = s.get("status", "")
        if status in ("done", "completed"):
            current_stage = s.get("stage", s.get("name"))
            current_record = s
            break

    if not current_stage:
        return

    # Drift guard: a completed stage that produced NO artifact usually means a
    # silently-failed publish (the exact failure this tooling has hit before —
    # publish returns an error to stderr but advance proceeds anyway, leaving
    # state ahead of reality). Warn, don't block. Some stages legitimately
    # produce no artifact (reflect closes the loop; goal_cycle commits
    # incrementally) — exempt them to avoid false-positive noise.
    _ARTIFACTLESS_STAGES = {"reflect", "goal_cycle"}
    if current_record is not None and not current_record.get("artifact_id") \
            and current_stage not in _ARTIFACTLESS_STAGES:
        print(json.dumps({
            "warning": f"stage '{current_stage}' is marked completed but has no "
                       f"artifact_id — its publish may have failed silently. "
                       f"Verify before advancing to '{next_state}'.",
        }), file=sys.stderr)

    # REPORT.md gate: deliver stage MUST produce REPORT.md before advancing.
    # 52% of historical "completed" runs lacked REPORT.md because this was never enforced.
    if current_stage == "deliver":
        # run_f3975b8b: `artifacts_dir` was previously defined by the deleted
        # active-run scan. Now that we validate ONLY the explicitly-named run,
        # build the run dir from the named run_id directly.
        run_dir = (
            _get_workspace() / "Projects" / project / ".artifacts" / "runs" / run_id
        )
        if not (run_dir / "REPORT.md").exists():
            print(json.dumps({
                "validation_blocked": True,
                "stage": "deliver",
                "errors": [
                    "[deliver] REPORT.md not found. DELIVER stage requires generating "
                    "REPORT.md at .artifacts/runs/<RUN_ID>/REPORT.md before advancing. "
                    "Run: artifact_cli.py run-report --project <PROJECT> --run-id <RUN_ID>"
                ],
            }, indent=2), file=sys.stderr)
            sys.exit(1)

    # Run validator
    try:
        validator = Path(__file__).parent / "pipeline_validator.py"
        result = subprocess.run(
            [sys.executable, str(validator), "check",
             "--project", project, "--run-id", run_id, "--stage", current_stage],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent.parent),
        )
        # FAIL-CLOSED on validator crash (Operational review HIGH-8): an
        # UNWRAPPED check that raises makes the validator exit non-zero with
        # EMPTY stdout (traceback on stderr). Without this guard, `if result.stdout`
        # would be False, the whole block would be skipped, and advance would
        # proceed = the hard gate fails OPEN. A crashed validator can NEVER verify
        # a stage, so it must block — matching the TimeoutExpired/JSONDecodeError
        # handlers below and the _CheckGuard hard-fail-closed contract.
        if result.returncode != 0 and not result.stdout.strip():
            raise RuntimeError(
                f"Pipeline validator crashed (exit {result.returncode}, no output) — "
                f"cannot verify stage '{current_stage}'. stderr: {result.stderr.strip()[:500]}"
            )
        if result.stdout:
            validation = json.loads(result.stdout)
            # errored[] (run_55710438): checks that could NOT run, distinct from
            # content failures. Surfaced in the audit + stderr so a blocked
            # advance tells the user WHY (bad content vs broken check).
            errored = validation.get("errored", [])
            if not validation.get("valid", True):
                errors = validation.get("errors", [])
                # Record validation block event in run.json
                _record_validation_event(
                    project, run_id, current_stage,
                    passed=False, errors=errors,
                    warnings=validation.get("warnings", []),
                    errored=errored,
                )
                print(json.dumps({
                    "validation_blocked": True,
                    "stage": current_stage,
                    "errors": errors,
                    "errored": errored,
                }, indent=2), file=sys.stderr)
                sys.exit(1)
            warnings = validation.get("warnings", [])
            if warnings or errored:
                print(json.dumps({
                    "validation_warnings": warnings,
                    "errored_checks": errored,
                }), file=sys.stderr)
    except subprocess.TimeoutExpired:
        # FAIL-CLOSED: validator timeout → block advancement (F3 fix)
        raise RuntimeError("Pipeline validator timed out (>10s) — cannot verify stage")
    except (json.JSONDecodeError, OSError) as exc:
        # FAIL-CLOSED: validator parse error → block advancement (F3 fix)
        raise RuntimeError(f"Pipeline validator produced invalid output: {exc}")


def cmd_learn(args, reg: ArtifactRegistry) -> None:
    """Record outcome of a pipeline run for learning feedback."""
    lessons = [l.strip() for l in (args.lessons or "").split(";") if l.strip()]
    reg.record_outcome(
        project=args.project,
        evaluation_id=args.evaluation_id,
        outcome=args.outcome,
        actual_effort=args.actual_effort,
        lessons=lessons or None,
    )
    print(json.dumps({
        "project": args.project,
        "evaluation_id": args.evaluation_id,
        "outcome": args.outcome,
        "recorded": True,
    }))


def cmd_projects(args, reg: ArtifactRegistry) -> None:
    """List all projects with pipeline status."""
    statuses = reg.list_projects()
    result = [
        {
            "project": s.project,
            "pipeline_state": s.pipeline_state,
            "artifact_count": s.artifact_count,
            "active_artifact_count": s.active_artifact_count,
            "latest_artifact": s.latest_artifact,
        }
        for s in statuses
    ]
    print(json.dumps({"projects": result, "count": len(result)}, indent=2))


# ── Pipeline run management ──────────────────────────────────────────

# Default token budget estimates per stage (CONSERVATIVE — used for run-budget
# should_checkpoint calculation). These are intentionally HIGHER than the
# INSTRUCTIONS.md "Base stage costs" table (which shows typical/observed costs)
# because run-budget must avoid premature checkpoints. The INSTRUCTIONS.md values
# (evaluate=6K, think=10K, plan=8K, build=40K, etc.) are what the agent targets;
# these values are what the budget system uses as worst-case estimates.
# After 5+ completed runs, _calibrated_stage_budget() overrides these with
# historical averages + 20% buffer — making both sets irrelevant.
DEFAULT_STAGE_BUDGETS = {
    "evaluate": 10_000,
    "think": 40_000,
    "plan": 30_000,
    "build": 60_000,
    "review": 25_000,
    "test": 40_000,
    "deliver": 15_000,
    "reflect": 10_000,
}
SESSION_BUDGET = 800_000       # 80% of 1M context window
CHECKPOINT_RESERVE = 50_000    # Reserve for checkpoint handoff


def _pipeline_runs_dir(project: str) -> Path:
    """Get the base runs directory: .artifacts/runs/."""
    return _get_workspace() / "Projects" / project / ".artifacts" / "runs"


def _run_dir(project: str, run_id: str) -> Path:
    """Get directory for a specific run: .artifacts/runs/<run_id>/."""
    return _pipeline_runs_dir(project) / run_id


def _resolve_run_file(project: str, run_id: str) -> Path:
    """Find the run.json file, checking new path (runs/<id>/run.json) then legacy (pipeline-run-<id>.json)."""
    # New path: .artifacts/runs/<run_id>/run.json
    new_path = _run_dir(project, run_id) / "run.json"
    if new_path.exists():
        return new_path

    # Legacy path: .artifacts/pipeline-run-<run_id>.json
    legacy_path = _get_workspace() / "Projects" / project / ".artifacts" / f"pipeline-run-{run_id}.json"
    if legacy_path.exists():
        return legacy_path

    print(json.dumps({"error": f"Pipeline run {run_id} not found"}), file=sys.stderr)
    sys.exit(1)


def _gen_run_id() -> str:
    import uuid
    return f"run_{uuid.uuid4().hex[:8]}"


def _run_tokens(data: dict) -> int:
    """Total tokens for a run = sum of per-stage token_cost.

    There is NO stored `tokens_consumed` field on run.json — run-status COMPUTES
    it as sum(stages[].token_cost) (see lines ~1490/1638). The abandon verdict
    must use the SAME computation, not data.get("tokens_consumed") (always None).
    """
    return sum(s.get("token_cost", 0) or 0 for s in data.get("stages", []) or [])


# Crash auto-checkpoint stamps this exact reason; an INTENTIONAL pause carries a
# real decision reason instead (e.g. "Gate 1 BLOCK", "DELIVERED ...", "ROOT CAUSE
# FALSIFIED"). This string is the primary zombie discriminator.
_CRASH_ZOMBIE_REASON = "session_crash_auto_detected"


def is_terminal_run(data: dict) -> bool:
    """Single source of truth: has this run reached a TERMINAL (finished) state,
    judged by STAGE PROGRESS — not by the top-level `status` string?

    Why stage-based, not status-based: the orphan-transition
    (proactive_intelligence: running->paused + reason=session_crash_auto_detected)
    can flip a run whose STAGES are all done to status='paused' during a session
    refresh. Such a run is FINISHED, not a crash orphan — but every liveness check
    that keys off `status` alone mis-reads it as a resumable crash-zombie and
    re-surfaces it forever (run_bf840159: 6/6 stages incl. reflect completed, yet
    status='paused'+crash-reason → endless auto-resume prompt).

    A run is terminal when EITHER:
      - top-level status is an explicit terminal value, OR
      - a completed 'reflect' or 'deliver' stage exists (the pipeline's own
        end-of-run markers — reaching either means the run delivered).

    NOT terminal just because "every LISTED stage is completed": a paused
    mid-pipeline run only lists the stages done SO FAR and carries a
    `checkpoint.next_stage` pointing at remaining work (e.g. evaluate+think done,
    next_stage=build). Treating that as terminal would silently skip a genuinely
    resumable run. The reflect/deliver marker is the reliable end-of-run signal —
    the pipeline appends those only at the very end.

    Terminal runs are NEVER crash orphans: crash-detect, orphan-transition,
    supersede, and _abandon_verdict must all skip them regardless of `status`.
    """
    if not isinstance(data, dict):
        return False
    status = data.get("status")
    if status in ("completed", "complete", "abandoned", "failed", "cancelled"):
        return True
    # End-of-run stage markers ONLY (see _delivered_by_stages): a completed
    # reflect/deliver means the run produced its deliverable. (Do NOT infer
    # terminal from "all listed stages done" — a mid-pipeline pause satisfies
    # that yet has next_stage remaining.)
    return _delivered_by_stages(data)


def _delivered_by_stages(data: dict) -> bool:
    """True iff the run's STAGE ARRAY shows a completed 'reflect' or 'deliver'
    stage — the pipeline's own end-of-run markers, appended only at the very end.

    This is the ONLY non-circular way to ask "did this run actually deliver?".
    `is_terminal_run` cannot answer it for an abandoned/paused run, because its
    status short-circuit (line above) returns True for status=='abandoned'
    REGARDLESS of stage progress — so a garbage-abandoned empty shell and a
    delivered-then-mislabeled-abandoned run are indistinguishable by
    `is_terminal_run`. Extracted (run_0e68e235) so `_is_garbage_run` and
    `_effective_analytics_status` can key on real delivery, not the status string.
    Behavior-preserving: `is_terminal_run` calls this after its status
    short-circuit, so all its consumers see identical results.
    """
    if not isinstance(data, dict):
        return False
    stages = data.get("stages") or []
    if not isinstance(stages, list) or not stages:
        return False
    return any(
        isinstance(s, dict)
        and s.get("stage") in ("reflect", "deliver")
        and s.get("status") == "completed"
        for s in stages
    )


def _is_garbage_run(raw: dict) -> bool:
    """Single source of truth: is this run GARBAGE that must be excluded from ALL
    pipeline analytics/stats (needs-you, completion_rate denominator, tokens,
    trend, profile_mix, by-project) AND is eligible for retention purge?

    Garbage = a run that NEVER delivered a result AND is in a dead/dead-residue
    state the user can take no action on:
      - status == 'abandoned' (reaper death verdict: crash_zombie /
        crash_residue_empty_shell / superseded_by_* / orphaned_no_resume), OR
      - status == 'paused' with the crash auto-checkpoint reason
        (_CRASH_ZOMBIE_REASON) — a dead session's residue, not a real decision-pause
    ...MINUS any run that actually delivered (`_delivered_by_stages`): the
    "mislabeled-done" class (measured 22/182 abandoned) reached a completed
    reflect/deliver stage and was only stamped abandoned/superseded AFTERWARD.
    Those are real completions — excluding them would UNDERCOUNT delivery
    (Gate-1 skeptic catch). They are recategorized to 'completed' by
    `_effective_analytics_status`, never dropped.

    NOT garbage: completed/complete, cancelled (a real user decision), failed
    (a real signal), running, and genuine (non-crash) decision-pauses. XG
    directive (run_0e68e235): only abandoned + crash-residue-paused are garbage.
    """
    if not isinstance(raw, dict):
        return False
    status = raw.get("status")
    is_crash_paused = (
        status == "paused"
        and (raw.get("checkpoint") or {}).get("reason") == _CRASH_ZOMBIE_REASON
    )
    if status != "abandoned" and not is_crash_paused:
        return False
    # A run that reached deliver/reflect delivered — never garbage.
    return not _delivered_by_stages(raw)


def _effective_analytics_status(raw: dict) -> str:
    """The status a run should be COUNTED AS in analytics rollups — corrects the
    mislabeled-done class. A run whose stages show a completed reflect/deliver has
    DELIVERED, regardless of a status string later stamped 'abandoned'/'paused'
    (superseded/crash-after-the-fact). Count those as 'completed' so
    completion_rate does not undercount (Gate-1 #4: covers BOTH abandoned+delivered
    AND paused+delivered). Everything else passes through unchanged.

    Consumers must still drop `_is_garbage_run` rows FIRST; this only decides how a
    KEPT run is bucketed (the 22 delivered-abandoned survive the garbage filter via
    _delivered_by_stages and land here → completed)."""
    if not isinstance(raw, dict):
        return ""
    status = raw.get("status") or ""
    if status in ("completed", "complete"):
        return "completed"
    if status in ("abandoned", "paused") and _delivered_by_stages(raw):
        return "completed"
    return status


def _abandon_verdict(data: dict, threshold) -> tuple[bool, str | None]:
    """Single source of truth for "should this run be auto-abandoned?".

    Shared by _auto_abandon_stale_runs (new-run trigger) and cleanup_orphans
    (batch/daily-sweep trigger) so the two can never drift (R25). Returns
    (should_abandon, abandon_reason).

    Two abandon classes, both age-gated by `threshold` (an aware datetime; a run
    older than it is stale):
      1. ORPHAN  — status=='running' and stale → "orphaned_no_resume"
         (a running run only gets cleaned up because time passed / a new run
         started; unchanged legacy behavior).
      2. CRASH-ZOMBIE — status=='paused' AND checkpoint.reason is the crash
         auto-checkpoint marker AND zero tokens AND stale → "crash_zombie".
         The crash-reason marker is what distinguishes a dead crash residue from
         an INTENTIONAL pause (Gate BLOCK / awaiting-decision), which is NEVER
         abandoned regardless of age. The token==0 + age gates are extra guards
         so a freshly-crashed run mid-work isn't reaped instantly.

    Anything else (intentional pause, fresh run, completed/failed/cancelled,
    paused-with-real-reason, paused-with-work-done) → (False, None).
    """
    status = data.get("status")
    if status not in ("running", "paused"):
        return (False, None)

    # TERMINAL GUARD (run_bf840159): a run whose STAGES are all done (or has a
    # completed reflect/deliver) is FINISHED — even if the orphan-transition
    # flipped its status to 'paused'+crash-reason during a session refresh. Never
    # abandon a finished run; it is not a crash-zombie, it is a completed run
    # wearing a stale status string.
    if is_terminal_run(data):
        return (False, None)

    # Age gate (shared) — a run newer than threshold is never reaped.
    updated_str = data.get("updated_at", data.get("created_at", ""))
    if not updated_str:
        return (False, None)
    try:
        updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return (False, None)
    if updated_at >= threshold:
        return (False, None)

    if status == "running":
        return (True, "orphaned_no_resume")

    # status == "paused": ONLY a crash-zombie qualifies. An intentional pause
    # (real decision reason) or one with any work done (tokens>0) is preserved.
    reason = (data.get("checkpoint") or {}).get("reason")
    if reason == _CRASH_ZOMBIE_REASON and _run_tokens(data) == 0:
        return (True, "crash_zombie")
    return (False, None)


def _auto_abandon_stale_runs(project: str, new_run_id: str, threshold_hours: float = 2.0) -> int:
    """Mark stale same-project 'running' runs as abandoned when a new run starts.

    Scans all runs for the given project. If status='running' and updated_at
    is older than threshold_hours, marks it 'abandoned' with a reason.

    Returns number of runs abandoned.
    """
    from datetime import datetime, timezone, timedelta

    ws = _get_workspace()
    runs_dir = ws / "Projects" / project / ".artifacts" / "runs"
    if not runs_dir.exists():
        return 0

    threshold = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)
    abandoned_count = 0

    for rd in runs_dir.iterdir():
        if not rd.is_dir():
            continue
        run_file = rd / "run.json"
        if not run_file.exists():
            continue

        try:
            data = json.loads(run_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        # Skip self — never abandon the run that triggered this scan.
        if data.get("id") == new_run_id:
            continue

        # Shared verdict (single source of truth; see _abandon_verdict): handles
        # BOTH the running-orphan case (unchanged "orphaned_no_resume") and the
        # crash-zombie paused case. Intentional pauses are preserved.
        should, reason = _abandon_verdict(data, threshold)
        if should:
            data["status"] = "abandoned"
            data["abandon_reason"] = reason
            data["abandoned_at"] = datetime.now(timezone.utc).isoformat()
            run_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            abandoned_count += 1

    return abandoned_count


def cleanup_orphans(threshold_hours: float = 2.0) -> dict:
    """Mark all stale 'running' runs across ALL projects as abandoned.

    Used as a batch cleanup subcommand. Returns summary dict.
    """
    from datetime import datetime, timezone, timedelta

    ws = _get_workspace()
    projects_dir = ws / "Projects"
    if not projects_dir.exists():
        return {"abandoned_count": 0, "projects_scanned": 0}

    threshold = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)
    abandoned_count = 0
    projects_scanned = 0

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        runs_dir = project_dir / ".artifacts" / "runs"
        if not runs_dir.exists():
            continue
        projects_scanned += 1

        for rd in runs_dir.iterdir():
            if not rd.is_dir():
                continue
            run_file = rd / "run.json"
            if not run_file.exists():
                continue

            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            # Shared verdict (single source of truth; see _abandon_verdict) —
            # same logic as the new-run trigger, so running-orphans AND
            # crash-zombie paused runs are both reaped here, while intentional
            # pauses are preserved. This is the gap fix: the old `status !=
            # running` skip meant crash zombies (which are PAUSED) accumulated
            # forever (12 found 2026-06-30).
            should, reason = _abandon_verdict(data, threshold)
            if should:
                data["status"] = "abandoned"
                data["abandon_reason"] = reason
                data["abandoned_at"] = datetime.now(timezone.utc).isoformat()
                run_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                abandoned_count += 1

    return {"abandoned_count": abandoned_count, "projects_scanned": projects_scanned}


def cmd_cleanup_orphans(args, reg: ArtifactRegistry) -> None:
    """CLI handler for cleanup-orphans subcommand."""
    threshold = getattr(args, "threshold", None)
    threshold = float(threshold) if threshold is not None else 2.0
    result = cleanup_orphans(threshold_hours=threshold)
    print(json.dumps(result))


def _trash_dir(path: Path) -> None:
    """Delete a run directory, PREFERRING recoverable trash but SILENTLY DEGRADING to
    PERMANENT delete when trash is unavailable. Path 1 (recoverable): macOS `trash` →
    goes to Trash, undoable (STEERING safety: trash > rm). Path 2 (PERMANENT, NOT
    recoverable): shutil.rmtree — taken when the `trash` tool is absent (CI, tmpfs, PATH
    gap under launchd) or errors. This is a deliberate degrade (a purge must still make
    progress), but it is NOT "recoverable" as the name implies — so which path ran is
    RECORDED to stderr, otherwise a permanent delete looks identical to a recoverable one
    in the logs. Raises on total failure so the caller counts it as not-purged."""
    import shutil
    import subprocess
    trash_bin = shutil.which("trash")
    if trash_bin:
        try:
            r = subprocess.run([trash_bin, str(path)], capture_output=True, timeout=30)
            if r.returncode == 0:
                print(json.dumps({"trash": "recoverable", "path": str(path)}),
                      file=sys.stderr)
                return
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to rmtree
    # PERMANENT delete path — make it auditable (see docstring): the delete is real and
    # unrecoverable, so record it rather than let it pass as a recoverable trash.
    print(json.dumps({"trash": "PERMANENT_rmtree", "path": str(path),
                      "reason": "trash tool absent or failed"}), file=sys.stderr)
    shutil.rmtree(path)


def _has_git_dir(repo_root: Path) -> bool:
    """Filesystem-only (NO subprocess) check for whether repo_root is inside a git
    working tree: walk up looking for a `.git` dir OR file (worktree/submodule use a
    `.git` FILE). Deliberately avoids `git`; it is the fail-CLOSED fallback used when
    the git SUBPROCESS is exactly what failed, so it must not depend on git running."""
    p = repo_root
    for _ in range(40):  # bounded walk-up (defensive against a symlink loop)
        if (p / ".git").exists():
            return True
        if p.parent == p:
            break
        p = p.parent
    return False


def _is_git_tracked(path: Path, repo_root: Path) -> bool:
    """True iff `path` MUST be treated as git-TRACKED for delete-safety purposes.

    Fail-CLOSED (run: purge fail-open fix): the guard's JOB is to STOP an unattended
    delete of a tracked file (Gate-2 CRITICAL run_0e68e235). `_trash_dir` deletes via
    trash/rmtree — NOT git — so deleting a tracked run's dir mutates the (public) git
    working tree even when git itself is unavailable. So we must distinguish:
      • git RAN and said untracked (returncode != 0) → genuinely untracked → False.
      • git RAN and said tracked (returncode == 0)   → tracked → True.
      • git could NOT run (OSError/timeout/subprocess error): INDETERMINATE. In a git
        working tree (`_has_git_dir`) we CANNOT rule out tracked → return True
        (fail-closed: skip the delete). Only when there is NO `.git` anywhere up the
        tree is there truly nothing to mutate → return False (non-git ws purges normally).
    The prior version returned False on ANY error, so a transient git hiccup under
    launchd (missing PATH / lock / timeout) would let the sweep trash a tracked public
    run unattended — the exact fail-open this closes."""
    import subprocess
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return False
    try:
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel)],
            cwd=str(repo_root), capture_output=True, timeout=15,
        )
        # Distinguish by EXACT rc (adversarial-review CONFIRMED bug): rc==0 tracked,
        # rc==1 genuinely untracked, rc>=128 FATAL (corrupt/locked index — a real
        # unattended failure mode). The old `return rc==0` collapsed rc==1 AND rc==128
        # into "untracked" → a corrupt index would let the sweep trash a TRACKED public
        # run (fail-open). Route any non-{0,1} code to the fail-CLOSED fallback.
        if r.returncode == 0:
            return True   # tracked
        if r.returncode == 1:
            return False  # genuinely untracked → delete-eligible
        return _has_git_dir(repo_root)  # fatal/unexpected → indeterminate → fail-closed
    except (OSError, subprocess.SubprocessError):
        # Indeterminate: fail-CLOSED inside a git working tree, open only if truly non-git.
        return _has_git_dir(repo_root)


def purge_garbage_runs(retention_days: float = 30.0, apply: bool = False,
                       include_tracked: bool = False) -> dict:
    """Retention purge: recoverably TRASH garbage run directories older than
    ``retention_days`` across ALL projects (run_0e68e235). SSOT for both the
    one-time cleanup script and the scheduled retention job — one definition, no
    drift (R25).

    Garbage = `_is_garbage_run` (abandoned OR crash-residue-paused, that never
    delivered). NEVER purges completed / cancelled / failed / running /
    genuine-decision-pause / delivered-but-mislabeled (they are not garbage) — nor
    FRESH garbage newer than the retention window (a just-crashed run may still be
    diagnosable). Age is measured on `abandoned_at` || `updated_at` || `created_at`.

    Dry-run is the DEFAULT (apply=False): reports `would_purge` and touches nothing.
    apply=True trashes the dirs (recoverable) and reports `purged`.

    🔒 GIT-TRACKED GUARD (Gate-2 CRITICAL, run_0e68e235): a run dir that is git-TRACKED
    is SKIPPED by default (counted in `skipped_tracked`). In the real workspace the
    SwarmAI project's `.artifacts/runs/*` ARE tracked (origin = the PUBLIC repo) — an
    earlier assumption that they were gitignored was WRONG (verified via `git ls-files`,
    not `git check-ignore`). Silently `git rm`-ing tracked runs from an unattended job
    would mutate the public working tree with no commit/guard. So the scheduled job
    only trashes UNTRACKED local garbage; deleting tracked garbage is a deliberate,
    user-owned decision (STEERING #5) gated behind `include_tracked=True`. Fail-safe:
    a non-git workspace has nothing tracked → everything purges normally.

    Ordering note (Gate-1 #6): this DELETES dirs that cleanup_orphans/proactive_
    intelligence earlier MARKED abandoned. The two never conflict — mark then delete,
    same direction; and _load_pipeline_runs tolerates a mid-scan-deleted dir
    (its read is wrapped in except OSError). Purge runs on a schedule, marking on
    session-refresh — different triggers, no race.
    """
    from datetime import datetime, timezone, timedelta

    ws = _get_workspace()
    projects_dir = ws / "Projects"
    if not projects_dir.exists():
        return {"would_purge": 0, "purged": 0, "projects_scanned": 0, "errors": 0,
                "skipped_tracked": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    would_purge = 0
    purged = 0
    errors = 0
    skipped_tracked = 0
    projects_scanned = 0

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        runs_dir = project_dir / ".artifacts" / "runs"
        if not runs_dir.exists():
            continue
        projects_scanned += 1

        for rd in list(runs_dir.iterdir()):
            if not rd.is_dir():
                continue
            run_file = rd / "run.json"
            if not run_file.exists():
                continue
            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if not _is_garbage_run(data):
                continue

            # Age gate: only purge garbage OLDER than the retention window.
            ts = data.get("abandoned_at") or data.get("updated_at") or data.get("created_at") or ""
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, AttributeError):
                # Undated / unparseable timestamp: fail-CLOSED — treat as FRESH and
                # KEEP it (run: purge fail-open fix). The prior code treated undated
                # garbage as OLD and purged it, which is the unsafe direction for an
                # unattended destructive sweep: a malformed (not truly absent) ts, or a
                # schema drift that stops populating these fields, would silently make
                # EVERY run purge-eligible. A dead-residue dir that genuinely has no
                # timestamp costs little to keep; deleting a mis-dated run is not
                # recoverable-by-design. (AttributeError guards ts being a non-str.)
                continue
            if when >= cutoff:
                continue  # fresh garbage — keep for now

            # 🔒 GIT-TRACKED GUARD (Gate-2 CRITICAL): never silently delete a tracked
            # run — that would `git rm` from the public working tree unattended. Skip
            # unless the caller explicitly opts into the deliberate git-rm path.
            if not include_tracked and _is_git_tracked(run_file, ws):
                skipped_tracked += 1
                continue

            would_purge += 1
            if apply:
                try:
                    _trash_dir(rd)
                    purged += 1
                except Exception:  # noqa: BLE001 — count failure, never abort the sweep
                    errors += 1

    return {
        "would_purge": would_purge,
        "purged": purged,
        "skipped_tracked": skipped_tracked,
        "projects_scanned": projects_scanned,
        "errors": errors,
        "retention_days": retention_days,
        "applied": apply,
        "include_tracked": include_tracked,
    }


def cmd_purge_garbage(args, reg: ArtifactRegistry) -> None:
    """CLI handler for purge-garbage subcommand (the scheduled retention entrypoint)."""
    days = getattr(args, "retention_days", None)
    days = float(days) if days is not None else 30.0
    result = purge_garbage_runs(
        retention_days=days,
        apply=bool(getattr(args, "apply", False)),
        include_tracked=bool(getattr(args, "include_tracked", False)),
    )
    print(json.dumps(result))


def _load_completed_runs(project: str, limit: int = 10) -> list[dict]:
    """Load completed pipeline runs for historical calibration.

    Scans both new path (runs/*/run.json) and legacy (pipeline-run-*.json).
    """
    artifacts_dir = _get_workspace() / "Projects" / project / ".artifacts"
    runs = []

    # New path: .artifacts/runs/*/run.json
    runs_dir = artifacts_dir / "runs"
    if runs_dir.exists():
        for rd in sorted(runs_dir.iterdir(), reverse=True):
            run_file = rd / "run.json"
            if run_file.exists():
                try:
                    state = json.loads(run_file.read_text(encoding="utf-8"))
                    if state.get("status") == "completed" and state.get("stages"):
                        runs.append(state)
                        if len(runs) >= limit:
                            return runs
                except (json.JSONDecodeError, KeyError):
                    continue

    # Legacy path: .artifacts/pipeline-run-*.json
    if artifacts_dir.exists():
        seen_ids = {r["id"] for r in runs}
        for f in sorted(artifacts_dir.glob("pipeline-run-*.json"), reverse=True):
            try:
                state = json.loads(f.read_text(encoding="utf-8"))
                if state["id"] in seen_ids:
                    continue
                if state.get("status") == "completed" and state.get("stages"):
                    runs.append(state)
                    if len(runs) >= limit:
                        return runs
            except (json.JSONDecodeError, KeyError):
                continue

    return runs


def _calibrated_stage_budget(project: str, stage: str) -> int:
    """Get calibrated token budget for a stage from historical data.
    Falls back to DEFAULT_STAGE_BUDGETS if no history."""
    runs = _load_completed_runs(project, limit=5)
    costs = []
    for r in runs:
        for s in r.get("stages", []):
            if s.get("stage") == stage and s.get("token_cost", 0) > 0:
                costs.append(s["token_cost"])
    if costs:
        avg = sum(costs) / len(costs)
        return int(avg * 1.2)  # 20% buffer over historical average
    return DEFAULT_STAGE_BUDGETS.get(stage, 30_000)


def _estimate_session_budget(project: str) -> dict:
    """Build a full budget estimate for a pipeline run."""
    stage_estimates = {}
    for stage in DEFAULT_STAGE_BUDGETS:
        stage_estimates[stage] = _calibrated_stage_budget(project, stage)

    return {
        "session_total": SESSION_BUDGET,
        "checkpoint_reserve": CHECKPOINT_RESERVE,
        "consumed": 0,
        "remaining": SESSION_BUDGET,
        "stage_estimates": stage_estimates,
        "calibration_source": "historical" if _load_completed_runs(project, 1) else "defaults",
    }


def cmd_run_create(args, reg: ArtifactRegistry) -> None:
    """Create a new pipeline run state file."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    run_id = _gen_run_id()

    # Load historical calibration for budget estimate
    budget = _estimate_session_budget(args.project)

    run_state = {
        "id": run_id,
        "project": args.project,
        "requirement": args.requirement,
        "profile": args.profile or None,
        "status": "running",
        "stages": [],
        "taste_decisions": [],
        "budget": budget,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }

    rd = _run_dir(args.project, run_id)
    rd.mkdir(parents=True, exist_ok=True)
    run_file = rd / "run.json"
    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    # Auto-abandon stale same-project runs (lifecycle management)
    abandoned = _auto_abandon_stale_runs(args.project, run_id)

    result = {"pipeline_id": run_id, "project": args.project, "file": str(run_file)}
    if abandoned > 0:
        result["auto_abandoned"] = abandoned
    print(json.dumps(result))


def cmd_run_update(args, reg: ArtifactRegistry) -> None:
    """Update a pipeline run's stage record or status."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    if args.status:
        # ── Single choke-point for the paused transition (COE10 dual-write guard) ──
        # run-update --status paused is the OTHER door to a pause; it must honor the
        # same confabulation gate as run-checkpoint, or the gate is trivially bypassed
        # (adversarial run_a822b3e8 finding F). run-checkpoint is the sanctioned pause
        # path (it also writes the checkpoint artifact + Radar todo); a bare status
        # flip to paused is refused unless it already measures should_checkpoint=true
        # or carries --force-checkpoint.
        if args.status == "paused" and not getattr(args, "force_checkpoint", False):
            _info = _compute_should_checkpoint(run_state, args.project)
            if not _info["should_checkpoint"]:
                print(json.dumps({
                    "blocked": True,
                    "error": "PAUSE REFUSED via run-update — use `run-checkpoint` "
                             "(the sanctioned pause path) or pass --force-checkpoint. "
                             "should_checkpoint=false. continue, period.",
                    "measurement": {"should_checkpoint": False,
                                    "pct_consumed": _info["pct_consumed"],
                                    "consumed": _info["consumed"]},
                }), file=sys.stderr)
                sys.exit(2)
        run_state["status"] = args.status
        if args.status == "running":
            # ── Lifecycle: auto-abandon stale same-project running runs ──
            abandoned = _auto_abandon_stale_runs(args.project, args.run_id)
            if abandoned > 0:
                import sys as _sys
                print(
                    json.dumps({"auto_abandoned": abandoned, "project": args.project}),
                    file=_sys.stderr,
                )
        if args.status == "completed":
            # ── Completion Gate: ALL profile stages must be done or explicitly skipped ──
            # Every stage in the DDD+pipeline loop has purpose. No silent skips.
            profile = run_state.get("profile", "full")
            profile_stages = _get_profile_stages(profile)

            stage_status_map: dict[str, str] = {}
            for s in run_state.get("stages", []):
                name = s.get("stage", s.get("name", "?"))
                stage_status_map[name] = s.get("status", "unknown")

            # ── Non-skippable stages per profile (BLOCKING) ──
            # These stages are structurally essential to the profile's quality
            # loop. Skipping them defeats the purpose of the profile.
            _NON_SKIPPABLE = {
                "full": {"deliver", "reflect"},
                "bugfix": {"deliver", "reflect"},
                "goal": {"goal_cycle", "deliver", "reflect"},
            }
            non_skippable = _NON_SKIPPABLE.get(profile, set())

            missing_stages = []
            for stg in profile_stages:
                status = stage_status_map.get(stg)
                if status in ("completed", "done"):
                    continue
                elif status == "skipped":
                    # Non-skippable stages CANNOT be skipped regardless of reason
                    if stg in non_skippable:
                        missing_stages.append(
                            f"{stg} (cannot be skipped — essential to {profile} profile quality loop)"
                        )
                        continue
                    # Other stages: skipped is allowed ONLY with an explicit reason
                    record = next(
                        (s for s in run_state.get("stages", [])
                         if s.get("stage", s.get("name")) == stg),
                        {},
                    )
                    reason = record.get("skip_reason") or record.get("notes") or ""
                    if not reason.strip():
                        missing_stages.append(
                            f"{stg} (skipped without reason — add skip_reason field)"
                        )
                else:
                    missing_stages.append(
                        f"{stg} (status={status or 'not recorded'})"
                    )

            if missing_stages:
                print(json.dumps({
                    "error": "Cannot mark completed: not all profile stages are done. "
                             "Every stage in the pipeline serves the DDD learning loop — "
                             "execute them or explicitly skip with a reason.",
                    "pipeline_id": args.run_id,
                    "profile": profile,
                    "missing_stages": missing_stages,
                }))
                return

            # ── REFLECT quality gate: lessons must be substantive ──
            # The whole point of REFLECT is DDD refresh. Empty/trivial lessons = didn't reflect.
            if "reflect" in profile_stages:
                reflect_record = next(
                    (s for s in run_state.get("stages", [])
                     if s.get("stage", s.get("name")) == "reflect"
                     and s.get("status") in ("completed", "done")),
                    None,
                )
                if reflect_record:
                    lessons = reflect_record.get("lessons", [])
                    valid_lessons = [
                        l for l in (lessons if isinstance(lessons, list) else [])
                        if isinstance(l, str) and len(l.strip()) > 20
                    ]
                    if not valid_lessons:
                        print(json.dumps({
                            "error": "Cannot mark completed: REFLECT stage has no substantive lessons. "
                                     "Each lesson must be >20 chars and actionable. "
                                     "Bad: 'done', '3 lessons captured'. "
                                     "Good: 'SMOKE tests caught 2 runtime crashes that unit tests missed'.",
                            "pipeline_id": args.run_id,
                            "lessons_found": lessons,
                        }))
                        return

            # ── REPORT.md gate: checked later (line ~1078) after validator runs.
            # Removed duplicate early check — single canonical gate is post-validator.

            # ── Stage metrics gate (full/bugfix only): audit trail completeness ──
            # Every stage must have token_cost recorded. Chat output IS the live
            # demo, run.json IS the audit trail, REPORT.md IS the projection.
            # Empty metrics = empty report = useless audit.
            if profile in ("full", "bugfix"):
                stages_without_metrics = []
                for s in run_state.get("stages", []):
                    name = s.get("stage", s.get("name", "?"))
                    status = s.get("status")
                    if status in ("completed", "done") and name != "reflect":
                        tc = s.get("token_cost", 0)
                        if not tc or tc == 0:
                            stages_without_metrics.append(name)
                # WARN but don't BLOCK (yet) — this is a new enforcement, start soft
                if stages_without_metrics:
                    import sys as _sys
                    print(json.dumps({
                        "warning": f"Stages missing token_cost (audit gap): "
                                   f"{stages_without_metrics}. Future: this will BLOCK.",
                        "pipeline_id": args.run_id,
                    }), file=_sys.stderr)

            # ── Auto-aggregate delivery artifact (P0 friction fix) ──────────────
            # When pipeline agent calls --status completed but no delivery artifact
            # exists, auto-construct it from stage-json. This eliminates the
            # publish → validation_failed → fix JSON → re-publish ceremony that
            # wastes ~5K tokens per run. Only for full/bugfix where delivery matters.
            _requirement = run_state.get("requirement", "")
            if profile in ("full", "bugfix"):
                deliver_stage_rec = next(
                    (s for s in run_state.get("stages", [])
                     if s.get("stage", s.get("name", "")) == "deliver"
                     and s.get("status") in ("completed", "done")),
                    None,
                )
                if deliver_stage_rec and not deliver_stage_rec.get("artifact_id"):
                    # Auto-aggregate from stage-json fields.
                    # Normalize to dicts FIRST (run_ca0190fb): an agent may set
                    # `adversarial_review: true` (a BOOL) on the DELIVER stage — a habit
                    # reinforced by the goal-path gate, which accepts a bool
                    # `adversarial_review` on the goal_cycle stage (a DIFFERENT,
                    # mutually-exclusive profile — so this coercion defends against the
                    # by-analogy habit, NOT a value that gate feeds into this path). A
                    # truthy non-dict means "review happened, details unrecorded" →
                    # coerce to a minimal dict so the `.get()` calls below never crash
                    # ('bool' has no .get). Safety is preserved downstream: the Rule-23
                    # validator still rejects a findings-less/evidence-less delivery, so
                    # the coercion defers to that clean error — it never launders an
                    # unreviewed delivery past the gate.
                    _adv = deliver_stage_rec.get("adversarial_review", {})
                    if not isinstance(_adv, dict):
                        _adv = {"spawned": bool(_adv), "findings": []}
                    _audit = deliver_stage_rec.get("completion_audit", {})
                    if not isinstance(_audit, dict):
                        _audit = {}
                    _ac = deliver_stage_rec.get("ac_verification", {})
                    if not isinstance(_ac, dict):
                        _ac = {}
                    if (_adv and _adv.get("findings")) or (_audit and _audit.get("criteria_met")) or _ac:
                        # Build delivery artifact data
                        auto_delivery = {
                            "title": _requirement[:80] or "Pipeline Delivery",
                            "summary": deliver_stage_rec.get("summary", _requirement[:200]),
                            "adversarial_review": _adv if _adv else {"spawned": False, "findings": []},
                            "completion_audit": _audit if _audit else {},
                            "ac_verification": _ac if _ac else {},
                            "confidence_score": deliver_stage_rec.get("confidence_score", 0),
                            "push_ready": deliver_stage_rec.get("push_ready", True),
                            "auto_aggregated": True,
                        }
                        # Add profile_tier for validator
                        if isinstance(_adv, dict) and not _adv.get("profile_tier"):
                            auto_delivery["adversarial_review"]["profile_tier"] = profile
                        # Publish internally (relaxed schema — skip user-facing strictness)
                        try:
                            art_id = reg.publish(
                                project=args.project,
                                artifact_type="delivery",
                                producer="s_autonomous-pipeline",
                                summary=f"[Auto-aggregated] {_requirement[:60]}",
                                data=auto_delivery,
                                run_id=args.run_id,
                            )
                            deliver_stage_rec["artifact_id"] = art_id
                            import sys as _sys
                            print(json.dumps({
                                "auto_aggregated": True,
                                "artifact_id": art_id,
                                "source": "deliver stage-json fields",
                            }), file=_sys.stderr)
                        except Exception as _agg_exc:
                            import sys as _sys
                            print(json.dumps({
                                "warning": f"Auto-aggregate delivery failed: {_agg_exc}",
                                "fallback": "Manual publish required",
                            }), file=_sys.stderr)

            # ── Validator Gate: run pipeline_validator on ALL completed stages ──
            # L2 enforcement: agent cannot close a run without passing structural
            # validation. Catches skipped adversarial review, missing artifacts,
            # wrong profile tier, empty findings, etc.
            # This is the mechanical gate that makes quality non-optional.
            try:
                # Import validator from same directory (both in backend/scripts/)
                import importlib.util
                _validator_path = Path(__file__).parent / "pipeline_validator.py"
                _spec = importlib.util.spec_from_file_location(
                    "pipeline_validator", _validator_path
                )
                _validator_mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_validator_mod)
                _validate_stage = _validator_mod.validate

                validator_errors: list[str] = []

                # Only validate DELIVER stage — this is where adversarial review
                # and completion audit live. Other stages were validated at publish
                # time via artifact_cli publish --stage (which calls validator inline).
                # The run-complete gate enforces: "you cannot close without proof
                # that adversarial review happened with correct tier."
                # Gate fires when deliver stage exists as completed.
                # If no artifact_id: that itself is a violation — proper pipeline
                # ceremony REQUIRES publish. Missing artifact_id = skipped ceremony
                # = BLOCK (unless profile is trivial/research/docs).
                deliver_rec = next(
                    (s for s in run_state.get("stages", [])
                     if s.get("stage", s.get("name", "")) == "deliver"
                     and s.get("status") in ("completed", "done")),
                    None,
                )
                # Block if deliver stage completed WITHOUT artifact_id
                # (trivial/research/docs profiles are exempt — they have no
                # adversarial review in DELIVER, so no artifact_id is expected.
                # Goal profile is also exempt here — its adversarial runs inside
                # goal_cycle, enforced by the adversarial_review gate below.)
                _profile = run_state.get("profile", "full")
                if deliver_rec and not deliver_rec.get("artifact_id"):
                    if _profile in ("full", "bugfix"):
                        validator_errors.append(
                            "[deliver] Stage completed without artifact_id. "
                            "Pipeline ceremony requires: publish deliver artifact "
                            "with adversarial_review.profile_tier field BEFORE "
                            "marking deliver complete."
                        )
                if deliver_rec and deliver_rec.get("artifact_id"):
                    try:
                        result = _validate_stage(
                            project=args.project,
                            run_id=args.run_id,
                            stage="deliver",
                        )
                    except Exception as _verr:
                        # FAIL-CLOSED on validator-internal crash (run_84316b42,
                        # reverses run_55710438 MED-8). If validate() raises before
                        # returning a dict (crash in the pre-load block, an import
                        # error, or any unwrapped path), the validator could NOT
                        # produce a verdict — so completion cannot be verified and
                        # MUST block, exactly like the ADVANCE path (cmd_advance).
                        #
                        # The prior fail-open rationale ("a validator bug must not
                        # permanently block every pipeline") does NOT hold: ADVANCE
                        # is hit on every stage transition and already fails closed
                        # on this same condition, so a real validator bug already
                        # blocks upstream. Failing open here only opened a hole at
                        # the LAST gate before status=completed (C037/CLASS A).
                        # Surfacing the crash as a blocking error is the correct,
                        # actionable response — the user fixes the validator, same
                        # as advance. No silent fail-open at the final gate.
                        result = None
                        # Distinguish a TRANSIENT I/O hiccup (file lock, partial
                        # read, concurrent writer) from a real validator code fault.
                        # Both fail closed — completion cannot be verified either way
                        # — but the message must be accurate: a transient error is
                        # RETRYABLE (re-run completion), not "fix the validator".
                        # (Adversarial review MED: validate()'s internal run.json
                        # re-read can raise OSError/JSONDecodeError on a benign hiccup.)
                        if isinstance(_verr, (OSError, json.JSONDecodeError)):
                            validator_errors.append(
                                f"[deliver] validator could not verify deliver stage "
                                f"(transient I/O error: {type(_verr).__name__}: {_verr}). "
                                f"Completion blocked — RETRY; if it persists, check the "
                                f"run.json file and filesystem (fail-closed, symmetric "
                                f"with the advance path)."
                            )
                        else:
                            validator_errors.append(
                                f"[deliver] validator ERRORED (could not run): "
                                f"{type(_verr).__name__}: {_verr}. Cannot verify deliver "
                                f"stage — fix the validator or input before completing "
                                f"(fail-closed, symmetric with the advance path)."
                            )
                    if result and result.get("errors"):
                        # Filter genuinely-ENVIRONMENTAL errors (the run/stage doesn't
                        # exist in this context — test env, stale lookup). Keep ALL
                        # SEMANTIC errors (wrong tier, unresolved findings, etc.).
                        #
                        # "could not be loaded" was REMOVED from this filter
                        # (run_95fc9b6a, deferred LOW from run_84316b42). It is NOT
                        # environmental: pipeline_validator emits it (L1511) only when
                        # the deliver artifact_id is set but the file is missing/corrupt.
                        # We reach this branch only inside the `deliver_rec.artifact_id`
                        # guard above — so a claimed-but-unloadable deliver artifact
                        # means the deliver stage CANNOT be verified, which must BLOCK
                        # completion, not be suppressed as noise (fail-open at the last
                        # gate = C037/CLASS A).
                        #
                        # ANCHORED match, not loose substring (adversarial MED,
                        # run_95fc9b6a): these are the validator's two EXACT
                        # early-return sentinels for "run/stage does not exist"
                        # (pipeline_validator L1472 + L1493). A loose "not found"
                        # substring would also swallow a fail-CLOSED crash message
                        # like "...ERRORED: FileNotFoundError: ... not found",
                        # re-opening the very fail-open hole this fix closes. Anchor
                        # on the full sentinel phrasing so only the genuinely-
                        # environmental run/stage-missing cases are suppressed.
                        _INFRA_SENTINELS = (
                            "not found for project",       # "Pipeline run X not found for project Y"
                            "no stage record found for",   # "No stage record found for 'S' in run X"
                        )
                        errors_list = [
                            e for e in result["errors"]
                            if not any(s in e.lower() for s in _INFRA_SENTINELS)
                        ]
                        for err in errors_list:
                            validator_errors.append(f"[deliver] {err}")

                # ── C2 completion backstop (run_7cf9da85) ──────────────────────
                # The deliver gate above only re-checks the deliver artifact. The 3
                # EVALUATE-stage gates (Understanding / Ambiguity / Working-Backwards)
                # ran ONLY at publish time — so an evaluate (or any non-deliver)
                # artifact that bypassed `publish --stage` (hand-edit, ignored exit
                # code) reaches completion unguarded. Close the hole at the SAME
                # single-source function the publish path uses: re-run
                # validate_artifact_data over every completed stage's artifact.
                # (The skeptic, run_7cf9da85, proved mirroring into _check_depth is
                # dead code — completion hardcodes stage='deliver' + _check_depth
                # excludes evaluate. This is the correct layer.)
                _validate_data = getattr(_validator_mod, "validate_artifact_data", None)
                _load_adata = getattr(_validator_mod, "_load_artifact_data", None)
                if _validate_data is not None and _load_adata is not None:
                    _profile = run_state.get("profile", "full")
                    _cur_profile_stages = _get_profile_stages(_profile)
                    for _s in run_state.get("stages", []):
                        _name = _s.get("stage", _s.get("name", ""))
                        # deliver already re-validated above; skip artifactless stages.
                        if _name in ("deliver", "reflect") or _s.get("status") not in ("completed", "done"):
                            continue
                        # This backstop re-validates historical artifacts for SHAPE only —
                        # profile membership is already enforced by completion Check 6 + the
                        # "all profile stages done" gate. Skip a stage not in the CURRENT
                        # profile: a legit profile upgrade (goal→full, both rank 4) leaves a
                        # completed off-profile stage record (goal_cycle), and
                        # validate_artifact_data now fail-closes on off-profile stages (Run A,
                        # run_7627f63c) — re-validating it here would newly BLOCK a run that
                        # validated before. Off-profile PUBLISHING is caught at the publish
                        # entrypoint (:216), the correct layer for it. (Gate-2 HIGH, run_7627f63c)
                        if _name not in _cur_profile_stages:
                            continue
                        _aid = _s.get("artifact_id")
                        if not _aid:
                            continue
                        _adata = _load_adata(args.project, args.run_id, _aid)
                        if not isinstance(_adata, dict):
                            continue  # unloadable → not this gate's concern (publish gate covers shape)
                        try:
                            _stage_errs = _validate_data(_name, _adata, profile=_profile)
                        except Exception:
                            continue  # never let the backstop itself crash completion
                        # KNOWN LIMITATION (adversarial MED, run_7cf9da85): a strict-
                        # profile evaluate artifact PREDATING the gate regime (no
                        # understanding/ambiguity/WB fields) would be re-blocked here on
                        # a resumed completion. We deliberately do NOT add a legacy
                        # bypass: a hand-edited bypass (the exact attack C2 closes) and
                        # a legacy artifact are field-indistinguishable, so a legacy
                        # exemption would re-open the hole. Current exposure is ZERO
                        # (0 in-flight pre-gate runs, measured run_7cf9da85); a resumed
                        # pre-gate run is fixed by re-publishing its evaluate artifact.
                        # Protecting the real bypass wins over a zero-exposure edge.
                        for _e in _stage_errs or []:
                            validator_errors.append(f"[{_name}] {_e}")

                if validator_errors:
                    print(json.dumps({
                        "error": "Cannot mark completed: pipeline validator found BLOCKING errors. "
                                 "Fix these issues before declaring the run done.",
                        "pipeline_id": args.run_id,
                        "validator_errors": validator_errors,
                    }))
                    return
            except Exception as exc:
                # FAIL-CLOSED on validator-gate crash (run_84316b42, reverses
                # run_55710438 MED-8). This wraps the validator IMPORT + the gate
                # logic. If the validator module cannot even load, or the gate
                # itself raises, we cannot verify the run — so block, do not
                # complete. Symmetric with ADVANCE. Returning here (not falling
                # through) is what makes it fail closed: the status=completed
                # write below is never reached.
                print(json.dumps({
                    "error": "Cannot mark completed: pipeline validator gate ERRORED "
                             "(could not run) — completion cannot be verified. Fix the "
                             "validator before declaring the run done (fail-closed, "
                             "symmetric with the advance path).",
                    "pipeline_id": args.run_id,
                    "validator_error": f"{type(exc).__name__}: {exc}",
                }))
                return

            # ── GOAL PROFILE: Adversarial Review Gate (BLOCKING) ──
            # Goal profile has no DELIVER stage, so the artifact_id gate above
            # doesn't fire. This parallel gate ensures adversarial review ran
            # before goal completion. Without it, agent can rationalize skip
            # (CLASS A pattern: "DoD passed, adversarial is redundant").
            _goal_profile = run_state.get("profile")
            if _goal_profile == "goal":
                goal_cycle_rec = next(
                    (s for s in run_state.get("stages", [])
                     if s.get("stage", s.get("name", "")) == "goal_cycle"
                     and s.get("status") in ("completed", "done")),
                    None,
                )
                if goal_cycle_rec and not goal_cycle_rec.get("adversarial_review"):
                    print(json.dumps({
                        "error": "Cannot mark completed: goal profile requires Final "
                                 "Adversarial Review on total changeset before completion. "
                                 "Run adversarial review (git diff start..HEAD), then "
                                 "update goal_cycle stage with adversarial_review: true.",
                        "pipeline_id": args.run_id,
                        "fix": 'run-update --stage-json \'{"stage":"goal_cycle",'
                               '"status":"completed","adversarial_review":true,'
                               '"stage_doc_consumed":true}\'',
                    }))
                    return

            # ── REPORT.md Gate (BLOCKING) ──
            # Every completed pipeline MUST have a REPORT.md that documents
            # the execution process (stages, decisions, findings, methodology).
            # Without this, pipeline outputs are untraceable.
            report_path = _run_dir(args.project, args.run_id) / "REPORT.md"
            if not report_path.exists():
                print(json.dumps({
                    "error": "Cannot mark completed: REPORT.md not found. "
                             "Generate the pipeline report at "
                             f".artifacts/runs/{args.run_id}/REPORT.md before completing. "
                             "The report must document pipeline execution process "
                             "(stages run, decisions made, findings, methodology impact).",
                    "pipeline_id": args.run_id,
                    "expected_path": str(report_path),
                }))
                return
            report_size = report_path.stat().st_size
            if report_size < 500:
                print(json.dumps({
                    "error": "Cannot mark completed: REPORT.md is too short "
                             f"({report_size} bytes). A valid pipeline report must "
                             "include: TL;DR, requirement, pipeline execution table, "
                             "quality gates, and lessons. Minimum ~500 bytes.",
                    "pipeline_id": args.run_id,
                    "report_path": str(report_path),
                    "report_size_bytes": report_size,
                }))
                return

            # ── Outputs-Surface Gate (run_608a6217 → run_b8ea6d5c, BLOCKING) ──
            # If this run COMMITTED source (run.json.commits) AND those committed
            # files intersect THIS run's files_touched (run-scoped — F3, so a
            # sibling session's commits on shared main never false-block us), the
            # source changes MUST have been surfaced to the Canvas OUTPUTS rail as a
            # PR-review batch at COMPLETE via the `surface_run_outputs` tool. We
            # enforce the agent RECORDED that surface (deliver.outputs_surfaced=true).
            # (Was local_pr_surfaced + a LOCAL_PR.md doc — replaced run_b8ea6d5c: the
            # aggregated doc had no review value; per-file rail rows are the deliverable.)
            #
            # HONEST SCOPE — this is SELF-ATTESTATION, not verification: the CLI
            # subprocess cannot observe whether the frontend actually rendered the
            # rows. The flag is agent-set. It is a defense against SILENTLY skipping
            # the surface step, not proof the rail populated. A run with NO run-scoped
            # source commits (knowledge/docs-only, OR commits that are all
            # sibling-session files) is NOT gated (no false-block).
            _commits = run_state.get("commits") or []
            _committed_files: set[str] = set()
            for _c in _commits:
                if isinstance(_c, dict):
                    for _f in _c.get("files", []) or []:
                        _committed_files.add(_f)
            _run_touched = set(run_state.get("files_touched") or [])
            # Run-scoped source = committed files that THIS run declared it touched.
            # Match a repo-relative commit path ("backend/foo.py") against a
            # files_touched entry regardless of absolute/relative recording, but
            # require a DIRECTORY-anchored suffix (not a bare basename) so unrelated
            # files that merely share a basename ("a/config.py" vs "b/config.py")
            # never collide (Gate-2 LOW 1). A path with no "/" (bare basename in
            # files_touched) only matches an identical bare basename or an exact
            # full-path equality — never a deep path by basename alone.
            def _tail_match(a: set[str], b: set[str]) -> bool:
                for x in a:
                    for y in b:
                        if x == y:
                            return True
                        # dir-anchored: the shorter must be a trailing PATH segment
                        # of the longer (both sides carry the separator), so
                        # "sub/foo.py" matches "/abs/sub/foo.py" but "foo.py" does
                        # NOT match "sub/foo.py".
                        if "/" in x and y.endswith("/" + x):
                            return True
                        if "/" in y and x.endswith("/" + y):
                            return True
                return False
            _run_scoped_source = bool(_committed_files) and _tail_match(_committed_files, _run_touched)
            if _run_scoped_source:
                # Consider BOTH the persisted deliver record AND an incoming
                # --stage-json in THIS same call (Gate-2 MEDIUM 1): the stage_json
                # write is applied later (below), so without this a single
                # `run-update --status completed --stage-json '{deliver...
                # outputs_surfaced:true}'` would read the STALE record and
                # false-block. Merge the incoming deliver ack into the gate's view.
                # Back-compat: accept the legacy local_pr_surfaced key too, so an
                # in-flight run recorded before run_b8ea6d5c still completes.
                _deliver = next(
                    (s for s in run_state.get("stages", [])
                     if s.get("stage", s.get("name", "")) == "deliver"
                     and s.get("status") in ("completed", "done")),
                    None,
                )
                def _ack(d: "dict | None") -> bool:
                    return bool(d and (d.get("outputs_surfaced") or d.get("local_pr_surfaced")))
                _surfaced = _ack(_deliver)
                if not _surfaced and getattr(args, "stage_json", None):
                    try:
                        _incoming = json.loads(args.stage_json)
                        if (_incoming.get("stage", _incoming.get("name")) == "deliver"
                                and _ack(_incoming)):
                            _surfaced = True
                    except (ValueError, TypeError):
                        pass
                if not _surfaced:
                    print(json.dumps({
                        "error": "Cannot mark completed: this run committed source "
                                 "changes but they were not surfaced to the Canvas "
                                 "OUTPUTS rail. At COMPLETE you MUST call the "
                                 "`surface_run_outputs` tool with this run_id (it emits "
                                 "the committed files as PR-review rows), then record it "
                                 "on the deliver stage.",
                        "pipeline_id": args.run_id,
                        "fix": 'run-update --stage-json \'{"stage":"deliver",'
                               '"status":"completed","outputs_surfaced":true,'
                               '"stage_doc_consumed":true}\'',
                        "committed_source_files": sorted(_committed_files),
                    }))
                    return

            run_state["completed_at"] = now
            # Auto-generate METRICS.json on completion
            _try_generate_metrics(args.project, args.run_id, run_state, reg)

    if args.stage_json:
        stage_record = json.loads(args.stage_json)
        # Normalize: accept both "name" and "stage" as the stage identifier
        if "name" in stage_record and "stage" not in stage_record:
            stage_record["stage"] = stage_record.pop("name")

        # ── MECHANICAL GATE: stage_doc_consumed ──────────────────────────────
        # Pipeline stages MUST read their stage doc before completing.
        # This gate prevents the pattern of "running pipeline as bookkeeping"
        # without actually executing stage behavior. (C011→C032 Class A fix)
        _STAGES_REQUIRING_DOC = {"evaluate", "build", "review", "test", "deliver", "reflect"}
        stage_name = stage_record.get("stage", "")
        stage_status = stage_record.get("status", "")
        if (
            stage_name in _STAGES_REQUIRING_DOC
            and stage_status in ("completed", "done")
            and not stage_record.get("stage_doc_consumed")
        ):
            print(json.dumps({
                "error": (
                    f"BLOCKED: stage '{stage_name}' requires 'stage_doc_consumed: true' in stage-json. "
                    f"You MUST Read stages/{stage_name}.md BEFORE marking this stage complete. "
                    f"This is a mechanical gate — no bypass."
                ),
                "pipeline_id": args.run_id,
            }))
            sys.exit(1)
        # ─────────────────────────────────────────────────────────────────────

        # Replace existing stage record or append
        existing_idx = next(
            (i for i, s in enumerate(run_state["stages"])
             if s.get("stage", s.get("name")) == stage_record["stage"]),
            None,
        )
        if existing_idx is not None:
            # Carry-forward safelist: run-update REPLACES the whole record, but the
            # documented finalize workflow (INSTRUCTIONS.md:1372-1379) omits fields
            # that a prior publish/record auto-set. Two are gate-load-bearing:
            #   - artifact_id — the stub at cmd_publish sets it; without carry-forward
            #     finalizing wipes the artifact link → completion's artifact check
            #     silently skips (run_dc86c466 hit this 2x).
            #   - adversarial_review — the deliver-stage auto-aggregation reads it to
            #     BUILD the delivery artifact; a finalize that omits it wiped it →
            #     auto-aggregation saw an empty adv → NO delivery artifact → the gate
            #     rejected with a MISLEADING "Stage completed without artifact_id"
            #     (run_f1fbf37d cost 4 manual re-records).
            # Preserve ONLY fields in this safelist when the incoming finalize omits
            # them; an explicit value always wins. Deliberately EXCLUDES `status` (it
            # must upgrade recorded→completed) and `auto_recorded` (provenance, no
            # gate reads it).
            existing_rec = run_state["stages"][existing_idx]
            if isinstance(existing_rec, dict):
                for _cf in _CARRY_FORWARD_FIELDS:
                    if _cf not in stage_record and existing_rec.get(_cf):
                        stage_record[_cf] = existing_rec[_cf]
            run_state["stages"][existing_idx] = stage_record
        else:
            run_state["stages"].append(stage_record)

        # Reset resume counters on successful stage completion — a stage advancing
        # proves progress, so the pipeline deserves fresh auto-resume budget if it
        # crashes again later at a different stage. Both the emit-throttle
        # (resume_attempts) AND the execution counter (resume_executions, R2) reset
        # together — otherwise a stale execution count would make the exhausted
        # diagnostic lie about a run that has since made progress.
        if stage_status in ("completed", "done"):
            if run_state.get("resume_attempts", 0) > 0:
                run_state["resume_attempts"] = 0
            if run_state.get("resume_executions", 0) > 0:
                run_state["resume_executions"] = 0

    if args.taste_decision:
        decision = json.loads(args.taste_decision)
        run_state["taste_decisions"].append(decision)

    if args.profile:
        # ── GATE: Profile immutability after BUILD ──────────────────────────────
        # C036 (2026-06-01): Agent circumvented adversarial review gate by
        # switching profile from "full" → "bugfix" at DELIVER stage. The adversarial
        # gate checks the CURRENT profile, so a downgrade makes it inapplicable.
        # Fix: profile downgrades are REJECTED once any stage past evaluate exists.
        # Upgrades (trivial→full) are always allowed (more rigor = safe).
        # "standard" kept as backwards-compat alias for "full" (legacy run.json files may use it)
        _PROFILE_RANK = {"trivial": 1, "docs": 2, "research": 2, "bugfix": 3, "full": 4, "standard": 4, "goal": 4}
        current_profile = run_state.get("profile")
        new_rank = _PROFILE_RANK.get(args.profile, 3)
        current_rank = _PROFILE_RANK.get(current_profile, 3)

        # Check if any stage beyond evaluate is completed
        post_evaluate_stages = [
            s for s in run_state.get("stages", [])
            if s.get("stage", s.get("name", "")) != "evaluate"
            and s.get("status") in ("completed", "done")
        ]

        if new_rank < current_rank and post_evaluate_stages:
            print(json.dumps({
                "error": (
                    f"BLOCKED: Profile downgrade '{current_profile}' → '{args.profile}' "
                    f"rejected. Profile is immutable after EVALUATE — {len(post_evaluate_stages)} "
                    f"stage(s) already completed at '{current_profile}' tier. "
                    f"Downgrades bypass quality gates (C036). "
                    f"Upgrades (e.g., bugfix→full) are allowed."
                ),
                "pipeline_id": args.run_id,
            }))
            sys.exit(1)
        # ─────────────────────────────────────────────────────────────────────────
        run_state["profile"] = args.profile

    if args.ddd_checksums:
        run_state["ddd_checksums"] = json.loads(args.ddd_checksums)

    if args.files_touched:
        # ── A1 run-level file tracking (run_76932250) ───────────────────────────
        # Accumulate the source files THIS run actually wrote, so `run-commit` can
        # `git add` exactly them — never `git add -A` (which would sweep a parallel
        # session's in-flight edits, R29). Dedup-append: BUILD may report in
        # batches across multiple run-update calls. Only WRITTEN files belong here
        # (the caller records Edit/Write targets, not Reads) — an over-broad list
        # would re-introduce the cross-session bleed this design exists to prevent.
        try:
            incoming = json.loads(args.files_touched)
        except (ValueError, TypeError):
            print(json.dumps({"error": "--files-touched must be a JSON array of paths"}),
                  file=sys.stderr)
            sys.exit(1)
        if not isinstance(incoming, list):
            print(json.dumps({"error": "--files-touched must be a JSON array of paths"}),
                  file=sys.stderr)
            sys.exit(1)
        existing = run_state.get("files_touched", [])
        # Preserve insertion order, dedup, drop empties/non-strings
        seen = set(existing)
        for p in incoming:
            if isinstance(p, str) and p.strip() and p not in seen:
                existing.append(p.strip())
                seen.add(p.strip())
        run_state["files_touched"] = existing

    run_state["updated_at"] = now
    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    result = {"pipeline_id": args.run_id, "updated": True}
    if args.status == "completed":
        result["reminder"] = (
            "⚠️ OUTPUT COMPLETION SUMMARY TO CHAT (Step 6.2 — MANDATORY). "
            "Pipeline is NOT done until user sees the formatted summary block. "
            "Silent completion = indistinguishable from crash."
        )

    # ── Auto-emit budget info on stage-json update (LL07 fix) ──────────
    # Agent doesn't need to remember to call run-budget — it's free info
    # emitted automatically on every stage update. Prevents feeling-based
    # checkpoints by making budget always visible.
    if args.stage_json and not args.status:
        try:
            budget = run_state.get("budget") or _estimate_session_budget(args.project)
            consumed = sum(s.get("token_cost", 0) for s in run_state.get("stages", []))
            session_total = budget.get("session_total", 800_000)
            remaining = session_total - consumed
            pct = round(consumed / session_total * 100, 1) if session_total > 0 else 0
            result["budget"] = {
                "consumed": consumed,
                "remaining": remaining,
                "pct": pct,
                "should_checkpoint": pct > 70,
            }
            if pct > 70:
                result["budget_warning"] = (
                    "⚠️ Budget >70% consumed. Run `run-budget` for full analysis "
                    "before starting next stage."
                )
        except Exception:
            pass  # Budget calculation failure should never block stage update

    print(json.dumps(result))


def cmd_run_surface_changes(args, reg: ArtifactRegistry) -> None:
    """Read-only git-based COMPLETE sweep (run_608a6217).

    Stands on GIT — the author-agnostic change truth. `git status` on the SwarmWS
    tree + every bound source worktree sees main-agent / sub-agent / CLI-subprocess /
    post-session-hook writes ALIKE (unlike process-tracing, which the SDK's sidechain
    filtering makes blind to sub-agent writes). Classifies every changed path via
    needs_human_review and prints the four buckets as JSON:
      {"content":[...], "knowledge":[...], "source":[...], "process":[...]}
    The COMPLETE step surfaces content+knowledge to Canvas immediately (file_changed)
    and batch-emits source as PR-review rows via the surface_run_outputs tool
    (run_b8ea6d5c — the LOCAL_PR aggregate was removed). Never mutates any tree.
    """
    from core.run_surface_changes import sweep_run_changes

    ws_root = _get_workspace()
    buckets = sweep_run_changes(swarmws_root=ws_root)
    print(json.dumps(buckets.as_dict()))


def _path_exists_in_repo(f: str, root: str) -> bool:
    """True if the run-recorded path ``f`` exists on disk, anchored to the resolved
    repo ``root`` (run_14e560ed, Gate-2 HIGH).

    An ABSOLUTE path is checked directly. A REPO-RELATIVE path (a supported
    files_touched form — build.md) is resolved against ``root``, NEVER the process
    cwd: in a multi-repo run cwd equals only one root at a time, so a bare
    ``Path(f).exists()`` would resolve a relative path against the wrong dir and
    mis-drop a REAL file in another repo as a ghost — the exact cwd trap the
    dirty-set block already avoids via ``git ls-files``.
    """
    p = Path(f)
    return p.exists() if p.is_absolute() else (Path(root) / f).exists()


def cmd_run_commit(args, reg: ArtifactRegistry) -> None:
    """Auto local-commit THIS run's files after PUSH-READY (A1, run_76932250).

    LOCAL COMMIT ONLY — never pushes (STEERING #5: push is user-initiated).
    Stages EXACTLY the files this run recorded in ``files_touched`` via
    ``git add -- <path>`` (pathspec), NEVER ``git add -A`` — so a parallel
    session's in-flight edits can't be swept in (R29). Files may live in the
    source repo OR the SwarmWS workspace; each file is committed in the git
    repo that actually contains it (grouped by repo root), so a run that
    touched both gets one commit per repo.

    Safety valves:
      - PUSH-READY gate must have passed (deliver stage push_ready=true), else refuse.
      - files_touched must be non-empty, else refuse (nothing to commit).
      - If the working tree has changes NOT in files_touched, WARN + list them
        (a forgotten record surfaces loudly) but still commit only tracked files.
    """
    import subprocess
    from collections import defaultdict

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    # ── Gate 1: PUSH-READY must have passed ──────────────────────────────────
    deliver_rec = next(
        (s for s in run_state.get("stages", [])
         if s.get("stage", s.get("name", "")) == "deliver"
         and s.get("status") in ("completed", "done")),
        None,
    )
    push_ready = bool(deliver_rec and deliver_rec.get("push_ready", False))
    if not push_ready and not getattr(args, "force", False):
        print(json.dumps({
            "blocked": True,
            "error": "run-commit refused: deliver stage is not PUSH-READY. "
                     "Auto-commit runs only after the push-ready gate passes. "
                     "Pass --force to override.",
        }), file=sys.stderr)
        sys.exit(2)

    # ── Gate 2: files_touched must be non-empty ──────────────────────────────
    files_touched = run_state.get("files_touched", [])
    if not files_touched:
        print(json.dumps({
            "blocked": True,
            "error": "run-commit refused: run.json has no files_touched. BUILD must "
                     "record written files via `run-update --files-touched '[...]'`. "
                     "Nothing to commit (this run tracked zero files).",
        }), file=sys.stderr)
        sys.exit(2)

    # ── Resolve each file to its git repo root; group so multi-repo runs get
    #    one commit per repo. Never `git add -A`. ────────────────────────────
    def _repo_root(path: str) -> str | None:
        p = Path(path)
        cwd = str(p.parent if p.is_absolute() else Path.cwd())
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=cwd, capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    by_repo: dict[str, list[str]] = defaultdict(list)
    unresolved: list[str] = []
    for f in files_touched:
        root = _repo_root(f)
        if root:
            by_repo[root].append(f)
        else:
            unresolved.append(f)

    requirement = run_state.get("requirement", "")[:72] or "pipeline changes"
    commit_msg = f"{requirement}\n\n(auto local-commit, run {args.run_id}; not pushed)"

    committed, warnings = [], []
    if unresolved:
        warnings.append(f"{len(unresolved)} file(s) not in any git repo, skipped: {unresolved}")

    for root, files in by_repo.items():
        # Warn if the working tree has changes outside this run's files (forgotten record)
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root, capture_output=True, text=True, timeout=10,
            )
            dirty = {ln[3:].strip() for ln in status.stdout.splitlines() if ln.strip()}
            # Repo-relative path of each run file via git itself — robust for BOTH
            # absolute and relative inputs (Path.resolve() would resolve a relative
            # path against the process cwd, misfiring when cwd != repo root).
            tracked_rel = set()
            for f in files:
                r = subprocess.run(
                    ["git", "-C", root, "ls-files", "--full-name", "--", f],
                    capture_output=True, text=True, timeout=10,
                )
                for ln in r.stdout.splitlines():
                    if ln.strip():
                        tracked_rel.add(ln.strip())
            untracked_by_run = dirty - tracked_rel
            if untracked_by_run:
                warnings.append(
                    f"[{root}] working tree has {len(untracked_by_run)} change(s) NOT tracked by "
                    f"this run (left uncommitted — verify they belong to another session): "
                    f"{sorted(untracked_by_run)[:10]}"
                )
        except (subprocess.TimeoutExpired, OSError):
            pass

        # Stage ONLY this run's files (pathspec — never -A). Filter to paths that
        # ACTUALLY EXIST on disk first (run_14e560ed): BUILD sometimes records a
        # HALLUCINATED path in files_touched (a path the agent believed it wrote but
        # didn't — e.g. desktop/src/lib/x.ts when the real file is components/x.ts).
        # `git add -- <real> <ghost>` fails ENTIRELY with exit 128 ("pathspec did not
        # match any files") and stages NOTHING → no commit → commits[] empty → the
        # Canvas finish-surface (build_surface_events reads commits[]) shows nothing.
        # Existence covers BOTH a tracked-modified file AND a brand-new untracked
        # file (git ls-files would miss the new one); a ghost path fails → drop.
        #
        # Existence is anchored to the already-resolved repo `root`, NOT the process
        # cwd (Gate-2 HIGH) — see _path_exists_in_repo. files_touched may be
        # REPO-RELATIVE (a supported input); a cwd-relative check would mis-drop a
        # real file in another repo as a ghost in a multi-repo run.
        real_files = [f for f in files if _path_exists_in_repo(f, root)]
        ghosts = [f for f in files if f not in real_files]
        if ghosts:
            warnings.append(
                f"[{root}] {len(ghosts)} recorded path(s) do not exist on disk, "
                f"dropped (BUILD recorded a wrong path): {ghosts[:10]}"
            )
        if not real_files:
            warnings.append(f"[{root}] no recorded files exist on disk — nothing to stage")
            continue
        add = subprocess.run(["git", "-C", root, "add", "--", *real_files],
                             capture_output=True, text=True, timeout=15)
        if add.returncode != 0:
            warnings.append(f"[{root}] git add failed: {add.stderr.strip()[:200]}")
            continue

        # Anything actually staged? (files may be unchanged)
        staged = subprocess.run(["git", "-C", root, "diff", "--cached", "--name-only"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if not staged:
            warnings.append(f"[{root}] no staged changes for tracked files (already committed?)")
            continue

        commit = subprocess.run(["git", "-C", root, "commit", "-m", commit_msg],
                                capture_output=True, text=True, timeout=15)
        if commit.returncode == 0:
            sha = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            committed.append({"repo": root, "sha": sha, "files": staged.splitlines()})
        else:
            warnings.append(f"[{root}] git commit failed: {commit.stderr.strip()[:200]}")

    # ── G1 (run_f8494370): persist committed shas to run.json.commits so the
    #    retro-analytics dashboard can trace WHICH run produced WHICH commit.
    #    The sha is already in hand (committed[] above) — this just records it.
    #    Gate-1 fix: RE-READ run.json fresh right before the write (run_state at
    #    the top of this fn is stale — a concurrent stage `run-update` may have
    #    written since), then merge ONLY the `commits` key, dedup by sha, and
    #    atomic tmp+replace. We never write back the stale full run_state, so a
    #    parallel stage update is not clobbered. R29-safe: `committed` holds ONLY
    #    this run's own commit results (per-repo pathspec add), never a git HEAD
    #    guess — so no parallel session's commit can leak in.
    if committed:
        try:
            fresh = json.loads(run_file.read_text(encoding="utf-8"))
            existing = fresh.get("commits", [])
            seen_shas = {c.get("sha") for c in existing if isinstance(c, dict)}
            for c in committed:
                if c.get("sha") not in seen_shas:
                    existing.append(c)
                    seen_shas.add(c.get("sha"))
            fresh["commits"] = existing
            tmp = run_file.with_suffix(".commits.tmp")
            tmp.write_text(json.dumps(fresh, indent=2), encoding="utf-8")
            tmp.replace(run_file)
        except (OSError, json.JSONDecodeError) as e:
            warnings.append(f"commits recorded to git but not persisted to run.json: {e}")

    # ── AC7 (run_dcce7023): generate the pipeline-finish "local PR" ──────────
    # Source changes are NOT displayed per-file mid-run (they carry kind=source →
    # excluded from the Canvas rail). Their review home is HERE: one aggregated
    # local-PR at finish (XG: 真实 repo 改动 …… 最后 commits 方式处理, 中途不 display).
    # Files come from `committed[].files` = `git diff --cached --name-only` — the
    # ACTUALLY-committed set (git diff ∩ files_touched), never a bare base..HEAD
    # range (which would bleed in a parallel session's commits — F-NEW-3 / R29).
    # LOCAL_PR.md generation REMOVED (run_b8ea6d5c): the aggregated doc had no review
    # value (it never enters the codebase, it is not what the user delivers). The
    # review deliverable is now per-file PR-review ROWS in the Canvas OUTPUTS rail,
    # emitted at COMPLETE by the `surface_run_outputs` tool (channel A). run-commit
    # only records the commits; surfacing happens on the live COMPLETE turn.
    print(json.dumps({
        "committed": committed,
        "warnings": warnings,
        "pushed": False,  # NEVER — push is user-initiated (STEERING #5)
        "note": ("Local commit only. At COMPLETE, call surface_run_outputs(run_id) to "
                 "show these files as PR-review rows in Canvas. To push: "
                 "`git push origin main` (user decision)."),
    }, indent=2))


# _write_local_pr REMOVED (run_b8ea6d5c): LOCAL_PR.md aggregation deleted. The
# review deliverable is now per-file PR-review rows in the Canvas OUTPUTS rail,
# emitted at COMPLETE by the surface_run_outputs tool (ui_actions.build_surface_events
# → orchestrator observe-emit). run-commit records commits; it no longer writes a doc.


# ── release-gate: the code-enforced CI-green gate for s_swarm-release 7b ──────
#
# Design A (run_9fec1fb1, 2026-07-04): the ONLY writer of the CI-green marker that
# `release_publish_guard` (security_hooks.py) reads to allow `gh release create`.
# This is the "check CI" half; the hook is the "enforce" half. Splitting it this
# way keeps ALL network I/O (the `gh run list` call, which can hang) OUT of the
# <5s PreToolUse hook — the hook only reads a local file + compares HEAD. That is
# the whole point of the marker design: we do NOT reintroduce the foreground-timeout
# hang trap (the exact bug the 7b runbook poll had, and that `gh run watch` has)
# inside the hook path.
#
# Marker: Projects/<project>/.artifacts/.release-ci-green.json
#   {"head_sha": "<40-char>", "run_id": <int>, "conclusion": "success", "ts": "<iso>"}
# The guard allows publish IFF marker.head_sha == current git HEAD. A stale marker
# (previous release's HEAD) therefore authorizes nothing on a new HEAD — fail-closed.

def _release_marker_path(project: str) -> Path:
    return (_get_workspace() / "Projects" / project / ".artifacts"
            / ".release-ci-green.json")


def cmd_release_gate(args, reg: "ArtifactRegistry") -> None:
    """CI-green gate for the release skill's Stage 7b (design A, run_9fec1fb1).

    --poll: do ONE `gh run list` check for the CI run on the CURRENT git HEAD.
      - CI conclusion == success  → atomically write the marker, print PASS, exit 0
      - CI completed but red       → print BLOCK + failing conclusion, exit 1 (no marker)
      - CI not done / not registered → print WAIT, exit 3 (agent should sleep + re-poll)
    --clear: delete the marker (call after a successful publish, or to reset).
    --status: print current marker vs HEAD without touching CI.

    NEVER loops internally — the AGENT drives polling (one fast call per invocation),
    so no long-lived process can be foreground-timeout-killed. Bounded git/gh calls
    each carry a timeout.
    """
    import subprocess
    from datetime import datetime, timezone

    marker = _release_marker_path(args.project)

    ref = getattr(args, "ref", None)  # optional tag/sha of the commit BEING RELEASED

    def _head_and_root() -> tuple[str | None, str | None]:
        """(commit sha, repo root) of the CWD's git repo. The CLI is run by the agent
        via `cd $SWARMAI_ROOT && ...`, so this resolves the SOURCE repo. Both are
        recorded in the marker so the (differently-cwd'd) hook can re-resolve against
        the SAME repo via `git -C <repo_root>` — never its own daemon cwd.

        When `--ref <tag-or-sha>` is given, resolve THAT ref to its commit
        (`<ref>^{commit}` — derefs an annotated tag to the commit it points at, and
        is idempotent on a plain sha). This is the fix for tag-based releases: the
        gate must verify the commit BEING RELEASED (the tag target), not the moving
        branch tip. No `--ref` → resolve HEAD (unchanged legacy behavior)."""
        target = f"{ref}^{{commit}}" if ref else "HEAD"
        try:
            r = subprocess.run(["git", "rev-parse", target, "--show-toplevel"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return None, None
            lines = r.stdout.strip().splitlines()
            head = lines[0].strip() if lines else None
            root = lines[1].strip() if len(lines) > 1 else None
            return head, root
        except (subprocess.TimeoutExpired, OSError):
            return None, None

    def _head() -> str | None:
        return _head_and_root()[0]

    if getattr(args, "clear", False):
        existed = marker.exists()
        marker.unlink(missing_ok=True)
        print(json.dumps({"cleared": existed, "marker": str(marker)}))
        return

    head, repo_root = _head_and_root()
    if not head:
        print(json.dumps({"error": "cannot resolve git HEAD (not a repo / git unavailable)"}),
              file=sys.stderr)
        sys.exit(2)

    if getattr(args, "status", False):
        cur = None
        if marker.exists():
            try:
                cur = json.loads(marker.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cur = None
        matches = bool(cur and cur.get("head_sha") == head)
        print(json.dumps({"head": head, "marker": cur, "authorizes_current_head": matches}))
        return

    # --poll (default): one gh check for the CI run on the target commit.
    # With --ref, query by the resolved COMMIT (`gh run list --commit <sha>`), NOT
    # a limited `--branch main` window: a re-pointed tag's commit can sit many
    # commits behind the moving tip and fall outside `--limit N`, making the gate
    # falsely WAIT forever (the exact class of bug this fix exists to kill). The
    # commit-filtered query is exact and window-independent. No --ref → legacy
    # branch-tip scan (unchanged).
    gh_cmd = (["gh", "run", "list", "--commit", head, "--limit", "20",
               "--json", "databaseId,name,headSha,status,conclusion"]
              if ref else
              ["gh", "run", "list", "--branch", "main", "--limit", "8",
               "--json", "databaseId,name,headSha,status,conclusion"])
    try:
        r = subprocess.run(gh_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        print(json.dumps({"state": "WAIT", "reason": "gh run list timed out (30s) — retry"}),
              file=sys.stderr)
        sys.exit(3)
    except OSError as e:
        print(json.dumps({"error": f"gh unavailable: {e}"}), file=sys.stderr)
        sys.exit(2)

    if r.returncode != 0:
        print(json.dumps({"state": "WAIT", "reason": f"gh error: {r.stderr.strip()[:160]}"}),
              file=sys.stderr)
        sys.exit(3)

    try:
        runs = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"state": "WAIT", "reason": "gh returned non-JSON — retry"}),
              file=sys.stderr)
        sys.exit(3)

    ci = next((x for x in runs
               if x.get("name") == "CI" and (x.get("headSha") or "").startswith(head[:8])),
              None)
    if ci is None:
        print(json.dumps({"state": "WAIT", "reason": "CI run not registered for HEAD yet",
                          "head": head}))
        sys.exit(3)

    status, concl = ci.get("status"), ci.get("conclusion")
    if status != "completed":
        print(json.dumps({"state": "WAIT", "status": status, "run_id": ci.get("databaseId"),
                          "head": head}))
        sys.exit(3)

    if concl == "success":
        # Atomic write (temp + replace) — mirror the file-write discipline used elsewhere.
        marker.parent.mkdir(parents=True, exist_ok=True)
        payload = {"head_sha": head, "repo_root": repo_root,
                   "run_id": ci.get("databaseId"),
                   "conclusion": "success",
                   "ts": datetime.now(timezone.utc).isoformat()}
        # Record the released ref (tag/sha) so the publish guard can verify the
        # PUBLISHED tag derefs to this same commit — the marker is authoritative,
        # the hook does one LOCAL deref and compares (no network). Absent when the
        # gate ran without --ref (legacy branch-tip release → hook falls back to HEAD).
        if ref:
            payload["tag"] = ref
        tmp = marker.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(marker)
        print(json.dumps({"state": "PASS", **payload, "marker": str(marker)}))
        return

    # completed + red
    print(json.dumps({"state": "BLOCK", "conclusion": concl,
                      "run_id": ci.get("databaseId"), "head": head,
                      "hint": "gh run view <run_id> --json jobs → fix → push → re-poll"}),
          file=sys.stderr)
    sys.exit(1)


def cmd_run_get(args, reg: ArtifactRegistry) -> None:
    """Get a pipeline run's current state."""
    if args.run_id:
        run_file = _resolve_run_file(args.project, args.run_id)
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
        print(json.dumps(run_state, indent=2))
        return

    # List all pipeline runs for this project (scan both new and legacy paths)
    runs = []
    artifacts_dir = _get_workspace() / "Projects" / args.project / ".artifacts"

    # New path: .artifacts/runs/*/run.json
    runs_dir = artifacts_dir / "runs"
    if runs_dir.exists():
        for rd in sorted(runs_dir.iterdir(), reverse=True):
            run_file = rd / "run.json"
            if run_file.exists():
                try:
                    state = json.loads(run_file.read_text(encoding="utf-8"))
                    runs.append(_run_summary(state))
                except (json.JSONDecodeError, KeyError):
                    continue

    # Legacy path: .artifacts/pipeline-run-*.json
    if artifacts_dir.exists():
        for f in sorted(artifacts_dir.glob("pipeline-run-*.json"), reverse=True):
            try:
                state = json.loads(f.read_text(encoding="utf-8"))
                # Skip if already found via new path
                if any(r["id"] == state["id"] for r in runs):
                    continue
                runs.append(_run_summary(state))
            except (json.JSONDecodeError, KeyError):
                continue

    print(json.dumps({"runs": runs, "count": len(runs)}, indent=2))


def _run_summary(state: dict) -> dict:
    """Extract summary fields from a pipeline run state."""
    return {
        "id": state["id"],
        "requirement": state["requirement"][:80],
        "status": state["status"],
        "profile": state.get("profile"),
        "stages_completed": sum(
            1 for s in state.get("stages", []) if s.get("status") == "completed"
        ),
        "created_at": state["created_at"],
    }


# ── Checkpoint guard vocabulary (single source of truth) ──────────────
# True triggers: a checkpoint with any of these in its reason is a REAL,
# measurement- or event-backed pause and is always allowed.
_CHECKPOINT_TRUE_TRIGGERS = (
    "budget", "l2", "block", "error", "crash", "escalat", "stuck",
    "judgment", "retry", "gate_spawn_blocked", "exhaust",
    "git revert", "external", "mutation", "re-baseline",  # event-triggers (GC11 kept half)
)
# Prefix-stems: triggers meant to match a word-START + any inflection
# ("escalat" → escalate/escalated/escalation). Without this they were matched as
# whole words (\bescalat\b), which never hit a real inflection → the stem was dead
# (Gate-2 informational finding, run_17e3399c). Listed explicitly so the default
# stays strict whole-word for everything else (no substring leaks).
_CHECKPOINT_TRUE_TRIGGER_STEMS = frozenset({"escalat"})
# Confabulation denylist: self-state narratives that are NOT measurable signals.
# A reason matching these is force-blocked (overridable ONLY by --force-checkpoint),
# even if it ALSO contains a true-trigger word — fake caution must never ride in
# on a borrowed justification. This is the structural fix for the
# "confabulated-self-state" bug class (CLASS A's evasion-mirror): the agent has no
# fatigue/session-length state to report; such claims are confabulation, not data.
_CHECKPOINT_CONFABULATION_DENYLIST = (
    r"\bfatigue\b", r"\btired\b", "疲劳", "疲倦", "累了", "累。",
    r"session\b.*\blong\b", r"\blong\b.*\bsession\b", "session 长", "长 session", "很长",
    r"\bbeen at this\b", r"\ba while\b", r"\bstepping back\b",
    "quality risk", r"quality.*degrad", "降智",
    r"context\b.*\bfull\b", "context 满", r"context.*getting full", r"lots of context",
    "clean attention", "fresh-context", "fresh context", "fresh session", "fresh start",
    r"\bfresh\b.*\bstart\b", r"\bclarity\b", r"\bbreather\b",
    r"\bfeeling\b", r"\bi feel\b", r"sense that", r"i think we should pause",
)


def _checkpoint_reason_has_true_trigger(reason: str) -> bool:
    """True iff reason contains a true-trigger as a WHOLE WORD (not substring).

    Substring matching let 'blocked'/'roadblock' satisfy 'block' and 'budget hygiene'
    satisfy 'budget' — a confabulation reason rode a borrowed keyword (adversarial
    run_a822b3e8). Word-boundary match closes that.
    """
    r = reason.lower()
    for t in _CHECKPOINT_TRUE_TRIGGERS:
        if " " in t or "-" in t or "_" in t:
            # multi-word/event triggers (e.g. "git revert", "re-baseline") match as phrase
            pat = re.escape(t)
        elif t in _CHECKPOINT_TRUE_TRIGGER_STEMS:
            # prefix-stems (e.g. "escalat" → escalat/escalate/escalation/escalated):
            # word-START boundary + allow trailing word chars. A bare \bescalat\b
            # never matched any real inflection (the trailing letters are word chars),
            # so the stem was DEAD — this revives it without reopening substring leaks
            # ('escalat' still can't match mid-word like 'deescalation' would need a
            # leading boundary, which \b provides).
            pat = rf"\b{re.escape(t)}\w*"
        else:
            # single whole-word tokens must match on both boundaries (so 'block'
            # does NOT match 'roadblock'/'blocked' — run_a822b3e8).
            pat = rf"\b{re.escape(t)}\b"
        if re.search(pat, r):
            return True
    return False


def _checkpoint_reason_hits_denylist(reason: str) -> bool:
    """True iff reason matches any confabulation pattern (already word-bounded)."""
    r = reason.lower()
    return any(re.search(p, r) for p in _CHECKPOINT_CONFABULATION_DENYLIST)


def _compute_should_checkpoint(run_state: dict, project: str) -> dict:
    """Compute should_checkpoint + budget numbers for a run (single source).

    Extracted so cmd_run_budget AND the checkpoint guard share ONE calculation
    (STEERING #3 — no duplicate logic). Returns the dict run-budget reports.
    """
    budget = run_state.get("budget", _estimate_session_budget(project))
    consumed = sum(s.get("token_cost", 0) for s in run_state.get("stages", []))
    remaining = budget["session_total"] - consumed
    usable = remaining - budget["checkpoint_reserve"]

    completed_stages = {s.get("stage", s.get("name", "unknown")) for s in run_state.get("stages", []) if s.get("status") == "completed"}
    profile_stages = _get_profile_stages(run_state.get("profile", "full"))
    next_stage = next((s for s in profile_stages if s not in completed_stages), None)
    next_stage_estimate = budget.get("stage_estimates", DEFAULT_STAGE_BUDGETS).get(next_stage, 30_000) if next_stage else 0

    should_checkpoint = next_stage is not None and usable < next_stage_estimate
    pct_consumed = consumed / budget["session_total"] if budget["session_total"] > 0 else 0
    if pct_consumed > 0.7 and next_stage:
        should_checkpoint = True

    return {
        "consumed": consumed, "remaining": remaining, "usable": usable,
        "pct_consumed": round(pct_consumed * 100, 1), "next_stage": next_stage,
        "next_stage_estimate": next_stage_estimate, "should_checkpoint": should_checkpoint,
        "budget_total": budget["session_total"],
        "calibration_source": budget.get("calibration_source", "defaults"),
    }


def cmd_run_checkpoint(args, reg: ArtifactRegistry) -> None:
    """Atomic checkpoint: pause run + publish checkpoint artifact + create Radar todo.

    HARD GATE (was a steamroll-able warning until run_a822b3e8): a checkpoint is
    BLOCKED — exit 2, before ANY state mutation — when the measurement says there
    is no reason to pause (should_checkpoint=false) AND the reason carries no true
    trigger AND --force-checkpoint was not passed. A confabulation-denylist reason
    (fatigue / "session long" / "fresh-context" / quality-risk) is force-blocked
    regardless of should_checkpoint, overridable only by --force-checkpoint.

    Why a block, not a warning: the prior warning fired and was ignored in the
    very session this fix was built (run_1e2e663b checkpointed at 3.4% budget on
    a "fresh-context clean attention" reason). Model proposes; the OS, reading the
    measurement, disposes. A judgment class that has misfired repeatedly does not
    get to make the call — the gate makes it.
    """
    import sys as _sys
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    # ── CONFABULATION GUARD (hard block, before any mutation) ──
    forced = getattr(args, "force_checkpoint", False)
    budget_info = _compute_should_checkpoint(run_state, args.project)
    measured_checkpoint = budget_info["should_checkpoint"]
    has_true_trigger = _checkpoint_reason_has_true_trigger(args.reason or "")
    hits_denylist = _checkpoint_reason_hits_denylist(args.reason or "")

    # Block conditions (either one), unless explicitly forced:
    #  (a) confabulation reason — fake self-state caution
    #  (b) no measured need AND no true trigger — feeling-based pause
    block = (not forced) and (
        hits_denylist or (not measured_checkpoint and not has_true_trigger)
    )
    if block:
        why = ("reason matches the confabulation denylist (self-state is not a "
               "measurable signal — you are stateless per-turn)" if hits_denylist
               else "should_checkpoint=false and no true-trigger reason")
        print(json.dumps({
            "blocked": True,
            "error": "CHECKPOINT REFUSED — " + why + ". continue, period.",
            "measurement": {
                "should_checkpoint": measured_checkpoint,
                "pct_consumed": budget_info["pct_consumed"],
                "consumed": budget_info["consumed"],
                "usable": budget_info["usable"],
                "next_stage": budget_info["next_stage"],
            },
            "reason_given": args.reason,
            "remedy": ("If this is a REAL pause, state a true trigger "
                       "(judgment-class decision / L2 block / retry-exhausted / "
                       "budget / external-git-mutation) or pass --force-checkpoint "
                       "with a measurement-backed justification."),
        }), file=_sys.stderr)
        _sys.exit(2)

    # 1. Pause the run
    run_state["status"] = "paused"
    run_state["updated_at"] = now

    # Store checkpoint metadata in the run state
    completed_stages = [s.get("stage", s.get("name", "unknown")) for s in run_state["stages"] if s.get("status") == "completed"]
    checkpoint_meta = {
        "reason": args.reason,
        "stage": args.stage,
        "checkpointed_at": now,
        "completed_stages": completed_stages,
        "taste_decisions_pending": len(run_state.get("taste_decisions", [])),
        "forced": forced,  # True = guard overridden via --force-checkpoint (auditable)
        "should_checkpoint_at_pause": measured_checkpoint,
    }
    run_state["checkpoint"] = checkpoint_meta
    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    # 2. Publish checkpoint artifact to the registry
    checkpoint_data = {
        "pipeline_id": args.run_id,
        "project": args.project,
        "requirement": run_state["requirement"],
        "completed_stages": [
            {
                "stage": s.get("stage", s.get("name", "unknown")),
                "artifact_id": s.get("artifact_id"),
                "notes": s.get("notes"),
            }
            for s in run_state["stages"]
            if s.get("status") == "completed"
        ],
        "next_stage": args.stage,
        "reason": args.reason,
        "taste_decisions": run_state.get("taste_decisions", []),
        "budget": run_state.get("budget"),
    }
    try:
        artifact_id = reg.publish(
            project=args.project,
            artifact_type="checkpoint",
            data=checkpoint_data,
            producer="s_autonomous-pipeline",
            summary=f"Pipeline paused at {args.stage}: {args.reason}",
            topic=args.run_id,
            run_id=args.run_id,
        )
    except (ValueError, FileNotFoundError):
        artifact_id = None

    # Pipeline-pause visibility lives in run.json (read by the Pipeline overlay)
    # and the paused_run Need-You source — NOT the user ToDo surface. No todo write:
    # the ToDo card is a pure user-planning surface, system signals keep their own homes.
    result = {
        "pipeline_id": args.run_id,
        "status": "paused",
        "checkpoint_artifact": artifact_id,
        "reason": args.reason,
        "next_stage": args.stage,
    }
    print(json.dumps(result, indent=2))


def cmd_run_history(args, reg: ArtifactRegistry) -> None:
    """Show historical token costs per stage from completed pipeline runs.

    Used for calibration: the agent reads this to know how many tokens
    each stage actually consumes, not just the default estimates.
    """
    runs = _load_completed_runs(args.project, limit=args.limit)
    if not runs:
        print(json.dumps({
            "project": args.project,
            "message": "No completed pipeline runs found",
            "stage_averages": {},
            "calibration": "defaults",
        }))
        return

    # Aggregate token costs per stage
    stage_costs: dict[str, list[int]] = {}
    for r in runs:
        for s in r.get("stages", []):
            name = s.get("stage", "unknown")
            cost = s.get("token_cost", 0)
            if cost > 0:
                stage_costs.setdefault(name, []).append(cost)

    averages = {}
    for stage, costs in sorted(stage_costs.items()):
        avg = sum(costs) / len(costs)
        averages[stage] = {
            "avg_tokens": int(avg),
            "min_tokens": min(costs),
            "max_tokens": max(costs),
            "samples": len(costs),
            "calibrated_estimate": int(avg * 1.2),  # +20% buffer
        }

    # Pipeline-level stats
    run_totals = []
    for r in runs:
        total = sum(s.get("token_cost", 0) for s in r.get("stages", []))
        if total > 0:
            run_totals.append(total)

    pipeline_stats = {}
    if run_totals:
        pipeline_stats = {
            "avg_total_tokens": int(sum(run_totals) / len(run_totals)),
            "min_total_tokens": min(run_totals),
            "max_total_tokens": max(run_totals),
            "runs_analyzed": len(run_totals),
            "fits_single_session": int(sum(run_totals) / len(run_totals)) < SESSION_BUDGET,
        }

    print(json.dumps({
        "project": args.project,
        "stage_averages": averages,
        "pipeline_stats": pipeline_stats,
        "calibration": "historical",
    }, indent=2))


def cmd_run_budget(args, reg: ArtifactRegistry) -> None:
    """Check budget status for an active pipeline run.

    Reports consumed tokens, remaining budget, and whether the next
    stage fits within the budget. Used by the agent to decide when
    to checkpoint.
    """
    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    # Single source of truth for should_checkpoint + budget numbers (STEERING #3).
    info = _compute_should_checkpoint(run_state, args.project)
    next_stage = info["next_stage"]
    result = {
        "pipeline_id": args.run_id,
        "budget_total": info["budget_total"],
        "consumed": info["consumed"],
        "remaining": info["remaining"],
        "usable": info["usable"],
        "pct_consumed": info["pct_consumed"],
        "next_stage": next_stage,
        "next_stage_estimate": info["next_stage_estimate"],
        "should_checkpoint": info["should_checkpoint"],
        "reason": (
            f"Budget insufficient for {next_stage} (need {info['next_stage_estimate']}, have {info['usable']})"
            if info["should_checkpoint"] and info["usable"] < info["next_stage_estimate"]
            else f"Context quality degradation (>{int(info['consumed'] / info['budget_total'] * 100) if info['budget_total'] else 0}% consumed)"
            if info["should_checkpoint"]
            else "Budget OK"
        ),
        "calibration_source": info["calibration_source"],
    }
    print(json.dumps(result, indent=2))


_get_profile_stages = get_profile_stages  # alias for backward compat within this file


def _status_entry(state: dict, project_name: str) -> dict:
    """Build a dashboard entry from a pipeline run state dict."""
    completed_stages = [s for s in state.get("stages", []) if s.get("status") == "completed"]
    total_stages = len(_get_profile_stages(state.get("profile")))
    consumed = sum(s.get("token_cost", 0) for s in state.get("stages", []))
    # Terminal-but-crashed presentation (parity with routers/pipelines.py _to_response):
    # a run that finished all stages (completed reflect/deliver) but crashed before
    # `run-update --status completed` is left status=running/paused on disk. Present it
    # as completed here too, so the CLI `run-status` dashboard and the web dashboard
    # agree — otherwise the two surfaces split-brain (CLI "running" vs web "completed")
    # for the identical run.json. Read-path only; disk is not mutated. Explicit terminal
    # statuses (failed/cancelled/abandoned) pass through is_terminal_run unchanged.
    raw_status = state.get("status", "running")
    display_status = raw_status
    if raw_status in ("running", "paused") and is_terminal_run(state):
        display_status = "completed"
    return {
        "id": state["id"],
        "project": project_name,
        "requirement": state.get("requirement", "")[:80],
        "status": display_status,
        "profile": state.get("profile", "full"),
        "progress": f"{len(completed_stages)}/{total_stages}",
        "stages_completed": len(completed_stages),
        "stages_total": total_stages,
        "tokens_consumed": consumed,
        "taste_decisions": len(state.get("taste_decisions", [])),
        "checkpoint": state.get("checkpoint"),
        # Surface WHY a run was abandoned so the dashboard can distinguish an
        # unrecovered orphan (orphaned_no_resume) from a legitimate supersession
        # (superseded_by_<id>). Present-but-None for non-abandoned runs, mirroring
        # the `checkpoint` key above (additive contract — never removes a key).
        "abandon_reason": state.get("abandon_reason"),
        "created_at": state.get("created_at", ""),
        "updated_at": state.get("updated_at", ""),
    }


def cmd_run_status(args, reg: ArtifactRegistry) -> None:
    """Cross-project pipeline dashboard data.

    Returns all active and recent pipeline runs across all projects.
    Designed for the Radar sidebar pipeline panel.
    """
    workspace = _get_workspace()
    projects_dir = workspace / "Projects"
    if not projects_dir.exists():
        print(json.dumps({"pipelines": [], "count": 0}))
        return

    all_pipelines = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        artifacts_dir = project_dir / ".artifacts"
        if not artifacts_dir.exists():
            continue

        project_name = project_dir.name
        seen_ids: set[str] = set()

        # New path: runs/*/run.json
        runs_dir = artifacts_dir / "runs"
        if runs_dir.exists():
            for rd in sorted(runs_dir.iterdir(), reverse=True):
                rf = rd / "run.json"
                if rf.exists():
                    try:
                        state = json.loads(rf.read_text(encoding="utf-8"))
                        state["_project"] = project_name
                        seen_ids.add(state["id"])
                        all_pipelines.append(_status_entry(state, project_name))
                    except (json.JSONDecodeError, OSError, KeyError):
                        continue

        # Legacy path: pipeline-run-*.json
        for run_file in sorted(artifacts_dir.glob("pipeline-run-*.json"), reverse=True):
            try:
                state = json.loads(run_file.read_text(encoding="utf-8"))
                if state.get("id") in seen_ids:
                    continue
                all_pipelines.append(_status_entry(state, project_name))
            except (json.JSONDecodeError, OSError, KeyError):
                continue

    # Sort: running first, then paused, then completed. Within each group, newest first.
    # ISO timestamps sort lexicographically, so negate by prepending complement for descending.
    status_order = {"running": 0, "paused": 1, "failed": 2, "completed": 3, "cancelled": 4}
    all_pipelines.sort(key=lambda p: p.get("updated_at", ""), reverse=True)  # newest first
    all_pipelines.sort(key=lambda p: status_order.get(p["status"], 9))  # stable: preserves newest-first within group

    # Limit: show all active (running/paused), up to 5 completed per project
    active = [p for p in all_pipelines if p["status"] in ("running", "paused")]
    completed = [p for p in all_pipelines if p["status"] not in ("running", "paused")]

    if args.active_only:
        output = active
    else:
        # Keep max 5 completed per project
        seen_completed: dict[str, int] = {}
        filtered_completed = []
        for p in completed:
            count = seen_completed.get(p["project"], 0)
            if count < 5:
                filtered_completed.append(p)
                seen_completed[p["project"]] = count + 1
        output = active + filtered_completed

    # Gap-2: split abandoned into GENUINE failures (crash/OOM/orphan) vs
    # REPLACED duplicates (superseded_by_*). The superseded_by_* reason is
    # written ONLY by proactive_intelligence under a newest-completed-successor
    # guard, so it always denotes a rerun that a completed successor replaced —
    # counting it as a failure double-counts one work unit and depresses the
    # completion rate a reader/judge computes. Keep `abandoned` = genuine
    # failures; surface `superseded` separately (neither success nor failure).
    def _is_superseded(p: dict) -> bool:
        return (p["status"] == "abandoned"
                and str(p.get("abandon_reason") or "").startswith("superseded_by_"))
    abandoned_all = [p for p in all_pipelines if p["status"] == "abandoned"]
    superseded = [p for p in abandoned_all if _is_superseded(p)]
    summary = {
        "running": sum(1 for p in all_pipelines if p["status"] == "running"),
        "paused": sum(1 for p in all_pipelines if p["status"] == "paused"),
        "completed": sum(1 for p in all_pipelines if p["status"] == "completed"),
        # Genuine failures only (crash/OOM/orphan) — replaced duplicates excluded.
        "abandoned": len(abandoned_all) - len(superseded),
        # Replaced-by-completed-successor reruns; not a failure, shown for legibility.
        "superseded": len(superseded),
        "total_tokens": sum(p["tokens_consumed"] for p in all_pipelines),
    }

    print(json.dumps({
        "pipelines": output,
        "count": len(output),
        "summary": summary,
    }, indent=2))


def cmd_run_resume(args, reg: ArtifactRegistry) -> None:
    """Resume a paused pipeline run.

    Checks that all pending escalations are resolved. If yes, sets
    status back to 'running' and clears the checkpoint. If not, reports
    which escalations are still open.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    if run_state["status"] != "paused":
        print(json.dumps({
            "error": f"Pipeline is '{run_state['status']}', not 'paused'",
            "pipeline_id": args.run_id,
        }), file=sys.stderr)
        sys.exit(1)

    # Check for unresolved escalations in blocked stages
    blocked_stages = [
        s for s in run_state.get("stages", [])
        if s.get("status") == "blocked" and s.get("escalation_id")
    ]

    checkpoint = run_state.get("checkpoint", {})
    next_stage = checkpoint.get("stage") or args.stage

    # Reset budget for new session
    budget = _estimate_session_budget(args.project)

    run_state["status"] = "running"
    run_state["budget"] = budget
    run_state["updated_at"] = now
    # Keep checkpoint for reference but mark it resolved
    if "checkpoint" in run_state:
        run_state["checkpoint"]["resumed_at"] = now

    # R2: count the REAL resume execution. resume_attempts (proactive_intelligence)
    # counts directive EMITS (a throttle); this counts the agent ACTUALLY running
    # run-resume — the true paused→running transition. The two answer different
    # questions: emitted-3x-executed-0x = delivery broken (agent never picked up
    # the briefing); executed-3x = pipeline broken (resume runs, keeps failing).
    # No extra lock: cmd_run_resume is the sole writer of status→running ON THE
    # RESUME PATH (the documented auto-resume flow + directive both use run-resume,
    # not run-update --status running), and is not self-concurrent (one agent
    # resumes one run). Placed AFTER the status!=paused early-exit so a no-op never
    # counts as an execution. (The pre-existing unlocked write here is unchanged;
    # this adds one field to it.)
    run_state["resume_executions"] = run_state.get("resume_executions", 0) + 1

    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    print(json.dumps({
        "pipeline_id": args.run_id,
        "status": "running",
        "resumed_from": next_stage,
        "completed_stages": [s.get("stage", s.get("name", "unknown")) for s in run_state["stages"] if s.get("status") == "completed"],
        "budget": budget,
        "blocked_stages": [s.get("stage", s.get("name", "unknown")) for s in blocked_stages],
    }, indent=2))


def cmd_run_report(args, reg: ArtifactRegistry) -> None:
    """Generate comprehensive REPORT.md for a completed (or running) pipeline run.

    Aggregates run.json + ALL published artifacts (evaluation, design_doc,
    changeset, review, test_report, delivery) into a full retrospective report.
    Covers: evaluation rationale, design approach, TDD results, review findings,
    adversarial review, completion audit, files changed, and lessons.
    """
    from datetime import datetime, timezone

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    stages = run_state.get("stages", [])
    profile = run_state.get("profile", "full")
    requirement = run_state.get("requirement", "")

    # ── Load artifact data strictly from this run ──────────────────────
    # IMPORTANT: Only load artifacts linked by this run's stage artifact_ids
    # or matching this run's date. Never use "latest" fallback — causes
    # cross-contamination between runs.
    art_data: dict[str, dict] = {}  # stage_name -> artifact data
    run_date = (run_state.get("created_at", "") or "")[:10].replace("-", "")  # YYYYMMDD

    for s in stages:
        stage_name = s.get("stage", "?")
        art_id = s.get("artifact_id")
        if art_id and art_id != "changeset":
            loaded = _load_artifact_for_metrics(args.project, art_id)
            if loaded:
                art_data[stage_name] = loaded
        elif art_id == "changeset" and run_date:
            # Changeset stored as date-stamped file — load matching run date
            art_data["build"] = _load_artifact_by_date(args.project, "changeset", run_date) or {}

    # Date-scoped fallback for stages without manifest-linked artifacts
    # Only loads artifacts from the SAME DATE as the run (prevents cross-contamination)
    if run_date:
        if "evaluate" not in art_data or not art_data["evaluate"].get("scores"):
            dated = _load_artifact_by_date(args.project, "evaluation", run_date)
            if dated:
                art_data["evaluate"] = dated
        if "plan" not in art_data and "think" not in art_data:
            dated = _load_artifact_by_date(args.project, "design_doc", run_date)
            if dated:
                art_data["plan"] = dated
        elif "plan" in art_data and not art_data["plan"].get("approach"):
            # plan artifact from manifest lacks approach — try date-scoped
            dated = _load_artifact_by_date(args.project, "design_doc", run_date)
            if dated and dated.get("approach"):
                art_data["plan"] = dated
        if "build" not in art_data or not art_data["build"].get("files_changed"):
            dated = _load_artifact_by_date(args.project, "changeset", run_date)
            if dated:
                art_data["build"] = dated
        if "review" not in art_data:
            dated = _load_artifact_by_date(args.project, "review", run_date)
            if dated:
                art_data["review"] = dated
        if "test" not in art_data or not art_data["test"].get("total"):
            dated = _load_artifact_by_date(args.project, "test_report", run_date)
            if dated:
                art_data["test"] = dated
        if "deliver" not in art_data or not art_data["deliver"].get("title"):
            dated = _load_artifact_by_date(args.project, "delivery", run_date)
            if dated:
                art_data["deliver"] = dated

    # ── Determine skipped stages ──────────────────────────────────────
    ALL_STAGES = ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"]
    present_stages = {s.get("stage", s.get("name", "?")) for s in stages}
    skipped_stages: dict[str, str] = {}  # stage -> reason
    for stg in ALL_STAGES:
        if stg not in present_stages:
            # Determine skip reason from profile
            if profile == "trivial" and stg in ("think", "plan", "reflect"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' omits research/planning"
            elif profile == "bugfix" and stg in ("think", "review"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' uses direct fix path"
            elif profile == "research" and stg in ("build", "review", "test", "deliver"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' is research-only"
            else:
                if stg == "reflect" and profile == "full":
                    skipped_stages[stg] = "Not executed — pipeline completed before REFLECT"
                else:
                    skipped_stages[stg] = f"Skipped — not required for profile '{profile}'"

    # ── Collect decisions ─────────────────────────────────────────────
    all_decisions = []
    for s in stages:
        for d in s.get("decisions", []):
            # Decisions come from two shapes in the wild: pipeline stages write
            # list[dict] ({classification, description, ...}), but session/
            # DailyActivity-sourced decisions are list[str]. A bare string here
            # crashes `**d` (TypeError: not a mapping). Coerce defensively — the
            # report is a best-effort renderer that must never crash on a
            # malformed-but-harmless stage record. (run_e346b8ed DoD0a)
            if not isinstance(d, dict):
                d = {"description": str(d)}
            all_decisions.append({
                "stage": s.get("stage", s.get("name", "?")),
                **d,
            })

    mech = sum(1 for d in all_decisions if d.get("classification") == "mechanical")
    taste = sum(1 for d in all_decisions if d.get("classification") == "taste")
    judgment = sum(1 for d in all_decisions if d.get("classification") == "judgment")

    # ── Stage map (needed by all sections for stage-json PRIMARY reads) ──
    _stage_map = {s.get("stage", s.get("name", "?")): s for s in stages}

    # ── Section 1: TL;DR ──────────────────────────────────────────────
    delivery = art_data.get("deliver", {})
    title = delivery.get("title", requirement[:80] or "Pipeline Report")

    # ── Section 2: Evaluation ─────────────────────────────────────────
    # PRIMARY: stage-json (always populated per Rule 22), FALLBACK: published artifact
    _eval_stage_rec = _stage_map.get("evaluate", {})
    eval_data = art_data.get("evaluate", {})
    eval_scores = eval_data.get("scores", {})
    eval_recommendation = _eval_stage_rec.get("recommendation") or eval_data.get("recommendation", "?")
    eval_scope = _eval_stage_rec.get("scope") or eval_data.get("scope", "?")
    eval_criteria = _as_list(_eval_stage_rec.get("acceptance_criteria") or eval_data.get("acceptance_criteria", []))

    eval_table_lines = []
    for dim in ["strategic", "priority", "historical", "feasibility"]:
        score = eval_scores.get(dim)
        if score is not None:
            # score may be numeric (3.0) or qualitative ("high") — same untyped
            # eval_scores dict as roi below; format both safely (see roi guard).
            score_str = f"{score:.2f}" if isinstance(score, (int, float)) else str(score)
            eval_table_lines.append(f"| {dim.capitalize()} | {score_str} | |")
    roi = eval_scores.get("roi")
    if roi is not None:
        # roi may be numeric (3.2) or a qualitative string ("high") — format both.
        roi_str = f"{roi:.3f}" if isinstance(roi, (int, float)) else str(roi)
        eval_table_lines.append(f"| **ROI** | **{roi_str}** | **{eval_recommendation}** |")

    # ── Section 3: Design & Approach ──────────────────────────────────
    # PRIMARY: stage-json (think/plan records), FALLBACK: published artifacts
    _think_stage_rec = _stage_map.get("think", {})
    _plan_stage_rec = _stage_map.get("plan", {})
    design = art_data.get("plan", {})
    if not design.get("approach"):
        design = art_data.get("think", {})
    approach = _plan_stage_rec.get("approach_chosen") or _think_stage_rec.get("approach_chosen") or design.get("approach", "")
    boundaries = design.get("boundaries", {})
    success_criteria = _as_list(_plan_stage_rec.get("spec_summary") or design.get("success_criteria", design.get("acceptance_criteria", [])))
    files_to_change = _plan_stage_rec.get("files_planned") or design.get("files_to_change", [])
    # Think stage research findings
    think_data = art_data.get("think", {})
    key_findings = _as_list(_think_stage_rec.get("key_findings") or think_data.get("key_findings", []))
    alternatives = _as_list(_think_stage_rec.get("alternatives") or think_data.get("alternatives", []))

    # ── Section 4: Pipeline Execution ─────────────────────────────────
    # Show ALL 8 standard stages — executed ones with data, skipped with reason
    stage_lines = []
    stage_data_map = {s.get("stage", s.get("name", "?")): s for s in stages}
    for stg in ALL_STAGES:
        if stg in stage_data_map:
            s = stage_data_map[stg]
            status = s.get("status", "?")
            artifact = s.get("artifact_id", "-")
            tokens = s.get("token_cost", 0)
            summary = s.get("summary", "")[:50]
            stage_lines.append(f"| {stg} | {status} | {artifact} | {tokens:,} | {summary} |")
        elif stg in skipped_stages:
            stage_lines.append(f"| {stg} | ⏭ skipped | - | 0 | {skipped_stages[stg]} |")

    # ── Extract stage-json metrics (Rule 22: stage records ARE the audit trail) ──
    # These supplement artifact data with per-stage metrics that may not be in published artifacts
    # (_stage_map already defined above for Section 2-3 reads)

    # Gate 1 verdict (from build stage-json)
    _build_rec = _stage_map.get("build", {})
    gate1_verdict = _build_rec.get("gate1_verdict", "N/A")
    gate1_checks = _build_rec.get("gate1_checks", {})
    gate1_override = _build_rec.get("gate1_override", False)

    # Files changed (prefer stage-json, fallback to artifact)
    stage_files_changed = _build_rec.get("files_changed", [])

    # TDD from stage-json (test or build stage)
    _test_rec = _stage_map.get("test", {})
    stage_tdd = _test_rec.get("tdd", _build_rec.get("tdd", {}))

    # Total token cost (sum all stages)
    total_token_cost = sum(s.get("token_cost", 0) for s in stages)

    # ── Section 5: TDD Results ────────────────────────────────────────
    test_data = art_data.get("test", {})
    # Prefer stage-json tdd over artifact data
    if stage_tdd:
        test_total = stage_tdd.get("tests_total", stage_tdd.get("total", 0))
        test_passed = stage_tdd.get("tests_passed", stage_tdd.get("passed", 0))
        test_failed = stage_tdd.get("tests_failed", stage_tdd.get("failed", 0))
    else:
        test_total = test_data.get("total", test_data.get("passed", 0) + test_data.get("failed", 0))
        test_passed = test_data.get("passed", 0)
        test_failed = test_data.get("failed", 0)
    test_new = test_data.get("new_tests", stage_tdd.get("new_tests", 0))
    test_duration = test_data.get("duration_s", 0)

    changeset = art_data.get("build", {})
    tests_added = changeset.get("tests_added", test_new)

    # ── Section 6: Decision Log ───────────────────────────────────────
    decision_lines = []
    for d in all_decisions:
        decision_lines.append(
            f"| {d.get('stage', '?')} | {d.get('description', '')[:60]} | "
            f"{d.get('classification', '?')} | {d.get('reasoning', '')[:50]} |"
        )

    # ── Section 7: Quality Gates ──────────────────────────────────────
    review_data = art_data.get("review", {})
    _rf = review_data.get("findings", [])
    review_findings = _rf if isinstance(_rf, list) else []
    review_findings_count = len(review_findings) if isinstance(_rf, list) else (_rf if isinstance(_rf, int) else 0)
    review_severity = review_data.get("severity", "?")

    adversarial = delivery.get("adversarial_review", {})
    adversarial_findings = _as_list(adversarial.get("findings", []))
    adversarial_verdict = adversarial.get("verdict", "?")

    completion_audit = delivery.get("completion_audit", {})
    criteria_met = _as_list(completion_audit.get("criteria_met", []))
    criteria_unmet = _as_list(completion_audit.get("criteria_unmet", []))

    confidence_score = delivery.get("confidence_score", 0)
    if isinstance(confidence_score, dict):
        confidence_score = confidence_score.get("score", 0)

    # ── Section 8: Files Changed ──────────────────────────────────────
    # Prefer stage-json files_changed (always populated per Rule 22), fallback to artifact
    _fc = stage_files_changed if stage_files_changed else changeset.get("files_changed", [])
    files_changed = _fc if isinstance(_fc, list) else []
    files_changed_count = len(files_changed) if isinstance(_fc, list) else (_fc if isinstance(_fc, int) else 0)
    lines_added = changeset.get("lines_added", 0)
    lines_removed = changeset.get("lines_removed", 0)

    # ── Duration & timestamps ─────────────────────────────────────────
    created = run_state.get("created_at", "")
    completed = run_state.get("completed_at", "")
    duration_str = ""
    if created and completed:
        try:
            t0 = datetime.fromisoformat(created)
            t1 = datetime.fromisoformat(completed)
            mins = round((t1 - t0).total_seconds() / 60, 1)
            duration_str = f"{mins} min"
        except (ValueError, TypeError):
            pass

    # ── Validation events ─────────────────────────────────────────────
    validation_events = run_state.get("validation_events", [])
    validation_blocks = sum(1 for v in validation_events if not v.get("passed"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ══════════════════════════════════════════════════════════════════
    # BUILD REPORT
    # ══════════════════════════════════════════════════════════════════
    sections = []

    sections.append(f"""# Autonomous Pipeline Report: {title}

**Run ID:** {run_state['id']} | **Project:** {args.project} | **Profile:** {profile}
**Date:** {now} | **Duration:** {duration_str or 'N/A'} | **Tokens:** {total_token_cost:,}
**Gate 1:** {gate1_verdict}{' (override)' if gate1_override else ''} | **Files:** {files_changed_count} | **Tests:** {test_passed}/{test_total}""")

    # TL;DR
    summary_text = delivery.get("summary", requirement[:200])
    sections.append(f"""## TL;DR
{summary_text}""")

    # 1. Requirement
    sections.append(f"""## 1. Requirement
{requirement}""")

    # 2. Evaluation
    eval_section = "## 2. Evaluation\n"
    if eval_table_lines:
        eval_section += "| Dimension | Score | Recommendation |\n|---|---|---|\n"
        eval_section += "\n".join(eval_table_lines)
    else:
        eval_section += f"Recommendation: **{eval_recommendation}** | Scope: {eval_scope}"
    if eval_criteria:
        eval_section += "\n\n**Acceptance Criteria:**\n"
        for i, ac in enumerate(eval_criteria[:12], 1):
            eval_section += f"{i}. {ac}\n"
    sections.append(eval_section)

    # 3. Design & Approach
    design_section = "## 3. Design & Approach\n"
    if key_findings:
        design_section += "**Research Findings (THINK):**\n"
        for kf in key_findings[:8]:
            design_section += f"- {kf}\n"
        design_section += "\n"
    if alternatives:
        design_section += "**Alternatives Evaluated:**\n"
        for alt in alternatives[:4]:
            if isinstance(alt, dict):
                rec = " ✅ (recommended)" if alt.get("recommendation") else ""
                design_section += f"- {alt.get('constraint', '?')} — effort: {alt.get('effort', '?')}{rec}\n"
            else:
                design_section += f"- {str(alt)[:80]}\n"
        design_section += "\n"
    if approach:
        design_section += f"**Chosen Approach:** {approach}\n\n"
    if success_criteria:
        design_section += "**Success Criteria:**\n"
        for sc in success_criteria[:10]:
            design_section += f"- {sc}\n"
    if boundaries and isinstance(boundaries, dict):
        always_rules = _as_list(boundaries.get("always", []))
        never_rules = _as_list(boundaries.get("never", []))
        if always_rules:
            design_section += "\n**Always:**\n"
            for r in always_rules[:5]:
                design_section += f"- {r}\n"
        if never_rules:
            design_section += "\n**Never:**\n"
            for r in never_rules[:5]:
                design_section += f"- {r}\n"
    if not approach and not success_criteria and not key_findings:
        if "think" in skipped_stages or "plan" in skipped_stages:
            design_section += f"**⏭ Skipped** — {skipped_stages.get('think', skipped_stages.get('plan', ''))}\n"
        else:
            design_section += "(No design artifact data captured for this run)\n"
    sections.append(design_section)

    # 4. Pipeline Execution
    total_tokens = sum(s.get("token_cost", 0) for s in stages)
    exec_section = f"""## 4. Pipeline Execution
| Stage | Status | Artifact | Tokens | Summary |
|-------|--------|----------|--------|---------|
{chr(10).join(stage_lines) if stage_lines else "| (no stages) | | | | |"}

**Total tokens:** {total_tokens:,} | **Stages:** {sum(1 for s in stages if s.get('status') in ('done', 'completed'))}/{len(stages)} completed"""
    if validation_blocks:
        exec_section += f" | **Validation blocks:** {validation_blocks}"
    sections.append(exec_section)

    # 5. TDD Results
    tdd_section = f"""## 5. TDD Results
| Metric | Value |
|--------|-------|
| Tests total | {test_total} |
| Tests passed | {test_passed} |
| Tests failed | {test_failed} |
| New tests added | {tests_added} |
| Duration | {test_duration}s |
| Regressions | {test_failed} |"""
    sections.append(tdd_section)

    # 6. Decision Log
    decision_section = f"""## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
{chr(10).join(decision_lines) if decision_lines else "| (no decisions) | | | |"}

**Summary:** {mech} mechanical, {taste} taste, {judgment} judgment"""
    sections.append(decision_section)

    # 7. Quality Gates
    quality_section = "## 7. Quality Gates\n"
    quality_section += "| Gate | Result |\n|------|--------|\n"
    quality_section += f"| REVIEW (code quality) | {review_findings_count} findings ({review_severity}) |\n"
    quality_section += f"| TEST (suite) | {test_passed}/{test_total} pass |\n"
    quality_section += f"| Confidence | {confidence_score}/10 |\n"

    # 7.5 Adversarial Review
    quality_section += f"\n### 7.5 Adversarial Review\n"
    if not adversarial and "deliver" not in stage_data_map:
        quality_section += f"**⏭ NOT RUN** — deliver stage was skipped (profile: {profile})\n"
    elif not adversarial or (adversarial_verdict == "?" and not adversarial_findings):
        quality_section += "**⚠️ NOT RUN** — adversarial sub-agent was not spawned during this run.\n"
        quality_section += "This is a quality gap. Pipeline DELIVER requires adversarial review.\n"
    elif adversarial_findings:
        quality_section += f"**{len(adversarial_findings)} findings** (verdict: {adversarial_verdict})\n"
        for f in adversarial_findings[:5]:
            if isinstance(f, dict):
                quality_section += f"- [{f.get('severity', '?')}] {f.get('description', f.get('finding', ''))[:80]}\n"
            else:
                quality_section += f"- {str(f)[:80]}\n"
    else:
        quality_section += f"Verdict: **{adversarial_verdict}** | Sub-agent spawned: ✅ | Findings: 0\n"

    # 7.6 Completion Audit
    quality_section += f"\n### 7.6 Completion Audit\n"
    if criteria_met or criteria_unmet:
        for c in criteria_met:
            quality_section += f"- [x] {c}\n"
        for c in criteria_unmet:
            quality_section += f"- [ ] {c}\n"
        quality_section += f"\n**Met:** {len(criteria_met)} | **Unmet:** {len(criteria_unmet)}"
    else:
        quality_section += "(No completion audit data)\n"
    sections.append(quality_section)

    # 8. Files Changed
    files_section = "## 8. Files Changed\n"
    if files_changed:
        for f in files_changed:
            files_section += f"- `{f}`\n"
        files_section += f"\n**+{lines_added} / -{lines_removed} lines** | {files_changed_count} files"
    elif files_changed_count > 0:
        files_section += f"**{files_changed_count} files** | +{lines_added} / -{lines_removed} lines\n"
        files_section += "(file list not captured in changeset artifact)\n"
    elif "build" in skipped_stages:
        files_section += f"**⏭ Skipped** — {skipped_stages['build']}\n"
    else:
        files_section += "(No changeset data captured for this run)\n"
    sections.append(files_section)

    # 9. Lessons — inline from REFLECT stage + calibration history
    lessons_section = "## 9. Lessons\n"
    lessons_items: list[str] = []

    # Source 1: REFLECT stage record in run.json
    reflect_stage = stage_data_map.get("reflect", {})

    # Primary: structured lessons list (new format from reflect.md step 7)
    stage_lessons = reflect_stage.get("lessons", [])
    if isinstance(stage_lessons, list):
        for item in stage_lessons:
            if isinstance(item, str) and item.strip() and len(item.strip()) > 5:
                lessons_items.append(item.strip())

    # Fallback: parse summary/notes (legacy format)
    if not lessons_items:
        reflect_summary = reflect_stage.get("summary", "") or ""
        if reflect_summary:
            if ": " in reflect_summary and "lesson" in reflect_summary.lower():
                _, _, lessons_text = reflect_summary.partition(": ")
                for item in lessons_text.split(", "):
                    item = item.strip()
                    if item and len(item) > 5:
                        lessons_items.append(item)
            elif len(reflect_summary) > 10:
                lessons_items.append(reflect_summary)

    # Source 2: calibration_history in decision-strategy.json (matched by eval artifact_id)
    eval_art_id = None
    for s in stages:
        if s.get("stage") == "evaluate":
            eval_art_id = s.get("artifact_id")
            break

    if eval_art_id:
        strategy_path = Path(reg.projects_root) / args.project / "decision-strategy.json"
        if strategy_path.is_file():
            try:
                strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
                for entry in strategy.get("calibration_history", []):
                    if entry.get("evaluation_id") == eval_art_id:
                        for lesson in entry.get("lessons", []):
                            if lesson.strip() and lesson.strip() not in lessons_items:
                                lessons_items.append(lesson.strip())
            except (json.JSONDecodeError, OSError):
                pass

    # Source 3: REFLECT stage decisions (if any contain lessons)
    for d in reflect_stage.get("decisions", []):
        # Coerce list[str] shape (see DoD0a note at the decisions-collection
        # loop) — a bare string decision must not crash d.get(). (run_e346b8ed)
        desc = (d if isinstance(d, str) else d.get("description", "")).strip()
        if desc and desc not in lessons_items:
            lessons_items.append(desc)

    if lessons_items:
        for item in lessons_items:
            lessons_section += f"- {item}\n"
    elif "reflect" in skipped_stages:
        lessons_section += f"**⏭ Skipped** — {skipped_stages['reflect']}\n"
    else:
        lessons_section += "(REFLECT stage did not record lessons for this run)\n"
    sections.append(lessons_section)

    # 10. Known Gaps
    gaps_section = "## 10. Known Gaps & Attention Flags\n"
    if criteria_unmet:
        for c in criteria_unmet:
            gaps_section += f"- {c}\n"
    if validation_blocks:
        gaps_section += f"- Validator blocked {validation_blocks} time(s) during execution\n"
    if not criteria_unmet and not validation_blocks:
        gaps_section += "None identified.\n"
    sections.append(gaps_section)

    # Footer
    sections.append(f"---\n*Generated by SwarmAI Autonomous Pipeline | {now}*")

    report = "\n\n".join(sections) + "\n"

    # Write REPORT.md.
    #
    # Skip-guard discriminates AUTO-generated reports from HAND-WRITTEN ones via
    # a generator-owned run.json flag (`report_autogenerated`), NOT by sniffing
    # the report body for a footer string (a marker-substring match is the exact
    # unfalsifiable-on-reword anti-pattern IMPROVEMENT.md warns against, 3×):
    #   - HAND-WRITTEN report (flag absent/False) + no --force → SKIP (preserve the
    #     human's work — the original data-loss guard).
    #   - AUTO-generated report (flag True) → REGENERATE even without --force. This
    #     is the real fix: DELIVER generates REPORT.md early (to pass the advance
    #     gate); REFLECT then records lessons; the mandatory COMPLETE-time
    #     `run-report` call (no --force) must pick those lessons up instead of
    #     skipping and freezing a stale, lessons-less report (empirically 5/6 of
    #     recent completed runs had an EMPTY Lessons section because of this).
    report_path = run_file.parent / "REPORT.md"
    force = getattr(args, "force", False)
    already_autogenerated = bool(run_state.get("report_autogenerated"))
    if report_path.exists() and not force and not already_autogenerated:
        print(json.dumps({
            "skipped": True,
            "reason": "REPORT.md exists and is hand-written (use --force to overwrite)",
            "report_path": str(report_path),
        }))
        return

    report_path.write_text(report, encoding="utf-8")

    # Stamp the generator-owned flag so a later no-force call (COMPLETE step)
    # knows this report is auto-owned and may be regenerated to inline late
    # reflect lessons. A human who hand-edits REPORT.md does NOT set this flag,
    # so their work stays protected. Best-effort: a flag-write failure must not
    # fail report generation (the report itself already succeeded).
    if not already_autogenerated:
        try:
            fresh_state = json.loads(run_file.read_text(encoding="utf-8"))
            fresh_state["report_autogenerated"] = True
            run_file.write_text(json.dumps(fresh_state, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass

    print(json.dumps({
        "report_path": str(report_path),
        "gate1_verdict": gate1_verdict,
        "total_tokens": total_token_cost,
        "stages": len(stages),
        "decisions": len(all_decisions),
        "files_changed": files_changed_count,
        "tests_passed": test_passed,
        "tests_total": test_total,
        "regenerated": already_autogenerated,
    }))


def _record_validation_event(
    project: str, run_id: str, stage: str,
    passed: bool, errors: list[str], warnings: list[str],
    errored: list[str] | None = None,
) -> None:
    """Append a validation event to run.json.validation_events[].

    Non-critical — failures are silently ignored (metrics are best-effort).

    ``errored`` (run_55710438) names checks that could NOT run (crashed),
    distinct from content failures in ``errors``. Additive + optional so
    existing callers are unaffected.
    """
    from datetime import datetime, timezone
    errored = errored or []
    try:
        run_file = _run_dir(project, run_id) / "run.json"
        if not run_file.exists():
            return
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
        events = run_state.setdefault("validation_events", [])
        events.append({
            "stage": stage,
            "passed": passed,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errored_count": len(errored),
            "errors": errors[:5],  # Cap at 5 to avoid bloat
            "errored": errored[:5],  # checks that could not run
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass  # Best-effort — never crash pipeline for metrics


def _try_generate_metrics(
    project: str, run_id: str, run_state: dict, reg: ArtifactRegistry,
) -> None:
    """Auto-generate METRICS.json when a pipeline run completes.

    Extracts catch metrics from review/deliver artifacts + validation events.
    Non-critical — failures silently ignored.
    """
    try:
        metrics = _extract_run_metrics(project, run_id, run_state)
        metrics_file = _run_dir(project, run_id) / "METRICS.json"
        metrics_file.parent.mkdir(parents=True, exist_ok=True)
        metrics_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    except Exception:
        pass  # Best-effort


def _extract_run_metrics(project: str, run_id: str, run_state: dict) -> dict:
    """Extract comprehensive metrics from a completed pipeline run.

    Reads run.json + artifact data to produce a flat metrics dict.
    """
    from datetime import datetime, timezone

    stages = run_state.get("stages", [])
    profile = run_state.get("profile", "full")

    # --- Token metrics ---
    total_tokens = sum(s.get("token_cost", 0) for s in stages)
    stage_tokens = {
        s.get("stage", "?"): s.get("token_cost", 0)
        for s in stages
    }

    # --- Decision metrics ---
    all_decisions = []
    for s in stages:
        for d in s.get("decisions", []):
            # Coerce list[str] shape (DoD0a, run_e346b8ed) — string decisions
            # have no classification; skip the count safely rather than crash.
            all_decisions.append(d if isinstance(d, dict) else {"description": str(d)})
    decision_counts = {
        "mechanical": sum(1 for d in all_decisions if d.get("classification") == "mechanical"),
        "taste": sum(1 for d in all_decisions if d.get("classification") == "taste"),
        "judgment": sum(1 for d in all_decisions if d.get("classification") == "judgment"),
        "total": len(all_decisions),
    }

    # --- Catch metrics (from artifacts) ---
    review_findings = 0
    review_rp_checked = 0
    adversarial_findings = 0
    adversarial_high = 0
    adversarial_resolved = 0
    confidence_score = 0
    completion_all_green = False
    test_passed = 0
    test_failed = 0
    build_files_changed = 0
    build_tests_generated = 0
    _deliver_artifact_ar = None  # arena-feed fallback (artifact-only adversarial_review)

    for s in stages:
        art_id = s.get("artifact_id")
        if not art_id:
            continue

        data = _load_artifact_for_metrics(project, art_id)
        if not data:
            continue

        stage_name = s.get("stage", "?")

        if stage_name == "review":
            fc = data.get("findings_count")
            findings_list = data.get("findings", [])
            review_findings = fc if fc is not None else (len(findings_list) if isinstance(findings_list, list) else 0)
            rp = data.get("runtime_patterns", {})
            review_rp_checked = rp.get("checked", 0) if isinstance(rp, dict) else 0

        elif stage_name == "deliver":
            ar = data.get("adversarial_review", {})
            if isinstance(ar, dict):
                # Arena feed fallback source: when the deliver STAGE RECORD omits
                # adversarial_review (recorded only in the artifact), reuse this
                # already-loaded artifact copy — no extra read (run_4db42c78).
                if isinstance(ar.get("findings"), list) and ar.get("findings"):
                    _deliver_artifact_ar = ar
                findings = ar.get("findings", [])
                adversarial_findings = len(findings)
                # Count HIGH+CRITICAL, case-insensitive — consistent with the
                # confidence gate's blocking severities (_blocked_findings). A
                # bare == "HIGH" under-counted CRITICAL/lowercase relative to what
                # the gate actually blocks (Gate-2 finding #3, run_7583af5f).
                adversarial_high = sum(
                    1 for f in findings
                    if isinstance(f, dict)
                    and str(f.get("severity", "")).strip().upper() in ("HIGH", "CRITICAL")
                )
                adversarial_resolved = sum(
                    1 for f in findings
                    if isinstance(f, dict) and f.get("resolved")
                )
            cs = data.get("confidence_score", {})
            if isinstance(cs, dict):
                confidence_score = cs.get("score", 0)
            elif isinstance(cs, (int, float)):
                confidence_score = cs
            ca = data.get("completion_audit", {})
            completion_all_green = ca.get("all_green", False) if isinstance(ca, dict) else False

        elif stage_name == "test":
            test_passed = data.get("passed", 0)
            test_failed = data.get("failed", 0)

        elif stage_name == "build":
            fc = data.get("files_changed", [])
            build_files_changed = len(fc) if isinstance(fc, list) else (fc if isinstance(fc, int) else 0)
            tdd = data.get("tdd", {})
            build_tests_generated = tdd.get("tests_generated", tdd.get("smoke_tests", 0)) if isinstance(tdd, dict) else 0

    # --- Validation events ---
    validation_events = run_state.get("validation_events", [])
    validation_blocks = sum(1 for v in validation_events if not v.get("passed"))

    # --- Duration ---
    created = run_state.get("created_at", "")
    completed = run_state.get("completed_at", "")
    duration_minutes = None
    if created and completed:
        try:
            t0 = datetime.fromisoformat(created)
            t1 = datetime.fromisoformat(completed)
            duration_minutes = round((t1 - t0).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            pass

    # --- Arena feed (Gate-2 → evolution, SCOPE B, run_4db42c78) ---
    # Auto-derive an adversarial_patterns block from the deliver stage RECORD
    # (run_state), NOT the artifact — so the environment-labeled verification
    # signal is captured at the completion gate without an artifact re-read
    # (Gate-1 fix: ACTION context is sourced from run_state stages).
    #
    # ACTION (what the agent CHOSE) is kept structurally separate from LABEL
    # (what the environment VERIFIED) — the authorship trap (CLASS A) defused at
    # the data layer. This path NEVER writes corrections.jsonl / correction_tracker
    # / auto_seed (AC6): it only reads run_state and returns a metrics dict.
    #
    # v1 keys by SEVERITY, not category — persisted findings carry `severity` but
    # NOT `category` (verified: 0 of 223 adversarial_review blocks have it), so a
    # category-keyed rollup is deferred to a future run that persists category.
    adversarial_patterns = None
    _deliver_rec = next((s for s in stages if s.get("stage") == "deliver"), None)
    if _deliver_rec is not None:
        # Prefer the stage-record adversarial_review; fall back to the
        # artifact copy loaded in the deliver branch above (some runs record it
        # only in the artifact — run_4db42c78 SMOKE). No extra read either way.
        _ar = _deliver_rec.get("adversarial_review")
        if not (isinstance(_ar, dict) and _ar.get("findings")):
            _ar = _deliver_artifact_ar
        _findings = _ar.get("findings", []) if isinstance(_ar, dict) else []
        if isinstance(_findings, list) and _findings:
            def _sev(f):
                return str(f.get("severity", "")).strip().upper() if isinstance(f, dict) else ""
            _high = sum(1 for f in _findings if _sev(f) in ("HIGH", "CRITICAL"))
            _med = sum(1 for f in _findings if _sev(f) in ("MED", "MEDIUM"))
            _low = sum(1 for f in _findings if _sev(f) == "LOW")
            # `other` = findings with an unrecognized/missing severity (INFO, "",
            # non-dict). Making it explicit keeps the partition TOTAL — a consumer
            # can rely on high+med+low+other == len(findings) (Gate-2 MED: unknown
            # severities used to vanish silently, breaking that invariant).
            _other = len(_findings) - _high - _med - _low
            _resolved = sum(1 for f in _findings
                            if isinstance(f, dict) and f.get("resolved"))
            _conv = _deliver_rec.get("convergence", {})
            _conv_iters = _conv.get("iterations", 0) if isinstance(_conv, dict) else 0
            # ACTION context from run_state stage records (no artifact re-read).
            # `next(..., {})` on the FIRST deliver rec — the pipeline is a linear
            # single-deliver flow; if retry-driven multi-deliver ever lands, revisit
            # (the artifact fallback would then track the last, not this first).
            _eval_rec = next((s for s in stages if s.get("stage") == "evaluate"), {})
            _think_rec = next((s for s in stages if s.get("stage") == "think"), {})
            adversarial_patterns = {
                "by_severity": {"high": _high, "med": _med, "low": _low, "other": _other},
                "resolved": _resolved,
                "first_pass_high": _high,
                "convergence_iterations": _conv_iters,
                "action": {
                    "profile": profile,
                    "scope": _eval_rec.get("scope", ""),
                    "approach": _think_rec.get("approach_chosen", ""),
                },
                "verifier_confidence": "structured",  # never a re-judged LLM score
            }

    _metrics = {
        "run_id": run_id,
        "project": project,
        "profile": profile,
        "status": run_state.get("status", "?"),
        "stages_completed": sum(1 for s in stages if s.get("status") in ("completed", "done")),
        "stages_total": len(stages),
        "duration_minutes": duration_minutes,
        # Tokens
        "total_tokens": total_tokens,
        "stage_tokens": stage_tokens,
        # Decisions
        "decisions": decision_counts,
        # Catches
        "catches": {
            "review_findings": review_findings,
            "review_rp_checked": review_rp_checked,
            "adversarial_findings": adversarial_findings,
            "adversarial_high": adversarial_high,
            "adversarial_resolved": adversarial_resolved,
            "validation_blocks": validation_blocks,
            "test_regressions": test_failed,
        },
        # Quality
        "quality": {
            "confidence_score": confidence_score,
            "completion_all_green": completion_all_green,
            "test_passed": test_passed,
            "test_failed": test_failed,
        },
        # Build
        "build": {
            "files_changed": build_files_changed,
            "tests_generated": build_tests_generated,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Only present when Gate-2 caught findings this run (no findings → no key,
    # so a clean run produces no arena signal — label-variance protection).
    if adversarial_patterns is not None:
        _metrics["adversarial_patterns"] = adversarial_patterns
    return _metrics


def _load_artifact_for_metrics(project: str, artifact_id: str) -> dict | None:
    """Load artifact data by ID from manifest (metrics-safe: returns None on failure)."""
    ws = _get_workspace()
    manifest_file = ws / "Projects" / project / ".artifacts" / "manifest.json"
    if not manifest_file.exists():
        return None
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    for entry in manifest.get("artifacts", []):
        if entry.get("id") == artifact_id:
            data_file = artifacts_dir / entry.get("file", "")
            if data_file.exists():
                try:
                    return json.loads(data_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    return None
    return None


def _load_latest_artifact_by_type(project: str, artifact_type: str) -> dict | None:
    """Load the most recent artifact file by type prefix (e.g. 'evaluation', 'changeset').

    ⚠️ TOP-LEVEL ONLY BY DESIGN: this scans only the top-level ``.artifacts/``
    dir, never ``runs/<run_id>/``. RUN-SCOPED artifacts must be read via the
    manifest ``entry['file']`` (get_artifact/discover), NOT reconstructed by
    name — their on-disk names carry an ``-<artifact_id>`` suffix (run_fc95d24c)
    precisely because same-type same-day names collide within a run. Do NOT add
    a ``runs/<id>/{type}-{date}.json`` glob here; it would miss the id-suffixed
    files. Top-level names remain the bare ``{type}-{date}.json`` scheme.

    Artifacts are stored as <type>-YYYYMMDD.json in .artifacts/ directory.
    Returns the most recent one (sorted by filename descending).

    WARNING: Only use for non-report contexts (e.g. metrics). For report generation,
    use _load_artifact_by_date() to prevent cross-contamination between runs.
    """
    ws = _get_workspace()
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    if not artifacts_dir.is_dir():
        return None
    try:
        candidates = sorted(
            [f for f in artifacts_dir.iterdir()
             if f.name.startswith(f"{artifact_type}-") and f.suffix == ".json"],
            key=lambda p: p.name,
            reverse=True,
        )
        if not candidates:
            return None
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_artifact_by_date(project: str, artifact_type: str, date_str: str) -> dict | None:
    """Load artifact file matching exact date (YYYYMMDD format).

    Prevents cross-contamination by only loading artifacts from the same day
    as the pipeline run. Returns None if no matching file exists.
    """
    ws = _get_workspace()
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    target = artifacts_dir / f"{artifact_type}-{date_str}.json"
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def cmd_run_metrics(args, reg: ArtifactRegistry) -> None:
    """Generate or read METRICS.json for a pipeline run.

    Extracts catch rates, token costs, and quality metrics from
    run.json + artifacts. Writes METRICS.json to the run directory.
    """
    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    metrics = _extract_run_metrics(args.project, args.run_id, run_state)

    # Write METRICS.json
    metrics_file = run_file.parent / "METRICS.json"
    metrics_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(json.dumps(metrics, indent=2))


def cmd_run_analytics(args, reg: ArtifactRegistry) -> None:
    """Cross-run analytics: aggregate metrics across completed pipeline runs.

    Reports: catch rates per stage, token trends, confidence distribution,
    adversarial review ROI, and validation block frequency.
    """
    runs = _load_completed_runs(args.project, limit=args.limit)
    if not runs:
        print(json.dumps({
            "project": args.project,
            "message": "No completed pipeline runs found",
        }))
        return

    # Collect metrics from each run (generate if needed)
    all_metrics: list[dict] = []
    for run_state in runs:
        run_id = run_state["id"]
        metrics_file = _run_dir(args.project, run_id) / "METRICS.json"
        if metrics_file.exists():
            try:
                m = json.loads(metrics_file.read_text(encoding="utf-8"))
                all_metrics.append(m)
                continue
            except (json.JSONDecodeError, OSError):
                pass
        # Generate on-demand
        m = _extract_run_metrics(args.project, run_id, run_state)
        all_metrics.append(m)
        # Persist for next time
        try:
            metrics_file.parent.mkdir(parents=True, exist_ok=True)
            metrics_file.write_text(json.dumps(m, indent=2), encoding="utf-8")
        except OSError:
            pass

    if not all_metrics:
        print(json.dumps({"project": args.project, "message": "No metrics extracted"}))
        return

    n = len(all_metrics)

    # --- Token analytics ---
    total_tokens_list = [m.get("total_tokens", 0) for m in all_metrics if m.get("total_tokens", 0) > 0]
    stage_token_agg: dict[str, list[int]] = {}
    for m in all_metrics:
        for stage, cost in m.get("stage_tokens", {}).items():
            if cost > 0:
                stage_token_agg.setdefault(stage, []).append(cost)

    # --- Catch rate analytics ---
    total_review_findings = sum(m.get("catches", {}).get("review_findings", 0) for m in all_metrics)
    total_adversarial = sum(m.get("catches", {}).get("adversarial_findings", 0) for m in all_metrics)
    total_adversarial_high = sum(m.get("catches", {}).get("adversarial_high", 0) for m in all_metrics)
    total_validation_blocks = sum(m.get("catches", {}).get("validation_blocks", 0) for m in all_metrics)
    total_test_regressions = sum(m.get("catches", {}).get("test_regressions", 0) for m in all_metrics)

    runs_with_adversarial = sum(1 for m in all_metrics if m.get("catches", {}).get("adversarial_findings", 0) > 0)
    runs_with_blocks = sum(1 for m in all_metrics if m.get("catches", {}).get("validation_blocks", 0) > 0)

    # --- Confidence distribution ---
    confidence_scores = [m.get("quality", {}).get("confidence_score", 0) for m in all_metrics if m.get("quality", {}).get("confidence_score", 0) > 0]

    # --- Decision analytics ---
    total_decisions = {
        "mechanical": sum(m.get("decisions", {}).get("mechanical", 0) for m in all_metrics),
        "taste": sum(m.get("decisions", {}).get("taste", 0) for m in all_metrics),
        "judgment": sum(m.get("decisions", {}).get("judgment", 0) for m in all_metrics),
    }

    # --- Profile distribution ---
    profile_counts: dict[str, int] = {}
    for m in all_metrics:
        p = m.get("profile", "unknown")
        profile_counts[p] = profile_counts.get(p, 0) + 1

    # --- Duration analytics ---
    durations = [m["duration_minutes"] for m in all_metrics if m.get("duration_minutes")]

    def _safe_avg(lst: list) -> float:
        return round(sum(lst) / len(lst), 1) if lst else 0

    analytics = {
        "project": args.project,
        "runs_analyzed": n,
        "profiles": profile_counts,
        # Token analytics
        "tokens": {
            "avg_per_run": _safe_avg(total_tokens_list),
            "min_per_run": min(total_tokens_list) if total_tokens_list else 0,
            "max_per_run": max(total_tokens_list) if total_tokens_list else 0,
            "total_all_runs": sum(total_tokens_list),
            "per_stage_avg": {
                stage: _safe_avg(costs)
                for stage, costs in sorted(stage_token_agg.items())
            },
        },
        # Catch analytics — the core value
        "catches": {
            "review_findings_total": total_review_findings,
            "review_findings_avg": round(total_review_findings / n, 2),
            "adversarial_findings_total": total_adversarial,
            "adversarial_high_total": total_adversarial_high,
            "adversarial_hit_rate": f"{runs_with_adversarial}/{n} runs ({round(runs_with_adversarial/n*100)}%)",
            "validation_blocks_total": total_validation_blocks,
            "validation_block_rate": f"{runs_with_blocks}/{n} runs ({round(runs_with_blocks/n*100)}%)",
            "test_regressions_total": total_test_regressions,
        },
        # Quality analytics
        "quality": {
            "confidence_avg": _safe_avg(confidence_scores),
            "confidence_min": min(confidence_scores) if confidence_scores else 0,
            "confidence_max": max(confidence_scores) if confidence_scores else 0,
            "confidence_distribution": {
                "low_0_4": sum(1 for s in confidence_scores if s <= 4),
                "med_5_7": sum(1 for s in confidence_scores if 5 <= s <= 7),
                "high_8_12": sum(1 for s in confidence_scores if s >= 8),
            },
        },
        # Decision analytics
        "decisions": {
            **total_decisions,
            "total": sum(total_decisions.values()),
            "automation_rate": (
                f"{round(total_decisions['mechanical'] / sum(total_decisions.values()) * 100)}%"
                if sum(total_decisions.values()) > 0 else "N/A"
            ),
        },
        # Duration analytics
        "duration": {
            "avg_minutes": _safe_avg(durations),
            "min_minutes": min(durations) if durations else 0,
            "max_minutes": max(durations) if durations else 0,
        },
    }

    print(json.dumps(analytics, indent=2))


def _collect_judgment_decisions(run_state: dict) -> list[str]:
    """Collect judgment-class stage decisions as ``[decision]``-tagged strings.

    GAP A (run_fdbc0f08): pipeline ``decisions[]`` were STRUCTURALLY invisible to
    cultivation — cmd_run_cultivate only ever read ``reflect.lessons[]`` — so the
    single richest source of decision-shaped rationale ("chose X over Y because Z")
    was discarded, starving the DDD ``decision`` type. This collects the
    ``judgment``-class decisions across ALL stages and tags each ``[decision] <desc>``
    so the (Run 1) declared-type honor routes them to PROJECT.md § Recent Decisions
    + escalate (safe_auto=False).

    Filter rationale (Gate-0/Gate-1 adjudicated): ``judgment`` only. ``mechanical``
    (which-file/wiring) and ``taste`` (naming/aesthetic) decisions are run-local
    noise, not reusable rationale. String-shaped decisions carry no ``classification``
    and are skipped here — they are session/DailyActivity-sourced (handled by the
    distillation/context_health path), not pipeline rationale.
    """
    tagged: list[str] = []
    for stage in run_state.get("stages", []):
        for d in stage.get("decisions", []):
            if not isinstance(d, dict):
                continue  # bare string = session/DA-sourced, not a pipeline decision
            if d.get("classification") != "judgment":
                continue  # mechanical/taste = run-local noise
            desc = (d.get("description") or "").strip()
            if not desc:
                continue
            tagged.append(f"[decision] {desc}")
    return tagged


def cmd_run_cultivate(args, reg: ArtifactRegistry) -> None:
    """Apply pipeline lessons + judgment decisions to DDD docs via cultivation.

    Reads the 'lessons' field from the reflect stage in run.json, then calls
    cultivate_from_reflect() which auto-applies safe additive lessons and
    escalates risky ones. ALSO (GAP A, run_fdbc0f08) collects judgment-class
    decisions[] across all stages and cultivates them as typed [decision]
    entries via cultivate_from_decisions() — closing the decision-starvation gap.

    This is the CLI bridge that makes cultivation callable from agent
    Bash tools — the agent reads reflect.md instructions and runs this.
    """
    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    # Extract lessons from reflect stage record
    lessons: list[str] = []
    for stage in run_state.get("stages", []):
        if stage.get("stage") == "reflect":
            lessons = stage.get("lessons", [])
            break

    # GAP A: judgment-class decisions across ALL stages (typed [decision]).
    decisions = _collect_judgment_decisions(run_state)

    if not lessons and not decisions:
        print(json.dumps({
            "applied": 0,
            "escalated": 0,
            "rejected": 0,
            "note": "No lessons or judgment decisions found — nothing to cultivate",
        }))
        return

    # Resolve project directory
    workspace = _get_workspace()
    project_dir = workspace / "Projects" / args.project

    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project directory not found: {project_dir}"}))
        return

    # Run cultivation — lessons via cultivate_from_reflect, judgment decisions
    # via cultivate_from_decisions (both delegate to the same classify_content;
    # the [decision] tag routes decisions to PROJECT § Recent Decisions).
    from core.ddd_cultivation import cultivate_from_reflect, cultivate_from_decisions

    result = {"applied": 0, "escalated": 0, "rejected": 0, "retired": 0, "drift_errors": []}
    if lessons:
        lres = cultivate_from_reflect(lessons, args.run_id, args.project, project_dir)
        for k in ("applied", "escalated", "rejected", "retired"):
            result[k] += lres.get(k, 0)
        result["drift_errors"].extend(lres.get("drift_errors", []))
    if decisions:
        dres = cultivate_from_decisions(decisions, args.run_id, args.project, project_dir)
        for k in ("applied", "escalated", "rejected", "retired"):
            result[k] += dres.get(k, 0)
        result["drift_errors"].extend(dres.get("drift_errors", []))
        result["judgment_decisions_cultivated"] = len(decisions)

    print(json.dumps(result, indent=2))
    # Surface section-name drift LOUDLY — a dropped lesson is a config bug, not
    # a benign rejection (run_45ab67c7 root cause). stderr so it can't be missed.
    for _drift in result.get("drift_errors", []):
        print(f"⚠️  DDD DRIFT: {_drift}", file=sys.stderr)


def cmd_run_observe(args, reg: ArtifactRegistry) -> None:
    """Record pipeline telemetry events (Meta-Intelligence Layer 1: OBSERVE).

    Appends structured observation data to METRICS.json for cross-run analysis.
    Called at stage boundaries, on profile selection, and on abandon.

    Events:
      stage_start   — stage begins execution (records timestamp)
      stage_end     — stage completes (records timing, retries)
      profile_selected — EVALUATE chose a profile (records indicators)
      abandon       — pipeline abandoned (records reason, partial progress)
      think_depth   — THINK stage depth measurement
      requirement_shape — requirement characteristic analysis
    """
    run_file = _resolve_run_file(args.project, args.run_id)
    run_dir = run_file.parent
    metrics_file = run_dir / "METRICS.json"

    # Load or initialize METRICS.json
    metrics: dict = {}
    if metrics_file.exists():
        try:
            metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    event = args.event
    now = args.timestamp or datetime.now(timezone.utc).isoformat()

    if event == "stage_start":
        stage_timing = metrics.setdefault("stage_timing", {})
        stage_timing[args.stage] = {"start": now}

    elif event == "stage_end":
        stage_timing = metrics.setdefault("stage_timing", {})
        entry = stage_timing.get(args.stage, {})
        entry["end"] = now
        # Calculate wall minutes if start exists
        if entry.get("start"):
            try:
                t0 = datetime.fromisoformat(entry["start"])
                t1 = datetime.fromisoformat(now)
                entry["wall_minutes"] = round((t1 - t0).total_seconds() / 60, 2)
            except (ValueError, TypeError):
                pass
        if args.retries:
            entry["retries"] = int(args.retries)
        stage_timing[args.stage] = entry

    elif event == "profile_selected":
        metrics["profile_decision"] = {
            "detected_scope": args.scope or "unknown",
            "indicators_matched": json.loads(args.indicators) if args.indicators else [],
            "user_override": args.user_override,
            "files_estimated": int(args.files_estimated) if args.files_estimated else None,
        }

    elif event == "abandon":
        metrics["abandon_context"] = {
            "reason": args.reason or "unknown",
            "last_stage": args.stage or "unknown",
            "tokens_consumed": int(args.tokens_consumed) if args.tokens_consumed else 0,
            "partial_output": args.partial or "",
            "recoverable": not (args.reason in ("superseded", "user_stopped")),
        }

    elif event == "think_depth":
        metrics["think_depth"] = {
            "alternatives_count": int(args.alternatives) if args.alternatives else 0,
            "risk_probes_count": int(args.probes) if args.probes else 0,
            "probes_self_resolved": int(args.resolved) if args.resolved else 0,
            "probes_escalated": int(args.escalated) if args.escalated else 0,
        }

    elif event == "requirement_shape":
        metrics["requirement_shape"] = {
            "touches_frontend": args.frontend == "true",
            "touches_backend": args.backend == "true",
            "estimated_files": int(args.files_estimated) if args.files_estimated else 0,
            "crosses_modules": json.loads(args.modules) if args.modules else [],
        }

    elif event == "adversarial_patterns":
        metrics["adversarial_patterns"] = {
            "findings_by_category": json.loads(args.categories) if args.categories else {},
            "rp_violations": json.loads(args.rp_violations) if args.rp_violations else [],
            "findings_fixed": int(args.fixed) if args.fixed else 0,
            "findings_dismissed": int(args.dismissed) if args.dismissed else 0,
            # Arena feed (Gate-2 → evolution, run_4db42c78): the ACTION context
            # (what the agent chose) kept separate from the LABEL (what the
            # verifier caught). Additive + optional — existing emitters omit them.
            "action": json.loads(args.action) if args.action else {},
            "first_pass_high": int(args.first_pass_high) if args.first_pass_high else 0,
            "convergence_iterations": (
                int(args.convergence_iterations) if args.convergence_iterations else 0
            ),
        }

    elif event == "review_gap":
        metrics["review_to_adversarial_ratio"] = {
            "review_findings": int(args.review_count) if args.review_count else 0,
            "adversarial_findings": int(args.adversarial_count) if args.adversarial_count else 0,
            "overlap": int(args.overlap) if args.overlap else 0,
        }

    else:
        print(json.dumps({"error": f"Unknown event type: {event}"}))
        return

    # Persist
    metrics_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "event": event, "run_id": args.run_id}))


def cmd_bind(args, reg) -> None:
    """CREATE→BIND→PULL: clone + index every binding of a project's bindings.yaml.

    The invokable entrypoint for the DDD binding layer — replaces the old python -c
    prose recipe. Calls core.ddd_bindings.bind_project (which loops ALL bindings with
    per-binding isolation) and prints one JSON outcome per binding. A pure-DDD project
    (no bindings.yaml) prints an empty list. Exit 1 only if EVERY binding failed
    (a mixed bound/deferred/failed set still exits 0 — deferred + isolated-failure are
    expected, not a run-level error).
    """
    from core.ddd_bindings import bind_project

    try:
        outcomes = bind_project(args.project)
    except (FileNotFoundError, ValueError) as e:
        # A malformed bindings.yaml (ValueError names the bad field) is a run-level error.
        print(json.dumps({"error": f"bind failed for '{args.project}': {e}"}), file=sys.stderr)
        sys.exit(1)

    payload = [
        {"repo": o.repo, "kind": o.kind, "status": o.status, "error": o.error,
         "worktree": o.worktree, "code_intel_db": o.code_intel_db, "node_count": o.node_count}
        for o in outcomes
    ]
    print(json.dumps({"project": args.project, "bindings": payload,
                      "counts": {
                          "bound": sum(1 for o in outcomes if o.status == "bound"),
                          "deferred": sum(1 for o in outcomes if o.status == "deferred"),
                          "failed": sum(1 for o in outcomes if o.status == "failed"),
                      }}, indent=2, default=str))
    # Run-level failure ONLY when there were bindings and every one failed.
    if outcomes and all(o.status == "failed" for o in outcomes):
        sys.exit(1)


def cmd_ddd_health(args, reg) -> None:
    """5-dimensional DDD health scoring per section."""
    project_dir = _get_workspace() / "Projects" / args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project '{args.project}' not found"}))
        return

    from core.ddd_health import compute_section_health

    # persist=False: this CLI just PRINTS the scores (a read/inspect command) —
    # it must not write section_health.json as a side effect; the scheduled
    # ddd_refresh job owns the snapshot. #5 fix, run_e90535ea.
    result = compute_section_health(project_dir, persist=False)
    print(json.dumps(result, indent=2, default=str))


def cmd_ddd_retire(args, reg) -> None:
    """Agent-directed RETIRE of ONE named (title, section) knowledge entry — the
    sanctioned 'out' side of the knowledge layer (the counterpart to s_persist's
    add). Archives the entry (stays FTS5-recallable) + physically strips it from
    the doc by (title, section) identity + writes a dated .bak. Default is a
    dry-run preview; pass --apply to persist.

    This exists so removals do NOT go through a raw Edit-to-delete (which skips
    the archive → lost from recall, and can destroy a same-title sibling).
    Fail-loud: no match / ambiguous duplicate → error, exit 1.
    """
    from core.ddd_entry_lifecycle import retire_entry, RetireError, MEMORY_EVERGREEN_SECTIONS

    ws = _get_workspace()
    p = Path(args.file)
    if not p.is_absolute():
        p = ws / args.file
    if not p.exists():
        print(json.dumps({"error": f"file not found: {p}"}), file=sys.stderr)
        sys.exit(1)

    content = p.read_text(encoding="utf-8")
    archive_name = args.archive_name or (
        "MEMORY-archive.md" if p.name == "MEMORY.md" else "IMPROVEMENT-archive.md"
    )
    try:
        report = retire_entry(
            content, title=args.title, section=args.section,
            project_dir=p.parent, archive_name=archive_name,
            source_path=p if args.apply else None,
            dry_run=not args.apply, force=bool(args.force),
            # MEMORY.md's evergreen sections (Open Threads, Standing Preferences,
            # etc.) need --force parity with the autonomous reclaim path.
            evergreen_sections=MEMORY_EVERGREEN_SECTIONS if p.name == "MEMORY.md" else None,
        )
    except RetireError as e:
        print(json.dumps({"error": str(e), "retired": False}), file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        "retired": bool(args.apply),
        "dry_run": not args.apply,
        "candidate": report.candidates[0] if report.candidates else None,
        "archived": report.archived,
        "archive_file": archive_name,
        "hint": ("preview only — pass --apply to persist" if not args.apply
                 else f"retired to {archive_name} (dated .bak written)"),
    }))


def cmd_ddd_noise(args, reg) -> None:
    """Per-doc DDD noise gate (M0 ②): reclaimable noise_rate + PASS/FAIL verdict.

    Measures the GATE metric (raw noise MINUS keep-class) so a doc heavy in
    permanent-but-dormant knowledge (COE/principles) doesn't FAIL spuriously.
    Verdict string (PASS/FAIL) is in stdout for the golden_set canary to assert.
    Always exits 0 — the canary keys off the string, not the exit code.
    """
    from datetime import date
    from core.ddd_entry_lifecycle import (
        parse_entries, compute_reclaimable_noise, NOISE_FAIL_THRESHOLD,
        MEMORY_EVERGREEN_SECTIONS,
    )

    ws = _get_workspace()
    today = date.today()

    # Build the doc list: --doc (one file) or default scan (MEMORY + all IMPROVEMENT).
    docs: list[tuple[str, "Path", "frozenset[str] | None"]] = []
    if getattr(args, "doc", None):
        p = Path(args.doc)
        if not p.is_absolute():
            p = ws / args.doc
        ever = MEMORY_EVERGREEN_SECTIONS if p.name == "MEMORY.md" else None
        docs.append((args.doc, p, ever))
    else:
        mem = ws / ".context" / "MEMORY.md"
        if mem.exists():
            docs.append(("MEMORY.md", mem, MEMORY_EVERGREEN_SECTIONS))
        proj_dir = ws / "Projects"
        if proj_dir.is_dir():
            for imp in sorted(proj_dir.glob("*/IMPROVEMENT.md")):
                docs.append((str(imp.relative_to(ws)), imp, None))

    results = []
    worst_fail = False
    for name, path, ever in docs:
        if not path.exists():
            results.append({"doc": name, "error": "not found"})
            continue
        report = compute_reclaimable_noise(
            parse_entries(path.read_text(encoding="utf-8")), today,
            evergreen_sections=ever,
        )
        verdict = "FAIL" if report.noise_rate > NOISE_FAIL_THRESHOLD else "PASS"
        if verdict == "FAIL":
            worst_fail = True
        results.append({
            "doc": name, "total": report.total, "noisy": report.noisy,
            "noise_rate": round(report.noise_rate, 4), "verdict": verdict,
        })

    print(json.dumps({
        "threshold": NOISE_FAIL_THRESHOLD,
        "overall": "FAIL" if worst_fail else "PASS",
        "docs": results,
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Artifact registry CLI for SwarmAI pipeline"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # discover
    p_discover = sub.add_parser("discover", help="Discover artifacts by type")
    p_discover.add_argument("--project", required=True)
    p_discover.add_argument("--types", required=True, help="Comma-separated types")
    p_discover.add_argument("--full", action="store_true", help="Include full artifact data")

    # publish
    p_publish = sub.add_parser("publish", help="Publish a new artifact")
    p_publish.add_argument("--project", required=True)
    p_publish.add_argument("--type", required=True)
    p_publish.add_argument("--producer", required=True)
    p_publish.add_argument("--summary", required=True)
    p_publish.add_argument("--data", required=True, help="JSON data string")
    p_publish.add_argument("--topic", default="")
    p_publish.add_argument("--run-id", default=None, help="Pipeline run ID (stores in runs/<id>/ subdir)")
    p_publish.add_argument("--stage", default=None, help="Pipeline stage for schema validation (validates BEFORE publishing)")
    p_publish.add_argument("--quiet", action="store_true",
                           help="Emit ONLY a single-line {\"artifact_id\": ...} on success "
                                "(parse-proof for orchestrators); on validation failure emit a "
                                "SHORT single-line {\"validation_failed\":true,\"errors\":[...]} "
                                "instead of the verbose indented schema dump.")

    # schema — fetch a stage's expected artifact template without a failed publish
    p_schema = sub.add_parser("schema", help="Print a stage's expected artifact schema + template (single-line JSON)")
    p_schema.add_argument("--stage", required=True,
                          help="Pipeline stage (evaluate/think/plan/build/review/test/deliver)")

    # state
    p_state = sub.add_parser("state", help="Get pipeline state")
    p_state.add_argument("--project", required=True)

    # advance
    p_advance = sub.add_parser("advance", help="Advance pipeline state")
    p_advance.add_argument("--project", required=True)
    p_advance.add_argument("--state", required=True)
    p_advance.add_argument(
        "--run-id",
        help="Run to auto-validate before advancing. REQUIRED to enable the "
             "pre-advance validation gate; omitting it skips validation (we never "
             "guess which run to validate — run_f3975b8b).",
    )

    # learn
    p_learn = sub.add_parser("learn", help="Record pipeline outcome for learning")
    p_learn.add_argument("--project", required=True)
    p_learn.add_argument("--evaluation-id", required=True, help="ID of evaluation artifact")
    p_learn.add_argument("--outcome", required=True, choices=["success", "partial", "failure", "cancelled"])
    p_learn.add_argument("--actual-effort", default=None, help="Actual effort (T-shirt or sessions)")
    p_learn.add_argument("--lessons", default=None, help="Semicolon-separated lessons")

    # projects
    sub.add_parser("projects", help="List all projects")

    # run-create
    p_run_create = sub.add_parser("run-create", help="Create a new pipeline run")
    p_run_create.add_argument("--project", required=True)
    p_run_create.add_argument("--requirement", required=True, help="Requirement text")
    p_run_create.add_argument("--profile", default=None, help="Pipeline profile: full/trivial/research/docs/bugfix")

    # run-update
    p_run_update = sub.add_parser("run-update", help="Update a pipeline run")
    p_run_update.add_argument("--project", required=True)
    p_run_update.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_run_update.add_argument("--status", default=None, help="New status: running/paused/completed/failed/cancelled")
    p_run_update.add_argument("--stage-json", default=None, help="Stage record JSON to add/update")
    p_run_update.add_argument("--taste-decision", default=None, help="Taste decision JSON to append")
    p_run_update.add_argument("--profile", default=None, help="Pipeline profile override")
    p_run_update.add_argument("--ddd-checksums", default=None, help="DDD doc checksums JSON (from ddd-check)")
    p_run_update.add_argument("--files-touched", default=None, help="JSON array of source file paths THIS run wrote (dedup-appended to run.json files_touched; consumed by run-commit)")
    p_run_update.add_argument("--force-checkpoint", action="store_true", help="Override the confabulation guard when setting --status paused")

    # run-get
    p_run_get = sub.add_parser("run-get", help="Get pipeline run state")
    p_run_get.add_argument("--project", required=True)
    p_run_get.add_argument("--run-id", default=None, help="Specific run ID (omit for list)")

    p_run_commit = sub.add_parser("run-commit", help="Auto local-commit this run's files after PUSH-READY (never pushes)")
    p_run_commit.add_argument("--project", required=True)
    p_run_commit.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_run_commit.add_argument("--force", action="store_true", help="Commit even if push_ready gate hasn't passed")

    # run-surface-changes (read-only git sweep → content/knowledge/source/process buckets)
    p_run_surface = sub.add_parser("run-surface-changes", help="Git-based COMPLETE sweep: classify this run's changed files (SwarmWS + bound repos) into surface buckets")
    p_run_surface.add_argument("--project", required=False, help="(optional) informational only — the sweep spans all trees")
    p_run_surface.add_argument("--run-id", required=False, help="(optional) informational only")

    # run-checkpoint
    p_run_cp = sub.add_parser("run-checkpoint", help="Checkpoint: pause + artifact + Radar todo")
    p_run_cp.add_argument("--project", required=True)
    p_run_cp.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_run_cp.add_argument("--stage", required=True, help="Stage where pipeline paused")
    p_run_cp.add_argument("--reason", required=True, help="Why the pipeline paused")
    p_run_cp.add_argument(
        "--force-checkpoint", action="store_true",
        help="Override the confabulation guard. Required to checkpoint when "
             "should_checkpoint=false and the reason is not a true trigger. "
             "Use ONLY for a deliberate checkpoint you can justify with a measurement.",
    )

    # run-history
    p_run_hist = sub.add_parser("run-history", help="Historical token costs for calibration")
    p_run_hist.add_argument("--project", required=True)
    p_run_hist.add_argument("--limit", type=int, default=10, help="Max completed runs to analyze")

    # run-budget
    p_run_bgt = sub.add_parser("run-budget", help="Check budget status for active pipeline")
    p_run_bgt.add_argument("--project", required=True)
    p_run_bgt.add_argument("--run-id", required=True, help="Pipeline run ID")

    # run-status (v3: cross-project dashboard)
    p_run_status = sub.add_parser("run-status", help="Cross-project pipeline dashboard")
    p_run_status.add_argument("--active-only", action="store_true", help="Only show running/paused")

    # run-resume (v3: resume a paused pipeline)
    p_run_resume = sub.add_parser("run-resume", help="Resume a paused pipeline")
    p_run_resume.add_argument("--project", required=True)
    p_run_resume.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_run_resume.add_argument("--stage", default=None, help="Override resume stage")

    # run-report (auto-generate REPORT.md)
    p_run_report = sub.add_parser("run-report", help="Generate REPORT.md for a pipeline run")
    p_run_report.add_argument("--project", required=True)
    p_run_report.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_run_report.add_argument("--force", action="store_true", help="Overwrite existing REPORT.md")

    # run-metrics (generate METRICS.json for one run)
    p_run_metrics = sub.add_parser("run-metrics", help="Generate METRICS.json for a pipeline run")
    p_run_metrics.add_argument("--project", required=True)
    p_run_metrics.add_argument("--run-id", required=True, help="Pipeline run ID")

    # run-analytics (cross-run aggregation)
    p_run_analytics = sub.add_parser("run-analytics", help="Cross-run pipeline analytics")
    p_run_analytics.add_argument("--project", required=True)
    p_run_analytics.add_argument("--limit", type=int, default=50, help="Max runs to analyze")

    # run-cultivate (DDD cultivation from pipeline lessons)
    p_run_cultivate = sub.add_parser("run-cultivate", help="Apply pipeline lessons to DDD docs via cultivation engine")
    p_run_cultivate.add_argument("--project", required=True)
    p_run_cultivate.add_argument("--run-id", required=True, help="Pipeline run ID (reads lessons from reflect stage)")

    # cleanup-orphans
    p_cleanup = sub.add_parser("cleanup-orphans", help="Mark stale 'running' pipeline runs as abandoned")
    p_cleanup.add_argument("--threshold", type=float, default=2.0, help="Hours threshold (default 2.0)")

    # purge-garbage (run_0e68e235: retention purge of garbage run dirs, recoverable)
    p_purge = sub.add_parser("purge-garbage", help="Recoverably trash garbage run dirs (abandoned/crash-paused, never delivered) older than N days")
    p_purge.add_argument("--retention-days", type=float, default=30.0, help="Purge garbage older than this many days (default 30)")
    p_purge.add_argument("--apply", action="store_true", help="Actually trash (default: dry-run, reports would_purge only)")
    p_purge.add_argument("--include-tracked", action="store_true", help="Also purge git-TRACKED garbage (default: skip — avoids an unattended git-rm on the public repo)")

    # run-observe (Meta-Intelligence L1: telemetry events)
    p_observe = sub.add_parser("run-observe", help="Record pipeline telemetry event (Meta-Intelligence)")
    p_observe.add_argument("--project", required=True)
    p_observe.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_observe.add_argument("--event", required=True, help="Event type: stage_start|stage_end|profile_selected|abandon|think_depth|requirement_shape|adversarial_patterns|review_gap")
    p_observe.add_argument("--stage", default=None, help="Stage name (for stage_start/stage_end/abandon)")
    p_observe.add_argument("--timestamp", default=None, help="ISO timestamp (default: now)")
    p_observe.add_argument("--retries", default=None, help="Retry count (for stage_end)")
    p_observe.add_argument("--reason", default=None, help="Abandon reason category")
    p_observe.add_argument("--partial", default=None, help="Partial output summary (for abandon)")
    p_observe.add_argument("--tokens-consumed", default=None, help="Tokens consumed so far")
    p_observe.add_argument("--scope", default=None, help="Detected scope (for profile_selected)")
    p_observe.add_argument("--indicators", default=None, help="JSON array of matched indicators")
    p_observe.add_argument("--user-override", default=None, help="User profile override")
    p_observe.add_argument("--files-estimated", default=None, help="Estimated file count")
    p_observe.add_argument("--frontend", default=None, help="Touches frontend (true/false)")
    p_observe.add_argument("--backend", default=None, help="Touches backend (true/false)")
    p_observe.add_argument("--modules", default=None, help="JSON array of crossed modules")
    p_observe.add_argument("--alternatives", default=None, help="Think alternatives count")
    p_observe.add_argument("--probes", default=None, help="Think risk probes count")
    p_observe.add_argument("--resolved", default=None, help="Self-resolved probes count")
    p_observe.add_argument("--escalated", default=None, help="Escalated probes count")
    p_observe.add_argument("--categories", default=None, help="JSON of findings by category")
    p_observe.add_argument("--rp-violations", default=None, help="JSON array of RP patterns violated")
    p_observe.add_argument("--fixed", default=None, help="Adversarial findings fixed count")
    p_observe.add_argument("--dismissed", default=None, help="Adversarial findings dismissed count")
    p_observe.add_argument("--action", default=None, help="JSON of the pipeline ACTION context (profile/scope/approach) — arena feed")
    p_observe.add_argument("--first-pass-high", default=None, help="Count of HIGH/CRITICAL findings caught at first adversarial pass")
    p_observe.add_argument("--convergence-iterations", default=None, help="Quality-convergence iteration count (credit-assignment signal)")
    p_observe.add_argument("--review-count", default=None, help="Review findings count")
    p_observe.add_argument("--adversarial-count", default=None, help="Adversarial findings count")
    p_observe.add_argument("--overlap", default=None, help="Overlap count (review ∩ adversarial)")

    # bind (CREATE→BIND→PULL: clone + index every binding of a project's bindings.yaml)
    p_bind = sub.add_parser("bind", help="PULL: clone + code-intel every binding in a project's bindings.yaml")
    p_bind.add_argument("--project", required=True, help="Project whose bindings.yaml to PULL")

    # ddd-health
    p_ddd_health = sub.add_parser("ddd-health", help="5-dimensional DDD health scoring per section")
    p_ddd_health.add_argument("--project", required=True)

    # ddd-retire (run_186a5f15: agent-directed named-entry RETIRE — knowledge 'out' side)
    p_ddd_retire = sub.add_parser("ddd-retire", help="Retire ONE named (title,section) knowledge entry: archive + strip (dry-run unless --apply)")
    p_ddd_retire.add_argument("--file", required=True, help="Doc path (relative to workspace or absolute), e.g. .context/MEMORY.md or Projects/X/IMPROVEMENT.md")
    p_ddd_retire.add_argument("--title", required=True, help="Exact entry title (the bold text after the [type] tag)")
    p_ddd_retire.add_argument("--section", required=True, help="Exact '## Section' the entry lives under")
    p_ddd_retire.add_argument("--archive-name", dest="archive_name", default=None, help="Archive filename (default MEMORY-archive.md for MEMORY.md, else IMPROVEMENT-archive.md)")
    p_ddd_retire.add_argument("--apply", action="store_true", help="Persist the retire (default is dry-run preview)")
    p_ddd_retire.add_argument("--force", action="store_true", help="Retire even a keep-class entry (decision/model/principle/correction/COE)")

    # ddd-noise (M0 ②: per-doc reclaimable-noise gate)
    p_ddd_noise = sub.add_parser("ddd-noise", help="Per-doc DDD noise gate (reclaimable noise_rate + PASS/FAIL)")
    p_ddd_noise.add_argument("--project", default=None, help="(unused; scans MEMORY + all IMPROVEMENT by default)")
    p_ddd_noise.add_argument("--doc", default=None, help="Single doc path (relative to workspace or absolute)")

    # release-gate (run_9fec1fb1: code-enforced CI-green gate for s_swarm-release 7b)
    p_rel_gate = sub.add_parser("release-gate", help="CI-green gate: poll CI for HEAD, write marker on green (authorizes gh release create)")
    p_rel_gate.add_argument("--project", default="SwarmAI", help="Project owning the marker (default SwarmAI)")
    p_rel_gate.add_argument("--poll", action="store_true", help="One gh run list check for HEAD; green→write marker (exit 0), red→exit 1, not-done→exit 3")
    p_rel_gate.add_argument("--status", action="store_true", help="Print marker vs HEAD (does it authorize current HEAD?) without touching CI")
    p_rel_gate.add_argument("--clear", action="store_true", help="Delete the marker (call after publish, or to reset)")
    p_rel_gate.add_argument("--ref", default=None, help="Tag or sha of the commit BEING RELEASED (e.g. v1.26.0). Resolves <ref>^{commit}, polls CI for THAT commit, records it (+the tag) in the marker. Omit for a branch-tip release (legacy HEAD behavior).")

    args = parser.parse_args()
    reg = ArtifactRegistry(_get_workspace())

    handlers = {
        "discover": cmd_discover,
        "publish": cmd_publish,
        "schema": cmd_schema,
        "learn": cmd_learn,
        "state": cmd_state,
        "advance": cmd_advance,
        "projects": cmd_projects,
        "run-create": cmd_run_create,
        "run-update": cmd_run_update,
        "run-get": cmd_run_get,
        "run-commit": cmd_run_commit,
        "run-surface-changes": cmd_run_surface_changes,
        "run-checkpoint": cmd_run_checkpoint,
        "run-history": cmd_run_history,
        "run-budget": cmd_run_budget,
        "run-status": cmd_run_status,
        "run-resume": cmd_run_resume,
        "run-report": cmd_run_report,
        "run-metrics": cmd_run_metrics,
        "run-analytics": cmd_run_analytics,
        "run-cultivate": cmd_run_cultivate,
        "cleanup-orphans": cmd_cleanup_orphans,
        "purge-garbage": cmd_purge_garbage,
        "run-observe": cmd_run_observe,
        "bind": cmd_bind,
        "ddd-health": cmd_ddd_health,
        "ddd-retire": cmd_ddd_retire,
        "ddd-noise": cmd_ddd_noise,
        "release-gate": cmd_release_gate,
    }
    handlers[args.command](args, reg)


if __name__ == "__main__":
    main()
