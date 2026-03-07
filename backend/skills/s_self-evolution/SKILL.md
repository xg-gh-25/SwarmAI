---
name: Self-Evolution Engine
description: >
  Detects capability gaps, optimization opportunities, and stuck states.
  Orchestrates evolution loops with up to 3 attempts per trigger.
  Persists results to EVOLUTION.md for cross-session growth.
---

## Self-Evolution Engine

You are equipped with a self-evolution capability. These instructions are always
active — loaded into every session via built-in skill projection. You continuously
self-monitor using these rules. Do NOT wait for the user to ask for evolution —
detect triggers proactively and act.

### When to Use

Always. This skill is not triggered by a user command. You apply these rules
throughout every session as you work on tasks. Self-monitor your own behavior,
tool outputs, and progress to detect evolution triggers described below.

### Trigger Detection Rules

Three trigger types govern when evolution activates:

#### 🔴 Reactive — Something isn't working

Detect these signals during task execution:

- A tool call or command returned an error indicating a **capability gap**
  (not a transient network timeout or rate limit)
- You cannot find a skill that matches what the user needs
- A required CLI tool is not installed (`command not found`, `No such file`)
- You lack knowledge to complete the task after exhausting available context

**Gap types to classify:**
- `missing_skill` — No existing skill covers the needed capability
- `missing_tool` — A CLI tool or package is not available in the environment
- `knowledge_gap` — You lack domain knowledge after exhausting context files

**Transient vs capability gap:** Treat the first occurrence of an error as
transient. If the same type of failure recurs, escalate to a capability gap
and enter the reactive evolution loop.

#### 🟡 Proactive (MVP) — You see a better way

Detect these signals while working:

- `known_better_approach` — You recognize a pattern from EVOLUTION.md that
  describes a better approach for the current task. An existing E-entry or
  O-entry has a `When to Use` / `When Applicable` field matching your situation.
- `applicable_lesson` — You read MEMORY.md or DailyActivity and find a lesson
  that directly applies to the current task.

**Important:** Proactive triggers are deferred. Note the opportunity but do NOT
interrupt the user's current task. Execute the proactive evolution after the
current task completes.

#### 🔵 Stuck — You're going in circles

Self-monitor for these 5 signals:

1. **`repeated_error`** — Same error output appearing 2+ times consecutively
2. **`rewrite_loop`** — You've edited the same file 3+ times without progress
3. **`silent_tool_chain`** — 5+ consecutive tool calls with no user-visible
   progress or output
