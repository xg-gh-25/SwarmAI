"""Pipeline REFLECT v2 five-part report tests.

Old lesson routing, annotation, and direct writers are intentionally absent. CDF S3
owns extraction and the writer owns all governed persistence.
"""
from __future__ import annotations

import pytest

from core.run_protocol.reflect import ReflectError, SECTIONS, prepare

ANCHOR = "run_event:event-1"


def _report():
    return {"delivered": [{
        "kind": "event", "when": "2026-10-27", "scope": "project:P",
        "title": "Run delivered", "what": "The run delivered the source migration.",
        "outcome": "CDF now owns source writes.", "evidence": [ANCHOR]}],
        "decisions": [], "corrections": [], "practices": [], "gaps": []}


def test_prepare_accepts_exact_five_sections():
    result = prepare(_report(), allowed_anchors={ANCHOR})
    assert result["schema"] == 2
    assert tuple(result["sections"]) == SECTIONS


def test_old_lesson_list_is_incompatible():
    with pytest.raises(ReflectError, match="exactly"):
        prepare({"lessons": []}, allowed_anchors=set())


def test_foreign_anchor_and_wrong_section_are_rejected():
    with pytest.raises(ReflectError, match="outside this run"):
        prepare(_report(), allowed_anchors={"run_event:other"})
    report = _report(); report["gaps"] = report["delivered"]
    with pytest.raises(ReflectError, match="wrong unit"):
        prepare(report, allowed_anchors={ANCHOR})


# --- lenient REFLECT (design 2026-11-01-pipeline-delivery-friction E1) ---

def test_lenient_keeps_valid_units_and_names_each_dropped_one():
    from core.run_protocol.reflect import prepare_lenient
    report = _report()
    good = report["delivered"][0]
    report["delivered"] = [good, {"kind": "event", "title": "no what"}, good]
    report["gaps"] = [good]                       # an event is not a gap
    report.pop("practices")                       # a missing section is empty
    report["lessons"] = []                        # an unknown key is dropped
    result = prepare_lenient(report, allowed_anchors={ANCHOR})
    assert len(result["sections"]["delivered"]) == 2
    assert result["sections"]["practices"] == [] and result["sections"]["gaps"] == []
    where = [(row["section"], row["index"]) for row in result["dropped"]]
    assert where == [("lessons", None), ("delivered", 1), ("gaps", 0)]


def test_lenient_still_refuses_the_old_lesson_list():
    from core.run_protocol.reflect import prepare_lenient
    with pytest.raises(ReflectError):
        prepare_lenient({"lessons": []}, allowed_anchors=set())
    with pytest.raises(ReflectError):
        prepare_lenient([], allowed_anchors=set())


def test_strict_prepare_raises_on_what_lenient_drops():
    report = _report()
    report["delivered"].append({"kind": "event"})
    with pytest.raises(ReflectError, match=r"delivered\[1\]"):
        prepare(report, allowed_anchors={ANCHOR})
