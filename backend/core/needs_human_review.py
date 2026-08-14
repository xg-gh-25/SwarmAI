"""needs_human_review — the SINGLE git-based authority for "should a human review
this file change?" (run_dcce7023, Canvas review-trigger unification).

Replaces the four scattered heuristics that used to answer this question
independently (a hand-written ``_BOOKKEEPING_DIRS`` blacklist duplicated in
backend AND frontend; a Bash-redirect sniffer; frontend Canvas gates). Those
were a whitelist-inverted-badly model: default = don't surface, hand-pick what
to surface → necessarily misses → patch per miss → forever scattered.

## The model reversal (why this is terminal, not another patch)

Stand on the signals the system ALREADY maintains authoritatively:

    review_worthy(path) =
          path resolves inside SwarmWS OR inside a bound-repo worktree (bindings.yaml)
      AND git check-ignore (run in THAT owning tree, on the TREE-RELATIVE path)
          says NOT-IGNORED          — layer 1: subtracts the whole .gitignore
      AND no dot-prefixed segment in the TREE-RELATIVE path
                                    — layer 2: hidden = system = never

- **Default = surface** (it's the human's work). The only subtractions are known
  machine noise. A miss on a NON-git-error path fails SAFE toward surfacing (an
  extra surface, never a dropped piece of the human's work).
- **BUT git-error fails CLOSED** (run_a18d69f5 #5, XG-decided): when
  ``git check-ignore`` itself errors (rc 128 / OSError / timeout / budget-spent)
  we CANNOT know whether the path is a gitignored secret (``secrets.yaml`` /
  ``prod.env`` — non-dot-prefixed, so Layer 2 doesn't catch them) or a real
  deliverable. Surfacing on git-error would leak a secret to the Canvas rail
  IRREVERSIBLY; under-surfacing a real deliverable is recoverable (the next
  turn-end sweep re-emits it). Secret-safety > one recoverable missed surface, so
  the git-error verdict is ``review_worthy=False`` — and is WARN-logged, never
  silent (the fail-closed silent-death twin, GUI98: a persistently broken git must
  make noise, not swallow every surface invisibly).

## Two layers, both load-bearing (Gate-1 corrections, run_dcce7023)

- **Layer 1 (check-ignore)** subtracts everything ``.gitignore`` already knows —
  dozens of rules (``*.db``, ``*.lock``, ``node_modules``, ``config.json``,
  ``*_state.json``, ``.context/*.jsonl`` …), far more than the old 3-dir list, and
  drift-proof (a new ``.gitignore`` line auto-updates the verdict).
- **Layer 2 (dot-segment)** is NOT decorative: ``.artifacts/`` is TRACKED in
  SwarmWS (5365 files) and NOT in ``.gitignore``, so check-ignore alone returns
  not-ignored for ``.artifacts/runs/x/REPORT.md`` and ``.context/*.json`` — the
  very machine-state we must exclude. A single structural rule (any hidden
  segment) removes it without enumerating.

## The seam trap this module exists to avoid (Gate-1 F-NEW-1 / F-NEW-2)

The dot-segment scan MUST run on the **tree-relative** path, NEVER the absolute
path — the whole workspace lives under ``~/.swarm-ai/``, so ``.swarm-ai`` is a
dot-segment of EVERY absolute path; scanning an absolute path drops every
deliverable (this exact bug was caught + reverted 2026-08-02, then nearly
re-landed twice in planning). So this module does its OWN owning-tree resolution
+ relativization (reusing ``ddd_bindings.load_bindings``) and never trusts a
pre-computed "relative" string from elsewhere.

Fail-safe: NEVER raises (runs on the streaming hot path). On any internal error,
returns ``review_worthy=False, kind="process"`` (the safe non-surfacing verdict —
an error must not crash the turn, and defaulting to "don't pop" on error avoids a
storm of false pops from a broken git). This same non-surfacing default is what
makes the git-error fail-CLOSED path (above) consistent: an unknowable path is
never surfaced, whether the unknowability comes from a crash or from git erroring.
"""
from __future__ import annotations

import functools
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

