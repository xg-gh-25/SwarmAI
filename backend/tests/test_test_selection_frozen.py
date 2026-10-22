"""Freeze guard for the pipeline test selection (docs/design/pipeline-test-speed.md §11).

The selection was rewritten six times in three days (10-17 .. 10-20). XG froze it on
2026-10-20: BUILD/fix rounds run ``--mode tdd`` / ``prove`` on named files; the whole
change scope (changed tests + direct importers + AC tests + import smoke + CI BVT) runs
at DELIVER only, at most FULL_SCOPE_MAX_ROUNDS times; CI runs the BVT manifest.

This test pins the constants and the source of the functions that decide WHAT runs.
A change that makes it fail is a change to the frozen design: it needs XG's explicit
approval first, then a new §11 entry and new digests here, in the same commit.
Do NOT update the digests to make a pipeline run go green.
"""

import hashlib
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pipeline_test as pt  # noqa: E402

FROZEN_FUNCTIONS = (
    "select_backend",
    "transitive_unselected",
    "_bvt_files",
    "_full_scope_refusal",
    "_smoke_modules",
    "_recent_failure_files",
    "_plan",
    "_risk_expansion",
    "_plan_ac_refs",
    "build_source_graph",
    "_resolve_script_imports",
)

DIGESTS = {
    "select_backend": "7274543cdb6db9cc",  # 10-21: source-scan suites on any module change (§11)
    "transitive_unselected": "b1acd1e5cfa8521f",
    "_bvt_files": "c275cb940b06a3a9",
    "_full_scope_refusal": "0e054d7718481d65",  # 10-20: recheck past the cap; tampered index refused (2c05cfbc, §11)
    "_smoke_modules": "1ea6c7b77910e927",
    "_recent_failure_files": "7173dd3412333fa3",
    "_plan": "1d6784388fa070b4",  # 10-22: probe: refs removed (v1 fixing spec B4, §11)
    "_risk_expansion": "4dc4d9b14e4de507",  # 10-22: v13 §B3.3, contract-capped
    "_plan_ac_refs": "4cb596d98ebdbe6f",  # 10-22: probe: refs removed (v1 fixing spec B4, §11)
    "build_source_graph": "e581602ee29be082",  # 10-21: bare script imports resolved (§11)
    "_resolve_script_imports": "3009fba168114713",  # 10-21: new, decides import edges (§11)
}


def _digest(name: str) -> str:
    src = inspect.getsource(getattr(pt, name))
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def test_modes_frozen():
    assert pt.TEST_MODES == ("tdd", "verify", "regression", "recheck")
    assert pt.FULL_SCOPE_MODES == ("verify", "regression", "recheck")
    assert pt.FULL_SCOPE_STAGES == ("deliver",)
    assert pt.FULL_SCOPE_MAX_ROUNDS == 2
    assert pt.RECHECK_MAX_FIXES == 3
    assert pt.REVERSE_DEPTH == 1


def test_selection_source_frozen():
    changed = {n: _digest(n) for n in FROZEN_FUNCTIONS if _digest(n) != DIGESTS.get(n)}
    assert not changed, (
        "test selection is frozen (pipeline-test-speed.md §11). Changed: "
        f"{sorted(changed)}. Get XG's approval, record it in §11, then update DIGESTS: "
        f"{changed}"
    )
