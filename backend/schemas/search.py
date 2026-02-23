"""Search result schemas for the Global Search functionality.

This module defines the Pydantic models for search results returned by
the SearchManager. Results are grouped by entity type with a limit of
50 items per type.

Requirements: 31.1-31.7, 38.1-38.12
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class SearchResultItem(BaseModel):
    """A single search result item."""
    id: str = Field(..., description="Entity ID")
    entity_type: str = Field(..., description="Type of entity (todo, task, plan_item, communication, artifact, reflection, thread)")
    title: str = Field(..., description="Entity title")
    description: Optional[str] = Field(None, description="Entity description or summary text")
    workspace_id: str = Field(..., description="Workspace the entity belongs to")
    workspace_name: Optional[str] = Field(None, description="Workspace display name (populated in global view)")
    is_archived: bool = Field(default=False, description="Whether the workspace is archived")
    updated_at: str = Field(..., description="Last updated timestamp")


class EntityTypeResults(BaseModel):
    """Search results for a single entity type."""
    entity_type: str = Field(..., description="Entity type name")
    items: List[SearchResultItem] = Field(default_factory=list, description="Matching items")
    total: int = Field(default=0, description="Total matching items (may exceed returned items)")
    has_more: bool = Field(default=False, description="Whether more results exist beyond the limit")


class SearchResults(BaseModel):
    """Aggregated search results across all entity types.

    Validates: Requirements 38.4, 38.10, 38.11
    """
    query: str = Field(..., description="The search query that was executed")
    scope: str = Field(..., description="Search scope (workspace_id or 'all')")
    groups: List[EntityTypeResults] = Field(default_factory=list, description="Results grouped by entity type")
    total: int = Field(default=0, description="Total results across all entity types")
