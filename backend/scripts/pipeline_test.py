"""Single test entry point for autonomous-pipeline runs (design §4-B and §4-G2).

The runner, not the model, decides the test scope from the change set. It runs
that scope sharded and in parallel under one global deadline, classifies every
failure against a HEAD baseline (B2), caches results by input digest, and writes
structured evidence that ``stage-complete`` reads. One call per mode; zero model cost.

Modes:
- ``tdd``        only the explicit ``--files`` (max 5); repeat counter per file
- ``verify``     changed test files + recent failures + every direct importer of a changed
                 module (transitive importers are named in REPORT §10, not run) + tests
                 naming a changed (or renamed / deleted) backend/scripts/*.py file; all of
                 it runs, across as many calls as it takes (``continue: true``)
- ``regression`` L1 ac_coverage tests + L2 dependency scope + import smoke + the CI BVT
                 manifest (``tests/bvt_manifest.txt``, the same files CI runs)
- ``recheck``    previous evidence failures + tests affected by files changed since
- ``probe``      G2 heavy probe with convergence rules (``--name N -- argv...``)
- ``mutation``   mutation testing in a throwaway worktree (``--mutations spec.json``)

Usage (from the repo root or backend/):
    perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py \\
        --project P [--run-id R] --mode MODE [--files F ...] [--deadline 95] [--json]

``--deadline`` defaults to 95 s so the runner reports before the documented
``alarm 110`` wrapper, inside the Bash tool's 120 s default. A selection that does not
fit is never cut: the output says ``continue: true`` with the ``unfinished`` count, and
the SAME command runs only those tests next (repeat until ``continue`` is gone). The test
modes refuse a deadline above 100 s (it would outlive ``alarm 110`` and die before
reporting); only ``probe`` / ``mutation`` (one indivisible check) take up to 575 s, with
the outer alarm at deadline + 10 s and the Bash tool timeout raised to match. SIGTERM / SIGALRM /
SIGINT abort every running child process group, remove any private or
half-created worktree (``git worktree remove --force`` + ``prune``) and print one
JSON error object (exit 2). Worktrees are added with ``core.hooksPath=/dev/null``.

Prints ONE compact JSON object. Exit codes:
- 0 = green (or only pre-existing / flaky failures, none of them a timeout)
- 1 = failures, any timeout (a GLOBAL-deadline kill is classified ``timeout``: not a
      code failure, but unverified — re-run the same command), a selected file /
      nodeid that collected zero tests, a shard that exited abnormally
      (``runner_errors``), or new probe findings
- 2 = usage error, refusal, runner error, or interrupted by a signal

Cache soundness (H1): the result-cache key covers HEAD, the selected tests, the
TRANSITIVE closure of backend-local imports of the tests, every conftest.py on their
path (and its closure), pytest config files, the smoke modules (and closure), and the
content of every changed file. A result with a timeout, an ``unknown``
classification or a runner error is never cached.

Per-file reuse (verify / regression, ``_l2_file_keys`` / ``_reusable``): an L2 test file
whose every result passed (or skipped) in this run's latest verify or regression is not
re-run when its per-file key is unchanged; its earlier results are merged in, listed in
``reused_from`` and named in a warning. The key covers the runner version, interpreter,
pytest configs (not HEAD: a main commit outside a file's closure keeps it), the file, its conftest chain and their transitive imports, and
every changed file (the whole-run key's input set: a module can load a changed file
without an import edge). So reuse serves re-runs on an unchanged tree: verify then
regression, a call that was not cacheable, a different selection. AC (L1) files,
nodeid-scoped targets and files with any failure are always re-run. Counts and
``layers.dependency_scoped`` include reused results (``reused_from.tests`` says how
many). Every verify / regression evidence records ``l2_file_keys``; an overlay drops the
keys of the files its recheck re-ran.

Classification (B2, see ``classify_failures``): a test that ran and failed and also
fails at HEAD (HEAD outcomes cached per sha + interpreter) is pre-existing at once, with
no re-run. Any other failing test's whole FILE is re-run
in its original order (catches order-dependence inside the file). When that passes,
its shard PREFIX (every shard arg before it, plus its own, original order) is re-run
too, catching state leaked by another file of the same shard: a reproduced failure is
``order_dependent`` (evidence also names a bisected ``culprit`` file when time allows)
and is a regression / pre-existing by the same prefix at HEAD, never flaky. The HEAD
baseline only records tests that reached a call report; a not-run test is ``unknown``
at HEAD (never cached, never pre-existing). flaky = file AND prefix re-runs pass AND
it passed at HEAD; still failing → pre-existing (failed at HEAD) / regression (passed
at HEAD); anything else (incl. no time left for a re-run) is ``unknown`` (exit 1).

Evidence (C2) adds ``head_sha``, ``tree_digest`` ({path: sha256} of every changed
input and selected test file, from the current tree also on a cache hit) and
``runner_errors``; failure entries add ``outcome`` and ``deadline``. Size bounds: a
passed ``results[]`` entry is nodeid + outcome (+ duration); nodeids over NODEID_CAP
chars are capped with a sha tag (``nodeid_truncated``). Retention: after each write,
``tests/`` keeps every ``latest-<mode>.json`` plus the newest EVIDENCE_KEEP timestamped
reports and log dirs per mode (``_prune_evidence``). Nodeids named by an explicit
ac_coverage / exit_criteria nodeid ref are never capped when capping would break the match.

Run index (``tests/run_index.json``): retention prunes the timestamped files, so COUNT
test runs from this file instead. A JSON list, append-only, never pruned, rewritten
atomically (tmp + replace) under ``tests/.run_index.lock``; one object per evidence write:
- ``kind``: ``"run"`` (a test-mode invocation, incl. a cache hit) or ``"overlay"`` (a
  recheck laid over verify / regression evidence: a rewrite, NOT another run)
- ``mode``: the evidence mode written (for an overlay: the base mode)
- ``finished_at``: the evidence's ``finished_at`` (ISO-8601 UTC, 1 s resolution)
- ``ts``: epoch seconds (float) at write time — compare to a stage-start epoch
- ``evidence``: the timestamped file name written (may since have been pruned)
- ``cached``: bool, a result-cache hit
- ``auto_recheck_of``: the mode whose identical re-run became this recheck, else null
- ``rechecked_from``: the base evidence ``finished_at`` a recheck / overlay built on, else null
- ``blocking_sig``: sorted nodeids of the evidence's blocking failures (``_blocking_sig``);
  the circuit breaker compares it across rounds to detect a loop making no progress
- ``unfinished``: tests the deadline left unrun; a continuation's progress (T5)
- ``unfinished_files`` / ``desktop_done``: files with unrun tests / desktop test files with a
  result — progress too (vitest reports its unrun rest as ONE placeholder, not a count)
- ``fix`` (a manual recheck only): true when it ran on code changed since the report it
  rechecks; the fix budget past the full-scope cap counts these (_full_scope_refusal)
An automatic recheck writes two entries: ``kind=run, mode=recheck, auto_recheck_of=M``
and ``kind=overlay, mode=M``.

Order-dependent rechecks: an ``order_dependent`` failure records its shard ``prefix``
(ordered args incl. ``--deselect=`` tokens, ≤ PREFIX_KEEP_ARGS). recheck re-runs it as
ONE ordered job (prefix, else ``[culprit, own]``) and lists it in ``od_in_context``; the
overlay never replaces an order_dependent failure with a pass that was not run in context.

Environment overrides (used by tests): SWARM_TEST_REPO_ROOT, SWARM_TEST_PYTHON,
SWARM_TEST_CACHE_DIR, SWARM_TEST_NPX, SWARM_WORKSPACE.

Public symbols:
- ``main(argv)`` — CLI entry; prints the JSON object, returns the exit code
- ``execute(ns)`` — one invocation, returns ``(output_dict, exit_code)``
- ``changed_files(repo, run_state, warnings, run_ref)`` — the run's files_touched when it has
  any in this repo (other changed files named in warnings); else git diff HEAD ∪ untracked
  ∪ the normalized entries
- ``build_source_graph(repo, cache_dir)`` — every backend .py file → imported modules (cached)
- ``build_import_graph(repo, cache_dir)`` — test file → imported module names (cached)
- ``import_closure(repo, sgraph, roots)`` — transitive backend-local file dependencies
- ``reverse_distances(repo, sgraph, roots, depth)`` — file → import distance to the roots
- ``select_backend(repo, changed, graph, cap, recent, sgraph)`` — scoped selection + overflow
- ``classify_failures(ctx, results)`` — new / flaky / pre-existing / regression / unknown
- ``_run_v1_probes`` — ``--mode probe``: a v1 run's DESIGN_SPEC probe exits (§A2.5);
  ``run_mutations(ctx, ns, ev_root, run_state)``
"""
from __future__ import annotations

import argparse
import ast
import glob
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import warnings as _warnings
import xml.etree.ElementTree as ET  # noqa: S405 — parses our own junit output only
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from utils.file_lock import flock_exclusive_nb, flock_unlock  # noqa: E402
from core.gate_force import force_enabled  # noqa: E402

RUNNER_VERSION = "2"
TEST_MODES = ("tdd", "verify", "regression", "recheck")
# Circuit breaker: stop a non-converging build<->verify loop from spinning forever.
# The loop is agent/skill-driven (prose caps only), so run_340478c9 ran 42 verify
# rounds over ~18h. Counted from the append-only run_index (per run_id). A hard ceiling
# catches runaway re-runs; a stall detector catches "same blocking failures N rounds
# running = not making progress". Operator override: SWARM_GATE_FORCE=circuit_breaker.
CIRCUIT_MAX_ROUNDS = 25    # total verify/recheck/regression runs in one run_id -> abort
CIRCUIT_STALL_ROUNDS = 4   # consecutive verify/recheck runs with the SAME blocking set -> abort
# goal profile: the cycle loop's stop rules are code, not card prose (run_a2f2c171 ran 236
# tdd calls / 232 min). tdd is the per-cycle RED/GREEN step, so its count bounds the loop.
GOAL_TDD_MAX = 80
ALL_MODES = TEST_MODES + ("validation", "probe", "mutation", "snapshot", "prove")
PROOF_MODES = ("snapshot", "prove")
TDD_MAX_FILES = 5
SMOKE_CAP = 40
TDD_REPEAT_WARN = 8
# verify selection: every test that imports a changed module directly. Depth 2 (test → module
# → changed module) selected 103-463 files for 21 changed ones (run_340478c9, run_7da44b0d).
REVERSE_DEPTH = 1
WRAPPER_ALARM_S = 110  # the documented perl alarm: under the Bash tool's 120 s default
DEFAULT_DEADLINE = 95.0
SAFE_DEADLINE = WRAPPER_ALARM_S - 10.0
MAX_DEADLINE = 575.0  # probe / mutation only: one Bash call is capped at 600 s
# Every call stays inside the 120 s Bash default (alarm 110, deadline 95). A selection
# that does not fit is not cut and not refused: the unfinished tests are reported
# (``unfinished``, ``continue: true``) and the SAME command runs only those next time
# (_auto_recheck), so a large scope takes several short calls, never one long one.
# Cross-run throttle: one machine-wide pool of worker tokens (``<cache>/workers/N.lock``,
# flock) shared by every runner of every session. A runner holds as many tokens as it
# runs workers, so concurrent runs SPLIT the cores instead of each sizing itself as if
# alone (the old 2-slot semaphore let two runners each start 4+ workers plus a vitest
# pool, and a third "ran anyway" after 20 s). Deadline timeouts measured 2026-10-19 on 427
# runner calls: 8 % with no other runner overlapping, 15 % with one, 23 % with two or more.
# Budget = cpu - WORKER_BUDGET_RESERVE: room for the model, the daemon, the IDE and the
# always-on endpoint agents (FortiDLP / Ava / Jamf measured at 3-4 of 12 cores).
WORKER_BUDGET_RESERVE = 4
SLOT_WAIT_S = 20.0
SUMMARY_LINES = 10
STACK_LINES = 40
OUTPUT_BUDGET = 3000
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Desktop vitest files an ac_coverage ref may name (file-level only, see _resolve_desktop_ref).
_DESKTOP_TEST_RE = re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx)$")
# Suites known to deadlock when sharing a process with others: one shard each.
_SERIAL_SUITES = ("session_unit", "lifecycle", "healing", "recovery", "context_health")
# pytest-timeout per test (thread method: a test over it ends its whole shard process).
PER_TEST_TIMEOUT_S = 30
# Per-nodeid duration history for cost-based shard packing (<cache>/durations.json).
MAX_DURATION_ENTRIES = 20000
DEFAULT_TEST_COST_S = 1.0    # a nodeid with no recorded duration
DEFAULT_FILE_COST_S = 10.0   # a whole file with no recorded test
DURATION_TTL_S = 30 * 86400  # an id not measured for 30 days (deleted / renamed) ages out
# A shard ended by a per-test timeout gets its not-run tests requeued ONCE, if at least
# this much of the phase budget is left. One round only: a module-level hang would time
# out every test in the file, and repeated rounds would spend the deadline on it.
REQUEUE_MIN_S = 5.0
_FAIL = ("failed", "error", "timeout")
_COUNT_KEY = {"passed": "passed", "failed": "failed", "error": "errors",
              "timeout": "timeouts", "skipped": "skipped"}


# ── small utilities ─────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_digest(path: Path, limit: int = 8 * 1024 * 1024) -> str:
    try:
        if path.stat().st_size > limit:
            st = path.stat()
            return f"big:{st.st_size}:{st.st_mtime_ns}"
        return _sha(path.read_bytes())
    except OSError:
        return "missing"


def _read_json(path: Path):
    """A JSON file, or None. A run's evidence file that its seal rejects (written or
    changed by someone other than this runner: core.evidence_seal) reads as None, so
    a forged report is never merged into, or re-sealed by, a later call."""
    from core import evidence_seal
    try:
        raw, ok, _why = evidence_seal.read_checked(path)
        return json.loads(raw.decode("utf-8")) if ok else None
    except (OSError, ValueError):
        return None


def _index_read(path: Path):
    """(data, tampered) for an append-only counter file (run_index.json, tdd_index.json).
    ``tampered`` names why the runner's seal rejects the file. A rejected index is never
    read as "no history" (that would reset every cap counted from it) and never
    re-sealed (that would make the edited content the runner's): the callers refuse."""
    data = _read_json(path)
    if data is not None or not path.exists():
        return data, None
    from core import evidence_seal
    ok, why = evidence_seal.check(path)
    return None, (None if ok else f"{path.parent.name}/{path.name} is not the runner's ({why})")


def _write_json(path: Path, data) -> None:
    """Atomic write (tmp + replace) so a concurrent reader never sees half a file; a
    run's evidence file is sealed under the same lock (core.evidence_seal)."""
    from core import evidence_seal
    warn = evidence_seal.write_sealed(path, json.dumps(data, indent=1))
    if warn:
        print(json.dumps({"warning": warn}), file=sys.stderr)


def _lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _try_lock(path: Path, timeout: float):
    """Open + non-blocking flock with polling; returns the fd or None on timeout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    end = time.monotonic() + max(0.0, timeout)
    while True:
        fd = open(path, "w")  # noqa: SIM115 — caller releases via _unlock
        try:
            flock_exclusive_nb(fd)
            return fd
        except (BlockingIOError, OSError):
            fd.close()
        if time.monotonic() >= end:
            return None
        time.sleep(0.1)


def _unlock(fd) -> None:
    if fd is not None:
        try:
            flock_unlock(fd)
        finally:
            fd.close()


# ── locations ───────────────────────────────────────────────────────────────
def _git_run(cwd: Path, *args: str, timeout: float = 20.0):
    """List-form git call; returns CompletedProcess or None when git can't run."""
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "core.quotepath=off", *args],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git(cwd: Path, *args: str, timeout: float = 20.0) -> str | None:
    p = _git_run(cwd, *args, timeout=timeout)
    return p.stdout if p is not None and p.returncode == 0 else None


def _repo_root() -> Path:
    env = os.environ.get("SWARM_TEST_REPO_ROOT")
    if env:
        return Path(env).resolve()
    out = _git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
    return Path(out.strip()).resolve() if out and out.strip() else _BACKEND_DIR.parent


def _cache_dir() -> Path:
    env = os.environ.get("SWARM_TEST_CACHE_DIR")
    d = Path(env) if env else Path.home() / ".swarm-ai" / "cache" / "pipeline_test"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def _python(repo: Path, warnings: list) -> str:
    env = os.environ.get("SWARM_TEST_PYTHON")
    if env:
        return env
    cand = repo / "backend" / ".venv" / "bin" / "python"
    if cand.exists():
        return str(cand)
    # A run worktree has no .venv of its own: it uses the main checkout's
    # (pipeline-run-worktree §5.3) — that is the design, not a fallback to warn about.
    main = _BACKEND_DIR / ".venv" / "bin" / "python"
    if _WORKTREE.get("repo") == repo and main.exists():
        return str(main)
    warnings.append(f"no venv python at {cand}; using {sys.executable}")
    return sys.executable


def _workspace() -> Path:
    ws = os.environ.get("SWARM_WORKSPACE")
    if not ws:
        try:
            from config import get_app_data_dir
            ws = str(get_app_data_dir() / "SwarmWS")
        except Exception:  # noqa: BLE001 — standalone use without the backend config
            ws = str(Path.home() / ".swarm-ai" / "SwarmWS")
    return Path(ws)


_WORKTREE: dict = {}  # set by _target_repo for an isolated run: repo, run_id, name


def _target_repo(run_id: "str | None", run_state: dict) -> "tuple[Path | None, str | None]":
    """(repo to test, error). An isolated run is tested in its OWN worktree — validated,
    never falling back to the main checkout (that would test without the run's change,
    a false green; pipeline-run-worktree §6.2). Main's ``backend`` is stripped from the
    children's sys.path (the venv's editable .pth would let a module the run deleted
    still import from main)."""
    main = _repo_root()
    if not run_id or run_state.get("worktree_mode") != "isolated":
        return main, None
    from core import run_worktree as rw
    for e in run_state.get("worktrees") or []:
        if not isinstance(e, dict) or e.get("state") != "active":
            continue
        # The entry OF THIS repo, as git names it (keys are not always the directory
        # name, §16.9). An entry that claims this repo (pinned main or legacy name) but
        # git cannot confirm is refused, never skipped: skipping would test main.
        real_main = os.path.realpath(str(main))
        claims = (os.path.realpath(str(e["main"])) == real_main
                  if e.get("main") else e.get("repo_name") == main.name)
        try:  # git's own answer counts too: a forged pinned main must not hide it
            g = rw.main_of(rw.wt_path(run_id, str(e.get("repo_name"))))
            claims = claims or (g is not None and os.path.realpath(str(g)) == real_main)
        except rw.WorktreeError:
            pass
        try:
            owner = rw.entry_main(run_id, e)
        except rw.WorktreeError as exc:
            if claims:
                return None, f"run worktree invalid: {exc.message}"
            continue
        if os.path.realpath(str(owner)) != os.path.realpath(str(main)):
            if claims:
                return None, ("run worktree invalid: its recorded main checkout is not the "
                              "repo git registered it under")
            continue
        try:
            wt = rw.validate(run_id, e, main)
        except rw.WorktreeError as exc:
            return None, f"run worktree invalid: {exc.message}"
        _WORKTREE.update(repo=wt, run_id=run_id, name=e["repo_name"])
        os.environ["PIPELINE_TEST_STRIP_ROOT"] = os.path.realpath(str(main / "backend"))
        return wt, None
    return main, None  # landed already (or no worktree for this repo): main holds the change


def _worktree_evidence(ctx) -> "dict | None":
    """§7.3: the tree tested and the un-added files present while testing."""
    if _WORKTREE.get("repo") != ctx.repo:
        return None
    from core import run_worktree as rw
    try:
        return {_WORKTREE["name"]: {"tree": rw.change_tree(ctx.repo, _WORKTREE["run_id"]),
                                    "base": _head_sha(ctx.repo),
                                    "untracked": rw.untracked_new_files(ctx.repo, _WORKTREE["run_id"])}}
    except rw.WorktreeError as exc:
        ctx.warnings.append(f"worktree evidence not recorded: {exc.message}")
        return None


def _run_dir(project: str, run_id: str) -> Path:
    if not _ID_RE.fullmatch(run_id) or not _ID_RE.fullmatch(project):
        raise ValueError(f"unsafe project/run id: {project!r} {run_id!r}")
    return _workspace() / "Projects" / project / ".artifacts" / "runs" / run_id


def _head_sha(repo: Path) -> str | None:
    out = _git(repo, "rev-parse", "HEAD")
    sha = (out or "").strip()
    return sha if re.fullmatch(r"[0-9a-f]{40,64}", sha) else None


# ── change set ──────────────────────────────────────────────────────────────
def _norm_path(repo: Path, p: str) -> str | None:
    """Repo-relative POSIX path for an absolute or repo-relative path; None if outside.

    Any ``..`` part is rejected outright (``backend/../../x`` never normalizes into
    the repo), as is an absolute path that does not resolve under ``repo``.
    """
    if not isinstance(p, str) or not p.strip() or "\0" in p:
        return None
    path = Path(p.strip())
    if ".." in path.parts:
        return None
    if path.is_absolute():
        try:
            s = path.resolve().relative_to(Path(repo).resolve()).as_posix()
        except (ValueError, OSError, RuntimeError):
            return None
        return None if s in ("", ".") else s
    s = path.as_posix()
    while s.startswith("./"):
        s = s[2:]
    return None if not s or s == "." else s


def _contained(base: Path, rel: str) -> Path | None:
    """``base/rel`` resolved (symlinks followed) iff it stays strictly under ``base``."""
    if not isinstance(rel, str) or _norm_path(base, rel) != rel:
        return None
    try:
        b = Path(base).resolve()
        p = (b / rel).resolve()
    except (OSError, RuntimeError):
        return None
    return p if b in p.parents else None


def _git_changed_set(repo: Path) -> set[str]:
    """``git diff --name-only HEAD`` ∪ untracked files."""
    out: set[str] = set()
    for args in (("diff", "--name-only", "HEAD"), ("ls-files", "--others", "--exclude-standard")):
        for line in (_git(repo, *args) or "").splitlines():
            if line.strip():
                out.add(line.strip())
    return out


def _repo_state(repo: Path) -> dict[str, str]:
    """{path: digest} of every file git reports changed or untracked right now; a file
    not listed is identical to HEAD. Taken before and after the test jobs of one call."""
    return {p: _file_digest(repo / p) for p in sorted(_git_changed_set(repo))}


def _check_repo_mutation(ctx, before: dict[str, str]) -> dict | None:
    """Did the tests of this call write into the repo under test? A test that rewrites a
    tracked file changes the change set, so the next call gets a new cache key and starts
    every file again instead of continuing (run_bd60daff 2026-10-19: refresh_ai_docs
    rewrote docs/CODEBASE_METRICS.md mid-verify; unfinished went 388 -> 156 -> 771).

    In a run's own isolated worktree only this runner writes while it runs, so a file
    that was identical to HEAD before and differs now is restored from HEAD. Anything
    else is reported, never touched: a file that was already changed (its pre-call
    content is not kept), a new untracked file, or any change in a shared checkout
    (another session may be editing it). Returns None when nothing moved."""
    after = _repo_state(ctx.repo)
    moved = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    if not moved:
        return None
    isolated = _WORKTREE.get("repo") == ctx.repo
    restored: list[str] = []
    left: list[str] = []
    for p in moved:
        tracked_clean_before = p not in before and _git(ctx.repo, "cat-file", "-e", f"HEAD:{p}") is not None
        if isolated and tracked_clean_before and _git(
                ctx.repo, "restore", "--source=HEAD", "--worktree", "--", p) is not None:
            restored.append(p)
        else:
            left.append(p)
    show = lambda xs: ", ".join(xs[:8]) + (f" (+{len(xs) - 8} more)" if len(xs) > 8 else "")
    if restored:
        ctx.warnings.append(
            f"a test in this call wrote {len(restored)} tracked file(s) of the repo: "
            f"{show(restored)}. Restored from HEAD (this run's own worktree), so the next "
            "call still continues. Fix that test: tests must not write the repo they test.")
    if left:
        ctx.warnings.append(
            f"{len(left)} file(s) of the repo changed while this call's tests ran: "
            f"{show(left)}. Not restored ({'already changed before the call or new' if isolated else 'shared checkout'}); "
            "if a test wrote them, the next call sees a different change set and starts over. "
            "Find the test that writes them and fix it, then restore the files.")
    return {"restored": restored, "left": left}


def _owned_files(repo: Path, run_state: dict | None,
                 git_set: set[str]) -> tuple[set[str], set[str]]:
    """(every normalized files_touched entry, the ones that are this repo's)."""
    named: set[str] = set()
    owned: set[str] = set()
    for f in (run_state or {}).get("files_touched") or []:
        n = _norm_path(repo, f)
        if not n:
            continue
        named.add(n)
        # Owned only if git reports it changed (a deletion included): an existing but
        # unchanged entry would replace the real change set (red-team 2026-10-19).
        if n in git_set:
            owned.add(n)
    return named, owned


# A sibling run counts as live when its run.json changed within this window (the same
# 3 h the pipeline test gate uses for "active").
SIBLING_ACTIVE_S = 3 * 3600


def _live_sibling_runs(runs_dir: Path, run_id: str | None) -> list[str]:
    """Ids of OTHER runs under ``runs_dir`` with status ``running`` and run.json
    modified within SIBLING_ACTIVE_S."""
    cutoff = time.time() - SIBLING_ACTIVE_S
    out: list[str] = []
    try:
        entries = sorted(runs_dir.iterdir())
    except OSError:
        return out
    for d in entries:
        rj = d / "run.json"
        if d.name == run_id or not rj.is_file():
            continue
        try:
            if rj.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        st = _read_json(rj)
        if isinstance(st, dict) and st.get("status") == "running":
            out.append(d.name)
    return out


def shared_tree_scope_error(repo: Path, run_state: dict | None, run_id: str | None,
                            project: str, runs_dir: Path) -> str | None:
    """Refuse a verify / regression whose change set would be other runs' work.

    Without a files_touched entry of this repo, ``changed_files`` falls back to the
    whole ``git diff HEAD`` ∪ untracked set. In a main checkout another live run is
    also editing, that set is mostly THEIR files: run_a2f2c171 (goal profile, no
    files_touched yet) selected 70 test files from 44 changed files, half of them a
    sibling's artifact_cli work plus a ``vitest related`` over the whole chat tree, and
    5 verify / recheck calls in a row hit the global deadline. Registering the run's
    own files is one call and makes the selection this run's. An isolated run (own
    worktree) or a run with no live sibling keeps the fallback."""
    if _WORKTREE or not run_id:
        return None
    git_set = _git_changed_set(repo)
    if not git_set or _owned_files(repo, run_state, git_set)[1]:
        return None
    siblings = _live_sibling_runs(runs_dir, run_id)
    if not siblings:
        return None
    shown = ", ".join(sorted(git_set)[:12]) + (f" (+{len(git_set) - 12} more)"
                                               if len(git_set) > 12 else "")
    return (f"this run has no files_touched yet, and the working tree is shared with "
            f"{len(siblings)} other live run(s) ({', '.join(siblings[:4])}): the "
            f"{len(git_set)} changed file(s) here ({shown}) are not all yours, and testing "
            f"all of them does not fit the deadline. Register the files THIS run changed, "
            f"then run the same command again: python backend/scripts/artifact_cli.py "
            f"run-update --project {project} --run-id {run_id} "
            f"--files-touched '[\"<path>\", ...]'")


def changed_files(repo: Path, run_state: dict | None, warnings: list | None = None,
                  run_ref: tuple[str, str] | None = None) -> list[str]:
    """The change set a run's tests are selected for.

    When run.json ``files_touched`` holds at least one file of THIS repo, the change
    set is exactly those files: the same set ``run-commit`` commits and the
    active-run commit gate treats as the run's own. ``git diff HEAD`` and untracked
    files are NOT added — in a working tree shared with other sessions they are
    other people's uncommitted work, and once pulled in a run's verify/regression
    selected 49 test files for 12 touched ones and never reached its own tests
    before the deadline. Changed files outside ``files_touched`` are named in
    ``warnings`` with the command that registers them, because an edit made by a
    shell script is not recorded automatically and its tests would otherwise be
    skipped without a trace.

    A ``files_touched`` entry counts as this repo's only if git reports it changed
    (a deleted file included): an existing unchanged entry, or a relative entry naming a workspace file
    (``Projects/...``) that merely normalizes like a repo path. With no such entry
    (missing, empty, or every entry in another repo) the change set is
    ``git diff --name-only HEAD`` ∪ untracked ∪ the normalized entries, as before.
    """
    git_set = _git_changed_set(repo)
    named, owned = _owned_files(repo, run_state, git_set)
    if not owned:
        return sorted(named | git_set)
    extra = sorted(git_set - owned)
    if extra and warnings is not None:
        project, run_id = run_ref or ("<project>", "<run_id>")
        shown = ", ".join(extra[:8]) + (f" (+{len(extra) - 8} more)" if len(extra) > 8 else "")
        warnings.append(
            f"{len(extra)} changed file(s) outside this run's files_touched were not used "
            f"for test selection: {shown}. If this run changed any of them, register them: "
            f"python backend/scripts/artifact_cli.py run-update --project {project} "
            f"--run-id {run_id} --files-touched '[\"<path>\"]'")
    return sorted(owned)


def _digest_map(repo: Path, files: list[str], cap: int = 2000) -> dict[str, str]:
    return {f: _file_digest(repo / f) for f in files[:cap]}


# ── import graph ────────────────────────────────────────────────────────────
def module_of(rel: str) -> str | None:
    """``backend/core/x.py`` → ``core.x``; ``backend/core/__init__.py`` → ``core``."""
    if not rel.startswith("backend/") or not rel.endswith(".py"):
        return None
    parts = rel[len("backend/"):-3].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def _module_file(repo: Path, mod: str) -> str | None:
    base = repo / "backend" / Path(*mod.split("."))
    for cand in (base.parent / (base.name + ".py"), base / "__init__.py"):
        if cand.is_file():
            return cand.relative_to(repo).as_posix()
    return None


def _pkg_of(rel: str) -> str | None:
    """Package a backend file's relative imports resolve against (None if not a module)."""
    m = module_of(rel)
    if m is None:
        return None
    return m if rel.endswith("__init__.py") else m.rpartition(".")[0]


def _imports_of_source(src: str, pkg: str | None = None) -> list[str]:
    """Absolute module names a file imports.

    ``from a.b import c`` yields both ``a.b`` and ``a.b.c`` (c may be a submodule).
    Relative imports are resolved against ``pkg`` when given, else skipped.
    """
    try:
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")  # e.g. SyntaxWarning for '\;' in a test file
            tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module
            if node.level:
                parts = pkg.split(".") if pkg else []
                if pkg is None or node.level - 1 > len(parts):
                    continue
                parts = parts[:len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            if not base:
                continue
            mods.add(base)
            mods.update(f"{base}.{a.name}" for a in node.names if a.name != "*")
    return sorted(mods)


def _is_backend_test(rel: str) -> bool:
    return rel.startswith("backend/tests/") and Path(rel).name.startswith("test_") \
        and rel.endswith(".py") and "/fixtures/" not in rel


_SKIP_DIRS = frozenset({"__pycache__", "node_modules", "build", "dist", "site-packages"})


def _backend_py_files(repo: Path) -> list[str]:
    """Every .py under backend/ (dot-dirs such as .venv and build output pruned)."""
    root = repo / "backend"
    out: list[str] = []
    if not root.is_dir():
        return out
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if not d.startswith(".") and d not in _SKIP_DIRS)
        for fn in fns:
            if fn.endswith(".py"):
                out.append((Path(dp) / fn).relative_to(repo).as_posix())
    return sorted(out)


def build_source_graph(repo: Path, cache_dir: Path) -> dict[str, list[str]]:
    """Map every backend .py file (sources AND tests) to the module names it imports.

    Cached at ``<cache>/srcgraph-<repo>.json`` keyed on names + mtimes + sizes; on a
    miss only files whose mtime/size changed are re-parsed.
    """
    stats = {}
    for rel in _backend_py_files(repo):
        try:
            st = (repo / rel).stat()
        except OSError:
            continue
        stats[rel] = [st.st_mtime_ns, st.st_size]
    dir_key = _sha(json.dumps(sorted(stats.items())).encode())
    cpath = cache_dir / f"srcgraph-{_sha(str(repo).encode())[:16]}.json"
    cached = _read_json(cpath) or {}
    old = cached.get("entries") if cached.get("v") == RUNNER_VERSION else None
    if isinstance(old, dict) and cached.get("key") == dir_key:
        return _resolve_script_imports(repo, {rel: e["mods"] for rel, e in old.items()})
    old = old if isinstance(old, dict) else {}
    entries = {}
    for rel, ms in stats.items():
        e = old.get(rel)
        if isinstance(e, dict) and e.get("ms") == ms:
            entries[rel] = e
            continue
        entries[rel] = {"ms": ms, "mods": _imports_of_source(_read_text(repo / rel), _pkg_of(rel))}
    try:
        _write_json(cpath, {"v": RUNNER_VERSION, "key": dir_key, "entries": entries})
    except OSError:
        pass
    return _resolve_script_imports(repo, {rel: e["mods"] for rel, e in entries.items()})


