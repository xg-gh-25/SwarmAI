"""Left Nav endpoints run on their own pool and their git never takes a lock
(design left-nav-backend-and-visual-system.md B4/B5, T5/T6).

What is tested:
- the routers that serve only nav overlays contain no ``asyncio.to_thread`` and no
  default-pool ``run_in_executor(None, ...)``: their blocking work goes to the
  ``nav`` pool, so a slow overlay can never queue ahead of chat or /health;
- in workspace_api (shared with Canvas file reads) only the tree endpoints are nav
  work: ``get_workspace_tree`` / ``expand_tree_directory`` do not use to_thread;
- every git subprocess in ddd_brain.py and workspace_api.py passes an env with
  ``GIT_OPTIONAL_LOCKS=0`` (a read-only git status/diff must not refresh the
  index, which takes .git/index.lock and collides with chat's per-turn snapshot);
- ``executors.run_nav`` runs a call with kwargs on a ``nav`` pool thread.
"""
from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path

ROUTERS = Path(__file__).resolve().parents[1] / "routers"

NAV_ONLY = ("attention", "community_api", "jobs", "library_api", "eval", "ddd_brain",
            "todos", "pipelines", "pollinate", "hive")
NAV_FUNCS_IN_SHARED = {"workspace_api": ("get_workspace_tree", "expand_tree_directory",
                                         "list_workspace_locations", "get_workspace_summary")}


def _default_pool_calls(node: ast.AST) -> list[int]:
    bad = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr == "to_thread":
            bad.append(n.lineno)
        if isinstance(f, ast.Attribute) and f.attr == "run_in_executor" and n.args \
                and isinstance(n.args[0], ast.Constant) and n.args[0].value is None:
            bad.append(n.lineno)
    return bad


def test_nav_routers_never_use_the_default_pool():
    found = {}
    for mod in NAV_ONLY:
        lines = _default_pool_calls(ast.parse((ROUTERS / f"{mod}.py").read_text()))
        if lines:
            found[mod] = lines
    for mod, funcs in NAV_FUNCS_IN_SHARED.items():
        tree = ast.parse((ROUTERS / f"{mod}.py").read_text())
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in funcs:
                lines = _default_pool_calls(fn)
                if lines:
                    found[f"{mod}.{fn.name}"] = lines
    assert found == {}


def _git_calls_without_optional_locks(src: str) -> list[int]:
    bad = []
    for n in ast.walk(ast.parse(src)):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("run", "Popen", "check_output")
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"):
            continue
        first = n.args[0] if n.args else None
        # a literal argv that is not git is out of scope; anything else (a literal
        # git argv, or a variable / expression that may build one) must carry the env
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts \
                and isinstance(first.elts[0], ast.Constant) and first.elts[0].value != "git":
            continue
        env = next((k.value for k in n.keywords if k.arg == "env"), None)
        text = ast.unparse(env) if env is not None else ""
        if "GIT_OPTIONAL_LOCKS" not in text and text != "_GIT_READ_ENV()":
            bad.append(n.lineno)
    return bad


def test_nav_git_never_takes_the_index_lock():
    found = {m: _git_calls_without_optional_locks((ROUTERS / f"{m}.py").read_text())
             for m in ("ddd_brain", "workspace_api")}
    found["core/workspace_locations"] = _git_calls_without_optional_locks(
        (ROUTERS.parent / "core" / "workspace_locations.py").read_text())
    assert {m: v for m, v in found.items() if v} == {}


def test_git_read_env_disables_optional_locks():
    from routers import ddd_brain, workspace_api
    for m in (ddd_brain, workspace_api):
        assert m._GIT_READ_ENV()["GIT_OPTIONAL_LOCKS"] == "0"


def test_run_nav_runs_on_the_nav_pool_with_kwargs():
    from core import executors

    def f(a, *, b):
        return a + b, threading.current_thread().name

    total, name = asyncio.run(executors.run_nav(f, 1, b=2))
    assert total == 3 and name.startswith("swarm-nav")
