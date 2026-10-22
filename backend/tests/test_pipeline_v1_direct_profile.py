"""direct without PLAN (execution plan v2 N1): the registry side.

- direct's stages are build → review → test → deliver → reflect, no plan;
- no check that reads REQUIREMENTS or DESIGN_SPEC (RQ-*, DS-*, BX-1, TS-2, TS-3) nor a
  decision check (DC-*) is registered for direct — each has an exemption reason;
- BX-2 (direct only, BUILD) is the change/tdd check that replaces them;
- trivial is byte-for-byte what it was before N1: stages, every check's document /
  effects / stages / variants, and its exemptions, against a frozen snapshot taken
  from the code before the change (tests/fixtures/pipeline_v1_trivial_profile.json).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.run_protocol import checks, lifecycle

SNAPSHOT = Path(__file__).parent / "fixtures" / "pipeline_v1_trivial_profile.json"


def test_direct_has_no_plan():
    assert lifecycle.PROFILE_STAGES["direct"] == ("build", "review", "test", "deliver",
                                                  "reflect")


def test_direct_registers_no_document_or_decision_check():
    ids = {spec.check_id for spec in checks._for("direct")}
    gone = {i for i in ids if i.startswith(("RQ-", "DS-", "DC-"))} | (ids & {"BX-1", "TS-2", "TS-3"})
    assert not gone, gone
    for check_id in ("RQ-1", "DS-9b", "BX-1", "TS-2", "TS-3", "DC-1", "DC-2"):
        assert "direct" in checks.EXEMPTIONS[("direct", check_id)].lower()
    assert [s.check_id for s in checks.stage_checks("direct", "build")] == ["BX-2"]
    assert "BX-2" not in {s.check_id for s in checks._for("trivial")}


def test_trivial_is_unchanged():
    snap = json.loads(SNAPSHOT.read_text())
    now = {"stages": list(lifecycle.PROFILE_STAGES["trivial"]),
           "checks": {s.check_id: {"document": s.document, "effects": sorted(s.effects),
                                   "stages": list(checks._stages_for(s, "trivial")),
                                   "variants": sorted(s.variants)}
                      for s in checks._for("trivial")},
           "exemptions": {c: r for (p, c), r in checks.EXEMPTIONS.items() if p == "trivial"}}
    before = set(snap["checks"]) | set(snap["exemptions"])
    new = set(now["exemptions"]) - before
    assert new == {"BX-2"}, new          # the only check added by N1, exempt for trivial
    now["exemptions"] = {c: r for c, r in now["exemptions"].items() if c in before}
    assert now == snap


def _bx2(final_row, changed=("backend/core/mod.py",)):
    from core.run_protocol import events
    ctx = checks.make_context(profile="direct", requirement_variant="lite", requirements={},
                              authority=events.EventAuthority(b"k" * 32),
                              final={"repos": {"repo": final_row}},
                              run={"code_trees": {"repo": "t"}, "changed_paths": list(changed)},
                              expected_repos={"repo"})
    return checks._bx2(ctx)


def test_bx2_fails_closed_when_selection_failed_or_selected_nothing():
    base = {"code_tree": "t", "tests": {"nodes": {}, "whole_files": []}}
    assert _bx2({**base})[0] is False                                    # selection failed
    assert _bx2({**base, "tdd_required": []})[0] is False                # nothing ran
    ran = {"code_tree": "t", "tdd_required": [],
           "tests": {"nodes": {"backend/tests/test_y.py::t": {"result": "pass"}},
                     "whole_files": ["backend/tests/test_y.py"]}}
    assert _bx2(ran)[0] is True                                          # a named test ran
    assert _bx2({**base, "tdd_required": []}, changed=("README.md",))[0] is True
