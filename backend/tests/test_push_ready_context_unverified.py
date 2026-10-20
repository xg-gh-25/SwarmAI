"""DELIVER push_ready vs tests a continuation ran without their file's earlier tests.

What is tested (artifact_cli._deliver_push_ready_bound):
- a continued test of a test file the run CHANGED still caps push_ready: the run's own
  tests must pass in their file context;
- continued tests of test files the run did NOT change are a warning only: their pass is
  real and the order risk predates the run (before this rule every run with a slow test
  file was never push-ready);
- touched paths are absolute, nodeids repo-relative: the match is by path suffix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import artifact_cli as cli  # noqa: E402


def _bound(tmp_path, monkeypatch, ctx: list[str], touched: list[str]):
    import pipeline_validator as pv
    report = {"mode": "regression", "run_id": "r", "counts": {"passed": 9}, "failures": [],
              "context_unverified": ctx}
    monkeypatch.setattr(pv, "load_test_evidence", lambda _d: [report])
    monkeypatch.setattr(pv, "push_ready_upper_bound", lambda *a, **k: (True, []))
    monkeypatch.setattr(pv, "evidence_freshness",
                        lambda ev, ft, roots=(): {"status": "fresh", "stale_files": []})
    monkeypatch.setattr(cli, "_stage_artifact", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_unverified_gates", lambda _s: [])
    state = {"files_touched": touched, "stages": []}
    return cli._deliver_push_ready_bound(project="P", run_id="r", run_dir=tmp_path,
                                         run_state=state, profile="full", artifact={},
                                         exit_result={"met": True})


def test_unchanged_test_files_continued_alone_warn_but_do_not_cap(tmp_path, monkeypatch):
    src = str(tmp_path / "wt" / "swarmai" / "backend" / "core" / "m.py")
    ctx = ["backend/tests/test_slow.py::t3", "backend/tests/test_slow.py::t4"]
    bound, reasons, warnings = _bound(tmp_path, monkeypatch, ctx, [src])
    assert bound is True, reasons
    assert any("2 continued test(s) passed without the earlier tests" in w for w in warnings)


def test_a_changed_test_file_continued_alone_still_caps(tmp_path, monkeypatch):
    root = tmp_path / "wt" / "swarmai"
    touched = [str(root / "backend" / "core" / "m.py"),
               str(root / "backend" / "tests" / "test_mine.py")]
    ctx = ["backend/tests/test_mine.py::t2", "backend/tests/test_slow.py::t3"]
    bound, reasons, warnings = _bound(tmp_path, monkeypatch, ctx, touched)
    assert bound is False
    hit = [r for r in reasons if "continued test(s) passed without the earlier tests" in r]
    assert len(hit) == 1 and "1 continued" in hit[0] and "backend/tests/test_mine.py" in hit[0]
    assert "test_slow.py" not in hit[0]
    assert any("1 continued test(s)" in w for w in warnings)


def test_suffix_match_does_not_confuse_a_same_named_file_elsewhere(tmp_path, monkeypatch):
    touched = [str(tmp_path / "other" / "test_mine.py")]  # not backend/tests/test_mine.py
    bound, reasons, _ = _bound(tmp_path, monkeypatch, ["backend/tests/test_mine.py::t1"], touched)
    assert bound is True, reasons
