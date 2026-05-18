"""Project path resolution for skill scripts.

Single source of truth for project output directories.
All CMHK skill generators import from here instead of hardcoding paths.

Zero-constant design: project names are discovered from filesystem, not hardcoded.
On rename, only `mv` the directory — this module auto-discovers the new name.

Usage (from any skill generator.py):
    import sys
    from pathlib import Path
    _SHARED = str(Path(__file__).resolve().parents[2] / "_shared")
    if _SHARED not in sys.path:
        sys.path.insert(0, _SHARED)
    from project_paths import get_output_dir, CMHK_PROJECT

Then replace:
    output_dir = os.path.join(swarmws, "Projects", "CMHK_SalesIntel", "outputs")
With:
    output_dir = str(get_output_dir())
"""

from __future__ import annotations

import os
from pathlib import Path

# ─── Path Resolution ─────────────────────────────────────────────────────────


def get_swarmws() -> Path:
    """Resolve SwarmWS root. Respects SWARMWS env var."""
    return Path(os.environ.get("SWARMWS", os.path.expanduser("~/.swarm-ai/SwarmWS")))


def _discover_cmhk_project() -> str:
    """Auto-discover the CMHK sales intelligence project by prefix match.

    Scans Projects/ for any directory starting with 'CMHK_' that has DDD docs.
    This eliminates the need for a hardcoded constant — rename the dir and
    this function finds it automatically.

    Falls back to "CMHK_SalesIntel" if discovery fails (e.g., missing dir).
    """
    projects_dir = get_swarmws() / "Projects"
    if not projects_dir.is_dir():
        return "CMHK_SalesIntel"

    for d in sorted(projects_dir.iterdir()):
        if d.is_dir() and d.name.startswith("CMHK_") and (d / "PRODUCT.md").exists():
            return d.name

    return "CMHK_SalesIntel"  # fallback


# Discovered at import time (cached for session lifetime)
CMHK_PROJECT: str = _discover_cmhk_project()
BMS_PROJECT: str = "BMS_BIZ"


def get_output_dir(project: str = CMHK_PROJECT, *, create: bool = True) -> Path:
    """Resolve the output directory for a project.

    Args:
        project: Project name (default: auto-discovered CMHK project).
        create: Create directory if missing.

    Returns:
        Path to project's outputs/ directory.
    """
    output_dir = get_swarmws() / "Projects" / project / "outputs"
    if create:
        output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
