#!/usr/bin/env python3
"""
migrate_skills.py — Migrate skills to the lazy-loading tier system.

For each skill in backend/skills/s_*/:
  1. Add tier: always|lazy to SKILL.md frontmatter
  2. Generate manifest.yaml for complex skills (3+ files including Python/JS scripts)
  3. For lazy-tier skills: create INSTRUCTIONS.md and replace SKILL.md with a minimal stub
  4. For always-tier skills: just add tier to frontmatter

Idempotent: safe to run multiple times.

Usage:
  python scripts/migrate_skills.py                 # execute migration
  python scripts/migrate_skills.py --dry-run       # preview only
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

ALWAYS_TIER_SKILLS = frozenset([
    "save-memory",
    "save-activity",
    "memory-distill",
    "workspace-finder",
    "workspace-git",
    "project-manager",
    "radar-todo",
    "evaluate",
    "deliver",
    "deep-research",
    "tavily-search",
    "summarize",
    "slack",
    "outlook-assistant",
    "self-evolution",
])

COMPLEX_SKILLS = frozenset([
    "cmhk-weekly-report",
    "pptx",
    "docx",
    "pdf",
    "cmhk-industry-gtm-analysis",
    "skill-builder",
    "cmhk-gtm-report-gen",
    "cmhk-customer-ai-research",
    "narrative-writing",
    "cmhk-data-proxy",
    "save-memory",
    "outlook-assistant",
    "browser-agent",
    "humanize",
    "estimate-tokens",
    "custom-agents",
])


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict | None, str, str]:
    """Return (fm_dict, raw_fm_yaml, body) or (None, '', text) if no frontmatter.

    fm_dict is a lightweight parse — we keep the raw YAML string to preserve
    formatting and reconstruct it by inserting/replacing the tier field.
    """
    m = _FM_RE.match(text)
    if not m:
        return None, "", text
    raw_yaml = m.group(1)
    body = text[m.end():]

    # Lightweight key-value extraction (handles multi-line YAML scalars like description: >)
    fm: dict[str, str] = {}
    current_key = None
    current_val_lines: list[str] = []

    for line in raw_yaml.splitlines():
        # New top-level key (not indented, has a colon)
        key_match = re.match(r"^(\w[\w-]*)\s*:\s*(.*)", line)
        if key_match:
            # Save previous key
            if current_key is not None:
                fm[current_key] = "\n".join(current_val_lines).strip()
            current_key = key_match.group(1)
            current_val_lines = [key_match.group(2)]
        else:
            # Continuation of previous value
            current_val_lines.append(line)

    if current_key is not None:
        fm[current_key] = "\n".join(current_val_lines).strip()

    return fm, raw_yaml, body


def set_tier_in_frontmatter(raw_yaml: str, tier: str) -> str:
    """Insert or replace `tier:` in the raw YAML frontmatter string.

    Returns the updated YAML content (without the --- delimiters).
    """
    lines = raw_yaml.rstrip("\n").split("\n")

    # Find and replace existing tier line
    tier_line = f"tier: {tier}"
    for i, line in enumerate(lines):
        if re.match(r"^tier\s*:", line):
            lines[i] = tier_line
            return "\n".join(lines) + "\n"

    # Not found — append before closing
    lines.append(tier_line)
    return "\n".join(lines) + "\n"


def rebuild_file(raw_yaml: str, body: str) -> str:
    """Reassemble a file from frontmatter YAML and body."""
    return f"---\n{raw_yaml}---\n{body}"


# ---------------------------------------------------------------------------
# Field extractors for the stub
# ---------------------------------------------------------------------------

def extract_field(fm: dict, key: str) -> str:
    """Pull a field out of the frontmatter dict, returning '' if absent."""
    return fm.get(key, "")


def extract_first_description_line(fm: dict) -> str:
    """Get the first meaningful line of the description field."""
    desc = fm.get("description", "")
    # description: > means the value follows on the next line(s) with indentation
    # Our parser already joined them; split on newlines and find the first non-empty
    for line in desc.split("\n"):
        line = line.strip().lstrip(">").strip()
        if line:
            return line
    return ""


def extract_trigger_and_do_not_use(fm: dict) -> tuple[str, str]:
    """Parse TRIGGER and DO NOT USE from the description field."""
    desc = fm.get("description", "")
    trigger = ""
    do_not_use = ""

    # Look for TRIGGER: ... and DO NOT USE: ...
    trigger_m = re.search(r"TRIGGER:\s*(.+?)(?:\n|DO NOT USE:|SIBLINGS:|$)", desc, re.DOTALL)
    if trigger_m:
        trigger = trigger_m.group(1).strip().rstrip(".")

    dnu_m = re.search(r"DO NOT USE:\s*(.+?)(?:\n|SIBLINGS:|$)", desc, re.DOTALL)
    if dnu_m:
        do_not_use = dnu_m.group(1).strip().rstrip(".")

    return trigger, do_not_use


def extract_title(body: str) -> str:
    """Extract the first # heading from the body."""
    for line in body.split("\n"):
        m = re.match(r"^#\s+(.+)", line)
        if m:
            return m.group(1).strip()
    return "Untitled"


