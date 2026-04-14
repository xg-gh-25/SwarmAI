# Skill Builder

Create, evaluate, and improve Agent skills to production quality.

## Quick Start

| Mode | When to Use | Starting Step |
|------|-------------|---------------|
| **Create** | Building a new skill from scratch | Step 1 |
| **Evaluate** | Scoring an existing skill | Step 4 |
| **Improve** | Upgrading a skill to 100/100 | Step 5 |

## Skill Files

| File | Purpose |
|------|---------|
| `SKILL.md` | This workflow |
| `SCORING.md` | Structure + Efficacy rubrics (MUST READ before scoring) |
| `TEMPLATES.md` | Starter templates and patterns (MUST READ before creating) |
| `EXAMPLES.md` | Before/after improvement examples |
| `CHECKLIST.md` | 50-point validation checklist |

---

## Mode 1: Create a New Skill

### Step 1: Gather Requirements

Ask the user:
1. **What does the skill do?** (core capability)
2. **When should it activate?** (trigger contexts)
3. **What tools/scripts are needed?** (dependencies)
4. **What's the expected output?** (deliverables)
5. **What input quality issues are common?** (see Input Decomposition below)
6. **What does this assume the user knows?** (see User Assumptions below)

#### Input Decomposition

> [!IMPORTANT]
> **Most real-world inputs are messy.** If the domain typically has vague, incomplete, or poorly-structured input, the skill MUST include a transformation step.

Ask: "What does bad input look like in this domain?"

| Input Quality | Skill Must Include |
|---------------|-------------------|
| Usually clean and structured | No transformation needed |
| Sometimes vague or incomplete | Validation step that asks for clarification |
| Often messy or ambiguous | **Decomposition step** with probing questions to transform input |

**Decomposition step pattern:**
```markdown
### Step N: Decompose Input

Transform raw input into structured form using these probes:

| Probe | Purpose |
|-------|---------|
| "What specifically happened?" | Extract concrete actions |
| "What was the outcome?" | Capture measurable results |
| "How often does this occur?" | Establish patterns |
```

#### User Capability Assumptions

List what the skill assumes the user can do. For each assumption, either:
- **(a) Remove it** by adding a compensating step, OR
- **(b) Document it** as a prerequisite

| Assumption | Compensation Strategy |
|------------|----------------------|
| User can provide structured input | Add decomposition step |
| User knows domain terminology | Add glossary or explain inline |
| User can make judgment calls | Add decision logic with explicit criteria |
| User knows quality standards | Add validation checklist |

### Step 1.5: Identify the Hardest Parts

> [!CRITICAL]
> **State-of-the-art skills solve the hard problems, not just the easy ones.** Before designing the workflow, identify where experts struggle and novices get stuck.

Ask: "What are the 2-3 hardest judgment calls in this domain?"

**Signs of a hard judgment call:**
- Experts disagree on the right answer
- Multiple valid options exist
- Context determines the best choice
- Novices consistently get it wrong

**For each hard part, the skill MUST include:**

| Hard Part Type | Required Solution |
|----------------|-------------------|
| Ambiguous categorization | Disambiguation logic with explicit criteria |
| Quality/intensity judgment | Calibration guidance with thresholds |
| Context-dependent choice | Decision matrix or if/then rules |
| Subjective evaluation | Rubric with concrete examples |

**Example pattern for disambiguation:**
```markdown
| If X could be A or B... | Ask this to disambiguate |
|-------------------------|--------------------------|
| [Ambiguous situation 1] | Was the emphasis on [criterion]? → A. On [other criterion]? → B |
| [Ambiguous situation 2] | Did it primarily [test for A] or [test for B]? |
```

> [!WARNING]
> **A lookup table is not disambiguation.** If your skill has a reference table but no logic for handling cases that match multiple entries, it's incomplete.

### Step 1.7: Design for Progressive Disclosure

> [!IMPORTANT]
> **Skills share the context window.** Claude is already very smart -- only add information it doesn't already possess. Every token in a skill costs context budget.

**Three-tier context loading model (aligned with AIM progressive disclosure):**

| Tier | What | Budget | When Loaded |
|------|------|--------|-------------|
| **Metadata** | Frontmatter `name` + `description` | ~100 words | Always (skill discovery) |
| **SKILL.md body** | Core workflow, steps, rules | <5,000 words | When skill is triggered |
| **Bundled resources** | references/, scripts/, manifest.yaml | Unlimited on disk | Conditionally, on demand |

