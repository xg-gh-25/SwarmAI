"""Single test entry point for autonomous-pipeline runs (design §4-B and §4-G2).

The runner, not the model, decides the test scope from the change set. It runs
that scope sharded and in parallel under one global deadline, classifies every
failure against a HEAD baseline (B2), caches results by input digest, and writes
structured evidence that ``stage-complete`` reads. One call per mode; zero model cost.

Modes:
- ``tdd``        only the explicit ``--files`` (max 5); repeat counter per file
- ``verify``     changed test files + recent failures + transitive importers of changed
                 modules (reverse import closure, depth ≤ REVERSE_DEPTH; cap 40)
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
- 1 = failures, any timeout, a selected file / nodeid that collected zero tests,
      a shard that exited abnormally (``runner_errors``), or new probe findings
- 2 = usage error, refusal, runner error, or interrupted by a signal

Cache soundness (H1): the result-cache key covers HEAD, the selected tests, the
TRANSITIVE closure of backend-local imports of the tests, every conftest.py on their
path (and its closure), pytest config files, the smoke modules (and closure), and the
content of every changed file. A result with a timeout, an ``unknown``
classification or a runner error is never cached.

Classification (B2, see ``classify_failures``): a failing test's whole FILE is re-run
in its original order (catches order-dependence inside the file). The HEAD baseline
only records tests that reached a call report; a not-run test is ``unknown`` at HEAD
(never cached, never pre-existing). flaky = file re-run passes AND it passed at HEAD;
still failing → pre-existing (failed at HEAD) / regression (passed at HEAD); anything
else is ``unknown`` (exit 1). Limit: order-dependence across files packed into the
same shard is not reproduced by the file re-run.

Evidence (C2) adds ``head_sha``, ``tree_digest`` ({path: sha256} of every changed
input and selected test file, from the current tree also on a cache hit) and
``runner_errors``; failure entries add ``outcome`` and ``deadline``.

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
ALL_MODES = TEST_MODES + ("probe", "mutation")
TDD_MAX_FILES = 5
CAP_FILES = 40
SMOKE_CAP = 40
TDD_REPEAT_WARN = 8
REVERSE_DEPTH = 2  # verify selection: test → module → changed module; deeper fans out to ~all
WRAPPER_ALARM_S = 110
DEFAULT_DEADLINE = 95.0
SAFE_DEADLINE = WRAPPER_ALARM_S - 10.0
MAX_DEADLINE = 1800.0
MAX_SLOTS = 2
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
    git_set: set[str] = set()
    for args in (("diff", "--name-only", "HEAD"), ("ls-files", "--others", "--exclude-standard")):
        for line in (_git(repo, *args) or "").splitlines():
            if line.strip():
                git_set.add(line.strip())
    named: set[str] = set()
    owned: set[str] = set()
    for f in (run_state or {}).get("files_touched") or []:
        n = _norm_path(repo, f)
        if not n:
            continue
        named.add(n)
        if n in git_set or (Path(repo) / n).exists():
            owned.add(n)
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


def select_backend(repo: Path, changed: list[str], graph: dict[str, list[str]],
                   cap: int, recent: list[str] | tuple = (),
                   sgraph: dict[str, list[str]] | None = None,
                   depth: int = REVERSE_DEPTH) -> tuple[list[str], list[str], list[str]]:
    """Return ``(selected, skipped_for_budget, changed_module_names)``.

    Order: changed test files, then files that failed recently, then importers of
    changed modules sorted by import distance (direct importers first; with
    ``sgraph`` also transitive importers up to ``depth`` hops), ties broken by how
    many changed modules a test imports directly. Overflow past ``cap`` is returned
    as skipped, never dropped silently.
    """
    changed_tests = [f for f in changed if _is_backend_test(f) and (repo / f).is_file()]
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
    rec = [f for f in dict.fromkeys(recent) if f not in changed_tests and (repo / f).is_file()]
    skip = set(changed_tests) | set(rec)
    importers = [t for t in sorted(rank, key=lambda t: (rank[t], t)) if t not in skip]
    ordered = changed_tests + rec + importers
    return ordered[:cap], ordered[cap:], [m for m, _ in mods]


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
    d = cache / "plugin" / _sha(_PLUGIN_SRC.encode())[:12]
    p = d / f"{_PLUGIN_NAME}.py"
    if _read_text(p) != _PLUGIN_SRC:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{_PLUGIN_NAME}.{os.getpid()}.tmp"
        tmp.write_text(_PLUGIN_SRC, encoding="utf-8")
        os.replace(tmp, p)
    return d


def _worker_count() -> int:
    """min(4, cpu, memory: 1 GB per worker keeping 4 GB headroom); 2 without psutil."""
    cpu = os.cpu_count() or 2
    try:
        import psutil
        by_mem = int((psutil.virtual_memory().available - 4 * 1024 ** 3) // 1024 ** 3)
    except Exception:  # noqa: BLE001 — psutil missing or unreadable
        by_mem = 2
    return max(1, min(4, cpu, by_mem))


def _acquire_slot(cache: Path, warnings: list):
    """Global semaphore: at most MAX_SLOTS runner executions across sessions."""
    end = time.monotonic() + SLOT_WAIT_S
    while True:
        for n in range(MAX_SLOTS):
            fd = _try_lock(cache / "slots" / f"{n}.lock", 0)
            if fd is not None:
                return fd
        if time.monotonic() >= end:
            warnings.append(f"all {MAX_SLOTS} test slots busy for {SLOT_WAIT_S:.0f}s; running anyway")
            return None
        time.sleep(0.25)


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
    """Run jobs with ``workers`` parallelism; nothing survives past ``deadline_at``."""
    pending, running = list(jobs), []
    while pending or running:
        while pending and len(running) < workers and time.monotonic() < deadline_at:
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


def _child_env(backend: Path, extra_path: list[Path]) -> dict:
    env = dict(os.environ)
    paths = [str(backend)] + [str(p) for p in extra_path]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.pop("PYTEST_ADDOPTS", None)
    return env


def _pytest_job(ctx, name: str, args: list[str], backend: Path, isolated: bool) -> _Job:
    # ``--deselect=<nodeid>`` tokens (from _shards' AC split) are not targets and not
    # pytest's --deselect (that one matches by prefix): they go to the plugin as an
    # exact-match list. Parsing and gap detection read ``meta["args"]`` = targets only.
    # ``--keep=<nodeid>`` (from a split heavy file) likewise goes to the plugin.
    targets_only = [a for a in args if not a.startswith(("--deselect=", "--keep="))]
    deselect_ids = [a[len("--deselect="):] for a in args if a.startswith("--deselect=")]
    keep_ids = [a[len("--keep="):] for a in args if a.startswith("--keep=")]
    junit = ctx.workdir / f"{name}.xml"
    events = ctx.workdir / f"{name}.events"
    argv = [ctx.python, "-X", "faulthandler", "-m", "pytest", *targets_only, "-q",
            "-o", "addopts=", "-n", "0", "-p", "no:cacheprovider",
            f"--timeout={PER_TEST_TIMEOUT_S}", "--timeout-method=thread", "--junitxml", str(junit),
            "--rootdir", str(backend), "-p", _PLUGIN_NAME]
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
    return _Job(name, argv, backend, env, ctx.workdir, "pytest",
                args=targets_only, junit=junit, events=events, deselected=deselected)


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
        return _parse_pytest(job)
    if job.kind == "smoke":
        return _parse_smoke(job)
    return _parse_frontend(job)


def _parse_pytest(job: _Job) -> list[dict]:
    pre = "backend/"
    args = job.meta["args"]
    if not job.started:
        return [_res(pre + a, "timeout", ["not started: global deadline reached"], deadline=True,
                     notrun=True) for a in args]
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
    if not evs:
        return _parse_pytest_fallback(job)
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
        return any(r["file"] == f for r in results)
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
    # rc 5 with every target accounted for = module-level skips only: not an error.
    if job.started and not job.killed and job.rc not in (0, 1, 5) \
            and not any(r["outcome"] in _FAIL for r in out + gaps):
        gaps.append(_res(f"shard::{job.name}", "error", [f"pytest shard exited rc={job.rc} "
                                                         "without a failing test"] + tail,
                         file="backend", notrun=True, runner_error=True))
    return gaps


def _parse_pytest_fallback(job: _Job) -> list[dict]:
    """No event stream: junit xml if present, else one error/timeout per shard arg."""
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
                     notrun=True) for a in job.meta["args"]]
    tail = _lines(job.text())[-SUMMARY_LINES:]
    return [_res(pre + a, "error", [f"pytest exited rc={job.rc}"] + tail, notrun=True,
                 runner_error=True) for a in job.meta["args"]]


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


def _frontend_jobs(ctx, desktop_files: list[str], with_tsc: bool) -> list[_Job]:
    desktop = ctx.repo / "desktop"
    env = dict(os.environ)
    rel = [f[len("desktop/"):] for f in desktop_files]
    out = ctx.workdir / "vitest.json"
    jobs = [_Job("vitest", [*_npx(), "vitest", "related", *rel, "--run", "--reporter=json",
                            f"--outputFile={out}"], desktop, env, ctx.workdir, "vitest", json=out)]
    if with_tsc:
        jobs.append(_Job("tsc", [*_npx(), "tsc", "--noEmit", "-p", "."], desktop, env,
                         ctx.workdir, "tsc"))
    return jobs


def _parse_frontend(job: _Job) -> list[dict]:
    if not job.started or job.killed:
        return [_res(f"desktop::{job.kind}", "timeout", ["global deadline reached"]
                     + (_stack(job) if job.started else []), file="desktop", layer=job.kind,
                     deadline=True)]
    if job.kind == "tsc":
        errs = [ln for ln in _lines(job.text()) if "error TS" in ln]
        if job.rc == 0:
            return [_res("desktop::tsc", "passed", file="desktop", layer="tsc")]
        return [_res("desktop::tsc", "failed", (errs or _lines(job.text()))[:SUMMARY_LINES],
                     file="desktop", layer="tsc")]
    data = _read_json(job.meta["json"])
    if not isinstance(data, dict):
        return [_res("desktop::vitest", "error", [f"vitest produced no JSON (rc={job.rc})"]
                     + _lines(job.text())[-SUMMARY_LINES:], file="desktop", layer="vitest")]
    out = []
    desktop = str(Path(job.cwd).resolve())
    for tr in data.get("testResults") or []:
        name = str(tr.get("name") or "")
        rel = os.path.relpath(name, desktop) if os.path.isabs(name) else name
        f = "desktop/" + Path(rel).as_posix()
        for a in tr.get("assertionResults") or []:
            st = a.get("status")
            oc = "passed" if st == "passed" else "failed" if st == "failed" else "skipped"
            msg = _lines("\n".join(a.get("failureMessages") or []))[:SUMMARY_LINES]
            out.append(_res(f"{f}::{a.get('fullName') or a.get('title')}", oc, msg, file=f,
                            layer="vitest"))
        if not tr.get("assertionResults") and tr.get("status") == "failed":
            out.append(_res(f, "error", _lines(str(tr.get("message") or ""))[:SUMMARY_LINES],
                            file=f, layer="vitest"))
    return out


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
            ran = [r for r in results if r["file"] == nodeid and not r.get("notrun")]
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
        return "passed" if any(r["file"] == nodeid and r.get("call") for r in results) else None
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
    """nodeid → new | flaky | pre-existing | regression | unknown, within the deadline.

    new: absent at HEAD. Every other backend failure: its whole FILE is re-run in the
    current tree in original order (H4: an order-dependent failure reproduces), and
    the nodeid's HEAD outcome is looked up in a reusable worktree (cached per sha in
    ``<cache>/baseline-<sha>-v<N>.json``; only tests that reached a call report).

    - re-run still fails  → pre-existing (failed at HEAD) / regression (passed at HEAD)
    - re-run passes       → flaky (passed at HEAD) / pre-existing (failed at HEAD)
    - HEAD unknown, test not run in the re-run, runner errors, no time → unknown
    """
    cls: dict[str, str] = {}
    todo = []
    for r in results:
        if r["outcome"] not in _FAIL:
            continue
        n = r["nodeid"]
        if r.get("runner_error"):
            cls[n] = "unknown"  # zero collected / abnormal shard exit: never green, never cached
        elif n.startswith("import::"):
            cls[n] = "new" if r.get("file") and _is_new(ctx, r["file"]) else "unknown"
        elif not n.startswith("backend/"):
            cls[n] = "unknown"  # frontend: no HEAD baseline
        elif _is_new(ctx, n):
            cls[n] = "new"
        else:
            todo.append(n)
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
    if passed_now or still:
        head = _baseline(ctx, passed_now + still)
        for n in still:
            cls[n] = {"failed": "pre-existing", "passed": "regression"}.get(head[n], "unknown")
        for n in passed_now:
            cls[n] = {"failed": "pre-existing", "passed": "flaky"}.get(head[n], "unknown")
    return cls


def _baseline(ctx, nodeids: list[str]) -> dict[str, str | None]:
    """nodeid → HEAD outcome ``passed`` | ``failed`` | None (unknown, never stored)."""
    sha = _head_sha(ctx.repo)
    if not sha:
        return {n: None for n in nodeids}
    bpath = _baseline_path(ctx.cache, sha)
    base = _read_json(bpath)
    base = base if isinstance(base, dict) else {}
    need = [n for n in nodeids if base.get(n) not in ("passed", "failed")]
    if need and ctx.left() >= 3:
        wt = _ensure_worktree(ctx, sha, ctx.left())
        if wt is not None and ctx.left() >= 2:
            res = run_pytest_targets(ctx, _targets_from_nodeids(need), ctx.deadline_at,
                                     "base", root=wt)
            fresh = {n: o for n in need if (o := _head_outcome(n, res)) is not None}
            if fresh:
                fd = _try_lock(ctx.cache / f".baseline-{sha}.lock", 5)
                try:
                    cur = _read_json(bpath)
                    cur = cur if isinstance(cur, dict) else {}
                    cur.update(fresh)
                    _write_json(bpath, cur)
                finally:
                    _unlock(fd)
                base.update(fresh)
    return {n: base.get(n) if base.get(n) in ("passed", "failed") else None for n in nodeids}


# ── regression L1: tests declared by the BUILD artifact's ac_coverage ───────
def _ac_entries(project: str, run_state: dict) -> list[list[str]]:
    """ac_coverage[].test refs per entry: ``file.py::test_fn`` / ``file.py`` (pytest),
    a desktop vitest file (``desktop/src/...test.tsx``, file-level), or
    ``probe:<name>`` (verified from the run's probe evidence)."""
    stages = [s for s in run_state.get("stages") or [] if isinstance(s, dict)]
    aid = next((s.get("artifact_id") for s in reversed(stages)
                if s.get("stage") == "build" and isinstance(s.get("artifact_id"), str)), None)
    if not aid:
        return []
    arts = (_workspace() / "Projects" / project / ".artifacts").resolve()
    man = _read_json(arts / "manifest.json")
    items = man.get("artifacts") if isinstance(man, dict) else None
    entry = next((e for e in items or [] if isinstance(e, dict) and e.get("id") == aid), None)
    if not entry or not isinstance(entry.get("file"), str):
        return []
    path = (arts / entry["file"]).resolve()
    if arts not in path.parents:
        return []  # manifest entry escaping .artifacts — ignore
    data = _read_json(path)
    if isinstance(data, dict) and "ac_coverage" not in data and isinstance(data.get("data"), dict):
        data = data["data"]
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
    if nodeid is None:
        return r["file"] == file
    n = r["nodeid"]
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
            if not hits or any(r["outcome"] in _FAIL for r in hits) \
                    or not any(r["outcome"] == "passed" for r in hits):
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
        self.durations = _load_durations(cache)  # main-run shard packing only
        self.head_src: dict[str, str | None] = {}
        self.seq = 0

    def left(self) -> float:
        return self.deadline_at - time.monotonic()


def _write_evidence(ev_root: Path, mode: str, ev: dict) -> Path:
    tdir = ev_root / "tests"
    stamp = _stamp()
    path, n = tdir / f"{mode}-{stamp}.json", 1
    while path.exists():  # two calls in one second keep both records
        n += 1
        path = tdir / f"{mode}-{stamp}-{n}.json"
    _write_json(path, ev)
    _write_json(tdir / f"latest-{mode}.json", ev)
    return path


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


def _frontend_key(ctx, mode: str, files: list[str]) -> str:
    h = hashlib.sha256((RUNNER_VERSION + mode + _desktop_state(ctx.repo)).encode())
    for f in sorted(files) + ["desktop/package-lock.json"]:
        h.update(f.encode() + b"\0" + _file_digest(ctx.repo / f).encode() + b"\0")
    return h.hexdigest()[:32]


# ── test-mode planning ──────────────────────────────────────────────────────
def _input_path(repo: Path, p: str) -> str | None:
    """--files entry (abs, repo-relative, cwd-relative or backend-relative) → repo-relative."""
    path, sep, rest = p.partition("::")
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


def _plan(ctx, ns, ev_root: Path, run_state: dict, graph: dict,
          sgraph: dict | None = None) -> tuple[dict, str | None]:
    plan = {"targets": {}, "skipped": [], "smoke": {}, "ac_entries": [], "l2": [],
            "desktop": [], "explicit": [], "rechecked": 0}
    changed = changed_files(ctx.repo, run_state, ctx.warnings, (ns.project, ns.run_id))
    plan["changed"] = changed
    mode = ns.mode
    if mode == "tdd":
        return plan, _plan_tdd(ctx, ns, plan)
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
            if n.startswith("backend/") and (ctx.repo / n.split("::")[0]).is_file():
                _add_target(plan["targets"], n.split("::")[0], n if "::" in n else None)
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
        if not (plan["targets"] or plan["smoke"] or plan["desktop"] or plan.get("probe_rows")):
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
    results; ``rechecked_from`` records which run was overlaid."""
    new = {r["nodeid"]: r for r in re_ev.get("results") or [] if isinstance(r, dict)}
    old = {r["nodeid"]: r for r in base.get("results") or [] if isinstance(r, dict)}
    results = [new.get(n, r) for n, r in old.items()] + [r for n, r in new.items() if n not in old]
    counts = {"passed": 0, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for r in results:
        counts[_COUNT_KEY.get(r.get("outcome"), "errors")] += 1
    failures = [f for f in base.get("failures") or [] if f.get("nodeid") not in new] \
        + list(re_ev.get("failures") or [])
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
    ev.update(results=results, counts=counts, failures=failures, runner_errors=runner_errors,
              layers=layers, finished_at=_now_iso(), cached=False,
              rechecked_from={"finished_at": base.get("finished_at"),
                              "rechecked": len(new), "auto": auto})
    return ev


def _run_test_mode(ctx, ns, ev_root: Path, run_state: dict, deadline: float) -> tuple[dict, int]:
    started_at = _now_iso()
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
    head = _head_sha(ctx.repo)
    changed_in = list(dict.fromkeys(plan["changed"] + plan["desktop"]))
    inputs = _key_inputs(ctx, targets, plan["smoke"], changed_in, sgraph)
    key = _cache_key(ctx, ns.mode, targets, plan["smoke"],
                     plan["explicit"] + [json.dumps(plan["ac_entries"]), json.dumps(plan.get("probe_rows") or []),
                                       json.dumps(plan["skipped"])],
                     inputs, head)
    tree = _tree_digest(inputs, changed_in, list(targets) + list(plan["smoke"].values()))
    if ns.mode in ("verify", "regression") and not getattr(ns, "auto_recheck_of", None):
        prev = _read_json(ev_root / "tests" / f"latest-{ns.mode}.json")
        if isinstance(prev, dict) and prev.get("cache_key") == key and not _cacheable(prev):
            auto = _auto_recheck(ctx, ns, ev_root, run_state, deadline, prev)
            if auto is not None:
                return auto
    fmode = ns.mode + ("+tsc" if plan.get("desktop_tsc") else "")
    fkey = _frontend_key(ctx, fmode, plan["desktop"]) if plan["desktop"] else ""
    cpath = ctx.cache / "results" / f"{key}{fkey[:16]}.json"
    hit = _read_json(cpath)
    if isinstance(hit, dict) and hit.get("schema") == 1 and _cacheable(hit):
        ev = dict(hit)
        ev.update(cached=True, duration_s=0, run_id=ns.run_id, started_at=started_at,
                  finished_at=_now_iso(), tdd_repeats=repeats,
                  warnings=ctx.warnings + ["cached: tests and their inputs are unchanged since the last run"],
                  changed_files=_digest_map(ctx.repo, plan["changed"]),
                  head_sha=head, tree_digest=tree)
        return _test_output(ev, _write_evidence(ev_root, ns.mode, ev)), _exit_code(ev)
    slot = _acquire_slot(ctx.cache, ctx.warnings)
    try:
        jobs: list[_Job] = []
        fpath = ctx.cache / "results" / f"frontend-{fkey}.json"
        fhit = _read_json(fpath) if fkey else None
        if fkey and not isinstance(fhit, list):
            jobs += _frontend_jobs(ctx, plan["desktop"],
                                   ns.mode in ("verify", "regression") or bool(plan.get("desktop_tsc")))
        if plan["smoke"]:
            sj = _smoke_job(ctx, list(plan["smoke"]))
            sj.meta["files"] = plan["smoke"]
            jobs.append(sj)
        jobs += _pytest_jobs(ctx, targets, "main", first=plan.get("ac_first"),
                             durations=ctx.durations) if targets else []
        main_end = ctx.deadline_at - min(20.0, deadline * 0.25)
        _run_jobs(jobs, ctx.workers, main_end)
        results = _results_with_requeue(ctx, jobs, main_end, "main")
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
        _unlock(slot)
    ev = _assemble(ctx, ns, plan, results, cls, started_at, repeats, key)
    ev.update(head_sha=head, tree_digest=tree)
    if _cacheable(ev):
        _write_json(cpath, ev)
    if ev["failures"]:
        ev["logs"] = str(_copy_logs(ctx, ev_root, ns.mode))
    path = _write_evidence(ev_root, ns.mode, ev)
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
            _write_evidence(ev_root, base_mode, merged)
            ev["overlaid_onto"] = base_mode
    return _test_output(ev, path), _exit_code(ev)


def _assemble(ctx, ns, plan, results, cls, started_at, repeats, key) -> dict:
    results = list(results) + list(plan.get("probe_rows") or [])
    counts = {"passed": 0, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for r in results:
        counts[_COUNT_KEY[r["outcome"]]] += 1
    # a failing probe verdict is definite ("new"); the cache key already holds the verdict
    cls = {**cls, **{r["nodeid"]: "new" for r in plan.get("probe_rows") or []}}
    failures = [{"nodeid": r["nodeid"], "classification": cls.get(r["nodeid"], "unknown"),
                 "summary": "\n".join(r["summary"][:STACK_LINES + 1]),
                 "outcome": r["outcome"], "deadline": bool(r.get("deadline"))}
                for r in results if r["outcome"] in _FAIL]
    l2 = set(plan["l2"])
    dep = [r for r in results if r["file"] in l2 and not r.get("layer")]
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
          "selected_files": sorted(set(plan["targets"]) | set(plan["desktop"])),
          "skipped_for_budget": plan["skipped"], "counts": counts,
          "results": [{"nodeid": r["nodeid"], "outcome": r["outcome"], "file": r["file"],
                       **({"requeued": True} if r.get("requeued") else {})}
                      for r in results],
          "failures": failures, "layers": layers, "tdd_repeats": repeats,
          "warnings": list(ctx.warnings), "cache_key": key,
          "changed_files": _digest_map(ctx.repo, plan["changed"]),
          "runner_errors": [r["nodeid"] for r in results if r.get("runner_error")]}
    if any(r.get("layer") in ("vitest", "tsc") for r in results):
        ev["frontend"] = _frontend_summary(results)
    if ns.mode == "recheck":
        ev["rechecked_failures"] = plan["rechecked"]
    return ev


def _copy_logs(ctx, ev_root: Path, mode: str) -> Path:
    dst = ev_root / "tests" / f"logs-{mode}-{_stamp()}"
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(ctx.workdir.iterdir()):
        if p.suffix in (".out", ".err") and p.is_file():
            shutil.copy2(p, dst / p.name)
    return dst


def _exit_code(ev: dict) -> int:
    """0 only when every failure is pre-existing / flaky AND none of them is a timeout
    (a hang is never green, whatever its classification) and no runner error occurred."""
    if ev.get("runner_errors"):
        return 1
    bad = [f for f in ev["failures"] if f["classification"] not in ("pre-existing", "flaky")
           or f.get("outcome") == "timeout"]
    return 1 if bad else 0


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
    out = {"mode": ev["mode"], "cached": ev["cached"], "ok": _exit_code(ev) == 0,
           "counts": ev["counts"], "duration_s": ev["duration_s"],
           "selected": len(ev["selected_files"]),
           "failures": [{"nodeid": f["nodeid"], "classification": f["classification"],
                         "summary": _short(f["summary"], SUMMARY_LINES)} for f in ev["failures"][:10]],
           "failures_total": len(ev["failures"]),
           "skipped_for_budget": ev["skipped_for_budget"][:10],
           "skipped_for_budget_total": len(ev["skipped_for_budget"]),
           "evidence": str(path), "warnings": ev.get("warnings") or []}
    if ev["mode"] in ("regression", "verify", "recheck"):
        out["layers"] = ev["layers"]
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
    p.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    p.add_argument("--json", action="store_true", help="accepted for clarity; output is always JSON")
    return p


def _clamp_deadline(raw) -> tuple[float | None, list[str]]:
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
    ctx = _Ctx(_repo_root(), cache, deadline)
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
