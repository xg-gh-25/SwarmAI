"""Section response schemas for the Daily Work Operating Loop sections.

This module defines the Pydantic models for section API responses, providing
a unified response contract for all six sections: Signals, Plan, Execute,
Communicate, Artifacts, and Reflection.

Requirements: 7.1, 7.9, 33.1
"""
from datetime import datetime
from typing import Generic, TypeVar, Optional
from pydantic import BaseModel, Field


# Generic type variable for section items
T = TypeVar("T")


class SectionGroup(BaseModel, Generic[T]):
    """A group of items within a section, organized by sub-category.
    
    Used to group items by status or type within each section.
    For example, Signals section groups items by: pending, overdue, in_discussion.
    
    Requirement 7.1-7.7: Section endpoints return items grouped by sub-category.
    """
    name: str = Field(..., description="Name of the group (e.g., 'pending', 'today', 'wip')")
    items: list[T] = Field(default_factory=list, description="List of items in this group")


class Pagination(BaseModel):
    """Pagination metadata for section responses.
    
    Requirement 7.10: ALL section list endpoints SHALL support pagination
    with limit/offset parameters (default limit: 50).
    
    Requirement 33.3: ALL section endpoints SHALL support `limit` and `offset`
    query parameters for pagination.
    """
    limit: int = Field(..., ge=1, le=100, description="Maximum number of items per page")
    offset: int = Field(..., ge=0, description="Number of items to skip")
    total: int = Field(..., ge=0, description="Total number of items available")
    has_more: bool = Field(..., description="Whether more items are available beyond this page")


class SectionResponse(BaseModel, Generic[T]):
    """Unified response model for all section list endpoints.
    
    Requirement 33.1: ALL section list endpoints SHALL return a standard response shape:
    {
      "counts": { "total": number, "byStatus": { ... } },
      "groups": [ { "name": string, "items": [...] } ],
      "pagination": { "limit": number, "offset": number, "total": number, "hasMore": boolean },
      "sortKeys": [ "created_at", "updated_at", "priority", ... ],
      "lastUpdatedAt": "ISO8601 timestamp"
    }
    
    Requirement 7.9: THE API SHALL include item counts in section responses for badge display.
    Requirement 7.11: ALL section endpoints SHALL return the unified response contract.
    """
    counts: dict[str, int] = Field(
        ..., 
        description="Item counts by sub-category (e.g., {'total': 10, 'pending': 5, 'overdue': 2})"
    )
    groups: list[SectionGroup[T]] = Field(
        default_factory=list,
        description="Items grouped by sub-category"
    )
    pagination: Pagination = Field(..., description="Pagination metadata")
    sort_keys: list[str] = Field(
        default_factory=list,
        description="Available sort keys for this section (e.g., ['created_at', 'updated_at', 'priority'])"
    )
    last_updated_at: Optional[datetime] = Field(
        None, 
        description="Timestamp of the most recently updated item in the response"
    )


class SignalsCounts(BaseModel):
    """Counts for the Signals section sub-categories.
    
    Requirement 3.6: THE Workspace_Explorer SHALL display the Signals section
    with sub-categories: Pending, Overdue, In Discussion.
    """
    total: int = Field(default=0, ge=0, description="Total number of signals")
    pending: int = Field(default=0, ge=0, description="Number of pending signals")
    overdue: int = Field(default=0, ge=0, description="Number of overdue signals")
    in_discussion: int = Field(default=0, ge=0, description="Number of signals in discussion")


class PlanCounts(BaseModel):
    """Counts for the Plan section sub-categories.
    
    Requirement 3.7: THE Workspace_Explorer SHALL display the Plan section
    with sub-categories: Today's Focus, Upcoming, Blocked.
    """
    total: int = Field(default=0, ge=0, description="Total number of plan items")
    today: int = Field(default=0, ge=0, description="Number of items in Today's Focus")
    upcoming: int = Field(default=0, ge=0, description="Number of upcoming items")
    blocked: int = Field(default=0, ge=0, description="Number of blocked items")


class ExecuteCounts(BaseModel):
    """Counts for the Execute section sub-categories.
    
    Requirement 3.8: THE Workspace_Explorer SHALL display the Execute section
    with sub-categories: Draft, WIP, Blocked, Completed.
    """
    total: int = Field(default=0, ge=0, description="Total number of tasks")
    draft: int = Field(default=0, ge=0, description="Number of draft tasks")
    wip: int = Field(default=0, ge=0, description="Number of work-in-progress tasks")
    blocked: int = Field(default=0, ge=0, description="Number of blocked tasks")
    completed: int = Field(default=0, ge=0, description="Number of completed tasks")


class CommunicateCounts(BaseModel):
    """Counts for the Communicate section sub-categories.
    
    Requirement 3.9: THE Workspace_Explorer SHALL display the Communicate section
    with sub-categories: Pending Replies, AI Drafts, Follow-ups.
    """
    total: int = Field(default=0, ge=0, description="Total number of communications")
    pending_reply: int = Field(default=0, ge=0, description="Number of pending replies")
    ai_draft: int = Field(default=0, ge=0, description="Number of AI drafts")
    follow_up: int = Field(default=0, ge=0, description="Number of follow-ups")


class ArtifactsCounts(BaseModel):
    """Counts for the Artifacts section sub-categories.
    
    Requirement 3.10: THE Workspace_Explorer SHALL display the Artifacts section
    with sub-categories: Plans, Reports, Docs, Decisions.
    """
    total: int = Field(default=0, ge=0, description="Total number of artifacts")
    plan: int = Field(default=0, ge=0, description="Number of plan artifacts")
    report: int = Field(default=0, ge=0, description="Number of report artifacts")
    doc: int = Field(default=0, ge=0, description="Number of doc artifacts")
    decision: int = Field(default=0, ge=0, description="Number of decision artifacts")


class ReflectionCounts(BaseModel):
    """Counts for the Reflection section sub-categories.
    
    Requirement 3.11: THE Workspace_Explorer SHALL display the Reflection section
    with sub-categories: Daily Recap, Weekly Summary, Lessons Learned.
    """
    total: int = Field(default=0, ge=0, description="Total number of reflections")
    daily_recap: int = Field(default=0, ge=0, description="Number of daily recaps")
    weekly_summary: int = Field(default=0, ge=0, description="Number of weekly summaries")
    lessons_learned: int = Field(default=0, ge=0, description="Number of lessons learned")


class SectionCounts(BaseModel):
    """Aggregated counts for all six Daily Work Operating Loop sections.
    
    Requirement 7.1: THE API SHALL provide GET /api/workspaces/{id}/sections
    endpoint returning aggregated counts for all six sections.
    
    Requirement 8.6: THE Frontend SHALL define SectionCounts interface with
    counts for each section and sub-category.
    """
    signals: SignalsCounts = Field(
        default_factory=SignalsCounts,
        description="Counts for the Signals section"
    )
    plan: PlanCounts = Field(
        default_factory=PlanCounts,
        description="Counts for the Plan section"
    )
    execute: ExecuteCounts = Field(
        default_factory=ExecuteCounts,
        description="Counts for the Execute section"
    )
    communicate: CommunicateCounts = Field(
        default_factory=CommunicateCounts,
        description="Counts for the Communicate section"
    )
    artifacts: ArtifactsCounts = Field(
        default_factory=ArtifactsCounts,
        description="Counts for the Artifacts section"
    )
    reflection: ReflectionCounts = Field(
        default_factory=ReflectionCounts,
        description="Counts for the Reflection section"
    )
