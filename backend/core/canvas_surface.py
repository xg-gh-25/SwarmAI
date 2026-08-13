"""canvas_surface — the Canvas-ONLY surface predicate for SwarmWS-OUTSIDE files.

## Why this exists (run_5d9178bf, the Universal Workspace Activity Ledger)

`needs_human_review` answers "should a human review this, and is it git-commit
material?" — its ``_owning_tree`` only knows SwarmWS + bound worktrees, so ANY file
the agent touches outside those (a plain-FS file under ``~/Desktop/AI-Native/``, or
a file in an arbitrary external git repo the user is developing) returns
``review_worthy=False, kind="process"`` → DROPPED from the Canvas rail.

XG's north star: SwarmAI is the ONE app you keep open, so **any file the session
touched — regardless of which repo/FS it lives in — should surface in the Canvas
rail** (git → diff; no-git → listed-only). But "should Canvas show it" and "should
auto_commit commit it" are DIFFERENT judgments. Widening ``_owning_tree`` would make
auto_commit (one of ~70 callers) try to commit external-repo files — a disaster
(R27). So this is a SEPARATE predicate, consulted ONLY for the outside-tree files
``needs_human_review`` already rejected. ``needs_human_review`` stays byte-unchanged.

## Contract

Called ONLY after ``needs_human_review`` returns a non-surfacing verdict for a
resolved ABSOLUTE path. Then:
  - If the path is INSIDE a known tree (SwarmWS / bound worktree) → decline
    (``surfaceable=False``) — that's ``needs_human_review``'s job, never double-classify.
  - OUTSIDE every known tree:
    - in a git repo (``git rev-parse`` succeeds from the file's dir):
      - git check-ignore says IGNORED → **decline** (a gitignored secret in an
        external repo must NOT leak — Gate-1 WARN #2, mirrors the SwarmWS secret rule).
      - not ignored → ``CanvasSurface(True, "external-diff", base_ref=<sha>^)``.
    - not in any git repo (plain FS) → ``CanvasSurface(True, "external-nodiff")``.

Fail-safe: NEVER raises (runs on the streaming hot path). On any error → decline.
All git calls are the CALLER's responsibility to run off-loop (``to_thread``) —
this module is sync (mirrors ``needs_human_review``).
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

__all__ = ["is_canvas_surfaceable", "CanvasSurface", "CanvasKind"]

CanvasKind = Literal["external-diff", "external-nodiff"]

_GIT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class CanvasSurface:
    """Verdict for a SwarmWS-outside file.

    - ``surfaceable`` — should this file appear as a Canvas rail row?
    - ``kind`` — ``external-diff`` (owning git repo → per-file diff) or
      ``external-nodiff`` (plain FS → listed only). ``None`` when not surfaceable.
    - ``base_ref`` — the git ref to diff against (``<sha>^``) for external-diff;
      ``None`` otherwise.
    """

    surfaceable: bool
    kind: Optional[CanvasKind] = None
    base_ref: Optional[str] = None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess | None:
    """Run a git command in ``cwd``; return the completed process, or ``None`` on
    any spawn/timeout error (fail-safe — the caller treats None as 'no git info')."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def is_canvas_surfaceable(
    abs_path: str,
    *,
    swarmws_root: "str | Path | None" = None,
) -> CanvasSurface:
    """Decide whether a SwarmWS-OUTSIDE file the session touched surfaces in Canvas.

    See the module docstring for the full contract. Consulted ONLY for paths
    ``needs_human_review`` already declined. Never raises.
    """
    try:
        if not abs_path or "\x00" in abs_path:
            return CanvasSurface(False)

        p = Path(os.path.expanduser(abs_path))
        # We are only asked about RESOLVED absolute paths (the emit gate resolves
        # first). A non-absolute input is not ours to classify → decline.
        if not p.is_absolute():
            return CanvasSurface(False)
        try:
            p = p.resolve()
        except (OSError, RuntimeError):
            return CanvasSurface(False)

        # 1) INSIDE a known tree → not our job (needs_human_review owns those).
        #    Reuse the SAME owning-tree resolver so the boundary can never drift
        #    between the two predicates (P8: one brain, consistent doors).
        from core.needs_human_review import _owning_tree
        from core.project_registry import get_swarmws

        ws_root = Path(swarmws_root).resolve() if swarmws_root is not None else Path(get_swarmws()).resolve()
        if _owning_tree(p, ws_root) is not None:
            return CanvasSurface(False)  # inside SwarmWS / a bound worktree

        # 2) OUTSIDE every known tree. Find the file's own directory to probe git.
        file_dir = p.parent
        if not file_dir.is_dir():
            return CanvasSurface(False)

        # In a git repo? (rev-parse from the file's dir)
        rp = _git(file_dir, "rev-parse", "--is-inside-work-tree")
        in_git = bool(rp) and rp.returncode == 0 and rp.stdout.decode("utf-8", "replace").strip() == "true"

        if not in_git:
            # Plain FS — listed only, no diff.
            return CanvasSurface(True, "external-nodiff")

        # In an external git repo. First: is it gitignored? A gitignored secret in
        # an external repo must NOT leak (Gate-1 WARN #2 — mirrors the SwarmWS
        # secret-safety rule). check-ignore -q: rc 0 = ignored, 1 = not, other = err.
        ci = _git(file_dir, "check-ignore", "-q", "--", str(p))
        if ci is None:
            # git errored → fail CLOSED (can't tell secret from deliverable).
            logger.warning(
                "is_canvas_surfaceable: git check-ignore errored for %s — declining (fail-closed).",
                p,
            )
            return CanvasSurface(False)
        if ci.returncode == 0:
            return CanvasSurface(False)  # ignored → do not surface
        if ci.returncode not in (0, 1):
            return CanvasSurface(False)  # unexpected git error → fail closed

        # Not ignored → surface with a per-file diff baseline. base_ref = HEAD^ for
        # the file's last commit if resolvable, else HEAD (uncommitted → diff vs HEAD).
        base_ref = "HEAD"
        log = _git(file_dir, "log", "-1", "--format=%H", "--", str(p))
        if log and log.returncode == 0:
            sha = log.stdout.decode("utf-8", "replace").strip()
            if sha:
                base_ref = f"{sha}^"
        return CanvasSurface(True, "external-diff", base_ref=base_ref)
    except Exception:  # noqa: BLE001 — hot-path fail-safe, never crash the turn
        return CanvasSurface(False)
