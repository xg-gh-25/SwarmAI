# Multi-Channel Notification — Instructions

## Overview

Send messages to 9 notification channels via simple HTTP webhooks. Each channel
has its own payload format — the module handles conversion automatically.

## Supported Channels

| Channel | Format | Config Fields |
|---------|--------|---------------|
| **Feishu** (飞书) | Interactive card + markdown | `webhook_url` |
| **DingTalk** (钉钉) | Markdown | `webhook_url` |
| **WeCom** (企业微信) | Markdown | `webhook_url` |
| **Telegram** | Markdown → Bot API | `bot_token`, `chat_id` |
| **Email** | SMTP + HTML | `from`, `password`, `to`, `smtp_server`, `smtp_port` |
| **ntfy** | Markdown POST | `server_url`, `topic`, `token` (optional) |
| **Bark** | iOS push | `url` |
| **Slack** | mrkdwn blocks | `webhook_url` |
| **Webhook** | Custom template | `url`, `payload_template` |

## Configuration

Config file: `~/.swarm-ai/notify-channels.yaml`

```yaml
channels:
  feishu:
    enabled: true
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_TOKEN"
  dingtalk:
    enabled: false
    webhook_url: ""
  telegram:
    enabled: true
    bot_token: "123456:ABC-DEF"
    chat_id: "999999"
  ntfy:
    enabled: true
    server_url: "https://ntfy.sh"
    topic: "my-swarm-alerts"
  slack:
    enabled: true
    webhook_url: "https://hooks.slack.com/services/T00/B00/xxx"
  # ... other channels
```

## Usage (Agent)

When the user asks to send a notification:

1. Read the config: check which channels are enabled
2. Format the message as markdown (most channels support it)
3. Call `send_notification()`:

```python
from skills.s_notify.notify import send_notification

# Send to all enabled channels
result = send_notification(
    message="**Alert:** Server CPU > 90%",
    title="System Alert",
)

# Send to specific channels only
result = send_notification(
    message="Weekly trending digest attached",
    title="热搜周报",
    channels=["feishu", "dingtalk"],
)
```

4. Report results to user:
   - `result["feishu"]["success"]` → True/False
   - `result["feishu"]["error"]` → error message if failed

## Usage (Jobs / Other Skills)

The module is importable by any Python code in the backend:

```python
from skills.s_notify.notify import send_notification

# From a scheduled job handler:
send_notification(
    message=digest_markdown,
    title="Daily Signal Digest",
    channels=["feishu", "telegram"],
)
```

## Message Format Tips

- Use **markdown** — most channels support `**bold**`, `[links](url)`, and lists
- Keep titles short (< 50 chars) — some channels truncate
- For Feishu: supports `<font color="red">colored text</font>`
- For Slack: `**bold**` auto-converts to `*bold*` (mrkdwn)
- For Telegram: markdown auto-converts to Telegram's markdown format
- For Email: markdown renders as preformatted text + HTML alternative

## Troubleshooting

- **"No notification channels configured"** → Create `~/.swarm-ai/notify-channels.yaml`
- **Channel not sending** → Check `enabled: true` in config
- **Feishu 400 error** → Webhook URL may have expired, regenerate in group settings
- **Telegram error** → Verify bot token and chat_id (use @userinfobot to get chat_id)
- **Email auth error** → Use app-specific password, not account password
