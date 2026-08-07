"""Code Change Feed — Channel 1 of DDD Cultivation.

Analyzes the last git commit after workspace_auto_commit fires.
Detects architecture-impacting changes and generates CultivationProposal
targeting TECH.md.

Heuristic rules (no LLM, <1s execution):
- New Python/TS module file → confidence 0.9, target "Key Subsystems"
- File rename/move → confidence 0.8, target "Architecture"
- New file in routers/ or routes/ → confidence 0.8, target "Key Subsystems"
- __init__.py modified → confidence 0.7, target "Architecture"

Skips: test-only commits, .context/ changes, Knowledge/ changes,
small same-directory edits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import anyio

from core.initialization_manager import initialization_manager

logger = logging.getLogger(__name__)

# Lazy-safe imports for code_intel (may not be available in all environments)
try:
    from core.code_intel import load_project_graph
    from core.code_intel.parser import parse_file
    _CODE_INTEL_AVAILABLE = True
except ImportError:
    _CODE_INTEL_AVAILABLE = False

# Paths that never produce proposals (content, not architecture)
# PE-6 fix: include common test paths for both SwarmWS and swarmai codebases
_SKIP_PREFIXES = (
    "tests/",
    "test_",
    "backend/tests/",  # swarmai codebase test path
    ".context/",
    "Knowledge/",
    ".artifacts/",
    "Projects/",
    "Attachments/",
    "Services/",
)

# Candidate locations for the swarmai product codebase (checked in order).
# Used by _find_codebase_repo() to locate the git repo that contains
# actual Python/TypeScript code (vs SwarmWS which is agent workspace).
_CODEBASE_CANDIDATES = (
    "Desktop/SwarmAI-Workspace/swarmai",
    "SwarmAI-Workspace/swarmai",
    "swarmai",
)

# Only these extensions indicate potential architecture changes
_CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".rs", ".go", ".js", ".jsx"}

# Git timeout (same as auto_commit_hook)
_GIT_TIMEOUT = 10


@dataclass
class ArchChange:
    """A detected architecture-impacting change."""

    change_type: str  # "new_module", "rename", "api_endpoint", "import_change"
    path: str
    confidence: float
    target_section: str  # TECH.md section to target


class CodeChangeFeed:
    """Post-commit hook: detects architecture changes → CultivationProposal.

    Registered AFTER workspace_auto_commit in session hooks.
    Reads the last commit's --name-status output and applies heuristics.
    """

    name = "code_change_feed"

    def __init__(self, git_lock: asyncio.Lock | None = None) -> None:
        self._git_lock = git_lock

    async def execute(self, context) -> None:
        """Analyze last commit in both SwarmWS and swarmai codebase repos."""
        ws_path = initialization_manager.get_cached_workspace_path()
        if not ws_path:
            return

        if self._git_lock:
            async with self._git_lock:
                await anyio.to_thread.run_sync(self._analyze_repos, ws_path)
        else:
            await anyio.to_thread.run_sync(self._analyze_repos, ws_path)

    def _analyze_repos(self, ws_path: str) -> None:
        """Analyze both SwarmWS (workspace) and swarmai (codebase) repos.

        Each repo is isolated — failure in one does not block the other.
        """
        # 1. Original: analyze SwarmWS workspace repo
        try:
            self._analyze_and_propose(ws_path)
        except Exception as exc:
            logger.debug("code_change_feed: workspace analysis failed: %s", exc)

        # 2. New: analyze swarmai product codebase repo (the real code)
        try:
            codebase_path = self._find_codebase_repo(ws_path)
            if codebase_path:
                self._analyze_and_propose_codebase(codebase_path, ws_path)
        except Exception as exc:
            logger.debug("code_change_feed: codebase analysis failed: %s", exc)

    def _analyze_and_propose(self, ws_path: str) -> None:
        """Run git log analysis and generate proposals (sync, in thread)."""
        try:
            # Get last commit's file changes
            result = subprocess.run(
                ["git", "log", "-1", "--name-status", "--format=%H %s"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=_GIT_TIMEOUT,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return

            lines = result.stdout.strip().splitlines()
            if not lines:
                return

            # First line: commit hash + subject
            header = lines[0]
            commit_hash = header.split()[0] if header else "unknown"
            commit_subject = " ".join(header.split()[1:]) if header else ""

            # Remaining lines: status + path pairs
            file_changes = self._parse_name_status(lines[1:])
            if not file_changes:
                return

            # Detect architecture-impacting changes
            arch_changes = self._detect_arch_changes(file_changes)
            if not arch_changes:
                return

            # Generate proposals
            self._generate_proposals(
                arch_changes, commit_hash, commit_subject, ws_path
            )

        except subprocess.TimeoutExpired:
            logger.debug("code_change_feed: git timed out, skipping")
        except Exception as exc:
            logger.debug("code_change_feed: %s", exc)

    def _parse_name_status(self, lines: list[str]) -> list[tuple[str, str]]:
        """Parse git name-status output into (status, path) tuples.

        Status codes: A=added, M=modified, D=deleted, R=renamed (Rxx path1 path2)
        """
        changes: list[tuple[str, str]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                status = parts[0][0] if parts[0] else ""  # First char (A/M/D/R)
                path = parts[-1]  # Last element (for renames, this is the new path)
                changes.append((status, path))
        return changes

    def _detect_arch_changes(
        self, file_changes: list[tuple[str, str]]
    ) -> list[ArchChange]:
        """Apply heuristics to detect architecture-impacting changes."""
        changes: list[ArchChange] = []

        # Filter out non-code and skip-prefix files
        code_changes = [
            (status, path)
            for status, path in file_changes
            if not any(path.startswith(p) for p in _SKIP_PREFIXES)
            and Path(path).suffix in _CODE_EXTENSIONS
        ]

        if not code_changes:
            return []

        # Heuristic: if ALL changes are in the same directory and <3 files, skip
        # UNLESS there are new files or renames (always architecturally significant)
        dirs = set(str(Path(p).parent) for _, p in code_changes)
        if len(dirs) == 1 and len(code_changes) < 3:
            has_structural = any(s in ("A", "R") for s, _ in code_changes)
            if not has_structural:
                return []

        for status, path in code_changes:
            if status == "A":
                # New file
                if "router" in path.lower() or "route" in path.lower():
                    changes.append(ArchChange(
                        change_type="api_endpoint",
                        path=path,
                        confidence=0.8,
                        target_section="Key Subsystems",
                    ))
                else:
                    changes.append(ArchChange(
                        change_type="new_module",
                        path=path,
                        confidence=0.9,
                        target_section="Key Subsystems",
                    ))
            elif status == "R":
                changes.append(ArchChange(
                    change_type="rename",
                    path=path,
                    confidence=0.8,
                    target_section="Architecture",
                ))
            elif status == "M" and path.endswith("__init__.py"):
                changes.append(ArchChange(
                    change_type="import_change",
                    path=path,
                    confidence=0.7,
                    target_section="Architecture",
                ))

        return changes

    def _find_codebase_repo(self, ws_path: str) -> Optional[str]:
        """Discover the swarmai product codebase git repo.

        Checks candidate paths relative to HOME. Returns the first path that
        has both .git/ and backend/ (confirming it's the swarmai repo).
        Returns None if not found (e.g., on Hive where codebase isn't local).
        """
        home = Path.home()
        for candidate in _CODEBASE_CANDIDATES:
            repo_path = home / candidate
            if (repo_path / ".git").is_dir() and (repo_path / "backend").is_dir():
                return str(repo_path)
        return None

    def _analyze_and_propose_codebase(self, codebase_path: str, ws_path: str) -> None:
        """Analyze last commit in the swarmai codebase and generate proposals.

        Same logic as _analyze_and_propose but:
        - Runs git in the codebase_path directory
        - Proposals write to SwarmWS Projects/SwarmAI/ (DDD lives in workspace, not codebase)
        - Tracks last-analyzed commit to avoid re-processing same commit across sessions
        """
        state_file = Path(ws_path) / ".context" / ".code_change_feed_last_commit.txt"

        try:
            result = subprocess.run(
                ["git", "log", "-1", "--name-status", "--format=%H %s"],
                cwd=codebase_path, capture_output=True, text=True,
                timeout=_GIT_TIMEOUT,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return

            lines = result.stdout.strip().splitlines()
            if not lines:
                return

            header = lines[0]
            commit_hash = header.split()[0] if header else "unknown"
            commit_subject = " ".join(header.split()[1:]) if header else ""

            # Idempotency: skip if we already processed this commit
            if state_file.is_file():
                try:
                    last_hash = state_file.read_text(encoding="utf-8").strip()
                    # Validate: must be a 40-char hex string (full SHA)
                    if len(last_hash) == 40 and last_hash == commit_hash:
                        return
                except (OSError, UnicodeDecodeError):
                    pass  # Corrupted state — re-process to be safe

            # Parse and detect arch changes
            file_changes = self._parse_name_status(lines[1:])
            if not file_changes:
                # No file changes in commit — mark as processed, skip
                self._persist_last_commit(state_file, commit_hash)
                return

            arch_changes = self._detect_arch_changes(file_changes)
            if not arch_changes:
                # Files changed but none architecturally significant — mark as processed
                self._persist_last_commit(state_file, commit_hash)
                return

            # Generate proposals targeting SwarmAI project in SwarmWS
            self._generate_proposals(arch_changes, commit_hash, commit_subject, ws_path)

            # Persist AFTER successful proposal generation — if _generate_proposals
            # raises, we retry on next invocation rather than losing the commit.
            self._persist_last_commit(state_file, commit_hash)

            # P3: Re-index changed code files into code_intel graph (best-effort).
            # Runs AFTER persist — reindex is non-critical, must not block state tracking.
            _reindex_changed_files(file_changes, codebase_path)

            logger.info(
                "code_change_feed: codebase commit %s analyzed — %d arch changes detected",
                commit_hash[:8], len(arch_changes),
            )

        except subprocess.TimeoutExpired:
            logger.debug("code_change_feed: codebase git timed out, skipping")
        except Exception as exc:
            logger.debug("code_change_feed: codebase analysis failed: %s", exc)

    @staticmethod
    def _persist_last_commit(state_file: Path, commit_hash: str) -> None:
        """Atomically persist last-analyzed commit hash."""
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = state_file.with_suffix(".tmp")
            tmp.write_text(commit_hash, encoding="utf-8")
            os.replace(tmp, state_file)
        except OSError as exc:
            # Non-critical (worst case: re-processes next session) but observable
            logger.warning("code_change_feed: state persist failed: %s", exc)

    def _generate_proposals(
        self,
        arch_changes: list[ArchChange],
        commit_hash: str,
        commit_subject: str,
        ws_path: str,
    ) -> None:
        """Observe detected arch changes — does NOT write a review-queue proposal.

        Admission root-fix (run_97519f7c): "a new module `foo.py` exists" is a GIT
        FACT, not knowledge, and it is NOT a decision that needs a human (R30#4 —
        drift-bait, zero decision value). Emitting a CultivationProposal per new
        code file was the single biggest source of review-queue noise (56 such
        proposals in one cleanup). The architecture signal is ALREADY captured
        structurally: `_reindex_changed_files` (called on every commit) writes the
        new/changed modules into the code_intel graph. So this method now only
        LOGS the observation for telemetry — it never writes to the pending queue.
        """
        if not arch_changes:
            return
        # Log only (telemetry) — the module-existence signal lives in the code_intel
        # graph via _reindex_changed_files, not in the human review queue.
        significant = [c for c in arch_changes if c.confidence >= 0.6]
        if significant:
            logger.info(
                "code_change_feed: %d arch change(s) observed at commit %s "
                "(captured in code_intel graph, NOT queued for human review): %s",
                len(significant),
                commit_hash[:8],
                ", ".join(f"{c.change_type}:{c.path}" for c in significant[:5]),
            )


# ── P3: Code Intelligence Graph Auto-Rebuild ──────────────────────────


# Extensions to re-index into code_intel (subset of _CODE_EXTENSIONS)
_REINDEX_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}


def _reindex_changed_files(
    file_changes: list[tuple[str, str]],
    repo_root: str,
) -> int:
    """Re-index changed code files into the project's code_intel graph.

    Called after architecture analysis. Non-blocking — all errors caught.
    Only fires if the project has an existing code_intel.db.

    Args:
        file_changes: List of (status, path) from git name-status.
        repo_root: Absolute path to the repo root directory.

    Returns:
        Number of files successfully re-indexed.
    """
    try:
        if not _CODE_INTEL_AVAILABLE:
            return 0

        graph = load_project_graph("SwarmAI")
        if graph is None:
            return 0

        root = Path(repo_root)
        parse_results = []

        for status, rel_path in file_changes:
            # Skip deletions (can't parse a deleted file)
            # Note: renames (R) re-index at new path but don't clean old path nodes.
            # Known limitation — old nodes become orphans until next full rebuild.
            if status == "D":
                continue

            # Skip non-code files
            if Path(rel_path).suffix not in _REINDEX_EXTENSIONS:
                continue

            # Skip test files (not part of the architecture graph)
            if any(rel_path.startswith(p) for p in _SKIP_PREFIXES):
                continue

            # File must exist on disk to parse
            abs_path = root / rel_path
            if not abs_path.is_file():
                continue

            # Parse the file
            try:
                result = parse_file(abs_path, root)
                if result.nodes:
                    parse_results.append(result)
            except Exception as exc:
                logger.debug("code_change_feed: reindex parse failed for %s: %s", rel_path, exc)
                continue

        if parse_results:
            graph.bulk_insert(parse_results, repo_root=root)
            logger.info(
                "code_change_feed: re-indexed %d file(s) into code_intel",
                len(parse_results),
            )

        return len(parse_results)

    except Exception as exc:
        logger.debug("code_change_feed: reindex failed: %s", exc)
        return 0
