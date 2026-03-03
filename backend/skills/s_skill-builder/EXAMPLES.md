# Skill Improvement Examples

Real before/after examples showing how skills are improved to 100/100.

---

## Example 1: Missing Frontmatter Fields

### Before (Score: 52/100)

```markdown
---
description: Extract entities from files
---

# Entity Extractor

This skill extracts entities from files.

## Steps
1. Read the file
2. Find entities
3. Save them
```

**Issues identified:**
- Missing `name` field (critical)
- Description too vague, no triggers
- No examples
- No quality rules

### After (Score: 100/100)

```markdown
---
name: document-entity-extraction
description: Extract named entities (people, organizations, locations) from document files. Use when processing documents for NER, building entity databases, or when the user mentions entity extraction, named entity recognition, or document parsing.
---

# Document Entity Extraction

Extract named entities from document files into structured format.

## Quick Start

1. Read document → 2. Extract entities → 3. Validate types → 4. Output JSON

## Steps

### 1. Read the Document
Read the full document content...

[etc.]
```

**Changes made:**
1. Added `name` field matching folder name
2. Enhanced description with triggers and keywords
3. Added Quick Start
4. Expanded workflow with details

---

## Example 2: Bloated SKILL.md Without Progressive Disclosure

### Before (Score: 68/100)

```markdown
---
name: processing-api-requests
description: Process API requests with validation and error handling.
---

# API Request Processor

[600+ lines of content including:]
- Full API reference tables
- 20+ endpoint examples
- Error code listings
- Authentication details
- Rate limiting rules
- All in one file
```

**Issues identified:**
- SKILL.md over 500 lines
- No progressive disclosure
- Reference material mixed with workflow
- Hard to navigate

### After (Score: 100/100)

**SKILL.md (180 lines):**
```markdown
---
name: processing-api-requests
description: Process API requests with validation and error handling. Use when making API calls, handling responses, or debugging API issues.
---

# API Request Processor

## Quick Start
1. Validate request → 2. Send request → 3. Handle response → 4. Process errors

## Workflow

### 1. Validate Request
[Core workflow only]

## Quick Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| /users | GET | List users |
| /users | POST | Create user |

For complete API reference, see **[REFERENCE.md](REFERENCE.md)**.
For examples, see **[EXAMPLES.md](EXAMPLES.md)**.
```

**REFERENCE.md (300 lines):**
```markdown
# API Reference

Complete endpoint documentation...
[All reference tables moved here]
```

**EXAMPLES.md (150 lines):**
```markdown
# API Examples

Request/response examples...
[All examples moved here]
```

**Changes made:**
1. Split into 3 files using progressive disclosure
2. SKILL.md now focused on workflow only
3. Quick reference table with link to full reference
4. Easy navigation between files

---

## Example 3: Scripts Without Error Handling

### Before (Score: 71/100)

```python
#!/usr/bin/env python3
import json

def process(file):
    data = json.load(open(file))
    return data["entities"]

if __name__ == "__main__":
    import sys
    print(process(sys.argv[1]))
```

**Issues identified:**
- No docstring
- No error handling
- File handle not closed properly
- No argument validation
- Crashes on missing file or bad JSON

### After (Score: 100/100)

