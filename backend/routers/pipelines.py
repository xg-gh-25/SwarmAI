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
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from core.pipeline_profiles import get_profile_stages
from schemas.pipeline_run import (
    PipelineCheckpoint,
    PipelineDashboard,
    PipelineRunResponse,
    PipelineRunStatus,
    PipelineStatusSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_swarmws() -> Path:
    """Resolve SwarmWS path. Function (not constant) for testability."""
    return Path.home() / ".swarm-ai" / "SwarmWS"


def _get_profile_stage_count(profile: str | None) -> int:
    return len(get_profile_stages(profile))


def _load_pipeline_runs() -> list[dict]:
    """Scan all projects for pipeline run files.

    Returns raw dicts sorted by updated_at (newest first).
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
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
    )


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

    responses = [_to_response(r) for r in all_runs]

    if active:
        responses = [r for r in responses if r.status in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED)]
    else:
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
        total_tokens=sum(r.tokens_consumed for r in responses),
    )

    return PipelineDashboard(
        pipelines=responses,
        count=len(responses),
        summary=summary,
    )
