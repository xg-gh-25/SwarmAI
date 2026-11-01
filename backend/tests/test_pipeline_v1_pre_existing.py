"""Red at the run's base (design 2026-11-01-pipeline-delivery-friction D6).

- a TEST command the agent reports ``pre_existing`` (it fails the same way on the base)
  needs a reason, does not stop TEST or the landing, and the REPORT lists it apart;
- ``pipeline-v1 base-tree`` puts the run's base of a repo in the system temp dir (not
  the workspace), returns the same directory on a second call, and the run's worktree
  release removes it.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.run_protocol import validation_report as vr
from tests.pipeline_v1_rig import SWARM, Rig

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    r.close()


def _red_report(r: Rig) -> dict:
    report = r.report()
    report["reports"][0]["commands"].append(
        {"command": "npm test -- chat", "cwd": "desktop", "result": "pre_existing",
         "reason": "the same 8 tests fail on the run's base"})
    return report


def test_pre_existing_needs_a_reason():
    row = {"repo": "repo", "commands": [{"command": "x", "cwd": ".", "result": "pre_existing"}],
           "tests_changed": [], "rationale": "r"}
    with pytest.raises(vr.ReportError, match="reason"):
        vr.parse({"reports": [row]}, ["repo"])
    row["commands"][0]["reason"] = "red on the base"
    payload = vr.event_payload("test", vr.parse({"reports": [row]}, ["repo"])[0], "0" * 40)
    assert payload["n_pre_existing"] == 1 and payload["n_fail"] == 0


def test_a_red_at_base_command_lands_and_the_report_lists_it_apart(rig):
    r = rig
    r.create()
    r.drive(until="test")
    out = r.v1("stage-complete", "--artifact", r.artifact("red", _red_report(r)))
    assert out.get("ok") is True and r.state()["stage"] == "deliver", out
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {
        "delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    r.v1("land", "--message", "fix: swap the operands" + SWARM)
    assert r.state().get("landing") == "landed"
    report = (r.engine().run_dir / "REPORT.md").read_text()
    assert "## Red at the run's base too" in report and "npm test -- chat" in report
    quality = next(line for line in report.splitlines() if line.startswith("| Quality |"))
    assert quality.startswith("| Quality | WARN |") and "1 red at base" in quality, quality


def test_base_tree_is_the_base_outside_the_workspace_and_goes_with_the_worktree(rig):
    r = rig
    r.create()
    r.drive(until="test")
    out = r.v1("base-tree", "--repo", "repo")
    assert out.get("ok") is True, out
    path = Path(out["path"])
    assert str(path).startswith(str(Path(tempfile.gettempdir())))
    assert not str(path).startswith(str(r.workspace)) if hasattr(r, "workspace") else True
    base_mod = (path / "backend/core/mod.py").read_text()
    work_mod = (r.worktree / "backend/core/mod.py").read_text()
    assert base_mod != work_mod and "b + a" not in base_mod      # the base, not the change
    assert not (path / ".git").exists()
    again = r.v1("base-tree", "--repo", "repo")
    assert again["path"] == out["path"] and again["reused"] is True
    assert r.v1("base-tree", "--repo", "nope").get("ok") is False
    r.v1("stage-complete", "--artifact", r.artifact("t", r.report()))
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {
        "delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    r.v1("land", "--message", "fix: swap the operands" + SWARM)
    assert r.state().get("landing") == "landed"
    assert not path.exists(), "the worktree release removes the run's base copy"
