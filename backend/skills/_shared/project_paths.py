"""Project path resolution for skill scripts.

Single source of truth for project output directories.
All CMHK skill generators import from here instead of hardcoding paths.

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

# ─── Project Name Constants (change HERE on rename, nowhere else) ────────────
# NOTE: This module intentionally duplicates logic from core/project_registry.py.
# Reason: skill scripts run as standalone CLIs (python generator.py) without the
# backend on sys.path. core/project_registry.py serves the backend process.
# Both must be updated together on project rename — but that's 2 lines, not 20+ files.

CMHK_PROJECT = "CMHK_SalesIntel"
BMS_PROJECT = "BMS_BIZ"

# ─── Path Resolution ─────────────────────────────────────────────────────────


def get_swarmws() -> Path:
    """Resolve SwarmWS root. Respects SWARMWS env var."""
    return Path(os.environ.get("SWARMWS", os.path.expanduser("~/.swarm-ai/SwarmWS")))


def get_output_dir(project: str = CMHK_PROJECT, *, create: bool = True) -> Path:
    """Resolve the output directory for a project.

    Args:
        project: Project name (default: CMHK_SalesIntel).
        create: Create directory if missing.

    Returns:
        Path to project's outputs/ directory.
    """
    output_dir = get_swarmws() / "Projects" / project / "outputs"
    if create:
        output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
