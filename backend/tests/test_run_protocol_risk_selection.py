"""Tests the frozen v13 risk-expansion table.

Methodology: tiny filesystem containing representative source and test files.
"""
from __future__ import annotations

from pathlib import Path

from core.run_protocol.risk_selection import expand


def _touch(root: Path, path: str, text: str = ""):
    target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text)


def test_pipeline_and_gate_paths_expand_to_their_suites(tmp_path):
    _touch(tmp_path, "backend/tests/test_run_protocol_x.py")
    _touch(tmp_path, "backend/tests/test_test_selection_frozen.py")
    _touch(tmp_path, "backend/tests/test_security_hooks.py")
    selected, rules, overflow = expand(
        tmp_path, ["backend/core/run_protocol/x.py", "backend/core/security_hooks.py"], cap=20)
    assert {"pipeline-protocol", "security-gates"} <= set(rules)
    assert "backend/tests/test_run_protocol_x.py" in selected
    assert "backend/tests/test_security_hooks.py" in selected and overflow == []


def test_public_contract_persistence_and_dynamic_import_expand(tmp_path):
    _touch(tmp_path, "backend/tests/test_api_contract.py", "import dynamic_mod")
    _touch(tmp_path, "backend/tests/test_schema.py")
    _touch(tmp_path, "backend/core/dynamic_mod.py", "import importlib\n")
    selected, rules, _ = expand(
        tmp_path, ["backend/routers/a.py", "backend/database/schema.py",
                   "backend/core/dynamic_mod.py"], cap=20)
    assert {"public-contract", "persistence", "dynamic-import"} <= set(rules)
    assert "backend/tests/test_api_contract.py" in selected


def test_shared_config_overflow_is_explicit_not_silently_partial(tmp_path):
    for n in range(5):
        _touch(tmp_path, f"backend/tests/test_{n}.py")
    selected, rules, overflow = expand(tmp_path, ["backend/conftest.py"], cap=2)
    assert rules == ["shared-config"] and len(selected) == 2 and len(overflow) == 3
    assert sorted(selected + overflow) == [f"backend/tests/test_{n}.py" for n in range(5)]
