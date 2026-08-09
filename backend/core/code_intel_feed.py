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

import json
import logging
import re
from pathlib import Path
from typing import Optional

from core.ddd_paths import ddd_path

logger = logging.getLogger(__name__)

# Minimum functions in a module to consider it "significant" enough for TECH.md
_MIN_FUNCTIONS_FOR_PROPOSAL = 5

# Confidence levels
_CONF_UNDOCUMENTED_MODULE = 0.8
_CONF_STALE_REFERENCE = 0.9
_CONF_NEW_ENTRY_POINT = 0.75


def _get_channel_threshold(channel: str, default: float) -> float:
    """Read adjusted confidence threshold from channel_stats.json (feedback loop).

    If the channel has low precision (< 40%), the threshold increases by 0.15
    per the ProposalFeedbackTracker rules. Falls back to default when stats
    file is absent or unreadable.
    """
    try:
        from core.proposal_feedback import ProposalFeedbackTracker, THRESHOLD_FLOOR
        import json as _json

        from config import get_app_data_dir
        stats_file = get_app_data_dir() / "SwarmWS" / "Projects" / "SwarmAI" / ".artifacts" / "channel_stats.json"
        if not stats_file.exists():
            return max(default, THRESHOLD_FLOOR)

        stats = _json.loads(stats_file.read_text(encoding="utf-8"))
        tracker = ProposalFeedbackTracker()
        return tracker.get_adjusted_threshold(channel, default, stats)
    except Exception:
        return default


