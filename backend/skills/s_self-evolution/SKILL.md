---
name: Self-Evolution Engine
description: >
  Detects capability gaps, optimization opportunities, and stuck states.
  Orchestrates evolution loops with up to 3 attempts per trigger.
  Persists results to EVOLUTION.md for cross-session growth.
  Captures corrections and competence. Auto-promotes recurring patterns.
---

## Self-Evolution Engine

Always-active self-improvement. Detect triggers proactively — don't wait for user.

### Trigger Detection

#### 🔴 Reactive — Something isn't working

- Tool/command error = **capability gap** (not transient)
- No skill matches need / CLI tool missing / knowledge exhausted
- Gap types: `missing_skill`, `missing_tool`, `knowledge_gap`
- First occurrence = transient. Recurrence = capability gap → evolution loop.

#### 🟡 Proactive — Better way exists

- EVOLUTION.md entry matches current situation
- MEMORY.md / DailyActivity lesson applies
- **Deferred:** Note but don't interrupt current task. Act after task completes.

#### 🔵 Stuck — Going in circles

1. `repeated_error` — Same error 2+ times consecutively
2. `rewrite_loop` — Same file edited 3+ times without progress
3. `silent_tool_chain` — 5+ tool calls with no visible progress
4. `self_revert` — Undoing your own changes
5. `cosmetic_retry` — Minor variations of a failing strategy

**When any fires → stop immediately, enter stuck escape protocol.**

#### 🟢 Correction Capture — User corrects your output

Detect: "不对", "应该是", "that's wrong", "actually it should be", user overrides.
Systematic gap → C-entry. Skip: typos, formatting preferences, one-off context.

#### 🟣 Task Completion Review — Post-task reflection

After 5+ tool calls: most efficient approach? Reusable insight? → O/K-entry if novel.

### Priority: Stuck > Reactive > Correction > Proactive

**Limits:** Max 3 triggers/session. 60s cooldown between same-type.
**Always return** to user's task after evolution. Summarize what changed.

### Trigger Counter (compaction-safe)

The in-context trigger count is lost when the context window compacts.
Persist it to a file so it survives:

- **Before each evolution trigger:** read the counter file:
  ```bash
  cat /tmp/swarm-evo-triggers 2>/dev/null || echo "0"
  ```
- If count ≥ `max_triggers_per_session` (default 3) → **skip**, do not evolve.
- **After each trigger fires:** increment and write back:
  ```bash
  echo "$(($(cat /tmp/swarm-evo-triggers 2>/dev/null || echo 0) + 1))" > /tmp/swarm-evo-triggers
  ```
- File is in `/tmp/` so it auto-cleans on reboot (session boundary).

### Drift Prevention (ADL Protocol)

**Stability > Interpretability > Reusability > Extensibility > Novelty**

Self-check: 1) More stable? 2) Understandable? 3) Safe to revert? If no → simplify/skip.

### Session Startup

1. Read `.context/EVOLUTION.md`
2. Match active entries to current situation → apply + increment usage count
3. Check salience: entries idle >30 days → mark `deprecated`
4. Scan for promotion candidates (3+ entries with same pattern)
5. No match → proceed; triggers fire if needed

### Evolution Loop (max 3 attempts, each DIFFERENT)

| Trigger | Try 1 | Try 2 | Try 3 |
|---------|-------|-------|-------|
| Reactive | `compose_existing` | `build_new` | `research_and_build` |
| Proactive | `optimize_in_place` | `build_replacement` | `research_best_practice` |
| Stuck | `completely_different` | `simplify_to_mvp` | `research_new_approach` |

**Cycle:** Build → Test against original task → Pass → register. Fail → next.

### Building Capabilities

- **Skill:** `.claude/skills/s_{name}/SKILL.md` — check `auto_approve_skills` first
- **Script:** `.swarm-ai/scripts/{name}.py` — chmod +x, test immediately
- **Tool install:** brew/pip/npm — **always** check `auto_approve_installs` first

### Verification

