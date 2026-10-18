"""PLAN rejects a ``kind=test`` exit criterion that can never be satisfied.

WHAT IS TESTED
- ``validate_exit_criteria`` rejects a test ref naming a directory (trailing
  slash, extensionless last segment, or an existing directory under any evidence
  root) or a glob (``*`` / ``?``), with an error naming ``exit_criteria[i].ref``
  and telling the planner to name each test file or use ``mode:regression``.
- Every ref shape the judge CAN satisfy keeps passing (file, nodeid, parametrized
  nodeid, desktop vitest file, bracketed route folder, ``./`` path, ``mode:<m>``)
  — an over-strict guard is as much a bug as a missing one.
- The check runs on relaxed profiles too (a directory ref is unsatisfiable on
  every profile).
- Default roots: SWARM_TEST_REPO_ROOT and the workspace are consulted, so a
  dotted directory name (``v1.2``) under either is caught by the filesystem check.
- The validator and the judge share ONE mode-ref predicate (``_is_mode_ref``):
  `` mode:x`` (leading space) is a path to both.
- The PLAN card and the exit-contract reference document the rule.

Method: real filesystem under ``tmp_path``; literal criterion dicts; no mocks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pipeline_validator as pv  # noqa: E402

_STAGES = _BACKEND / "skills" / "s_autonomous-pipeline" / "stages"


def _crit(ref: str, kind: str = "test") -> list[dict]:
    return [{"id": "X1", "kind": kind, "ref": ref, "expect": "pass"}]


def _errs(ref: str, *, roots=(), required: bool = True, kind: str = "test") -> list[str]:
    return pv.validate_exit_criteria(_crit(ref, kind), required=required, roots=roots)


# ── AC1: directory refs ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ref", [
    "desktop/src/components/file-viewer/__tests__",   # the run_2e1a4548 ref
    "desktop/src/pages/chat/components/__tests__",    # the run_cb0e98ad ref
    "backend/tests/",
    "backend/tests",
    "backend/tests/::test_a",
    "desktop",                 # the namespace alone is still a directory
    "desktop::",
    "import",
])
def test_directory_ref_is_rejected(ref):
    errs = _errs(ref)
    assert len(errs) == 1, errs
    assert "exit_criteria[0].ref" in errs[0]
    assert "directory" in errs[0]


def test_dotted_directory_under_a_root_is_rejected(tmp_path):
    (tmp_path / "tests" / "v1.2").mkdir(parents=True)
    errs = _errs("tests/v1.2", roots=[str(tmp_path)])
    assert len(errs) == 1 and "exit_criteria[0].ref" in errs[0], errs


def test_existing_file_under_a_root_passes(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    assert _errs("tests/test_a.py", roots=[str(tmp_path)]) == []


def test_default_roots_include_test_repo_root_and_workspace(tmp_path, monkeypatch):
    repo, ws = tmp_path / "repo", tmp_path / "ws"
    (repo / "suite" / "v2.0").mkdir(parents=True)
    (ws / "more" / "v3.1").mkdir(parents=True)
    monkeypatch.setenv("SWARM_TEST_REPO_ROOT", str(repo))
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    assert pv.validate_exit_criteria(_crit("suite/v2.0"), required=True)
    assert pv.validate_exit_criteria(_crit("more/v3.1"), required=True)
    assert pv.validate_exit_criteria(_crit("suite/v9.9"), required=True) == []


def test_directory_ref_is_rejected_on_a_relaxed_profile_too():
    assert _errs("backend/tests/", required=False)


# ── AC2: glob refs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("ref", [
    "backend/tests/test_*.py",
    "desktop/src/**/x.test.tsx",
    "backend/tests/test_?.py",
    "backend/tests/test_*.py::test_a",
])
def test_glob_ref_is_rejected(ref):
    errs = _errs(ref)
    assert len(errs) == 1 and "exit_criteria[0].ref" in errs[0], errs
    assert "glob" in errs[0]


# ── AC3: everything the judge can satisfy still passes ───────────────────────

@pytest.mark.parametrize("ref", [
    "file.py",
    "backend/tests/test_x.py",
    "backend/tests/test_x.py::test_a",
    "test_x.py::TestC::test_b[a/b.c]",
    "backend/tests/test_x.py::test_p[a*b?]",
    "desktop/src/a.test.tsx",
    "desktop/src/app/[id]/page.test.tsx",
    "./backend/tests/test_x.py",
    "mode:regression",
    "mode:verify",
    "desktop::tsc",            # runner synthetic nodeids (pipeline_test._res)
    "desktop::vitest",
    "import::core.pipeline_profiles",
])
def test_satisfiable_ref_passes(ref):
    assert _errs(ref) == []


def test_other_kinds_are_not_shape_checked():
    assert _errs("ref-shape", kind="mutation") == []
    assert pv.validate_exit_criteria(
        [{"id": "X1", "kind": "manual", "ref": "docs/", "expect": "done"}], required=True) == []


def test_plan_template_still_validates_clean():
    assert pv.validate_exit_criteria(pv.STAGE_TEMPLATES["plan"]["exit_criteria"], required=True) == []


# ── AC4: actionable message ──────────────────────────────────────────────────

def test_error_tells_the_planner_what_to_write_instead():
    msg = _errs("backend/tests/")[0]
    assert "test file" in msg and "mode:regression" in msg


# ── AC6: one mode-ref predicate shared with the judge ────────────────────────

def test_leading_space_mode_ref_is_a_path_to_both_validator_and_judge():
    assert pv._is_mode_ref("mode:regression") is True
    assert pv._is_mode_ref(" mode:regression") is False
    assert _errs(" mode:regression")  # rejected: the judge would read it as a path
    errs = _errs(" mode:v1.2")  # a dotted mode name has a suffix: still rejected
    assert len(errs) == 1 and "whitespace before 'mode:'" in errs[0], errs
    ev = [{"mode": "regression", "counts": {"passed": 3}, "results": []}]
    ok, why = pv._judge_test_criterion(" mode:regression", ev)
    assert ok is False and "no test evidence mentions" in why
    ok, _ = pv._judge_test_criterion("mode:regression", ev)
    assert ok is True


# ── AC5: documented ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc", ["plan.md", "ref/plan/exit-contract.md"])
def test_docs_state_the_rule(doc):
    text = (_STAGES / doc).read_text(encoding="utf-8")
    assert "directory" in text and "glob" in text and "mode:regression" in text