**Skill loading tier (`tier` in frontmatter):**

| Tier | System-Reminder | When to Use |
|------|-----------------|-------------|
| **always** | Full description injected at session start | High-frequency skills used in >10% of sessions (save-memory, slack, radar-todo, etc.) |
| **lazy** (default) | Stub only — name + trigger + "call to activate" | Everything else. Full instructions in INSTRUCTIONS.md, loaded on invocation |

**Lazy skill pattern (default for all new skills):**
- SKILL.md = minimal stub (frontmatter + triggers + one-line purpose)
- INSTRUCTIONS.md = full workflow (what would otherwise be the SKILL.md body)
- At session start: agent sees one-liner for routing
- On invocation: agent reads INSTRUCTIONS.md for full instructions, then reference files as needed

> [!TIP]
> Only promote a skill to `tier: always` after it's proven high-frequency. Start lazy, promote based on usage data from SkillMetrics.

**Design rules:**
- Metadata must be ruthlessly concise -- it's loaded in every session for routing
- Description MUST be under 1024 characters (AIM spec limit, enforced for routing accuracy)
- For always-tier skills: SKILL.md contains the complete workflow
- For lazy-tier skills: SKILL.md is a stub, INSTRUCTIONS.md has the workflow
- Move variant-specific details, lookup tables, and large reference data into separate files
- Use "Read REFERENCE.md for..." links instead of inlining everything
- If SKILL.md exceeds 300 lines, refactor: extract reference material to supporting files

**Context budget test:** Before finalizing, ask: "If I removed this section, would the skill still work for 80% of cases?" If yes, move it to a reference file.

### Step 2: Assess Complexity & Choose Structure

> [!CAUTION]
> **Default to Simple.** Only upgrade complexity if the skill genuinely needs it. Ask: "Would this skill work without this file?" If yes, don't add it.

**Complexity Assessment:**

| If the skill... | Then it's... |
|-----------------|--------------|
| Does ONE thing, linear flow, no scripts, <5 decision points | **Simple** |
| Multi-step workflow, needs reference tables, moderate domain knowledge | **Standard** |
| Many conditionals, requires scripts, extensive domain expertise, high failure modes | **Complex** |

**Structure by Complexity:**

| Complexity | Structure |
|------------|-----------|
| Simple | `SKILL.md` only |
| Standard | `SKILL.md` + `REFERENCE.md` or `EXAMPLES.md` |
| Complex | Above + `manifest.yaml` + `scripts/` + optional `TESTING.md` |

**manifest.yaml (required for Complex skills with scripts):**

A machine-readable declaration of what scripts the skill contains, what it needs, and how to run it. See Step 4.7 for details.

> [!TIP]
> **Signs you're over-engineering:**
> - Adding TESTING.md with obvious scenarios ("it should work")
> - Creating REFERENCE.md that repeats the workflow
> - Writing EXAMPLES.md when 2 inline examples suffice
>
> **Read TEMPLATES.md** for starter templates.

### Step 3: Determine Output Location

All user-created skills are saved to:
```
~/.swarm-ai/skills/<name>/SKILL.md
```

This is the standard location for user-created skills in the three-tier model (Built-in, User, Plugin).

### Step 4: Write the SKILL.md

Use templates from TEMPLATES.md. Ensure:
1. **Frontmatter** — valid YAML with `name` (must match folder name), `description`, and `tier`
2. **Description schema** — MUST follow this exact pattern (max 1024 chars total):
   ```yaml
   name: skill-name
   description: >
     One-line purpose sentence.
     TRIGGER: "phrase1", "phrase2", "phrase3".
     DO NOT USE: when condition (use alternative-skill instead).
     VERIFY_WITH: skill-name (optional — which skill independently validates this skill's output).
   tier: lazy
   ```
   - `name`: lowercase, hyphens, numbers only. Max 64 chars. Must match folder name.
   - `description`: max 1024 characters. First line = purpose. Then TRIGGER, DO NOT USE, VERIFY_WITH.
   - `tier`: `lazy` (default — stub + INSTRUCTIONS.md) or `always` (full SKILL.md in every session)
   - `TRIGGER:` — quoted phrases the user would say to invoke this skill
   - `DO NOT USE:` — when a similar skill should be used instead, with explicit boundary
   - `VERIFY_WITH:` — (optional) names a skill that can independently validate output quality
