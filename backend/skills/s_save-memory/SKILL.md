---
name: Save Memory
description: Update persistent memory that loads automatically in future sessions. Use when user says "save memory", "remember this", "update memory", or "persist learnings". Not for agent handoffs (use save-context instead).
---

## Memory Save Skill

You manage a persistent memory system that preserves session knowledge across conversations. Memory is stored per-project at `~/.swarm-ai/projects/<project-name>/memory/`.

### First-run setup

Before doing anything else, check if `~/.swarm-ai/steering/memory.md` exists. If it does NOT exist, create it with the following content:

```markdown
# Persistent Memory System

You have a persistent memory system that stores knowledge across sessions. Memory lives at `~/.swarm-ai/projects/<project-name>/memory/MEMORY.md`.

## At session start

Check if a MEMORY.md file exists for the current project at `~/.swarm-ai/projects/<project-name>/memory/MEMORY.md`. If it exists, read it silently to restore context from previous sessions. Also check for any topic files in the same directory and note their existence for on-demand reading.

Derive the project name with: `basename "$(pwd)" | tr '[:upper:] ' '[:lower:]-'` (e.g., `My-Cool-Project` becomes `my-cool-project`).

## During the session

When you discover something worth persisting (debugging insights, project patterns, architecture decisions, user corrections), note it mentally. You do not need to write to memory on every turn.

If the user asks you to "remember" something specific, update the memory file immediately.

## At session end

When the user says "save memory", "update memory", "remember this", or similar, use the memory-save skill to persist the session knowledge.

## Reading topic files

The memory directory may contain topic files beyond MEMORY.md (e.g., `debugging.md`, `architecture.md`). These are NOT loaded at startup. Read them on demand when you need detailed information about a specific topic referenced in MEMORY.md.
```

This ensures the memory system is fully active for all future sessions after the skill is used once.

### Memory directory structure

```
~/.swarm-ai/projects/<project-name>/memory/
  MEMORY.md           # Concise index, loaded into every session
  debugging.md        # Detailed notes on debugging patterns (optional)
  architecture.md     # Architecture decisions (optional)
  ...                 # Any other topic files you create
```

### How to save memory

Follow these steps IN ORDER. Do not skip or combine steps.

#### Step 1: Determine project name

```bash
basename "$(pwd)" | tr '[:upper:] ' '[:lower:]-'
```

#### Step 2: Ensure memory directory exists

```bash
mkdir -p ~/.swarm-ai/projects/<project-name>/memory
```

#### Step 3: Read existing MEMORY.md

```bash
cat ~/.swarm-ai/projects/<project-name>/memory/MEMORY.md 2>/dev/null || echo "NO_MEMORY_FILE"
```

If `NO_MEMORY_FILE`, read `TEMPLATE.md` in this skill directory, use it as the starting structure, write the new file, and STOP.

If the file exists, you MUST read its full content before proceeding. Do NOT proceed to Step 4 without completing this step.

#### Step 4: Update specific sections using str_replace

DO NOT use `fs_write create`. The file already exists. You MUST use `fs_write str_replace` to update individual sections.

For each section you need to update:
1. Identify the exact existing text (old_str) from what you read in Step 3
2. Write the replacement text (new_str) that merges existing + new content
3. Apply the str_replace

Common updates:
- **Current State**: replace the existing content with what's current now
- **Worklog (current session)**: replace with this session's worklog. If there was already a "current session" worklog, rename it to "previous sessions" first.
- **Errors and Corrections**: append new errors to the existing list, never remove old ones
- **Learnings**: append new learnings to the existing list, never remove old ones
- **Key results**: append new results

If you need to add an entirely new section that doesn't exist yet, use `fs_write append`.

#### Rules

- NEVER use `fs_write create` on an existing MEMORY.md. This is the single most important rule.
- NEVER remove or condense previous session content unless it exceeds 200 lines total
- Previous errors, corrections, and learnings are PERMANENT. Never delete them.
- Each str_replace must include enough context in old_str to be unique
- Keep MEMORY.md under 200 lines total. If approaching the limit, move detailed content to topic files.

#### What to record

- Project patterns and conventions discovered during the session
- Debugging insights: what failed, why, and the fix
- Architecture decisions and their rationale
- Commands that work (and those that do not)
- Corrections the user made to your output
- Key results or outputs the user requested

#### What NOT to record

- Information already in project steering files or AGENTS.md
- Generic knowledge not specific to this project
- Temporary or one-off context that will not matter next session
