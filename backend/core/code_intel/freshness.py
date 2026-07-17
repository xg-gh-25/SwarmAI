"""
Graph freshness detection via git SHA tracking.

Pure git — works on GitHub, code.amazon.com, GitLab, any git host.
Detects ALL changes: our commits, teammates' commits, rebases, merges.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)


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

    # Check if it's a git repo
    if not (repo_root / ".git").exists() and not _is_git_worktree(repo_root):
        return _mtime_freshness(graph_store, repo_root)

    last_commit = graph_store.get_meta("last_indexed_commit")

    # Compute HEAD once (best-effort). BOTH the never-indexed path and the
    # comparison below need it. Critically, populating current_head on the
    # never-indexed path lets the 3 marker writers — all guarded by
    # `if freshness.current_head:` (code_intel_reindex.py:73/129,
    # context_health_hook.py:649) — persist last_indexed_commit. Without it,
    # check_freshness returned current_head=None on "Never indexed", the marker
    # never persisted, and every on:git_commit reindex redid a full repo reparse
    # (~85-118s) forever, flapping past the 120s timeout (run_9a23dd4a).
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

    # Edge case: force push / rebase removed our baseline commit
    try:
        ancestor_check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", last_commit, current_head],
            cwd=repo_root, capture_output=True, timeout=5
        )
        if ancestor_check.returncode != 0:
            return FreshnessResult(
                stale=True,
                suggest_full_rebuild=True,
                current_head=current_head,
                last_indexed=last_commit,
                reason=f"Base commit {last_commit[:8]} rebased away — incremental impossible"
            )
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
            "diff", "--name-only", f"{last_commit}..{current_head}"
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

    return FreshnessResult(
        stale=True,
        changed_files=changed_files,
        commits_behind=commit_count,
        last_indexed=last_commit,
        current_head=current_head,
        suggest_full_rebuild=(len(changed_files) > 100 or commit_count > 50),
    )


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

    last_indexed_str = graph_store.get_meta("last_full_index")
    last_indexed_ts = float(last_indexed_str) if last_indexed_str else 0.0

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


# ─── Run 4b (run_2bad039d, §8.6): spec-details staleness detector ───
# ─── run_fe26ed6c RESHAPE: mtime → domain CONTENT-HASH (Gate-1 F1/F1b) ───

# The spec-hash marker embedded in each .spec.md header by the skill's
# project_domain_skeleton. READ-only regex (never computes a hash), so duplicating
# the *pattern* here does NOT reintroduce two-writer drift — the hash VALUE is
# computed at exactly one site (ai_ready_helpers._spec_content_hash, stamped into
# code-intel.json domains[].spec_hash at export). freshness.py only COMPARES the
# marker to that stamp; C046 keeps core from importing the skill.
_SPEC_HASH_MARKER_RE = re.compile(r"<!--\s*spec-hash:\s*([0-9a-f]{64})\s*-->")


def detect_spec_details_staleness(project_dir: Path) -> list[str]:
    """Return the names of spec-details/*.spec.md files that are STALE vs their
    domain — i.e. the domain's CONTENT changed since the spec was last projected.

    CONTENT-HASH based, NOT mtime (Gate-1 RESHAPE, run_fe26ed6c). Each domain in
    code-intel.json carries a ``spec_hash`` (stamped at export from the domain +
    its flows + steps); each .spec.md embeds a ``<!-- spec-hash: X -->`` marker.
    A spec is STALE iff its marker is MISSING or != its domain's spec_hash. mtime
    is irrelevant: a reindex rewrites code-intel.json (mtime bumps) while PRESERVING
    identical domains[] — the old mtime detector false-fired EVERY spec after any
    rebuild (the exact bug this replaces).

    Matching: spec file ``<name>.spec.md`` ↔ domain id ``domain:<name>`` (the skill's
    own filename convention, INSTRUCTIONS.md: ``domain['id'].split(':',1)[-1]``).

    PURE detection only (read + hash-compare, no regeneration). Regeneration is
    skill-owned (LLM-in-agent, C046) — this detector lets a core hook SIGNAL
    staleness so regeneration gets scheduled, without the coupling.

    Returns [] when: no code-intel.json, no spec-details/ dir, no .spec.md files,
    no domains[] with spec_hash, or every spec's marker matches. A domain WITHOUT
    a spec_hash stamp (pre-reshape doc) is unjudgeable → its spec is NOT flagged
    (never false-positive). Never raises on a missing/unreadable file.
    """
    project_dir = Path(project_dir)
    ci = project_dir / "code-intel.json"
    sd = project_dir / "spec-details"
    if not ci.is_file() or not sd.is_dir():
        return []
    try:
        doc = json.loads(ci.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    # Map spec-filename-stem → domain spec_hash (only domains that carry one).
    want: dict[str, str] = {}
    for dom in (doc.get("domains") or []):
        if not isinstance(dom, dict):
            continue
        did = dom.get("id") or ""
        sh = dom.get("spec_hash")
        if did and sh:
            want[did.split(":", 1)[-1]] = sh
    if not want:
        return []
    stale: list[str] = []
    for spec in sorted(sd.glob("*.spec.md")):
        stem = spec.name[: -len(".spec.md")]
        expected = want.get(stem)
        if not expected:
            continue  # no domain stamp for this spec → unjudgeable, not stale
        try:
            text = spec.read_text(encoding="utf-8")
        except OSError:
            continue  # can't read → don't claim stale
        m = _SPEC_HASH_MARKER_RE.search(text)
        if m is None or m.group(1) != expected:
            stale.append(spec.name)
    return stale
