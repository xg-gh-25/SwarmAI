"""Section API endpoints for the Daily Work Operating Loop.

This module provides endpoints for querying section data across the six phases:
Signals → Plan → Execute → Communicate → Artifacts → Reflection.

Each endpoint returns a unified SectionResponse with counts, groups, pagination,
sort_keys, and last_updated_at. Supports workspace_id="all" for aggregation
across all non-archived workspaces.

Requirements: 7.1-7.12
"""
import logging
from typing import Optional

from fastapi import APIRouter, Query

from core.section_manager import section_manager
from schemas.section import SectionCounts, SectionResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{workspace_id}/sections", response_model=SectionCounts)
async def get_section_counts(workspace_id: str):
    """Get aggregated counts for all six sections.

    Args:
        workspace_id: Workspace ID or "all" for cross-workspace aggregation.

    Returns:
        SectionCounts with counts for each section and sub-category.

    Requirement 7.1: GET /api/workspaces/{id}/sections returning aggregated
    counts for all six sections.
    Requirement 7.8: workspace_id="all" aggregates across non-archived workspaces.
    """
    return await section_manager.get_section_counts(workspace_id)


@router.get("/{workspace_id}/sections/signals")
async def get_signals(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    sort_by: Optional[str] = Query(None, description="Sort field (e.g., created_at, updated_at, priority, due_date)"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    global_view: bool = Query(False, description="When true with workspace_id='all', enables opinionated SwarmWS Global View with recommended group"),
):
    """Get Signals (ToDos) grouped by status sub-category.

    Args:
        workspace_id: Workspace ID or "all" for cross-workspace aggregation.
        limit: Max items per page (default 50).
        offset: Items to skip.
        sort_by: Optional sort field.
        sort_order: Sort direction (asc/desc).
        global_view: When true and workspace_id="all", include a "recommended"
            group with top N items sorted by priority desc, updated_at desc.
            This is the opinionated SwarmWS Global View. When false (default),
            the neutral "all" scope aggregation is used without recommendations.

    Returns:
        SectionResponse with ToDos grouped by status.

    Requirement 7.2: GET /api/workspaces/{id}/sections/signals returning
    ToDos grouped by status sub-category.
    Requirement 7.8: workspace_id="all" aggregates across non-archived workspaces.
    Requirement 7.10: Support pagination with limit/offset parameters.
    Requirement 37.1-37.12: SwarmWS Global View vs neutral "all" scope.
    """
    return await section_manager.get_signals(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
        global_view=global_view,
    )


@router.get("/{workspace_id}/sections/plan")
async def get_plan(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    sort_by: Optional[str] = Query(None, description="Sort field (e.g., sort_order, created_at, updated_at, priority)"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    global_view: bool = Query(False, description="When true with workspace_id='all', enables opinionated SwarmWS Global View with recommended group"),
):
    """Get PlanItems grouped by focus_type sub-category.

    Args:
        workspace_id: Workspace ID or "all" for cross-workspace aggregation.
        limit: Max items per page (default 50).
        offset: Items to skip.
        sort_by: Optional sort field.
        sort_order: Sort direction (asc/desc).
        global_view: When true and workspace_id="all", include a "recommended"
            group. See Requirement 37 for details.

    Returns:
        SectionResponse with PlanItems grouped by focus_type.

    Requirement 7.3, 7.8, 7.10, 37.1-37.12
    """
    return await section_manager.get_plan(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
        global_view=global_view,
    )


@router.get("/{workspace_id}/sections/execute")
async def get_execute(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    sort_by: Optional[str] = Query(None, description="Sort field (e.g., created_at, updated_at, priority, status)"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    global_view: bool = Query(False, description="When true with workspace_id='all', enables opinionated SwarmWS Global View with recommended group"),
):
    """Get Tasks grouped by status sub-category.

    Args:
        workspace_id: Workspace ID or "all" for cross-workspace aggregation.
        limit: Max items per page (default 50).
        offset: Items to skip.
        sort_by: Optional sort field.
        sort_order: Sort direction (asc/desc).
        global_view: When true and workspace_id="all", include a "recommended"
            group. See Requirement 37 for details.

    Returns:
        SectionResponse with Tasks grouped by status.

    Requirement 7.4, 7.8, 7.10, 37.1-37.12
    """
    return await section_manager.get_execute(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
        global_view=global_view,
    )


@router.get("/{workspace_id}/sections/communicate")
async def get_communicate(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    sort_by: Optional[str] = Query(None, description="Sort field (e.g., created_at, updated_at, priority, due_date)"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    global_view: bool = Query(False, description="When true with workspace_id='all', enables opinionated SwarmWS Global View with recommended group"),
):
    """Get Communications grouped by status sub-category.

    Args:
        workspace_id: Workspace ID or "all" for cross-workspace aggregation.
        limit: Max items per page (default 50).
        offset: Items to skip.
        sort_by: Optional sort field.
        sort_order: Sort direction (asc/desc).
        global_view: When true and workspace_id="all", include a "recommended"
            group. See Requirement 37 for details.

    Returns:
        SectionResponse with Communications grouped by status.

    Requirement 7.5, 7.8, 7.10, 37.1-37.12
    """
    return await section_manager.get_communicate(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
        global_view=global_view,
    )


@router.get("/{workspace_id}/sections/artifacts")
async def get_artifacts(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    sort_by: Optional[str] = Query(None, description="Sort field (e.g., created_at, updated_at, artifact_type, title)"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    global_view: bool = Query(False, description="When true with workspace_id='all', enables opinionated SwarmWS Global View with recommended group"),
):
    """Get Artifacts grouped by artifact_type sub-category.

    Args:
        workspace_id: Workspace ID or "all" for cross-workspace aggregation.
        limit: Max items per page (default 50).
        offset: Items to skip.
        sort_by: Optional sort field.
        sort_order: Sort direction (asc/desc).
        global_view: When true and workspace_id="all", include a "recommended"
            group. See Requirement 37 for details.

    Returns:
        SectionResponse with Artifacts grouped by artifact_type.

    Requirement 7.6, 7.8, 7.10, 37.1-37.12
    """
    return await section_manager.get_artifacts(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
        global_view=global_view,
    )


@router.get("/{workspace_id}/sections/reflection")
async def get_reflection(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    sort_by: Optional[str] = Query(None, description="Sort field (e.g., created_at, updated_at, reflection_type, period_start)"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    global_view: bool = Query(False, description="When true with workspace_id='all', enables opinionated SwarmWS Global View with recommended group"),
):
    """Get Reflections grouped by reflection_type sub-category.

    Args:
        workspace_id: Workspace ID or "all" for cross-workspace aggregation.
        limit: Max items per page (default 50).
        offset: Items to skip.
        sort_by: Optional sort field.
        sort_order: Sort direction (asc/desc).
        global_view: When true and workspace_id="all", include a "recommended"
            group. See Requirement 37 for details.

    Returns:
        SectionResponse with Reflections grouped by reflection_type.

    Requirement 7.7, 7.8, 7.10, 37.1-37.12
    """
    return await section_manager.get_reflection(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
        global_view=global_view,
    )
