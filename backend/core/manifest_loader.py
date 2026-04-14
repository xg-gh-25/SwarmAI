"""Skill manifest loader — reads manifest.yaml for skill package metadata.

Parses skill package descriptors, validates dependencies, and generates
script index strings. Used by skill_registry.py for tier classification.
Script index injection at invocation time is planned for Phase 4.

Key public symbols:

- ``ManifestLoader``  — Classmethod-based loader with module-level cache.
- ``SkillManifest``   — Pydantic model for the manifest.yaml schema.
- ``ScriptEntry``     — Individual script declaration within a manifest.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Lazy import yaml to avoid hard dependency at module level
_yaml = None


def _get_yaml():
    global _yaml
    if _yaml is None:
        import yaml
        _yaml = yaml
    return _yaml


class ScriptEntry(BaseModel):
    """A single script declared in a skill manifest."""

    path: str
    description: str
    entry: bool = False
    args: Optional[str] = None


class ResourceEntry(BaseModel):
    """A static resource declared in a skill manifest."""

    path: str
    description: str


class SkillManifest(BaseModel):
    """Pydantic model for manifest.yaml — the skill package descriptor."""

    name: str
    version: str = "1.0.0"
    tier: str = "lazy"
    scripts: list[ScriptEntry] = []
    resources: list[ResourceEntry] = []
    dependencies: dict = {}
    timeout: int = 120

    def get_entry_script(self) -> Optional[ScriptEntry]:
        """Return the primary entry point script, if declared.

        Falls back to the first script if none is marked ``entry: true``.
        """
        for s in self.scripts:
            if s.entry:
                return s
        return self.scripts[0] if self.scripts else None

    def generate_script_index(self, skill_dir: Path) -> str:
        """Generate human-readable script index for agent context injection."""
        if not self.scripts:
            return ""
        lines = ["**Available scripts:**"]
        for s in self.scripts:
            full_path = skill_dir / s.path
            marker = " [ENTRY]" if s.entry else ""
            status = "exists" if full_path.exists() else "MISSING"
            lines.append(f"- `{s.path}`{marker}: {s.description} ({status})")
            if s.args:
                lines.append(f"  Args: `{s.args}`")
        return "\n".join(lines)


# Module-level cache: str(skill_dir) → Optional[SkillManifest]
_manifest_cache: dict[str, Optional[SkillManifest]] = {}


class ManifestLoader:
    """Load and cache manifest.yaml from skill directories.

    Uses a module-level dict cache (same pattern as _session_recall_cache
    and _registry_cache) to avoid re-parsing on every prompt build.
    """

    @classmethod
    def load(cls, skill_dir: Path) -> Optional[SkillManifest]:
        """Load manifest.yaml from skill directory. Returns None if not found."""
        key = str(skill_dir)
        if key in _manifest_cache:
            return _manifest_cache[key]

        manifest_path = skill_dir / "manifest.yaml"
        if not manifest_path.exists():
            _manifest_cache[key] = None
            return None

        try:
            yaml = _get_yaml()
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("manifest.yaml for %s is not a dict", skill_dir.name)
                _manifest_cache[key] = None
                return None
            # Normalize None → empty list for optional list fields
            for field in ("scripts", "resources"):
                if field in data and data[field] is None:
                    data[field] = []
            # Normalize resources: accept strings, dicts, or ResourceEntry
            if "resources" in data and isinstance(data["resources"], list):
                normalized = []
                for r in data["resources"]:
                    if isinstance(r, ResourceEntry):
                        normalized.append(r)
                    elif isinstance(r, dict):
                        normalized.append(ResourceEntry(**r))
                    elif isinstance(r, str):
                        # Plain string → treat as path with auto description
                        normalized.append(ResourceEntry(path=r, description=r))
                    else:
                        continue  # Skip invalid entries
                data["resources"] = normalized
            manifest = SkillManifest(**data)
            _manifest_cache[key] = manifest
            return manifest
        except Exception as e:
            logger.warning("Failed to parse manifest for %s: %s", skill_dir.name, e)
            _manifest_cache[key] = None
            return None

    @classmethod
    def invalidate(cls, skill_dir: Optional[Path] = None) -> None:
        """Clear cache for a specific skill or all skills."""
        if skill_dir:
            _manifest_cache.pop(str(skill_dir), None)
        else:
            _manifest_cache.clear()
