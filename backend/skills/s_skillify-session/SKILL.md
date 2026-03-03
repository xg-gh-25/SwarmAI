---
name: Skillify Session
description: Convert the current conversation into a reusable skill by extracting the workflow that was just performed. Use when the user says "skillify", "turn this into a skill", "make this repeatable", "save this workflow as a skill", or "create a skill from this session".
---

# Skillify Current Session

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
- Ask where the skill should be saved:
  - **This project** (`.swarm-ai/skills/<name>/SKILL.md`) for project-specific workflows
  - **Personal/global** (`~/.swarm-ai/skills/<name>/SKILL.md`) for cross-project workflows

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

Create the skill directory and SKILL.md file at the location chosen in Round 2.

Use this format:

```markdown
---
name: <skill-name>
description: <One-line description. Start with what action it performs. Include trigger phrases. Max 1024 chars.>
---

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
- `description`: Critical for activation. Start with the action verb. Include trigger phrases. SwarmAI matches this against user requests to decide when to load the full skill

## Step 4: Confirm and Save

Before writing the file, output the complete SKILL.md content in a markdown code block so the user can review it. Then ask: "Does this look good to save?"

After writing, tell the user:
- Where the skill was saved
- How it will activate (automatically when SwarmAI matches the description to a user request)
- That they can edit the SKILL.md directly to refine it

## References

For the SKILL.md format specification, see the Agent Skills standard at https://agentskills.io/specification
