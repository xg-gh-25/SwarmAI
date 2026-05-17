"""GitHub Community Engine — PUBLISH stage.

Posts comments to GitHub with confidence gate, engagement logging, and dry-run support.
Enforces the 4-condition quality gate before any publish.

Usage:
  python -m skills.s_github_community.scripts.publish \
    --repo <owner/name> --issue <number> --body <comment_text> \
    --confidence <1-10> --topic <T-XXX> [--dry-run]
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Constants
AUTO_PUBLISH_THRESHOLD = 8
MAX_COMMENTS_PER_REPO_PER_WEEK = 3
ARTIFACTS_DIR = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / ".artifacts"
ENGAGEMENT_LOG = ARTIFACTS_DIR / "engagement_log.jsonl"


def get_weekly_comment_count(repo: str) -> int:
    """Count comments posted to a repo in the last 7 days."""
    if not ENGAGEMENT_LOG.exists():
        return 0
    count = 0
    cutoff = (datetime.now(timezone.utc).timestamp()) - (7 * 24 * 3600)
    with open(ENGAGEMENT_LOG) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if entry.get("repo") == repo:
                    posted_at = entry.get("posted_at", "")
                    if posted_at:
                        entry_ts = datetime.fromisoformat(posted_at.replace("Z", "+00:00")).timestamp()
                        if entry_ts > cutoff:
                            count += 1
            except (json.JSONDecodeError, ValueError):
                continue
    return count


def quality_gate(confidence: int, repo: str, body: str) -> tuple[bool, str]:
    """Enforce the 4-condition quality gate + anti-spam.

    Returns (passed: bool, reason: str)
    """
    # Anti-spam check
    weekly_count = get_weekly_comment_count(repo)
    if weekly_count >= MAX_COMMENTS_PER_REPO_PER_WEEK:
        return False, f"anti_spam: {weekly_count}/{MAX_COMMENTS_PER_REPO_PER_WEEK} comments this week on {repo}"

    # Confidence threshold
    if confidence < AUTO_PUBLISH_THRESHOLD:
        return False, f"confidence: {confidence} < {AUTO_PUBLISH_THRESHOLD} (held for manual review)"

    # Body quality checks
    if len(body) < 100:
        return False, "body_too_short: < 100 chars (no substance)"

    if len(body) > 4000:
        return False, "body_too_long: > 4000 chars (will be buried)"

    # Must contain code/data (at least one code block or number)
    has_code = "```" in body or "`" in body
    has_data = any(c.isdigit() for c in body[:500])
    if not has_code and not has_data:
        return False, "no_substance: missing code snippet or measured data"

    # Must have footer link
    if "SwarmAI" not in body and "swarm-content" not in body:
        return False, "no_footer: missing SwarmAI attribution link"

    return True, "passed"


def publish_comment(
    repo: str,
    issue_number: int,
    body: str,
    confidence: int,
    topic: str,
    dry_run: bool = False,
) -> dict:
    """Publish a comment to GitHub with full quality gate.

    Returns result dict with status, comment_id (if published), reason.
    """
    # Run quality gate
    passed, reason = quality_gate(confidence, repo, body)

    result = {
        "repo": repo,
        "issue_number": issue_number,
        "confidence": confidence,
        "topic": topic,
        "gate_passed": passed,
        "gate_reason": reason,
        "published": False,
        "comment_id": None,
        "comment_url": None,
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }

    if not passed:
        if dry_run:
            print(f"[DRY RUN] BLOCKED: {reason}")
        result["status"] = "blocked"
        return result

    if dry_run:
        print(f"[DRY RUN] WOULD PUBLISH to {repo}#{issue_number} (confidence {confidence})")
        print(f"  Body preview: {body[:200]}...")
        result["status"] = "dry_run"
        return result

    # Publish via gh api
    cmd = [
        "gh", "api", f"repos/{repo}/issues/{issue_number}/comments",
        "-X", "POST", "-f", f"body={body}", "--jq", ".id,.html_url",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            lines = proc.stdout.strip().split("\n")
            result["comment_id"] = int(lines[0]) if lines else None
            result["comment_url"] = lines[1] if len(lines) > 1 else None
            result["published"] = True
            result["status"] = "published"
            print(f"✓ Published: {result['comment_url']}")
        else:
            result["status"] = "error"
            result["error"] = proc.stderr[:200]
            print(f"✗ Failed: {proc.stderr[:200]}")
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        print("✗ Timeout publishing comment")

    # Log to engagement log (always, even on failure)
    log_entry = {**result, "status": result.get("status", "unknown")}
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENGAGEMENT_LOG, "a") as f:
        f.write(json.dumps(log_entry, default=str) + "\n")

    return result


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Publish comment to GitHub")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--issue", type=int, required=True, help="Issue/PR number")
    parser.add_argument("--body", required=True, help="Comment body text")
    parser.add_argument("--confidence", type=int, required=True, help="Confidence score 1-10")
    parser.add_argument("--topic", default="unknown", help="Topic ID (T-XXX)")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually publish")
    args = parser.parse_args()

    publish_comment(
        repo=args.repo,
        issue_number=args.issue,
        body=args.body,
        confidence=args.confidence,
        topic=args.topic,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
