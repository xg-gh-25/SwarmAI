# SwarmAI Self-Evolution — End-to-End Architecture

## Overview

SwarmAI's self-evolution system enables the agent to detect capability gaps, learn from mistakes, and build new capabilities (skills, scripts, knowledge) that persist across sessions. The architecture is **prompt-driven** — the core logic lives in a skill (`s_self-evolution/SKILL.md`) that instructs the agent how to detect triggers, execute evolution loops, and persist results. Backend code provides infrastructure: EVOLUTION.md in the system prompt, `locked_write.py` for atomic field updates, SSE event parsing for frontend rendering, and config defaults.

### Design Philosophy

- **Prompt-driven by design**: Trigger detection and evolution loops are agent self-monitoring instructions, not backend state machines. This is intentional — the agent's reasoning is better at classifying capability gaps than regex patterns.
- **Code-enforced where it matters**: EVOLUTION.md loading (system prompt), SSE event parsing (backend), config defaults (AppConfigManager), and file provisioning (ensure_directory) are all code-enforced.
- **Filesystem as single source of truth**: All evolution data lives in `.context/EVOLUTION.md` — no database tables.

---

## End-to-End Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                     1. SESSION START (Context Loading)               │
│                                                                     │
│  [CODE-ENFORCED] ContextDirectoryLoader.ensure_directory()          │
│     → Provisions EVOLUTION.md from template if missing (0o644)      │
│     → Provisions EVOLUTION_CHANGELOG.jsonl if missing               │
│                                                                     │
│  [CODE-ENFORCED] ContextDirectoryLoader.load_all()                  │
│     → EVOLUTION.md loaded at P8 (truncates from head = newest kept) │
│     → Agent sees all active capabilities, optimizations, corrections│
│                                                                     │
│  [PROMPT-DRIVEN] s_self-evolution skill (always-active built-in)    │
│     → Agent reviews EVOLUTION.md entries                            │
│     → Matches active entries to current situation                   │
│     → Checks salience: entries idle >30 days → mark deprecated      │
│     → Scans for promotion candidates (3+ entries with same pattern) │
│                                                                     │
│  Result: Agent has full evolution context + active skill loaded     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   2. DURING SESSION (Trigger Detection)              │
│                                                                     │
│  [PROMPT-DRIVEN] Agent self-monitors for 5 trigger types:           │
│                                                                     │
│    🔴 Reactive — Tool/command failure, missing skill, knowledge gap │
│       First occurrence = transient. Recurrence = capability gap.    │
│                                                                     │
│    🟡 Proactive — EVOLUTION.md entry matches current situation,     │
│       MEMORY.md lesson applies. DEFERRED until task completes.      │
│                                                                     │
│    🔵 Stuck — Same error 2x, file edited 3x without progress,      │
│       5+ silent tool calls, self-revert, cosmetic retry.            │
│       → Stop immediately, enter escape protocol.                    │
│                                                                     │
│    🟢 Correction — User corrects agent output.                      │
│       Systematic gap → C-entry. Skip typos/preferences.            │
│                                                                     │
│    🟣 Task Completion Review — After 5+ tool calls, reflect.        │
│       Novel insight → O/K-entry.                                    │
│                                                                     │
│  Priority: Stuck > Reactive > Correction > Proactive                │
│  Limits: Max 3 triggers/session, 60s cooldown between same-type     │
│  Counter persisted to /tmp/swarm-evo-triggers (survives compaction) │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   3. EVOLUTION LOOP (Max 3 Attempts)                │
│                                                                     │
│  [PROMPT-DRIVEN] Per-trigger strategy sequences:                    │
│                                                                     │
│  Reactive:  compose_existing → build_new → research_and_build       │
│  Proactive: optimize_in_place → build_replacement → research_best   │
│  Stuck:     completely_different → simplify_to_mvp → research_new   │
│                                                                     │
│  Each attempt:                                                      │
│    1. Select strategy (must be fundamentally different from prior)   │
│    2. Build capability (Skill, Script, or tool install)             │
│       → Skills: .claude/skills/s_{name}/SKILL.md                    │
│       → Scripts: .swarm-ai/scripts/{name}.py                        │
│       → Installs: brew/pip/npm (requires auto_approve_installs)     │
│    3. Verify: re-attempt original triggering task                   │
│    4. Pass → register in EVOLUTION.md. Fail → next strategy.        │
│                                                                     │
│  After 3 failures → Help Request to user with full context          │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   4. PERSISTENCE (EVOLUTION.md)                      │
│                                                                     │
│  [PROMPT-DRIVEN] Agent writes entries using two methods:            │
│                                                                     │
│    New entries: Read + Edit tools (need full-file context for IDs)  │
│    Field updates: locked_write.py --increment-field / --set-field   │
│                   (atomic, file-locked read-modify-write)           │
│                                                                     │
│  [PROMPT-DRIVEN] JSONL changelog append after every mutation:       │
│    echo '{"ts":"...","action":"add","id":"E006",...}'               │
│      >> .context/EVOLUTION_CHANGELOG.jsonl                          │
│                                                                     │
│  Entry types:                                                       │
│    E-entries: Capabilities Built (skills, scripts, tools, knowledge)│
│    O-entries: Optimizations Learned (before/after comparisons)      │
│    C-entries: Corrections Captured (user corrections, prevention)   │
│    K-entries: Competence Learned (procedures, success rates)        │
│    F-entries: Failed Evolutions (what was tried, why it failed)     │
│                                                                     │
│  Lifecycle: active → deprecated (30 days idle or cap exceeded)      │
│  Soft cap: 30 active entries. Oldest + lowest usage deprecated first│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   5. SSE EVENTS (Frontend Rendering)                │
│                                                                     │
│  [PROMPT-DRIVEN] Agent emits markers in text output:                │
│    <!-- EVOLUTION_EVENT: {"event":"evolution_start","data":{...}} -->│
│                                                                     │
│  [CODE-ENFORCED] Backend parses markers:                            │
│    chat.py → _extract_evolution_events() → regex match              │
│    → Emitted as separate SSE data lines in sse_with_heartbeat()     │
│                                                                     │
│  [CODE-ENFORCED] Frontend renders:                                  │
│    useChatStreamingLifecycle catches event.type.startsWith('evo_')  │
│    → Creates Message with evolutionEvent property                   │
│    → ChatPage.tsx renders via EvolutionMessage component            │
│    → Trigger-type icons (⚡🔍🔄), colored borders, expandable      │
│                                                                     │
│  4 event types:                                                     │
│    evolution_start — trigger type, strategy, attempt number         │
│    evolution_result — success/failure, capability created, duration  │
│    evolution_stuck_detected — signals, summary, escape strategy     │
│    evolution_help_request — task summary, all attempts, suggestion  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   6. NEXT SESSION (Cross-Session Growth)            │
│                                                                     │
│  [CODE-ENFORCED] EVOLUTION.md loaded into system prompt at P8       │
│  [PROMPT-DRIVEN] Agent matches active entries to current task       │
│    → If match found: apply capability, increment Usage Count        │
│    → If no match: proceed normally, triggers fire if needed         │
│                                                                     │
│  The cycle repeats. Capabilities accumulate across sessions.        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Trigger Mechanism Classification

