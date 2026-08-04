"""Git-based COMPLETE-stage change sweep — the pipeline-finish Canvas review panel.

WHY GIT (run_608a6217): "what changed on disk this run?" is a STATE question. Git
answers it author-agnostically — main-agent, sub-agent, CLI-subprocess, and
post-session-hook writes ALL appear in `git status`, whereas tracing the producing
process CANNOT see them (the Claude Agent SDK filters sub-agent sidechain messages —
claude_agent_sdk/types.py:1599 "tool-use sidechain messages are filtered out"; and
live SSE is per-turn so CLI/hook writes have no stream to ride). So at COMPLETE we
stand on git, not on emit-source coverage.

WHAT IT DOES: `git status --porcelain` on BOTH trees (the SwarmWS workspace + each
bound source repo), classify every changed path via `needs_human_review`, and bucket:
  - content / knowledge → pop to Canvas (DDD / design docs / MEMORY / KNOWLEDGE — the
    "normal workflow, surface if present" default)
  - source              → aggregate into the finish-time LOCAL_PR (code — the ONE
    special case that is NOT popped per-file mid-run)
  - process             → machine noise (`.artifacts`, `.context/*.json`, …), dropped

F1 (Gate-1 critical contract — the reason classify_git_status_paths exists as a
seam): `git status --porcelain` emits paths RELATIVE to each repo root. But
`needs_human_review` joins a non-absolute path against the SwarmWS root
(needs_human_review.py:237-238), so a source-repo-relative path ("backend/foo.py")
would resolve under SwarmWS → owning-tree=SwarmWS → misclassified as `content`
(popped per-file) instead of `source` (aggregated). Every porcelain path MUST be
absolutized against ITS OWN repo root before classification. Proven live 2026-08-04:
source-relative → content (bug), source-absolute → source (correct).
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.needs_human_review import needs_human_review, ReviewVerdict

logger = logging.getLogger(__name__)


class _GitSweepError(Exception):
    """A `git status` call failed for one tree. Carries the tree + cause so the
    sweep records it as a SKIPPED tree (observable) rather than a silent all-clear."""

    def __init__(self, tree: str, returncode: Optional[int], detail: str):
        self.tree = tree
        self.returncode = returncode
        self.detail = detail
        super().__init__(f"git status failed for {tree} (rc={returncode}): {detail[:120]}")


def classify_git_status_paths(
    repo_root: Path | str,
    porcelain_paths: list[str],
    swarmws_root: Path | str,
) -> list[tuple[str, ReviewVerdict]]:
    """Classify each git-status-relative path from ONE repo.

    Each ``porcelain_path`` is relative to ``repo_root`` (as `git status
    --porcelain` emits). It is absolutized against ``repo_root`` BEFORE calling
    ``needs_human_review`` (the F1 contract — never pass a repo-relative path to the
    classifier, it would join against SwarmWS root and misclassify a source file as
    content). Returns ``(relative_path, verdict)`` pairs in input order.
    """
    root = Path(repo_root)
    results: list[tuple[str, ReviewVerdict]] = []
    for rel in porcelain_paths:
        abs_path = str((root / rel).resolve())
        verdict = needs_human_review(abs_path, "written", swarmws_root=swarmws_root)
        results.append((rel, verdict))
    return results


def _porcelain_paths(repo_root: Path | str) -> list[str]:
    """Return the changed-file paths from `git status --porcelain -z` in *repo_root*.

    Uses ``-z`` (NUL-separated) so paths with spaces/newlines are handled. Each
    porcelain record is ``XY <path>`` (2 status chars + a space + the path); a
    rename record ``R  <new>\\x00<old>`` yields BOTH the new and old path. Fail-safe:
    any git error returns [] (a sweep that can't read a tree surfaces nothing from
    it, never crashes COMPLETE).
    """
    try:
        # -uall (untracked-files=all): list INDIVIDUAL untracked files, not the
        # collapsed parent directory. Without it, a brand-new file under an
        # otherwise-untracked dir reports as "?? Knowledge/" (the dir) instead of
        # "?? Knowledge/Designs/foo.md" — and a bare dir path can't be classified
        # per-file (it'd bucket the whole dir by one verdict). Verified: default -z
        # collapses to "?? Knowledge/", -uall yields the full file path.
        out = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "-z", "-uall"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            # Gate-2 MEDIUM 2: a git error must NOT be a SILENT all-clear — an empty
            # result on error is indistinguishable from "tree is clean", so a real
            # deliverable would silently NOT surface (the exact failure this sweep
            # exists to prevent). Log loudly; the caller records it as a skipped tree.
            logger.warning(
                "run_surface_changes: git status failed for %s (rc=%s) — changes "
                "NOT surfaced from this tree; stderr=%s",
                repo_root, out.returncode, (out.stderr or "").strip()[:200],
            )
            raise _GitSweepError(str(repo_root), out.returncode, out.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(
            "run_surface_changes: git status errored for %s: %s — changes NOT surfaced",
            repo_root, f"{type(e).__name__}: {e}",
        )
        raise _GitSweepError(str(repo_root), None, str(e))

    paths: list[str] = []
    # -z output: records are NUL-terminated. A rename/copy record is followed by an
    # EXTRA NUL-terminated field (the source path). We treat every field that isn't
    # a pure status code as a path — simplest robust parse for our classify use.
    records = [r for r in out.stdout.split("\x00") if r]
    i = 0
    while i < len(records):
        rec = records[i]
        # A porcelain record is "XY path" (status is 2 chars + 1 space = 3-char prefix).
        if len(rec) >= 4 and rec[2] == " ":
            status = rec[:2]
            path = rec[3:]
            paths.append(path)
            # Rename/copy (R./C.) carry the ORIGIN path as the next NUL field.
            if status[0] in ("R", "C") and i + 1 < len(records):
                paths.append(records[i + 1])
                i += 1
        i += 1
    return paths


@dataclass
class SurfaceBuckets:
    """The four review buckets. Display-path convention (Gate-2 LOW 2 — stated
    plainly, no hedge):
      - ``content`` / ``knowledge`` → SwarmWS-relative paths, RESOLVER-SAFE — hand
        each to ``ui_action open-canvas-file`` to pop it into Canvas.
      - ``source`` → SOURCE-REPO-relative paths, for LOCAL_PR DISPLAY ONLY — do NOT
        feed these to open-canvas-file (they resolve against the wrong tree); they
        are aggregated into the finish-time LOCAL_PR instead.
      - ``process`` → machine noise, dropped (kept only for observability/debug).

    ``errors`` records any tree whose `git status` failed (Gate-2 MEDIUM 2) so a
    git error surfaces as a KNOWN skipped tree, never a silent all-clear."""
    content: list[str] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)
    source: list[str] = field(default_factory=list)
    process: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        d = {
            "content": self.content,
            "knowledge": self.knowledge,
            "source": self.source,
            "process": self.process,
        }
        if self.errors:  # only present when a tree was skipped — loud, not silent
            d["errors"] = self.errors
        return d


def sweep_run_changes(swarmws_root: Path | str) -> SurfaceBuckets:
    """Sweep git status across the SwarmWS tree + every bound source worktree and
    bucket every changed path by ``needs_human_review`` kind.

    Author-agnostic: git sees main-agent / sub-agent / CLI / hook writes alike.
    Read-only — never mutates any tree. A git error on one tree does NOT crash the
    sweep NOR silently vanish: the tree is recorded in ``buckets.errors`` (a KNOWN
    skipped tree, not a false all-clear — Gate-2 MEDIUM 2).

    Display-path convention (Gate-2 LOW 2 — no hedge):
      - content/knowledge → SwarmWS-relative ("Knowledge/Designs/x.md"),
        RESOLVER-SAFE: hand to open-canvas-file.
      - source → SOURCE-REPO-relative ("backend/foo.py"), LOCAL_PR DISPLAY ONLY:
        do NOT feed to open-canvas-file (wrong tree); aggregate into the LOCAL_PR.
    """
    from core.needs_human_review import _worktree_roots

    ws_root = Path(os.path.expanduser(str(swarmws_root))).resolve()
    buckets = SurfaceBuckets()

    # The set of trees to sweep: every bound source worktree, PLUS the SwarmWS tree.
    trees: list[Path] = [Path(wt) for wt, _repo in _worktree_roots(str(ws_root))]
    trees.append(ws_root)

    seen_abs: set[str] = set()
    for tree in trees:
        try:
            paths = _porcelain_paths(tree)
        except _GitSweepError as e:
            # Gate-2 MEDIUM 2: record the skipped tree so a git error is a KNOWN
            # gap, never a silent all-clear. The other trees still sweep.
            buckets.errors.append(e.tree)
            continue
        for rel, verdict in classify_git_status_paths(tree, paths, ws_root):
            abs_key = str((tree / rel).resolve())
            if abs_key in seen_abs:
                continue  # a path under both a worktree and SwarmWS — classify once
            seen_abs.add(abs_key)
            getattr(buckets, verdict.kind).append(rel)
    return buckets
