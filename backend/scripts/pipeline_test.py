"""Single test entry point for autonomous-pipeline runs (design §4-B and §4-G2).

The runner, not the model, decides the test scope from the change set. It runs
that scope sharded and in parallel under one global deadline, classifies every
failure against a HEAD baseline (B2), caches results by input digest, and writes
structured evidence that ``stage-complete`` reads. One call per mode; zero model cost.

Modes:
- ``tdd``        only the explicit ``--files`` (max 5); repeat counter per file
- ``verify``     changed test files + recent failures + transitive importers of changed
                 modules that the change reaches (they name a changed symbol or a same-file
                 caller of one; every importer of a module whose change cannot be told);
                 all of it runs, ``--deadline auto`` sizes the time to it) + tests
                 naming a changed backend/scripts/*.py file (subprocess-driven)
- ``regression`` L1 ac_coverage tests + L2 dependency scope + L3 import smoke
- ``recheck``    previous evidence failures + tests affected by files changed since
- ``probe``      G2 heavy probe with convergence rules (``--name N -- argv...``)
- ``mutation``   mutation testing in a throwaway worktree (``--mutations spec.json``)

Usage (from the repo root or backend/):
    perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py \\
        --project P [--run-id R] --mode MODE [--files F ...] [--deadline 95] [--json]

``--deadline`` defaults to 95 s so the runner reports before the documented
``alarm 110`` wrapper. Values above 100 s are allowed up to 1800 s (clamped) but
warn: the outer alarm must then be at least deadline + 10 s. SIGTERM / SIGALRM /
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
HEAD, pytest configs, the file, its conftest chain and their transitive imports, and
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
- ``run_probe(ctx, ns, ev_root, run_state)`` / ``run_mutations(ctx, ns, ev_root, run_state)``
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

RUNNER_VERSION = "2"
TEST_MODES = ("tdd", "verify", "regression", "recheck")
# Circuit breaker: stop a non-converging build<->verify loop from spinning forever.
# The loop is agent/skill-driven (prose caps only), so run_340478c9 ran 42 verify
# rounds over ~18h. Counted from the append-only run_index (per run_id). A hard ceiling
# catches runaway re-runs; a stall detector catches "same blocking failures N rounds
# running = not making progress". Operator override: SWARM_CIRCUIT_BREAKER_FORCE=1.
CIRCUIT_MAX_ROUNDS = 25    # total verify/recheck/regression runs in one run_id -> abort
CIRCUIT_STALL_ROUNDS = 4   # consecutive verify/recheck runs with the SAME blocking set -> abort
ALL_MODES = TEST_MODES + ("probe", "mutation")
TDD_MAX_FILES = 5
CAP_FILES = 40
SMOKE_CAP = 40
TDD_REPEAT_WARN = 8
# verify selection: tests that import a changed module directly (then only those the
# change reaches, ``affected_importers``). Depth 2 (test → module
# → changed module) selected 103-463 files for 21 changed ones (run_340478c9, run_7da44b0d).
REVERSE_DEPTH = 1
WRAPPER_ALARM_S = 110
DEFAULT_DEADLINE = 95.0
SAFE_DEADLINE = WRAPPER_ALARM_S - 10.0
MAX_DEADLINE = 1800.0
# --deadline auto (verify / regression): the runner sizes the deadline to the must-run
# set. One Bash call is capped at 600 s (Claude Code's BASH_MAX_TIMEOUT), so the wrapper
# is alarm 590 and the deadline at most 575. Hangs stay bounded by the per-test timeout.
AUTO_ALARM_S = 590
AUTO_MAX_DEADLINE = 575.0
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
# ac_coverage refs of the form probe:<name> are verified from evidence/probes/<name>.json.
PROBE_REF_PREFIX = "probe:"
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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data) -> None:
    """Atomic write (tmp + replace) so a concurrent reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


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
        if e.get("repo_name") != main.name:
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
        if n in git_set or (Path(repo) / n).exists():
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

    A ``files_touched`` entry counts as this repo's only if it exists here or git
    reports it changed (a deleted file): a relative entry may name a workspace file
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
        return {rel: e["mods"] for rel, e in old.items()}
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
    return {rel: e["mods"] for rel, e in entries.items()}


