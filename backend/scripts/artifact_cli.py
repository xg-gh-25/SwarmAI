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

Usage — Stage boundaries (one call in, one call out per stage):
    python artifact_cli.py stage-start --project SwarmAI --run-id run_xxx --stage build
    python artifact_cli.py stage-complete --project SwarmAI --run-id run_xxx --stage build \\
        --data @/tmp/run_xxx/build.json [--check]

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
- ``cmd_stage_start`` / ``cmd_stage_complete`` — the stage-boundary commands.
  stage-complete writes run.json ONCE (atomic) after a stub-free publish; a write
  failure prints one ``{"ok":false,"code":"write_failed",...}`` line.
- ``_deliver_push_ready_bound`` — the DELIVER push_ready upper bound (all runner
  evidence, freshness vs files_touched, exit contract); shared by stage-complete
  and the legacy ``run-update --stage-json`` path, which may only LOWER push_ready.
- ``_profile_change_error`` — profile changes after EVALUATE may not drop a stage
  of the current profile that has not completed yet.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path so we can import core modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.artifact_registry import ArtifactRegistry
from core.pipeline_profiles import get_profile_stages, normalize_profile

# Sibling module in backend/scripts/ — pure completion commit+surface gate logic.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from completion_gate import completion_surface_verdict
from core.surface_marker import MARKER_NAME as SURFACE_MARKER_NAME, marker_covers


