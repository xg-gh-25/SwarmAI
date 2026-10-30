---
title: "The Hidden Defaults That Break Your AI Agent — Claude Code CLI's Undocumented Limits"
created: 2026-06-07
updated: 2026-10-30
status: published
---
<!-- GitHub Discussion #56: https://github.com/xg-gh-25/SwarmAI/discussions/56 -->

> **Update (2026-10-30).** Two parts of the original post turned out wrong in production, and the numbers have moved. Corrected in place below:
> - `max_turns` counts model calls **within one user message** and resets on the next one. When it is hit the CLI does **not** exit: it ends that message with `error_max_turns` and keeps reading stdin. Our ceiling is now 5000 on desktop (a runaway guard only) and 100 on channels.
> - `task_budget` does **not** move the autocompact threshold. We measured it: on a 1M model the threshold stayed at ~967K whatever `task_budget` said, so compaction never fired. The setting that moves it is `autoCompactWindow`, passed through `--settings`.

Your AI agent works perfectly for 18 minutes — tools calling, code flowing, pipeline running beautifully. Then suddenly: **"Interrupted."** All content gone. No warning. No graceful degradation.

We just spent a full debugging session on two P0 production bugs caused by **undocumented CLI defaults** in the Claude Code SDK. This post shares what we found, how we fixed it, and what every SDK consumer should know.

---

## Bug #1: The Invisible Turn Limit (`maxTurns=100`)

**Symptom:** Our autonomous pipeline (EVALUATE→BUILD→REVIEW→TEST→DELIVER) ran perfectly for ~18 minutes. At turn 101, the CLI ended the message with `is_error=True, subtype="error_max_turns"`. Frontend displayed "Interrupted" and the user lost all visible progress.

**Root cause:** Claude Code CLI defaults to `maxTurns=100`. This limit:
- Does NOT appear in `claude --help`
- Is NOT documented in the SDK README
- Ends the current message with a generic error (the process stays alive and waits for the next message)
- Counts model calls within ONE user message, not across the session

For interactive terminal use (where a human can type `/continue`), 100 turns is generous. For SDK consumers running autonomous pipelines, it's a landmine.

**The fix:**
```python
# Override the undocumented default
options = ClaudeAgentOptions(max_turns=5000)  # Desktop: runaway guard only
# Channel sessions (unattended): max_turns=100
```

We first raised desktop to 200, then 500. Both were still too low: one productive message ran 2 hours and 501 model calls before it was cut off. With autocompact keeping a long message inside the context window, the ceiling only has to stop a model that never finishes, so it is now 5000.

Don't build a "wrap up before the limit" warning on a counter of your own unless it counts the same unit. Ours counted completed responses (user messages) and ran only between messages, while the limit is hit inside a single message, so it never fired. We deleted it.

Plus graceful handling when the limit IS hit:
```python
if result.subtype == "error_max_turns":
    # Don't treat as error — emit turn_limit_reached event
    # Preserve all streamed content, let user decide to continue
    yield {"type": "turn_limit_reached", "content": result.content}
```

---

## Bug #2: The Context Compaction Trap

**Symptom:** During deep investigations, the agent would suddenly "forget" everything it had discovered mid-task. It would re-read files it already analyzed, re-ask questions it already answered, and lose its chain of reasoning.

**What we first thought:** that `task_budget` controls when the CLI runs `autoCompact` (summarizing the conversation to free space), so we raised it to 800K.

**What we measured later:** `task_budget` does not move the autocompact threshold at all. It is an API-side budget the *model* is told about ("pace your tool use and wrap up"). On a 1M model the CLI threshold stayed at ~967K regardless, and across 7 days and 170 sessions with context peaks up to ~904K, autocompact fired zero times. The CLI threshold is `autoCompactWindow - 33K` (measured buffer).

**The fix that works:**
```python
import json

# Desktop: trigger at 75% of the window; channels: a fixed 400K.
trigger = 750_000                      # 0.75 x 1M
settings = {"autoCompactWindow": trigger + 33_000}
options = ClaudeAgentOptions(settings=json.dumps(settings))
# ...and stop sending task_budget.
```

