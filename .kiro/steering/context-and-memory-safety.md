---
inclusion: fileMatch
fileMatchPattern: ["backend/core/context_directory_loader.py", "backend/core/context_injector.py", "backend/core/system_prompt.py", "backend/core/prompt_builder.py", "backend/core/memory_extractor.py", "backend/core/daily_activity_writer.py", "backend/hooks/*.py", "backend/core/ingestion/writer.py", "backend/context/*.md"]
---

# Context and Memory Safety Principles

The file list, priorities, and budgets are summarized in AGENTS.md "Context and Memory
System". The source of truth is `CONTEXT_FILES` and `compute_token_budget` in
`backend/core/context_directory_loader.py`. This file covers the invariants that are easy
to break.

## Core Invariant: Context Loading Never Blocks Agent Startup

The assembly pipeline (`ContextDirectoryLoader.load_all()` →
`PromptBuilder.build_system_prompt()` in `prompt_builder.py` → `SystemPromptBuilder.build()`)
must degrade gracefully. A failure at any stage starts the agent with reduced context; it
never crashes the session.

## Ownership and Two-Mode Copy (ensure_directory)

These rules are the user trust model. Violating them destroys user customizations.

1. System files (`user_customized=False`: SWARMAI, IDENTITY, SOUL, AGENT) are ALWAYS
   overwritten from the template, only when the bytes changed, and set to 0o444.
2. Everything with `user_customized=True` (SELF, USER, STEERING, TOOLS, MEMORY, EVOLUTION,
   KNOWLEDGE) is copied ONLY if missing and never overwritten (0o644). SELF.md is
   runtime-owned: the live `.context/SELF.md` is authoritative, so a template edit does not
   reach an existing install.
3. New context files go into `CONTEXT_FILES` with an explicit priority and ownership.
   PROJECTS.md was removed (2026-08-14); don't reintroduce an auto-generated index file.

## Token Budget: Warn, Never Truncate

- Budget tiers (`compute_token_budget`): ≥500K window → 150K, ≥200K → 50K, ≥64K → 30K,
  <64K → instance default (L0 compact path). Below 32K, KNOWLEDGE.md is skipped.
- The read-line NEVER truncates. Over budget, `_enforce_token_budget` logs an
  `over_budget` warning and returns full content. `_truncate_section` was deleted on
  2026-08-14; the `truncatable`/`direction` fields are legacy and inert.
- Size is bounded by whole-entry SELECTION, not by trimming files: over budget,
  `core/memory_injection.select_context_file` injects the highest-scoring whole MEMORY /
  EVOLUTION entries (24K / 8K tokens), query-free and stable between file edits and day
  boundaries; recall reaches the rest. No job archives or evicts entries (the size valves
  were removed at the CDF cutover). If the prompt is too big, tune the selector budget,
  never the loader, and never add a write-side trimmer.
- Don't flip `truncatable` to False on MEMORY/EVOLUTION: `core_section_names()` treats
  `truncatable is False` as "conceptual core".

## L0/L1 Cache Correctness

- L1 cache (`L1_SYSTEM_PROMPTS.md`) carries a `<!-- budget:NNNNN -->` header. If the cached
  budget ≠ the current session budget (model switch), the cache is STALE and must be
  reassembled.
- L1 freshness: git-first check (`git status --porcelain`), mtime fallback.
- L0 is the compact cache for <64K models, separate from L1.
- Never edit a cache file. It is derived — edit the source `.context/*.md` files.

## Memory Lifecycle — Code-Enforced Path

```
Conversation → DailyActivity (hook) → Distillation (hook) → MEMORY.md → next session
```

- Post-session hooks are registered in `main.py` lifespan (the authoritative list and
  order — don't enumerate them elsewhere). They run through `BackgroundHookExecutor`: one
  serial worker, error-isolated, 180s per hook. They never block chat.
- MEMORY.md / EVOLUTION.md load via `CONTEXT_FILES`. DailyActivity: whole session rows
  and day summaries of the last 7 days, selected by `memory_injection.select_daily_activity`
  (4K tokens, skipped for channel sessions, disk never modified).
- Resume: `context_injector.build_resume_context()` on cold resume.
- Prompt-dependent by design: user-invoked skills (save-memory, save-activity), which
  submit to the ingestion Inbox.

## File Locking Rules

- MEMORY.md / EVOLUTION.md / USER / STEERING / TOOLS / DDD docs: written ONLY by CDF's
  ingestion writer (`core/ingestion/writer.py`, under the `.md.lock` sidecar). Skills and
  hooks submit units (`ingest submit` / a source's `enqueue`); a user edit goes through
  `governed_edit.apply_user_edit` with `expected_revision`. `locked_write.py` is gone.
- DailyActivity: append-only with `O_APPEND`, no lock.
- L1 cache: single writer (ContextDirectoryLoader), no lock; stale reads are harmless.
- Never write untrusted external text into MEMORY.md as a standing directive
  (security baseline B5).

## BOOTSTRAP.md — Ephemeral Onboarding

- NOT in `CONTEXT_FILES` and never cached in L1. Created only when USER.md is an unfilled
  template (`_is_empty_template()`), injected ephemerally, gone once USER.md is filled.
- Test the first-run experience before changing its detection logic.

## Regression Checklist

When modifying context or memory code:

- [ ] `ensure_directory()` still respects two-mode copy (system=overwrite, user=copy-if-missing)
- [ ] System files still get 0o444 after write; user files keep 0o644
- [ ] `compute_token_budget` tiers unchanged, or AGENTS.md updated with them
- [ ] Nothing truncates on the read-line; over-budget only warns
- [ ] L1 cache includes the budget header and validates it on load
- [ ] DailyActivity files are never modified on disk during loading
- [ ] No code writes MEMORY.md / EVOLUTION.md directly (`test_cdf_materializer_static` green)
- [ ] Hooks stay fire-and-forget through `BackgroundHookExecutor` and error-isolated
- [ ] `build_resume_context()` still drops the last assistant message
- [ ] New context files have correct priority and ownership in `CONTEXT_FILES`
