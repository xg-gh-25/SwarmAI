# Skill Spec Template

Use this template to create specs for new Agent skills. It provides a structured approach to defining requirements, design, and implementation tasks that target 100/100 scores on both Structure and Efficacy.

## How to Use

### Option 1: Copy and Customize

1. Copy this entire folder to `.swarm-ai/specs/{your-skill-name}/`
2. Replace all `{placeholders}` in each file
3. Delete this README.md from your copy
4. Work through the spec workflow: requirements → design → tasks

### Option 2: Reference While Creating

1. Keep this template open as reference
2. Create your spec files from scratch using the patterns shown
3. Use the checklists to ensure completeness

## Template Files

| File | Purpose |
|------|---------|
| `requirements.md` | EARS-pattern requirements with acceptance criteria |
| `design.md` | Architecture, components, data models, correctness properties |
| `tasks.md` | Implementation checklist targeting 100/100 scores |
| `README.md` | This usage guide (delete from your copy) |

## Complexity Patterns

Choose the right structure based on skill complexity:

| Complexity | Skill Files | Spec Scope |
|------------|-------------|------------|
| **Simple** | `SKILL.md` only | 3-5 requirements, 3-5 properties, minimal tasks |
| **Standard** | `SKILL.md` + `REFERENCE.md` or `EXAMPLES.md` | 5-8 requirements, 5-10 properties, full tasks |
| **Complex** | Above + `TESTING.md` + `scripts/` | 8+ requirements, 10+ properties, script tasks |

## Key Sections to Customize

### In requirements.md:
- Glossary terms for your domain
- User stories with specific benefits
- Acceptance criteria using EARS patterns (WHEN/IF/THE/SHALL)

### In design.md:
- Architecture diagram for your workflow
- Components specific to your skill
- Data models for inputs/outputs
- Correctness properties that map to requirements

### In tasks.md:
- Skill folder name (kebab-case)
- Description with trigger keywords
- Workflow steps with decision criteria
- Reference content (if applicable)
- Example scenarios (if applicable)
- Quality rules specific to your domain

## Scoring Targets

The template is designed to achieve:

**Structure Score: 100/100**
- Frontmatter: 15/15
- Description: 15/15
- Structure: 10/10
- Workflow: 15-20/15-20 (redistributed if no scripts)
- Examples: 10-15/10-15 (redistributed if no scripts)
- Quality Rules: 10-15/10-15 (redistributed if no scripts)
- Consistency: 5/5
- Testing: 5/5

**Efficacy Score: 100/100**
- Clarity: 20/20
- Decision Density: 25/25
- Minimalism: 20/20
- Autonomy: 15/15
- Failure Recovery: 10/10
- Battle-Tested: 10/10
- Safety: 10/10

## Reference

- **[skill-builder/SCORING.md](../SCORING.md)** -- Full scoring rubrics
- **[skill-builder/TEMPLATES.md](../TEMPLATES.md)** -- SKILL.md templates
- **[skill-builder/CHECKLIST.md](../CHECKLIST.md)** -- 58-point validation checklist
- **[annual-review-feedback spec](../.swarm-ai/specs/annual-review-feedback/)** -- Real example achieving 100/100
