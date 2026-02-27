"""Autonomous job schema definitions for the Swarm Radar placeholder API.

This module defines the Pydantic models for the autonomous jobs endpoint,
which returns hardcoded mock data in the initial release. The models
support two job categories (system built-in and user-defined) with four
possible statuses.

Key models:

- ``AutonomousJobCategory``  — Enum: system, user_defined
- ``AutonomousJobStatus``    — Enum: running, paused, error, completed
- ``AutonomousJobResponse``  — Full job response model with snake_case fields

This is a placeholder implementation. Future releases will populate jobs
from actual background task execution and user-configured schedules.
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
        None, description="Human-readable schedule description (e.g., 'Daily at 9am')"
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
