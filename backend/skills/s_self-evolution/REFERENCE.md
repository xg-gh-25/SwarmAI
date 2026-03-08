# Self-Evolution Engine — Reference

Read this file when you need write templates, SSE formats, or config details.

## EVOLUTION.md Write Protocol

**Always** use `locked_write.py` for writes to `.context/EVOLUTION.md`. Never
edit the file directly.

### E-entry (Capability Built)

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

### O-entry (Optimization Learned)

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

### F-entry (Failed Evolution)

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

### C-entry (Correction Captured)

```bash
python backend/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Corrections Captured" \
  --append "### C{NNN} | {YYYY-MM-DD}
- **What I Did Wrong**: {incorrect_approach}
- **Correct Approach**: {what_user_showed_or_said}
- **Root Cause**: {why_I_got_it_wrong}
- **Prevention Rule**: {rule_to_prevent_recurrence}
- **Occurrences**: 1"
```

### K-entry (Competence Learned)

Competence entries record HOW to solve a class of problems, not just what
happened. They track procedural knowledge with success metrics.

```bash
python backend/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Competence Learned" \
  --append "### K{NNN} | {YYYY-MM-DD}
- **Problem Class**: {type_of_problem_this_solves}
- **Procedure**: {step_by_step_how_to_solve}
- **When to Apply**: {matching_criteria}
- **Success Rate**: 1/1
- **Last Used**: {YYYY-MM-DD}
- **Status**: active"
```

### Updating Competence Success Rate

```bash
python backend/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Competence Learned" \
  --increment-field "Success Rate" \
  --entry-id "K{NNN}"
```

### Incrementing Usage Count

```bash
python backend/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Capabilities Built" \
  --increment-field "Usage Count" \
  --entry-id "E{NNN}"
```

### Setting Status (deprecation, supersede, etc.)

```bash
python backend/scripts/locked_write.py \
  --file .context/EVOLUTION.md \
  --section "Capabilities Built" \
  --set-field "Status" --value "deprecated" \
  --entry-id "E{NNN}"
```

### ID Generation

Read the last ID in each section and increment:
- Capabilities Built: E001 → E002 → E003 ...
- Optimizations Learned: O001 → O002 → O003 ...
- Failed Evolutions: F001 → F002 → F003 ...
- Corrections Captured: C001 → C002 → C003 ...
- Competence Learned: K001 → K002 → K003 ...

If a section is empty (no entries yet), start with 001.

## JSONL Changelog

Append to `.context/EVOLUTION_CHANGELOG.jsonl` for every write operation.

**Format:**
```json
{"ts":"2026-03-08T02:30:00Z","action":"add","type":"correction","id":"C001","summary":"Wrong API convention assumed"}
{"ts":"2026-03-08T03:00:00Z","action":"promote","type":"learning","id":"O003","target":"MEMORY.md","summary":"Always verify lib conventions"}
{"ts":"2026-03-08T04:00:00Z","action":"supersede","type":"capability","id":"E002","superseded_by":"E009","summary":"Replaced with faster approach"}
```

**Valid actions:** `add`, `promote`, `supersede`, `fork`, `contest`, `deprecate`, `use`

**Write command:**
```bash
echo '{"ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","action":"{action}","type":"{type}","id":"{id}","summary":"{summary}"}' >> .context/EVOLUTION_CHANGELOG.jsonl
```

## SSE Event Emission

Output structured markers for the backend to parse as SSE events:

```
<!-- EVOLUTION_EVENT: {"event": "evolution_start", "data": {"triggerType": "reactive", "description": "...", "strategySelected": "compose_existing", "attemptNumber": 1, "principleApplied": "Reuse before you build"}} -->
```

**Event types:**

| Event | When | Key Fields |
|-------|------|------------|
| `evolution_start` | Beginning an attempt | `triggerType`, `description`, `strategySelected`, `attemptNumber`, `principleApplied` |
| `evolution_result` | Attempt completes | `outcome`, `durationMs`, `capabilityCreated`, `evolutionId`, `failureReason` |
| `evolution_stuck_detected` | Stuck state detected | `detectedSignals`, `triedSummary`, `escapeStrategy` |
| `evolution_help_request` | All 3 attempts failed | `taskSummary`, `triggerType`, `attempts`, `suggestedNextStep` |
| `evolution_correction_captured` | User correction recorded | `correctionId`, `whatWasWrong`, `preventionRule` |
| `evolution_promoted` | Entries promoted | `sourceIds`, `target`, `vfmScore`, `summary` |

## Help Request Format

When all 3 attempts fail:

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

Also emit an `evolution_help_request` SSE event.

## Config Reference

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

## Value-First Scoring (VFM)

Before promoting any entry, score across four dimensions:

| Dimension | Weight | Question |
|-----------|--------|----------|
| Reusability | 3x | Will future tasks leverage this repeatedly? |
| Error Prevention | 3x | Does this prevent a recurring failure? |
| Analysis Quality | 2x | Does this improve output depth or accuracy? |
| Efficiency Gain | 2x | Does this save time or reduce tool calls? |

**Formula:** `VFM = (Reusability*3 + ErrorPrevention*3 + AnalysisQuality*2 + EfficiencyGain*2) / 100 * 100`

| VFM Score | Action |
|-----------|--------|
| >=70 | Promote immediately |
| 50-69 | Promote if 3+ occurrences confirm the pattern |
| <50 | Keep in EVOLUTION.md, do not promote |
