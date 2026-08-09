"""Pipeline run schema definitions for the Radar pipeline panel API.

Defines Pydantic models for the ``/api/pipelines`` endpoint, which reads
pipeline run state from ``.artifacts/runs/*/run.json`` files (with legacy ``pipeline-run-*.json`` fallback) across
all projects.

Key models:

- ``PipelineRunStatus``   -- Enum: running, paused, completed, failed, cancelled
- ``PipelineRunResponse`` -- Single pipeline run for the dashboard
- ``PipelineStatusSummary`` -- Aggregate counts across all projects
- ``PipelineDashboard``   -- Top-level response with list + summary

The frontend service layer converts to camelCase in
``desktop/src/services/radar.ts``.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PipelineRunStatus(str, Enum):
    """Pipeline run execution status."""
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # An abandoned run: a stale orphan that was never resumed, OR a paused run
    # superseded by a later completed run. The distinction lives in
    # `abandon_reason` (orphaned_no_resume vs superseded_by_<id>). Without this
    # enum value, _to_response silently fell abandoned→RUNNING, mis-rendering
    # abandoned runs as active in the dashboard.
    ABANDONED = "abandoned"


class PipelineCheckpoint(BaseModel):
    """Checkpoint metadata for a paused pipeline."""
    reason: str = Field(..., description="Why the pipeline paused")
    stage: str = Field(..., description="Stage where it paused")
    checkpointed_at: str = Field(..., description="ISO timestamp")
    completed_stages: list[str] = Field(default_factory=list)
    resumed_at: Optional[str] = Field(None, description="ISO timestamp if resumed")


class PipelineRunResponse(BaseModel):
    """Response model for a single pipeline run.

    All field names use snake_case per backend convention.
    Frontend converts to camelCase.
    """
    id: str = Field(..., description="Pipeline run ID (run_<uuid8>)")
    project: str = Field(..., description="Project name")
    requirement: str = Field(..., description="Requirement text (truncated to 80 chars)")
    status: PipelineRunStatus
    profile: str = Field("full", description="Pipeline profile: full/trivial/research/docs/bugfix")
    progress: str = Field(..., description="Stages completed/total (e.g., '3/8')")
    stages_completed: int = Field(0, description="Number of completed stages")
    stages_total: int = Field(8, description="Total stages in profile")
    tokens_consumed: int = Field(0, description="Total tokens used across all stages")
    taste_decisions: int = Field(0, description="Number of pending taste decisions")
    checkpoint: Optional[PipelineCheckpoint] = Field(None, description="Checkpoint info if paused")
    pause_kind: Optional[str] = Field(
        None,
        description="Semantic classification of a paused run, for attention-queue "
                    "consumers (Radar 'NEEDS YOU'). 'crash_residue' = paused by the "
                    "orphan-transition when a session died (checkpoint.reason == the "
                    "canonical _CRASH_ZOMBIE_REASON) — NOT a real decision, drop it. "
                    "'decision' = a genuine pause the user must act on (Gate BLOCK / "
                    "L2 escalation / budget / retry-exhausted). None for non-paused runs.",
    )
    abandon_reason: Optional[str] = Field(
        None,
        description="Why an abandoned run was abandoned: 'orphaned_no_resume' "
                    "(unrecovered crash-orphan) vs 'superseded_by_<id>' "
                    "(finished by a later run). None for non-abandoned runs.",
    )
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")


class PipelineStatusSummary(BaseModel):
    """Aggregate counts across all projects."""
    running: int = 0
    paused: int = 0
    completed: int = 0
    abandoned: int = 0
    total_tokens: int = 0


class PipelineDashboard(BaseModel):
    """Top-level response for the pipeline dashboard."""
    pipelines: list[PipelineRunResponse] = Field(default_factory=list)
    count: int = 0
    summary: PipelineStatusSummary = Field(default_factory=PipelineStatusSummary)


# ── Retro-Analytics dashboard (run_f8494370) ─────────────────────────────────
# The WORK-zone Pipeline NavCard is a RETROSPECTIVE surface (chat is the live
# surface). These models back GET /api/pipelines/analytics (overall + trend +
# by-project) and GET /api/pipelines/{run_id} (per-run retro detail).

class PipelineRunSummary(BaseModel):
    """One run's row in the by-project roster (subset for the list view)."""
    id: str
    requirement: str
    status: str
    profile: str
    progress: str = ""
    cycle_time_min: Optional[float] = Field(None, description="duration_minutes; None if not finished")
    tokens_actual: int = 0
    tokens_est: int = 0
    created_at: str = ""
    updated_at: str = ""
    pause_kind: Optional[str] = None
    checkpoint_reason: Optional[str] = None
    report_path: Optional[str] = Field(
        None,
        description=(
            "Workspace-relative path to this run's REPORT.md if it exists, else None. "
            "The overlay dispatches swarm:open-file{path} to render it in Canvas. "
            "Presence == 'has a report' (frontend derives hasReport = reportPath != null)."
        ),
    )


