"""
Swarm Job System — Data Models

Core Pydantic models for the standalone job scheduler.
Job, Feed, RawSignal, SignalDigest, JobResult, JobSafety.

Zero dependency on SwarmAI backend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────

class JobType(str, Enum):
    SIGNAL_FETCH = "signal_fetch"
    SIGNAL_DIGEST = "signal_digest"
    AGENT_TASK = "agent_task"
    SCRIPT = "script"
    NOTIFY = "notify"
    MAINTENANCE = "maintenance"
    DDD_REFRESH = "ddd_refresh"
    MEMORY_HEALTH = "memory_health"
    SKILL_PROPOSER = "skill_proposer"
    TODO_RESOLUTION = "todo_resolution"


class FeedType(str, Enum):
    WEB_SEARCH = "web-search"
    RSS = "rss"
    GITHUB_RELEASES = "github-releases"
    HACKER_NEWS = "hacker-news"
    TRENDING = "trending"
    GITHUB_TRENDING = "github-trending"


class TierType(str, Enum):
    """Signal source authority tier — controls weighting and auto-disable behavior."""
    FRONTIER = "frontier"       # Official labs (OpenAI, Anthropic, Google, etc.)
    LEADERS = "leaders"         # AI leaders & thinkers (Sam Altman, Karpathy, etc.)
    RESEARCH = "research"       # Academic/research (arXiv, research blogs)
    ENGINEERING = "engineering"  # Engineering blogs, frameworks (default)
    OPINION = "opinion"         # Thought leaders, commentary
    AGGREGATE = "aggregate"     # Newsletters, aggregators (second-hand signal)


# Tier weight multipliers for relevance scoring
TIER_WEIGHTS: dict[str, float] = {
    TierType.FRONTIER: 2.0,
    TierType.LEADERS: 1.5,
    TierType.RESEARCH: 1.5,
    TierType.ENGINEERING: 1.0,
    TierType.OPINION: 1.0,
    TierType.AGGREGATE: 0.8,
}

# Tier-specific auto-disable thresholds (days of zero usage before auto-disable)
TIER_DISABLE_THRESHOLDS: dict[str, int | None] = {
    TierType.FRONTIER: None,   # Never auto-disable
    TierType.LEADERS: None,    # Never auto-disable
    TierType.RESEARCH: 30,     # 30 days
    TierType.ENGINEERING: 14,  # 14 days (default behavior)
    TierType.OPINION: 14,
    TierType.AGGREGATE: 14,
}


# ── Signal Models ─────────────────────────────────────────────────────

class RawSignal(BaseModel):
    """A single raw signal fetched from a feed."""
    feed_id: str
    title: str
    url: str
    summary: str = ""
    published: datetime | None = None
    source: str = ""         # e.g. "Simon Willison's Weblog"
    tags: list[str] = []
    score: float = 0.0       # relevance score (0-1), set by digester
    tier: str = "engineering"  # inherited from feed's tier during fetch


class Feed(BaseModel):
    """A signal feed source definition."""
    id: str
    name: str
    type: FeedType
    config: dict[str, Any] = {}
    tags: list[str] = []
    enabled: bool = True
    managed_by: Literal["manual", "self-tune"] = "manual"
    tier: TierType = TierType.ENGINEERING


# ── Job Models ────────────────────────────────────────────────────────

class JobSafety(BaseModel):
    """Per-job permission scope for safe execution.

    Budget control uses --max-budget-usd (Claude CLI flag).
    max_budget_usd is the per-run spend cap in dollars.
    """
    max_budget_usd: float = 5.00
    timeout_seconds: int = 300
    allowed_tools: list[str] = []
    allow_write: bool = False
    allow_send: bool = False
    allow_network: bool = True


class Job(BaseModel):
    """A scheduled unit of work (system or user)."""
    id: str
    name: str
    type: JobType
    schedule: str                  # cron expression OR "after:<job-id>"
    enabled: bool = True
    category: Literal["system", "user"] = "system"
    created: datetime | None = None
    config: dict[str, Any] = {}
    safety: JobSafety = Field(default_factory=JobSafety)


class JobResult(BaseModel):
    """Outcome of a single job execution."""
    job_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["success", "partial", "failed", "skipped", "auth_failed"]
    summary: str = ""
    output_path: str | None = None
    tokens_used: int = 0
    duration_seconds: float = 0.0
    error: str | None = None
    signals_count: int = 0         # for signal jobs


# ── State Models ──────────────────────────────────────────────────────

class JobState(BaseModel):
    """Runtime state for a single job."""
    last_run: datetime | None = None
    last_status: str = "never"
    consecutive_failures: int = 0
    total_runs: int = 0
    total_tokens: int = 0


class SchedulerState(BaseModel):
    """Full scheduler runtime state, persisted to state.json."""
    jobs: dict[str, JobState] = {}
    raw_signals: list[RawSignal] = []  # buffer between fetch and digest
    dedup_cache: list[str] = []        # recent URLs for dedup (7-day window)
    last_scheduler_run: datetime | None = None
    monthly_tokens_used: int = 0       # legacy, kept for backwards compat
    monthly_spend_usd: float = 0.0     # cumulative monthly spend in dollars
    monthly_reset_date: str = ""       # YYYY-MM for token/spend reset tracking


# ── Config Models ─────────────────────────────────────────────────────

class SchedulerDefaults(BaseModel):
    """Global scheduler defaults from config.yaml."""
    max_age_hours: int = 48
    dedup_window_days: int = 7
    relevance_threshold: float = 0.3
    max_active_feeds: int = 15
    max_daily_agent_tasks: int = 20
    max_monthly_spend_usd: float = 10.0


class UserContext(BaseModel):
    """Auto-populated user context for relevance scoring."""
    interests: list[str] = []
    projects: list[str] = []
    tech_stack: list[str] = []
    recent_topics: list[str] = []


class SchedulerConfig(BaseModel):
    """Top-level config from config.yaml."""
    version: int = 1
    defaults: SchedulerDefaults = Field(default_factory=SchedulerDefaults)
    user_context: UserContext = Field(default_factory=UserContext)
    feeds: list[Feed] = []