Re-attempt the original triggering task. Pass → register. Fail → next strategy.
Never declare a capability successful without testing it.

---

## Config

Defaults are hardcoded below. User can override in `~/.swarm-ai/config.json`
under the `"evolution"` key. If the key is absent or empty, defaults apply.

| Key | Default | Effect |
|-----|---------|--------|
| `enabled` | `true` | `false` = disable all evolution |
| `auto_approve_skills` | `false` | `false` = ask user before creating skills |
| `auto_approve_scripts` | `false` | `false` = ask user before creating scripts |
| `auto_approve_installs` | `false` | `false` = ask user before installing packages |
| `proactive_enabled` | `true` | `false` = skip proactive triggers |
| `stuck_detection_enabled` | `true` | `false` = skip stuck detection |
| `max_triggers_per_session` | `3` | Cap on evolution triggers per session |
| `max_retries` | `3` | Max attempts per trigger |
| `max_active_entries` | `30` | Soft cap on active EVOLUTION.md entries |
| `deprecation_days` | `30` | Days idle before auto-deprecation |

To check for user overrides:
```bash
cat ~/.swarm-ai/config.json 2>/dev/null | jq '.evolution // empty' 2>/dev/null || echo "Using defaults"
```

---

## Writing to EVOLUTION.md

**New entries:** Use the built-in Read + Edit tools (need full-file context for ID generation).
**Field updates (Usage Count, Status):** Use `locked_write.py` for atomic, locked modifications.

**Every write is a 2-step atomic pair — never do step 1 without step 2:**
1. **Edit/locked_write** EVOLUTION.md (add/update/deprecate entry)
2. **Immediately** append JSONL changelog line (same tool-call batch, no other work in between)

### Append a new entry

1. `Read .context/EVOLUTION.md`
2. Find the target section's last content
3. `Edit` tool: `old_string` = last lines before next `## Section` header,
   `new_string` = those same lines + newline + new entry
4. **Immediately run** the JSONL append (see below)

**Example — appending E006 after E005 ends with `- **Auto Generated**: true`
and the next section is `## Optimizations Learned`:**

```
old_string:
- **Auto Generated**: true

## Optimizations Learned

new_string:
- **Auto Generated**: true

### E006 | reactive | skill | 2026-03-15
- **Name**: New Skill
- **Description**: Does the thing
- **Location**: .claude/skills/s_new-skill/
- **Usage**: Instruction skill
- **When to Use**: When user needs the thing
- **Principle Applied**: Reuse before you build
- **Usage Count**: 0
- **Status**: active
- **Auto Generated**: true

## Optimizations Learned
```

### Update a field (use locked_write.py for atomicity)

For Usage Count increments:
```bash
python3 .claude/skills/s_save-memory/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Capabilities Built" \
  --increment-field "Usage Count" \
  --entry-id "E003"
```

For Status changes (e.g., deprecation):
```bash
python3 .claude/skills/s_save-memory/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Capabilities Built" \
  --set-field "Status" --value "deprecated" \
  --entry-id "E003"
```

These operations are atomic (file-locked read-modify-write) and safe for concurrent access.

### JSONL Changelog (mandatory — run immediately after every Edit)

Append one line per mutation. **This is not optional — Rule #14.**
```bash
echo '{"ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","action":"add","type":"capability","id":"E006","summary":"..."}' >> .context/EVOLUTION_CHANGELOG.jsonl
```

Valid actions: `add`, `use`, `promote`, `supersede`, `fork`, `contest`, `deprecate`

---

## Entry Templates

**ID generation:** Read last ID in target section, increment. Empty section → 001.

### E-entry → "Capabilities Built"
```
### E{NNN} | {trigger_type} | {capability_type} | {YYYY-MM-DD}
- **Name**: {name}
- **Description**: {description}
- **Location**: {file_path}
- **Usage**: {usage_instructions}
- **When to Use**: {matching_criteria}
- **Principle Applied**: {principle_name}
- **Usage Count**: 0
- **Status**: active
- **Auto Generated**: true
```