class PipelineProjectGroup(BaseModel):
    """A project/DDD group: its health rollup + its runs."""
    project: str
    run_count: int = 0
    completion_rate: float = Field(0.0, description="completed / total (garbage excluded), 0-1")
    avg_cycle_min: Optional[float] = None
    # run_0e68e235: GENUINE decision-pauses only. Garbage (abandoned / crash-residue-
    # paused, never delivered) is excluded from analytics entirely and is NOT
    # needs-you — the user can take no action on a dead run. Delivered-but-mislabeled
    # abandoned runs are recategorized to completed, not counted here.
    aborted_count: int = Field(0, description="genuine decision-pauses needing your attention (garbage excluded)")
    runs: list[PipelineRunSummary] = Field(default_factory=list)


class PipelineTrendPoint(BaseModel):
    """One time bucket (ISO week) in the trend series."""
    week: str = Field(..., description="ISO week start date YYYY-MM-DD")
    runs: int = 0
    completed: int = 0
    avg_cycle_min: Optional[float] = None
    tokens: int = 0


class PipelineOverall(BaseModel):
    """Global rollup across all projects in the window."""
    total_runs: int = 0
    completed: int = 0
    completion_rate: float = 0.0
    avg_cycle_min: Optional[float] = None
    tokens_actual: int = 0
    tokens_est: int = 0
    profile_mix: dict[str, int] = Field(default_factory=dict)
    aborted_count: int = 0


class PipelineAnalytics(BaseModel):
    """GET /api/pipelines/analytics — the retro dashboard payload."""
    window: str = Field("30d", description="30d | ytd")
    overall: PipelineOverall = Field(default_factory=PipelineOverall)
    trend: list[PipelineTrendPoint] = Field(default_factory=list)
    by_project: list[PipelineProjectGroup] = Field(default_factory=list)


class PipelineStageTokens(BaseModel):
    """Per-stage estimated-vs-actual token cost for the detail view."""
    stage: str
    est: int = 0
    actual: int = 0


class PipelineCommit(BaseModel):
    """A commit this run produced (persisted by run-commit, G1)."""
    repo: str = ""
    sha: str = ""
    files: list[str] = Field(default_factory=list)


class PipelineRunDetail(BaseModel):
    """GET /api/pipelines/{run_id} — one run's full retrospective."""
    id: str
    project: str = ""
    requirement: str = ""
    status: str = ""
    profile: str = ""
    cycle_time_min: Optional[float] = None
    report_md: str = Field("", description="REPORT.md body if present, else empty")
    report_path: Optional[str] = Field(
        None,
        description=(
            "Workspace-relative path to REPORT.md if present, else None. The detail "
            "drawer's 'View report in Canvas' button dispatches swarm:open-file{path}."
        ),
    )
    reflect_lessons: list[str] = Field(default_factory=list)
    stage_tokens: list[PipelineStageTokens] = Field(default_factory=list)
    commits: list[PipelineCommit] = Field(default_factory=list)
    checkpoint_reason: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
