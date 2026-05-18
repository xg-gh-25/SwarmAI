"""GitHub Community Engine — TRACK stage.

Checks replies on published comments, logs engagement data.
Maintains engagement_log.jsonl and reply_archive.jsonl.

Usage:
  python -m skills.s_github_community.scripts.track [--dry-run]
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ARTIFACTS_DIR = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / ".artifacts"
ENGAGEMENT_LOG = ARTIFACTS_DIR / "engagement_log.jsonl"
REPLY_ARCHIVE = ARTIFACTS_DIR / "reply_archive.jsonl"
STAR_LOG = ARTIFACTS_DIR / "star_log.jsonl"


def load_active_threads() -> list[dict]:
    """Load active threads from PROJECT.md (parsed) or engagement log."""
    if not ENGAGEMENT_LOG.exists():
        return []
    threads = []
    with open(ENGAGEMENT_LOG) as f:
        for line in f:
            entry = json.loads(line.strip())
            if entry.get("status") == "active":
                threads.append(entry)
    return threads


def check_replies(repo: str, issue_number: int, our_comment_id: int) -> list[dict]:
    """Check if our comment got replies (comments posted after ours)."""
    cmd = [
        "gh", "api", f"repos/{repo}/issues/{issue_number}/comments",
        "--jq", f'[.[] | select(.id > {our_comment_id}) | '
        '{id: .id, author: .user.login, body: .body[:500], '
        'created_at: .created_at, is_maintainer: (.author_association == "MEMBER" or .author_association == "OWNER")}]'
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def log_engagement(entry: dict):
    """Append an engagement entry to the log."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENGAGEMENT_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_known_reply_ids() -> set[int]:
    """Load reply IDs already in the archive to prevent duplicates."""
    if not REPLY_ARCHIVE.exists():
        return set()
    ids = set()
    with open(REPLY_ARCHIVE) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                ids.add(entry.get("id", 0))
            except json.JSONDecodeError:
                continue
    return ids


def log_reply(reply: dict, known_ids: set[int] | None = None):
    """Append a reply to the archive (skips duplicates)."""
    if known_ids is not None and reply.get("id") in known_ids:
        return False
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPLY_ARCHIVE, "a") as f:
        f.write(json.dumps(reply, default=str) + "\n")
    return True


def score_engagement(entry: dict, replies: list[dict]) -> int:
    """Score engagement quality: 0=ignored, 1=upvoted, 2=replied, 3=maintainer."""
    if not replies:
        return 0
    has_maintainer = any(r.get("is_maintainer") for r in replies)
    if has_maintainer:
        return 3
    return 2  # Has replies but no maintainer


def _load_known_stargazers() -> set[str]:
    """Load stargazers we've already logged."""
    if not STAR_LOG.exists():
        return set()
    known = set()
    with open(STAR_LOG) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                known.add(entry.get("user", ""))
            except json.JSONDecodeError:
                continue
    return known


