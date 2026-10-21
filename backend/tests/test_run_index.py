"""core/run_index.py — one shared, incremental read of every run.json (F2).

What is tested: every run.json under Projects/*/.artifacts/runs/ is listed; an
unchanged file is not parsed again (json.load counted); a rewritten file is; a
half-written file is skipped and read again next call; a removed run drops out;
callers get copies (setting a top-level key does not leak into the next call);
live_runs keeps running/paused only.
"""
from __future__ import annotations

import json
import os

import pytest

from core import run_index


@pytest.fixture(autouse=True)
def _fresh():
    run_index._clear_for_tests()
    yield
    run_index._clear_for_tests()


def _write(ws, project, rid, **data):
    rd = ws / "Projects" / project / ".artifacts" / "runs" / rid
    rd.mkdir(parents=True, exist_ok=True)
    f = rd / "run.json"
    f.write_text(json.dumps({"id": rid, **data}))
    return f


def test_lists_every_run_and_parses_only_changed_files(tmp_path, monkeypatch):
    _write(tmp_path, "A", "run_1", status="completed")
    f2 = _write(tmp_path, "B", "run_2", status="running")
    loads = {"n": 0}
    real = run_index.json.load

    def counting(fh):
        loads["n"] += 1
        return real(fh)

    monkeypatch.setattr(run_index.json, "load", counting)
    assert {r.path.parent.name for r in run_index.runs(tmp_path)} == {"run_1", "run_2"}
    assert loads["n"] == 2
    run_index.runs(tmp_path)
    assert loads["n"] == 2  # nothing changed: no parse
    f2.write_text(json.dumps({"id": "run_2", "status": "completed", "pad": "x" * 10}))
    os.utime(f2, ns=(os.stat(f2).st_atime_ns, os.stat(f2).st_mtime_ns + 1_000_000))
    got = {r.path.parent.name: r.data["status"] for r in run_index.runs(tmp_path)}
    assert got == {"run_1": "completed", "run_2": "completed"} and loads["n"] == 3


def test_half_written_file_is_skipped_then_read(tmp_path):
    f = _write(tmp_path, "A", "run_1", status="running")
    f.write_text('{"id": "run_1", "sta')
    assert run_index.runs(tmp_path) == []
    f.write_text(json.dumps({"id": "run_1", "status": "running"}))
    assert [r.data["status"] for r in run_index.runs(tmp_path)] == ["running"]


def test_removed_run_drops_out_and_callers_get_copies(tmp_path):
    f = _write(tmp_path, "A", "run_1", status="paused")
    _write(tmp_path, "A", "run_2", status="completed")
    first = run_index.runs(tmp_path)
    for r in first:
        r.data["_project"] = "mutated"
    assert all("_project" not in r.data for r in run_index.runs(tmp_path))
    assert [r.path.parent.name for r in run_index.live_runs(tmp_path)] == ["run_1"]
    f.unlink()
    f.parent.rmdir()
    assert [r.path.parent.name for r in run_index.runs(tmp_path)] == ["run_2"]


def test_no_projects_dir_is_empty(tmp_path):
    assert run_index.runs(tmp_path / "nowhere") == []
