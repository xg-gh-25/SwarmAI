---
name: notify
description: "Send a one-way message to Feishu, DingTalk, WeCom, Telegram, Email, ntfy, Bark, Slack or a webhook (config in notify-channels.yaml). TRIGGER: \"notify me\", \"send a notification\", \"push to my phone\", \"通知我\". NOT FOR: conversations."
tier: lazy
python-deps: [httpx, yaml]
---

# Multi-Channel Notification

Send notifications to configured channels. Read INSTRUCTIONS.md for full docs.

TRIGGER: "notify", "send notification", "push to feishu", "send to dingtalk",
         "alert via telegram", "notify channels", "send to slack",
         "push notification", "send alert"
DO NOT USE: for Slack DM conversations, calendar events
  (use outlook-assistant), or Apple Reminders (use apple-reminders).
s_notify = one-way push to 9 channels
