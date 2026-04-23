---
name: Multi-Channel Notify
description: >
  Send messages to 9 notification channels: Feishu, DingTalk, WeCom, Telegram,
  Email, ntfy, Bark, Slack, and generic webhooks. Config-driven via
  ~/.swarm-ai/notify-channels.yaml. Also callable by other skills and jobs.
tier: lazy
---

# Multi-Channel Notification

Send notifications to configured channels. Read INSTRUCTIONS.md for full docs.

TRIGGER: "notify", "send notification", "push to feishu", "send to dingtalk",
         "alert via telegram", "notify channels", "send to slack",
         "push notification", "send alert"
DO NOT USE: for Slack DM conversations (use s_slack), calendar events
  (use outlook-assistant), or Apple Reminders (use apple-reminders).
SIBLINGS: s_slack = Slack DM/channel conversations | s_notify = one-way push to 9 channels
