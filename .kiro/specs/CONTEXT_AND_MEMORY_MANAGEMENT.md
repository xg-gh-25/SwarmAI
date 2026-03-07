# SwarmAI Context and Memory Management — End-to-End Architecture

## Overview

SwarmAI's context and memory system gives agents persistent identity, personality, knowledge, and cross-session memory. All context lives in a single hidden directory (`~/.swarm-ai/SwarmWS/.context/`) using filesystem-only storage — no database for context content. The system assembles context into the system prompt on every session start, with dynamic token budgets and L0/L1 caching for different model sizes.

Three cooperating systems build the final system prompt:
1. `ContextDirectoryLoader` — reads 10 source files from `.context/`, enforces token budget
2. `_build_system_prompt()` in AgentManager — orchestrates assembly, adds ephemeral context
3. `SystemPromptBuilder` — appends non-file sections (safety, datetime, runtime metadata)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    CONTEXT ASSEMBLY PIPELINE                     │
│                                                                  │
│  backend/context/          ~/.swarm-ai/SwarmWS/.context/         │
│  (templates, 12 files)     (runtime, 10 source + 2 cache)       │
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
│  │         (AgentManager)                        │               │
│  │                                               │               │
│  │  1. ContextDirectoryLoader output             │               │
│  │  2. BOOTSTRAP.md (ephemeral, first-run)       │               │
│  │  3. DailyActivity (today + yesterday, 2K cap) │               │
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

## The 10 Context Files

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
    ContextFileSpec("SWARMAI.md",    0, "SwarmAI",          False, False, "tail"),
    ContextFileSpec("IDENTITY.md",   1, "Identity",         False, False, "tail"),
    ContextFileSpec("SOUL.md",       2, "Soul",             False, False, "tail"),
    ContextFileSpec("AGENT.md",      3, "Agent Directives", True,  False, "tail"),
    ContextFileSpec("USER.md",       4, "User",             True,  True,  "tail"),
    ContextFileSpec("STEERING.md",   5, "Steering",         True,  True,  "tail"),
    ContextFileSpec("TOOLS.md",      6, "Tools",            True,  True,  "tail"),
    ContextFileSpec("MEMORY.md",     7, "Memory",           True,  True,  "head"),
    ContextFileSpec("KNOWLEDGE.md",  8, "Knowledge",        True,  True,  "tail"),
    ContextFileSpec("PROJECTS.md",   9, "Projects",         True,  True,  "tail"),
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
| KNOWLEDGE.md | 8 | Knowledge | Yes | User | Domain knowledge, API references, codebase conventions |
| PROJECTS.md | 9 | Projects | Yes | User | Active projects summary, priorities, status |

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
| ≥ 200K tokens | 40,000 | `BUDGET_LARGE_MODEL` |
| 64K – 200K | 25,000 | `DEFAULT_TOKEN_BUDGET` |
| < 64K | 25,000 (instance default) | `self.token_budget` |
| None / 0 | 25,000 | `DEFAULT_TOKEN_BUDGET` |

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

Truncation order: PROJECTS (P9) → KNOWLEDGE (P8) → MEMORY (P7) → TOOLS (P6) → STEERING (P5) → USER (P4) → AGENT (P3). Priorities 0–2 (SWARMAI, IDENTITY, SOUL) are never truncated.

MEMORY.md is the only file that truncates from head — this keeps the newest content (at the bottom of the file) and discards older entries.

### Small Model Exclusions

For models with context window < 32K (`THRESHOLD_SKIP_LOW_PRIORITY`), KNOWLEDGE.md and PROJECTS.md are excluded entirely from assembly — they never even enter the truncation pipeline.

---

## L0/L1 Cache System

### L1 Cache (Full Assembly, ≥ 64K models)

```
Source files (10 .md files)
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

## Context Assembly in AgentManager (`_build_system_prompt`)

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
  ├── 3. DailyActivity (ephemeral, today + yesterday)
  │     For each of [today, yesterday]:
  │       Read Knowledge/DailyActivity/{date}.md
  │       If token_count > 2000: _truncate_daily_content() (keep tail/newest)
  │       Append as "## Daily Activity ({date})\n{content}"
  │     Disk files are NEVER modified — truncation is ephemeral
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

## Memory System

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
- Write mode: OS `O_APPEND` flag — no lock needed (atomic appends)
- Read: Today + yesterday loaded at session start (ephemeral, not cached)
- Token cap: 2,000 tokens per file (`TOKEN_CAP_PER_DAILY_FILE`)
- Truncation: From head (keeps newest entries), ephemeral only — disk files never modified
- Auto-pruning: Archives older than 90 days via `prune_archives()` in `verify_integrity()`

### Memory Distillation

Agent-driven via `s_memory-distill` skill:
- Reads MEMORY.md, identifies stale/redundant entries
- Compresses and rewrites via `locked_write.py`
- Frontmatter tracking: `parse_frontmatter()` / `write_frontmatter()` in `frontmatter.py`
- DailyActivity files get `distilled: true` frontmatter after processing

### STEERING.md — Session-Level Overrides

Two-tier memory protocol:
- MEMORY.md: long-term, curated, distilled periodically
- STEERING.md: short-term, session-level rules, temporary focus areas
- Agent reads both at session start; STEERING.md takes precedence for conflicts

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

## Knowledge                           ← P8, user-customized
<domain knowledge, references>

## Projects                            ← P9, lowest priority
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
  System prompt (.context/ files)        ~25,000-40,000  (12-20%)
  SDK internal instructions               ~8,000          (4%)
  MCP tool definitions (5 servers)        ~10,000-20,000  (5-10%)
  ──────────────────────────────────────────────────────────
  Total overhead                          ~43,000-68,000  (21-34%)
  Remaining for conversation              ~132,000-157,000

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
│   ├── agent_manager.py             # _build_system_prompt(), _get_model_context_window()
│   └── frontmatter.py              # parse_frontmatter(), write_frontmatter()
├── context/                         # Default templates (12 files)
│   ├── SWARMAI.md ... PROJECTS.md   # 10 source file templates
│   ├── BOOTSTRAP.md                 # First-run onboarding template
│   ├── L0_SYSTEM_PROMPTS.md         # Compact cache template
│   ├── L1_SYSTEM_PROMPTS.md         # Full cache template
│   └── USER.example.md             # Example user profile
└── scripts/
    └── locked_write.py              # Locked MEMORY.md modification
```
