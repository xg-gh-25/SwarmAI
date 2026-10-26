"""SwarmAI's own test selector: which backend test files a change touches.

A SwarmAI repo tool, not part of the product pipeline (design usable-by-anyone
§3.3.3: the pipeline does not choose or run tests). It was the selection half of the
retired ``pipeline_test.py`` runner. The agent calls it natively and runs what it
prints with the repo's own pytest command.

Selection: changed test files, then every test that imports a changed backend module
(directly, or within ``--depth`` import hops), tests that name a changed
``backend/scripts/*.py`` by file name, and the doc-lint / context / source-scan suites
when files of those kinds changed. Nothing is capped. It also lists the entries of
the repo's ``validations.json`` (lint, typecheck, build checks) whose path patterns
match a changed file, with the command each one runs.

Usage (from the repo root or backend/):
    backend/.venv/bin/python backend/scripts/swarm_select_tests.py [--base main] [--depth 1]
        [--files F ...] [--json]

Public symbols:
- ``changed_files``  -- paths changed since the merge-base with ``--base`` + untracked
- ``select_backend`` -- ``(selected, [], changed_module_names)``
- ``transitive_unselected`` -- tests reaching a change only through other modules
- ``validations_for`` -- the ``validations.json`` entries a change needs
- ``touches_app_index`` -- whether a change can leave the generated App index stale
- ``main``           -- the CLI
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import warnings as _warnings
from pathlib import Path, PurePosixPath

RUNNER_VERSION = "select-1"
REVERSE_DEPTH = 1


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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
    "backend/tests/test_skill_python.py",          # skill scripts: syntax and imports
    "backend/tests/test_skill_scripts_import.py",
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
# tests/test_swarm_select_tests.py pins this list against every test that globs *.py.
_SOURCE_SCAN_SUITES = (
    # skill scripts import backend modules by name: a module rename can break them
    "backend/tests/test_skill_scripts_import.py",
    "backend/tests/test_app_config_manager.py",
    "backend/tests/test_bash_misuse_report.py",
    "backend/tests/test_cjk_encoding.py",
    "backend/tests/test_ddd_canonical_docs_single_source.py",
    "backend/tests/test_ddd_paths_single_source.py",
    "backend/tests/test_gate_decision_log.py",
    "backend/tests/test_guard_contracts.py",
    "backend/tests/test_handler_log_safety.py",
    "backend/tests/test_job_model_ssot.py",
    "backend/tests/test_nav_routers_import.py",
    "backend/tests/test_output_degeneration.py",
    "backend/tests/test_pipeline_command.py",
    "backend/tests/test_pipeline_v1_cards.py",
    "backend/tests/test_pipeline_v1_cutover.py",
    "backend/tests/test_pipeline_v1_profile_table.py",
    "backend/tests/test_r6a_ceiling_decouple.py",
    "backend/tests/test_router_async_blocking.py",
    "backend/tests/test_silent_except_baseline.py",
    "backend/tests/test_swarmws_resolver.py",
    "backend/tests/test_transcript_event_schema.py",
    "backend/tests/test_turn_writer.py",
)
# Glob *.py but not over backend source (a tmp_path or a generated tree).
# globs *.py of the skills, not of backend modules (selected with _DOC_LINT_SUITES);
# the acceptance matrix globs its own tmp_path repo.
_NOT_SOURCE_SCANS = ("backend/tests/test_skill_python.py",
                     "backend/tests/test_pipeline_v1_acceptance_matrix.py")
# The generated App index hashes every file an App declares (backend/apps/*/app.json
# "source"), frontend files included: a change to one leaves the index stale and the
# packaged build fails on it (af8274c5e edited PollinateOverlay.tsx, 10-27). No import
# edge reaches the freshness test, so select it by path.
_APP_INDEX_SUITES = ("backend/tests/test_app_registry.py",)


def _app_sources(repo: Path) -> list[str]:
    """Every ``source`` path declared by a ``backend/apps/*/app.json``."""
    out: list[str] = []
    for manifest in sorted((repo / "backend" / "apps").glob("*/app.json")):
        data = _read_json(manifest)
        for res in (data or {}).get("resources", []) if isinstance(data, dict) else []:
            src = res.get("source") if isinstance(res, dict) else None
            if isinstance(src, str) and src:
                out.append(src.rstrip("/"))
    return out