__all__ = ["needs_human_review", "ReviewVerdict", "Kind"]

# `source-final` (run_b8ea6d5c): the finish-time PR-review batch kind. Minted ONLY
# by the orchestrator's surface_run_outputs observe-emit path (never by this
# classifier), so a mid-run source edit is still `source` (suppressed) — this kind
# exists so the frontend rail can ACCEPT the finish batch while keeping mid-run
# `source` dropped. It never appears as a _classify_kind return.
Kind = Literal["content", "knowledge", "source", "source-final", "process"]


@dataclass(frozen=True)
class ReviewVerdict:
    """The verdict for one file change.

    - ``review_worthy`` — does a human care about this change?
    - ``kind`` — HOW to surface it (see AC4 precedence): ``content``/``knowledge``
      → pop to Canvas; ``source`` → aggregate into the pipeline-finish local PR
      (not per-file mid-run); ``process`` → never surface.
    - ``repo`` — the bound-repo name if the path is inside a bound worktree, else
      ``None`` (it's SwarmWS content).
    """

    review_worthy: bool
    kind: Kind
    repo: Optional[str] = None


# ── knowledge-doc detection (AC4 rule 2 — CHECKED BEFORE source) ──────────────
# These SwarmWS docs are the human-facing knowledge store. `.md` here = knowledge;
# the sibling `.json/.jsonl` machine-state is already killed by the dot-segment /
# check-ignore layers.
_KNOWLEDGE_BASENAMES = {"MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md"}  # PROJECTS.md removed 2026-08-14


def _is_surfaceable_knowledge(rel_path: str) -> bool:
    """PR-review surface allowlist (run_b8ea6d5c): a dot-dir-resident file that IS a
    user-facing deliverable. Whole-path rule, checked AHEAD of the dot-segment +
    check-ignore blocks. run_4de279ca: this is now the SOLE copy — the former
    file_change_classifier._is_surfaceable_knowledge duplicate was retired when the
    orchestrator relevance gate it fed was removed (git verdict is the one authority)."""
    parts = [p for p in Path(rel_path).parts if p not in (".", "")]
    base = parts[-1] if parts else ""
    if base in _KNOWLEDGE_BASENAMES and ".context" in parts:
        return True
    if base == "REPORT.md" and ".artifacts" in parts and "runs" in parts:
        return True
    return False


def _has_dot_segment(rel_path: str) -> bool:
    """True if ANY path segment of the TREE-RELATIVE path is dot-prefixed (hidden).

    MUST be called with a tree-relative path — see the module docstring's seam
    trap. `.artifacts`, `.context`, `.git`, and any hidden dir/file are system by
    definition. A dot in a FILENAME stem (``2026-08-03-foo.md``) is NOT a hidden
    segment — only a segment that *starts* with ``.`` counts.
    """
    return any(seg.startswith(".") for seg in Path(rel_path).parts if seg not in (".", ""))


@functools.lru_cache(maxsize=1)
def _worktree_roots(swarmws_root_str: str) -> tuple[tuple[str, str], ...]:
    """Enumerate (worktree_abs, repo_name) for every bound repo with a real
    on-disk worktree, across all ``Projects/*/bindings.yaml``.

    Cached (bindings change rarely). Fail-safe: any load error on one project is
    skipped, not raised. ``worktree=None`` bindings (deferred/internal) are
    skipped. Returns a tuple (hashable, cacheable).
    """
    from core.ddd_bindings import load_bindings  # local import: avoid import cycle

    roots: list[tuple[str, str]] = []
    projects_dir = Path(swarmws_root_str) / "Projects"
    if not projects_dir.is_dir():
        return ()
    for project in sorted(projects_dir.iterdir()):
        if not project.is_dir():
            continue
        bindings_file = project / "bindings.yaml"
        if not bindings_file.is_file():
            continue
        try:
            doc = load_bindings(bindings_file)
        except Exception:  # noqa: BLE001 — fail-safe: a bad one project never breaks classification
            continue
        for b in doc.bindings:
            wt = getattr(b, "worktree", None)
            if not wt:  # None or "" → deferred/internal, no on-disk tree
                continue
            try:
                wt_abs = str(Path(os.path.expanduser(wt)).resolve())
            except (OSError, ValueError):
                continue
            if Path(wt_abs).is_dir():
                roots.append((wt_abs, b.repo))
    # Longest path first so a nested worktree wins over an ancestor.
    roots.sort(key=lambda pr: len(pr[0]), reverse=True)
    return tuple(roots)


