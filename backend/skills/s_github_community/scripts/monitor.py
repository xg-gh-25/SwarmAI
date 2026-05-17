"""GitHub Community Engine — MONITOR stage.

Scans Source Matrix repos for new signals (issues, discussions, replies).
Outputs signals.json for the MATCH stage to score.

Usage:
  python -m skills.s_github_community.scripts.monitor [--dry-run] [--output PATH]
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# Source Matrix — Tier 1 repos to scan every cycle
TIER1_REPOS = [
    "NousResearch/hermes-agent",
    "anthropics/skills",
    "MemPalace/mempalace",
    "garrytan/gstack",
]

# Tier 2 — scan daily but lower priority
TIER2_REPOS = [
    "anthropics/claude-code",
    "bytedance/deer-flow",
    "mattpocock/skills",
    "forrestchang/andrej-karpathy-skills",
    "crewAIInc/crewAI",
    "volcengine/OpenViking",
]

# Topic keywords for signal matching
TOPIC_KEYWORDS = {
    "T-MEM": ["memory", "context", "session", "persist", "recall", "amnesia", "remember"],
    "T-MvS": ["multi-agent", "coordination", "handoff", "orchestrat", "consensus"],
    "T-CBB": ["pipeline", "review", "TDD", "delivery", "autonomous", "black box"],
    "T-DDD": ["domain", "knowledge", "documentation", "stale", "CLAUDE.md", "context file"],
    "T-CUL": ["config", "cultivat", "grow", "evolv", "compound", "accumulate"],
    "T-6SX": ["self-heal", "self-evolv", "self-monitor", "harness", "agent lifecycle"],
    "T-SxT": ["transformation", "adoption", "org", "team scale", "solo builder"],
    "T-SOV": ["memory ownership", "vendor lock", "platform memory", "sovereign"],
}


def run_gh(args: list[str], timeout: int = 30) -> dict | list | None:
    """Run a gh CLI command and parse JSON output."""
    cmd = ["gh"] + args + ["--json"] if "--json" not in " ".join(args) else ["gh"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout) if result.stdout.strip() else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def fetch_recent_issues(repo: str, since_hours: int = 24) -> list[dict]:
    """Fetch issues updated in the last N hours."""
    since = (datetime.now(tz=None) - timedelta(hours=since_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Use --paginate=false and simple jq to avoid escaping issues
    cmd = [
        "gh", "api", f"repos/{repo}/issues",
        "-q", f'[.[] | select(.updated_at > "{since}")][:10]',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        issues = json.loads(result.stdout) if result.stdout.strip() else []
        # Normalize to consistent shape
        return [
            {
                "number": i.get("number"),
                "title": i.get("title", ""),
                "user": i.get("user", {}).get("login", "unknown"),
                "comments": i.get("comments", 0),
                "created_at": i.get("created_at", ""),
                "updated_at": i.get("updated_at", ""),
                "html_url": i.get("html_url", ""),
            }
            for i in issues
        ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def match_topics(title: str, body: str = "") -> list[str]:
    """Match issue/discussion text against topic keywords."""
    text = (title + " " + body).lower()
    matched = []
    for topic_id, keywords in TOPIC_KEYWORDS.items():
        if any(kw.lower() in text for kw in keywords):
            matched.append(topic_id)
    return matched


def scan_repos(repos: list[str], tier: int, since_hours: int = 24) -> list[dict]:
    """Scan a list of repos for new signals."""
    signals = []
    for repo in repos:
        issues = fetch_recent_issues(repo, since_hours)
        for issue in issues:
            topics = match_topics(issue.get("title", ""))
            if topics:
                signals.append({
                    "repo": repo,
                    "tier": tier,
                    "issue_number": issue["number"],
                    "title": issue["title"],
                    "url": issue.get("html_url", ""),
                    "author": issue.get("user", "unknown"),
                    "existing_comments": issue.get("comments", 0),
                    "created_at": issue.get("created_at", ""),
                    "updated_at": issue.get("updated_at", ""),
                    "matched_topics": topics,
                    "scanned_at": datetime.utcnow().isoformat(),
                })
    return signals


def monitor(dry_run: bool = False, output_path: str | None = None) -> list[dict]:
    """Run full monitor cycle — scan all tiers, output signals."""
    all_signals = []

    # Tier 1: full scan
    all_signals.extend(scan_repos(TIER1_REPOS, tier=1))

    # Tier 2: daily scan
    all_signals.extend(scan_repos(TIER2_REPOS, tier=2, since_hours=24))

    # Sort by recency
    all_signals.sort(key=lambda s: s.get("updated_at", ""), reverse=True)

    if dry_run:
        print(f"[DRY RUN] Found {len(all_signals)} signals")
        for s in all_signals[:5]:
            print(f"  {s['repo']}#{s['issue_number']}: {s['title']} ({s['matched_topics']})")
        return all_signals

    # Write output
    if output_path:
        out = Path(output_path)
    else:
        out = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / ".artifacts" / "signals.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_signals, indent=2, default=str))
    print(f"Wrote {len(all_signals)} signals to {out}")
    return all_signals


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    output = None
    for i, arg in enumerate(sys.argv):
        if arg == "--output" and i + 1 < len(sys.argv):
            output = sys.argv[i + 1]
    monitor(dry_run=dry_run, output_path=output)
