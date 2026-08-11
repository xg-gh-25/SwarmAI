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

# The four canonical DDD documents, in priority order. This is the SINGLE
# SOURCE OF TRUTH — every loop that reads/indexes/checks-completeness of DDD
# docs (recall, cultivation, bindings, health, reports, metrics) MUST import
# this rather than hardcoding the tuple. A stray literal
# ("PRODUCT.md","TECH.md","IMPROVEMENT.md","PROJECT.md") elsewhere is a bug —
# guarded by tests/test_ddd_canonical_docs_single_source.py (grep gate).
# Rationale: run_393e3dc1 (code-intel v3 Run 0). 22 hardcoded copies across
# ~15 files were the GUI10/OT07 "add-a-doc → hunt-every-file" bug class.
DDD_CANONICAL_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")

# spec-details/ is a DERIVED PROJECTION directory (not a 5th canonical doc) —
# it is deliberately NOT part of DDD_CANONICAL_DOCS so the completeness gate
# (context_health_hook DDD-INCOMPLETE) never false-flags a project that has it.
# Wiring spec-details INTO recall/cultivation is Run 3/4, not this constant.
SPEC_DETAILS_DIR = "spec-details"

# Backward-compat alias (pre-Run-0 name). New code uses DDD_CANONICAL_DOCS.
_DDD_FILES = DDD_CANONICAL_DOCS

# Well-known project names — used as defaults/fallbacks only.
# The authoritative source is always filesystem discovery (list_projects()).
# On rename: just `mv` the directory. These constants are convenience aliases
# that auto-resolve on next import if the prefix pattern still matches.
SWARMAI = "SwarmAI"  # never renamed (protected)


# ─── Core Functions (must be defined before alias discovery) ─────────────────

def get_swarmws() -> Path:
    """Resolve SwarmWS root path. Respects SWARMWS env var override."""
    return Path(os.environ.get("SWARMWS", os.path.expanduser("~/.swarm-ai/SwarmWS")))


def get_projects_dir() -> Path:
    """Resolve the Projects/ directory."""
    return get_swarmws() / "Projects"


def get_project_dir(name: str) -> Path:
    """Resolve a specific project's root directory.

    Args:
        name: Project directory name (e.g., "MyProject")

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
        name: Project directory name (e.g., "MyProject")
        create: If True, create the directory if it doesn't exist.

    Returns:
        Path to the project's outputs/ directory.
    """
    output_dir = get_projects_dir() / name / "outputs"
    if create:
        output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _has_any_ddd_doc(d: Path) -> bool:
    """True if the project dir has at least one canonical DDD doc, in EITHER layout.

    Strangler-aware: the numbered-layout redesign (commit ad7f6623) moved the 4
    canonical docs under 2-understanding/, so a root-only `(d / f).exists()` probe
    silently STOPS DISCOVERING every migrated project (live-confirmed: list_projects
    returned only 1 of N projects — SwarmAI/AIDLC/SecDLC were all invisible). Route
    through the same resolver everything else uses. LAZY import: core.ddd_paths
    imports DDD_CANONICAL_DOCS from THIS module, so a top-level import would be
    circular — import inside the function. Off-host fallback (no SwarmAI core) checks
    2-understanding/ then root, matching ddd_path's on-host order.
    """
    try:  # ddd-six-section-fallback
        from core.ddd_paths import ddd_path
        return any(ddd_path(d, f).exists() for f in _DDD_FILES)
    except ImportError:
        return any(
            (d / "2-understanding" / f).exists() or (d / f).exists()
            for f in _DDD_FILES
        )


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
        # At least one DDD doc must exist (either layout — see _has_any_ddd_doc).
        if _has_any_ddd_doc(d):
            result.append(d)
    return result


def list_project_names() -> list[str]:
    """Return sorted list of project names (directory names only)."""
    return [p.name for p in list_projects()]


def project_exists(name: str) -> bool:
    """Check if a project exists by name."""
    return (get_projects_dir() / name).is_dir()


# ─── Pinned gallery / Welcome-Top-N order ────────────────────────────────────
# SwarmAI is ALWAYS first (the protected primary brain — the OS's own DDD). The
# rest is a mutable config list: the focus projects that get a pinned slot on the
# gallery top row + the Welcome Top-N. Existence-guarded — a name whose dir is
# absent (deleted, or a local-only project like CMHK on a public checkout) is
# silently dropped, never emitted as a broken pin (run_9ada46ae, Gate-1).
#
# FOLLOW-UP (recorded, not this run): make this user-configurable via a `pinned`
# tag in each .project.json instead of a code const — then changing focus projects
# needs no code edit. For now it's a one-line const in the SSOT registry (still
# beats a hardcoded frontend list: one place, backend-owned, test-covered).
_PINNED_AFTER_SWARMAI = ("AIDLC", "CMHK_SalesIntel")


def get_pinned_projects() -> list[str]:
    """Ordered pinned project names: SwarmAI first, then the configured focus
    projects — each filtered to those that actually exist on disk."""
    ordered = [SWARMAI, *_PINNED_AFTER_SWARMAI]
    return [n for n in ordered if project_exists(n)]


# ─── Auto-Discovered Aliases ─────────────────────────────────────────────────
# These resolve from filesystem at import time. On project rename, just `mv`
# the directory — next import auto-discovers the new name. Zero manual updates.

def _discover_by_prefix(prefix: str, fallback: str) -> str:
    """Discover a project by name prefix from filesystem. Returns first match."""
    projects_dir = get_projects_dir()
    if projects_dir.is_dir():
        for d in sorted(projects_dir.iterdir()):
            if d.is_dir() and d.name.startswith(prefix) and _has_any_ddd_doc(d):
                return d.name
    return fallback


# CMHK_SALESINTEL discovery removed — project is local-only (not in public repo)
BMS_BIZ: str = _discover_by_prefix("BMS_", "BMS_BIZ")
AIDLC: str = _discover_by_prefix("AIDLC", "AIDLC")
GITHUB_COMMUNITY: str = _discover_by_prefix("GitHub_", "GitHub_Community")
PHYSICAL_AI: str = _discover_by_prefix("Physical", "PhysicalAI")
QUICK_FOR_BIZ: str = _discover_by_prefix("Quick_", "Quick_For_Biz")
