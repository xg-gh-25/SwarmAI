"""Channel monitor fallback — reads Slack channels via bot_token.

Used when the primary channel-monitor (agent_task via slack-mcp) fails
due to Midway SAML auth expiry. The bot_token has channels:history scope
and doesn't depend on SSO session.

Usage:
    python -m jobs.channel_monitor_fallback

Reads bot_token from the channels table, fetches last 24h messages from
configured channels, produces a markdown report to stdout.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

from .paths import APP_DATA_DIR, DB_PATH, JOB_RESULTS_DIR

# Channel IDs to monitor — loaded from config file or defaults.
_DEFAULT_CHANNELS = {
    "C09QMPNSCTS": "#all-things-ai",
    "C08T2E4KQPJ": "#amazon-builder-genai-power-users-digest",
    "C068NQ56JMN": "#sergey-ai-notes",
}


def _load_channels_to_monitor() -> dict[str, str]:
    """Load channels from config file, fall back to defaults."""
    config_path = APP_DATA_DIR / "channel-monitor-channels.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass  # fall through to defaults
    return dict(_DEFAULT_CHANNELS)


_CHANNELS_TO_MONITOR = _load_channels_to_monitor()


def _get_bot_token() -> str:
    """Read bot_token from the channels DB table."""
    db_path = DB_PATH
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT config FROM channels WHERE channel_type = 'slack' LIMIT 1"
        ).fetchone()
        if not row:
            raise ValueError("No Slack channel configured in DB")
        config = json.loads(row[0])
        token = config.get("bot_token", "")
        if not token:
            raise ValueError("No bot_token in channel config")
        return token
    finally:
        conn.close()


def _fetch_messages(token: str, channel_id: str, hours: int = 24) -> list[dict]:
    """Fetch recent messages from a Slack channel via Web API."""
    try:
        from slack_sdk import WebClient
    except ImportError:
        print("ERROR: slack_sdk not installed", file=sys.stderr)
        return []

    client = WebClient(token=token)
    oldest = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()

    try:
        result = client.conversations_history(
            channel=channel_id,
            oldest=str(oldest),
            limit=30,
        )
        return result.get("messages", [])
    except Exception as exc:
        print(f"WARNING: Failed to fetch {channel_id}: {exc}", file=sys.stderr)
        return []


def main() -> None:
    """Run channel monitor via bot_token and print markdown report."""
    try:
        token = _get_bot_token()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "---",
        "job_id: channel-monitor",
        "job_name: Critical Slack Channel Monitor (bot_token fallback)",
        f"run_at: {datetime.now(timezone.utc).isoformat()}",
        "status: success",
        "tokens_used: 0",
        "---",
        "",
        f"# Slack Channel Monitor — {date_str} (bot_token fallback)",
        "",
        "> ⚠️ This report was generated via bot_token fallback because "
        "slack-mcp auth (Midway) was expired. Content is raw messages "
        "without AI categorization.",
        "",
    ]

    total_msgs = 0
    for channel_id, channel_name in _CHANNELS_TO_MONITOR.items():
        messages = _fetch_messages(token, channel_id)
        total_msgs += len(messages)

        lines.append(f"## {channel_name} ({len(messages)} messages)")
        lines.append("")

        if not messages:
            lines.append("_No messages in the last 24 hours._")
            lines.append("")
            continue

        # Messages come newest-first; reverse for chronological order
        for msg in reversed(messages[:20]):  # cap at 20 per channel
            text = msg.get("text", "").replace("\n", " ")[:200]
            user = msg.get("user", "unknown")
            ts = msg.get("ts", "")
            reply_count = msg.get("reply_count", 0)
            thread_info = f" (💬 {reply_count} replies)" if reply_count else ""

            lines.append(f"- **{user}**{thread_info}: {text}")

        lines.append("")

    lines.append(f"## Summary")
    lines.append(f"- Total messages: {total_msgs}")
    lines.append(f"- Channels monitored: {len(_CHANNELS_TO_MONITOR)}")
    lines.append(f"- Mode: bot_token fallback (no AI categorization)")
    lines.append("")

    # Write report — don't overwrite a better agent_task report (PE5)
    report_dir = JOB_RESULTS_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{date_str}-channel-monitor.md"
    report_content = "\n".join(lines)
    if report_path.exists():
        # Existing report from agent_task is likely higher quality (AI-categorized).
        # Append fallback as supplement instead of overwriting.
        report_path = report_dir / f"{date_str}-channel-monitor-fallback.md"
    report_path.write_text(report_content)

    # Also print to stdout for job executor
    print(report_content)


if __name__ == "__main__":
    main()