def detect_tech_drift(workspace_path: str, project: str = "SwarmAI") -> int:
    """Compare code graph against TECH.md to detect architectural drift.

    Drift is a machine OBSERVATION, not a human-reviewable judgment — so (Knowledge
    Admission Component A, run_8d5fe9d1) it is emitted as a **health signal**, NOT as
    a CultivationProposal that would clog the human-review queue. Detects:
    1. Modules with >=5 functions not mentioned in TECH.md
    2. Symbols referenced in TECH.md that don't exist in the code graph

    Persists a drift health record (enumerable, pull-only) and returns the drift count
    (kept as the return contract for existing callers). NEVER calls write_proposal.
    """
    from core.code_intel import load_project_graph

    graph = load_project_graph(project)
    if graph is None:
        logger.debug("code_intel_feed: no graph for project %s", project)
        return 0

    root = Path(workspace_path)
    project_dir = root / "Projects" / project
    tech_md_path = ddd_path(project_dir, "TECH.md")

    if not tech_md_path.exists():
        return 0

    try:
        tech_content = tech_md_path.read_text(encoding="utf-8")
    except OSError:
        return 0

    proposals_count = 0
    drift_items: list[dict] = []  # health-signal records (NOT proposals)

    # Feedback loop: read adjusted threshold for this channel
    channel_threshold = _get_channel_threshold("code_intel_feed", 0.7)

    # 1. Undocumented modules (>=5 functions, not mentioned in TECH.md)
    module_map = graph.get_module_map()
    if not module_map:
        return 0
    tech_lower = tech_content.lower()

    for mod_path, nodes in module_map.items():
        if not nodes:
            continue
        # Count functions/methods in this module
        fn_count = sum(
            1 for n in nodes
            if n.get("node_type") in ("function", "method")
        )
        if fn_count < _MIN_FUNCTIONS_FOR_PROPOSAL:
            continue

        # H2 fix: check ALL path components (not just last) to avoid false negatives
        mod_parts = [p for p in mod_path.split("/") if p]
        if any(part.lower() in tech_lower for part in mod_parts):
            continue

        # Also check if any significant symbol names are mentioned
        # H1 fix: safe .get() access for "name" field
        top_symbols = [n.get("name", "") for n in nodes[:5] if n.get("name")]
        if any(sym.lower() in tech_lower for sym in top_symbols):
            continue

        # Generate proposal (H1 fix: safe access)
        # Feedback gate: skip if confidence below adjusted threshold
        if _CONF_UNDOCUMENTED_MODULE < channel_threshold:
            continue

        symbol_names = [n.get("name", "") for n in nodes if n.get("name")][:5]
        drift_items.append({
            "kind": "undocumented_module",
            "module": mod_path,
            "fn_count": fn_count,
            "key_symbols": symbol_names,
        })
        proposals_count += 1

    # 2. Stale references in TECH.md (symbols mentioned but not in graph)
    tech_symbols = _extract_backtick_symbols(tech_content)
    graph_symbols = _get_all_symbol_names(graph)

    # M5 fix: separate cap for stale refs (don't let module proposals steal the budget)
    stale_ref_count = 0
    for sym in tech_symbols:
        if sym not in graph_symbols and len(sym) > 3:
            # M6 fix: only flag if it looks like a code symbol (contains _ or camelCase)
            if not ("_" in sym or any(c.isupper() for c in sym[1:])):
                continue  # Skip common English words like "data", "test", "main"

            # Feedback gate: skip if confidence below adjusted threshold
            if _CONF_STALE_REFERENCE < channel_threshold:
                continue

            drift_items.append({
                "kind": "stale_reference",
                "symbol": sym,
            })
            proposals_count += 1
            stale_ref_count += 1

            # Cap stale ref proposals independently
            if stale_ref_count >= 5:
                break

    logger.info(
        "code_intel_feed: %d drift items (health signal, not proposals) for %s",
        proposals_count, project,
    )

    # Persist the drift as a HEALTH RECORD (pull-only: visible when a human looks at
    # the health report; never pushed to the Need-You review queue). Append-only,
    # overwritten each scan with the current drift set. Best-effort — a write failure
    # must not break the health deep-check that calls us.
    try:
        health_dir = project_dir / ".artifacts"
        health_dir.mkdir(parents=True, exist_ok=True)
        drift_record = {
            "generated_at_scan": True,
            "project": project,
            "drift_count": proposals_count,
            "items": drift_items[:50],  # bound the payload
        }
        (health_dir / "code_drift_health.json").write_text(
            json.dumps(drift_record, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.debug("code_intel_feed: drift health record write failed: %s", e)

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
    tech_md_path = ddd_path(root / "Projects" / project, "TECH.md")
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
        return None  # M7 fix: can't measure coverage without significant modules

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

    M8 fix: checks whether test files exist in the graph for each module,
    by looking for nodes with file_path containing "test_<module_name>".
    Does NOT check the module's own path — checks the test directory.

    Returns {module_path: has_tests} dict.
    Modules with tests can be promoted to "Growing" maturity faster.
    """
    from core.code_intel import load_project_graph

    graph = load_project_graph(project)
    if graph is None:
        return {}

    module_map = graph.get_module_map()
    if not module_map:
        return {}

    # Build a set of all file paths that contain "test" (test files in the graph)
    all_test_files: set[str] = set()
    for nodes in module_map.values():
        for n in nodes:
            fp = (n.get("file_path") or "").lower()
            if "test" in fp:
                all_test_files.add(fp)

    result: dict[str, bool] = {}

    for mod_path, nodes in module_map.items():
        # Skip test modules themselves
        if "test" in mod_path.lower():
            continue

        # M8 fix: check if a test file exists for this module's name
        mod_name = mod_path.split("/")[-1] if "/" in mod_path else mod_path
        has_test = any(
            f"test_{mod_name}" in tf or f"test_{mod_name.replace('.py', '')}" in tf
            for tf in all_test_files
        )
        result[mod_path] = has_test

    return result


def _extract_backtick_symbols(text: str) -> set[str]:
    """Extract symbols enclosed in backticks from markdown text.

    Filters to likely code symbols (no spaces, reasonable length).
    """
    pattern = r"`([^`\s]{3,50})`"
    matches = re.findall(pattern, text)
    # Filter: must look like a code symbol, not a filename or config entry
    symbols = set()
    for m in matches:
        # Skip file paths and URLs
        if "/" in m:
            continue
        if m.startswith("http") or m.startswith("--"):
            continue
        # Skip filenames (contain extension-like dots: .py, .md, .json, .toml, etc.)
        if re.search(r"\.\w{1,4}$", m) and "." in m:
            continue
        # Skip attribute access patterns (Foo.bar) — these aren't standalone symbols
        if "." in m and m[0].isupper():
            continue
        # Keep function-like names: word chars and underscores only
        if re.match(r"^[\w]+$", m):
            symbols.add(m)
    return symbols


def _get_all_symbol_names(graph) -> set[str]:
    """Get all symbol names from the code graph for existence checks."""
    module_map = graph.get_module_map()
    if not module_map:
        return set()
    names = set()
    for nodes in module_map.values():
        if not nodes:
            continue
        for n in nodes:
            name = n.get("name")
            if name:
                names.add(name)
    return names
