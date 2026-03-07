# Skill Scoring Rubric

Complete evaluation rubric for scoring Agent skills. **Two dimensions:**

1. **Structure Score** (0-100): Documentation completeness — does it have all the right pieces?
2. **Efficacy Score** (0-100): Actual effectiveness — does it get the job done brilliantly?

> [!IMPORTANT]
> A skill needs BOTH high Structure AND high Efficacy to be production-ready. See the Combined Score Matrix at the end.

---

# Part 1: Structure Score (0-100)

## Structure Interpretation

| Score | Rating | Meaning |
|-------|--------|---------|
| 95-100 | Excellent | All documentation in place, reference quality |
| 85-94 | Good | Functional with minor documentation gaps |
| 70-84 | Adequate | Works but missing key documentation |
| 50-69 | Needs Work | Significant documentation gaps |
| 0-49 | Poor | Major documentation problems |

---

## Category 1: Frontmatter (15 points)

The YAML frontmatter is required for the skill to load.

| Points | Criteria |
|--------|----------|
| 0 | Missing frontmatter or invalid YAML syntax |
| 5 | Has frontmatter but missing required fields |
| 10 | Has `name` and `description` but name doesn't match folder name |
| 15 | Valid YAML, has `name` (matches folder name) and `description`, no syntax errors |

**Validation rules:**
- `name`: max 64 chars, lowercase letters/numbers/hyphens only
- `description`: non-empty, max 1024 chars
- No XML tags or reserved words ("anthropic", "claude")

**Examples:**

```yaml
# 15 points - Perfect (folder: generating-commit-messages/)
---
name: generating-commit-messages
description: >
  Generate descriptive commit messages from git diffs.
  TRIGGER: "write commit", "commit message", "review staged changes".
  DO NOT USE: for code review feedback (use code-review skill instead).
---

# 10 points - Name doesn't match folder
---
name: commit-generator
description: Generate descriptive commit messages from git diffs.
---

# 5 points - Missing description
---
name: generating-commits
---

# 0 points - Invalid YAML
--
name: broken
description missing colon
---
```

---

## Category 2: Description Quality (15 points)

The description determines when the agent invokes the skill.

| Points | Criteria |
|--------|----------|
| 0 | Empty or missing description |
| 5 | Vague description (e.g., "helps with files") |
| 10 | Clear purpose sentence but missing `TRIGGER:` or `DO NOT USE:` lines |
| 13 | Purpose + `TRIGGER:` phrases present, but no `DO NOT USE:` boundary |
| 15 | Purpose + `TRIGGER:` + `DO NOT USE:` + **"Why?" line** after title |

**Required description schema:**
```yaml
description: >
  [One-line purpose sentence].
  TRIGGER: "[phrase 1]", "[phrase 2]", "[phrase 3]".
  DO NOT USE: [when condition] (use [alternative] instead).
```

**"Why?" line** (after title in SKILL.md body):
```markdown
**Why?** [One sentence explaining the problem this solves]
```

**Examples:**

```yaml
# 15 points - Excellent
description: >
  Extract entities and relationships from vault files into the GraphRAG knowledge graph.
  TRIGGER: "process meeting notes", "sync knowledge graph", "index files for GraphRAG".
  DO NOT USE: for simple file search (use workspace search directly).

# 10 points - Missing TRIGGER/DO NOT USE
description: Extract entities and relationships from vault files into the knowledge graph.

# 5 points - Vague
description: Helps with graph stuff and file processing.

# 0 points - Empty
description: ""
```

---

## Category 3: Structure (10 points)

File organization and progressive disclosure.

| Points | Criteria |
|--------|----------|
| 0 | No SKILL.md or broken file structure |
| 3 | SKILL.md exists but >500 lines with no progressive disclosure |
| 6 | SKILL.md reasonable length but poor organization |
| 8 | Good structure but missing one key supporting file OR over-engineered |
| 10 | Appropriate structure for complexity, progressive disclosure used correctly |

**Structure guidelines:**
- SKILL.md should be under 500 lines
- Reference material belongs in REFERENCE.md
- Examples belong in EXAMPLES.md
- Testing scenarios belong in TESTING.md
- File references should be one level deep from SKILL.md

**Right-sizing check:**
- Simple skills should NOT have TESTING.md, REFERENCE.md, or EXAMPLES.md
- Only add supporting files if they contain substantial unique content
- Ask: "Would this skill work without this file?" If yes, remove it

