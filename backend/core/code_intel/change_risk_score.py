"""Six-dimensional change risk scoring.

Each dimension scores 0.0-1.0, final score = weighted sum.
Buckets: <0.3 LOW, 0.3-0.6 MEDIUM, 0.6-0.8 HIGH, >0.8 CRITICAL.

Consumes ``GraphStore`` from ``graph_store.py``.  Key API surface used:

- ``get_nodes_by_file(path)`` -> list[dict]
- ``find_callers(node_id, depth)`` -> list[tuple[caller_id, hop]]
- ``count_callers_by_file(path)`` -> dict[node_id, count]
"""

from __future__ import annotations

import subprocess
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)


RISK_WEIGHTS: dict[str, float] = {
    "module_spread":    0.20,   # files span N distinct modules
    "test_gap":         0.25,   # changed functions without test coverage
    "caller_count":     0.15,   # sum of callers across changed nodes
    "security_surface": 0.15,   # auth/crypto/input/sql/exec keywords
    "file_churn":       0.10,   # git log --follow frequency (30d)
    "module_crossing":  0.15,   # edges crossing module boundaries
}

SECURITY_KEYWORDS: set[str] = {
    "auth", "password", "secret", "token", "key", "crypto",
    "hash", "sql", "query", "exec", "eval", "input", "sanitize",
    "escape", "inject", "csrf", "xss", "cors",
}


@dataclass
class DimensionScore:
    """Score for a single risk dimension."""
    name: str
    raw: float          # 0.0-1.0
    weighted: float     # raw * weight
    detail: str = ""    # human explanation


