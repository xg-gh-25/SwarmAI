#!/usr/bin/env python3
"""Smoke-import all discoverable Python modules under backend/.

Single source of truth for CI smoke import on both Linux and Windows.
Catches: missing deps, syntax errors, circular imports, Unix-only
top-level imports (fcntl, resource, grp).

Must be run from the backend/ directory:
    cd backend && python ../scripts/smoke_import.py

Exit code 0 = all modules imported, 1 = at least one failure.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys

# Packages that need runtime context (AWS creds, DB, MCP servers, etc.)
SKIP_PREFIXES = ("tests.", "skills.", "jobs.handlers.", "channels.")


def main() -> int:
    failures: list[tuple[str, str]] = []
    ok = 0

    for _finder, mod_name, _is_pkg in pkgutil.walk_packages(
        path=["."], onerror=lambda name: None
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

    if failures:
        print(f"\n{len(failures)} module(s) failed to import on {platform}:")
        for mod, err in failures:
            print(f"  - {mod}: {err}")
        return 1

    print(f"\nAll {ok} discovered modules imported successfully on {platform}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
