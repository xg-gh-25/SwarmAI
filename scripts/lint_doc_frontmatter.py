#!/usr/bin/env python3
"""Lint design doc frontmatter — ensures docs/*.md have required metadata fields.

Pre-commit hook companion: validates that staged docs have `created:` and `updated:`
in their YAML frontmatter. Optionally checks for a Change Log section.

Usage:
    python scripts/lint_doc_frontmatter.py                    # check all docs/*.md
    python scripts/lint_doc_frontmatter.py file1.md file2.md  # check specific files
    python scripts/lint_doc_frontmatter.py --staged           # check git-staged docs only

Exit codes:
    0 = all pass
    1 = violations found (prints details to stderr)
"""

import re
import subprocess
import sys
from pathlib import Path

DOCS_DIR = Path("docs")
REQUIRED_FIELDS = ("created", "updated")
RECOMMENDED_FIELDS = ("title", "status")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Files exempt from frontmatter checks (non-design docs)
EXEMPT_FILES = {"README.md", "CONVERGENCE.md", "USER_GUIDE.md"}


def parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML frontmatter fields from markdown content."""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    fm_block = content[3:end].strip()
    fields = {}
    for line in fm_block.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def check_file(filepath: Path) -> list[str]:
    """Check a single file for frontmatter compliance. Returns list of violations."""
    violations = []

    if not filepath.exists():
        return []

    if filepath.name in EXEMPT_FILES:
        return []

    content = filepath.read_text(encoding="utf-8")

    # Must have frontmatter
    if not content.startswith("---"):
        violations.append(f"  Missing YAML frontmatter (must start with ---)")
        return violations

    fields = parse_frontmatter(content)

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in fields:
            violations.append(f"  Missing required field: {field}")
        elif field in ("created", "updated") and not DATE_PATTERN.match(fields[field]):
            violations.append(
                f"  Field '{field}' must be YYYY-MM-DD format, got: {fields[field]}"
            )

    # Date sanity: updated >= created
    if "created" in fields and "updated" in fields:
        if DATE_PATTERN.match(fields["created"]) and DATE_PATTERN.match(
            fields["updated"]
        ):
            if fields["updated"] < fields["created"]:
                violations.append(
                    f"  'updated' ({fields['updated']}) is before 'created' ({fields['created']})"
                )

    # Recommended fields (warn, don't fail)
    for field in RECOMMENDED_FIELDS:
        if field not in fields:
            violations.append(f"  [warn] Missing recommended field: {field}")

    return violations


def get_staged_docs() -> list[Path]:
    """Get docs/*.md files that are staged for commit."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
    )
    staged = []
    for line in result.stdout.strip().split("\n"):
        if line.startswith("docs/") and line.endswith(".md"):
            staged.append(Path(line))
    return staged


def main():
    args = sys.argv[1:]

    if "--staged" in args:
        files = get_staged_docs()
        if not files:
            sys.exit(0)
    elif args:
        files = [Path(a) for a in args if a.endswith(".md")]
    else:
        files = sorted(DOCS_DIR.glob("*.md"))

    all_violations: dict[str, list[str]] = {}

    for filepath in files:
        violations = check_file(filepath)
        if violations:
            all_violations[str(filepath)] = violations

    if not all_violations:
        if "--quiet" not in args:
            print(f"✓ {len(files)} docs pass frontmatter check")
        sys.exit(0)

    # Report violations
    errors = 0
    warnings = 0
    print("Doc frontmatter violations:", file=sys.stderr)
    for filepath, violations in all_violations.items():
        print(f"\n  {filepath}:", file=sys.stderr)
        for v in violations:
            if "[warn]" in v:
                warnings += 1
            else:
                errors += 1
            print(f"    {v}", file=sys.stderr)

    print(f"\n  {errors} error(s), {warnings} warning(s)", file=sys.stderr)

    # Only fail on errors, not warnings
    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()