Compaction summarizes earlier context and *continues* the turn; it never cuts off in-flight work. So this is a trigger line, not a cost cap.

---

## The Broader Problem: SDK ≠ CLI

Claude Code was designed as an **interactive terminal tool**. The defaults make sense for that context:
- 100 turns? Plenty for a human asking questions
- Compaction only near the full window? Fine for a short terminal chat

But when you use Claude Code as an **SDK** (subprocess driving autonomous agents), these defaults become invisible failure modes:
- No warning before the limit hits
- Generic error types (`is_error=True`) with no machine-readable distinction
- No `--help` documentation of these parameters

### The Hidden Default Matrix

| Parameter | CLI Default | What It Does | SDK Risk |
|-----------|------------|--------------|----------|
| `maxTurns` | 100 | Ends a message after N model calls within it | Agent cut off mid-task |
| `task_budget` | — | Budget the model is told to pace against (does NOT trigger compaction) | Model wraps up early |
| `autoCompactWindow` | ~1M on a 1M model (threshold ~967K) | Autocompact fires at window − 33K | Context full before any compaction |

### Override Methods (discovered by reading source)

| Parameter | Override |
|-----------|----------|
| `maxTurns` | `ClaudeAgentOptions.max_turns` or `CLAUDE_CODE_MAX_TURNS` env |
| `task_budget` | `ClaudeAgentOptions.task_budget` or `--task-budget` flag |
| `autoCompactWindow` | `"autoCompactWindow"` in the `--settings` JSON (`ClaudeAgentOptions.settings`) |

---

## Lessons for Anyone Building on Claude Code SDK

**1. Read the source, not the docs.** The SDK's true behavior lives in the implementation. `--help` shows you flags, not defaults. The most dangerous parameters are the ones that aren't listed.

**2. Interactive defaults ≠ autonomous defaults.** Any time you're wrapping a CLI tool designed for humans into an autonomous pipeline, audit every implicit assumption. Timeouts, limits, and safety nets designed for "user can intervene" become silent killers in "unattended agent" mode.

**3. Error semantics matter.** `is_error=True` is not granular enough. Our fix distinguishes `error_max_turns` from real errors — the former is a graceful pause, not a failure. Treating all non-success as "broken" loses user trust.

**4. Persist streaming state.** If your agent streams results over 18 minutes and the transport can break, you need a checkpoint mechanism. We now persist content to `sessionStorage` every 10 seconds — if the stream breaks, a page refresh recovers everything.

**5. Test at scale, not at demo.** 100 turns or a context window filling up? You'll never hit those in a 3-minute demo. You'll hit them every time your agent does real work. Test with your actual pipelines, not toy examples.

---

## What We'd Like to See

For Anthropic / Claude Code maintainers:

1. **Document all defaults** in the SDK consumer guide — especially the ones that silently terminate or compress
2. **Machine-readable result subtypes** — `error_max_turns` is great, but make it a first-class field, not something we reverse-engineer from `subtype`
3. **SDK-specific default profiles** — interactive CLI and embedded SDK have fundamentally different use cases. A single set of defaults can't serve both.

---

## Context

We're building [SwarmAI](https://github.com/xg-gh-25/SwarmAI) — a personal AI command center that runs Claude Code as its execution engine. Our autonomous pipeline regularly runs 150+ turns per task. These bugs were invisible until production load exposed them.

The first fixes shipped in commits `e2e604ca` through `004c2c16`; the current settings live in [`backend/core/prompt_builder.py`](https://github.com/xg-gh-25/SwarmAI/blob/main/backend/core/prompt_builder.py) (`build_options`) and [`backend/core/session_healing.py`](https://github.com/xg-gh-25/SwarmAI/blob/main/backend/core/session_healing.py) (`DESKTOP_MAX_TURNS`).

---

*If you're building on Claude Code SDK and hit similar issues, check your `maxTurns` and `autoCompactWindow`. The defaults were made for a different use case.*