# §9 "no lessons" placeholder — the string cmd_run_report emits when the reflect
# record carries no lessons. Renderer-only: §9 lives in ledger A and is not part of
# the committed review deliverable B (_B_REVIEW_SECTION_WHITELIST), because REFLECT
# writes the lessons after DELIVER has landed the change.
_NO_LESSONS_PLACEHOLDER = "(REFLECT stage did not record lessons for this run)"


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
    # gate1_verdict BEFORE publish ran), make the existing record reflect the
    # LATEST published artifact. Publish is the SOLE writer of a stage's
    # artifact_id (grep: every other `stage.artifact_id` write —
    # _CARRY_FORWARD_FIELDS at cmd_run_update, and the test/deliver metrics
    # backfills — is if-absent only and never originates a competing id), so the
    # just-published artifact IS the current one and the stage must follow it.
    # Two facets, both handled by the `!= _incoming_aid` condition below:
    #   • BACK-FILL (run_b7620c6e): existing record has NO artifact_id (gate-1
    #     wrote it first) → link it. (`None != art_x` is True.) Skipping entirely
    #     (the original behavior) left the stage permanently unlinked.
    #   • RE-POINT (run_59e9e36a / run_05b42b8b): existing record has the FIRST
    #     publish's id and the SAME stage is published again → update to the new
    #     id. The interim back-fill-ONLY guard (`not _existing.get(...)`) fixed
    #     back-fill but pinned re-publish to the stale id, so the validator read
    #     acceptance-criteria from the OLD artifact → false "AC not covered"
    #     (~4 manual re-record cycles, IMPROVEMENT.md:1685). Re-pointing is safe
    #     precisely because publish is the sole writer — there is no "explicit
    #     value from elsewhere" to clobber. A same-id re-record is a no-op.
    _existing = next((s for s in stages if s.get("stage") == stage_record["stage"]), None)
    if _existing is not None:
        _incoming_aid = stage_record.get("artifact_id")
        if _incoming_aid and _existing.get("artifact_id") != _incoming_aid:
            _existing["artifact_id"] = _incoming_aid
            data["stages"] = stages
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write_run_atomic(run_file, data)
        return
    stages.append(stage_record)
    data["stages"] = stages
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Match the formatting of every other run.json writer (cmd_run_update,
    # cmd_run_create, etc.): indent=2, default ensure_ascii, utf-8. Avoids the
    # same file flipping between raw-UTF8 and \uXXXX escapes depending on which
    # command wrote last (noisy diffs in .artifacts/runs/*/run.json).
    _write_run_atomic(run_file, data)


def _read_json_arg(raw: str, argname: str) -> str:
    """Resolve a CLI JSON argument's RAW-STRING source — @path / @- / inline.

    Shared by ``publish --data`` and ``run-update --stage-json`` so both read a
    file identically (run_b7620c6e introduced @file for --data only; this unifies
    it — R25 de-dup / P8 one entry rule). ``@PATH`` reads the file, ``@-`` reads
    stdin, anything else is returned verbatim (a JSON value never starts with '@',
    so the prefix is an unambiguous discriminator).

    DELIBERATELY does NOT ``json.loads`` — it returns the raw string and the caller
    parses it. This preserves the asymmetric exception semantics at the two
    run-update call sites: the deliver-ack precheck swallows a parse error
    (best-effort), the main parse lets it raise. Merging json.loads here would
    erase that difference (contract drift). ``argname`` only parameterizes the
    read-failure message so a --stage-json failure does not misreport "--data".
    """
    if isinstance(raw, str) and raw.startswith("@"):
        try:
            if raw == "@-":
                return sys.stdin.read()
            return Path(raw[1:]).expanduser().read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            print(json.dumps({"error": f"Could not read {argname} source {raw!r}: {e}"}),
                  file=sys.stderr)
            sys.exit(1)
    return raw


class _PublishRefused(Exception):
    """A publish refused BEFORE any write (unsafe identifier / phantom run).

    ``payload`` is the exact JSON error object the CLI prints; ``exit_code`` is the
    documented exit status (3 = rejected before write)."""

    def __init__(self, payload: dict, exit_code: int = 3):
        super().__init__(payload.get("error", "publish refused"))
        self.payload = payload
        self.exit_code = exit_code


def _publish_core(
    reg: ArtifactRegistry, *, project: str, artifact_type: str, data: dict,
    producer: str, summary: str, topic: str = "", run_id: "str | None" = None,
    stage: "str | None" = None, record_stub: bool = True,
) -> dict:
    """THE artifact publish code path — shared by ``publish`` and ``stage-complete``.

    1. TRUST BOUNDARY (run_44a06138, Kiro #2): validate project + run_id BEFORE
       reg.publish() writes anything, so a bad identifier writes ZERO bytes.
    2. PHANTOM-RUN check (Kiro #3): a --stage publish to a named run requires that
       run to exist and own this project/id — else a phantom runs/<id>/ is minted.
    3. reg.publish() (ValueError / FileNotFoundError propagate to the caller).
    4. Auto-record SAFETY NET (stage + run_id): append a ``status="recorded"`` stub
       (NOT "completed", no stage_doc_consumed) so a forgotten finalize never loses
       the stage→artifact link, while the completion gate still demands a real
       completed record. Best-effort: a failure is RETURNED as ``warning``.
       ``record_stub=False`` (stage-complete) skips it: that caller writes the
       full record itself in one atomic run.json write.

    Raises _PublishRefused for steps 1-2. Returns
    ``{"artifact_id", "auto_recorded": True|False|None, "warning": dict|None}``.
    """
    try:
        reg._validate_path_identifiers(project, run_id)
    except ValueError as _ve:
        raise _PublishRefused({"error": f"unsafe path identifier (rejected before write): {_ve}"})

    if stage and run_id:
        _run_json = (
            Path(reg.workspace_root) / "Projects" / project
            / ".artifacts" / "runs" / run_id / "run.json"
        )
        _phantom = None
        if not _run_json.is_file():
            _phantom = f"run '{run_id}' does not exist (no runs/{run_id}/run.json)"
        else:
            try:
                _rj = json.loads(_run_json.read_text(encoding="utf-8"))
                _rj_proj = _rj.get("project")
                _rj_id = _rj.get("id") or _rj.get("pipeline_id") or _rj.get("run_id")
                if _rj_proj not in (None, project):
                    _phantom = f"run '{run_id}' belongs to project {_rj_proj!r}, not {project!r}"
                elif _rj_id not in (None, run_id):
                    _phantom = f"run.json id {_rj_id!r} does not match run_id {run_id!r}"
            except (json.JSONDecodeError, OSError) as _e:
                _phantom = f"run '{run_id}' run.json unreadable: {_e}"
        if _phantom:
            raise _PublishRefused({"error": f"stage publish to a non-existent/foreign run "
                                            f"(rejected before write): {_phantom}"})

    artifact_id = reg.publish(
        project=project, artifact_type=artifact_type, data=data,
        producer=producer, summary=summary, topic=topic or "", run_id=run_id,
    )
    out: dict = {"artifact_id": artifact_id, "auto_recorded": None, "warning": None}
    if stage and run_id and record_stub:
        try:
            _append_stage_to_run(project, run_id, {
                "stage": stage, "status": "recorded",
                "artifact_id": artifact_id, "auto_recorded": True,
            }, reg)
            out["auto_recorded"] = True
        except Exception as e:
            out["auto_recorded"] = False
            out["warning"] = {
                "warning": "auto-record stage failed; run.json not updated. "
                           "Record manually via run-update --stage-json.",
                "stage": stage,
                "detail": str(e),
            }
    return out


def cmd_publish(args, reg: ArtifactRegistry) -> None:
    """Publish a bare artifact (no run, no stage): manifest-tracked, read by id.

    Pipeline v1 retired ``--stage`` / ``--run-id`` publishes; run documents are
    written through ``pipeline_v1.py``.
    """
    # --data accepts a raw JSON string OR "@PATH" (read a file) OR "@-" (stdin),
    # resolved by the shared _read_json_arg helper (same path as --stage-json).
    _raw = _read_json_arg(args.data, "--data")
    try:
        data = json.loads(_raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON data: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Pre-publish validation: check schema + depth BEFORE writing to disk
    # Uses pipeline_validator as single source of truth (no duplicate schemas).
    # `--stage` / `--run-id` publishes are retired (Pipeline v1; main() refuses them):
    # only a bare publish reaches here. A bare publish is how standalone skills
    # (s_evaluate on ESCALATE, s_deliver / s_qa standalone) emit an artifact outside a
    # run; it is manifest-tracked and read only by artifact_id.
    run_id, stage = None, None

    # ── TRUST BOUNDARY + PHANTOM-RUN + write + auto-record ──
    # Delegated to _publish_core — the ONE publish code path, shared with
    # `stage-complete` (A1) so the two cannot drift. Validation order (identifiers
    # → phantom run → write) and the exit codes are unchanged.
    try:
        try:
            _core = _publish_core(
                reg, project=args.project, artifact_type=args.type, data=data,
                producer=args.producer, summary=args.summary,
                topic=args.topic or "", run_id=run_id, stage=stage,
            )
        except _PublishRefused as _pr:
            if getattr(args, "quiet", False):
                print(json.dumps(_pr.payload), file=sys.stderr)
            else:
                print(json.dumps(_pr.payload, indent=2), file=sys.stderr)
            sys.exit(_pr.exit_code)
        artifact_id = _core["artifact_id"]
        result = {"artifact_id": artifact_id, "project": args.project}
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


# ── Stage-advance continuation contract (the CONTINUE-side carrier) ─────────
# The pipeline's STOP side is code-enforced (this function's own fail-closed
# sys.exit(1) below; `run-update --status completed`; `cmd_run_checkpoint`'s
# should_checkpoint hard-block). Its CONTINUE side used to be prose ONLY
# (INSTRUCTIONS.md §3f / the Core Loop / Rule 20) with no turn-boundary
# directive and no carrier — so `advance` read as a terminal action and the
# orchestrating agent ended its turn at the stage boundary (stop_reason=
# end_turn, zero tool_use). This constant is that missing carrier, and it is
# SINGLE-SOURCED on purpose: the same phrase is asserted in the 7 stage docs,
# the 3 sibling skills, and INSTRUCTIONS.md §3f, so the contract cannot drift
# across those sources (R27). Changing this string requires updating them all —
# `backend/tests/test_advance_continuation.py` enforces that.
_CONTINUATION_PHRASE = "advance is not the end of your turn"

# Where the stage docs physically live. `Path(__file__).parent.parent` is
# `backend/` (this file is `backend/scripts/artifact_cli.py`), matching the
# existing convention used for pipeline_validator/pollinate_validator lookups.
# This deliberately resolves the `backend/skills/` SOURCE OF TRUTH, never the
# `.claude/skills/` projection, which can be stale within a session
# (INSTRUCTIONS.md § "Read from backend/skills").
_STAGE_DOC_DIR = Path(__file__).resolve().parent.parent / "skills" / "s_autonomous-pipeline" / "stages"


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


def _ensure_project_dir(project: str) -> Path:
    """Return ``Projects/<project>/``, guaranteeing it exists AND is REGISTERED.

    THE single choke point for minting a project directory from the pipeline CLI.
    Every command that would ``mkdir(parents=True)`` a ``Projects/<name>/`` subtree
    (run-create, release-gate, run-report, run-analytics) must obtain the project dir
    THROUGH here first — so a ``Projects/<name>/`` can NEVER be created without its
    ``.project.json`` (which is what makes it visible to Brain Hub). This closes the
    "pseudo-DDD" class at the birth point rather than in one command (see run_be30a0c6).

    Idempotent + non-destructive: ``ensure_project_metadata`` is only-if-absent, so an
    already-registered project keeps its metadata/history untouched. Returns the dir.
    """
    from core.swarm_workspace_manager import ensure_project_metadata
    # Containment guard (adversarial-review HIGH): `project` is a CLI arg that upstream
    # tooling may fill from requirement/LLM text — an unvalidated segment like
    # "../../.ssh" would let mkdir(parents=True) escape the workspace.
    # SSOT (run_44a06138, Gate-1 amendment A): delegate the project-name
    # containment to ArtifactRegistry._validate_path_identifiers — the ONE place
    # that owns "is this caller-supplied identifier safe to build a path from?"
    # Do NOT re-derive the resolve()/relative_to idiom here (a 2nd copy drifts).
    from core.artifact_registry import ArtifactRegistry
    projects_root = _get_workspace() / "Projects"
    ArtifactRegistry(_get_workspace())._validate_path_identifiers(project)
    pdir = projects_root / project
    pdir.mkdir(parents=True, exist_ok=True)
    ensure_project_metadata(pdir, project)
    return pdir


# Crash auto-checkpoint stamps this exact reason; an INTENTIONAL pause carries a
# real decision reason instead (e.g. "Gate 1 BLOCK", "DELIVERED ...", "ROOT CAUSE
# FALSIFIED"). This string is the primary zombie discriminator.
_CRASH_ZOMBIE_REASON = "session_crash_auto_detected"

# C1: the terminal status set — a run in one of these has FINISHED. Single source
# of truth shared by is_terminal_run() (liveness) AND the run-update transition guard
# (revival protection), so the two can never drift on what "terminal" means.
_TERMINAL_STATUSES = ("completed", "complete", "abandoned", "failed", "cancelled")
# All legal status values run-update accepts (argparse choices) — rejects typos/junk.
_VALID_STATUSES = ("running", "paused", "completed", "complete", "failed",
                   "cancelled", "abandoned")


def is_terminal_run(data: dict) -> bool:
    """Single source of truth: has this run reached a TERMINAL (finished) state,
    judged by STAGE PROGRESS — not by the top-level `status` string?

    Why stage-based, not status-based: a since-deleted orphan-transition
    (running->paused + reason=session_crash_auto_detected, once run on every
    session refresh) left legacy run.json files whose STAGES are all done but
    whose status is 'paused'. Such a run is FINISHED, not a crash orphan — but every liveness check
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
    if status in _TERMINAL_STATUSES:
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
    if isinstance(stages, dict):
        return stages.get("reflect") in ("completed", "degraded") \
            or stages.get("deliver") in ("completed", "degraded")
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


def _completion_verdict(run_state: dict) -> "tuple[bool | None, list[str]]":
    """Whether delivery is verified; v1 reads the generated audit projection."""
    if isinstance(run_state.get("stages"), dict):
        ready = run_state.get("push_ready")
        return (ready, [] if ready is True else ["run_audit push_ready is false"]
                if ready is False else [])
    recs = [s for s in run_state.get("stages") or [] if isinstance(s, dict)
            and s.get("stage") == "deliver" and s.get("status") == "completed"]
    if not recs:
        return None, []
    rec = recs[-1]
    if rec.get("push_ready") is True:
        return True, []
    why = str(rec.get("push_ready_reason") or "push_ready is false")
    return False, [r.strip() for r in why.split("; ") if r.strip()][:10]


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
    if isinstance(raw.get("stages"), dict):
        return {"active": "running", "awaiting_human": "paused",
                "reflecting": "running"}.get(status, status)
    if status in ("completed", "complete"):
        return "completed"
    if status in ("abandoned", "paused") and _delivered_by_stages(raw):
        return "completed"
    return status


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
            # A run whose worktree is not released yet keeps its record: the sweep
            # needs it to snapshot + release the worktree (pipeline-run-worktree §8.1).
            if any(isinstance(e, dict) and e.get("state") in ("active", "integrated")
                   for e in data.get("worktrees") or []):
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


_RED_TEAM_EXPERT_ID = "red-team"


GATE1_PROFILES = ("full", "bugfix")  # stages/ref/build/gate1.md: BUILD always runs Gate-1


# _write_local_pr REMOVED (run_b8ea6d5c): LOCAL_PR.md aggregation deleted. The
# review deliverable is now per-file PR-review rows in the Canvas OUTPUTS rail,
# emitted at COMPLETE by the surface_run_outputs tool (ui_actions.build_surface_events
# → orchestrator observe-emit). run-commit records commits; it no longer writes a doc.


# ── release-gate: the code-enforced CI-green gate for s_swarm-release 7b/7c ──────
#
# Design A (run_9fec1fb1, 2026-07-04; hook→flow-step run_d613bb27): `--poll` is the
# ONLY writer of the CI-green marker; `--verify` is the fail-closed reader that
# authorizes the publish (the "check CI" half + the "enforce" half, both in this CLI).
# `--verify` REPLACES the old `release_publish_guard` PreToolUse hook — a per-command
# product-wide hook was the wrong layer for SwarmAI's OWN release discipline, so the
# enforce step moved into the release flow (s_swarm-release 7c calls `--verify` before
# `gh release edit --draft=false`). Both `--poll` (network `gh run list`) and `--verify`
# (local marker read + `git rev-parse`) run as explicit one-time CLI calls, so neither
# reintroduces the foreground-timeout hang trap that lived in the 7b runbook poll.
#
# Marker: Projects/<project>/.artifacts/.release-ci-green.json
#   {"head_sha": "<40-char>", "repo_root": "<abs>", "run_id": <int>, "tag": "<opt>",
#    "conclusion": "success", "ts": "<iso>"}
# --verify authorizes publish IFF the marker attests the commit being released (tag-anchored
# via the marker's `tag`, else HEAD-anchored). A stale marker authorizes nothing — fail-closed.

def _release_marker_path(project: str) -> Path:
    return (_get_workspace() / "Projects" / project / ".artifacts"
            / ".release-ci-green.json")


def _release_marker_authorizes_head(
    marker: Path, repo_root: str | None, published_tag: str | None = None,
) -> tuple[bool, str]:
    """(authorized, reason). True IFF the CI-green marker attests the commit BEING
    PUBLISHED. Moved from security_hooks.release_publish_guard (run_d613bb27) — the
    pre-publish check is now a one-time gate in the release flow, not a per-command
    hook. Fail-CLOSED on any failure.

    Two modes, chosen by whether the marker records a `tag`:
    - **Tag-anchored** (marker has `tag`): resolve the PUBLISHED tag to its commit
      LOCALLY and require it == marker.head_sha (CI green on the exact shipped commit).
    - **HEAD-anchored** (legacy, no `tag`): require marker.head_sha == current HEAD.
    """
    import subprocess
    if not marker.exists():
        return False, "no CI-green marker (run `artifact_cli.py release-gate --poll` until PASS)"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"marker unreadable: {type(e).__name__}"
    marker_head = data.get("head_sha") or ""
    # fail-CLOSED: the marker MUST record its own repo_root (--poll always writes it).
    # Do NOT fall back to the caller's cwd — a marker missing repo_root is a stale/
    # malformed marker and must BLOCK, exactly as the original release_publish_guard did.
    marker_repo = data.get("repo_root") or ""
    marker_tag = data.get("tag") or ""
    if not marker_head or not marker_repo:
        return False, "marker missing head_sha/repo_root (stale format — re-poll release-gate)"

    def _rev_parse(spec: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", marker_repo, "rev-parse", spec],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    if marker_tag:
        if not published_tag:
            return (False,
                    f"marker is tag-anchored (tag {marker_tag}) but the publish names no "
                    "tag to verify — fail-closed (pass --ref <tag>, or re-poll release-gate)")
        published_commit = _rev_parse(f"{published_tag}^{{commit}}")
        if not published_commit:
            return (False,
                    f"cannot resolve published tag '{published_tag}' in {marker_repo} "
                    "(local tag missing?) — fail-closed")
        if published_commit != marker_head:
            return (False,
                    f"published tag {published_tag} → {published_commit[:8]} != CI-verified "
                    f"commit {marker_head[:8]} (marker tag {marker_tag}) — CI not green on the "
                    "commit being published")
        return (True,
                f"CI green on {marker_head[:8]} — published tag {published_tag} matches "
                f"marker tag {marker_tag} (run {data.get('run_id')})")

    head = _rev_parse("HEAD")
    if not head:
        return False, f"git HEAD unresolved in marker repo_root {marker_repo}"
    if marker_head != head:
        return False, f"marker HEAD {marker_head[:8]} != current HEAD {head[:8]} (stale — CI not green on THIS HEAD)"
    return True, f"CI green on HEAD {head[:8]} (run {data.get('run_id')})"


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

    if getattr(args, "verify", False):
        # --verify: the PRE-PUBLISH gate. Formerly the `release_publish_guard`
        # PreToolUse hook; moved into the release flow (s_swarm-release Stage 7b)
        # as a one-time check instead of a per-command runtime hook (a self-dev
        # concern does not belong in the product-wide hook layer). Fail-CLOSED:
        # authorizes the publish IFF the CI-green marker attests the commit being
        # released. Prints the verdict; exit 0 = authorized, exit 1 = blocked.
        # All-local (marker read + `git -C <repo_root> rev-parse`), never network.
        ok, reason = _release_marker_authorizes_head(
            marker, repo_root, published_tag=ref,
        )
        print(json.dumps({"state": "PASS" if ok else "BLOCK", "reason": reason,
                          "head": head, "published_ref": ref}),
              file=sys.stdout if ok else sys.stderr)
        sys.exit(0 if ok else 1)

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
        # Register the project first — this .artifacts/ mkdir would otherwise mint an
        # unregistered Projects/<name>/ (pseudo-DDD) if release-gate ran before run-create.
        _ensure_project_dir(args.project)
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


_BRIEF_SUMMARY = 160
_BRIEF_FAILURES = 10


# ── Checkpoint guard vocabulary (single source of truth) ──────────────
# True triggers: a checkpoint with any of these in its reason is a REAL,
# measurement- or event-backed pause and is always allowed.
_CHECKPOINT_TRUE_TRIGGERS = (
    # NOTE: "budget" is DELIBERATELY ABSENT (removed 2026-10: budget is never a
    # legitimate pause reason — STEERING #2 + XG directive "budget 从来不应该是 block run
    # 的重要因素"). A budget-worded pause reason therefore no longer counts as a true
    # trigger: it is refused by both pause gates and no longer protected from auto-abandon.
    # Do NOT re-add it. "exhaust" stays (resource/retry exhaustion, not token budget).
    "l2", "block", "error", "crash", "escalat", "stuck",
    "judgment", "retry", "gate_spawn_blocked", "exhaust",
    "git revert", "external", "mutation", "re-baseline",  # event-triggers (GC11 kept half)
)
# Prefix-stems: triggers meant to match a word-START + any inflection
# ("escalat" → escalate/escalated/escalation). Without this they were matched as
# whole words (\bescalat\b), which never hit a real inflection → the stem was dead
# (Gate-2 informational finding, run_17e3399c). Listed explicitly so the default
# stays strict whole-word for everything else (no substring leaks).
_CHECKPOINT_TRUE_TRIGGER_STEMS = frozenset({"escalat"})


_get_profile_stages = get_profile_stages  # alias for backward compat within this file


# ── REPORT read helpers: the reader follows the writer ───────────────────────
# The report used to read fields no stage writes (``tdd.tests_total`` off the BUILD
# record, adversarial ``verdict``, completion ``criteria_met``, ``confidence_score``)
# and default each miss to 0 / "?", so a real run rendered "Tests: 0/0", "verdict: ?"
# and "Confidence 0/10" over a 2847-test, 16-finding, push-ready delivery. Every field
# read below is pinned to the writer schema (pipeline_validator.get_stage_schema) by
# tests/test_run_report_writer_fields.py — a reader that drifts goes red there. A
# value no stage wrote is OMITTED ("not recorded"), never rendered as a measurement.


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _report_test_metrics(test_data: dict) -> dict:
    """Measured test metrics from the test_report artifact; {} when none recorded.

    ``passed`` is a boolean "suite passed" in the current schema. Older runs wrote
    an integer pass COUNT there, in which case total = passed + failed.
    """
    if not isinstance(test_data, dict) or not test_data:
        return {}
    out: dict = {}
    total = _int_or_none(test_data.get("tests_total"))
    failed = _int_or_none(test_data.get("failed"))
    passed = test_data.get("passed")
    if total is None and _int_or_none(passed) is not None:
        total = passed + (failed or 0)
    if total is not None:
        out["tests_total"] = total
    if failed is not None:
        out["failed"] = failed
    for k in ("tests_new", "regressions", "pre_existing_failures", "flaky_failures",
              "tests_reused"):
        v = _int_or_none(test_data.get(k))
        if v is not None:
            out[k] = v
    if isinstance(passed, bool):
        out["suite_passed"] = passed
    return out


#: (stage, nested path) -> fields the report reads there. Two tests hold it honest:
#: every entry must exist in the writer schema (template + required + recommended),
#: and every field the read helpers below actually ``.get()`` must be listed here
#: (found by parsing their code) — so the list cannot drift from the code either.
_REPORT_READS = {
    ("test", ()): ("tests_total", "passed", "failed", "tests_new", "regressions"),
    ("review", ()): ("findings_count", "findings", "approved"),
    ("build", ("tdd",)): ("tests_generated",),
    ("deliver", ("adversarial_review",)): (
        "spawned", "profile_tier", "findings_total", "findings_fixed",
        "findings_remaining", "findings",
    ),
    ("deliver", ("completion_audit",)): (
        "all_green", "requirements_met", "requirements_total", "gaps", "unfixable_gaps",
    ),
}

#: Fields read ONLY so that reports of runs written under an older schema still
#: render. Not in the current writer schema on purpose — new runs never write them.
_REPORT_LEGACY_READS = {
    ("deliver", ("adversarial_review",)): ("verdict",),
    ("deliver", ("completion_audit",)): ("criteria_met", "criteria_unmet"),
}


def _extract_run_metrics(project: str, run_id: str, run_state: dict) -> dict:
    """Extract comprehensive metrics from a completed pipeline run.

    Reads run.json + artifact data to produce a flat metrics dict.
    """
    from datetime import datetime, timezone

    stages = run_state.get("stages", [])
    profile = run_state.get("profile", "full")


    if isinstance(stages, dict):
        audit = run_state.get("audit_summary") if isinstance(run_state.get("audit_summary"), dict) else {}
        speed = audit.get("speed") if isinstance(audit.get("speed"), dict) else {}
        token = audit.get("token_cost") if isinstance(audit.get("token_cost"), dict) else {}
        quality = audit.get("quality") if isinstance(audit.get("quality"), dict) else {}
        autonomy = audit.get("autonomy") if isinstance(audit.get("autonomy"), dict) else {}
        completed = sum(value in ("completed", "degraded") for value in stages.values())
        return {
            "run_id": run_id, "project": project, "profile": profile,
            "status": run_state.get("status", "?"), "stages_completed": completed,
            "stages_total": len(stages),
            "duration_minutes": (round(float(speed["wall_seconds"]) / 60, 1)
                                 if isinstance(speed.get("wall_seconds"), (int, float)) else None),
            "total_tokens": int(token.get("total_tokens") or 0), "stage_tokens": {},
            "decisions": {"mechanical": 0, "taste": 0,
                          "judgment": int(autonomy.get("human_decisions") or 0),
                          "total": int(autonomy.get("human_decisions") or 0)},
            "catches": {"review_findings": int(quality.get("findings_total") or 0),
                        "review_rp_checked": 0, "adversarial_findings": 0,
                        "adversarial_high": 0, "adversarial_resolved": 0,
                        "validation_blocks": 0, "test_regressions": 0},
            "quality": {"confidence_score": 0,
                        "completion_all_green": quality.get("verdict") == "PASS",
                        "test_passed": int(quality.get("verification_passed") or 0),
                        "test_failed": max(0, int(quality.get("verification_required") or 0)
                                           - int(quality.get("verification_passed") or 0))},
            "build": {"files_changed": 0, "tests_generated": 0},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
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

        stage_name = s.get("stage", "?")
        _res = _load_artifact_for_metrics(
            project, run_id, art_id, _STAGE_ARTIFACT_TYPE.get(stage_name)
        )
        data = _res.data
        if not data:
            continue

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
            # Same reader as REPORT.md: ``passed`` is a boolean in the current
            # test_report schema, so it is not a count of passing tests.
            _tm = _report_test_metrics(data)
            test_failed = _tm.get("failed", 0)
            test_passed = (_tm["tests_total"] - test_failed) if "tests_total" in _tm else 0

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


_CANON_STAGE_ORDER = (
    "evaluate", "think", "plan", "build", "review", "test",
    "goal_cycle", "deliver", "reflect",
)


class LoadResult:
    """3-state result of a read-contract artifact load (Action B, Kiro #7).

    Distinguishes the three outcomes the old `dict | None` return CONFLATED, so a
    REPORT can render "unavailable" instead of a misleading real-looking 0:
      - ``available``   — artifact found, owned by this run, right type, parsed.
      - ``unavailable`` — a stage DID record an artifact_id, but it does not
        resolve (missing / corrupt / wrong-run / wrong-type / path-escape).
      - ``not_run``     — the stage recorded NO artifact_id at all (art_id None/empty).

    ``__bool__`` is True ONLY for ``available`` — so every legacy ``if loaded:`` /
    ``if isinstance(art, dict)`` caller keeps working unchanged while gaining the
    3-state distinction when it reads ``.state``.
    """

    __slots__ = ("state", "data")

    def __init__(self, state: str, data: dict | None = None):
        self.state = state
        self.data = data

    def __bool__(self) -> bool:
        return self.state == "available"


def _load_artifact_for_metrics(
    project: str, run_id: str | None, artifact_id: str | None,
    expected_type: "str | set[str] | frozenset[str] | list[str] | tuple[str, ...] | None" = None,
) -> LoadResult:
    """Load artifact data by ID with the Action B read contract.

    Enforces, in order:
      - ``not_run`` when ``artifact_id`` is falsy (no artifact recorded).
      - run OWNERSHIP (Kiro #4) — CONDITIONAL: a manifest entry carrying a
        ``run_id`` must match the passed ``run_id``; a legacy entry WITHOUT a
        run_id (~2169 pre-date the write side) passes (rejecting it would lose
        real data). A mismatch → ``unavailable``.
      - artifact TYPE (Kiro #4) — when ``expected_type`` is given, ``entry.type``
        must be IN it: a scalar is one valid type, a set/collection is a closed
        allowlist of valid types (think accepts research OR alternatives). A
        scalar is normalized to a 1-element set, so single-type stages stay
        exactly as strict; a type outside the set → ``unavailable`` (never
        fail-open). ``expected_type=None`` skips the check entirely.
      - the existing path-containment TRUST BOUNDARY (run_44a06138) — a
        ``..``-escaping ``entry.file`` is refused loudly → ``unavailable``.
    Any missing/corrupt file or manifest → ``unavailable`` (never a silent 0).
    """
    if not artifact_id:
        return LoadResult("not_run")
    ws = _get_workspace()
    manifest_file = ws / "Projects" / project / ".artifacts" / "manifest.json"
    if not manifest_file.exists():
        return LoadResult("unavailable")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return LoadResult("unavailable")
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    for entry in manifest.get("artifacts", []):
        if entry.get("id") != artifact_id:
            continue
        # OWNERSHIP (#4) — conditional: enforce only when the entry HAS a run_id.
        # A legacy run_id-less entry passes (the ~2169-entry back-compat case);
        # a present-but-mismatched run_id is a cross-run reference → refuse.
        entry_run_id = entry.get("run_id")
        if entry_run_id is not None and run_id is not None and entry_run_id != run_id:
            return LoadResult("unavailable")
        # TYPE (#4) — enforce only when caller declares an expected_type.
        # MEMBERSHIP, not scalar equality: a stage may have >1 contract-blessed
        # artifact type (think → research OR alternatives, per STAGE_ROUTING's
        # produces + optional_produces). A scalar expected_type is normalized to a
        # 1-element set, so single-type stages stay exactly as strict. Still a
        # CLOSED allowlist — a type outside the set is rejected (never fail-open).
        if expected_type is not None:
            accepted = (
                expected_type
                if isinstance(expected_type, (set, frozenset, list, tuple))
                else {expected_type}
            )
            if entry.get("type") not in accepted:
                return LoadResult("unavailable")
        data_file = artifacts_dir / entry.get("file", "")
        # TRUST BOUNDARY (run_44a06138): read-side twin of
        # ArtifactRegistry._entry_to_artifact. The manifest `file` is
        # caller-influenced; a tampered `../../../secret.json` must NOT be
        # read. Both legit shapes (bare + runs/<id>/) resolve INSIDE
        # .artifacts/; only a ..-escape fails. Surface loudly, drop.
        try:
            if not data_file.resolve().is_relative_to(artifacts_dir.resolve()):
                print(json.dumps({"security": (
                    f"metrics load for artifact {artifact_id!r} in project "
                    f"{project!r} has an out-of-bounds file path "
                    f"{entry.get('file')!r} — refusing to read (manifest tampering?)."
                )}), file=sys.stderr)
                return LoadResult("unavailable")
        except (OSError, ValueError):
            return LoadResult("unavailable")  # un-resolvable path → fail closed
        if data_file.exists():
            try:
                return LoadResult("available", json.loads(data_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                return LoadResult("unavailable")
        return LoadResult("unavailable")  # entry matched but data file gone
    return LoadResult("unavailable")  # no entry with this id


# Stage → expected artifact type(s) (the read-contract #4 type check). Mirrors the
# STAGE_ROUTING produces(+optional_produces) contract: evaluate→evaluation,
# think→{research, alternatives} (think's produces=research + optional_produces=
# alternatives — BOTH are valid, so a set), plan→design_doc, build→changeset,
# review→review, test→test_report, deliver→delivery, goal_cycle→changeset.
# A scalar value = single valid type; a set = any member is valid (membership
# test in _load_artifact_for_metrics). Keeping think scalar silently dropped
# every type=alternatives THINK artifact from REPORT.md (#3).
_STAGE_ARTIFACT_TYPE = {
    "evaluate": "evaluation",
    "think": {"research", "alternatives"},
    "plan": "design_doc",
    "build": "changeset",
    "goal_cycle": "changeset",
    "review": "review",
    "test": "test_report",
    "deliver": "delivery",
}


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


# ═══════════════════════════════════════════════════════════════════════════
# Stage boundaries: `stage-start` / `stage-complete` (pipeline-speed-systematic-fix
# §4-A, F2, G1). Mechanical work moves into the tool: the model writes judgment
# fields only; derived fields, publish, record, timing and advance are computed
# here. ALL checks run BEFORE any write (fail-closed: an error writes nothing).
# The checks themselves are the existing ones — validate_artifact_data, validate()
# (via pipeline_validator.validate_stage_state on the in-memory candidate) and
# the stage-record schema — never a copy.
# ═══════════════════════════════════════════════════════════════════════════

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _write_run_atomic(run_file: Path, data: dict) -> None:
    """Write run.json via a same-dir temp file + os.replace (no torn file)."""
    tmp = run_file.with_name(f".run.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, run_file)


def _refuse_if_worktree_copy(script: str) -> None:
    """A copy of this tool inside a run worktree must never write the workspace that
    owns that worktree (it may be half-edited). Against another workspace (a test's
    temp SwarmWS) it is the code under test and may run."""
    from core import run_worktree as rw
    hit = rw.run_for_path(script)
    if hit is None:
        return
    rid, repo = hit
    ws = os.environ.get("SWARM_WORKSPACE")
    if ws and not rw.owned_by(rid, Path(ws).expanduser()):
        return
    print(json.dumps({"ok": False, "error": "this tool copy lives inside run worktree "
                      f"{rw.wt_path(rid, repo)}; run the main checkout's copy instead"}))
    sys.exit(2)


def main() -> None:
    import functools
    parser = argparse.ArgumentParser(
        description="Artifact registry CLI for SwarmAI pipeline", allow_abbrev=False,
    )
    # No option abbreviations: the run-owner gate reads --run-id/--stage/--status exactly
    # as written, so `--run-i X` must not silently mean --run-id (pipeline-run-worktree §4.2).
    sub = parser.add_subparsers(dest="command", required=True,
                                parser_class=functools.partial(argparse.ArgumentParser,
                                                               allow_abbrev=False))

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


    # learn
    p_learn = sub.add_parser("learn", help="Record pipeline outcome for learning")
    p_learn.add_argument("--project", required=True)
    p_learn.add_argument("--evaluation-id", required=True, help="ID of evaluation artifact")
    p_learn.add_argument("--outcome", required=True, choices=["success", "partial", "failure", "cancelled"])
    p_learn.add_argument("--actual-effort", default=None, help="Actual effort (T-shirt or sessions)")
    p_learn.add_argument("--lessons", default=None, help="Semicolon-separated lessons")

    # projects
    sub.add_parser("projects", help="List all projects")


    # main-red (core/main_red.py): tests red on main, each awaiting its own fix run
    sub.add_parser("main-red", help="List tests red on main (pre-existing), read-only")


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
    p_rel_gate.add_argument("--verify", action="store_true", help="PRE-PUBLISH gate (fail-closed): authorize the publish IFF the CI-green marker attests the commit being released. exit 0=authorized, 1=blocked. All-local, no network. Call in s_swarm-release Stage 7b BEFORE `gh release create/edit`. (Replaces the release_publish_guard hook.)")
    p_rel_gate.add_argument("--clear", action="store_true", help="Delete the marker (call after publish, or to reset)")
    p_rel_gate.add_argument("--ref", default=None, help="Tag or sha of the commit BEING RELEASED (e.g. v1.26.0). Resolves <ref>^{commit}, polls CI for THAT commit, records it (+the tag) in the marker. Omit for a branch-tip release (legacy HEAD behavior).")


    _refuse_if_worktree_copy(__file__)
    retired_run_commands = {
        "schema", "state", "advance", "run-get", "run-surface-changes", "run-history", "run-budget", "main-red", "run-status", "run-summary", "run-analytics",
        "run-create", "run-update", "run-commit", "run-checkpoint", "run-resume",
        "run-report", "run-metrics", "run-cultivate", "run-observe", "stage-start",
        "stage-complete", "gate0-prompt", "gate2-recheck", "gate2-fixspec",
        "gate2-ledger", "gate2-next", "run-diff", "run-repo-add", "run-claim-main",
        "cleanup-orphans", "purge-garbage",
    }
    if len(sys.argv) > 1 and sys.argv[1] in retired_run_commands:
        print(json.dumps({
            "error": "legacy pipeline mutation command removed by Pipeline v1; use "
                     "backend/scripts/pipeline_v1.py (runs without protected contract.json "
                     "are read-only historical records)"}), file=sys.stderr)
        sys.exit(2)
    args = parser.parse_args()

    retired = (args.command == "publish" and (getattr(args, "run_id", None)
                                            or getattr(args, "stage", None)))
    if retired:
        print(json.dumps({
            "error": "legacy pipeline mutation command removed by Pipeline v1; use "
                     "backend/scripts/pipeline_v1.py (runs without protected contract.json "
                     "are read-only historical records)"
        }), file=sys.stderr)
        raise SystemExit(2)
    reg = ArtifactRegistry(_get_workspace())

    handlers = {
        "discover": cmd_discover,
        "publish": cmd_publish,
        "learn": cmd_learn,
        "projects": cmd_projects,
        "bind": cmd_bind,
        "ddd-health": cmd_ddd_health,
        "ddd-retire": cmd_ddd_retire,
        "ddd-noise": cmd_ddd_noise,
        "release-gate": cmd_release_gate,
    }
    try:
        handlers[args.command](args, reg)
    except json.JSONDecodeError:
        # NARROW the catch below: JSONDecodeError subclasses ValueError. A corrupt
        # user-supplied --stage-json / --taste-decision / a malformed run.json must
        # KEEP its original uncaught-traceback behavior (it is a user/data error, not
        # a blocked-identifier refusal), not be mislabeled as a path-identifier reject.
        raise
    except ValueError as e:
        # run_dd8411f6 (#2, Gate-1 amendment B): a traversal run_id/project raises
        # ValueError from _validate_path_identifiers (via _run_dir / _ensure_project_dir).
        # main() has no other top-level catch, so without this a blocked-but-malicious
        # identifier would crash with a Python traceback instead of the CLI's standard
        # {"error": ...} JSON + clean non-zero exit. The traversal is still BLOCKED
        # (no path escape) either way; this just makes the refusal legible, not a crash.
        # NOTE: the `advance` command does NOT reach here — cmd_advance wraps
        # _auto_validate_before_advance in its own `except Exception` and surfaces a
        # traversal run_id as "Validator crashed" + exit 1 (still fail-closed/blocked,
        # just a different exit code + message). Every OTHER run-id-bearing command
        # (run-update/commit/checkpoint/resume/observe/report) reaches this handler.
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