```python
#!/usr/bin/env python3
"""
Entity Processor - Extract entities from JSON files.

Usage:
    python process_entities.py input.json
    python process_entities.py --help

Returns entities array from input JSON file.
"""

import argparse
import json
import sys
from pathlib import Path


def process_file(file_path: Path) -> list:
    """
    Extract entities from a JSON file.

    Args:
        file_path: Path to JSON file containing entities

    Returns:
        List of entity dictionaries

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file is not valid JSON
        KeyError: If 'entities' key is missing
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"Invalid JSON in {file_path}: {e.msg}",
                e.doc, e.pos
            )

    if "entities" not in data:
        raise KeyError(f"Missing 'entities' key in {file_path}")

    return data["entities"]


def main():
    parser = argparse.ArgumentParser(
        description="Extract entities from JSON files"
    )
    parser.add_argument("input", help="Path to input JSON file")
    args = parser.parse_args()

    try:
        entities = process_file(Path(args.input))
        print(json.dumps(entities, indent=2))
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"JSON Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyError as e:
        print(f"Data Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Changes made:**
1. Added comprehensive docstring with usage
2. Type hints for function parameters
3. Explicit error handling for each failure mode
4. Proper file handle with context manager
5. Argument parsing with help text
6. Helpful error messages to stderr
7. Appropriate exit codes

---

## Example 4: Vague Description to Specific

### Before

```yaml
description: Helps with code review
```

**Score impact:** 5/15 points (vague, no triggers)

### After

```yaml
description: Review code changes for best practices, security issues, and performance concerns. Use when reviewing pull requests, checking code quality, analyzing diffs, or when the user mentions code review, PR review, or code analysis.
```

**Score impact:** 15/15 points

**Improvement pattern:**
```
[Vague verb] → [Specific actions] + [What aspects] + [When triggers]
```

---

## Example 5: Missing Testing Documentation

### Before (Score: 85/100)

No TESTING.md file. Skill works but no evidence of validation.

### After (Score: 100/100)

**TESTING.md:**
```markdown
# Testing & Evaluation

## Testing Summary

| Model | Tested | Result |
|-------|--------|--------|
| Claude Haiku | Yes | Works for simple cases, needs prompting for complex |
| Claude Sonnet | Yes | Reliable, good balance |
| Claude Opus | Yes | Comprehensive, handles edge cases well |

## Evaluation Scenarios

### Scenario 1: Basic Usage

**Query:** "Review my latest commit"

**Expected behaviors:**
- [ ] Runs git diff to see changes
- [ ] Identifies code patterns
- [ ] Provides actionable feedback
- [ ] Organizes by severity

**Failure indicators:**
- Generic feedback not specific to code
- Missing security considerations
- No actionable suggestions

### Scenario 2: Large Diff

**Query:** "Review these 500 lines of changes"

**Expected behaviors:**
- [ ] Groups changes by file/component
- [ ] Prioritizes critical issues
- [ ] Doesn't get overwhelmed

### Scenario 3: No Changes

**Query:** "Review my code" (but nothing staged)

**Expected behaviors:**
- [ ] Detects no changes to review
- [ ] Asks user to stage changes or specify files

## Validation Commands

```bash
# Verify skill activates on trigger
echo "review my PR" | claude

# Test with actual diff
git diff HEAD~1 | claude "review this"
```
```

**Changes made:**
1. Created TESTING.md file
2. Documented model coverage
3. Added 3+ evaluation scenarios
4. Included failure indicators
5. Added validation commands

---

## Common Improvement Patterns

| Issue | Pattern | Example |
|-------|---------|---------|
| Vague description | Add "Use when..." clause | "Process files. Use when..." |
| Missing name | Match folder name | `name: processing-files` (for `processing-files/` folder) |
| No examples | Add 2-3 diverse examples | Happy path + edge case + error |
| Long SKILL.md | Split into REFERENCE.md + EXAMPLES.md | Keep workflow, move tables |
| Script errors | Add try/except with helpful messages | "File not found: {path}" |
| No quick start | Add 1-line summary at top | "1. Read → 2. Process → 3. Save" |
| No troubleshooting | Add problem/cause/solution table | Common issues with fixes |
| Inconsistent terms | Pick one term, use throughout | "raw transcript" not "companion" |

---

## Score Improvement Summary

| Starting Score | Key Fixes | Resulting Score |
|----------------|-----------|-----------------|
| 0-49 | Add frontmatter, basic workflow | 50-69 |
| 50-69 | Fix description, add examples | 70-84 |
| 70-84 | Add progressive disclosure, testing | 85-94 |
| 85-94 | Polish consistency, troubleshooting | 95-100 |