def _fetch_stargazers() -> list[dict]:
    """Fetch SwarmAI stargazers with timestamps."""
    cmd = [
        "gh", "api", "repos/xg-gh-25/SwarmAI/stargazers?per_page=100",
        "-H", "Accept: application/vnd.github.v3.star+json",
        "--jq", '[.[] | {user: .user.login, starred_at: .starred_at}]',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def _check_user_in_discussions(user: str, repos: list[str]) -> list[str]:
    """Check if a stargazer participates in discussions/issues we've engaged with.

    Checks both issues/comments AND discussion comments (separate GitHub APIs).
    """
    found_in = []
    for repo in repos[:5]:  # Limit API calls
        # Check issues/comments
        cmd = [
            "gh", "api", f"repos/{repo}/issues/comments",
            "--jq", f'[.[] | select(.user.login=="{user}")] | length',
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip() not in ("0", "", "[]"):
                count = int(result.stdout.strip())
                if count > 0:
                    found_in.append(repo)
                    continue
        except (subprocess.TimeoutExpired, ValueError):
            pass

        # Check discussion comments via GraphQL
        owner, name = repo.split("/", 1) if "/" in repo else ("", repo)
        query = (
            '{ repository(owner:"%s", name:"%s") { '
            'discussionComments(last:100) { nodes { author { login } } } } }' % (owner, name)
        )
        cmd_gql = ["gh", "api", "graphql", "-f", f"query={query}",
                   "--jq", f'[.data.repository.discussionComments.nodes[] | select(.author.login=="{user}")] | length']
        try:
            result = subprocess.run(cmd_gql, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip() not in ("0", "", "[]"):
                count = int(result.stdout.strip())
                if count > 0:
                    found_in.append(repo)
        except (subprocess.TimeoutExpired, ValueError):
            continue
    return found_in


def track_stars(engagement_log: list[dict], dry_run: bool = False) -> dict:
    """Track new stars and attribute them to engagement activity.

    Attribution logic:
    - HIGH: starrer participates in same discussion we commented on
    - MEDIUM: star came after our comment + starrer has starred repos in our Source Matrix
    - LOW: star came after we started engaging but no direct connection found
    - ORGANIC: star before our first engagement (2026-05-17)
    """
    ENGAGEMENT_START = "2026-05-17T00:00:00Z"  # First day we posted comments

    known = _load_known_stargazers()
    stargazers = _fetch_stargazers()
    new_stars = [s for s in stargazers if s["user"] not in known]

    # Repos we've engaged with (for attribution check)
    engaged_repos = list({e.get("repo", "") for e in engagement_log if e.get("repo")})

    star_results = {
        "total_stars": len(stargazers),
        "new_stars": len(new_stars),
        "attributed": [],
    }

    for star in new_stars:
        user = star["user"]
        starred_at = star["starred_at"]

        # Determine attribution confidence
        if starred_at < ENGAGEMENT_START:
            confidence = "organic"
            source = "pre-engagement"
            source_url = ""
        else:
            # Check if user is active in repos we commented on
            overlap = _check_user_in_discussions(user, engaged_repos)
            if overlap:
                confidence = "high"
                source = f"active in {', '.join(overlap)}"
                # Link to the discussion/issue where we both participated
                repo = overlap[0]
                # Find our comment in that repo from engagement log
                our_issue = None
                for e in engagement_log:
                    if e.get("repo") == repo:
                        our_issue = e.get("issue_number")
                        break
                if our_issue:
                    source_url = f"https://github.com/{repo}/issues/{our_issue}"
                else:
                    source_url = f"https://github.com/{repo}"
            else:
                confidence = "low"
                source = "post-engagement, no direct link found"
                source_url = ""

        attribution = {
            "user": user,
            "starred_at": starred_at,
            "confidence": confidence,
            "source": source,
            "source_url": source_url,
            "tracked_at": datetime.now(timezone.utc).isoformat(),
        }
        star_results["attributed"].append(attribution)

        if not dry_run:
            with open(STAR_LOG, "a") as f:
                f.write(json.dumps(attribution, default=str) + "\n")

    if dry_run and new_stars:
        print(f"[DRY RUN] Stars: {len(stargazers)} total, {len(new_stars)} new")
        for a in star_results["attributed"]:
            print(f"  ⭐ {a['user']} ({a['confidence']}) — {a['source']}")

    return star_results


def track(dry_run: bool = False) -> dict:
    """Run full track cycle — check all active threads for replies + star attribution."""
    threads = load_active_threads()
    known_reply_ids = _load_known_reply_ids()
    results = {
        "tracked_at": datetime.now(timezone.utc).isoformat(),
        "threads_checked": len(threads),
        "replies_found": 0,
        "new_replies": 0,
        "maintainer_replies": 0,
        "scores": [],
        "stars": {},
    }

    for thread in threads:
        repo = thread.get("repo")
        issue_number = thread.get("issue_number")
        comment_id = thread.get("comment_id", 0)

        if not repo or not issue_number:
            continue

        replies = check_replies(repo, issue_number, comment_id)

        if replies:
            results["replies_found"] += len(replies)
            maintainer_count = sum(1 for r in replies if r.get("is_maintainer"))
            results["maintainer_replies"] += maintainer_count

            if not dry_run:
                for reply in replies:
                    reply["source_repo"] = repo
                    reply["source_issue"] = issue_number
                    reply["tracked_at"] = datetime.now(timezone.utc).isoformat()
                    if log_reply(reply, known_ids=known_reply_ids):
                        results["new_replies"] += 1
                        known_reply_ids.add(reply.get("id", 0))

        score = score_engagement(thread, replies)
        results["scores"].append({
            "repo": repo,
            "issue": issue_number,
            "score": score,
            "reply_count": len(replies),
        })

    # Star attribution
    engagement_log = []
    if ENGAGEMENT_LOG.exists():
        with open(ENGAGEMENT_LOG) as f:
            for line in f:
                try:
                    engagement_log.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue

    star_results = track_stars(engagement_log, dry_run=dry_run)
    results["stars"] = star_results

    if dry_run:
        print(f"[DRY RUN] Tracked {results['threads_checked']} threads, "
              f"found {results['replies_found']} replies "
              f"({results['maintainer_replies']} from maintainers)")
        print(f"[DRY RUN] Stars: {star_results.get('total_stars', 0)} total, "
              f"{star_results.get('new_stars', 0)} new")
    else:
        # Save tracking results
        track_log = ARTIFACTS_DIR / "track_results.json"
        track_log.write_text(json.dumps(results, indent=2, default=str))

    return results


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    track(dry_run=dry_run)