### O-entry → "Optimizations Learned"
```
### O{NNN} | {YYYY-MM-DD}
- **Optimization**: {description}
- **Context**: {when_this_applies}
- **Before**: {old_approach}
- **After**: {new_approach}
- **When Applicable**: {matching_criteria}
```

### C-entry → "Corrections Captured"
```
### C{NNN} | {YYYY-MM-DD}
- **What I Did Wrong**: {incorrect_approach}
- **Correct Approach**: {what_user_showed}
- **Root Cause**: {why_I_got_it_wrong}
- **Prevention Rule**: {rule_to_prevent_recurrence}
- **Occurrences**: 1
```

### K-entry → "Competence Learned"
```
### K{NNN} | {YYYY-MM-DD}
- **Problem Class**: {type_of_problem}
- **Procedure**: {step_by_step_how}
- **When to Apply**: {matching_criteria}
- **Success Rate**: 1/1
- **Last Used**: {YYYY-MM-DD}
- **Status**: active
```

### F-entry → "Failed Evolutions"
```
### F{NNN} | {trigger_type} | {YYYY-MM-DD}
- **Attempted**: {what_was_tried}
- **Strategy**: {strategy_name}
- **Why Failed**: {failure_reason}
- **Lesson**: {what_was_learned}
- **Alternative**: {suggested_alternative}
```

---

## Pattern Promotion

3+ entries with same root cause + VFM ≥50 → promote to MEMORY.md or new skill.

**VFM:** `(Reusability×3 + ErrorPrevention×3 + AnalysisQuality×2 + EfficiencyGain×2) / 10`
- ≥70 → promote immediately. 50-69 → promote if 3+ occurrences. <50 → keep.

### Salience

Start 1.0. Usage resets to 1.0. Decay -0.1/week idle. K-entries >80% success → half decay.
At 0.3 → `fading`. At 0.0 → `deprecated`.

---

## SSE Events

Emit markers for frontend rendering:
```
<!-- EVOLUTION_EVENT: {"type": "evolution_start", "data": {"triggerType": "reactive", "description": "...", "strategySelected": "compose_existing", "attemptNumber": 1}} -->
```

Events: `evolution_start`, `evolution_result`, `evolution_stuck_detected`, `evolution_help_request`

## Help Request (after 3 failures)

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

---

## Context Window Monitoring

Context usage is monitored automatically by the inline pipeline in
``agent_manager.py`` — no manual script invocation needed.  The backend
emits ``context_warning`` SSE events after every turn with usage data.

**Act on result (when visible in SSE events):**
- `ok` (<70%) — silent
- `warn` (70-84%) — append note after response
- `critical` (≥85%) — warn at start of response, offer save-context

---

## Growth Principles

- "Reuse before you build" → check EVOLUTION.md first
- "Small fix over big system" → scripts > skills for simple tasks
- "Verify before you declare" → test before registering
- "Know when to stop" → hard stop at 3 attempts
- "Stability over novelty" → ADL priority ordering

## DailyActivity Logging

After significant evolution events, append to `Knowledge/DailyActivity/YYYY-MM-DD.md`.

## Rules (Hard Constraints)

1. **Max 3 attempts per trigger** — then Help Request and stop
2. **Verify before registering** — test against original task
3. **Always record failures** — every failed attempt gets an F-entry
4. **Always return to user's task** — summarize evolution, resume work
5. **Respect config toggles** — check before acting
6. **Never install without checking `auto_approve_installs`**
7. **Never create skills without checking `auto_approve_skills`**
8. **Use Read + Edit for new entries, locked_write.py for field updates** — new entries need full-file context for ID generation; field updates (Usage Count, Status) use locked_write.py for atomicity
9. **Proactive triggers are deferred** — never interrupt active work
10. **Each attempt must be fundamentally different**
11. **Corrections require novelty filter** — systematic gaps only
12. **Promotion requires VFM ≥50**
13. **ADL drift prevention** — stability over everything
14. **Always write JSONL changelog** — no silent mutations
