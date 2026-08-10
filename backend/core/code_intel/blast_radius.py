"""Blast radius analysis -- maps code changes to affected downstream/upstream symbols.

Consumes ``GraphStore`` from ``graph_store.py``.  Key API surface used:

- ``get_nodes_by_file(path)`` -> list[dict] with id, name, line_start, line_end
- ``blast_radius(node_ids, max_depth)`` -> list[tuple[node_id, depth]]
- ``find_callers(node_id, depth)`` -> list[tuple[caller_id, hop]]
- ``count_callers_by_file(path)`` -> dict[node_id, count]
"""

from __future__ import annotations

import re
import subprocess
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)


@dataclass
class ImpactNode:
    """A code symbol affected by a change."""
    id: str
    file_path: str
    name: str
    depth: int
    has_test: bool
    module: str


@dataclass
class BlastRadiusResult:
    """Aggregate result from blast radius analysis."""
    changed_nodes: list[str] = field(default_factory=list)
    affected_callers: list[ImpactNode] = field(default_factory=list)
    modules_crossed: set[str] = field(default_factory=set)
    untested_callers: list[str] = field(default_factory=list)
    total_affected: int = 0
    risk_level: str = "LOW"

    def to_minimal_context(self) -> str:
        """~50 tokens: one-line risk summary."""
        return (
            f"Risk: {self.risk_level} | {len(self.changed_nodes)} changed | "
            f"{self.total_affected} affected | {len(self.untested_callers)} untested | "
            f"Modules: {', '.join(sorted(self.modules_crossed)) or 'single'}"
        )

    def to_full_context(self) -> str:
        """~500 tokens: detailed findings."""
        lines = [self.to_minimal_context(), ""]
        lines.append("Changed symbols:")
        for n in self.changed_nodes[:10]:
            lines.append(f"  - {n}")
        if self.untested_callers:
            lines.append(f"\nUntested callers ({len(self.untested_callers)}):")
            for c in self.untested_callers[:5]:
                lines.append(f"  WARNING {c}")
        if self.affected_callers:
            lines.append(f"\nAffected callers ({len(self.affected_callers)}):")
            for a in sorted(self.affected_callers, key=lambda x: x.depth)[:10]:
                indent = "  " * a.depth
                lines.append(f"  {indent}{a.name} ({a.file_path}, depth={a.depth})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

_DIFF_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _parse_diff_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse unified diff output into {file_path: [(start, end), ...]}."""
    result: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None

    for line in diff_text.splitlines():
        file_match = _DIFF_FILE_RE.match(line)
        if file_match:
            current_file = file_match.group(2)
            if current_file not in result:
                result[current_file] = []
            continue

        hunk_match = _HUNK_RE.match(line)
        if hunk_match and current_file is not None:
            start = int(hunk_match.group(1))
            count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            end = start + max(count - 1, 0)
            result[current_file].append((start, end))

    return result


def _get_module(file_path: str, depth: int = 2) -> str:
    """Extract module name from first N path components.

    Example: 'backend/core/auth.py' with depth=2 -> 'backend/core'
    """
    parts = Path(file_path).parts
    return "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "root"


def _node_has_test_caller(node_id: str, graph_store: GraphStore) -> bool:
    """Check whether any caller of *node_id* is a test function.

    Uses ``find_callers`` which returns ``(caller_id, hop)`` tuples.
    A caller is considered a test if its id contains ``test``.
    """
    try:
        callers = graph_store.find_callers(node_id, depth=1)
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE. False reads as "no test covers this node" — a claim about
        # coverage that a failed lookup has not actually established.
        logger.debug("test-caller lookup failed for %s: %s", node_id, exc)
        return False
    return any("test" in caller_id.lower() for caller_id, _hop in callers)


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------

def _classify_risk(total_affected: int, untested_count: int, modules_crossed: int) -> str:
    """Assign risk level based on thresholds."""
    if total_affected > 20 or untested_count > 5:
        return "CRITICAL"
    if total_affected > 10 or untested_count > 2:
        return "HIGH"
    if total_affected > 3 or modules_crossed > 2:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Node resolution helper
# ---------------------------------------------------------------------------

_QUALIFIED_SEP = "::"  # must match parser.QUALIFIED_SEPARATOR


def _resolve_node(node_id: str, graph_store: GraphStore) -> dict | None:
    """Look up a node's metadata by scanning its file.

    ``GraphStore`` exposes ``get_nodes_by_file`` but not ``get_node_by_id``.
    We extract the file_path from the node_id (format: ``path::Name``)
    and search within that file.
    """
    if _QUALIFIED_SEP in node_id:
        file_path = node_id.split(_QUALIFIED_SEP, 1)[0]
    elif ":" in node_id:
        # Fallback for non-standard separators
        file_path = node_id.rsplit(":", 1)[0].rstrip(":")
    else:
        return None

    try:
        nodes = graph_store.get_nodes_by_file(file_path)
        for n in nodes:
            if n["id"] == node_id:
                return n
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_diff(
    graph_store: GraphStore,
    repo_root: Path,
    base_ref: str = "HEAD~1",
    end_ref: str | None = None,
) -> BlastRadiusResult:
    """Run blast radius analysis for changes between *base_ref* and *end_ref*.

    Parameters
    ----------
    base_ref : str
        Starting point for diff (e.g. "HEAD~1" or a commit SHA).
    end_ref : str | None
        Ending point. ``None`` diffs against the **working tree** (not HEAD).
        This includes uncommitted changes, which is the intended behavior
        for pre-commit review. Pass "HEAD" explicitly to diff committed-only.

    Steps:
    1. ``git diff base_ref [end_ref] --unified=0`` to get changed lines per file.
    2. Map changed lines to code_nodes (line range overlap).
    3. ``graph_store.blast_radius(changed_nodes, max_depth=2)`` for bidirectional
       transitive impact.
    4. Annotate each impacted node with test coverage and module.
    5. Determine risk_level from thresholds.
    """
    result = BlastRadiusResult()

    # 1. Get diff output
    diff_cmd = ["git", "diff", base_ref]
    if end_ref:
        diff_cmd.append(end_ref)
    diff_cmd.append("--unified=0")
    try:
        proc = subprocess.run(
            diff_cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            logger.warning("git diff failed: %s", proc.stderr.strip())
            return result
        diff_text = proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("git diff error: %s", exc)
        return result

    if not diff_text.strip():
        return result

    file_ranges = _parse_diff_line_ranges(diff_text)

    # 2. Map changed lines to graph nodes
    changed_node_ids: list[str] = []
    # Cache file nodes for later resolution
    file_nodes_cache: dict[str, list[dict]] = {}
    for file_path, ranges in file_ranges.items():
        try:
            nodes = graph_store.get_nodes_by_file(file_path)
        except Exception:
            continue
        file_nodes_cache[file_path] = nodes
        for node in nodes:
            node_start = node.get("line_start", 0)
            node_end = node.get("line_end", node_start)
            for diff_start, diff_end in ranges:
                if node_start <= diff_end and diff_start <= node_end:
                    changed_node_ids.append(node["id"])
                    break

    result.changed_nodes = changed_node_ids
    if not changed_node_ids:
        return result

    # 3. Bidirectional traversal via graph_store.blast_radius()
    # Returns list[tuple[node_id, depth]]
    try:
        affected_tuples = graph_store.blast_radius(changed_node_ids, max_depth=2)
    except Exception as exc:
        logger.warning("blast_radius query failed: %s", exc)
        affected_tuples = []

    # Build a lookup of all cached nodes by id for fast resolution
    id_to_node: dict[str, dict] = {}
    for nodes in file_nodes_cache.values():
        for n in nodes:
            id_to_node[n["id"]] = n

    # 4. Build ImpactNode list with annotations
    #    Skip unresolved bare names (no "::" = builtins/stdlib/unresolved)
    seen_ids: set[str] = set(changed_node_ids)
    for node_id, depth in affected_tuples:
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)

        # Filter: bare names without qualified separator are unresolved
        # (e.g. "Lock", "RuntimeError", "ValueError") — not real nodes
        if _QUALIFIED_SEP not in node_id and ":" not in node_id:
            continue

        # Resolve node metadata -- try cache first, then graph lookup
        node_info = id_to_node.get(node_id) or _resolve_node(node_id, graph_store)
        fp = node_info.get("file_path", "") if node_info else ""
        name = node_info.get("name", node_id) if node_info else node_id
        mod = _get_module(fp) if fp else "unknown"

        has_test = _node_has_test_caller(node_id, graph_store)

        impact = ImpactNode(
            id=node_id,
            file_path=fp,
            name=name,
            depth=depth,
            has_test=has_test,
            module=mod,
        )
        result.affected_callers.append(impact)
        result.modules_crossed.add(mod)

        if not has_test:
            result.untested_callers.append(impact.name)

    # Add modules from changed nodes themselves
    for file_path in file_ranges:
        result.modules_crossed.add(_get_module(file_path))

    result.total_affected = len(result.affected_callers)

    # 5. Risk classification
    result.risk_level = _classify_risk(
        result.total_affected,
        len(result.untested_callers),
        len(result.modules_crossed),
    )
    return result