| Operation | Mechanism | Can model forget? | Mitigation |
|-----------|-----------|-------------------|------------|
| EVOLUTION.md loading | `CONTEXT_FILES` P8 → `ContextDirectoryLoader` | No — code-enforced | — |
| EVOLUTION.md provisioning | `ensure_directory()` copies template | No — code-enforced | — |
| SSE event parsing | `_extract_evolution_events()` in `chat.py` | No — code-enforced | — |
| Frontend rendering | `useChatStreamingLifecycle` + `EvolutionMessage` | No — code-enforced | — |
| Config defaults | `AppConfigManager` `evolution` key | No — code-enforced | — |
| Trigger detection | `s_self-evolution/SKILL.md` instructions | Yes — prompt-dependent | Always-active skill, loaded every session |
| Evolution loop execution | `s_self-evolution/SKILL.md` instructions | Yes — prompt-dependent | Hard 3-attempt limit, per-session counter |
| EVOLUTION.md writes | Agent uses Read+Edit / locked_write.py | Yes — prompt-dependent | Skill Rule #8 with explicit instructions |
| JSONL changelog | Agent appends after every Edit | Yes — prompt-dependent | Skill Rule #14 (mandatory) |
| Trigger counter | `/tmp/swarm-evo-triggers` file | No — persisted to filesystem | Survives context compaction |

---

## EVOLUTION.md File Format

Single source of truth for all evolution data. Five sections with sequential IDs:

