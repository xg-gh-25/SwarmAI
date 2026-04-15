"""Skill manifest loader — reads manifest.yaml for skill package metadata.

Parses skill package descriptors, validates and provisions dependencies,
and generates script index strings. Used by skill_registry.py for tier
classification. Dependency provisioning runs on first use per skill.

Key public symbols:

- ``ManifestLoader``  — Classmethod-based loader with module-level cache.
- ``SkillManifest``   — Pydantic model for the manifest.yaml schema.
- ``ScriptEntry``     — Individual script declaration within a manifest.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
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

        At most one script should be marked ``entry: true``. If multiple
        are marked, the first one wins and a warning is logged.
        Falls back to the first script if none is marked.
        """
        entry_scripts = [s for s in self.scripts if s.entry]
        if len(entry_scripts) > 1:
            logger.warning(
                "Manifest '%s' has %d scripts marked entry:true — using first (%s)",
                self.name, len(entry_scripts), entry_scripts[0].path,
            )
        if entry_scripts:
            return entry_scripts[0]
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
    def ensure_dependencies(cls, manifest: SkillManifest) -> list[str]:
        """Check and install missing npm dependencies declared in manifest.

        Python deps are handled via pyproject.toml at build time.
        npm deps need runtime provisioning since they're global installs.

        Returns list of newly installed package names. Empty if all present.
        """
        npm_deps = manifest.dependencies.get("npm", [])
        if not npm_deps:
            return []

        cache_key = f"_deps_checked_{manifest.name}"
        if cache_key in _manifest_cache:
            return []

        npm_bin = shutil.which("npm")
        if not npm_bin:
            logger.warning("npm not found — cannot provision deps for %s", manifest.name)
            _manifest_cache[cache_key] = True
            return []

        # Check which packages are missing
        missing = []
        try:
            result = subprocess.run(
                [npm_bin, "list", "-g", "--depth=0", "--json"],
                capture_output=True, text=True, timeout=15,
            )
            installed = set()
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                installed = set(data.get("dependencies", {}).keys())
            missing = [pkg for pkg in npm_deps if pkg not in installed]
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning("Failed to check npm deps for %s: %s", manifest.name, e)
            missing = npm_deps  # Assume all missing, try install

        if not missing:
            _manifest_cache[cache_key] = True
            return []

        # Install missing packages
        logger.info("Installing npm deps for skill %s: %s", manifest.name, missing)
        installed = []
        try:
            result = subprocess.run(
                [npm_bin, "install", "-g", *missing],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                installed = missing
                logger.info("Installed npm deps for %s: %s", manifest.name, missing)
            else:
                logger.warning(
                    "npm install failed for %s (exit %d): %s",
                    manifest.name, result.returncode, result.stderr[:200],
                )
        except subprocess.TimeoutExpired:
            logger.warning("npm install timed out for skill %s", manifest.name)
        except Exception as e:
            logger.warning("npm install error for %s: %s", manifest.name, e)

        _manifest_cache[cache_key] = True
        return installed

    @classmethod
    def invalidate(cls, skill_dir: Optional[Path] = None) -> None:
        """Clear cache for a specific skill or all skills."""
        if skill_dir:
            _manifest_cache.pop(str(skill_dir), None)
        else:
            _manifest_cache.clear()