def clear_worktree_cache() -> None:
    """Drop the bindings/worktree cache (call after a bind/unbind mutation)."""
    _worktree_roots.cache_clear()


def _owning_tree(abs_path: Path, swarmws_root: Path) -> tuple[Path, str, Optional[str]] | None:
    """Return (tree_root, tree_relative_path, repo_name) for ``abs_path``.

    Checks bound-repo worktrees FIRST (longest-match, so a worktree nested under
    SwarmWS would win), then SwarmWS itself. ``repo_name`` is the bound repo's
    name when the path is inside a worktree, else ``None`` (SwarmWS content).
    Returns ``None`` if the path is outside every known tree.
    """
    for wt_abs, repo in _worktree_roots(str(swarmws_root)):
        wt_root = Path(wt_abs)
        try:
            rel = abs_path.relative_to(wt_root)
            return wt_root, str(rel), repo
        except ValueError:
            continue
    try:
        rel = abs_path.relative_to(swarmws_root)
        return swarmws_root, str(rel), None
    except ValueError:
        return None


def _git_ignored(tree_root: Path, rel_path: str) -> bool | None:
    """``git check-ignore`` for ``rel_path`` inside ``tree_root``.

    Returns True if IGNORED (exit 0), False if NOT-IGNORED (exit 1), or ``None``
    if git errored (locked/absent/other) — the caller decides the fail direction
    for None (run_a18d69f5 #5: callers now fail-CLOSED on None — an unknowable path
    might be a gitignored secret, so it is NOT surfaced; WARN-logged, never silent).
    """
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-q", "--", rel_path],
            cwd=str(tree_root),
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 0:
        return True   # ignored
    if r.returncode == 1:
        return False  # not ignored
    return None       # 128 etc → git error


def _git_ignored_batch(
    tree_root: Path, rel_paths: list[str], deadline=None
) -> dict[str, bool | None]:
    """Batch ``git check-ignore --stdin`` for MANY rel_paths inside ONE tree_root.

    ONE subprocess for all paths (vs N single calls — the run_4de279ca perf fix).

    Parsing contract (Gate-1 pinned): ``git check-ignore --stdin -z`` prints ONLY
    the paths that ARE ignored (a not-ignored input produces NO output line). So
    the mapping is SET-MEMBERSHIP, never index-align. Exit-code contract:
      - rc 0 = at least one ignored, rc 1 = none ignored → BOTH success
      - rc 128 / OSError / timeout → git error → every path maps to None
        (the caller fails-CLOSED on None per run_a18d69f5 #5: an unknowable path
        might be a gitignored secret, so it is NOT surfaced — WARN-logged, and a
        real deliverable re-emits on the next turn-end sweep)

    ``deadline`` (run_a18d69f5 #2): a duck-typed object with ``.git_timeout()`` (the
    run_surface_changes._Deadline) that caps this call at the REMAINING shared budget
    so a serial multi-tree sweep self-terminates near the total budget. None = the
    default 15s per-call ceiling. Kept duck-typed (no type import) to avoid a circular
    import — run_surface_changes imports THIS module.

    Returns {rel_path: True(ignored) | False(not) | None(git-errored)}.
    """
    if not rel_paths:
        return {}
    _git_to = deadline.git_timeout() if deadline is not None else 15.0
    if _git_to <= 0:
        return {rel: None for rel in rel_paths}  # budget spent → None (caller fails CLOSED, #5)
    try:
        r = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            cwd=str(tree_root),
            input=("\x00".join(rel_paths) + "\x00").encode("utf-8"),
            capture_output=True,
            timeout=_git_to,
        )
    except (OSError, subprocess.SubprocessError):
        return {rel: None for rel in rel_paths}
    if r.returncode not in (0, 1):
        # 128 etc → git error → None for all (caller fails CLOSED per #5, same as single-path)
        return {rel: None for rel in rel_paths}
    ignored_set = {s for s in r.stdout.decode("utf-8", "replace").split("\x00") if s}
    return {rel: (rel in ignored_set) for rel in rel_paths}


