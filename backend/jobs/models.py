"""
Swarm Job System — Data Models

Core Pydantic models for the standalone job scheduler.
Job, Feed, RawSignal, JobResult, JobSafety, SchedulerState.

Zero dependency on SwarmAI backend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────

class JobType(str, Enum):
    AGENT_TASK = "agent_task"
    SCRIPT = "script"
    NOTIFY = "notify"
    MAINTENANCE = "maintenance"
    DDD_REFRESH = "ddd_refresh"
    MEMORY_HEALTH = "memory_health"
    SKILL_PROPOSER = "skill_proposer"
    BRAIN_MISTAKE_AUDIT = "brain_mistake_audit"
    SESSION_HEALTH_PROBE = "session_health_probe"
    EVAL_SCHEDULED = "eval_scheduled"
    SESSION_QUALITY = "session_quality"
    CDF_HEALTH = "cdf_health"
    LIBRARY_FRESHNESS = "library_freshness"
    LIBRARY_HEALTH = "library_health"
    WORKTREE_SWEEP = "worktree_sweep"
    LOG_PATROL = "log_patrol"
    RECORDS_SWEEP = "records_sweep"
    BRAIN_SETUP_RESUME = "brain_setup_resume"


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
# key holds this feed's editable string members" (consumed by core.inbox.feeds.config.parse_sources
# + the /api/inbox/feeds/{id}/members endpoints). A feed's members are the individual
# subscriptions inside it: an RSS feed's urls, a Hacker-News feed's keywords, etc.
# `None` = this feed type has no user-editable string-list members.
# ⚠️ EVERY FeedType MUST have an entry (test_member_key_covers_every_feed_type enforces
# it) — a missing entry is the silent parallel-enumeration drift Gate-1 flagged.
MEMBER_KEY: dict["FeedType", str | None] = {
    FeedType.RSS: "urls",                     # pundit/lab blog feeds (Sam Altman, Karpathy, OpenAI…)
    FeedType.HACKER_NEWS: "keywords",         # topic keywords to watch on HN
    FeedType.GITHUB_RELEASES: "repos",        # watched repos (owner/name flat strings)
    # github-community: config.repos is a flat list of owner/name strings (the GitHub
    # community engine's subscription list) — same shape as github-releases, so it IS
    # string-editable.
    FeedType.GITHUB_COMMUNITY: "repos",
    # github-people watches individual GitHub users (R2 名人层). config.logins is a
    # flat list of login strings — the overlay member editor edits them like repos.
    # Read by monitor.load_people_from_feed; scanned via scan_people (gh search issues
    # --author). It has no feed fetcher (it's the community engine's store).
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
    """Feed source authority tier, set per feed in config.yaml and shown in the Inbox feed settings."""
    FRONTIER = "frontier"       # Official labs (OpenAI, Anthropic, Google, etc.)
    LEADERS = "leaders"         # AI leaders & thinkers (Sam Altman, Karpathy, etc.)
    RESEARCH = "research"       # Academic/research (arXiv, research blogs)
    ENGINEERING = "engineering"  # Engineering blogs, frameworks (default)
    OPINION = "opinion"         # Thought leaders, commentary
    AGGREGATE = "aggregate"     # Newsletters, aggregators (second-hand signal)


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
    score: float = 0.0       # the fetcher's own 0-1 ranking hint (stars, heat), when it has one
    tier: str = "engineering"  # inherited from feed's tier during fetch


class Feed(BaseModel):
    """A signal feed source definition."""
    id: str
    name: str
    type: FeedType
    config: dict[str, Any] = {}
    tags: list[str] = []
    enabled: bool = True
    # "user" = created/edited in the Inbox feed settings; "self-tune" is a value an
    # older config.yaml may still carry (the feed auto-tuner is gone) and is kept as is.
    managed_by: Literal["manual", "self-tune", "user"] = "manual"
    tier: TierType = TierType.ENGINEERING


# ── Job Models ────────────────────────────────────────────────────────

class JobSafety(BaseModel):
    """Per-job permission scope for safe execution. There is no spend cap: what a job
    spends is recorded in the usage ledger; ``timeout_seconds`` bounds a hang. (A
    ``max_budget_usd`` left in an older jobs file is ignored.)

    ``allowed_tools``: the built-in tools the job can call (passed as ``--tools``; empty =
    the CLI's default set) plus any ``mcp__`` tools it may use. A file-writing tool
    (Write, Edit, MultiEdit, NotebookEdit) is never given, declared or not: a job writes
    through its result or through its sandboxed Bash. ``allow_network``: whether
    the job's sandboxed Bash may open connections. Every job's Bash runs in the CLI
    sandbox (``jobs.executor._job_settings``). ``allow_write`` / ``allow_send`` were never
    read and are gone; a jobs file that still has them loads with a warning."""
    timeout_seconds: int = 300
    allowed_tools: list[str] = []
    allow_network: bool = True
    # retryable: opt-in bounded in-tick retry for a transient agent_task failure
    # (timeout / CLI-exit-nonzero). DEFAULT False for idempotency safety — an
    # agent_task can produce side effects (post a GitHub comment, send a Slack
    # message) before it times out, so retrying the WHOLE cycle can double-act
    # (e.g. publish.py has anti-spam but no per-issue dedup). Only set
    # retryable=true on a job whose cycle is provably idempotent. auth_failed is
    # NEVER retried via this path (it has its own next-tick fast-retry). run_d383e9a0.
    retryable: bool = False


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


# ── State Models ──────────────────────────────────────────────────────

class JobState(BaseModel):
    """Runtime state for a single job."""
    last_run: datetime | None = None
    last_status: str = "never"
    last_error: str | None = None      # error/summary of most recent failure (🔔 diagnostics); cleared on success
    consecutive_failures: int = 0
    # Transient auth failures don't count toward consecutive_failures (they self-heal
    # + auto-retry), but a PERSISTENT auth failure (revoked IdC, wrong region) must
    # still reach the user instead of retrying invisibly forever. Tracked separately
    # so it can escalate to the BLOCKING Need-You queue past a threshold.
    consecutive_auth_failures: int = 0
    total_runs: int = 0
    total_tokens: int = 0


class SchedulerState(BaseModel):
    """Full scheduler runtime state, persisted to state.json."""
    jobs: dict[str, JobState] = {}
    pending_events: list[dict[str, Any]] = []  # event queue for on:<event> jobs
    last_scheduler_run: datetime | None = None
    monthly_tokens_used: int = 0       # legacy, kept for backwards compat
    monthly_spend_usd: float = 0.0     # legacy, no longer written: spend is the usage ledger's
    monthly_reset_date: str = ""       # YYYY-MM for token/spend reset tracking


# ── Config Models ─────────────────────────────────────────────────────

class SchedulerDefaults(BaseModel):
    """Global scheduler defaults from config.yaml."""
    max_age_hours: int = 48
    dedup_window_days: int = 7
    relevance_threshold: float = 0.3
    max_active_feeds: int = 15
    max_daily_agent_tasks: int = 20
