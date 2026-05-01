#!/usr/bin/env python3
"""Lint all SKILL.md files for metadata consistency.

Uses ``parse_frontmatter()`` from ``core.skill_manager`` — the same
parser the runtime uses — so format changes never cause linter/runtime
disagreement.

Catches:
  - name: field doesn't match folder name (case-insensitive)
  - name: field is not lowercase (SDK matches case-sensitively)
  - missing required fields (name, description)
  - malformed YAML frontmatter

Run from repo root (CI runs after backend deps are installed):
    python scripts/lint_skills.py

Exit code 0 = all skills valid, 1 = at least one error.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add backend/ to sys.path so we can import core.skill_manager
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from core.skill_manager import SkillParseError, parse_frontmatter  # noqa: E402

SKILL_DIRS = [Path("backend/skills")]


def lint_skill(skill_md: Path) -> list[str]:
    """Return list of error strings for a single SKILL.md."""
    errors: list[str] = []
    folder = skill_md.parent.name  # e.g. "s_weather"

    try:
        meta, _body = parse_frontmatter(skill_md)
    except SkillParseError as e:
        return [f"{skill_md}: {e}"]
    except Exception as e:
        return [f"{skill_md}: cannot read: {e}"]

    name = meta.get("name")
    if not name:
        errors.append(f"{skill_md}: missing 'name:' field in frontmatter")
        return errors

    name = str(name)

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
    if not meta.get("description"):
        errors.append(f"{skill_md}: missing 'description:' field in frontmatter")

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
