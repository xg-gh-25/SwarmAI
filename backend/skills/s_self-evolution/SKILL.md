---
name: Self-Evolution Engine
description: >
  Detects capability gaps, optimization opportunities, and stuck states.
  Orchestrates evolution loops with up to 3 attempts per trigger.
  Persists results to EVOLUTION.md for cross-session growth.
  Captures corrections and competence. Auto-promotes recurring patterns.
---

## Self-Evolution Engine

Always-active self-improvement capability. Continuously self-monitor during
every session. Do NOT wait for user to ask — detect triggers proactively.

### Trigger Detection (5 types)

#### 🔴 Reactive — Something isn't working

- Tool/command error indicating a **capability gap** (not transient)
- No skill matches user's need
- CLI tool not installed (`command not found`)
- Knowledge exhausted after checking all context

**Gap types:** `missing_skill`, `missing_tool`, `knowledge_gap`
**First occurrence = transient. Recurrence = capability gap → enter evolution loop.**

#### 🟡 Proactive — You see a better way

- `known_better_approach` — EVOLUTION.md entry matches current situation
- `applicable_lesson` — MEMORY.md / DailyActivity lesson applies here

**Deferred:** Note but don't interrupt current task. Act after task completes.

#### 🔵 Stuck — Going in circles

1. `repeated_error` — Same error 2+ times consecutively
2. `rewrite_loop` — Same file edited 3+ times without progress
3. `silent_tool_chain` — 5+ tool calls with no visible progress
4. `self_revert` — Undoing your own changes
5. `cosmetic_retry` — Minor variations of a failing strategy

**When any fires → stop immediately, enter stuck escape protocol.**

#### 🟢 Correction Capture — User corrects your output

Detect: "不对", "应该是", "that's wrong", "actually it should be", user overrides
your suggestion, or user provides a better approach.

**When detected:** Assess novelty → if systematic gap → record C-entry silently.
**Skip:** Typos, formatting preferences, one-off context corrections, known facts.

#### 🟣 Task Completion Review — Post-task reflection

After significant tasks (5+ tool calls): Was this the most efficient approach?
Did I discover something reusable? Would a competence entry help next time?

**Novel insight → record O/K-entry. No insight → skip. Spend <5 seconds.**

### Priority: Stuck > Reactive > Correction > Proactive

**Session limits:** Max 3 triggers/session. 60s cooldown between same-type.
**Always return** to user's task after evolution. Summarize what changed.

### Drift Prevention (ADL Protocol)

**Stability > Interpretability > Reusability > Extensibility > Novelty**

Prohibited: complexity for appearance, unverifiable changes, sacrificing
stability for extensibility, unnecessary dependencies, frameworks when
scripts suffice.

**Self-check:** 1) More or less stable? 2) Understandable? 3) Safe to revert?
If any answer is concerning → simplify or skip.

### Session Startup Review

1. Read `.context/EVOLUTION.md`
2. Match active entries to current situation → apply + increment usage count
3. Check salience thresholds → update fading/deprecated entries
4. Scan for promotion candidates (3+ entries with same pattern)
5. No match → proceed; triggers will fire if needed

### Evolution Loop (max 3 attempts, each DIFFERENT)

| Trigger | Strategy 1 | Strategy 2 | Strategy 3 |
|---------|-----------|-----------|-----------|
| Reactive | `compose_existing` | `build_new` | `research_and_build` |
| Proactive | `optimize_in_place` | `build_replacement` | `research_best_practice_and_rebuild` |
| Stuck | `completely_different_approach` | `simplify_to_mvp` | `research_and_new_approach` |

**Cycle:** Build → Test against original task → Pass → register. Fail → next strategy.

### Capability Building

- **Skill:** `.claude/skills/s_{name}/SKILL.md` — check `auto_approve_skills`
- **Script:** `.swarm-ai/scripts/{name}.py` — chmod +x, test immediately — check `auto_approve_scripts`
- **Tool:** pip/npm/brew install — check `auto_approve_installs` (always ask if false)

### Verification

