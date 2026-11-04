"""A run's REFLECT report can cite only what ``pipeline-v1 events`` shows it.

Questions this file answers:
- does ``pipeline-v1 events`` list, before any ``reflect-prepare``, the exact anchors
  ``reflect-prepare`` accepts as evidence (one list, read the same way)?
- is a report whose every entry cites something else kept empty with a loud warning
  (REFLECT stays lenient: it never fails the step), and does the dropped reason say
  where the valid anchors are?
"""
from __future__ import annotations


def _event(evidence: list[str]) -> dict:
    return {"kind": "event", "when": "2026-11-04", "scope": "project:P",
            "title": "Tab refresh fixed", "what": "The tab refreshes.",
            "outcome": "The tab shows new runs.", "evidence": evidence}


def _report(evidence: list[str]) -> dict:
    return {"delivered": [_event(evidence)], "decisions": [], "corrections": [],
            "practices": [], "gaps": []}


def test_events_lists_the_anchors_reflect_accepts(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import Rig
    r = Rig(tmp_path, monkeypatch, profile="direct")
    try:
        r.create()
        r.drive(until="deliver")
        out = r.v1("events")
        anchors = out["anchors"]
        assert anchors, out                        # shown before any reflect-prepare
        assert "events" not in out                 # raw event ids are not citable
        from core.run_protocol.provenance import run_roots
        assert sorted(anchors) == sorted(run_roots(r.run_id))
        kept = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", _report(anchors)))
        assert kept["reflect"]["sections"]["delivered"], kept
        assert not kept["reflect"].get("dropped") and not kept["reflect"].get("warning")
    finally:
        r.close()


def test_a_report_that_keeps_nothing_warns_and_names_the_anchors(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import Rig
    r = Rig(tmp_path, monkeypatch, profile="direct")
    try:
        r.create()
        r.drive(until="deliver")
        bare = r.events()[-1]["event_id"][:12]      # an event id, not an anchor
        out = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", _report([bare])))
        assert out.get("ok") is True               # lenient: the step is not failed
        rep = out["reflect"]
        assert rep["sections"]["delivered"] == []
        assert rep["dropped"] and "pipeline-v1 events" in rep["dropped"][0]["reason"]
        assert "anchors" in rep["dropped"][0]["reason"]
        assert "nothing" in rep["warning"] and "1" in rep["warning"]
    finally:
        r.close()
