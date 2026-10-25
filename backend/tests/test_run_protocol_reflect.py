"""Tests Pipeline v1 reflection preparation and background processing.

Methodology: pure lesson rows with injected judge/writer functions.
"""
from __future__ import annotations

import pytest

from core.run_protocol.reflect import ReflectError, destination, prepare, process

EVENT = "a" * 64


def _lesson(**changes):
    row = {"id": "L1", "type": "pitfall", "scope": "domain:P",
           "statement": "Validate the final tree before landing", "tell": "when landing",
           "evidence": [EVENT], "relation": {"kind": "new", "entry": None}}
    row.update(changes)
    return row


def test_prepare_derives_layer_destination_and_keeps_one_source():
    result = prepare([_lesson()], event_ids={EVENT}, candidates={})
    row = result["lessons"][0]
    assert row["layer"] == "operation"
    assert row["destination"] == "domain:P:IMPROVEMENT:What to Watch For"
    assert row["outcome"] is None


def test_candidate_relation_target_must_exist_and_new_stays_allowed():
    # research-deliver #3: "new" with candidates is accepted (they stay on the lesson)
    assert prepare([_lesson()], event_ids={EVENT}, candidates={"L1": ["old"]})
    with pytest.raises(ReflectError, match="not a candidate"):
        prepare([_lesson(relation={"kind": "refines", "entry": "other"})],
                event_ids={EVENT}, candidates={"L1": ["old"]})
    related = _lesson(relation={"kind": "refines", "entry": "old"})
    assert prepare([related], event_ids={EVENT}, candidates={"L1": ["old"]})


@pytest.mark.parametrize("change", [
    {"statement": "run_abc is done"}, {"statement": "/Users/alice/x"},
    {"evidence": ["foreign"]}, {"scope": "unknown"},
])
def test_volatile_or_foreign_lesson_is_rejected(change):
    with pytest.raises(ReflectError):
        prepare([_lesson(**change)], event_ids={EVENT}, candidates={})


def test_background_process_records_every_outcome_and_degrades_on_failure():
    doc = prepare([_lesson()], event_ids={EVENT}, candidates={})
    ok = process(doc, judge=lambda row: "keep", writer=lambda row, dest: "applied",
                 max_attempts=1)
    assert ok["reflect"] == "ok" and ok["lessons"][0]["outcome"]["status"] == "applied"
    bad = process(doc, judge=lambda row: (_ for _ in ()).throw(RuntimeError()),
                  writer=lambda row, dest: dest, max_attempts=1)
    assert bad["reflect"] == "degraded" and bad["lessons"][0]["outcome"]["status"] == "retry"


def test_product_and_instance_routing():
    assert destination("principle", "product") == "self-evolution-proposal"
    assert destination("correction", "instance") == "EVOLUTION"
    assert destination("decision", "instance") == "MEMORY"


@pytest.mark.parametrize("scope", ["domain:../../x", "domain:/etc", "domain:a/b", "domain:."])
def test_a_domain_scope_is_a_project_name_never_a_path(scope):
    with pytest.raises(ReflectError):
        destination("pitfall", scope)


def test_a_busy_destination_is_retried_then_degrades_and_a_refusal_is_not_applied():
    prepared = prepare([_lesson(), _lesson(id="L2")], event_ids={EVENT}, candidates={})
    results = iter(["locked", "locked", "rejected_low_value"])
    out = process(prepared, judge=lambda lesson: "keep",
                  writer=lambda lesson, dest: next(results))
    first, second = out["lessons"]
    assert first["outcome"]["status"] == "retry" and out["reflect"] == "degraded"
    assert second["outcome"]["status"] == "rejected"


def test_routing_never_leaves_the_workspace_projects(tmp_path):
    from core.run_protocol import reflect_job
    (tmp_path / "Projects" / "P").mkdir(parents=True)
    lesson = _lesson()
    assert reflect_job.route(lesson, "domain:..:TECH:Conventions", workspace=tmp_path,
                             project="P", run_id="run_1") == "rejected_unknown_project"
    # S6-4d: a project that does not exist is not outside Projects/: the lesson is kept
    # in the run's own record (and REPORT), not rejected
    assert reflect_job.route(lesson, "domain:Q:TECH:Conventions", workspace=tmp_path,
                             project="P", run_id="run_1") == "kept_in_run"


def test_annotating_an_entry_twice_writes_one_note(tmp_path):
    """Re-audit ca91762f MED: a crash after annotate_entry and before reflect.json is
    written replays the lesson; the same note is not added a second time."""
    from core.run_protocol.reflect_job import annotate_entry
    project = tmp_path / "Projects" / "P"
    (project / "2-understanding").mkdir(parents=True)
    doc = project / "2-understanding" / "TECH.md"
    doc.write_text("# TECH\n\n## Architecture\n\n- the store is append-only\n")
    entry = "TECH.md#Architecture: the store is append-only"
    note = "reinforced by run_a L1 (evidence abc)"
    assert annotate_entry(tmp_path, "P", entry, note) == "applied"
    assert annotate_entry(tmp_path, "P", entry, note) == "applied"
    assert doc.read_text().count(note) == 1


def test_reflecting_runs_are_queued_again_after_a_restart(monkeypatch, tmp_path):
    """Re-audit ca91762f MED: the queue lives in daemon memory; at startup every run
    whose SIGNED status is reflecting is queued again."""
    from core import run_worktree
    from core.run_protocol import reflect_job
    views = [(tmp_path / "Projects/P/.artifacts/runs/run_r1/run.json", {"status": "reflecting"}),
             (tmp_path / "Projects/P/.artifacts/runs/run_r2/run.json", {"status": "active"})]
    monkeypatch.setattr(run_worktree, "_iter_runs", lambda ws: iter(views))
    queued = []
    monkeypatch.setattr(reflect_job, "_queue_run", lambda rid: queued.append(rid) or True)
    assert reflect_job.reschedule_reflecting(tmp_path) == ["run_r1"]
    assert queued == ["run_r1"]
