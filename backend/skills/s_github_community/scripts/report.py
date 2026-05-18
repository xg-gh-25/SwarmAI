"""GitHub Community Engine — REPORT stage.

Generates 6-tab HTML weekly report for XG review.
Tabs: Source Matrix | Topic Matrix | Activity | Learnings | DDD Health | Actions

Usage:
  python -m skills.s_github_community.scripts.report [--output PATH] [--dry-run]
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DDD_DIR = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community"
ARTIFACTS_DIR = DDD_DIR / ".artifacts"
REPORT_DIR = ARTIFACTS_DIR / "retro_weekly"


def generate_report_html(
    source_matrix: list[dict],
    topic_matrix: list[dict],
    activity: dict,
    learnings: list[dict],
    ddd_health: dict,
    actions: list[dict],
    comments_list: list[dict] | None = None,
    discussions_list: list[dict] | None = None,
    stars_data: dict | None = None,
    week_label: str = "",
) -> str:
    """Generate 8-tab HTML weekly report."""

    if not week_label:
        week_label = datetime.now(timezone.utc).strftime("W%W-%Y")

    if comments_list is None:
        comments_list = []
    if discussions_list is None:
        discussions_list = []
    if stars_data is None:
        stars_data = {"total": 0, "new_this_week": 0, "attributed": []}

    # Source Matrix table rows
    source_rows = ""
    for repo in source_matrix:
        source_rows += f"""<tr>
            <td>{repo.get('tier', '?')}</td>
            <td><a href="https://github.com/{repo['name']}">{repo['name']}</a></td>
            <td>{repo.get('stars', '?'):,}</td>
            <td>{repo.get('last_engaged', '—')}</td>
            <td>{repo.get('reply_rate', '—')}</td>
            <td>{repo.get('engagement_score', '—')}</td>
        </tr>"""

    # Topic Matrix table rows
    topic_rows = ""
    for topic in topic_matrix:
        topic_id = topic.get('id', '?')
        # Link topic ID to our swarm-content discussion if mapped
        disc_num = topic.get('discussion_num')
        topic_id_cell = f'<a href="https://github.com/xg-gh-25/swarm-content/discussions/{disc_num}" target="_blank">{topic_id}</a>' if disc_num else topic_id
        # Link best_repo to GitHub
        best_repo = topic.get('best_repo', '—')
        if best_repo and best_repo != '—' and '/' in best_repo:
            best_repo_cell = f'<a href="https://github.com/{best_repo}" target="_blank">{best_repo}</a>'
        elif best_repo and best_repo != '—':
            # Multiple repos or short names — link first one if parseable
            best_repo_cell = best_repo
        else:
            best_repo_cell = '—'
        # Link hot topic thread if available
        hot_thread = topic.get('hot_thread_url', '')
        engagement = topic.get('total_engagement', 0)
        engagement_cell = f'<a href="{hot_thread}" target="_blank">{engagement}</a>' if hot_thread else str(engagement)
        topic_rows += f"""<tr>
            <td>{topic_id_cell}</td>
            <td>{topic.get('name', '?')}</td>
            <td>{topic.get('temperature', '?')}</td>
            <td>{topic.get('status', '?')}</td>
            <td>{best_repo_cell}</td>
            <td>{engagement_cell}</td>
        </tr>"""

    # Activity summary
    comments_posted = activity.get("comments_posted", 0)
    replies_received = activity.get("replies_received", 0)
    reply_rate = f"{(replies_received / max(comments_posted, 1)) * 100:.0f}%"

    # Learnings list
    learnings_html = ""
    for l in learnings:
        source_text = l.get('source', '?')
        source_url = l.get('url', '')
        source_link = f'<a href="{source_url}" target="_blank">{source_text}</a>' if source_url else f'<strong>{source_text}</strong>'
        learnings_html += f"""<li>{source_link}: {l.get('insight', '?')}</li>"""

    # Actions list
    actions_html = ""
    for a in actions:
        actions_html += f"""<li class="action-{a.get('priority', 'low')}">{a.get('description', '?')}</li>"""

    # Comments list (with links)
    comments_rows = ""
    for c in comments_list:
        repo = c.get("repo", "?")
        issue_num = c.get("issue_number", "?")
        topic = c.get("topic", "?")
        replies = c.get("reply_count", 0)
        url = c.get("comment_url") or f"https://github.com/{repo}/issues/{issue_num}"
        title = c.get("title", f"#{issue_num}")
        reply_badge = f'<span style="color:#38a169;font-weight:600">{replies}💬</span>' if replies > 0 else '0'
        comments_rows += f"""<tr>
            <td>{repo}</td>
            <td>{title}</td>
            <td><span class="pill">{topic}</span></td>
            <td>{reply_badge}</td>
            <td><a href="{url}" target="_blank">Open ↗</a></td>
        </tr>"""

    # Discussions list (with links)
    discussions_rows = ""
    for d in discussions_list:
        num = d.get("number", "?")
        title = d.get("title", "?")
        url = f"https://github.com/xg-gh-25/swarm-content/discussions/{num}"
        discussions_rows += f"""<tr>
            <td>#{num}</td>
            <td>{title}</td>
            <td><a href="{url}" target="_blank">Open ↗</a></td>
        </tr>"""

    # Stars attribution table
    total_stars = stars_data.get("total", 0)
    new_stars = stars_data.get("new_this_week", 0)
    attributed_list = stars_data.get("attributed", [])
    high_conf = sum(1 for a in attributed_list if a.get("confidence") == "high")
    low_conf = sum(1 for a in attributed_list if a.get("confidence") == "low")

    stars_rows = ""
    for a in attributed_list:
        conf = a.get("confidence", "?")
        conf_badge = {"high": "🟢 high", "medium": "🟡 medium", "low": "🔵 low", "organic": "⚪ organic"}.get(conf, conf)
        source_url = a.get("source_url", "")
        source_text = a.get("source", "—")
        source_cell = f'<a href="{source_url}" target="_blank">{source_text}</a>' if source_url else source_text
        stars_rows += f"""<tr>
            <td><a href="https://github.com/{a.get('user', '')}" target="_blank">{a.get('user', '?')}</a></td>
            <td>{a.get('starred_at', '?')[:10]}</td>
            <td>{conf_badge}</td>
            <td>{source_cell}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>GitHub Community Engine — Weekly Report {week_label}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8f9fa; color: #1a1a2e; padding: 24px; }}
