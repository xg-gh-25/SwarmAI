"""Skill Registry — skill discovery, SkillGuard scanning, and tier classification.

The Claude Agent SDK handles skill listing via system-reminder injection
(reads each SKILL.md and injects name+description+triggers). This module
provides:

1. SkillGuard trust scanning on discovery (security).
2. Tier classification utility (``_read_tier``) for any code that needs
   to know if a skill is always or lazy (e.g., future manifest-aware
   invocation in Phase 4).
3. ``generate_compact_registry()`` for test/debug use only — NOT injected
   into production prompts (removed in 1dc2a7b, SDK handles it).

Key public symbols:

- ``SkillRegistry``    — Scanner, categorizer, SkillGuard scanner, tier classifier.
- ``SKILL_CATEGORIES`` — Known category mapping for skill names.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional

from .manifest_loader import ManifestLoader

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

# ---------------------------------------------------------------------------
# Capabilities-domain: user-facing category + visibility derivation
# (run_b5d98151). PURE functions of the folder name (no session/request
# context) so they compute once at list-build time and cache with the skill
# list. Consumed by routers/skills.py::_skill_info_to_response.
# ---------------------------------------------------------------------------

# Valid load tiers. A skill is `always` (full SKILL.md loaded at session start) or
# `lazy` (stub + on-invocation Read). Anything else is clamped to `lazy` (see resolve_tier).
_VALID_TIERS: tuple[str, ...] = ("always", "lazy")
DEFAULT_TIER = "lazy"


def resolve_tier(
    manifest_present: bool,
    manifest_tier: Optional[str] = None,
    frontmatter_tier: Optional[str] = None,
) -> str:
    """Resolve a skill's load tier — the SINGLE source of precedence + clamping.

    Precedence (mirrors the original ``SkillRegistry._read_tier`` short-circuit):
    ``manifest.yaml`` is TERMINAL when present (even if its ``tier`` defaulted to
    ``lazy``, it wins over any frontmatter value) > SKILL.md frontmatter ``tier`` >
    ``DEFAULT_TIER``.

    CLAMP (run_a85e6641 Gate-1 BLOCK-5): ``SkillManifest.tier`` is an UNVALIDATED
    ``str`` — a ``manifest.yaml`` with ``tier: eager`` (typo) would otherwise flow
    verbatim into ``SkillResponse.tier`` (a ``Literal["always","lazy"]``) and raise a
    pydantic ValidationError → 500 on the WHOLE skills list. So any value not in
    ``_VALID_TIERS`` is clamped to ``DEFAULT_TIER``. Shared by ``_read_tier`` and
    ``parse_skill_md`` so tier resolution can never diverge between them.
    """
    if manifest_present:
        chosen = manifest_tier
    elif frontmatter_tier is not None:
        chosen = frontmatter_tier
    else:
        chosen = DEFAULT_TIER
    return chosen if chosen in _VALID_TIERS else DEFAULT_TIER


# The fallback category — a skill with no mapping never vanishes from the UI.
DEFAULT_CATEGORY = "Utilities"

# Name-prefix rules for Amazon-internal / customer-specific skills. These form
# the owner-only "Internal" group AND are visibility=internal (hidden from
# non-owner surfaces via the run-mode filter in routers/skills.py). Matched
# against the STRIPPED name (s_ removed).
_INTERNAL_NAME_PREFIXES: tuple[str, ...] = (
    "cmhk-",       # CMHK sales intelligence (internal customer)
    "ivt-",        # IVTHub (internal customer)
    "internal-",   # s_internal-* (brazil, crux-cr, crux-review)
    "meddpicc",    # MEDDPICC scorecard (internal sales methodology)
)


def _strip_prefix(folder_name: str) -> str:
    """Strip the ``s_`` folder prefix to get the mapping key. Safe on empty."""
    if folder_name.startswith("s_"):
        return folder_name[2:]
    return folder_name


def derive_visibility(
    folder_name: str,
    frontmatter_visibility: Optional[str] = None,
) -> str:
    """Return ``"internal"`` or ``"public"`` for a skill.

    Priority: explicit frontmatter ``visibility:`` > internal-prefix rule >
    ``"public"`` default. Pure function of the name — no session context. A
    malformed frontmatter value is ignored (falls through to the rule).
    """
    if frontmatter_visibility in ("public", "internal"):
        return frontmatter_visibility
    name = _strip_prefix(folder_name)
    if any(name.startswith(p) for p in _INTERNAL_NAME_PREFIXES):
        return "internal"
    return "public"


def derive_category(
    folder_name: str,
    frontmatter_category: Optional[str] = None,
) -> str:
    """Return the user-facing category group name for a skill.

    Priority: explicit frontmatter ``category:`` > internal-prefix ("Internal")
    > curated ``SKILL_CATEGORIES`` map > ``DEFAULT_CATEGORY`` fallback. Pure
    function of the name — an unmapped skill lands in Utilities, never vanishes.
    """
    if frontmatter_category:
        return frontmatter_category
    # Internal skills group under a single owner-only "Internal" group,
    # regardless of what functional bucket they'd otherwise map to.
    if derive_visibility(folder_name) == "internal":
        return "Internal"
    name = _strip_prefix(folder_name)
    return _SKILL_TO_CATEGORY.get(name, DEFAULT_CATEGORY)


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
        """Scan skills directory and return compact markdown registry.

        NOTE: Not injected into production prompts. The Claude Agent SDK's
        system-reminder handles skill discovery directly by reading each
        SKILL.md. This method exists for test/debug use and SkillGuard
        scanning (triggered as side-effect of _discover_skills).

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
        """Hash of all skill directory names + their SKILL.md/manifest.yaml mtimes."""
        if not self._skills_dir.is_dir():
            return ""
        parts: list[str] = []
        for entry in sorted(self._skills_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith("s_"):
                skill_md = entry / "SKILL.md"
                if skill_md.exists():
                    mtime = os.path.getmtime(str(skill_md))
                    parts.append(f"{entry.name}:{mtime}")
                # Include manifest.yaml mtime to bust cache on manifest changes
                manifest = entry / "manifest.yaml"
                if manifest.exists():
                    parts.append(f"{entry.name}:m:{os.path.getmtime(str(manifest))}")
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
            except Exception as exc:
                logger.debug("SkillGuard scan failed for %s: %s", name, exc)
                status = "unscanned"
            self._trust_cache[content_hash] = status
        except Exception as exc:
            logger.debug("Skill trust scan skipped for %s: %s", name, exc)

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

    def _read_tier(self, name: str) -> str:
        """Read tier for a skill: manifest.yaml > SKILL.md frontmatter > 'lazy'.

        Follows single source of truth: manifest.yaml is authoritative
        when present; SKILL.md frontmatter is fallback for simple skills.
        Delegates the precedence + clamping to the module-level
        :func:`resolve_tier` so ``parse_skill_md`` (skill_manager.py) and this
        method share ONE definition of "what tier is this skill".
        """
        skill_dir = self._skills_dir / f"s_{name}"

        manifest = ManifestLoader.load(skill_dir)
        manifest_tier = manifest.tier if manifest is not None else None
        manifest_present = manifest is not None

        # Frontmatter tier is only consulted when no manifest is present (matches
        # the original short-circuit: manifest, when present, is terminal).
        frontmatter_tier = None
        if not manifest_present:
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    frontmatter_tier = self._extract_tier_from_frontmatter(content)
                except Exception:
                    frontmatter_tier = None

        return resolve_tier(
            manifest_present=manifest_present,
            manifest_tier=manifest_tier,
            frontmatter_tier=frontmatter_tier,
        )

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