```markdown
# SwarmAI Evolution Registry

## Capabilities Built
### E001 | reactive | skill | 2026-03-07
- **Name**: auto-git-conflict-resolver
- **Description**: Resolves simple git merge conflicts
- **Location**: .claude/skills/s_auto-git-conflict-resolver
- **Usage**: Invoked when git conflict detected
- **When to Use**: git merge/rebase with simple line-level conflicts
- **Principle Applied**: Reuse before you build
- **Usage Count**: 3
- **Status**: active
- **Auto Generated**: true

## Optimizations Learned
### O001 | 2026-03-07
- **Optimization**: Use ripgrep instead of grep for large codebases
- **Context**: Projects with >1000 files
- **Before**: grep -r takes >30s
- **After**: rg takes <2s
- **When Applicable**: File count >1000

## Corrections Captured
### C001 | 2026-03-07
- **What I Did Wrong**: Used regex for XML parsing
- **Correct Approach**: Use lxml.etree
- **Root Cause**: Defaulted to string manipulation
- **Prevention Rule**: Always use a proper parser for structured formats
- **Occurrences**: 1

## Competence Learned
### K001 | 2026-03-07
- **Problem Class**: Python dependency conflicts
- **Procedure**: 1. Check pyproject.toml 2. Use uv pip compile 3. Verify
- **When to Apply**: Any pip install failure
- **Success Rate**: 3/3
- **Last Used**: 2026-03-07
- **Status**: active

## Failed Evolutions
### F001 | reactive | 2026-03-07
- **Attempted**: Install custom linter as npm global
- **Strategy**: install_plugin
- **Why Failed**: Permission denied on npm global directory
- **Lesson**: Prefer npx or local install over global
- **Alternative**: Use npx or add to devDependencies
```

### ID Generation
- E-IDs: Sequential within "Capabilities Built" (E001, E002, ...)
- O-IDs: Sequential within "Optimizations Learned" (O001, O002, ...)
- C-IDs: Sequential within "Corrections Captured" (C001, C002, ...)
- K-IDs: Sequential within "Competence Learned" (K001, K002, ...)
- F-IDs: Sequential within "Failed Evolutions" (F001, F002, ...)

### Entry Lifecycle
```
[new] → active → deprecated (30 days idle OR cap exceeded)
                → superseded (replaced by better entry)
[failed] → recorded in Failed Evolutions (permanent reference)
```

---

## Pattern Promotion (VFM Scoring)

When 3+ entries share the same root cause, the agent evaluates promotion using VFM:

```
VFM = (Reusability×3 + ErrorPrevention×3 + AnalysisQuality×2 + EfficiencyGain×2) / 10

≥70 → promote immediately (to MEMORY.md or new skill)
50-69 → promote if 3+ occurrences confirm the pattern
<50 → keep in EVOLUTION.md, do not promote
```

### Salience Decay
- Start at 1.0 on creation. Usage resets to 1.0.
- Decay: -0.1/week idle. K-entries with >80% success rate → half decay.
- At 0.3 → `fading`. At 0.0 → `deprecated`.

---

## Configuration

All config lives under `config.json["evolution"]`, read via `AppConfigManager.get("evolution")`:

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
| `same_type_cooldown_seconds` | `60` | Cooldown between same-type triggers |
| `verification_timeout_seconds` | `120` | Max time for capability verification |

---

## Growth Principles

Embedded in `s_self-evolution/SKILL.md` (previously a separate GROWTH_PRINCIPLES.md, folded into SOUL.md + skill):

1. **Reuse before you build** → check EVOLUTION.md first
2. **Small fix over big system** → scripts > skills for simple tasks
3. **Verify before you declare** → test before registering
4. **Know when to stop** → hard stop at 3 attempts
5. **Stability over novelty** → ADL priority ordering (Stability > Interpretability > Reusability > Extensibility > Novelty)

---

## SSE Event Flow (Detailed)

```
Agent text output contains:
  "I'll try a different approach <!-- EVOLUTION_EVENT: {"event":"evolution_start","data":{"triggerType":"stuck",...}} -->"
       │
       ▼
Backend: _format_message() → assistant content block with text (marker embedded)
       │
       ▼
Backend: sse_with_heartbeat() yields formatted message as SSE data line
       │
       ▼
Backend: _extract_evolution_events(item) → regex parses marker → JSON payload
       │  Normalizes "event" → "type" for frontend compatibility
       ▼
Backend: yields evolution event as SEPARATE SSE data line
       │
       ▼
Frontend: chat.ts SSE handler parses both data lines
       │
       ▼
Frontend: useChatStreamingLifecycle.createStreamHandler()
       │  Checks: event.type?.startsWith('evolution_')
       │  Creates Message with evolutionEvent property
       │  Adds to tab messages array
       ▼
Frontend: ChatPage.tsx renders via EvolutionMessage component
       │  Trigger-type styling (⚡🔍🔄), expandable details
       ▼
User sees evolution event inline in chat stream
```