Re-attempt the original triggering task. Pass → register. Fail → next strategy.
Timeout: `verification_timeout_seconds` (default 120s). Never declare success untested.

### Entry Types (write templates in REFERENCE.md)

| Type | Section | Purpose |
|------|---------|---------|
| E-entry | Capabilities Built | New capability created |
| O-entry | Optimizations Learned | Better approach discovered |
| F-entry | Failed Evolutions | Failed attempt (always record) |
| C-entry | Corrections Captured | User correction with systematic gap |
| K-entry | Competence Learned | HOW to solve a problem class + success rate |

**Always use `locked_write.py`.** Read REFERENCE.md for exact write templates.
**Always write JSONL changelog.** See REFERENCE.md for format.

### Pattern Detection and Auto-Promotion

When 3+ entries share the same root cause / problem class:

```
Raw entries (E/O/C/K) → [≥3 times + VFM ≥50] → MEMORY.md or new skill
```

1. Identify pattern across 3+ entries
2. Score with VFM (see REFERENCE.md) — must be ≥50
3. Synthesize into actionable rule or procedure
4. Write to MEMORY.md (principle) or new skill (procedure)
5. Mark sources with `Promoted-To:` + log to JSONL changelog

### Salience-Based Entry Management

- Start salience = 1.0. Usage resets to 1.0. Decay: -0.1/week idle.
- K-entries with >80% success rate decay at half speed (-0.05/week).
- At 0.3 → `fading`. At 0.0 → `deprecated`.
- Revision ops: `supersede` (link replacement), `fork` (variant), `contest` (conflicting evidence).

### Rules (Hard Constraints)

1. **Max 3 attempts per trigger** — then Help Request and stop
2. **Verify before registering** — test against original task
3. **Always record failures** — every failed attempt gets an F-entry
4. **Always return to user's task** — summarize evolution, resume work
5. **Respect all config toggles** — check before acting
6. **Never install without checking `auto_approve_installs`**
7. **Never create skills without checking `auto_approve_skills`**
8. **Use `locked_write.py` for ALL EVOLUTION.md writes**
9. **Proactive triggers are deferred** — never interrupt active work
10. **Each attempt must be fundamentally different**
11. **Corrections require novelty filter** — systematic gaps only
12. **Promotion requires VFM ≥50** — no exceptions
13. **ADL drift prevention** — stability > everything
14. **Always write JSONL changelog** — no silent mutations

### Growth Principles

- "Reuse before you build" → check EVOLUTION.md first
- "Small fix over big system" → scripts > skills for simple tasks
- "Verify before you declare" → test before registering
- "Know when to stop" → hard stop at 3 attempts
- "If you're stuck, step back and switch" → different approach, not variation
- "Stability over novelty" → ADL priority ordering
- "Earn your promotion" → VFM ≥50 + 3 occurrences

### Context Window Monitoring

Always-on background check. Runs `s_context-monitor/context-check.mjs` to
estimate context window usage and warn the user before the session runs out.

**When to check:**
- Every ~15 user messages (count turns: 15, 30, 45...)
- Before starting multi-step tasks (deep-research, skill building, etc.)
- After 10+ consecutive tool calls
- When user asks ("context left?", "how much space?")

**How to check:**
```bash
node .claude/skills/s_context-monitor/context-check.mjs
```

**Act on result:**
- `ok` (< 70%) — silent, do nothing
- `warn` (70-84%) — append note AFTER current response:
  "Heads up — session context is ~{pct}% full. Consider saving context soon."
- `critical` (>= 85%) — warn at START of response:
  "**Context alert**: {pct}% full. Recommend `save context` + new session."
  Then still complete the task. Offer to run save-context afterward.

**Rules:** Never interrupt task to warn. Max 1 check per 15 messages.

### DailyActivity Logging

After significant evolution events, append to `Knowledge/DailyActivity/YYYY-MM-DD.md`:
- Successful capability build — what, why, where
- All-3-failed — what was attempted, why it failed

### Config & SSE Events

See REFERENCE.md for the full config table, SSE event formats, and help request template.
