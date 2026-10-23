"""Every nav router that calls executors.* imports it (the S4 regression class:
a module-level name used but never imported only fails at request time).

What is tested: a static check over routers/*.py: a module that references
``executors.`` binds the name ``executors`` at module level or in the function.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROUTERS = Path(__file__).resolve().parents[1] / "routers"


def _binds(tree: ast.AST, name: str) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            if any((a.asname or a.name.split(".")[0]) == name for a in n.names):
                return True
    return False


def test_every_router_using_executors_imports_it():
    bad = []
    for f in sorted(ROUTERS.glob("*.py")):
        tree = ast.parse(f.read_text())
        uses = any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                   and n.value.id == "executors" for n in ast.walk(tree))
        if not uses:
            continue
        module_level = any(isinstance(n, (ast.Import, ast.ImportFrom)) and
                           any((a.asname or a.name.split(".")[0]) == "executors" for a in n.names)
                           for n in tree.body)
        if module_level:
            continue
        # every function that uses it must import it itself
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                used = any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                           and n.value.id == "executors" for n in ast.walk(fn))
                if used and not _binds(fn, "executors"):
                    bad.append(f"{f.name}:{fn.name}")
    assert bad == []
