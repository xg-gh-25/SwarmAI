# Skill Validation Checklist

Complete 64-point checklist for validating skills before deployment.

---

## How to Use

1. Go through each section
2. Check off items that pass
3. Fix any unchecked items
4. Re-validate until all items pass

**Passing threshold:** All 64 items must be checked for 100/100 score.

---

## Section 1: Frontmatter (5 items)

### Required Fields

- [ ] **1.1** SKILL.md starts with `---` on line 1
- [ ] **1.2** SKILL.md has closing `---` after frontmatter
- [ ] **1.3** `name` field is present
- [ ] **1.4** `description` field is present and non-empty
- [ ] **1.5** YAML syntax is valid (no tabs, proper indentation)

### Validation Commands

```bash
# Check YAML validity
head -20 SKILL.md | grep -E "^(---|name:|description:)"
```

---

## Section 2: Name Field (5 items)

### Format Requirements

- [ ] **2.1** Name matches folder name (source of truth)
- [ ] **2.2** Name is lowercase
- [ ] **2.3** Name uses hyphens between words (not underscores)
- [ ] **2.4** Name is under 64 characters
- [ ] **2.5** Name doesn't contain reserved words ("anthropic", "claude")

### Examples

| Valid | Invalid |
|-------|---------|
| `processing-files` (matches folder) | `process-files` (doesn't match folder) |
| `entity-extraction` | `Entity-Extraction` (uppercase) |
| `graphrag-knowledge` | `graphrag_knowledge` (underscores) |

---

## Section 3: Description Field (6 items)

### Content Requirements

- [ ] **3.1** Description first line states WHAT the skill does (one sentence)
- [ ] **3.2** Description has `TRIGGER:` line with quoted user phrases
- [ ] **3.3** Description has `DO NOT USE:` line with boundary and alternative skill
- [ ] **3.4** Description is specific (not vague like "helps with stuff")
- [ ] **3.5** Description is under 1024 characters
- [ ] **3.6** Description doesn't contain XML tags
- [ ] **3.7** `VERIFY_WITH:` considered (required for generator/code/document skills, optional for others)

### Pattern

```
[What it does]. Use when [trigger 1], [trigger 2], or when the user mentions [keywords].
```

---

## Section 4: Structure (6 items)

### File Organization

- [ ] **4.1** SKILL.md exists in skill folder
- [ ] **4.2** SKILL.md is under 500 lines
- [ ] **4.3** If >200 lines, progressive disclosure is used (REFERENCE.md, EXAMPLES.md)
- [ ] **4.4** All file references are one level deep from SKILL.md
- [ ] **4.5** Folder uses noun-form naming (e.g., `skill-name/` not `doing-skill/`)
- [ ] **4.6** Scripts are in `scripts/` subdirectory (if applicable)

### Structure Check

```bash
# Count lines in SKILL.md
wc -l SKILL.md

# List all files
ls -la
```

---

## Section 5: Workflow (8 items)

### Essential Elements

- [ ] **5.1** Quick Start summary at the top (for returning users)
- [ ] **5.2** Numbered steps (### 1. Step Name)
- [ ] **5.3** Clear prerequisites section
- [ ] **5.4** Callouts for warnings (> [!WARNING])
- [ ] **5.5** Callouts for tips (> [!TIP])
- [ ] **5.6** Callouts for important info (> [!IMPORTANT])
- [ ] **5.7** Validation/checkpoint steps included
- [ ] **5.8** Troubleshooting table at the end

### Callout Syntax

```markdown
> [!TIP]
> Helpful tip here

> [!WARNING]
> Warning message here

> [!CAUTION]
> Critical warning here

> [!IMPORTANT]
> Important information here

> [!NOTE]
> Additional note here
```

---

## Section 6: Examples (5 items)

### Example Quality

- [ ] **6.1** At least 2 examples provided
- [ ] **6.2** Examples show concrete input
- [ ] **6.3** Examples show expected output
- [ ] **6.4** Examples cover different use cases
- [ ] **6.5** "Common mistakes" section included

---

## Section 7: Scripts (8 items)

*Skip this section if skill has no scripts. Redistribute points to other sections.*

### Code Quality

- [ ] **7.1** Shebang line present (`#!/usr/bin/env python3`)
- [ ] **7.2** Module docstring with usage instructions
- [ ] **7.3** Function docstrings for all public functions
- [ ] **7.4** Type hints on function parameters
- [ ] **7.5** Explicit error handling (try/except)
- [ ] **7.6** Helpful error messages (not just stack traces)
- [ ] **7.7** No deprecated APIs (e.g., `datetime.utcnow()`)
- [ ] **7.8** All constants documented with rationale

### Code Check

```python
# Good constant documentation
# 60% threshold: balances catching typos while avoiding false positives.
# Tuned empirically on meeting notes dataset.
SIMILARITY_THRESHOLD = 0.6

# Bad - no explanation
THRESHOLD = 0.6
```

---

## Section 8: Paths & Compatibility (4 items)

### Cross-Platform

- [ ] **8.1** All paths use forward slashes (Unix-style)
- [ ] **8.2** No hardcoded absolute paths
- [ ] **8.3** Paths relative to script location use `Path(__file__).resolve().parent`
- [ ] **8.4** No Windows-specific path separators (`\`)

### Example

```python
# Good
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"

# Bad
DATA_DIR = "C:\\Users\\name\\data"
DATA_DIR = "/Users/specific/path/data"
```

---

## Section 9: Consistency (5 items)

### Terminology & Style

- [ ] **9.1** Same term used for same concept throughout
- [ ] **9.2** Consistent heading levels (## for sections, ### for steps)
- [ ] **9.3** Consistent code block language tags
- [ ] **9.4** Consistent callout formatting
- [ ] **9.5** No conflicting instructions

### Common Inconsistencies to Check

| Check For | Should Be |
|-----------|-----------|
| "companion" vs "raw transcript" | Pick one |
| "file" vs "document" | Pick one |
| "entity" vs "node" | Pick one |
| Mixed heading levels | Standardize |

---

## Section 10: Testing (5 items)

### Documentation

- [ ] **10.1** Testing documentation exists (TESTING.md or section in SKILL.md)
- [ ] **10.2** Model coverage noted (Haiku, Sonnet, Opus)
- [ ] **10.3** At least 3 evaluation scenarios
- [ ] **10.4** Expected behaviors listed for each scenario
- [ ] **10.5** Failure indicators listed for each scenario

---

## Section 11: Quality Rules (3 items)

### Guidance

- [ ] **11.1** Dedicated quality rules section exists
- [ ] **11.2** Anti-patterns or "what not to do" documented
- [ ] **11.3** Validation checklist for output quality

---

## Section 12: Guardrails (5 items)

### Anti-Skip Rules

- [ ] **12.1** Dedicated `## Guardrails` section exists
- [ ] **12.2** Contains 3-5 "DO NOT" rules (not "please verify" suggestions)
- [ ] **12.3** Each guardrail targets a specific failure mode (not generic)
- [ ] **12.4** Guardrails cover: skipping validation, false assumptions, missing evidence
- [ ] **12.5** Guardrails are testable (you can tell if one was violated)

### What Good Guardrails Look Like

```markdown
# GOOD — Specific, testable, targets real failure mode
- DO NOT synthesize conclusions from fewer than 3 sources.
- DO NOT skip the build step. Check exit code even if output looks correct.
- DO NOT generate IAM policies without reading the existing role first.

# BAD — Vague, untestable, generic
- Be careful with the output.
- Please verify your work.
- Try to be accurate.
```

---

## Section 13: Maintainability & Standards (4 items)

### Future-Proofing

- [ ] **13.1** Skill follows Agent Skills spec (frontmatter, folder structure)
- [ ] **13.2** Modular design—sections can be updated independently
- [ ] **13.3** No hardcoded assumptions that will break with workflow changes
- [ ] **13.4** Documentation is clear enough for someone else to maintain

---

## Section 14: Safety & Security (4 items)

### Security Review

- [ ] **14.1** Skill only accesses data it actually needs (least privilege)
- [ ] **14.2** No arbitrary shell/code execution without input validation
- [ ] **14.3** Sensitive data (credentials, PII) handled appropriately or avoided
- [ ] **14.4** Reviewed for potential misuse vectors (e.g., prompt injection via skill)

---

## Final Verification

After completing all sections:

- [ ] **FINAL** All 64 items are checked

---

## Quick Validation Script

Run this to check basic requirements:

```bash
#!/bin/bash
SKILL_DIR="$1"

echo "Validating skill: $SKILL_DIR"
echo "================================"

# Check SKILL.md exists
if [ -f "$SKILL_DIR/SKILL.md" ]; then
    echo "✓ SKILL.md exists"
else
    echo "✗ SKILL.md missing"
    exit 1
fi

# Check frontmatter
if head -1 "$SKILL_DIR/SKILL.md" | grep -q "^---$"; then
    echo "✓ Frontmatter opens correctly"
else
    echo "✗ Missing opening ---"
fi

# Check name field
if grep -q "^name:" "$SKILL_DIR/SKILL.md"; then
    echo "✓ name field present"
else
    echo "✗ name field missing"
fi

# Check description field
if grep -q "^description:" "$SKILL_DIR/SKILL.md"; then
    echo "✓ description field present"
else
    echo "✗ description field missing"
fi

# Check line count
LINES=$(wc -l < "$SKILL_DIR/SKILL.md")
if [ "$LINES" -lt 500 ]; then
    echo "✓ SKILL.md is $LINES lines (under 500)"
else
    echo "⚠ SKILL.md is $LINES lines (over 500, consider progressive disclosure)"
fi

echo "================================"
echo "Basic validation complete"
```

---

## Checklist Summary

| Section | Items | Focus |
|---------|-------|-------|
| Frontmatter | 5 | YAML validity |
| Name Field | 5 | Format compliance |
| Description | 7 | Discoverability + VERIFY_WITH |
| Structure | 6 | Organization |
| Workflow | 8 | Clarity |
| Examples | 5 | Teaching |
| Scripts | 8 | Code quality |
| Paths | 4 | Compatibility |
| Consistency | 5 | Polish |
| Testing | 5 | Validation |
| Quality Rules | 3 | Guidance |
| **Guardrails** | **5** | **Anti-skip enforcement** |
| Maintainability | 4 | Future-proofing |
| Safety & Security | 4 | Risk mitigation |
| **Total** | **64** | |
