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
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import anyio

from core.initialization_manager import initialization_manager

logger = logging.getLogger(__name__)

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
        """Analyze last commit and generate proposals if warranted."""
        ws_path = initialization_manager.get_cached_workspace_path()
        if not ws_path:
            return

        if self._git_lock:
            async with self._git_lock:
                await anyio.to_thread.run_sync(self._analyze_and_propose, ws_path)
        else:
            await anyio.to_thread.run_sync(self._analyze_and_propose, ws_path)

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

    def _generate_proposals(
        self,
        arch_changes: list[ArchChange],
        commit_hash: str,
        commit_subject: str,
        ws_path: str,
    ) -> None:
        """Generate CultivationProposal for detected changes."""
        from core.ddd_cultivation import CultivationProposal, write_proposal

        # Determine active project (default to SwarmAI for workspace commits)
        project_dir = Path(ws_path) / "Projects" / "SwarmAI"
        if not project_dir.exists():
            return

        # Group by target section for a single consolidated proposal
        by_section: dict[str, list[ArchChange]] = {}
        for change in arch_changes:
            if change.confidence >= 0.6:
                by_section.setdefault(change.target_section, []).append(change)

        for section, section_changes in by_section.items():
            # Build content summary
            summaries = []
            for c in section_changes[:5]:  # Cap at 5 per proposal
                summaries.append(f"- {c.change_type}: `{c.path}`")
            content = "\n".join(summaries)

            # Use highest confidence from the group
            max_confidence = max(c.confidence for c in section_changes)

            # Build evidence string for source_run_id field
            evidence_str = f"commit:{commit_hash[:8]} | {commit_subject[:80]}"

            proposal = CultivationProposal(
                target_doc="TECH.md",
                target_section=section,
                content=f"Architecture change detected:\n{content}",
                source_run_id=evidence_str,
                confidence=max_confidence,
                source_stage="code_change_feed",
            )

            write_proposal(proposal, project_dir)
            logger.info(
                "code_change_feed: proposal generated for TECH.md#%s "
                "(confidence=%.1f, commit=%s)",
                section,
                max_confidence,
                commit_hash[:8],
            )
