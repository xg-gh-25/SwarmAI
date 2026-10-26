"""A main-checkout file reported once is not reported in full again (design 2026-10-26-
pipeline-review-closure-and-visibility §5.2 point 4, step 12).

Before: the seen-map compared [status, size, mtime]; another session that kept editing
one file had it reported in full after every Bash call of this run.

What is tested (a real repo and run worktree, as test_main_dirty_scan_v1): the second
change of a reported path comes as one summary line naming it, not the full report; a
path seen for the first time still gets the full report; a path dirty at worktree
creation (the baseline, reported then) counts as reported.
"""
from __future__ import annotations

from core.main_dirty_scan import create_main_dirty_reporter
from core.worktree_write_gate import V1_MAIN_EDIT_REMEDY
from tests.test_main_dirty_scan_v1 import _context, _invoke, _start_v1_run


def test_a_reported_path_changing_again_is_one_summary_line(tmp_path, monkeypatch):
    repo, _engine = _start_v1_run(tmp_path, monkeypatch)
    reporter = create_main_dirty_reporter({"sdk_session_id": "session-v1"})
    assert _invoke(reporter) == {}
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    first = _context(_invoke(reporter))
    assert V1_MAIN_EDIT_REMEDY in first and str(repo / "app.py") in first

    (repo / "app.py").write_text("value = 33\n", encoding="utf-8")       # again
    (repo / "new.py").write_text("x = 1\n", encoding="utf-8")            # first time
    second = _context(_invoke(reporter))
    assert str(repo / "new.py") in second and V1_MAIN_EDIT_REMEDY in second
    head, _, tail = second.partition("still changing, reported before:")
    assert tail and "app.py" in tail and str(repo / "app.py") not in head, second

    (repo / "app.py").write_text("value = 444\n", encoding="utf-8")      # only the old one
    third = _context(_invoke(reporter))
    assert "still changing, reported before:" in third and V1_MAIN_EDIT_REMEDY not in third


def test_a_baseline_path_counts_as_reported(tmp_path, monkeypatch):
    from core import main_dirty_scan as mds
    repo, engine = _start_v1_run(tmp_path, monkeypatch)
    (repo / "app.py").write_text("value = 5\n", encoding="utf-8")
    from core import run_worktree as rw
    mds.record_baseline(engine.run_dir, repo, rw.main_dirty(str(repo)))   # as creation does
    reporter = create_main_dirty_reporter({"sdk_session_id": "session-v1"})
    (repo / "app.py").write_text("value = 6\n", encoding="utf-8")
    out = _context(_invoke(reporter))
    assert "still changing, reported before:" in out and V1_MAIN_EDIT_REMEDY not in out, out