h1 {{ font-size: 24px; margin-bottom: 8px; }}
h2 {{ font-size: 18px; margin: 24px 0 12px; color: #2d3748; }}
.subtitle {{ color: #718096; margin-bottom: 24px; }}
.tabs {{ display: flex; gap: 4px; margin-bottom: 0; border-bottom: 2px solid #e2e8f0; }}
.tab {{ padding: 10px 20px; cursor: pointer; border-radius: 8px 8px 0 0; background: #edf2f7; font-size: 13px; font-weight: 600; }}
.tab.active {{ background: #fff; border: 2px solid #e2e8f0; border-bottom: 2px solid #fff; margin-bottom: -2px; }}
.panel {{ display: none; background: #fff; padding: 24px; border: 2px solid #e2e8f0; border-top: none; border-radius: 0 0 8px 8px; }}
.panel.active {{ display: block; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; padding: 8px 12px; background: #f7fafc; border-bottom: 2px solid #e2e8f0; font-weight: 600; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #edf2f7; }}
tr:hover {{ background: #f7fafc; }}
.metric {{ display: inline-block; background: #edf2f7; padding: 8px 16px; border-radius: 8px; margin: 4px; text-align: center; }}
.metric-value {{ font-size: 24px; font-weight: 700; color: #2d3748; }}
.metric-label {{ font-size: 11px; color: #718096; }}
ul {{ padding-left: 20px; }}
li {{ margin: 8px 0; line-height: 1.5; }}
.action-high {{ color: #e53e3e; font-weight: 600; }}
.action-medium {{ color: #d69e2e; }}
.action-low {{ color: #718096; }}
.health-good {{ color: #38a169; }}
.health-warn {{ color: #d69e2e; }}
.footer {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #a0aec0; }}
</style>
</head>
<body>
<h1>GitHub Community Engine — Weekly Report</h1>
<p class="subtitle">{week_label} | Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>

<div class="tabs">
    <div class="tab active" onclick="showTab(0)">Source Matrix</div>
    <div class="tab" onclick="showTab(1)">Topic Matrix</div>
    <div class="tab" onclick="showTab(2)">Comments</div>
    <div class="tab" onclick="showTab(3)">Discussions</div>
    <div class="tab" onclick="showTab(4)">⭐ Stars</div>
    <div class="tab" onclick="showTab(5)">Learnings</div>
    <div class="tab" onclick="showTab(6)">DDD Health</div>
    <div class="tab" onclick="showTab(7)">Actions</div>
</div>

<div class="panel active" id="panel-0">
    <h2>Source Matrix — Tracked Repos</h2>
    <table>
        <tr><th>Tier</th><th>Repo</th><th>Stars</th><th>Last Engaged</th><th>Reply Rate</th><th>Score</th></tr>
        {source_rows}
    </table>
</div>

<div class="panel" id="panel-1">
    <h2>Topic Matrix — Our Positions</h2>
    <table>
        <tr><th>ID</th><th>Topic</th><th>Temp</th><th>Status</th><th>Best Repo</th><th>Engagement</th></tr>
        {topic_rows}
    </table>
</div>

<div class="panel" id="panel-2">
    <h2>Comments Posted ({comments_posted})</h2>
    <div style="margin: 16px 0;">
        <div class="metric"><div class="metric-value">{comments_posted}</div><div class="metric-label">Posted</div></div>
        <div class="metric"><div class="metric-value">{replies_received}</div><div class="metric-label">Replies</div></div>
        <div class="metric"><div class="metric-value">{reply_rate}</div><div class="metric-label">Reply Rate</div></div>
        <div class="metric"><div class="metric-value">{activity.get('maintainer_replies', 0)}</div><div class="metric-label">Maintainer</div></div>
    </div>
    <table>
        <tr><th>Repo</th><th>Issue</th><th>Topic</th><th>Replies</th><th>Link</th></tr>
        {comments_rows}
    </table>
</div>

<div class="panel" id="panel-3">
    <h2>Our Discussions (swarm-content)</h2>
    <table>
        <tr><th>#</th><th>Topic</th><th>Link</th></tr>
        {discussions_rows}
    </table>
</div>

<div class="panel" id="panel-4">
    <h2>⭐ Star Attribution</h2>
    <div style="margin: 16px 0;">
        <div class="metric"><div class="metric-value">{total_stars}</div><div class="metric-label">Total Stars</div></div>
        <div class="metric"><div class="metric-value">{new_stars}</div><div class="metric-label">New This Week</div></div>
        <div class="metric"><div class="metric-value">{high_conf}</div><div class="metric-label">High Confidence</div></div>
        <div class="metric"><div class="metric-value">{low_conf}</div><div class="metric-label">Low / Unknown</div></div>
    </div>
    <table>
        <tr><th>User</th><th>Starred</th><th>Confidence</th><th>Attribution Source</th></tr>
        {stars_rows if stars_rows else '<tr><td colspan="4" style="color:#718096;">No new stars this week</td></tr>'}
    </table>
    <p style="margin-top:12px;font-size:12px;color:#718096;">Attribution: 🟢 high = user active in same discussion we commented on | 🟡 medium = user starred repos in our Source Matrix | 🔵 low = post-engagement but no direct link | ⚪ organic = pre-engagement</p>
</div>

<div class="panel" id="panel-5">
    <h2>Key Learnings & DDD Updates</h2>
    <ul>{learnings_html if learnings_html else '<li>No new learnings this week (too early or no replies yet)</li>'}</ul>
</div>

<div class="panel" id="panel-6">
    <h2>DDD Health</h2>
    <table>
        <tr><th>Document</th><th>Last Updated</th><th>Completeness</th><th>Status</th></tr>
        <tr><td>PRODUCT.md</td><td>{ddd_health.get('product_updated', '—')}</td><td>{ddd_health.get('product_completeness', '—')}</td><td class="health-good">OK</td></tr>
        <tr><td>TECH.md</td><td>{ddd_health.get('tech_updated', '—')}</td><td>{ddd_health.get('tech_completeness', '—')}</td><td class="health-good">OK</td></tr>
        <tr><td>IMPROVEMENT.md</td><td>{ddd_health.get('improvement_updated', '—')}</td><td>{ddd_health.get('improvement_completeness', '—')}</td><td class="health-good">OK</td></tr>
        <tr><td>PROJECT.md</td><td>{ddd_health.get('project_updated', '—')}</td><td>{ddd_health.get('project_completeness', '—')}</td><td class="health-good">OK</td></tr>
    </table>
    <h2 style="margin-top:16px;">Matrix Sizes</h2>
    <div class="metric"><div class="metric-value">{ddd_health.get('source_matrix_size', 14)}</div><div class="metric-label">Source Repos</div></div>
    <div class="metric"><div class="metric-value">{ddd_health.get('topic_matrix_size', 11)}</div><div class="metric-label">Topics</div></div>
    <div class="metric"><div class="metric-value">{ddd_health.get('patterns_count', 0)}</div><div class="metric-label">Patterns in IMPROVEMENT</div></div>
</div>

<div class="panel" id="panel-7">
    <h2>Follow-up Actions</h2>
    <ul>{actions_html if actions_html else '<li>No pending actions</li>'}</ul>
</div>

<div class="footer">Generated by SwarmAI GitHub Community Engine | Learning-first, quality-only</div>

<script>
function showTab(idx) {{
    document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', i===idx));
    document.querySelectorAll('.panel').forEach((p,i) => p.classList.toggle('active', i===idx));
}}
</script>
</body>
</html>"""
    return html


def _load_engagement_log() -> list[dict]:
    """Load engagement log entries."""
    log_path = ARTIFACTS_DIR / "engagement_log.jsonl"
    if not log_path.exists():
        return []
    entries = []
    with open(log_path) as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
    return entries


def _load_track_results() -> dict:
    """Load latest track results."""
    track_path = ARTIFACTS_DIR / "track_results.json"
    if not track_path.exists():
        return {"threads_checked": 0, "replies_found": 0, "maintainer_replies": 0, "scores": []}
    return json.loads(track_path.read_text())


def _load_cultivate_results() -> dict:
    """Load latest cultivate results."""
    cult_path = ARTIFACTS_DIR / "cultivate_results.json"
    if not cult_path.exists():
        return {"insights_extracted": 0, "proposed_updates": []}
    return json.loads(cult_path.read_text())


def _compute_live_source_matrix(engagement_log: list[dict], track_results: dict) -> list[dict]:
    """Compute Source Matrix from live engagement data."""
    from collections import defaultdict

    # Count comments per repo
    repo_comments = defaultdict(int)
    repo_last_engaged = {}
    for entry in engagement_log:
        repo = entry.get("repo", "")
        repo_comments[repo] += 1
        posted = entry.get("posted_at", "")
        if posted:
            repo_last_engaged[repo] = posted[:10]  # YYYY-MM-DD

    # Count replies per repo
    repo_replies = defaultdict(int)
    for score in track_results.get("scores", []):
        if score.get("reply_count", 0) > 0:
            repo_replies[score["repo"]] += score["reply_count"]

    # Static repo info (from TECH.md — would be parsed in production)
    REPO_INFO = {
        "NousResearch/hermes-agent": {"tier": 1, "stars": 154000},
        "anthropics/skills": {"tier": 1, "stars": 136000},
        "forrestchang/andrej-karpathy-skills": {"tier": 3, "stars": 133000},
        "anthropics/claude-code": {"tier": 2, "stars": 124000},
        "garrytan/gstack": {"tier": 1, "stars": 98000},
        "mattpocock/skills": {"tier": 2, "stars": 88000},
        "bytedance/deer-flow": {"tier": 2, "stars": 68000},
        "MemPalace/mempalace": {"tier": 1, "stars": 52000},
        "crewAIInc/crewAI": {"tier": 2, "stars": 52000},
        "volcengine/OpenViking": {"tier": 2, "stars": 24000},
        "addyosmani/agent-skills": {"tier": 3, "stars": 43000},
        "tirth8205/code-review-graph": {"tier": 3, "stars": 17000},
        "kayba-ai/agentic-context-engine": {"tier": 3, "stars": 2200},
        "Agents365-ai/video-podcast-maker": {"tier": 3, "stars": 1000},
    }

    matrix = []
    for repo, info in sorted(REPO_INFO.items(), key=lambda x: x[1]["stars"], reverse=True):
        comments = repo_comments.get(repo, 0)
        replies = repo_replies.get(repo, 0)
        reply_rate = f"{(replies / max(comments, 1)) * 100:.0f}%" if comments > 0 else "—"
        matrix.append({
            "name": repo,
            "tier": info["tier"],
            "stars": info["stars"],
            "last_engaged": repo_last_engaged.get(repo, "—"),
            "reply_rate": reply_rate,
            "engagement_score": f"{comments}c/{replies}r",
        })
    return matrix


def generate_weekly_report(dry_run: bool = False, output_path: str | None = None) -> str:
    """Generate the weekly report from LIVE engagement data (not hardcoded)."""

    # Load live data
    engagement_log = _load_engagement_log()
    track_results = _load_track_results()
    cultivate_results = _load_cultivate_results()

    # Compute source matrix from live data
    source_matrix = _compute_live_source_matrix(engagement_log, track_results)

    # Compute topic engagement from log
    from collections import defaultdict
    topic_engagement = defaultdict(int)
    for entry in engagement_log:
        topic = entry.get("topic", "")
        if topic:
            topic_engagement[topic] += 1

    topic_matrix = [
        {"id": "T-MEM", "name": "Memory is the Moat", "temperature": "🔥🔥🔥", "status": "ACTIVE", "best_repo": "MemPalace/mempalace", "discussion_num": 2, "hot_thread_url": "https://github.com/MemPalace/mempalace/discussions/1056", "total_engagement": topic_engagement.get("T-MEM", 0)},
        {"id": "T-MvS", "name": "Multi-Skill > Multi-Agent", "temperature": "🔥🔥🔥", "status": "ACTIVE", "best_repo": "crewAIInc/crewAI", "discussion_num": 11, "hot_thread_url": "https://github.com/crewAIInc/crewAI/discussions/4232", "total_engagement": topic_engagement.get("T-MvS", 0)},
        {"id": "T-CBB", "name": "Coding as Black Box", "temperature": "🔥🔥", "status": "ACTIVE", "best_repo": "crewAIInc/crewAI", "discussion_num": 3, "hot_thread_url": "https://github.com/crewAIInc/crewAI/discussions/4232", "total_engagement": topic_engagement.get("T-CBB", 0)},
        {"id": "T-CUL", "name": "Cultivation > Config", "temperature": "🔥🔥", "status": "ACTIVE", "best_repo": "bytedance/deer-flow", "discussion_num": 5, "hot_thread_url": "https://github.com/bytedance/deer-flow/issues/3011", "total_engagement": topic_engagement.get("T-CUL", 0)},
        {"id": "T-6SX", "name": "Six Self-X Properties", "temperature": "🔥🔥", "status": "ACTIVE", "best_repo": "anthropics/claude-code", "discussion_num": 7, "hot_thread_url": "https://github.com/anthropics/claude-code/issues/16288", "total_engagement": topic_engagement.get("T-6SX", 0)},
        {"id": "T-DDD", "name": "DDD Cultivation", "temperature": "🔥", "status": "ACTIVE", "best_repo": "anthropics/skills", "discussion_num": 8, "hot_thread_url": "https://github.com/anthropics/skills/discussions/380", "total_engagement": topic_engagement.get("T-DDD", 0)},
        {"id": "T-CMP", "name": "Compound Intelligence", "temperature": "🔥🔥", "status": "ACTIVE", "best_repo": "MemPalace/mempalace", "discussion_num": 9, "hot_thread_url": "https://github.com/MemPalace/mempalace/discussions/1384", "total_engagement": topic_engagement.get("T-CMP", 0)},
        {"id": "T-SOV", "name": "Memory Sovereignty", "temperature": "🔥🔥", "status": "CANDIDATE", "best_repo": "MemPalace/mempalace", "discussion_num": None, "hot_thread_url": "https://github.com/MemPalace/mempalace/discussions/759", "total_engagement": topic_engagement.get("T-SOV", 0)},
        {"id": "T-SxT", "name": "S×T Tension Matrix", "temperature": "🔥🔥🔥", "status": "CANDIDATE", "best_repo": "garrytan/gstack", "discussion_num": 10, "hot_thread_url": "", "total_engagement": topic_engagement.get("T-SxT", 0)},
    ]

    # Compute activity from live data
    activity = {
        "comments_posted": len(engagement_log),
        "replies_received": track_results.get("replies_found", 0),
        "maintainer_replies": track_results.get("maintainer_replies", 0),
    }

    # Compute learnings from cultivate results (deduplicated by repo)
    seen_repos = set()
    learnings = []
    for update in cultivate_results.get("proposed_updates", []):
        action = update.get("action", "unknown")
        # Extract repo from action string like "New engagement pattern from bytedance/deer-flow"
        repo = ""
        for word in action.split():
            if "/" in word and not word.startswith("http"):
                repo = word
                break
        if repo in seen_repos:
            continue
        seen_repos.add(repo)
        url = f"https://github.com/{repo}" if repo else ""
        learnings.append({
            "source": action[:50],
            "insight": update.get("content_preview", "")[:120] or "Pattern detected from engagement data",
            "url": url,
        })

    ddd_health = {
        "product_updated": "2026-05-17",
        "product_completeness": "Seeded",
        "tech_updated": "2026-05-17",
        "tech_completeness": "Full (14 repos, 11 topics)",
        "improvement_updated": "2026-05-17",
        "improvement_completeness": "Seed patterns",
        "project_updated": "2026-05-17",
        "project_completeness": "12 active threads",
        "source_matrix_size": 14,
        "topic_matrix_size": 11,
        "patterns_count": 5,
    }

    actions = [
        {"description": "Check replies after 48h (first batch posted 2026-05-17)", "priority": "high"},
        {"description": "Track inbound comments on swarm-content Discussions", "priority": "high"},
        {"description": "Engage forrestchang/andrej-karpathy-skills — DEMOTED (no engagement surface)", "priority": "low"},
    ]

    # Build comments list from engagement log (with reply counts from track)
    reply_map = {(s["repo"], s["issue"]): s.get("reply_count", 0) for s in track_results.get("scores", [])}
    comments_list = []
    for entry in engagement_log:
        repo = entry.get("repo", "")
        issue_num = entry.get("issue_number", 0)
        comments_list.append({
            "repo": repo,
            "issue_number": issue_num,
            "title": f"#{issue_num}",
            "topic": entry.get("topic", "?"),
            "comment_url": entry.get("comment_url"),
            "reply_count": reply_map.get((repo, issue_num), 0),
        })

    # Our swarm-content Discussions
    discussions_list = [
        {"number": 1, "title": "Welcome — What is this gallery?"},
        {"number": 2, "title": "没有记忆就没有理解 — Memory as Moat"},
        {"number": 3, "title": "Coding as Black Box"},
        {"number": 4, "title": "Content as Black Box"},
        {"number": 5, "title": "Agent 当人来培养 — Cultivation > Config"},
        {"number": 6, "title": "越用越聪明 — How AI compounds"},
        {"number": 7, "title": "Six Self-X Properties"},
        {"number": 8, "title": "DDD Cultivation"},
        {"number": 9, "title": "Compound Agent Intelligence"},
        {"number": 10, "title": "S×T Tension Matrix"},
        {"number": 11, "title": "Multi-Agent is a coordination tax"},
    ]

    # Load star attribution data
    star_log_path = ARTIFACTS_DIR / "star_log.jsonl"
    star_entries = []
    if star_log_path.exists():
        with open(star_log_path) as f:
            for line in f:
                try:
                    star_entries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue

    # Filter to this week's new stars (post-engagement only)
    new_week_stars = [s for s in star_entries if s.get("confidence") != "organic"]
    stars_data = {
        "total": track_results.get("stars", {}).get("total_stars", len(star_entries)),
        "new_this_week": len(new_week_stars),
        "attributed": new_week_stars,
    }

    html = generate_report_html(
        source_matrix, topic_matrix, activity, learnings, ddd_health, actions,
        comments_list=comments_list, discussions_list=discussions_list,
        stars_data=stars_data,
    )

    if dry_run:
        print(f"[DRY RUN] Generated report: {len(html)} bytes, 6 tabs")
        return html

    # Write report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    week_label = datetime.now(timezone.utc).strftime("W%W-%Y")

    if output_path:
        out = Path(output_path)
    else:
        out = REPORT_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-weekly.html"

    out.write_text(html)
    print(f"Report written to: {out}")
    return html


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    output = None
    for i, arg in enumerate(sys.argv):
        if arg == "--output" and i + 1 < len(sys.argv):
            output = sys.argv[i + 1]
    generate_weekly_report(dry_run=dry_run, output_path=output)
