"""Dependency-range carry-over decisions for Pipeline v1 finals.

Public symbols:
- ``carry_decision`` — decide whether a final survives a moving integration base.
- ``affected_tests`` — select only test dependency scopes intersecting main changes.
- ``CarryDecision`` — explicit reusable/recheck result with evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


class CarryError(ValueError):
    """Dependency provenance is missing or malformed, so reuse must fail closed."""


@dataclass(frozen=True)
class CarryDecision:
    reusable: bool
    reason: str
    intersecting: tuple[str, ...]
    affected_tests: tuple[str, ...]


def _paths(values: Sequence[str], field: str) -> set[str]:
    if not isinstance(values, (list, tuple, set, frozenset)) or any(
        not isinstance(value, str) or not value or value.startswith("/") or value == ".."
        or value.startswith("../") for value in values
    ):
        raise CarryError(f"{field} has unsafe paths")
    return set(values)


def affected_tests(main_changed: Sequence[str], test_dependencies: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    changed = _paths(main_changed, "main_changed")
    if not isinstance(test_dependencies, Mapping):
        raise CarryError("test dependency map is required")
    affected: list[str] = []
    for nodeid, dependencies in test_dependencies.items():
        if not isinstance(nodeid, str) or not nodeid:
            raise CarryError("test dependency nodeid is invalid")
        dependency_paths = _paths(dependencies, f"dependencies of {nodeid}") | {nodeid}
        if changed & dependency_paths:
            affected.append(nodeid)
    return tuple(sorted(affected))


def carry_decision(
    *, old_base: str, new_base: str, old_tree: str, new_tree: str,
    main_changed: Sequence[str], run_changed: Sequence[str],
    selected_tests: Sequence[str], test_dependencies: Mapping[str, Sequence[str]],
) -> CarryDecision:
    """Reuse only when main changes miss every run/test/direct-import dependency."""
    if not all(isinstance(value, str) and len(value) == 40
               for value in (old_base, new_base, old_tree, new_tree)):
        raise CarryError("base/tree identities are required")
    if (old_base, old_tree) == (new_base, new_tree):
        return CarryDecision(True, "identical base and tree", (), ())
    changed = _paths(main_changed, "main_changed")
    mine = _paths(run_changed, "run_changed")
    tests = _paths(selected_tests, "selected_tests")
    dependencies = set(mine) | set(tests)
    for nodeid, paths in test_dependencies.items():
        if nodeid in tests:
            dependencies |= _paths(paths, f"dependencies of {nodeid}")
    intersecting = tuple(sorted(changed & dependencies))
    affected = affected_tests(main_changed, test_dependencies)
    if intersecting:
        return CarryDecision(False, "integration changes intersect the final dependency scope",
                             intersecting, affected)
    return CarryDecision(True, "integration changes are outside the final dependency scope", (), ())
