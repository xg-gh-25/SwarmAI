# Skill Templates

Starter templates for different skill types. Copy and customize for your needs.

---

## Template 1: Simple Skill

Use for single-capability skills without scripts.

**Structure:**
```
skill-name/
└── SKILL.md
```

**SKILL.md:**

```markdown
---
name: skill-folder-name
description: >
  [What it does in one sentence].
  TRIGGER: "[phrase 1]", "[phrase 2]", "[phrase 3]".
  DO NOT USE: [when a different skill or approach is better] (use [alternative] instead).
  VERIFY_WITH: [validator-skill-name] (optional — which skill validates output).
---

# [Skill Title]

[One-sentence summary of what this skill does.]

**Why?** [One sentence explaining the problem this solves — what pain point or gap motivated this skill's existence.]

## Quick Start

[2-3 line summary for returning users]

## How It Works

### Step 1: [First Action]
[Instructions for step 1]

### Step 2: [Second Action]
[Instructions for step 2]

### Step 3: [Final Action]
[Instructions for step 3]

## Guardrails

- DO NOT [skip specific validation]. [Run/verify] even if output looks correct.
- DO NOT [assume X]. Verify by [method].
- DO NOT [produce output] without [evidence/checkpoint].

## Examples

**Example 1: [Scenario Name]**
- Input: [what the user provides]
- Output: [what the skill produces]

**Example 2: [Scenario Name]**
- Input: [what the user provides]
- Output: [what the skill produces]

## Quality Guidelines

- [Guideline 1]
- [Guideline 2]
- [Guideline 3]
```

---

## Template 2: Workflow Skill

Use for multi-step processes with validation points.

**Structure:**
```
skill-name/
├── SKILL.md
├── REFERENCE.md
└── EXAMPLES.md
```

**SKILL.md:**

```markdown
---
name: workflow-skill-folder-name
description: >
  [What it does in one sentence].
  TRIGGER: "[phrase 1]", "[phrase 2]", "[phrase 3]".
  DO NOT USE: [when condition] (use [alternative] instead).
  VERIFY_WITH: [validator-skill-name] (optional — which skill validates output).
---

# [Workflow Name]

[One-sentence summary.]

**Why?** [One sentence explaining the problem this solves — what pain point or gap motivated this skill's existence.]

## Quick Start (for returning users)

1. [Step 1 summary] → 2. [Step 2 summary] → 3. [Step 3 summary] → Done

## Prerequisites

- [Prerequisite 1]
- [Prerequisite 2]
- [Prerequisite 3]

## Workflow Steps

### 1. [First Major Step]

[Detailed instructions]

```bash
# Command example
command --with-args
```

> [!TIP]
> [Helpful tip for this step]

### 2. [Second Major Step]

[Detailed instructions]

> [!CAUTION]
> [Important warning for this step]

### 3. [Third Major Step]

[Detailed instructions]

> [!IMPORTANT]
> [Critical information]

### 4. [Validation Step]

[How to verify success]

## Guardrails

- DO NOT [skip specific validation step]. [Run/verify] even if output looks correct.
- DO NOT [assume X]. Verify by [method].
- DO NOT [produce output] without [evidence/checkpoint].
- DO NOT [proceed past step N] without confirming [condition].

## Reference

For complete reference documentation, see **[REFERENCE.md](REFERENCE.md)**.

**Quick reference (most common):**

| Item | Format | Example |
|------|--------|---------|
| [Type 1] | [format] | [example] |
| [Type 2] | [format] | [example] |

## Quality Rules

> [!WARNING]
> [Most important rule highlighted]

- [Rule 1]
- [Rule 2]
- [Rule 3]

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| [Issue 1] | [Why it happens] | [How to fix] |
| [Issue 2] | [Why it happens] | [How to fix] |
| [Issue 3] | [Why it happens] | [How to fix] |

## Additional Resources

- **[REFERENCE.md](REFERENCE.md)** — Complete reference documentation
- **[EXAMPLES.md](EXAMPLES.md)** — Detailed examples and common mistakes
```

**REFERENCE.md:**

```markdown
# [Skill Name] Reference

Complete reference documentation for [skill name].

## [Category 1]

| Item | Description | Example |
|------|-------------|---------|
| [Item 1] | [Description] | [Example] |
| [Item 2] | [Description] | [Example] |

## [Category 2]

| Item | Description | Example |
|------|-------------|---------|
| [Item 1] | [Description] | [Example] |
| [Item 2] | [Description] | [Example] |

## Conventions

- [Convention 1]
- [Convention 2]

## Quality Checklist

Before completing, verify:

- [ ] [Check 1]
- [ ] [Check 2]
- [ ] [Check 3]
```

