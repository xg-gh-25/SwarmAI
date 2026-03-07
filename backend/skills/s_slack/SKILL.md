---
name: Slack
description: >
  Send, read, search, and manage Slack messages, channels, and reactions via Slack MCP server or API.
  TRIGGER: "slack", "send slack message", "check slack", "slack channel", "slack DM", "post to slack".
  DO NOT USE: for email (use Outlook Assistant), Discord, or Teams messages.
---

# Slack

**Why?** Stay on top of Slack without context-switching. Send messages, check channels, search conversations, and manage reactions -- all from your working session.

---

## Quick Start

```
"Send a message to #general: standup starts in 5 min"
"Check my unread Slack messages"
"Search Slack for the deployment runbook"
```

---

## Tool Detection

This skill adapts to available Slack tools. Detect at skill start:

### Priority 1: Slack MCP Server

Check for MCP tools matching `mcp__*slack*` patterns (e.g., `mcp__slack__send_message`, `mcp__slack__read_channel`).

If Slack MCP tools are available, use them directly -- they handle authentication automatically.

### Priority 2: Slack CLI (`slack-cli` or `slackdump`)

```bash
which slack 2>/dev/null || which slackdump 2>/dev/null
```

### Priority 3: Direct Slack API via curl

Requires `SLACK_BOT_TOKEN` or `SLACK_TOKEN` environment variable:
```bash
[ -n "$SLACK_BOT_TOKEN" ] && echo "Bot token available" || echo "No bot token"
[ -n "$SLACK_TOKEN" ] && echo "User token available" || echo "No token"
```

If nothing is available, guide the user to set up a Slack integration (see Setup section).

---

## Setup

### Option A: Slack MCP Server (Recommended)

If the user's Claude/SwarmAI environment has a Slack MCP server configured, no additional setup is needed. MCP tools will be auto-discovered.

### Option B: Slack Bot Token (API Access)

1. Go to https://api.slack.com/apps and create a new app (or use existing)
2. Under **OAuth & Permissions**, add scopes:
   - `channels:read`, `channels:history` -- Read public channels
   - `chat:write` -- Send messages
   - `reactions:read`, `reactions:write` -- Manage reactions
   - `search:read` -- Search messages
   - `users:read` -- Look up user info
   - `im:read`, `im:history` -- Read DMs
   - `groups:read`, `groups:history` -- Read private channels (if needed)
3. Install to workspace and copy the Bot User OAuth Token
4. Set environment variable:
   ```bash
   export SLACK_BOT_TOKEN=xoxb-your-token-here
   ```

---

## MCP Tool Reference

When Slack MCP tools are available, use these operations:

| Operation | Typical MCP Tool | Notes |
|-----------|-----------------|-------|
| Send message | `send_message` or `post_message` | Requires channel ID + text |
| Read channel | `read_channel` or `get_channel_history` | Returns recent messages |
| Search messages | `search_messages` | Query across workspace |
| List channels | `list_channels` | Get channel names and IDs |
| React to message | `add_reaction` | Requires channel + timestamp + emoji name |
| Get user info | `get_user_info` | Look up user by ID |
| Read thread | `get_thread` or `read_thread` | Requires channel + thread timestamp |
| Send DM | `send_message` to user's DM channel | Look up DM channel ID first |

**Important:** MCP tool names vary by server implementation. Discover actual tool names at runtime.

---

## Slack API Reference (curl fallback)

When using direct API calls with `SLACK_BOT_TOKEN`:

### Send Message

```bash
curl -s -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "C0123456789",
    "text": "Hello from SwarmAI!"
  }'
```

### Read Channel History

```bash
curl -s "https://slack.com/api/conversations.history?channel=C0123456789&limit=20" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN"
```

### Search Messages

```bash
# Requires a user token (xoxp-), not bot token, for search
curl -s "https://slack.com/api/search.messages?query=deployment+runbook&count=10" \
  -H "Authorization: Bearer $SLACK_TOKEN"
```

### List Channels

```bash
curl -s "https://slack.com/api/conversations.list?types=public_channel,private_channel&limit=100" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN"
```

### Add Reaction

```bash
curl -s -X POST https://slack.com/api/reactions.add \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "C0123456789",
    "timestamp": "1712023032.1234",
    "name": "white_check_mark"
  }'
```

### Get User Info

```bash
curl -s "https://slack.com/api/users.info?user=U0123456789" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN"
```

### List Pinned Messages

```bash
curl -s "https://slack.com/api/pins.list?channel=C0123456789" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN"
```

### Reply to Thread

```bash
curl -s -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "C0123456789",
    "thread_ts": "1712023032.1234",
    "text": "Replying in thread!"
  }'
```

---

## Workflow

### Step 1: Detect Available Tool

Run tool detection (see Tool Detection section). Use the highest-priority available method.

### Step 2: Resolve Identifiers

