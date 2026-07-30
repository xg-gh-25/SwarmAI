"""Pipeline runs API router.

Reads pipeline run state from ``.artifacts/runs/*/run.json`` files (with legacy ``pipeline-run-*.json`` fallback)
across all projects in SwarmWS. Serves real data to the Radar pipeline panel.

Key endpoints:

- ``GET /``            -- All pipelines (active + recent completed)
- ``GET /?active=true`` -- Only running/paused pipelines

The router is registered in main.py with prefix ``/api/pipelines``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from core.pipeline_profiles import get_profile_stages
from jobs.paths import SWARMWS
from schemas.pipeline_run import (
    PipelineCheckpoint,
    PipelineDashboard,
    PipelineRunResponse,
    PipelineRunStatus,
    PipelineStatusSummary,
)
# Canonical crash-zombie discriminator + terminal-run predicate — single source
# of truth in artifact_cli (already reused by proactive_intelligence). Importing
# them here (rather than re-inventing a string/status match) keeps ONE definition
# of "this pause is crash residue" / "this run is finished" across all consumers.
from scripts.artifact_cli import _CRASH_ZOMBIE_REASON, is_terminal_run

logger = logging.getLogger(__name__)
router = APIRouter()

# Runs stuck in "running" with no update for this long are auto-marked failed.
# Configurable via env var for long-running pipelines (default: 60 min).
try:
    _STALE_THRESHOLD_MINUTES = int(os.environ.get("PIPELINE_STALE_THRESHOLD_MINUTES", "60"))
except (ValueError, TypeError):
    _STALE_THRESHOLD_MINUTES = 60


def _get_swarmws() -> Path:
    """Resolve SwarmWS path. Function (not constant) for testability."""
    return SWARMWS


def _get_profile_stage_count(profile: str | None) -> int:
    return len(get_profile_stages(profile))


def _is_stale(state: dict) -> bool:
    """Check if a running pipeline has gone stale (no update in threshold).

    A TERMINAL run is never stale — even if left status="running" on disk.
    A run that finished all stages (a completed reflect/deliver marker) but
    crashed before `run-update --status completed` ran is FINISHED, not a
    failure. Honoring `is_terminal_run` here mirrors the SAME guard that
    `artifact_cli._abandon_verdict` (before its abandon verdict) and
    `proactive_intelligence` (before auto-resume) already apply — so all three
    consumers of on-disk run state agree: terminal runs are skipped, never
    re-labeled. Without this, `_mark_failed` flipped delivered-but-crashed runs
    to "failed" on every dashboard poll (run_aad474c7: 9/9 stages completed,
    status=failed).
    """
    if state.get("status") != "running":
        return False
    if is_terminal_run(state):
        return False
    updated_at = state.get("updated_at", "")
    if not updated_at:
        return True
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age_minutes = (datetime.now(timezone.utc) - updated).total_seconds() / 60
        return age_minutes > _STALE_THRESHOLD_MINUTES
    except (ValueError, TypeError):
        return True


def _mark_failed(run_file: Path, state: dict) -> None:
    """Atomically mark a stale run as failed on disk."""
    state["status"] = "failed"
    state["failure_reason"] = "session ended without completion (auto-detected stale)"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        tmp = run_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(run_file)
        logger.info("Auto-marked stale pipeline %s as failed", state.get("id"))
    except OSError as e:
        logger.warning("Failed to mark stale pipeline %s: %s", state.get("id"), e)


def _load_pipeline_runs() -> list[dict]:
    """Scan all projects for pipeline run files.

    Returns raw dicts sorted by updated_at (newest first).
    Automatically marks stale "running" pipelines as "failed".
    Never raises — returns empty list on any error.
    """
    projects_dir = _get_swarmws() / "Projects"
    if not projects_dir.exists():
        return []

    runs = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        artifacts_dir = project_dir / ".artifacts"
        if not artifacts_dir.exists():
            continue

        seen_ids: set[str] = set()

        # New path: .artifacts/runs/*/run.json
        runs_subdir = artifacts_dir / "runs"
        if runs_subdir.exists():
            for rd in runs_subdir.iterdir():
                rf = rd / "run.json"
                if rf.exists():
                    try:
                        state = json.loads(rf.read_text(encoding="utf-8"))
                        # Auto-fail stale runs
                        if _is_stale(state):
                            _mark_failed(rf, state)
                        state["_project"] = project_dir.name
                        seen_ids.add(state.get("id", ""))
                        runs.append(state)
                    except (json.JSONDecodeError, OSError, KeyError) as e:
                        logger.debug("Skipping %s: %s", rf, e)

        # Legacy path: .artifacts/pipeline-run-*.json
        for run_file in artifacts_dir.glob("pipeline-run-*.json"):
            try:
                state = json.loads(run_file.read_text(encoding="utf-8"))
                if state.get("id") in seen_ids:
                    continue
                if _is_stale(state):
                    _mark_failed(run_file, state)
                state["_project"] = project_dir.name
                runs.append(state)
            except (json.JSONDecodeError, OSError, KeyError) as e:
                logger.debug("Skipping %s: %s", run_file.name, e)

    runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return runs


def _to_response(raw: dict) -> PipelineRunResponse:
    """Convert raw pipeline-run JSON dict to response model."""
    profile = raw.get("profile") or "full"
    stages = raw.get("stages", [])
    completed = sum(1 for s in stages if s.get("status") == "completed")
    total = _get_profile_stage_count(profile)
    consumed = sum(s.get("token_cost", 0) for s in stages)

    checkpoint_raw = raw.get("checkpoint")
    checkpoint = None
    if checkpoint_raw and isinstance(checkpoint_raw, dict):
        checkpoint = PipelineCheckpoint(
            reason=checkpoint_raw.get("reason", "unknown"),
            stage=checkpoint_raw.get("stage", "unknown"),
            checkpointed_at=checkpoint_raw.get("checkpointed_at", ""),
            completed_stages=checkpoint_raw.get("completed_stages", []),
            resumed_at=checkpoint_raw.get("resumed_at"),
        )

    try:
        status = PipelineRunStatus(raw.get("status", "running"))
    except ValueError:
        status = PipelineRunStatus.RUNNING

    # Terminal-but-crashed presentation (run_0f03fa9d): a run that finished all
    # stages (completed reflect/deliver) but crashed before `run-update --status
    # completed` is left status=running/paused on disk. It DELIVERED — present it
    # as completed rather than "running forever". Disk is NOT mutated (the stale
    # detector now skips terminal runs); this is read-path only, so the honest
    # status reaches BOTH the list partition and the summary counts. Only the
    # running/paused live-status values are coerced — an explicit terminal status
    # (failed/cancelled/abandoned) is never overridden.
    if status in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED) and is_terminal_run(raw):
        status = PipelineRunStatus.COMPLETED

    # Classify a paused run for attention-queue consumers (Radar "NEEDS YOU").
    # Only a genuine decision-pause should demand the user's attention; a pause
    # stamped with the canonical crash marker is residue left by a dead session.
    # Everything-not-crash → "decision" is intentional: budget / retry-exhausted /
    # gate_spawn_blocked / Gate BLOCK are ALL real pauses the user should act on
    # (mirrors artifact_cli._abandon_verdict, which reaps ONLY _CRASH_ZOMBIE_REASON).
    pause_kind: Optional[str] = None
    if status == PipelineRunStatus.PAUSED:
        reason = checkpoint_raw.get("reason") if isinstance(checkpoint_raw, dict) else None
        pause_kind = "crash_residue" if reason == _CRASH_ZOMBIE_REASON else "decision"

    return PipelineRunResponse(
        id=raw.get("id", "unknown"),
        project=raw.get("_project", raw.get("project", "unknown")),
        requirement=raw.get("requirement", "")[:80],
        status=status,
        profile=profile,
        progress=f"{completed}/{total}",
        stages_completed=completed,
        stages_total=total,
        tokens_consumed=consumed,
        taste_decisions=len(raw.get("taste_decisions", [])),
        checkpoint=checkpoint,
        pause_kind=pause_kind,
        abandon_reason=raw.get("abandon_reason"),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
    )


@router.patch("/{run_id}/cancel")
async def cancel_pipeline(run_id: str) -> dict:
    """Cancel a pipeline run by updating its run.json status to 'cancelled'.

    Returns 200 on success, 404 if run not found. Atomic write prevents corruption.
    """
    from fastapi.responses import JSONResponse

    projects_dir = _get_swarmws() / "Projects"
    if not projects_dir.exists():
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        run_file = project_dir / ".artifacts" / "runs" / run_id / "run.json"
        if run_file.exists():
            try:
                state = json.loads(run_file.read_text(encoding="utf-8"))
                state["status"] = "cancelled"
                # Atomic write: write to tmp then replace to prevent corruption
                tmp_file = run_file.with_suffix(".tmp")
                tmp_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
                tmp_file.replace(run_file)
                logger.info("Cancelled pipeline %s in project %s", run_id, project_dir.name)
                return {"status": "cancelled", "run_id": run_id}
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Failed to cancel %s: %s", run_id, e)
                return JSONResponse(
                    status_code=500,
                    content={"status": "error", "run_id": run_id, "detail": str(e)},
                )

    return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})


@router.get("", response_model=PipelineDashboard)
async def list_pipelines(
    active: Optional[bool] = Query(None, description="If true, only running/paused"),
) -> PipelineDashboard:
    """Return all pipeline runs with aggregate summary.

    Always returns HTTP 200 — empty dashboard if no pipelines exist.
    """
    all_runs = _load_pipeline_runs()
    if not all_runs:
        return PipelineDashboard()

    # Pair each raw run dict with its response: is_terminal_run needs the RAW
    # dict (stage array), which _to_response does not carry forward. Filtering on
    # the response alone (status only) would leave terminal zombies in `active`.
    paired = [(raw, _to_response(raw)) for raw in all_runs]

    if active:
        # Active = running/paused, EXCLUDING terminal zombies. A run whose stages
        # are all done (reflect/deliver completed) but whose status was flipped to
        # paused+crash-reason by the orphan-transition is FINISHED, not resumable —
        # it must not surface as active to ANY consumer. is_terminal_run is
        # stage-based (not status-based) precisely so a genuine mid-pipeline pause
        # (evaluate+think done, no reflect/deliver) is NOT misread as terminal.
        responses = [
            r for raw, r in paired
            if r.status in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED)
            and not is_terminal_run(raw)
        ]
    else:
        responses = [r for _raw, r in paired]
        # Keep all active + max 5 completed per project
        active_runs = [r for r in responses if r.status in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED)]
        completed_runs = [r for r in responses if r.status not in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED)]
        seen: dict[str, int] = {}
        trimmed = []
        for r in completed_runs:
            count = seen.get(r.project, 0)
            if count < 5:
                trimmed.append(r)
                seen[r.project] = count + 1
        responses = active_runs + trimmed

    summary = PipelineStatusSummary(
        running=sum(1 for r in responses if r.status == PipelineRunStatus.RUNNING),
        paused=sum(1 for r in responses if r.status == PipelineRunStatus.PAUSED),
        completed=sum(1 for r in responses if r.status == PipelineRunStatus.COMPLETED),
        abandoned=sum(1 for r in responses if r.status == PipelineRunStatus.ABANDONED),
        total_tokens=sum(r.tokens_consumed for r in responses),
    )

    return PipelineDashboard(
        pipelines=responses,
        count=len(responses),
        summary=summary,
    )
