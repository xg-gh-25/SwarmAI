"""GitHub Community Engine — PUBLISH stage.

Posts comments to GitHub Issues/PRs AND Discussions with confidence gate,
engagement logging, and dry-run support.
Enforces the 4-condition quality gate before any publish.

Handles both:
  - Issues/PRs: REST API /repos/{owner}/{repo}/issues/{number}/comments
  - Discussions: GraphQL addDiscussionComment mutation

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
                if entry.get("repo") == repo and entry.get("status") == "published":
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
    if "SwarmAI" not in body:
        return False, "no_footer: missing SwarmAI attribution link"

    return True, "passed"


def _resolve_target(repo: str, number: int) -> tuple[str, str | None]:
    """Determine if target is an Issue/PR or a Discussion.

    Returns (target_type, node_id):
      - ("issue", None) — use REST API
      - ("discussion", "D_kwDO...") — use GraphQL
      - ("not_found", None) — target doesn't exist
    """
    # Try as Issue/PR first (most common case)
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{number}", "--jq", ".node_id"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return "issue", None
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return "error", str(e)

    # Try as Discussion (GraphQL)
    owner, name = repo.split("/")
    gql = json.dumps({
        "query": f'{{ repository(owner: "{owner}", name: "{name}") {{ discussion(number: {number}) {{ id }} }} }}'
    })
    try:
        proc = subprocess.run(
            ["gh", "api", "graphql", "--input", "-"],
            input=gql, capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            disc = data.get("data", {}).get("repository", {}).get("discussion")
            if disc and disc.get("id"):
                return "discussion", disc["id"]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return "not_found", None


def _publish_to_issue(repo: str, number: int, body: str) -> dict:
    """Post comment via REST API (Issues/PRs)."""
    cmd = [
        "gh", "api", f"repos/{repo}/issues/{number}/comments",
        "-X", "POST", "-f", f"body={body}", "--jq", ".id,.html_url",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode == 0:
        lines = proc.stdout.strip().split("\n")
        return {
            "published": True,
            "status": "published",
            "comment_id": int(lines[0]) if lines and lines[0].isdigit() else None,
            "comment_url": lines[1] if len(lines) > 1 else None,
        }
    return {
        "published": False,
        "status": "error",
        "error": proc.stderr[:200],
    }


def _publish_to_discussion(repo: str, number: int, body: str, discussion_id: str) -> dict:
    """Post comment via GraphQL mutation (Discussions)."""
    # Escape body for GraphQL JSON
    escaped_body = body.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    mutation = json.dumps({
        "query": f'mutation {{ addDiscussionComment(input: {{discussionId: "{discussion_id}", body: "{escaped_body}"}}) {{ comment {{ id url }} }} }}'
    })
    proc = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=mutation, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
            comment = data.get("data", {}).get("addDiscussionComment", {}).get("comment", {})
            if comment.get("url"):
                return {
                    "published": True,
                    "status": "published",
                    "comment_id": comment.get("id"),
                    "comment_url": comment["url"],
                    "target_type": "discussion",
                }
        except json.JSONDecodeError:
            pass
    return {
        "published": False,
        "status": "error",
        "error": f"graphql_error: {proc.stderr[:100] or proc.stdout[:100]}",
        "target_type": "discussion",
    }


def _log_result(result: dict) -> None:
    """Append result to engagement log."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENGAGEMENT_LOG, "a") as f:
        f.write(json.dumps(result, default=str) + "\n")


def publish_comment(
    repo: str,
    issue_number: int,
    body: str,
    confidence: int,
    topic: str,
    dry_run: bool = False,
) -> dict:
    """Publish a comment to GitHub with full quality gate.

    Automatically detects Issue/PR vs Discussion and uses the correct API.
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

    # Resolve target type (Issue/PR vs Discussion)
    try:
        target_type, node_id = _resolve_target(repo, issue_number)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"resolve_failed: {type(e).__name__}: {e}"
        print(f"✗ Cannot resolve target: {e}")
        _log_result(result)
        return result

    if target_type == "not_found":
        result["status"] = "error"
        result["error"] = f"target_not_found: {repo}#{issue_number} (not an Issue, PR, or Discussion)"
        print(f"✗ Target not found: {repo}#{issue_number}")
        _log_result(result)
        return result

    if target_type == "error":
        result["status"] = "error"
        result["error"] = f"resolve_error: {node_id}"
        print(f"✗ Error resolving target: {node_id}")
        _log_result(result)
        return result

    # Publish to the correct endpoint
    result["target_type"] = target_type
    try:
        if target_type == "issue":
            pub_result = _publish_to_issue(repo, issue_number, body)
        else:  # discussion
            pub_result = _publish_to_discussion(repo, issue_number, body, node_id)
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        print(f"✗ Timeout publishing to {target_type}")
        _log_result(result)
        return result
    except FileNotFoundError:
        result["status"] = "error"
        result["error"] = "gh_not_found: 'gh' CLI not in PATH"
        print("✗ 'gh' CLI not found in PATH")
        _log_result(result)
        return result

    result.update(pub_result)

    if result["published"]:
        print(f"✓ Published ({target_type}): {result['comment_url']}")
    else:
        print(f"✗ Failed ({target_type}): {result.get('error', 'unknown')}")

    _log_result(result)
    return result


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Publish comment to GitHub (Issues/PRs + Discussions)")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--issue", type=int, required=True, help="Issue/PR/Discussion number")
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
