# SwarmAI Context and Memory Management — End-to-End Architecture

## Overview

SwarmAI's context and memory system gives agents persistent identity, personality, knowledge, and cross-session memory. All context lives in a single hidden directory (`~/.swarm-ai/SwarmWS/.context/`) using filesystem-only storage — no database for context content. The system assembles context into the system prompt on every session start, with dynamic token budgets and L0/L1 caching for different model sizes.

Three cooperating systems build the final system prompt:
1. `ContextDirectoryLoader` — reads 11 source files from `.context/`, enforces token budget
2. `_build_system_prompt()` in SessionRouter — orchestrates assembly, adds ephemeral context
3. `SystemPromptBuilder` — appends non-file sections (safety, datetime, runtime metadata)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    CONTEXT ASSEMBLY PIPELINE                     │
│                                                                  │
│  backend/context/          ~/.swarm-ai/SwarmWS/.context/         │
│  (templates, 12 files)     (runtime, 11 source + 2 cache)       │
│         │                           │                            │
│         ▼                           ▼                            │
│  ┌──────────────────────────────────────────────┐               │
│  │         ContextDirectoryLoader                │               │
│  │                                               │               │
│  │  ensure_directory()                           │               │
│  │  ├── System files: always overwrite (0o444)   │               │
│  │  └── User files: copy-only-if-missing (0o644) │               │
│  │                                               │               │
│  │  load_all(model_context_window)               │               │
│  │  ├── ≥64K: L1 cache or assemble sources       │               │
│  │  ├── <64K: L0 compact cache                   │               │
│  │  └── _enforce_token_budget()                  │               │
│  └──────────────────┬───────────────────────────┘               │
│                     │                                            │
│  ┌──────────────────▼───────────────────────────┐               │
│  │         _build_system_prompt()                │               │
│  │         (SessionRouter)                        │               │
│  │                                               │               │
│  │  1. ContextDirectoryLoader output             │               │
│  │  2. BOOTSTRAP.md (ephemeral, first-run)       │               │
│  │  3. DailyActivity (last 2 by date, 2K cap)  │               │
│  │  4. Metadata for TSCC viewer                  │               │
│  └──────────────────┬───────────────────────────┘               │
│                     │                                            │
│  ┌──────────────────▼───────────────────────────┐               │
│  │         SystemPromptBuilder                   │               │
│  │                                               │               │
│  │  _section_identity()   → "You are {name}..."  │               │
│  │  _section_safety()     → 6 safety rules        │               │
│  │  _section_workspace()  → cwd path              │               │
│  │  _section_datetime()   → UTC + local time       │               │
│  │  _section_runtime()    → agent/model/OS/channel │               │
│  └──────────────────┬───────────────────────────┘               │
│                     │                                            │
│                     ▼                                            │
│            ClaudeAgentOptions.system_prompt                      │
│            (sent to Claude SDK subprocess)                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## The 11 Context Files

### ContextFileSpec Data Model

Each context file is defined by a frozen dataclass:

```python
@dataclass(frozen=True)
class ContextFileSpec:
    filename: str                              # e.g. "SWARMAI.md"
    priority: int                              # 0 = highest, 9 = lowest
    section_name: str                          # Header in assembled output
    truncatable: bool                          # Can be shortened under budget pressure
    user_customized: bool = False              # True = copy-only-if-missing, 0o644
    truncate_from: Literal["head", "tail"] = "tail"  # Direction of truncation
```

### File Registry

```python
CONTEXT_FILES = [
    ContextFileSpec("SWARMAI.md",    0, "SwarmAI",            False, False, "tail"),
    ContextFileSpec("IDENTITY.md",   1, "Identity",           False, False, "tail"),
    ContextFileSpec("SOUL.md",       2, "Soul",               False, False, "tail"),
    ContextFileSpec("AGENT.md",      3, "Agent Directives",   True,  False, "tail"),
    ContextFileSpec("USER.md",       4, "User",               True,  True,  "tail"),
    ContextFileSpec("STEERING.md",   5, "Steering",           True,  True,  "tail"),
    ContextFileSpec("TOOLS.md",      6, "Tools",              True,  True,  "tail"),
    ContextFileSpec("MEMORY.md",     7, "Memory",             True,  True,  "head"),
    ContextFileSpec("EVOLUTION.md",  8, "Evolution Registry",  True,  True,  "head"),
    ContextFileSpec("KNOWLEDGE.md",  9, "Knowledge",          True,  True,  "tail"),
    ContextFileSpec("PROJECTS.md",  10, "Projects",           True,  True,  "tail"),
]
```

