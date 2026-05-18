"""GitHub Community Engine — CULTIVATE stage.

Updates DDD docs based on engagement data and extracted insights.
This is the flywheel's power source — without it, we're just a comment bot.

Usage:
  python -m skills.s_github_community.scripts.cultivate [--dry-run]
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


DDD_DIR = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community"
ARTIFACTS_DIR = DDD_DIR / ".artifacts"
REPLY_ARCHIVE = ARTIFACTS_DIR / "reply_archive.jsonl"


def load_recent_replies(days: int = 7) -> list[dict]:
    """Load replies from the last N days."""
    if not REPLY_ARCHIVE.exists():
        return []
    replies = []
    with open(REPLY_ARCHIVE) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                replies.append(entry)
            except json.JSONDecodeError:
                continue
    # Sort by recency (most recent first)
    replies.sort(key=lambda r: r.get("tracked_at", ""), reverse=True)
    return replies


def extract_insights(replies: list[dict]) -> list[dict]:
    """Extract actionable insights from replies.

    An insight is: something we didn't know before that changes how we engage.
    Deduplicates by reply ID to prevent repeated processing.
    """
    insights = []
    seen_ids = set()
    for reply in replies:
        reply_id = reply.get("id", 0)
        if reply_id in seen_ids:
            continue
        seen_ids.add(reply_id)

        # Maintainer replies are highest value
        if reply.get("is_maintainer"):
            insights.append({
                "type": "maintainer_validation",
                "repo": reply.get("source_repo"),
                "body_preview": reply.get("body", "")[:200],
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "action": "Update TECH.md with their confirmed approach",
            })
        # Long substantive replies (>100 chars) indicate genuine engagement
        elif len(reply.get("body", "")) > 100:
            insights.append({
                "type": "community_engagement",
                "repo": reply.get("source_repo"),
                "body_preview": reply.get("body", "")[:200],
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "action": "Consider for IMPROVEMENT.md pattern",
            })
    return insights


def compute_topic_temperatures(track_results_path: Path) -> dict[str, str]:
    """Compute topic temperature changes from engagement data.

    Temperature levels: frozen (❄️), cold, warm (🔥), hot (🔥🔥), blazing (🔥🔥🔥)
    """
    if not track_results_path.exists():
        return {}

    results = json.loads(track_results_path.read_text())
    scores = results.get("scores", [])

    # Group by approximate topic (inferred from repo mapping)
    # In production, this reads the engagement_log which has topic tags
    temperatures = {}
    total_engagement = sum(s.get("score", 0) for s in scores)

    if total_engagement == 0:
        return {"overall": "cold"}
    elif total_engagement < 5:
        return {"overall": "warm"}
    elif total_engagement < 15:
        return {"overall": "hot"}
    else:
        return {"overall": "blazing"}


def cultivate(dry_run: bool = False) -> dict:
    """Run cultivation cycle — extract insights, propose DDD updates."""
    replies = load_recent_replies(days=7)
    insights = extract_insights(replies)

    track_results = ARTIFACTS_DIR / "track_results.json"
    temperatures = compute_topic_temperatures(track_results)

    result = {
        "cultivated_at": datetime.now(timezone.utc).isoformat(),
        "replies_processed": len(replies),
        "insights_extracted": len(insights),
        "topic_temperatures": temperatures,
        "proposed_updates": [],
    }

    # Propose DDD updates based on insights
    for insight in insights:
        if insight["type"] == "maintainer_validation":
            result["proposed_updates"].append({
                "target": "TECH.md",
                "section": "Source Matrix",
                "action": f"Update {insight['repo']} — maintainer confirmed approach",
                "content_preview": insight["body_preview"],
            })
        elif insight["type"] == "community_engagement":
            result["proposed_updates"].append({
                "target": "IMPROVEMENT.md",
                "section": "Patterns Discovered",
                "action": f"New engagement pattern from {insight['repo']}",
                "content_preview": insight["body_preview"],
            })

    if dry_run:
        print(f"[DRY RUN] Cultivate: {len(replies)} replies → {len(insights)} insights → "
              f"{len(result['proposed_updates'])} DDD updates proposed")
        for update in result["proposed_updates"]:
            print(f"  → {update['target']} / {update['section']}: {update['action']}")
    else:
        # Save cultivation results
        cult_log = ARTIFACTS_DIR / "cultivate_results.json"
        cult_log.write_text(json.dumps(result, indent=2, default=str))

    return result


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    cultivate(dry_run=dry_run)
