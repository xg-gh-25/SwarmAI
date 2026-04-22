# Skillify Session

You are capturing this session's repeatable process as a reusable SwarmAI skill following the Agent Skills standard.

## Step 1: Analyze the Session

Before asking any questions, analyze the full conversation to identify:
- What repeatable process was performed
- What the inputs/parameters were
- The distinct steps (in order)
- The success artifacts/criteria (e.g. not just "writing code," but "a working implementation with no errors") for each step
- Where the user corrected or steered you
- What tools were needed
- What the goals and success artifacts were

## Step 2: Interview the User

Ask the user questions to refine the skill. Keep it conversational, do not over-ask for simple processes.

**Round 1: High level confirmation**
- Suggest a name and description for the skill based on your analysis. Ask the user to confirm or rename.
- Suggest high-level goal(s) and specific success criteria for the skill.

**Round 2: More details**
- Present the high-level steps you identified as a numbered list. Tell the user you will dig into the detail in the next round.
- If the skill requires arguments, suggest them based on what you observed.

**Round 3: Breaking down each step**
For each major step, if not glaringly obvious, ask:
- What does this step produce that later steps need? (data, artifacts, IDs)
- What proves that this step succeeded and we can move on?
- Should the user be asked to confirm before proceeding? (especially for irreversible actions)
- Are any steps independent and could run in parallel?
- What are the hard constraints or hard preferences?

You may do multiple rounds here, one per step, especially if there are more than 3 steps. Iterate as much as needed.

Pay special attention to places where the user corrected you during the session, to help inform your design.

**Round 4: Final questions**
- Confirm when this skill should be invoked, and suggest trigger phrases. Example: "Use when the user wants to process a meeting transcript. Examples: 'process this meeting', 'clean up transcript', 'inbox'."
- Ask for any other gotchas or things to watch out for.

Stop interviewing once you have enough information.

## Step 3: Write the SKILL.md

Create the skill directory and SKILL.md file at:
```
~/.swarm-ai/skills/<skill-name>/SKILL.md
```

This is the standard location for user-created skills in the three-tier model (Built-in, User, Plugin).

Use this format:

```markdown
---
name: <skill-name>
description: >
  <One-line purpose sentence>.
  TRIGGER: "<phrase 1>", "<phrase 2>", "<phrase 3>".
  DO NOT USE: <when a different skill or approach is better> (use <alternative> instead).
tier: lazy
---

# <Skill Title>

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "<phrase 1>", "<phrase 2>", "<phrase 3>"
DO NOT USE: <when a different skill or approach is better> (use <alternative> instead)
```

Then write the full workflow into `INSTRUCTIONS.md` in the same directory:

```markdown
# <Skill Title>

Description of what this skill does and why.

## Inputs
- `$arg_name`: Description of this input (only if the skill takes parameters)

## Goal

Clearly stated goal for this workflow. Define specific artifacts or criteria for completion.

## Steps

### 1. Step Name

What to do in this step. Be specific and actionable. Include commands when appropriate.

**Success criteria**: What proves this step is done and we can move on.

### 2. Next Step Name

...
```

**Per-step annotations (include when relevant):**
- **Success criteria** is REQUIRED on every step
- **Artifacts**: Data this step produces that later steps need (e.g., PR number, file path)
- **Human checkpoint**: When to pause and ask the user before proceeding (irreversible actions, error judgment, output review)
- **Rules**: Hard rules for the workflow. User corrections during the session are especially useful here

**Step structure tips:**
- Steps that can run concurrently use sub-numbers: 3a, 3b
- Steps requiring the user to act get `[human]` in the title
- Keep simple skills simple. A 2-step skill does not need annotations on every step

**Frontmatter rules:**
- `name`: Must match folder name. Lowercase, numbers, hyphens only (max 64 chars)
- `description`: Must follow this schema:
  - First line: one-sentence purpose (start with action verb)
  - `TRIGGER:` line with quoted phrases the user would say
  - `DO NOT USE:` line with boundary and alternative skill
  - Max 1024 chars total. Critical for activation — SwarmAI matches this against user requests
- `tier`: Always set to `lazy` for new skills (default). Only use `always` for proven high-frequency skills.

**Progressive disclosure pattern:**
- SKILL.md = stub for session-start discovery (lightweight, ~25 tokens in system-reminder)
- INSTRUCTIONS.md = full workflow loaded on invocation (only when needed)
- Supporting files (REFERENCE.md, scripts/) = loaded on demand within the workflow

### Step 3.5: Generate manifest.yaml (if scripts detected)

If the session involved running Python/JS scripts, shell commands, or using templates, generate a `manifest.yaml` alongside SKILL.md:

```yaml
name: <skill-name>
version: "1.0.0"
tier: lazy

scripts:
  - path: scripts/<main_script>.py
    description: "<What it does>"
    entry: true
    args: "<typical arguments>"
  - path: scripts/<helper>.py
    description: "<What it does>"

dependencies:
  python: ["<package1>", "<package2>"]

timeout: 120
```

Place executable scripts in `scripts/` subdirectory. The manifest tells the agent what's available without parsing SKILL.md.

## Step 4: Confirm and Save

Before writing files, output all generated content in markdown code blocks for review:
1. **SKILL.md** (stub with frontmatter + triggers)
2. **INSTRUCTIONS.md** (full workflow)
3. **manifest.yaml** (only if scripts were detected in Step 3.5)

Then ask: "Does this look good to save?"

**Directory structure created:**
```
~/.swarm-ai/skills/<skill-name>/
├── SKILL.md              ← Stub (loaded at session start for discovery)
├── INSTRUCTIONS.md       ← Full workflow (loaded on invocation)
└── manifest.yaml         ← Package descriptor (only if scripts exist)
    └── scripts/          ← Executable scripts (only if applicable)
```

After writing, tell the user:
- Where the skill was saved and what files were created
- **Important:** The skill will be available in your **next chat session**. To use it now, you need to start a new chat session (the current session's Claude SDK client has already scanned for skills and won't detect new ones until restarted).
- How it will activate (automatically when SwarmAI matches the description to a user request)
- That they can edit SKILL.md (triggers/description) or INSTRUCTIONS.md (workflow) directly

**Why the delay?** The Claude SDK client scans for skills once when a chat session starts. It reuses the same client throughout the session for performance. New skills created during the session are saved to disk and will be discovered when the next session starts.

## References

For the SKILL.md format specification, see the Agent Skills standard at https://agentskills.io/specification

