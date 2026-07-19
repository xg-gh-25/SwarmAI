"""GitHub Community Engine — MONITOR stage.

Scans Source Matrix repos for new signals (issues, discussions, replies).
Outputs signals.json for the MATCH stage to score.

Usage:
  python -m skills.s_github_community.scripts.monitor [--dry-run] [--output PATH]
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# --- Source Matrix (single source of truth = TECH.md) ---
#
# The repos to scan are DERIVED AT RUNTIME from the GitHub_Community project's
# TECH.md "### Current Roster" table — NOT hardcoded here. This kills the drift
# where the roster (TECH.md) and the scan list (this file) diverged: a repo added
# to TECH.md is now picked up automatically on the next monitor cycle, with no
# second list to hand-maintain. Tier 3 is intentionally excluded (not scanned).
# See Projects/GitHub_Community/TECH.md § "Three Matrices — DO NOT MERGE THEM".

# Default location of the authoritative roster (workspace tree, same base as the
# signals.json output path used in monitor() below).
_TECH_MD_PATH = (
    Path.home()
    / ".swarm-ai"
    / "SwarmWS"
    / "Projects"
    / "GitHub_Community"
    / "TECH.md"
)

# Minimum plausible roster size — a parse yielding fewer than this means the table
# format changed or the section was not found, and we FAIL LOUD rather than
# silently scan almost nothing (the prior hardcoded Tier-1 alone was 6 repos).
_MIN_PLAUSIBLE_REPOS = 5

# owner/name: GitHub slug chars only (letters, digits, ., _, -), exactly one slash.
_REPO_RE = re.compile(r"([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)")
# Prefer the repo named in a github.com URL — robust to truncated link TEXT.
_URL_REPO_RE = re.compile(r"github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)")


def _extract_repo_from_cell(cell: str) -> str | None:
    """Extract owner/name from a roster table's repo cell (column 2).

    Handles: `[text](https://github.com/OWNER/NAME)`, a bare `owner/name`, and a
    truncated link text where the full name lives only in the URL (URL wins).
    Operates on the CELL ONLY (never the whole row) so links embedded in a
    description column can't leak. Returns None if no repo slug is found.
    """
    cell = cell.strip()
    m = _URL_REPO_RE.search(cell)  # URL-first — survives truncated link text
    if m:
        return m.group(1)
    m = _REPO_RE.search(cell)  # bare owner/name fallback
    if m:
        return m.group(1)
    return None


def _parse_stars(s: str) -> int:
    """Parse a Current Roster stars cell to an int. Robust by construction:
    strips markdown bold/commas, handles K/k suffix (incl. decimals like 25.5K),
    bare integers, and returns 0 for missing/em-dash/unparseable (NEVER raises —
    a bad stars cell must not crash the whole matrix parse)."""
    s = s.strip().strip("*").replace(",", "").strip()
    if not s or s in ("—", "-"):
        return 0
    try:
        if s[-1] in ("K", "k"):
            return int(float(s[:-1]) * 1000)
        return int(s)
    except (ValueError, IndexError):
        return 0


def load_source_matrix(
    tech_md_path: "Path | str | None" = None,
) -> "list[dict]":
    """THE single parser for the TECH.md Source Matrix — the SSOT consumed by BOTH
    monitor (scan list, via load_tracked_repos) and report (Source Matrix tab +
    DDD health). Returns the FULL roster (all tiers incl. 3) as
    [{"repo": str, "tier": int, "stars": int}], insertion-ordered, deduped
    (first occurrence wins).

    Section-scoped to the "### Current Roster" table: parsing starts at that
    heading and stops at the next `##`/`###` heading — structurally excluding the
    Hot Topics/Rankings table (shares the `| N |` row shape but column 2 is topic
    prose) and the "Source Matrix Notes" prose below. Repo is extracted from
    column 2 only (URL-first); tier from the leading `| N |` digit; stars from
    column 3 via _parse_stars.

    Pure parse — NO fail-loud here (callers decide). load_tracked_repos keeps the
    scan-list fail-loud; report warns on an implausibly small result.
    """
    path = Path(tech_md_path) if tech_md_path else _TECH_MD_PATH
    lines = path.read_text(encoding="utf-8").splitlines()

    # Isolate the Current Roster section: from its heading to the next heading.
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^#{2,3}\s+Current Roster\b", line.strip()):
            start = i + 1
            break
    if start is None:
        return []
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^#{2,3}\s+", lines[j].strip()):
            end = j
            break

    matrix: list[dict] = []
    seen: set[str] = set()
    for line in lines[start:end]:
        # A roster row looks like: | 1 | <repo cell> | stars | category | last |
        cells = [c.strip() for c in line.split("|")]
        # split on '|' of a well-formed row yields ['', '1', '<repo>', '<stars>', ...]
        if len(cells) < 4:
            continue
        if cells[1] not in ("1", "2", "3"):
            continue
        repo = _extract_repo_from_cell(cells[2])
        if not repo or repo in seen:
            continue
        seen.add(repo)
        matrix.append({"repo": repo, "tier": int(cells[1]), "stars": _parse_stars(cells[3])})
    return matrix


def load_tracked_repos(
    tech_md_path: "Path | str | None" = None,
) -> "tuple[list[str], list[str]]":
    """Return (tier1_repos, tier2_repos) for the monitor scan list — a thin
    wrapper over load_source_matrix() (the SSOT). Tier 3 is tracked but NOT
    scanned, so it is filtered out here. Order preserved, deduped (via
    load_source_matrix's single-pass dedup, so cross-tier dupes keep first
    occurrence — same semantics as before the extraction).

    Raises RuntimeError if fewer than _MIN_PLAUSIBLE_REPOS tier1+tier2 repos are
    found (parse broke — fail loud instead of scanning nothing). The threshold is
    counted on tier1+tier2 ONLY (not the tier3-inclusive full matrix), preserving
    the exact pre-refactor behavior.
    """
    path = Path(tech_md_path) if tech_md_path else _TECH_MD_PATH
    matrix = load_source_matrix(path)
    tier1 = [m["repo"] for m in matrix if m["tier"] == 1]
    tier2 = [m["repo"] for m in matrix if m["tier"] == 2]

    total = len(tier1) + len(tier2)
    if total < _MIN_PLAUSIBLE_REPOS:
        raise RuntimeError(
            f"load_tracked_repos: only {total} repos parsed from {path} "
            f"(< {_MIN_PLAUSIBLE_REPOS}); the Source Matrix table format likely "
            f"changed — refusing to scan an implausibly small set."
        )
    return tier1, tier2


def resolve_repo_short_name(
    short: str, source_matrix: "list[dict] | None" = None
) -> "str | None":
    """Resolve a TECH.md Our-Topic-Matrix short repo name (e.g. 'hermes',
    'MemPalace') to a full owner/name from the Source Matrix roster.

    The Our Topic Matrix 'Primary Repos' column uses short names; the roster uses
    owner/name. Match case-insensitively against the name half (after '/'), then
    against the owner half as a fallback. Returns None if unresolvable (caller
    renders it as plain text — never a broken link).
    """
    short = short.strip()
    if not short:
        return None
    if "/" in short:  # already a full name
        return short
    matrix = source_matrix if source_matrix is not None else load_source_matrix()
    low = short.lower()
    # EXACT matches only (name-half, then owner-half). We deliberately do NOT do
    # substring/fuzzy matching: a generic word in the Primary-Repos column (e.g.
    # "enterprise", "skills ecosystem") would substring-match an unrelated repo
    # (aws-samples/...-enterprise-agents-...) and render a wrong link — the exact
    # kind of drift this SSOT work removes. Unresolvable → None (caller shows the
    # raw short name as plain text). The fix for a real repo that doesn't resolve
    # is to write its exact name in TECH.md, not to loosen this matcher.
    for m in matrix:
        if m["repo"].split("/", 1)[-1].lower() == low:  # name-half exact
            return m["repo"]
    for m in matrix:
        if m["repo"].split("/", 1)[0].lower() == low:  # owner-half exact
            return m["repo"]
    return None


def load_topic_matrix(
    tech_md_path: "Path | str | None" = None,
) -> "list[dict]":
    """Parse the TECH.md '### Current Topics' table (under 'Our Topic Matrix') —
    the SSOT for OUR topics (supply side). Returns
    [{"id","name","status","thesis","primary_repos":[full owner/name...],
      "primary_repos_raw":[short...]}], insertion-ordered.

    Section-scoped from the '### Current Topics' heading to the next heading.
    primary_repos are resolved from short names to full owner/name via the Source
    Matrix roster (unresolvable ones are dropped from primary_repos but kept in
    primary_repos_raw for display). This replaces report.py's hardcoded 9-item
    topic_matrix array so the two never drift.
    """
    path = Path(tech_md_path) if tech_md_path else _TECH_MD_PATH
    lines = path.read_text(encoding="utf-8").splitlines()

    start = None
    for i, line in enumerate(lines):
        if re.match(r"^#{2,3}\s+Current Topics\b", line.strip()):
            start = i + 1
            break
    if start is None:
        return []
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^#{2,3}\s+", lines[j].strip()):
            end = j
            break

    roster = load_source_matrix(path)  # for short-name resolution
    topics: list[dict] = []
    seen: set[str] = set()
    for line in lines[start:end]:
        cells = [c.strip() for c in line.split("|")]
        # | ID | Topic | Status | Thesis | Primary Repos | Hot Topic Match |
        if len(cells) < 7:
            continue
        tid = cells[1]
        if not re.match(r"^T-[A-Za-z0-9]+$", tid) or tid in seen:
            continue
        seen.add(tid)
        raw_repos = [r.strip() for r in cells[5].split(",") if r.strip()]
        resolved = []
        for r in raw_repos:
            full = resolve_repo_short_name(r, roster)
            if full and full not in resolved:
                resolved.append(full)
        topics.append({
            "id": tid,
            "name": cells[2],
            "status": cells[3],
            "thesis": cells[4],
            "primary_repos": resolved,
            "primary_repos_raw": raw_repos,
        })
    return topics


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


# --- Signal quality gate (single admission threshold for ALL producers) ---
#
# Every produced signal — matrix scan (issues + discussions), dashboard discovery,
# AND the hot-topics discussion feed — must pass is_valuable_signal(). This closes
# the drift where the dashboard branch exempted unknown-repo signals from the topic
# check, letting bot/PR noise through (42% of a real run: dependabot, auto-release
# PRs, eks-distro-pr-bot). "Unfamiliar repo" is NOT a value signal.

# Auto-release PR titles from CI bots (dependabot, eks-distro-pr-bot, etc.).
# Anchored so a human "Update X to reflect Y" without the release/latest tail does
# not match — but "Bump/Update ... to latest|release" (the machine pattern) does.
_RELEASE_BOT_TITLE_RE = re.compile(
    r"^(\[.*?\]\s*)?(Bump|Update)\b.*\b(to latest|latest release|release)\b",
    re.IGNORECASE,
)


def _is_bot_author(author: str) -> bool:
    """True if the author login is a machine account.

    GitHub App bots end in '[bot]' (dependabot[bot]), but org CI machine-users do
    NOT (eks-distro-pr-bot — 19% of a real run's noise). Both endings are covered.
    """
    if not author:
        return False
    a = author.lower()
    return a.endswith("[bot]") or a.endswith("-bot")


def _is_release_bot_title(title: str) -> bool:
    """True if the title matches an automated dependency-bump / release-bump PR."""
    if not title:
        return False
    return bool(_RELEASE_BOT_TITLE_RE.match(title))


def _is_new(created_at: str, within_hours: int = 24) -> bool:
    """True if created_at is within the last `within_hours`.

    Empty/malformed created_at → False (never raise). This matters because the
    discussion path historically hardcoded created_at="" — an unguarded
    datetime.fromisoformat("") would crash the whole monitor cycle.
    """
    if not created_at:
        return False
    try:
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= datetime.now(timezone.utc) - timedelta(hours=within_hours)


def is_valuable_signal(signal: dict) -> tuple[bool, str]:
    """The single admission gate. Returns (keep?, reason).

    Layered, ORDER MATTERS — bot must be checked BEFORE activity, or a brand-new
    bot issue would slip through the is_new branch:
      1. not a PR       (url contains '/pull/')
      2. topics present (matched_topics non-empty)
      3. not a bot      (author login OR auto-release title)
      4. has activity   (existing_comments > 0 OR is_new)

    Operates on the NORMALIZED signal dict (author / existing_comments / title /
    url / matched_topics / created_at) — the shape all producers emit — so the
    same call is correct for scan and dashboard signals alike.
    """
    if "/pull/" in signal.get("url", ""):
        return False, "rejected: pull request (not an engageable issue/discussion)"
    if not signal.get("matched_topics"):
        return False, "rejected: no topic match (off our demand/supply intersection)"
    author = signal.get("author", "")
    title = signal.get("title", "")
    if _is_bot_author(author) or _is_release_bot_title(title):
        return False, f"rejected: bot/automated (author={author!r})"
    if signal.get("existing_comments", 0) > 0 or _is_new(signal.get("created_at", "")):
        return True, "ok"
    return False, "rejected: inactive (0 comments and not newly opened)"


def fetch_recent_discussions(repo: str, since_hours: int = 24) -> list[dict]:
    """Fetch discussions updated in the last N hours via GraphQL."""
    owner, name = repo.split("/", 1) if "/" in repo else ("", repo)
    query = """query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        discussions(first: 15, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes { number title body comments { totalCount } createdAt updatedAt url
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
                    "created_at": d.get("createdAt", ""),
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
            signal = {
                "repo": repo,
                "tier": tier,
                "issue_number": issue["number"],
                "title": issue["title"],
                "url": issue.get("html_url", ""),
                "author": issue.get("user", "unknown"),
                "existing_comments": issue.get("comments", 0),
                "created_at": issue.get("created_at", ""),
                "updated_at": issue.get("updated_at", ""),
                "matched_topics": match_topics(issue.get("title", "")),
                "signal_type": "issue",
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            }
            if is_valuable_signal(signal)[0]:
                signals.append(signal)

        # Scan Discussions (where most community activity happens)
        discussions = fetch_recent_discussions(repo, since_hours)
        for disc in discussions:
            signal = {
                "repo": repo,
                "tier": tier,
                "issue_number": disc["number"],
                "title": disc["title"],
                "url": disc.get("html_url", ""),
                "author": disc.get("user", "unknown"),
                "existing_comments": disc.get("comments", 0),
                "created_at": disc.get("created_at", ""),
                "updated_at": disc.get("updated_at", ""),
                "matched_topics": match_topics(disc.get("title", "")),
                "signal_type": "discussion",
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            }
            if is_valuable_signal(signal)[0]:
                signals.append(signal)
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
              nodes { number title body comments { totalCount } updatedAt category { name }
                      author { login } }
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
                    # Hot-topics is the 4th signal path — it must ALSO exclude bot
                    # discussions, or bot noise inflates the topic-engagement ranks.
                    author = d.get("author", {}).get("login", "") if d.get("author") else ""
                    if _is_bot_author(author) or _is_release_bot_title(d.get("title", "")):
                        continue
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
    """Convert dashboard events to signals via the unified quality gate.

    Both known-repo and discovery (unknown-repo) events must pass
    is_valuable_signal(). "Unfamiliar repo" is NOT a value signal — the old
    exemption (let any unknown-repo event through regardless of topic) was the
    root of the dashboard noise: dependabot / auto-release PRs / eks-distro-pr-bot
    (42% of a real run). Discovery is preserved for repos that DO pass the gate
    (topic-matched, non-PR, non-bot, active) — tagged tier=0 / is_discovery.
    """
    signals = []
    for event in events:
        repo = event.get("repo", "")
        is_new_repo = repo not in known_repos
        signal = {
            "repo": repo,
            "tier": 0 if is_new_repo else None,  # tier 0 = discovery
            "issue_number": event.get("number", 0),
            "title": event.get("title", ""),
            "url": event.get("url", ""),
            "author": event.get("author", "unknown"),
            "existing_comments": event.get("comments", 0),
            # NB: for an IssueCommentEvent this is the COMMENT/event time, not the
            # issue's open time — so is_new() here measures feed recency, not issue
            # age. Harmless (feed events are inherently recent) but don't read it as
            # "issue opened at".
            "created_at": event.get("created", ""),
            "updated_at": event.get("created", ""),
            "matched_topics": match_topics(event.get("title", "")),
            "source": "dashboard",
            "event_type": event.get("type", ""),
            "is_discovery": is_new_repo,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }
        if is_valuable_signal(signal)[0]:
            signals.append(signal)

    return signals


# --- Main Monitor ---


def monitor(dry_run: bool = False, output_path: str | None = None) -> dict:
    """Run full monitor cycle — scan signals + track hot topics + dashboard feed."""
    all_signals = []

    # Derive the scan list from TECH.md at runtime (single source of truth).
    tier1_repos, tier2_repos = load_tracked_repos()

    # Tier 1: full scan (signals for engagement)
    all_signals.extend(scan_repos(tier1_repos, tier=1))

    # Tier 2: daily scan
    all_signals.extend(scan_repos(tier2_repos, tier=2, since_hours=24))

    # Dashboard feed: dynamic signals from GitHub activity feed
    known_repos = set(tier1_repos + tier2_repos)
    dashboard_events = fetch_dashboard_feed()
    dashboard_signals = dashboard_to_signals(dashboard_events, known_repos)

    # Deduplicate dashboard signals against matrix-sourced ones
    existing_keys = {(s["repo"], s["issue_number"]) for s in all_signals}
    new_dashboard = [s for s in dashboard_signals if (s["repo"], s["issue_number"]) not in existing_keys]
    all_signals.extend(new_dashboard)

    # Sort by recency
    all_signals.sort(key=lambda s: s.get("updated_at", ""), reverse=True)

    # Hot Topics: scan discussions for demand-side tracking
    discussion_repos = [r for r in tier1_repos + tier2_repos]
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
