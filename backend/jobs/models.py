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
    DDD_WEEKLY_REPORT = "ddd_weekly_report"
    DDD_SELF_AUDIT = "ddd_self_audit"
    SWARMAI_MONTHLY_REPORT = "swarmai_monthly_report"
    SELF_TUNE = "self_tune"
    SESSION_HEALTH_PROBE = "session_health_probe"
    EVAL_SCHEDULED = "eval_scheduled"
    SESSION_QUALITY = "session_quality"
    LIBRARY_FRESHNESS = "library_freshness"
    LIBRARY_HEALTH = "library_health"


class FeedType(str, Enum):
    WEB_SEARCH = "web-search"
    RSS = "rss"
    GITHUB_RELEASES = "github-releases"
    HACKER_NEWS = "hacker-news"
    TRENDING = "trending"
    GITHUB_TRENDING = "github-trending"
    GITHUB_COMMUNITY = "github-community"
    GITHUB_PEOPLE = "github-people"
    WEIBO_TRENDING = "weibo-trending"
    EASTMONEY_MARKET = "eastmoney-market"


# Per-FeedType editable-member config key — the SINGLE SOURCE for "which config
# key holds this feed's editable string members" (consumed by community_data.parse_sources
# + the /community/feeds/{id}/members endpoints). A feed's members are the individual
# subscriptions inside it: an RSS feed's urls, a Hacker-News feed's keywords, etc.
# `None` = this feed type has no user-editable string-list members.
# ⚠️ EVERY FeedType MUST have an entry (test_member_key_covers_every_feed_type enforces
# it) — a missing entry is the silent parallel-enumeration drift Gate-1 flagged.
MEMBER_KEY: dict["FeedType", str | None] = {
    FeedType.RSS: "urls",                     # pundit/lab blog feeds (Sam Altman, Karpathy, OpenAI…)
    FeedType.HACKER_NEWS: "keywords",         # topic keywords to watch on HN
    FeedType.GITHUB_RELEASES: "repos",        # watched repos (owner/name flat strings)
    # github-community reads config.repos (github_community.py:83, SOURCE_REPOS = flat
    # owner/name strings) — same shape as github-releases, so it IS string-editable.
    # (It's the feed type behind the overlay's own Engagement tab — was wrongly None.)
    FeedType.GITHUB_COMMUNITY: "repos",
    # github-people watches individual GitHub users (R2 名人层). config.logins is a
    # flat list of login strings — the overlay member editor edits them like repos.
    # Read by monitor.load_people_from_feed; scanned via scan_people (gh search issues
    # --author). NOT wired into the signal pipeline (it's the community engine's store).
    FeedType.GITHUB_PEOPLE: "logins",
    FeedType.WEIBO_TRENDING: "keywords",      # weibo topic keywords
    FeedType.EASTMONEY_MARKET: "concept_keywords",  # market concept keywords
    # web-search's fetch adapter is UNIMPLEMENTED (web_search.py returns [] on every
    # path; `queries` is a commented-out TODO). Exposing member editing would be an
    # affordance with no real backing (violates the overlay's honesty rule) → None until
    # the Tavily adapter is actually implemented.
    FeedType.WEB_SEARCH: None,
    # trending.platforms is a list of STRUCTURED objects ({id, name}), NOT flat strings
    # (the adapter does platform.get("id")). NOT user-editable via the string-member path
    # — appending a bare string would corrupt the list and crash the trending adapter.
    FeedType.TRENDING: None,
    FeedType.GITHUB_TRENDING: None,           # no member list (spoken_language/since/top_n scalars only)
}


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
    # "user" = created/edited via the Community overlay (Phase-2); protected from
    # self_tune auto-disable exactly like "manual" (prune only disables "self-tune").
    managed_by: Literal["manual", "self-tune", "user"] = "manual"
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
    last_error: str | None = None      # error/summary of most recent failure (🔔 diagnostics); cleared on success
    consecutive_failures: int = 0
    total_runs: int = 0
    total_tokens: int = 0


class SchedulerState(BaseModel):
    """Full scheduler runtime state, persisted to state.json."""
    jobs: dict[str, JobState] = {}
    raw_signals: list[RawSignal] = []  # buffer between fetch and digest
    dedup_cache: list[str] = []        # recent URLs for dedup (7-day window)
    pending_events: list[dict[str, Any]] = []  # event queue for on:<event> jobs
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
