"""Project registry — single source of truth for project paths and discovery.

All project path resolution flows through this module. Skills, hooks, jobs,
and context builders import from here instead of hardcoding project names.

This eliminates the class of bugs where renaming a project requires editing
20+ files across 7 subsystems.

Key functions:
    list_projects()       — discover all DDD projects from filesystem
    get_output_dir(name)  — resolve output directory for a project (creates if missing)
    get_project_dir(name) — resolve project root directory
    get_swarmws()         — resolve SwarmWS root path
"""

from __future__ import annotations

import os
from pathlib import Path

# ─── Constants ───────────────────────────────────────────────────────────────

_DDD_FILES = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")

# Project name constants — single place to update on rename
CMHK_SALESINTEL = "CMHK_SalesIntel"
BMS_BIZ = "BMS_BIZ"
SWARMAI = "SwarmAI"
AIDLC = "AIDLC"
GITHUB_COMMUNITY = "GitHub_Community"
PHYSICAL_AI = "PhysicalAI"
QUICK_FOR_BIZ = "Quick_For_Biz"


# ─── Core Functions ──────────────────────────────────────────────────────────

def get_swarmws() -> Path:
    """Resolve SwarmWS root path. Respects SWARMWS env var override."""
    return Path(os.environ.get("SWARMWS", os.path.expanduser("~/.swarm-ai/SwarmWS")))


def get_projects_dir() -> Path:
    """Resolve the Projects/ directory."""
    return get_swarmws() / "Projects"


def get_project_dir(name: str) -> Path:
    """Resolve a specific project's root directory.

    Args:
        name: Project directory name (e.g., "CMHK_SalesIntel")

    Returns:
        Path to the project directory.

    Raises:
        FileNotFoundError: If the project directory does not exist.
    """
    project_dir = get_projects_dir() / name
    if not project_dir.is_dir():
        raise FileNotFoundError(
            f"Project '{name}' not found at {project_dir}. "
            f"Available: {[p.name for p in list_projects()]}"
        )
    return project_dir


def get_output_dir(name: str, *, create: bool = True) -> Path:
    """Resolve the output directory for a project.

    All skill generators should use this instead of hardcoding paths.

    Args:
        name: Project directory name (e.g., "CMHK_SalesIntel")
        create: If True, create the directory if it doesn't exist.

    Returns:
        Path to the project's outputs/ directory.
    """
    output_dir = get_projects_dir() / name / "outputs"
    if create:
        output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def list_projects() -> list[Path]:
    """Discover all DDD projects from filesystem.

    A valid project is any directory under Projects/ that contains
    at least one DDD file (PRODUCT.md, TECH.md, etc.) and doesn't
    start with a dot.

    Returns:
        Sorted list of project directory Paths.
    """
    projects_dir = get_projects_dir()
    if not projects_dir.is_dir():
        return []

    result = []
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        # At least one DDD file must exist
        if any((d / f).exists() for f in _DDD_FILES):
            result.append(d)
    return result


def list_project_names() -> list[str]:
    """Return sorted list of project names (directory names only)."""
    return [p.name for p in list_projects()]


def project_exists(name: str) -> bool:
    """Check if a project exists by name."""
    return (get_projects_dir() / name).is_dir()