3. **"Why?" line** — one sentence after title explaining the problem this solves
4. **Workflow** — clear, numbered steps
5. **Progressive disclosure** — link to supporting files (only if needed)

**For lazy-tier skills (default):** Write the full workflow into `INSTRUCTIONS.md`. SKILL.md should be a minimal stub:
```markdown
---
name: my-skill
description: >
  One-line purpose.
  TRIGGER: "phrase1", "phrase2".
  DO NOT USE: when X (use Y instead).
tier: lazy
---
# My Skill

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "phrase1", "phrase2"
DO NOT USE: for X (use Y instead)
```

> [!TIP]
> The `TRIGGER:` and `DO NOT USE:` lines are critical for skill discovery and disambiguation.
> Without them, the agent guesses — and guesses wrong on similar skills.

### Step 4.5: Write Guardrails Section

> [!CRITICAL]
> **Every skill MUST have a Guardrails section.** This is the single most effective pattern for preventing agent execution failures. Without explicit prohibitions, LLMs take the shortest path and skip validation.

Add a `## Guardrails` section with 3-5 "DO NOT" rules specific to this skill's failure modes. These are **hard constraints**, not suggestions.

**Pattern:**
```markdown
## Guardrails

- DO NOT [skip specific validation step]. [Run/check] it even if output looks correct.
- DO NOT [assume common incorrect assumption]. Verify by [specific method].
- DO NOT [produce output without specific evidence/checkpoint].
- DO NOT [common shortcut that causes quality problems].
```

**How to identify guardrails:**
1. Ask: "What would a lazy execution of this skill look like?" → prohibit each shortcut
2. Ask: "What does this skill assume that might not be true?" → require verification
3. Ask: "When this skill fails, what's the root cause?" → prohibit the root cause behavior

**Examples by skill type:**

| Skill Type | Example Guardrails |
|------------|-------------------|
| Research | DO NOT synthesize conclusions from fewer than 3 sources. DO NOT cite a source without reading its full content via WebFetch. |
| Code generation | DO NOT skip the build/test step. DO NOT generate code without reading the existing codebase patterns first. |
| Document generation | DO NOT produce output without verifying all data points against source material. DO NOT use placeholder text in final output. |
| Data analysis | DO NOT report statistics without showing the underlying data. DO NOT skip outlier analysis. |

> [!TIP]
> **"DO NOT" is more reliable than "please verify"** for controlling LLM behavior. Positive instructions ("verify the output") are treated as suggestions. Negative constraints ("DO NOT skip verification") create hard boundaries. This is empirically validated across multi-agent systems.

### Step 4.7: Generate manifest.yaml (Complex skills only)

> [!IMPORTANT]
> **Required when the skill has scripts/ or multiple executable files.** manifest.yaml is a machine-readable package descriptor that tells the agent what scripts exist, what they do, and how to run them — without parsing SKILL.md.

**When to generate:** Skill has 2+ executable files (Python, JS, shell) or a `scripts/` directory.

**Schema:**

```yaml
# manifest.yaml — Skill Package Descriptor
name: skill-name                        # Must match folder name and SKILL.md name
version: "1.0.0"                        # Semantic version
tier: lazy                              # "always" or "lazy"

scripts:
  - path: scripts/generate.py           # Relative to skill directory
    description: "Main report generator"
    entry: true                         # Primary entry point (at most one)
    args: "--scope {scope} --output {output_dir}"
  - path: scripts/fetch_data.py
    description: "Fetch data from API"
  - path: scripts/helpers.py
    description: "Shared utility functions"

resources:
  - path: templates/                    # Directories or files
    description: "HTML report templates"
  - path: knowledge/domain.md
    description: "Domain reference data"

dependencies:
  python: ["cairosvg", "openpyxl"]      # pip packages
  system: ["cairo"]                     # Homebrew/system packages
  env:                                  # Required environment variables
    - DYLD_LIBRARY_PATH=/opt/homebrew/lib

timeout: 300                            # Max seconds for skill execution
```

**Rules:**
- `scripts[].path` must be relative to the skill directory
- At most one script should have `entry: true` (the primary entry point)
- `description` for each script should be a single sentence — the agent reads these to decide which script to run
- `dependencies` are declared, not auto-installed (safety). Agent warns if missing.
- `resources` lists static files the agent may need to read during execution