@dataclass
class RiskScoreResult:
    """Aggregate result of risk scoring."""
    total_score: float = 0.0
    risk_level: str = "LOW"
    dimensions: list[DimensionScore] = field(default_factory=list)

    def to_minimal_context(self) -> str:
        return f"Risk: {self.risk_level} ({self.total_score:.2f})"

    def to_full_context(self) -> str:
        lines = [self.to_minimal_context(), ""]
        for d in self.dimensions:
            bar = "#" * int(d.raw * 10)
            lines.append(f"  {d.name:20s} {d.raw:.2f} [{bar:<10s}] {d.detail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_of(file_path: str, depth: int = 2) -> str:
    parts = Path(file_path).parts
    return "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "root"


_QSEP = "::"  # must match parser.QUALIFIED_SEPARATOR


def _extract_file_path(node_id: str) -> str:
    """Extract file_path from a qualified node_id ('path.py::Name' → 'path.py')."""
    if _QSEP in node_id:
        return node_id.split(_QSEP, 1)[0]
    if ":" in node_id:
        return node_id.rsplit(":", 1)[0].rstrip(":")
    return ""


def _resolve_node_name(node_id: str, graph_store: GraphStore) -> str:
    """Best-effort name extraction.  Falls back to the id itself."""
    file_path = _extract_file_path(node_id)
    if file_path:
        try:
            for n in graph_store.get_nodes_by_file(file_path):
                if n["id"] == node_id:
                    return n.get("name", node_id)
        except Exception:
            pass
    return node_id


def _resolve_node_file(node_id: str, graph_store: GraphStore) -> str:
    """Best-effort file path extraction."""
    file_path = _extract_file_path(node_id)
    if file_path:
        try:
            for n in graph_store.get_nodes_by_file(file_path):
                if n["id"] == node_id:
                    return n.get("file_path", "")
        except Exception:
            pass
    return ""


# ---------------------------------------------------------------------------
# Individual dimension scorers
# ---------------------------------------------------------------------------

def _score_module_spread(changed_files: list[str], depth: int = 2) -> DimensionScore:
    """How many distinct modules the changeset touches."""
    modules: set[str] = set()
    for fp in changed_files:
        modules.add(_module_of(fp, depth))
    count = len(modules)
    # Normalize: 1 module = 0.0, 5+ = 1.0
    raw = min(max((count - 1) / 4.0, 0.0), 1.0)
    return DimensionScore(
        name="module_spread",
        raw=raw,
        weighted=raw * RISK_WEIGHTS["module_spread"],
        detail=f"{count} modules",
    )


def _score_test_gap(changed_node_ids: list[str], graph_store: GraphStore) -> DimensionScore:
    """Fraction of changed functions that lack test callers."""
    if not changed_node_ids:
        return DimensionScore(name="test_gap", raw=0.0, weighted=0.0, detail="no nodes")
    untested = 0
    for nid in changed_node_ids:
        try:
            callers = graph_store.find_callers(nid, depth=1)
        except Exception:
            callers = []
        has_test = any("test" in cid.lower() for cid, _hop in callers)
        if not has_test:
            untested += 1
    raw = untested / len(changed_node_ids)
    return DimensionScore(
        name="test_gap",
        raw=raw,
        weighted=raw * RISK_WEIGHTS["test_gap"],
        detail=f"{untested}/{len(changed_node_ids)} untested",
    )


def _score_caller_count(changed_node_ids: list[str], graph_store: GraphStore) -> DimensionScore:
    """Sum of callers across all changed nodes.  10+ callers -> 1.0."""
    total = 0
    for nid in changed_node_ids:
        try:
            callers = graph_store.find_callers(nid, depth=1)
            total += len(callers)
        except Exception:
            pass
    raw = min(total / 10.0, 1.0)
    return DimensionScore(
        name="caller_count",
        raw=raw,
        weighted=raw * RISK_WEIGHTS["caller_count"],
        detail=f"{total} callers total",
    )


def _score_security_surface(changed_files: list[str], changed_node_ids: list[str],
                            graph_store: GraphStore) -> DimensionScore:
    """Presence of security-sensitive keywords in changed file paths and node names."""
    hits: set[str] = set()
    for fp in changed_files:
        lower = fp.lower()
        for kw in SECURITY_KEYWORDS:
            if kw in lower:
                hits.add(kw)
    for nid in changed_node_ids:
        name_lower = _resolve_node_name(nid, graph_store).lower()
        for kw in SECURITY_KEYWORDS:
            if kw in name_lower:
                hits.add(kw)
    # 1 keyword = 0.3, 3+ = 1.0
    raw = min(len(hits) / 3.0, 1.0) if hits else 0.0
    return DimensionScore(
        name="security_surface",
        raw=raw,
        weighted=raw * RISK_WEIGHTS["security_surface"],
        detail=f"keywords: {', '.join(sorted(hits)) or 'none'}",
    )


def _score_file_churn(changed_files: list[str], repo_root: Path) -> DimensionScore:
    """Average 30-day commit frequency across changed files. 10+ commits -> 1.0."""
    if not changed_files:
        return DimensionScore(name="file_churn", raw=0.0, weighted=0.0, detail="no files")
    total_commits = 0
    counted = 0
    for fp in changed_files:
        try:
            proc = subprocess.run(
                ["git", "log", "--oneline", "--since=30.days", "--follow", "--", fp],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                commits = len([l for l in proc.stdout.strip().splitlines() if l])
                total_commits += commits
                counted += 1
        except Exception:
            continue
    avg = (total_commits / counted) if counted else 0.0
    raw = min(avg / 10.0, 1.0)
    return DimensionScore(
        name="file_churn",
        raw=raw,
        weighted=raw * RISK_WEIGHTS["file_churn"],
        detail=f"avg {avg:.1f} commits/30d",
    )


def _score_module_crossing(changed_node_ids: list[str], graph_store: GraphStore,
                           depth: int = 2) -> DimensionScore:
    """Fraction of edges from changed nodes that cross module boundaries."""
    total_edges = 0
    crossing = 0
    for nid in changed_node_ids:
        node_file = _resolve_node_file(nid, graph_store)
        node_mod = _module_of(node_file, depth) if node_file else "unknown"
        try:
            callers = graph_store.find_callers(nid, depth=1)
        except Exception:
            continue
        for caller_id, _hop in callers:
            total_edges += 1
            caller_file = _resolve_node_file(caller_id, graph_store)
            caller_mod = _module_of(caller_file, depth) if caller_file else "unknown"
            if caller_mod != node_mod:
                crossing += 1
    raw = (crossing / total_edges) if total_edges else 0.0
    return DimensionScore(
        name="module_crossing",
        raw=raw,
        weighted=raw * RISK_WEIGHTS["module_crossing"],
        detail=f"{crossing}/{total_edges} cross-module edges",
    )


# ---------------------------------------------------------------------------
# Risk bucket
# ---------------------------------------------------------------------------

def _bucket(score: float) -> str:
    if score > 0.8:
        return "CRITICAL"
    if score > 0.6:
        return "HIGH"
    if score > 0.3:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_change(
    graph_store: GraphStore,
    repo_root: Path,
    changed_files: list[str],
    changed_node_ids: list[str],
) -> RiskScoreResult:
    """Compute the 6-dimensional risk score for a changeset.

    Parameters
    ----------
    graph_store : GraphStore
        The code graph backing store.
    repo_root : Path
        Repository root for git commands.
    changed_files : list[str]
        Relative file paths that were changed.
    changed_node_ids : list[str]
        IDs of code_nodes that overlap with changed lines.
    """
    dims = [
        _score_module_spread(changed_files),
        _score_test_gap(changed_node_ids, graph_store),
        _score_caller_count(changed_node_ids, graph_store),
        _score_security_surface(changed_files, changed_node_ids, graph_store),
        _score_file_churn(changed_files, repo_root),
        _score_module_crossing(changed_node_ids, graph_store),
    ]
    total = sum(d.weighted for d in dims)
    return RiskScoreResult(
        total_score=total,
        risk_level=_bucket(total),
        dimensions=dims,
    )
