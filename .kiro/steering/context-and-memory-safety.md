---
inclusion: fileMatch
fileMatchPattern: "backend/core/context_directory_loader.py,backend/core/system_prompt.py,backend/core/agent_manager.py,backend/core/memory_extractor.py,backend/core/daily_activity_writer.py,backend/hooks/*.py,backend/scripts/locked_write.py,backend/context/*.md"
---

# Context and Memory Safety Principles

## Core Invariant: Context Loading Never Blocks Agent Startup

The entire context assembly pipeline (`ContextDirectoryLoader.load_all()` → `_build_system_prompt()` → `SystemPromptBuilder.build()`) is wrapped in try/except. A failure at any stage MUST degrade gracefully — the agent starts with reduced context rather than crashing.

## The 11 Context Files — Priority and Ownership

```
P0  SWARMAI.md     System  Never truncated   App's core identity
P1  IDENTITY.md    System  Never truncated   Agent name/avatar
P2  SOUL.md        System  Never truncated   Personality/principles
P3  AGENT.md       System  Truncatable       Behavioral rules
P4  USER.md        User    Truncatable       User profile
P5  STEERING.md    User    Truncatable       Session-level rules
P6  TOOLS.md       User    Truncatable       Tool guidance
P7  MEMORY.md      User    Head-truncated    Cross-session memory
P8  EVOLUTION.md   User    Head-truncated    Self-evolution registry
P9  KNOWLEDGE.md   User    Truncatable       Domain knowledge
P10 PROJECTS.md    User    Lowest priority   Active projects
```

## Two-Mode Copy Rules (ensure_directory)

These rules are the foundation of the user trust model. Violating them destroys user customizations.

1. System files (P0-P3, `user_customized=False`): ALWAYS overwrite on startup. Permissions set to 0o444 (readonly). These are the app's voice — updated on every release.
2. User files (P4-P10, `user_customized=True`): Copy ONLY if missing. Never overwrite. Permissions 0o644. These are the user's data.
3. Byte-comparison optimization: System files are only rewritten if content actually changed (avoids unnecessary disk IO and git noise).

## Token Budget Invariants

- Budget scales dynamically: ≥200K model → 40K tokens, 64K-200K → 25K, <64K → 25K (instance default)
- Truncation order is strictly by priority DESC: P10 → P9 → P8 → ... → P3. P0-P2 are NEVER truncated.
- MEMORY.md and EVOLUTION.md truncate from HEAD (discard oldest, keep newest at bottom)
- All other truncatable files truncate from TAIL (keep beginning, discard end)
- For models <32K, KNOWLEDGE.md and PROJECTS.md are excluded entirely (never enter truncation pipeline)

## L0/L1 Cache Correctness

- L1 cache (`L1_SYSTEM_PROMPTS.md`) includes a `<!-- budget:NNNNN -->` header for budget-tier validation
- If cached budget ≠ current session budget (user switched models), cache is STALE — must reassemble
- L1 freshness: git-first check (`git status --porcelain`), mtime fallback if git unavailable
- L0 cache is AI-summarized compact version for <64K models — separate from L1

Anti-pattern: Modifying L1 cache directly instead of modifying source files. The cache is derived — always edit the source `.context/*.md` files.

## Memory System — Critical Path Safety

The memory lifecycle is a closed loop. Every step in the critical path is code-enforced via backend hooks:

```
Conversation → DailyActivity (hook) → Distillation (hook) → MEMORY.md → Next session
```

### Code-Enforced (Cannot Be Forgotten)
- DailyActivity extraction: `DailyActivityExtractionHook` at session close
- Workspace auto-commit: `WorkspaceAutoCommitHook` at session close
- Distillation trigger: `DistillationTriggerHook` at session close
- MEMORY.md loading: `CONTEXT_FILES` P7 in `ContextDirectoryLoader`
- EVOLUTION.md loading: `CONTEXT_FILES` P8 in `ContextDirectoryLoader`
- DailyActivity loading: `_build_system_prompt()` directory scan

### Prompt-Dependent (By Design)
- Self-evolution trigger detection (agent self-monitoring)
- Distillation flag fallback (`.needs_distillation` → system prompt injection)
- User-invoked skills (save-memory, save-activity)

## File Locking Rules

- MEMORY.md: ALWAYS use `locked_write.py` (fcntl.flock advisory lock via `.md.lock` file)
- DailyActivity: Append-only with OS `O_APPEND` flag — no lock needed (atomic appends)
- EVOLUTION.md: Use `locked_write.py` for field updates (`--increment-field`, `--set-field`); Read+Edit for new entries
- L1 cache: No lock — single writer (ContextDirectoryLoader), stale reads are harmless

Anti-pattern: Writing to MEMORY.md directly via file write tools without `locked_write.py` — risks data corruption if concurrent writes occur (e.g., distillation hook + user skill).

## DailyActivity Safety

- Files are NEVER modified on disk during context loading — truncation is ephemeral (in-memory only)
- Per-file token cap (2000 tokens) prevents DailyActivity from squeezing higher-priority context
- Last 2 files loaded by filename date (handles weekends/gaps — always most recent 2)
- Frontmatter tracks `distilled: true/false` and `sessions_count` — used by distillation trigger

## BOOTSTRAP.md — Ephemeral Onboarding

- NOT in CONTEXT_FILES — detected separately, never cached in L1
- Created only when USER.md is an unfilled template (`_is_empty_template()` structural check)
- Prepended as `## Onboarding` section — disappears once user fills in USER.md
- Never modify BOOTSTRAP.md detection logic without testing the first-run experience

## Regression Checklist

When modifying context or memory code:

- [ ] `ensure_directory()` still respects two-mode copy (system=overwrite, user=copy-if-missing)
- [ ] System files still get 0o444 permissions after write
- [ ] Token budget computation matches model context window correctly
- [ ] Truncation order follows priority DESC (P10 first, P3 last)
- [ ] MEMORY.md and EVOLUTION.md still truncate from HEAD
- [ ] L1 cache includes budget header and validates on load
- [ ] DailyActivity files are never modified on disk during loading
- [ ] `locked_write.py` is used for all MEMORY.md writes
- [ ] Session lifecycle hooks still fire in correct order (DailyActivity → AutoCommit → Distillation)
- [ ] Each hook is error-isolated (failure doesn't block subsequent hooks)
- [ ] New context files added to CONTEXT_FILES get correct priority, truncation direction, and ownership
