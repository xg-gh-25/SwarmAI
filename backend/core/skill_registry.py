"""Skill Registry — compact skill index for system prompt injection.

Scans the skills directory, categorizes skills by known prefixes, and
generates a compact markdown registry suitable for system prompt injection.
Results are cached and only regenerated when directory content changes.

Key public symbols:

- ``SkillRegistry``    — Scanner, categorizer, and cache manager.
- ``SKILL_CATEGORIES`` — Known category mapping for skill names.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Category mapping for known skill prefixes/names
SKILL_CATEGORIES: dict[str, list[str]] = {
    "Memory": ["save-memory", "save-activity", "save-context", "memory-distill"],
    "Development": ["code-review", "qa", "skill-builder", "skill-feedback", "skillify-session", "estimate-tokens"],
    "Research": ["deep-research", "github-research", "consulting-report", "tavily-search", "summarize"],
    "Writing": ["narrative-writing", "humanize", "translate", "pptx", "docx", "xlsx", "pdf"],
    "Integrations": ["slack", "outlook-assistant", "google-workspace", "apple-reminders", "sonos"],
    "Automation": ["autonomous-pipeline", "browser-agent", "peekaboo", "tmux", "scheduler", "job-manager"],
    "Workspace": ["workspace-finder", "workspace-git", "workspace-organizer", "ws-context-init", "project-manager"],
    "Ops": ["radar-todo", "system-health", "health-check", "chat-brain-check", "deliver", "evaluate", "custom-agents"],
    "Content": ["image-gen", "video-gen", "podcast-gen", "weather", "finance"],
    "UI": ["frontend-design", "web-design-review", "wireframe"],
    "System": ["self-evolution"],
}

# Reverse map: skill_name -> category
_SKILL_TO_CATEGORY: dict[str, str] = {}
for _cat, _skills in SKILL_CATEGORIES.items():
    for _s in _skills:
        _SKILL_TO_CATEGORY[_s] = _cat


class SkillRegistry:
    """Scans skills directory and generates a compact markdown registry."""

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._cache: Optional[str] = None
        self._cache_hash: Optional[str] = None

    def generate_compact_registry(self) -> str:
        """Scan skills directory and return compact markdown registry.

        Caches result. Regenerates only when directory content changes
        (mtime-based hash).
        """
        current_hash = self._compute_dir_hash()
        if self._cache is not None and self._cache_hash == current_hash:
            return self._cache

        skill_names = self._discover_skills()
        if not skill_names:
            self._cache = ""
            self._cache_hash = current_hash
            return ""

        categories = self._categorize(skill_names)

        lines: list[str] = [f"## Available Skills ({len(skill_names)})"]
        for cat_name in list(SKILL_CATEGORIES.keys()) + ["Other"]:
            skills = categories.get(cat_name, [])
            if skills:
                lines.append(f"### {cat_name}: {', '.join(sorted(skills))}")

        result = "\n".join(lines)
        self._cache = result
        self._cache_hash = current_hash
        return result

    def _compute_dir_hash(self) -> str:
        """Hash of all skill directory names + their SKILL.md mtimes."""
        if not self._skills_dir.is_dir():
            return ""
        parts: list[str] = []
        for entry in sorted(self._skills_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith("s_"):
                skill_md = entry / "SKILL.md"
                if skill_md.exists():
                    mtime = os.path.getmtime(str(skill_md))
                    parts.append(f"{entry.name}:{mtime}")
        return hashlib.md5("|".join(parts).encode(), usedforsecurity=False).hexdigest()

    def _discover_skills(self) -> list[str]:
        """List all skill names from s_*/SKILL.md directories."""
        if not self._skills_dir.is_dir():
            return []
        skills: list[str] = []
        for entry in sorted(self._skills_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith("s_"):
                if (entry / "SKILL.md").exists():
                    # Strip the "s_" prefix to get the skill name
                    skills.append(entry.name[2:])
        return skills

    def _categorize(self, skill_names: list[str]) -> dict[str, list[str]]:
        """Map skills to categories. Uncategorized go to 'Other'."""
        result: dict[str, list[str]] = {}
        for name in skill_names:
            category = _SKILL_TO_CATEGORY.get(name, "Other")
            result.setdefault(category, []).append(name)
        return result
