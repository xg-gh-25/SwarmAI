"""Dead code detection -- find exported symbols with zero incoming edges.

Identifies code that is no longer called from anywhere, excluding known
entry points per language.  Results are sorted by last commit date (oldest
dead code first) so the most stale symbols surface at the top.

Consumes ``GraphStore`` from ``graph_store.py``.  Key API surface used:

- ``find_dead_code()`` -> list[dict] with id, file_path, node_type, name
- ``get_nodes_by_file(path)`` -> list[dict] for richer metadata
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


@dataclass
class DeadSymbol:
    """A code symbol with zero callers that is not an entry point."""
    id: str
    name: str
    file_path: str
    kind: str            # "function", "class", "method"
    language: str
    last_commit_ts: int  # unix timestamp; 0 = unknown


@dataclass
class DeadCodeResult:
    symbols: list[DeadSymbol] = field(default_factory=list)
    total_scanned: int = 0

    def to_minimal_context(self) -> str:
        return f"Dead code: {len(self.symbols)} symbols (scanned {self.total_scanned})"

    def to_full_context(self) -> str:
        lines = [self.to_minimal_context(), ""]
        for s in self.symbols[:20]:
            lines.append(f"  {s.name} ({s.file_path}) [{s.kind}] last_commit={s.last_commit_ts}")
        if len(self.symbols) > 20:
            lines.append(f"  ... and {len(self.symbols) - 20} more")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-language entry point exclusions
# ---------------------------------------------------------------------------

def _is_entry_point(node: dict) -> bool:
    """Check if a node is a known entry point using only name + file_path.

    NOTE: We intentionally check only name and file_path because parser.py
    does NOT extract source text, decorators, or annotations into the graph.
    Checks that would need source/decorators (e.g. @app.route, @Test,
    export default, @abstractmethod) are deferred to Phase 2 when the parser
    stores richer metadata. This means Phase 1 will over-report dead code
    for decorated entry points — an acceptable false-positive rate vs the
    alternative of phantom checks that silently never trigger.
    """
    name = node.get("name", "")
    fp = node.get("file_path", "")
    lang = node.get("language", "").lower()

    # ── Python ──
    if lang == "python":
        if name.startswith("test_") or Path(fp).name.startswith("test_"):
            return True
        fname = Path(fp).name
        if fname in ("conftest.py", "__init__.py", "__main__.py"):
            return True
        if name in ("main", "cli", "app", "setup"):
            return True
        return False

    # ── TypeScript / JavaScript ──
    if lang in ("typescript", "javascript"):
        if any(fp.endswith(s) for s in (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx",
                                        ".test.js", ".test.jsx", ".spec.js", ".spec.jsx")):
            return True
        # Common entry point names
        if name in ("main", "handler", "default"):
            return True
        return False

    # ── Java ──
    if lang == "java":
        if name == "main":
            return True
        if name.startswith("test") or name.startswith("Test"):
            return True
        return False

    # ── Go ──
    if lang == "go":
        if name in ("main", "init"):
            return True
        if name.startswith("Test") or name.startswith("Benchmark"):
            return True
        return False

    return False


# ---------------------------------------------------------------------------
# Commit timestamp helper
# ---------------------------------------------------------------------------

def _batch_last_commit_timestamps(file_paths: list[str], repo_root: Path) -> dict[str, int]:
    """Get the unix timestamp of the last commit for each file — single git call.

    P1-8: Batched to avoid O(n) subprocess spawns for dead symbol analysis.
    Uses `git log --format=%ct --name-only` to get all timestamps in one call.
    """
    if not file_paths:
        return {}
    result: dict[str, int] = {fp: 0 for fp in file_paths}
    unique_files = sorted(set(file_paths))
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%ct", "--name-only", "--diff-filter=AMRC", "--"]
            + unique_files,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            current_ts = 0
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.isdigit():
                    current_ts = int(line)
                elif current_ts and line in result and result[line] == 0:
                    result[line] = current_ts  # first occurrence = most recent
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_dead_code(
    graph_store: GraphStore,
    repo_root: Path,
    *,
    languages: list[str] | None = None,
) -> DeadCodeResult:
    """Scan the graph for dead symbols.

    Uses ``graph_store.find_dead_code()`` for the SQL-level filter (exported,
    non-entry-point, zero incoming edges), then applies language-specific
    entry point heuristics on top.

    Parameters
    ----------
    graph_store : GraphStore
        The code graph backing store.
    repo_root : Path
        Repository root for ``git log`` lookups.
    languages : list[str] | None
        Restrict to certain languages. ``None`` means all.

    Returns
    -------
    DeadCodeResult
        Symbols sorted by last_commit_ts ascending (oldest first).
    """
    try:
        raw_dead = graph_store.find_dead_code()
    except Exception as exc:
        logger.warning("Failed to query dead code: %s", exc)
        return DeadCodeResult()

    # Enrich each candidate with full metadata from get_nodes_by_file
    # and apply language-specific entry point filters
    dead: list[DeadSymbol] = []
    total = len(raw_dead)

    # P1-8: Batch git timestamps for all candidate files (single subprocess call)
    all_file_paths = list({c.get("file_path", "") for c in raw_dead if c.get("file_path")})
    ts_map = _batch_last_commit_timestamps(all_file_paths, repo_root)

    # Cache file lookups to avoid repeated queries
    file_cache: dict[str, list[dict]] = {}

    for candidate in raw_dead:
        fp = candidate.get("file_path", "")
        node_id = candidate.get("id", "")

        # Get enriched node data
        if fp not in file_cache:
            try:
                file_cache[fp] = graph_store.get_nodes_by_file(fp)
            except Exception:
                file_cache[fp] = []

        # Find the enriched node to get language info
        enriched = None
        for n in file_cache[fp]:
            if n["id"] == node_id:
                enriched = n
                break

        lang = enriched.get("language", "").lower() if enriched else _infer_language(fp)
        kind = candidate.get("node_type", "function")

        # Language filter
        if languages and lang not in [l.lower() for l in languages]:
            continue

        if _is_entry_point({"name": candidate.get("name", ""), "file_path": fp, "language": lang}):
            continue

        ts = ts_map.get(fp, 0)
        dead.append(DeadSymbol(
            id=node_id,
            name=candidate.get("name", node_id),
            file_path=fp,
            kind=kind,
            language=lang,
            last_commit_ts=ts,
        ))

    # Sort oldest first
    dead.sort(key=lambda s: s.last_commit_ts)

    return DeadCodeResult(symbols=dead, total_scanned=total)


def _infer_language(file_path: str) -> str:
    """Guess language from file extension."""
    ext = Path(file_path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".java": "java",
        ".go": "go",
    }.get(ext, "")