**EXAMPLES.md:**

```markdown
# [Skill Name] Examples

Real-world examples demonstrating proper usage.

## Example 1: [Scenario Name]

**Context:** [Brief description of the scenario]

**Input:**
```
[Input content]
```

**Output:**
```
[Expected output]
```

**Why this is correct:**
- [Reason 1]
- [Reason 2]

---

## Example 2: [Different Scenario]

**Context:** [Brief description]

**Input:**
```
[Input content]
```

**Output:**
```
[Expected output]
```

---

## Common Mistakes

### Mistake 1: [Description]

```
[Bad example]
```

**Problem:** [Why this is wrong]
**Fix:** [How to correct it]

### Mistake 2: [Description]

```
[Bad example]
```

**Problem:** [Why this is wrong]
**Fix:** [How to correct it]
```

---

## Template 3: Complex Skill with Scripts

Use for skills that include helper scripts.

**Structure:**
```
skill-name/
├── SKILL.md
├── REFERENCE.md
├── EXAMPLES.md
├── TESTING.md
└── scripts/
    ├── main_script.py
    └── helper.py
```

**scripts/main_script.py:**

```python
#!/usr/bin/env python3
"""
[Script Name] - [Brief description]

This script [what it does in detail].

Usage:
    python main_script.py --input file.txt
    python main_script.py --help

Requirements:
    - Python 3.8+
    - [Any required packages]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Configuration constants
# Explanation: [Why this value was chosen]
DEFAULT_LIMIT = 50

# Paths relative to script location
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent.parent.parent / "data"


def main_function(input_path: Path) -> dict:
    """
    [What this function does].

    Args:
        input_path: Path to input file

    Returns:
        Dict with results

    Raises:
        FileNotFoundError: If input file doesn't exist
        ValueError: If input format is invalid
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Implementation here
    return {"status": "success"}


def main():
    parser = argparse.ArgumentParser(
        description="[Script description]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main_script.py --input data.json
    python main_script.py --input data.json --verbose
        """
    )
    parser.add_argument("--input", "-i", required=True, help="Path to input file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    try:
        result = main_function(Path(args.input))
        print(json.dumps(result, indent=2))
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Invalid input: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**TESTING.md:**

```markdown
# Testing & Evaluation

Testing documentation for [skill name].

## Testing Summary

| Model | Tested | Result |
|-------|--------|--------|
| Claude Haiku | Yes/No | [Notes] |
| Claude Sonnet | Yes/No | [Notes] |
| Claude Opus | Yes/No | [Notes] |

## Evaluation Scenarios

### Scenario 1: [Basic Use Case]

**Query:** "[Example user query]"

**Expected behaviors:**
- [ ] [Behavior 1]
- [ ] [Behavior 2]
- [ ] [Behavior 3]

**Failure indicators:**
- [What would indicate failure]
- [What would indicate failure]

---

### Scenario 2: [Edge Case]

**Query:** "[Example user query]"

**Expected behaviors:**
- [ ] [Behavior 1]
- [ ] [Behavior 2]

**Failure indicators:**
- [What would indicate failure]

---

### Scenario 3: [Error Handling]

**Query:** "[Query that should trigger error handling]"

**Expected behaviors:**
- [ ] [How errors should be handled]
- [ ] [What feedback user should receive]

---

## Validation Commands

```bash
# [Description of what this validates]
python scripts/main_script.py --help

# [Description of what this validates]
python scripts/main_script.py --input test_data.json
```

## Known Edge Cases

| Case | Expected Behavior |
|------|-------------------|
| [Edge case 1] | [How to handle] |
| [Edge case 2] | [How to handle] |
```

---

## Template 4: Generator Skill

Use for skills that generate artifacts (commits, PRs, reports).

**SKILL.md:**

```markdown
---
name: artifact-generator-folder-name
description: >
  Generate [artifact type] by [method].
  TRIGGER: "[phrase 1]", "[phrase 2]", "[phrase 3]".
  DO NOT USE: [when condition] (use [alternative] instead).
  VERIFY_WITH: [validator-skill-name] (recommended for all generator skills).
---

# [Artifact Type] Generator

Generate [artifact type] following best practices and conventions.

