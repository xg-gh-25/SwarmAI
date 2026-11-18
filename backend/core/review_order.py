"""review_order — the order a person reads a change's files in.

The heart of a change first, then what checks it, then the glue: source files, largest
change first; then test files; then the files that support the change (docs, config,
lockfiles, generated output, locale strings, CI). The order is from the path and the
line counts alone, so it works for any repository and language and costs nothing.

Every list of a change's files shown for review uses it: the chat turn's "files
changed" card and its landings (``core.turn_changes``, ``core.session_landings``),
which the Canvas Changes tab reads, and a Hive return (``core.hive_return``). When a
list has a row cap, the cap drops supporting files before source files.

Public: ``role``, ``ordered``, ``ROLES``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

ROLES = ("source", "test", "supporting")

_TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|specs?|e2e|testdata|fixtures)/", re.I)
_TEST_FILE = re.compile(
    r"(^test_.*|.*_test\.\w+|.*\.(test|spec)\.\w+|.*Tests?\.(java|kt|cs|swift|scala)|conftest\.py)$")
_SUPPORT_DIR = re.compile(r"(^|/)(docs?|generated|locales?|i18n|\.github|\.circleci)/", re.I)
_SUPPORT_EXT = re.compile(
    r"\.(md|mdx|rst|txt|adoc|json|ya?ml|toml|ini|cfg|conf|lock|csv|snap|svg|png|jpe?g|gif|ico|webp)$",
    re.I)
_SUPPORT_NAME = re.compile(
    r"^(go\.sum|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Cargo\.lock|"
    r"Gemfile\.lock|uv\.lock|requirements[^/]*\.txt|LICENSE[^/]*|NOTICE|CHANGELOG[^/]*|"
    r"\.gitignore|\.gitattributes|\.editorconfig|\.env\.example)$")


def role(path: str) -> str:
    """``source``, ``test`` or ``supporting`` for a repository-relative *path*."""
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if _TEST_DIR.search(p) or _TEST_FILE.match(name):
        return "test"
    if _SUPPORT_DIR.search(p) or _SUPPORT_NAME.match(name) or _SUPPORT_EXT.search(name):
        return "supporting"
    return "source"


def _size(f: Mapping[str, Any]) -> int:
    return (f.get("added") or 0) + (f.get("deleted") or 0)


def ordered(files: Iterable[Mapping[str, Any]]) -> list:
    """*files* (dicts with ``path`` and optional ``added`` / ``deleted``) in review order:
    by role, then the largest change first, then by path."""
    return sorted(files, key=lambda f: (ROLES.index(role(str(f.get("path") or ""))),
                                        -_size(f), str(f.get("path") or "")))
