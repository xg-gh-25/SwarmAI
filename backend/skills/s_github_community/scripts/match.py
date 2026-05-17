"""GitHub Community Engine — MATCH stage.

Scores signals against Topic Matrix + DDD, ranks opportunities.
Enforces admission gates for Source Matrix and Topic Matrix.
"""

from dataclasses import dataclass
from typing import Optional


# --- Constants ---

AUTO_PUBLISH_THRESHOLD = 8  # Confidence >= 8 → auto-publish
SCORE_THRESHOLD = 30  # Score >= 30 → proceed to DRAFT
MAX_COMMENTS_PER_REPO_PER_WEEK = 3
FIRST_RESPONDER_BONUS = 5
REPLY_TO_US_BONUS = 50  # Replies ALWAYS pass threshold — highest priority
MAINTAINER_ISSUE_BONUS = 3
STALENESS_PENALTY_PER_DAY = 2
MIN_STARS_FOR_ADMISSION = 1000
MIN_ACTIVE_REPOS_FOR_TOPIC = 2


# --- Data Models ---


@dataclass
class RepoEntry:
    """A repository in the Source Matrix."""

    name: str
    stars: int
    has_production_experience: bool
    active_last_30d: bool
    teaches_us: bool


@dataclass
class TopicEntry:
    """A topic in the Topic Matrix."""

    id: str
    name: str
    has_evidence: bool
    thesis_link: Optional[str]
    active_repos_count: int


@dataclass
class Signal:
    """A raw signal from MONITOR stage — an opportunity to engage."""

    repo: str
    issue_number: int
    title: str
    topic_relevance: int  # 0-5
    expertise_depth: int  # 0-5
    audience_reach: int  # 0-5
    existing_comments: int
    is_reply_to_us: bool
    is_maintainer_issue: bool
    days_old: int
    repo_comments_this_week: int


# --- Admission Gates ---


def check_repo_admission(repo: RepoEntry) -> tuple[bool, list[str]]:
    """Check if a repo passes the Source Matrix admission gate.

    ALL 4 conditions must pass:
    1. Production experience in their problem domain
    2. Stars >= 1K
    3. Active community (last 30 days)
    4. Teaches us something or challenges our thesis

    Returns (passed: bool, failure_reasons: list[str])
    """
    failures = []

    if not repo.has_production_experience:
        failures.append("production_experience: no first-hand experience in this domain")

    if repo.stars < MIN_STARS_FOR_ADMISSION:
        failures.append(f"stars: {repo.stars} < {MIN_STARS_FOR_ADMISSION} minimum")

    if not repo.active_last_30d:
        failures.append("active: no community activity in last 30 days")

    if not repo.teaches_us:
        failures.append("teaches: nothing to learn from this repo / doesn't challenge our thesis")

    return (len(failures) == 0, failures)


def check_topic_admission(topic: TopicEntry) -> tuple[bool, list[str]]:
    """Check if a topic passes the Topic Matrix admission gate.

    ALL 3 conditions must pass:
    1. Defensible position backed by evidence
    2. Connects to at least 1 Thesis (T1-T6)
    3. At least 2 Source Matrix repos have active discussions

    Returns (passed: bool, failure_reasons: list[str])
    """
    failures = []

    if not topic.has_evidence:
        failures.append("evidence: no defensible position backed by code/data/production experience")

    if not topic.thesis_link:
        failures.append("thesis: does not connect to any Thesis (T1-T6)")

    if topic.active_repos_count < MIN_ACTIVE_REPOS_FOR_TOPIC:
        failures.append(
            f"repos: only {topic.active_repos_count} active repos "
            f"(need >= {MIN_ACTIVE_REPOS_FOR_TOPIC})"
        )

    return (len(failures) == 0, failures)


# --- Scoring ---


def score_opportunity(signal: Signal) -> float:
    """Score a signal opportunity for engagement priority.

    Formula:
      base = topic_relevance × expertise_depth × audience_reach
      + bonuses (first responder, reply to us, maintainer issue)
      - penalties (staleness, anti-spam)

    Returns: float score. >= SCORE_THRESHOLD means proceed to DRAFT.
    """
    # Anti-spam gate — kills score entirely
    if signal.repo_comments_this_week >= MAX_COMMENTS_PER_REPO_PER_WEEK:
        return -999.0

    # Base score (multiplicative — all three must be present for high score)
    base = signal.topic_relevance * signal.expertise_depth * signal.audience_reach

    # Bonuses (additive)
    bonus = 0.0
    if signal.existing_comments == 0:
        bonus += FIRST_RESPONDER_BONUS
    if signal.is_reply_to_us:
        bonus += REPLY_TO_US_BONUS
    if signal.is_maintainer_issue:
        bonus += MAINTAINER_ISSUE_BONUS

    # Penalties (subtractive)
    penalty = signal.days_old * STALENESS_PENALTY_PER_DAY

    return base + bonus - penalty