def build_import_graph(repo: Path, cache_dir: Path,
                       sgraph: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Map every backend test file to the module names it imports (from the source graph)."""
    sg = sgraph if sgraph is not None else build_source_graph(repo, cache_dir)
    return {rel: mods for rel, mods in sg.items() if _is_backend_test(rel)}


def _imports_module(mods: list[str], changed_mod: str, is_pkg: bool) -> bool:
    return any(m == changed_mod or (is_pkg and m.startswith(changed_mod + ".")) for m in mods)


def _changed_module_files(repo: Path, changed: list[str]) -> list[tuple[str, str, bool]]:
    """(file, module, is_package) for every changed, existing, non-test backend module."""
    out = []
    for f in changed:
        if f.startswith("backend/tests/") and Path(f).name.startswith(("test_", "conftest")):
            continue
        m = module_of(f)
        if m and (repo / f).is_file():
            out.append((f, m, f.endswith("__init__.py")))
    return out


_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)
# Too generic to tell one test from another when grepped as a word.
_GENERIC_NAMES = frozenset({"main", "run", "get", "set", "update", "load", "save", "init",
                            "__init__", "__call__", "setup", "close", "start", "stop"})


def _changed_lines(repo: Path, f: str) -> "list[int] | None":
    """New-side line numbers ``git diff -U0 HEAD`` touches in ``f`` (a deletion counts as
    the line it sits at). None = no diff available (untracked, git error)."""
    out = _git(repo, "diff", "-U0", "--no-color", "HEAD", "--", f)
    if not out:
        return None
    lines: list[int] = []
    for m in _HUNK_RE.finditer(out):
        start, cnt = int(m.group(2)), int(m.group(3) if m.group(3) is not None else 1)
        lines += list(range(start, start + cnt)) if cnt else [start]
    return lines or None


def changed_symbols(repo: Path, f: str) -> "set[str] | None":
    """Names a test must mention to be affected by this diff of backend module ``f``:
    the functions / classes (and module-level assignment targets) whose lines
    ``git diff HEAD`` touches, plus every function / class in the same file that calls
    or references one of them (one hop: ``_binding_findings`` changed →
    ``cmd_stage_complete``, which calls it, counts too).

    None = cannot tell, and the caller falls back to every direct importer: untracked
    file, no diff, unparsable, a changed line outside any def / class / assignment that
    is not blank, a comment or the module docstring (an import, a top-level call), or
    only names too generic to grep for."""
    lines = _changed_lines(repo, f)
    if not lines:
        return None
    text = _read_text(repo / f)
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    src = text.splitlines()
    names: set[str] = set()
    want = set(lines)
    covered: set[int] = set()
    doc = tree.body[0] if tree.body and isinstance(tree.body[0], ast.Expr) \
        and isinstance(getattr(tree.body[0], "value", None), ast.Constant) \
        and isinstance(tree.body[0].value.value, str) else None
    if doc is not None:
        covered |= set(range(doc.lineno, getattr(doc, "end_lineno", doc.lineno) + 1))
    defs: list = []  # every def / class node, any depth

    def visit(node, stack):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs.append(ch)
                end = getattr(ch, "end_lineno", ch.lineno)
                first = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                span = set(range(first, end + 1))
                if span & want:
                    covered.update(span)
                    names.update(stack + [ch.name])
                visit(ch, stack + [ch.name] if isinstance(ch, ast.ClassDef) else stack)
            elif node is tree and isinstance(ch, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                end = getattr(ch, "end_lineno", ch.lineno)
                span = set(range(ch.lineno, end + 1))
                if span & want:
                    covered.update(span)
                    tgts = ch.targets if isinstance(ch, ast.Assign) else [ch.target]
                    names.update(t.id for t in tgts if isinstance(t, ast.Name))

    visit(tree, [])
    imports = set()
    for n in tree.body:  # a top-level import only matters where it is used, and the use
        if isinstance(n, (ast.Import, ast.ImportFrom)):  # is in a changed symbol
            imports |= set(range(n.lineno, getattr(n, "end_lineno", n.lineno) + 1))
    for ln in want - covered - imports:
        body = src[ln - 1].strip() if 0 < ln <= len(src) else ""
        if body and not body.startswith("#"):
            return None  # a change outside any symbol: top-level code
    changed = set(names)
    for d in defs:  # one hop: same-file callers / referrers of a changed symbol
        if d.name in changed:
            continue
        for n in ast.walk(d):
            ref = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
            if ref in changed:
                names.add(d.name)
                break
    out = {n for n in names if len(n) >= 4 and n not in _GENERIC_NAMES}
    return out or None


def _script_name_re(repo: Path, changed: list[str]) -> "re.Pattern | None":
    """Whole-file-name pattern of the changed ``backend/scripts/*.py`` files: a test that
    names one drives that script by subprocess (no import edge reaches it)."""
    scripts = [Path(f).name for f in changed if f.startswith("backend/scripts/")
               and f.endswith(".py") and (repo / f).is_file()]
    if not scripts:
        return None
    # a whole file name: "x_test.py" must not match inside "test_x_test.py"
    return re.compile(r"(?<![\w.-])(?:" + "|".join(map(re.escape, scripts)) + r")\b")


_PLAIN_NAME_RE = re.compile(r"[a-z][a-z0-9]{0,11}")


def _symbol_patterns(module: str, syms: set[str]) -> list:
    """Regex a test must match to reach ``syms`` of ``module``. A distinctive name
    (an underscore, a capital, or 13+ chars) matches as a word. A plain short name
    (``create``, ``sweep``, ``land``) matches only qualified by the module
    (``run_worktree.create``, ``rw.create`` after ``import ... as rw``) or imported by
    name from it: as a bare word it matched 26 of 80 tests that never call it."""
    leaf = module.rsplit(".", 1)[-1]
    plain = sorted(n for n in syms if _PLAIN_NAME_RE.fullmatch(n))
    distinct = sorted(n for n in syms if n not in plain)
    alts = []
    if distinct:
        alts.append(r"\b(?:" + "|".join(map(re.escape, distinct)) + r")\b")
    if plain:
        p = "|".join(map(re.escape, plain))
        alts.append(r"\b" + re.escape(leaf) + r"\.(?:" + p + r")\b")
        alts.append(r"(?m)^\s*from\s+[\w.]*\b" + re.escape(leaf) + r"\s+import\b[^\n]*\b(?:"
                    + p + r")\b")
        alts.append(r"(?s)import\s+[\w.]*\b" + re.escape(leaf) + r"\s+as\s+(\w+)"
                    r"(?=.*\b\1\.(?:" + p + r")\b)")
    return [re.compile(a) for a in alts]


def affected_importers(repo: Path, changed: list[str], tests: list[str],
                       graph: dict[str, list[str]]) -> set[str]:
    """The tests among ``tests`` this change reaches, decided per changed module:
    a module whose changed symbols are known (``changed_symbols``, same-file callers
    included) reaches the tests that use one of them (``_symbol_patterns``); a module
    whose change cannot be told (new file, top-level code changed) reaches every test
    importing it. A test naming a changed script's file name (subprocess-driven) is
    reached too."""
    pats: list = []
    blind: list[tuple[str, bool]] = []
    blind_scripts: list[str] = []
    subs: set[str] = set()
    for f, m, pkg in _changed_module_files(repo, changed):
        got = changed_symbols(repo, f)
        if got is None:
            blind.append((m, pkg))
            if f.startswith("backend/scripts/"):
                blind_scripts.append(f)
        else:
            pats += _symbol_patterns(m, got)
            if f.startswith("backend/scripts/"):
                # a subprocess test reaches a changed cmd_<x> through its subcommand
                subs |= {n[4:].replace("_", "-") for n in got if n.startswith("cmd_")}
    # A test naming a script's file drives it by subprocess. When the script's change is
    # known, only the changed subcommands count: naming artifact_cli.py alone pulled in
    # 41 of 46 selected files for a change to one subcommand (run_925edb0e).
    named = _script_name_re(repo, blind_scripts)
    if named is not None:
        pats.append(named)
    if subs:
        pats.append(re.compile(r"(?<![\w-])(?:" + "|".join(map(re.escape, sorted(subs)))
                               + r")(?![\w-])"))
    out: set[str] = set()
    for t in tests:
        if any(_imports_module(graph.get(t) or [], m, pkg) for m, pkg in blind):
            out.add(t)
            continue
        body = _read_text(repo / t)
        if any(p.search(body) for p in pats):
            out.add(t)
    return out


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
    "backend/tests/test_check_rule_preservation.py",
)


def select_backend(repo: Path, changed: list[str], graph: dict[str, list[str]],
                   cap: int, recent: list[str] | tuple = (),
                   sgraph: dict[str, list[str]] | None = None,
                   depth: int = REVERSE_DEPTH) -> tuple[list[str], list[str], list[str]]:
    """Return ``(selected, skipped_for_budget, changed_module_names)``.

    Order: changed test files, then files that failed recently, then importers of
    changed modules sorted by import distance (direct importers first; with
    ``sgraph`` also transitive importers up to ``depth`` hops), ties broken by how
    many changed modules a test imports directly. A test that names a changed
    ``backend/scripts/*.py`` file by file name (a subprocess-driven CLI test) ranks as a
    direct importer. Overflow past ``cap`` is returned as skipped, never dropped silently.
    """
    changed_tests = [f for f in changed if _is_backend_test(f) and (repo / f).is_file()]
    # A skill card or expert file has no importers, yet the linters and the rule
    # preservation suite read them (run_376299fa pushed plan.md past the card limit
    # with regression green): select those suites as direct when one changed.
    if any(f.startswith(("backend/skills/", "backend/experts/")) for f in changed):
        changed_tests += [t for t in _DOC_LINT_SUITES
                          if t not in changed_tests and (repo / t).is_file()]
    mfiles = _changed_module_files(repo, changed)
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
    # Only the importers this change reaches (they name a changed symbol, a caller of
    # one in the same file, or a changed script): a hub module such as artifact_cli has
    # 60+ direct importers and most never touch what changed. The scope is fixed by the
    # change, never by machine load. When a changed module's symbols cannot be told
    # (imports / top-level code changed, new file), every direct importer counts and the
    # file cap bounds them, overflow in skipped.
    hit = affected_importers(repo, changed, [t for t in rank if t not in skip], graph)
    importers = [t for t in sorted(rank, key=lambda t: (rank[t], t)) if t in hit]
    # ``cap`` is kept for callers' signature; the reached set is the scope, uncapped.
    del cap
    return changed_tests + rec + importers, [], [m for m, _ in mods]


def must_run_files(repo: Path, plan: dict) -> set[str]:
    """Every selected target: since ``select_backend`` selects only what this change
    reaches (plus the run's own, AC and recent-failure tests), the whole selection is
    must-run and nothing is cut for time. Fixed by the change, not by machine load."""
    return set(plan.get("targets") or {}) | set(plan.get("ac_files") or []) \
        | set((plan.get("ac_first") or {}).keys())


# ── per-test event stream (survives a killed or os._exit()-ed shard) ────────
_PLUGIN_NAME = "_pipeline_test_events"
_PLUGIN_SRC = '''"""pytest plugin generated by pipeline_test.py.

Streams per-test events as JSON lines to $PIPELINE_TEST_EVENTS so outcomes are
known even when the shard is killed at the deadline or pytest-timeout exits.
"""
import json
import os

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


def pytest_runtest_logreport(report):
    _emit({"ev": "report", "nodeid": report.nodeid, "when": report.when,
           "outcome": report.outcome, "dur": float(getattr(report, "duration", 0.0) or 0.0),
           "longrepr": str(report.longrepr)[-3000:] if report.failed else ""})
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
BUDGET_FILL = 0.6


def _target_cost(f: str, sel, durations: dict) -> float:
    """Recorded seconds for one target (whole file or a nodeid set)."""
    if sel is not None:
        return sum(durations.get(n, DEFAULT_TEST_COST_S) for n in sel)
    known = [v for k, v in durations.items() if k.split("::")[0] == f]
    return sum(known) if known else DEFAULT_FILE_COST_S


def _seconds_needed(targets: dict, durations: dict, workers: int) -> float:
    """Wall seconds ``targets`` need on ``workers`` at BUDGET_FILL (0 without history)."""
    if not durations or not targets:
        return 0.0
    cost = sum(_target_cost(f, sel, durations) for f, sel in targets.items())
    return cost / (max(1, workers) * BUDGET_FILL)


def _must_run_refusal(ns, deadline_s: float, n_files: int, workers: int) -> dict:
    """Error for a must-run set that does not fit the deadline: the scope is fixed by
    the change, so the time grows, not the scope shrinks. Gives the exact command."""
    d = int(min(AUTO_MAX_DEADLINE, math.ceil(deadline_s / 10.0) * 10))
    run = f" --run-id {ns.run_id}" if ns.run_id else ""
    cmd = (f"perl -e 'alarm {AUTO_ALARM_S}; exec @ARGV' backend/.venv/bin/python "
           f"backend/scripts/pipeline_test.py --project {ns.project}{run} --mode {ns.mode} "
           f"--deadline auto")
    tmo = (AUTO_ALARM_S + 10) * 1000
    return {"mode": ns.mode, "error": (
        f"the {n_files} must-run test file(s) of this change (its own tests, AC tests, "
        f"tests reaching a changed symbol) need about {d}s on the {workers} worker(s) "
        f"granted, more than this call's deadline. They are never cut for time. Run "
        f"exactly this, with the Bash tool timeout set to {tmo}: {cmd}"),
        "next_action": cmd, "bash_timeout_ms": tmo}


def _is_auto(raw) -> bool:
    return isinstance(raw, str) and raw.strip().lower() == "auto"


def _cut_to_budget(targets: dict, durations: dict, workers: int, seconds: float,
                   protect: set) -> tuple[dict, list[str]]:
    """Keep targets, in priority order, while their recorded cost fits
    ``seconds × workers × BUDGET_FILL``; return ``(kept, cut)``.

    A deadline cut leaves evidence nobody can use (every unfinished test is a
    ``timeout``, not green, and the model re-runs the same call): of 130 verify /
    regression / recheck calls on 2026-10-18/19, 34 ended with deadline timeouts, most
    with 40 selected files on 1-4 granted workers. Cutting the low-priority tail up
    front gives a green-or-red answer on the files that matter, and names the rest in
    ``skipped_for_budget``. ``protect`` (the run's own tests, AC files) and the first
    target are never cut. No duration history = no cut."""
    if not durations or seconds <= 0:
        return targets, []
    budget = seconds * max(1, workers) * BUDGET_FILL
    kept: dict = {}
    cut: list[str] = []
    used = 0.0
    for i, (f, sel) in enumerate(targets.items()):
        c = _target_cost(f, sel, durations)
        if i == 0 or f in protect or used + c <= budget:
            kept[f] = sel
            used += c
        else:
            cut.append(f)
    return kept, cut


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
                          "cost": sum(durations.get(n, DEFAULT_TEST_COST_S) for n in rest)})
            continue
        known = [n for n in by_file.get(f, []) if not _matches_any(n, out_n)]
        whole.append({"rank": rank, "file": f, "args": args, "known": known,
                      "cost": sum(durations[n] for n in known) if known else DEFAULT_FILE_COST_S})
    nbins = max(1, workers * 2)
    share = sum(u["cost"] for u in whole) / nbins
    units: list[dict] = []
    for u in whole:
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
                   with_backend: bool = False) -> list[_Job]:
    desktop = ctx.repo / "desktop"
    env = dict(os.environ)
    rel = [f[len("desktop/"):] for f in desktop_files]
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
                            f"--maxWorkers={forks}"],
                 desktop, env, ctx.workdir, "vitest", json=out, files=desktop_files,
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
    for ln in _lines(_read_text(job.out)):
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
    probe:<name> matches the synthetic probe row _probe_row adds to the results."""
    if ref.startswith(PROBE_REF_PREFIX):
        return ref, None
    drv = _resolve_desktop_ref(repo, ref)
    if drv is not None:
        return drv if drv[0] else None
    return _resolve_ref(repo, ref)


def _probe_row(ev_root: Path, repo: Path, ref: str, changed: list[str]) -> dict:
    """Verdict for a ``probe:<name>`` AC ref, as a result row (layer "probe").

    Passes only if the probe's evidence is converged, its latest EXECUTED round saw
    zero current findings, and it converged on THIS run's current changed files (the
    target digest run_probe computes when called without --files). Anything else fails
    closed with the re-run command: a probe run on a narrower --files set, old evidence
    without current_findings, a latest round whose probe exited non-zero (a crashed
    probe that printed no findings checked nothing), or any edit to the changed files
    since (another session's included).
    """
    name = ref[len(PROBE_REF_PREFIX):]
    rerun = f"re-run it: --mode probe --name {name} -- <probe command> (without --files)"

    def failed(msg: str) -> dict:
        return _res(ref, "failed", [msg], file=ref, layer="probe")
    if not _ID_RE.fullmatch(name):
        return failed(f"invalid probe name {name!r}")
    st = _read_json(ev_root / "probes" / f"{name}.json")
    if not isinstance(st, dict):
        return failed(f"no evidence for probe {name}; {rerun}")
    if not st.get("converged"):
        return failed(f"probe {name} has not converged; {rerun}")
    executed = [r for r in st.get("rounds") or [] if isinstance(r, dict) and not r.get("cached")]
    current = executed[-1].get("current_findings") if executed else None
    if current is None:
        return failed(f"probe {name} evidence has no current_findings (older runner); {rerun}")
    if executed[-1].get("rc") != 0:
        return failed(f"probe {name}: its latest round exited rc={executed[-1].get('rc')}; {rerun}")
    if current:
        return failed(f"probe {name}: its latest round still reports {current} finding(s)")
    if (st.get("state") or {}).get("converged_target") != _digest_files(repo, changed):
        return failed(f"probe {name} is stale: this run's changed files changed since it "
                      f"converged; {rerun}")
    return _res(ref, "passed", file=ref, layer="probe")


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
                    rechecked_from: str | None = None) -> Path:
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
            "rechecked_from": rechecked_from, "blocking_sig": _blocking_sig(ev)})
    except OSError:  # bookkeeping only; never fails the evidence write
        pass
    try:
        _prune_evidence(tdir)
    except OSError:  # housekeeping only; the evidence above is already written
        pass
    return path


RUN_INDEX = "run_index.json"


def _append_run_index(tdir: Path, entry: dict) -> None:
    """Append ``entry`` to ``tests/run_index.json`` (append-only, never pruned; format in
    the module docstring) under ``.run_index.lock``, atomically. A held lock past 5 s
    still appends (like the tdd index): a lost record would under-count the budget."""
    fd = _try_lock(tdir / ".run_index.lock", 5)
    try:
        cur = _read_json(tdir / RUN_INDEX)
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


def _breaker_result(mode: str, reason: str) -> dict:
    """Abort payload (exit 2) when the circuit breaker trips. Points at run-checkpoint;
    re-running is futile because the breaker re-trips on the unchanged run_index until the
    run is checkpointed or the root cause is fixed."""
    return {"mode": mode, "ok": False, "circuit_breaker": {"tripped": True, "reason": reason},
            "error": "circuit breaker: " + reason,
            "action": ("run-checkpoint (true trigger: stuck / retries exhausted) and fix the root "
                       "cause or escalate — do NOT re-run tests, the loop is not converging. "
                       "Operator override only: SWARM_CIRCUIT_BREAKER_FORCE=1")}


def _circuit_breaker_check(ev_root: Path, mode: str) -> dict | None:
    """Refuse another verify/recheck/regression when this run is in a non-converging loop
    (prevents run_340478c9's 42-round / ~18h spin). Reads the append-only run_index;
    returns an abort dict (caller exits 2) or None to proceed. tdd is never gated — it is
    the write-tests phase and is expected to repeat. Operator override:
    SWARM_CIRCUIT_BREAKER_FORCE=1."""
    if mode not in ("verify", "recheck", "regression"):
        return None
    if os.environ.get("SWARM_CIRCUIT_BREAKER_FORCE") == "1":
        return None
    idx = _read_json(ev_root / "tests" / RUN_INDEX)
    if not isinstance(idx, list):
        return None
    runs = [e for e in idx if isinstance(e, dict) and e.get("kind") == "run"
            and e.get("mode") in ("verify", "recheck", "regression")]
    if len(runs) >= CIRCUIT_MAX_ROUNDS:
        return _breaker_result(
            mode, f"{len(runs)} verify/recheck/regression rounds in this run (hard limit "
            f"{CIRCUIT_MAX_ROUNDS}) — the build<->verify loop is not converging")
    vr = [e for e in runs if e.get("mode") in ("verify", "recheck")]
    sigs = [tuple(e.get("blocking_sig") or []) for e in vr[-CIRCUIT_STALL_ROUNDS:]]
    if len(sigs) >= CIRCUIT_STALL_ROUNDS and sigs[0] and all(s == sigs[0] for s in sigs):
        shown = list(sigs[0])[:5]
        return _breaker_result(
            mode, f"{CIRCUIT_STALL_ROUNDS} consecutive verify/recheck rounds left the SAME "
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
        data = _read_json(idx)
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


# ── per-file reuse between verify / regression runs of one pipeline run ────
# Measured on the recorded runs (2026-10-18): of 49,049 L2 test executions in
# consecutive verify / regression calls of one run, 9,548 (301 files) were of an
# all-pass file on the same HEAD with no changed file edited in between.
REUSE_MODES = ("verify", "regression")
_REUSABLE_OUTCOMES = ("passed", "skipped")


def _l2_file_keys(ctx, files, changed: list[str], sgraph: dict | None,
                  head: str | None) -> dict[str, str]:
    """test file → digest of every input a WHOLE-file result of it depends on.

    Runner version, interpreter, HEAD, pytest configs, the file, its conftest chain and
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

    common = hashlib.sha256("\0".join([RUNNER_VERSION, _python_id(ctx.python),
                                       head or "none"]).encode())
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


def _probe_rows(ctx, ev_root: Path, refs: list[str], changed: list[str]) -> list[dict]:
    """Evaluate every probe:<name> ref now; a failing one is also a warning (its summary
    names the re-run command). A recheck of a regression calls this too, so a probe
    that went stale (or re-converged) since the base is never carried over by overlay."""
    rows = [_probe_row(ev_root, ctx.repo, r, changed) for r in dict.fromkeys(refs)]
    for row in rows:
        if row["outcome"] != "passed":
            ctx.warnings.append(f"ac_coverage {row['nodeid']}: {row['summary']}")
    return rows


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
            elif n.startswith(PROBE_REF_PREFIX):
                plan["rechecked"] += 1  # re-evaluated below with every other probe ref
            elif n in ("desktop::vitest", "desktop::tsc") or n.startswith("desktop/"):
                plan["desktop_recheck"] = True
                plan["desktop_tsc"] = plan.get("desktop_tsc") or n == "desktop::tsc"
                plan["rechecked"] += 1
            else:
                lost.append(n or "<no nodeid>")
        before = prev.get("changed_files") if isinstance(prev.get("changed_files"), dict) else {}
        scope = [f for f in changed if before.get(f) != _file_digest(ctx.repo / f)]
        if plan["base_mode"] == "regression":
            plan["probe_rows"] = _probe_rows(ctx, ev_root, [
                r for refs in _ac_entries(ns.project, run_state) for r in refs
                if r.startswith(PROBE_REF_PREFIX)], changed)
    recent = _recent_failure_files(ev_root) if mode != "recheck" else []
    sel, skipped, _ = select_backend(ctx.repo, scope, graph, CAP_FILES, recent, sgraph)
    for f in sel:
        _add_target(plan["targets"], f, None)
    plan["l2"], plan["skipped"] = sel, list(skipped)
    if mode == "regression":
        plan["ac_entries"] = _ac_entries(ns.project, run_state)
        ac_files: list[str] = []
        ac_desktop: list[str] = []
        probe_refs: list[str] = []
        for refs in plan["ac_entries"]:
            for ref in refs:
                if ref.startswith(PROBE_REF_PREFIX):
                    probe_refs.append(ref)
                    continue
                drv = _resolve_desktop_ref(ctx.repo, ref, ctx.warnings)
                if drv is not None:  # desktop: vitest file list only, never a pytest target
                    if drv[0]:
                        ac_desktop.append(drv[0])
                    continue
                rv = _resolve_ref(ctx.repo, ref)
                if rv is None:
                    ctx.warnings.append(f"ac_coverage test ref not found: {ref}")
                    continue
                _add_target(plan["targets"], *rv)
                ac_files.append(rv[0])
                if rv[1] is not None:
                    plan.setdefault("ac_first", {}).setdefault(rv[0], set()).add(rv[1])
        plan["ac_files"] = list(dict.fromkeys(ac_files))  # L1: never reused (_reusable)
        plan["ac_desktop"] = list(dict.fromkeys(ac_desktop))
        plan["probe_rows"] = _probe_rows(ctx, ev_root, probe_refs, changed)
        # AC targets first in priority order (a whole-file AC ref is not split out).
        plan["targets"] = {**{f: plan["targets"][f] for f in dict.fromkeys(ac_files)},
                           **plan["targets"]}
        mods = _smoke_modules(ctx, changed)
        names = list(mods)
        plan["smoke"] = {m: mods[m] for m in names[:SMOKE_CAP]}
        plan["skipped"] += [f"import::{m}" for m in names[SMOKE_CAP:]]
    # A desktop failure being rechecked reruns the run's desktop files even if none
    # changed since: the failure is the reason to run, not a digest change.
    plan["desktop"] = [f for f in (changed if plan.get("desktop_recheck") else scope)
                       if f.startswith("desktop/src/") and (ctx.repo / f).is_file()]
    # AC-named desktop tests run in the same vitest job (and so join its cache key).
    plan["desktop"] += [f for f in plan.get("ac_desktop", []) if f not in plan["desktop"]]
    if mode == "recheck" and plan.get("desktop_recheck") and not plan["desktop"]:
        lost.append("desktop:: (no desktop/src file in this run's change set)")
    if mode == "recheck" and lost and getattr(ns, "auto_recheck_of", None):
        # An automatic recheck that cannot re-run every failure (e.g. a shard:: runner
        # error) would keep that failure forever; the caller falls back to a full run.
        return plan, f"auto-recheck cannot re-target: {', '.join(lost[:5])}"
    if mode == "recheck" and prev_failures:
        if not (plan["targets"] or plan["smoke"] or plan["desktop"]
                or plan.get("probe_rows") or plan.get("ordered")):
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
                  prev: dict) -> "tuple[dict, int] | None":
    """Same inputs as the last ``ns.mode`` run (same cache key) and that result was not
    cacheable (a timeout, an unknown classification or a runner error): re-running all
    of it would spend the budget on tests that already passed. Run a recheck — only
    the failed and not-run tests — and overlay it onto the ``ns.mode`` evidence, the
    file the TEST stage reads. Returns None — run the full mode instead — when the
    recheck cannot re-target every failure, skipped tests for budget, or did not land
    on THIS mode's evidence."""
    note = (f"inputs identical to the last {ns.mode}, whose result was not cacheable: "
            "ran a recheck of its failed / not-run tests and overlaid the results")
    ctx.warnings.append(note)  # lands in the overlaid evidence; withdrawn on fallback
    ns2 = argparse.Namespace(**{**vars(ns), "mode": "recheck", "auto_recheck_of": ns.mode})
    out, code = _run_test_mode(ctx, ns2, ev_root, run_state, deadline)
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
    # An order_dependent failure only reproduces after its shard prefix / culprit: a
    # pass from a recheck that did not re-run that context is no verdict on it.
    in_ctx = set(re_ev.get("od_in_context") or [])
    held = {f.get("nodeid") for f in base_fails if f.get("order_dependent")
            and f.get("nodeid") not in in_ctx
            and (new.get(f.get("nodeid")) or {}).get("outcome") not in _FAIL}
    new = {n: r for n, r in new.items() if n not in held}
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
    if base.get("mode") == "regression" and ac_entries:
        layers["ac_driven"] = _ac_layer(repo, ac_entries, results)
    ev = dict(base)
    # The tree the merged evidence describes is the RECHECK's: it re-selected tests for
    # every file changed since the base (an overlay with budget skips is never made), so
    # its file digests, HEAD and tree digest are the ones staleness checks must compare.
    for k in ("changed_files", "head_sha", "tree_digest"):
        if re_ev.get(k) is not None:
            ev[k] = re_ev[k]
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
    ev.update(results=results, counts=counts, failures=failures, runner_errors=runner_errors,
              layers=layers, finished_at=_now_iso(), cached=False,
              rechecked_from={"finished_at": base.get("finished_at"),
                              "rechecked": len(new), "auto": auto,
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


def _run_test_mode(ctx, ns, ev_root: Path, run_state: dict, deadline: float) -> tuple[dict, int]:
    started_at = _now_iso()
    if ns.run_id:
        tripped = _circuit_breaker_check(ev_root, ns.mode)
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
    key = _cache_key(ctx, ns.mode, targets, plan["smoke"],
                     plan["explicit"] + [json.dumps(plan["ac_entries"]),
                                         json.dumps(plan.get("probe_rows") or []),
                                         json.dumps(plan["skipped"])]
                     + ([json.dumps(ordered)] if ordered else []),
                     inputs, head)
    tree = _tree_digest(inputs, changed_in, list(targets) + list(ord_files)
                        + list(plan["smoke"].values()))
    if ns.mode in ("verify", "regression") and not getattr(ns, "auto_recheck_of", None):
        prev = _read_json(ev_root / "tests" / f"latest-{ns.mode}.json")
        # a budget-cut result is complete for what it ran: nothing for a recheck to finish
        if isinstance(prev, dict) and prev.get("cache_key") == key \
                and not _cacheable({**prev, "budget_cut": False}):
            auto = _auto_recheck(ctx, ns, ev_root, run_state, deadline, prev)
            if auto is not None:
                return auto
    idx_info = {"auto_recheck_of": getattr(ns, "auto_recheck_of", None),
                "rechecked_from": plan.get("base_finished_at") if ns.mode == "recheck" else None}
    fmode = ns.mode + ("+tsc" if plan.get("desktop_tsc") else "")
    fkey = _frontend_key(ctx, fmode, plan["desktop"], plan["changed"]) if plan["desktop"] else ""
    cpath = ctx.cache / "results" / f"{key}{fkey[:16]}.json"
    hit = _read_json(cpath)
    if isinstance(hit, dict) and hit.get("schema") == 1 and _cacheable(hit):
        ev = dict(hit)
        ev.pop("reused_from", None)  # names evidence of the call that wrote the cache entry
        ev.update(cached=True, duration_s=0, run_id=ns.run_id, started_at=started_at,
                  finished_at=_now_iso(), tdd_repeats=repeats,
                  warnings=ctx.warnings + ["cached: tests and their inputs are unchanged since the last run"],
                  changed_files=_digest_map(ctx.repo, plan["changed"]),
                  head_sha=head, tree_digest=tree)
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
    run_targets = {f: v for f, v in targets.items() if f not in reused}
    # Machine-wide worker tokens: this call runs only as many workers as it holds, so
    # concurrent runs share the cores instead of oversubscribing them.
    tokens = _acquire_workers(ctx.cache, ctx.workers, ctx.warnings)
    ctx.workers = max(1, len(tokens))
    budget_cut: list[str] = []
    if ns.mode in REUSE_MODES and run_targets:
        protect = must_run_files(ctx.repo, plan)
        reserve = min(20.0, deadline * 0.25)
        secs = ctx.deadline_at - reserve - time.monotonic()
        must = {f: v for f, v in run_targets.items() if f in protect}
        need = _seconds_needed(must, ctx.durations, ctx.workers)
        if _is_auto(ns.deadline):
            # size the deadline to the must-run set (+ the reserve and start-up), never
            # below the default; the tail beyond it is cut below as before
            want = max(DEFAULT_DEADLINE, (time.monotonic() - ctx.t0) + need + reserve + 15)
            ctx.deadline_at = ctx.t0 + min(AUTO_MAX_DEADLINE, want)
            secs = ctx.deadline_at - reserve - time.monotonic()
            if need > secs:
                ctx.warnings.append(
                    f"the must-run tests need about {need:.0f}s on {ctx.workers} worker(s), over "
                    f"the {AUTO_MAX_DEADLINE:g}s a single call allows: some will be reported "
                    f"'timeout'; re-run the same command and the runner finishes only those")
        elif need > secs:
            _release_workers(tokens)
            return _must_run_refusal(ns, need + reserve + 5, len(must), ctx.workers), 2
        run_targets, budget_cut = _cut_to_budget(run_targets, ctx.durations, ctx.workers,
                                                 secs, protect)
        if budget_cut:
            for f in budget_cut:
                plan["targets"].pop(f, None)
            plan["skipped"] = list(plan["skipped"]) + budget_cut
            plan["budget_cut"] = True
            ctx.warnings.append(
                f"{len(budget_cut)} lower-priority test file(s) left out: their recorded cost "
                f"does not fit {secs:.0f}s on the {ctx.workers} worker(s) granted (other runs "
                f"hold the rest). Listed in skipped_for_budget; this result is not cached, so "
                f"a later call on a quieter machine runs them")
    try:
        jobs: list[_Job] = []
        fpath = ctx.cache / "results" / f"frontend-{fkey}.json"
        fhit = _read_json(fpath) if fkey else None
        if fkey and not isinstance(fhit, list):
            jobs += _frontend_jobs(ctx, plan["desktop"],
                                   ns.mode in ("verify", "regression") or bool(plan.get("desktop_tsc")),
                                   with_backend=bool(run_targets or ordered))
        if plan["smoke"]:
            sj = _smoke_job(ctx, list(plan["smoke"]))
            sj.meta["files"] = plan["smoke"]
            jobs.append(sj)
        if run_targets or ordered:
            _drop_stale_pyc(ctx.repo, plan["changed"])
        ctx.seq += 1
        backend = ctx.repo / "backend"
        # recheck of order_dependent failures: their prefix runs as ONE ordered job, first
        jobs = [_pytest_job(ctx, f"ordered{ctx.seq}-{k}", o, backend, False)
                for k, o in enumerate(ordered)] + jobs
        jobs += _pytest_jobs(ctx, run_targets, "main", first=plan.get("ac_first"),
                             durations=ctx.durations) if run_targets else []
        if tokens:
            tokens = _trim_tokens(tokens, jobs)
            ctx.workers = len(tokens)  # re-runs in classify_failures stay inside them too
        main_end = ctx.deadline_at - min(20.0, deadline * 0.25)
        _run_jobs(jobs, ctx.workers, main_end)
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
            if fres and not any(r["outcome"] in _FAIL for r in fres):
                _write_json(fpath, fres)
        cls = classify_failures(ctx, results)
    finally:
        _release_workers(tokens)
    # Reused entries are evidence entries already (compact): _assemble keeps them as is.
    results += [{**r, "_reused": True} for f in reused for r in reused[f]]
    ev = _assemble(ctx, ns, plan, results, cls, started_at, repeats, key)
    ev.update(head_sha=head, tree_digest=tree)
    if plan.get("budget_cut"):
        ev["budget_cut"] = True  # depends on the workers granted: never served from cache
    if _cacheable(ev):
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


def _assemble(ctx, ns, plan, results, cls, started_at, repeats, key) -> dict:
    results = list(results) + list(plan.get("probe_rows") or [])
    counts = {"passed": 0, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for r in results:
        counts[_COUNT_KEY[r["outcome"]]] += 1
    # a failing probe verdict is definite ("new"); the cache key already holds the verdict
    cls = {**cls, **{r["nodeid"]: "new" for r in plan.get("probe_rows") or []}}
    refs = getattr(ctx, "ref_ids", None) or ()
    failures = [{**_capped_id(r["nodeid"], refs), "classification": cls.get(r["nodeid"], "unknown"),
                 "summary": "\n".join(r["summary"][:STACK_LINES + 1]),
                 "outcome": r["outcome"], "deadline": bool(r.get("deadline")),
                 **getattr(ctx, "order_dep", {}).get(r["nodeid"], {})}
                for r in results if r["outcome"] in _FAIL]
    dl_timeouts = sum(1 for f in failures if f.get("classification") == "timeout")
    if dl_timeouts:
        ctx.warnings.append(
            f"{dl_timeouts} test(s) hit the GLOBAL deadline and were NOT verified "
            "(classification 'timeout'): this is NOT a code failure — do not edit code for "
            "it. Re-run the SAME command unchanged: it re-runs only the unfinished tests, "
            "and the result is not green until they have run")
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
    ev = {"schema": 1, "mode": ns.mode, "run_id": ns.run_id, "started_at": started_at,
          "finished_at": _now_iso(), "duration_s": round(time.monotonic() - ctx.t0, 2),
          "cached": False,
          "selected_files": sorted(set(plan["targets"]) | set(plan["desktop"])
                                   | {"backend/" + a.split("::")[0] for o in plan.get("ordered") or []
                                      for a in o if not a.startswith(_SHARD_MODIFIERS)}),
          "skipped_for_budget": plan["skipped"], "counts": counts,
          "results": [{k: v for k, v in r.items() if k != "_reused"} if r.get("_reused")
                      else {**_evidence_result(r, refs),
                            **({"requeued": True} if r.get("requeued") else {})}
                      for r in results],
          "failures": failures, "layers": layers, "tdd_repeats": repeats,
          "warnings": list(ctx.warnings), "cache_key": key,
          "changed_files": _digest_map(ctx.repo, plan["changed"]),
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
        and not ev.get("budget_cut") \
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
    out = {"mode": ev.get("mode"), "cached": bool(ev.get("cached")), "ok": _exit_code(ev) == 0,
           "counts": ev.get("counts") or {}, "duration_s": ev.get("duration_s", 0),
           "selected": len(ev.get("selected_files") or []),
           "failures": [{"nodeid": f.get("nodeid"), "classification": f.get("classification", "unknown"),
                         "summary": _short(str(f.get("summary") or ""), SUMMARY_LINES),
                         **{k: f[k] for k in ("order_dependent", "culprit") if k in f}}
                        for f in fails[:10]],
           "failures_total": len(fails),
           "skipped_for_budget": skipped[:10],
           "skipped_for_budget_total": len(skipped),
           "evidence": str(path), "warnings": ev.get("warnings") or []}
    if ev.get("mode") in ("regression", "verify", "recheck"):
        out["layers"] = ev.get("layers") or {}
    for k in ("frontend", "tdd_repeats", "rechecked_failures", "runner_errors", "overlaid_onto",
              "rechecked_from"):
        if ev.get(k):
            out[k] = ev[k][:10] if k == "runner_errors" else ev[k]
    return out


# ── G2 probe mode ───────────────────────────────────────────────────────────
def _digest_files(repo: Path, files: list[str]) -> str:
    h = hashlib.sha256()
    for f in sorted(set(files)):
        h.update(f.encode() + b"\0" + _file_digest(repo / f).encode() + b"\0")
    return h.hexdigest()[:32]


def _probe_digest(repo: Path, argv: list[str]) -> str:
    """argv + contents of every argv element that names an existing file."""
    h = hashlib.sha256()
    for a in argv:
        h.update(a.encode() + b"\0")
        p = Path(a) if Path(a).is_absolute() else repo / a
        try:
            if p.is_file():
                h.update(_file_digest(p).encode() + b"\0")
        except OSError:
            pass
    return h.hexdigest()[:32]


def _probe_out(st: dict, path: Path, **kw) -> dict:
    out = {"mode": "probe", "name": st["name"], "converged": st["converged"],
           "rounds": len(st["rounds"]), "findings_total": len(st["findings"]),
           "evidence": str(path)}
    out.update(kw)
    return out


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


def run_probe(ctx, ns, ev_root: Path, run_state: dict) -> tuple[dict, int]:
    """One probe round under the G2 convergence rules (see module docstring)."""
    name, argv = ns.name or "", list(ns.probe_argv or [])
    if not _ID_RE.fullmatch(name):
        return {"mode": "probe", "error": "--name must match [A-Za-z0-9_-]{1,64}"}, 2
    if not argv:
        return {"mode": "probe", "error": "probe needs its command after `--` (list form)"}, 2
    files = []
    for raw in ns.files or []:
        rel = _input_path(ctx.repo, raw)
        if rel is None:
            return {"mode": "probe", "error": f"--files entry not found: {raw}"}, 2
        files.append(rel.split("::")[0])
    target = _digest_files(ctx.repo, files or changed_files(
        ctx.repo, run_state, ctx.warnings, (ns.project, ns.run_id)))
    probe = _probe_digest(ctx.repo, argv)
    path = ev_root / "probes" / f"{name}.json"
    fd = _try_lock(ev_root / "probes" / f".{name}.lock", 10)
    try:
        st = _read_json(path)
        if not isinstance(st, dict) or not isinstance(st.get("rounds"), list):
            st = {"schema": 1, "name": name, "rounds": [], "converged": False, "findings": []}
        s = st.setdefault("state", {})
        if st["converged"] and s.get("converged_target") != target:
            st["converged"] = False
            s.update(extension_used=False, seen_probes=[])
            ctx.warnings.append("target changed since convergence: convergence reset")
        extension = bool(st["converged"])
        if st["converged"] and probe in (s.get("seen_probes") or []):
            st["rounds"].append({"at": _now_iso(), "target_digest": target, "probe_digest": probe,
                                 "new_findings": 0, "findings_total": len(st["findings"]),
                                 "duration_s": 0, "cached": True})
            _write_json(path, st)
            return _probe_out(st, path, cached=True, new_findings=0, warnings=ctx.warnings,
                              message="converged and target + probe unchanged: returned the "
                                      "last result without running"), 0
        if st["converged"] and s.get("extension_used"):
            return _probe_out(st, path, cached=False, refused=True, new_findings=0,
                              error="refused: this probe already converged and its one extra "
                                    "round after a probe change found nothing. Further probe "
                                    "changes need a new finding first (or a target change)."), 2
        job = _Job("probe", argv, ctx.repo, dict(os.environ), ctx.workdir, "probe")
        _run_jobs([job], 1, ctx.deadline_at)
        if job.killed or not job.started:
            return _probe_out(st, path, cached=False, error="probe hit the global deadline",
                              stack=_stack(job)[:SUMMARY_LINES] if job.started else []), 2
        findings = _parse_findings(_read_text(job.out))
        if findings is None:
            return _probe_out(st, path, cached=False,
                              error=f'probe stdout is not JSON {{"findings": [...]}} (rc={job.rc})',
                              tail=_lines(job.text())[-SUMMARY_LINES:]), 2
        new, known = _new_findings(st, findings, ctx.warnings)
        st["findings"].extend(new)
        st["rounds"].append({"at": _now_iso(), "target_digest": target, "probe_digest": probe,
                             "new_findings": len(new), "findings_total": len(st["findings"]),
                             "duration_s": round(job.duration, 2), "cached": False, "rc": job.rc,
                             "current_findings": len(findings)})
        if new:
            st["converged"] = False
            s.update(extension_used=False, seen_probes=[])
            s.pop("converged_target", None)
        else:
            seen = list(s.get("seen_probes") or []) if extension else []
            st["converged"] = True
            s.update(converged_target=target, extension_used=extension, seen_probes=seen + [probe])
        _write_json(path, st)
    finally:
        _unlock(fd)
    msg = (f"{len(new)} new finding(s); not converged" if new else
           "0 new findings: converged" + (" (final: extension round)" if extension else ""))
    return _probe_out(st, path, cached=False, new_findings=len(new), warnings=ctx.warnings,
                      new_finding_ids=[str(f["id"]) for f in new[:10]], message=msg), (1 if new else 0)


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


# ── CLI ─────────────────────────────────────────────────────────────────────
def _size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":")))


def _compact(out: dict) -> dict:
    """Shrink the printed object toward OUTPUT_BUDGET bytes; evidence keeps everything."""
    out = json.loads(json.dumps(out))
    steps = [("summary", 5), ("summary", 2), ("warnings", 3), ("failures", 5),
             ("summary", 0), ("skipped_for_budget", 3), ("failures", 3), ("warnings", 1)]
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
    p.add_argument("--deadline", default=DEFAULT_DEADLINE,
                   help="seconds, or 'auto' (verify / regression: sized to the must-run set, "
                        f"up to {AUTO_MAX_DEADLINE:g}s; wrap in alarm {AUTO_ALARM_S} and give "
                        f"the Bash call timeout {(AUTO_ALARM_S + 10) * 1000})")
    p.add_argument("--json", action="store_true", help="accepted for clarity; output is always JSON")
    return p


def _clamp_deadline(raw) -> tuple[float | None, list[str]]:
    if isinstance(raw, str) and raw.strip().lower() == "auto":
        return AUTO_MAX_DEADLINE, []  # ceiling; _run_test_mode sizes it to the must-run set
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
    repo, wt_err = _target_repo(ns.run_id, run_state)
    if wt_err:
        return {"error": wt_err}, 2
    ctx = _Ctx(repo, cache, deadline)
    ctx.warnings.extend(dl_warn)
    for w in dl_warn:
        print(f"pipeline_test.py: warning: {w}", file=sys.stderr)
    try:
        if ns.mode == "probe":
            return run_probe(ctx, ns, ev_root, run_state)
        if ns.mode == "mutation":
            return run_mutations(ctx, ns, ev_root, run_state)
        return _run_test_mode(ctx, ns, ev_root, run_state, deadline)
    finally:
        shutil.rmtree(ctx.workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    from core.run_worktree import run_for_path
    if run_for_path(__file__) is not None:  # a run's half-edited copy of the runner
        print(json.dumps({"error": "this pipeline_test.py lives inside a run worktree; run "
                                   "the main checkout's backend/scripts/pipeline_test.py"}))
        return 2
    args = list(sys.argv[1:] if argv is None else argv)
    probe_argv: list[str] = []
    if "--" in args:
        i = args.index("--")
        args, probe_argv = args[:i], args[i + 1:]
    try:
        ns = _parser().parse_args(args)
    except _ArgError as exc:
        print(json.dumps({"error": f"usage: {exc}"}))
        return 2
    ns.probe_argv = probe_argv
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