# ---------------------------------------------------------------------------
# Manifest generation for complex skills
# ---------------------------------------------------------------------------

def extract_first_docstring(filepath: Path) -> str:
    """Extract the first line of a Python docstring, or a JS comment block."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    if filepath.suffix == ".py":
        # Use AST for Python
        try:
            tree = ast.parse(content)
            docstring = ast.get_docstring(tree)
            if docstring:
                return docstring.split("\n")[0].strip()
        except SyntaxError:
            pass
        # Fallback: look for a comment line near the top
        for line in content.split("\n")[:20]:
            line = line.strip()
            if line.startswith("#") and not line.startswith("#!"):
                return line.lstrip("# ").strip()
        return ""

    elif filepath.suffix in (".js", ".mjs"):
        # Look for JSDoc /** ... */ or // comment
        jsdoc = re.search(r"/\*\*?\s*\n?\s*\*?\s*(.+?)(?:\n|\*/)", content)
        if jsdoc:
            return jsdoc.group(1).strip().rstrip("*").strip()
        for line in content.split("\n")[:20]:
            line = line.strip()
            if line.startswith("//"):
                return line.lstrip("/ ").strip()
        return ""

    return ""


def is_entry_point(filepath: Path, skill_name: str) -> bool:
    """Heuristic: is this file likely an entry point?"""
    stem = filepath.stem.lower()
    for keyword in ("generator", "main", "entry", "cli"):
        if keyword in stem:
            return True
    # Skill name match (e.g. browser-agent.mjs for browser-agent skill)
    normalized = skill_name.replace("-", "").replace("_", "")
    if normalized in stem.replace("-", "").replace("_", ""):
        return True
    return False


def generate_manifest(skill_dir: Path, skill_name: str) -> str:
    """Generate manifest.yaml content for a complex skill."""
    tier = "always" if skill_name in ALWAYS_TIER_SKILLS else "lazy"
    lines = [
        f"# Auto-generated manifest for {skill_name}",
        f"name: {skill_name}",
        f'version: "1.0.0"',
        f"tier: {tier}",
        "",
        "scripts:",
    ]

    # Collect .py and .js/.mjs files (up to 2 levels deep)
    code_files: list[Path] = []
    for ext in ("*.py", "*.js", "*.mjs"):
        code_files.extend(skill_dir.rglob(ext))

    # Sort for determinism, exclude __pycache__
    code_files = sorted(
        f for f in code_files
        if "__pycache__" not in str(f) and f.name != "__init__.py"
    )

    for cf in code_files:
        rel = cf.relative_to(skill_dir)
        desc = extract_first_docstring(cf)
        entry = is_entry_point(cf, skill_name)
        line = f"  - path: {rel}"
        if desc:
            line += f"\n    description: \"{desc}\""
        if entry:
            line += "\n    entry: true"
        lines.append(line)

    # Subdirectories as resources
    subdirs = sorted(
        d.relative_to(skill_dir)
        for d in skill_dir.iterdir()
        if d.is_dir() and d.name != "__pycache__" and not d.name.startswith(".")
    )
    if subdirs:
        lines.append("")
        lines.append("resources:")
        for sd in subdirs:
            lines.append(f"  - {sd}")

    # Dependencies from requirements.txt
    req_file = skill_dir / "requirements.txt"
    if req_file.exists():
        try:
            deps = [
                line.strip()
                for line in req_file.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            if deps:
                lines.append("")
                lines.append("dependencies:")
                for dep in deps:
                    lines.append(f"  - {dep}")
        except Exception:
            pass

    # Also check for package.json
    pkg_json = skill_dir / "package.json"
    if pkg_json.exists():
        lines.append("")
        lines.append("# Note: package.json found — npm dependencies may apply")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stub generation for lazy-tier skills
# ---------------------------------------------------------------------------

def build_stub(fm: dict, raw_yaml_with_tier: str) -> str:
    """Build a minimal SKILL.md stub for a lazy-tier skill."""
    name = fm.get("name", "Unknown")
    first_desc = extract_first_description_line(fm)
    trigger, do_not_use = extract_trigger_and_do_not_use(fm)
    title = name  # Use the frontmatter name as the title

    # Build description block for frontmatter
    desc_parts = [first_desc]
    if trigger:
        desc_parts.append(f"TRIGGER: {trigger}")
    if do_not_use:
        desc_parts.append(f"DO NOT USE: {do_not_use}")

    # Rebuild frontmatter preserving all original fields
    # We use the raw_yaml_with_tier which already has tier set
    stub_fm = f"---\n{raw_yaml_with_tier}---\n"

    # Build body
    body_lines = [
        f"# {title}",
        "",
        "> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.",
        "",
    ]
    if trigger:
        body_lines.append(f"TRIGGER: {trigger}")
    if do_not_use:
        body_lines.append(f"DO NOT USE: {do_not_use}")
    if trigger or do_not_use:
        body_lines.append("")

    return stub_fm + "\n".join(body_lines)


# ---------------------------------------------------------------------------
# Per-skill migration
# ---------------------------------------------------------------------------

class MigrationStats:
    def __init__(self):
        self.skills_processed = 0
        self.tier_added_always = 0
        self.tier_added_lazy = 0
        self.tier_already_set = 0
        self.instructions_created = 0
        self.instructions_already_exist = 0
        self.manifests_created = 0
        self.manifests_already_exist = 0
        self.errors: list[str] = []
        self.skipped: list[str] = []

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            "  Migration Summary",
            "=" * 60,
            f"  Skills processed:         {self.skills_processed}",
            f"  tier: always added:       {self.tier_added_always}",
            f"  tier: lazy added:         {self.tier_added_lazy}",
            f"  tier already set:         {self.tier_already_set}",
            f"  INSTRUCTIONS.md created:  {self.instructions_created}",
            f"  INSTRUCTIONS.md existed:  {self.instructions_already_exist}",
            f"  manifest.yaml created:    {self.manifests_created}",
            f"  manifest.yaml existed:    {self.manifests_already_exist}",
            f"  Skipped:                  {len(self.skipped)}",
            f"  Errors:                   {len(self.errors)}",
        ]
        if self.skipped:
            lines.append("")
            lines.append("  Skipped skills:")
            for s in self.skipped:
                lines.append(f"    - {s}")
        if self.errors:
            lines.append("")
            lines.append("  Errors:")
            for e in self.errors:
                lines.append(f"    - {e}")
        lines.append("=" * 60)
        return "\n".join(lines)


def skill_name_from_dir(d: Path) -> str:
    """Extract skill name from directory name like s_save-memory -> save-memory."""
    return d.name.removeprefix("s_")


def migrate_skill(skill_dir: Path, dry_run: bool, stats: MigrationStats) -> None:
    """Migrate a single skill."""
    skill_name = skill_name_from_dir(skill_dir)
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        stats.skipped.append(f"{skill_name}: no SKILL.md")
        return

    stats.skills_processed += 1
    tier = "always" if skill_name in ALWAYS_TIER_SKILLS else "lazy"

    try:
        original_content = skill_md.read_text(encoding="utf-8")
    except Exception as e:
        stats.errors.append(f"{skill_name}: failed to read SKILL.md: {e}")
        return

    fm, raw_yaml, body = parse_frontmatter(original_content)
    if fm is None:
        stats.errors.append(f"{skill_name}: no frontmatter found in SKILL.md")
        return

    # --- Check if tier is already set correctly ---
    existing_tier = fm.get("tier", "").strip()
    tier_needs_update = existing_tier != tier

    if not tier_needs_update:
        stats.tier_already_set += 1
    else:
        if tier == "always":
            stats.tier_added_always += 1
        else:
            stats.tier_added_lazy += 1

    # --- Update frontmatter with tier ---
    updated_yaml = set_tier_in_frontmatter(raw_yaml, tier)

    # --- Always-tier: just update frontmatter ---
    if tier == "always":
        if tier_needs_update:
            new_content = rebuild_file(updated_yaml, body)
            if dry_run:
                print(f"  [DRY-RUN] {skill_name}: would add tier: always to SKILL.md")
            else:
                skill_md.write_text(new_content, encoding="utf-8")
                print(f"  {skill_name}: added tier: always to SKILL.md")
        else:
            print(f"  {skill_name}: tier: always already set (no change)")

        # Complex skill: generate manifest even for always-tier
        if skill_name in COMPLEX_SKILLS:
            _maybe_generate_manifest(skill_dir, skill_name, dry_run, stats)
        return

    # --- Lazy-tier: create INSTRUCTIONS.md + stub ---
    instructions_md = skill_dir / "INSTRUCTIONS.md"

    # Step 1: Create INSTRUCTIONS.md (full original content)
    if instructions_md.exists():
        stats.instructions_already_exist += 1
        print(f"  {skill_name}: INSTRUCTIONS.md already exists (preserving)")
    else:
        # INSTRUCTIONS.md gets everything from the original SKILL.md below the frontmatter
        if dry_run:
            print(f"  [DRY-RUN] {skill_name}: would create INSTRUCTIONS.md ({len(body)} chars)")
        else:
            instructions_md.write_text(body, encoding="utf-8")
            print(f"  {skill_name}: created INSTRUCTIONS.md ({len(body)} chars)")
        stats.instructions_created += 1

    # Step 2: Replace SKILL.md with stub
    # Check if SKILL.md is already a stub (has tier: lazy and the activation marker)
    is_already_stub = (
        existing_tier == "lazy"
        and "Read INSTRUCTIONS.md before proceeding" in original_content
    )

    if is_already_stub:
        print(f"  {skill_name}: SKILL.md already stubbed (no change)")
    else:
        stub_content = build_stub(fm, updated_yaml)
        if dry_run:
            print(f"  [DRY-RUN] {skill_name}: would replace SKILL.md with stub")
        else:
            skill_md.write_text(stub_content, encoding="utf-8")
            print(f"  {skill_name}: replaced SKILL.md with lazy stub")

    # Step 3: Complex skill: generate manifest
    if skill_name in COMPLEX_SKILLS:
        _maybe_generate_manifest(skill_dir, skill_name, dry_run, stats)


def _maybe_generate_manifest(
    skill_dir: Path, skill_name: str, dry_run: bool, stats: MigrationStats
) -> None:
    """Generate manifest.yaml if it doesn't already exist."""
    manifest_path = skill_dir / "manifest.yaml"
    if manifest_path.exists():
        stats.manifests_already_exist += 1
        print(f"  {skill_name}: manifest.yaml already exists (preserving)")
        return

    manifest_content = generate_manifest(skill_dir, skill_name)
    if dry_run:
        print(f"  [DRY-RUN] {skill_name}: would create manifest.yaml")
    else:
        manifest_path.write_text(manifest_content, encoding="utf-8")
        print(f"  {skill_name}: created manifest.yaml")
    stats.manifests_created += 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate skills to the lazy-loading tier system."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files.",
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=SKILLS_DIR,
        help="Override the skills directory (default: auto-detected).",
    )
    args = parser.parse_args()

    skills_dir = args.skills_dir.resolve()
    if not skills_dir.is_dir():
        print(f"ERROR: skills directory not found: {skills_dir}", file=sys.stderr)
        return 1

    # Discover skill directories
    skill_dirs = sorted(
        d for d in skills_dir.iterdir()
        if d.is_dir() and d.name.startswith("s_")
    )

    if not skill_dirs:
        print(f"ERROR: no s_* skill directories found in {skills_dir}", file=sys.stderr)
        return 1

    mode = "[DRY-RUN] " if args.dry_run else ""
    print(f"{mode}Migrating {len(skill_dirs)} skills in {skills_dir}")
    print("-" * 60)

    stats = MigrationStats()

    for skill_dir in skill_dirs:
        try:
            migrate_skill(skill_dir, args.dry_run, stats)
        except Exception as e:
            skill_name = skill_name_from_dir(skill_dir)
            stats.errors.append(f"{skill_name}: unexpected error: {e}")
            print(f"  ERROR {skill_name}: {e}", file=sys.stderr)

    print(stats.summary())
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
