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
