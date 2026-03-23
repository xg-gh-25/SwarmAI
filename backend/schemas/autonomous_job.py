"""Autonomous job schema definitions for the Swarm Radar API.

Defines the Pydantic models for the autonomous jobs endpoint, which reads
real job definitions from ``jobs.yaml`` and runtime state from ``state.json``
in the SwarmWS job scheduler directory.

Key models:

- ``AutonomousJobCategory``  — Enum: system, user_defined
- ``AutonomousJobStatus``    — Enum: running, paused, error, completed
- ``AutonomousJobResponse``  — Full job response model with snake_case fields

The frontend service layer converts to camelCase via ``jobToCamelCase()``
in ``desktop/src/services/radar.ts``.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class AutonomousJobCategory(str, Enum):
    """Category of an autonomous job."""
    SYSTEM = "system"
    USER_DEFINED = "user_defined"


class AutonomousJobStatus(str, Enum):
    """Execution status of an autonomous job."""
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


class AutonomousJobResponse(BaseModel):
    """Response model for a single autonomous job.

    All field names use snake_case per the backend convention.
    The frontend service layer converts to camelCase via jobToCamelCase().
    """
    id: str = Field(..., description="Unique job identifier")
    name: str = Field(..., description="Human-readable job name")
    category: AutonomousJobCategory = Field(
        ..., description="Job category: system or user_defined"
    )
    status: AutonomousJobStatus = Field(
        ..., description="Current execution status"
    )
    schedule: Optional[str] = Field(
        None, description="Cron expression or dependency chain (e.g., '0 8,14,20 * * *' or 'after:signal-fetch')"
    )
    last_run_at: Optional[str] = Field(
        None, description="ISO 8601 timestamp of last execution"
    )
    next_run_at: Optional[str] = Field(
        None, description="ISO 8601 timestamp of next scheduled execution"
    )
    description: Optional[str] = Field(
        None, description="Brief description of what the job does"
    )
    total_runs: int = Field(
        0, description="Total number of times this job has executed"
    )
    consecutive_failures: int = Field(
        0, description="Current streak of consecutive failures (0 = healthy)"
    )
    last_status: Optional[str] = Field(
        None, description="Outcome of last execution: success, failed, skipped, never"
    )
