# Implementation Plan: {Skill Name}

## Overview

This plan creates an Agent skill for {brief description of what the skill does}. The skill follows the "{Simple | Standard | Complex}" complexity pattern: {list of files, e.g., "`SKILL.md` + `REFERENCE.md` + `EXAMPLES.md`"}.

**Target Scores:** Structure 100/100, Efficacy 100/100

## Tasks

- [ ] 1. Create skill directory and SKILL.md
  - [ ] 1.1 Create the skill folder structure at `{skill-name}/`
    - Create directory with proper naming (kebab-case, noun-form)
    - _Requirements: {X.Y}_
  - [ ] 1.2 Write SKILL.md frontmatter and header
    - Valid YAML frontmatter with `name: {skill-name}` (must match folder)
    - Description: "{What it does}. Use when {trigger 1}, {trigger 2}, or when the user mentions {keywords}."
    - Title: "{Skill Title}"
    - One-sentence summary
    - **Why?** line: "{One sentence explaining the problem this solves}"
    - _Requirements: All_
  - [ ] 1.3 Write Quick Start section
    - Format: 1. {Step 1} → 2. {Step 2} → 3. {Step 3} → Done
    - Immediately actionable for returning users
    - _Requirements: {X.Y}_
  - [ ] 1.4 Write Workflow Steps with explicit decision criteria
    - **Step 1: {Gather Input}**
      - {What to collect}
      - > [!IMPORTANT] {Key constraint or validation rule}
    - **Step 2: {Process/Select}**
      - {What happens in this step}
      - > [!TIP] {Helpful guidance}
    - **Step 3: {Generate/Transform}**
      - {What is produced}
      - > [!WARNING] {Important constraint}
    - **Step 4: {Validate Output}**
      - Run through quality checklist before presenting
    - _Requirements: {X.Y, X.Z}_
  - [ ] 1.5 Write Reference section (if applicable)
    - Link to REFERENCE.md for full documentation
    - Quick reference table: {Column 1} | {Column 2} | {Column 3}
    - _Requirements: {X.Y}_
  - [ ] 1.6 Write Quality Rules section with validation checklist
    - > [!WARNING] Most important: {Primary quality rule}
    - **Must Do:**
      - {Rule 1}
      - {Rule 2}
      - {Rule 3}
    - **Must NOT Do:**
      - {Anti-pattern 1}
      - {Anti-pattern 2}
    - **Validation Checklist:**
      - [ ] {Check 1}
      - [ ] {Check 2}
      - [ ] {Check 3}
    - _Requirements: {X.Y}_
  - [ ] 1.7 Write Troubleshooting table
    - Problem | Cause | Solution format
    - {Problem 1} | {Cause} | {Solution}
    - {Problem 2} | {Cause} | {Solution}
    - {Problem 3} | {Cause} | {Solution}
    - _Requirements: {X.Y}_
  - [ ] 1.8 Write Additional Resources section
    - Link to REFERENCE.md — {Description}
    - Link to EXAMPLES.md — {Description}
    - _Requirements: {X.Y}_

- [ ] 2. Create REFERENCE.md (if needed for Standard/Complex skills)
  - [ ] 2.1 Write reference header and introduction
    - Explain {how to use the reference}
    - How to use: {navigation guidance}
    - _Requirements: {X.Y}_
  - [ ] 2.2 Document {domain knowledge/reference content}
    - For each {item}:
      - {Field 1}
      - {Field 2}
      - {Field 3}
    - Format as searchable sections with consistent structure
    - _Requirements: {X.Y}_
  - [ ] 2.3 Add {Selection/Lookup Guide}
    - Table: "If you {observed/need this}..." → "Consider {this option}"
    - Help users {make appropriate choices}
    - _Requirements: {X.Y}_

