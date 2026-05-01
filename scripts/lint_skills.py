#!/usr/bin/env python3
"""Lint all SKILL.md files for metadata consistency.

Catches:
  - name: field doesn't match folder name (case-insensitive)
  - name: field is not lowercase (SDK matches case-sensitively)
  - missing required fields (name, description)

Run from repo root:
    python scripts/lint_skills.py

Exit code 0 = all skills valid, 1 = at least one error.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SKILL_DIRS = [Path("backend/skills")]
NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
DESC_RE = re.compile(r"^description:", re.MULTILINE)


def lint_skill(skill_md: Path) -> list[str]:
    """Return list of error strings for a single SKILL.md."""
    errors: list[str] = []
    folder = skill_md.parent.name  # e.g. "s_weather"

    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception as e:
        return [f"{skill_md}: cannot read: {e}"]

    # Check name field exists
    match = NAME_RE.search(text)
    if not match:
        errors.append(f"{skill_md}: missing 'name:' field")
        return errors

    name = match.group(1).strip().strip("'\"")

    # Check lowercase
    if name != name.lower():
        errors.append(
            f"{skill_md}: name '{name}' must be lowercase ('{name.lower()}')"
        )

    # Check matches folder (strip s_ prefix)
    folder_base = folder.removeprefix("s_")
    if name.lower() != folder_base.lower():
        errors.append(
            f"{skill_md}: name '{name}' doesn't match folder '{folder}' "
            f"(expected '{folder_base}')"
        )

    # Check description exists
    if not DESC_RE.search(text):
        errors.append(f"{skill_md}: missing 'description:' field")

    return errors


def main() -> int:
    all_errors: list[str] = []

    for skill_dir in SKILL_DIRS:
        if not skill_dir.exists():
            continue
        for skill_md in sorted(skill_dir.glob("s_*/SKILL.md")):
            all_errors.extend(lint_skill(skill_md))

    if all_errors:
        print(f"\n{len(all_errors)} skill lint error(s):")
        for err in all_errors:
            print(f"  ✗ {err}")
        return 1

    count = sum(
        1
        for d in SKILL_DIRS
        if d.exists()
        for _ in d.glob("s_*/SKILL.md")
    )
    print(f"All {count} SKILL.md files pass lint checks ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
