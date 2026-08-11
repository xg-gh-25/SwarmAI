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
            "Provenance tag: Project, Library, Notes, Reports, Meetings, "
            "Archives, DailyActivity, Memory, or External MCP"
        ),
    )


class TSCCLiveState(BaseModel):
    """Live cognitive state for a single thread.

    After TSCC simplification (Req 6.3-6.4), only ``context`` is actively
    populated.  The remaining fields are retained for API compatibility
    but are always empty/default.
    """

    context: TSCCContext = Field(..., description="Scope and thread metadata")
    active_agents: list[str] = Field(
        default_factory=list, description="Always empty — retained for API compat"
    )
    active_capabilities: TSCCActiveCapabilities = Field(
        default_factory=TSCCActiveCapabilities,
        description="Always empty — retained for API compat",
    )
    what_ai_doing: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Always empty — retained for API compat",
    )
    active_sources: list[TSCCSource] = Field(
        default_factory=list, description="Always empty — retained for API compat"
    )
    key_summary: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Always empty — retained for API compat",
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
    # The DYNAMIC per-model budget from ContextDirectoryLoader.compute_token_budget
    # (100K at >=500K window, 50K at >=200K, 30K at >=64K, instance default below).
    # Declared here for the same reason as ``degraded``: without a field, Pydantic
    # drops it and the panel is left guessing — it hardcoded the 100K tier and so
    # under-reported usage by 2-3x on every smaller model (review run_abab234c,
    # MED #5). 0 means "not reported"; consumers must not substitute a tier.
    effective_token_budget: int = Field(
        0,
        description=(
            "Dynamic token budget actually applied for this model's context "
            "window; 0 when the build did not report one"
        ),
    )
    # The fail-loud degradation reason from prompt assembly (prompt_builder mirrors
    # ``_context_degraded`` into the metadata dict). This field MUST exist here or
    # Pydantic's default extra='ignore' silently drops it at the response boundary,
    # which is exactly how the signal stayed unreachable (review run_abab234c,
    # HIGH #2). Empty string = prompt assembled completely.
    degraded: str = Field(
        "",
        description=(
            "Fail-loud degradation reason (e.g. 'missing_core_sections: SOUL'); "
            "empty when the prompt assembled completely"
        ),
    )


class RecallHit(BaseModel):
    """One structured recalled hit — the REAL hit that fed the injected block this
    turn (extracted from the BucketedRecall, not a re-run). Powers the mockup's
    per-source recall cards with their BM25 scores."""

    domain: str = Field("", description="Recall domain: context_files | library | ddd | session | codeintel")
    source: str = Field("", description="Source file / section / heading the hit came from")
    score: float = Field(0.0, description="BM25/hybrid relevance score, normalized [0,1] (only meaningful when has_score)")
    has_score: bool = Field(False, description="True when score is a real normalized [0,1] value; False for domains with no comparable score (context_files/session/codeintel)")
    method: str = Field("", description="How it matched: keyword | hybrid | fts | graph")
    text: str = Field("", description="Hit excerpt (truncated)")


class RecallSnapshot(BaseModel):
    """Recalled-knowledge snapshot for a session, returned by the recall endpoint.

    Populated as a fire-and-forget side effect of the ONE recall leg that already
    runs on the first user message (``_maybe_inject_recall`` in session_router).
    The panel reads this snapshot only — it never triggers recall itself, so this
    endpoint is fully decoupled from the chat send path.

    ``hits`` are the STRUCTURED per-source hits (domain/source/score) extracted from
    the SAME BucketedRecall that produced the injected block — the real session
    state, not a re-run. ``body`` is a fallback rendered string used ONLY when
    structured hits are unavailable (legacy fallback path).

    ``ran`` means the recall LEG EXECUTED, which is deliberately not the same as
    "recall found something": a keyword miss stashes ``ran=True`` with zero hits.
    That distinction matters — "ran and matched nothing" is a tracked degradation
    signal, while ``ran=False`` means recall never ran at all (channel session,
    turn 2+, or the disaster-timeout / exception paths). Collapsing the two would
    report a systematic keyword miss as a feature that never fired.
    """

    ran: bool = Field(
        False,
        description=(
            "Whether the recall leg executed this session (True even when it "
            "matched nothing — check hits for that)"
        ),
    )
    hits: list[RecallHit] = Field(
        default_factory=list, description="Structured per-source hits (the real recalled hits)"
    )
    body: str = Field("", description="Fallback rendered markdown (only when structured hits unavailable)")
    tokens: int = Field(0, description="Estimated tokens of the recalled block (estimate_tokens)")
    latency_ms: float = Field(0.0, description="Recall-leg wall-clock in ms")
    keywords: list[str] = Field(
        default_factory=list, description="Query keywords that drove the recall"
    )


class SecurityFinding(BaseModel):
    """A single detector's verdict from the system-prompt security scan.

    ``detail`` is ALWAYS masked — the raw secret/PII value is never echoed
    back into the response (first 2 chars + ``***``).
    """

    detector: str = Field(..., description="Detector name, e.g. 'credentials' or 'pii_email'")
    severity: str = Field(
        ..., description='Severity: "critical", "high", "medium", or "info"'
    )
    status: str = Field(..., description='Detector result: "pass" or "warn"')
    count: int = Field(0, description="Number of matches found")
    detail: str = Field(
        "",
        description="Human-readable detail — MUST be masked, never the raw secret",
    )


class SecurityScanResult(BaseModel):
    """Structured verdict for the read-only security-scan panel."""

    grade: str = Field("n/a", description='Overall grade, e.g. "A", "A-", "B", "C", or "n/a"')
    findings: list[SecurityFinding] = Field(
        default_factory=list, description="Per-detector findings"
    )
    critical: int = Field(0, description="Count of critical-severity findings")
    high: int = Field(0, description="Count of high-severity findings")
    medium: int = Field(0, description="Count of medium-severity findings")
    info: int = Field(0, description="Count of info-severity findings")
    scanned_files: int = Field(0, description="Number of context files scanned")
