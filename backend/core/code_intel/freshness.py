"""
Graph freshness detection via git SHA tracking.

Pure git — works on GitHub, code.amazon.com, GitLab, any git host.
Detects ALL changes: our commits, teammates' commits, rebases, merges.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .build import FULL_REBUILD_FILES, parser_outdated

OUTDATED_REASON = "built by an older parser; it is rebuilt in full"

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# A git-path result depends only on (repo, HEAD, last indexed commit): recall checks
# freshness every turn, so the merge-base / diff / rev-list work is done once per
# such triple. Only final answers are kept (a git error is not cached). Bounded by
# clearing when full.
_DIFF_CACHE: dict[tuple[str, str, str], "FreshnessResult"] = {}
_DIFF_CACHE_MAX = 256


def _remember(key: tuple[str, str, str], result: "FreshnessResult") -> "FreshnessResult":
    if len(_DIFF_CACHE) >= _DIFF_CACHE_MAX:
        _DIFF_CACHE.clear()
    _DIFF_CACHE[key] = result
    return replace(result, changed_files=list(result.changed_files))


class GitError(Exception):
    """Git command failed."""


@dataclass
class FreshnessResult:
    stale: bool
    changed_files: list[str] = field(default_factory=list)
    commits_behind: int = 0
    last_indexed: str | None = None
    current_head: str | None = None
    suggest_full_rebuild: bool = False
    reason: str = ""


def check_freshness(graph_store: GraphStore) -> FreshnessResult:
    """
    Pure git — works on GitHub, code.amazon.com, GitLab, any git host.
    Detects ALL changes: our commits, teammates' commits, rebases, merges.
    """
    repo_root_str = graph_store.get_meta("repo_root")
    if not repo_root_str:
        return FreshnessResult(stale=True, suggest_full_rebuild=True,
                               reason="No repo_root in graph metadata")

    repo_root = Path(repo_root_str)
    if not repo_root.is_dir():
        return FreshnessResult(stale=True, reason=f"repo not found: {repo_root}")

    # Before every other answer (HEAD equal to the marker, the diff cache, the mtime
    # path): symbols an older parser wrote are wrong at any commit, and an incremental
    # update would mix two parsers' output in one graph.
    if parser_outdated(graph_store):
        return FreshnessResult(stale=True, suggest_full_rebuild=True,
                               last_indexed=graph_store.get_meta("last_indexed_commit"),
                               reason=OUTDATED_REASON)

    # Check if it's a git repo
    if not (repo_root / ".git").exists() and not _is_git_worktree(repo_root):
        return _mtime_freshness(graph_store, repo_root)

    last_commit = graph_store.get_meta("last_indexed_commit")

    # Compute HEAD once (best-effort). BOTH the never-indexed path and the
    # comparison below need it. Critically, populating current_head on the
    # never-indexed path lets the marker writer (``build.ensure_fresh`` / ``rebuild``,
    # guarded by ``if freshness.current_head:``) persist last_indexed_commit. Without
    # it the marker never persisted on a never-indexed graph and every refresh redid a
    # full repo reparse (run_9a23dd4a).
    try:
        current_head = _git(repo_root, ["rev-parse", "HEAD"]).strip()
        head_error: GitError | None = None
    except GitError as e:
        logger.warning(f"Git rev-parse failed: {e}")
        current_head = None
        head_error = e

    if not last_commit:
        # Never indexed → full rebuild regardless of git outcome. current_head is
        # best-effort: set when git works (breaks the perpetual-rebuild loop),
        # None on genuine git failure (write-guard stays closed, no crash).
        return FreshnessResult(stale=True, suggest_full_rebuild=True,
                               current_head=current_head,
                               reason="Never indexed")

    # Beyond here we need a real HEAD to diff against last_commit.
    if current_head is None:
        return FreshnessResult(stale=True, reason=f"git error: {head_error}")

    if current_head == last_commit:
        # Carry current_head even on the fresh path so the field's contract is
        # uniform ("set whenever git succeeded"). A --full rebuild on an
        # already-fresh repo can then still refresh the marker via the
        # `if freshness.current_head:` writer (Gate-2 MED, run_9a23dd4a).
        return FreshnessResult(stale=False, current_head=current_head)

    key = (str(repo_root), current_head, last_commit)
    cached = _DIFF_CACHE.get(key)
    if cached is not None:
        return replace(cached, changed_files=list(cached.changed_files))

    # Edge case: force push / rebase removed our baseline commit
    try:
        ancestor_check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", last_commit, current_head],
            cwd=repo_root, capture_output=True, timeout=5
        )
        if ancestor_check.returncode != 0:
            return _remember(key, FreshnessResult(
                stale=True,
                suggest_full_rebuild=True,
                current_head=current_head,
                last_indexed=last_commit,
                reason=f"Base commit {last_commit[:8]} rebased away — incremental impossible"
            ))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return FreshnessResult(
            stale=True,
            suggest_full_rebuild=True,
            current_head=current_head,
            reason="git merge-base failed"
        )

    # Normal case: what files changed?
    try:
        changed_output = _git(repo_root, [
            # --no-renames: a rename is listed as its old path (deleted, so its nodes
            # are removed) plus its new path; with rename detection only the new
            # path appears and the old path's nodes would stay in the graph.
            "diff", "--name-only", "--no-renames", f"{last_commit}..{current_head}"
        ])
        changed_files = [f for f in changed_output.strip().split("\n") if f]

        commit_count_str = _git(repo_root, [
            "rev-list", "--count", f"{last_commit}..{current_head}"
        ]).strip()
        commit_count = int(commit_count_str) if commit_count_str else 0
    except (GitError, ValueError) as e:
        logger.warning(f"Git diff/rev-list failed: {e}")
        return FreshnessResult(
            stale=True,
            suggest_full_rebuild=True,
            current_head=current_head,
            last_indexed=last_commit,
            reason=f"git error: {e}"
        )

    return _remember(key, FreshnessResult(
        stale=True,
        changed_files=changed_files,
        commits_behind=commit_count,
        last_indexed=last_commit,
        current_head=current_head,
        suggest_full_rebuild=(len(changed_files) > FULL_REBUILD_FILES or commit_count > 50),
    ))


def _git(cwd: Path, args: list[str], timeout: int = 5) -> str:
    """Bare git command. No hosting API. Works on any git repo."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise GitError("git not found on PATH")
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)}: timed out after {timeout}s")

    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def _is_git_worktree(path: Path) -> bool:
    """Check if path is a git worktree (has .git file pointing to main repo)."""
    git_file = path / ".git"
    if git_file.is_file():
        try:
            content = git_file.read_text().strip()
            return content.startswith("gitdir:")
        except Exception:
            pass
    return False