**Why?** [One sentence explaining the problem this solves — what pain point or gap motivated this skill's existence.]

## Quick Start

1. [Gather info] → 2. [Generate draft] → 3. [Validate] → 4. [Output]

## Generation Process

### 1. Gather Information

[What information to collect]

### 2. Generate Draft

Use this template:

```
[Template structure]
```

### 3. Validate

Check against:
- [ ] [Validation rule 1]
- [ ] [Validation rule 2]
- [ ] [Validation rule 3]

### 4. Output

[How to present the final result]

## Guardrails

- DO NOT generate output without first reading [relevant source material/context].
- DO NOT skip the validation step (Step 3). Run every check even if the draft looks correct.
- DO NOT declare the artifact complete without [specific evidence of correctness].
- DO NOT [common shortcut that produces low-quality artifacts].

## Template

```
[Complete template with placeholders]
```

## Examples

### Example 1: [Type]

**Input:** [Description]

**Generated Output:**
```
[Example output]
```

### Example 2: [Type]

**Input:** [Description]

**Generated Output:**
```
[Example output]
```

## Quality Standards

- [Standard 1]
- [Standard 2]
- [Standard 3]

## Anti-Patterns

- [What NOT to do 1]
- [What NOT to do 2]
```

---

## Template Variants

> [!IMPORTANT]
> **One-size-fits-all templates produce generic output.** For skills with variable output, create template variants selected based on input characteristics.

### When to Use Variants

| Situation | Use Variants |
|-----------|--------------|
| Output structure is always the same | No — single template is fine |
| Output varies by input type/category | Yes — variant per category |
| Output varies by context (level, relationship, scope) | Yes — variant per context |
| Output intensity varies by evidence strength | Yes — variant per strength level |

### Variant Selection Pattern

```markdown
### Step N: Generate Output

**Select template based on [dimension]:**

| [Dimension Value] | Template Pattern |
|-------------------|------------------|
| **Type A** | Lead with [X]: "[structure for type A]" |
| **Type B** | Lead with [Y]: "[structure for type B]" |
| **Type C** | Lead with [Z]: "[structure for type C]" |

**[Second dimension] variants:**

| [Dimension Value] | Opening | Connector |
|-------------------|---------|-----------|
| **High** | "[strong opening]" | "consistently", "repeatedly" |
| **Medium** | "[moderate opening]" | "effectively", "notably" |
| **Low** | "[cautious opening]" | "recently", "beginning to" |
```

### Common Variant Dimensions

| Dimension | When to Use | Example Values |
|-----------|-------------|----------------|
| **Category/Type** | Output structure differs by input classification | Execution vs Strategy vs People |
| **Evidence Strength** | Language intensity should match confidence | Strong / Moderate / Emerging |
| **User Level** | Expectations differ by seniority | Junior / Mid / Senior |
| **Relationship** | Tone differs by audience | Peer / Manager / External |
| **Scope** | Scale affects framing | Local / Team / Org-wide |
| **Formality** | Register varies by context | Casual / Professional / Formal |

### Anti-Pattern: Hardcoded Templates

```markdown
# BAD - One template for all cases
**Template:**
[Name] demonstrates [skill] by [behavior], resulting in [outcome].

# GOOD - Variants based on context
**Select template based on category:**

| Category | Template |
|----------|----------|
| Execution | "[Name] delivered [outcome] by [behavior]." |
| Strategy | "[Name] shaped [direction] through [behavior]." |
| People | "[Name]'s [behavior] enabled [team impact]." |
```

---

## Common Skill Patterns

When building a skill, choose the pattern that best fits its purpose:

### Pattern: Workflow Skill
For multi-step processes with validation loops.
- Numbered steps (1, 2, 3...)
- Callouts for warnings/tips
- Checkpoints between major phases
- **Guardrails section** with "DO NOT skip" rules for each checkpoint
- Troubleshooting table at the end

### Pattern: Reference Skill
For API documentation or domain knowledge.
- Quick start at the top
- Detailed reference in REFERENCE.md
- Examples organized by use case
- Search-friendly headings
- **Guardrails section** with "DO NOT assume" rules for common misinterpretations

### Pattern: Generator Skill
For creating artifacts (commits, PRs, reports).
- Template structure in SKILL.md
- Input/output examples
- Validation rules
- Format specifications
- **Guardrails section** with "DO NOT generate without" evidence rules
- **VERIFY_WITH** in frontmatter pointing to an independent validator skill

---

## Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Folder name | hyphenated-name (source of truth) | `graphrag-knowledge-extraction` |
| `name` field | must match folder name | `graphrag-knowledge-extraction` |
| SKILL.md title | Title Case | `GraphRAG Knowledge Extraction Workflow` |
| Script files | snake_case.py | `find_unprocessed_files.py` |
| Supporting docs | UPPERCASE.md | `REFERENCE.md`, `EXAMPLES.md` |
