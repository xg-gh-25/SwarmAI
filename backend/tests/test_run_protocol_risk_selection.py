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


def test_nested_conftest_and_other_vitest_configs_are_shared_config(tmp_path):
    """E: shared config is not only the exact roots: a nested backend conftest.py and
    any desktop vitest.config.* / vite.config.* change every test of the area."""
    from core.run_protocol.risk_selection import expand
    for rel in ("backend/tests/test_a.py", "backend/tests/sub/test_b.py",
                "desktop/src/x.test.ts"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")
    for changed, want in (("backend/tests/sub/conftest.py", "backend/tests/test_a.py"),
                          ("desktop/vitest.config.mts", "desktop/src/x.test.ts"),
                          ("desktop/packages/ui/vite.config.ts", "desktop/src/x.test.ts")):
        selected, hits, _ = expand(tmp_path, [changed], cap=50)
        assert "shared-config" in hits and want in selected, (changed, hits, selected)
    _, hits, _ = expand(tmp_path, ["desktop/src/conftest.py"], cap=50)
    assert "shared-config" not in hits


def test_shared_config_selects_only_test_files_of_its_area(tmp_path):
    """R7: a shared-config change reruns the area's TESTS, never its sources (a
    desktop/src/bad.ts selected as a "test" became a pytest target)."""
    for rel in ("backend/tests/test_a.py", "backend/tests/pipeline_v1_rig.py",
                "backend/tests/conftest.py", "desktop/src/x.test.ts",
                "desktop/src/y.test.tsx", "desktop/src/bad.ts", "desktop/src/App.tsx"):
        _touch(tmp_path, rel, "x\n")
    selected, hits, _ = expand(tmp_path, ["desktop/package.json"], cap=50)
    assert "shared-config" in hits
    assert selected == ["desktop/src/x.test.ts", "desktop/src/y.test.tsx"]
    selected, _, _ = expand(tmp_path, ["backend/conftest.py"], cap=50)
    assert selected == ["backend/tests/test_a.py"]