def _resolve_script_imports(repo: Path, sgraph: dict[str, list[str]]) -> dict[str, list[str]]:
    """Map a bare ``import x`` to ``scripts.x`` when ``backend/scripts/x.py`` exists and
    ``x`` is not a backend-root module. Tests (and scripts) put ``backend/scripts`` on
    ``sys.path`` and import a script by its bare name; without this the edge was lost and
    56 of 66 test -> script pairs were never selected when the script changed (10-21, §11)."""
    scripts = {p.stem for p in (repo / "backend" / "scripts").glob("*.py")}
    scripts -= {m for m in scripts if _module_file(repo, m)}  # backend-root module wins

    def fix(m: str) -> str:
        return f"scripts.{m}" if m.split(".", 1)[0] in scripts else m

    return {rel: sorted({fix(m) for m in mods}) for rel, mods in sgraph.items()}


def build_import_graph(repo: Path, cache_dir: Path,
                       sgraph: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Map every backend test file to the module names it imports (from the source graph)."""
    sg = sgraph if sgraph is not None else build_source_graph(repo, cache_dir)
    return {rel: mods for rel, mods in sg.items() if _is_backend_test(rel)}


def _imports_module(mods: list[str], changed_mod: str, is_pkg: bool) -> bool:
    return any(m == changed_mod or (is_pkg and m.startswith(changed_mod + ".")) for m in mods)


def _changed_module_files(repo: Path, changed: list[str],
                          include_deleted: bool = False) -> list[tuple[str, str, bool]]:
    """(file, module, is_package) for every changed, existing, non-test backend module.
    ``include_deleted``: also a deleted (or renamed-away) one: its importers must still
    run, they are the tests the deletion breaks (red-team 2026-10-19)."""
    out = []
    for f in changed:
        if f.startswith("backend/tests/") and Path(f).name.startswith(("test_", "conftest")):
            continue
        m = module_of(f)
        if m and ((repo / f).is_file() or (include_deleted and not (repo / f).exists())):
            out.append((f, m, f.endswith("__init__.py")))
    return out


def _script_name_re(repo: Path, changed: list[str]) -> "re.Pattern | None":
    """Whole-file-name pattern of the changed ``backend/scripts/*.py`` files: a test that
    names one drives that script by subprocess (no import edge reaches it)."""
    # a deleted / renamed-away script counts too: its old name is what tests still call
    scripts = [Path(f).name for f in changed if f.startswith("backend/scripts/")
               and f.endswith(".py")]
    if not scripts:
        return None
    # a whole file name: "x_test.py" must not match inside "test_x_test.py"
    return re.compile(r"(?<![\w.-])(?:" + "|".join(map(re.escape, scripts)) + r")\b")


def _changed_modules(repo: Path, changed: list[str]) -> list[tuple[str, bool]]:
    return [(m, pkg) for _, m, pkg in _changed_module_files(repo, changed)]


class _Deps:
    """File-level backend-local import edges (memoized). Importing ``a.b.c`` depends on
    ``a/__init__.py``, ``a/b/__init__.py`` and ``a/b/c.py`` — whichever exist."""

    def __init__(self, repo: Path, sgraph: dict[str, list[str]] | None):
        self.repo, self.sgraph = repo, sgraph or {}
        self._mf: dict[str, str | None] = {}
        self._edges: dict[str, set[str]] = {}

    def module_file(self, mod: str) -> str | None:
        if mod not in self._mf:
            self._mf[mod] = _module_file(self.repo, mod)
        return self._mf[mod]

    def deps(self, rel: str) -> set[str]:
        if rel not in self._edges:
            mods = self.sgraph.get(rel)
            if mods is None:
                mods = _imports_of_source(_read_text(self.repo / rel), _pkg_of(rel))
            out: set[str] = set()
            for m in mods:
                parts = m.split(".")
                for i in range(1, len(parts) + 1):
                    f = self.module_file(".".join(parts[:i]))
                    if f:
                        out.add(f)
            out.discard(rel)
            self._edges[rel] = out
        return self._edges[rel]

    def closure(self, roots) -> set[str]:
        seen: set[str] = set()
        stack = [r for r in roots if r]
        while stack:
            f = stack.pop()
            if f in seen:
                continue
            seen.add(f)
            if f.endswith(".py"):
                stack.extend(self.deps(f) - seen)
        return seen


def import_closure(repo: Path, sgraph: dict[str, list[str]] | None, roots) -> set[str]:
    """``roots`` plus every backend-local file they transitively import."""
    return _Deps(repo, sgraph).closure(roots)


def reverse_distances(repo: Path, sgraph: dict[str, list[str]], roots,
                      depth: int = REVERSE_DEPTH, deps: _Deps | None = None) -> dict[str, int]:
    """file → shortest import distance to any root (roots = 0), at most ``depth`` hops."""
    deps = deps or _Deps(repo, sgraph)
    rev: dict[str, set[str]] = {}
    for f in sgraph:
        for d in deps.deps(f):
            rev.setdefault(d, set()).add(f)
    dist = {r: 0 for r in roots}
    frontier = list(dist)
    for level in range(1, max(0, depth) + 1):
        nxt = []
        for f in frontier:
            for g in sorted(rev.get(f, ())):
                if g not in dist:
                    dist[g] = level
                    nxt.append(g)
        frontier = nxt
    return dist


_DOC_LINT_SUITES = (
    "backend/tests/test_lint_skills_body.py",
    "backend/tests/test_lint_skills_size.py",
    "backend/tests/test_lint_experts.py",
)
# The suites that read the governed context sources (backend/context/): a change there
# has no importers either.
_CONTEXT_SUITES = (
    "backend/tests/test_context_templates.py",
    "backend/tests/test_context_directory_loader.py",
)
# The suites that scan backend source text or AST (rglob over *.py) for a rule: no
# import edge reaches them, so a change to any backend module can turn them red
# unseen (8d3b7c1e reddened test_silent_except_baseline on main, 10-21). ~20s in total.
# test_pipeline_test.py pins this list against every test that globs *.py.
_SOURCE_SCAN_SUITES = (
    "backend/tests/test_app_config_manager.py",
    "backend/tests/test_bash_misuse_report.py",
    "backend/tests/test_cjk_encoding.py",
    "backend/tests/test_ddd_canonical_docs_single_source.py",
    "backend/tests/test_ddd_paths_single_source.py",
    "backend/tests/test_gate_decision_log.py",
    "backend/tests/test_handler_log_safety.py",
    "backend/tests/test_job_model_ssot.py",
    "backend/tests/test_output_degeneration.py",
    "backend/tests/test_pipeline_v1_cutover.py",
    "backend/tests/test_r6a_ceiling_decouple.py",
    "backend/tests/test_router_async_blocking.py",
    "backend/tests/test_silent_except_baseline.py",
)
# Glob *.py but not over backend source (a tmp_path or a generated tree).
_NOT_SOURCE_SCANS = ("backend/tests/test_gate_scaffold.py",)


def select_backend(repo: Path, changed: list[str], graph: dict[str, list[str]],
                   cap: "int | None" = None, recent: list[str] | tuple = (),
                   sgraph: dict[str, list[str]] | None = None,
                   depth: int = REVERSE_DEPTH) -> tuple[list[str], list[str], list[str]]:
    """Return ``(selected, skipped_for_budget, changed_module_names)``.

    Order: changed test files, then files that failed recently, then importers of
    changed modules sorted by import distance (direct importers first; with
    ``sgraph`` also transitive importers up to ``depth`` hops), ties broken by how
    many changed modules a test imports directly. A test that names a changed
    ``backend/scripts/*.py`` file by file name (a subprocess-driven CLI test) ranks as a
    direct importer. Nothing is capped (``cap`` is accepted and ignored) and the skipped
    list is always empty. A deleted module's importers are selected too.
    """
    changed_tests = [f for f in changed if _is_backend_test(f) and (repo / f).is_file()]
    # A skill card or expert file has no importers, yet the linters and the rule
    # preservation suite read them (run_376299fa pushed plan.md past the card limit
    # with regression green): select those suites as direct when one changed.
    if any(f.startswith(("backend/skills/", "backend/experts/")) for f in changed):
        changed_tests += [t for t in _DOC_LINT_SUITES
                          if t not in changed_tests and (repo / t).is_file()]
    if any(f.startswith("backend/context/") for f in changed):
        changed_tests += [t for t in _CONTEXT_SUITES
                          if t not in changed_tests and (repo / t).is_file()]
    if any(module_of(f) and not _is_backend_test(f) for f in changed):
        changed_tests += [t for t in _SOURCE_SCAN_SUITES
                          if t not in changed_tests and (repo / t).is_file()]
    mfiles = _changed_module_files(repo, changed, include_deleted=True)
    mods = [(m, pkg) for _, m, pkg in mfiles]
    rank: dict[str, tuple[int, int]] = {}
    for t, tm in graph.items():
        n = sum(1 for m, pkg in mods if _imports_module(tm, m, pkg))
        if n:
            rank[t] = (1, -n)
    if sgraph is not None and mfiles:
        dist = reverse_distances(repo, sgraph, [f for f, _, _ in mfiles], depth)
        for t in graph:
            d = dist.get(t)
            if d is not None and d >= 1 and t not in rank:
                rank[t] = (d, 0)  # direct importers by name already hold (1, -n)
    named = _script_name_re(repo, changed)
    if named is not None:
        for t in graph:
            if rank.get(t, (2, 0)) > (1, 0) and named.search(_read_text(repo / t)):
                rank[t] = (1, 0)
    rec = [f for f in dict.fromkeys(recent) if f not in changed_tests and (repo / f).is_file()]
    skip = set(changed_tests) | set(rec)
    # Every direct importer (and every test naming a changed script) runs: no narrowing
    # by symbol name. Expert review 2026-10-19 showed name matching misses renames,
    # import rebinding, multi-line imports, getattr dispatch, multi-hop callers and
    # constructed subcommands: each a silent false green. The scope is fixed by the
    # change and never capped; a scope that does not fit one call continues in the next.
    del cap
    importers = [t for t in sorted(rank, key=lambda t: (rank[t], t)) if t not in skip]
    return changed_tests + rec + importers, [], [m for m, _ in mods]


def transitive_unselected(repo: Path, sgraph: dict | None, graph: dict, changed: list[str],
                          selected) -> list[str]:
    """Test files that reach a changed module only through other modules (outside the
    direct-importer scope): not run by verify / regression, named in REPORT §10."""
    mods = _changed_module_files(repo, changed, include_deleted=True)
    if not sgraph or not mods:
        return []
    roots = [f for f, _, _ in mods if (repo / f).is_file()]
    # a deleted module has no file to reverse-walk from: start from the sources that
    # still import it by name (red-team 2026-10-19: its transitive tests went unlisted)
    gone = [(m, pkg) for f, m, pkg in mods if not (repo / f).exists()]
    if gone:
        roots += [f for f, imp in sgraph.items() if not _is_backend_test(f)
                  and any(_imports_module(imp, m, pkg) for m, pkg in gone)]
    if not roots:
        return []
    dist = reverse_distances(repo, sgraph, roots, 99)
    sel = set(selected)
    return sorted(t for t in graph if t not in sel and (dist.get(t) or 0) >= 1)


# ── per-test event stream (survives a killed or os._exit()-ed shard) ────────
_PLUGIN_NAME = "_pipeline_test_events"
_PLUGIN_SRC = '''"""pytest plugin generated by pipeline_test.py.

Streams per-test events as JSON lines to $PIPELINE_TEST_EVENTS so outcomes are
known even when the shard is killed at the deadline or pytest-timeout exits.
"""
import json
import os

import pytest

_PATH = os.environ.get("PIPELINE_TEST_EVENTS")
_FH = []


def _emit(rec):
    if not _PATH:
        return
    if not _FH:
        _FH.append(open(_PATH, "a", encoding="utf-8"))
    _FH[0].write(json.dumps(rec) + "\\n")
    _FH[0].flush()


_DESELECT = set(json.loads(os.environ.get("PIPELINE_TEST_DESELECT") or "[]"))
# Split chunks of one heavy file: a file with a keep list runs only the tests it names.
# Scoped per file, so the other targets of the same shard are untouched.
_KEEP = set(json.loads(os.environ.get("PIPELINE_TEST_KEEP") or "[]"))
_KEEP_FILES = {k.split("::")[0] for k in _KEEP}


def _matches(nodeid, refs):
    # The ref itself, or what it contains: its parametrizations (``ref[...]``) and, for a
    # class ref, its methods (``ref::...``). NOT a bare prefix: pytest's own --deselect
    # matches by prefix, so deselecting test_parse also dropped test_parse_empty.
    return any(nodeid == d or nodeid.startswith((d + "[", d + "::")) for d in refs)


def _deselected(nodeid):
    if _matches(nodeid, _DESELECT):
        return True
    return nodeid.split("::")[0] in _KEEP_FILES and not _matches(nodeid, _KEEP)


def pytest_collection_modifyitems(session, config, items):
    if not _DESELECT and not _KEEP:
        return
    drop = [i for i in items if _deselected(i.nodeid)]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = [i for i in items if not _deselected(i.nodeid)]


def pytest_collection_finish(session):
    _emit({"ev": "collected", "nodeids": [i.nodeid for i in session.items]})


def pytest_collectreport(report):
    if report.failed:
        _emit({"ev": "collect_error", "nodeid": report.nodeid,
               "longrepr": str(report.longrepr)[-3000:]})
    elif report.skipped:
        _emit({"ev": "collect_skip", "nodeid": report.nodeid})


def pytest_runtest_logstart(nodeid, location):
    _emit({"ev": "start", "nodeid": nodeid})


def _exc_kind(et):
    # "assert": an AssertionError (or subclass); "fail": pytest.fail / pytest.raises'
    # DID NOT RAISE (both raise pytest's Failed); "error": anything else.
    if issubclass(et, AssertionError):
        return "assert"
    failed = getattr(pytest.fail, "Exception", None)
    if isinstance(failed, type) and issubclass(et, failed):
        return "fail"
    return "error"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if call.excinfo is not None and rep.failed:
        et = call.excinfo.type
        rep._pt_exc = (getattr(et, "__module__", "?") + "." + getattr(et, "__qualname__", "?"))[:200]
        rep._pt_kind = _exc_kind(et)


def pytest_runtest_logreport(report):
    rec = {"ev": "report", "nodeid": report.nodeid, "when": report.when,
           "outcome": report.outcome, "dur": float(getattr(report, "duration", 0.0) or 0.0),
           "longrepr": str(report.longrepr)[-3000:] if report.failed else ""}
    if report.failed and getattr(report, "_pt_kind", None):
        rec["exc"], rec["exc_kind"] = report._pt_exc, report._pt_kind
    _emit(rec)
'''


def _plugin_dir(cache: Path) -> Path:
    # One directory per plugin SOURCE: runners on other commits (worktrees, an older
    # checkout) share the cache, and a single shared file let them swap in a plugin
    # without the keep list in the middle of a run.
    return _write_once(cache / "plugin" / _sha(_PLUGIN_SRC.encode())[:12],
                       f"{_PLUGIN_NAME}.py", _PLUGIN_SRC)


def _site_dir(cache: Path) -> Path:
    """A directory holding ONLY the generated sitecustomize — put on PYTHONPATH just for
    a run-worktree test run, so no other run sees (or is shadowed by) it."""
    return _write_once(cache / "site" / _sha(_SITECUSTOMIZE_SRC.encode())[:12],
                       "sitecustomize.py", _SITECUSTOMIZE_SRC)


def _write_once(d: Path, name: str, src: str) -> Path:
    p = d / name
    if _read_text(p) != src:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{name}.{os.getpid()}.tmp"
        tmp.write_text(src, encoding="utf-8")
        os.replace(tmp, p)
    return d


# Imported by every child Python whose PYTHONPATH holds the plugin dir. For a run
# worktree it drops the MAIN checkout's backend dir, which the venv's editable .pth
# adds: otherwise a module the run deleted or renamed still imports from main
# (pipeline-run-worktree §6.2). No-op unless PIPELINE_TEST_STRIP_ROOT is set.
_SITECUSTOMIZE_SRC = '''"""Generated by pipeline_test.py: strip PIPELINE_TEST_STRIP_ROOT from sys.path."""
import os as _os
import sys as _sys

_root = _os.environ.get("PIPELINE_TEST_STRIP_ROOT")
if _root:
    _sys.path[:] = [p for p in _sys.path if _os.path.realpath(p or ".") != _root]
'''


def _worker_count() -> int:
    """min(4, cpu, memory: 1 GB per worker keeping 4 GB headroom); 2 without psutil.
    Raised toward the idle cores by ``_scale_up`` when no other runner is busy.

    ``SWARM_TEST_MAX_WORKERS`` (a positive integer) caps it — never raises it. The
    runner's own test rig sets 1: those tests start nested runners, and once their file
    is split into concurrent chunks, every nested runner sizing itself to 4 workers
    oversubscribed the machine. A missing or invalid value changes nothing.
    """
    cpu = os.cpu_count() or 2
    try:
        import psutil
        by_mem = int((psutil.virtual_memory().available - 4 * 1024 ** 3) // 1024 ** 3)
    except Exception:  # noqa: BLE001 — psutil missing or unreadable
        by_mem = 2
    n = _scale_up(max(1, min(4, cpu, by_mem)))
    cap = (os.environ.get("SWARM_TEST_MAX_WORKERS") or "").strip()
    if cap.isascii() and cap.isdigit() and int(cap) >= 1:
        n = min(n, int(cap))
    return n


MAX_WORKERS = 8       # ceiling when no other runner holds a worker token
IDLE_SAMPLE_S = 0.25  # psutil.cpu_percent sampling window


def _worker_budget() -> int:
    """Size of the machine-wide worker-token pool: ``cpu - WORKER_BUDGET_RESERVE``, at
    least 2. ``SWARM_TEST_WORKER_BUDGET`` (a positive integer) overrides it."""
    raw = (os.environ.get("SWARM_TEST_WORKER_BUDGET") or "").strip()
    if raw.isascii() and raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return max(2, (os.cpu_count() or 2) - WORKER_BUDGET_RESERVE)


def _token_path(cache: Path, n: int) -> Path:
    return cache / "workers" / f"{n}.lock"


def _busy_tokens(cache: Path) -> int:
    """How many worker tokens other runners hold right now."""
    busy = 0
    for n in range(_worker_budget()):
        fd = _try_lock(_token_path(cache, n), 0)
        if fd is None:
            busy += 1
        else:
            _unlock(fd)
    return busy


def _scale_up(base: int) -> int:
    """``base`` raised toward the cores idle right now (a fixed 4 left 8 of 12 cores
    unused on a quiet machine). Never below ``base``. Only when no other runner holds a
    worker token; the token pool (``_acquire_workers``) caps what is actually granted.
    Ceiling: ``MAX_WORKERS``, ``cpu - 2`` (room for the model and the daemon), free
    memory (1 GB per worker, 4 GB headroom). ``SWARM_TEST_MAX_WORKERS`` set, no psutil,
    or an unreadable token dir = ``base``."""
    if os.environ.get("SWARM_TEST_MAX_WORKERS"):
        return base
    try:
        if _busy_tokens(_cache_dir()):
            return base
        import psutil
        cpu = os.cpu_count() or 2
        idle = cpu * (100.0 - psutil.cpu_percent(interval=IDLE_SAMPLE_S)) / 100.0
        by_mem = int((psutil.virtual_memory().available - 4 * 1024 ** 3) // 1024 ** 3)
    except Exception:  # noqa: BLE001 — psutil missing / slot dir unreadable: keep the base
        return base
    return max(base, min(MAX_WORKERS, cpu - 2, by_mem, int(idle)))



def _acquire_workers(cache: Path, want: int, warnings: list) -> list:
    """Take up to ``want`` tokens from the machine-wide pool; returns their lock fds.

    Takes whatever is free (at least one) instead of waiting for all ``want``: a
    smaller share now beats idling while another runner finishes. With none free it
    polls for SLOT_WAIT_S, then returns [] and the caller runs ONE untokened worker
    (warned): bounded overshoot instead of eating the deadline. Release with
    ``_release_workers``."""
    budget, want = _worker_budget(), max(1, want)
    end = time.monotonic() + SLOT_WAIT_S
    while True:
        got = []
        for n in range(budget):
            if len(got) >= want:
                break
            fd = _try_lock(_token_path(cache, n), 0)
            if fd is not None:
                got.append(fd)
        if got:
            if len(got) < want:
                warnings.append(f"test worker pool shared with other runs: {len(got)} of "
                                f"{want} workers granted (pool {budget})")
            return got
        if time.monotonic() >= end:
            warnings.append(f"all {budget} test worker tokens busy for {SLOT_WAIT_S:.0f}s; "
                            "running with 1 worker")
            return []
        time.sleep(0.25)


def _release_workers(fds: list) -> None:
    for fd in fds:
        _unlock(fd)


def _trim_tokens(tokens: list, jobs: list) -> list:
    """Give back the tokens the built jobs cannot use (a one-file tdd call must not hold
    four). Demand = total job weight (a vitest job weighs its fork count). Keeps >= 1."""
    need = max(1, sum(getattr(j, "weight", 1) for j in jobs))
    if len(tokens) > need:
        _release_workers(tokens[need:])
        return tokens[:need]
    return tokens


# ── signal handling (M1) ────────────────────────────────────────────────────
# Process-local registries (this is a one-shot CLI, never a long-lived server):
# jobs whose process group is alive, and worktrees that must not outlive us.
_LIVE_JOBS: list = []
_PRIVATE_WT: dict[Path, Path] = {}  # worktree path → repo it belongs to
_SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGALRM", "SIGINT", "SIGHUP")
                 if hasattr(signal, n))


class _Interrupted(BaseException):
    """Raised from the signal handler; BaseException so ``except Exception`` can't eat it."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


def _remove_worktree(repo: Path, wt: Path) -> None:
    _git_run(repo, "worktree", "remove", "--force", str(wt), timeout=30)
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
    _git_run(repo, "worktree", "prune", timeout=30)


def _cleanup_private_worktrees() -> None:
    for wt, repo in list(_PRIVATE_WT.items()):
        _remove_worktree(repo, wt)
        _PRIVATE_WT.pop(wt, None)


def _on_signal(signum, frame) -> None:  # noqa: ARG001 — signal handler signature
    for s in _SIGNALS:  # no re-entry while we clean up
        try:
            signal.signal(s, signal.SIG_IGN)
        except (OSError, ValueError):
            pass
    live = [j for j in _LIVE_JOBS if j.proc is not None and j.rc is None]
    if live:
        _abort(live)
    raise _Interrupted(signum)


def _install_signal_handlers() -> dict | None:
    import threading
    if threading.current_thread() is not threading.main_thread():
        return None
    prev = {}
    for s in _SIGNALS:
        try:
            prev[s] = signal.signal(s, _on_signal)
        except (OSError, ValueError):
            pass
    return prev


def _restore_signal_handlers(prev: dict | None) -> None:
    for s, h in (prev or {}).items():
        try:
            signal.signal(s, h if h is not None else signal.SIG_DFL)
        except (OSError, ValueError, TypeError):
            pass


# ── process jobs ────────────────────────────────────────────────────────────
class _Job:
    """One foreground subprocess in its own process group, output to files."""

    def __init__(self, name: str, argv: list[str], cwd: Path, env: dict, workdir: Path,
                 kind: str, **meta):
        self.name, self.argv, self.cwd, self.env, self.kind = name, argv, cwd, env, kind
        self.meta = meta
        # Scheduling weight in _run_jobs = processes this job puts on the machine (a
        # vitest job runs its own fork pool); every other job is one process.
        self.weight = max(1, int(meta.pop("weight", 1) or 1))
        self.out = workdir / f"{name}.out"
        self.err = workdir / f"{name}.err"
        self.proc = None
        self.rc: int | None = None
        self.started = self.killed = False
        self.t0 = 0.0
        self.duration = 0.0

    def start(self) -> None:
        self.started = True
        self.t0 = time.monotonic()
        try:
            with open(self.out, "wb") as o, open(self.err, "wb") as e:
                self.proc = subprocess.Popen(
                    self.argv, cwd=str(self.cwd), env=self.env, stdout=o, stderr=e,
                    stdin=subprocess.DEVNULL, start_new_session=True,
                )
            _LIVE_JOBS.append(self)
        except OSError as exc:
            self.err.write_text(f"cannot start {self.argv[0]}: {exc}\n", encoding="utf-8")
            self.rc = 127

    def _done(self) -> None:
        if self in _LIVE_JOBS:
            _LIVE_JOBS.remove(self)

    def poll(self) -> bool:
        if self.proc is None or self.proc.poll() is None:
            return self.proc is None
        self.rc = self.proc.returncode
        self.duration = time.monotonic() - self.t0
        self._reap_group()
        self._done()
        return True

    def _reap_group(self) -> None:
        """Kill anything the job left behind in its process group."""
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except OSError:
            pass

    def text(self) -> str:
        return _read_text(self.err) + "\n" + _read_text(self.out)


def _abort(jobs: list[_Job]) -> None:
    """Deadline: SIGABRT (faulthandler dumps every thread's stack), then killpg."""
    for j in jobs:
        j.killed = True
        try:
            os.kill(j.proc.pid, signal.SIGABRT)
        except OSError:
            pass
    end = time.monotonic() + 1.5
    for j in jobs:
        try:
            j.proc.wait(timeout=max(0.05, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for j in jobs:
        j._reap_group()
        try:
            j.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        j.rc = j.proc.returncode
        j.duration = time.monotonic() - j.t0
        j._done()


def _run_jobs(jobs: list[_Job], workers: int, deadline_at: float) -> None:
    """Run jobs, in list (priority) order, with at most ``workers`` processes: each job
    counts its ``weight`` (a vitest job its fork pool), and a job always starts when
    nothing else runs. Nothing survives past ``deadline_at``."""
    pending, running = list(jobs), []
    while pending or running:
        while pending and time.monotonic() < deadline_at:
            load = sum(getattr(r, "weight", 1) for r in running)
            if running and load + getattr(pending[0], "weight", 1) > workers:
                break
            j = pending.pop(0)
            j.start()
            if j.rc is None:
                running.append(j)
        for j in list(running):
            if j.poll():
                running.remove(j)
        if time.monotonic() >= deadline_at:
            _abort(running)
            return
        if running:
            time.sleep(0.05)
        elif pending and time.monotonic() >= deadline_at:
            return


# ── pytest shards ───────────────────────────────────────────────────────────
def _backend_rel(target: str) -> str:
    return target[len("backend/"):] if target.startswith("backend/") else target


def _shards(targets: dict, workers: int, first: dict | None = None,
            durations: dict | None = None) -> list[list[str]]:
    """Group targets (file → None for whole file, or a set of nodeids) into shards.

    Known deadlock-prone suites get a shard of their own; the rest are packed into
    about ``2 × workers`` groups so process start-up cost stays bounded.

    Shards keep the PRIORITY order of ``targets`` (the planner inserts the run's
    own tests first, then importers by distance), and ``_run_jobs`` starts them in
    list order. Under the global deadline the late shards are the ones that never
    start, so the order decides which files get evidence: sorting alphabetically
    and putting the serial suites first once left a run's own test files unstarted
    while an unrelated 66 s serial suite ran.

    ``first`` (file → AC nodeids) runs ahead of everything, as its own leading
    shard(s): an AC test inside a slow file would otherwise wait for every test above
    it. The same nodeids leave their normal position — removed from a nodeid set, or
    ``--deselect``-ed from a whole-file target — so they run once. Serial suites stay
    isolated in the lead too.

    ``durations`` (``backend/`` nodeid → seconds, the main run's history) switches the
    non-serial packing from file count to recorded cost — see ``_cost_bins``. Empty or
    None keeps the count-based plan exactly; only the main run passes it.
    """
    def is_serial(f: str) -> bool:
        return any(k in Path(f).name for k in _SERIAL_SUITES)

    first = first or {}
    pulled = {f: set(first[f]) for f in targets if first.get(f)}  # in priority order
    lead_serial, lead_normal = [], []
    for f, nids in pulled.items():
        (lead_serial if is_serial(f) else lead_normal).append([_backend_rel(n) for n in sorted(nids)])
    lead = lead_serial + ([sum(lead_normal, [])] if lead_normal else [])
    serial, normal = [], []
    for rank, f in enumerate(targets):
        sel = targets[f]
        out_n = pulled.get(f, set())
        if sel is None:
            args = [_backend_rel(f)] + [f"--deselect={_backend_rel(n)}" for n in sorted(out_n)]
        else:
            rest = sorted(set(sel) - out_n)
            if not rest:
                continue
            args = [_backend_rel(n) for n in rest]
        if is_serial(f):
            serial.append((rank, args))
        elif durations:
            normal.append((rank, f, sel, out_n, args))
        else:
            normal.append((rank, args))
    if durations:
        return lead + [args for _, args in sorted(serial + _cost_bins(normal, workers, durations),
                                                    key=lambda g: g[0])]
    size = max(1, -(-len(normal) // max(1, workers * 2)))
    groups = [(normal[i][0], sum((a for _, a in normal[i:i + size]), []))
              for i in range(0, len(normal), size)]
    return lead + [args for _, args in sorted(serial + groups, key=lambda g: g[0])]


def _family(nid: str) -> str:
    """``file::first-segment`` without ``[params]``: two ids where one contains the other
    (``t`` and ``t[1]``, ``C`` and ``C::m``) always share a family, so placing a family in
    one chunk keeps a stale history id from selecting a test a sibling chunk also runs."""
    f, _, rest = nid.partition("::")
    return f + "::" + rest.split("::")[0].split("[")[0]


# Fraction of (seconds × workers) a selection may fill: shard start-up, collection and
# the requeue reserve take the rest. Measured 2026-10-19: verify calls whose recorded cost
# was over ~0.6 of the slot budget were the ones that ended with deadline timeouts.
def _target_cost(f: str, sel, durations: dict) -> float:
    """Recorded seconds for one target (whole file or a nodeid set)."""
    if sel is not None:
        return sum(durations.get(n, DEFAULT_TEST_COST_S) for n in sel)
    known = [v for k, v in durations.items() if k.split("::")[0] == f]
    return sum(known) if known else DEFAULT_FILE_COST_S


BVT_MANIFEST = "backend/tests/bvt_manifest.txt"


def _bvt_files(repo: Path, warnings: list) -> list[str]:
    """The CI BVT test files (``tests/bvt_manifest.txt``, parsed as ci.yml does: ``#``
    comments and blanks dropped, one ``tests/``-relative path per line), repo-relative.
    A listed file that is missing is warned (CI fails on it too)."""
    text = _read_text(repo / BVT_MANIFEST)
    out = []
    for line in (text or "").splitlines():
        name = line.split("#", 1)[0].strip()
        if not name:
            continue
        f = f"backend/tests/{name}"
        if (repo / f).is_file():
            out.append(f)
        else:
            warnings.append(f"bvt_manifest.txt lists a missing file: {name} (CI fails on it)")
    return list(dict.fromkeys(out))


def _cost_bins(items: list, workers: int, durations: dict) -> list[tuple[int, list[str]]]:
    """Pack non-serial targets into shards of about one cost share each, in priority order.

    ``share`` = total recorded cost / ``2 × workers``. A whole-file target costlier than
    one share is split by test family (``_split_file``). The resulting units are then cut
    into CONTIGUOUS groups in target-rank order, closing a group once it reaches a share
    or would hold two chunks of one file. Contiguous, not LPT: ``_run_jobs`` starts shards
    in list order and the deadline drops the tail, so the run's own files (top ranks) must
    sit in the first shards — LPT spread them over every bin. Returns ``(lowest target
    rank, args)`` per group.
    """
    by_file: dict[str, list[str]] = {}
    for nid in durations:
        by_file.setdefault(nid.split("::")[0], []).append(nid)
    whole: list[dict] = []
    for rank, f, sel, out_n, args in items:
        if sel is not None:
            rest = set(sel) - set(out_n)  # the AC-lead ids run in the lead, not here
            whole.append({"rank": rank, "file": None, "args": args, "known": None,
                          "ids": sorted(rest),
                          "cost": sum(durations.get(n, DEFAULT_TEST_COST_S) for n in rest)})
            continue
        known = [n for n in by_file.get(f, []) if not _matches_any(n, out_n)]
        whole.append({"rank": rank, "file": f, "args": args, "known": known,
                      "cost": sum(durations[n] for n in known) if known else DEFAULT_FILE_COST_S})
    nbins = max(1, workers * 2)
    share = sum(u["cost"] for u in whole) / nbins
    units: list[dict] = []
    for u in whole:
        if u.get("ids") and u["cost"] > share:
            chunks = _split_ids(u, share, nbins, durations)
        else:
            chunks = _split_file(u, share, nbins, durations) if u["known"] and u["cost"] > share else None
        units += chunks or [{**u, "sub": 0}]
    groups: list[dict] = []
    cur: dict | None = None
    for u in sorted(units, key=lambda x: (x["rank"], x["sub"])):
        clash = cur is not None and u["file"] is not None and u["file"] in cur["files"]
        if cur is None or clash or (cur["cost"] > 0 and cur["cost"] + u["cost"] > share * 1.1):
            cur = {"rank": u["rank"], "cost": 0.0, "args": [], "files": set()}
            groups.append(cur)
        cur["cost"] += u["cost"]
        cur["args"] += u["args"]
        if u["file"] is not None:
            cur["files"].add(u["file"])
    return [(g["rank"], g["args"]) for g in groups]


def _split_ids(u: dict, share: float, nbins: int, durations: dict) -> list[dict] | None:
    """Split one heavy nodeid-set unit (a continuation's unfinished tests of one file)
    into chunks of about one share by test family, so the rest of a slow file spreads
    over every worker instead of one shard. Family order is kept inside each chunk."""
    fams: dict[str, list[str]] = {}
    for n in u["ids"]:
        fams.setdefault(_family(n), []).append(n)
    n_chunks = min(int(-(-u["cost"] // share)) if share > 0 else 1, nbins, len(fams))
    if n_chunks < 2:
        return None
    chunks = [[0.0, []] for _ in range(n_chunks)]
    for fam, ids in fams.items():
        ch = min(chunks, key=lambda x: (x[0], len(x[1])))
        ch[0] += sum(durations.get(n, DEFAULT_TEST_COST_S) for n in ids)
        ch[1] += ids
    return [{"rank": u["rank"], "file": None, "known": None, "sub": i, "cost": c,
             "args": [_backend_rel(n) for n in ids]}
            for i, (c, ids) in enumerate(ch for ch in chunks if ch[1])]


def _split_file(u: dict, share: float, nbins: int, durations: dict) -> list[dict] | None:
    """Split one heavy whole-file unit into chunks of about one share, by test family.

    Every chunk but the last carries ``--keep=`` ids (the plugin runs only those in that
    file); the last runs the file with every other chunk's ids deselected, so a test
    missing from history runs there and only there. Every chunk keeps ``u["args"]`` (the
    file + its AC-lead deselects), so a stale parent id (``t`` kept, the lead holds
    ``t[1]``) cannot rerun a lead test. None = not worth splitting.
    """
    fams: dict[str, float] = {}
    fam_ids: dict[str, list[str]] = {}
    for n in u["known"]:
        fams[_family(n)] = fams.get(_family(n), 0.0) + durations[n]
        fam_ids.setdefault(_family(n), []).append(n)
    n_chunks = min(int(-(-u["cost"] // share)) if share > 0 else 1, nbins, len(fams))
    if n_chunks < 2:
        return None
    chunks = [[0.0, []] for _ in range(n_chunks)]
    for fam, c in sorted(fams.items(), key=lambda kv: (-kv[1], kv[0])):
        # Ties on cost (e.g. tests recorded at 0.0 s) go to the chunk with the fewest
        # families, so no chunk stays empty.
        ch = min(chunks, key=lambda x: (x[0], len(x[1])))
        ch[0] += c
        ch[1].append(fam)
    # An empty keep chunk would carry no keep list and run the WHOLE file: never emit one.
    chunks = [ch for ch in chunks if ch[1]]
    if len(chunks) < 2:
        return None
    out, others = [], []
    for i, (c, fl) in enumerate(chunks[:-1]):
        ids = sorted(n for fam in fl for n in fam_ids[fam])
        others += ids
        out.append({**u, "sub": i, "cost": c,
                    "args": u["args"] + [f"--keep={_backend_rel(n)}" for n in ids]})
    out.append({**u, "sub": len(chunks) - 1, "cost": chunks[-1][0],
                "args": u["args"] + [f"--deselect={_backend_rel(n)}" for n in others]})
    return out


def _matches_any(nid: str, refs) -> bool:
    return any(nid == d or nid.startswith((d + "[", d + "::")) for d in refs or ())


def _load_durations(cache: Path) -> dict[str, float]:
    """``backend/`` nodeid → seconds from ``<cache>/durations.json``; unreadable = empty."""
    raw = _read_json(cache / "durations.json")
    if not isinstance(raw, dict):
        return {}
    cutoff = time.time() - DURATION_TTL_S
    return {k: float(v[0]) for k, v in raw.items() if _valid_entry(k, v) and v[1] >= cutoff}


def _valid_entry(k, v) -> bool:
    return (isinstance(k, str) and "::" in k and isinstance(v, list) and len(v) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                    for x in v) and v[0] >= 0)


def _save_durations(cache: Path, results: list[dict]) -> str | None:
    """Merge a MAIN run's ran tests into the history (newest wins, oldest evicted).

    Only leaf ids (``::``) with a measured duration that actually ran: not-run,
    deadline-cut and runner-error results carry no real cost, and a failed / errored
    test often stops early (a fast failure would erase a slow test's real cost).
    Entries older than ``DURATION_TTL_S`` (deleted or renamed tests) are dropped.
    Concurrent runners = last writer wins; the history is a packing hint, a lost update
    only mis-balances. Returns a warning on a write error, else None.
    """
    path = cache / "durations.json"
    raw = _read_json(path)
    now = time.time()
    hist = {k: v for k, v in raw.items() if _valid_entry(k, v) and v[1] >= now - DURATION_TTL_S} \
        if isinstance(raw, dict) else {}
    new = 0
    for r in results:
        d = r.get("duration")
        if "::" not in str(r.get("nodeid", "")) or not isinstance(d, (int, float)) or isinstance(d, bool) \
                or r.get("notrun") or r.get("deadline") or r.get("runner_error") \
                or r.get("outcome") in ("failed", "error") or not math.isfinite(d):
            continue
        hist[r["nodeid"]] = [round(float(d), 3), now]
        new += 1
    if not new:
        return None
    if len(hist) > MAX_DURATION_ENTRIES:
        keep = sorted(hist.items(), key=lambda kv: kv[1][1] if isinstance(kv[1][1], (int, float)) else 0,
                      reverse=True)[:MAX_DURATION_ENTRIES]
        hist = dict(keep)
    try:
        _write_json(path, hist)
    except OSError as e:
        return f"duration history not saved ({e}): the next run packs shards with stale costs"
    return None


_PLUGIN_ENV = ("PIPELINE_TEST_EVENTS", "PIPELINE_TEST_DESELECT", "PIPELINE_TEST_KEEP")


def _child_env(backend: Path, extra_path: list[Path]) -> dict:
    env = dict(os.environ)
    paths = [str(backend)] + [str(p) for p in extra_path]
    site = _WORKTREE.get("site_dir")  # the sitecustomize that strips main's backend
    if env.get("PIPELINE_TEST_STRIP_ROOT") and site and str(site) not in paths:
        paths.append(str(site))
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.pop("PYTEST_ADDOPTS", None)
    # The events plugin's per-shard selection must never leak into a child: when this
    # runner itself runs inside a shard (its own tests start nested runners), the outer
    # shard's keep / deselect lists would otherwise filter the nested shard's files.
    for k in _PLUGIN_ENV:
        env.pop(k, None)
    return env


def _pytest_job(ctx, name: str, args: list[str], backend: Path, isolated: bool) -> _Job:
    # ``--deselect=<nodeid>`` tokens (from _shards' AC split) are not targets and not
    # pytest's --deselect (that one matches by prefix): they go to the plugin as an
    # exact-match list. Parsing and gap detection read ``meta["args"]`` = targets only.
    # ``--keep=<nodeid>`` (from a split heavy file) likewise goes to the plugin.
    #
    # A NODEID target never reaches pytest argv either: pytest exits rc=4 for one id it
    # cannot find and runs nothing, which errored every target of the shard (a renamed
    # AC test once took 18 AC-lead tests down). It becomes its FILE plus a keep entry;
    # the id is kept in meta["required"] and _target_gaps reports one not-found error
    # for it alone. A file that is also a whole-file target here wins (no keep — a keep
    # list would silently drop the rest of that file) unless the shard already carries
    # an explicit split-chunk keep list for it. A target whose file does not exist is
    # left out of argv (pytest would exit rc=4 for it too) and reported from
    # meta["missing"].
    deselect_ids = [a[len("--deselect="):] for a in args if a.startswith("--deselect=")]
    keep_ids = [a[len("--keep="):] for a in args if a.startswith("--keep=")]
    explicit_keep_files = {k.split("::")[0] for k in keep_ids}
    plain_files = {a for a in args if not a.startswith("--") and "::" not in a}
    targets_only, required, missing = [], [], []
    for a in args:
        if a.startswith("--"):
            continue
        f = a.split("::")[0]
        if not (backend / f).exists():
            missing.append(a)
            continue
        if "::" in a:
            required.append(a)
            if f not in plain_files or f in explicit_keep_files:
                keep_ids.append(a)
        if f not in targets_only:
            targets_only.append(f)
    junit = ctx.workdir / f"{name}.xml"
    events = ctx.workdir / f"{name}.events"
    argv = [ctx.python, "-X", "faulthandler", "-m", "pytest", *targets_only, "-q",
            "-o", "addopts=", "-n", "0", "-p", "no:cacheprovider",
            f"--timeout={PER_TEST_TIMEOUT_S}", "--timeout-method=thread", "--junitxml", str(junit),
            "--rootdir", str(backend), "-p", _PLUGIN_NAME]
    if not targets_only:  # every target was missing: never let pytest collect the rootdir
        argv = [ctx.python, "-c", "pass"]
    env = _child_env(backend, [ctx.plugin_dir])
    env["PIPELINE_TEST_EVENTS"] = str(events)
    if deselect_ids:
        env["PIPELINE_TEST_DESELECT"] = json.dumps(deselect_ids)
    if keep_ids:
        env["PIPELINE_TEST_KEEP"] = json.dumps(keep_ids)
    if isolated:
        # Worktree runs (baseline, mutation): a fresh bytecode dir per run. A
        # same-size edit (``a + b`` → ``a - b``) within one second would otherwise
        # be served from a stale .pyc and a killed mutation would look survived.
        env["PYTHONPYCACHEPREFIX"] = str(ctx.workdir / f"pyc-{name}")
    # A file with deselects or a keep list may legitimately run zero tests in this
    # shard (all pulled to the AC lead, or a keep chunk whose ids left the file).
    deselected = {a.split("=", 1)[1].split("::")[0] for a in args
                  if a.startswith(("--deselect=", "--keep="))}
    deselected |= {k.split("::")[0] for k in keep_ids}  # its required ids are checked one by one
    # What to name when the shard yields no per-test results (not started, killed, no
    # event stream): each required id, and each whole-file target without one.
    req_files = {r.split("::")[0] for r in required}
    units = required + [f for f in targets_only if f in plain_files or f not in req_files]
    # ``shard_args`` = the full ordered args incl. ``--deselect=`` tokens: results are
    # tagged with it so a prefix re-run deselects what the original shard deselected.
    return _Job(name, argv, backend, env, ctx.workdir, "pytest",
                args=targets_only, shard_args=list(args), junit=junit, events=events,
                deselected=deselected, required=required, missing=missing,
                units=list(dict.fromkeys(units)))


def _drop_stale_pyc(repo: Path, changed: list[str]) -> int:
    """Delete main-tree ``__pycache__`` entries of changed .py files that may be stale.

    CPython validates a .pyc by the source's whole-second mtime + size, so a same-size
    edit within the second of an earlier compile is served the OLD bytecode (a false
    green). A .pyc written no later than that second may predate the edit: drop it."""
    n = 0
    for f in changed:
        src = _contained(repo, f) if f.endswith(".py") else None
        try:
            if src is None or not src.is_file():
                continue
            sec = int(src.stat().st_mtime)
            for pyc in (src.parent / "__pycache__").glob(f"{src.stem}.*.pyc"):
                if int(pyc.stat().st_mtime) <= sec:
                    pyc.unlink(missing_ok=True)
                    n += 1
        except OSError:
            continue
    return n


def _pytest_jobs(ctx, targets: dict, tag: str, root: Path | None = None,
                 first: dict | None = None, durations: dict | None = None) -> list[_Job]:
    # ``durations`` only from the main run: baseline, mutant, flaky re-run and requeue
    # plans stay count-based (a different tree, or an already-partial selection).
    backend = (root or ctx.repo) / "backend"
    ctx.seq += 1
    shards = _shards(targets, ctx.workers, first, durations=durations)
    return [_pytest_job(ctx, f"{tag}{ctx.seq}-{i}", args, backend, root is not None)
            for i, args in enumerate(shards)]


def _results_with_requeue(ctx, jobs: list[_Job], end_at: float, tag: str,
                          root: Path | None = None) -> list[dict]:
    """Parse ``jobs``; requeue, ONCE, the tests a per-test timeout left unrun.

    A per-test timeout (thread method) ends the whole shard process, and every test
    after the hung one is reported ``notrun`` — one slow test used to take a whole
    file's evidence down with it (including AC tests). Here those not-run nodeids get
    one more shard if ``end_at`` leaves at least ``REQUEUE_MIN_S``. Not requeued: the
    timed-out test itself (not ``notrun``), shards cut by the global deadline
    (``deadline``), never-started shards and runner errors. Results merge by nodeid,
    the later result replacing the not-run one; requeued results carry
    ``requeued: True``. A second timeout in the requeued shard is reported as is.
    """
    per = [(j, _parse_job(j)) for j in jobs]
    results = _dedupe_file_level([r for _, rs in per for r in rs])
    left = list(dict.fromkeys(
        r["nodeid"] for j, rs in per if j.kind == "pytest" and j.started and not j.killed
        for r in rs if r.get("notrun") and not r.get("deadline") and not r.get("runner_error")
        and "::" in r["nodeid"]))
    if not left or end_at - time.monotonic() < REQUEUE_MIN_S:
        return results
    jobs2 = _pytest_jobs(ctx, _targets_from_nodeids(left), tag + "rq", root)
    _run_jobs(jobs2, ctx.workers, end_at)
    later: dict[str, dict] = {}
    for j in jobs2:
        for r in _parse_job(j):
            r["requeued"] = True
            later[r["nodeid"]] = r
    merged, seen = [], set()
    for r in results:
        n = r["nodeid"]
        if n in later:
            if n not in seen:
                merged.append(later[n])
                seen.add(n)
        else:
            merged.append(r)
    return merged + [r for n, r in later.items() if n not in seen]


def _dedupe_file_level(results: list[dict]) -> list[dict]:
    """One result per file-level id (no ``::``) and outcome: every chunk of a split file
    imports the module, so a collection error / module skip came back once per chunk."""
    seen, out = set(), []
    for r in results:
        key = (r["nodeid"], r["outcome"]) if "::" not in r["nodeid"] else None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
    return out


def run_pytest_targets(ctx, targets: dict, deadline_at: float, tag: str,
                       root: Path | None = None, requeue: bool = False) -> list[dict]:
    """Run targets (in ``root`` — the repo or a worktree) and return per-test results."""
    if not targets:
        return []
    jobs = _pytest_jobs(ctx, targets, tag, root)
    _run_jobs(jobs, ctx.workers, deadline_at)
    if requeue:
        return _results_with_requeue(ctx, jobs, deadline_at, tag, root)
    # Flaky re-runs, the HEAD baseline and mutants never requeue: a mutant that hangs a
    # test is already killed, and requeueing under it would only spend the deadline.
    return [r for j in jobs for r in _parse_job(j)]


# ── result parsing ──────────────────────────────────────────────────────────
def _rfile(r: dict) -> str:
    """A result's file. Compact evidence entries (passes, see ``_evidence_result``) omit
    ``file`` when it is the nodeid's own path, so every reader falls back to that."""
    f = r.get("file")
    return f if isinstance(f, str) and f else str(r.get("nodeid") or "").split("::")[0]


def _res(nodeid: str, outcome: str, summary=None, file: str | None = None, **extra) -> dict:
    r = {"nodeid": nodeid, "outcome": outcome, "file": file or nodeid.split("::")[0],
         "summary": list(summary or [])}
    r.update(extra)
    return r


def _stack(job: _Job) -> list[str]:
    """Stack of a hung shard: from the timeout / faulthandler marker, ≤ STACK_LINES."""
    lines = _lines(job.text())
    for marker in ("+ Timeout +", "Fatal Python error", "most recent call first"):
        for i, ln in enumerate(lines):
            if marker in ln:
                return lines[i:i + STACK_LINES]
    return lines[-STACK_LINES:]


def _events(path: Path) -> list[dict]:
    out = []
    for ln in _read_text(path).splitlines():
        try:
            rec = json.loads(ln)
        except ValueError:
            continue  # a line cut short by a kill
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _outcome(reps: list[dict]) -> tuple[str | None, list[str]]:
    """Final outcome from setup/call/teardown reports; None while unfinished."""
    def text(rs):
        return _lines("\n".join(r.get("longrepr") or "" for r in rs))[-SUMMARY_LINES:]
    failed = [r for r in reps if r.get("outcome") == "failed"]
    if any(r.get("when") == "call" for r in failed):
        return "failed", text(failed)
    if not any(r.get("when") == "teardown" for r in reps):
        return None, []
    if failed:
        return "error", text(failed)
    if any(r.get("outcome") == "skipped" for r in reps):
        return "skipped", []
    return "passed", []


def _parse_job(job: _Job) -> list[dict]:
    if job.kind == "pytest":
        out = _parse_pytest(job)
        shard = job.meta.get("shard_args") or job.meta["args"]
        for r in out:  # the shard's ordered args: classify_failures re-runs its prefix
            r.setdefault("shard", shard)
        return out
    if job.kind == "smoke":
        return _parse_smoke(job)
    return _parse_frontend(job)


def _parse_pytest(job: _Job) -> list[dict]:
    pre = "backend/"
    if not job.started:
        return [_res(pre + a, "timeout", ["not started: global deadline reached"], deadline=True,
                     notrun=True) for a in _report_units(job)] + _missing_results(job)
    evs = _events(job.meta["events"])
    collected, started, reps, out = [], set(), {}, []
    for e in evs:
        kind = e.get("ev")
        if kind == "collected":
            collected = [n for n in e.get("nodeids") or [] if isinstance(n, str)]
        elif kind == "start":
            started.add(e.get("nodeid"))
        elif kind == "report":
            reps.setdefault(e.get("nodeid"), []).append(e)
        elif kind == "collect_error":
            nid = str(e.get("nodeid") or "")
            out.append(_res(pre + nid, "error", _lines(e.get("longrepr") or "")[-SUMMARY_LINES:]))
        elif kind == "collect_skip":  # module-level skip: collected, deliberately empty
            out.append(_res(pre + str(e.get("nodeid") or ""), "skipped", collect_skip=True))
    if not evs:  # e.g. every target was missing (argv ran ``-c pass``): still report them
        return _parse_pytest_fallback(job) + _missing_results(job)
    stack = None
    for nid in list(dict.fromkeys(collected + [n for n in reps if n not in collected])):
        if not isinstance(nid, str):
            continue
        rs = reps.get(nid, [])
        outcome, summary = _outcome(rs)
        notrun = False
        extra = {}
        durs = [r["dur"] for r in rs if isinstance(r.get("dur"), (int, float))]
        if durs:  # an older plugin sends no "dur": no measurement, not 0 s
            extra["duration"] = float(sum(durs))
        if outcome is None:
            if nid in started:
                extra["duration"] = float(PER_TEST_TIMEOUT_S)  # ran to a timeout: that long
                stack = stack if stack is not None else _stack(job)
                why = "global deadline reached" if job.killed else f"shard exited rc={job.rc}"
                outcome, summary = "timeout", [f"test did not finish ({why}); stack:"] + stack
            else:
                outcome, notrun = "timeout", True
                extra = {}
                summary = ["not run: shard aborted before this test (deadline or per-test timeout)"]
        callf = [r for r in rs if r.get("when") == "call" and r.get("outcome") == "failed"]
        if callf and callf[0].get("exc_kind"):  # what the call phase raised (prove mode)
            extra["exc_kind"], extra["exc"] = callf[0]["exc_kind"], callf[0].get("exc")
        out.append(_res(pre + nid, outcome, summary, deadline=job.killed, notrun=notrun,
                        call=any(r.get("when") == "call" for r in rs), **extra))
    if not out and job.rc not in (0, 5):
        out = _parse_pytest_fallback(job)
    return out + _target_gaps(job, out)


def _target_hit(arg_nid: str, results: list[dict]) -> bool:
    """Did any result come back for this shard argument (file or nodeid)?"""
    f = arg_nid.split("::")[0]
    if "::" not in arg_nid:
        return any(_rfile(r) == f for r in results)
    return any(r["nodeid"] == f or _matches_any(r["nodeid"], [arg_nid]) for r in results)


def _target_gaps(job: _Job, out: list[dict]) -> list[dict]:
    """H2: zero results for a requested target, or an abnormal shard exit, is an error.

    pytest rc 0 = passed, 1 = some tests failed; anything else (2 interrupted /
    collection error, 3 internal, 4 usage e.g. nodeid not found, 5 nothing collected,
    a signal) is not green on its own. These results carry ``runner_error`` so they
    are classified ``unknown`` and never cached.
    """
    pre = "backend/"
    gaps = []
    tail = _lines(job.text())[-SUMMARY_LINES:]
    for a in job.meta["args"]:
        nid = pre + a
        if _target_hit(nid, out + gaps):
            continue
        if a in (job.meta.get("deselected") or ()) and job.started and not job.killed:
            continue  # its tests ran elsewhere: pulled into the AC lead, or (a split keep
            #           chunk whose ids left the file) in the file's last chunk
        if job.killed or not job.started:
            gaps.append(_res(nid, "timeout", ["global deadline reached before this target "
                                              "was collected"], deadline=True, notrun=True))
        else:
            gaps.append(_res(nid, "error", [f"no tests collected for this target (pytest rc="
                                            f"{job.rc}): a selected target that runs zero tests "
                                            "is not green"] + tail, notrun=True, runner_error=True))
    for rid in job.meta.get("required") or ():
        nid = pre + rid
        if _target_hit(nid, out + gaps):
            continue  # ran, or its file reported a collection error / module skip
        if job.killed or not job.started:
            gaps.append(_res(nid, "timeout", ["global deadline reached before this test ran"],
                             deadline=True, notrun=True))
        else:
            gaps.append(_res(nid, "error", [f"test not found: {rid} was not collected from its "
                                            "file (renamed or deleted?)"], notrun=True,
                             runner_error=True))
    gaps += _missing_results(job)
    # rc 5 with every target accounted for = module-level skips only: not an error.
    if job.started and not job.killed and job.rc not in (0, 1, 5) \
            and not any(r["outcome"] in _FAIL for r in out + gaps):
        gaps.append(_res(f"shard::{job.name}", "error", [f"pytest shard exited rc={job.rc} "
                                                         "without a failing test"] + tail,
                         file="backend", notrun=True, runner_error=True))
    return gaps


def _report_units(job: _Job) -> list[str]:
    """What a shard reports on when it yields no per-test result: each whole-file
    target and each required nodeid (set by ``_pytest_job``)."""
    return job.meta["units"]


def _missing_results(job: _Job) -> list[dict]:
    return [_res("backend/" + m, "error", [f"not found: {m} (its file does not exist)"],
                 notrun=True, runner_error=True) for m in job.meta.get("missing") or ()]


def _parse_pytest_fallback(job: _Job) -> list[dict]:
    """No event stream: junit xml if present, else one error/timeout per report unit
    (``_report_units``).

    Never includes ``_missing_results``: each caller adds them exactly once (the
    no-events path directly, the empty-events path through ``_target_gaps``)."""
    pre = "backend/"
    junit = job.meta["junit"]
    if junit.exists() and not job.killed:
        try:
            return _parse_junit(junit, job.meta["args"])
        except (ET.ParseError, OSError):
            pass
    if job.rc in (0, 5) and not job.killed:
        return []
    if job.killed:
        stack = _stack(job)
        return [_res(pre + a, "timeout", ["global deadline reached; stack:"] + stack, deadline=True,
                     notrun=True) for a in _report_units(job)]
    tail = _lines(job.text())[-SUMMARY_LINES:]
    return [_res(pre + a, "error", [f"pytest exited rc={job.rc}"] + tail, notrun=True,
                 runner_error=True) for a in _report_units(job)]


def _parse_junit(path: Path, args: list[str]) -> list[dict]:
    root = ET.parse(path).getroot()  # noqa: S314 — our own pytest output
    files = sorted({a.split("::")[0] for a in args}, key=len, reverse=True)
    mods = {f[:-3].replace("/", "."): f for f in files if f.endswith(".py")}
    out = []
    for tc in root.iter("testcase"):
        cls, name = tc.get("classname") or "", tc.get("name") or ""
        mod = next((m for m in mods if cls == m or cls.startswith(m + ".")), None)
        if mod is None:
            continue
        rest = [p for p in cls[len(mod) + 1:].split(".") if p]
        nid = "::".join(["backend/" + mods[mod], *rest, name])
        outcome, summary = "passed", []
        for tag, oc in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            el = tc.find(tag)
            if el is not None:
                outcome = oc
                summary = _lines((el.get("message") or "") + "\n" + (el.text or ""))[-SUMMARY_LINES:]
                break
        # junit can't tell a setup error from a call error: only passed / failure count as
        # having reached the call phase (H3).
        out.append(_res(nid, outcome, summary if outcome != "skipped" else [],
                        call=outcome in ("passed", "failed")))
    return out


# ── L3 import smoke (one subprocess for every changed module) ───────────────
_SMOKE_SENTINEL = "PIPELINE_SMOKE "
_SMOKE_SRC = (
    "import importlib, json, sys\n"
    "res = {}\n"
    "for m in sys.argv[1:]:\n"
    "    try:\n"
    "        importlib.import_module(m)\n"
    "        res[m] = ''\n"
    "    except BaseException as e:  # SystemExit at import counts as a failure\n"
    "        res[m] = (type(e).__name__ + ': ' + str(e))[:300] or type(e).__name__\n"
    f"print({_SMOKE_SENTINEL!r} + json.dumps(res), flush=True)\n"
)


def _smoke_job(ctx, mods: list[str]) -> _Job:
    backend = ctx.repo / "backend"
    env = _child_env(backend, [])
    for k in [k for k in env if k.startswith(("SWARM_", "SWARMAI_"))]:
        del env[k]  # same isolation the suite's conftest applies
    data = ctx.workdir / "smoke_data"
    data.mkdir(exist_ok=True)
    env["SWARM_DATA_DIR"] = str(data)
    argv = [ctx.python, "-X", "faulthandler", "-c", _SMOKE_SRC, *mods]
    return _Job("smoke", argv, backend, env, ctx.workdir, "smoke", mods=mods)


def _parse_smoke(job: _Job) -> list[dict]:
    mods = job.meta["mods"]
    files = job.meta.get("files") or {}
    res = None
    for ln in _read_text(job.out).splitlines():
        if ln.startswith(_SMOKE_SENTINEL):
            try:
                res = json.loads(ln[len(_SMOKE_SENTINEL):])
            except ValueError:
                res = None
    out = []
    for m in mods:
        f = files.get(m)
        if isinstance(res, dict) and m in res:
            ok = res[m] == ""
            out.append(_res(f"import::{m}", "passed" if ok else "error",
                            [] if ok else [str(res[m])], file=f, layer="smoke"))
        elif job.killed or not job.started:
            out.append(_res(f"import::{m}", "timeout", ["import smoke hit the global deadline"]
                            + (_stack(job) if job.started else []), file=f, layer="smoke",
                            deadline=True))
        else:
            out.append(_res(f"import::{m}", "error", [f"import smoke crashed rc={job.rc}"]
                            + _lines(job.text())[-SUMMARY_LINES:], file=f, layer="smoke"))
    return out


# ── frontend (vitest related + tsc) ─────────────────────────────────────────
def _npx() -> list[str]:
    npx = os.environ.get("SWARM_TEST_NPX") or shutil.which("npx") or "npx"
    return [sys.executable, npx] if npx.endswith(".py") else [npx]


def _frontend_jobs(ctx, desktop_files: list[str], with_tsc: bool,
                   with_backend: bool = False, exclude: "list[str] | None" = None) -> list[_Job]:
    """``exclude``: desktop test files a continuation already has results for, or (an
    explicit recheck on unchanged desktop inputs) that already passed: run only the rest
    of the related set — one call cannot hold a large set, and re-running it whole every
    call never finishes."""
    desktop = ctx.repo / "desktop"
    env = dict(os.environ)
    rel = [f[len("desktop/"):] for f in desktop_files]
    skip = [a for f in exclude or () for a in ("--exclude", f[len("desktop/"):])]
    if skip:
        skip.append("--passWithNoTests")  # every related file already has a result
    out = ctx.workdir / "vitest.json"
    # The default reporter streams one status line per finished file to stdout, so a
    # run killed at the deadline still says which files passed and which never ended.
    # ``--maxWorkers``: vitest's default pool takes nearly every core while the runner
    # counts it as ONE of its workers, so with the pytest shards beside it a run put
    # ~cpu + workers processes on the machine. Its pool is a SHARE of the runner's
    # workers (half when pytest shards run beside it, the rest of the budget otherwise)
    # and the job weighs that many in _run_jobs, so the call stays inside its tokens.
    # At least 2 forks: with one, a single hanging test file holds the pool until the
    # deadline and every other file of the call is reported not-run. The weight always
    # leaves one worker for the other jobs (pytest shards, tsc) when there are two.
    w = ctx.workers
    forks = max(2, w // 2 if with_backend else w - (1 if with_tsc else 0))
    jobs = [_Job("vitest", [*_npx(), "vitest", "related", *rel, "--run", "--reporter=json",
                            "--reporter=default", f"--outputFile.json={out}",
                            f"--maxWorkers={forks}", *skip],
                 desktop, env, ctx.workdir, "vitest", json=out,
                 # an excluded file has its result already: not requested from this job
                 files=[f for f in desktop_files if f not in set(exclude or ())],
                 weight=min(forks, max(1, w - 1)))]
    if with_tsc:
        jobs.append(_Job("tsc", [*_npx(), "tsc", "--noEmit", "--pretty", "false", "-p", "."],
                         desktop, env, ctx.workdir, "tsc"))
    return jobs


def _parse_frontend(job: _Job) -> list[dict]:
    if job.kind == "vitest" and job.started and job.killed:
        return _vitest_partial(job)
    if not job.started or job.killed:
        return [_res(f"desktop::{job.kind}", "timeout", ["global deadline reached"]
                     + (_fe_lines(_stack(job)) if job.started else []), file="desktop",
                     layer=job.kind, deadline=True)]
    if job.kind == "tsc":
        return _tsc_results(job)
    data = _read_json(job.meta["json"])
    if not isinstance(data, dict):
        return [_res("desktop::vitest", "error", [f"vitest produced no JSON (rc={job.rc})"]
                     + _fe_lines(_lines(job.text()))[-SUMMARY_LINES:], file="desktop",
                     layer="vitest")]
    out = []
    desktop = str(Path(job.cwd).resolve())
    for tr in data.get("testResults") or []:
        name = str(tr.get("name") or "")
        rel = os.path.relpath(name, desktop) if os.path.isabs(name) else name
        f = "desktop/" + Path(rel).as_posix()
        for a in tr.get("assertionResults") or []:
            st = a.get("status")
            oc = "passed" if st == "passed" else "failed" if st == "failed" else "skipped"
            msg = _fe_lines(_lines("\n".join(a.get("failureMessages") or [])))[:SUMMARY_LINES]
            dur = a.get("duration")
            out.append(_res(f"{f}::{a.get('fullName') or a.get('title')}", oc, msg, file=f,
                            layer="vitest", **({"duration": round(dur / 1000, 3)}
                                               if isinstance(dur, (int, float)) else {})))
        if not tr.get("assertionResults") and tr.get("status") == "failed":
            out.append(_res(f, "error", _lines(str(tr.get("message") or ""))[:SUMMARY_LINES],
                            file=f, layer="vitest"))
    return out + _vitest_gaps(job, out)


_FE_NOISE = re.compile(r"Browserslist|update-browserslist-db|browserslist/update-db|/node_modules/")
_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_VITEST_FILE_LINE = re.compile(r"^\s*([✓❯×])\s+(\S+?\.(?:test|spec)\.[cm]?[jt]sx?)\s+\(")
_TSC_LINE = re.compile(r"^(?P<f>[^\s(:][^(:]*?)(?:\((?P<l>\d+),(?P<c>\d+)\):|:(?P<l2>\d+):(?P<c2>\d+)"
                       r"\s+-)\s*(?P<msg>error TS.*)$")  # --pretty false | pretty format


def _fe_lines(lines: list[str]) -> list[str]:
    """Drop toolchain noise (browserslist nags, node_modules stack frames)."""
    kept = [ln for ln in lines if not _FE_NOISE.search(ln)]
    return kept or lines


def _requested_tests(job: _Job) -> list[str]:
    return [f for f in job.meta.get("files") or []
            if re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", f)]


def _vitest_gaps(job: _Job, out: list[dict]) -> list[dict]:
    """H2 for vitest: a requested test file with no result, or a non-zero exit with no
    failing result (an unhandled error / crashed worker), is an error — never green."""
    tail = _fe_lines(_lines(job.text()))[-SUMMARY_LINES:]
    gaps = [_res(f, "error", ["no vitest result for this requested test file "
                              f"(vitest rc={job.rc})"] + tail, file=f, layer="vitest")
            for f in _requested_tests(job) if not any(_rfile(r) == f for r in out)]
    if job.rc != 0 and not any(r["outcome"] in _FAIL for r in out + gaps):
        gaps.append(_res("desktop::vitest", "error", [f"vitest exited rc={job.rc} without a "
                                                      "failing test"] + tail,
                         file="desktop", layer="vitest"))
    return gaps


def _vitest_partial(job: _Job) -> list[dict]:
    """vitest killed at the deadline (no JSON): file-level results from the default
    reporter's per-file status lines; each requested test file that never reported is
    a timeout. At least one timeout is always returned (a killed run is never green)."""
    done: dict[str, str] = {}
    for raw in _lines(_read_text(job.out)):
        ln = _ANSI_CSI.sub("", raw)
        m = _VITEST_FILE_LINE.match(ln)
        if m:
            done["desktop/" + m.group(2)] = "passed" if m.group(1) == "✓" else "failed"
    out = [_res(f, oc, [] if oc == "passed" else
                ["file failed; per-test detail lost: vitest was killed at the global deadline"],
                file=f, layer="vitest", deadline=True) for f, oc in done.items()]
    out += [_res(f, "timeout", ["did not finish before the global deadline (vitest killed)"],
                 file=f, layer="vitest", deadline=True)
            for f in _requested_tests(job) if f not in done]
    if not any(r["outcome"] == "timeout" for r in out):
        out.append(_res("desktop::vitest", "timeout", ["global deadline reached"]
                        + _fe_lines(_stack(job)), file="desktop", layer="vitest", deadline=True))
    return out


def _tsc_results(job: _Job) -> list[dict]:
    """tsc --noEmit: passed, or ONE ``error`` result whose summary lists the type errors
    as ``desktop/<file>:<line>:<col>: error TS…`` (repo-relative), then a ``+N more`` line."""
    if job.rc == 0:
        return [_res("desktop::tsc", "passed", file="desktop", layer="tsc")]
    errs = []
    for ln in _lines(job.text()):
        m = _TSC_LINE.match(ln.strip())
        if m:
            errs.append(f"desktop/{m['f'].strip()}:{m['l'] or m['l2']}:{m['c'] or m['c2']}: "
                        f"{m['msg']}")
        elif "error TS" in ln:
            errs.append(ln.strip())
    head = errs[:SUMMARY_LINES] or _fe_lines(_lines(job.text()))[-SUMMARY_LINES:]
    if len(errs) > SUMMARY_LINES:
        head.append(f"+{len(errs) - SUMMARY_LINES} more type error(s)")
    return [_res("desktop::tsc", "error", [f"tsc: {len(errs)} type error(s), rc={job.rc}"] + head,
                 file="desktop", layer="tsc")]


def _frontend_summary(results: list[dict]) -> dict:
    vit = [r for r in results if r.get("layer") == "vitest"]
    tsc = [r for r in results if r.get("layer") == "tsc"]
    out = {"vitest": {"passed": sum(r["outcome"] == "passed" for r in vit),
                      "failed": sum(r["outcome"] in _FAIL for r in vit)}}
    if tsc:
        out["tsc"] = {"ok": tsc[0]["outcome"] == "passed",
                      "errors_head": tsc[0]["summary"][:SUMMARY_LINES]}
    return out


# ── B2 failure classification ───────────────────────────────────────────────
def _is_new(ctx, nodeid: str) -> bool:
    """Test absent at HEAD: its file is untracked, or HEAD's file lacks ``def <name>``."""
    f = nodeid.split("::")[0]
    if f not in ctx.head_src:
        ctx.head_src[f] = _git(ctx.repo, "show", f"HEAD:{f}")
    src = ctx.head_src[f]
    if src is None:
        return True
    parts = nodeid.split("::")
    if len(parts) < 2:
        return False
    name = parts[-1].split("[")[0]
    return not re.search(rf"^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(", src, re.M)


def _rerun_state(nodeid: str, results: list[dict]) -> str | None:
    """Current-tree re-run verdict: ``passed`` | ``failing`` | None (not run / no data).

    A not-run test or a deadline timeout tells nothing; a hang that hit the per-test
    timeout (started, no deadline) is a genuine failure.
    """
    exact = [r for r in results if r["nodeid"] == nodeid]
    if not exact:
        if "::" not in nodeid:  # file-level (collection) error that now collects
            ran = [r for r in results if _rfile(r) == nodeid and not r.get("notrun")]
            return "passed" if ran else None
        return None
    r = exact[0]
    if r.get("notrun") or r.get("runner_error") or (r["outcome"] == "timeout" and r.get("deadline")):
        return None
    if r["outcome"] == "passed":
        return "passed"
    return "failing" if r["outcome"] in _FAIL else None


def _head_outcome(nodeid: str, results: list[dict]) -> str | None:
    """HEAD baseline verdict (H3): ``passed`` | ``failed`` only for a test that reached a
    call report; a file-level nodeid is ``failed`` on a real collection error and
    ``passed`` when some test of the file reached its call. Anything else → None."""
    if "::" not in nodeid:
        if any(r["nodeid"] == nodeid and r["outcome"] == "error" and not r.get("notrun")
               and not r.get("runner_error") for r in results):
            return "failed"
        return "passed" if any(_rfile(r) == nodeid and r.get("call") for r in results) else None
    exact = [r for r in results if r["nodeid"] == nodeid and r.get("call")]
    if not exact:
        return None
    o = exact[0]["outcome"]
    return "passed" if o == "passed" else "failed" if o in ("failed", "error") else None


def _baseline_path(cache: Path, sha: str) -> Path:
    return cache / f"baseline-{sha}-v{RUNNER_VERSION}.json"


def _targets_from_nodeids(nodeids: list[str]) -> dict:
    t: dict = {}
    for n in nodeids:
        f = n.split("::")[0]
        if "::" not in n:
            t[f] = None
        elif t.get(f, set()) is not None:
            t.setdefault(f, set()).add(n)
    return t


# Per-sha baseline worktrees are a cache, and every commit makes a new HEAD, so
# without eviction they pile up (a full checkout each). Keep the most recently used
# BASELINE_KEEP; evict an older one only when it has been idle longer than any run
# can last (MAX_DEADLINE + margin) AND its per-sha lock is free — so a worktree
# another session is creating or running tests in is never removed.
BASELINE_KEEP = 2
BASELINE_IDLE_S = MAX_DEADLINE + 1800.0


def _baseline_last_used(base: Path, sha: str) -> float:
    for p in (base / f"{sha}.used", base / sha):
        try:
            return p.stat().st_mtime
        except OSError:
            continue
    return 0.0


def _evict_baselines(repo: Path, base: Path, keep_sha: str | None,
                     keep: int = BASELINE_KEEP, idle_s: float = BASELINE_IDLE_S,
                     now: float | None = None) -> list[str]:
    """Remove baseline worktrees beyond the ``keep`` most recently used. Returns the
    evicted shas. Never touches ``keep_sha``, a worktree used in the last ``idle_s``
    seconds, or one whose lock is held. Also drops the evicted sha's outcome file."""
    if not base.is_dir():
        return []
    now = time.time() if now is None else now
    shas = [d.name for d in base.iterdir()
            if d.is_dir() and re.fullmatch(r"[0-9a-f]{40}", d.name) and d.name != keep_sha]
    shas.sort(key=lambda s: _baseline_last_used(base, s), reverse=True)
    evicted = []
    for sha in shas[max(0, keep - (1 if keep_sha else 0)):]:
        if now - _baseline_last_used(base, sha) < idle_s:
            continue
        fd = _try_lock(base / f"{sha}.lock", 0)
        if fd is None:
            continue  # being created or used right now
        try:
            _remove_worktree(repo, base / sha)
            for p in (base / f"{sha}.used", base.parent / f"baseline-{sha}-v{RUNNER_VERSION}.json"):
                p.unlink(missing_ok=True)
            evicted.append(sha)
        finally:
            _unlock(fd)
        (base / f"{sha}.lock").unlink(missing_ok=True)
    return evicted


def _mark_baseline_used(ctx, base: Path, sha: str) -> None:
    (base / f"{sha}.used").touch()
    try:
        gone = _evict_baselines(ctx.repo, base, keep_sha=sha)
    except OSError as exc:  # eviction is housekeeping; never fail a test run on it
        ctx.warnings.append(f"baseline eviction skipped: {exc}")
        return
    if gone:
        ctx.warnings.append(f"evicted {len(gone)} old baseline worktree(s)")


def _ensure_worktree(ctx, sha: str, time_left: float) -> Path | None:
    """Reusable detached worktree ``<cache>/test-baseline/<sha>`` (created once).

    Each use stamps ``<sha>.used`` and evicts idle older shas (``_evict_baselines``)."""
    base = ctx.cache / "test-baseline"
    wt = base / sha
    fd = _try_lock(base / f"{sha}.lock", min(30.0, max(0.0, time_left - 2)))
    if fd is None:
        ctx.warnings.append("baseline worktree lock busy; failures left unclassified")
        return None
    try:
        if (wt / ".git").exists() and (wt / "backend").is_dir():
            _mark_baseline_used(ctx, base, sha)
            return wt
        if wt.exists():  # half-created dir inside our cache
            _remove_worktree(ctx.repo, wt)
        # Registered while being created: a signal mid-``worktree add`` removes it.
        _PRIVATE_WT[wt] = ctx.repo
        p = _git_run(ctx.repo, "-c", "core.hooksPath=/dev/null", "worktree", "add", "-f",
                     "--detach", str(wt), sha, timeout=max(5.0, min(60.0, time_left - 2)))
        if p is None or p.returncode != 0 or not (wt / "backend").is_dir():
            msg = (p.stderr.strip().splitlines() or ["?"])[-1] if p is not None else "git failed"
            ctx.warnings.append(f"baseline worktree unavailable: {msg[:200]}")
            _remove_worktree(ctx.repo, wt)
            _PRIVATE_WT.pop(wt, None)
            return None
        _PRIVATE_WT.pop(wt, None)  # complete: a reusable per-sha cache from here on
        _mark_baseline_used(ctx, base, sha)
        return wt
    finally:
        _unlock(fd)


def classify_failures(ctx, results: list[dict]) -> dict[str, str]:
    """nodeid → new | flaky | pre-existing | regression | timeout | unknown, within the deadline.

    timeout: a GLOBAL wall-clock deadline killed the shard (``deadline=True``) — the
    test never finished, so it is unverified (still blocks ``_exit_code``) but it is NOT
    a code failure: no HEAD baseline, no re-runs, never routed as ``new``. A per-test
    hang (``deadline=False``, caught by ``PER_TEST_TIMEOUT_S``) is NOT this class.

    new: absent at HEAD. pre-existing without any re-run: an observed failure (it ran
    and failed, ``_rerun_state``) whose HEAD outcome is ``failed`` (looked up first; it
    skips the order-dependence probe, so no ``order_dependent`` info is recorded for it).
    Every other backend failure: its whole FILE is re-run in the
    current tree in original order (H4: an order-dependent failure reproduces), and
    the nodeid's HEAD outcome is looked up in a reusable worktree (cached per sha in
    ``<cache>/baseline-<sha>-v<N>.json``; only tests that reached a call report).

    - re-run still fails  → pre-existing (failed at HEAD) / regression (passed at HEAD)
    - re-run passes       → the shard PREFIX (every shard arg up to and including the
      test's, original order) is re-run (``_split_order_dependent``) to catch state
      leaked from another file of the same shard:
      - prefix fails  → order-dependent (``_classify_order_dependent``): regression
        when the same prefix passes at HEAD (or it passed at HEAD and the HEAD prefix
        is unknown), pre-existing when it fails at HEAD; never flaky
      - prefix passes → flaky (passed at HEAD) / pre-existing (failed at HEAD)
    - HEAD unknown, test not run in the re-run, runner errors, no time → unknown
    """
    cls: dict[str, str] = {}
    todo = []
    for r in results:
        if r["outcome"] not in _FAIL:
            continue
        n = r["nodeid"]
        if r["outcome"] == "timeout" and r.get("deadline"):
            # A GLOBAL wall-clock deadline killed the shard (job.killed), not a per-test
            # hang: the scope ran out of wall time. Never a code regression — its own
            # class so the orchestrator re-runs the scope instead of routing a phantom
            # "new" failure back to BUILD. Still unverified, so _exit_code blocks it.
            # Per-test hangs (deadline falsey) fall through below.
            cls[n] = "timeout"
            continue
        if r.get("runner_error"):
            cls[n] = "unknown"  # zero collected / abnormal shard exit: never green, never cached
        elif n.startswith("import::"):
            cls[n] = "new" if r.get("file") and _is_new(ctx, _rfile(r)) else "unknown"
        elif not n.startswith("backend/"):
            # frontend: no HEAD baseline; a failing test file absent at HEAD is new
            new = n.startswith("desktop/") and r.get("layer") == "vitest" \
                and _is_new(ctx, _rfile(r))
            cls[n] = "new" if new else "unknown"
        elif _is_new(ctx, n):
            cls[n] = "new"
        else:
            todo.append(n)
    if not todo:
        return cls
    if ctx.left() < 3:
        return {**cls, **{n: "unknown" for n in todo}}
    # Failed at HEAD + an observed failure now = pre-existing whatever a re-run shows
    # (every branch below maps HEAD "failed" to pre-existing), so look HEAD up FIRST and
    # skip the file / prefix re-runs for those. A known pre-existing failure recurs on
    # every verify / regression of a run; its HEAD outcome is cached per sha. Unlike the
    # old order, a re-run that would have found no time / no verdict no longer turns it
    # "unknown": it ran and failed, and it fails at HEAD. A not-run test is no observed
    # failure (H3): it still goes through the re-runs.
    seen = [n for n in todo if _rerun_state(n, results) == "failing"]
    if seen:
        head0 = _baseline(ctx, seen, until=time.monotonic() + ctx.left() / 2)
        pre = {n for n in seen if head0.get(n) == "failed"}
        cls.update({n: "pre-existing" for n in pre})
        todo = [n for n in todo if n not in pre]
        if not todo:
            return cls
        if ctx.left() < 3:
            return {**cls, **{n: "unknown" for n in todo}}
    files = {n.split("::")[0]: None for n in todo}  # whole files, original order
    rerun = run_pytest_targets(ctx, files, time.monotonic() + ctx.left() / 2, "rerun")
    passed_now, still = [], []
    for n in todo:
        s = _rerun_state(n, rerun)
        if s == "passed":
            passed_now.append(n)
        elif s == "failing":
            still.append(n)
        else:
            cls[n] = "unknown"
    od, passed_now, lost, plans = _split_order_dependent(ctx, passed_now, results)
    cls.update({n: "unknown" for n in lost})
    if passed_now or still or od:
        head = _baseline(ctx, passed_now + still + od)
        for n in still:
            cls[n] = {"failed": "pre-existing", "passed": "regression"}.get(head[n], "unknown")
        for n in passed_now:
            cls[n] = {"failed": "pre-existing", "passed": "flaky"}.get(head[n], "unknown")
        cls.update(_classify_order_dependent(ctx, od, head, plans))
    return cls


# ── cross-file order dependence (shard prefix re-run) ───────────────────────
CULPRIT_MAX = 3  # order-dependent failures per call that get a culprit bisect


# Shard-arg tokens that modify a file target instead of naming one: --deselect=
# (pulled out to the AC lead shard) and --keep= (a split file's chunk). A prefix
# re-run carries them so it reproduces what the original shard actually ran.
_SHARD_MODIFIERS = ("--deselect=", "--keep=")


def _arg_index(nodeid: str, args: list[str]) -> int | None:
    """Index of the first shard arg (backend-relative file or nodeid) holding ``nodeid``;
    ``--deselect=`` / ``--keep=`` tokens are not targets and never match."""
    r = {"nodeid": nodeid, "file": nodeid.split("::")[0]}
    for i, a in enumerate(args):
        if a.startswith(_SHARD_MODIFIERS):
            continue
        b = "backend/" + a
        if _ref_matches(r, b.split("::")[0], b if "::" in a else None):
            return i
    return None


def _prefix_plans(nodeids: list[str], results: list[dict]) -> tuple[dict, list[str]]:
    """({shard args tuple: [args, cut, [nodeids]]}, nodeids needing no prefix).

    ``cut`` is the furthest arg index of the shard's nodeids, so one run of
    ``args[:cut + 1]`` gives every one of them exactly its original predecessors.
    A test with no arg from ANOTHER file before it (first file of its shard, or an
    AC-lead / requeue / recheck shard led by its own file's nodeids) was already
    covered by the file re-run; one without shard data keeps the file verdict."""
    shard_of = {r["nodeid"]: r.get("shard") for r in results if r.get("shard")}
    plans: dict = {}
    direct = []
    for n in nodeids:
        args = list(shard_of.get(n) or [])
        i = _arg_index(n, args)
        own = _backend_rel(n.split("::")[0])
        if i is None or all(a.split("::")[0] == own for a in args[:i]
                            if not a.startswith(_SHARD_MODIFIERS)):
            direct.append(n)
            continue
        p = plans.setdefault(tuple(args), [args, i, []])
        p[1] = max(p[1], i)
        p[2].append(n)
    return plans, direct


def _prefix_args(args: list[str], cut: int, backend: Path | None = None) -> list[str]:
    """``args[:cut + 1]`` targets (only those whose file exists under ``backend``) in
    order, then every ``--deselect=`` token of the shard (before or after the cut:
    pytest only applies a deselect to a collected nodeid, so extra ones are inert)."""
    sel = [a for a in args[:cut + 1] if not a.startswith(_SHARD_MODIFIERS)
           and (backend is None or (backend / a.split("::")[0]).is_file())]
    return sel + [a for a in args if a.startswith(_SHARD_MODIFIERS)]


def _run_prefixes(ctx, plans: dict, deadline_at: float, tag: str,
                  root: Path | None = None) -> dict[str, str | None]:
    """Run each plan's ``args[:cut + 1]`` in ONE job, in order (in ``root`` — the repo
    or the HEAD worktree, where args whose file is absent are dropped), plus the
    shard's ``--deselect=`` tokens. Returns nodeid → ``_rerun_state`` verdict."""
    backend = (root or ctx.repo) / "backend"
    ctx.seq += 1
    jobs, owners = [], []
    for k, (args, cut, ns) in enumerate(plans.values()):
        sel = _prefix_args(args, cut, backend)
        if not any(not a.startswith(_SHARD_MODIFIERS) for a in sel):
            continue
        jobs.append(_pytest_job(ctx, f"{tag}{ctx.seq}-{k}", sel, backend, root is not None))
        owners.append(ns)
    _run_jobs(jobs, ctx.workers, deadline_at)
    out: dict[str, str | None] = {}
    for j, ns in zip(jobs, owners):
        res = _parse_job(j)
        out.update({n: _rerun_state(n, res) for n in ns})
    return out


def _split_order_dependent(ctx, nodeids: list[str], results: list[dict]):
    """Tests that passed their file re-run → (order_dependent, prefix_passed, unknown, plans).

    Their shard prefix is re-run in the current tree with half the time left; no
    time (or no verdict) → unknown, never flaky."""
    plans, direct = _prefix_plans(nodeids, results)
    if not plans:
        return [], direct, [], plans
    pending = [n for p in plans.values() for n in p[2]]
    if ctx.left() < 3:
        return [], direct, pending, plans
    st = _run_prefixes(ctx, plans, time.monotonic() + ctx.left() / 2, "prefix")
    od = [n for n in pending if st.get(n) == "failing"]
    ok = [n for n in pending if st.get(n) == "passed"]
    lost = [n for n in pending if st.get(n) not in ("failing", "passed")]
    return od, direct + ok, lost, plans


def _classify_order_dependent(ctx, od: list[str], head: dict, plans: dict) -> dict[str, str]:
    """Order-dependent failures → regression | pre-existing | unknown (never flaky).

    Failed alone at HEAD → pre-existing. Otherwise the same prefix is re-run in the
    HEAD worktree: passes → regression, fails → pre-existing; HEAD prefix unknown →
    regression when the test passed at HEAD, else unknown. Records
    ``ctx.order_dep[nodeid]`` (``order_dependent`` + optional ``culprit`` file)."""
    if not od:
        return {}
    info = getattr(ctx, "order_dep", None)
    if info is None:
        info = {}
        ctx.order_dep = info
    need = {n for n in od if head.get(n) != "failed"}
    hp: dict[str, str | None] = {}
    sha = _head_sha(ctx.repo) if need and ctx.left() >= 3 else None
    wt = _ensure_worktree(ctx, sha, ctx.left()) if sha else None
    if wt is not None and ctx.left() >= 2:
        sub = {k: v for k, v in plans.items() if need & set(v[2])}
        hp = _run_prefixes(ctx, sub, ctx.deadline_at, "headprefix", root=wt)
    out = {}
    for n in od:
        if head.get(n) == "failed" or hp.get(n) == "failing":
            out[n] = "pre-existing"
        elif hp.get(n) == "passed" or head.get(n) == "passed":
            out[n] = "regression"
        else:
            out[n] = "unknown"
        info[n] = {"order_dependent": True}
        pre = _od_prefix(n, plans)
        if pre:
            info[n]["prefix"] = pre
    info_c = _culprits(ctx, od, plans)
    for n, c in info_c.items():
        info[n]["culprit"] = c
    return out


PREFIX_KEEP_ARGS = 40  # an order_dependent failure records its prefix (for recheck) up to this


def _od_prefix(nodeid: str, plans: dict) -> list[str] | None:
    """The backend-relative shard args a recheck must re-run, in order, to reproduce
    ``nodeid``'s context: its prefix up to its own arg + the shard's deselects. None
    when too long to keep in evidence (recheck then uses the culprit, if any)."""
    args = next((p[0] for p in plans.values() if nodeid in p[2]), None)
    i = _arg_index(nodeid, args or [])
    if i is None:
        return None
    pre = _prefix_args(args, i)
    if len(pre) > PREFIX_KEEP_ARGS or any(len(a) > NODEID_CAP for a in pre):
        return None
    return pre


CULPRIT_BUDGET_S = 60.0  # per shard bisect: min(this, a third of the time left)


def _culprits(ctx, od: list[str], plans: dict) -> dict[str, str]:
    """nodeid → culprit file, ONE bisect per shard (first CULPRIT_MAX shards with an
    order_dependent failure), each capped at min(CULPRIT_BUDGET_S, left / 3) so it never
    eats the deadline. The shard's first failure leads the bisect; one confirm probe
    (culprit + the shard's OTHER failures' own args, original order) attributes it to
    each of them it reproduces. Best effort; never affects a verdict."""
    out: dict[str, str] = {}
    odset = set(od)
    shards = [p for p in plans.values() if odset & set(p[2])][:CULPRIT_MAX]
    for args, _cut, ns in shards:
        mine = [n for n in ns if n in odset]
        end = time.monotonic() + min(CULPRIT_BUDGET_S, max(0.0, ctx.left()) / 3)
        c = _culprit(ctx, mine[0], args, end)
        if c is None:
            continue
        out[mine[0]] = c
        rest = [n for n in mine[1:] if _arg_index(n, args) is not None]
        if not rest:
            continue
        idx = sorted({_arg_index(n, args) for n in rest})
        if any(args[i].split("::")[0] == _backend_rel(c) for i in idx):
            continue  # the culprit holds a failing test itself: no clean probe
        sel = [_backend_rel(c)] + [args[i] for i in idx] \
            + [a for a in args if a.startswith(_SHARD_MODIFIERS)]
        st = _probe(ctx, sel, rest, end)
        out.update({n: c for n in rest if st.get(n) == "failing"})
    return out


def _probe(ctx, sel: list[str], nodeids: list[str], end: float) -> dict[str, str | None]:
    """One ordered run of ``sel`` (incl. deselect tokens) before ``end``; {} if no time."""
    if ctx.left() < 5 or end - time.monotonic() < 2:
        return {}
    cut = max(i for i, a in enumerate(sel) if not a.startswith(_SHARD_MODIFIERS))
    return _run_prefixes(ctx, {"c": [sel, cut, nodeids]},
                         min(end, time.monotonic() + ctx.left() / 2), "culprit")


def _culprit(ctx, nodeid: str, args: list[str], end: float) -> str | None:
    """Best-effort single culprit file for ``nodeid`` in shard ``args``: bisect the
    prefix's OTHER files (each probe = a half + the test's own arg + the shard's
    deselects, original order) and confirm the last candidate. None when the budget
    (``end``) or the deadline runs out."""
    i = _arg_index(nodeid, args)
    if i is None:
        return None
    own = args[i].split("::")[0]
    desel = [a for a in args if a.startswith(_SHARD_MODIFIERS)]
    cands = [a for a in args[:i] if not a.startswith(_SHARD_MODIFIERS) and a.split("::")[0] != own]

    def fails(subset: list[str]) -> bool | None:
        st = _probe(ctx, subset + [args[i]] + desel, [nodeid], end).get(nodeid)
        return {"failing": True, "passed": False}.get(st or "")

    while len(cands) > 1:
        half = cands[:len(cands) // 2]
        hit = fails(half)
        if hit is None:
            return None
        cands = half if hit else cands[len(half):]
    if not cands or not fails(cands):
        return None
    return "backend/" + cands[0].split("::")[0]

_BASELINE_PY = "_python"  # interpreter the stored HEAD outcomes were measured with


def _baseline(ctx, nodeids: list[str], until: float | None = None) -> dict[str, str | None]:
    """nodeid → HEAD outcome ``passed`` | ``failed`` | None (unknown, never stored).

    Stored per sha with the interpreter id (``_BASELINE_PY``): outcomes measured with
    another interpreter (a venv rebuilt or upgraded) are discarded, not trusted. The
    HEAD run for uncached nodeids ends by ``until`` (default: the global deadline)."""
    sha = _head_sha(ctx.repo)
    if not sha:
        return {n: None for n in nodeids}
    pid = _python_id(ctx.python)
    bpath = _baseline_path(ctx.cache, sha)
    base = _read_json(bpath)
    base = base if isinstance(base, dict) and base.get(_BASELINE_PY) == pid else {}
    need = [n for n in nodeids if base.get(n) not in ("passed", "failed")]
    if need and ctx.left() >= 3:
        wt = _ensure_worktree(ctx, sha, ctx.left())
        if wt is not None and ctx.left() >= 2:
            res = run_pytest_targets(ctx, _targets_from_nodeids(need),
                                     min(until or ctx.deadline_at, ctx.deadline_at),
                                     "base", root=wt)
            fresh = {n: o for n in need if (o := _head_outcome(n, res)) is not None}
            if fresh:
                fd = _try_lock(ctx.cache / f".baseline-{sha}.lock", 5)
                try:
                    cur = _read_json(bpath)
                    cur = cur if isinstance(cur, dict) and cur.get(_BASELINE_PY) == pid else {}
                    cur.update(fresh)
                    cur[_BASELINE_PY] = pid
                    _write_json(bpath, cur)
                finally:
                    _unlock(fd)
                base.update(fresh)
    return {n: base.get(n) if base.get(n) in ("passed", "failed") else None for n in nodeids}


# ── regression L1: tests declared by the BUILD artifact's ac_coverage ───────
def _stage_data(project: str, run_state: dict, stage: str, key: str = "") -> dict | None:
    """The newest ``stage`` artifact's payload (its ``data`` object when ``key`` is not
    at the top level), via the project manifest; None when absent or escaping."""
    stages = [s for s in (run_state or {}).get("stages") or [] if isinstance(s, dict)]
    aid = next((s.get("artifact_id") for s in reversed(stages)
                if s.get("stage") == stage and isinstance(s.get("artifact_id"), str)), None)
    if not aid:
        return None
    arts = (_workspace() / "Projects" / project / ".artifacts").resolve()
    man = _read_json(arts / "manifest.json")
    items = man.get("artifacts") if isinstance(man, dict) else None
    entry = next((e for e in items or [] if isinstance(e, dict) and e.get("id") == aid), None)
    if not entry or not isinstance(entry.get("file"), str):
        return None
    path = (arts / entry["file"]).resolve()
    if arts not in path.parents:
        return None  # manifest entry escaping .artifacts — ignore
    data = _read_json(path)
    key = key or {"build": "ac_coverage", "plan": "exit_criteria"}.get(stage, "")
    if isinstance(data, dict) and key not in data and isinstance(data.get("data"), dict):
        data = data["data"]
    return data if isinstance(data, dict) else None


def _ac_entries(project: str, run_state: dict) -> list[list[str]]:
    """ac_coverage[].test refs per entry: ``file.py::test_fn`` / ``file.py`` (pytest),
    a desktop vitest file (``desktop/src/...test.tsx``, file-level), or
    ``probe:<name>`` (verified from the run's probe evidence)."""
    data = _stage_data(project, run_state, "build")
    cov = data.get("ac_coverage") if isinstance(data, dict) else None
    out = []
    for e in cov if isinstance(cov, list) else []:
        t = e.get("test") if isinstance(e, dict) else None
        refs = [x for x in re.split(r"[,;\s]+", t) if x] if isinstance(t, str) else []
        out.append(refs)
    return out


def _resolve_ref(repo: Path, ref: str) -> tuple[str, str | None] | None:
    """``file.py[::name]`` in any common spelling → (repo-relative file, nodeid|None)."""
    path, _, rest = ref.partition("::")
    rest = re.sub(r"\(\)$", "", rest.strip())
    path = _norm_path(repo, path) or ""
    if not path.endswith(".py"):
        return None
    for cand in (path, f"backend/{path}", f"backend/tests/{path}",
                 f"backend/tests/{Path(path).name}"):
        if (repo / cand).is_file():
            return cand, (f"{cand}::{rest}" if rest else None)
    return None


def _ac_name_status(repo: Path, nodeid: str) -> str:
    """Is the test a ``file.py::name[::name]`` nodeid names defined in its file?
    "present", "missing", or "unknown" when a static read cannot tell: the file does
    not parse, the name may come from an import (``from x import test_a`` / ``*``), or
    a method may be inherited (its class has bases). Only "missing" is acted on. A
    parametrized ``name[id]`` is judged by ``name``."""
    import ast
    file, _, rest = nodeid.partition("::")
    parts = [re.sub(r"\[.*\]$", "", p) for p in rest.split("::") if p]
    if not parts:
        return "present"
    try:
        tree = ast.parse((repo / file).read_text(encoding="utf-8"))
    except (OSError, ValueError, SyntaxError):
        return "unknown"

    def flat(body) -> list:
        # a name defined under a module-level if / try / with still exists
        out = []
        for n in body:
            out.append(n)
            if isinstance(n, (ast.If, ast.Try, ast.With)) or type(n).__name__ == "TryStar":
                for part in ("body", "orelse", "finalbody"):
                    out += flat(getattr(n, part, None) or [])
                for h in getattr(n, "handlers", None) or []:
                    out += flat(h.body)
        return out

    def names(body) -> "tuple[dict, bool]":
        defs: dict = {}
        imported = False
        for n in flat(body):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[n.name] = n
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    if isinstance(t, ast.Name):
                        defs[t.id] = n
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    if a.name == "*":
                        imported = True
                    else:
                        defs[a.asname or a.name.split(".")[0]] = None   # None: imported
        return defs, imported
    body = tree.body
    for i, name in enumerate(parts):
        defs, star = names(body)
        if name not in defs:
            return "unknown" if star else "missing"
        node = defs[name]
        if node is None:
            return "unknown"   # imported: defined elsewhere
        if i + 1 < len(parts):
            if not isinstance(node, ast.ClassDef):
                return "missing"
            if node.bases or node.keywords:
                inner, _ = names(node.body)
                if parts[i + 1] not in inner:
                    return "unknown"   # may be inherited
            body = node.body
    return "present"


def _ref_matches(r: dict, file: str, nodeid: str | None) -> bool:
    """Does result / evidence entry ``r`` match a resolved ref? A whole-file ref
    (``nodeid`` None) compares the entry's file, which a compact pass omits (``_rfile``)."""
    if nodeid is None:
        return _rfile(r) == file
    n = str(r.get("nodeid") or "")
    return n == nodeid or n.startswith(nodeid + "[") or n.startswith(nodeid + "::")


def _resolve_desktop_ref(repo: Path, ref: str, warnings: list | None = None):
    """A desktop vitest ac_coverage ref -> (repo-relative file, None); None if the ref is
    not desktop-shaped; ("", None) if it is desktop-shaped but unresolvable (warned).

    Accepted spellings: ``desktop/src/...``, ``src/...`` (desktop-relative) or a bare
    filename that is unique under ``desktop/src``. Refs are FILE-level only: vitest
    full names contain spaces and _ac_entries splits refs on whitespace, so a
    ``file::name`` ref could never match one test; it is rejected with a warning.
    Separate from _resolve_ref on purpose: mutation specs and every other caller of
    _resolve_ref keep accepting backend .py refs only.
    """
    path, sep, _rest = ref.partition("::")
    if not _DESKTOP_TEST_RE.search(path):
        return None
    if sep:
        if warnings is not None:
            warnings.append(f"ac_coverage desktop ref must be file-level (no ::name); "
                            f"use the file path: {ref}")
        return "", None
    norm = _norm_path(repo, path) or ""
    for cand in (norm, f"desktop/{norm}"):
        if cand.startswith("desktop/src/") and (repo / cand).is_file():
            return cand, None
    if norm and "/" not in norm:
        root = repo / "desktop" / "src"
        # glob.escape: the ref is a literal file name, never a pattern
        hits = sorted(p.relative_to(repo).as_posix() for p in root.rglob(glob.escape(norm))
                      if p.is_file() and "node_modules" not in p.parts) if root.is_dir() else []
        if len(hits) == 1:
            return hits[0], None
        if hits and warnings is not None:
            warnings.append(f"ac_coverage desktop ref is ambiguous ({len(hits)} files): {ref}")
            return "", None
    if warnings is not None:
        warnings.append(f"ac_coverage test ref not found: {ref}")
    return "", None


def _ac_ref_target(repo: Path, ref: str):
    """ac_coverage ref -> (file, nodeid|None) for _ref_matches, or None if unresolvable.
    """
    drv = _resolve_desktop_ref(repo, ref)
    if drv is not None:
        return drv if drv[0] else None
    return _resolve_ref(repo, ref)


def _ac_layer(repo: Path, entries: list[list[str]], results: list[dict]) -> dict:
    ok_n = fail_n = 0
    for refs in entries:
        good = bool(refs)
        for ref in refs:
            rv = _ac_ref_target(repo, ref)
            hits = [r for r in results if rv and _ref_matches(r, *rv)]
            if not hits or any(r.get("outcome") in _FAIL for r in hits) \
                    or not any(r.get("outcome") == "passed" for r in hits):
                good = False
        ok_n, fail_n = ok_n + good, fail_n + (not good)
    return {"run": bool(entries), "pass": ok_n, "fail": fail_n}


# ── invocation context, evidence, cache ─────────────────────────────────────
class _Ctx:
    def __init__(self, repo: Path, cache: Path, deadline: float):
        self.repo, self.cache = repo, cache
        self.warnings: list[str] = []
        self.python = _python(repo, self.warnings)
        self.workers = _worker_count()
        self.t0 = time.monotonic()
        self.deadline_at = self.t0 + deadline
        (cache / "tmp").mkdir(parents=True, exist_ok=True)
        self.workdir = Path(tempfile.mkdtemp(prefix="run-", dir=cache / "tmp"))
        self.plugin_dir = _plugin_dir(cache)
        if _WORKTREE.get("repo") == repo:
            _WORKTREE["site_dir"] = _site_dir(cache)
        self.durations = _load_durations(cache)  # main-run shard packing only
        self.head_src: dict[str, str | None] = {}
        self.order_dep: dict[str, dict] = {}  # nodeid → {order_dependent, culprit?}
        self.seq = 0

    def left(self) -> float:
        return self.deadline_at - time.monotonic()


def _write_evidence(ev_root: Path, mode: str, ev: dict, kind: str = "run",
                    auto_recheck_of: str | None = None,
                    rechecked_from: str | None = None, fix: bool | None = None) -> Path:
    tdir = ev_root / "tests"
    stamp = _stamp()
    path, n = tdir / f"{mode}-{stamp}.json", 1
    while path.exists():  # two calls in one second keep both records
        n += 1
        path = tdir / f"{mode}-{stamp}-{n}.json"
    _write_json(path, ev)
    _write_json(tdir / f"latest-{mode}.json", ev)
    try:
        _append_run_index(tdir, {
            "kind": kind, "mode": mode, "finished_at": ev.get("finished_at"),
            "ts": round(time.time(), 3), "evidence": path.name,
            "cached": bool(ev.get("cached")), "auto_recheck_of": auto_recheck_of,
            "rechecked_from": rechecked_from, "blocking_sig": _blocking_sig(ev),
            **({"fix": bool(fix)} if fix is not None else {}),
            "unfinished": len(_unfinished_failures(ev)),
            "unfinished_files": len({str(f.get("nodeid") or "").split("::")[0]
                                     for f in _unfinished_failures(ev)}),
            "desktop_done": len(_desktop_done(ev))})
    except OSError:  # bookkeeping only; never fails the evidence write
        pass
    try:
        _prune_evidence(tdir)
    except OSError:  # housekeeping only; the evidence above is already written
        pass
    if not ev.get("cached"):
        try:  # main-red registry (core/main_red.py): a red on main gets its own fix run
            from core import main_red
            main_red.record_seen(ev.get("failures"), ev.get("run_id"), ev.get("head_sha"))
        except Exception as e:  # noqa: BLE001 — bookkeeping only, never fails the run
            print(json.dumps({"warning": f"main-red registry not updated: {type(e).__name__}"}),
                  file=sys.stderr)
    return path


RUN_INDEX = "run_index.json"


def _append_run_index(tdir: Path, entry: dict) -> None:
    """Append ``entry`` to ``tests/run_index.json`` (append-only, never pruned; format in
    the module docstring) under ``.run_index.lock``, atomically. A held lock past 5 s
    still appends (like the tdd index): a lost record would under-count the budget."""
    fd = _try_lock(tdir / ".run_index.lock", 5)
    try:
        cur, tampered = _index_read(tdir / RUN_INDEX)
        if tampered:  # never re-seal an index the runner did not write; the caps refuse
            print(json.dumps({"warning": tampered + ": this run is not recorded"}),
                  file=sys.stderr)
            return
        cur = [e for e in cur if isinstance(e, dict)] if isinstance(cur, list) else []
        cur.append(entry)
        _write_json(tdir / RUN_INDEX, cur)
    finally:
        _unlock(fd)


def _blocking_sig(ev: dict) -> list[str]:
    """Sorted nodeids of the BLOCKING failures in this evidence — the circuit breaker's
    per-round fingerprint. Non-blocking failures (pre-existing / flaky, via
    ``_failure_nonblocking``) are excluded, so a stable pre-existing set never reads as a
    stall. Deadline timeouts are included: re-running re-runs only the unfinished tests, so
    the set shrinks while there is progress; the SAME set N rounds running means there is
    none (a scope that cannot finish under this load) and a human should decide."""
    return sorted(f.get("nodeid", "") for f in (ev.get("failures") or [])
                  if isinstance(f, dict) and not _failure_nonblocking(f))


# Full-scope runs (verify / regression: the change's whole dependency scope) happen at
# DELIVER only, after the last change (XG 2026-10-20), at most FULL_SCOPE_MAX_ROUNDS per
# run: landing reads the latest scope report on the final code, so a scope run in BUILD
# or TEST was never used. Fix rounds run the affected tests (tdd) or break tests (prove).
# Continuing the tests a deadline left unrun is the same round. A recheck (also DELIVER
# only) is not a round: it re-runs the reds of the latest report and the tests of the
# files changed since, and is how a fix is judged once the rounds are used up; at most
# RECHECK_MAX_FIXES rechecks on changed code per run. A goal run keeps its own loop.
FULL_SCOPE_MODES = ("verify", "regression", "recheck")
FULL_SCOPE_STAGES = ("deliver",)
FULL_SCOPE_MAX_ROUNDS = 2
# A recheck on changed code is a fix: this many per run, before or after the round cap
# (design: each red test gets three fix attempts). A recheck on unchanged code only
# finishes what was left and is not counted.
RECHECK_MAX_FIXES = 3


def _unfinished(f: dict) -> bool:
    """A failure entry for a test that never ran to an outcome (deadline / not run)."""
    return bool(f.get("deadline") or f.get("notrun")) and not f.get("order_dependent")


def _unfinished_failures(ev: dict) -> list:
    return [f for f in ev.get("failures") or [] if isinstance(f, dict) and _unfinished(f)]


# T5: a continuation makes progress when the count of tests the deadline left unrun
# shrinks by STALL_SHRINK, or at least one more file finished (_stalled_calls); counting
# passes hid a stall (+3 passes per call, the rest never started).
STALL_SHRINK = 0.10    # a call must cut the unfinished count by at least this share
STALL_SPLIT_AFTER = 2  # this many stalled calls in a row -> split per file
STALL_TRIP_AFTER = 4   # this many -> stop (the breaker)


def _continuation_rows(ev_root: Path, mode: str) -> "list[dict]":
    """Run-index rows of the current round of ``mode``: its first run, then each
    continuation's merged result (kind=overlay), oldest first; only rows with an int
    ``unfinished``."""
    idx, _tampered = _index_read(ev_root / "tests" / RUN_INDEX)  # breaker refuses it
    if not isinstance(idx, list):
        return []
    rows = [e for e in idx if isinstance(e, dict)]
    start = max((i for i, e in enumerate(rows) if e.get("kind") == "run"
                 and e.get("mode") == mode and not e.get("auto_recheck_of")), default=None)
    if start is None:
        return []
    out = [rows[start]] + [e for e in rows[start + 1:]
                           if e.get("kind") == "overlay" and e.get("mode") == mode]
    return [e for e in out if isinstance(e.get("unfinished"), int)]


def _continuation_series(ev_root: Path, mode: str) -> "list[int]":
    """Unfinished test counts of the current round of ``mode`` (see _continuation_rows)."""
    return [e["unfinished"] for e in _continuation_rows(ev_root, mode)]


def _desktop_key(repo: Path) -> str:
    """The desktop inputs a vitest result ran on: HEAD plus every uncommitted change
    under desktop/ (``_desktop_state``; package-lock.json and the vitest / ts configs are
    under desktop/ too). A desktop result is reused only under the key it was recorded
    with; if git cannot say what changed, the key is unique per call (nothing reused)."""
    return hashlib.sha256(_desktop_state(repo).encode()).hexdigest()[:32]


#: prefix of the desktop_key of a call whose desktop inputs changed while it ran. Each
#: such key is unique (random suffix), so it equals no other key, another call's
#: unknown key included: nothing is reused from it, and a later recheck's results
#: replace all of its desktop results.
DESKTOP_KEY_UNKNOWN = "unknown"


def _desktop_key_of_call(repo: Path, plan: dict) -> str:
    """The key taken before the call ran, if the desktop inputs are still the same at its
    end; a unique ``DESKTOP_KEY_UNKNOWN-…`` key when they changed meanwhile (results of
    mixed inputs)."""
    start = plan.get("desktop_key")
    if start and start == _desktop_key(repo):
        return start
    return f"{DESKTOP_KEY_UNKNOWN}-{os.urandom(8).hex()}"


def _is_desktop_result(r: dict) -> bool:
    return str(_rfile(r)).startswith("desktop/src/") or r.get("nodeid") == "desktop::vitest"


def _desktop_ran(ev: dict) -> bool:
    """``ev`` holds a vitest result (a test, a file-level entry or the vitest placeholder)."""
    return any(isinstance(r, dict) and _is_desktop_result(r)
               for r in list(ev.get("results") or []) + list(ev.get("failures") or []))


def _desktop_passed(ev: dict) -> "set[str]":
    """Desktop test files whose every result in ``ev`` passed (or was skipped by the test
    itself): what an explicit recheck on unchanged desktop inputs leaves out of the related
    set. A file with any failed, errored or unfinished result is not in it, so a recheck
    always re-runs a failure."""
    outcomes: dict[str, set] = {}
    for r in list(ev.get("results") or []) + list(ev.get("failures") or []):
        if not isinstance(r, dict):
            continue
        f = str(_rfile(r))
        if f.startswith("desktop/src/") and _DESKTOP_TEST_RE.search(f):
            outcomes.setdefault(f, set()).add(r.get("outcome"))
    return {f for f, oc in outcomes.items() if oc and oc <= {"passed", "skipped"}}


def _desktop_done(ev: dict) -> "set[str]":
    """Desktop test files with a result (not a deadline timeout) in ``ev``: what a vitest
    continuation leaves out of the related set, and how its progress is counted (vitest
    reports one ``desktop::vitest`` placeholder for the unrun rest, not a count)."""
    return {str(_rfile(r)) for r in list(ev.get("results") or []) + list(ev.get("failures") or [])
            if isinstance(r, dict) and r.get("outcome") != "timeout"
            and str(_rfile(r)).startswith("desktop/src/") and _DESKTOP_TEST_RE.search(str(_rfile(r)))}


def _continuation_desktop_series(ev_root: Path, mode: str) -> "list[int | None]":
    """Desktop test files with a result, aligned with _continuation_series."""
    return [e.get("desktop_done") if isinstance(e.get("desktop_done"), int) else None
            for e in _continuation_rows(ev_root, mode)]


def _continuation_file_series(ev_root: Path, mode: str) -> "list[int | None]":
    """Unfinished FILE counts, aligned with _continuation_series (None for a row written
    before the run index recorded them)."""
    return [e.get("unfinished_files") if isinstance(e.get("unfinished_files"), int) else None
            for e in _continuation_rows(ev_root, mode)]


def _stalled_calls(series: "list[int]", files: "list[int | None] | None" = None,
                   desktop: "list[int | None] | None" = None) -> int:
    """Trailing calls in a row that made no progress. A call made progress when it cut the
    unfinished test count by at least STALL_SHRINK, OR (with ``files``, aligned) finished
    at least one more file while the test count fell. The second clause matters for a very
    large scope: 30 equal files, two finished per call, is 29 -> 27 -> 25 unfinished,
    under 10% each time but steady; judged by share alone it split, then tripped the
    breaker on a scope that a dozen more calls would finish (design §10)."""
    files = files if files is not None and len(files) == len(series) else [None] * len(series)
    desktop = desktop if desktop is not None and len(desktop) == len(series) else [None] * len(series)
    n = 0
    pairs = list(zip(series[:-1], series[1:], files[:-1], files[1:], desktop[:-1], desktop[1:]))
    for a, b, fa, fb, da, db in reversed(pairs):
        shrank = a > 0 and b <= a * (1 - STALL_SHRINK)
        finished_a_file = fa is not None and fb is not None and fb < fa and b < a
        # a vitest continuation: more desktop test files have a result than before
        more_desktop = da is not None and db is not None and db > da
        if a > 0 and not (shrank or finished_a_file or more_desktop):
            n += 1
        else:
            break
    return n


def _open_stage(run_state: dict) -> "str | None":
    """The stage open now: started and not completed (the latest-started if several)."""
    best = None
    for stage, t in (run_state.get("stage_timing") or {}).items():
        if not isinstance(t, dict) or t.get("completed_at") or not t.get("started_at"):
            continue
        if best is None or str(t["started_at"]) > best[0]:
            best = (str(t["started_at"]), stage)
    return best[1] if best else None


def _is_fix_recheck(base: dict, current: dict) -> bool:
    """A recheck runs on CHANGED code (a fix) when the change set's file digests differ
    from those of the report it rechecks; a report with no digests counts as changed."""
    before = base.get("changed_files") if isinstance(base, dict) else None
    return not isinstance(before, dict) or not before or dict(before) != dict(current)


def _full_scope_refusal(ev_root: Path, mode: str, run_state: dict,
                        fix: bool = True) -> "dict | None":
    """Refuse a full-scope call outside DELIVER; a NEW verify / regression round past
    FULL_SCOPE_MAX_ROUNDS; a recheck on changed code (*fix*) past RECHECK_MAX_FIXES.
    A recheck is not a full-scope round: past the cap it is how a fix is judged, so the
    cap never locks against the landing check (which reads the overlaid report).
    Operator override: SWARM_GATE_FORCE=full_scope."""
    if mode not in FULL_SCOPE_MODES or force_enabled("full_scope"):
        return None
    if str(run_state.get("profile") or "").strip().lower() == "goal":
        return None
    stage = _open_stage(run_state)
    if stage is None:
        return None  # no stage open: not a pipeline stage call (manual use, a test rig)
    narrow = ("for a fix, run the tests it affects: `--mode tdd --files <test files>` "
              "(or `--mode prove` for break tests)")
    if stage not in FULL_SCOPE_STAGES:
        return {"mode": mode, "ok": False, "full_scope": {"refused": True, "stage": stage},
                "error": (f"--mode {mode} runs the change's whole dependency scope, and that "
                          f"runs once per run, at DELIVER after the last change (landing reads "
                          f"only that report). Not in {stage}: {narrow}.")}
    idx, tampered = _index_read(ev_root / "tests" / RUN_INDEX)
    if tampered:
        return {"mode": mode, "ok": False, "full_scope": {"refused": True, "tampered": True},
                "error": f"{tampered}: the run's full-scope rounds cannot be counted, so "
                         "none is allowed. Operator override only: SWARM_GATE_FORCE=full_scope"}
    own = [e for e in idx if isinstance(e, dict) and e.get("kind") == "run"
           and not e.get("auto_recheck_of") and not e.get("cached")] \
        if isinstance(idx, list) else []
    if mode == "recheck":
        if not fix:
            return None  # unchanged code: it finishes the last report, it is not a fix
        fixes = [e for e in own if e.get("mode") == "recheck" and e.get("fix", True)]
        if len(fixes) >= RECHECK_MAX_FIXES:
            return {"mode": mode, "ok": False,
                    "full_scope": {"refused": True, "fix_rechecks": len(fixes)},
                    "error": (f"{len(fixes)} rechecks on changed code already ran in this run "
                              f"(limit {RECHECK_MAX_FIXES}): stop fixing. The change does not "
                              "land while a test in latest-regression.json is red or "
                              "unfinished: complete DELIVER with those tests named in "
                              "attention_flags (REPORT §10); the code stays in the run's "
                              "worktree. Operator override only: SWARM_GATE_FORCE=full_scope")}
        return None
    rounds = [e for e in own if e.get("mode") in ("verify", "regression")]
    if len(rounds) >= FULL_SCOPE_MAX_ROUNDS:
        return {"mode": mode, "ok": False, "full_scope": {"refused": True, "rounds": len(rounds)},
                "error": (f"{len(rounds)} full-scope rounds already ran in this run (limit "
                          f"{FULL_SCOPE_MAX_ROUNDS}): no new round. Fix the failing tests, "
                          "then run `--mode recheck`: it re-runs the failing and unfinished "
                          "tests of the last report and the tests of every file changed "
                          "since, on the current code, and updates latest-regression.json "
                          f"(at most {RECHECK_MAX_FIXES} rechecks on changed code). "
                          "Operator override only: SWARM_GATE_FORCE=full_scope")}
    return None


def _breaker_result(mode: str, reason: str) -> dict:
    """Abort payload (exit 2) when the circuit breaker trips. Re-running is futile (the
    breaker re-trips on the unchanged run_index). The run is autonomous, so the action is
    never a checkpoint or a human: stop testing, finish the run not push-ready."""
    return {"mode": mode, "ok": False, "circuit_breaker": {"tripped": True, "reason": reason},
            "error": "circuit breaker: " + reason,
            "action": ("stop re-running tests and stop fixing: the loop is not converging. "
                       "Complete the CURRENT stage now with what remains (stage-complete "
                       "degrades a gate error at once), then DELIVER with push_ready false "
                       "and this reason in attention_flags (REPORT §10). Landing is decided "
                       "by the test evidence: while a test is red or unfinished the change "
                       "does not land; then checkpoint with the failing tests named as the "
                       "reason (the code stays in the run's worktree). "
                       "Operator override only: SWARM_GATE_FORCE=circuit_breaker")}


def _circuit_breaker_check(ev_root: Path, mode: str, profile: str = "") -> dict | None:
    """Refuse another verify/recheck/regression when this run is in a non-converging loop
    (prevents run_340478c9's 42-round / ~18h spin). Reads the append-only run_index;
    returns an abort dict (caller exits 2) or None to proceed. tdd is never gated — it is
    the write-tests phase and is expected to repeat. Operator override:
    SWARM_GATE_FORCE=circuit_breaker. A goal run's tdd calls are bounded too
    (GOAL_TDD_MAX): the goal loop's stop rule, enforced here instead of by the card."""
    if force_enabled("circuit_breaker"):
        return None
    if mode in ("tdd", "verify", "recheck", "regression"):
        _idx, tampered = _index_read(ev_root / "tests" / RUN_INDEX)
        if tampered:
            return _breaker_result(mode, tampered + ": the rounds of this run cannot be counted")
    if mode == "tdd" and profile == "goal":
        idx, _t = _index_read(ev_root / "tests" / RUN_INDEX)
        n = sum(1 for e in idx if isinstance(e, dict) and e.get("mode") == "tdd") \
            if isinstance(idx, list) else 0
        if n >= GOAL_TDD_MAX:
            return _breaker_result(
                mode, f"{n} tdd calls in this goal run (limit {GOAL_TDD_MAX}): the cycle loop "
                "is not converging — exit UNMET (stage-complete goal_cycle with dod_met "
                "false and what is left), then DELIVER")
        return None
    if mode not in ("verify", "recheck", "regression"):
        return None
    idx, _t = _index_read(ev_root / "tests" / RUN_INDEX)
    if not isinstance(idx, list):
        return None
    runs = [e for e in idx if isinstance(e, dict) and e.get("kind") == "run"
            and e.get("mode") in ("verify", "recheck", "regression")]
    # a continuation (same inputs, only the unfinished tests: auto_recheck_of) is the
    # same round, not a new one — a 90-file scope takes ~5 calls per round
    rounds = [e for e in runs if not e.get("auto_recheck_of")]
    if len(rounds) >= CIRCUIT_MAX_ROUNDS:
        return _breaker_result(
            mode, f"{len(rounds)} verify/recheck/regression rounds in this run (hard limit "
            f"{CIRCUIT_MAX_ROUNDS}) — the build<->verify loop is not converging")
    if mode in ("verify", "regression"):
        stalled = _stalled_calls(_continuation_series(ev_root, mode),
                                 _continuation_file_series(ev_root, mode),
                                 _continuation_desktop_series(ev_root, mode))
        if stalled >= STALL_TRIP_AFTER:
            return _breaker_result(
                mode, f"{stalled} continuation calls in a row cut the count of unfinished "
                f"tests by less than {STALL_SHRINK:.0%} and finished no file (even split per "
                "file) — the scope "
                "does not finish")
    vr = runs  # regression too: a continuation that stops shrinking is a stall
    sigs = [tuple(e.get("blocking_sig") or []) for e in vr[-CIRCUIT_STALL_ROUNDS:]]
    if len(sigs) >= CIRCUIT_STALL_ROUNDS and sigs[0] and all(s == sigs[0] for s in sigs):
        shown = list(sigs[0])[:5]
        return _breaker_result(
            mode, f"{CIRCUIT_STALL_ROUNDS} consecutive verify/recheck/regression calls left the SAME "
            f"blocking failure(s) unfixed ({', '.join(shown)}"
            f"{'...' if len(sigs[0]) > 5 else ''}) — re-running is not making progress")
    return None


# Evidence retention: every call writes ``<mode>-<ts>.json`` (+ ``logs-<mode>-<ts>/``
# on failures); a busy run reached 66 entries / 126 MB. Keep every latest-*.json
# (the only files stage-complete reads), the newest EVIDENCE_KEEP of each kind per
# mode, and any logs dir a latest-*.json points at.
EVIDENCE_KEEP = 3
EVIDENCE_MIN_AGE_S = 120.0  # never prune an entry this fresh (a concurrent writer's)
_EV_NAME = re.compile(r"(?P<mode>[a-z]+)-(?P<ts>\d{8}T\d{6})(?:-(?P<n>\d+))?\.json")
_LOG_NAME = re.compile(r"logs-(?P<mode>[a-z]+)-(?P<ts>\d{8}T\d{6})(?:-(?P<n>\d+))?")


def _prune_evidence(tdir: Path, keep: int = EVIDENCE_KEEP,
                    min_age_s: float = EVIDENCE_MIN_AGE_S, now: float | None = None) -> list[str]:
    """Delete timestamped evidence files / log dirs beyond the newest ``keep`` per
    (kind, mode) in THIS run's ``tests/`` dir. Returns the removed names. Skipped when
    another invocation holds the prune lock; never touches latest-*, dotfiles (atomic
    write temps), other names, entries younger than ``min_age_s``, or a referenced logs dir."""
    fd = _try_lock(tdir / ".prune.lock", 2)
    if fd is None:
        return []
    try:
        return _prune_locked(tdir, keep, min_age_s, time.time() if now is None else now)
    finally:
        _unlock(fd)


def _prune_locked(tdir: Path, keep: int, min_age_s: float, now: float) -> list[str]:
    referenced = set()
    for m in TEST_MODES:
        ev = _read_json(tdir / f"latest-{m}.json")
        if isinstance(ev, dict) and isinstance(ev.get("logs"), str):
            referenced.add(Path(ev["logs"]).name)
    groups: dict[tuple[str, str], list[tuple[tuple, Path]]] = {}
    for p in tdir.iterdir():
        is_dir = p.is_dir() and not p.is_symlink()
        mt = (_LOG_NAME if is_dir else _EV_NAME).fullmatch(p.name)
        if mt is None or mt["mode"] not in TEST_MODES or (not is_dir and not p.is_file()):
            continue
        rank = (mt["ts"], int(mt["n"] or 1))
        groups.setdefault(("logs" if is_dir else "ev", mt["mode"]), []).append((rank, p))
    removed = []
    for entries in groups.values():
        entries.sort(key=lambda e: e[0], reverse=True)
        for _, p in entries[keep:]:
            try:
                fresh = now - p.stat().st_mtime < min_age_s
            except OSError:
                continue
            if fresh or p.name in referenced:
                continue
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
            removed.append(p.name)
    return sorted(removed)


# Evidence a recheck may build on: verify or regression only. Never tdd (it covers only
# its --files, so its failure list drops everything else the regression found) and never
# another recheck (each recheck is laid over its base, so the base already carries it;
# basing on a recheck would skip the overlay onto the file the TEST stage reads).
RECHECK_BASE_MODES = ("verify", "regression")


def _latest_evidence(ev_root: Path, modes: tuple = TEST_MODES) -> dict | None:
    """Newest ``latest-<mode>.json`` among ``modes``, by file mtime: ``finished_at`` has
    one-second resolution, and an overlay written in the same second as the recheck it
    carries used to tie."""
    best, best_t = None, None
    for m in modes:
        p = ev_root / "tests" / f"latest-{m}.json"
        ev = _read_json(p)
        if not isinstance(ev, dict):
            continue
        try:
            t = p.stat().st_mtime
        except OSError:
            continue
        if best is None or t >= best_t:
            best, best_t = ev, t
    return best


def _recent_failure_files(ev_root: Path) -> list[str]:
    out = []
    for m in TEST_MODES:
        ev = _read_json(ev_root / "tests" / f"latest-{m}.json")
        fails = ev.get("failures") if isinstance(ev, dict) else None
        for f in fails if isinstance(fails, list) else []:
            n = str(f.get("nodeid") or "") if isinstance(f, dict) else ""
            # A deadline timeout or a pre-existing failure says nothing about this change:
            # counting it pulled every unfinished file to the front of the next call.
            if isinstance(f, dict) and (f.get("outcome") == "timeout"
                                        or f.get("classification") in ("timeout", "pre-existing")):
                continue
            if n.startswith("backend/") and n.split("::")[0].endswith(".py"):
                out.append(n.split("::")[0])
    return out


def _bump_tdd(ev_root: Path, files: list[str]) -> dict[str, int]:
    """Run-scoped tdd invocation counter per file (never blocks)."""
    idx = ev_root / "tests" / "tdd_index.json"
    fd = _try_lock(ev_root / "tests" / ".tdd_index.lock", 5)
    try:
        data, tampered = _index_read(idx)
        if tampered:  # a display counter: never re-seal it, report no repeats
            print(json.dumps({"warning": tampered}), file=sys.stderr)
            return {}
        data = data if isinstance(data, dict) else {}
        for f in files:
            data[f] = int(data.get(f, 0) or 0) + 1
        _write_json(idx, data)
        return {f: data[f] for f in files}
    finally:
        _unlock(fd)


def _python_id(python: str) -> str:
    cfg = Path(python).absolute().parent.parent / "pyvenv.cfg"  # no resolve: venv python is a symlink
    try:
        st = cfg.stat()
        return f"{python}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return python


_PYTEST_CONFIGS = ("backend/pyproject.toml", "backend/pytest.ini", "backend/setup.cfg",
                   "backend/tox.ini")


def _conftests_for(rel: str) -> list[str]:
    """Every conftest.py pytest would load for ``rel``: its dir up to backend/."""
    out = []
    d = Path(rel).parent
    while d.parts and d.parts[0] == "backend":
        out.append((d / "conftest.py").as_posix())
        d = d.parent
    return out


def _key_inputs(ctx, targets: dict, smoke: dict[str, str], changed: list[str],
                sgraph: dict | None) -> dict[str, str]:
    """path → digest of every file a test-mode result depends on (H1).

    Selected test files, every conftest.py on their path, the TRANSITIVE closure of
    backend-local imports of all of those (and of the smoke modules), pytest config
    files, and every changed file. Missing paths hash as ``missing``.
    """
    roots: set[str] = set()
    for f in targets:
        roots.add(f)
        roots.update(_conftests_for(f))
    roots.update(mf for mf in smoke.values() if mf)
    existing = {r for r in roots if (ctx.repo / r).is_file()}
    files = set(roots) | import_closure(ctx.repo, sgraph, existing)
    files |= set(_PYTEST_CONFIGS) | {"backend/conftest.py", "backend/tests/conftest.py"}
    files |= set(changed)
    return {f: _file_digest(ctx.repo / f) for f in sorted(files)}


def _cache_key(ctx, mode: str, targets: dict, smoke: dict[str, str], extra: list[str],
               inputs: dict[str, str], head: str | None) -> str:
    """digest(version, mode, interpreter, HEAD, target nodeid sets, smoke module names,
    explicit inputs, and the digest of every file in ``inputs``)."""
    h = hashlib.sha256()

    def add(label: str, value: str) -> None:
        h.update(label.encode() + b"\0" + value.encode() + b"\0")

    add("v", RUNNER_VERSION)
    add("mode", mode)
    add("python", _python_id(ctx.python))
    add("head", head or "none")
    for f in sorted(targets):
        sel = targets[f]
        add("n:" + f, json.dumps(sorted(sel) if sel is not None else None))
    for m in sorted(smoke):
        add("s", m)
    for f, d in sorted(inputs.items()):
        add("f:" + f, d)
    for x in extra:
        add("x", x)
    return h.hexdigest()[:32]


def _tree_digest(inputs: dict[str, str], changed: list[str], run_files) -> dict[str, str]:
    """M2: {path: sha256} of the changed inputs and the files actually run, from the
    same digests the cache key was built from (so a cache hit reports the current tree)."""
    keep = set(changed) | set(run_files)
    return {f: d for f, d in sorted(inputs.items()) if f in keep}


def _v1_cache_key(ctx, runner, mode: str, plan: dict, targets: dict,
                  inputs: dict[str, str], extra: list[str], frontend_key: str) -> str:
    """Complete immutable identity for the active v1 whole-call result cache."""
    from core.run_protocol.cache import cache_key
    from core.run_protocol.risk_selection import RISK_RULES_VERSION
    selectors = {path: (sorted(nodeids) if nodeids is not None else None)
                 for path, nodeids in sorted(targets.items())}
    return cache_key({
        "kind": "test_result", "repo": runner.repo_name, "tree": runner.code_tree,
        "pipeline_code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "protocol_version": 1,
        "rules_version": f"runner-{RUNNER_VERSION}/{RISK_RULES_VERSION}",
        "mode": mode, "python": _python_id(ctx.python), "selectors": selectors,
        "smoke": dict(sorted(plan.get("smoke", {}).items())),
        "dependency_scope": sorted(plan.get("l2") or []),
        "risk_rules": list(plan.get("risk_rules") or []),
        "manifest": runner.manifest_identity(plan.get("changed") or []),
        "inputs": dict(sorted(inputs.items())), "extra": list(extra),
        "frontend_key": frontend_key or None,
        "runner_code_hash": hashlib.sha256(
            (_BACKEND_DIR / "core" / "run_protocol" / "runner.py").read_bytes()).hexdigest(),
    })


def _v1_cache_value(ev: dict) -> dict:
    """Remove invocation-local data before cross-run immutable comparison/storage."""
    drop = {"run_id", "started_at", "finished_at", "duration_s", "cached", "warnings",
            "logs", "worktree", "reused_from", "repo_mutations", "tdd_repeats"}
    value = {key: val for key, val in ev.items() if key not in drop}
    value["results"] = [{key: val for key, val in row.items() if key != "duration"}
                        for row in value.get("results") or [] if isinstance(row, dict)]
    return value


def _v1_cache_conflict(ctx, runner, key: str, new_value=None) -> None:
    from core.run_protocol.cache import conflict_hashes, value_hash
    hashes = conflict_hashes(ctx.cache / "v1", "test", key)
    if hashes is None:
        first_hash = "0" * 64
        new_hash = value_hash(new_value) if new_value is not None else "0" * 64
    else:
        first_hash, new_hash = hashes
    runner.record_cache_conflict(key=key, first_hash=first_hash, new_hash=new_hash)
    ctx.warnings.append(f"v1 cache key {key[:12]} is poisoned by conflicting cross-run values; ignored")


def _v1_cache_read(ctx, runner, key: str):
    from core.run_protocol.cache import CacheError, read
    try:
        return read(ctx.cache / "v1", "test", key)
    except CacheError as exc:
        if "poisoned" not in str(exc):
            raise
        _v1_cache_conflict(ctx, runner, key)
        return None


def _v1_cache_put(ctx, runner, key: str, ev: dict) -> None:
    from core.run_protocol.cache import put_once
    value = _v1_cache_value(ev)
    if put_once(ctx.cache / "v1", "test", key, value) == "conflict":
        _v1_cache_conflict(ctx, runner, key, value)


# ── per-file reuse between verify / regression runs of one pipeline run ────
# Measured on the recorded runs (2026-10-18): of 49,049 L2 test executions in
# consecutive verify / regression calls of one run, 9,548 (301 files) were of an
# all-pass file on the same HEAD with no changed file edited in between.
REUSE_MODES = ("verify", "regression")
_REUSABLE_OUTCOMES = ("passed", "skipped")


def _bvt_keys(ctx, files: list[str], sgraph: dict | None) -> dict[str, str]:
    """BVT file → cross-run key: runner version, interpreter, pytest configs, the file, its
    conftest chain and their transitive backend-local imports — NOT HEAD and not the run's
    changed set, so a pass carries over to the next run while none of those inputs moved.
    Only for the CI BVT manifest (pure OS-layer logic tests, CI re-runs them on push);
    an edit to anything in a file's closure changes its key and it runs again."""
    return _l2_file_keys(ctx, files, [], sgraph, None, cross_run=True)


def _bvt_cache_reuse(ctx, plan: dict, sgraph: dict | None) -> "tuple[dict, dict]":
    """(file → cached evidence results, file → key) for this regression's BVT files."""
    files = [f for f in plan.get("bvt") or [] if plan["targets"].get(f, 0) is None]
    if not files:
        return {}, {}
    keys = _bvt_keys(ctx, files, sgraph)
    out = {}
    for f, k in keys.items():
        hit = _read_json(ctx.cache / "bvt" / f"{k}.json")
        if isinstance(hit, list) and any(isinstance(r, dict) and r.get("outcome") == "passed"
                                         for r in hit) and all(
                isinstance(r, dict) and r.get("outcome") in _REUSABLE_OUTCOMES for r in hit):
            out[f] = [dict(r) for r in hit]
    return out, keys


def _bvt_cache_store(ctx, ev: dict, keys: dict, reused: dict) -> None:
    """Cache each BVT file whose every result in ``ev`` passed / skipped (run here)."""
    by_file: dict[str, list[dict]] = {}
    for r in ev.get("results") or []:
        if isinstance(r, dict) and isinstance(r.get("nodeid"), str):
            by_file.setdefault(_rfile(r), []).append(r)
    for f, k in keys.items():
        rs = by_file.get(f)
        if f in reused or not rs or not any(r.get("outcome") == "passed" for r in rs) \
                or not all(r.get("outcome") in _REUSABLE_OUTCOMES for r in rs):
            continue
        try:
            (ctx.cache / "bvt").mkdir(parents=True, exist_ok=True)
            _write_json(ctx.cache / "bvt" / f"{k}.json", rs)
        except OSError:
            pass


def _l2_file_keys(ctx, files, changed: list[str], sgraph: dict | None,
                  head: str | None, cross_run: bool = False) -> dict[str, str]:
    """test file → digest of every input a WHOLE-file result of it depends on.

    Runner version, interpreter, pytest configs (no HEAD), the file, its conftest chain and
    their transitive backend-local imports, and EVERY changed file — the same input set
    the whole-run key (``_key_inputs``) trusts. Static imports alone are not enough: a
    module can load a changed file by computed name (``importlib``), by bare-name
    import of a sibling script, by path (``spec_from_file_location``, ``open``) or by
    walking a directory, and none of those is an import edge (red-team 2026-10-18
    proved a stale pass through ``importlib.import_module``). So any edit to a changed
    file between two calls re-runs every file; reuse covers re-runs on an unchanged
    tree (a mode switch, a call that was not cacheable, a different selection)."""
    deps = _Deps(ctx.repo, sgraph)
    digests: dict[str, str] = {}

    def digest(rel: str) -> str:
        if rel not in digests:
            digests[rel] = _file_digest(ctx.repo / rel)
        return digests[rel]

    # No HEAD in the key (T6, XG 2026-10-20): main moves every few minutes and a HEAD key
    # re-ran every file each time. A main commit reaches a test through its import closure
    # (hashed below) or, by dynamic load, only through a changed file of THIS run (all in
    # ``changed``, hashed for every file); a main commit that loads code by computed name
    # outside both is CI's (BVT) to catch, accepted.
    common = hashlib.sha256("\0".join([RUNNER_VERSION, _python_id(ctx.python),
                                       "bvt-cross-run" if cross_run else "per-file"]).encode())
    for p in sorted(set(changed) | set(_PYTEST_CONFIGS)):
        common.update(p.encode() + b"\0" + digest(p).encode() + b"\0")
    out: dict[str, str] = {}
    for f in files:
        roots = [f] + _conftests_for(f)
        h = common.copy()
        for p in sorted(deps.closure([r for r in roots if (ctx.repo / r).is_file()]) | set(roots)):
            h.update(p.encode() + b"\0" + digest(p).encode() + b"\0")
        out[f] = h.hexdigest()[:32]
    return out


def _reusable(ev_root: Path, run_id: str | None, plan: dict,
              keys: dict[str, str]) -> tuple[dict[str, list[dict]], list[dict]]:
    """(file → earlier results, sources) for each keyed L2 file this run's latest
    verify / regression evidence (newest first) ran under the same key with every
    result passed or skipped. Never an AC file, a nodeid-scoped target or a file named
    by a runner error. Results are the evidence entries, copied."""
    ac = set(plan.get("ac_files") or [])
    evs = []
    for m in REUSE_MODES:
        ev = _read_json(ev_root / "tests" / f"latest-{m}.json")
        if (isinstance(ev, dict) and ev.get("schema") == 1 and ev.get("run_id") == run_id
                and isinstance(ev.get("l2_file_keys"), dict)):
            evs.append(ev)
    evs.sort(key=lambda e: str(e.get("finished_at") or ""), reverse=True)
    out: dict[str, list[dict]] = {}
    sources: list[dict] = []
    for ev in evs:
        by_file: dict[str, list[dict]] = {}
        for r in ev.get("results") or []:
            if isinstance(r, dict) and isinstance(r.get("nodeid"), str):
                by_file.setdefault(_rfile(r), []).append(r)
        broken = {str(n).split("::")[0] for n in ev.get("runner_errors") or []}
        took = []
        for f, k in keys.items():
            if (f in out or f in ac or f in broken or f not in plan["targets"]
                    or plan["targets"][f] is not None or ev["l2_file_keys"].get(f) != k):
                continue
            rs = by_file.get(f)
            if rs and all(r.get("outcome") in _REUSABLE_OUTCOMES for r in rs):
                out[f] = [dict(r) for r in rs]
                took.append(f)
        if took:
            sources.append({"mode": ev.get("mode"), "finished_at": ev.get("finished_at"),
                            "files": len(took)})
    return out, sources


def _desktop_state(repo: Path) -> str:
    """HEAD + the content of every uncommitted change under desktop/.

    vitest runs a named test against whatever source it imports, so a cached result
    is only valid for the same desktop source: keying on the named test files alone
    served a stale pass (or failure) after the code under test changed. If git cannot
    say what changed, the key is unique per call (fail closed: no cache hit)."""
    head = _head_sha(repo)
    out = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--", "desktop")
    if head is None or out is None:
        return "uncacheable-" + os.urandom(8).hex()
    h = hashlib.sha256(head.encode())
    entries, paths = out.split("\0"), set()
    i = 0
    while i < len(entries):
        e = entries[i]
        if len(e) > 3:
            paths.add(e[3:])
            if e[0] in "RC":
                i += 1  # porcelain -z: a rename/copy is followed by its source path
        i += 1
    paths = sorted(paths)
    for p in paths:
        h.update(p.encode() + b"\0" + _file_digest(repo / p).encode() + b"\0")
    return h.hexdigest()


def _frontend_key(ctx, mode: str, files: list[str], changed=()) -> str:
    """digest(version, mode, _desktop_state — HEAD + every uncommitted desktop/
    change, so vitest/tsc never reuse a result across changed source — the listed
    files and package-lock.json). ``changed`` is accepted for call-site
    compatibility; _desktop_state already covers every changed desktop file."""
    h = hashlib.sha256((RUNNER_VERSION + mode + _desktop_state(ctx.repo)).encode())
    for f in sorted(files) + ["desktop/package-lock.json"]:
        h.update(f.encode() + b"\0" + _file_digest(ctx.repo / f).encode() + b"\0")
    return h.hexdigest()[:32]


# ── test-mode planning ──────────────────────────────────────────────────────
def _input_path(repo: Path, p: str) -> str | None:
    """--files entry (abs, repo-relative, cwd-relative or backend-relative) → repo-relative."""
    path, sep, rest = p.partition("::")
    main = _BACKEND_DIR.parent
    if _WORKTREE.get("repo") == repo and Path(path).is_absolute():
        try:  # a main-checkout path names the run's worktree twin (§6.2)
            path = str(repo / Path(os.path.realpath(path)).relative_to(os.path.realpath(main)))
        except ValueError:
            pass
    cands = [Path(path)] if Path(path).is_absolute() else [
        Path.cwd() / path, repo / path, repo / "backend" / path]
    for c in cands:
        if c.is_file():
            rel = _norm_path(repo, str(c.resolve()))
            if rel:
                return rel + (sep + rest if sep else "")
    return None


def _add_target(targets: dict, file: str, nodeid: str | None) -> None:
    if nodeid is None:
        targets[file] = None
    elif targets.get(file, set()) is not None:
        targets.setdefault(file, set()).add(nodeid)


def _plan_tdd(ctx, ns, plan: dict) -> str | None:
    if not ns.files:
        return "tdd needs --files (the test files of this RED/GREEN step)"
    if len(ns.files) > TDD_MAX_FILES:
        return (f"tdd takes at most {TDD_MAX_FILES} files, got {len(ns.files)}: batch your "
                f"changes and pick the ≤{TDD_MAX_FILES} files of this step, or use --mode verify")
    for raw in ns.files:
        rel = _input_path(ctx.repo, raw)
        if rel is None:
            return f"--files entry not found: {raw}"
        f = rel.split("::")[0]
        if f.startswith("desktop/"):
            plan["desktop"].append(f)
        elif _is_backend_test(f):
            _add_target(plan["targets"], f, rel if "::" in rel else None)
        else:
            return f"tdd runs test files only (backend/tests/test_*.py or desktop/): {raw}"
        plan["explicit"].append(rel)
    return None


def _smoke_modules(ctx, changed: list[str]) -> dict[str, str]:
    out = {}
    for f in changed:
        if f.startswith("backend/tests/") or Path(f).name == "conftest.py":
            continue
        m = module_of(f)
        if m and (ctx.repo / f).is_file():
            out[m] = f
    return dict(sorted(out.items()))


CONTEXT_REPLAY_MAX_S = 45.0  # recorded seconds of earlier same-file tests a continuation replays


def _replay_file_context(ctx, plan: dict, prev: dict, prev_failures: list,
                         no_replay: bool = False) -> None:
    """A continuation runs a file's unfinished tests in a fresh process, without the
    tests that ran before them in that file: one that only fails after an earlier
    test's leftovers would pass here (red-team 2026-10-19). So, per file, replay the
    tests that already passed earlier in it, then the unfinished ones, as ONE ordered
    job, when that prefix costs at most CONTEXT_REPLAY_MAX_S recorded seconds. A
    longer prefix is not replayed (it would eat the call and never finish): the tests
    run alone and are listed in the evidence's ``context_unverified`` (REPORT §10)."""
    unfinished: dict[str, list[str]] = {}
    for f in prev_failures:
        n = str(f.get("nodeid") or "")
        if ((f.get("deadline") or f.get("notrun")) and not f.get("order_dependent")
                and n.startswith("backend/") and "::" in n and not f.get("nodeid_truncated")):
            unfinished.setdefault(n.split("::")[0], []).append(n)
    if not unfinished:
        return
    order = [r["nodeid"] for r in prev.get("results") or []
             if isinstance(r, dict) and isinstance(r.get("nodeid"), str)]
    passed = {r["nodeid"] for r in prev.get("results") or []
              if isinstance(r, dict) and r.get("outcome") == "passed"}
    alone = 0
    # T4: a file's prefix is replayed on ONE continuation only. Replaying it every call
    # spent half of each 95 s call on tests that had passed, so a slow file never got
    # through (run_600db8ac: 3 tests per call). After that one replay its remaining tests
    # run alone and are listed as context-unverified, never hidden.
    replayed = set(prev.get("context_replayed") or [])
    for file, ids in unfinished.items():
        first = min((order.index(n) for n in ids if n in order), default=None)
        pre = [n for n in order[:first or 0] if n.split("::")[0] == file and n in passed]
        if not pre:
            continue
        cost = sum(ctx.durations.get(n, DEFAULT_TEST_COST_S) for n in pre)
        if (no_replay or file in replayed or cost > CONTEXT_REPLAY_MAX_S
                or len(pre) + len(ids) > PREFIX_KEEP_ARGS):
            alone += len(ids)
            # their pass is real but not in-file-context: listed (REPORT §10), never hidden
            plan.setdefault("context_unverified", []).extend(ids)
            continue
        sel = plan["targets"].get(file)
        if isinstance(sel, (set, list)):
            rest = [n for n in sel if n not in set(ids)]
            if rest:
                plan["targets"][file] = type(sel)(rest)
            else:
                plan["targets"].pop(file, None)
        plan.setdefault("ordered", []).append([_backend_rel(n) for n in pre + ids])
        plan.setdefault("context_replayed", []).append(file)
    if replayed:
        plan["context_replayed"] = sorted(replayed | set(plan.get("context_replayed") or []))
    if alone:
        ctx.warnings.append(
            f"{alone} unfinished test(s) continued without replaying the earlier tests of "
            f"their file (that prefix records over {CONTEXT_REPLAY_MAX_S:g}s): an order "
            "dependence on those earlier tests would not show in this call")


def _od_context_args(repo: Path, f: dict) -> list[str] | None:
    """Ordered backend-relative shard args that reproduce an ``order_dependent``
    failure ``f``'s context: its recorded ``prefix``, else ``[culprit, own arg]``.
    None (plain re-target; the overlay then keeps the failure over a pass) when
    neither is usable or an arg names a file outside backend/ or not on disk."""
    n = str(f.get("nodeid") or "")
    if not n.startswith("backend/"):
        return None
    backend = repo / "backend"
    pre = f.get("prefix")
    if not (isinstance(pre, list) and pre and all(isinstance(a, str) for a in pre)):
        c = f.get("culprit")
        if not (isinstance(c, str) and c.startswith("backend/")):
            return None
        own = n.split("::")[0] if f.get("nodeid_truncated") or "::" not in n else n
        pre = [_backend_rel(c), _backend_rel(own)]
    for a in pre:
        if a.startswith(_SHARD_MODIFIERS):
            continue
        p = _contained(backend, a.split("::")[0])
        if p is None or not p.is_file():
            return None
    if _arg_index(n, pre) is None and not f.get("nodeid_truncated"):
        return None  # the prefix must hold the test itself
    return list(pre)


def _plan_ac_refs(ctx, plan: dict) -> "tuple[list[str], list[str]]":
    """Regression L1: turn the BUILD ac_coverage refs (``plan["ac_entries"]``) into
    targets. Fills ``plan`` targets / ac_first / ac_files / ac_desktop and
    ``ac_refs_missing``; returns (ac_files, probe refs)."""
    ac_files: list[str] = []
    ac_desktop: list[str] = []
    probe_refs: list[str] = []
    for refs in plan["ac_entries"]:
        for ref in refs:
            drv = _resolve_desktop_ref(ctx.repo, ref, ctx.warnings)
            if drv is not None:  # desktop: vitest file list only, never a pytest target
                if drv[0]:
                    ac_desktop.append(drv[0])
                continue
            rv = _resolve_ref(ctx.repo, ref)
            if rv is None or (rv[1] is not None
                              and _ac_name_status(ctx.repo, rv[1]) == "missing"):
                # a stale BUILD ref (a renamed / deleted test) is a bookkeeping gap,
                # never a pytest target: as a nodeid pytest would report it as a runner
                # error and block landing. Recorded, so push_ready names it.
                ctx.warnings.append(f"ac_coverage test ref not found: {ref}"
                                    + ("" if rv is None else " (no such test in its file: "
                                                             "renamed or deleted?)"))
                if ".py" in ref:   # a test ref, not a stray word of a free-text field
                    plan.setdefault("ac_refs_missing", []).append(ref)
                continue
            _add_target(plan["targets"], *rv)
            ac_files.append(rv[0])
            if rv[1] is not None:
                plan.setdefault("ac_first", {}).setdefault(rv[0], set()).add(rv[1])
    plan["ac_files"] = list(dict.fromkeys(ac_files))  # L1: never reused (_reusable)
    plan["ac_desktop"] = list(dict.fromkeys(ac_desktop))
    return ac_files, probe_refs




def _risk_expansion(ns, changed: list[str], repo: Path) -> tuple[list[str], list[str], list[str]]:
    """V13 risk table; inert for legacy runs, bounded by protected contract.json."""
    if not ns.run_id:
        return [], [], []
    from config import get_app_data_dir
    from core.run_protocol.risk_selection import expand
    from core.run_protocol.runner import read_contract
    contract = read_contract(get_app_data_dir(), ns.run_id)
    if contract is None:
        return [], [], []
    cap = contract.get("risk_test_file_cap")
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise ValueError("v1 contract lacks a positive risk_test_file_cap")
    return expand(repo, changed, cap=cap)
def _plan(ctx, ns, ev_root: Path, run_state: dict, graph: dict,
          sgraph: dict | None = None) -> tuple[dict, str | None]:
    plan = {"targets": {}, "skipped": [], "smoke": {}, "ac_entries": [], "l2": [],
            "desktop": [], "explicit": [], "rechecked": 0}
    changed = changed_files(ctx.repo, run_state, ctx.warnings, (ns.project, ns.run_id))
    plan["changed"] = changed
    mode = ns.mode
    if mode == "tdd":
        return plan, _plan_tdd(ctx, ns, plan)
    if mode in ("verify", "regression") and ns.run_id:
        err = shared_tree_scope_error(ctx.repo, run_state, ns.run_id, ns.project,
                                      ev_root.parent.parent)
        if err:
            return plan, err
    scope = changed
    prev_failures: list = []
    lost: list[str] = []
    if mode == "recheck":
        auto_of = getattr(ns, "auto_recheck_of", None)
        prev = _latest_evidence(ev_root, (auto_of,) if auto_of else RECHECK_BASE_MODES)
        if not prev:
            return plan, "recheck needs previous verify or regression evidence; run --mode verify first"
        prev_failures = [f for f in prev.get("failures") or [] if isinstance(f, dict)]
        if auto_of:
            # A continuation runs only what the deadline left unrun; a test that ran
            # and failed keeps its result (the overlay leaves base entries it did not
            # re-run), never re-run on unchanged code for a lucky pass.
            prev_failures = [f for f in prev_failures if _unfinished(f)
                             or ("::" not in str(f.get("nodeid") or "")
                                 and f.get("classification") == "timeout")]
        plan["base_mode"], plan["base_finished_at"] = prev.get("mode"), prev.get("finished_at")
        for f in prev_failures:
            n = str(f.get("nodeid") or "")
            ordered = _od_context_args(ctx.repo, f) if f.get("order_dependent") else None
            if ordered:
                # Re-run it after its prefix / culprit, in order: alone it passes.
                plan.setdefault("ordered", []).append(ordered)
                plan.setdefault("od_in_context", []).append(n)
                plan["rechecked"] += 1
            elif n.startswith("backend/") and (ctx.repo / n.split("::")[0]).is_file():
                whole = "::" not in n or f.get("nodeid_truncated")  # capped id: rerun its file
                _add_target(plan["targets"], n.split("::")[0], None if whole else n)
                plan["rechecked"] += 1
            elif n.startswith("import::") and _module_file(ctx.repo, n[len("import::"):]):
                mod = n[len("import::"):]
                plan["smoke"][mod] = _module_file(ctx.repo, mod)
                plan["rechecked"] += 1
            elif n in ("desktop::vitest", "desktop::tsc") or n.startswith("desktop/"):
                plan["desktop_recheck"] = True
                if _DESKTOP_TEST_RE.search(n.split("::")[0]):
                    # named to vitest: a file outside the related set of the changed
                    # files would otherwise never be answered
                    plan.setdefault("desktop_rerun", []).append(n.split("::")[0])
                plan["desktop_tsc"] = plan.get("desktop_tsc") or n == "desktop::tsc"
                plan["rechecked"] += 1
            else:
                lost.append(n or "<no nodeid>")
        stalled = (_stalled_calls(_continuation_series(ev_root, auto_of),
                                  _continuation_file_series(ev_root, auto_of),
                                  _continuation_desktop_series(ev_root, auto_of)) if auto_of else 0)
        split = bool(auto_of) and stalled >= STALL_SPLIT_AFTER
        _replay_file_context(ctx, plan, prev, prev_failures, no_replay=split)
        if split:
            # T5: no replay (it is what eats the call) and the cheapest files first, so
            # whatever fits finishes; the rest stay listed, never hidden.
            def cost(f):
                sel = plan["targets"][f]
                return sum(ctx.durations.get(n, DEFAULT_TEST_COST_S) for n in (sel or [f]))
            plan["targets"] = dict(sorted(plan["targets"].items(), key=lambda kv: cost(kv[0])))
            plan["per_file_split"] = True
            ctx.warnings.append(f"continuation stalled {stalled} calls in a row (unfinished "
                                f"count cut by < {STALL_SHRINK:.0%}, no file finished): split per file, cheapest "
                                "first, no replay of earlier tests")
        if auto_of and not plan.get("per_file_split"):
            started = {_rfile(r) for r in prev.get("results") or []
                       if isinstance(r, dict) and r.get("outcome") not in ("timeout", None)}
            plan["targets"] = dict(sorted(plan["targets"].items(),
                                          key=lambda kv: kv[0] in started))
            plan["fresh_first"] = True
        before = prev.get("changed_files") if isinstance(prev.get("changed_files"), dict) else {}
        scope = [f for f in changed if before.get(f) != _file_digest(ctx.repo / f)]
    recent = _recent_failure_files(ev_root) if mode != "recheck" else []
    sel, skipped, _ = select_backend(ctx.repo, scope, graph, None, recent, sgraph)
    risk_selected, risk_rules, risk_overflow = _risk_expansion(ns, scope, ctx.repo)
    sel = list(dict.fromkeys([*sel, *risk_selected]))
    skipped = [*skipped, *risk_overflow]
    if risk_rules:
        plan["risk_rules"] = risk_rules
    if risk_overflow:
        plan["risk_overflow"] = risk_overflow
        ctx.warnings.append(
            f"risk expansion exceeded contract cap by {len(risk_overflow)} test file(s); "
            "the final is incomplete, not partially green")
    for f in sel:
        _add_target(plan["targets"], f, None)
    plan["l2"], plan["skipped"] = sel, list(skipped)
    if mode in ("verify", "regression"):
        plan["transitive_unrun"] = transitive_unselected(ctx.repo, sgraph, graph, scope, sel)
    if mode == "regression":
        plan["ac_entries"] = _ac_entries(ns.project, run_state)
        ac_files, probe_refs = _plan_ac_refs(ctx, plan)
        # AC targets first in priority order (a whole-file AC ref is not split out).
        plan["targets"] = {**{f: plan["targets"][f] for f in dict.fromkeys(ac_files)},
                           **plan["targets"]}
        mods = _smoke_modules(ctx, changed)
        names = list(mods)
        plan["smoke"] = {m: mods[m] for m in names[:SMOKE_CAP]}
        plan["skipped"] += [f"import::{m}" for m in names[SMOKE_CAP:]]
        # L3: the CI BVT, run here because the pipeline stops at a local commit and never
        # sees CI. Same manifest CI reads; lowest priority, never cut.
        plan["bvt"] = [f for f in _bvt_files(ctx.repo, ctx.warnings) if f not in plan["targets"]]
        for f in plan["bvt"]:
            _add_target(plan["targets"], f, None)
    # A desktop failure being rechecked reruns the run's desktop files even if none
    # changed since: the failure is the reason to run, not a digest change.
    plan["desktop"] = [f for f in (changed if plan.get("desktop_recheck") else scope)
                       if f.startswith("desktop/src/") and (ctx.repo / f).is_file()]
    # AC-named desktop tests run in the same vitest job (and so join its cache key).
    plan["desktop"] += [f for f in plan.get("ac_desktop", []) if f not in plan["desktop"]]
    if plan["desktop"]:
        plan["desktop"] += [f for f in dict.fromkeys(plan.get("desktop_rerun", []))
                            if f not in plan["desktop"] and (ctx.repo / f).is_file()]
        # the desktop inputs this call's vitest results run on (checked again at its end)
        plan["desktop_key"] = _desktop_key(ctx.repo)
    if mode == "recheck" and getattr(ns, "auto_recheck_of", None) and plan["desktop"] and prev:
        # A continuation runs only what the deadline left unrun (as for backend tests):
        # every desktop test file with a result in the report it continues stays out
        # of the related set; the overlay keeps those results.
        plan["desktop_done"] = sorted(_desktop_done(prev))
    elif (mode == "recheck" and plan["desktop"] and prev and prev.get("desktop_key")
          and prev.get("desktop_key") == plan.get("desktop_key")):
        # An explicit recheck on the desktop inputs its base ran on continues the vitest
        # part too: files whose every test passed there stay out of the related set (a
        # file with a failure always re-runs), so a set larger than one call finishes.
        plan["desktop_done"] = sorted(_desktop_passed(prev))
    if mode == "recheck" and plan.get("desktop_recheck") and not plan["desktop"]:
        lost.append("desktop:: (no desktop/src file in this run's change set)")
    if mode == "recheck" and lost and getattr(ns, "auto_recheck_of", None):
        # An automatic recheck that cannot re-run every failure (e.g. a shard:: runner
        # error) would keep that failure forever; the caller falls back to a full run.
        return plan, f"auto-recheck cannot re-target: {', '.join(lost[:5])}"
    if mode == "recheck" and prev_failures:
        if not (plan["targets"] or plan["smoke"] or plan["desktop"]
                or plan.get("ordered")):
            # Never green: there were failures and nothing here re-runs them.
            return plan, (f"recheck found {len(prev_failures)} previous failure(s) but could not "
                          f"re-target any of them ({', '.join(lost[:5]) or 'none matched'}); "
                          "run --mode verify or regression instead")
        if lost:
            ctx.warnings.append(f"recheck could not re-target {len(lost)} previous failure(s), "
                                f"not re-run: {', '.join(lost[:8])}")
    return plan, None


# ── test-mode execution ─────────────────────────────────────────────────────
def _auto_recheck(ctx, ns, ev_root: Path, run_state: dict, deadline: float,
                  prev: dict, v1=None) -> "tuple[dict, int] | None":
    """Same inputs as the last ``ns.mode`` run (same cache key) and the deadline left
    tests unrun: CONTINUE it. A recheck runs only those unrun tests and is overlaid onto
    the ``ns.mode`` evidence; tests that ran and failed keep their result (never re-run
    on unchanged code). The caller reports a finished non-cacheable result as it stands. Returns None — run the full mode instead — when the
    recheck cannot re-target every failure, skipped tests for budget, or did not land
    on THIS mode's evidence."""
    note = (f"inputs identical to the last {ns.mode}, whose result was not cacheable: "
            "ran a recheck of its failed / not-run tests and overlaid the results")
    ctx.warnings.append(note)  # lands in the overlaid evidence; withdrawn on fallback
    ns2 = argparse.Namespace(**{**vars(ns), "mode": "recheck", "auto_recheck_of": ns.mode})
    out, code = _run_test_mode(ctx, ns2, ev_root, run_state, deadline, v1=v1)
    merged = _read_json(ev_root / "tests" / f"latest-{ns.mode}.json")
    if out.get("error") or out.get("overlaid_onto") != ns.mode or not isinstance(merged, dict):
        if note in ctx.warnings:
            ctx.warnings.remove(note)
        ctx.warnings.append(f"auto-recheck did not update latest-{ns.mode}.json "
                            f"({out.get('error') or 'nothing overlaid'}); running the full {ns.mode}")
        return None  # caller runs the full mode
    tdir = ev_root / "tests"
    newest = max(tdir.glob(f"{ns.mode}-*.json"), key=lambda q: q.stat().st_mtime, default=None)
    return _test_output(merged, newest or tdir / f"latest-{ns.mode}.json"), _exit_code(merged)


def _overlay_evidence(repo: Path, base: dict, re_ev: dict, ac_entries: list,
                      auto: bool = False) -> dict:
    """``base`` (a verify / regression evidence) with ``re_ev`` (a recheck of it) laid over
    it by nodeid: a rechecked test's new result replaces its old one, everything else
    stays. Counts, failures, runner errors and layers are recomputed from the merged
    results; ``rechecked_from`` records which run was overlaid. An ``order_dependent``
    base failure is replaced only by a recheck that re-ran it in its context (listed in
    ``re_ev["od_in_context"]``) or that failed it again; otherwise it is kept and named
    in ``rechecked_from.held_order_dependent``. Entries may be compact (no ``file``)."""
    new = {r["nodeid"]: r for r in re_ev.get("results") or []
           if isinstance(r, dict) and isinstance(r.get("nodeid"), str)}
    old = {r["nodeid"]: r for r in base.get("results") or []
           if isinstance(r, dict) and isinstance(r.get("nodeid"), str)}
    base_fails = [f for f in base.get("failures") or [] if isinstance(f, dict)]
    if (_desktop_ran(re_ev) and "desktop_key" in base
            and re_ev.get("desktop_key") != base.get("desktop_key")):
        # The recheck ran vitest on other desktop inputs than the base: every base desktop
        # result is stale. Each base desktop test file the recheck did not report stays
        # listed as not run, so a later call runs it on the current code. A base written
        # before desktop keys existed (no field) keeps its results, as it always did.
        reported = {str(_rfile(r)) for r in new.values()}
        lost = sorted({str(_rfile(r)) for r in list(old.values()) + base_fails
                       if _is_desktop_result(r) and _DESKTOP_TEST_RE.search(str(_rfile(r)))}
                      - reported)
        old = {n: r for n, r in old.items() if not _is_desktop_result(r)}
        base_fails = [f for f in base_fails if not _is_desktop_result(f)]
        for f in lost:
            ph = _res(f, "timeout", ["not run on the current desktop code: its earlier result "
                                     "came from other desktop inputs"], file=f, layer="vitest",
                      notrun=True)
            old[f] = ph
            base_fails.append(ph)
    # An order_dependent failure only reproduces after its shard prefix / culprit: a
    # pass from a recheck that did not re-run that context is no verdict on it.
    in_ctx = set(re_ev.get("od_in_context") or [])
    held = {f.get("nodeid") for f in base_fails if f.get("order_dependent")
            and f.get("nodeid") not in in_ctx
            and (new.get(f.get("nodeid")) or {}).get("outcome") not in _FAIL}
    new = {n: r for n, r in new.items() if n not in held}
    # A shard that never started is recorded under a FILE-level placeholder; once the
    # recheck reports that file's tests, the placeholder is answered (it used to stay a
    # timeout forever, so a continuation never went green).
    answered = {n for n, r in old.items() if "::" not in n and n not in new
                and (r.get("notrun") or r.get("outcome") == "timeout")
                and any(k.startswith(n + "::") for k in new)}
    if re_ev.get("vitest_finished") and "desktop::vitest" not in new:
        # the vitest job of the recheck ran its (rest of the) related set to the end
        answered |= {n for n, r in old.items() if n == "desktop::vitest"
                     and (r.get("notrun") or r.get("outcome") == "timeout")}
        answered |= {f.get("nodeid") for f in base_fails if f.get("nodeid") == "desktop::vitest"
                     and f.get("outcome") == "timeout"}
    old = {n: r for n, r in old.items() if n not in answered}
    base_fails = [f for f in base_fails if f.get("nodeid") not in answered]
    results = [new.get(n, r) for n, r in old.items()] + [r for n, r in new.items() if n not in old]
    counts = {"passed": 0, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for r in results:
        counts[_COUNT_KEY.get(r.get("outcome"), "errors")] += 1
    failures = [f for f in base_fails if f.get("nodeid") not in new] \
        + [f for f in re_ev.get("failures") or [] if isinstance(f, dict) and f.get("nodeid") not in held]
    runner_errors = [n for n in base.get("runner_errors") or [] if n not in new] \
        + list(re_ev.get("runner_errors") or [])
    layers = {k: dict(v) for k, v in (base.get("layers") or {}).items() if isinstance(v, dict)}

    def gained(kind) -> int:  # nodeids that went from not-passed to passed
        return sum(1 for n, r in new.items() if kind(n) and r.get("outcome") == "passed"
                   and (old.get(n) or {}).get("outcome") != "passed")
    if "dependency_scoped" in layers:
        layers["dependency_scoped"]["pass"] = layers["dependency_scoped"].get("pass", 0) + gained(
            lambda n: n.startswith("backend/"))
    if "import_smoke" in layers:
        layers["import_smoke"]["pass"] = layers["import_smoke"].get("pass", 0) + gained(
            lambda n: n.startswith("import::"))
    if "ci_bvt" in layers:
        bvt_set = set(base.get("bvt_files") or [])
        layers["ci_bvt"]["pass"] = layers["ci_bvt"].get("pass", 0) + gained(
            lambda n: n.split("::")[0] in bvt_set)
    if base.get("mode") == "regression" and ac_entries:
        layers["ac_driven"] = _ac_layer(repo, ac_entries, results)
    ev = dict(base)
    cr = sorted(set(base.get("context_replayed") or []) | set(re_ev.get("context_replayed") or []))
    if cr:
        ev_replayed = cr
    else:
        ev_replayed = None
    cu = sorted(set(base.get("context_unverified") or []) | set(re_ev.get("context_unverified") or []))
    if cu:
        ev["context_unverified"] = cu
    if ev_replayed:
        ev["context_replayed"] = ev_replayed
    # The tree the merged evidence describes is the RECHECK's: it re-selected tests for
    # every file changed since the base (an overlay with budget skips is never made), so
    # its file digests, HEAD and tree digest are the ones staleness checks must compare.
    # Sticky: once a recheck on changed code is laid over this report, a later overlay on
    # the same code (finishing what a deadline left) keeps the mark. Only a fresh verify /
    # regression, which never goes through here, writes a report without it.
    prior = base.get("rechecked_from") if isinstance(base.get("rechecked_from"), dict) else {}
    fix = bool(prior.get("fix")) or (_is_fix_recheck(base, re_ev.get("changed_files") or {})
                                    if isinstance(re_ev.get("changed_files"), dict) else False)
    # worktree too: the landing check binds the change to it. Left at the base's tree,
    # a green recheck on fixed code was refused as "produced on a different tree".
    for k in ("changed_files", "head_sha", "tree_digest", "worktree"):
        if re_ev.get(k) is not None:
            ev[k] = re_ev[k]
    if _desktop_ran(re_ev) and re_ev.get("desktop_key"):
        # the merged desktop results are the recheck's inputs' now (stale ones dropped
        # above); a recheck without vitest leaves the base's key with the base's results
        ev["desktop_key"] = re_ev["desktop_key"]
    if isinstance(base.get("l2_file_keys"), dict):
        # A rechecked file's results now come from a later tree: its old key no longer
        # describes them, so it is never reused from this evidence (_reusable).
        redone = {_rfile(r) for r in new.values()}
        ev["l2_file_keys"] = {f: k for f, k in base["l2_file_keys"].items() if f not in redone}
    ru = base.get("reused_from")
    if isinstance(ru, dict):
        # A rechecked file's results are fresh now, not reused: report only the rest.
        redone = {_rfile(r) for r in new.values()}
        keep = [f for f in ru.get("files") or [] if f not in redone]
        n = sum(1 for r in results if _rfile(r) in set(keep))
        if keep:
            ev["reused_from"] = {**ru, "files": keep, "tests": n}
        else:
            ev.pop("reused_from", None)
    counts["not_run"] = _not_run_count(failures)
    ev.update(results=results, counts=counts, failures=failures, runner_errors=runner_errors,
              layers=layers, finished_at=_now_iso(), cached=False,
              rechecked_from={"finished_at": base.get("finished_at"),
                              "rechecked": len(new), "auto": auto,
                              **({"fix": True} if fix else {}),
                              **({"held_order_dependent": sorted(held)} if held else {})})
    return ev


def _dedupe_results(results: list[dict]) -> list[dict]:
    """One result per nodeid (a recheck's ordered prefix job can re-run a test another
    shard also ran): the first FAILING one wins, else the first; first-seen order."""
    pick: dict[str, dict] = {}
    for r in results:
        n = r["nodeid"]
        if n not in pick or (r["outcome"] in _FAIL and pick[n]["outcome"] not in _FAIL):
            pick[n] = r
    return list(pick.values())


def _ref_nodeids(repo: Path, project: str, run_state: dict, ac_entries: list) -> list[str]:
    """Resolved nodeid refs (not whole-file refs) of the run's BUILD ac_coverage and
    PLAN ``exit_criteria[kind=test].ref``: evidence never caps a nodeid one of them
    names (``_capped_id``), or the ref could no longer match it."""
    refs = [r for e in (ac_entries or _ac_entries(project, run_state)) for r in e]
    plan = _stage_data(project, run_state, "plan")
    crit = plan.get("exit_criteria") if isinstance(plan, dict) else None
    for c in crit if isinstance(crit, list) else []:
        if isinstance(c, dict) and c.get("kind") == "test" and isinstance(c.get("ref"), str):
            refs.append(c["ref"])
    out = []
    for ref in refs:
        rv = _resolve_ref(repo, ref)
        if rv and rv[1]:
            out.append(rv[1])
    return list(dict.fromkeys(out))


def _run_test_mode(ctx, ns, ev_root: Path, run_state: dict, deadline: float,
                   v1=None) -> tuple[dict, int]:
    started_at = _now_iso()
    if ns.run_id:
        tripped = _circuit_breaker_check(ev_root, ns.mode,
                                         str(run_state.get("profile") or "").strip().lower())
        if tripped is not None:
            return tripped, 2
    sgraph = build_source_graph(ctx.repo, ctx.cache)
    graph = build_import_graph(ctx.repo, ctx.cache, sgraph)
    plan, err = _plan(ctx, ns, ev_root, run_state, graph, sgraph)
    if err:
        return {"mode": ns.mode, "error": err}, 2
    repeats: dict[str, int] = {}
    if ns.mode == "tdd":
        repeats = _bump_tdd(ev_root, list(dict.fromkeys(x.split("::")[0] for x in plan["explicit"])))
        hot = sorted(f for f, c in repeats.items() if c > TDD_REPEAT_WARN)
        if hot:
            ctx.warnings.append(f"tdd run #{max(repeats[f] for f in hot)} on {', '.join(hot)}: "
                                "batch your changes before re-running")
    targets = plan["targets"]
    ordered = plan.get("ordered") or []
    ctx.ref_ids = _ref_nodeids(ctx.repo, ns.project, run_state, plan["ac_entries"])
    ord_files = {"backend/" + a.split("::")[0]: None for o in ordered for a in o
                 if not a.startswith(_SHARD_MODIFIERS)}
    head = _head_sha(ctx.repo)
    changed_in = list(dict.fromkeys(plan["changed"] + plan["desktop"]))
    inputs = _key_inputs(ctx, {**ord_files, **targets}, plan["smoke"], changed_in, sgraph)
    fmode = ns.mode + ("+tsc" if plan.get("desktop_tsc") else "")
    fkey = _frontend_key(ctx, fmode, plan["desktop"], plan["changed"]) if plan["desktop"] else ""
    key_extra = (plan["explicit"] + [json.dumps(plan["ac_entries"]),
                                     "[]",
                                     json.dumps(plan["skipped"])]
                 + ([json.dumps(ordered)] if ordered else [])
                 + ([json.dumps(plan["desktop_done"])] if plan.get("desktop_done") else []))
    legacy_key = _cache_key(ctx, ns.mode, targets, plan["smoke"], key_extra, inputs, head)
    key = (_v1_cache_key(ctx, v1, ns.mode, plan, targets, inputs, key_extra, fkey)
           if v1 is not None else legacy_key)
    tree = _tree_digest(inputs, changed_in, list(targets) + list(ord_files)
                        + list(plan["smoke"].values()))
    if ns.mode in ("verify", "regression") and not getattr(ns, "auto_recheck_of", None):
        prev = _read_json(ev_root / "tests" / f"latest-{ns.mode}.json")
        if isinstance(prev, dict) and prev.get("cache_key") == key and not _cacheable(prev):
            if not _unfinished_failures(prev):
                # Same inputs, every test finished: the result stands. Re-running the
                # failed tests on unchanged code would only hunt for a lucky pass.
                ev = dict(prev)
                ev["warnings"] = list(ctx.warnings) + [
                    f"inputs are unchanged since the last {ns.mode} and every test finished: "
                    "its result stands as reported (not re-run). Fix the code, then run again"]
                return _test_output(ev, ev_root / "tests" / f"latest-{ns.mode}.json"), _exit_code(ev)
            # Tests that never got to run before the deadline: continue only those.
            auto = _auto_recheck(ctx, ns, ev_root, run_state, deadline, prev, v1=v1)
            if auto is not None:
                return auto
    fix = None
    if ns.mode == "recheck" and not getattr(ns, "auto_recheck_of", None):
        fix = _is_fix_recheck(_read_json(ev_root / "tests" / f"latest-{plan.get('base_mode')}.json"),
                              _digest_map(ctx.repo, plan["changed"]))
    if ns.run_id and not getattr(ns, "auto_recheck_of", None):
        refused = _full_scope_refusal(ev_root, ns.mode, run_state,
                                      fix=True if fix is None else fix)
        if refused is not None:
            return refused, 2
    idx_info = {"auto_recheck_of": getattr(ns, "auto_recheck_of", None),
                "rechecked_from": plan.get("base_finished_at") if ns.mode == "recheck" else None,
                "fix": fix}
    cpath = ctx.cache / "results" / f"{legacy_key}{fkey[:16]}.json"
    hit = (_v1_cache_read(ctx, v1, key) if v1 is not None else _read_json(cpath))
    if isinstance(hit, dict) and hit.get("schema") == 1 and _cacheable(hit):
        ev = dict(hit)
        ev.pop("reused_from", None)  # names evidence of the call that wrote the cache entry
        ev.update(cached=True, duration_s=0, run_id=ns.run_id, started_at=started_at,
                  finished_at=_now_iso(), tdd_repeats=repeats,
                  warnings=ctx.warnings + ["cached: tests and their inputs are unchanged since the last run"],
                  changed_files=_digest_map(ctx.repo, plan["changed"]),
                  head_sha=head, tree_digest=tree)
        if plan["desktop"]:
            ev["desktop_key"] = _desktop_key_of_call(ctx.repo, plan)
        ev.pop("worktree", None)
        wtev = _worktree_evidence(ctx)
        if wtev:
            ev["worktree"] = wtev
        return _test_output(ev, _write_evidence(ev_root, ns.mode, ev, **idx_info)), _exit_code(ev)
    keys: dict[str, str] = {}
    reused: dict[str, list[dict]] = {}
    if ns.mode in REUSE_MODES:
        keys = _l2_file_keys(ctx, [f for f in plan["l2"] if f in targets and targets[f] is None],
                             plan["changed"], sgraph, head)
        reused, sources = _reusable(ev_root, ns.run_id, plan, keys)
        if reused:
            n = sum(len(rs) for rs in reused.values())
            plan["reused_from"] = {"files": sorted(reused), "tests": n, "sources": sources}
            ctx.warnings.append(
                f"reused {len(reused)} test file(s) ({n} tests) that passed in "
                + ", ".join(f"{s['mode']} {s['finished_at']}" for s in sources)
                + ": nothing they import or may read changed since; not re-run")
    plan["l2_file_keys"] = keys
    bvt_keys: dict = {}
    if v1 is None and ns.mode == "regression" and plan.get("bvt"):
        bvt_hit, bvt_keys = _bvt_cache_reuse(ctx, plan, sgraph)
        if bvt_hit:
            reused.update(bvt_hit)
            ctx.warnings.append(
                f"reused {len(bvt_hit)} CI BVT file(s) that passed in an earlier run with the "
                "same file + import closure (cross-run cache); CI re-runs them on push")
            plan["bvt_reused"] = sorted(bvt_hit)
    run_targets = {f: v for f, v in targets.items() if f not in reused}
    repo_before = _repo_state(ctx.repo)  # a test writing the repo breaks continuation
    # Machine-wide worker tokens: this call runs only as many workers as it holds, so
    # concurrent runs share the cores instead of oversubscribing them.
    tokens = _acquire_workers(ctx.cache, ctx.workers, ctx.warnings)
    ctx.workers = max(1, len(tokens))
    vitest_finished = False
    try:
        jobs: list[_Job] = []
        fpath = ctx.cache / "results" / f"frontend-{fkey}.json"
        fhit = _read_json(fpath) if fkey and v1 is None else None
        if fkey and not isinstance(fhit, list):
            jobs += _frontend_jobs(ctx, plan["desktop"],
                                   ns.mode in ("verify", "regression") or bool(plan.get("desktop_tsc")),
                                   with_backend=bool(run_targets or ordered),
                                   exclude=plan.get("desktop_done"))
        if plan["smoke"]:
            sj = _smoke_job(ctx, list(plan["smoke"]))
            sj.meta["files"] = plan["smoke"]
            jobs.append(sj)
        if run_targets or ordered:
            _drop_stale_pyc(ctx.repo, plan["changed"])
        ctx.seq += 1
        backend = ctx.repo / "backend"
        # recheck of order_dependent failures: their prefix runs as ONE ordered job, first
        ord_jobs = [_pytest_job(ctx, f"ordered{ctx.seq}-{k}", o, backend, False)
                    for k, o in enumerate(ordered)]
        main_jobs = _pytest_jobs(ctx, run_targets, "main", first=plan.get("ac_first"),
                                 durations=ctx.durations) if run_targets else []
        if plan.get("fresh_first"):
            # T3: a continuation starts the files that never got to run before the
            # half-done ones (whose replay jobs are the ordered ones): otherwise the same
            # slow files fill every call and the rest never start.
            jobs = jobs + main_jobs + ord_jobs
        else:
            jobs = ord_jobs + jobs + main_jobs
        if tokens:
            tokens = _trim_tokens(tokens, jobs)
            ctx.workers = len(tokens)  # re-runs in classify_failures stay inside them too
        # reserve for classifying real failures (HEAD baseline, prefix re-runs); a call cut
        # by the deadline needs none of it, so keep it small: more tests per call
        main_end = ctx.deadline_at - min(12.0, deadline * 0.15)
        _run_jobs(jobs, ctx.workers, main_end)
        vitest_finished = any(j.kind == "vitest" and j.started and not j.killed for j in jobs)
        results = _results_with_requeue(ctx, jobs, main_end, "main")
        if ordered:
            results = _dedupe_results(results)
        w = _save_durations(ctx.cache, results)
        if w:
            ctx.warnings.append(w)
        if isinstance(fhit, list):
            results += fhit
        elif fkey:
            fres = [r for r in results if r.get("layer") in ("vitest", "tsc")]
            # Cache only an all-green frontend result: a failure (flaky or real) is
            # always re-run, never served from frontend-{fkey}.json.
            if (v1 is None and fres and not any(r["outcome"] in _FAIL for r in fres)
                    and not plan.get("desktop_done")):
                _write_json(fpath, fres)
        cls = classify_failures(ctx, results)
    finally:
        _release_workers(tokens)
    mutated = _check_repo_mutation(ctx, repo_before)
    # Reused entries are evidence entries already (compact): _assemble keeps them as is.
    results += [{**r, "_reused": True} for f in reused for r in reused[f]]
    ev = _assemble(ctx, ns, plan, results, cls, started_at, repeats, key)
    ev.update(head_sha=head, tree_digest=tree)
    if vitest_finished:
        ev["vitest_finished"] = True  # _overlay_evidence: the unrun-rest placeholder is answered
    if mutated:
        ev["repo_mutations"] = mutated
    if bvt_keys:
        _bvt_cache_store(ctx, ev, bvt_keys, reused)
        if plan.get("bvt_reused"):
            ev["bvt_reused"] = plan["bvt_reused"]
    if _cacheable(ev) and not (mutated and mutated["left"]):  # tests ran on a moving tree
        if v1 is not None:
            _v1_cache_put(ctx, v1, key, ev)
        else:
            _write_json(cpath, ev)
    wtev = _worktree_evidence(ctx)  # after the cache write: it describes THIS worktree
    if wtev:
        ev["worktree"] = wtev
    if ev["failures"]:
        ev["logs"] = str(_copy_logs(ctx, ev_root, ns.mode))
    path = _write_evidence(ev_root, ns.mode, ev, **idx_info)
    base_mode = plan.get("base_mode")
    if ns.mode == "recheck" and base_mode in ("verify", "regression"):
        # Lay the recheck over the evidence it re-ran, so the file the TEST stage reads
        # (latest-regression.json) reflects it. Only if that evidence is still the one
        # the recheck was planned from.
        base = _read_json(ev_root / "tests" / f"latest-{base_mode}.json")
        if ev["skipped_for_budget"]:
            ctx.warnings.append("recheck skipped tests for budget, so its results were NOT laid "
                                f"over latest-{base_mode}.json (stale passes would hide them); "
                                f"run --mode {base_mode}")
            ev["warnings"] = list(ctx.warnings)
        elif isinstance(base, dict) and base.get("finished_at") == plan.get("base_finished_at"):
            merged = _overlay_evidence(ctx.repo, base, ev, _ac_entries(ns.project, run_state)
                                       if base_mode == "regression" else [],
                                       auto=bool(getattr(ns, "auto_recheck_of", None)))
            merged["warnings"] = list(dict.fromkeys(list(base.get("warnings") or [])
                                                    + list(ctx.warnings)))
            # kind "overlay": a rewrite of base_mode's evidence, not another test run
            _write_evidence(ev_root, base_mode, merged, kind="overlay", **idx_info)
            ev["overlaid_onto"] = base_mode
    return _test_output(ev, path), _exit_code(ev)


NODEID_CAP = 400  # a parametrize id can be megabytes (one real run: 1 MB per nodeid)


def _capped_id(nodeid: str, refs=()) -> dict:
    """{"nodeid": ...} for evidence; an id over NODEID_CAP chars keeps its head plus a
    sha256 tag and adds ``nodeid_truncated`` (recheck then re-runs its whole file).
    Never capped when an explicit nodeid ref in ``refs`` (ac_coverage / exit_criteria,
    see ``_ref_nodeids``) matches the full id but would not match the capped one."""
    if len(nodeid) <= NODEID_CAP:
        return {"nodeid": nodeid}
    tag = _sha(nodeid.encode("utf-8", "replace"))[:12]
    capped = f"{nodeid[:NODEID_CAP - 20]}…[sha:{tag}]"
    full, cut = {"nodeid": nodeid}, {"nodeid": capped}
    if any(_ref_matches(full, "", ref) and not _ref_matches(cut, "", ref) for ref in refs):
        return full
    return {"nodeid": capped, "nodeid_truncated": True}


def _evidence_result(r: dict, refs=()) -> dict:
    """One evidence ``results[]`` entry. Failures, errors, timeouts and skips keep
    nodeid + outcome + file; a pass is compact: nodeid + outcome (+ duration), with
    ``file`` only when it is not the nodeid's own path (``import::`` smoke). Readers
    (pipeline_validator ``_evidence_matches``; here ``_rfile`` for the overlay's AC
    layer) fall back to that path."""
    e = {**_capped_id(r["nodeid"], refs), "outcome": r["outcome"]}
    if r["outcome"] != "passed" or _rfile(r) != r["nodeid"].split("::")[0]:
        e["file"] = _rfile(r)
    if r["outcome"] == "passed" and r.get("duration") is not None:
        e["duration"] = round(float(r["duration"]), 4)  # compact: 0.1 ms is plenty
    return e


def _not_run_count(failures) -> int:
    """Tests a global deadline left unstarted or unfinished: unfinished work for the next
    call, not a failure. They are inside ``counts["timeouts"]`` (every consumer that asks
    "is anything unfinished" reads that total); ``counts["not_run"]`` says how many of
    them are this kind, so the output does not read as that many hung tests."""
    return sum(1 for f in failures or () if isinstance(f, dict)
               and f.get("outcome") == "timeout" and f.get("deadline"))


def _assemble(ctx, ns, plan, results, cls, started_at, repeats, key) -> dict:
    results = list(results)
    counts = {"passed": 0, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for r in results:
        counts[_COUNT_KEY[r["outcome"]]] += 1
    # a failing probe verdict is definite ("new"); the cache key already holds the verdict
    cls = dict(cls)
    refs = getattr(ctx, "ref_ids", None) or ()
    failures = [{**_capped_id(r["nodeid"], refs), "classification": cls.get(r["nodeid"], "unknown"),
                 "summary": "\n".join(r["summary"][:STACK_LINES + 1]),
                 "outcome": r["outcome"], "deadline": bool(r.get("deadline")),
                 # never started (a deadline / per-test timeout ended its shard first):
                 # unfinished work for the next call, not a code failure
                 **({"notrun": True} if r.get("notrun") and not r.get("runner_error") else {}),
                 **getattr(ctx, "order_dep", {}).get(r["nodeid"], {})}
                for r in results if r["outcome"] in _FAIL]
    counts["not_run"] = _not_run_count(failures)
    dl_timeouts = sum(1 for f in failures if f.get("classification") == "timeout")
    if dl_timeouts:
        ctx.warnings.append(
            f"{dl_timeouts} test(s) not finished before this call's global deadline "
            "(classification 'timeout'): "
            "this is NOT a code failure, do not edit code for it. Re-run the SAME command "
            "unchanged: it continues with only the unfinished tests; repeat until the "
            "output no longer says continue: true")
    l2 = set(plan["l2"])
    dep = [r for r in results if _rfile(r) in l2 and not r.get("layer")]
    smoke = [r for r in results if r.get("layer") == "smoke"]
    layers = {
        "ac_driven": (_ac_layer(ctx.repo, plan["ac_entries"], results) if ns.mode == "regression"
                      else {"run": False, "pass": 0, "fail": 0}),
        "dependency_scoped": {"run": bool(l2), "tests": len(dep),
                              "pass": sum(r["outcome"] == "passed" for r in dep)},
        "import_smoke": {"run": bool(smoke), "modules": len(smoke),
                         "pass": sum(r["outcome"] == "passed" for r in smoke)},
    }
    if plan.get("bvt"):
        bvt_set = set(plan["bvt"])
        bvt = [r for r in results if _rfile(r) in bvt_set and not r.get("layer")]
        layers["ci_bvt"] = {"run": True, "files": len(bvt_set), "tests": len(bvt),
                            "pass": sum(r["outcome"] == "passed" for r in bvt)}
    ev = {"schema": 1, "mode": ns.mode, "run_id": ns.run_id, "started_at": started_at,
          "finished_at": _now_iso(), "duration_s": round(time.monotonic() - ctx.t0, 2),
          "cached": False,
          "selected_files": sorted(set(plan["targets"]) | set(plan["desktop"])
                                   | {"backend/" + a.split("::")[0] for o in plan.get("ordered") or []
                                      for a in o if not a.startswith(_SHARD_MODIFIERS)}),
          "skipped_for_budget": plan["skipped"], "counts": counts,
          "dependency_scope": sorted(l2),
          **({"risk_rules": list(plan["risk_rules"])} if plan.get("risk_rules") else {}),
          **({"risk_overflow": list(plan["risk_overflow"])} if plan.get("risk_overflow") else {}),
          "transitive_unrun": list(plan.get("transitive_unrun") or []),
          **({"bvt_files": list(plan["bvt"])} if plan.get("bvt") else {}),
          **({"context_unverified": sorted(set(plan["context_unverified"]))}
             if plan.get("context_unverified") else {}),
          **({"ac_refs_missing": list(dict.fromkeys(plan["ac_refs_missing"]))}
             if plan.get("ac_refs_missing") else {}),
          **({"context_replayed": sorted(set(plan["context_replayed"]))}
             if plan.get("context_replayed") else {}),
          "results": [{k: v for k, v in r.items() if k != "_reused"} if r.get("_reused")
                      else {**_evidence_result(r, refs),
                            **({"requeued": True} if r.get("requeued") else {})}
                      for r in results],
          "failures": failures, "layers": layers, "tdd_repeats": repeats,
          "warnings": list(ctx.warnings), "cache_key": key,
          "changed_files": _digest_map(ctx.repo, plan["changed"]),
          **({"desktop_key": _desktop_key_of_call(ctx.repo, plan)} if plan["desktop"] else {}),
          "runner_errors": [_capped_id(r["nodeid"], refs)["nodeid"] for r in results
                            if r.get("runner_error")]}
    if any(r.get("layer") in ("vitest", "tsc") for r in results):
        ev["frontend"] = _frontend_summary(results)
    if ns.mode in REUSE_MODES:
        ev["l2_file_keys"] = dict(plan.get("l2_file_keys") or {})
        if plan.get("reused_from"):
            ev["reused_from"] = plan["reused_from"]
    if ns.mode == "recheck":
        ev["rechecked_failures"] = plan["rechecked"]
        if plan.get("od_in_context"):  # order_dependent failures re-run after their prefix
            ev["od_in_context"] = list(plan["od_in_context"])
    return ev


def _copy_logs(ctx, ev_root: Path, mode: str) -> Path:
    dst = ev_root / "tests" / f"logs-{mode}-{_stamp()}"
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(ctx.workdir.iterdir()):
        if p.suffix in (".out", ".err") and p.is_file():
            shutil.copy2(p, dst / p.name)
    return dst


def _failure_nonblocking(f: object) -> bool:
    """One failure the exit code accepts — the SSOT rule shared, byte-for-byte, with
    ``pipeline_validator._evidence_failure_ok``: classified pre-existing (fails at the
    HEAD baseline too) or flaky, and never a timeout.

    A GLOBAL-deadline timeout (classification ``timeout``) is NOT a code failure — it
    must not send the run back to BUILD — but it is NOT verified either: the test never
    finished, so it is not green and blocks push-ready. Re-running the same mode on the
    unchanged tree re-runs only those tests (``_auto_recheck``), so it converges; the
    circuit breaker bounds it. A per-test hang blocks too — a hang is never green."""
    if not isinstance(f, dict):
        return False
    return f.get("classification") in ("pre-existing", "flaky") and f.get("outcome") != "timeout"


def _exit_code(ev: dict) -> int:
    """0 only when no runner error occurred and every failure is non-blocking
    (``_failure_nonblocking``: pre-existing / flaky, never a timeout). A deadline
    timeout is unverified, so it is not green. ``pipeline_validator`` applies the
    identical rule so the judge and the runner never disagree."""
    if ev.get("runner_errors"):
        return 1
    return 0 if all(_failure_nonblocking(f) for f in ev.get("failures") or []) else 1


def _cacheable(ev: dict) -> bool:
    """Never cache (or trust a cached) result with a timeout, an unknown classification
    or a runner error (zero collected / abnormal shard exit)."""
    return ev.get("counts", {}).get("timeouts", 1) == 0 and not ev.get("runner_errors") \
        and all(f.get("classification") != "unknown" for f in ev.get("failures") or [])


def _short(summary: str, n: int) -> list[str]:
    lines = summary.splitlines()
    if lines and lines[0].endswith("stack:"):  # timeout: keep the repo frames
        frames = [ln for ln in lines[1:] if 'File "' in ln and "site-packages" not in ln
                  and "/lib/python" not in ln]
        lines = [lines[0]] + (frames or lines[1:])
        return [ln[:200] for ln in lines[:n]]
    return [ln[:200] for ln in lines[-n:]] if n else []


def _test_output(ev: dict, path: Path) -> dict:
    # Tolerant reads: an overlay's base may be hand-written / older evidence.
    fails = [f for f in ev.get("failures") or [] if isinstance(f, dict)]
    skipped = list(ev.get("skipped_for_budget") or [])
    selected_files = sorted(set(ev.get("selected_files") or []))
    selected_nodeids = sorted({str(row.get("nodeid")) for row in ev.get("results") or []
                               if isinstance(row, dict) and row.get("nodeid")})
    out = {"mode": ev.get("mode"), "cached": bool(ev.get("cached")), "ok": _exit_code(ev) == 0,
           "counts": ev.get("counts") or {}, "duration_s": ev.get("duration_s", 0),
           "selected": len(selected_files), "selected_files": selected_files,
           "selected_nodeids": selected_nodeids, "cache_key": ev.get("cache_key"),
           "dependency_scope": {"files": list(ev.get("dependency_scope") or []),
                                "unrun": sorted(set(ev.get("transitive_unrun") or [])),
                                "layer": (ev.get("layers") or {}).get("dependency_scoped") or {}},
           "risk_rules": list(ev.get("risk_rules") or []),
           "risk_overflow": list(ev.get("risk_overflow") or []),
           "failures": [{"nodeid": f.get("nodeid"), "classification": f.get("classification", "unknown"),
                         "summary": _short(str(f.get("summary") or ""), SUMMARY_LINES),
                         **{k: f[k] for k in ("order_dependent", "culprit") if k in f}}
                        for f in fails[:10]],
           "failures_total": len(fails),
           "skipped_for_budget": skipped[:10],
           "skipped_for_budget_total": len(skipped),
           "transitive_unrun_total": len(ev.get("transitive_unrun") or []),
           "evidence": str(path), "warnings": ev.get("warnings") or []}
    unfinished = sum(1 for f in fails if f.get("deadline") or f.get("notrun"))
    if unfinished:  # cut by this call's deadline: the same command continues with them
        out.update(unfinished=unfinished, **{"continue": True})
    if ev.get("mode") in ("regression", "verify", "recheck"):
        out["layers"] = ev.get("layers") or {}
    for k in ("frontend", "tdd_repeats", "rechecked_failures", "runner_errors", "overlaid_onto",
              "rechecked_from", "repo_mutations"):
        if ev.get(k):
            out[k] = ev[k][:10] if k == "runner_errors" else ev[k]
    return out


# ── G2 probe mode ───────────────────────────────────────────────────────────
def _digest_files(repo: Path, files: list[str]) -> str:
    h = hashlib.sha256()
    for f in sorted(set(files)):
        h.update(f.encode() + b"\0" + _file_digest(repo / f).encode() + b"\0")
    return h.hexdigest()[:32]


def _parse_findings(text: str) -> list | None:
    cands = [text.strip()] + [ln.strip() for ln in reversed(text.splitlines()) if ln.strip()]
    for c in cands:
        try:
            data = json.loads(c)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("findings"), list):
            return data["findings"]
    return None


def _new_findings(st: dict, findings: list, warnings: list) -> tuple[list, set]:
    known = {str(f.get("id")) for f in st["findings"] if isinstance(f, dict)}
    new, bad = [], 0
    for f in findings:
        if not isinstance(f, dict) or "id" not in f:
            bad += 1
        elif str(f["id"]) not in known:
            known.add(str(f["id"]))
            new.append(f)
    if bad:
        warnings.append(f"{bad} finding(s) without an id were ignored")
    return new, known


# ── G2 mutation mode ────────────────────────────────────────────────────────
def _load_mutation_spec(ctx, raw: str | None) -> tuple[dict, list, str | None]:
    if not raw:
        return {}, [], "mutation needs --mutations spec.json"
    p = Path(raw) if Path(raw).is_absolute() else Path.cwd() / raw
    spec = _read_json(p if p.is_file() else ctx.repo / raw)
    if not isinstance(spec, dict) or not isinstance(spec.get("mutations"), list) \
            or not isinstance(spec.get("tests"), list) or not spec["tests"]:
        return {}, [], 'spec must be {"tests": [...], "mutations": [{"id","file","find","replace"}]}'
    targets: dict = {}
    for t in spec["tests"]:
        rv = _resolve_ref(ctx.repo, str(t))
        if rv is None:
            return {}, [], f"mutation test not found: {t}"
        _add_target(targets, *rv)
    muts = []
    for m in spec["mutations"]:
        ok = isinstance(m, dict) and all(isinstance(m.get(k), str) for k in ("id", "file", "find", "replace"))
        f = _norm_path(ctx.repo, m["file"]) if ok else None
        if not ok or not f or not m["find"]:
            return {}, [], f"bad mutation entry: {str(m)[:120]}"
        muts.append({**m, "file": f})
    return targets, muts, None


def _apply_and_run(ctx, wt: Path, m: dict, targets: dict, base_fail: set, i: int) -> dict:
    rec = {"id": m["id"], "file": m["file"], "killed": False, "red_tests": []}
    p = _contained(wt, m["file"])
    if p is None:
        return {**rec, "error": "file path escapes the worktree"}
    try:
        orig = p.read_bytes()
    except OSError:
        return {**rec, "error": "file missing in the worktree"}
    find, repl = m["find"].encode(), m["replace"].encode()
    n = orig.count(find)
    if n != 1:
        return {**rec, "error": f"find string occurs {n} times (must be exactly once)"}
    p.write_bytes(orig.replace(find, repl, 1))
    try:
        res = run_pytest_targets(ctx, targets, ctx.deadline_at, f"mut{i}-", root=wt)
    finally:
        p.write_bytes(orig)
    red = sorted({r["nodeid"] for r in res if r["outcome"] in _FAIL and not r.get("deadline")
                  and r["nodeid"] not in base_fail})
    rec.update(killed=bool(red), red_tests=red)
    if not red and any(r.get("deadline") for r in res):
        rec["error"] = "global deadline reached before the tests finished"
    elif not res:
        rec["error"] = "no test results"
    return rec


def run_mutations(ctx, ns, ev_root: Path, run_state: dict) -> tuple[dict, int]:
    """Apply each mutation in a throwaway worktree (HEAD + current changed files),
    run the spec's tests once per mutation, revert. The main tree is never written."""
    targets, muts, err = _load_mutation_spec(ctx, ns.mutations)
    if err:
        return {"mode": "mutation", "error": err}, 2
    parent = ctx.cache / "mutation"
    parent.mkdir(parents=True, exist_ok=True)
    wt = Path(tempfile.mkdtemp(prefix="wt-", dir=parent)).resolve()
    _PRIVATE_WT[wt] = ctx.repo  # a signal from here on removes it (M1)
    p = _git_run(ctx.repo, "-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach",
                 str(wt), "HEAD", timeout=60)
    if p is None or p.returncode != 0:
        _remove_worktree(ctx.repo, wt)
        _PRIVATE_WT.pop(wt, None)
        msg = (p.stderr.strip().splitlines() or ["?"])[-1] if p is not None else "git failed"
        return {"mode": "mutation", "error": f"cannot create worktree: {msg[:200]}"}, 2
    records, base_counts = [], {}
    tokens = _acquire_workers(ctx.cache, ctx.workers, ctx.warnings)  # cross-run throttle
    ctx.workers = max(1, len(tokens))
    try:
        for f in changed_files(ctx.repo, run_state, ctx.warnings, (ns.project, ns.run_id)):
            # H5: both ends resolved and contained before any write / unlink.
            src, dst = _contained(ctx.repo, f), _contained(wt, f)
            if src is None or dst is None:
                ctx.warnings.append(f"changed path outside the repo/worktree skipped: {f[:120]}")
                continue
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif not src.exists() and dst.is_file():
                dst.unlink()
        base = run_pytest_targets(ctx, targets, ctx.deadline_at, "mutbase-", root=wt)
        base_fail = {r["nodeid"] for r in base if r["outcome"] in _FAIL}
        base_counts = {"passed": sum(r["outcome"] == "passed" for r in base),
                       "failed": len(base_fail)}
        if base_fail:
            ctx.warnings.append(f"{len(base_fail)} test(s) already fail without any mutation; "
                                "they are ignored when judging kills")
        for i, m in enumerate(muts):
            if ctx.left() < 2:
                records.append({"id": m["id"], "file": m["file"], "killed": False,
                                "red_tests": [], "error": "global deadline reached"})
                continue
            records.append(_apply_and_run(ctx, wt, m, targets, base_fail, i))
    finally:
        _release_workers(tokens)
        _remove_worktree(ctx.repo, wt)
        _PRIVATE_WT.pop(wt, None)
    ev = {"schema": 1, "run_id": ns.run_id, "at": _now_iso(), "mutations": records,
          "all_killed": bool(records) and all(r["killed"] for r in records),
          "baseline": base_counts, "warnings": list(ctx.warnings)}
    mdir = ev_root / "mutations"
    path = mdir / f"{_stamp()}.json"
    _write_json(path, ev)
    _write_json(mdir / "latest.json", ev)
    out = {"mode": "mutation", "all_killed": ev["all_killed"], "baseline": base_counts,
           "mutations": [{"id": r["id"], "killed": r["killed"], "red_tests": r["red_tests"][:5],
                          **({"error": r["error"]} if r.get("error") else {})} for r in records],
           "evidence": str(path), "warnings": ctx.warnings}
    return out, 0 if ev["all_killed"] else 1


# ── break-test proofs: --mode snapshot / --mode prove (deterministic convergence §4) ──
# A Gate-2 finding is closed by a test, not by a model: its break test must FAIL by an
# assertion on the snapshot of the round that reported it (twice: one flaky run is not
# a proof) and PASS on the current code. The snapshot is a commit of the run's change
# set taken when the round's review starts; the break test is overlaid onto it as a
# content-addressed commit, so the same snapshot + the same test bytes always check out
# the same tree and its results are cached by that tree (a rebase never re-runs them).
PROVE_MAX_IDS = 20


def _proofs_dir(ev_root: Path) -> Path:
    return ev_root / "proofs"


def _isolated(ctx) -> bool:
    return _WORKTREE.get("repo") == ctx.repo


def run_snapshot(ctx, ns, ev_root: Path) -> tuple[dict, int]:
    """Freeze the run worktree's change set as round ``ns.round``'s snapshot. Taken once
    per round: a second call returns the recorded snapshot unchanged (a snapshot that
    moved would silently re-base every proof of that round)."""
    from core import run_worktree as rw
    out = {"mode": "snapshot", "round": ns.round}
    pdir = _proofs_dir(ev_root)
    pdir.mkdir(parents=True, exist_ok=True)
    fd = _try_lock(pdir / ".lock", 10)
    if fd is None:
        return {**out, "error": "proofs lock busy (another snapshot/prove call); retry"}, 2
    try:
        snaps = _read_json(pdir / "snapshots.json")
        snaps = snaps if isinstance(snaps, dict) else {}
        if isinstance(snaps.get(ns.round), dict):
            return {**out, **snaps[ns.round], "already": True}, 0
        try:
            rec = rw.snapshot_commit(ctx.repo, ns.run_id, ns.round)
        except rw.WorktreeError as exc:
            return {**out, "error": f"snapshot failed: {exc.message}"}, 2
        rec["at"] = _now_iso()
        rec["untracked"] = rw.untracked_new_files(ctx.repo, ns.run_id)
        snaps[ns.round] = rec
        _write_json(pdir / "snapshots.json", snaps)
    finally:
        _unlock(fd)
    if rec["untracked"]:
        ctx.warnings.append(f"{len(rec['untracked'])} un-added file(s) are not in the snapshot "
                            "(git add them before the review starts): "
                            + ", ".join(rec["untracked"][:5]))
    return {**out, **rec, "warnings": ctx.warnings}, 0


def proof_outcome(r: "dict | None") -> tuple[str | None, str]:
    """A runner result → (label, detail); label None = unfinished (no verdict yet).

    Labels are finding_ledger.OLD_LABELS. ``assert_fail`` only for a call-phase
    AssertionError (or subclass) or pytest.fail / DID NOT RAISE; any other exception
    in the body, a collection / setup error, a missing nodeid → ``error``; a skip →
    ``skip``; a per-test timeout → ``timeout``. A deadline cut or a not-run test is
    unfinished, never a verdict."""
    if r is None:
        return None, "no result"
    o = r.get("outcome")
    last = (r.get("summary") or [""])[-1] if r.get("summary") else ""
    if r.get("notrun") and not r.get("runner_error"):
        return None, "not run (deadline or aborted shard)"
    if o == "timeout":
        return (None, "global deadline") if r.get("deadline") else ("timeout", last[:200])
    if o == "passed":
        return "pass", ""
    if o == "skipped":
        return "skip", last[:200]
    if o == "failed":
        kind = r.get("exc_kind")
        if kind in ("assert", "fail"):
            return "assert_fail", (r.get("exc") or kind)[:200]
        return "error", f"{r.get('exc') or 'exception'} in the test body: {last}"[:300]
    return "error", (last or str(o))[:300]


def _result_for(nodeid: str, results: list[dict]) -> "dict | None":
    """The result for ``nodeid``, else its file's collection error (a module that
    cannot be imported reports at file level, not per test)."""
    exact = [r for r in results if r.get("nodeid") == nodeid]
    if exact:
        return exact[0]
    f = nodeid.split("::")[0]
    return next((r for r in results if r.get("nodeid") == f and r.get("outcome") == "error"),
                None)


def _prove_ids(ctx, raw: list[str]) -> tuple[list[str], str | None]:
    """Repo-relative backend test nodeids (``backend/tests/test_x.py::name``). A whole
    file is refused: a break test is one test, and a file-level pass proves nothing."""
    ids = []
    for x in raw:
        if not isinstance(x, str) or "::" not in x:
            return [], f"prove takes test nodeids (file::test), not a whole file: {x}"
        f, rest = x.strip().split("::", 1)
        f = _norm_path(ctx.repo, f)
        if not f or not rest or not _is_backend_test(f) or _contained(ctx.repo, f) is None:
            return [], f"not a backend test nodeid (backend/tests/test_*.py::name): {x}"
        ids.append(f"{f}::{rest}")
    ids = list(dict.fromkeys(ids))
    if not ids:
        return [], "prove needs --files <nodeid ...> (the break tests)"
    if len(ids) > PROVE_MAX_IDS:
        return [], f"prove takes at most {PROVE_MAX_IDS} nodeids, got {len(ids)}"
    return ids, None


def _overlay_paths(ctx, snap_sha: str, ids: list[str]) -> list[str]:
    """The break tests' files plus every test-only file (under backend/tests/) the run
    changed since the snapshot: fixtures and helpers the break tests use. Code under
    test is never overlaid — that would test the fix, not the snapshot."""
    from core import run_worktree as rw
    paths = {n.split("::")[0] for n in ids}
    try:
        cur = rw.change_tree(ctx.repo, _WORKTREE["run_id"])
        for p in rw.changed_paths(ctx.repo, snap_sha, cur):
            if p.startswith("backend/tests/"):
                paths.add(p)
    except rw.WorktreeError as exc:
        ctx.warnings.append(f"changed test files not resolved: {exc.message}")
    # new test files not yet git-added (a break test written after the snapshot)
    untracked = _git(ctx.repo, "ls-files", "--others", "--exclude-standard", "-z",
                     "--", "backend/tests") or ""
    # bytecode is never test content: a new-side run writes __pycache__ into the
    # worktree, and overlaying it would change the overlay tree (a cache miss each call)
    paths.update(p for p in untracked.split("\0")
                 if p and "__pycache__" not in Path(p).parts and not p.endswith(".pyc"))
    return sorted(paths)


def _old_cache_key(tree: str, pyid: str, nodeid: str) -> str:
    return f"{tree}|{pyid}|{nodeid}"


def run_prove(ctx, ns, ev_root: Path) -> tuple[dict, int]:
    """For each break test: up to two runs on round ``ns.round``'s snapshot (with the
    test overlaid) and one on the current worktree; verdict per finding_ledger.

    Old-side outcomes are cached by (overlay tree, interpreter, nodeid) after every
    batch, so a deadline between the two runs loses nothing and a later call (or a
    call after a rebase) re-runs only the new side. ``continue: true`` when some
    verdict is still pending; the next call of the same command finishes it."""
    from core.run_protocol import findings as fl
    from core import run_worktree as rw
    out: dict = {"mode": "prove", "round": ns.round}
    ids, err = _prove_ids(ctx, ns.files)
    if err:
        return {**out, "error": err}, 2
    pdir = _proofs_dir(ev_root)
    snaps = _read_json(pdir / "snapshots.json")
    snap = snaps.get(ns.round) if isinstance(snaps, dict) else None
    if not isinstance(snap, dict) or not snap.get("sha"):
        return {**out, "error": f"no snapshot for round {ns.round}: run --mode snapshot "
                                f"--round {ns.round} when that round's review starts"}, 2
    if _git(ctx.repo, "cat-file", "-e", snap["sha"] + "^{commit}") is None:
        return {**out, "error": f"snapshot {snap['sha'][:12]} is not in the repo"}, 2
    try:
        ovl = rw.overlay_commit(ctx.repo, snap["sha"], _overlay_paths(ctx, snap["sha"], ids))
    except rw.WorktreeError as exc:
        return {**out, "error": f"overlay failed: {exc.message}"}, 2
    pyid = _python_id(ctx.python)
    cpath = pdir / "old-side.json"
    cache = _read_json(cpath)
    cache = cache if isinstance(cache, dict) else {}
    old: dict[str, list] = {n: list(cache.get(_old_cache_key(ovl["tree"], pyid, n)) or [])
                            for n in ids}
    tokens = _acquire_workers(ctx.cache, ctx.workers, ctx.warnings)
    ctx.workers = max(1, len(tokens))
    new: dict[str, tuple] = {}
    try:
        for _ in range(2):  # at most two old-side batches: run 1, then run 2
            need = [n for n in ids if fl.old_runs_needed([x["o"] for x in old[n]]) > 0]
            if not need or ctx.left() < 5:
                break
            wt = _ensure_worktree(ctx, ovl["sha"], ctx.left())
            if wt is None:
                break
            res = run_pytest_targets(ctx, _targets_from_nodeids(
                [n[len("backend/"):] for n in need]), ctx.deadline_at, "old", root=wt)
            got = {}
            for n in need:
                lab, det = proof_outcome(_result_for(n, res))
                if lab is not None:
                    old[n].append({"o": lab, "d": det})
                    got[n] = old[n]
            if got:  # persist after every batch (F2)
                fd = _try_lock(pdir / ".lock", 10)
                try:
                    cur = _read_json(cpath)
                    cur = cur if isinstance(cur, dict) else {}
                    for n, v in got.items():
                        cur[_old_cache_key(ovl["tree"], pyid, n)] = v
                    _write_json(cpath, cur)
                finally:
                    _unlock(fd)
            if len(got) < len(need):
                break  # something unfinished: the deadline decides the rest
        ready = [n for n in ids if fl.old_runs_needed([x["o"] for x in old[n]]) == 0
                 and fl.proof_verdict([x["o"] for x in old[n]], "pass")[0] != "invalid"]
        if ready and ctx.left() >= 3:
            res = run_pytest_targets(ctx, _targets_from_nodeids(
                [n[len("backend/"):] for n in ready]), ctx.deadline_at, "new")
            for n in ready:
                new[n] = proof_outcome(_result_for(n, res))
    finally:
        _release_workers(tokens)
    rows, pending = [], []
    for n in ids:
        labels = [x["o"] for x in old[n]]
        nl, nd = new.get(n, (None, ""))
        verdict, code = fl.proof_verdict(labels, nl)
        reason = fl.PROOF_CODES[code][1]
        detail = next((x["d"] for x in old[n] if x["o"] != "assert_fail" and x["d"]), "") \
            if code.startswith("old_") else nd if code == "new_not_pass" else ""
        row = {"nodeid": n, "old": labels, "new": nl, "verdict": verdict, "code": code,
               "reason": reason + (f" ({detail})" if detail else "")}
        rows.append(row)
        if verdict == "pending":
            pending.append(n)
    ev = {"schema": 1, "run_id": ns.run_id, "round": ns.round, "at": _now_iso(),
          "snapshot": snap["sha"], "overlay": ovl["sha"], "overlay_tree": ovl["tree"],
          "overlaid": ovl["blobs"], "python": pyid, "proofs": rows,
          "warnings": list(ctx.warnings)}
    path = pdir / f"prove-{ns.round}-{_stamp()}.json"
    _write_json(path, ev)
    _write_json(pdir / f"latest-prove-{ns.round}.json", ev)
    out.update(proofs=rows, evidence=str(path), warnings=ctx.warnings,
               valid=sum(r["verdict"] == "valid" for r in rows),
               invalid=sum(r["verdict"] == "invalid" for r in rows))
    if pending:
        out.update(unfinished=pending, **{"continue": True})
        return out, 1
    return out, 0 if all(r["verdict"] == "valid" for r in rows) else 1


# ── CLI ─────────────────────────────────────────────────────────────────────
def _size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":")))


def _compact(out: dict) -> dict:
    """Shrink the printed object toward OUTPUT_BUDGET bytes; evidence keeps everything."""
    out = json.loads(json.dumps(out))
    steps = [("summary", 5), ("summary", 2), ("warnings", 3), ("failures", 5),
             ("summary", 0), ("skipped_for_budget", 3), ("failures", 3), ("warnings", 1),
             # the v1 selection lists are recorded in full by the runner before printing
             ("selected_nodeids", 0), ("selected_files", 0), ("risk_rules", 0)]
    for field, n in steps:
        if _size(out) <= OUTPUT_BUDGET:
            break
        if field == "summary":
            for f in out.get("failures") or []:
                if isinstance(f, dict) and isinstance(f.get("summary"), list):
                    f["summary"] = f["summary"][:n] if n else []
        elif isinstance(out.get(field), list):
            out[field] = out[field][:n]
    for k in ("warnings",):
        out[k] = [str(w)[:300] for w in out.get(k) or []]
    return out


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # JSON-only stdout: no argparse usage dump
        raise _ArgError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="pipeline_test.py", description="Pipeline test runner (one call per mode).")
    p.add_argument("--project", required=True)
    p.add_argument("--run-id")
    p.add_argument("--mode", required=True, choices=ALL_MODES)
    p.add_argument("--files", nargs="+", default=[])
    p.add_argument("--name")
    p.add_argument("--mutations")
    p.add_argument("--round", help="snapshot / prove: the review round label (R1, R2)")
    p.add_argument(
        "--finding", action="append", default=[], metavar="CF-ID=NODEID",
        help="prove mode: bind one canonical finding to its exact break-test nodeid")
    p.add_argument("--deadline", default=DEFAULT_DEADLINE)
    p.add_argument("--json", action="store_true", help="accepted for clarity; output is always JSON")
    return p


def _clamp_deadline(raw) -> tuple[float | None, list[str]]:
    if isinstance(raw, str) and raw.strip().lower() == "auto":  # retired 2026-10-19
        return DEFAULT_DEADLINE, ["--deadline auto is retired: calls stay at "
                                  f"{DEFAULT_DEADLINE:g}s and continue across calls"]
    return _clamp_seconds(raw)


def _clamp_seconds(raw) -> tuple[float | None, list[str]]:
    """(deadline, warnings). Default 95 s fits ``alarm 110``; above SAFE_DEADLINE warns,
    above MAX_DEADLINE clamps; a non-finite value → (None, [error])."""
    import math
    try:
        d = float(raw)
    except (TypeError, ValueError):
        d = float("nan")
    if not math.isfinite(d):
        return None, [f"--deadline must be a finite number of seconds, got {raw!r}"]
    warn = []
    if d > MAX_DEADLINE:
        warn.append(f"--deadline {d:g}s clamped to the maximum {MAX_DEADLINE:g}s")
    d = max(1.0, min(d, MAX_DEADLINE))
    if d > SAFE_DEADLINE:
        warn.append(f"--deadline {d:g}s exceeds {SAFE_DEADLINE:g}s, the budget of the documented "
                    f"`alarm {WRAPPER_ALARM_S}` wrapper: the outer alarm must be at least "
                    f"{d + 10:g}s or the runner is killed before it reports")
    return d, warn


def _v1_runner(ns, run_dir: Path, run_state: dict, ctx):
    """Build the v1 adapter only when protected contract.json says protocol v1."""
    if not ns.run_id:
        return None
    from config import get_app_data_dir
    from core.run_protocol import git_tree
    from core.run_protocol.runner import V1Runner, read_contract
    contract = read_contract(get_app_data_dir(), ns.run_id)
    if contract is None:
        return None
    repo_name = str(_WORKTREE.get("name") or ctx.repo.name)
    code_tree = git_tree.write_tree(
        str(ctx.repo), (f".aidlc/deliverables/{ns.run_id}/",), {})
    base = _head_sha(ctx.repo)
    if base is None:
        raise RuntimeError("v1 runner cannot resolve repo base")
    return V1Runner(
        run_dir=run_dir, repo_root=ctx.repo, repo_name=repo_name,
        run_id=ns.run_id, base=base, code_tree=code_tree,
        contract=contract, python=ctx.python,
    )


def _v1_exits(runner) -> list[dict]:
    """DESIGN_SPEC's exit criteria, read from the v1 run's own document (B1): never
    from run.json, whose v1 ``stages`` is a status dict, not stage data."""
    try:
        design = json.loads((runner.run_dir / "artifacts" / "design_spec.json")
                            .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    exits = design.get("exit_criteria") if isinstance(design, dict) else None
    return [row for row in exits if isinstance(row, dict)] if isinstance(exits, list) else []


def _v1_main_red_provenance() -> dict[str, str | None]:
    from core import main_red
    path = main_red.registry_path()
    try:
        data = path.read_bytes()
        generated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except FileNotFoundError:
        data, generated = b"", None
    return {"sha": hashlib.sha256(data).hexdigest(), "generated_at": generated}


def _v1_finalize(ns, run_state: dict, runner, *, call: dict, result: str,
                 changed: list[str], cache_keys: list[str],
                 risk_status: str = "within_cap", selection: dict | None = None) -> dict:
    runner_hash = hashlib.sha256(
        (_BACKEND_DIR / "core" / "run_protocol" / "runner.py").read_bytes()).hexdigest()
    pipeline_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    actual = dict(selection or {})
    actual.setdefault("files", [])
    actual.setdefault("nodeids", [])
    actual.setdefault("summary", {})
    actual.setdefault("dependency_scope", {})
    actual.setdefault("risk_rules", [])
    actual.update(mode=ns.mode, rules_version=RUNNER_VERSION, risk_status=risk_status)
    nodes = runner.node_results()
    final = runner.finalize(
        changed_paths=changed, runner_code_hash=runner_hash,
        pipeline_code_hash=pipeline_hash,
        runner_argv=[str(Path(__file__).resolve()), *sys.argv[1:]],
        selection=actual,
        cache_keys=cache_keys, main_red=_v1_main_red_provenance(),
        trace=runner.trace(_v1_exits(runner), nodes), node_results=nodes,
    )
    return {"repo": runner.repo_name, "tests": final["tests"],
            "validations": final["validations"],
            "final": str(runner.run_dir / "evidence" / "tests" /
                         f"final-{runner.repo_name}.json")}


def _run_v1_validation(ns, run_state: dict, runner, changed: list[str]) -> tuple[dict, int]:
    entries = {entry["id"]: entry for entry in runner.required_validations(changed)}
    if not ns.name or ns.name not in entries:
        return {"mode": "validation", "error": "--name must identify a required base validation",
                "required": sorted(entries)}, 2
    call = runner.run_validation(entries[ns.name])
    prior = runner.prior_selection()
    selection = prior or {"files": [], "nodeids": [], "summary": {"validation": ns.name},
                          "dependency_scope": {}, "risk_rules": []}
    summary = _v1_finalize(ns, run_state, runner, call=call, result=call["result"],
                           changed=changed, cache_keys=[], selection=selection,
                           risk_status=str(prior.get("risk_status") or "within_cap"))
    return {"mode": "validation", "name": ns.name, "ok": call["result"] == "pass",
            "call": call, **summary}, (0 if call["result"] == "pass" else 1)



def _record_v1_test_result(ns, run_state: dict, runner, output: dict, code: int,
                           changed: list[str]) -> dict:
    call = runner.record_pipeline_call(
        argv=[str(Path(__file__).resolve()), *sys.argv[1:]],
        tool_version=f"pipeline-test-{RUNNER_VERSION}", target=ns.mode,
        output=output, exit_code=code)
    result = call["result"]
    selection = {"files": list(output.get("selected_files") or []),
                 "nodeids": list(output.get("selected_nodeids") or []),
                 "summary": dict(output.get("counts") or {}),
                 "dependency_scope": dict(output.get("dependency_scope") or {}),
                 "risk_rules": list(output.get("risk_rules") or [])}
    deps = _v1_test_dependencies(runner.repo_root, selection["files"])
    if deps is not None:
        selection["test_dependencies"] = deps
    summary = _v1_finalize(ns, run_state, runner, call=call, result=result,
                           changed=changed, selection=selection,
                           cache_keys=[str(output.get("cache_key"))]
                           if output.get("cache_key") else [],
                           risk_status=("incomplete" if output.get("risk_overflow")
                                        or output.get("skipped_for_budget_total") else "within_cap"))
    return {**output, "v1": summary}


def _run_v1_probes(ns, run_state: dict, runner, changed: list[str]) -> tuple[dict, int]:
    """Run the DESIGN_SPEC probe exits (all, or ``--name <exit id>``) through the
    runner's §A2.5 executor, then refresh the final so its trace carries them."""
    exits = [row for row in _v1_exits(runner) if row.get("kind") == "probe"]
    if ns.name:
        exits = [row for row in exits if row.get("id") == ns.name]
    if not exits:
        return {"mode": "probe", "error": "no matching probe exit in DESIGN_SPEC"}, 2
    from core.run_protocol.probe import ProbeError
    calls = []
    for row in exits:
        try:
            call = runner.run_probe(row)
        except ProbeError as exc:
            return {"mode": "probe", "error": f"{row.get('id')}: {exc}"}, 2
        calls.append({"exit": row["id"], "result": call["result"], "seq": call["seq"]})
    prior = runner.prior_selection()
    selection = prior or {"files": [], "nodeids": [], "summary": {"probes": len(calls)},
                          "dependency_scope": {}, "risk_rules": []}
    summary = _v1_finalize(ns, run_state, runner, call=calls[-1], result=calls[-1]["result"],
                           changed=changed, cache_keys=[], selection=selection,
                           risk_status=str(prior.get("risk_status") or "within_cap"))
    ok = all(row["result"] == "pass" for row in calls)
    return {"mode": "probe", "ok": ok, "probes": calls, **summary}, (0 if ok else 1)


def _v1_test_dependencies(repo: Path, files: list[str]) -> "dict[str, list[str]] | None":
    """``{selected backend test file: the repo files it imports, transitively}`` —
    what the engine's carry decision reads when main moves (fixing spec B3). None
    (no carry: the next land reruns) when there is nothing to map or it is invalid."""
    from core.run_protocol.carry import CarryError, test_dependency_map
    tests = [f for f in files if _is_backend_test(f)]
    if not tests:
        return None
    try:
        sg = build_source_graph(Path(repo), _cache_dir())
        return test_dependency_map({f: sorted(import_closure(Path(repo), sg, [f]) - {f})
                                    for f in tests})
    except (CarryError, OSError, ValueError):
        return None


def _v1_proof_bindings(values: list[str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for value in values:
        finding_id, sep, nodeid = value.partition("=")
        if not sep or not re.fullmatch(r"CF-[0-9a-f]{8}", finding_id) \
                or "::" not in nodeid or finding_id in bindings:
            raise ValueError(
                "--finding must uniquely use CF-1234abcd=backend/tests/test_x.py::test_name")
        bindings[finding_id] = nodeid
    return bindings

def execute(ns) -> tuple[dict, int]:
    """Run one parsed invocation; returns (output dict, exit code)."""
    if not _ID_RE.fullmatch(ns.project or ""):
        return {"error": f"unsafe --project: {ns.project!r}"}, 2
    run_state: dict = {}
    if ns.run_id:
        try:
            run_dir = _run_dir(ns.project, ns.run_id)
        except ValueError as exc:
            return {"error": str(exc)}, 2
        if not run_dir.is_dir():
            return {"error": f"run dir not found: {run_dir}"}, 2
        rs = _read_json(run_dir / "run.json")
        run_state = rs if isinstance(rs, dict) else {}
        ev_root = run_dir / "evidence"
    cache = _cache_dir()
    if not ns.run_id:
        ev_root = cache / "evidence" / ns.project
    deadline, dl_warn = _clamp_deadline(ns.deadline)
    if deadline is None:
        return {"error": dl_warn[0]}, 2
    if ns.mode in PROOF_MODES:
        if not ns.run_id:
            return {"error": f"--mode {ns.mode} needs --run-id"}, 2
        if not isinstance(ns.round, str) or not re.fullmatch(r"R[0-9]{1,2}", ns.round):
            return {"error": f"--mode {ns.mode} needs --round R1|R2 (got {ns.round!r})"}, 2
    if ns.mode in TEST_MODES + ("prove",) and deadline > SAFE_DEADLINE:
        # it would outlive the documented alarm 110 and die before writing evidence;
        # a scope that needs longer continues across calls instead
        return {"error": f"--deadline {deadline:g}s is over {SAFE_DEADLINE:g}s, the most a "
                         f"--mode {ns.mode} call may take: drop --deadline (default "
                         f"{DEFAULT_DEADLINE:g}s); a scope that does not fit continues in the "
                         "next call of the same command (continue: true)"}, 2
    repo, wt_err = _target_repo(ns.run_id, run_state)
    if wt_err:
        return {"error": wt_err}, 2
    ctx = _Ctx(repo, cache, deadline)
    ctx.warnings.extend(dl_warn)
    for w in dl_warn:
        print(f"pipeline_test.py: warning: {w}", file=sys.stderr)
    v1 = _v1_runner(ns, run_dir, run_state, ctx) if ns.run_id else None
    changed = changed_files(ctx.repo, run_state, ctx.warnings, (ns.project, ns.run_id))
    if ns.mode == "validation":
        if v1 is None:
            return {"mode": "validation", "error": "validation mode requires a v1 contract"}, 2
        return _run_v1_validation(ns, run_state, v1, changed)
    if ns.mode in PROOF_MODES and not _isolated(ctx):
        shutil.rmtree(ctx.workdir, ignore_errors=True)
        return {"mode": ns.mode, "error": f"--mode {ns.mode} needs an isolated run worktree "
                                          "(worktree_mode isolated): a snapshot of the shared "
                                          "checkout would include other sessions' edits"}, 2
    try:
        if ns.mode == "snapshot":
            return run_snapshot(ctx, ns, ev_root)
        if ns.mode == "prove":
            output, code = run_prove(ctx, ns, ev_root)
            if v1 is not None:
                bindings = _v1_proof_bindings(list(getattr(ns, "finding", []) or []))
                recorded = v1.record_proof_result(
                    output=output, exit_code=code, bindings=bindings)
                output = {**output, "v1": recorded}
            return output, code
        if ns.mode == "probe":
            if v1 is None:
                return {"mode": "probe", "error": "probe mode runs a v1 run's DESIGN_SPEC "
                                                  "probe exits: give --run-id"}, 2
            return _run_v1_probes(ns, run_state, v1, changed)
        if ns.mode == "mutation":
            output, code = run_mutations(ctx, ns, ev_root, run_state)
            if v1 is not None and code in (0, 1) and not output.get("error"):
                # B5: the mutation result is evidence of the run, so it is a signed call
                import time as _time
                now = _time.time()
                v1._record_result(
                    call_type="mutation", argv=[str(Path(__file__).resolve()), *sys.argv[1:]],
                    tool_version=f"pipeline-test-{RUNNER_VERSION}",
                    result="pass" if code == 0 else "fail", target=str(ns.name or "mutation"),
                    details={"evidence": output.get("evidence")}, started_at=now,
                    finished_at=now)
            return output, code
        output, code = _run_test_mode(ctx, ns, ev_root, run_state, deadline, v1=v1)
        if v1 is not None:
            output = _record_v1_test_result(ns, run_state, v1, output, code, changed)
        return output, code
    finally:
        shutil.rmtree(ctx.workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    from core.run_worktree import run_for_path
    if run_for_path(__file__) is not None:  # a run's half-edited copy of the runner
        print(json.dumps({"error": "this pipeline_test.py lives inside a run worktree; run "
                                   "the main checkout's backend/scripts/pipeline_test.py"}))
        return 2
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        ns = _parser().parse_args(args)
    except _ArgError as exc:
        print(json.dumps({"error": f"usage: {exc}"}))
        return 2
    prev = _install_signal_handlers()
    try:
        try:
            out, code = execute(ns)
        except _Interrupted as exc:
            _cleanup_private_worktrees()
            try:
                name = signal.Signals(exc.signum).name
            except ValueError:
                name = str(exc.signum)
            out, code = {"mode": ns.mode, "error": f"interrupted by {name}: running tests were "
                         "aborted and temporary worktrees removed; nothing was cached"}, 2
        except Exception as exc:  # noqa: BLE001 — the caller always gets one JSON object
            out, code = {"mode": ns.mode, "error": f"runner error: {type(exc).__name__}: {exc}"[:500]}, 2
        print(json.dumps(_compact(out), separators=(",", ":")), flush=True)
        return code
    finally:
        _restore_signal_handlers(prev)
        _cleanup_private_worktrees()  # normally empty; covers an unexpected exception path
        _LIVE_JOBS.clear()


if __name__ == "__main__":
    sys.exit(main())