---

## Category 4: Workflow Clarity (15 points)

How clearly the skill guides the agent through the process.

| Points | Criteria |
|--------|----------|
| 0 | No workflow or completely unclear instructions |
| 5 | Steps exist but are vague or incomplete |
| 10 | Clear steps but missing validation points or callouts |
| 12 | Good workflow with callouts, missing quick start |
| 15 | Clear numbered steps, validation loops, callouts, quick start for returning users |

**Elements of a 15-point workflow:**
- Quick Start summary at the top
- Numbered steps (### 1. Step Name)
- Callouts for warnings/tips (> [!WARNING], > [!TIP], > [!CAUTION])
- Validation checkpoints between major phases
- Troubleshooting table
- Clear decision points for conditional flows

---

## Category 5: Scripts Quality (15 points)

Quality of any Python/Bash scripts included with the skill.

| Points | Criteria |
|--------|----------|
| N/A | No scripts (allocate these 15 points to other categories proportionally) |
| 0 | Scripts with critical bugs or security issues |
| 5 | Scripts work but no error handling |
| 8 | Basic error handling but missing docstrings/comments |
| 12 | Good error handling, documented, but uses deprecated APIs |
| 15 | Complete error handling, documented, modern APIs, all constants explained |

**Script quality checklist:**
- [ ] Shebang line present (`#!/usr/bin/env python3`)
- [ ] Docstring explaining purpose and usage
- [ ] Explicit error handling with helpful messages
- [ ] All constants documented with rationale
- [ ] No deprecated APIs (e.g., `datetime.utcnow()`)
- [ ] Unix-style paths (forward slashes)
- [ ] No hardcoded paths that won't work across environments

**If skill has no scripts:** Redistribute the 15 points:
- +5 to Workflow Clarity (max 20)
- +5 to Examples (max 15)
- +5 to Quality Rules (max 15)

---

## Category 6: Examples (10 points)

Concrete input/output examples that teach usage.

| Points | Criteria |
|--------|----------|
| 0 | No examples |
| 3 | One basic example |
| 6 | Multiple examples but all similar |
| 8 | Diverse examples covering main use cases |
| 10 | Comprehensive examples including edge cases and common mistakes |

**Example quality indicators:**
- Shows actual input and expected output
- Covers happy path and edge cases
- Includes "common mistakes to avoid" section
- Uses realistic data (not just "foo", "bar")

---

## Category 7: Quality Rules (10 points)

Explicit guidance on quality standards.

| Points | Criteria |
|--------|----------|
| 0 | No quality guidance |
| 3 | Basic rules mentioned in passing |
| 6 | Dedicated quality section but incomplete |
| 8 | Good quality rules but missing validation checklist |
| 10 | Comprehensive quality rules with validation checklist |

**Quality rules should cover:**
- ID/naming conventions
- Required vs optional fields
- Validation requirements
- Anti-patterns to avoid
- Edge case handling

---

## Category 8: Consistency (5 points)

Terminology and formatting consistency throughout.

| Points | Criteria |
|--------|----------|
| 0 | Major inconsistencies causing confusion |
| 2 | Several terminology switches or formatting issues |
| 4 | Minor inconsistencies (1-2 term variations) |
| 5 | Consistent terminology, formatting, and style throughout |

**Consistency checklist:**
- Same term used for same concept (don't switch between "companion" and "raw transcript")
- Consistent heading levels
- Consistent code block formatting
- Consistent callout style

---

## Category 9: Testing (5 points)

Evidence of testing and evaluation scenarios.

| Points | Criteria |
|--------|----------|
| 0 | No testing documentation |
| 2 | Basic testing mentioned but no scenarios |
| 4 | Evaluation scenarios but incomplete |
| 5 | Complete testing documentation with multiple scenarios, validation commands, and model coverage |

**Testing documentation should include:**
- Which models were tested (Haiku, Sonnet, Opus)
- 3+ evaluation scenarios with expected behaviors
- Failure indicators for each scenario
- Validation commands to verify behavior
- Known edge cases and expected handling

---

## Scoring Worksheet

Use this worksheet when evaluating a skill:

```markdown
## Skill: {name}

### Frontmatter (15 points)
- [ ] Valid YAML syntax
- [ ] `name` field present and matches folder name
- [ ] `description` field present and non-empty
- [ ] No reserved words or XML tags
**Score: __/15**

### Description Quality (15 points)
- [ ] First line states what the skill does (one sentence)
- [ ] Has `TRIGGER:` line with quoted user phrases
- [ ] Has `DO NOT USE:` line with boundary and alternative
- [ ] Specific, not vague
**Score: __/15**

### Structure (10 points)
- [ ] SKILL.md under 500 lines
- [ ] Progressive disclosure for large content
- [ ] Supporting files appropriately organized
- [ ] File references one level deep
**Score: __/10**

### Workflow Clarity (15 points)
- [ ] Quick start summary
- [ ] Numbered steps
- [ ] Callouts for warnings/tips
- [ ] Validation checkpoints
- [ ] Troubleshooting section
**Score: __/15**

### Scripts Quality (15 points)
- [ ] Error handling present
- [ ] Docstrings and comments
- [ ] No deprecated APIs
- [ ] Constants documented
- [ ] Unix-style paths
**Score: __/15** (or N/A if no scripts)

### Examples (10 points)
- [ ] Multiple examples
- [ ] Diverse use cases
- [ ] Shows input and output
- [ ] Common mistakes section
**Score: __/10**

### Quality Rules (10 points)
- [ ] Dedicated quality section
- [ ] Naming conventions
- [ ] Validation requirements
- [ ] Anti-patterns listed
**Score: __/10**

### Consistency (5 points)
- [ ] Consistent terminology
- [ ] Consistent formatting
- [ ] No confusing term switches
**Score: __/5**

### Testing (5 points)
- [ ] Testing documentation exists
- [ ] Multiple evaluation scenarios
- [ ] Model coverage noted
- [ ] Validation commands
**Score: __/5**

---

## TOTAL: __/100
```

---

## Score-to-Action Guide

| Score Range | Recommended Action |
|-------------|-------------------|
| 95-100 | Ready for production use |
| 85-94 | Fix Priority 3 items (polish) |
| 70-84 | Fix Priority 2 items (important improvements) |
| 50-69 | Fix Priority 1 items (critical fixes) |
| 0-49 | Consider rewriting from template |

---

# Part 2: Efficacy Score (0-100)

The Structure Score (above) measures **documentation completeness**. The Efficacy Score measures **actual effectiveness**—can the skill get the job done brilliantly?

> [!IMPORTANT]
> A skill needs BOTH scores to be production-ready. High structure + low efficacy = well-documented but clunky. Low structure + high efficacy = rough but brilliant (may not need polish).

## Efficacy Interpretation

| Score | Rating | Meaning |
|-------|--------|---------|
| 90-100 | Brilliant | Gets the job done elegantly with minimal friction |
| 75-89 | Effective | Works well, minor rough edges |
| 60-74 | Adequate | Accomplishes goal but with notable friction |
| 40-59 | Clunky | Works but feels over-engineered or confusing |
| 0-39 | Ineffective | Fails to accomplish core purpose reliably |

---

## Efficacy Category 1: Clarity of Intent (20 points)

Can you understand what this skill does in 10 seconds? Does it embed domain expertise?

| Points | Criteria |
|--------|----------|
| 0 | Purpose buried or unclear after reading |
| 5 | Purpose eventually clear after reading entire skill |
| 10 | Purpose clear from description but workflow muddies it |
| 15 | Purpose immediately obvious from Quick Start |
| 20 | Purpose crystal clear; skill does ONE thing brilliantly AND embeds domain-specific expertise |

**Signals of low clarity:**
- Description uses jargon without explanation
- Multiple unrelated capabilities crammed together
- User has to read 3+ sections to understand what it does

> [!IMPORTANT]
> **Anti-pattern: Skill delegation**
> A skill should contain all its workflow steps directly, not delegate to other skills. If you find your skill calling another skill mid-workflow, either merge them (if tightly coupled) or rethink the boundaries. One skill = one self-contained capability.

> [!TIP]
> **Domain Expertise: Reference vs Procedural**
> State-of-the-art skills embed TWO types of expert knowledge:
> 
> | Type | What it is | Example |
> |------|------------|---------|
> | **Reference** | Domain facts, definitions, lookup tables | "Here are all the categories and their definitions" |
> | **Procedural** | How to apply knowledge in ambiguous situations | "If X could be category A or B, ask this question to decide" |
> 
> **Reference alone is not enough.** A skill with comprehensive reference material but no procedural guidance leaves users to figure out the hard parts themselves. State-of-the-art means the skill knows HOW to apply its knowledge, not just WHAT the knowledge is.

---

## Efficacy Category 2: Decision Density (20 points)

At every decision point, is guidance unambiguous? Does it handle ambiguous cases?

| Points | Criteria |
|--------|----------|
| 0 | Constant "use judgment" with no criteria |
| 6 | Some decision points have criteria, others don't |
| 12 | Most decisions have if/then guidance, few gaps |
| 16 | All decisions have explicit criteria and thresholds |
| 20 | Decisions are so clear they could be automated, **including disambiguation for overlapping cases** |

**What to look for:**
- Explicit thresholds (e.g., "if overlap > 70%, DEEPEN")
- Decision tables with clear conditions
- No ambiguous phrases like "when appropriate" without criteria
- **Disambiguation logic for cases that match multiple options**

**Anti-patterns:**
- "Use your best judgment"
- "If it seems right, proceed"
- "Consider whether..." (without saying what to conclude)
- **Lookup table without disambiguation** (what if input matches multiple entries?)

> [!IMPORTANT]
> **Lookup tables ≠ Decision logic.** A reference table that maps inputs to outputs is passive. State-of-the-art skills include active disambiguation: "If X could be A or B, ask [question] to determine which."

---

## Efficacy Category 3: Minimalism (15 points)

Does every section earn its place? Could it be simpler?

| Points | Criteria |
|--------|----------|
| 0 | Massive over-documentation, 3x longer than needed |
| 4 | Significant filler, many sections could be cut |
| 8 | Some redundancy, 20-30% could be removed |
| 12 | Lean but complete, minor trimming possible |
| 15 | Every word earns its place; couldn't be shorter without loss |

**The Simplicity Test:**
1. Could this skill be 50% shorter and still work? → Major minimalism problem
2. Could 20% be removed? → Minor trimming needed
3. Removing anything would hurt? → Excellent minimalism

**Signs of bloat:**
- Same concept explained 3+ times in different sections
- Examples that repeat the workflow instead of adding value
- Troubleshooting entries for things that never happen
- Supporting files that could be inline sections

---

## Efficacy Category 4: Calibration (10 points)

Does output vary appropriately based on input context?

| Points | Criteria |
|--------|----------|
| 0 | One-size-fits-all output regardless of context |
| 3 | Acknowledges context matters but no guidance |
| 6 | Some calibration guidance but incomplete |
| 8 | Clear calibration for main context dimensions |
| 10 | Comprehensive calibration with matrices/tables for all relevant dimensions |

**Context dimensions to consider:**
- **Evidence strength**: How confident should claims be based on input quality?
- **User level/role**: Should output differ for junior vs senior, peer vs manager?
- **Scope/scale**: Does output adjust for small vs large, local vs global?
- **Formality**: Should tone vary by audience or purpose?

**Calibration pattern:**
```markdown
| Context | Output Adjustment |
|---------|-------------------|
| Strong evidence | "excels at", "consistently demonstrates" |
| Moderate evidence | "demonstrates", "shows" |
| Limited evidence | "has shown", "is developing" |
```

> [!TIP]
> **Don't overclaim.** A skill that always uses superlatives regardless of input quality undermines credibility. Match output intensity to input strength.

---

## Efficacy Category 5: Autonomy (10 points)

Can it run with minimal user intervention?

| Points | Criteria |
|--------|----------|
| 0 | Requires user input every 2-3 steps |
| 3 | Frequent checkpoints, but some autonomous stretches |
| 6 | Key checkpoints only, good autonomous flow |
| 10 | Runs almost entirely autonomously; user only confirms final output |

**Autonomy spectrum:**
- **High autonomy**: "Run this, get output"
- **Balanced**: "Gather input → Process autonomously → Confirm output"
- **Low autonomy**: "Ask user → Do one thing → Ask user again" (may be intentional)

> [!NOTE]
> Some skills *should* have low autonomy (e.g., skills requiring clarification). Score based on whether the autonomy level is appropriate for the task.

---

## Efficacy Category 6: Failure Recovery (10 points)

When things go wrong, does it help recover?

| Points | Criteria |
|--------|----------|
| 0 | Only covers happy path; failures = dead end |
| 3 | Mentions errors exist but no recovery guidance |
| 6 | Some failure cases covered with recovery steps |
| 8 | Most failures have explicit recovery paths |
| 10 | Comprehensive failure handling; skill is resilient |

**What good failure recovery includes:**
- "If X fails, try Y"
- Troubleshooting table with actual observed problems
- Validation steps that catch errors before they cascade
- Graceful degradation (partial success still valuable)

---

## Efficacy Category 7: Battle-Tested (5 points)

Evidence of real usage and iteration.

| Points | Criteria |
|--------|----------|
| 0 | Clearly theoretical, never used |
| 2 | Appears used but no evidence of iteration |
| 3 | Some feedback incorporated, shows evolution |
| 4 | Clear iteration history, multiple improvements |
| 5 | Extensively used with feedback loop; refinements visible |

**Evidence of battle-testing:**
- Troubleshooting entries from real problems encountered
- Examples using real data (not generic "foo/bar")
- Version history showing improvements
- "Common Mistakes" that came from actual mistakes

---

## Efficacy Category 8: Safety & Security (10 points)

Does the skill avoid unsafe actions and respect least privilege?

| Points | Criteria |
|--------|----------|
| 0 | Skill permits arbitrary code execution or exposes sensitive data |
| 3 | Some risky actions without safeguards |
| 6 | Generally safe but accesses more data/functions than needed |
| 8 | Follows least privilege; minor security gaps |
| 10 | Minimal data access, no unsafe actions, audited for misuse risks |

**Security checklist:**
- Only accesses data the skill actually needs
- No arbitrary shell/code execution without validation
- Sensitive data (credentials, PII) handled appropriately
- Has been reviewed for potential misuse vectors
- Respects user trust boundaries

> [!CAUTION]
> A skill extends the agent's capabilities. A poorly designed skill could inadvertently introduce vulnerabilities—leaking sensitive info, permitting unintended actions, or being exploited by malicious inputs. Security is not optional for production skills.

---

## Efficacy Scoring Worksheet

```markdown
## Skill: {name}

### Clarity of Intent (20 points)
- [ ] Purpose obvious in 10 seconds
- [ ] Does ONE thing well (not 5 things poorly)
- [ ] Quick Start immediately actionable
**Score: __/20**

### Decision Density (20 points)
- [ ] All decision points have explicit criteria
- [ ] Thresholds are specific (numbers, conditions)
- [ ] No "use judgment" without guidance
- [ ] Disambiguation logic for overlapping/ambiguous cases
**Score: __/20**

### Minimalism (15 points)
- [ ] Every section earns its place
- [ ] No redundant explanations
- [ ] Couldn't be shorter without loss
**Score: __/15**

### Calibration (10 points)
- [ ] Output varies based on input context
- [ ] Evidence strength affects language intensity
- [ ] User level/role considered where relevant
**Score: __/10**

### Autonomy (10 points)
- [ ] Autonomy level appropriate for task
- [ ] Checkpoints are meaningful, not excessive
- [ ] Can run without constant hand-holding
**Score: __/10**

### Failure Recovery (10 points)
- [ ] Common failures have recovery paths
- [ ] Validation catches errors early
- [ ] Troubleshooting reflects real problems
**Score: __/10**

### Battle-Tested (5 points)
- [ ] Evidence of real usage
- [ ] Examples use realistic data
- [ ] Shows iteration and improvement
**Score: __/5**

### Safety & Security (10 points)
- [ ] Only accesses data it needs
- [ ] No unsafe actions without validation
- [ ] Reviewed for misuse vectors
**Score: __/10**

---

## EFFICACY TOTAL: __/100
```

---

## Combined Score Matrix

Use both scores to determine true production-readiness:

| Structure | Efficacy | Verdict |
|-----------|----------|---------|
| 95+ | 90+ | **Production Ready** - Ship it |
| 95+ | 75-89 | **Well-documented but needs UX polish** - Simplify |
| 95+ | <75 | **Over-engineered** - Rethink approach |
| 75-94 | 90+ | **Rough but brilliant** - Light documentation pass |
| 75-94 | 75-89 | **Solid** - Standard improvements |
| <75 | 90+ | **Hidden gem** - Document the magic |
| <75 | <75 | **Needs work** - Major revision |

> [!TIP]
> **Quick gut-check**: After reading a skill, ask: "Would I enjoy using this, or dread it?" That feeling often captures efficacy better than any rubric.
