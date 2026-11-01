"""The dev-box refusal of a test run wider than one change's tests (tests/conftest.py
``wide_scope_refusal``). Questions: is a run over the file or test count refused with
the way out named, is a scoped run allowed, and do CI and SWARMAI_SUITE=1 pass?"""
from __future__ import annotations

from types import SimpleNamespace

from tests.conftest import WIDE_SCOPE_FILES, WIDE_SCOPE_TESTS, wide_scope_refusal


def _items(files: int, per_file: int = 1):
    return [SimpleNamespace(nodeid=f"tests/test_f{f}.py::test_{t}")
            for f in range(files) for t in range(per_file)]


def test_a_wide_run_is_refused_and_says_how_to_scope_it():
    for items in (_items(WIDE_SCOPE_FILES + 1), _items(2, WIDE_SCOPE_TESTS // 2 + 1)):
        why = wide_scope_refusal(items, environ={})
        assert why and "swarm_select_tests.py" in why and "SWARMAI_SUITE=1" in why


def test_a_scoped_run_is_allowed():
    assert wide_scope_refusal(_items(WIDE_SCOPE_FILES, WIDE_SCOPE_TESTS // WIDE_SCOPE_FILES), environ={}) is None


def test_ci_and_a_wide_run_asked_for_pass():
    wide = _items(WIDE_SCOPE_FILES + 10)
    assert wide_scope_refusal(wide, environ={"CI": "true"}) is None
    assert wide_scope_refusal(wide, environ={"SWARMAI_SUITE": "1"}) is None
    assert wide_scope_refusal(wide, environ={"SWARMAI_SUITE": "0"}) is not None
