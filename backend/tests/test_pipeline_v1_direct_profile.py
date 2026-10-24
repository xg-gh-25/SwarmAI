"""direct without PLAN (execution plan v2 N1): the registry side.

- direct's stages are build → review → test → deliver → reflect, no plan;
- no check that reads REQUIREMENTS or DESIGN_SPEC (RQ-*, DS-*, TS-3) nor a
  decision check (DC-*) is registered for direct — each has an exemption reason;
- BX-3 (every code profile, BUILD) is the validation-report check (S6-1);
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
    gone = {i for i in ids if i.startswith(("RQ-", "DS-", "DC-"))} | (ids & {"TS-3"})
    assert not gone, gone
    for check_id in ("RQ-1", "DS-9a", "TS-3", "DC-1", "DC-2"):
        assert "direct" in checks.EXEMPTIONS[("direct", check_id)].lower()
    assert [s.check_id for s in checks.stage_checks("direct", "build")] == ["BX-3"]


def test_trivial_keeps_its_stages_and_document_checks():
    """S6-1 removed the runner's checks (DS-2b/4b/9b, BX-1/2, TS-2, LG-2/5, QX-1/2, MD-2)
    and added BX-3; S6-2 added RV-4; every other trivial check is as the frozen snapshot recorded it."""
    snap = json.loads(SNAPSHOT.read_text())
    removed = {"DS-2b", "DS-4b", "DS-9b", "BX-1", "BX-2", "TS-2", "LG-2", "LG-5",
               "QX-1", "QX-2", "MD-2"}
    assert list(lifecycle.PROFILE_STAGES["trivial"]) == snap["stages"]
    now = {s.check_id: s for s in checks._for("trivial")}
    assert set(now) == (set(snap["checks"]) - removed) | {"BX-3", "RV-4"}
    for cid, row in snap["checks"].items():
        if cid in removed or cid == "TS-1":
            continue
        spec = now[cid]
        assert {"document": spec.document, "effects": sorted(spec.effects),
                "stages": list(checks._stages_for(spec, "trivial")),
                "variants": sorted(spec.variants)} == row, cid