Note: Evolution markers remain in the original text content saved to DB (as HTML comments, invisible in rendered markdown). The extracted evolution events are frontend-only — not persisted separately to DB.

---

## Help Request Flow

When all 3 evolution attempts fail:

```
Agent generates structured Help Request:
  "I need your help. Here's what happened:
   Original Task: {description}
   Trigger: {type} — {details}
   Attempts:
   1. Strategy: {s1} — Failed: {reason1}
   2. Strategy: {s2} — Failed: {reason2}
   3. Strategy: {s3} — Failed: {reason3}
   My Assessment: {why blocked}
   Suggested Next Step: {what user could do}"
       │
       ▼
Agent emits: <!-- EVOLUTION_EVENT: {"event":"evolution_help_request","data":{...}} -->
       │
       ▼
Backend parses → SSE event → Frontend renders as 🆘 styled message
       │
       ▼
User provides guidance → Agent incorporates and optionally retries
```

---

## Drift Prevention (ADL Protocol)

The self-evolution system includes a drift prevention mechanism to ensure evolution improves stability rather than introducing complexity:

Priority ordering: **Stability > Interpretability > Reusability > Extensibility > Novelty**

Before any evolution action, the agent self-checks:
1. Will this make the system more stable?
2. Is the result understandable?
3. Is it safe to revert?

If any answer is "no" → simplify or skip the evolution.

---

## File Structure Reference

```
backend/
├── core/
│   ├── context_directory_loader.py  # EVOLUTION.md in CONTEXT_FILES at P8
│   ├── agent_manager.py             # _build_system_prompt() loads EVOLUTION.md
│   ├── app_config_manager.py        # evolution config defaults
│   └── evolution_events.py          # SSE event helper functions (if exists)
├── context/
│   ├── EVOLUTION.md                 # Default template (5 sections, empty)
│   └── EVOLUTION_CHANGELOG.jsonl    # Empty seed file
├── scripts/
│   └── locked_write.py              # --increment-field, --set-field for EVOLUTION.md
├── routers/
│   └── chat.py                      # _extract_evolution_events() marker parsing
└── skills/
    └── s_self-evolution/
        ├── SKILL.md                 # Core evolution engine (~350 lines)
        └── REFERENCE.md             # Supplementary: VFM scoring, SSE fields

desktop/src/
├── components/chat/
│   └── EvolutionMessage.tsx         # Evolution event renderer (expandable, styled)
├── hooks/
│   └── useChatStreamingLifecycle.ts # Catches evolution_* events → Message objects
├── services/
│   └── evolution.ts                 # TypeScript interfaces for evolution events
└── pages/
    └── ChatPage.tsx                 # Renders EvolutionMessage for evolutionEvent messages

~/.swarm-ai/SwarmWS/.context/
├── EVOLUTION.md                     # Agent-managed evolution registry (P8 in system prompt)
└── EVOLUTION_CHANGELOG.jsonl        # Append-only audit log (not in system prompt)
```

---

## Known Limitations

1. **Trigger detection is prompt-dependent**: The agent may not detect all triggers, especially under heavy context pressure or after context compaction. The trigger counter in `/tmp/` mitigates over-triggering but can't force under-triggering.

2. **JSONL changelog compliance is prompt-dependent**: Rule #14 mandates changelog writes, but the agent may skip it. The changelog is an audit trail, not a functional dependency — EVOLUTION.md is the source of truth.

3. **Evolution events not persisted to DB**: SSE evolution events exist only in the frontend's in-memory message array. On page reload, they're lost. The evolution results are persisted in EVOLUTION.md itself.

4. **Trigger counter uses `/tmp/`**: The per-session counter at `/tmp/swarm-evo-triggers` survives context compaction but not OS restart. Multiple concurrent sessions share the same file (no session ID scoping). This is acceptable for the current single-user desktop app.

5. **No backend validation of evolution entries**: The agent writes EVOLUTION.md entries directly. There's no schema validation — malformed entries won't crash anything but may not be matched correctly in future sessions.