4. **`self_revert`** — You are reverting your own changes (undoing a previous edit)
5. **`cosmetic_retry`** — You're trying cosmetic variations of a failing strategy
   (same approach with minor differences that don't address the root cause)

When any stuck signal fires, stop immediately and enter the stuck escape protocol.

### Priority and Cooldown

When multiple triggers fire simultaneously, prioritize:

1. **Stuck** (highest) — Escape takes precedence over everything
2. **Reactive** — Capability gaps block task completion
3. **Proactive** — Optimizations can wait

**Session limits:**
- Maximum **3 evolution triggers** per session (check config `max_triggers_per_session`)
- **60-second cooldown** between same-type triggers (check config `same_type_cooldown_seconds`)
- After 3 triggers in a session, stop triggering. Note remaining opportunities
  in EVOLUTION.md for the next session.

**Proactive deferral:** Note the opportunity but defer action until the current
user task is complete. Never interrupt active work for an optimization.

**Always return** to the user's original task after evolution completes.
Summarize what you evolved and continue where you left off.

### Before You Start (Session Startup Review)

At the beginning of each session, before working on any user task:

1. Read `.context/EVOLUTION.md`
2. Check if a known solution matches your current situation — compare against
   `When to Use` / `When Applicable` fields in active entries
3. If a match exists → apply it, increment usage count:
   ```bash
   python backend/scripts/locked_write.py \
     --file .context/EVOLUTION.md \
     --section "Capabilities Built" \
     --increment-field "Usage Count" \
     --entry-id "E{NNN}"
   ```
   Done — no evolution loop needed.
4. If no match → proceed normally; evolution triggers will fire if needed
5. Review entries for deprecation: if any active entry has a date >30 days ago
   and hasn't been used recently, mark as deprecated:
   ```bash
   python backend/scripts/locked_write.py \
     --file .context/EVOLUTION.md \
     --section "Capabilities Built" \
     --set-field "Status" --value "deprecated" \
     --entry-id "E{NNN}"
   ```

### Evolution Loop Protocol (max 3 attempts)

Each attempt MUST try a **different** approach. Never repeat a strategy.

#### For Reactive Gaps (capability missing)

1. **`compose_existing`** — Combine existing skills, tools, or EVOLUTION.md
   entries to solve the problem. Check EVOLUTION.md first.
2. **`build_new`** — Create a new Skill (SKILL.md in `.claude/skills/s_xxx/`)
   or Script (in `.swarm-ai/scripts/`).
3. **`research_and_build`** — Use WebFetch to research the problem, then build
   an informed solution based on what you learn.

#### For Proactive Improvements (better way exists)

1. **`optimize_in_place`** — Improve the current approach without creating new
   files. Refactor, simplify, or apply a known pattern.
2. **`build_replacement`** — Create a cleaner replacement script or skill.
3. **`research_best_practice_and_rebuild`** — Research the best practice for
   this type of task, then rebuild using that approach.

#### For Stuck Escape (no progress)

1. **`completely_different_approach`** — Try a fundamentally different tool,
   language, or method. Not a variation — a completely different angle.
2. **`simplify_to_mvp`** — Reduce to the simplest possible version that works.
   Strip away complexity until you have a minimal working solution.
3. **`research_and_new_approach`** — Research from scratch using WebFetch, then
   try a completely new approach informed by what you find.

**Each attempt follows this cycle:**
Build the capability → Test it against the original task → Pass → register
in EVOLUTION.md. Fail → move to the next strategy.

### Capability Building Instructions

#### Creating a Skill

1. Create directory `.claude/skills/s_{name}/`
2. Write `SKILL.md` with YAML frontmatter (`name`, `description`) and a
   markdown body containing "When to Use", "How to", and "Rules" sections
3. Skills are auto-loaded by the existing skill system in future sessions
4. Check config `auto_approve_skills` — if false, ask the user before creating

#### Creating a Script

1. Write to `.swarm-ai/scripts/{name}.py` (or `.sh`, `.js`)
2. Make executable: `chmod +x .swarm-ai/scripts/{name}.py`
3. Test the script immediately after creation
4. Check config `auto_approve_scripts` — if false, ask the user before creating

#### Installing a Tool

1. Use `pip install --user {pkg}` for Python packages
2. Use `npm install -g {pkg}` for Node.js tools
3. Use `brew install {pkg}` for system tools (macOS)
4. Check config `auto_approve_installs` — if false, **always** ask the user
   before installing any package

### Verification Protocol

After building any capability, you MUST verify it works:

1. Re-attempt the **original task** that triggered the evolution
2. If verification succeeds → register in EVOLUTION.md, continue with user's task
3. If verification fails → treat as a failed attempt, move to the next strategy
4. Respect config `verification_timeout_seconds` (default 120s) — if verification
   takes too long, stop and record the attempt as a failure

Never declare a capability as successful without testing it against the original
triggering task.

### EVOLUTION.md Write Protocol

**Always** use `locked_write.py` for writes to `.context/EVOLUTION.md`. Never
edit the file directly.

#### Appending a new E-entry (Capability Built)

```bash
python backend/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Capabilities Built" \
  --append "### E{NNN} | {trigger_type} | {capability_type} | {YYYY-MM-DD}
- **Name**: {name}
- **Description**: {description}
- **Location**: {file_path}
- **Usage**: {usage_instructions}
- **When to Use**: {matching_criteria}
- **Principle Applied**: {principle_name}
- **Usage Count**: 0
- **Status**: active
- **Auto Generated**: true"
```

#### Appending a new O-entry (Optimization Learned)

```bash
python backend/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Optimizations Learned" \
  --append "### O{NNN} | {YYYY-MM-DD}
- **Optimization**: {description}
- **Context**: {when_this_applies}
- **Before**: {old_approach}
- **After**: {new_approach}
- **When Applicable**: {matching_criteria}"
```

#### Appending a new F-entry (Failed Evolution)

```bash
python backend/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Failed Evolutions" \
  --append "### F{NNN} | {trigger_type} | {YYYY-MM-DD}
- **Attempted**: {what_was_tried}
- **Strategy**: {strategy_name}
- **Why Failed**: {failure_reason}
- **Lesson**: {what_was_learned}
- **Alternative**: {suggested_alternative}"
```

#### ID Generation

Read the last ID in each section and increment:
- Capabilities Built: E001 → E002 → E003 ...
- Optimizations Learned: O001 → O002 → O003 ...
- Failed Evolutions: F001 → F002 → F003 ...

If a section is empty (no entries yet), start with 001 (E001, O001, or F001).

### SSE Event Emission

Output structured markers in your text for the backend to parse and emit as
SSE events to the frontend. Use this exact format:

```
<!-- EVOLUTION_EVENT: {"event": "evolution_start", "data": {"triggerType": "reactive", "description": "...", "strategySelected": "compose_existing", "attemptNumber": 1, "principleApplied": "Reuse before you build"}} -->
```

**Emit events for:**

- **`evolution_start`** — When beginning an evolution attempt. Include:
  `triggerType`, `description`, `strategySelected`, `attemptNumber`,
  `principleApplied`

- **`evolution_result`** — When an attempt completes (success or failure).
  Include: `outcome` ("success" or "failure"), `durationMs`,
  `capabilityCreated` (E-ID/O-ID if success), `evolutionId`,
  `failureReason` (if failed)

- **`evolution_stuck_detected`** — When a stuck state is detected. Include:
  `detectedSignals` (array of signal names), `triedSummary`,
  `escapeStrategy`

- **`evolution_help_request`** — When all 3 attempts fail. Include:
  `taskSummary`, `triggerType`, `attempts` (array of {strategy,
  failureReason}), `suggestedNextStep`

### Help Request Format

When all 3 attempts fail for a trigger, output a structured help request to
the user:

```
I need your help. Here's what happened:

**Original Task**: {what you were trying to do}
**Trigger**: {trigger type} — {description}

**Attempts**:
1. Strategy: {strategy_1} — Failed because: {reason_1}
2. Strategy: {strategy_2} — Failed because: {reason_2}
3. Strategy: {strategy_3} — Failed because: {reason_3}

**My Assessment**: {why the task is blocked}
**Suggested Next Step**: {what the user could do}
```

Also emit an `evolution_help_request` SSE event (see above) so the frontend
can render this as a structured UI element.

### Growth Principles Reference

Reference `.context/GROWTH_PRINCIPLES.md` when making evolution decisions.
Record which principle guided each decision in the EVOLUTION.md entry.

Key principles to apply:

- **"Reuse before you build"** → Always check EVOLUTION.md first for existing
  capabilities before creating something new
- **"Small fix over big system"** → Prefer scripts over skills for simple tasks;
  prefer a 10-line fix over a new framework
- **"Verify before you declare"** → Always test before registering a capability
  as successful in EVOLUTION.md
- **"Know when to stop"** → Hard stop at 3 attempts. Do not exceed this.
- **"If you're stuck, step back and switch"** → Fundamentally different approach,
  not a cosmetic variation of the same failing strategy

### Config Awareness

Before acting on any evolution trigger, check `evolution.*` config values.
Read config via the evolution section in the system prompt or config context.

| Config Key | Effect |
|------------|--------|
| `enabled` | If false, do not trigger any evolution at all |
| `auto_approve_skills` | If false, ask user before creating skills |
| `auto_approve_scripts` | If false, ask user before creating scripts |
| `auto_approve_installs` | If false, ask user before installing packages |
| `proactive_enabled` | If false, skip proactive trigger detection entirely |
| `stuck_detection_enabled` | If false, skip stuck detection entirely |
| `max_triggers_per_session` | Maximum evolution triggers allowed per session (default 3) |
| `same_type_cooldown_seconds` | Minimum seconds between same-type triggers (default 60) |
| `max_retries` | Maximum attempts per trigger (default 3) |
| `verification_timeout_seconds` | Max time for verification step (default 120) |
| `max_active_entries` | Cap on active EVOLUTION.md entries (default 30) |
| `deprecation_days` | Days of inactivity before auto-deprecation (default 30) |

If `enabled` changes from true to false during a session, complete any
in-progress evolution attempt but do not initiate new ones.

### DailyActivity Logging

After significant evolution events, write a summary to the DailyActivity log
at `Knowledge/DailyActivity/YYYY-MM-DD.md`:

- **Successful capability build** — Log what was built, why, and where it lives
- **All-3-failed** — Log what was attempted, why it failed, and the help request

Use the same DailyActivity format as the save-activity skill. Append to the
existing file if it exists; create it if it doesn't.

### Rules (Hard Constraints)

These rules are non-negotiable. Violating any of them is a bug.

1. **Maximum 3 evolution attempts per trigger** — NEVER exceed this. After 3
   failures, generate a Help Request and stop.
2. **Always verify before registering** — Never record a capability as
   successful in EVOLUTION.md without testing it against the original task.
3. **Always record failures** — Every failed attempt gets an F-entry in
   EVOLUTION.md. Do not silently discard failures.
4. **Always return to the user's original task** — After evolution completes
   (success or failure), resume the user's task. Summarize what happened.
5. **Respect all config toggles** — Check config before acting. If a feature
   is disabled, do not use it.
6. **Never install packages without checking `auto_approve_installs`** — If
   the config says false, ask the user first. Always.
7. **Never create skills without checking `auto_approve_skills`** — Same rule.
   Ask if config requires it.
8. **Use `locked_write.py` for ALL writes to EVOLUTION.md** — Never edit the
   file directly. Concurrent safety depends on this.
9. **Proactive triggers are deferred** — Do not interrupt the user's current
   task for an optimization. Note it and act later.
10. **Each attempt must be fundamentally different** — Cosmetic variations of
    a failing strategy count as stuck behavior, not as a new attempt.
