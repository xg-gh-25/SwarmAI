"""_signal_stale_spec_details raises a Need-You escalation when a project's
spec-details drift from their domain content-hash, with dedup.

History: the signal used to create a Radar ToDo (run_fe26ed6c). The ToDo card became
a pure user-planning surface (run_50db230a), and the signal moved to an escalation
file read by the Need-You panel (attention_authority._collect_escalations). These
tests pin that contract; they replaced tests that still mocked the deleted ToDo
writer and failed on every run.
"""
import json
from pathlib import Path

from core.escalation import get_open_escalations
from hooks.context_health_hook import ContextHealthHook


def _stale_project(tmp_path) -> Path:
    """A project whose one spec.md marker != its domain spec_hash → stale."""
    proj = tmp_path / "Projects" / "P"
    (proj / "spec-details").mkdir(parents=True)
    doc = {"domains": [{"id": "domain:orders", "name": "orders", "spec_hash": "a" * 64}]}
    (proj / "code-intel.json").write_text(json.dumps(doc), encoding="utf-8")
    (proj / "spec-details" / "orders.spec.md").write_text(
        "# 规格:orders\n<!-- spec-hash: " + ("b" * 64) + " -->\nbody\n", encoding="utf-8")
    return proj.parent  # the Projects/ dir


def test_stale_spec_creates_an_escalation(tmp_path):
    ContextHealthHook()._signal_stale_spec_details(_stale_project(tmp_path))
    open_ = get_open_escalations(tmp_path, "P")
    assert len(open_) == 1, f"expected 1 escalation, got {open_}"
    assert open_[0].id == "esc_spec_details_stale__P"
    assert "orders.spec.md" in open_[0].situation


def test_dedup_no_duplicate_on_second_call(tmp_path):
    projects_dir = _stale_project(tmp_path)
    hook = ContextHealthHook()
    hook._signal_stale_spec_details(projects_dir)
    hook._signal_stale_spec_details(projects_dir)  # second call — must NOT dup
    assert len(get_open_escalations(tmp_path, "P")) == 1


def test_fresh_project_creates_no_escalation(tmp_path):
    proj = tmp_path / "Projects" / "P"
    (proj / "spec-details").mkdir(parents=True)
    h = "c" * 64
    (proj / "code-intel.json").write_text(
        json.dumps({"domains": [{"id": "domain:orders", "spec_hash": h}]}), encoding="utf-8")
    (proj / "spec-details" / "orders.spec.md").write_text(
        f"# x\n<!-- spec-hash: {h} -->\n", encoding="utf-8")
    ContextHealthHook()._signal_stale_spec_details(proj.parent)
    assert get_open_escalations(tmp_path, "P") == []
