"""Tests for core.ddd_setup_state and its writers / reader.

What is tested: the record round-trips and rejects unknown states; a missing or
malformed file reads as None (an older Brain is never "being born"); the P6 gate's
--record writes ready on PASS and setting-up on FAIL; create_project starts a NEW
project as setting-up; the gallery summary carries setupState.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from core.ddd_setup_state import SETUP_FILE, read_setup_state, write_setup_state


def _verify_mod():
    p = Path(__file__).resolve().parents[1] / "skills/s_project-manager/scripts/verify_ddd_complete.py"
    spec = importlib.util.spec_from_file_location("verify_ddd_complete_ss", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_round_trip_and_unknown_state(tmp_path):
    assert read_setup_state(tmp_path) is None
    write_setup_state(tmp_path, "setting-up")
    assert read_setup_state(tmp_path) == "setting-up"
    write_setup_state(tmp_path, "ready", {"PASS": 7})
    assert read_setup_state(tmp_path) == "ready"
    assert json.loads((tmp_path / SETUP_FILE).read_text())["counts"] == {"PASS": 7}
    with pytest.raises(ValueError):
        write_setup_state(tmp_path, "done")


def test_malformed_reads_none(tmp_path):
    (tmp_path / ".artifacts").mkdir()
    (tmp_path / SETUP_FILE).write_text("{not json")
    assert read_setup_state(tmp_path) is None
    (tmp_path / SETUP_FILE).write_text('{"state": "half"}')
    assert read_setup_state(tmp_path) is None


def test_gate_record_ready_on_pass_setting_up_on_fail(tmp_path):
    mod = _verify_mod()
    mod.record_setup_state(tmp_path, {"overall": mod.STATUS_FAIL, "counts": {"FAIL": 1}})
    assert read_setup_state(tmp_path) == "setting-up"
    mod.record_setup_state(tmp_path, {"overall": mod.STATUS_PASS, "counts": {"PASS": 7}})
    assert read_setup_state(tmp_path) == "ready"


def test_gate_cli_record_flag(tmp_path):
    mod = _verify_mod()
    assert mod.main(["--project-dir", str(tmp_path)]) == 1          # empty dir fails
    assert read_setup_state(tmp_path) is None                       # no --record: nothing written
    mod.main(["--project-dir", str(tmp_path), "--record"])
    assert read_setup_state(tmp_path) == "setting-up"


def test_create_project_starts_setting_up(tmp_path):
    from core.swarm_workspace_manager import SwarmWorkspaceManager
    mgr = SwarmWorkspaceManager()
    (tmp_path / "Projects").mkdir()
    asyncio.run(mgr.create_project("Fresh", str(tmp_path)))
    assert read_setup_state(tmp_path / "Projects" / "Fresh") == "setting-up"


def test_gallery_summary_carries_setup_state(tmp_path):
    from routers.ddd_brain import _brain_summary
    write_setup_state(tmp_path, "ready")
    assert _brain_summary(tmp_path)["setupState"] == "ready"


def test_setup_progress_reads_the_last_p6_counts(tmp_path):
    """The Hub card's 'Being born · N of M checks': PASS + N/A of all checks, from
    the counts the last P6 --record wrote. No counts yet, or ready -> None."""
    from core.ddd_setup_state import read_setup_progress
    from routers import ddd_brain as m

    assert read_setup_progress(tmp_path) is None                     # no record
    write_setup_state(tmp_path, "setting-up")                         # create_project
    assert read_setup_progress(tmp_path) is None                      # no P6 run yet
    write_setup_state(tmp_path, "setting-up", {"PASS": 3, "FAIL": 2, "PENDING": 1, "N/A": 1})
    assert read_setup_progress(tmp_path) == {"done": 4, "total": 7}
    assert m._brain_summary(tmp_path)["setupProgress"] == {"done": 4, "total": 7}
    write_setup_state(tmp_path, "ready", {"PASS": 6, "PENDING": 1})
    assert read_setup_progress(tmp_path) is None                      # finished
    (tmp_path / SETUP_FILE).write_text(json.dumps({"state": "setting-up", "counts": {"PASS": "x"}}))
    assert read_setup_progress(tmp_path) is None                      # garbage never raises
    (tmp_path / SETUP_FILE).write_text("[1, 2]")
    assert read_setup_state(tmp_path) is None and read_setup_progress(tmp_path) is None
