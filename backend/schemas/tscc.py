"""Thread-Scoped Cognitive Context (TSCC) Pydantic schemas.

This module defines the data models for the TSCC feature — a thread-owned,
collapsible cognitive context panel that provides live, thread-specific
cognitive state via SSE telemetry events, with filesystem-based snapshot
archival.

Key public models:

- ``TSCCContext``              — Scope and thread metadata (label, title, mode)
- ``TSCCActiveCapabilities``   — Grouped capability lists (skills, MCPs, tools)
- ``TSCCSource``               — A referenced source with workspace-relative path
- ``TSCCLiveState``            — Live cognitive state for a single thread
- ``TSCCState``                — Full TSCC state including thread metadata
- ``TSCCSnapshot``             — Point-in-time capture of TSCC state
- ``TelemetryEvent``           — A single SSE telemetry event
- ``SnapshotCreateRequest``    — Request body for snapshot creation

All field names use snake_case per backend convention.

Requirements: 18.1, 18.2, 18.3, 18.4
"""
from typing import Optional

from pydantic import BaseModel, Field


class TSCCContext(BaseModel):
    """Scope and thread metadata for the Current Context cognitive module.

    Displays where the user is working: workspace root or a specific project.
    """

    scope_label: str = Field(
        ...,
        description=(
            'Human-readable scope label, e.g. "Workspace: SwarmWS (General)" '
            'or "Project: {name}"'
        ),
    )
    thread_title: str = Field(..., description="Title of the chat thread")
    mode: Optional[str] = Field(
        None,
        description=(
            "Optional working mode tag: Research, Writing, Debugging, "
            "Exploration, or None"
        ),
    )


class TSCCActiveCapabilities(BaseModel):
    """Grouped capability lists activated during thread execution."""

    skills: list[str] = Field(default_factory=list, description="Active skill names")
    mcps: list[str] = Field(default_factory=list, description="Active MCP connector names")
    tools: list[str] = Field(default_factory=list, description="Active tool names")


class TSCCSource(BaseModel):
    """A source file or material referenced during thread execution."""

    path: str = Field(..., description="Workspace-relative path to the source")
    origin: str = Field(
        ...,
        description=(
            "Provenance tag: Project, Knowledge Base, Notes, Memory, "
            "or External MCP"
        ),
    )


class TSCCLiveState(BaseModel):
    """Live cognitive state for a single thread.

    Contains data for all five cognitive modules: context, active agents,
    active capabilities, what AI is doing, active sources, and key summary.
    """

    context: TSCCContext = Field(..., description="Scope and thread metadata")
    active_agents: list[str] = Field(
        default_factory=list, description="Subagents currently engaged in the thread"
    )
    active_capabilities: TSCCActiveCapabilities = Field(
        default_factory=TSCCActiveCapabilities,
        description="Grouped capabilities (skills, MCPs, tools)",
    )
    what_ai_doing: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Current agent activity in human-readable language (max 4 items)",
    )
    active_sources: list[TSCCSource] = Field(
        default_factory=list, description="Sources referenced during execution"
    )
    key_summary: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Working conclusion bullet points (max 5 items)",
    )


class TSCCState(BaseModel):
    """Full TSCC state for a chat thread.

    Combines thread metadata (ID, project, scope) with the live cognitive
    state.  The ``lifecycle_state`` tracks the thread's execution phase.
    """

    thread_id: str = Field(..., description="Unique identifier for the chat thread")
    project_id: Optional[str] = Field(
        None, description="Project UUID, None for workspace-scoped threads"
    )
    scope_type: str = Field(
        ..., description='Operational scope: "workspace" or "project"'
    )
    last_updated_at: str = Field(..., description="ISO 8601 timestamp of last update")
    lifecycle_state: str = Field(
        "new",
        description=(
            "Thread lifecycle state: new, active, paused, failed, cancelled, "
            "or idle"
        ),
    )
    live_state: TSCCLiveState = Field(..., description="Live cognitive state")


class TSCCSnapshot(BaseModel):
    """Point-in-time capture of TSCC state, stored as a JSON file.

    Snapshots are created at key decision points (plan decomposition,
    decision recorded, multi-step phase completed) and archived in the
    thread's snapshot directory.
    """

    snapshot_id: str = Field(..., description="Unique snapshot identifier (UUID)")
    thread_id: str = Field(..., description="Chat thread this snapshot belongs to")
    timestamp: str = Field(..., description="ISO 8601 timestamp of snapshot creation")
    reason: str = Field(..., description="Snapshot trigger reason")
    lifecycle_state: str = Field(
        ..., description="Thread lifecycle state at snapshot time"
    )
    active_agents: list[str] = Field(
        default_factory=list, description="Agents active at snapshot time"
    )
    active_capabilities: TSCCActiveCapabilities = Field(
        default_factory=TSCCActiveCapabilities,
        description="Capabilities active at snapshot time",
    )
    what_ai_doing: list[str] = Field(
        default_factory=list, description="Agent activity at snapshot time"
    )
    active_sources: list[TSCCSource] = Field(
        default_factory=list, description="Sources referenced at snapshot time"
    )
    key_summary: list[str] = Field(
        default_factory=list, description="Working conclusion at snapshot time"
    )


class TelemetryEvent(BaseModel):
    """A single TSCC telemetry event emitted via SSE.

    Five event types: agent_activity, tool_invocation, capability_activated,
    sources_updated, summary_updated.
    """

    type: str = Field(
        ...,
        description=(
            "Event type: agent_activity, tool_invocation, "
            "capability_activated, sources_updated, or summary_updated"
        ),
    )
    thread_id: str = Field(..., description="Thread this event belongs to")
    timestamp: str = Field(..., description="ISO 8601 timestamp of event emission")
    data: dict = Field(
        ..., description="Event-specific payload with snake_case field names"
    )


class SnapshotCreateRequest(BaseModel):
    """Request body for creating a TSCC snapshot."""

    reason: str = Field(..., description="Trigger reason for the snapshot")
