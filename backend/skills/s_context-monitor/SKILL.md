---
name: Context Monitor
description: >
  Monitors context window usage and warns before session runs out of space.
  Always-on background check embedded in self-evolution's self-monitoring loop.
  TRIGGER: always active — runs automatically, not user-invoked.
  DO NOT USE: as a user-facing command. This is an internal monitoring skill.
---

# Context Monitor — Session Context Window Watchdog

Lightweight, always-on monitor that estimates context window usage and
proactively warns the user before the session runs out of space.

## How It Works

A Node.js script analyzes the current session's transcript `.jsonl` file:

1. Finds the latest transcript in `.claude/projects/`
2. Detects compaction boundaries (only counts post-compaction content)
3. Sums content chars from user/assistant/tool messages
4. Estimates tokens using mixed-language ratio (~3 chars/token)
5. Adds baseline overhead (system prompts + skills = ~40K tokens)
6. Returns structured status with level: `ok` | `warn` | `critical`

### Script

```bash
node .claude/skills/s_context-monitor/context-check.mjs
```

Output:
```json
{
  "tokensEst": 60000,
  "pct": 30,
  "level": "ok",
  "message": "Context 30% full (~60K/200K tokens). Plenty of room.",
  "details": { ... }
}
```

## Integration with Self-Evolution

This skill is NOT triggered by user commands. It integrates into the
self-evolution engine's continuous self-monitoring loop.

### When to Check

Run a context check at these moments:

1. **Every ~15 user messages** — count user turns; when count hits 15, 30, 45...
   run the check silently
2. **Before starting a large task** — if the user asks for deep-research,
   consulting-report, or any multi-step task that will generate lots of
   tool calls, check first
3. **After heavy tool sequences** — if you just did 10+ tool calls in a row
   (e.g., building a skill, browsing multiple pages), check afterward
4. **When the user asks** — "how much context left", "context usage",
   "session space"

### How to Act on Results

#### Level: `ok` (< 70%)
- Do nothing. Continue normally.
- No need to mention to the user.

#### Level: `warn` (70-84%)
- Inform the user naturally within your next response:
  > "Heads up — we've used about {pct}% of this session's context window
  > (~{K}K tokens). If we have more heavy tasks, consider saving context
  > soon."
- Continue working normally.
- Do NOT interrupt the current task to warn.
- Append the warning AFTER completing the user's current request.

#### Level: `critical` (>= 85%)
- Warn the user prominently at the START of your response:
  > "**Context alert**: Session is {pct}% full (~{K}K/{max}K tokens).
  > I recommend we save context (`save context`) and continue in a fresh
  > session to avoid losing conversation quality."
- Still complete the user's current request if possible.
- After completing, offer to run `save context` or `save activity`.

### SSE Event (for frontend)

When level is `warn` or `critical`, emit:
```
<!-- CONTEXT_MONITOR: {"pct": 75, "level": "warn", "tokensEst": 150000} -->
```

## Self-Monitoring Integration Instructions

Add this to your self-monitoring checklist (alongside stuck detection,
evolution triggers, etc.):

```
CONTEXT CHECK PROTOCOL:
- Track user message count internally
- At messages 15, 30, 45, 60... → run context-check.mjs silently
- Before multi-step tasks → run context-check.mjs
- If level != "ok" → inform user per protocol above
- If critical → strongly recommend save-context
```

## Calibration Notes

The token estimation uses these assumptions:
- **3 chars/token** — compromise between English (~4) and Chinese (~1.5)
- **40K baseline** — system prompts + all skill SKILL.md injections
- **200K window** — Claude Opus context window size

These may need tuning. If the session gets compacted earlier than expected
(suggesting underestimation), increase the baseline or decrease chars/token
ratio. Record calibration changes as O-entries in EVOLUTION.md.

## Rules

1. **Never interrupt the user's task to warn** — append warnings after
   completing the current request
2. **Critical is the only exception** — at critical level, warn at the
   start of your response, then still complete the task
3. **Don't over-check** — max 1 check per 15 user messages. Each check
   costs ~1 tool call of context.
4. **Silent when ok** — never mention context usage when level is ok
   unless the user explicitly asks
5. **Suggest save-context, not save-activity** — save-context creates a
   full handoff document; save-activity is just a log entry
