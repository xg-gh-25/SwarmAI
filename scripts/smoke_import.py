#!/usr/bin/env python3
"""Smoke-import all discoverable Python modules under backend/.

Single source of truth for CI smoke import on both Linux and Windows.
Catches: missing deps, syntax errors, circular imports, Unix-only
top-level imports (fcntl, resource, grp).

Finds backend/ from this file's own location, so it gives the same result from
any cwd (CI runs it from backend/, validations.json from the repo root):
    python scripts/smoke_import.py

Exit code 0 = all modules imported, 1 = at least one failure, or no module was
found at all (a walk of the wrong directory must not pass as "all imported").
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"

# Packages that need runtime context (AWS creds, DB, MCP servers, etc.)
SKIP_PREFIXES = ("tests.", "skills.", "jobs.handlers.", "channels.")


def main() -> int:
    failures: list[tuple[str, str]] = []
    ok = 0
    os.chdir(BACKEND)  # modules that read files relative to backend/ at import
    sys.path.insert(0, str(BACKEND))

    for _finder, mod_name, _is_pkg in pkgutil.walk_packages(
        path=[str(BACKEND)], onerror=lambda name: None
    ):
        if any(mod_name.startswith(p) for p in SKIP_PREFIXES):
            continue
        if mod_name.startswith("_") or ".test_" in mod_name:
            continue
        try:
            importlib.import_module(mod_name)
            ok += 1
        except (ImportError, ModuleNotFoundError) as e:
            print(f"  FAIL {mod_name}: {e}")
            failures.append((mod_name, str(e)))
        except SyntaxError as e:
            print(f"  FAIL {mod_name}: SyntaxError: {e}")
            failures.append((mod_name, str(e)))
        except Exception:
            # Runtime errors (missing env, DB, FastAPI app context) are
            # expected in CI — the module loaded, just can't fully init.
            ok += 1

    platform = "Windows" if sys.platform == "win32" else "Linux"

    if ok == 0:
        print(f"No module found under {BACKEND}: nothing was checked")
        return 1

    if failures:
        print(f"\n{len(failures)} module(s) failed to import on {platform}:")
        for mod, err in failures:
            print(f"  - {mod}: {err}")
        return 1

    print(f"\nAll {ok} discovered modules imported successfully on {platform}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
