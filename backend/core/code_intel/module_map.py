"""Module map -- cluster code nodes by directory prefix and measure cohesion.

Groups nodes into modules based on their 2-level directory prefix (e.g.
``backend/core/*.py`` -> ``backend/core``).  When a single prefix holds
>80% of nodes, falls back to package-based grouping using the nearest
parent directory.

Consumes ``GraphStore`` from ``graph_store.py``.  Key API surface used:

- ``get_module_map()`` -> dict[module_prefix, list[dict]]
- ``get_nodes_by_file(path)`` -> list[dict]
- ``find_callers(node_id, depth)`` -> list[tuple[caller_id, hop]]
- ``get_codebase_summary()`` -> dict  (for total counts)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)


@dataclass
class ModuleInfo:
    """Metrics for a single module."""
    name: str
    file_count: int = 0
    function_count: int = 0
    class_count: int = 0
    internal_edges: int = 0
    external_edges: int = 0

    @property
    def cohesion(self) -> float:
        """internal_edges / total_edges.  1.0 = fully self-contained."""
        total = self.internal_edges + self.external_edges
        return self.internal_edges / total if total > 0 else 1.0


@dataclass
class ModuleCrossing:
    """An edge in the change set that crosses a module boundary."""
    source_module: str
    target_module: str
    source_node: str
    target_node: str


@dataclass
class ModuleMapResult:
    modules: list[ModuleInfo] = field(default_factory=list)

    def to_minimal_context(self) -> str:
        names = [m.name for m in self.modules]
        return f"Modules ({len(names)}): {', '.join(names[:10])}"

    def to_full_context(self) -> str:
        lines = [self.to_minimal_context(), ""]
        for m in sorted(self.modules, key=lambda x: x.cohesion):
            lines.append(
                f"  {m.name:30s}  files={m.file_count}  funcs={m.function_count}  "
                f"classes={m.class_count}  cohesion={m.cohesion:.2f}  "
                f"internal={m.internal_edges}  external={m.external_edges}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module grouping helpers
# ---------------------------------------------------------------------------

def _group_nodes(nodes: list[dict], depth: int = 2) -> dict[str, list[dict]]:
    """Group a list of node dicts by their 2-level directory prefix.

    Falls back to package-based grouping if >80% share a single prefix.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        fp = node.get("file_path", "")
        prefix = _dir_prefix(fp, depth)
        groups[prefix].append(node)
    return dict(_regroup_if_flat(dict(groups)))


def _dir_prefix(file_path: str, depth: int = 2) -> str:
    """First *depth* path components, or all components except filename."""
    parts = Path(file_path).parts
    if len(parts) > depth:
        return "/".join(parts[:depth])
    return "/".join(parts[:-1]) or "root"


def _package_based_prefix(file_path: str) -> str:
    """Use the nearest parent directory as the module name (flat-repo fallback)."""
    parent = str(Path(file_path).parent)
    return parent if parent and parent != "." else "root"


def _regroup_if_flat(
    module_map: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """If >80% of nodes sit in a single module prefix, regroup by parent dir."""
    total = sum(len(v) for v in module_map.values())
    if total == 0:
        return module_map
    max_count = max(len(v) for v in module_map.values())
    if max_count / total <= 0.80:
        return module_map

    # Regroup by package (parent directory)
    regrouped: dict[str, list[dict]] = defaultdict(list)
    for nodes in module_map.values():
        for node in nodes:
            fp = node.get("file_path", "")
            mod = _package_based_prefix(fp)
            regrouped[mod].append(node)
    return dict(regrouped)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_module_map(graph_store: GraphStore) -> ModuleMapResult:
    """Build the module map from the full code graph.

    Uses ``graph_store.get_module_map()`` for the initial grouping, then
    applies flat-repo detection.  For each module, counts files, functions,
    classes, internal edges, and external edges.

    Cohesion = internal / (internal + external).
    """
    try:
        raw_map = graph_store.get_module_map()
    except Exception as exc:
        logger.warning("Failed to fetch module map: %s", exc)
        return ModuleMapResult()

    groups = _regroup_if_flat(raw_map)

    # Build lookup: node_id -> module_name
    node_to_module: dict[str, str] = {}
    for mod_name, nodes in groups.items():
        for node in nodes:
            node_to_module[node.get("id", "")] = mod_name

    modules: list[ModuleInfo] = []
    for mod_name, nodes in groups.items():
        files: set[str] = set()
        func_count = 0
        class_count = 0
        internal = 0
        external = 0

        for node in nodes:
            files.add(node.get("file_path", ""))
            ntype = node.get("node_type", "").lower()
            if ntype in ("function", "method"):
                func_count += 1
            elif ntype == "class":
                class_count += 1

            # Count caller edges
            node_id = node.get("id", "")
            try:
                callers = graph_store.find_callers(node_id, depth=1)
            except Exception:
                callers = []
            for caller_id, _hop in callers:
                caller_mod = node_to_module.get(caller_id, "")
                if caller_mod == mod_name:
                    internal += 1
                else:
                    external += 1

        modules.append(ModuleInfo(
            name=mod_name,
            file_count=len(files),
            function_count=func_count,
            class_count=class_count,
            internal_edges=internal,
            external_edges=external,
        ))

    return ModuleMapResult(modules=modules)


def detect_cross_module_changes(
    diff_files: list[str],
    graph_store: GraphStore,
) -> list[ModuleCrossing]:
    """Find edges in the changed files that cross module boundaries.

    For every node in each changed file, check its callers (via
    ``find_callers``).  If the caller lives in a different module, record a
    ``ModuleCrossing``.
    """
    # Build full module map for prefix resolution
    try:
        raw_map = graph_store.get_module_map()
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE. [] reads as "this change crosses no module boundary",
        # which is the answer that suppresses the warning the function exists to raise.
        logger.debug("module map unavailable, reporting no cross-module changes: %s",
                     exc)
        return []

    groups = _regroup_if_flat(raw_map)

    node_to_module: dict[str, str] = {}
    node_to_name: dict[str, str] = {}
    for mod_name, nodes in groups.items():
        for node in nodes:
            nid = node.get("id", "")
            node_to_module[nid] = mod_name
            node_to_name[nid] = node.get("name", nid)

    # Gather nodes from changed files
    changed_nodes: list[dict] = []
    diff_file_set = set(diff_files)
    for nodes in groups.values():
        for node in nodes:
            if node.get("file_path", "") in diff_file_set:
                changed_nodes.append(node)

    crossings: list[ModuleCrossing] = []
    seen: set[tuple[str, str]] = set()

    for node in changed_nodes:
        nid = node.get("id", "")
        n_mod = node_to_module.get(nid, "")

        # Check callers (upstream)
        try:
            callers = graph_store.find_callers(nid, depth=1)
        except Exception:
            callers = []

        for caller_id, _hop in callers:
            c_mod = node_to_module.get(caller_id, "")
            if c_mod and c_mod != n_mod:
                edge_key = (caller_id, nid)
                if edge_key not in seen:
                    seen.add(edge_key)
                    crossings.append(ModuleCrossing(
                        source_module=c_mod,
                        target_module=n_mod,
                        source_node=node_to_name.get(caller_id, caller_id),
                        target_node=node_to_name.get(nid, nid),
                    ))

    return crossings
