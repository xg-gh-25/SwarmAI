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


def log_reply(reply: dict):
    """Append a reply to the archive."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPLY_ARCHIVE, "a") as f:
        f.write(json.dumps(reply, default=str) + "\n")


def score_engagement(entry: dict, replies: list[dict]) -> int:
    """Score engagement quality: 0=ignored, 1=upvoted, 2=replied, 3=maintainer."""
    if not replies:
        return 0
    has_maintainer = any(r.get("is_maintainer") for r in replies)
    if has_maintainer:
        return 3
    return 2  # Has replies but no maintainer


def track(dry_run: bool = False) -> dict:
    """Run full track cycle — check all active threads for replies."""
    threads = load_active_threads()
    results = {
        "tracked_at": datetime.now(timezone.utc).isoformat(),
        "threads_checked": len(threads),
        "replies_found": 0,
        "maintainer_replies": 0,
        "scores": [],
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
                    log_reply(reply)

        score = score_engagement(thread, replies)
        results["scores"].append({
            "repo": repo,
            "issue": issue_number,
            "score": score,
            "reply_count": len(replies),
        })

    if dry_run:
        print(f"[DRY RUN] Tracked {results['threads_checked']} threads, "
              f"found {results['replies_found']} replies "
              f"({results['maintainer_replies']} from maintainers)")
    else:
        # Save tracking results
        track_log = ARTIFACTS_DIR / "track_results.json"
        track_log.write_text(json.dumps(results, indent=2, default=str))

    return results


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    track(dry_run=dry_run)
