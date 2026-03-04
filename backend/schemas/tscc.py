"""Thread-Scoped Cognitive Context (TSCC) Pydantic schemas.

This module defines the data models for the TSCC feature — a thread-owned
cognitive context panel that displays system prompt metadata.

Key public models:

- ``TSCCContext``              — Scope and thread metadata (label, title, mode)
- ``TSCCActiveCapabilities``   — Grouped capability lists (skills, MCPs, tools)
- ``TSCCSource``               — A referenced source with workspace-relative path
- ``TSCCLiveState``            — Live cognitive state for a single thread
- ``TSCCState``                — Full TSCC state including thread metadata
- ``SystemPromptFileInfo``     — Metadata for a single context file
- ``SystemPromptMetadata``     — System prompt metadata (files, tokens, full text)

All field names use snake_case per backend convention.

Requirements: 6.1, 6.2, 6.7
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

    After TSCC simplification (Req 6.3-6.4), only ``context`` is actively
    populated.  The remaining fields are retained for API backward
    compatibility but are always empty/default.
    """

    context: TSCCContext = Field(..., description="Scope and thread metadata")
    # ── Deprecated fields (always empty after TSCC simplification) ──
    active_agents: list[str] = Field(
        default_factory=list, description="[Deprecated] Always empty"
    )
    active_capabilities: TSCCActiveCapabilities = Field(
        default_factory=TSCCActiveCapabilities,
        description="[Deprecated] Always empty",
    )
    what_ai_doing: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="[Deprecated] Always empty",
    )
    active_sources: list[TSCCSource] = Field(
        default_factory=list, description="[Deprecated] Always empty"
    )
    key_summary: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="[Deprecated] Always empty",
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


class SystemPromptFileInfo(BaseModel):
    """Metadata for a single context file loaded into the system prompt."""

    filename: str = Field(..., description="Context file name (e.g. SWARMAI.md)")
    tokens: int = Field(..., description="Estimated token count for this file")
    truncated: bool = Field(False, description="Whether this file was truncated to fit budget")


class SystemPromptMetadata(BaseModel):
    """System prompt metadata returned by the system-prompt endpoint."""

    files: list[SystemPromptFileInfo] = Field(
        default_factory=list, description="Context files loaded into the prompt"
    )
    total_tokens: int = Field(0, description="Total estimated tokens across all files")
    full_text: str = Field("", description="Complete assembled system prompt text")
