"""
GitHub Community Adapter

Scans Source Matrix repos for engagement opportunities matching our Topic Matrix.
Uses `gh` CLI for GitHub API access (authenticated via existing SSO).

Returns signals as RawSignal for the signal pipeline digest.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta

from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)

# Source Matrix — repos to scan (Tier 1 + Tier 2)
SOURCE_REPOS = [
    "NousResearch/hermes-agent",
    "anthropics/skills",
    "MemPalace/mempalace",
    "garrytan/gstack",
    "anthropics/claude-code",
    "bytedance/deer-flow",
    "crewAIInc/crewAI",
    "mattpocock/skills",
    "volcengine/OpenViking",
    "nexu-io/open-design",
    "awslabs/aidlc-workflows",
    "aws-samples/sample-ai-plc",
    "aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore",
]

# Topic keywords for signal matching (our supply + community demand)
TOPIC_KEYWORDS = {
    "memory": ["memory", "context", "persist", "recall", "forget", "amnesia"],
    "multi-agent": ["multi-agent", "coordination", "handoff", "orchestrat", "consensus"],
    "pipeline": ["pipeline", "review", "TDD", "delivery", "autonomous", "black box"],
    "skills": ["skill", "hierarchy", "DRY", "governance", "CLAUDE.md", "instructions"],
    "production-ops": ["production", "monitoring", "observability", "cost management"],
    "autonomy": ["autonomy", "pause", "resume", "approve", "guardrail", "human-in-loop"],
}


def _run_gh(args: list[str], timeout: int = 20) -> str | None:
    """Run gh CLI command, return stdout or None on failure."""
    try:
        result = subprocess.run(
            ["gh"] + args, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _parse_created(created_at: str | None) -> datetime | None:
    """Parse a GitHub ISO8601 timestamp (e.g. '2024-01-15T10:30:00Z') to a tz-aware
    datetime, fail-safe to None. Mirrors github_releases.py's parse — GitHub uses
    ISO8601 (Z suffix), NOT RFC2822, so fromisoformat after a Z→+00:00 swap is the
    correct parser. A missing/malformed value returns None (never raises) so a bad
    timestamp cannot crash the scan. (run_36d8ba1c: fixes the published_at→published
    kwarg drop that left every github-community signal timestamp-less.)"""
    if not created_at:
        return None
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        return None


def _match_topics(text: str) -> list[str]:
    """Match text against topic keywords."""
    text_lower = text.lower()
    return [
        topic for topic, keywords in TOPIC_KEYWORDS.items()
        if any(kw in text_lower for kw in keywords)
    ]


def fetch_github_community(feed: Feed, max_age_hours: int = 24) -> list[RawSignal]:
    """
    Fetch community engagement opportunities from Source Matrix repos.

    Args:
        feed: Feed config. config keys:
            - repos: list of repos to scan (overrides SOURCE_REPOS if set)
            - since_hours: lookback window (default 24)
            - top_n: max signals to return (default 15)
        max_age_hours: lookback window override

    Returns:
        List of RawSignal for the signal pipeline
    """
    repos = feed.config.get("repos", SOURCE_REPOS)
    since_hours = feed.config.get("since_hours", max_age_hours)
    top_n = feed.config.get("top_n", 15)

    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    signals: list[RawSignal] = []

    for repo in repos:
        # Fetch recent issues
        jq_filter = f'[.[] | select(.updated_at > "{since}")][:5]'
        output = _run_gh(["api", f"repos/{repo}/issues", "-q", jq_filter])
        if not output:
            continue

        try:
            issues = json.loads(output)
        except json.JSONDecodeError:
            continue

        for issue in issues:
            title = issue.get("title", "")
            topics = _match_topics(title)
            if not topics:
                continue

            signals.append(RawSignal(
                feed_id=feed.id,
                title=f"[{repo}] {title}",
                url=issue.get("html_url", f"https://github.com/{repo}/issues/{issue.get('number', '')}"),
                summary=f"#{issue.get('number')} | {issue.get('comments', 0)} comments | Topics: {', '.join(topics)}",
                source=f"github:{repo}",
                tags=["github-community"] + topics,
                score=min(issue.get("comments", 0) * 2 + (5 if issue.get("comments", 0) == 0 else 0), 50),
                published=_parse_created(issue.get("created_at")),
            ))

    # Sort by score descending, limit
    signals.sort(key=lambda s: s.score, reverse=True)
    signals = signals[:top_n]

    logger.info(
        "github-community: scanned %d repos, found %d signals",
        len(repos), len(signals),
    )
    return signals
