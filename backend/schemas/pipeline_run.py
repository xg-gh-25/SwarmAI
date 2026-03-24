"""Pipeline run schema definitions for the Radar pipeline panel API.

Defines Pydantic models for the ``/api/pipelines`` endpoint, which reads
pipeline run state from ``.artifacts/pipeline-run-*.json`` files across
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
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")


class PipelineStatusSummary(BaseModel):
    """Aggregate counts across all projects."""
    running: int = 0
    paused: int = 0
    completed: int = 0
    total_tokens: int = 0


class PipelineDashboard(BaseModel):
    """Top-level response for the pipeline dashboard."""
    pipelines: list[PipelineRunResponse] = Field(default_factory=list)
    count: int = 0
    summary: PipelineStatusSummary = Field(default_factory=PipelineStatusSummary)
