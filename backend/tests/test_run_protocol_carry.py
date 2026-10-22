"""Tests dependency-range final carry-over decisions.

Methodology: pure path sets; proves same-file and imported-file changes invalidate while
unrelated integration changes carry the final with explicit provenance.
"""
from __future__ import annotations

import pytest

from core.run_protocol.carry import CarryError, affected_tests, carry_decision

ID = "1" * 40


def _decide(changed):
    return carry_decision(
        old_base=ID, new_base="2" * 40, old_tree="3" * 40, new_tree="4" * 40,
        main_changed=changed, run_changed=["backend/core/a.py"],
        selected_tests=["backend/tests/test_a.py"],
        test_dependencies={"backend/tests/test_a.py": ["backend/core/a.py", "backend/core/dep.py"]})


def test_unrelated_main_change_reuses_final():
    result = _decide(["docs/readme.md"])
    assert result.reusable and result.intersecting == ()


@pytest.mark.parametrize("path", ["backend/core/a.py", "backend/core/dep.py", "backend/tests/test_a.py"])
def test_run_test_or_import_change_requires_recheck(path):
    result = _decide([path])
    assert not result.reusable and path in result.intersecting
    assert result.affected_tests == ("backend/tests/test_a.py",)


def test_affected_tests_only_returns_intersecting_scopes():
    deps = {"a": ["x", "shared"], "b": ["y", "shared"], "c": ["z"]}
    assert affected_tests(["shared"], deps) == ("a", "b")


def test_missing_or_unsafe_dependency_provenance_fails_closed():
    with pytest.raises(CarryError):
        _decide(["../outside"])
    with pytest.raises(CarryError):
        affected_tests(["x"], {"a": ["../outside"]})
