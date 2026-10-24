"""Pipeline run schema definitions for the Radar pipeline panel API.

Defines Pydantic models for the ``/api/pipelines`` endpoint: Pipeline v1 runs only
(a protected contract), every value from the run's projection through
``core.pipeline_metrics``; a value never measured is None.

Key models:

- ``PipelineRunStatus``   -- Enum: running, paused, completed, failed, abandoned
- ``PipelineRunResponse`` -- Single pipeline run for the dashboard
- ``PipelineStatusSummary`` -- Aggregate counts across all projects
- ``PipelineDashboard``   -- Top-level response with list + summary

The frontend service layer converts to camelCase in
``desktop/src/services/pipelines.ts``.
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
    ABANDONED = "abandoned"   # a signed abandon event (cancelled by the user included)


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
    tokens_consumed: Optional[int] = Field(None, description="Measured tokens; None when never measured")
    pause_kind: Optional[str] = Field(
        None, description="What the run needs from a person: 'decision' / 'stopped' / "
                          "'worktree' / 'idle' (core.pipeline_metrics.pause_kind), else None")
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")
    audit_card: Optional[str] = Field(
        None, description="Renderer-owned five-dimension completed run card")


class PipelineStatusSummary(BaseModel):
    """Aggregate counts across all projects."""
    running: int = 0
    paused: int = 0
    completed: int = 0
    abandoned: int = 0
    total_tokens: Optional[int] = None  # None when any listed run is unmeasured


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
    tokens_actual: Optional[int] = None  # None when unmeasured (or any run in it is)
    created_at: str = ""
    updated_at: str = ""
    pause_kind: Optional[str] = None
    needs_you: bool = Field(False, description="the user has to act on this run (core.pipeline_metrics.needs_you)")
    checkpoint_reason: Optional[str] = None
    owner_session_id: Optional[str] = Field(
        None, description="Pipeline v1: the chat session that drives the run now")
    verified: Optional[bool] = Field(
        None,
        description=("completed runs only: True when DELIVER was push-ready (every gate and "
                     "the test evidence held on the final code), False when it completed "
                     "unverified; None when not applicable (the run's push_ready)."),
    )
    report_path: Optional[str] = Field(
        None,
        description=(
            "Workspace-relative path to this run's REPORT.md if it exists, else None. "
            "The overlay dispatches swarm:open-file{path} to render it in Canvas. "
            "Presence == 'has a report' (frontend derives hasReport = reportPath != null)."
        ),
    )
    audit_card: Optional[str] = None


class PipelineProjectGroup(BaseModel):
    """A project/DDD group: its health rollup + its runs."""
    project: str
    run_count: int = 0
    completion_rate: float = Field(0.0, description="completed / total, 0-1")
    avg_cycle_min: Optional[float] = None
    aborted_count: int = Field(0, description="runs that need you (core.pipeline_metrics.needs_you)")
    runs: list[PipelineRunSummary] = Field(default_factory=list)


class PipelineTrendPoint(BaseModel):
    """One time bucket (ISO week) in the trend series."""
    week: str = Field(..., description="ISO week start date YYYY-MM-DD")
    runs: int = 0
    completed: int = 0
    avg_cycle_min: Optional[float] = None
    tokens: Optional[int] = None  # None when any run of the week is unmeasured


class PipelineOverall(BaseModel):
    """Global rollup across all projects in the window."""
    total_runs: int = 0
    completed: int = 0
    completion_rate: float = 0.0
    avg_cycle_min: Optional[float] = None
    tokens_actual: Optional[int] = None  # None when unmeasured (or any run in it is)
    profile_mix: dict[str, int] = Field(default_factory=dict)
    aborted_count: int = 0


class PipelineAnalytics(BaseModel):
    """GET /api/pipelines/analytics — the retro dashboard payload."""
    window: str = Field("30d", description="always 30d (the run window)")
    overall: PipelineOverall = Field(default_factory=PipelineOverall)
    trend: list[PipelineTrendPoint] = Field(default_factory=list)
    by_project: list[PipelineProjectGroup] = Field(default_factory=list)


class PipelineCommit(BaseModel):
    """A commit this run landed (the run's landing progress)."""
    repo: str = ""
    sha: str = ""
    files: list[str] = Field(default_factory=list)


class PipelineDecisionOption(BaseModel):
    """One option of a pending Pipeline v1 decision (core/run_protocol/decision.py)."""
    id: str
    choice: str = ""
    consequence: str = ""
    reversible: Optional[bool] = None
    risk: Optional[str] = None


class PipelineDecision(BaseModel):
    """The question a Pipeline v1 run is waiting on a person to answer."""
    request_id: str
    kind: Optional[str] = None
    question: str = ""
    why_evidence_insufficient: Optional[str] = None
    options: list[PipelineDecisionOption] = Field(default_factory=list)


class PipelineDecisionAnswer(BaseModel):
    """POST /api/pipelines/{run_id}/decision — a person's pick on the dashboard."""
    request_id: str
    option_id: str


class PipelineRunDetail(BaseModel):
    """GET /api/pipelines/{run_id} — one run's full retrospective."""
    id: str
    project: str = ""
    continue_prompt: str = Field(
        "", description="what the owner session is asked to run to carry the run on "
                        "(routers.pipelines.continue_prompt: the pipeline command form is "
                        "the backend's, never the frontend's)")
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
    commits: list[PipelineCommit] = Field(default_factory=list)
    checkpoint_reason: Optional[str] = None
    pause_kind: Optional[str] = Field(
        None, description=("What the run needs from the user: 'decision' (answer the "
                           "question), 'stopped' (Stop / tab closed; Resume), 'worktree' "
                           "(worktree unavailable; Resume retries), 'idle' (active but no "
                           "session drives it; continue in its session), else None"))
    decision: Optional[PipelineDecision] = Field(
        None, description="Pipeline v1: the pending human decision, when pause_kind is 'decision'")
    owner_session_id: Optional[str] = Field(
        None, description="Pipeline v1: the chat session that drives the run now")
    created_at: str = ""
    updated_at: str = ""
    audit_card: Optional[str] = Field(None, description="Renderer-owned final run audit card")
