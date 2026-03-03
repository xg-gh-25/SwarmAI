---
name: Skill Builder
description: Create, evaluate, and improve Agent skills to production quality (100/100). Use when the user wants to create a new skill from scratch, review an existing skill, score a skill against best practices, or improve a skill's quality. Also use when the user mentions skill development, skill templates, or skill optimization. Do NOT use when the user wants to convert the current session into a skill (use skillify-session instead).
---

# Skill Builder Workflow

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
| Complex | Above + `TESTING.md` + `scripts/` |

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
1. **Frontmatter** — valid YAML with `name` (must match folder name) and `description`
2. **Description** — includes BOTH what it does AND when to use it
3. **"Why?" line** — one sentence after title explaining the problem this solves
4. **Workflow** — clear, numbered steps
5. **Progressive disclosure** — link to supporting files (only if needed)

> [!TIP]
> Description is critical for discovery. Include multiple trigger keywords.

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
2. Enhance description with trigger keywords
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
- [ ] `name` in frontmatter matches folder name
- [ ] `description` includes what AND when
- [ ] **"Why?" line** present after title
- [ ] SKILL.md under 500 lines
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
- **CHECKLIST.md** — 50-point validation checklist