def _mtime_freshness(graph_store: GraphStore, repo_root: Path) -> FreshnessResult:
    """
    Fallback: check file mtimes vs last_indexed timestamp.
    Lower accuracy than git (catches file saves, misses renames/deletes).
    """
    from .parser import LANGUAGE_MAP

    # The last FULL index only: an incremental batch may cover part of the changed
    # files (the reindex job takes the first 200), so its stamp would hide the rest.
    from .graph_store import parse_index_time
    last_indexed_ts = parse_index_time(graph_store.get_meta("last_full_index")) or 0.0

    changed = []
    file_count = 0
    max_files = 10000  # cap to avoid walking huge non-git dirs

    for ext in LANGUAGE_MAP:
        for f in repo_root.rglob(f"*{ext}"):
            file_count += 1
            if file_count > max_files:
                return FreshnessResult(
                    stale=True,
                    suggest_full_rebuild=True,
                    reason=f"mtime scan: too many files (>{max_files})"
                )
            try:
                if f.stat().st_mtime > last_indexed_ts:
                    changed.append(str(f.relative_to(repo_root)))
            except OSError:
                continue

    return FreshnessResult(
        stale=bool(changed),
        changed_files=changed,
        reason="mtime-based (no git)"
    )


def head_stamp(graph_store: GraphStore) -> tuple[bool | None, str | None]:
    """``(stale, head)`` from ONE ``git rev-parse HEAD`` against the graph's commit
    marker — the stamp a reader in the daemon shows (it never refreshes a graph).
    ``stale`` is None when it cannot tell (no repo root, not a git repo, git failed).
    It does not read the parser version: a daemon reader asks ``build.parser_outdated``
    first (an outdated graph is wrong at any commit)."""
    root = graph_store.get_meta("repo_root")
    marker = graph_store.get_meta("last_indexed_commit")
    if not root or not Path(root).is_dir():
        return None, None
    try:
        head = _git(Path(root), ["rev-parse", "HEAD"]).strip() or None
    except GitError:
        return None, None
    if head is None:
        return None, None
    return (marker != head), head