### File Responsibilities

| File | P | Section | Truncatable | Owner | Purpose |
|------|---|---------|-------------|-------|---------|
| SWARMAI.md | 0 | SwarmAI | No | System | Core system prompt — foundational identity and mission |
| IDENTITY.md | 1 | Identity | No | System | Agent name, avatar, self-description |
| SOUL.md | 2 | Soul | No | System | Personality, tone, communication style, 6 Operating Principles |
| AGENT.md | 3 | Agent Directives | Yes | System | Behavioral rules, safety rules, action boundaries |
| USER.md | 4 | User | Yes | User | User profile, preferences, timezone, what they care about |
| STEERING.md | 5 | Steering | Yes | User | Session-level rules, temporary focus areas, overrides |
| TOOLS.md | 6 | Tools | Yes | User | Tool usage guidance and conventions |
| MEMORY.md | 7 | Memory | Yes (head) | User | Cross-session persistent memory (curated, not raw logs) |
| EVOLUTION.md | 8 | Evolution Registry | Yes (head) | User | Self-evolution capabilities, optimizations, corrections, failures |
| KNOWLEDGE.md | 9 | Knowledge | Yes | User | Domain knowledge, API references, codebase conventions |
| PROJECTS.md | 10 | Projects | Yes | User | Active projects summary, priorities, status |

---

## Two-Mode Copy Behavior (`ensure_directory`)

Called at every session start to ensure `.context/` exists and files are current:

```
ensure_directory()
  │
  ├── Create ~/.swarm-ai/SwarmWS/.context/ (mkdir -p)
  │
  ├── For each ContextFileSpec in CONTEXT_FILES:
  │   │
  │   ├── user_customized=False (System defaults: SWARMAI, IDENTITY, SOUL, AGENT)
  │   │   ├── Read source bytes from backend/context/{filename}
  │   │   ├── If dest exists AND bytes identical → skip (no-op)
  │   │   ├── If dest exists AND bytes differ → chmod 0o644, overwrite, chmod 0o444
  │   │   └── If dest missing → write, chmod 0o444
  │   │
  │   └── user_customized=True (User files: USER, STEERING, TOOLS, MEMORY, KNOWLEDGE, PROJECTS)
  │       ├── If dest exists → skip entirely (preserve user edits)
  │       └── If dest missing → copy from template, chmod 0o644
  │
  └── _maybe_create_bootstrap()
      ├── If BOOTSTRAP.md already exists → skip
      ├── If USER.md doesn't exist → skip
      ├── If USER.md has user content → skip
      └── If USER.md is empty template → copy BOOTSTRAP.md from templates
```

Key design decisions:
- System files are the app's voice — updated on every release
- User files are never overwritten — edits survive across updates
- Byte-comparison optimization avoids unnecessary writes for system files
- All `chmod` calls wrapped in try/except for Windows compatibility
- BOOTSTRAP.md is NOT in CONTEXT_FILES — detected separately as ephemeral onboarding

---

## Token Budget System

### Dynamic Budget Computation

`compute_token_budget()` scales the budget to the model's context window:

| Model Context Window | Token Budget | Constant |
|---------------------|-------------|----------|
| ≥ 200K tokens | 50,000 | `BUDGET_LARGE_MODEL` |
| 64K – 200K | 30,000 | `DEFAULT_TOKEN_BUDGET` |
| < 64K | 30,000 (instance default) | `self.token_budget` |
| None / 0 | 30,000 | `DEFAULT_TOKEN_BUDGET` |

### Token Estimation

```python
@staticmethod
def estimate_tokens(text: str) -> int:
    word_count = len(text.split())
    return max(1, int(word_count * 4 / 3))
    # 1 token ≈ 0.75 words — fast, dependency-free heuristic
```

### Budget Enforcement (`_enforce_token_budget`)

When total tokens exceed the budget, truncatable sections are progressively shortened starting from the lowest priority (highest number) upward:

