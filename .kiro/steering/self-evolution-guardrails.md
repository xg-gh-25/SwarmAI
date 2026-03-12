---
inclusion: fileMatch
fileMatchPattern: "backend/skills/s_self-evolution/*,backend/context/EVOLUTION.md,backend/context/EVOLUTION_CHANGELOG.jsonl,backend/routers/chat.py,backend/hooks/evolution_maintenance_hook.py,backend/hooks/evolution_trigger_hook.py,desktop/src/hooks/useChatStreamingLifecycle.ts,desktop/src/services/evolution.ts"
---

# Self-Evolution Guardrails

## Core Invariant: Evolution Improves Stability, Not Complexity

The self-evolution system is prompt-driven by design. The agent detects capability gaps, builds solutions, and persists them in EVOLUTION.md. Backend code provides infrastructure only: file provisioning, SSE event parsing, config defaults, and locked writes.

Priority ordering (ADL Protocol): Stability > Interpretability > Reusability > Extensibility > Novelty

## What Is Code-Enforced vs Prompt-Dependent

| Layer | Mechanism | Reliability |
|-------|-----------|-------------|
| EVOLUTION.md loading at P8 | `ContextDirectoryLoader` | Code-enforced |
| EVOLUTION.md provisioning | `ensure_directory()` | Code-enforced |
| SSE event marker parsing | `_extract_evolution_events()` regex in `chat.py` | Code-enforced |
| Frontend evolution rendering | `useChatStreamingLifecycle` + `EvolutionMessage` | Code-enforced |
| EvolutionBadge wiring | `deriveEvolutionCounts` in ChatPage → SwarmRadar prop | Code-enforced |
| Config defaults | `AppConfigManager` `evolution` key | Code-enforced |
| Entry deprecation + pruning | `EvolutionMaintenanceHook` at session close | Code-enforced |
| Tool failure trigger nudge | `ToolFailureTracker` in message loop | Code-enforced |
| Trigger detection | `s_self-evolution/SKILL.md` instructions | Prompt-dependent (code-assisted) |
| Evolution loop execution | `s_self-evolution/SKILL.md` instructions | Prompt-dependent |
| EVOLUTION.md writes | Agent uses Read+Edit / `locked_write.py` | Prompt-dependent |
| Entry dedup before write | `s_self-evolution/SKILL.md` Step 0 procedure | Prompt-dependent |
| JSONL changelog | Agent appends after every Edit | Prompt-dependent |

## SSE Event Flow — Do Not Break the Chain

```
Agent text → <!-- EVOLUTION_EVENT: {...} --> marker
  → _extract_evolution_events() regex parse in chat.py
  → Separate SSE data line emitted by sse_with_heartbeat()
  → Frontend: event.type.startsWith('evolution_') check
  → Message with evolutionEvent property → EvolutionMessage component
```

Rules:
- Evolution markers are HTML comments embedded in assistant text — they survive DB persistence (invisible in rendered markdown)
- Extracted evolution events are frontend-only — NOT persisted separately to DB
- The regex in `_extract_evolution_events()` MUST match the marker format emitted by the skill
- If you change the marker format in the skill, you MUST update the regex

## EVOLUTION.md File Format Invariants

- Five sections with sequential IDs: E-entries (Capabilities), O-entries (Optimizations), C-entries (Corrections), K-entries (Competence), F-entries (Failed)
- IDs are sequential within each section (E001, E002, ...; O001, O002, ...)
- Entry lifecycle: `active` → `deprecated` (30 days idle or cap exceeded) → `superseded` (replaced)
- Soft cap: 30 active entries. Oldest + lowest usage deprecated first.
- Salience decay: -0.1/week idle, usage resets to 1.0. At 0.3 → `fading`, at 0.0 → `deprecated`.

### EvolutionMaintenanceHook (Code-Enforced Lifecycle)

`EvolutionMaintenanceHook` runs as the 4th session lifecycle hook and performs code-enforced EVOLUTION.md housekeeping that was previously prompt-dependent:

- Scans `Capabilities Built` and `Competence Learned` sections for entries with Status + Usage Count fields
- Deprecation: entries with `status=active`, idle >30 days, `usage_count=0` → set to `deprecated`
- Pruning: entries with `status=deprecated`, `usage_count=0`, idle >30 days → removed from file
- All actions logged to `EVOLUTION_CHANGELOG.jsonl` with `source: "maintenance_hook"`
- Uses `locked_write.py` functions for atomic field updates (imported as library, not shelled out)
- Configurable `deprecation_days` (default 30)

## Configuration Boundaries

All evolution config lives under `config.json["evolution"]`, read via `AppConfigManager.get("evolution")`:

- `enabled: true` — master switch. `false` disables ALL evolution.
- `auto_approve_skills/scripts/installs: false` — user must approve capability creation by default
- `max_triggers_per_session: 3` — hard cap, enforced by `/tmp/swarm-evo-triggers-{session_id}` counter
- `max_retries: 3` — per-trigger attempt limit
- `max_active_entries: 30` — soft cap on EVOLUTION.md entries

When modifying config defaults, NEVER change `auto_approve_*` to `true` — this would allow the agent to create skills/scripts without user consent.

## Trigger Counter Isolation

The per-session trigger counter uses session-scoped files at `/tmp/swarm-evo-triggers-{session_id}`. Each session gets its own counter file, preventing cross-session interference. Files in `/tmp/` auto-clean on OS reboot.

## Tool Failure Tracker (Code-Assisted Trigger Detection)

`ToolFailureTracker` is instantiated per-session (stored in `_active_sessions[sid]["failure_tracker"]`) and watches for repeated tool failures:

- Tracks failure signatures: `tool_name.lower() + ":" + first_100_chars_of_error`
- After `FAILURE_THRESHOLD` (2) consecutive failures with same signature → emits evolution nudge
- Nudge cooldown: 120s per signature, max 3 nudges per session (`_max_nudges_per_session`)
- On tool success → `reset_tool()` clears all failure signatures for that tool
- Nudge is a system-level hint injected into agent context, not user-visible
- No shared mutable state — each session has its own tracker instance

Anti-pattern: Making `ToolFailureTracker` a module-level singleton — this would leak failure state between sessions.

## Regression Checklist

When modifying self-evolution code:

- [ ] `_extract_evolution_events()` regex still matches the marker format in `s_self-evolution/SKILL.md`
- [ ] Evolution SSE events are emitted as SEPARATE data lines (not embedded in assistant content)
- [ ] Frontend `event.type.startsWith('evolution_')` check still catches all 4 event types
- [ ] EVOLUTION.md is still at P8 in CONTEXT_FILES with `truncate_from="head"` and `user_customized=True`
- [ ] `ensure_directory()` provisions EVOLUTION.md and EVOLUTION_CHANGELOG.jsonl from templates
- [ ] Config defaults in `AppConfigManager` match the documented defaults above
- [ ] `auto_approve_*` defaults remain `false`
- [ ] Evolution markers in assistant text are HTML comments (invisible in rendered markdown)
- [ ] `EvolutionMaintenanceHook` registered as 4th hook in lifespan (after DistillationTriggerHook)
- [ ] `ToolFailureTracker` is per-session (stored in `_active_sessions`), not module-level
- [ ] `ToolFailureTracker.reset_tool()` called on tool success to clear stale failure counts
