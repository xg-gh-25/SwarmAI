"""Tests for core.surface_marker — the evidence the completion gate accepts that a
run's committed source rows were surfaced to Canvas (run_a87bfe3f).

The marker is written by the daemon's streaming orchestrator after it emits a
surface_run_outputs batch, beside the run's run.json, only when at least one
committed source row was emitted. The CLI completion
gate accepts it only if it names every commit the run has recorded, so a commit made
after surfacing does not pass on an old marker.
"""
from __future__ import annotations

import json

import pytest

from core import surface_marker as sm


def _run(tmp_path, run_id="run_abc", commits=None):
    run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True)
    rj = run_dir / "run.json"
    rj.write_text(json.dumps({"id": run_id, "commits": commits or []}), encoding="utf-8")
    return rj


SRC = {"type": "file_changed", "path": "backend/a.py", "kind": "source-final"}
REPORT = {"type": "file_changed", "path": "x/REPORT.md", "kind": "knowledge"}


class TestFindRunJson:
    def test_finds_the_run(self, tmp_path):
        rj = _run(tmp_path)
        assert sm.find_run_json("run_abc", tmp_path) == rj

    @pytest.mark.parametrize("bad", ["", "../x", "run_*", "a/b", None, 7])
    def test_rejects_unsafe_ids(self, tmp_path, bad):
        _run(tmp_path)
        assert sm.find_run_json(bad, tmp_path) is None

    def test_missing_run(self, tmp_path):
        assert sm.find_run_json("run_none", tmp_path) is None


class TestWriteMarker:
    def test_source_row_writes_marker_with_commits(self, tmp_path):
        rj = _run(tmp_path, commits=[{"sha": "c1", "files": ["backend/a.py"]}])
        assert sm.write_marker(rj, [SRC, REPORT]) is True
        data = json.loads((rj.parent / sm.MARKER_NAME).read_text())
        assert data["source_rows"] == 1
        assert data["commits"] == ["c1"]
        assert data["run_id"] == "run_abc"

    def test_report_only_writes_nothing(self, tmp_path):
        rj = _run(tmp_path)
        assert sm.write_marker(rj, [REPORT]) is False
        assert not (rj.parent / sm.MARKER_NAME).exists()

    def test_no_events_writes_nothing(self, tmp_path):
        rj = _run(tmp_path)
        assert sm.write_marker(rj, []) is False
        assert not (rj.parent / sm.MARKER_NAME).exists()

    def test_concurrent_writers_leave_one_valid_marker_and_no_temp(self, tmp_path):
        """code-quality review: the daemon writes from worker threads, so two writes
        for one run can overlap in one process; a pid-based temp name collided."""
        import threading
        rj = _run(tmp_path, commits=[{"sha": "c1"}])
        results = []
        ts = [threading.Thread(target=lambda: results.append(sm.write_marker(rj, [SRC])))
              for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert results == [True] * 8
        assert json.loads((rj.parent / sm.MARKER_NAME).read_text())["commits"] == ["c1"]
        assert not list(rj.parent.glob("*.tmp"))

    def test_never_raises_on_unreadable_run_json(self, tmp_path):
        rj = _run(tmp_path)
        rj.write_text("{not json", encoding="utf-8")
        assert sm.write_marker(rj, [SRC]) is False
        assert sm.write_marker(tmp_path / "nope" / "run.json", [SRC]) is False


class TestMarkerCovers:
    def test_covers_recorded_commits(self, tmp_path):
        rj = _run(tmp_path, commits=[{"sha": "c1"}])
        sm.write_marker(rj, [SRC])
        assert sm.marker_covers(rj.parent, [{"sha": "c1"}]) is True

    def test_commit_after_surfacing_is_not_covered(self, tmp_path):
        rj = _run(tmp_path, commits=[{"sha": "c1"}])
        sm.write_marker(rj, [SRC])
        assert sm.marker_covers(rj.parent, [{"sha": "c1"}, {"sha": "c2"}]) is False

    def test_no_marker(self, tmp_path):
        rj = _run(tmp_path)
        assert sm.marker_covers(rj.parent, [{"sha": "c1"}]) is False

    def test_corrupt_marker(self, tmp_path):
        rj = _run(tmp_path)
        (rj.parent / sm.MARKER_NAME).write_text("[", encoding="utf-8")
        assert sm.marker_covers(rj.parent, [{"sha": "c1"}]) is False

    def test_zero_source_rows_is_not_evidence(self, tmp_path):
        rj = _run(tmp_path)
        (rj.parent / sm.MARKER_NAME).write_text(
            json.dumps({"source_rows": 0, "commits": ["c1"]}), encoding="utf-8")
        assert sm.marker_covers(rj.parent, [{"sha": "c1"}]) is False
