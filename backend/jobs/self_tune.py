#!/usr/bin/env python3
"""
Self-Tune Engine — Auto-evolve signal feeds from Swarm's own context.

Reads MEMORY.md, PROJECTS.md, and recent DailyActivity to understand
what the user cares about NOW, then adjusts config.yaml feeds accordingly.

Actions:
  1. Update user_context in config.yaml (interests, projects, tech_stack, recent_topics)
  2. Auto-disable feeds with zero signal references in 14+ days
  3. Auto-add search queries for new projects/topics trending in DailyActivity
  4. Track signal usage (which signals got referenced in sessions)

Runs daily as a system job (before first fetch), or manually:
    python self_tune.py                    # Normal run
    python self_tune.py --dry-run          # Show what would change
    python self_tune.py --report           # Show current feed health

Zero dependencies on SwarmAI backend. Stdlib + pyyaml only.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

# Paths — centralized in paths.py
from .paths import (
    CONFIG_FILE, STATE_FILE, LOG_DIR, SWARMWS,
    CONTEXT_DIR, DAILY_DIR, SIGNALS_DIR, PROJECTS_DIR, SIGNAL_DIGEST_FILE,
)

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "self-tune.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("self-tune")


# ── Context Extraction ───────────────────────────────────────────────────

def extract_projects() -> list[dict]:
    """Extract active projects from PROJECTS.md and Projects/ directory."""
    projects = []

    # Read PROJECTS.md for structured project list
    projects_md = CONTEXT_DIR / "PROJECTS.md"
    if projects_md.exists():
        content = projects_md.read_text(encoding="utf-8")
        # Extract project names from table rows: | **Name** | ...
        for match in re.finditer(r'\*\*(\w[\w\s-]+)\*\*', content):
            projects.append({"name": match.group(1).strip()})

    # Read TECH.md from each project for tech stack
    if PROJECTS_DIR.exists():
        for proj_dir in PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir() or proj_dir.name.startswith('.'):
                continue
            tech_md = proj_dir / "TECH.md"
            if tech_md.exists():
                tech_content = tech_md.read_text(encoding="utf-8")[:3000]
                # Find existing project or create new
                proj = next((p for p in projects if p["name"].lower() == proj_dir.name.lower()), None)
                if not proj:
                    proj = {"name": proj_dir.name}
                    projects.append(proj)
                proj["tech_content"] = tech_content

    return projects


def extract_recent_topics(days: int = 7) -> Counter:
    """Extract topic frequency from recent DailyActivity files.

    Returns a Counter of topic keywords mentioned across recent sessions.
    """
    topics = Counter()
    now = datetime.now(timezone.utc)

    if not DAILY_DIR.exists():
        return topics

    for days_ago in range(days):
        date_str = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        path = DAILY_DIR / f"{date_str}.md"
        if not path.exists():
            continue

        content = path.read_text(encoding="utf-8").lower()

        # Extract meaningful n-grams and technical terms
        # Focus on terms that indicate user interest, not noise
        tech_patterns = [
            # AI/ML terms
            r'\b(ai agent|llm|context window|rag|mcp|claude|bedrock|anthropic)\b',
            r'\b(fine.?tuning|embedding|vector|prompt engineering|context engineering)\b',
            r'\b(autonomous|self.?evolution|proactive|signal pipeline)\b',
            # Frameworks/tools
            r'\b(react|fastapi|tauri|pydantic|typescript|python|rust)\b',
            r'\b(playwright|sqlite|redis|docker|kubernetes)\b',
            # Concepts
            r'\b(ddd|tdd|aidlc|ci.?cd|deployment|refactor)\b',
            r'\b(security|performance|optimization|caching)\b',
        ]

        for pattern in tech_patterns:
            for match in re.finditer(pattern, content):
                term = match.group(1).strip()
                # Normalize common variations
                term = term.replace('-', ' ').replace('_', ' ')
                topics[term] += 1

    return topics


def extract_interests_from_memory() -> list[str]:
    """Extract user interests from MEMORY.md key decisions and lessons."""
    memory_path = CONTEXT_DIR / "MEMORY.md"
    if not memory_path.exists():
        return []

    content = memory_path.read_text(encoding="utf-8")[:5000]
    interests = set()

    # Look for technology/tool mentions in Key Decisions and Lessons
    tech_mentions = re.findall(
        r'\b(Claude|Bedrock|MCP|DDD|AIDLC|Tauri|FastAPI|React|Pydantic|'
        r'Claude Code|Agent SDK|SSE|SQLite|launchd|psutil|Playwright)\b',
        content, re.IGNORECASE
    )
    for t in tech_mentions:
        interests.add(t.lower())

    return sorted(interests)


def extract_tech_stack_from_projects(projects: list[dict]) -> list[str]:
    """Extract tech stack keywords from project TECH.md files."""
    stack = set()
    tech_keywords = [
        'python', 'typescript', 'react', 'fastapi', 'tauri', 'rust',
        'pydantic', 'sqlite', 'bedrock', 'claude', 'playwright',
        'tailwind', 'vite', 'pytest', 'vitest',
    ]

    for proj in projects:
        content = proj.get("tech_content", "").lower()
        for kw in tech_keywords:
            if kw in content:
                stack.add(kw)

    return sorted(stack)


# ── Signal Usage Tracking ────────────────────────────────────────────────

def track_signal_usage(days: int = 14) -> dict[str, int]:
    """Track which signal feed sources are referenced in DailyActivity.

    Scans recent DailyActivity for URLs or source names that match
    signal digest entries. Returns {feed_id: reference_count}.
    """
    usage: dict[str, int] = {}

    # Load signal digest to get URLs and sources
    digest_path = SIGNAL_DIGEST_FILE
    if not digest_path.exists():
        return usage

    try:
        digest = json.loads(digest_path.read_text(encoding="utf-8"))
        items = digest if isinstance(digest, list) else digest.get("items", [])
    except (json.JSONDecodeError, OSError):
        return usage

    # Build lookup: URL domain -> source, source name -> feed approx
    source_to_feed = {
        "simon willison": "ai-engineering",
        "lilian weng": "ai-engineering",
        "latent.space": "ai-engineering",
        "latent space": "ai-engineering",
        "langchain": "ai-engineering",
        "anthropic": "ai-engineering",
        "hacker news": "hn-ai",
        "github": "tool-releases",
    }

    # Scan DailyActivity for references
    now = datetime.now(timezone.utc)
    daily_content = ""
    for days_ago in range(days):
        date_str = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        path = DAILY_DIR / f"{date_str}.md"
        if path.exists():
            daily_content += path.read_text(encoding="utf-8").lower() + "\n"

    # Also scan signal digest markdown files for "Act Now" items that got actioned
    for digest_file in SIGNALS_DIR.iterdir() if SIGNALS_DIR.exists() else []:
        if digest_file.suffix == ".md":
            daily_content += digest_file.read_text(encoding="utf-8").lower() + "\n"

    # Count references per feed
    for source_key, feed_id in source_to_feed.items():
        count = daily_content.count(source_key)
        if count > 0:
            usage[feed_id] = usage.get(feed_id, 0) + count

    # Also check for direct URL references from signals
    for item in items:
        url = item.get("url", "")
        if url and url.lower() in daily_content:
            source = item.get("source", "").lower()
            for source_key, feed_id in source_to_feed.items():
                if source_key in source:
                    usage[feed_id] = usage.get(feed_id, 0) + 1
                    break

    return usage


# ── Tuning Actions ───────────────────────────────────────────────────────

def update_user_context(
    config: dict,
    projects: list[dict],
    topics: Counter,
    interests: list[str],
    tech_stack: list[str],
    dry_run: bool = False,
) -> list[str]:
    """Update user_context section in config.yaml.

    Returns list of changes made.
    """
    changes = []

    # Build new user_context
    project_names = [p["name"] for p in projects]
    top_topics = [term for term, count in topics.most_common(10) if count >= 2]

    current = config.get("user_context", {})
    new_context = {
        "interests": interests,
        "projects": project_names,
        "tech_stack": tech_stack,
        "recent_topics": top_topics,
    }

    # Compare and report changes
    for key in new_context:
        old = sorted(current.get(key, []))
        new = sorted(new_context[key])
        if old != new:
            added = set(new) - set(old)
            removed = set(old) - set(new)
            if added:
                changes.append(f"user_context.{key}: +{added}")
            if removed:
                changes.append(f"user_context.{key}: -{removed}")

    if changes and not dry_run:
        config["user_context"] = new_context

    return changes


def prune_unused_feeds(
    config: dict,
    usage: dict[str, int],
    min_days: int = 14,
    dry_run: bool = False,
) -> list[str]:
    """Disable feeds with zero references in the tracking window.

    Only auto-disables feeds managed_by: "self-tune" or feeds that have
    been active for at least min_days. Never touches manually managed feeds
    unless they've had 0 usage for the full window.

    Returns list of changes made.
    """
    changes = []
    feeds = config.get("feeds", [])

    for feed in feeds:
        if not feed.get("enabled", True):
            continue

        feed_id = feed.get("id", "")
        ref_count = usage.get(feed_id, 0)

        if ref_count == 0:
            # Only auto-disable if managed by self-tune
            # For manual feeds, just report (don't touch)
            managed = feed.get("managed_by", "manual")
            if managed == "self-tune":
                changes.append(f"DISABLE feed '{feed_id}': 0 references in {min_days}d (auto-managed)")
                if not dry_run:
                    feed["enabled"] = False
            else:
                changes.append(f"NOTE: feed '{feed_id}' has 0 references in {min_days}d (manual — not auto-disabled)")

    return changes


def suggest_new_feeds(
    config: dict,
    topics: Counter,
    dry_run: bool = False,
) -> list[str]:
    """Suggest or add new search queries based on trending topics.

    Only adds to existing web-search feeds or creates a new auto-managed
    feed. Never exceeds max_active_feeds from defaults.

    Returns list of changes made.
    """
    changes = []
    feeds = config.get("feeds", [])
    defaults = config.get("defaults", {})
    max_feeds = defaults.get("max_active_feeds", 15)

    active_count = sum(1 for f in feeds if f.get("enabled", True))

    # Find topics trending strongly (5+ mentions in 7 days) that aren't
    # already covered by existing feed keywords
    existing_keywords = set()
    for feed in feeds:
        feed_config = feed.get("config", {})
        for q in feed_config.get("queries", []):
            existing_keywords.update(q.lower().split())
        for kw in feed_config.get("keywords", []):
            existing_keywords.add(kw.lower())

    # Filter out project-internal acronyms and very short terms that would
    # generate noise on HN/web search (e.g., "ddd", "tdd" are methodology
    # acronyms, not useful HN search terms)
    skip_terms = {
        "ddd", "tdd", "aidlc", "python", "react", "typescript", "rust",
        "sqlite", "fastapi", "pydantic", "tauri", "vite", "tailwind",
        "pytest", "vitest",  # Generic tech — already well-covered
    }
    trending = [
        (term, count)
        for term, count in topics.most_common(20)
        if count >= 5
        and term.lower() not in existing_keywords
        and term.lower() not in skip_terms
        and len(term) > 3  # Skip short acronyms
    ]

    if not trending:
        return changes

    # Add trending terms to existing HN feed keywords (safest, free)
    hn_feed = next((f for f in feeds if f.get("id") == "hn-ai"), None)
    if hn_feed:
        current_kw = hn_feed.get("config", {}).get("keywords", [])
        for term, count in trending[:3]:  # Max 3 new keywords per tune
            if term not in [k.lower() for k in current_kw]:
                changes.append(f"ADD keyword '{term}' to hn-ai ({count} mentions in 7d)")
                if not dry_run:
                    current_kw.append(term)

    # If we have room, suggest a new auto-managed feed for very strong trends
    if active_count < max_feeds:
        for term, count in trending:
            if count >= 10:  # Very strong signal
                changes.append(
                    f"SUGGEST: Create auto-managed feed for '{term}' ({count} mentions). "
                    f"Requires Tavily API key for web-search."
                )

    return changes


# ── Main ─────────────────────────────────────────────────────────────────

def run_self_tune(dry_run: bool = False) -> dict:
    """Execute self-tune cycle. Returns summary dict."""
    logger.info("Self-tune starting")

    # Load config
    if not CONFIG_FILE.exists():
        logger.error(f"Config not found: {CONFIG_FILE}")
        return {"error": "config_not_found"}

    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f) or {}

    # Extract context
    projects = extract_projects()
    topics = extract_recent_topics(days=7)
    interests = extract_interests_from_memory()
    tech_stack = extract_tech_stack_from_projects(projects)
    usage = track_signal_usage(days=14)

    logger.info(
        f"Context: {len(projects)} projects, {len(topics)} topic types, "
        f"{len(interests)} interests, {len(tech_stack)} tech stack items"
    )
    logger.info(f"Signal usage (14d): {usage}")

    all_changes = []

    # 1. Update user_context
    changes = update_user_context(config, projects, topics, interests, tech_stack, dry_run)
    all_changes.extend(changes)

    # 2. Prune unused feeds
    changes = prune_unused_feeds(config, usage, min_days=14, dry_run=dry_run)
    all_changes.extend(changes)

    # 3. Suggest new feeds
    changes = suggest_new_feeds(config, topics, dry_run=dry_run)
    all_changes.extend(changes)

    # Write config if changes were made
    if all_changes and not dry_run:
        header = (
            "# Swarm Signal Pipeline — Feed Configuration\n"
            "# Auto-tuned by self_tune.py based on MEMORY.md + PROJECTS.md + DailyActivity.\n"
            "# Manual edits are preserved; self-tune only modifies user_context and\n"
            "# auto-managed feeds.\n\n"
        )
        with open(CONFIG_FILE, "w") as f:
            f.write(header)
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        logger.info(f"Config updated with {len(all_changes)} changes")
    elif all_changes:
        logger.info(f"[DRY RUN] Would make {len(all_changes)} changes:")
        for c in all_changes:
            logger.info(f"  {c}")
    else:
        logger.info("No changes needed")

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "projects": [p["name"] for p in projects],
        "top_topics": [f"{t}({c})" for t, c in topics.most_common(10)],
        "signal_usage": usage,
        "changes": all_changes,
        "dry_run": dry_run,
    }

    return summary


def show_report() -> None:
    """Show current feed health and context state."""
    # Load config
    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f) or {}

    # Load state for run counts
    state = {}
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)

    usage = track_signal_usage(days=14)
    topics = extract_recent_topics(days=7)

    print(f"\n{'='*60}")
    print("Self-Tune Report")
    print(f"{'='*60}\n")

    # Feed health
    print("Feed Health (14-day window):")
    for feed in config.get("feeds", []):
        fid = feed.get("id", "?")
        enabled = "ON " if feed.get("enabled", True) else "OFF"
        managed = feed.get("managed_by", "manual")
        refs = usage.get(fid, 0)
        health = "healthy" if refs > 0 else "unused"
        print(f"  [{enabled}] {fid:<25} refs={refs:<3} managed={managed:<10} {health}")

    # User context
    ctx = config.get("user_context", {})
    print(f"\nUser Context:")
    for key in ("interests", "projects", "tech_stack", "recent_topics"):
        vals = ctx.get(key, [])
        print(f"  {key}: {vals if vals else '(empty)'}")

    # Trending topics
    print(f"\nTrending Topics (7d):")
    for term, count in topics.most_common(15):
        bar = "#" * min(count, 20)
        print(f"  {term:<25} {count:>3} {bar}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Swarm Self-Tune Engine")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--report", action="store_true", help="Show feed health report")
    args = parser.parse_args()

    if args.report:
        show_report()
    else:
        result = run_self_tune(dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
