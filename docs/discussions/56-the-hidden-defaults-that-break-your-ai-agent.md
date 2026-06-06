# The Hidden Defaults That Break Your AI Agent — Claude Code CLI's Undocumented Limits

> GitHub Discussion: https://github.com/xg-gh-25/SwarmAI/discussions/56

Your AI agent works perfectly for 18 minutes — tools calling, code flowing, pipeline running beautifully. Then suddenly: **"Interrupted."** All content gone. No warning. No graceful degradation.

We just spent a full debugging session on two P0 production bugs caused by **undocumented CLI defaults** in the Claude Code SDK. This post shares what we found, how we fixed it, and what every SDK consumer should know.

---

## Bug #1: The Invisible Turn Limit (`maxTurns=100`)

**Symptom:** Our autonomous pipeline (EVALUATE→BUILD→REVIEW→TEST→DELIVER) ran perfectly for ~18 minutes. At turn 101, the CLI subprocess exited with `is_error=True, subtype="error_max_turns"`. Frontend displayed "Interrupted" and the user lost all visible progress.

**Root cause:** Claude Code CLI defaults to `maxTurns=100`. This limit:
- Does NOT appear in `claude --help`
- Is NOT documented in the SDK README
- Silently terminates the agent with a generic error

For interactive terminal use (where a human can type `/continue`), 100 turns is generous. For SDK consumers running autonomous pipelines, it's a landmine.

**The fix:**
```python
# Override the undocumented default
options = ClaudeAgentOptions(max_turns=200)  # Desktop sessions
# Channel sessions keep conservative: max_turns=15
```

Plus graceful handling when the limit IS hit:
```python
if result.subtype == "error_max_turns":
    # Don't treat as error — emit turn_limit_reached event
    # Preserve all streamed content, let user decide to continue
    yield {"type": "turn_limit_reached", "content": result.content}
```

---

## Bug #2: The Context Compaction Trap (`task_budget=128K`)

**Symptom:** During deep investigations, the agent would suddenly "forget" everything it had discovered mid-task. It would re-read files it already analyzed, re-ask questions it already answered, and lose its chain of reasoning.

**Root cause:** Claude Code CLI defaults `task_budget=128K` tokens. When a single user→agent interaction chain exceeds this budget, the CLI triggers `autoCompact` — summarizing the conversation to free space. The agent loses granular context and starts over from a compressed summary.

On a **1M context window model**, compacting at 128K means you're using 12.8% of available capacity before forced compression. For complex tasks (multi-file refactors, pipeline runs, deep research), 128K is easily exceeded in a single interaction.

**The fix:**
```python
options = ClaudeAgentOptions(task_budget=800_000)  # Desktop: use the window
# Channels: 400K (unattended, cost-conscious but still generous)
```

**Why not unlimited?** Runaway cost protection. 800K still leaves 200K headroom, and users have a stop button. Channel sessions use 400K because they're unattended — `max_turns=15` is the primary safety there.

---

## The Broader Problem: SDK ≠ CLI

Claude Code was designed as an **interactive terminal tool**. The defaults make sense for that context:
- 100 turns? Plenty for a human asking questions
- 128K task budget? Fast compaction keeps the terminal responsive

But when you use Claude Code as an **SDK** (subprocess driving autonomous agents), these defaults become invisible failure modes:
- No warning before the limit hits
- Generic error types (`is_error=True`) with no machine-readable distinction
- No `--help` documentation of these parameters

### The Hidden Default Matrix

| Parameter | CLI Default | What It Does | SDK Risk |
|-----------|------------|--------------|----------|
| `maxTurns` | 100 | Hard stop after N tool calls | Agent cut off mid-task |
| `task_budget` | 128K | Trigger autoCompact | Agent forgets context |
| `autoCompact` | enabled | Summarize to free space | Loss of granular reasoning |

### Override Methods (discovered by reading source)

| Parameter | Override |
|-----------|---------|
| `maxTurns` | `ClaudeAgentOptions.max_turns` or `CLAUDE_CODE_MAX_TURNS` env |
| `task_budget` | `ClaudeAgentOptions.task_budget` or `--task-budget` flag |
| `autoCompact` | `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` env |

---

## Lessons for Anyone Building on Claude Code SDK

**1. Read the source, not the docs.** The SDK's true behavior lives in the implementation. `--help` shows you flags, not defaults. The most dangerous parameters are the ones that aren't listed.

**2. Interactive defaults ≠ autonomous defaults.** Any time you're wrapping a CLI tool designed for humans into an autonomous pipeline, audit every implicit assumption. Timeouts, limits, and safety nets designed for "user can intervene" become silent killers in "unattended agent" mode.

**3. Error semantics matter.** `is_error=True` is not granular enough. Our fix distinguishes `error_max_turns` from real errors — the former is a graceful pause, not a failure. Treating all non-success as "broken" loses user trust.

**4. Persist streaming state.** If your agent streams results over 18 minutes and the transport can break, you need a checkpoint mechanism. We now persist content to `sessionStorage` every 10 seconds — if the stream breaks, a page refresh recovers everything.

**5. Test at scale, not at demo.** 100 turns and 128K tokens? You'll never hit those in a 3-minute demo. You'll hit them every time your agent does real work. Test with your actual pipelines, not toy examples.

---

## What We'd Like to See

For Anthropic / Claude Code maintainers:

1. **Document all defaults** in the SDK consumer guide — especially the ones that silently terminate or compress
2. **Machine-readable result subtypes** — `error_max_turns` is great, but make it a first-class field, not something we reverse-engineer from `subtype`
3. **SDK-specific default profiles** — interactive CLI and embedded SDK have fundamentally different use cases. A single set of defaults can't serve both.

---

## Context

We're building [SwarmAI](https://github.com/xg-gh-25/SwarmAI) — a personal AI command center that runs Claude Code as its execution engine. Our autonomous pipeline regularly runs 150+ turns per task. These bugs were invisible until production load exposed them.

The fixes shipped in commits `e2e604ca` through `004c2c16` (6 commits, 2 rounds of adversarial review, 95 tests passing). Full technical details in our [KNOWLEDGE.md](https://github.com/xg-gh-25/SwarmAI/blob/main/.swarm-ai/SwarmWS/.context/KNOWLEDGE.md).

---

*If you're building on Claude Code SDK and hit similar issues, check your `maxTurns` and `task_budget`. The defaults were made for a different use case.*
