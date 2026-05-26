"""GitHub Community Engine — MONITOR stage.

Scans Source Matrix repos for new signals (issues, discussions, replies).
Outputs signals.json for the MATCH stage to score.

Usage:
  python -m skills.s_github_community.scripts.monitor [--dry-run] [--output PATH]
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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
    "nexu-io/open-design",
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


def fetch_recent_discussions(repo: str, since_hours: int = 24) -> list[dict]:
    """Fetch discussions updated in the last N hours via GraphQL."""
    owner, name = repo.split("/", 1) if "/" in repo else ("", repo)
    query = """query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        discussions(first: 15, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes { number title body comments { totalCount } updatedAt url
                  author { login } }
        }
      }
    }"""
    cmd = [
        "gh", "api", "graphql",
        "-f", f"owner={owner}", "-f", f"name={name}",
        "-f", f"query={query}",
        "--jq", ".data.repository.discussions.nodes // []",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        discussions = json.loads(result.stdout) if result.stdout.strip() else []
        # Filter by recency
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        recent = []
        for d in discussions:
            updated = d.get("updatedAt", "")
            if updated and updated > cutoff.isoformat():
                recent.append({
                    "number": d.get("number"),
                    "title": d.get("title", ""),
                    "user": d.get("author", {}).get("login", "unknown") if d.get("author") else "unknown",
                    "comments": d.get("comments", {}).get("totalCount", 0),
                    "updated_at": updated,
                    "html_url": d.get("url", ""),
                    "type": "discussion",
                })
        return recent
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def scan_repos(repos: list[str], tier: int, since_hours: int = 24) -> list[dict]:
    """Scan a list of repos for new signals (Issues + Discussions)."""
    signals = []
    for repo in repos:
        # Scan Issues
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
                    "signal_type": "issue",
                    "scanned_at": datetime.now(timezone.utc).isoformat(),
                })

        # Scan Discussions (where most community activity happens)
        discussions = fetch_recent_discussions(repo, since_hours)
        for disc in discussions:
            topics = match_topics(disc.get("title", ""))
            if topics:
                signals.append({
                    "repo": repo,
                    "tier": tier,
                    "issue_number": disc["number"],
                    "title": disc["title"],
                    "url": disc.get("html_url", ""),
                    "author": disc.get("user", "unknown"),
                    "existing_comments": disc.get("comments", 0),
                    "created_at": "",
                    "updated_at": disc.get("updated_at", ""),
                    "matched_topics": topics,
                    "signal_type": "discussion",
                    "scanned_at": datetime.now(timezone.utc).isoformat(),
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


# --- Dashboard Feed ---


def fetch_dashboard_feed() -> list[dict]:
    """Fetch GitHub dashboard feed (received_events) for dynamic signal discovery.

    The dashboard feed surfaces activity in repos you star/watch/follow —
    a natural high-relevance signal source that complements the static Source Matrix.

    Filters for engagement-worthy events:
    - IssuesEvent (opened) — new issues to jump on (first-responder bonus)
    - IssueCommentEvent — active discussions happening now
    - DiscussionEvent — new discussions opened
    - DiscussionCommentEvent — active discussion threads
    """
    all_events = []
    for page in range(1, 4):  # 3 pages × 30 = up to 90 events
        cmd = [
            "gh", "api", f"/users/xg-gh-25/received_events?per_page=30&page={page}",
            "--jq", '[.[] | select('
            '.type == "IssuesEvent" or '
            '.type == "IssueCommentEvent" or '
            '.type == "DiscussionEvent" or '
            '.type == "DiscussionCommentEvent"'
            ') | {'
            'type: .type, '
            'repo: .repo.name, '
            'action: (.payload.action // ""), '
            'number: (.payload.issue.number // .payload.discussion.number // 0), '
            'title: (.payload.issue.title // .payload.discussion.title // ""), '
            'author: (.payload.issue.user.login // .payload.discussion.user.login // .payload.comment.user.login // ""), '
            'comments: (.payload.issue.comments // 0), '
            'url: (.payload.issue.html_url // .payload.discussion.html_url // ""), '
            'created: .created_at'
            '}]',
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                events = json.loads(result.stdout)
                all_events.extend(events)
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            continue

    # Deduplicate by (repo, number)
    seen = set()
    unique = []
    for e in all_events:
        key = (e.get("repo"), e.get("number"))
        if key not in seen and e.get("number", 0) > 0:
            seen.add(key)
            unique.append(e)

    return unique


def dashboard_to_signals(events: list[dict], known_repos: set[str]) -> list[dict]:
    """Convert dashboard events to signals, separating known vs discovery.

    - Events from Source Matrix repos → merged as regular signals (tagged source=dashboard)
    - Events from NEW repos → tagged as "discovery" signals (potential new Source Matrix entries)
    """
    signals = []
    for event in events:
        repo = event.get("repo", "")
        title = event.get("title", "")
        topics = match_topics(title)

        # Only keep events that match our topics OR are from unknown repos (discovery)
        is_new_repo = repo not in known_repos
        if not topics and not is_new_repo:
            continue

        signal = {
            "repo": repo,
            "tier": 0 if is_new_repo else None,  # tier 0 = discovery
            "issue_number": event.get("number", 0),
            "title": title,
            "url": event.get("url", ""),
            "author": event.get("author", "unknown"),
            "existing_comments": event.get("comments", 0),
            "created_at": event.get("created", ""),
            "updated_at": event.get("created", ""),
            "matched_topics": topics,
            "source": "dashboard",
            "event_type": event.get("type", ""),
            "is_discovery": is_new_repo,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }
        signals.append(signal)

    return signals


# --- Main Monitor ---


def monitor(dry_run: bool = False, output_path: str | None = None) -> dict:
    """Run full monitor cycle — scan signals + track hot topics + dashboard feed."""
    all_signals = []

    # Tier 1: full scan (signals for engagement)
    all_signals.extend(scan_repos(TIER1_REPOS, tier=1))

    # Tier 2: daily scan
    all_signals.extend(scan_repos(TIER2_REPOS, tier=2, since_hours=24))

    # Dashboard feed: dynamic signals from GitHub activity feed
    known_repos = set(TIER1_REPOS + TIER2_REPOS)
    dashboard_events = fetch_dashboard_feed()
    dashboard_signals = dashboard_to_signals(dashboard_events, known_repos)

    # Deduplicate dashboard signals against matrix-sourced ones
    existing_keys = {(s["repo"], s["issue_number"]) for s in all_signals}
    new_dashboard = [s for s in dashboard_signals if (s["repo"], s["issue_number"]) not in existing_keys]
    all_signals.extend(new_dashboard)

    # Sort by recency
    all_signals.sort(key=lambda s: s.get("updated_at", ""), reverse=True)

    # Hot Topics: scan discussions for demand-side tracking
    discussion_repos = [r for r in TIER1_REPOS + TIER2_REPOS]
    hot_discussions = fetch_hot_discussions(discussion_repos)
    hot_topics = compute_hot_topics(hot_discussions)

    output_data = {
        "scanned_at": datetime.now().isoformat(),
        "signals": all_signals,
        "dashboard_signals": new_dashboard,
        "hot_topics": hot_topics,
        "hot_discussions_scanned": len(hot_discussions),
        "dashboard_events_fetched": len(dashboard_events),
    }

    if dry_run:
        print(f"[DRY RUN] Found {len(all_signals)} signals "
              f"({len(all_signals) - len(new_dashboard)} from matrix, {len(new_dashboard)} from dashboard)")
        for s in all_signals[:5]:
            src = s.get("source", "matrix")
            disc = " 🆕" if s.get("is_discovery") else ""
            print(f"  [{src}{disc}] {s['repo']}#{s['issue_number']}: {s['title'][:60]} ({s['matched_topics']})")
        if new_dashboard:
            discoveries = [s for s in new_dashboard if s.get("is_discovery")]
            if discoveries:
                print(f"\n[DISCOVERY] {len(discoveries)} signals from repos NOT in Source Matrix:")
                for s in discoveries[:5]:
                    print(f"  🆕 {s['repo']}#{s['issue_number']}: {s['title'][:60]}")
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
    print(f"Wrote {len(all_signals)} signals ({len(new_dashboard)} from dashboard) + "
          f"{len(hot_topics)} hot topics to {out}")
    return output_data


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    output = None
    for i, arg in enumerate(sys.argv):
        if arg == "--output" and i + 1 < len(sys.argv):
            output = sys.argv[i + 1]
    monitor(dry_run=dry_run, output_path=output)
