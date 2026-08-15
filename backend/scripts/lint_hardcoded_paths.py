#!/usr/bin/env python3
"""Lint: detect hardcoded Path.home() / ".swarm-ai" that should use centralized paths.

Run as pre-commit or CI check:
    python backend/scripts/lint_hardcoded_paths.py [--fix]

Exit code 0 = clean, 1 = violations found.

Allowlist: files that intentionally contain the pattern (source of truth,
standalone scripts without module access, migration code).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Pattern to detect: Path.home() / ".swarm-ai" (with any quoting style)
_PATTERN = re.compile(r"""Path\.home\(\)\s*/\s*['"]\.swarm-ai['"]""")

# Files allowed to contain the pattern (with reason)
_ALLOWLIST = {
    "backend/config.py",                    # Single source of truth
    "backend/scripts/uninstall_cleanup.py", # Standalone uninstall tool
    "backend/scripts/verify_build.py",      # Standalone build verifier
    "backend/jobs/paths.py",                # Migration code references old path
    # Skill scripts use env var + fallback (structurally correct)
    "backend/skills/s_loops-health/scripts/loops_health_check.py",
    "backend/skills/s_radar-todo/scripts/todo_db.py",
    "backend/skills/s_notify/notify.py",
    "backend/skills/s_library/scripts/library.py",
    "backend/scripts/lint_hardcoded_paths.py",  # Self-reference in docstring/pattern
}


def main() -> int:
    # Find project root (backend/../)
    script_dir = Path(__file__).resolve().parent
    backend_dir = script_dir.parent
    project_root = backend_dir.parent

    violations: list[tuple[str, int, str]] = []

    for py_file in backend_dir.rglob("*.py"):
        rel = str(py_file.relative_to(project_root))
        if rel in _ALLOWLIST:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if _PATTERN.search(line):
                violations.append((rel, i, line.strip()))

    if not violations:
        print("✅ No hardcoded Path.home()/.swarm-ai found outside allowlist")
        return 0

    print(f"❌ {len(violations)} hardcoded path(s) found — use config.get_app_data_dir() or jobs.paths instead:\n")
    for path, lineno, line in violations:
        print(f"  {path}:{lineno}")
        print(f"    {line}\n")
    print("Fix: import from config.get_app_data_dir() or jobs.paths (APP_DATA_DIR, SWARMWS, DB_PATH, STATE_DIR, etc.)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
