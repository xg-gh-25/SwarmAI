"""Deterministic v13 risk-based test-scope expansion.

Public symbols:
- ``expand`` — map changed paths/source patterns to additional test files.
- ``RISK_RULES_VERSION`` — cache/final provenance identity for the frozen table.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Sequence

RISK_RULES_VERSION = "v13-2026-10-21"

_RULES = (
    ("pipeline-protocol", (
        "backend/core/run_protocol/**", "backend/scripts/artifact_cli.py",
        "backend/scripts/pipeline_*.py", "backend/core/pipeline_*.py"),
     ("backend/tests/test_run_protocol_*.py", "backend/tests/test_pipeline_*.py",
      "backend/tests/test_test_selection_frozen.py")),
    ("security-gates", (
        "backend/core/security_hooks.py", "backend/core/*_gate.py",
        "backend/core/hook_builder.py", "backend/core/hook_token.py",
        "backend/core/permission_manager.py"),
     ("backend/tests/test_*gate*.py", "backend/tests/test_security_hooks.py",
      "backend/tests/test_hook_builder.py", "backend/tests/test_hook_token.py",
      "backend/tests/test_worktree_gates.py")),
    ("test-selector", ("backend/scripts/pipeline_test.py",),
     ("backend/tests/test_test_selection_frozen.py", "backend/tests/test_pipeline_test*.py")),
    ("persistence", ("backend/database/**", "backend/**/*schema*.py", "backend/**/migrations/**"),
     ("backend/tests/test_database*.py", "backend/tests/test_*migration*.py",
      "backend/tests/test_*schema*.py")),
    ("public-contract", ("backend/routers/**", "backend/schemas/**",
                         "backend/core/streaming_orchestrator.py"),
     ("backend/tests/test_*router*.py", "backend/tests/test_*contract*.py",
      "desktop/src/**/*.test.ts", "desktop/src/**/*.test.tsx")),
)
_SHARED_CONFIG = ("backend/conftest.py", "backend/pyproject.toml", "backend/pytest.ini",
                  "desktop/vitest.config.ts", "desktop/package.json")
_DYNAMIC_RE = re.compile(r"\b(?:import\s+importlib\b|from\s+importlib\b|importlib\.|__import__\s*\()")


def _match(path: str, pattern: str) -> bool:
    item = PurePosixPath(path)
    return item.match(pattern) or ("/**/" in pattern and item.match(pattern.replace("/**/", "/")))


def _files(repo: Path, patterns: Sequence[str]) -> set[str]:
    out: set[str] = set()
    for pattern in patterns:
        for path in repo.glob(pattern):
            if path.is_file():
                out.add(path.relative_to(repo).as_posix())
    return out


def expand(repo: str | Path, changed: Sequence[str], *, cap: int) -> tuple[list[str], list[str], list[str]]:
    """Return ``(selected, hit_rules, overflow)``; overflow is never silently run partly."""
    root = Path(repo)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise ValueError("risk test file cap must be a positive integer")
    changed_paths = tuple(dict.fromkeys(changed))
    if any(not isinstance(path, str) or path.startswith("/") or path.startswith("../")
           for path in changed_paths):
        raise ValueError("changed path leaves repository")
    selected: set[str] = set()
    hits: list[str] = []
    for rule, path_patterns, test_patterns in _RULES:
        if any(_match(path, pattern) for path in changed_paths for pattern in path_patterns):
            hits.append(rule)
            selected |= _files(root, test_patterns)
    shared = [path for path in changed_paths if path in _SHARED_CONFIG]
    if shared:
        hits.append("shared-config")
        for path in shared:
            area = "backend/tests" if path.startswith("backend/") else "desktop/src"
            selected |= _files(root, (area + "/**/*.py", area + "/**/*.ts", area + "/**/*.tsx"))
    dynamic_modules: list[str] = []
    for path in changed_paths:
        target = root / path
        try:
            text = target.read_text(encoding="utf-8") if target.is_file() else ""
        except (OSError, UnicodeDecodeError):
            text = ""
        if _DYNAMIC_RE.search(text):
            dynamic_modules.append(PurePosixPath(path).stem)
    if dynamic_modules:
        hits.append("dynamic-import")
        for test in _files(root, ("backend/tests/**/*.py",)):
            try:
                text = (root / test).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if any(module in text for module in dynamic_modules):
                selected.add(test)
    ordered = sorted(selected)
    return ordered[:cap], list(dict.fromkeys(hits)), ordered[cap:]