- [ ] 3. Create EXAMPLES.md (if needed for Standard/Complex skills)
  - [ ] 3.1 Write examples header
    - Explain how to use examples as templates
    - _Requirements: {X.Y}_
  - [ ] 3.2 Write {N} diverse examples
    - Use realistic data (not generic "foo/bar")
    - Each example shows:
      - Context: {Input provided}
      - {Selections/choices made}
      - Generated output
      - Why this is correct (brief explanation)
    - Cover variety: {different scenarios/combinations}
    - _Requirements: {X.Y}_
  - [ ] 3.3 Write Common Mistakes section
    - **Mistake 1: {Description}**
      - Bad: "{Example of bad output}"
      - Problem: {Why it's wrong}
      - Fix: "{Example of correct output}"
    - **Mistake 2: {Description}**
      - Bad: "{Example}"
      - Problem: {Why}
      - Fix: "{Correction}"
    - {Continue for 4-6 common mistakes}
    - _Requirements: {X.Y}_

- [ ] 4. Checkpoint - Validate skill structure against scoring rubric
  - **Structure Score Checklist:**
    - [ ] Frontmatter: Valid YAML, name matches folder, description has triggers (15/15)
    - [ ] Description: What + When + Why line (15/15)
    - [ ] Structure: SKILL.md <500 lines, progressive disclosure used (10/10)
    - [ ] Workflow: Quick Start, numbered steps, callouts, validation, troubleshooting (15-20/15-20)
    - [ ] Examples: {N} diverse examples, input/output, common mistakes (10-15/10-15)
    - [ ] Quality Rules: Dedicated section, validation checklist, anti-patterns (10-15/10-15)
    - [ ] Consistency: Same terms throughout (5/5)
    - [ ] Testing: Scenarios in EXAMPLES.md serve as evaluation (5/5)
  - **Efficacy Score Checklist:**
    - [ ] Clarity: Purpose obvious in 10 seconds, ONE thing, domain expertise (20/20)
    - [ ] Decision Density: Explicit criteria (thresholds, constraints, rules) (25/25)
    - [ ] Minimalism: Every section earns its place (20/20)
    - [ ] Autonomy: Balanced - gather → generate → confirm (15/15)
    - [ ] Failure Recovery: Troubleshooting with recovery paths (10/10)
    - [ ] Battle-Tested: Real examples from actual usage (10/10)
    - [ ] Safety: No sensitive data, no code execution risks (10/10)
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Register and test the skill
  - [ ] 5.1 Run skill index updater
    - Execute: `SKILL_HOME=$([ -d "$HOME/.swarm-ai/skills" ] && echo "$HOME/.swarm-ai/skills" || echo "$HOME/.swarm-ai/skills") && python3 "$SKILL_HOME/skills-index-updater/scripts/update_skill_index.py"`
    - Verify skill appears in AGENTS.md
    - _Requirements: All_
  - [ ] 5.2 Test skill activation
    - Verify skill activates on triggers: {list trigger phrases}
    - _Requirements: All_
  - [ ] 5.3 Test complete workflow
    - Run through: {step 1} → {step 2} → {step 3}
    - Verify output meets quality checklist
    - _Requirements: All_

- [ ] 6. Final checkpoint - Confirm 100/100 scores
  - Re-score against SCORING.md worksheets
  - Verify Structure: 100/100
  - Verify Efficacy: 100/100
  - Ensure all tests pass, ask the user if questions arise.

## Notes

{Customize these notes based on skill complexity:}

**For skills WITHOUT scripts:**
- This skill has no scripts (pure workflow skill) - redistribute 15 script points: +5 Workflow, +5 Examples, +5 Quality Rules

**For skills WITH scripts:**
- Scripts are in `scripts/` subdirectory
- Each script has docstrings, error handling, and type hints
- Scripts use Unix-style paths and no hardcoded absolute paths

**General notes:**
- Target line counts: SKILL.md ~{150-300} lines, REFERENCE.md ~{300-600} lines, EXAMPLES.md ~{200-400} lines
- {Any skill-specific constraints or considerations}

---

## Template Usage Notes

> [!TIP]
> **Customize this template:**
> 1. Replace all `{placeholders}` with skill-specific content
> 2. Add/remove tasks based on skill complexity:
>    - Simple skills: Tasks 1, 4, 5, 6 only (no REFERENCE.md or EXAMPLES.md)
>    - Standard skills: All tasks
>    - Complex skills: All tasks + additional script tasks
> 3. Ensure each task references specific requirements
> 4. Mark optional sub-tasks with `*` suffix (e.g., `- [ ]* 2.2 Optional task`)

> [!IMPORTANT]
> **Task Guidelines:**
> - Each task should be completable by a coding agent
> - Tasks should build incrementally (no orphaned code)
> - Include checkpoints at reasonable breaks
> - Reference specific requirements for traceability