def _classify_kind(rel_path: str, repo: Optional[str]) -> Kind:
    """AC4 precedence (order matters):
    (1) dot-segment → process; (2) knowledge md → knowledge (BEFORE source, so a
    DDD doc inside a bound worktree is knowledge not source); (3) inside a bound
    worktree → source; (4) else → content.
    """
    if _has_dot_segment(rel_path):
        return "process"
    parts = Path(rel_path).parts
    base = parts[-1] if parts else ""
    is_md = base.endswith(".md")
    if is_md and (base in _KNOWLEDGE_BASENAMES or "2-understanding" in parts):
        return "knowledge"
    if repo is not None:
        return "source"
    return "content"


def needs_human_review(
    path: str,
    operation: str = "written",
    context: Optional[dict] = None,
    *,
    swarmws_root: Optional[str | Path] = None,
) -> ReviewVerdict:
    """THE authority: should a human review this file change, and how to surface it.

    Two-layer predicate (see module docstring). Fail-safe: never raises; on any
    internal error returns ``review_worthy=False, kind="process"``.

    ``path`` may be absolute OR SwarmWS-relative — this function resolves the
    owning tree itself and relativizes to it (NEVER trusts a pre-computed
    "relative" string; see the F-NEW-2 seam trap).
    """
    try:
        if not path or "\x00" in path:
            return ReviewVerdict(False, "process")

        if swarmws_root is not None:
            ws_root = Path(swarmws_root)
        else:
            from core.project_registry import get_swarmws
            ws_root = Path(get_swarmws())
        ws_root = ws_root.resolve()

        abs_path = Path(os.path.expanduser(path))
        if not abs_path.is_absolute():
            abs_path = (ws_root / path)
        try:
            abs_path = abs_path.resolve()
        except (OSError, RuntimeError):
            return ReviewVerdict(False, "process")

        owning = _owning_tree(abs_path, ws_root)
        if owning is None:
            # Outside every known tree — not our concern (NOT fail-open junk).
            return ReviewVerdict(False, "process")
        tree_root, rel_path, repo = owning

        # PR-review surface allowlist (run_b8ea6d5c): a few knowledge/report docs
        # live UNDER dot-dirs (.context/, .artifacts/runs/) but ARE user-facing
        # deliverables reviewed on every change. They must escape BOTH the
        # dot-segment (Layer 2) and check-ignore (Layer 1) blocks below. Whole-path
        # rule. run_4de279ca: this is the SOLE allowlist (the former duplicate in
        # file_change_classifier was retired). Narrow: exact basenames + REPORT.md
        # under a run dir only.
        if _is_surfaceable_knowledge(rel_path):
            return ReviewVerdict(True, "knowledge", repo)

        # Layer 2: dot-segment on the TREE-RELATIVE path (the seam trap fix).
        if _has_dot_segment(rel_path):
            return ReviewVerdict(False, "process", repo)

        # Layer 1: git check-ignore in the OWNING tree, on the RELATIVE path.
        ignored = _git_ignored(tree_root, rel_path)
        if ignored is True:
            return ReviewVerdict(False, "process", repo)
        if ignored is None:
            # git ERRORED on a resolved path → fail-CLOSED (run_a18d69f5 #5): we
            # can't tell a gitignored secret from a deliverable, so don't surface.
            # WARN (not silent) so a persistently broken git is visible (GUI98).
            logger.warning(
                "needs_human_review: git check-ignore errored for %s in %s — "
                "failing CLOSED (not surfaced); a real deliverable will re-emit "
                "on the next turn-end sweep.",
                rel_path, tree_root,
            )
            return ReviewVerdict(False, "process", repo)
        # ignored is False (not-ignored) → review-worthy (the safe direction on a
        # NON-error verdict: an extra surface, never a dropped piece of work).
        kind = _classify_kind(rel_path, repo)
        return ReviewVerdict(True, kind, repo)
    except Exception:  # noqa: BLE001 — hot-path fail-safe, never crash the turn
        return ReviewVerdict(False, "process")


