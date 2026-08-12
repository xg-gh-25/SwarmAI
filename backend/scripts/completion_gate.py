"""completion_gate — the PURE decision for the pipeline completion "did you
finish delivery?" gate (commit + Canvas-surface enforcement).

WHY THIS EXISTS (run_0851350b): the completion gate in artifact_cli.py used to
key its "was source surfaced to Canvas?" check off ``run_state.commits`` — a
field written ONLY by ``run-commit``. So a run whose source was HAND-committed
(``git commit`` directly, commits stays empty) was mis-classified as a
docs-only run and skipped BOTH enforcements: no commit-record check, no
Canvas-surface check. Two real runs (run_3e0672d2, run_2c89bc8d) completed that
way — no auto-commit, no Canvas open — the N-th recurrence of C045.

ROOT FIX: key the gate off ``files_touched`` — the BUILD ground truth of what
source the run actually WROTE (run-commit uses it for ``git add -- <exactly
these>``; DDD docs are NOT in it, they surface via a separate immediate rail).
So ``files_touched`` non-empty == this run did committable source work, which is
the correct, classifier-free trigger:

  - source work + NO commits recorded  -> BLOCK ("run-commit first")
  - source work + committed + NOT surfaced -> BLOCK ("surface_run_outputs")
  - source work + committed + surfaced -> OK
  - no source work (docs/knowledge-only) -> OK (never gated)
  - files_touched UNKNOWN (legacy/in-flight resume) -> OK+warn (never false-block)

Pure: no I/O, no run.json read. artifact_cli.py passes the already-loaded state
in and acts on the verdict. Keeps the gate unit-testable (RED/GREEN) instead of
buried in the CLI command.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompletionVerdict:
    ok: bool
    # "uncommitted_source" | "unsurfaced_source" | None (ok)
    block_reason: str | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    committed_source_files: list[str] = field(default_factory=list)


def _tail_match(a: set[str], b: set[str]) -> bool:
    """True if any path in `a` and any in `b` refer to the same file, matching a
    repo-relative path against an absolute/relative one by a DIRECTORY-anchored
    suffix (never a bare basename, so a/config.py != b/config.py). Mirrors the
    original artifact_cli logic so run-scoping behaviour is unchanged."""
    for x in a:
        for y in b:
            if x == y:
                return True
            if "/" in x and y.endswith("/" + x):
                return True
            if "/" in y and x.endswith("/" + y):
                return True
    return False


def completion_surface_verdict(
    files_touched: list[str] | None,
    commits: list[dict] | None,
    deliver_surfaced: bool,
    untrackable_source: list[str] | None = None,
) -> CompletionVerdict:
    """Decide whether a run may be marked completed w.r.t. commit + Canvas-surface.

    files_touched: the source files THIS run WROTE (BUILD ground truth). ``None``
        means the field was never recorded (legacy / in-flight resume) — treated
        as UNKNOWN (never a hard block, to avoid breaking resumes), distinct from
        ``[]`` which means "explicitly no source work" (docs/knowledge-only).
    commits: run_state.commits (written by run-commit); each {files:[...]}.
    deliver_surfaced: the deliver stage recorded outputs_surfaced (or legacy
        local_pr_surfaced) == True.
    untrackable_source: the subset of files_touched that git CANNOT commit because
        they are gitignored (run_e0aa14f7 — a run in a gitignored project, e.g.
        CMHK_SalesIntel per STEERING #5 where only Projects/SwarmAI/ is trackable).
        The caller computes this via ``git check-ignore`` per file, FAIL-SAFE: any
        check-ignore error → the file is treated as trackable (NOT listed here) so
        it still blocks — never fail-open. ``None``/``[]`` → nothing untrackable
        (every existing caller keeps its exact prior behaviour).

        These files are excluded from the COMMIT check ONLY — git can't commit
        them, so demanding a commit is impossible. They are NOT excluded from the
        SURFACE check: gitignored SOURCE is real produced output (not docs-only),
        so it must still be surfaced to the Canvas OUTPUTS rail, or a run could
        produce work and be marked complete with nothing to review (the C045
        bypass this gate exists to prevent).
    """
    # UNKNOWN — legacy/in-flight run that predates files_touched recording.
    # Never hard-block (resume safety); nudge instead.
    if files_touched is None:
        return CompletionVerdict(
            ok=True,
            warnings=["files_touched not recorded — cannot verify commit/surface "
                      "for this run (legacy/in-flight). BUILD should record "
                      "written files so the completion gate can enforce delivery."],
        )

    source_files = [f for f in files_touched if isinstance(f, str) and f.strip()]

    # No source work → docs/knowledge-only run → never gated.
    if not source_files:
        return CompletionVerdict(ok=True)

    # Exclude git-untrackable (gitignored) source from the COMMIT check ONLY — git
    # cannot commit these, so demanding a commit deadlocks the run (run_e0aa14f7).
    # `trackable_source` drives the commit check; the FULL `source_files` still
    # drives the surface check below (gitignored source is real output to review).
    #
    # EXACT membership, NOT _tail_match (Gate-2 attack#3): untrackable_source is
    # built FROM files_touched by the caller, so it carries the SAME string
    # representation as source_files — a plain set-membership is correct AND
    # collision-free. _tail_match's dir-anchored suffix logic is for reconciling
    # DIFFERENT representations (repo-relative commit vs absolute files_touched)
    # and here would WRONGLY drop a trackable relative path that is a dir-suffix of
    # a gitignored absolute path (e.g. "a/x.py" vs "/repo/a/x.py") → C045 hole.
    untrackable = {f for f in (untrackable_source or []) if isinstance(f, str) and f.strip()}
    trackable_source = [f for f in source_files if f not in untrackable]

    committed_files: set[str] = set()
    for c in (commits or []):
        if isinstance(c, dict):
            for f in (c.get("files") or []):
                committed_files.add(f)

    # TRACKABLE source work but nothing committed → the hand-commit / no-commit
    # bypass. (Also fires when commits exist but none intersect THIS run's source —
    #  i.e. only sibling-session commits.) Skipped when trackable_source is empty
    # (a gitignored-only run has nothing git could commit — fall through to surface).
    if trackable_source:
        run_scoped_committed = bool(committed_files) and _tail_match(committed_files, set(trackable_source))
        if not run_scoped_committed:
            return CompletionVerdict(
                ok=False,
                block_reason="uncommitted_source",
                message=("Cannot mark completed: this run wrote source files but did "
                         "not commit them via run-commit (commit is part of pipeline "
                         "delivery, never a user question — C045). Run: "
                         "artifact_cli.py run-commit --project <P> --run-id <R>"),
                committed_source_files=sorted(committed_files),
            )

    # Committed but not surfaced to Canvas → the existing surface gate.
    if not deliver_surfaced:
        return CompletionVerdict(
            ok=False,
            block_reason="unsurfaced_source",
            message=("Cannot mark completed: this run committed source but did not "
                     "surface it to the Canvas OUTPUTS rail. Call the "
                     "surface_run_outputs tool with this run_id, then record "
                     'deliver.outputs_surfaced=true.'),
            committed_source_files=sorted(committed_files),
        )

    return CompletionVerdict(ok=True, committed_source_files=sorted(committed_files))