Slack API uses IDs, not names. When the user says "#general" or "@alice":

**Channel resolution:**
```bash
# List channels and find by name
curl -s "https://slack.com/api/conversations.list?types=public_channel&limit=200" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" | jq '.channels[] | select(.name=="general") | .id'
```

**User resolution:**
```bash
curl -s "https://slack.com/api/users.list?limit=200" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" | jq '.members[] | select(.name=="alice") | .id'
```

Cache channel/user IDs within the session to avoid repeated lookups.

### Step 3: Execute Operation

Based on user request, map to the appropriate action:

| User Says | Action |
|-----------|--------|
| "Send X to #channel" | Resolve channel ID, send message |
| "Check #channel" | Read recent history, summarize |
| "What's new in Slack?" | Check unread across key channels |
| "Search for X" | Search messages workspace-wide |
| "React with Y to that message" | Add reaction to last referenced message |
| "DM Alice about X" | Resolve user, find/open DM channel, send |
| "Post in thread" | Reply using thread_ts |
| "What are the pinned messages in #channel?" | List pins |

### Step 4: Format Output

**For reading messages**, present as:

```
#engineering (last 5 messages):

Alice (2h ago): Deployed v2.3.1 to staging
Bob (1h ago): Running integration tests now
  -> Carol (45m ago): Tests passing, LGTM
Alice (30m ago): Promoting to production
Bot (15m ago): Deploy complete: v2.3.1 is live
```

**Rules:**
- Show relative timestamps ("2h ago", "yesterday")
- Indent thread replies with ->
- Show display names, not user IDs
- Truncate long messages (show first 200 chars + "...")
- For summaries of many messages, group by topic/thread

**For sending messages**, confirm:

```
Sent to #engineering: "standup starts in 5 min"
```

---

## Common Patterns

### Morning Slack Check

When user asks "check my Slack" or "what did I miss?":

1. List channels with unread messages (if API supports)
2. Fetch recent messages from top 3-5 active channels
3. Summarize: key discussions, mentions, action items
4. Present grouped by channel

### Send + React Workflow

```
User: "Post the release notes to #releases and add a rocket emoji"
1. Send message to #releases
2. Get the message timestamp from response
3. Add :rocket: reaction to that message
```

### Channel Summary

When user asks "summarize #channel":
1. Fetch last 50-100 messages
2. Group by thread/topic
3. Summarize key discussions, decisions, and action items
4. Note any unresolved questions

---

## Message Formatting

Slack uses its own markup (mrkdwn), not standard Markdown:

| Format | Slack Syntax |
|--------|-------------|
| **Bold** | `*bold*` |
| _Italic_ | `_italic_` |
| ~~Strike~~ | `~strike~` |
| `Code` | `` `code` `` |
| Code block | ` ```code block``` ` |
| Link | `<https://example.com\|Link text>` |
| Mention user | `<@U0123456789>` |
| Mention channel | `<#C0123456789>` |
| Bullet list | Use newlines with `- ` or `* ` |

When composing messages for the user, use Slack mrkdwn format, not Markdown.

---

## Rate Limits

Slack API enforces rate limits per method:

| Tier | Rate | Common Methods |
|------|------|---------------|
| Tier 1 | 1 req/min | `search.messages` |
| Tier 2 | 20 req/min | `conversations.history`, `users.list` |
| Tier 3 | 50 req/min | `chat.postMessage`, `reactions.add` |
| Tier 4 | 100 req/min | `conversations.list` |

If rate-limited (HTTP 429), the response includes `Retry-After` header. Wait and retry.

---

## Security Guidelines

- Never log or display the Slack token
- Never send tokens in message text
- Be cautious with DMs -- confirm recipient before sending
- For sensitive messages, confirm content before posting
- Never read DMs without explicit user request

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "not_authed" | Token not set or expired. Re-export `SLACK_BOT_TOKEN` |
| "channel_not_found" | Wrong channel ID. Re-resolve from channel name |
| "not_in_channel" | Bot needs to join the channel first. Invite bot or use `conversations.join` |
| "missing_scope" | Bot token missing required OAuth scope. Update in Slack app settings |
| Rate limited (429) | Wait for `Retry-After` seconds, then retry |
| Search returns nothing | Search requires user token (`xoxp-`), not bot token (`xoxb-`) |
| Can't send DMs | Need `im:write` scope. Or open DM channel first with `conversations.open` |
| MCP tools not found | Slack MCP server not configured. Fall back to API method |

---

## Quality Rules

- Always resolve channel/user names to IDs before API calls
- Confirm before sending messages (show preview)
- For reading, show display names not raw user IDs
- Use relative timestamps for readability
- When summarizing channels, focus on decisions and action items
- Never auto-send messages -- always confirm content with user first
- Cache channel and user ID lookups within session
- Use Slack mrkdwn formatting, not standard Markdown, in message content
