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


# --- Hot Topics Tracking ---

# GitHub Hot Topics keywords — tracks what the COMMUNITY is discussing (demand side)
# Independent from TOPIC_KEYWORDS (our supply side)
HOT_TOPIC_KEYWORDS = {
    "HT-PROD-OPS": ["production", "monitoring", "observability", "debugging", "cost management", "runtime"],
    "HT-MEMORY": ["memory", "persist", "recall", "forget", "RAG", "retrieval", "embedding"],
    "HT-COORDINATION": ["multi-agent", "shared state", "coordination", "consensus", "handoff", "flow"],
    "HT-SKILL-ARCH": ["skill", "hierarchy", "DRY", "governance", "discovery", "registry"],
    "HT-AUTONOMY": ["autonomy", "pause", "resume", "approve", "human-in-loop", "guardrail"],
    "HT-CHUNKING": ["chunking", "chunk", "split", "retrieval strategy", "vector", "graph"],
    "HT-STREAMING": ["streaming", "SSE", "websocket", "real-time", "flow-to-user"],
    "HT-API-COST": ["cost", "budget", "token usage", "rate limit", "model routing", "pricing"],
}


def fetch_hot_discussions(repos: list[str], limit: int = 10) -> list[dict]:
    """Fetch recent discussions from repos to track Hot Topics engagement."""
    hot_data = []
    for repo in repos:
        owner, name = repo.split("/")
        query = """query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            discussions(first: 10, orderBy: {field: UPDATED_AT, direction: DESC}) {
              nodes { number title body comments { totalCount } updatedAt category { name } }
            }
          }
        }"""
        cmd = [
            "gh", "api", "graphql",
            "-f", f"owner={owner}",
            "-f", f"name={name}",
            "-f", f"query={query}",
            "--jq", ".data.repository.discussions.nodes // []",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                discussions = json.loads(result.stdout)
                for d in discussions:
                    hot_data.append({
                        "repo": repo,
                        "number": d.get("number"),
                        "title": d.get("title", ""),
                        "comments": d.get("comments", {}).get("totalCount", 0),
                        "updated_at": d.get("updatedAt", ""),
                        "category": d.get("category", {}).get("name", ""),
                    })
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            continue
    return hot_data


def compute_hot_topics(discussions: list[dict]) -> list[dict]:
    """Compute Hot Topics rankings from discussion engagement data."""
    topic_scores: dict[str, dict[str, Any]] = {}

    for ht_id, keywords in HOT_TOPIC_KEYWORDS.items():
        topic_scores[ht_id] = {
            "id": ht_id,
            "total_comments": 0,
            "thread_count": 0,
            "top_thread": None,
            "repos": set(),
        }

    for disc in discussions:
        text = (disc.get("title", "") + " " + disc.get("category", "")).lower()
        for ht_id, keywords in HOT_TOPIC_KEYWORDS.items():
            if any(kw.lower() in text for kw in keywords):
                topic_scores[ht_id]["total_comments"] += disc.get("comments", 0)
                topic_scores[ht_id]["thread_count"] += 1
                topic_scores[ht_id]["repos"].add(disc.get("repo", ""))
                # Track top thread by comments
                if (
                    not topic_scores[ht_id]["top_thread"]
                    or disc.get("comments", 0) > topic_scores[ht_id]["top_thread"].get("comments", 0)
                ):
                    topic_scores[ht_id]["top_thread"] = disc

    # Rank by total engagement
    ranked = sorted(
        topic_scores.values(),
        key=lambda t: t["total_comments"],
        reverse=True,
    )

    # Convert sets to lists for JSON serialization
    for t in ranked:
        t["repos"] = list(t["repos"])

    return ranked


# --- Main Monitor ---


def monitor(dry_run: bool = False, output_path: str | None = None) -> dict:
    """Run full monitor cycle — scan signals + track hot topics."""
    all_signals = []

    # Tier 1: full scan (signals for engagement)
    all_signals.extend(scan_repos(TIER1_REPOS, tier=1))

    # Tier 2: daily scan
    all_signals.extend(scan_repos(TIER2_REPOS, tier=2, since_hours=24))

    # Sort by recency
    all_signals.sort(key=lambda s: s.get("updated_at", ""), reverse=True)

    # Hot Topics: scan discussions for demand-side tracking
    discussion_repos = [r for r in TIER1_REPOS + TIER2_REPOS]
    hot_discussions = fetch_hot_discussions(discussion_repos)
    hot_topics = compute_hot_topics(hot_discussions)

    output_data = {
        "scanned_at": datetime.now().isoformat(),
        "signals": all_signals,
        "hot_topics": hot_topics,
        "hot_discussions_scanned": len(hot_discussions),
    }

    if dry_run:
        print(f"[DRY RUN] Found {len(all_signals)} signals")
        for s in all_signals[:5]:
            print(f"  {s['repo']}#{s['issue_number']}: {s['title']} ({s['matched_topics']})")
        print(f"\n[HOT TOPICS] Ranked by community engagement:")
        for ht in hot_topics[:5]:
            if ht["total_comments"] > 0:
                top = ht.get("top_thread")
                top_info = f" (top: {top['repo']}#{top['number']} {top['comments']}💬)" if top else ""
                print(f"  {ht['id']}: {ht['total_comments']}💬 across {ht['thread_count']} threads{top_info}")
        return output_data

    # Write output
    if output_path:
        out = Path(output_path)
    else:
        out = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / ".artifacts" / "signals.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output_data, indent=2, default=str))
    print(f"Wrote {len(all_signals)} signals + {len(hot_topics)} hot topics to {out}")
    return output_data


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    output = None
    for i, arg in enumerate(sys.argv):
        if arg == "--output" and i + 1 < len(sys.argv):
            output = sys.argv[i + 1]
    monitor(dry_run=dry_run, output_path=output)
