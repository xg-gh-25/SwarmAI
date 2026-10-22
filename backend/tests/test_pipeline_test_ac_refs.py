"""A stale BUILD ac_coverage ref is a named push_ready gap, never a runner error.

What is tested:
- ``pipeline_test._ac_name_status``: a test / class / method a nodeid names is
  present, missing, or unknown (unparsable file, an imported name, a ``*`` import, a
  method a class with bases may inherit); ``name[param]`` is judged by ``name``.
- ``_plan_ac_refs``: a ref whose test is missing (or whose file is missing) is not a
  pytest target, is warned and recorded in ``ac_refs_missing``; a present or unknown
  one stays a target; a stray non-test word is warned, not recorded.
- ``artifact_cli._deliver_push_ready_bound`` lowers push_ready with a named reason when
  the full-scope report carries ``ac_refs_missing``.
Invariant I2: a stale ac_coverage ref never errors the runner and never passes silently.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "scripts"))
import pipeline_test as pt  # noqa: E402
import artifact_cli as cli  # noqa: E402

SRC = '''
import pytest
from helpers import test_imported

class Base:
    def test_base(self):
        pass

class TestPlain:
    def test_m(self):
        pass

class TestChild(Base):
    def test_own(self):
        pass

@pytest.mark.parametrize("x", [1, 2])
def test_param(x):
    pass

test_assigned = pytest.mark.skip(lambda: None)

try:
    from optional import test_optional
except ImportError:
    def test_fallback():
        pass

if True:
    def test_guarded():
        pass
'''


@pytest.fixture
def repo(tmp_path):
    t = tmp_path / "backend" / "tests"
    t.mkdir(parents=True)
    (t / "test_a.py").write_text(SRC)
    (t / "test_bad.py").write_text("def test_x(:\n")
    (t / "test_star.py").write_text("from shared import *\n")
    return tmp_path


@pytest.mark.parametrize("nodeid,expect", [
    ("backend/tests/test_a.py::test_param", "present"),
    ("backend/tests/test_a.py::test_param[1]", "present"),
    ("backend/tests/test_a.py::test_assigned", "present"),
    ("backend/tests/test_a.py::TestPlain::test_m", "present"),
    ("backend/tests/test_a.py::TestChild::test_own", "present"),
    ("backend/tests/test_a.py::test_fallback", "present"),
    ("backend/tests/test_a.py::test_guarded", "present"),
    ("backend/tests/test_a.py::test_optional", "unknown"),
    ("backend/tests/test_a.py::test_renamed", "missing"),
    ("backend/tests/test_a.py::TestPlain::test_gone", "missing"),
    ("backend/tests/test_a.py::TestGone::test_m", "missing"),
    ("backend/tests/test_a.py::test_param::test_x", "missing"),
    ("backend/tests/test_a.py::test_imported", "unknown"),
    ("backend/tests/test_a.py::TestChild::test_base", "unknown"),
    ("backend/tests/test_bad.py::test_x", "unknown"),
    ("backend/tests/test_star.py::test_any", "unknown"),
])
def test_name_status(repo, nodeid, expect):
    assert pt._ac_name_status(repo, nodeid) == expect


def test_a_stale_ref_is_recorded_not_targeted(repo):
    ctx = SimpleNamespace(repo=repo, warnings=[])
    plan = {"targets": {}, "ac_entries": [
        ["backend/tests/test_a.py::test_param"],
        ["backend/tests/test_a.py::test_renamed", "backend/tests/test_gone.py::test_x"],
        ["backend/tests/test_a.py::test_imported", "and"]]}
    ac_files, probes = pt._plan_ac_refs(ctx, plan)
    assert plan["ac_refs_missing"] == ["backend/tests/test_a.py::test_renamed",
                                       "backend/tests/test_gone.py::test_x"]
    assert plan["targets"] == {"backend/tests/test_a.py": {
        "backend/tests/test_a.py::test_param", "backend/tests/test_a.py::test_imported"}}
    assert ac_files == ["backend/tests/test_a.py", "backend/tests/test_a.py"] and probes == []
    assert sum("not found" in w for w in ctx.warnings) == 3   # the word "and" is warned too
    assert any("renamed or deleted" in w for w in ctx.warnings)


def _bound(tmp_path, monkeypatch, report, newer=()):
    import pipeline_validator as pv
    src = tmp_path / "m.py"
    src.write_text("x = 1\n")
    monkeypatch.setattr(pv, "load_test_evidence", lambda _d: [*newer, report])
    monkeypatch.setattr(pv, "push_ready_upper_bound", lambda *a, **k: (True, []))
    monkeypatch.setattr(pv, "evidence_freshness",
                        lambda ev, ft, roots=(): {"status": "fresh", "stale_files": []})
    monkeypatch.setattr(cli, "_stage_artifact", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_unverified_gates", lambda _s: [])
    return cli._deliver_push_ready_bound(project="P", run_id="r", run_dir=tmp_path,
                                         run_state={"files_touched": [str(src)], "stages": []},
                                         profile="full", artifact={}, exit_result={"met": True})