```
Total tokens = sum of all section tokens + separator tokens ("\n\n")

If total > budget:
  1. Sort truncatable sections by priority DESC (P9 first, P3 last)
  2. For each section:
     a. Calculate overshoot = total - budget
     b. If overshoot >= section tokens → remove entire section content
        (leave "[Truncated: N,NNN → 0 tokens]" indicator)
     c. If overshoot < section tokens → partially truncate:
        - truncate_from="tail": keep beginning, trim end
        - truncate_from="head": keep end (newest), trim beginning
     d. Recalculate total
     e. If total <= budget → stop
```

Truncation order: PROJECTS (P10) → KNOWLEDGE (P9) → EVOLUTION (P8) → MEMORY (P7) → TOOLS (P6) → STEERING (P5) → USER (P4) → AGENT (P3). Priorities 0–2 (SWARMAI, IDENTITY, SOUL) are never truncated.

MEMORY.md and EVOLUTION.md both truncate from head — this keeps the newest content (at the bottom of the file) and discards older entries. All other files truncate from tail (keeps the beginning).

### Small Model Exclusions

For models with context window < 32K (`THRESHOLD_SKIP_LOW_PRIORITY`), KNOWLEDGE.md and PROJECTS.md are excluded entirely from assembly — they never even enter the truncation pipeline.

---

## L0/L1 Cache System

### L1 Cache (Full Assembly, ≥ 64K models)

```
Source files (11 .md files)
         │
    _assemble_from_sources()
         │
    _enforce_token_budget()
         │
    _write_l1_cache(content, budget)
         │
    L1_SYSTEM_PROMPTS.md
    ├── First line: <!-- budget:40000 -->
    └── Rest: assembled context with ## headers
```

L1 freshness check (`_is_l1_fresh()`):
1. **Git-first** (preferred): `git status --porcelain -- .context/` — if no changes, cache is fresh
2. **Mtime fallback** (git unavailable): compare L1 mtime against all source file mtimes

Budget-tier validation: L1 cache includes a `<!-- budget:NNNNN -->` header. If the cached budget doesn't match the current session's budget (e.g., user switched models), the cache is treated as stale and reassembled.

### L0 Cache (Compact, < 64K models)

`L0_SYSTEM_PROMPTS.md` is an AI-summarized compact version of all source files. Each file compressed to essential directives only. If L0 cache is missing, falls back to `_assemble_from_sources()` with aggressive truncation.

### Loading Strategy (`load_all`)

```python
def load_all(self, model_context_window=200_000):
    dynamic_budget = self.compute_token_budget(model_context_window)

    if model_context_window < 64_000:  # THRESHOLD_USE_L1
        return self._load_l0(model_context_window)

    # Try L1 cache (budget-tier validated)
    cached = self._load_l1_if_fresh(expected_budget=dynamic_budget)
    if cached:
        return cached

    # Assemble from sources, write L1 cache
    assembled = self._assemble_from_sources(model_context_window, dynamic_budget)
    if assembled:
        self._write_l1_cache(assembled, budget=dynamic_budget)
    return assembled
```

Entire method wrapped in try/except — context loading failures never block agent startup.

---

## Context Assembly in SessionRouter (`_build_system_prompt`)

This is the orchestration method that calls ContextDirectoryLoader and adds ephemeral context:

```
_build_system_prompt(agent_config, working_directory, channel_context)
  │
  ├── 1. ContextDirectoryLoader
  │     context_dir = Path(working_directory) / ".context"
  │     loader = ContextDirectoryLoader(context_dir, budget, templates_dir)
  │     loader.ensure_directory()
  │     model = _resolve_model(agent_config)
  │     model_context_window = _get_model_context_window(model)
  │     context_text = loader.load_all(model_context_window)
  │
  ├── 2. BOOTSTRAP.md (ephemeral, not in L1 cache)
  │     If .context/BOOTSTRAP.md exists:
  │       context_text = "## Onboarding\n{bootstrap}" + context_text
  │
  ├── 3. DailyActivity (ephemeral, last 2 by filename date)
  │     Scan Knowledge/DailyActivity/, sort *.md by filename DESC, take top 2
  │     Handles date gaps (weekends, holidays) — always loads most recent 2
  │     For each file:
  │       Read content, apply token cap (2000 tokens), truncate from head if over
  │       Append as "## Daily Activity ({date})\n{content}"
  │     Disk files are NEVER modified — truncation is ephemeral
  │     Also checks for .needs_distillation flag → injects "Memory Maintenance Required"
  │
  ├── 4. Inject into agent_config["system_prompt"]
  │     existing + "\n\n" + context_text
  │
  ├── 5. Metadata collection for TSCC viewer
  │     For each ContextFileSpec:
  │       Record: filename, tokens, truncated flag, user_customized
  │     Store on agent_config["_system_prompt_metadata"]
  │
  └── 6. SystemPromptBuilder.build()
        → Identity, Safety, Workspace, Datetime, Runtime
        → Returns final system prompt string
```