def touches_app_index(repo: Path, changed: list[str]) -> bool:
    """True when a changed path is an App manifest, a generated App file, or a
    file an App declares as a source (the file itself or under a declared dir)."""
    if any(f.startswith(("backend/apps/", "backend/generated/", "desktop/src/generated/"))
           for f in changed):
        return True
    sources = _app_sources(repo)
    return any(f == s or f.startswith(s + "/") for f in changed for s in sources)


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
    if touches_app_index(repo, changed):
        changed_tests += [t for t in _APP_INDEX_SUITES
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


def _path_match(path: str, pattern: str) -> bool:
    item = PurePosixPath(path)
    # pathlib reads ``a/**/*.py`` as one or more directories; the manifest means zero
    # or more, so also try the zero-directory spelling
    return item.match(pattern) or ("/**/" in pattern
                                   and item.match(pattern.replace("/**/", "/")))


def validations_for(repo: Path, changed: list[str]) -> list[dict]:
    """Entries of ``<repo>/validations.json`` whose ``paths`` match a changed file."""
    data = _read_json(repo / "validations.json")
    entries = data.get("entries") if isinstance(data, dict) else None
    return [e for e in entries or [] if isinstance(e, dict)
            and any(_path_match(f, pat) for f in changed for pat in e.get("paths") or [])]


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def changed_files(repo: Path, base: str) -> list[str]:
    """Files changed since the merge-base of HEAD and *base*, plus uncommitted and
    untracked ones (deleted files included: their importers are selected)."""
    fork = _git(repo, "merge-base", "HEAD", base).strip()
    out = set(_git(repo, "diff", "--name-only", "--no-renames", fork).split())
    out |= set(_git(repo, "ls-files", "--others", "--exclude-standard").split())
    return sorted(out)


def _repo_root(start: Path) -> Path:
    return Path(_git(start, "rev-parse", "--show-toplevel").strip())


def _cache_dir(repo: Path) -> Path:
    return repo / "backend" / ".pytest_cache" / "swarm_select_tests"


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--base", default="main", help="compare with this ref (default main)")
    ap.add_argument("--files", nargs="*", help="use these changed paths instead of git")
    ap.add_argument("--depth", type=int, default=REVERSE_DEPTH,
                    help="import hops for transitive importers (default 1)")
    ap.add_argument("--json", action="store_true", help="print one JSON object")
    args = ap.parse_args(argv)
    repo = _repo_root(Path.cwd())
    changed = sorted(args.files) if args.files is not None else changed_files(repo, args.base)
    cache = _cache_dir(repo)
    sgraph = build_source_graph(repo, cache)
    graph = build_import_graph(repo, cache, sgraph)
    selected, _, mods = select_backend(repo, changed, graph, sgraph=sgraph, depth=args.depth)
    beyond = transitive_unselected(repo, sgraph, graph, changed, selected)
    checks = [{"id": e.get("id"), "cwd": e.get("cwd", "."), "argv": e.get("argv")}
              for e in validations_for(repo, changed)]
    if args.json:
        print(json.dumps({"changed": changed, "modules": mods, "selected": selected,
                          "transitive_not_selected": beyond, "validations": checks}))
    else:
        for t in selected:
            print(t)
        for c in checks:
            print(f"# validation {c['id']} (cwd {c['cwd']}): {' '.join(map(str, c['argv'] or []))}",
                  file=sys.stderr)
        if beyond:
            print(f"# {len(beyond)} more tests reach the change only through other "
                  f"modules (--depth {args.depth + 1} adds the next hop)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
