"""Skill Registry — compact skill index for system prompt injection.

Scans the skills directory, categorizes skills by known prefixes, and
generates a tiered (always/lazy) markdown registry suitable for system
prompt injection. Reads tier from manifest.yaml (if present) or SKILL.md
frontmatter. Results are cached and only regenerated when directory content
changes.

Key public symbols:

- ``SkillRegistry``    — Scanner, categorizer, tier partitioner, cache manager.
- ``SKILL_CATEGORIES`` — Known category mapping for skill names.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
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


# Module-level singleton cache: skills_dir (str) → SkillRegistry instance.
# Avoids re-creating the registry (and re-scanning the directory) on every
# prompt build.  Same pattern as _session_recall_cache in memory_index.py.
_registry_cache: dict[str, "SkillRegistry"] = {}


def _get_skill_registry(skills_dir: Path) -> "SkillRegistry":
    """Return a cached SkillRegistry for the given skills directory."""
    key = str(skills_dir)
    if key not in _registry_cache:
        _registry_cache[key] = SkillRegistry(skills_dir)
    return _registry_cache[key]


class SkillRegistry:
    """Scans skills directory and generates a compact markdown registry."""

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._cache: Optional[str] = None
        self._cache_hash: Optional[str] = None
        # Content-hash → trust status cache for SkillGuard scans
        self._trust_cache: dict[str, str] = {}

    def generate_compact_registry(self) -> str:
        """Scan skills directory and return tiered compact markdown registry.

        Always-tier skills: categorized names (current format).
        Lazy-tier skills: flat list with one-line descriptions.

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

        always_skills, lazy_skills = self._partition_by_tier(skill_names)

        lines: list[str] = [f"## Available Skills ({len(skill_names)})"]

        # Always tier: categorized names (existing format)
        always_cats = self._categorize(always_skills)
        for cat_name in list(SKILL_CATEGORIES.keys()) + ["Other"]:
            skills = always_cats.get(cat_name, [])
            if skills:
                lines.append(f"### {cat_name}: {', '.join(sorted(skills))}")

        # Lazy tier: flat list with one-line descriptions
        if lazy_skills:
            lines.append(f"\n### On-Demand ({len(lazy_skills)} more skills)")
            for name in sorted(lazy_skills):
                desc = self._get_one_liner(name)
                lines.append(f"- **{name}**: {desc}")

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
        """List all skill names from s_*/SKILL.md directories.

        Also runs SkillGuard scan on each discovered skill, caching
        results by content hash to avoid redundant scans.
        """
        if not self._skills_dir.is_dir():
            return []
        skills: list[str] = []
        for entry in sorted(self._skills_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith("s_"):
                skill_md = entry / "SKILL.md"
                if skill_md.exists():
                    name = entry.name[2:]
                    skills.append(name)
                    self._scan_skill_trust(skill_md, name)
        return skills

    def _scan_skill_trust(self, skill_md: Path, name: str) -> None:
        """Scan a SKILL.md with SkillGuard, cache by content hash."""
        try:
            content = skill_md.read_text(encoding="utf-8")
            content_hash = hashlib.md5(
                content.encode(), usedforsecurity=False
            ).hexdigest()
            if content_hash in self._trust_cache:
                return  # Already scanned this exact content
            try:
                from .skill_guard import SkillGuard, TrustLevel
                guard = SkillGuard()
                result = guard.scan_skill(skill_md, TrustLevel.BUILTIN)
                status = "trusted" if result.allowed else "flagged"
            except ImportError:
                status = "unscanned"
            except Exception:
                status = "unscanned"
            self._trust_cache[content_hash] = status
        except Exception:
            pass  # Don't break discovery on scan failure

    def _categorize(self, skill_names: list[str]) -> dict[str, list[str]]:
        """Map skills to categories. Uncategorized go to 'Other'."""
        result: dict[str, list[str]] = {}
        for name in skill_names:
            category = _SKILL_TO_CATEGORY.get(name, "Other")
            result.setdefault(category, []).append(name)
        return result

    # ------------------------------------------------------------------
    # Tier support (lazy / always)
    # ------------------------------------------------------------------

    def _partition_by_tier(
        self, names: list[str]
    ) -> tuple[list[str], list[str]]:
        """Split skills into (always, lazy) lists based on tier metadata."""
        always: list[str] = []
        lazy: list[str] = []
        for name in names:
            tier = self._read_tier(name)
            (always if tier == "always" else lazy).append(name)
        return always, lazy

    def _read_tier(self, name: str) -> str:
        """Read tier for a skill: manifest.yaml > SKILL.md frontmatter > 'lazy'.

        Follows single source of truth: manifest.yaml is authoritative
        when present; SKILL.md frontmatter is fallback for simple skills.
        """
        skill_dir = self._skills_dir / f"s_{name}"

        # 1. Try manifest.yaml (authoritative)
        try:
            from .manifest_loader import ManifestLoader
            manifest = ManifestLoader.load(skill_dir)
            if manifest is not None:
                return manifest.tier
        except ImportError:
            pass

        # 2. Fallback: parse SKILL.md frontmatter for tier field
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8")
                return self._extract_tier_from_frontmatter(content)
            except Exception:
                pass

        return "lazy"  # Conservative default

    @staticmethod
    def _extract_tier_from_frontmatter(content: str) -> str:
        """Extract tier value from YAML frontmatter in SKILL.md."""
        # Match YAML frontmatter between --- markers
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return "lazy"
        frontmatter = match.group(1)
        # Look for tier: always or tier: lazy
        tier_match = re.search(r"^tier:\s*(\w+)", frontmatter, re.MULTILINE)
        if tier_match:
            tier = tier_match.group(1).strip().lower()
            if tier in ("always", "lazy"):
                return tier
        return "lazy"

    def _get_one_liner(self, name: str) -> str:
        """Extract first sentence of description for lazy skill listing.

        Reads from manifest.yaml or SKILL.md frontmatter description field.
        Returns a short string suitable for the On-Demand section.
        """
        skill_dir = self._skills_dir / f"s_{name}"

        # 1. Try manifest — not typical for simple skills but check anyway
        try:
            from .manifest_loader import ManifestLoader
            manifest = ManifestLoader.load(skill_dir)
            if manifest is not None:
                return name  # Manifest doesn't carry description; use name
        except ImportError:
            pass

        # 2. Parse SKILL.md frontmatter description
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8")
                return self._extract_description_one_liner(content)
            except Exception:
                pass

        return name  # Fallback to just the name

    @staticmethod
    def _extract_description_one_liner(content: str) -> str:
        """Extract first sentence from SKILL.md frontmatter description."""
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return ""
        frontmatter = match.group(1)
        # Multi-line description after 'description:' or 'description: >'
        desc_match = re.search(
            r"^description:\s*>?\s*\n((?:\s+.*\n)*)",
            frontmatter,
            re.MULTILINE,
        )
        if desc_match:
            lines = desc_match.group(1).strip().split("\n")
            if lines:
                first = lines[0].strip()
                # Take up to first period or the whole first line
                dot = first.find(".")
                if dot > 0:
                    return first[: dot + 1]
                return first
        # Single-line description
        desc_match = re.search(
            r"^description:\s*(.+)$", frontmatter, re.MULTILINE
        )
        if desc_match:
            desc = desc_match.group(1).strip().strip("\"'")
            dot = desc.find(".")
            if dot > 0:
                return desc[: dot + 1]
            return desc
        return ""