**On skill activation,** the manifest is loaded and a script index is injected:
```
Available scripts:
- scripts/generate.py [ENTRY]: Main report generator (args: --scope --output)
- scripts/fetch_data.py: Fetch data from API
- scripts/helpers.py: Shared utility functions
```

The agent runs scripts directly: `python .claude/skills/s_skill-name/scripts/generate.py --scope gcr`

### Step 5: Save and Notify User

After creating the skill file, inform the user:

**Skill saved to:** `~/.swarm-ai/skills/<name>/SKILL.md`

**⚠️ Important:** The new skill will be available in your **next chat session**. To use it now, start a new chat session.

**Why?** The Claude SDK client scans for skills once when a chat session starts, then reuses the same client throughout the session for performance. New skills created during the session are saved to disk and symlinked to `.claude/skills/`, but the current client won't detect them until it restarts. Starting a new chat session creates a new client that will discover the new skill.

---

## Mode 2: Evaluate an Existing Skill

### Step 6: Score the Skill

> [!CRITICAL]
> **Read SCORING.md completely** before scoring. It contains both rubrics and scoring worksheets.

**Process:**
1. Read all skill files (SKILL.md + supporting files)
2. Score **Structure** (0-100): 9 categories — documentation completeness
3. Score **Efficacy** (0-100): 6 categories — actual effectiveness
4. Use Combined Score Matrix in SCORING.md for verdict
5. Identify gaps in both dimensions

**Present results using the format in SCORING.md.**

If either score < 90, proceed to **Step 7**.

---

## Mode 3: Improve to 100/100

### Step 7: Plan Improvements

Based on evaluation, prioritize:

| Priority | Fixes | Target |
|----------|-------|--------|
| **P1 Critical** | Missing frontmatter, invalid YAML, empty description | Required to function |
| **P2 Important** | Missing triggers, no examples, no progressive disclosure | Required for 95+ |
| **P3 Polish** | Missing troubleshooting, no quick start, terminology issues | Required for 100 |

### Step 8: Execute Improvements

> [!CAUTION]
> **Get user approval before making changes.** Present the plan and wait for confirmation.

Work systematically:
1. Fix frontmatter first (skill won't load without valid YAML)
2. Standardize description: purpose + `TRIGGER:` + `DO NOT USE:` schema
3. Add progressive disclosure if SKILL.md > 200 lines
4. Create supporting files as needed
5. Add quality sections (Troubleshooting, Quick Start)

### Step 9: Verify Final Score

1. Re-read all skill files
2. Re-score against both rubrics
3. Confirm scores meet target
4. Present final structure and summary

---

## Validation Checklist (Quick)

Before declaring complete:
- [ ] `name` in frontmatter matches folder name (lowercase, hyphens, numbers, max 64 chars)
- [ ] `description` has: purpose sentence + `TRIGGER:` phrases + `DO NOT USE:` boundary (max 1024 chars)
- [ ] `tier` is set: `lazy` (default) or `always` (only for proven high-frequency skills)
- [ ] **"Why?" line** present after title
- [ ] **Guardrails section** present with 3-5 "DO NOT" rules
- [ ] `VERIFY_WITH:` considered (required for generator/code/document skills)
- [ ] For lazy-tier: SKILL.md is stub, full workflow in INSTRUCTIONS.md
- [ ] For always-tier: SKILL.md contains full workflow, under 500 lines
- [ ] For complex skills with scripts: `manifest.yaml` present with all scripts declared
- [ ] manifest.yaml scripts match actual files in directory (no stale entries)
- [ ] Structure matches complexity (not over-engineered)
- [ ] Examples show concrete input/output
- [ ] Consistent terminology throughout

Full checklist: **CHECKLIST.md**

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Skill not discovered | Check description has trigger keywords |
| Low Structure score | Add missing sections per SCORING.md rubric |
| Low Efficacy score | Simplify — skill may be doing too many things |
| Frontmatter errors | Validate YAML syntax, check for reserved words |
| User confused by skill | Add Quick Start, improve decision density |

---

## Reference

- **SCORING.md** — Structure + Efficacy rubrics with worksheets
- **TEMPLATES.md** — Starter templates and common patterns
- **EXAMPLES.md** — Before/after improvement examples
- **CHECKLIST.md** — 64-point validation checklist
