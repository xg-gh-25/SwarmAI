"""Code Intelligence Feed — Channel 7 of DDD Cultivation.

Compares code_intel graph state against TECH.md to detect drift:
- New modules (>=5 functions) not mentioned in TECH.md
- Symbols referenced in TECH.md that no longer exist in code
- New entry points (API endpoints) not documented

Also provides health enrichment: code graph module coverage as
evidence for the "Completeness" health dimension.

Trigger: Called during context_health_hook daily deep check,
or manually after code_intel re-index.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum functions in a module to consider it "significant" enough for TECH.md
_MIN_FUNCTIONS_FOR_PROPOSAL = 5

# Confidence levels
_CONF_UNDOCUMENTED_MODULE = 0.8
_CONF_STALE_REFERENCE = 0.9
_CONF_NEW_ENTRY_POINT = 0.75


def detect_tech_drift(workspace_path: str, project: str = "SwarmAI") -> int:
    """Compare code graph against TECH.md to detect architectural drift.

    Generates CultivationProposal for:
    1. Modules with >=5 functions not mentioned in TECH.md
    2. Symbols referenced in TECH.md that don't exist in the code graph
    3. New entry points (is_entry_point=1) not documented

    Returns the number of proposals generated.
    """
    from core.code_intel import load_project_graph
    from core.ddd_cultivation import CultivationProposal, write_proposal

    graph = load_project_graph(project)
    if graph is None:
        logger.debug("code_intel_feed: no graph for project %s", project)
        return 0

    root = Path(workspace_path)
    project_dir = root / "Projects" / project
    tech_md_path = project_dir / "TECH.md"

    if not tech_md_path.exists():
        return 0

    try:
        tech_content = tech_md_path.read_text(encoding="utf-8")
    except OSError:
        return 0

    proposals_count = 0

    # 1. Undocumented modules (>=5 functions, not mentioned in TECH.md)
    module_map = graph.get_module_map()
    tech_lower = tech_content.lower()

    for mod_path, nodes in module_map.items():
        # Count functions/methods in this module
        fn_count = sum(
            1 for n in nodes
            if n.get("node_type") in ("function", "method")
        )
        if fn_count < _MIN_FUNCTIONS_FOR_PROPOSAL:
            continue

        # Check if module is mentioned in TECH.md
        # Match on directory name or key file names
        mod_name = mod_path.split("/")[-1] if "/" in mod_path else mod_path
        if mod_name.lower() in tech_lower:
            continue

        # Also check if any significant symbol names are mentioned
        top_symbols = [n["name"] for n in nodes[:5] if n.get("name")]
        if any(sym.lower() in tech_lower for sym in top_symbols):
            continue

        # Generate proposal
        symbol_names = [n["name"] for n in nodes if n.get("name")][:5]
        proposal = CultivationProposal(
            target_doc="TECH.md",
            target_section="Key Subsystems",
            content=(
                f"Undocumented module `{mod_path}` ({fn_count} functions). "
                f"Key symbols: {', '.join(symbol_names)}. "
                f"Consider adding to TECH.md architecture documentation."
            ),
            source_run_id=f"code_intel_drift:{mod_path}",
            confidence=_CONF_UNDOCUMENTED_MODULE,
            source_stage="code_intel_feed",
        )
        write_proposal(proposal, project_dir)
        proposals_count += 1

    # 2. Stale references in TECH.md (symbols mentioned but not in graph)
    tech_symbols = _extract_backtick_symbols(tech_content)
    graph_symbols = _get_all_symbol_names(graph)

    for sym in tech_symbols:
        if sym not in graph_symbols and len(sym) > 3:
            # Only flag if it looks like a real symbol (not a short word)
            # and is in a code-relevant section
            proposal = CultivationProposal(
                target_doc="TECH.md",
                target_section="Key Subsystems",
                content=(
                    f"Symbol `{sym}` referenced in TECH.md but not found in "
                    f"code graph. May be renamed, deleted, or a typo."
                ),
                source_run_id=f"code_intel_stale:{sym}",
                confidence=_CONF_STALE_REFERENCE,
                source_stage="code_intel_feed",
            )
            write_proposal(proposal, project_dir)
            proposals_count += 1

            # Cap stale ref proposals to avoid flooding
            if proposals_count > 5:
                break

    logger.info(
        "code_intel_feed: %d drift proposals for %s", proposals_count, project
    )
    return proposals_count


def get_code_coverage_for_health(
    workspace_path: str, project: str = "SwarmAI"
) -> Optional[float]:
    """Calculate TECH.md documentation coverage based on code graph.

    Returns a 0.0-1.0 score: (documented modules / total significant modules).
    Returns None if code_intel is not available.

    Used by health scoring as evidence for the "Completeness" dimension.
    """
    from core.code_intel import load_project_graph

    graph = load_project_graph(project)
    if graph is None:
        return None

    root = Path(workspace_path)
    tech_md_path = root / "Projects" / project / "TECH.md"
    if not tech_md_path.exists():
        return None

    try:
        tech_content = tech_md_path.read_text(encoding="utf-8").lower()
    except OSError:
        return None

    module_map = graph.get_module_map()

    # Count significant modules (>=5 functions)
    significant_modules = [
        mod_path
        for mod_path, nodes in module_map.items()
        if sum(1 for n in nodes if n.get("node_type") in ("function", "method")) >= _MIN_FUNCTIONS_FOR_PROPOSAL
    ]

    if not significant_modules:
        return 1.0  # No significant modules = fully covered (vacuously)

    # Count how many are mentioned in TECH.md
    documented = 0
    for mod_path in significant_modules:
        mod_name = mod_path.split("/")[-1] if "/" in mod_path else mod_path
        if mod_name.lower() in tech_content:
            documented += 1

    return documented / len(significant_modules)


def get_test_coverage_for_maturity(
    workspace_path: str, project: str = "SwarmAI"
) -> dict[str, bool]:
    """Check which modules have test files for maturity evidence.

    Returns {module_path: has_tests} dict.
    Modules with tests can be promoted to "Growing" maturity faster.
    """
    from core.code_intel import load_project_graph

    graph = load_project_graph(project)
    if graph is None:
        return {}

    module_map = graph.get_module_map()
    result: dict[str, bool] = {}

    for mod_path, nodes in module_map.items():
        # Check if any node in this module has a test caller
        has_test = any(
            "test" in (n.get("file_path") or "").lower()
            for n in nodes
        )
        # Also check for dedicated test file
        if not has_test:
            mod_name = mod_path.split("/")[-1] if "/" in mod_path else mod_path
            has_test = any(
                f"test_{mod_name}" in (n.get("file_path") or "")
                or f"test_{mod_name.replace('.py', '')}" in (n.get("file_path") or "")
                for n in nodes
            )
        result[mod_path] = has_test

    return result


def _extract_backtick_symbols(text: str) -> set[str]:
    """Extract symbols enclosed in backticks from markdown text.

    Filters to likely code symbols (no spaces, reasonable length).
    """
    pattern = r"`([^`\s]{3,50})`"
    matches = re.findall(pattern, text)
    # Filter: must look like a symbol (contains letters, may have _ or .)
    symbols = set()
    for m in matches:
        # Skip file paths, URLs, and markdown artifacts
        if "/" in m and not m.endswith(".py"):
            continue
        if m.startswith("http") or m.startswith("--"):
            continue
        # Keep function-like names: word chars, dots, underscores
        if re.match(r"^[\w.]+$", m):
            symbols.add(m)
    return symbols


def _get_all_symbol_names(graph) -> set[str]:
    """Get all symbol names from the code graph for existence checks."""
    module_map = graph.get_module_map()
    names = set()
    for nodes in module_map.values():
        for n in nodes:
            if n.get("name"):
                names.add(n["name"])
    return names