def needs_human_review_batch(
    paths: list[str],
    operation: str = "written",
    *,
    swarmws_root: Optional[str | Path] = None,
    deadline=None,
) -> dict[str, ReviewVerdict]:
    """Batch authority: verdict for MANY paths with ONE check-ignore subprocess PER
    TREE (the run_4de279ca hot-path fix — replaces N per-file subprocesses).

    Semantically IDENTICAL to calling ``needs_human_review`` per path (same
    precedence: surfaceable-knowledge → dot-segment → check-ignore → kind; same
    fail-CLOSED on git error per #5 — an unknowable path is NOT surfaced, WARN-logged;
    same fail-safe never-raise). Returns {input_path:
    ReviewVerdict} keyed by the ORIGINAL input string (so callers map back
    directly). A path that errors individually gets a process verdict, never
    sinking the batch.
    """
    out: dict[str, ReviewVerdict] = {}
    if not paths:
        return out
    try:
        if swarmws_root is not None:
            ws_root = Path(swarmws_root).resolve()
        else:
            from core.project_registry import get_swarmws
            ws_root = Path(get_swarmws()).resolve()
    except Exception:  # noqa: BLE001
        return {p: ReviewVerdict(False, "process") for p in paths}

    # Phase 1: resolve owning tree + apply the pre-check-ignore layers (surfaceable
    # allowlist, dot-segment) per path. Collect the survivors that still need a
    # check-ignore verdict, GROUPED BY owning tree so each tree batches once.
    #   pending[tree_root] = list of (input_path, rel_path, repo)
    pending: dict[Path, list[tuple[str, str, Optional[str]]]] = {}
    for p in paths:
        try:
            if not p or "\x00" in p:
                out[p] = ReviewVerdict(False, "process")
                continue
            abs_path = Path(os.path.expanduser(p))
            if not abs_path.is_absolute():
                abs_path = ws_root / p
            try:
                abs_path = abs_path.resolve()
            except (OSError, RuntimeError):
                out[p] = ReviewVerdict(False, "process")
                continue
            owning = _owning_tree(abs_path, ws_root)
            if owning is None:
                out[p] = ReviewVerdict(False, "process")
                continue
            tree_root, rel_path, repo = owning
            if _is_surfaceable_knowledge(rel_path):
                out[p] = ReviewVerdict(True, "knowledge", repo)
                continue
            if _has_dot_segment(rel_path):
                out[p] = ReviewVerdict(False, "process", repo)
                continue
            pending.setdefault(tree_root, []).append((p, rel_path, repo))
        except Exception:  # noqa: BLE001 — one bad path never sinks the batch
            out[p] = ReviewVerdict(False, "process")

    # Phase 2: ONE batch check-ignore per tree, then classify_kind on survivors.
    for tree_root, entries in pending.items():
        rel_list = [rel for (_p, rel, _repo) in entries]
        ignored_map = _git_ignored_batch(tree_root, rel_list, deadline)
        for (p, rel_path, repo) in entries:
            ignored = ignored_map.get(rel_path)
            if ignored is True:
                out[p] = ReviewVerdict(False, "process", repo)
            elif ignored is None:
                # git ERRORED → fail-CLOSED (run_a18d69f5 #5): unknowable path is
                # never surfaced (a gitignored secret must not leak); WARN, not
                # silent (GUI98). A real deliverable re-emits next turn-end sweep.
                logger.warning(
                    "needs_human_review_batch: git check-ignore errored for %s in "
                    "%s — failing CLOSED (not surfaced).",
                    rel_path, tree_root,
                )
                out[p] = ReviewVerdict(False, "process", repo)
            else:
                # False (not-ignored) → review-worthy (safe direction, non-error).
                out[p] = ReviewVerdict(True, _classify_kind(rel_path, repo), repo)
    return out