### Model Context Window Resolution

`_get_model_context_window()` maps model IDs to context window sizes:

```python
_MODEL_CONTEXT_WINDOWS = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-opus-4-5-20251101": 200_000,
}
_DEFAULT_CONTEXT_WINDOW = 200_000
```

Bedrock model IDs are stripped of prefix/suffix before lookup: `us.anthropic.claude-opus-4-6-v1` → `claude-opus-4-6`.

---

## Memory System — End-to-End Lifecycle

The memory system forms a closed loop: conversations produce DailyActivity → DailyActivity gets distilled into MEMORY.md → MEMORY.md loads into the next session's system prompt. Every step in the critical path is code-enforced via backend hooks — the model cannot forget to persist memory.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     1. SESSION START (Loading)                       │
│                                                                     │
│  _build_system_prompt() assembles context:                          │
│    [CODE-ENFORCED] ContextDirectoryLoader.load_all()                │
│       → Reads 11 context files from ~/.swarm-ai/.context/           │
│       → MEMORY.md at P7 (truncates from head = newest kept)         │
│       → EVOLUTION.md at P8 (truncates from head = newest kept)      │
│    [CODE-ENFORCED] DailyActivity loading                            │
│       → Scans Knowledge/DailyActivity/, sorts by filename DESC      │
│       → Loads last 2 files regardless of date gaps (weekends OK)    │
│       → Per-file token cap (2000) prevents squeezing higher ctx     │
│    [CODE-ENFORCED] Distillation flag check                          │
│       → If .needs_distillation exists → injects maintenance prompt  │
│    [CODE-ENFORCED] SystemPromptBuilder adds identity, safety, etc.  │
│                                                                     │
│  Result: Agent starts with full memory + evolution context +         │
│          session briefing (proactive intelligence)                    │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   2. DURING SESSION (In-Memory)                     │
│                                                                     │
│  [SDK-MANAGED] Claude SDK remembers everything within the session   │
│  [USER-TRIGGERED] "save memory" → s_save-memory → locked_write.py  │
│  [USER-TRIGGERED] "save activity" → s_save-activity → Write tool   │
│  [USER-TRIGGERED] 🧠 button → POST /api/memory/save-session        │
│       → memory_extractor.py → LLM extraction → locked_write.py     │
│                                                                     │
│  No automatic memory writes during session — SDK handles context    │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│               3. SESSION CLOSE (Recording — Code-Enforced)          │
│                                                                     │
│  Triggers: TTL expiry (2h) │ explicit delete │ backend shutdown    │
│                                                                     │
│  [CODE-ENFORCED] SessionLifecycleHookManager fires 4 hooks          │
│  via BackgroundHookExecutor (fire-and-forget asyncio.Task):         │
│                                                                     │
│    Hook 1: DailyActivityExtractionHook                              │
│       → Retrieves conversation log from DB (limit=500 messages)     │
│       → SummarizationPipeline extracts topics, decisions, files     │
│       → write_daily_activity() appends to YYYY-MM-DD.md (flock)    │
│       → ComplianceTracker records success/failure                   │
│                                                                     │
│    Hook 2: WorkspaceAutoCommitHook                                  │
│       → git diff --stat → categorize files → conventional commit    │
│       → One commit per session (not per-turn)                       │
│       → Uses shared git_lock to prevent .git/index.lock contention  │
│                                                                     │
│    Hook 3: DistillationTriggerHook                                  │
│       → Scans DailyActivity/*.md frontmatter (last 30 days)        │
│       → If undistilled count > 3: direct regex distillation         │
│         → Extracts decisions/lessons → locked_write.py → MEMORY.md  │
│         → Marks files as distilled: true                            │
│       → If direct distillation fails: writes .needs_distillation    │
│                                                                     │
│    Hook 4: EvolutionMaintenanceHook                                 │
│       → Scans EVOLUTION.md Capabilities + Competence sections       │
│       → Deprecates: active entries idle >30d with 0 usage           │
│       → Prunes: deprecated entries with 0 usage idle >30d           │
│       → Logs all actions to EVOLUTION_CHANGELOG.jsonl               │
│                                                                     │
│  All hooks error-isolated — failures don't block cleanup            │
│  Per-hook timeout: 30 seconds                                       │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  4. NEXT SESSION START (Loop)                       │
│                                                                     │
│  _build_system_prompt() loads updated MEMORY.md + recent            │
│  DailyActivity → agent has full accumulated knowledge               │
│                                                                     │
│  The cycle repeats. Memory is never lost.                           │
└─────────────────────────────────────────────────────────────────────┘
```

### Trigger Mechanism Classification

| Operation | Mechanism | Can model forget? |
|-----------|-----------|-------------------|
| DailyActivity extraction | `SessionLifecycleHookManager` → `DailyActivityExtractionHook` | No — code-enforced |
| Workspace auto-commit | `SessionLifecycleHookManager` → `WorkspaceAutoCommitHook` | No — code-enforced |
| Distillation (primary) | `SessionLifecycleHookManager` → `DistillationTriggerHook` | No — code-enforced |
| Evolution maintenance | `SessionLifecycleHookManager` → `EvolutionMaintenanceHook` | No — code-enforced |
| Tool failure nudge | `ToolFailureTracker` in message loop | No — code-enforced |
| Distillation (fallback) | `.needs_distillation` flag → system prompt injection | Yes — prompt-dependent |
| MEMORY.md loading | `CONTEXT_FILES` P7 → `ContextDirectoryLoader` | No — code-enforced |
| EVOLUTION.md loading | `CONTEXT_FILES` P8 → `ContextDirectoryLoader` | No — code-enforced |
| DailyActivity loading | `_build_system_prompt()` directory scan | No — code-enforced |
| Resume context injection | `context_injector.build_resume_context()` | No — code-enforced |
| One-click 🧠 save | `POST /api/memory/save-session` → `memory_extractor.py` | No — backend API |
| User "save memory" | Agent invokes `s_save-memory` skill | Yes — but user-initiated |
| User "save activity" | Agent invokes `s_save-activity` skill | Yes — but user-initiated |

### MEMORY.md — Cross-Session Persistent Memory

- Location: `~/.swarm-ai/SwarmWS/.context/MEMORY.md`
- Priority: 7 (truncatable, user-customized)
- Truncation: From head (keeps newest content at bottom)
- Content: Curated decisions, lessons learned, important context — NOT raw logs

### Locked Write (`locked_write.py`)

A CLI script called by skills to safely modify MEMORY.md under advisory file lock:

```python
def locked_read_modify_write(file_path, section, text, mode="append"):
    # 1. Open lock file (.md.lock)
    # 2. fcntl.flock(LOCK_EX | LOCK_NB) with 5s timeout
    # 3. Read current content
    # 4. Find section by ## header (regex match)
    # 5. Append or replace section content
    # 6. Write back
    # 7. Release lock
```

Usage from skills:
```bash
python locked_write.py --file PATH --section "Recent Context" --append "New content"
python locked_write.py --file PATH --section "Recent Context" --replace "Replacement"
```

Section finding: regex matches `## {section_name}` headers. If section not found, appends under `## Distilled` fallback section.

### DailyActivity — Append-Only Logs

- Location: `~/.swarm-ai/SwarmWS/Knowledge/DailyActivity/{YYYY-MM-DD}.md`
- Write: Automatic via `DailyActivityExtractionHook` at session close (code-enforced, `fcntl.flock`)
- Read: Last 2 files by filename date loaded at session start (handles gaps, ephemeral, not cached)
- Token cap: 2,000 tokens per file (`TOKEN_CAP_PER_DAILY_FILE`)
- Truncation: From head (keeps newest entries), ephemeral only — disk files never modified
- Format: YAML frontmatter (`date`, `sessions_count`, `distilled`) + `## Session — HH:MM | id | title` entries
- Auto-pruning: Archives older than 90 days via `prune_archives()` in `verify_integrity()`

### Memory Distillation

Two-path distillation system:

1. **Primary (code-enforced)**: `DistillationTriggerHook` fires at session close, checks undistilled count, runs direct regex extraction if threshold (>3) exceeded. Writes to MEMORY.md via `locked_write.py`. Marks files as `distilled: true`.
2. **Fallback (prompt-dependent)**: If direct distillation fails, writes `.needs_distillation` flag file. Next session's `_build_system_prompt()` injects "Memory Maintenance Required" instruction, prompting agent to run `s_memory-distill` skill.

### STEERING.md — Session-Level Overrides

Two-tier memory protocol:
- MEMORY.md: long-term, curated, distilled periodically
- STEERING.md: short-term, session-level rules, temporary focus areas
- Agent reads both at session start; STEERING.md takes precedence for conflicts

### Proactive Intelligence (Session Briefing)

`proactive_intelligence.py` generates a `## Session Briefing` section (~185 tokens) injected into the system prompt at session start. Pure parsing, no LLM calls, <1ms.

```
_build_system_prompt()
  │
  └── build_session_briefing(workspace_dir, memory_text)
        ├── L0: _parse_open_threads(memory_text) → thread titles, priorities, status
        ├── L1: _parse_continue_hints(daily_dir) → "Next:" lines from DailyActivity
        ├── L1: _detect_temporal_signals() → first session of day, session gap, stale P0
        ├── L2: _build_suggestions() → ScoredItem ranking (priority + staleness + blocking)
        ├── L2: _format_suggestions() → "Suggested focus" with reasoning
        ├── L3: _load_learning_state() → persistent preferences from .proactive_state.json
        └── L3: _apply_learning() → skip penalties, affinity bonuses
```

Multi-tab safe: read-only, no writes, no shared state, no locks.

---

## Knowledge Directory

```
~/.swarm-ai/SwarmWS/Knowledge/
├── Notes/           # Quick notes and scratchpad
├── Reports/         # Generated reports
├── Meetings/        # Meeting notes
├── Library/         # Reference material (migrated from legacy Knowledge Base/)
├── Archives/        # Auto-pruned at 90 days
└── DailyActivity/   # Append-only daily logs
    ├── 2026-03-05.md
    ├── 2026-03-06.md
    └── 2026-03-07.md
```

Legacy migration: `_cleanup_legacy_content()` migrates `Knowledge Base/` → `Library/` (preserves user files, removes empty legacy dir).

---

## BOOTSTRAP.md — First-Run Onboarding

Ephemeral onboarding file, NOT in CONTEXT_FILES:

```
_maybe_create_bootstrap()
  ├── If BOOTSTRAP.md already exists → skip
  ├── If USER.md doesn't exist → skip
  ├── _is_empty_template(USER.md content)?
  │   ├── Checks **Name:**, **Timezone:**, **Role:** fields
  │   ├── If all empty/placeholder → USER.md is unfilled template
  │   └── If any field has real content → user has customized
  ├── If empty template → copy BOOTSTRAP.md from backend/context/
  └── If user has content → skip (not a first-run)
```

At session start, `_build_system_prompt()` checks for BOOTSTRAP.md and prepends it as `## Onboarding` section. This is ephemeral — not included in L1 cache.

---

## What the Final System Prompt Looks Like

```
## Onboarding                          ← BOOTSTRAP.md (first-run only)
<first-run onboarding instructions>

## SwarmAI                             ← P0, never truncated
<core system prompt>

## Identity                            ← P1, never truncated
<agent name, avatar, self-description>

## Soul                                ← P2, never truncated
<personality, tone, 6 Operating Principles>

## Agent Directives                    ← P3, truncatable
<behavioral rules, safety rules>

## User                                ← P4, user-customized
<user profile, preferences, timezone>

## Steering                            ← P5, user-customized
<session-level rules, overrides>

## Tools                               ← P6, user-customized
<tool usage guidance>

## Memory                              ← P7, truncates from HEAD
<cross-session persistent memory>

## Evolution Registry                  ← P8, truncates from HEAD
<self-evolution capabilities, optimizations, corrections>

## Knowledge                           ← P9, user-customized
<domain knowledge, references>

## Projects                            ← P10, lowest priority
<active projects summary>

## Daily Activity (2026-03-07)         ← Ephemeral, 2K cap
<today's activity log>

## Daily Activity (2026-03-06)         ← Ephemeral, 2K cap
<yesterday's activity log>

You are SwarmAgent, a personal assistant running inside SwarmAI.

## Safety Principles
- You have no independent goals beyond helping the user.
- Never attempt self-preservation...

Your working directory is: `/Users/x/.swarm-ai/SwarmWS`

Current date/time: 2026-03-07 10:30 UTC / 2026-03-07 18:30 CST

`agent=SwarmAgent | model=us.anthropic.claude-opus-4-6-v1 | os=Darwin (arm64) | channel=direct`
```

---

## Token Budget Reality Check (200K model)

```
Context window: 200,000 tokens

Fixed overhead:
  System prompt (.context/ files)        ~30,000-50,000  (15-25%)
  SDK internal instructions               ~8,000          (4%)
  MCP tool definitions (5 servers)        ~10,000-20,000  (5-10%)
  ──────────────────────────────────────────────────────────
  Total overhead                          ~48,000-78,000  (24-39%)
  Remaining for conversation              ~122,000-152,000

Per conversation turn (heavy agentic):
  User message                              ~500
  Assistant reasoning + tool_use calls      ~3,000
  Tool results (Read, Bash, Grep)           ~10,000
  Assistant response                        ~2,000
  ──────────────────────────────────────────────────────────
  ~15,500 tokens per turn

Estimated turns: ~8-10 heavy turns, ~26-31 light turns
Bottleneck: tool results, not system prompt size
```

---

## Known Gap: No Project-Scoped Context Injection

The old architecture referenced an 8-layer `ContextAssembler` for project-scoped context. This module was fully removed. Currently `_build_system_prompt()` only uses:

1. `ContextDirectoryLoader` — global context from `.context/`
2. `SystemPromptBuilder` — non-file sections

When a chat is bound to a project via ChatThread, the agent receives the same global context as an unbound chat. Project-specific instructions, context files, or semantic retrieval are not implemented.

---

## File Structure Reference

```
backend/
├── core/
│   ├── context_directory_loader.py  # ContextDirectoryLoader, ContextFileSpec, CONTEXT_FILES
│   ├── system_prompt.py             # SystemPromptBuilder (non-file sections)
│   ├── session_registry.py             # _build_system_prompt(), _get_model_context_window()
│   ├── session_hooks.py             # SessionLifecycleHookManager, HookContext, Protocol
│   ├── summarization.py             # SummarizationPipeline, StructuredSummary
│   ├── daily_activity_writer.py     # write_daily_activity(), parse/write_frontmatter
│   ├── compliance.py                # ComplianceTracker, DailyMetrics
│   ├── memory_extractor.py          # LLM-powered extraction for one-click 🧠 button
│   ├── proactive_intelligence.py    # Session briefing, open thread parsing, learning state
│   └── frontmatter.py              # parse_frontmatter(), write_frontmatter()
├── hooks/
│   ├── daily_activity_hook.py       # DailyActivityExtractionHook
│   ├── auto_commit_hook.py          # WorkspaceAutoCommitHook
│   ├── distillation_hook.py         # DistillationTriggerHook
│   ├── evolution_maintenance_hook.py# EvolutionMaintenanceHook
│   └── evolution_trigger_hook.py    # ToolFailureTracker
├── routers/
│   └── memory.py                    # /api/memory-compliance, /api/memory/save-session
├── context/                         # Default templates
│   ├── SWARMAI.md ... PROJECTS.md   # 11 source file templates + EVOLUTION_CHANGELOG.jsonl
│   ├── BOOTSTRAP.md                 # First-run onboarding template
│   ├── L0_SYSTEM_PROMPTS.md         # Compact cache template
│   ├── L1_SYSTEM_PROMPTS.md         # Full cache template
│   └── USER.example.md             # Example user profile
├── scripts/
│   └── locked_write.py              # Locked MEMORY.md/EVOLUTION.md modification
└── skills/
    ├── s_save-memory/               # User-triggered MEMORY.md writes
    ├── s_save-activity/             # User-triggered DailyActivity writes
    └── s_memory-distill/            # Agent-driven distillation (fallback path)
```
